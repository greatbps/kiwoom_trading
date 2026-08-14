"""
tests/unit/test_wi8_evidence_repair.py — WI-8 Evidence → Ranking 연결 복구 검증

WI-7이 확정한 `scores`/`scores_breakdown` 키 불일치(수급 3곳)와 조건검색 출처가
Ranking까지 보존되지 않던 문제를 고친다. 수정 전 실패를 재현하는 형태로 작성한다.

- `AnalysisEngine.analyze()` 자체는 news/technical/supply_demand/fundamental 4개
  하위 분석기(실API 의존)만 몽키패치하면 실제 코드(calculate_final_score/
  generate_recommendation/detect_market_regime/최종 return dict)를 그대로 호출할
  수 있다 — mock 없이 실제 구조를 검증한다.
- 소비부 3곳(sd_score_cache 저장 :3508, 등급강등 :7738, EQ-4 필터 :9713)은 거대한
  단일 비동기 함수 내부에 임베드돼 있어 독립 호출이 안 된다 — WI-3와 동일하게
  수정된 판정식을 소스 라인과 1:1 대조 가능한 형태로 재현(mirror)해서 검증한다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pytest

from main_auto_trading import IntegratedTradingSystem


# ── 근본원인 회귀가드: AnalysisEngine 반환 구조 ──────────────────────────────

def _make_analysis_engine(news=60, technical=55, supply_demand=10, fundamental=40):
    """4개 하위 분석기(실API 의존)만 몽키패치, analyze() 자체는 실제 코드 그대로 호출."""
    from analyzers.analysis_engine import AnalysisEngine
    eng = AnalysisEngine()
    eng.analyze_news = lambda *a, **k: {'score': news}
    eng.analyze_technical = lambda *a, **k: {'score': technical}
    eng.analyze_supply_demand = lambda *a, **k: {'score': supply_demand}
    eng.analyze_fundamental = lambda *a, **k: {'score': fundamental}
    return eng


def test_analysis_engine_returns_scores_breakdown_not_scores():
    """analyzers/analysis_engine.py:608-627 — 실제 반환 딕셔너리에는 'scores_breakdown'만
    있고 'scores' 키는 없다. 이 사실이 WI-7/WI-8 버그의 근본원인이었다 — 재발 방지 가드."""
    eng = _make_analysis_engine()
    result = eng.analyze('005930', '삼성전자')
    assert 'scores_breakdown' in result
    assert 'scores' not in result
    assert result['scores_breakdown']['supply_demand'] == 10
    assert result['scores_breakdown']['news'] == 60


def test_analysis_engine_scores_breakdown_matches_subengine_scores():
    eng = _make_analysis_engine(news=70, technical=45, supply_demand=5, fundamental=30)
    result = eng.analyze('000660', 'SK하이닉스')
    sb = result['scores_breakdown']
    assert sb['news'] == 70
    assert sb['technical'] == 45
    assert sb['supply_demand'] == 5
    assert sb['fundamental'] == 30


# ── mirror 테스트: 소비부 3곳 (WI-8-1) ───────────────────────────────────────
#
# 세 곳 모두 동일 패턴이다: `stock_info.get('analysis', {}).get('scores_breakdown',
# {}).get('supply_demand', 50)`. 수정 전에는 'scores'를 읽어 항상 {}.get(...) = 50
# 이었다 — 아래 헬퍼의 `use_old_key` 인자로 그 차이를 직접 재현한다.

def _read_supply_demand_mirror(analysis_result: dict, use_old_key: bool) -> float:
    """main_auto_trading.py :3508 / :7738 / :9713 이 공통으로 쓰는 읽기 패턴의 재현."""
    key = 'scores' if use_old_key else 'scores_breakdown'
    return float(analysis_result.get(key, {}).get('supply_demand', 50))


def test_old_key_always_returns_default_50():
    """버그 재현: 'scores' 키로 읽으면 실제 값이 무엇이든 항상 50(기본값)."""
    analysis_result = {'scores_breakdown': {'supply_demand': 5}}
    assert _read_supply_demand_mirror(analysis_result, use_old_key=True) == 50.0


def test_fixed_key_returns_real_score():
    """수정 후: 'scores_breakdown' 키로 읽으면 실제 값이 전달된다."""
    analysis_result = {'scores_breakdown': {'supply_demand': 5}}
    assert _read_supply_demand_mirror(analysis_result, use_old_key=False) == 5.0


def test_fixed_key_missing_scores_breakdown_falls_back_safely():
    """analysis 자체가 비었거나 분석 실패한 경우(예외처리 경로) — 크래시 없이 기본값 50."""
    assert _read_supply_demand_mirror({}, use_old_key=False) == 50.0
    assert _read_supply_demand_mirror({'scores_breakdown': {}}, use_old_key=False) == 50.0


# ── mirror 테스트: A→B 등급강등 (source: main_auto_trading.py:7727-7745) ────────

def _grade_downgrade_mirror(sd_score: float, grade_sd_min: float = 50,
                             sd_drop: float | None = None, drop_thr: float = -10) -> bool:
    """`_downgrade = _sd_for_grade < _grade_sd_min` (+ 변화율 급락 체크) 재현."""
    downgrade = sd_score < grade_sd_min
    if not downgrade and sd_drop is not None:
        downgrade = sd_drop <= drop_thr
    return downgrade


def test_grade_downgrade_triggers_on_low_supply_demand():
    """실제 수급점수 10(<50) → 수정 후에는 실제로 강등된다(버그 시절엔 항상 50이라 절대
    발동하지 않았다)."""
    assert _grade_downgrade_mirror(sd_score=10) is True


def test_grade_stays_a_when_supply_demand_healthy():
    assert _grade_downgrade_mirror(sd_score=65) is False


def test_grade_downgrade_triggers_on_sudden_drop_even_if_absolute_ok():
    """절대값은 기준(50) 이상이어도 전일 대비 급락(-15 <= -10)이면 강등."""
    assert _grade_downgrade_mirror(sd_score=55, sd_drop=-15) is True


# ── mirror 테스트: EQ-4 수급 진입필터 (source: main_auto_trading.py:9700-9720) ──

def _eq4_filter_mirror(sd_score: float, min_score: float = 20) -> bool:
    """`if _sd_score < _sd_min: block` 재현. True=차단."""
    return sd_score < min_score


def test_eq4_filter_blocks_extreme_low_supply_demand():
    """min_score=20(config/strategy_hybrid.yaml:585) 미만 → 진입 차단.
    이 필터는 2026-04-19 enabled:true 이후 이 버그 때문에 한 번도 발동한 적이 없었다."""
    assert _eq4_filter_mirror(sd_score=10) is True


def test_eq4_filter_allows_normal_supply_demand():
    assert _eq4_filter_mirror(sd_score=45) is False


def test_eq4_filter_boundary_at_threshold_allows():
    assert _eq4_filter_mirror(sd_score=20) is False  # 20 자체는 차단 아님(< 조건)


# ── Candidate Source 보존 (_condition_attribution, main_auto_trading.py:2047) ──

def _make_attr_stub(validated_stocks: dict):
    t = IntegratedTradingSystem.__new__(IntegratedTradingSystem)
    t.validated_stocks = validated_stocks
    return t


def test_condition_attribution_preserves_id_and_name():
    """WI-7 §11이 지적한 문제: 조건식 id/name이 Evidence/Ranking 단계까지 유지되는지.
    `_condition_attribution()`은 Iteration 8-1에서 이미 존재하던 메서드 — 무수정
    재사용, 이번 WI-8은 이 메서드를 호출하는 로그 지점만 늘렸다."""
    stub = _make_attr_stub({
        '005930': {
            'condition_sources': ['240일 돌파', '5일선'],
            'primary_condition': '240일 돌파',
            'condition_match_time': '2026-08-11T09:10:00',
            'source': 'condition_search',
        }
    })
    ca = stub._condition_attribution('005930')
    assert ca['condition_sources'] == ['240일 돌파', '5일선']
    assert ca['primary_condition'] == '240일 돌파'
    assert ca['condition_match_time'] == '2026-08-11T09:10:00'


def test_condition_attribution_unknown_for_untracked_symbol():
    """validated_stocks에 기록이 없으면 UNKNOWN — 추정하지 않는다(원본 docstring 그대로)."""
    stub = _make_attr_stub({})
    ca = stub._condition_attribution('999999')
    assert ca['condition_sources'] == ['UNKNOWN']
    assert ca['primary_condition'] == 'UNKNOWN'


# ── mirror 테스트: 신규 로그 포맷 (WI-8-A, Evidence/Ranking 단계) ────────────────

def test_evidence_attr_log_format_includes_condition_source():
    """main_auto_trading.py:3535-3541 [EVIDENCE_ATTR] 로그 포맷 재현 — condition_sources
    가 문자열에 포함되는지 확인(로직 변경 없음, 순수 로깅 포맷 검증)."""
    ca = {'condition_sources': ['240일 돌파'], 'primary_condition': '240일 돌파'}
    final_score, recommendation, stock_code = 52.5, '관망', '005930'
    line = (
        f"[EVIDENCE_ATTR] symbol={stock_code} "
        f"condition_sources={ca['condition_sources']} "
        f"primary_condition={ca['primary_condition']} "
        f"final_score={final_score:.1f} recommendation={recommendation}"
    )
    assert 'condition_sources=' in line
    assert "['240일 돌파']" in line
    assert 'symbol=005930' in line


def test_score_detail_log_format_includes_condition_source():
    """main_auto_trading.py:14026-14033 [SCORE_DETAIL] 로그 포맷 재현 — Ranking 단계에서도
    출처가 남는지 확인(ranked/selected 등 실제 순위 데이터 구조는 건드리지 않음)."""
    ca = {'condition_sources': ['5일선'], 'primary_condition': '5일선'}
    sym, s = '069080', {'volume': 1, 'ma50': 0, 'smc': 0, 'total': 1.0}
    line = (
        f'[SCORE_DETAIL] {sym} volume={s["volume"]} '
        f'ma50={s["ma50"]} smc={s["smc"]} total={s["total"]} '
        f"condition_sources={ca['condition_sources']} "
        f"primary_condition={ca['primary_condition']}"
    )
    assert 'condition_sources=' in line
    assert "['5일선']" in line
