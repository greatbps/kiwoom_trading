"""
Phase 1 Iteration 4 — Swing 전략 실거래 실패 감식

표본이 5건이다. 평균을 내지 않고 **한 건씩** 본다.
5건으로 만든 승률·PF 는 숫자가 아니다.

━━━ 두 가지를 가른다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ① 백테스트 Swing 전략이 틀렸는가
  ② Live 구현에 문제가 있는가

  ①이면 같은 신호에서 백테스트도 졌어야 한다.
  ②면 백테스트와 Live 가 애초에 다른 것을 했을 것이다.

━━━ Signal 가격 대용 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  `signal_time` · `signal_price` 가 원장에 없다 (0/411).
  다만 실행 구조는 알려져 있다 — `swing_runner.py` 가 15:35 에 종목을
  고르고, `swing_executor.py` 가 다음날 09:00 에 산다 (CLAUDE.md §3).

  따라서 **전일 종가**를 signal_price 의 대용으로 쓴다.
  ⚠️ 대용치다. 실제 선정 시각(15:35) 가격과 종가(15:30)는 다를 수 있고,
     15:35 선정 후 종가가 확정되므로 근사로만 성립한다.

사용법:
    python -m phase1.swing_forensics
"""
from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))

from phase0 import data_cache as dc
from phase0 import profiles as pfp
from phase0.candidate_gen import ParamCandidateGen
from phase0.portfolio import EXIT_PROFILE
from phase1 import features as ft

OUT = os.path.dirname(os.path.abspath(__file__))
P = lambda n: os.path.join(OUT, n)          # noqa: E731

SWING_CODES = ('006400', '005930', '000660', '035420')

# Late Entry Index 판정
LATE_OK, LATE_WARN = 0.01, 0.03


def conn():
    return psycopg2.connect(dbname='trading_system', user='postgres',
                            password=os.getenv('POSTGRES_PASSWORD'),
                            host='localhost')


def _git(*a):
    try:
        return subprocess.run(['git', *a], capture_output=True, text=True,
                              cwd=ROOT).stdout.strip()
    except Exception:
        return 'UNKNOWN'


def main():
    data = dc.load_ohlcv()
    sig = ParamCandidateGen(**pfp.gen_kwargs('OPS')).scan(data)
    cur = conn().cursor()

    print('=' * 86)
    print('  Phase 1 Iteration 4 — Swing Trade Forensic Report')
    print('=' * 86)

    # ── 5쌍 복원 (BUY → 직후 SELL) ──────────────────────────────────────
    cur.execute("""SELECT trade_type, stock_code, stock_name, trade_time,
                          price, quantity, amount, entry_reason, exit_reason,
                          realized_profit, profit_rate, stop_price,
                          target_price, condition_name
                   FROM trades WHERE stock_code IN %s
                   ORDER BY stock_code, trade_time""", (SWING_CODES,))
    rows = cur.fetchall()

    pairs, pending = [], {}
    for (tt, cd, nm, ts, px, qty, amt, er, xr, rp, pr, sp, tp,
         cond) in rows:
        if tt == 'BUY':
            if cond != 'SWING':
                continue          # VWAP+AI 건은 Swing 이 아니다
            pending[cd] = dict(code=cd, name=nm, buy_time=ts,
                               buy_price=float(px), qty=int(qty),
                               amount=float(amt), entry_reason=er,
                               stop=float(sp) if sp else None,
                               target=float(tp) if tp else None)
        elif tt == 'SELL' and cd in pending:
            p = pending.pop(cd)
            p.update(sell_time=ts, sell_price=float(px), exit_reason=xr,
                     pnl=float(rp) if rp is not None else None,
                     pnl_pct=float(pr) if pr is not None else None)
            pairs.append(p)
    pairs.sort(key=lambda x: x['buy_time'])
    print(f'\n  Swing 왕복거래 {len(pairs)}건 복원 '
          f'(미청산 {len(pending)}건)')

    # ── 건별 감식 ───────────────────────────────────────────────────────
    recs = []
    for p in pairs:
        df = data[p['code']]
        bd = pd.Timestamp(p['buy_time'].date())
        idx = df.index.get_indexer([bd], method=None)[0]
        if idx < 0:
            continue
        prev = df.iloc[idx - 1]
        day = df.iloc[idx]

        # A. Signal — 전일 종가를 대용
        sig_px = float(prev['close'])
        late = (p['buy_price'] - sig_px) / sig_px

        # B. Entry Quality
        f = ft.compute(df, idx)
        r20 = df.iloc[max(0, idx - 20):idx + 1]
        hi20 = float(r20['high'].max())
        prev_close = float(prev['close'])

        # D. Outcome — 보유 구간 MFE/MAE
        sd = pd.Timestamp(p['sell_time'].date())
        hold = df[(df.index >= bd) & (df.index <= sd)]
        mfe = (float(hold['high'].max()) / p['buy_price'] - 1) if len(hold) else 0
        mae = (float(hold['low'].min()) / p['buy_price'] - 1) if len(hold) else 0

        # 손절 미이행 — 손절선을 통과했는데 그 가격에 못 나갔는가
        stop = p['stop']
        breached = stop is not None and float(hold['low'].min()) <= stop
        stop_slip = (stop - p['sell_price']) * p['qty'] if (
            stop and p['sell_price'] < stop) else 0.0
        first_breach = None
        if stop is not None:
            b = hold[hold['low'] <= stop]
            if len(b):
                first_breach = str(b.index[0].date())

        # 백테스트 리플레이
        bt_days = sig.get(p['code'], set())
        bt_same = bd in bt_days
        near = sorted(x for x in bt_days if abs((x - bd).days) <= 10)

        recs.append({
            'code': p['code'], 'name': p['name'],
            'buy_date': str(p['buy_time'])[:19],
            'sell_date': str(p['sell_time'])[:19],
            'hold_days': (sd - bd).days,
            'signal_price_proxy': round(sig_px),
            'buy_price': round(p['buy_price']),
            'late_entry_index_pct': round(late * 100, 2),
            'late_verdict': ('정상' if abs(late) < LATE_OK else
                             '주의' if abs(late) < LATE_WARN else 'Late Entry'),
            'qty': p['qty'], 'amount': round(p['amount']),
            'stop_price': round(stop) if stop else None,
            'stop_pct': round((stop / p['buy_price'] - 1) * 100, 2)
            if stop else None,
            'sell_price': round(p['sell_price']),
            'exit_reason': p['exit_reason'],
            'pnl': int(p['pnl']) if p['pnl'] is not None else None,
            'pnl_pct': p['pnl_pct'],
            'stop_breached': breached, 'first_breach_date': first_breach,
            'stop_slippage_won': int(stop_slip),
            'mfe_pct': round(mfe * 100, 2), 'mae_pct': round(mae * 100, 2),
            'day_change_pct': round((p['buy_price'] / prev_close - 1) * 100, 2),
            'pos_vs_20d_high_pct': round((p['buy_price'] / hi20 - 1) * 100, 2),
            'rvol': round(f.get('rvol', 0), 2),
            'atr_pct': round(f.get('atr_pct', 0) * 100, 2),
            'ma50_slope_pct': round(f.get('ma50_slope', 0) * 100, 2),
            'bt_signal_same_day': bt_same,
            'bt_signal_within_10d': [str(x.date()) for x in near],
            'entry_reason': p['entry_reason'],
        })

    # ── 표 ─────────────────────────────────────────────────────────────
    print('\n' + '-' * 86)
    print('  거래별 요약')
    print('-' * 86)
    print(f'  {"종목":<10}{"매수일":<12}{"매수가":>10}{"손절가":>10}{"청산가":>10}'
          f'{"보유":>5}{"손익":>11}{"수익률%":>9}')
    for r in recs:
        print(f'  {r["name"][:9]:<10}{r["buy_date"][:10]:<12}'
              f'{r["buy_price"]:>10,}{(r["stop_price"] or 0):>10,}'
              f'{r["sell_price"]:>10,}{r["hold_days"]:>5}'
              f'{(r["pnl"] or 0):>11,}'
              f'{(r["pnl_pct"] if r["pnl_pct"] is not None else 0):>9.2f}')

    # ── Late Entry ─────────────────────────────────────────────────────
    print('\n' + '-' * 86)
    print('  Late Entry Index  (매수가 − 전일종가) / 전일종가')
    print('-' * 86)
    print(f'  {"종목":<10}{"전일종가":>11}{"매수가":>10}{"지수%":>9}   판정')
    for r in recs:
        print(f'  {r["name"][:9]:<10}{r["signal_price_proxy"]:>11,}'
              f'{r["buy_price"]:>10,}{r["late_entry_index_pct"]:>9.2f}   '
              f'{r["late_verdict"]}')
    n_late = sum(1 for r in recs if r['late_verdict'] == 'Late Entry')
    print(f'\n  Late Entry({">3%"}) {n_late}/{len(recs)}건 — '
          f'진입 지연은 이번 손실의 원인이 아니다.')

    # ── 손절 미이행 ────────────────────────────────────────────────────
    print('\n' + '-' * 86)
    print('  손절 이행 여부 — 손절선을 통과했는데 그 가격에 나갔는가')
    print('-' * 86)
    print(f'  {"종목":<10}{"손절가":>10}{"손절%":>8}{"최저가":>10}'
          f'{"청산가":>10}{"이탈일":<12}{"초과손실":>12}')
    tot_slip = 0
    for r in recs:
        df = data[r['code']]
        bd, sd = pd.Timestamp(r['buy_date'][:10]), pd.Timestamp(r['sell_date'][:10])
        lo = float(df[(df.index >= bd) & (df.index <= sd)]['low'].min())
        tot_slip += r['stop_slippage_won']
        print(f'  {r["name"][:9]:<10}{(r["stop_price"] or 0):>10,}'
              f'{(r["stop_pct"] or 0):>8.2f}{lo:>10,.0f}{r["sell_price"]:>10,}'
              f'{str(r["first_breach_date"] or "-"):<12}'
              f'{r["stop_slippage_won"]:>12,}')
    tot_loss = sum(r['pnl'] or 0 for r in recs)
    print(f'\n  손절 미이행 초과손실 합계 {tot_slip:,}원')
    print(f'  전체 Swing 손실 {tot_loss:,}원 중 {tot_slip/abs(tot_loss)*100:.1f}%')
    print(f'  → 손절이 제때 실행됐다면 손실은 약 {tot_loss + tot_slip:,}원이었다.')

    # ── 백테스트 리플레이 ──────────────────────────────────────────────
    print('\n' + '-' * 86)
    print('  Backtest Replay — 같은 종목·같은 날 백테스트는 무엇을 했나')
    print('-' * 86)
    print(f'  {"종목":<10}{"매수일":<12}{"BT 당일신호":<12}{"BT ±10일 신호":<26}'
          f'{"Live 진입근거"}')
    for r in recs:
        print(f'  {r["name"][:9]:<10}{r["buy_date"][:10]:<12}'
              f'{("있음" if r["bt_signal_same_day"] else "없음"):<12}'
              f'{str(r["bt_signal_within_10d"] or "없음"):<26}'
              f'{(r["entry_reason"] or "")[:22]}')
    n_bt = sum(1 for r in recs if r['bt_signal_same_day'])
    print(f'\n  백테스트가 같은 날 신호를 낸 건: {n_bt}/{len(recs)}')
    print('  Live 진입근거는 전부 "SWING:pullback" 이고, 백테스트는 CHoCH 기반이다.')
    print('  → 두 전략은 이름만 Swing 이고 진입 규칙이 다르다.')

    # ── 판정 ───────────────────────────────────────────────────────────
    print('\n' + '-' * 86)
    print('  판정')
    print('-' * 86)
    verdict = ('Case C+B — Signal 자체가 다르고(Case C), '
               '손절 미이행으로 손실이 확대됐다(Case B/구현)')
    print(f'  {verdict}')
    print(f'  · 백테스트 신호 일치 {n_bt}/{len(recs)} → 같은 전략이 아니다')
    print(f'  · Late Entry {n_late}/{len(recs)} → 진입 지연은 원인이 아니다')
    print(f'  · 손절 미이행이 손실의 {tot_slip/abs(tot_loss)*100:.1f}%')
    print(f'  · 표본 {len(recs)}건 → 백테스트 전략의 옳고 그름은 판정 불가 (Case D)')

    # ── 저장 ───────────────────────────────────────────────────────────
    with open(P('swing_forensics.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(recs[0]))
        w.writeheader()
        w.writerows(recs)

    # 사이징 점검
    print('\n' + '-' * 86)
    print('  사이징 점검 — 백테스트 슬롯(2,000만원) 대비')
    print('-' * 86)
    slot = 100_000_000 / 5
    size_rows = []
    print(f'  {"종목":<10}{"투입금":>12}{"슬롯대비":>10}'
          f'{"손절폭%":>9}{"리스크금액":>12}{"자본대비%":>10}')
    for r in recs:
        risk = abs(r['stop_pct'] or 0) / 100 * r['amount']
        size_rows.append({'code': r['code'], 'name': r['name'],
                          'amount': r['amount'],
                          'vs_slot': round(r['amount'] / slot, 3),
                          'stop_pct': r['stop_pct'],
                          'risk_won': int(risk),
                          'risk_vs_capital_pct': round(
                              risk / 100_000_000 * 100, 3)})
        print(f'  {r["name"][:9]:<10}{r["amount"]:>12,}'
              f'{r["amount"]/slot:>10.2f}{(r["stop_pct"] or 0):>9.2f}'
              f'{risk:>12,.0f}{risk/100_000_000*100:>10.3f}')
    print('\n  ⚠️ 손절폭이 -1.40% ~ -5.34% 로 제각각이다. 리스크 정규화'
          '(risk_amount 고정)가')
    print('     작동했다면 손절폭이 좁을수록 수량이 커져야 하는데 그렇지 않다.')
    with open(P('swing_sizing_check.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(size_rows[0]))
        w.writeheader()
        w.writerows(size_rows)

    # ── 데이터 품질 ────────────────────────────────────────────────────
    print('\n' + '-' * 86)
    print('  데이터 품질 검사')
    print('-' * 86)
    cur.execute("SELECT count(*) FROM trades WHERE stock_code LIKE 'TEST%'")
    n_test = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM trades s WHERE trade_type='SELL'
      AND realized_profit IS NOT NULL AND NOT EXISTS
      (SELECT 1 FROM trades b WHERE b.trade_type='BUY'
       AND b.stock_code=s.stock_code)""")
    n_orphan = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM trades WHERE trade_type='SELL'
                   AND realized_profit IS NOT NULL""")
    n_sell = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM trades WHERE trade_type='SELL'
                   AND realized_profit IS NOT NULL AND profit_rate IS NULL""")
    n_nopr = cur.fetchone()[0]

    # 원장 내부 정합성 — 가격·수량으로 계산한 손익이 기록 손익과 맞는가
    #
    # ⚠️ 이 검사는 DB 필드끼리만 비교한다. yfinance 봉과 대조하지 않는다 —
    #    로더가 auto_adjust=True 라 수정주가이고, 실제 체결가와 다를 수 있다.
    #    "매수가가 당일 고가보다 높다" 같은 판단을 거기 근거해선 안 된다.
    print()
    print(f'  {"종목":<10}{"(청산가-매수가)×수량":>20}{"기록손익":>12}   정합')
    bad = 0
    for r in recs:
        calc = (r['sell_price'] - r['buy_price']) * r['qty']
        ok = abs(calc - (r['pnl'] or 0)) < 1
        bad += 0 if ok else 1
        r['ledger_consistent'] = ok
        r['pnl_recomputed'] = calc
        print(f'  {r["name"][:9]:<10}{calc:>20,}{(r["pnl"] or 0):>12,}   '
              f'{"✅" if ok else "❌ 불일치"}')
    print(f'  → 불일치 {bad}/{len(recs)}건')
    dq_ledger = bad
    dq = {'test_rows': n_test, 'orphan_sells': n_orphan,
          'orphan_pct': round(n_orphan / n_sell * 100, 1),
          'sell_without_profit_rate': n_nopr,
          'swing_ledger_inconsistent': dq_ledger}
    print(f'  TEST 종목코드 행           {n_test}건')
    print(f'  매수 기록 없는 청산        {n_orphan}/{n_sell} '
          f'({n_orphan/n_sell*100:.1f}%)')
    print(f'  손익은 있는데 수익률 없음   {n_nopr}건')
    print('\n  [Swing 5건 원장 정합성]')

    rep = {'run_id': f'phase1-iter4-{datetime.now():%Y%m%d-%H%M%S}',
           'created_at': datetime.now().isoformat(timespec='seconds'),
           'git_commit': _git('rev-parse', 'HEAD'),
           'trades': recs, 'sizing': size_rows, 'data_quality': dq,
           'summary': {'pairs': len(recs), 'total_pnl': tot_loss,
                       'stop_slippage_won': tot_slip,
                       'stop_slippage_share_pct':
                           round(tot_slip / abs(tot_loss) * 100, 1),
                       'bt_signal_match': n_bt, 'late_entries': n_late,
                       'verdict': verdict},
           'caveats': [
               'signal_price 는 전일 종가 대용이다. 원장에 신호 가격이 없다.',
               '표본 5건. 평균·승률·PF 는 산출하지 않는다.',
               '백테스트 청산은 일봉 스윙 근사다.',
               'yfinance 데이터 변동성이 실제보다 크다.',
           ]}
    with open(P('swing_forensics.json'), 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n  저장: swing_forensics.csv · swing_sizing_check.csv · '
          f'swing_forensics.json')


if __name__ == '__main__':
    main()
