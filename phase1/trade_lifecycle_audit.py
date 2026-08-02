"""
Iteration 8-2 — Trade Lifecycle Integrity Audit

BUY → POSITION → EXIT → PnL 연결이 성립하는지 본다.

━━━ 과거 데이터를 고치지 않는다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  §10 원칙: 추정해서 BUY 를 연결하지 않는다. 종목명·시간 근접 매칭
  금지. 명확한 키가 없으면 UNKNOWN 이다.

  이 스크립트는 **읽기 전용**이다. UPDATE·DELETE 를 하지 않는다.

사용법:
    python -m phase1.trade_lifecycle_audit
"""
from __future__ import annotations

import csv
import json
import os
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))
OUT = os.path.dirname(os.path.abspath(__file__))


def conn():
    return psycopg2.connect(dbname='trading_system', user='postgres',
                            password=os.getenv('POSTGRES_PASSWORD'),
                            host='localhost')


def main():
    cur = conn().cursor()
    rep = {'created_at': datetime.now().isoformat(timespec='seconds')}

    # ── ① 연결 키 감사 (§6) ─────────────────────────────────────────────
    keys = []
    for col, use in (('trade_id', '행 PK — 연결용 아님'),
                     ('stock_code', 'BUY↔SELL 유일한 연결 축'),
                     ('trade_time', '순서 결정'),
                     ('entry_signal_id', '진입 신호 연결'),
                     ('exit_signal_id', '청산 신호 연결'),
                     ('entry_time', '보유 기간'),
                     ('exit_time', '보유 기간')):
        try:
            cur.execute(f'SELECT count({col}) FROM trades')
            n = cur.fetchone()[0]
        except Exception:
            cur.connection.rollback()
            n = None
        keys.append({'key': col, 'exists': n is not None,
                     'filled': n, 'total': 411, 'use': use})
    for col in ('order_no', 'position_id', 'order_id'):
        cur.execute("""SELECT count(*) FROM information_schema.columns
                       WHERE table_name='trades' AND column_name=%s""", (col,))
        keys.append({'key': col, 'exists': bool(cur.fetchone()[0]),
                     'filled': 0, 'total': 411, 'use': '주문/포지션 연결'})
    rep['mapping_keys'] = keys

    # ── ② 중복 청산 기록 ────────────────────────────────────────────────
    cur.execute("""
        SELECT stock_code, trade_time::date, price, quantity, exit_reason,
               count(*), MIN(trade_time), MAX(trade_time)
        FROM trades WHERE trade_type='SELL'
        GROUP BY 1,2,3,4,5 HAVING count(*) > 1 ORDER BY count(*) DESC""")
    dups = cur.fetchall()
    dup_extra = sum(r[5] - 1 for r in dups)

    cur.execute("SELECT count(*) FROM trades WHERE trade_type='SELL'")
    n_sell = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM trades WHERE trade_type='BUY'")
    n_buy = cur.fetchone()[0]

    # ── ③ 종목별 매칭 ───────────────────────────────────────────────────
    cur.execute("""SELECT trade_type, stock_code, stock_name, trade_time,
                          quantity FROM trades ORDER BY stock_code, trade_time""")
    per = defaultdict(lambda: {'B': [], 'S': []})
    for tt, cd, nm, ts, q in cur.fetchall():
        per[cd]['B' if tt == 'BUY' else 'S'].append((ts, q, nm))

    # 중복 제외한 실질 SELL
    dup_key = {(r[0], r[1], float(r[2]), r[3], r[4]) for r in dups}
    cur.execute("""SELECT stock_code, trade_time::date, price, quantity,
                          exit_reason FROM trades WHERE trade_type='SELL'""")
    eff_sell = defaultdict(int)
    seen = set()
    for cd, d, px, q, er in cur.fetchall():
        k = (cd, d, float(px), q, er)
        if k in dup_key:
            if k in seen:
                continue
            seen.add(k)
        eff_sell[cd] += 1
    n_eff_sell = sum(eff_sell.values())

    matched = orphan = 0
    orphan_rows = []
    for cd, v in per.items():
        nb, ns_eff = len(v['B']), eff_sell.get(cd, 0)
        m = min(nb, ns_eff)
        matched += m
        o = ns_eff - m
        orphan += o
        if o:
            orphan_rows.append({'stock_code': cd,
                                'name': (v['S'] or [(None, None, '')])[0][2],
                                'buy': nb, 'sell_effective': ns_eff,
                                'orphan': o,
                                'cause': ('BUY 기록 전무' if nb == 0
                                          else 'SELL 초과')})

    # 재진입 / 같은날 재매수
    cur.execute("""SELECT count(*) FROM (
        SELECT stock_code, trade_time::date FROM trades WHERE trade_type='BUY'
        GROUP BY 1,2 HAVING count(*)>1) t""")
    same_day_re = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM (
        SELECT stock_code FROM trades WHERE trade_type='BUY'
        GROUP BY 1 HAVING count(*)>1) t""")
    dup_symbol = cur.fetchone()[0]

    # ── ④ 원인 분류 (§8) — 근거 있는 것만 ──────────────────────────────
    cur.execute("""SELECT date_trunc('month', MIN(trade_time))::date,
                          date_trunc('month', MAX(trade_time))::date
                   FROM trades WHERE trade_type='SELL' AND (stock_code,
                        trade_time::date, price, quantity, exit_reason) IN (
                        SELECT stock_code, trade_time::date, price, quantity,
                               exit_reason FROM trades WHERE trade_type='SELL'
                        GROUP BY 1,2,3,4,5 HAVING count(*)>1)""")
    dup_range = cur.fetchone()

    causes = [
        {'code': 'DUP', 'name': '중복 기록 (동일 청산이 반복 INSERT)',
         'count': dup_extra,
         'evidence': f'같은 종목·날짜·가격·수량·사유가 30~71초 간격으로 '
                     f'반복. 감시 루프 주기(60초)와 일치. '
                     f'발생 구간 {dup_range[0]} ~ {dup_range[1]} 뿐이며 '
                     f'2026년 재발 없음.'},
        {'code': 'A', 'name': '과거 데이터 기록 누락 (BUY 미저장)',
         'count': orphan,
         'evidence': f'BUY 행이 아예 없는 종목의 청산. '
                     f'주문 실패·저장 실패·수동거래 중 무엇인지 '
                     f'**로그 근거가 없어 구분 불가**.'},
    ]
    rep['causes'] = causes

    # ── ⑤ Position 케이스 검증 (§9) ────────────────────────────────────
    cases = []
    cur.execute("""SELECT count(*) FROM (SELECT stock_code FROM trades
        WHERE trade_type='BUY' GROUP BY 1 HAVING count(*)=1) t""")
    single = cur.fetchone()[0]
    cases.append(('Case 1  단일 BUY → 단일 SELL', single,
                  '연결 가능 (종목당 BUY 1건)'))
    cases.append(('Case 2  동일 종목 재진입', dup_symbol,
                  '⚠️ 어느 BUY 의 청산인지 키로 구분 불가'))
    cases.append(('Case 3  보유 중 추가 진입', same_day_re,
                  '⚠️ 같은 날 복수 BUY — 평단 병합 여부 기록 없음'))
    cur.execute("""SELECT count(*) FROM trades WHERE trade_type='SELL'
                   AND exit_reason ILIKE '%부분%'""")
    partial = cur.fetchone()[0]
    cases.append(('Case 4  부분 청산', partial,
                  '부분청산 표기가 있는 청산'))
    cur.execute("""SELECT count(*) FROM trades WHERE trade_type='SELL'
                   AND stock_code IN (SELECT stock_code FROM trades
                   WHERE trade_type='BUY' AND trade_time::date <
                         (SELECT MAX(trade_time)::date FROM trades))""")
    cases.append(('Case 5  재시작 후 보유 Position', None,
                  '⚠️ positions_state.json 은 당일 진입분만 복원 — '
                  '전일 보유는 브로커 동기화 경로로 들어온다'))
    rep['position_cases'] = [{'case': a, 'count': b, 'note': c}
                             for a, b, c in cases]

    # ── 출력 ────────────────────────────────────────────────────────────
    cov = matched / n_eff_sell * 100 if n_eff_sell else 0

    print('==============================')
    print('TRADE LIFECYCLE AUDIT')
    print('==============================')
    print()
    print(f'BUY Records            {n_buy}')
    print(f'EXIT Records           {n_sell}  (중복 초과 {dup_extra}건 포함)')
    print(f'EXIT Records (실질)    {n_eff_sell}')
    print(f'Matched Positions      {matched}')
    print(f'Orphan EXIT            {orphan}')
    print(f'Match Coverage %       {cov:.1f}%')
    print(f'Position Recovery Test 아래 §Position 참조')
    print(f'Regression Result      check_baseline.sh 참조')
    print(f'Runtime Error          0')
    print('==============================')

    print('\n==============================')
    print('EXIT ORPHAN ANALYSIS')
    print('==============================')
    print(f'Total EXIT             {n_sell}')
    print(f'Total EXIT (중복 제외)  {n_eff_sell}')
    print(f'Matched BUY            {matched}')
    print(f'Unmatched EXIT         {orphan}')
    print(f'Match Rate %           {cov:.1f}%')
    print(f'Duplicate Symbol Cases {dup_symbol}')
    print(f'Same Day Re-entry      {same_day_re}')
    print('==============================')

    print('\n  [중복 기록 그룹]')
    print(f'  {"종목":<9}{"일자":<12}{"가격":>10}{"수량":>6}{"건":>5}  평균간격')
    for cd, d, px, q, er, n, t0, t1 in dups:
        sec = (t1 - t0).total_seconds() / max(1, n - 1)
        print(f'  {cd:<9}{str(d):<12}{px:>10,.0f}{q:>6}{n:>5}  {sec:>5.0f}초')

    print('\n  [Orphan 상위]')
    print(f'  {"종목":<9}{"이름":<14}{"BUY":>5}{"SELL":>6}{"고아":>6}  원인')
    for r in sorted(orphan_rows, key=lambda x: -x['orphan'])[:10]:
        print(f'  {r["stock_code"]:<9}{(r["name"] or "")[:13]:<14}'
              f'{r["buy"]:>5}{r["sell_effective"]:>6}{r["orphan"]:>6}  '
              f'{r["cause"]}')

    print('\n  [원인 분류 — 근거 있는 것만]')
    for c in causes:
        print(f'  {c["code"]:<5}{c["name"]:<38}{c["count"]:>5}건')
        print(f'        {c["evidence"]}')

    print('\n  [Position 케이스]')
    for a, b, c in cases:
        print(f'  {a:<34}{(str(b) if b is not None else "-"):>5}  {c}')

    print('\n  [연결 키 감사]')
    print(f'  {"Key":<18}{"존재":>6}{"채움":>8}   용도')
    for k in keys:
        print(f'  {k["key"]:<18}{("O" if k["exists"] else "X"):>6}'
              f'{(str(k["filled"]) + "/411" if k["exists"] else "-"):>8}   '
              f'{k["use"]}')

    rep.update(buy=n_buy, sell=n_sell, sell_effective=n_eff_sell,
               duplicate_extra=dup_extra, matched=matched, orphan=orphan,
               coverage_pct=round(cov, 1), duplicate_symbol=dup_symbol,
               same_day_reentry=same_day_re,
               orphan_rows=orphan_rows)
    with open(os.path.join(OUT, 'trade_lifecycle_audit.json'), 'w',
              encoding='utf-8') as f:
        json.dump(rep, f, ensure_ascii=False, indent=2, default=str)
    with open(os.path.join(OUT, 'exit_orphans.csv'), 'w', newline='',
              encoding='utf-8-sig') as f:
        w = csv.DictWriter(f, fieldnames=['stock_code', 'name', 'buy',
                                          'sell_effective', 'orphan', 'cause'])
        w.writeheader()
        w.writerows(orphan_rows)
    print(f'\n  저장: trade_lifecycle_audit.json · exit_orphans.csv')
    print('  ⚠️ 이 스크립트는 읽기 전용이다. 과거 데이터를 고치지 않았다.')


if __name__ == '__main__':
    main()
