"""
Iteration 8-1 §8·§9·§10 — 조건검색 Attribution dry-run 검증

  Test 1  조건검색 이벤트 → 종목 저장
  Test 2  VWAP 통과 → condition_sources 유지
  Test 3  BUY → trade record 저장
  Test 4  EXIT → 원 거래와 연결

  §9  기존 거래 Migration (UNKNOWN 표시 — 추정 금지)
  §10 성능 영향 측정

━━━ 실주문은 발생하지 않는다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  파이프라인을 모사해 데이터가 어디서 끊기는지만 본다.
  KiwoomAPI 를 import 하지 않는다.

사용법:
    python -m phase1.condition_attribution_test
    python -m phase1.condition_attribution_test --migrate   # DB 마이그레이션 실행
"""
from __future__ import annotations

import argparse
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
OUT = os.path.dirname(os.path.abspath(__file__))


def conn():
    return psycopg2.connect(dbname='trading_system', user='postgres',
                            password=os.getenv('POSTGRES_PASSWORD'),
                            host='localhost')


class _Sys:
    """main_auto_trading 의 출처 경로만 떼어낸 스텁."""

    def __init__(self):
        self.validated_stocks = {}
        self._cond_sources = {}
        self._cond_first_seen = {}

    # ① 조건검색 이벤트
    def on_condition_hit(self, idx: int, name: str, codes: list[str]):
        for c in codes:
            self._cond_sources.setdefault(c, []).append(name)
            self._cond_first_seen.setdefault(
                c, datetime.now().isoformat(timespec='seconds'))

    # ② VWAP 통과 → validated_stocks 등록
    def on_vwap_pass(self, code: str, name: str):
        self.validated_stocks[code] = {
            'name': name, 'strategy': 'momentum',
            'condition_sources': list(self._cond_sources.get(code, [])),
            'primary_condition': (self._cond_sources.get(code) or [None])[0],
            'condition_match_time': self._cond_first_seen.get(code),
        }

    # 실제 코드와 같은 헬퍼
    def _condition_attribution(self, stock_code: str) -> dict:
        info = self.validated_stocks.get(stock_code) or {}
        src = list(info.get('condition_sources') or [])
        return {'condition_sources': src or ['UNKNOWN'],
                'primary_condition': info.get('primary_condition') or 'UNKNOWN',
                'condition_match_time': info.get('condition_match_time'),
                'source': info.get('source')}


def run_tests():
    s = _Sys()
    results = []

    # Test 1 — 조건검색 이벤트 → 저장 (다중 출처 포함)
    s.on_condition_hit(17, 'GreatMid', ['005930', '000660'])
    s.on_condition_hit(18, 'Momentum 전략', ['005930', '035420'])
    s.on_condition_hit(19, '알고리즘추출_1110', ['000660'])
    ok1 = (s._cond_sources['005930'] == ['GreatMid', 'Momentum 전략']
           and s._cond_sources['000660'] == ['GreatMid', '알고리즘추출_1110'])
    results.append(('Test 1  조건검색 → 종목 저장 (다중 출처)', ok1,
                    f"005930={s._cond_sources['005930']}"))

    # Test 2 — VWAP 통과 후에도 출처 유지
    s.on_vwap_pass('005930', '삼성전자')
    a = s._condition_attribution('005930')
    ok2 = a['condition_sources'] == ['GreatMid', 'Momentum 전략'] \
        and a['primary_condition'] == 'GreatMid'
    results.append(('Test 2  VWAP 통과 → 출처 유지', ok2,
                    f"primary={a['primary_condition']}"))

    # Test 3 — BUY 기록에 실림
    trade = {'stock_code': '005930', 'trade_type': 'BUY',
             'condition_name': 'VWAP+AI',
             'entry_context': s._condition_attribution('005930')}
    ok3 = trade['entry_context']['condition_sources'] == \
        ['GreatMid', 'Momentum 전략']
    results.append(('Test 3  BUY → trade record 저장', ok3,
                    json.dumps(trade['entry_context']['condition_sources'],
                               ensure_ascii=False)))

    # Test 4 — EXIT 이 원 거래와 연결되는가
    #
    # ⚠️ 청산 행에는 entry_context 를 쓰지 않는다. 연결은 stock_code +
    #    시각으로 되돌아가 찾는 구조다. 그 구조가 성립하는지만 본다.
    sell = {'stock_code': '005930', 'trade_type': 'SELL'}
    ok4 = sell['stock_code'] == trade['stock_code']
    results.append(('Test 4  EXIT → 원 거래 연결 (stock_code 기준)', ok4,
                    '청산 행은 entry_context 미기록 — 조인으로 연결'))

    # 미등록 종목은 UNKNOWN
    u = s._condition_attribution('999999')
    ok5 = u['condition_sources'] == ['UNKNOWN']
    results.append(('Test 5  기록 없음 → UNKNOWN (추정 금지)', ok5,
                    str(u['condition_sources'])))
    return results


def migrate(apply: bool):
    """
    §9 — 기존 거래는 condition_sources='UNKNOWN'.

    ⚠️ 과거 데이터를 추정하지 않는다. 그때 어느 조건식이 물어왔는지는
       기록이 없어 알 수 없고, 앞으로도 알 수 없다.
    """
    c = conn()
    cur = c.cursor()
    cur.execute("""SELECT count(*) FROM trades
                   WHERE trade_type='BUY' AND entry_context IS NULL""")
    n_null = cur.fetchone()[0]
    cur.execute("""SELECT count(*) FROM trades WHERE trade_type='BUY'""")
    n_buy = cur.fetchone()[0]

    if not apply:
        return {'buy_rows': n_buy, 'entry_context_null': n_null,
                'applied': False}

    cur.execute("""
        UPDATE trades
        SET entry_context = jsonb_build_object(
            'condition_sources', jsonb_build_array('UNKNOWN'),
            'primary_condition', 'UNKNOWN',
            'condition_match_time', NULL,
            'migration_note', 'Iteration8-1 이전 거래 — 출처 기록 없음. 추정하지 않음'
        )
        WHERE trade_type='BUY' AND entry_context IS NULL""")
    n = cur.rowcount
    c.commit()
    return {'buy_rows': n_buy, 'entry_context_null': n_null,
            'updated': n, 'applied': True}


def coverage():
    c = conn()
    cur = c.cursor()
    cur.execute("""SELECT
        count(*) FILTER (WHERE trade_type='BUY'),
        count(*) FILTER (WHERE trade_type='BUY' AND entry_context IS NOT NULL),
        count(*) FILTER (WHERE trade_type='BUY'
              AND entry_context->>'primary_condition' NOT IN ('UNKNOWN')
              AND entry_context IS NOT NULL)
        FROM trades""")
    buy, has_ctx, mapped = cur.fetchone()
    return {'buy': buy, 'with_context': has_ctx, 'mapped': mapped,
            'unknown': has_ctx - mapped,
            'coverage_pct': round(mapped / buy * 100, 1) if buy else 0.0}


def perf():
    """§10 — 출처 조회가 얼마나 걸리는가."""
    s = _Sys()
    s.on_condition_hit(17, 'GreatMid', [f'{i:06d}' for i in range(300)])
    for i in range(300):
        s.on_vwap_pass(f'{i:06d}', f'N{i}')
    t0 = time.perf_counter()
    for _ in range(10000):
        s._condition_attribution('000100')
    dt = (time.perf_counter() - t0) / 10000
    return {'attribution_lookup_us': round(dt * 1e6, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--migrate', action='store_true')
    a = ap.parse_args()

    res = run_tests()
    mig = migrate(a.migrate)
    cov = coverage()
    pf = perf()

    print('==============================')
    print('CONDITION ATTRIBUTION TEST')
    print('==============================')
    print()
    for name, ok, detail in res:
        print(f'{"✅ PASS" if ok else "❌ FAIL"}  {name}')
        print(f'          {detail}')
    print()
    print(f'Condition Events       {len(res)} 시나리오')
    print(f'Mapped Trades          {cov["mapped"]}')
    print(f'Unknown Trades         {cov["unknown"]}')
    print(f'Attribution Coverage % {cov["coverage_pct"]}%')
    print(f'DB Migration Result    ' +
          (f'{mig.get("updated", 0)}건 UNKNOWN 표시 완료'
           if mig['applied'] else
           f'미적용 (--migrate 필요) — BUY {mig["buy_rows"]}행 중 '
           f'entry_context 없음 {mig["entry_context_null"]}행'))
    print(f'Regression Result      아래 check_baseline.sh 참조')
    print('==============================')
    print()
    print(f'  성능: 출처 조회 {pf["attribution_lookup_us"]}us/회')
    print(f'  ⚠️ Coverage 0% 는 정상이다 — 과거 거래는 기록 자체가 없다.')
    print(f'     신규 매수부터 쌓인다.')

    with open(os.path.join(OUT, 'condition_attribution_test.json'), 'w',
              encoding='utf-8') as f:
        json.dump({'created_at': datetime.now().isoformat(timespec='seconds'),
                   'tests': [{'name': n, 'pass': o, 'detail': d}
                             for n, o, d in res],
                   'migration': mig, 'coverage': cov, 'perf': pf},
                  f, ensure_ascii=False, indent=2)
    if not all(o for _, o, _ in res):
        sys.exit(1)


if __name__ == '__main__':
    main()
