"""
tests/unit/test_regime_analyzer.py

analyzers/market/regime_analyzer.py — RegimeDecision.index_metrics 노출 회귀 테스트.

배경 (2026-07-27, Regime Gate E2->E3 Evidence 확보 작업): 기존엔 KOSPI/KOSDAQ
원시 종가/EMA20 값이 _score_index() 내부 지역변수로만 존재하고 어디에도
노출되지 않아 Decision Ledger에 "왜 차단됐는지"를 사후 재현할 수 없었음.
index_metrics 필드를 추가해 노출하되, 기존 스코어링/threshold 로직과
하위 호환성은 절대 깨지지 않아야 한다 — 이 테스트가 그 계약을 고정한다.
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analyzers.market.regime_analyzer import RegimeAnalyzer, RegimeDecision


def test_regime_decision_default_index_metrics_is_empty_dict():
    """index_metrics를 안 넘기고 만들어도(기존 호출부) 에러 없이 빈 dict 기본값."""
    d = RegimeDecision(
        regime='NEUTRAL', score=0, allow_new_entries=True,
        allowed_min_grade='A', size_multiplier=0.7, allow_rae=False,
    )
    assert d.index_metrics == {}


def test_make_decision_backward_compatible_without_index_metrics():
    """_make_decision(regime, score, reasons) — index_metrics 생략 시 기존 3-인자 호출도 그대로 동작."""
    analyzer = RegimeAnalyzer(api=None, config={})
    d = analyzer._make_decision('RISK_OFF', -4, ['KOSPI_below_EMA20'])
    assert d.regime == 'RISK_OFF'
    assert d.score == -4
    assert d.index_metrics == {}
    assert d.allow_new_entries is False  # RISK_OFF 기본 정책 불변 확인


def test_make_decision_carries_index_metrics_through():
    """index_metrics를 넘기면 RegimeDecision에 그대로 실림 (점수/정책 로직에는 영향 없음)."""
    analyzer = RegimeAnalyzer(api=None, config={})
    metrics = {
        'kospi':  {'close': 6647.0, 'ema20': 7245.9, 'ema20_prev': 7308.9},
        'kosdaq': {'close': 767.0,  'ema20': 818.1,  'ema20_prev': 823.5},
    }
    d = analyzer._make_decision('RISK_OFF', -4, ['KOSPI_below_EMA20'], metrics)
    assert d.index_metrics == metrics
    assert d.index_metrics['kospi']['close'] == 6647.0


def test_score_index_returns_three_tuple_with_metrics():
    """_score_index()는 (score, reasons, metrics) 3-tuple을 반환해야 함 (기존 2-tuple에서 확장)."""
    analyzer = RegimeAnalyzer(api=None, config={})
    # 데이터 조회 실패(api=None) 시에도 3-tuple 계약은 유지되어야 함
    score, reasons, metrics = analyzer._score_index('069500', 'KOSPI', 20, {})
    assert isinstance(score, int)
    assert isinstance(reasons, list)
    assert isinstance(metrics, dict)


def test_trend_up_and_neutral_policies_unchanged_by_index_metrics():
    """TREND_UP/NEUTRAL 분기의 기존 사이즈/등급 정책이 index_metrics 추가로 변하지 않았는지 확인."""
    analyzer = RegimeAnalyzer(api=None, config={
        'market_regime': {'trend_up_size_mult': 1.0, 'neutral_size_mult': 0.7,
                           'allow_rae_in_trend_up': True, 'allow_rae_in_neutral': False}
    })
    up = analyzer._make_decision('TREND_UP', 4, ['KOSPI_above_EMA20'], {'kospi': {'close': 100}})
    neutral = analyzer._make_decision('NEUTRAL', 0, [], {})

    assert up.allow_new_entries is True and up.allowed_min_grade == 'A'
    assert abs(up.size_multiplier - 1.0) < 1e-9
    assert neutral.allow_new_entries is True
    assert abs(neutral.size_multiplier - 0.7) < 1e-9
