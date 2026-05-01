"""
SMC→TREND 전환 테스트 (2026-04-05)

시나리오:
  1. SMC 성과 정상 → active=True
  2. SMC 성과 악화 (expectancy<0, hard_stop_ratio>0.30) → active=False
  3. TREND 데이터 부족 (n<10) → ignored=True, active=True (안전장치)
  4. TREND 성과 정상 → active=True
  5. 런타임 게이트: SMC disabled → is_strategy_allowed(regime=SMC) → False
  6. 런타임 게이트: TREND active → is_strategy_allowed(regime=TREND) → True
  7. SMC disabled, TREND unknown → TREND 허용 (미지 레이블 허용 정책)
  8. 캐시 무효화 후 새 스냅샷 반영
  9. normalize_regime: 공백/대소문자 정규화
  10. normalize_entry_reason: 주요 키워드 매핑
  11. hard_stop 판별: 한국어/영어 토큰
  12. SMC+TREND 전환 엔드투엔드 시나리오
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

from analysis.strategy_optimizer import (
    DEFAULT_CACHE_TTL_SECONDS,
    TradeEvent,
    normalize_regime,
    normalize_entry_reason,
    normalize_time_bucket,
    invalidate_strategy_rule_cache,
    get_strategy_rule_snapshot,
    _evaluate_group,
    _is_hard_stop,
)
from strategy.ai_rules_active import is_strategy_allowed


# ─────────────────────────────────────────────
# 헬퍼: TradeEvent 생성
# ─────────────────────────────────────────────

def make_event(
    regime: str = "SMC",
    entry_reason: str = "SMC",
    time_bucket: str = "10:00",
    pnl: float = 1.0,
    exit_reason: str = "Trailing Stop",
) -> TradeEvent:
    return TradeEvent(
        event_ts=datetime.now(timezone.utc),
        regime=regime,
        entry_reason=entry_reason,
        time_bucket=time_bucket,
        pnl=pnl,
        exit_reason=exit_reason,
    )


def make_smc_events(wins: int, losses: int, hard_stops: int = 0) -> list:
    """wins + losses 혼합 SMC 이벤트 생성. hard_stops는 losses 중 일부."""
    events = []
    for _ in range(wins):
        events.append(make_event(regime="SMC", pnl=2.0, exit_reason="Trailing Stop"))
    for i in range(losses):
        reason = "Hard Stop" if i < hard_stops else "Time Exit"
        events.append(make_event(regime="SMC", pnl=-2.0, exit_reason=reason))
    return events


def make_trend_events(wins: int, losses: int) -> list:
    events = []
    for _ in range(wins):
        events.append(make_event(regime="TREND", pnl=3.0, exit_reason="Trailing Stop"))
    for _ in range(losses):
        events.append(make_event(regime="TREND", pnl=-1.5, exit_reason="Time Exit"))
    return events


# ─────────────────────────────────────────────
# 정규화 함수 테스트
# ─────────────────────────────────────────────

def test_01_normalize_regime_smc():
    """소문자 공백 → 대문자 정규화"""
    assert normalize_regime("  smc ") == "SMC"
    assert normalize_regime("SMC") == "SMC"
    assert normalize_regime("trend") == "TREND"
    assert normalize_regime("TREND") == "TREND"


def test_02_normalize_regime_empty():
    """None / 빈 문자열 → [EMPTY]"""
    assert normalize_regime(None) == "[EMPTY]"
    assert normalize_regime("") == "[EMPTY]"
    assert normalize_regime("   ") == "[EMPTY]"


def test_03_normalize_time_bucket():
    assert normalize_time_bucket(" 09:30 ") == "09:30"
    assert normalize_time_bucket(None) == "[EMPTY]"
    assert normalize_time_bucket("") == "[EMPTY]"


def test_04_normalize_entry_reason_smc():
    """SMC 키워드 → 'SMC' 반환"""
    assert normalize_entry_reason("SMC 진입") == "SMC"
    assert normalize_entry_reason("10:37 SMC_OB 공략") == "SMC_OB"
    assert normalize_entry_reason("10:37 진입 (신뢰도: 85%)") == "진입 (신뢰도: 85%)"


def test_05_normalize_entry_reason_trend():
    """TREND 키워드 → 'TREND' 반환"""
    assert normalize_entry_reason("TREND 돌파 진입") == "TREND"
    assert normalize_entry_reason("09:45 TREND 상승 돌파") == "TREND"


def test_06_normalize_entry_reason_empty():
    assert normalize_entry_reason(None) == "[EMPTY]"
    assert normalize_entry_reason("") == "[EMPTY]"


# ─────────────────────────────────────────────
# hard_stop 판별 테스트
# ─────────────────────────────────────────────

def test_07_is_hard_stop_english():
    assert _is_hard_stop("Hard Stop") is True
    assert _is_hard_stop("HARD_STOP") is True
    assert _is_hard_stop("Stop Loss triggered") is True
    assert _is_hard_stop("STOPLOSS") is True


def test_08_is_hard_stop_korean():
    assert _is_hard_stop("자동손절") is True
    assert _is_hard_stop("긴급 손절") is True
    assert _is_hard_stop("구조손절") is True
    assert _is_hard_stop("손절") is True


def test_09_is_hard_stop_negative():
    """일반 청산은 hard_stop 아님"""
    assert _is_hard_stop("Trailing Stop") is False
    assert _is_hard_stop("Time Exit") is False
    assert _is_hard_stop("Take Profit") is False
    assert _is_hard_stop(None) is False
    assert _is_hard_stop("") is False


# ─────────────────────────────────────────────
# _evaluate_group 단위 테스트
# ─────────────────────────────────────────────

def test_10_smc_healthy():
    """SMC 정상: 30 trades, 승률 50%, expectancy > 0 → active=True"""
    events = make_smc_events(wins=15, losses=15, hard_stops=2)
    result = _evaluate_group(events, lambda e: normalize_regime(e.regime))
    smc = result["SMC"]

    assert smc["trades"] == 30
    assert smc["active"] is True
    assert smc["ignored"] is False
    assert smc["win_rate"] == 0.5
    assert smc["expectancy"] == 0.0  # 15×2 + 15×(-2) = 0 / 30
    # expectancy == 0.0 이면 disable 조건(< 0.0) 미충족 → active 유지
    assert smc["disable_reasons"] == []


def test_11_smc_degraded_expectancy():
    """SMC 악화: expectancy < 0 → active=False, disable_reasons에 포함"""
    # 10 win, 20 loss → expectancy = (10×2 + 20×(-3)) / 30 = -1.33
    events = []
    for _ in range(10):
        events.append(make_event(regime="SMC", pnl=2.0))
    for _ in range(20):
        events.append(make_event(regime="SMC", pnl=-3.0))

    result = _evaluate_group(events, lambda e: normalize_regime(e.regime))
    smc = result["SMC"]

    assert smc["active"] is False
    assert smc["ignored"] is False
    assert any("expectancy" in r for r in smc["disable_reasons"])


def test_12_smc_degraded_hard_stop():
    """SMC 악화: hard_stop_ratio > 0.30 → active=False"""
    # 15 wins, 15 losses, 10 hard stops → ratio = 10/30 = 0.333 > 0.30
    events = make_smc_events(wins=15, losses=15, hard_stops=10)
    result = _evaluate_group(events, lambda e: normalize_regime(e.regime))
    smc = result["SMC"]

    assert smc["active"] is False
    assert smc["hard_stop_ratio"] > 0.30
    assert any("hard_stop_ratio" in r for r in smc["disable_reasons"])


def test_13_trend_insufficient_data():
    """TREND 데이터 부족 (n < 10) → ignored=True, active=True (안전장치)"""
    events = [make_event(regime="TREND", pnl=-5.0) for _ in range(5)]
    result = _evaluate_group(events, lambda e: normalize_regime(e.regime))
    trend = result["TREND"]

    assert trend["trades"] == 5
    assert trend["ignored"] is True
    assert trend["active"] is True  # 절대 비활성화 안 함
    assert trend["disable_reasons"] == []


def test_14_trend_healthy():
    """TREND 정상: 15 trades, 승률 60%, expectancy > 0 → active=True"""
    events = make_trend_events(wins=9, losses=6)
    result = _evaluate_group(events, lambda e: normalize_regime(e.regime))
    trend = result["TREND"]

    assert trend["trades"] == 15
    assert trend["active"] is True
    assert trend["win_rate"] == 0.6
    assert trend["expectancy"] > 0


def test_15_smc_trend_coexist():
    """SMC 악화 + TREND 정상 → SMC disabled, TREND active"""
    smc_events = []
    for _ in range(5):
        smc_events.append(make_event(regime="SMC", pnl=1.0))
    for _ in range(25):
        smc_events.append(make_event(regime="SMC", pnl=-2.0, exit_reason="Hard Stop"))

    trend_events = make_trend_events(wins=10, losses=5)
    all_events = smc_events + trend_events

    result = _evaluate_group(all_events, lambda e: normalize_regime(e.regime))

    assert result["SMC"]["active"] is False, "SMC는 악화로 비활성화되어야 함"
    assert result["TREND"]["active"] is True, "TREND는 정상이므로 활성 유지"


def test_16_max_consecutive_losses():
    """연속 손실 추적 정확성 확인"""
    events = [
        make_event(regime="SMC", pnl=-1.0),
        make_event(regime="SMC", pnl=-1.0),
        make_event(regime="SMC", pnl=-1.0),
        make_event(regime="SMC", pnl=2.0),   # 연패 리셋
        make_event(regime="SMC", pnl=-1.0),
        make_event(regime="SMC", pnl=-1.0),
        # 추가 데이터로 min_trades 10개 충족
        *[make_event(regime="SMC", pnl=1.0) for _ in range(4)],
    ]
    result = _evaluate_group(events, lambda e: normalize_regime(e.regime))
    assert result["SMC"]["max_consecutive_losses"] == 3


# ─────────────────────────────────────────────
# 런타임 게이트 테스트 (is_strategy_allowed)
# ─────────────────────────────────────────────

def _make_mock_snapshot(smc_active: bool, trend_active: bool) -> dict:
    """테스트용 스냅샷 픽스처"""
    smc_metrics = {
        "trades": 30, "win_rate": 0.3 if smc_active else 0.15,
        "expectancy": 0.2 if smc_active else -1.5,
        "max_consecutive_losses": 3,
        "hard_stop_ratio": 0.1 if smc_active else 0.45,
        "hard_stop_count": 3 if smc_active else 13,
        "active": smc_active, "ignored": False,
        "disable_reasons": [] if smc_active else ["expectancy<0.00", "hard_stop_ratio>0.30"],
    }
    trend_metrics = {
        "trades": 15, "win_rate": 0.6 if trend_active else 0.2,
        "expectancy": 1.5 if trend_active else -0.5,
        "max_consecutive_losses": 2,
        "hard_stop_ratio": 0.07,
        "hard_stop_count": 1,
        "active": trend_active, "ignored": False,
        "disable_reasons": [] if trend_active else ["expectancy<0.00"],
    }
    return {
        "generated_at": "2026-04-05T10:00:00+00:00",
        "source": "test",
        "lookback_days": 90,
        "ttl_seconds": 60,
        "thresholds": {},
        "total_events": 45,
        "regime": {"SMC": smc_active, "TREND": trend_active},
        "entry_reason": {},
        "time_bucket": {},
        "metrics": {
            "regime": {"SMC": smc_metrics, "TREND": trend_metrics},
            "entry_reason": {},
            "time_bucket": {},
        },
        "disabled": {
            "regime": ([] if smc_active else ["SMC"]) + ([] if trend_active else ["TREND"]),
            "entry_reason": [],
            "time_bucket": [],
        },
    }


def test_17_runtime_gate_smc_disabled():
    """SMC disabled → is_strategy_allowed(regime='SMC') → (False, reason 포함)"""
    snapshot = _make_mock_snapshot(smc_active=False, trend_active=True)

    with patch("strategy.ai_rules_active.get_strategy_rule_snapshot", return_value=snapshot):
        allowed, reason = is_strategy_allowed(regime="SMC", entry_reason="", time_bucket="")

    assert allowed is False
    assert "SMC" in reason
    assert "[SELF_OPT]" in reason


def test_18_runtime_gate_trend_active():
    """TREND active → is_strategy_allowed(regime='TREND') → (True, '')"""
    snapshot = _make_mock_snapshot(smc_active=False, trend_active=True)

    with patch("strategy.ai_rules_active.get_strategy_rule_snapshot", return_value=snapshot):
        allowed, reason = is_strategy_allowed(regime="TREND", entry_reason="", time_bucket="")

    assert allowed is True
    assert reason == ""


def test_19_runtime_gate_unknown_regime_allowed():
    """미지 레이블(RANGE 등)은 스냅샷에 없으면 허용 (보수적 정책)"""
    snapshot = _make_mock_snapshot(smc_active=False, trend_active=True)

    with patch("strategy.ai_rules_active.get_strategy_rule_snapshot", return_value=snapshot):
        allowed, reason = is_strategy_allowed(regime="RANGE", entry_reason="", time_bucket="")

    assert allowed is True


def test_20_runtime_gate_empty_regime_skipped():
    """빈 regime → [EMPTY] → 체크 건너뜀 → 허용"""
    snapshot = _make_mock_snapshot(smc_active=False, trend_active=True)

    with patch("strategy.ai_rules_active.get_strategy_rule_snapshot", return_value=snapshot):
        allowed, reason = is_strategy_allowed(regime="", entry_reason="", time_bucket="")

    assert allowed is True


def test_21_runtime_gate_smc_trend_transition():
    """
    SMC→TREND 전환 핵심 시나리오:
      - SMC 악화로 disabled
      - TREND 신규 활성
      - SMC 진입 차단, TREND 진입 허용
    """
    snapshot = _make_mock_snapshot(smc_active=False, trend_active=True)

    with patch("strategy.ai_rules_active.get_strategy_rule_snapshot", return_value=snapshot):
        # SMC 진입 → 차단
        smc_ok, smc_reason = is_strategy_allowed(
            regime="SMC",
            entry_reason="SMC_OB 공략",
            time_bucket="10:30",
        )
        # TREND 진입 → 허용
        trend_ok, trend_reason = is_strategy_allowed(
            regime="TREND",
            entry_reason="TREND 돌파 진입",
            time_bucket="10:30",
        )

    assert smc_ok is False, "SMC 진입이 차단되어야 함"
    assert trend_ok is True, "TREND 진입이 허용되어야 함"
    assert "expectancy" in smc_reason or "hard_stop" in smc_reason or "SMC" in smc_reason


# ─────────────────────────────────────────────
# 캐시 테스트
# ─────────────────────────────────────────────

def test_22_cache_invalidation():
    """캐시 무효화 후 force_refresh 없이도 재조회 발생"""
    call_count = [0]
    dummy_snapshot = _make_mock_snapshot(smc_active=True, trend_active=True)

    def mock_build(lookback_days):
        call_count[0] += 1
        return dummy_snapshot

    invalidate_strategy_rule_cache()

    with patch("analysis.strategy_optimizer._build_snapshot", side_effect=mock_build):
        get_strategy_rule_snapshot(ttl_seconds=60)
        get_strategy_rule_snapshot(ttl_seconds=60)  # TTL 내 → 캐시 히트

    assert call_count[0] == 1, "TTL 내에는 1회만 조회되어야 함"

    invalidate_strategy_rule_cache()

    with patch("analysis.strategy_optimizer._build_snapshot", side_effect=mock_build):
        get_strategy_rule_snapshot(ttl_seconds=60)  # 캐시 무효화 후 재조회

    assert call_count[0] == 2, "캐시 무효화 후 재조회가 발생해야 함"


def test_23_force_refresh():
    """force_refresh=True → TTL 무시하고 즉시 재조회"""
    call_count = [0]
    dummy_snapshot = _make_mock_snapshot(smc_active=True, trend_active=True)

    def mock_build(lookback_days):
        call_count[0] += 1
        return dummy_snapshot

    invalidate_strategy_rule_cache()

    with patch("analysis.strategy_optimizer._build_snapshot", side_effect=mock_build):
        get_strategy_rule_snapshot(ttl_seconds=3600)                    # 1회 조회
        get_strategy_rule_snapshot(ttl_seconds=3600, force_refresh=True)  # 강제 재조회

    assert call_count[0] == 2


def test_24_default_cache_ttl_constant():
    """DEFAULT_CACHE_TTL_SECONDS가 int이고 양수임"""
    assert isinstance(DEFAULT_CACHE_TTL_SECONDS, int)
    assert DEFAULT_CACHE_TTL_SECONDS > 0


# ─────────────────────────────────────────────
# 스냅샷 구조 검증
# ─────────────────────────────────────────────

def test_25_snapshot_structure_keys():
    """get_strategy_rule_snapshot 반환 dict의 필수 키 존재"""
    dummy = _make_mock_snapshot(smc_active=True, trend_active=True)

    with patch("analysis.strategy_optimizer._build_snapshot", return_value=dummy):
        invalidate_strategy_rule_cache()
        snapshot = get_strategy_rule_snapshot()

    required_keys = {
        "generated_at", "source", "lookback_days", "ttl_seconds",
        "thresholds", "total_events", "regime", "entry_reason",
        "time_bucket", "metrics", "disabled",
    }
    assert required_keys.issubset(set(snapshot.keys()))


def test_26_metrics_per_regime_keys():
    """metrics.regime[label] 의 필수 키 존재"""
    events = make_smc_events(wins=6, losses=4)
    result = _evaluate_group(events, lambda e: normalize_regime(e.regime))
    smc = result["SMC"]

    required = {
        "trades", "win_rate", "expectancy", "max_consecutive_losses",
        "hard_stop_ratio", "hard_stop_count", "active", "ignored", "disable_reasons",
    }
    assert required.issubset(set(smc.keys()))


# ─────────────────────────────────────────────
# 엣지 케이스
# ─────────────────────────────────────────────

def test_27_exactly_10_trades_not_ignored():
    """정확히 10 trades → ignored=False (경계 조건)"""
    events = make_smc_events(wins=5, losses=5)
    assert len(events) == 10
    result = _evaluate_group(events, lambda e: normalize_regime(e.regime))
    assert result["SMC"]["ignored"] is False


def test_28_win_rate_low_but_trades_under_threshold():
    """win_rate < 0.30 이어도 trades ≤ 20 이면 win_rate 조건 비활성화"""
    # 20 trades, 승률 10% (2승 18패) — expectancy는 양수로 유지
    events = [make_event(regime="SMC", pnl=10.0) for _ in range(2)]
    events += [make_event(regime="SMC", pnl=-1.0) for _ in range(18)]
    result = _evaluate_group(events, lambda e: normalize_regime(e.regime))
    smc = result["SMC"]

    # win_rate disable은 trades > 20 전제 → 20 trades는 미적용
    win_rate_reasons = [r for r in smc["disable_reasons"] if "win_rate" in r]
    assert len(win_rate_reasons) == 0


def test_29_win_rate_low_and_trades_over_threshold():
    """win_rate < 0.30 AND trades > 20 → 비활성화"""
    # 21 trades, 5승 16패, pnl 양수 유지해서 expectancy > 0
    events = [make_event(regime="SMC", pnl=5.0) for _ in range(5)]
    events += [make_event(regime="SMC", pnl=-1.0) for _ in range(16)]
    assert len(events) == 21
    result = _evaluate_group(events, lambda e: normalize_regime(e.regime))
    smc = result["SMC"]

    assert smc["win_rate"] < 0.30
    win_rate_reasons = [r for r in smc["disable_reasons"] if "win_rate" in r]
    assert len(win_rate_reasons) > 0


# ─────────────────────────────────────────────
# 러너
# ─────────────────────────────────────────────

if __name__ == "__main__":
    import inspect
    tests = [
        (name, fn)
        for name, fn in sorted(globals().items())
        if name.startswith("test_") and callable(fn)
    ]
    passed, failed = 0, []
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as exc:
            print(f"  FAIL  {name}: {exc}")
            failed.append(name)

    print(f"\n{passed}/{len(tests)} 통과", end="")
    if failed:
        print(f"  |  실패: {', '.join(failed)}")
    else:
        print("  ✓ 전체 통과")
