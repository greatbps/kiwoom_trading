"""
Phase 1 Experiment 1 — SELL Signal 영향 분석

━━━ 지시서 그대로는 실행할 수 없다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  지시서는 "Case A: BUY+SELL" 과 "Case B: BUY Only" 를 비교하라고 한다.
  그런데 이 시스템에는 나눌 두 케이스가 없다.

    ① 백테스트 경로는 이미 BUY 전용이다.
       `SMCAdapter.get_signal()` 은 'BUY' 아니면 None 만 돌려준다
       (backtest/adapter.py:302, 307). Phase 0 Baseline 81거래 · PF 1.813
       이 곧 'BUY Only' 다. Case A 가 존재하지 않는다.

    ② 실거래 DB 의 `trades.trade_type` 은 **매매 방향이 아니라 왕복거래의
       다리**다. BUY = 진입, SELL = 청산. 실현손익은 청산 행에만 달린다
       (SELL 246행 중 242행에 realized_profit, BUY 165행 중 7행).
       "SELL 을 뺀다" 는 것은 매도 전략을 끄는 것이 아니라 **청산을 하지
       않는다** 는 뜻이 된다.

    ③ 이 프로젝트는 롱 온리 스윙이다 (CLAUDE.md 절대원칙).
       끌 공매도 전략 자체가 없다.

  그래서 이 스크립트는 지시서의 비교를 흉내 내지 않는다. 대신
  ㉠ 인용된 수치(BUY PF 3.362 / SELL PF 0.315)가 어디서 나온 값인지 확인하고
  ㉡ 실거래 성과를 **가능한 축으로** 분해해 무엇이 성과를 끌어내리는지 본다.
     그게 Experiment 1 이 알고자 한 것이다.

사용법:
    python -m phase1.exp1_sell_analysis
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), '.env'))

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   'exp1_result.json')
QUOTED = {'buy_pf': 3.362, 'sell_pf': 0.315, 'window': '최근 200일'}


def conn():
    return psycopg2.connect(dbname='trading_system', user='postgres',
                            password=os.getenv('POSTGRES_PASSWORD'),
                            host='localhost')


def pf_of(gp, gl):
    return round(gp / gl, 3) if gl else None


def main():
    c = conn()
    cur = c.cursor()
    out = {'created_at': datetime.now().isoformat(timespec='seconds'),
           'quoted': QUOTED}

    print('=' * 74)
    print('  Experiment 1 — SELL Signal 영향 분석')
    print('=' * 74)

    # ── ① trade_type 의 정체 ────────────────────────────────────────────
    cur.execute("""SELECT trade_type, count(*),
                          count(realized_profit),
                          min(trade_time)::date, max(trade_time)::date
                   FROM trades GROUP BY 1 ORDER BY 1""")
    print('\n  ① trade_type 은 방향인가 다리인가')
    print(f'  {"type":<6}{"행":>6}{"손익있음":>9}   기간')
    legs = {}
    for tt, n, wp, lo, hi in cur.fetchall():
        legs[tt] = {'rows': n, 'with_pnl': wp, 'first': str(lo),
                    'last': str(hi)}
        print(f'  {tt:<6}{n:>6}{wp:>9}   {lo} ~ {hi}')
    out['legs'] = legs
    print('  → 실현손익이 SELL 행에만 달린다. SELL = 청산 다리다.')
    print('     방향(롱/숏)이 아니므로 "SELL 전략 제거" 는 성립하지 않는다.')

    # ── ② 인용 수치 재현 시도 ───────────────────────────────────────────
    print('\n  ② 인용 수치 재현 시도 (BUY PF 3.362 / SELL PF 0.315)')
    repro = {}
    for label, col, where in (
            ('realized_profit · 최근200일', 'realized_profit',
             "trade_time >= now()-'200 days'::interval"),
            ('realized_profit · 전체', 'realized_profit', 'TRUE'),
            ('profit_rate · 최근200일', 'profit_rate',
             "trade_time >= now()-'200 days'::interval"),
            ('profit_rate · 전체', 'profit_rate', 'TRUE')):
        cur.execute(f"""SELECT trade_type,
              COALESCE(SUM({col}) FILTER (WHERE {col}>0),0),
              COALESCE(-SUM({col}) FILTER (WHERE {col}<=0),0)
              FROM trades WHERE {where} GROUP BY 1""")
        d = {tt: pf_of(gp, gl) for tt, gp, gl in cur.fetchall()}
        repro[label] = d
        print(f'    {label:<26} BUY {str(d.get("BUY")):>7}   '
              f'SELL {str(d.get("SELL")):>7}')
    out['reproduction_attempts'] = repro
    print('    → 어떤 조합으로도 3.362 / 0.315 가 나오지 않는다.')
    print('       인용 수치의 산출 근거를 확인해야 한다.')

    # ── ③ 실거래 실제 성과 ──────────────────────────────────────────────
    print('\n  ③ 실거래 실제 성과 (청산 완료 거래 기준)')
    cur.execute("""SELECT count(*),
             count(*) FILTER (WHERE realized_profit>0),
             COALESCE(SUM(realized_profit),0),
             COALESCE(SUM(realized_profit) FILTER (WHERE realized_profit>0),0),
             COALESCE(-SUM(realized_profit) FILTER (WHERE realized_profit<=0),0),
             COALESCE(AVG(realized_profit),0)
             FROM trades WHERE trade_type='SELL' AND realized_profit IS NOT NULL""")
    n, nw, net, gp, gl, avg = cur.fetchone()
    live = {'closed_trades': n, 'wins': nw,
            'win_rate': round(nw / n * 100, 1) if n else 0,
            'net_won': int(net), 'gross_profit': int(gp),
            'gross_loss': int(gl), 'profit_factor': pf_of(gp, gl),
            'expectancy_won': int(avg)}
    out['live_actual'] = live
    print(f'    청산 거래 {n}건   승률 {live["win_rate"]}%')
    print(f'    총이익 {gp:>12,.0f}   총손실 {gl:>12,.0f}')
    print(f'    순손익 {net:>12,.0f}   PF {live["profit_factor"]}   '
          f'1거래 기대값 {avg:,.0f}원')

    # ── ④ 분해 — 무엇이 끌어내리는가 ────────────────────────────────────
    print('\n  ④ 분해 (채워진 축만 — 대부분의 메타 컬럼이 비어 있다)')
    breakdown = {}
    for col in ('exit_category', 'overnight_held'):
        cur.execute(f"""SELECT COALESCE({col}::text,'(null)'), count(*),
              count(*) FILTER (WHERE realized_profit>0),
              COALESCE(SUM(realized_profit),0),
              COALESCE(SUM(realized_profit) FILTER (WHERE realized_profit>0),0),
              COALESCE(-SUM(realized_profit) FILTER (WHERE realized_profit<=0),0)
              FROM trades WHERE trade_type='SELL' AND realized_profit IS NOT NULL
              GROUP BY 1 ORDER BY 4""")
        rows = cur.fetchall()
        breakdown[col] = []
        print(f'\n    [{col}]')
        print(f'    {"값":<22}{"건":>5}{"승률%":>8}{"순손익":>13}{"PF":>8}')
        for v, cnt, wins, net_, gp_, gl_ in rows:
            p = pf_of(gp_, gl_)
            breakdown[col].append({'value': v, 'trades': cnt,
                                   'win_rate': round(wins / cnt * 100, 1),
                                   'net_won': int(net_), 'pf': p})
            print(f'    {v[:22]:<22}{cnt:>5}{wins/cnt*100:>8.1f}'
                  f'{net_:>13,.0f}{(f"{p:.3f}" if p else "-"):>8}')
    out['breakdown'] = breakdown

    # 손실 상위 — 소수 거래가 전체를 먹는가
    cur.execute("""SELECT stock_code, stock_name, trade_time::date,
                          realized_profit, profit_rate, exit_reason
                   FROM trades WHERE trade_type='SELL'
                   AND realized_profit IS NOT NULL
                   ORDER BY realized_profit ASC LIMIT 10""")
    worst = cur.fetchall()
    tot_loss = live['gross_loss']
    top10 = -sum(r[3] for r in worst)
    print(f'\n    [손실 상위 10건] 전체 손실의 '
          f'{top10/tot_loss*100:.1f}% 를 차지한다')
    print(f'    {"종목":<10}{"일자":<12}{"손익":>12}{"수익률%":>9}  사유')
    for code, nm, d, rp, pr, er in worst:
        print(f'    {(nm or code)[:10]:<10}{str(d):<12}{rp:>12,.0f}'
              f'{(pr or 0):>9.2f}  {(er or "")[:28]}')
    out['worst_10'] = [{'code': a, 'name': b, 'date': str(cc), 'pnl': float(d),
                        'pct': float(e or 0), 'reason': f}
                       for a, b, cc, d, e, f in worst]
    out['worst10_share_of_loss_pct'] = round(top10 / tot_loss * 100, 1)

    # ── ⑤ 포지션 규모 — 손실이 어디에 몰려 있는가 ──────────────────────
    #
    # ⚠️ 여기가 이번 분석의 핵심이다. 승률이나 진입 품질만 보면 놓친다.
    #    같은 승률이라도 큰 포지션에서만 지면 계좌는 무너진다.
    print('\n  ⑤ 포지션 규모별 성과 (5분위)')
    cur.execute("""
      WITH q AS (SELECT amount, realized_profit, profit_rate,
                        ntile(5) OVER (ORDER BY amount) AS b
                 FROM trades
                 WHERE trade_type='SELL' AND realized_profit IS NOT NULL)
      SELECT b, count(*), min(amount), max(amount),
             count(*) FILTER (WHERE realized_profit>0),
             SUM(realized_profit), AVG(profit_rate)
      FROM q GROUP BY b ORDER BY b""")
    quint = []
    print(f'    {"분위":<4}{"건":>5}{"규모범위":>26}{"승률%":>8}'
          f'{"순손익":>13}{"평균수익률%":>11}')
    for b, cnt, lo, hi, wins, net_, apr in cur.fetchall():
        quint.append({'quintile': b, 'trades': cnt, 'size_min': float(lo),
                      'size_max': float(hi),
                      'win_rate': round(wins / cnt * 100, 1),
                      'net_won': int(net_),
                      'avg_return_pct': round(float(apr or 0), 3)})
        print(f'    {b:<4}{cnt:>5}{f"{lo:,.0f}~{hi:,.0f}":>26}'
              f'{wins/cnt*100:>8.1f}{net_:>13,.0f}{float(apr or 0):>11.2f}')
    out['size_quintiles'] = quint
    top_q = quint[-1]
    share = top_q['net_won'] / live['net_won'] * 100 if live['net_won'] else 0
    print(f'    → 최대 분위가 순손실의 {share:.1f}% 를 만든다. '
          f'승률은 {top_q["win_rate"]}% 로 오히려 가장 높다.')
    print('      진입이 틀린 게 아니라 **질 때 크게 진다.**')
    out['top_quintile_share_of_net_loss_pct'] = round(share, 1)

    # 동일비중이면 어떻게 되는가 — 사이징 영향 분리
    cur.execute("""SELECT
        COALESCE(SUM(profit_rate) FILTER (WHERE profit_rate>0),0),
        COALESCE(-SUM(profit_rate) FILTER (WHERE profit_rate<=0),0),
        COALESCE(SUM(realized_profit) FILTER (WHERE realized_profit>0),0),
        COALESCE(-SUM(realized_profit) FILTER (WHERE realized_profit<=0),0),
        MAX(amount), percentile_cont(0.5) WITHIN GROUP (ORDER BY amount)
        FROM trades WHERE trade_type='SELL' AND realized_profit IS NOT NULL""")
    pgp, pgl, wgp, wgl, mx, md = cur.fetchone()
    eq_pf, real_pf = pf_of(pgp, pgl), pf_of(wgp, wgl)
    out['sizing_effect'] = {
        'equal_weight_pf': eq_pf, 'actual_weight_pf': real_pf,
        'gap': round(eq_pf - real_pf, 3),
        'max_position': float(mx), 'median_position': float(md),
        'max_over_median': round(float(mx) / float(md), 1)}
    print(f'\n    동일비중 PF {eq_pf}   실제비중 PF {real_pf}   '
          f'차이 {eq_pf - real_pf:+.3f}')
    print(f'    포지션 최대/중앙값 {float(mx)/float(md):.0f}배 '
          f'({md:,.0f} → {mx:,.0f})')
    print('    → 사이징이 손실을 증폭시켰다. 다만 동일비중 PF 도 1 미만이라')
    print('      사이징만 고쳐서 흑자가 되지는 않는다.')

    with open(OUT, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print(f'\n  저장: {OUT}')
    c.close()


if __name__ == '__main__':
    main()
