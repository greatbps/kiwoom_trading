"""
Iteration 7-3 Phase 2~4 — Peak 재산출 · Drawdown 재계산 · EC_HALT 재평가

⚠️ 읽기 전용 분석. 운영 코드·설정·상태파일을 건드리지 않는다.

입력 (전부 브로커 실측):
    phase1/equity/broker_daily_equity.json   ka01690 qry_dt 일별 (True Equity)
    phase1/equity/cashflow_raw.json          kt00015 위탁종합거래내역 (입출금/환전)
                                             ⚠️ 재수집 시 `prcsr`(처리자 실명) 반드시 마스킹

세 가지 정의로 peak/dd 를 각각 산출해 EC_HALT 를 재평가한다.

    OLD   운영 기록 total_assets (pymn_alow_amt + 평가액)
    TRUE  브로커 day_stk_asst    (D+2 정산 예수금 + 평가액)  ← Iteration 7-2A 확정
    TWR   TRUE 에서 입출금/환전 효과를 제거한 시간가중수익 지수

사용법:
    python -m phase1.ec_halt_replay
"""
from __future__ import annotations

import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import yaml
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'equity')

# 국내 매매 = 계좌 내부 이동. 자본 유출입이 아니다.
INTERNAL_TRADE = {'장내매수', '장내매도', 'KOSDAQ매수', 'KOSDAQ매도', '매수', '매도'}
# 원화 계좌 기준 외부 유입(+) / 유출(-)
FLOW_SIGN = {
    '이체입금(지급결제)': +1,
    '환전정산입금': +1,
    '외화매도': +1,
    '원화주문 외화매수': -1,      # 원화가 외화계좌로 이동 — 손실이 아니다
}
# 투자수익으로 보아 조정하지 않는 항목 (참고용 기록만)
INVESTMENT_INCOME = {'예탁금이용료(이자)입금', '수익분배금입금',
                     '예탁금이용료이자세금출금', '외화예탁금이용료(이자)입금'}


def main():
    cfg = yaml.safe_load(open(os.path.join(ROOT, 'config', 'strategy_hybrid.yaml')))
    thr = (cfg.get('equity_control') or {}).get('max_dd_halt', -0.18)

    eq = json.load(open(os.path.join(OUT, 'broker_daily_equity.json')))
    days = sorted(k for k, v in eq.items() if 'day_stk_asst' in v)
    true_eq = {d: eq[d]['day_stk_asst'] for d in days}

    rows = json.load(open(os.path.join(OUT, 'cashflow_raw.json')))
    flows = defaultdict(float)
    income = defaultdict(float)
    detail = []
    for r in rows:
        nm = (r.get('rmrk_nm') or '').strip()
        if nm in INTERNAL_TRADE:
            continue
        amt = float(str(r.get('exct_amt') or 0).strip() or 0)
        if not amt:
            continue
        if nm in FLOW_SIGN:
            flows[r['trde_dt']] += FLOW_SIGN[nm] * amt
            detail.append({'date': r['trde_dt'], 'name': nm,
                           'amount': FLOW_SIGN[nm] * amt, 'kind': 'external'})
        elif nm in INVESTMENT_INCOME:
            income[r['trde_dt']] += amt
            detail.append({'date': r['trde_dt'], 'name': nm,
                           'amount': amt, 'kind': 'income'})
        else:
            detail.append({'date': r['trde_dt'], 'name': nm,
                           'amount': amt, 'kind': 'UNCLASSIFIED'})

    unk = [d for d in detail if d['kind'] == 'UNCLASSIFIED']

    print('=' * 78)
    print('  EC_HALT Replay — Peak 재산출 / Drawdown 재계산 (읽기 전용)')
    print('=' * 78)
    print(f'\n[입력] 브로커 일별 {len(days)}일 · 거래내역 {len(rows)}건')
    print(f'  외부 현금흐름 {sum(1 for d in detail if d["kind"]=="external")}건 · '
          f'투자수익 {sum(1 for d in detail if d["kind"]=="income")}건 · '
          f'미분류 {len(unk)}건')
    for d in detail:
        if d['kind'] != 'income':
            print(f'    {d["date"]}  {d["name"]:<22}{d["amount"]:>+14,.0f}  [{d["kind"]}]')
    if unk:
        print('  ⚠️ 미분류 항목이 있다. 조정에 반영하지 않았다 — 결과 해석 시 주의.')

    # ── 세 정의로 peak/dd 산출 ─────────────────────────────────────────
    # OLD: 운영이 기록한 total_assets
    c = psycopg2.connect(dbname='trading_system', user='postgres',
                         password=os.getenv('POSTGRES_PASSWORD'), host='localhost')
    cur = c.cursor()
    cur.execute("""SELECT DISTINCT ON (snapshot_at::date) snapshot_at::date, total_assets
                   FROM account_snapshot ORDER BY snapshot_at::date, snapshot_at DESC""")
    old_eq = {d.strftime('%Y%m%d'): float(t) for d, t in cur.fetchall()}

    # 실제 EC_HALT 발생일
    cur.execute("""SELECT date(rejected_at), count(*) FROM signal_rejections
                   WHERE rejection_reason LIKE '%%EC_HALT%%' GROUP BY 1""")
    halt_actual = {d.strftime('%Y%m%d'): n for d, n in cur.fetchall()}
    c.close()

    def curve(series):
        """eod_only_peak=True · peak 단조증가 — 운영 정책과 동일."""
        peak = 0.0
        out = {}
        for d in days:
            e = series.get(d)
            if e is None:
                continue
            dd = (e - peak) / peak if peak > 0 else 0.0
            out[d] = {'equity': e, 'peak': peak, 'dd': dd}
            if e > peak:
                peak = e
        return out, peak

    # TWR 지수 — 외부 현금흐름 효과 제거 (기말 유입 가정)
    twr = {}
    idx = 1_000_000.0
    prev = None
    for d in days:
        e = true_eq[d]
        f = flows.get(d, 0.0)
        if prev is not None and prev > 0:
            r = (e - f) / prev - 1.0
            idx *= (1.0 + r)
        twr[d] = idx
        prev = e

    c_old, p_old = curve(old_eq)
    c_true, p_true = curve(true_eq)
    c_twr, p_twr = curve(twr)

    print(f'\n[Phase 2] Peak 재산출')
    print(f'  {"정의":<8}{"Peak":>14}{"Peak 일자":>12}{"최종 equity":>14}')
    for nm, cv in (('OLD', c_old), ('TRUE', c_true), ('TWR', c_twr)):
        pk = max((v['equity'] for v in cv.values()), default=0)
        pd_ = next((d for d in days if d in cv and cv[d]['equity'] == pk), '-')
        last = cv[days[-1]]['equity'] if days[-1] in cv else 0
        print(f'  {nm:<8}{pk:>14,.0f}{pd_:>12}{last:>14,.0f}')

    print(f'\n[Phase 3] Drawdown 재계산 — 최근 10 영업일')
    print(f'  {"일자":<10}{"OLD dd":>9}{"TRUE dd":>10}{"TWR dd":>9}   실제 EC_HALT')
    for d in days[-10:]:
        o = c_old.get(d, {}).get('dd')
        t = c_true.get(d, {}).get('dd')
        w = c_twr.get(d, {}).get('dd')
        print(f'  {d:<10}'
              f'{("-" if o is None else f"{o*100:.1f}%"):>9}'
              f'{("-" if t is None else f"{t*100:.1f}%"):>10}'
              f'{("-" if w is None else f"{w*100:.1f}%"):>9}'
              f'   {halt_actual.get(d,0):,}건')

    # ── Phase 4: EC_HALT 재평가 ────────────────────────────────────────
    print(f'\n[Phase 4] EC_HALT 재평가 (임계값 {thr:.0%}, EOD 기준)')
    hd = sorted(halt_actual)
    stat = {}
    for nm, cv in (('OLD', c_old), ('TRUE', c_true), ('TWR', c_twr)):
        blocked = [d for d in hd if d in cv and cv[d]['dd'] <= thr]
        unblocked = [d for d in hd if d in cv and cv[d]['dd'] > thr]
        miss = [d for d in hd if d not in cv]
        stat[nm] = {'halt_days': len(hd), 'still_blocked': len(blocked),
                    'would_unblock': len(unblocked), 'no_data': len(miss),
                    'unblock_pct': (len(unblocked) / len(hd) * 100) if hd else 0.0,
                    'unblocked_days': unblocked}
        print(f'  {nm:<6} 실제 HALT {len(hd)}일 중 → 여전히 차단 {len(blocked)}일 · '
              f'해제 {len(unblocked)}일 ({stat[nm]["unblock_pct"]:.0f}%)'
              f'{f" · 데이터없음 {len(miss)}일" if miss else ""}')

    rep = {
        'replayed_at': datetime.now().isoformat(timespec='seconds'),
        'threshold': thr, 'days': len(days),
        'cashflows': detail, 'unclassified': len(unk),
        'peak': {'OLD': max((v['equity'] for v in c_old.values()), default=0),
                 'TRUE': max((v['equity'] for v in c_true.values()), default=0),
                 'TWR': max((v['equity'] for v in c_twr.values()), default=0)},
        'stat': stat,
        'series': {d: {'true_equity': true_eq[d],
                       'twr_index': round(twr[d], 2),
                       'flow': flows.get(d, 0.0),
                       'dd_true': round(c_true[d]['dd'], 6) if d in c_true else None,
                       'dd_twr': round(c_twr[d]['dd'], 6) if d in c_twr else None,
                       'dd_old': round(c_old[d]['dd'], 6) if d in c_old else None,
                       'ec_halt_actual': halt_actual.get(d, 0)} for d in days},
    }
    with open(os.path.join(OUT, 'ec_halt_replay.json'), 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print(f'\n  저장: {OUT}/ec_halt_replay.json')
    return rep


if __name__ == '__main__':
    main()
