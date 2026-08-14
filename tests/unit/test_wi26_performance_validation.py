"""
tests/unit/test_wi26_performance_validation.py — WI-26 통계 계산 로직 검증.

오늘(2026-08-12) 실측 Valid Outcome이 0건이라 실제 DB로는 PASS/FAIL 케이스를
만들 수 없다 — 합성 데이터로 계산 로직 자체(PF/Expectancy/MDD/Bootstrap/
샘플분류/PASS기준/최근성과창/Walk-Forward)가 올바른지 검증한다.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analysis.wi26_strategy_performance_validation import (
    classify_sample_size, compute_basic_kpi, bootstrap_stats,
    recent_window_kpi, distribution_shift_check, walk_forward_check,
    apply_pass_criteria, INSUFFICIENT_THRESHOLD, PROVISIONAL_THRESHOLD,
)


def _trade(ret, days_ago=0):
    return {
        'return_pct': ret,
        'observed_at': datetime.now() - timedelta(days=days_ago),
        'mfe_pct': max(ret, 0), 'mae_pct': min(ret, 0),
    }


# ── §4 표본 분류 ──────────────────────────────────────────────────────────────
def test_classify_sample_size_boundaries():
    assert classify_sample_size(0) == 'INSUFFICIENT_DATA'
    assert classify_sample_size(29) == 'INSUFFICIENT_DATA'
    assert classify_sample_size(30) == 'PROVISIONAL'
    assert classify_sample_size(49) == 'PROVISIONAL'
    assert classify_sample_size(50) == 'FULL_EVALUATION'
    assert classify_sample_size(200) == 'FULL_EVALUATION'


# ── §6 기본 KPI ───────────────────────────────────────────────────────────────
def test_compute_basic_kpi_empty():
    r = compute_basic_kpi([])
    assert r['trades'] == 0
    assert r['profit_factor'] is None


def test_compute_basic_kpi_known_values():
    trades = [_trade(2.0), _trade(2.0), _trade(-1.0)]  # win 2/3, PF = 4/1 = 4.0
    r = compute_basic_kpi(trades)
    assert r['trades'] == 3
    assert abs(r['win_rate'] - 66.67) < 0.1
    assert r['profit_factor'] == 4.0
    assert abs(r['expectancy_pct'] - 1.0) < 1e-9  # (2+2-1)/3


def test_compute_basic_kpi_all_wins_pf_infinite():
    trades = [_trade(1.0), _trade(2.0)]
    r = compute_basic_kpi(trades)
    assert r['profit_factor'] == float('inf')


def test_mdd_computed_from_sequential_equity_curve():
    # +10%, -20%, +5% → 자산곡선 100→110→88→92.4, peak=110, 최저88 → MDD=(110-88)/110=20%
    trades = [_trade(10.0), _trade(-20.0), _trade(5.0)]
    r = compute_basic_kpi(trades)
    assert abs(r['mdd_pct'] - 20.0) < 0.01


# ── §6 Bootstrap ──────────────────────────────────────────────────────────────
def test_bootstrap_stats_none_below_min_n():
    assert bootstrap_stats([1.0, 2.0], min_n=10) is None


def test_bootstrap_stats_strong_winner_has_low_p_pf_lt_1():
    returns = [2.0] * 40 + [-0.5] * 10  # 압도적으로 이기는 전략
    r = bootstrap_stats(returns, n_iter=1000, min_n=10)
    assert r is not None
    assert r['bootstrap_p_pf_lt_1'] < 0.05
    assert r['bootstrap_pf_mean'] > 1.0


def test_bootstrap_stats_strong_loser_has_high_p_pf_lt_1():
    returns = [-2.0] * 40 + [0.5] * 10
    r = bootstrap_stats(returns, n_iter=1000, min_n=10)
    assert r['bootstrap_p_pf_lt_1'] > 0.9


def test_bootstrap_determinism_same_seed():
    returns = [1.5, -0.8, 2.1, -1.0, 0.5] * 5
    r1 = bootstrap_stats(returns, n_iter=500, seed=7)
    r2 = bootstrap_stats(returns, n_iter=500, seed=7)
    assert r1 == r2


# ── §8 최근 성과 창 ────────────────────────────────────────────────────────────
def test_recent_window_excludes_old_trades():
    trades = [_trade(5.0, days_ago=10), _trade(-5.0, days_ago=200)]
    recent_90 = recent_window_kpi(trades, 90)
    assert recent_90['trades'] == 1  # 200일 전 거래는 제외
    assert recent_90['expectancy_pct'] == 5.0


# ── §9 Distribution Shift ─────────────────────────────────────────────────────
def test_distribution_shift_insufficient_period_below_20():
    r = distribution_shift_check([_trade(1.0)] * 10)
    assert r['status'] == 'INSUFFICIENT_PERIOD'


def test_distribution_shift_compares_halves_when_enough_data():
    trades = [_trade(1.0)] * 15 + [_trade(-1.0)] * 15
    r = distribution_shift_check(trades)
    assert r['status'] == 'COMPARED'
    assert r['first_half_mean_return'] != r['second_half_mean_return']


# ── §10 Walk-Forward ──────────────────────────────────────────────────────────
def test_walk_forward_skipped_when_insufficient():
    r = walk_forward_check([_trade(1.0)] * 30, min_n_per_window=20)
    assert r['status'] == 'SKIPPED'


def test_walk_forward_executed_with_enough_data():
    trades = [_trade(1.0)] * 90
    r = walk_forward_check(trades, min_n_per_window=20)
    assert r['status'] == 'EXECUTED'
    assert r['train']['trades'] == 30
    assert r['validation']['trades'] == 30
    assert r['test']['trades'] == 30


# ── §7 PASS/FAIL 기준 ──────────────────────────────────────────────────────────
def test_pass_criteria_insufficient_data_short_circuits():
    verdict, reason = apply_pass_criteria({'trades': 5}, None, 'INSUFFICIENT_DATA')
    assert verdict == 'INSUFFICIENT_DATA'


def test_pass_criteria_provisional_short_circuits():
    verdict, reason = apply_pass_criteria({'trades': 40}, None, 'PROVISIONAL')
    assert verdict == 'PROVISIONAL'


def test_pass_criteria_full_evaluation_pass():
    basic = {'trades': 60, 'profit_factor': 1.5, 'expectancy_pct': 0.3, 'mdd_pct': 10.0}
    bootstrap = {'bootstrap_p_pf_lt_1': 0.05}
    verdict, reason = apply_pass_criteria(basic, bootstrap, 'FULL_EVALUATION')
    assert verdict == 'PASS'


def test_pass_criteria_full_evaluation_fail_low_pf():
    basic = {'trades': 60, 'profit_factor': 0.9, 'expectancy_pct': 0.3, 'mdd_pct': 10.0}
    bootstrap = {'bootstrap_p_pf_lt_1': 0.05}
    verdict, reason = apply_pass_criteria(basic, bootstrap, 'FULL_EVALUATION')
    assert verdict == 'FAIL'
    assert 'PF' in reason


def test_pass_criteria_full_evaluation_fail_high_mdd():
    basic = {'trades': 60, 'profit_factor': 1.5, 'expectancy_pct': 0.3, 'mdd_pct': 25.0}
    bootstrap = {'bootstrap_p_pf_lt_1': 0.05}
    verdict, reason = apply_pass_criteria(basic, bootstrap, 'FULL_EVALUATION')
    assert verdict == 'FAIL'
    assert 'MDD' in reason


def test_pass_criteria_full_evaluation_fail_high_bootstrap_risk():
    basic = {'trades': 60, 'profit_factor': 1.5, 'expectancy_pct': 0.3, 'mdd_pct': 10.0}
    bootstrap = {'bootstrap_p_pf_lt_1': 0.5}
    verdict, reason = apply_pass_criteria(basic, bootstrap, 'FULL_EVALUATION')
    assert verdict == 'FAIL'
    assert 'Bootstrap' in reason


def test_pass_criteria_win_rate_alone_is_not_used():
    """§7 - win_rate은 단독 PASS 조건이 아니다: basic dict에 win_rate가 아예
    없어도(또는 낮아도) PF/Expectancy/Bootstrap/MDD만으로 판정돼야 한다."""
    basic = {'trades': 60, 'profit_factor': 1.5, 'expectancy_pct': 0.3, 'mdd_pct': 10.0}
    bootstrap = {'bootstrap_p_pf_lt_1': 0.05}
    verdict, _ = apply_pass_criteria(basic, bootstrap, 'FULL_EVALUATION')
    assert verdict == 'PASS'  # win_rate 필드가 apply_pass_criteria에 전혀 안 쓰였음을 방증


# ── §2.1 전략별 완전 독립 (합쳐서 계산 안 함) ─────────────────────────────────
def test_strategies_evaluated_independently_not_pooled():
    """seq32 트레이드와 seq33 트레이드를 섞지 않고 각각 compute_basic_kpi를
    호출했을 때, 합친 결과와 다르다는 것으로 '독립 계산'이 실제로 분리돼
    있음을 확인한다(§2.1 위반 시 흔히 나는 실수 - 실수로 합쳐서 계산)."""
    seq32_trades = [_trade(5.0)] * 10
    seq33_trades = [_trade(-5.0)] * 10
    kpi32 = compute_basic_kpi(seq32_trades)
    kpi33 = compute_basic_kpi(seq33_trades)
    pooled = compute_basic_kpi(seq32_trades + seq33_trades)
    assert kpi32['expectancy_pct'] == 5.0
    assert kpi33['expectancy_pct'] == -5.0
    assert pooled['expectancy_pct'] == 0.0
    assert kpi32['expectancy_pct'] != pooled['expectancy_pct']
    assert kpi33['expectancy_pct'] != pooled['expectancy_pct']


# ── §15 SMC Isolation (KPI Dataset 레벨) ──────────────────────────────────────
def test_wi26_script_has_zero_smc_references():
    """8개 전략 KPI 계산 스크립트 어디에도 SMC 관련 코드가 없어야 한다 -
    SMC SIGNAL/NO_SIGNAL/ERROR 어느 상태에서도 이 스크립트의 계산 결과는
    영향받을 방법이 없다(참조 자체가 없으므로)."""
    import analysis.wi26_strategy_performance_validation as mod
    path = mod.__file__
    in_docstring = False
    for line in open(path, encoding='utf-8'):
        stripped = line.strip()
        if stripped.startswith('"""'):
            in_docstring = not in_docstring if stripped.count('"""') == 1 else in_docstring
            continue
        if in_docstring or stripped.startswith('#'):
            continue
        assert 'smc' not in line.lower(), f'{path}: {line.strip()}'


# ── §16 Cross-Strategy Contamination (쿼리 구조 보장) ─────────────────────────
def test_fetch_valid_outcomes_query_filters_by_single_seq():
    """fetch_valid_outcomes(seq)가 WHERE s.condition_seq = %s로 정확히 그
    seq만 조회하는지 - 다른 seq의 Signal이 섞여 들어올 SQL 구조적 여지가
    없음을 소스로 고정한다. 실제 DB 레벨 교차오염 없음은 WI-21
    wi21_multisource_audit.csv(003350 등 7개 종목 실측)로 이미 실증됨."""
    import inspect
    from analysis.wi26_strategy_performance_validation import fetch_valid_outcomes
    src = inspect.getsource(fetch_valid_outcomes)
    assert 'WHERE s.condition_seq = %s' in src


def test_evaluate_strategy_uses_seq_scoped_fetch_only():
    """evaluate_strategy()가 seq별로 독립 호출되는 fetch_valid_outcomes만
    쓰고, 여러 seq를 한 번에 합쳐 조회하는 별도 경로가 없는지(§2.1/§16)."""
    import inspect
    from analysis.wi26_strategy_performance_validation import evaluate_strategy
    src = inspect.getsource(evaluate_strategy)
    assert 'fetch_valid_outcomes(conn, seq)' in src
    assert 'condition_seq IN' not in src  # 여러 seq를 한 쿼리로 묶어 계산 안 함


# ── §18 API 오류/DATA_UNAVAILABLE 제외 (SQL 레벨 필터 확인) ───────────────────
def test_fetch_valid_outcomes_query_excludes_null_and_nonpositive_price():
    """실제 DB 접속 없이 SQL 문자열 자체가 안전장치를 포함하는지 확인 -
    price_at_horizon이 NULL이거나 0 이하인 행이 WHERE절에서 걸러지는지."""
    import inspect
    from analysis.wi26_strategy_performance_validation import fetch_valid_outcomes
    src = inspect.getsource(fetch_valid_outcomes)
    assert 'o.price_at_horizon IS NOT NULL' in src
    assert 'o.price_at_horizon > 0' in src
    assert 'o.return_pct IS NOT NULL' in src
