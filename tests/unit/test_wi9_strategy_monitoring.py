"""
tests/unit/test_wi9_strategy_monitoring.py — WI-9 Strategy Attribution 검증

seq(HTS 조건식 고정 ID, WI-9 §2가 공식 식별자로 지정)가 Candidate→Evidence→Gate→
Slot→Order 전 구간에서 정확히 보존되는지 확인한다. `_condition_attribution()`
(Iteration 8-1, WI-8/WI-9에서 확장)을 실제 메서드로 직접 호출해 검증한다
(`IntegratedTradingSystem.__new__()` 스텁 패턴, WI-3/WI-8과 동일).
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from main_auto_trading import IntegratedTradingSystem

SEQ_STRATEGIES = {
    32: 'Momentum', 33: 'Breakout', 34: 'EOD',
    35: 'Supertrend + EMA + RSI', 36: 'VWAP',
    37: 'Squeeze Momentum Pro', 38: 'Bottom', 39: 'ITS',
}


def _make_stub(validated_stocks: dict):
    t = IntegratedTradingSystem.__new__(IntegratedTradingSystem)
    t.validated_stocks = validated_stocks
    return t


# ── seq 32~39 Attribution 왕복 (WI-9 §14 항목 1~8) ──────────────────────────
@pytest.mark.parametrize('seq,name', sorted(SEQ_STRATEGIES.items()))
def test_seq_attribution_roundtrip(seq, name):
    sym = f'SIM{seq}'
    stub = _make_stub({
        sym: {
            'condition_sources': [name],
            'primary_condition': name,
            'condition_match_time': '2026-08-11T09:10:00',
            'strategy_seqs': [seq],
            'primary_strategy_seq': seq,
        }
    })
    ca = stub._condition_attribution(sym)
    assert ca['primary_strategy_seq'] == seq
    assert ca['strategy_seqs'] == [seq]
    assert ca['primary_condition'] == name


# ── 항목 9: Unknown seq 안전 차단/기록 ───────────────────────────────────────
def test_unknown_symbol_returns_unknown_strategy_seq():
    """기록이 없는 종목은 추정하지 않고 UNKNOWN — 기존 condition_sources 정책과 동일
    (main_auto_trading.py:2047-2064 docstring: '추정하지 않는다')."""
    stub = _make_stub({})
    ca = stub._condition_attribution('999999')
    assert ca['primary_strategy_seq'] == 'UNKNOWN'
    assert ca['strategy_seqs'] == ['UNKNOWN']


# ── 항목 10: 전략 Attribution 누락 감지 ──────────────────────────────────────
def test_missing_strategy_seqs_field_falls_back_to_unknown_not_crash():
    """condition_sources는 있지만 strategy_seqs가 누락된 레거시 레코드(예: WI-8 이전
    데이터) — 크래시 없이 UNKNOWN으로 안전 폴백해야 한다."""
    stub = _make_stub({
        '005930': {
            'condition_sources': ['알고리즘추출_1110'],
            'primary_condition': '알고리즘추출_1110',
            'condition_match_time': '2026-07-28T09:10:00',
            # strategy_seqs / primary_strategy_seq 없음 (구버전 레코드 시뮬레이션)
        }
    })
    ca = stub._condition_attribution('005930')
    assert ca['primary_strategy_seq'] == 'UNKNOWN'
    assert ca['strategy_seqs'] == ['UNKNOWN']
    # 기존 필드(condition_sources)는 영향받지 않는다
    assert ca['primary_condition'] == '알고리즘추출_1110'


# ── 항목 11: 전략 간 Attribution 혼합 방지 ───────────────────────────────────
def test_no_cross_contamination_between_symbols():
    """종목 A(seq32/33 중복)와 종목 B(seq38)가 같은 validated_stocks 안에 있어도
    서로의 attribution이 섞이지 않는다 — dict 키가 stock_code 기준임을 확인."""
    stub = _make_stub({
        'AAA': {
            'condition_sources': ['Momentum', 'Breakout'],
            'primary_condition': 'Momentum',
            'strategy_seqs': [32, 33],
            'primary_strategy_seq': 32,
        },
        'BBB': {
            'condition_sources': ['Bottom'],
            'primary_condition': 'Bottom',
            'strategy_seqs': [38],
            'primary_strategy_seq': 38,
        },
    })
    ca_a = stub._condition_attribution('AAA')
    ca_b = stub._condition_attribution('BBB')
    assert ca_a['strategy_seqs'] == [32, 33]
    assert ca_b['strategy_seqs'] == [38]
    assert ca_a['primary_strategy_seq'] != ca_b['primary_strategy_seq']
    # 서로의 seq가 상대방 attribution에 나타나지 않는다
    assert 38 not in ca_a['strategy_seqs']
    assert 32 not in ca_b['strategy_seqs'] and 33 not in ca_b['strategy_seqs']


# ── 항목 12: 기존 Pipeline Regression — 소스 구조 특성화 테스트 ────────────────
def test_cond_seqs_accumulation_pattern_present_in_source():
    """`_cond_sources`(WI-8) 옆에 `_cond_seqs`가 동일 패턴(setdefault+append)으로
    양쪽 분기(Bottom/Momentum) 모두에 존재하는지 — WI-3/WI-8 방식의 소스 특성화
    회귀가드(tests/unit/test_condition_attribution.py와 동일 기법)."""
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    src = open(os.path.join(root, 'main_auto_trading.py'), encoding='utf-8').read()
    assert src.count('_cond_seqs.setdefault(stock_code, []).append(cond_seq)') == 2, (
        'Bottom / Momentum 두 분기 모두에서 seq를 누적해야 한다'
    )
    assert "'strategy_seqs': list(_cond_seqs.get(stock_code, []))" in src
    assert "'primary_strategy_seq': (_cond_seqs.get(stock_code) or [None])[0]" in src
