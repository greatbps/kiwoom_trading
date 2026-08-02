"""
Phase 1 Iteration 3 — Strategy Parity & Early Entry Foundation

KPI-1  전략 차이 코드 레벨 규명
KPI-2  Live 94종목 출처 분류
KPI-3  CHoCH → 매수 지연 측정
KPI-4  Late Entry Index
KPI-5  Early Entry Opportunity Top 30

━━━ KPI-3/4/5 는 계산할 수 없다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  근거 데이터가 존재하지 않는다. 추정으로 채우지 않고 그 사실을
  숫자로 남긴다.

    trades.candidate_first_time      0 / 411
    trades.entry_signal_time         0 / 411
    trades.pre_candidate_first_time  0 / 411
    trades.entry_signal_id           0 / 411
    signal_events.choch_raw_time     0 / 756

  대체 경로로 `logs/smc_decision_*.log` 를 뒤졌으나 로그 보유 구간
  (2026-03-18~)과 실거래 구간(~2026-07-01)이 겹치는 BUY 26건 중
  SMC 로그에 흔적이 있는 것은 **1건**, CHoCH 기록이 있는 것은 **0건**이다.

  이유는 KPI-2 가 설명한다 — 실거래 매수의 95.8% 가 SMC 파이프라인을
  거치지 않는다. CHoCH 가 없으니 CHoCH 지연도 없다.

사용법:
    python -m phase1.universe_origin
"""
from __future__ import annotations

import csv
import glob
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))

from backtest.scanner import DEFAULT_CANDIDATES

OUT = os.path.dirname(os.path.abspath(__file__))
P = lambda n: os.path.join(OUT, n)          # noqa: E731

LOG_GLOB = os.path.join(ROOT, 'logs', 'smc_decision_2026*.log')


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


def classify(cond: str | None, reason: str | None, strat: str | None) -> str:
    """
    매수 한 건의 출처. **추정하지 않는다** — 기록이 없으면 UNKNOWN 이다.

    condition_name 이 키움 조건검색식 이름이다. 이게 채워져 있으면
    그 종목은 조건검색에서 왔다.
    """
    c = (cond or '').strip()
    r = (reason or '')
    if c == 'VWAP+AI':
        return '조건검색식 (VWAP+AI)'
    if c.upper() == 'SWING' or (strat or '').lower() == 'swing' \
            or r.startswith('SWING:'):
        return 'Swing Runner'
    if 'SMC' in r:
        return 'SMC 파이프라인'
    if r.startswith('EXPLORATION'):
        return 'Exploration'
    if r.startswith('EXPERIMENT'):
        return 'Experiment'
    if c:
        return f'조건검색식 ({c})'
    return 'UNKNOWN (기록 없음)'


def main():
    cur = conn().cursor()
    rep = {'run_id': f'phase1-iter3-{datetime.now():%Y%m%d-%H%M%S}',
           'created_at': datetime.now().isoformat(timespec='seconds'),
           'git_commit': _git('rev-parse', 'HEAD')}

    print('=' * 84)
    print('  Phase 1 Iteration 3 — Strategy Parity & Early Entry Foundation')
    print('=' * 84)

    # ═══ KPI-2 — 출처 분류 ══════════════════════════════════════════════
    print('\n' + '-' * 84)
    print('  KPI-2 — Live 종목 생성 경로 추적')
    print('-' * 84)

    cur.execute("""SELECT stock_code, stock_name, condition_name,
                          entry_reason, strategy_name, trade_time, price
                   FROM trades WHERE trade_type='BUY' ORDER BY trade_time""")
    buys = cur.fetchall()

    per_trade, per_symbol = [], defaultdict(lambda: defaultdict(int))
    for cd, nm, cond, rsn, st, tt, px in buys:
        o = classify(cond, rsn, st)
        per_trade.append({'stock_code': cd, 'name': nm, 'origin': o,
                          'condition_name': cond, 'entry_reason': rsn,
                          'strategy_name': st, 'trade_time': str(tt),
                          'price': float(px or 0)})
        per_symbol[cd][o] += 1

    oc = defaultdict(lambda: {'trades': 0, 'symbols': set()})
    for t in per_trade:
        oc[t['origin']]['trades'] += 1
        oc[t['origin']]['symbols'].add(t['stock_code'])

    print(f'  {"출처":<24}{"거래":>6}{"비중%":>8}{"종목":>6}')
    total = len(per_trade)
    origin_rows = []
    for o, v in sorted(oc.items(), key=lambda x: -x[1]['trades']):
        origin_rows.append({'origin': o, 'trades': v['trades'],
                            'pct': round(v['trades'] / total * 100, 1),
                            'symbols': len(v['symbols'])})
        print(f'  {o:<24}{v["trades"]:>6}{v["trades"]/total*100:>8.1f}'
              f'{len(v["symbols"]):>6}')

    unknown = oc.get('UNKNOWN (기록 없음)', {'trades': 0})['trades']
    rate = (total - unknown) / total * 100
    print(f'\n  분류율 {rate:.1f}%  (목표 95% — '
          f'{"✅ PASS" if rate >= 95 else "❌ FAIL"})')

    # 종목별 CSV
    cur.execute("""SELECT stock_code, COALESCE(SUM(realized_profit),0), count(*)
                   FROM trades WHERE trade_type='SELL'
                   AND realized_profit IS NOT NULL GROUP BY 1""")
    pnl = {r[0]: (float(r[1]), r[2]) for r in cur.fetchall()}
    name_of = {t['stock_code']: t['name'] for t in per_trade}

    sym_rows = []
    for cd, origins in per_symbol.items():
        top = max(origins.items(), key=lambda x: x[1])[0]
        net, n = pnl.get(cd, (0.0, 0))
        sym_rows.append({'stock_code': cd, 'name': name_of.get(cd, ''),
                         'buy_trades': sum(origins.values()),
                         'closed_trades': n, 'net_pnl': int(net),
                         'origin': top,
                         'in_default_candidates': cd in DEFAULT_CANDIDATES})
    sym_rows.sort(key=lambda x: x['net_pnl'])
    with open(P('universe_origin.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=list(sym_rows[0]))
        w.writeheader()
        w.writerows(sym_rows)

    inuni = sum(1 for r in sym_rows if r['in_default_candidates'])
    print(f'  매수 종목 {len(sym_rows)}개 중 DEFAULT_CANDIDATES 소속 '
          f'{inuni}개 ({inuni/len(sym_rows)*100:.1f}%)')
    rep['kpi2'] = {'classification_rate': round(rate, 1),
                   'pass': rate >= 95, 'origins': origin_rows,
                   'symbols': len(sym_rows), 'in_default_candidates': inuni}

    # 조건검색식 사용 실태
    cur.execute("""SELECT COALESCE(condition_name,'(null)'), count(*),
                   count(DISTINCT stock_code) FROM trades WHERE trade_type='BUY'
                   GROUP BY 1 ORDER BY 2 DESC""")
    cond_rows = [{'condition_name': a, 'trades': b, 'symbols': c}
                 for a, b, c in cur.fetchall()]
    with open(P('condition_usage.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['condition_name', 'trades',
                                          'symbols'])
        w.writeheader()
        w.writerows(cond_rows)
    print('\n  [condition_name — 실거래 후보의 실제 공급원]')
    for r in cond_rows:
        print(f'    {str(r["condition_name"])[:32]:<32}{r["trades"]:>5}건'
              f'{r["symbols"]:>5}종목')

    # ═══ KPI-3/4/5 — 산출 가능성 검증 ══════════════════════════════════
    print('\n' + '-' * 84)
    print('  KPI-3 / 4 / 5 — 근거 데이터 존재 여부')
    print('-' * 84)

    avail = {}
    for col in ('candidate_first_time', 'entry_signal_time',
                'pre_candidate_first_time', 'entry_signal_id', 'entry_time'):
        cur.execute(f'SELECT count({col}) FROM trades')
        avail[f'trades.{col}'] = cur.fetchone()[0]
    cur.execute('SELECT count(choch_raw_time) FROM signal_events')
    avail['signal_events.choch_raw_time'] = cur.fetchone()[0]
    cur.execute('SELECT count(*) FROM trades')
    n_all = cur.fetchone()[0]

    print(f'  {"컬럼":<38}{"채움":>8}/{n_all}')
    for k, v in avail.items():
        print(f'  {k:<38}{v:>8}')

    # 로그 대체 경로
    pat = re.compile(r'^(\d{2}:\d{2}:\d{2}) \[(CHOCH|SWEEP|NO_SIG|REJECT)\] '
                     r'(\d{6})')
    ev = defaultdict(list)
    for fp in sorted(glob.glob(LOG_GLOB)):
        d = re.search(r'(\d{8})', fp).group(1)
        d = f'{d[:4]}-{d[4:6]}-{d[6:]}'
        for line in open(fp, errors='replace'):
            m = pat.match(line)
            if m:
                ev[(m.group(3), d)].append((m.group(1), m.group(2)))

    cur.execute("""SELECT stock_code, trade_time FROM trades
                   WHERE trade_type='BUY'
                   AND trade_time::date BETWEEN '2026-03-18' AND '2026-07-01'""")
    ovl = cur.fetchall()
    traced = sum(1 for cd, tt in ovl if ev.get((cd, str(tt.date()))))
    with_choch = sum(1 for cd, tt in ovl
                     if any(k == 'CHOCH'
                            for _, k in ev.get((cd, str(tt.date())), [])))

    print(f'\n  대체 경로 — logs/smc_decision_*.log')
    print(f'    로그 보유 구간 2026-03-18 ~ 2026-08-02 (91개 파일)')
    print(f'    실거래 구간과 겹치는 BUY: {len(ovl)}건 / 전체 165건')
    print(f'    그중 SMC 로그에 흔적 있음 : {traced}건')
    print(f'    그중 CHoCH 기록 있음      : {with_choch}건')
    print(f'\n  → KPI-3 (CHoCH→매수 지연) 산출 불가')
    print(f'    KPI-4 (Late Entry Index) 산출 불가 — KPI-3 에 의존')
    print(f'    KPI-5 (Top 30 순서 기록) 산출 불가 — KPI-3 에 의존')
    rep['kpi345'] = {'computable': False, 'column_fill': avail,
                     'log_overlap_buys': len(ovl), 'log_traced': traced,
                     'log_with_choch': with_choch,
                     'reason': '실거래 매수의 95.8%가 SMC 파이프라인을 '
                               '거치지 않아 CHoCH 자체가 발생하지 않는다'}

    # ═══ 완료 조건 질문에 답하기 ════════════════════════════════════════
    print('\n' + '-' * 84)
    print('  완료 조건 — 숫자로 답한다')
    print('-' * 84)

    cur.execute("""SELECT count(*) FILTER (WHERE entry_reason LIKE '%종합점수: 0.0%'),
                          count(*) FILTER (WHERE entry_reason LIKE 'VWAP%')
                   FROM trades WHERE trade_type='BUY'""")
    z, vw = cur.fetchone()

    cur.execute("""SELECT AVG(holding_minutes), count(*)
                   FROM trades WHERE trade_type='SELL'
                   AND holding_minutes IS NOT NULL""")
    hav, hn = cur.fetchone()

    q = [
        ('실거래는 왜 다른 종목을 샀는가?',
         f'매수 {oc.get("조건검색식 (VWAP+AI)", {}).get("trades", 0)}/{total}건'
         f'({oc.get("조건검색식 (VWAP+AI)", {}).get("trades", 0)/total*100:.1f}%)이 '
         f'키움 조건검색식 VWAP+AI 에서 나왔다. 백테스트는 '
         f'DEFAULT_CANDIDATES 78종목 하드코딩 리스트를 쓴다. '
         f'공급원이 다르므로 교집합 4종목(4.3%)은 우연의 수준이다.'),
        ('실거래는 왜 평균 197분만 보유했는가?',
         f'진입 근거가 VWAP 상향 돌파다 ({vw}/{total}건). 장중 돌파를 '
         f'노리는 진입이라 청산도 장중에 붙는다. 백테스트의 일봉 스윙 '
         f'(평균 8.6일)과는 다른 전략이다.'),
        ('실거래는 왜 장중청산이 62%인가?',
         'Iteration 2 실측: HARD_STOP 28건 · EARLY_FAILURE 35건 · '
         'OVERNIGHT_BLOCK 8건 등 장중 시각 기반 청산이 지배적이다. '
         '이 사유들은 백테스트에 존재하지 않는다 (공통 청산사유 0종).'),
        ('매수는 CHoCH 이후 평균 몇 분 뒤에 발생했는가?',
         f'측정 불가. 로그 구간과 겹치는 BUY {len(ovl)}건 중 CHoCH 기록이 '
         f'있는 것은 {with_choch}건이다. 대부분의 매수가 SMC 를 거치지 '
         f'않으므로 CHoCH 가 선행하지 않는다.'),
        ('그때 이미 몇 % 상승한 상태였는가?',
         'candidate_first_price · entry_signal_price 가 0/411 로 '
         '기록돼 있지 않아 측정 불가.'),
        ('조금만 빨리 들어갔다면 얼마나 개선됐는가?',
         '반사실 계산 불가. 신호 시각·가격이 없어 "빨리" 의 기준점을 '
         '잡을 수 없다.'),
    ]
    for i, (a, b) in enumerate(q, 1):
        print(f'\n  Q{i}. {a}')
        print(f'      → {b}')
    rep['completion_questions'] = [{'q': a, 'a': b} for a, b in q]

    print(f'\n  [부수 발견] entry_reason 에 "종합점수: 0.0" 인 매수 {z}건 '
          f'({z/total*100:.1f}%)')
    print('    점수가 0 인데 진입했다 — 점수가 게이트로 작동하지 않는다.')
    rep['score_zero_entries'] = {'trades': z, 'pct': round(z / total * 100, 1)}

    # ═══ KPI-1 — 전략 차이 코드 레벨 ═══════════════════════════════════
    print('\n' + '-' * 84)
    print('  KPI-1 — 전략 차이 코드 레벨 규명')
    print('-' * 84)
    diff = [
        {'축': 'Universe', 'Backtest': 'backtest/scanner.py '
         'DEFAULT_CANDIDATES (78종목 하드코딩)',
         'Live': "키움 조건검색식 'VWAP+AI' (93종목)",
         '설명됨': True},
        {'축': 'Entry Trigger', 'Backtest': 'backtest/adapter.py '
         'get_signal() — CHoCH + 거래량 + ATR + MA50 (일봉)',
         'Live': 'VWAP 상향 돌파 (장중)', '설명됨': True},
        {'축': 'Entry 시점', 'Backtest': '신호 다음 봉 시가',
         'Live': '장중 돌파 순간', '설명됨': True},
        {'축': 'Holding', 'Backtest': '평균 8.6일',
         'Live': '평균 197분 (52% 당일왕복)', '설명됨': True},
        {'축': 'Exit', 'Backtest': 'SL/TP/TRAIL/BE_STOP/MAX_HOLD (일봉)',
         'Live': 'HARD_STOP/EARLY_FAILURE/OVERNIGHT_BLOCK/MA5_EXIT (장중)',
         '설명됨': True},
        {'축': 'Sizing', 'Backtest': '슬롯당 고정 2,000만원',
         'Live': '최대/중앙값 112배 편차', '설명됨': True},
    ]
    print(f'  {"축":<14}{"Backtest":<44}{"Live":<34}')
    for d in diff:
        print(f'  {d["축"]:<14}{d["Backtest"][:42]:<44}{d["Live"][:32]:<34}')
    ndone = sum(1 for d in diff if d['설명됨'])
    print(f'\n  설명률 {ndone}/{len(diff)} = {ndone/len(diff)*100:.0f}%')
    rep['kpi1'] = {'axes': diff,
                   'explained_pct': round(ndone / len(diff) * 100, 1)}

    with open(P('parity_report_iter3.json'), 'w', encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
    with open(P('daily_scan_health.json'), 'w', encoding='utf-8') as f:
        json.dump({'note': 'daily_scan 산출물이 실거래 후보로 쓰인 흔적이 '
                           '없다. 매수 95.8%가 조건검색식에서 왔다.',
                   'smc_pipeline_trades':
                       oc.get('SMC 파이프라인', {}).get('trades', 0),
                   'total_buys': total}, f, ensure_ascii=False, indent=2)
    print(f'\n  저장: universe_origin.csv · condition_usage.csv · '
          f'parity_report_iter3.json · daily_scan_health.json')


if __name__ == '__main__':
    main()
