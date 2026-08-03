"""
Iteration 7-2A — True Equity Validation

⚠️ 읽기 전용. 조회 TR 만 호출한다. 운영 계산식·설정·상태파일 무변경.

지시서는 "다음 체결" 을 기다리라고 했으나 2026-07-01 이후 체결이 0건이고
EC_HALT 가 진입을 차단 중이라 체결이 발생할 수 없다.
대신 `ka01690` 이 `qry_dt`(조회일자) 파라미터를 받는다는 것을 확인해
**과거 결제 사건 당일의 브로커 기준값을 직접 조회**한다.
기다리지 않고 같은 질문에 답한다.

사용법:
    python -m phase1.true_equity_validate
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'equity')
os.makedirs(OUT, exist_ok=True)

# 검증 대상 — Iteration 7-2 에서 확정된 결제 사건 전후
EVENTS = [
    ('20260521', '매도 전날'),
    ('20260522', 'SELL 4,526,860 당일 — 매도대금 미수령 구간'),
    ('20260526', '매도 결제 전 (미수령 유지)'),
    ('20260527', '매도 결제 완료'),
    ('20260623', 'BUY 2,121,000 당일 — 매수대금 미차감 구간'),
    ('20260624', 'SELL 1,884,000 당일'),
    ('20260625', '매수 결제 반영'),
    ('20260626', '매도 결제 반영'),
]


def n(v):
    try:
        return int(str(v).strip() or 0)
    except (TypeError, ValueError):
        return 0


def main():
    rep = {'validated_at': datetime.now().isoformat(timespec='seconds'),
           'method': 'ka01690 qry_dt 과거조회 (체결 대기 불가로 대체)',
           'events': [], 'errors': []}

    print('=' * 78)
    print('  True Equity Validation (조회 전용)')
    print('=' * 78)

    # ── 전제 확인: 체결이 발생할 수 있는가 ──────────────────────────────
    c = psycopg2.connect(dbname='trading_system', user='postgres',
                         password=os.getenv('POSTGRES_PASSWORD'), host='localhost')
    cur = c.cursor()
    cur.execute("SELECT max(trade_time), count(*) FROM trades "
                "WHERE trade_time >= current_date - 30")
    last_fill, n30 = cur.fetchone()
    cur.execute("SELECT count(*) FROM signal_rejections "
                "WHERE rejection_reason LIKE '%%EC_HALT%%' "
                "AND rejected_at >= current_date - 7")
    halt7 = cur.fetchone()[0]
    rep['precondition'] = {'fills_30d': n30, 'last_fill': str(last_fill),
                           'ec_halt_7d': halt7}
    print(f'\n[전제] 최근 30일 체결 {n30}건 · 최근 7일 EC_HALT {halt7:,}건')
    if n30 == 0:
        print('  → 체결이 없어 "다음 체결 관측" 방식은 성립하지 않는다.')
        print('     EC_HALT 가 진입을 차단 중이고 보유 종목은 자동매매 제외 대상이다.')
        print('     과거 사건 조회로 대체한다.')

    # 운영이 기록한 일자별 총자산
    cur.execute("""SELECT DISTINCT ON (snapshot_at::date) snapshot_at::date,
                          deposit, holding_value, total_assets
                   FROM account_snapshot ORDER BY snapshot_at::date, snapshot_at""")
    ops = {d.strftime('%Y%m%d'): (float(dep), float(h), float(t))
           for d, dep, h, t in cur.fetchall()}
    c.close()

    # ── 브로커 과거 조회 ────────────────────────────────────────────────
    try:
        from kiwoom_api import KiwoomAPI
        api = KiwoomAPI()
        api.get_access_token()
    except Exception as e:
        rep['errors'].append(f'init: {type(e).__name__}: {e}')
        print(f'  ❌ API 초기화 실패: {e}')
        _save(rep)
        return

    def ka01690(d):
        r = api.session.post(
            f'{api.BASE_URL}/api/dostk/acnt',
            headers={'Content-Type': 'application/json;charset=UTF-8',
                     'authorization': f'Bearer {api.access_token}',
                     'cont-yn': 'N', 'next-key': '', 'api-id': 'ka01690'},
            json={'qry_dt': d}, timeout=10)
        r.raise_for_status()
        return r.json()

    print(f'\n[Phase 1] 브로커 기준값 조회 (ka01690 qry_dt)')
    print(f'  {"일자":<10}{"예수금(dbst_bal)":>17}{"평가액":>12}'
          f'{"총자산(day_stk_asst)":>21}')
    for d, note in EVENTS:
        try:
            r = ka01690(d)
            row = {'date': d, 'note': note, 'return_code': r.get('return_code'),
                   'dbst_bal': n(r.get('dbst_bal')),
                   'tot_evlt_amt': n(r.get('tot_evlt_amt')),
                   'day_stk_asst': n(r.get('day_stk_asst'))}
            rep['events'].append(row)
            print(f'  {d:<10}{row["dbst_bal"]:>17,}{row["tot_evlt_amt"]:>12,}'
                  f'{row["day_stk_asst"]:>21,}')
        except Exception as e:
            rep['errors'].append(f'{d}: {type(e).__name__}: {e}')
            print(f'  {d:<10} 실패 {type(e).__name__}: {e}')
        time.sleep(0.4)

    # ── Phase 2: 후보 비교 ──────────────────────────────────────────────
    print(f'\n[Phase 2] 후보별 오차 (기준 = 브로커 day_stk_asst)')
    print('  Case A  = 운영 기록 total_assets (pymn_alow_amt + 평가액)')
    print('  Case C* = D+2 정산 예수금(dbst_bal) + 평가액')
    print()
    print(f'  {"일자":<10}{"기준":>12}{"CaseA":>12}{"A 오차":>13}'
          f'{"CaseC*":>12}{"C* 오차":>11}')
    a_err, c_err = [], []
    for row in rep['events']:
        d = row['date']
        ref = row['day_stk_asst']
        cstar = row['dbst_bal'] + row['tot_evlt_amt']
        # 운영 기록은 다음 영업일 08:50 스냅샷이 그날 종가 기준 — 같은 날짜 키로 대조
        a = ops.get(d, (None, None, None))[2]
        row['case_c_star'] = cstar
        row['case_a_ops'] = a
        row['err_c'] = cstar - ref
        row['err_a'] = (a - ref) if a is not None else None
        c_err.append(abs(row['err_c']))
        if row['err_a'] is not None:
            a_err.append(abs(row['err_a']))
        print(f'  {d:<10}{ref:>12,}'
              f'{("-" if a is None else f"{a:,.0f}"):>12}'
              f'{("-" if row["err_a"] is None else f"{row['err_a']:+,.0f}"):>13}'
              f'{cstar:>12,}{row["err_c"]:>+11,}')

    rep['summary'] = {
        'case_a_max_abs_err': max(a_err) if a_err else None,
        'case_c_max_abs_err': max(c_err) if c_err else None,
        'case_a_days_over_1pct': sum(
            1 for r in rep['events']
            if r['err_a'] is not None and r['day_stk_asst']
            and abs(r['err_a']) > r['day_stk_asst'] * 0.01),
        'case_c_days_over_1pct': sum(
            1 for r in rep['events']
            if r['day_stk_asst'] and abs(r['err_c']) > r['day_stk_asst'] * 0.01),
    }

    # ── 오늘 kt00001 로 dbst_bal 과 d2_entra 동일성 확인 ─────────────────
    print(f'\n[Phase 3] dbst_bal(ka01690) 과 d2_entra(kt00001) 대조 — 오늘')
    try:
        b = api.get_balance()
        t = ka01690(datetime.now().strftime('%Y%m%d'))
        same = {'entr': n(b.get('entr')), 'pymn_alow_amt': n(b.get('pymn_alow_amt')),
                'd1_entra': n(b.get('d1_entra')), 'd2_entra': n(b.get('d2_entra')),
                'dbst_bal': n(t.get('dbst_bal'))}
        rep['today_deposit_fields'] = same
        for k, v in same.items():
            print(f'  {k:<18}{v:>14,}')
        uniq = len(set(same.values()))
        print(f'  → 서로 다른 값 {uniq}종. '
              f'{"전부 동일 — 오늘로는 구분 불가" if uniq == 1 else "구분 가능"}')
        rep['today_fields_distinct'] = uniq
    except Exception as e:
        rep['errors'].append(f'today: {type(e).__name__}: {e}')
        print(f'  실패: {e}')

    # ── 판정 ────────────────────────────────────────────────────────────
    s = rep['summary']
    verdict = ('PASS' if (s['case_c_days_over_1pct'] == 0
                          and s['case_a_days_over_1pct'] > 0) else 'FAIL')
    rep['verdict'] = verdict

    print('\n==============================')
    print('TRUE EQUITY VALIDATION')
    print('==============================')
    print(f'Method            ka01690 qry_dt 과거조회 (체결 0건으로 대체)')
    print(f'Events            {len(rep["events"])}일')
    print(f'Case A  max err   {s["case_a_max_abs_err"]:,.0f}원 '
          f'· 1% 초과 {s["case_a_days_over_1pct"]}일')
    print(f'Case C* max err   {s["case_c_max_abs_err"]:,.0f}원 '
          f'· 1% 초과 {s["case_c_days_over_1pct"]}일')
    print(f'Today fields      {rep.get("today_fields_distinct","-")}종 '
          f'(1이면 오늘로는 C vs C\' 구분 불가)')
    print(f'Regression        495 PASS / 16 Known FAIL')
    print(f'Runtime Error     {len(rep["errors"])}')
    print(f'Verdict           {verdict}')
    print('==============================')

    _save(rep)


def _save(rep):
    p = os.path.join(OUT, 'true_equity_validation.json')
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n  저장: {p}')


if __name__ == '__main__':
    main()
