"""
Iteration 8-3 §8 — Order Traceability & Duplicate Prevention dry-run

━━━ 실거래 원장을 오염시키지 않는다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━

  검증용 종목코드 `ZZ` 접두를 쓰고, 끝나면 반드시 지운다.
  실패해도 지우도록 finally 에 넣는다.

  ⚠️ 원장에 TEST01/TEST99 가 남아 있는 전례가 있다 (Iteration 4 발견).
     같은 실수를 반복하지 않는다.

사용법:
    python -m phase1.order_traceability_test
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
from dotenv import load_dotenv

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(ROOT, '.env'))
OUT = os.path.dirname(os.path.abspath(__file__))

from database.trading_db import TradingDatabase

PREFIX = 'ZZTEST'


def conn():
    return psycopg2.connect(dbname='trading_system', user='postgres',
                            password=os.getenv('POSTGRES_PASSWORD'),
                            host='localhost')


def cleanup():
    c = conn()
    cur = c.cursor()
    cur.execute("DELETE FROM trades WHERE stock_code LIKE %s", (PREFIX + '%',))
    n = cur.rowcount
    c.commit()
    c.close()
    return n


def _trade(code, ttype='SELL', price=1000.0, qty=10, reason='TEST_EXIT',
           order_no=None, when=None):
    t = when or datetime.now()
    d = {'stock_code': code, 'stock_name': 'DRYRUN',
         'trade_type': ttype, 'trade_time': t.isoformat(),
         'price': price, 'quantity': qty,
         'amount': price * qty, 'exit_reason': reason}
    if order_no:
        d['order_no'] = order_no
    return d


def main():
    db = TradingDatabase()
    results, blocked = [], 0
    lat = {}
    try:
        cleanup()

        # ── Test 1  동일 EXIT 2회 호출 ──────────────────────────────────
        t0 = time.perf_counter()
        id1 = db.insert_trade(_trade(PREFIX + '1'))
        lat['insert_ms'] = (time.perf_counter() - t0) * 1000
        t0 = time.perf_counter()
        id2 = db.insert_trade(_trade(PREFIX + '1'))
        lat['dup_check_ms'] = (time.perf_counter() - t0) * 1000
        ok1 = id1 > 0 and id2 == 0
        blocked += 1 if id2 == 0 else 0
        results.append(('Test 1  동일 EXIT 2회 → 1건 저장 · 1건 차단', ok1,
                        f'1회차 trade_id={id1}, 2회차={id2}'))

        # ── Test 2  다른 가격 ───────────────────────────────────────────
        id3 = db.insert_trade(_trade(PREFIX + '1', price=1100.0))
        ok2 = id3 > 0
        results.append(('Test 2  다른 가격 EXIT → 정상 저장', ok2,
                        f'trade_id={id3}'))

        # ── Test 3  다른 종목 ───────────────────────────────────────────
        id4 = db.insert_trade(_trade(PREFIX + '2'))
        ok3 = id4 > 0
        results.append(('Test 3  다른 종목 EXIT → 정상 저장', ok3,
                        f'trade_id={id4}'))

        # ── Test 4  재시작 후 EXIT (프로세스 재연결 모사) ───────────────
        #
        # 중복 판단이 프로세스 메모리가 아니라 DB 에 있으므로
        # 새 커넥션에서도 막혀야 한다.
        db2 = TradingDatabase()
        id5 = db2.insert_trade(_trade(PREFIX + '1'))
        ok4 = id5 == 0
        blocked += 1 if id5 == 0 else 0
        results.append(('Test 4  재시작 후 동일 EXIT → 차단', ok4,
                        f'새 인스턴스 결과={id5} (DB 기준 판단)'))

        # ── Test 5  시간 창 밖이면 통과 ─────────────────────────────────
        #
        # ⚠️ 같은 종목을 같은 가격·수량으로 하루에 두 번 거래하는 것은
        #    정상이다. 창이 없으면 정상 거래를 막는다.
        old = datetime.now() - timedelta(seconds=db._DUP_WINDOW_SEC + 60)
        id6 = db.insert_trade(_trade(PREFIX + '1', when=old))
        ok5 = id6 > 0
        results.append((f'Test 5  {db._DUP_WINDOW_SEC}초 밖 동일 거래 → 통과',
                        ok5, f'trade_id={id6} (정상거래 차단 방지)'))

        # ── Test 6  order_no 보존 ───────────────────────────────────────
        id7 = db.insert_trade(_trade(PREFIX + '3', ttype='BUY',
                                     reason=None, order_no='0163513'))
        c = conn()
        cur = c.cursor()
        cur.execute("SELECT entry_context FROM trades WHERE trade_id=%s",
                    (id7,))
        ctx = cur.fetchone()[0]
        c.close()
        ok6 = bool(ctx) and ctx.get('order_no') == '0163513'
        results.append(('Test 6  BUY order_no → entry_context 보존', ok6,
                        json.dumps(ctx, ensure_ascii=False)))

        # ── Test 7  SELL 은 exit_order_no 로 ────────────────────────────
        id8 = db.insert_trade(_trade(PREFIX + '4', ttype='SELL',
                                     order_no='0163999'))
        c = conn()
        cur = c.cursor()
        cur.execute("SELECT entry_context FROM trades WHERE trade_id=%s",
                    (id8,))
        ctx2 = cur.fetchone()[0]
        c.close()
        ok7 = bool(ctx2) and ctx2.get('exit_order_no') == '0163999'
        results.append(('Test 7  SELL order_no → exit_order_no 보존', ok7,
                        json.dumps(ctx2, ensure_ascii=False)))

        # ── Test 8  기존 entry_context 를 버리지 않는가 ─────────────────
        d = _trade(PREFIX + '5', ttype='BUY', reason=None, order_no='0164000')
        d['entry_context'] = {'condition_sources': ['GreatMid']}
        id9 = db.insert_trade(d)
        c = conn()
        cur = c.cursor()
        cur.execute("SELECT entry_context FROM trades WHERE trade_id=%s",
                    (id9,))
        ctx3 = cur.fetchone()[0]
        c.close()
        ok8 = (ctx3.get('order_no') == '0164000'
               and ctx3.get('condition_sources') == ['GreatMid'])
        results.append(('Test 8  기존 entry_context 보존 + order_no 병합', ok8,
                        json.dumps(ctx3, ensure_ascii=False)))

    finally:
        removed = cleanup()

    # ── 커버리지 ────────────────────────────────────────────────────────
    c = conn()
    cur = c.cursor()
    cur.execute("""SELECT
        count(*) FILTER (WHERE trade_type='BUY'),
        count(*) FILTER (WHERE trade_type='SELL'),
        count(*) FILTER (WHERE trade_type='BUY'
              AND entry_context ? 'order_no'),
        count(*) FILTER (WHERE trade_type='SELL'
              AND entry_context ? 'exit_order_no')
        FROM trades""")
    nb, ns, nbo, nso = cur.fetchone()
    c.close()

    print('==============================')
    print('ORDER TRACEABILITY AUDIT')
    print('==============================')
    print()
    for name, ok, detail in results:
        print(f'{"✅ PASS" if ok else "❌ FAIL"}  {name}')
        print(f'          {detail}')
    print()
    print(f'BUY Records              {nb}')
    print(f'EXIT Records             {ns}')
    print(f'Order No Coverage %      BUY {nbo/nb*100:.1f}%  '
          f'EXIT {nso/ns*100:.1f}%   (기존 거래는 기록 없음)')
    print(f'Duplicate Detection Test {sum(1 for _,o,_ in results if o)}/'
          f'{len(results)} PASS')
    print(f'Duplicate Block Count    {blocked}')
    print(f'Regression Result        check_baseline.sh 참조')
    print(f'Runtime Error            0')
    print('==============================')
    print()
    print(f'  성능: INSERT {lat["insert_ms"]:.2f}ms · '
          f'중복검사 포함 {lat["dup_check_ms"]:.2f}ms '
          f'(증가 {lat["dup_check_ms"]-lat["insert_ms"]:+.2f}ms)')
    print(f'  검증 데이터 정리: {removed}행 삭제 (원장 오염 0)')

    with open(os.path.join(OUT, 'order_traceability_test.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'created_at': datetime.now().isoformat(timespec='seconds'),
                   'tests': [{'name': n, 'pass': o, 'detail': d}
                             for n, o, d in results],
                   'blocked': blocked, 'latency_ms': lat,
                   'coverage': {'buy': nb, 'sell': ns,
                                'buy_with_order_no': nbo,
                                'sell_with_order_no': nso},
                   'cleanup_rows': removed},
                  f, ensure_ascii=False, indent=2)
    if not all(o for _, o, _ in results):
        sys.exit(1)


if __name__ == '__main__':
    main()
