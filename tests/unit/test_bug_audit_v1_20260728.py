"""
tests/unit/test_bug_audit_v1_20260728.py

Production Reliability Audit v1.0 (2026-07-28) 발견 버그의 회귀 테스트.
수정 적용: Production Audit Fix v1.1 (docs/PIPELINE_AUDIT_FIX_REPORT_v1.1.md)

커버 대상:
  BUG-01 [HIGH]   Entry Quality YAML 설정 무시 (eq_config 점표기 조회)
  BUG-02 [MEDIUM] Partial Exit trailing stop 단조성 위반
  BUG-03 [HIGH]   candidate=0 시 Gate Health 알람 침묵
  BUG-04 [MEDIUM] Gate Funnel 지표 출처 혼합 (DB/로그)
"""

from __future__ import annotations

import sys
import tempfile
from contextlib import ExitStack
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from utils.config_loader import ConfigLoader

_CFG_PATH = Path(__file__).parent.parent.parent / 'config' / 'strategy_hybrid.yaml'


# ═════════════════════════════════════════════════════════════════════════════
# BUG-01 [HIGH] Entry Quality 설정이 YAML을 무시하고 하드코딩 기본값 사용
#
# 원인: self.config.get('entry_quality', {}) 는 일반 dict를 반환하는데,
#       거기에 .get('rvol_filter.enabled') 처럼 점표기 키로 조회 → 항상 miss.
#       ConfigLoader.get()은 점 경로를 지원하지만 그 반환값(dict)은 지원하지 않는다.
# 수정: self.config.get('entry_quality.rvol_filter.enabled') 형태로 통일 (방식 A).
# ═════════════════════════════════════════════════════════════════════════════

_EQ_DOTTED_KEYS = [
    'rvol_filter.enabled',
    'rvol_filter.threshold',
    'ema9_pullback.enabled',
    'vwap_distance.enabled',
    'vwap_distance.max_pct',
]


def test_bug01_configloader_contract_dotted_key_only_works_on_loader():
    """
    버그의 근본 원인을 고정한다: 점 경로는 ConfigLoader에서만 동작하고,
    반환된 일반 dict에서는 동작하지 않는다.
    """
    cfg = ConfigLoader(str(_CFG_PATH))
    eq_dict = cfg.get('entry_quality', {})
    assert isinstance(eq_dict, dict)

    for key in _EQ_DOTTED_KEYS:
        # 일반 dict: 점표기 키는 존재하지 않는다 (miss → 기본값 폴백의 원인)
        assert key not in eq_dict, f"{key!r}가 dict에 리터럴로 존재 — 테스트 전제 재확인 필요"
        # ConfigLoader: 전체 경로로는 정상 도달
        assert cfg.get(f'entry_quality.{key}', None) is not None


def test_bug01_no_dotted_key_lookup_on_plain_dict_remains():
    """수정 후: main_auto_trading.py에 '일반 dict + 점표기 키' 패턴이 남아있지 않아야 한다."""
    import re

    src = (Path(__file__).parent.parent.parent / 'main_auto_trading.py').read_text(
        encoding='utf-8', errors='ignore'
    )
    # eq_config 같은 지역 dict 변수에 대한 점표기 조회 탐지
    bad = re.findall(r"""eq_config\.get\(\s*['"][A-Za-z0-9_]+\.[A-Za-z0-9_.]+['"]""", src)
    assert bad == [], f"점표기-on-dict 패턴 잔존: {bad}"


def _write_temp_config(overrides: dict) -> str:
    """실제 YAML을 복사해 entry_quality만 덮어쓴 임시 설정 파일 생성."""
    base = yaml.safe_load(_CFG_PATH.read_text(encoding='utf-8'))
    base['entry_quality'] = overrides
    tmp = tempfile.NamedTemporaryFile('w', suffix='.yaml', delete=False, encoding='utf-8')
    yaml.safe_dump(base, tmp, allow_unicode=True)
    tmp.close()
    return tmp.name


@pytest.mark.parametrize(
    'yaml_value,expected',
    [
        (1.8, 1.8),      # 기본값(1.7)과 다른 값 → YAML이 반영되어야 함
        (3.5, 3.5),
    ],
)
def test_bug01_yaml_threshold_is_honored(yaml_value, expected):
    """YAML의 rvol threshold를 기본값과 다르게 설정하면 그 값이 실제로 읽혀야 한다."""
    path = _write_temp_config({
        'rvol_filter':   {'enabled': True, 'threshold': yaml_value},
        'ema9_pullback': {'enabled': True},
        'vwap_distance': {'enabled': True, 'max_pct': 1.8},
    })
    cfg = ConfigLoader(path)
    # 수정된 코드가 사용하는 조회 방식
    assert cfg.get('entry_quality.rvol_filter.threshold', 1.7) == expected


def test_bug01_yaml_enabled_false_is_honored():
    """YAML에서 enabled=False로 끄면 그 값이 실제로 읽혀야 한다(기본값 True에 가려지면 안 됨)."""
    path = _write_temp_config({
        'rvol_filter':   {'enabled': False, 'threshold': 1.7},
        'ema9_pullback': {'enabled': False},
        'vwap_distance': {'enabled': False, 'max_pct': 1.8},
    })
    cfg = ConfigLoader(path)
    assert cfg.get('entry_quality.rvol_filter.enabled', True) is False
    assert cfg.get('entry_quality.ema9_pullback.enabled', True) is False
    assert cfg.get('entry_quality.vwap_distance.enabled', True) is False


@pytest.mark.parametrize(
    'yaml_threshold,should_block',
    [
        (99.0, True),    # 비현실적으로 높은 기준 → 반드시 차단되어야 함
        (0.1, False),    # 비현실적으로 낮은 기준 → 통과해야 함
    ],
)
def test_bug01_end_to_end_rvol_gate_uses_yaml_threshold(caplog, yaml_threshold, should_block):
    """
    End-to-End: execute_buy()의 EQ-1 게이트가 YAML threshold를 실제로 읽는지.
    하드코딩 기본값(1.7)만 쓴다면 두 케이스 모두 동일하게 동작해 이 테스트가 실패한다.
    """
    import logging

    from tests.simulation.test_execute_buy_dry_run import _make_df, _make_stub, _patches

    stub = _make_stub()
    stub.dry_run_mode = True
    path = _write_temp_config({
        'rvol_filter':   {'enabled': True, 'threshold': yaml_threshold},
        'ema9_pullback': {'enabled': False},
        'vwap_distance': {'enabled': False, 'max_pct': 1.8},
    })
    real_cfg = ConfigLoader(path)

    mock_cfg = stub.config
    original_get = mock_cfg.get

    def hybrid_get(key, default=None):
        # entry_quality 조회만 실제 ConfigLoader로 위임, 나머지는 MockConfig 유지
        if isinstance(key, str) and key.startswith('entry_quality'):
            return real_cfg.get(key, default)
        return original_get(key, default)

    with patch.object(mock_cfg, 'get', side_effect=hybrid_get):
        with ExitStack() as stack:
            for p in _patches(stub):
                stack.enter_context(p)
            with caplog.at_level(logging.INFO):
                stub.execute_buy(
                    stock_code='005930', stock_name='삼성전자', price=75_000,
                    df=_make_df(), entry_reason='SMC:test', entry_confidence=0.95,
                )

    blocked = '[RVOL_BLOCK]' in caplog.text
    assert blocked is should_block, (
        f"threshold={yaml_threshold} 인데 차단여부={blocked} (기대={should_block}) — "
        f"YAML 값이 반영되지 않았을 가능성"
    )
    if should_block:
        # 로그에 YAML 값이 그대로 찍혀야 한다 (기본값 1.7이 아니라)
        assert str(yaml_threshold) in caplog.text


# ═════════════════════════════════════════════════════════════════════════════
# BUG-02 [MEDIUM] Partial Exit trailing stop 단조성 위반
#
# main_auto_trading.py 의 stage>=2 부분청산 경로가 max() 가드 없이 raw 대입해
# 기존보다 낮은 스탑으로 덮어쓸 수 있었다.
# ═════════════════════════════════════════════════════════════════════════════

def _partial_exit_stage2_stop(position: dict, ratio: float) -> dict:
    """수정된 main_auto_trading.py:12225-12231 로직을 그대로 옮긴 함수."""
    position['highest_price'] = max(position.get('highest_price', 0), position['current_price'])
    position['trailing_active'] = True
    calc_stop = position['highest_price'] * (1 - ratio / 100)
    prev_stop = position.get('trailing_stop_price') or 0
    position['trailing_stop_price'] = max(prev_stop, calc_stop)
    return position


def test_bug02_previous_stop_higher_is_preserved():
    """지시서 명시 케이스: previous=100, calculated=95 → new_stop=100."""
    position = {'highest_price': 100.0, 'current_price': 100.0, 'trailing_stop_price': 100.0}
    # ratio=5.0 → calc = 100*0.95 = 95
    _partial_exit_stage2_stop(position, ratio=5.0)
    assert position['trailing_stop_price'] == 100.0


def test_bug02_trailing_stop_is_monotonic_across_partial_exit():
    """ATR 기반으로 높게 래칫된 스탑이 부분청산으로 하락하지 않는다."""
    position = {'highest_price': 10_000.0, 'current_price': 10_000.0, 'trailing_stop_price': 9_960.0}
    before = position['trailing_stop_price']
    _partial_exit_stage2_stop(position, ratio=1.0)      # calc = 9,900
    assert position['trailing_stop_price'] >= before
    assert position['trailing_stop_price'] == 9_960.0


def test_bug02_higher_calculated_stop_still_ratchets_up():
    """계산값이 더 높으면 정상적으로 상향 갱신된다(가드가 상향까지 막으면 안 됨)."""
    position = {'highest_price': 12_000.0, 'current_price': 12_000.0, 'trailing_stop_price': 9_960.0}
    _partial_exit_stage2_stop(position, ratio=1.0)      # calc = 11,880
    assert position['trailing_stop_price'] == pytest.approx(11_880.0)


def test_bug02_source_has_monotonic_guard():
    """소스 레벨 회귀 방지: 부분청산 경로에 max() 가드가 실제로 존재하는지."""
    src = (Path(__file__).parent.parent.parent / 'main_auto_trading.py').read_text(
        encoding='utf-8', errors='ignore'
    )
    assert "max(_pe_prev_stop, _pe_calc_stop)" in src, "부분청산 monotonic 가드가 사라짐"


# ═════════════════════════════════════════════════════════════════════════════
# BUG-03 [HIGH] candidate=0 시 Gate Health 알람 침묵
#
# 기존 즉시경고가 전부 `candidates > 0` 전제라, candidate가 0이면(가장 의심스러운
# 상황) 어떤 알람도 울리지 않고 NORMAL로 보고되었다.
# ═════════════════════════════════════════════════════════════════════════════

def _build_report(**kw):
    """build_report()는 DB/파일 I/O 없는 순수 함수라 직접 호출한다."""
    from analysis.gate_health_check import build_report

    params = dict(
        target_date=date(2026, 7, 27),
        candidates=0,
        gate_bd={'__total__': 0},
        passed=0,
        regime_evaluated=0,
        entry_signal_checked=0,
        orders=0,
        history_days=[],
        history=[],
    )
    params.update(kw)
    return build_report(**params)


_LIVE_OK = {
    'trading_log_present': True,
    'regime_evaluated':    92,
    'scanner_ran':         True,
    'heartbeat_fresh':     True,
}
_LIVE_DEAD = {
    'trading_log_present': False,
    'regime_evaluated':    0,
    'scanner_ran':         False,
    'heartbeat_fresh':     False,
}


def test_bug03_zero_candidate_no_longer_silently_normal():
    """candidate=0이면 더 이상 조용히 NORMAL이 아니다."""
    report = _build_report(candidates=0, liveness=_LIVE_OK, regime_evaluated=92)
    assert report['system_status'] != 'NORMAL'
    assert report['warnings'] or report['errors']


def test_bug03_pipeline_alive_zero_candidate_is_warning():
    """정상 케이스: 스캐너/레짐 정상인데 후보 0 → WARNING (전략 필터가 거른 것)."""
    report = _build_report(candidates=0, liveness=_LIVE_OK, regime_evaluated=92)
    assert report['zero_candidate_severity'] == 'WARNING'
    assert report['errors'] == []
    assert any('Candidate zero detected' in w for w in report['warnings'])


def test_bug03_pipeline_dead_zero_candidate_is_error():
    """장애 케이스: 스캐너 미실행 + 레짐 평가 0 → ERROR."""
    report = _build_report(candidates=0, liveness=_LIVE_DEAD, regime_evaluated=0)
    assert report['zero_candidate_severity'] == 'ERROR'
    assert report['errors']
    assert report['system_status'].startswith('ERROR')


def test_bug03_stalled_loop_is_error():
    """로그는 있으나 regime 평가가 0 → 파이프라인 정지로 ERROR."""
    liveness = dict(_LIVE_OK, regime_evaluated=0)
    report = _build_report(candidates=0, liveness=liveness, regime_evaluated=0)
    assert report['zero_candidate_severity'] == 'ERROR'


def test_bug03_missing_liveness_probe_does_not_stay_silent():
    """생존신호를 못 구했을 때도 침묵하지 않고 판정불가를 알린다."""
    report = _build_report(candidates=0, liveness=None)
    assert report['system_status'] != 'NORMAL'
    assert any('liveness probe unavailable' in w for w in report['warnings'])


def test_bug03_normal_day_with_candidates_still_normal():
    """후보가 정상 생성되고 진입평가까지 도달하면 기존대로 NORMAL 유지(오탐 방지)."""
    report = _build_report(
        candidates=300, liveness=_LIVE_OK, regime_evaluated=92,
        entry_signal_checked=12, orders=2, gate_bd={'__total__': 10},
    )
    assert report['system_status'] == 'NORMAL'
    assert report['errors'] == []


def test_bug03_classifier_unit():
    """classify_zero_candidate() 단위 검증 — scanner_ran=None은 ERROR로 올리지 않는다."""
    from analysis.gate_health_check import classify_zero_candidate

    assert classify_zero_candidate(_LIVE_DEAD)[0] == 'ERROR'
    assert classify_zero_candidate(_LIVE_OK)[0] == 'WARNING'
    # 과거 날짜 조회: 산출물 파일이 덮어써져 판정 불가(None) → WARNING 유지
    assert classify_zero_candidate(dict(_LIVE_OK, scanner_ran=None))[0] == 'WARNING'


# ═════════════════════════════════════════════════════════════════════════════
# BUG-04 [MEDIUM] Gate Funnel 지표 출처 혼합 (DB/로그)
#
# 기존: candidates/passed/orders는 DB, entry_signal_checked는 로그 태그 산술.
#       분모가 달라 REGIME_BLOCK 707 vs Candidate 293 (241%) 같은 값이 나왔다.
# 수정: 퍼널 지표를 research.decision_ledger 단일 출처로 통일.
# ═════════════════════════════════════════════════════════════════════════════

def test_bug04_entry_stage_reasons_cover_execute_buy_codes():
    """execute_buy() 단계 reason_code가 퍼널 집계 대상에 모두 포함되어야 한다."""
    from analysis.gate_health_check import _ENTRY_STAGE_REASONS
    from services.decision_service import DecisionService

    # migration 005에서 추가한 execute_buy 단계 코드
    expected = {
        'PASS', 'COOLDOWN_ACTIVE', 'RISK_BLOCKED', 'DUPLICATE_POSITION',
        'MAX_POSITIONS', 'INSUFFICIENT_CAPITAL', 'ENTRY_QUALITY_BLOCKED',
        'API_FAILURE', 'ORDER_FAILURE',
    }
    assert expected == set(_ENTRY_STAGE_REASONS)
    # 전부 DecisionService가 인정하는 유효 코드여야 한다 (FK 위반 방지)
    assert expected <= DecisionService._VALID_REASON_CODES


def test_bug04_entry_evaluation_no_longer_derived_from_logs():
    """
    회귀 방지: run_gate_health_check()가 entry_signal_checked를 로그 태그 산술로
    계산하지 않고 DB 집계 함수를 사용해야 한다.
    """
    src = (Path(__file__).parent.parent.parent / 'analysis' / 'gate_health_check.py').read_text(
        encoding='utf-8', errors='ignore'
    )
    assert "entry_signal_checked = _entry_stage_count(" in src, "DB 기반 집계로 전환되지 않음"
    assert "entry_signal_checked = max(0, logs['regime_evaluated']" not in src, \
        "로그 태그 산술 방식이 남아있음"


def test_bug04_report_exposes_db_sourced_funnel_fields():
    """리포트가 DB 단일 출처 지표를 노출해 로그 기반 값과 구분 가능해야 한다."""
    report = _build_report(
        candidates=300, liveness=_LIVE_OK, regime_evaluated=707,
        entry_signal_checked=12, orders=2, decision_total=300,
        reason_bd={'REGIME_BLOCKED': 250, 'ENTRY_QUALITY_BLOCKED': 38, 'PASS': 12},
    )
    assert report['decision_total'] == 300
    assert report['reason_breakdown']['REGIME_BLOCKED'] == 250
    # 로그 기반 값은 별도 필드로 유지되어 퍼널 분모와 섞이지 않는다
    assert report['v14_regime_evaluated'] == 707


def test_bug04_funnel_counts_are_internally_consistent():
    """
    퍼널 정합성: DB 단일 출처에서는 각 단계가 상위 단계를 초과할 수 없다.
    (기존 로그 혼합 방식에서는 REGIME_BLOCK 707 > Candidate 293 처럼 역전이 발생했다)
    """
    candidates = 300
    reason_bd = {'REGIME_BLOCKED': 250, 'ENTRY_QUALITY_BLOCKED': 38, 'PASS': 12}
    decision_total = sum(reason_bd.values())
    entry_stage = reason_bd['ENTRY_QUALITY_BLOCKED'] + reason_bd['PASS']

    report = _build_report(
        candidates=candidates, liveness=_LIVE_OK, regime_evaluated=707,
        entry_signal_checked=entry_stage, orders=reason_bd['PASS'],
        decision_total=decision_total, reason_bd=reason_bd,
    )

    assert report['decision_total'] <= candidates
    assert report['entry_signal_checked'] <= report['decision_total']
    assert report['orders_submitted'] <= report['entry_signal_checked']
