"""
tests/unit/test_ranking_determinism.py — WI3-B/E Ranking→Slot 순서보존 검증

`main_auto_trading.watchlist_rank_key(sym, score_map)` — Score DESC, 동점 Symbol ASC
tie-break 정렬키. watchlist 캡(:4347)과 메인 스캔 순회(:4361) 양쪽이 이 키로 정렬한다.
이 파일은 정렬키 자체가 deterministic한지, set() 변환 없이 순서가 보존되는지 검증한다
(실제 main_auto_trading.py 인스턴스화 없이 모듈레벨 순수함수만 테스트 — import 안전).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from main_auto_trading import watchlist_rank_key


def test_score_desc_order():
    scores = {'A': 1.0, 'B': 5.0, 'C': 3.0}
    ranked = sorted(scores, key=lambda s: watchlist_rank_key(s, scores))
    assert ranked == ['B', 'C', 'A']


def test_tie_break_symbol_asc():
    scores = {'005930': 5.0, '000660': 5.0, '005380': 5.0}
    ranked = sorted(scores, key=lambda s: watchlist_rank_key(s, scores))
    assert ranked == ['000660', '005380', '005930']


def test_unknown_symbol_sorts_last():
    """score_map에 없는 종목(예: DIAG_INJECT로 강제포함된 종목)은 최하위로 밀린다."""
    scores = {'A': 1.0, 'B': 2.0}
    symbols = ['A', 'B', 'UNKNOWN']
    ranked = sorted(symbols, key=lambda s: watchlist_rank_key(s, scores))
    assert ranked == ['B', 'A', 'UNKNOWN']


def test_repeated_sort_identical_result():
    """동일 입력을 10회 반복 정렬해도 항상 동일한 순서(비결정성 없음)."""
    scores = {f'SYM{i:03d}': float(i % 7) for i in range(30)}
    symbols = list(scores.keys())
    results = []
    for _ in range(10):
        # 매번 dict/list를 새로 만들어 파이썬 내부 해시 상태에 의존하지 않는지도 함께 확인
        fresh_scores = dict(scores)
        fresh_symbols = list(symbols)
        results.append(tuple(sorted(fresh_symbols, key=lambda s: watchlist_rank_key(s, fresh_scores))))
    assert len(set(results)) == 1, "동일 입력인데 반복 실행 결과가 달라짐 — 비결정적"


def test_set_conversion_would_lose_order_but_key_recovers_it():
    """set()에 넣으면 순서가 소실되지만, watchlist_rank_key로 재정렬하면 항상 같은
    (스코어 기준) 순서로 복원된다는 것을 확인 — 실제 코드의 캡/순회 로직과 동일한 패턴."""
    scores = {'005930': 9.0, '000660': 7.0, '005380': 3.0, '035720': 7.0}
    as_set = set(scores.keys())  # 순서 소실 시뮬레이션(main_auto_trading.py의 self.watchlist)
    ranked = sorted(as_set, key=lambda s: watchlist_rank_key(s, scores))
    assert ranked == ['005930', '000660', '035720', '005380']


def test_max_watchlist_size_cap_keeps_top_scores():
    """워치리스트 캡(max_watchlist_size)이 임의 종목이 아니라 스코어 상위 N개를 남기는지 확인."""
    scores = {f'SYM{i:03d}': float(30 - i) for i in range(30)}  # SYM000이 최고점
    as_set = set(scores.keys())
    capped = sorted(as_set, key=lambda s: watchlist_rank_key(s, scores))[:5]
    assert capped == ['SYM000', 'SYM001', 'SYM002', 'SYM003', 'SYM004']
