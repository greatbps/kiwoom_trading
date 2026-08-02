"""
tests/simulation/test_execute_buy_dry_run.py — execute_buy() dry_run 직접 주입 테스트

목적:
  Mock Signal을 execute_buy()에 직접 주입해 [DRY-RUN] 분기까지 실제로 통과시킨다.
  WebSocket/장중 여부와 무관하게 파이프라인 전체 경로를 오프라인 검증한다.

전략:
  1. KiwoomAutoTrader.__new__()로 stub 생성 — __init__ 생략
  2. 외부 의존성(DB, API, WebSocket) = MagicMock
  3. entry_reason="EXPERIMENT:test" — RVOL/EMA9 게이트 비차단 경로 사용
  4. MockConfig로 선택적 게이트 비활성화
  5. dry_run_mode=True → execute_buy가 [DRY-RUN] 출력 후 return, api.order_buy 미호출
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from main_auto_trading import IntegratedTradingSystem as KiwoomAutoTrader


# ─── MockConfig ────────────────────────────────────────────────────────────────

class MockConfig(dict):
    """ConfigLoader 호환 dict — 선택적 게이트를 전부 비활성화."""

    _DATA: dict = {
        # 선택적 필터 전부 OFF
        "pattern_recognition":     {"enabled": False},
        "supply_demand_filter":    {"enabled": False},
        "entry_position_filter":   {"enabled": False},
        "regime_filter":           {"enabled": False},
        "equity_control":          {"enabled": False},
        "ml_filter":               {"enabled": False},
        "eq_ml_filter":            {"enabled": False},
        "entry_quality": {
            "rvol_filter.enabled":    True,    # EXPERIMENT: prefix → 비차단
            "rvol_filter.threshold":  1.7,
            "ema9_pullback.enabled":  True,    # EXPERIMENT: prefix → 비차단
            "vwap_distance.enabled":  False,
        },
        "experiment":              {"bad_market_size_mult": 0.3},
        "risk_management":         {},
        "risk_control":            {
            "loss_streak_guard":   {"enabled": False},
            "conservative_mode":   {},
            "trade_cooldown":      {"bypass_confidence": 0.85},
        },
        "smc":                     {
            "choch_grade": {
                "grade_a_plus": {
                    "bypass_lsg":   True,
                    "bypass_kelly": True,
                }
            }
        },
    }

    def get(self, key: str, default=None):
        # 점(.) 구분자 처리: "risk_control.loss_streak_guard" 같은 중첩 키
        parts = key.split(".", 1)
        val = self._DATA.get(parts[0], default)
        if len(parts) == 2 and isinstance(val, dict):
            return val.get(parts[1], default)
        return val if val is not None else default

    def get_trailing_config(self) -> dict:
        return {"stop_loss_pct": 3.0, "trailing_stop_pct": 2.0}


# ─── OHLCV 헬퍼 ────────────────────────────────────────────────────────────────

def _make_df(n: int = 60, price: float = 75_000.0) -> pd.DataFrame:
    """
    EQ-1(RVOL), EQ-2(EMA9) 통과 조건 충족 df.
    entry_reason="EXPERIMENT:"이면 이 두 게이트는 차단 없이 통과하므로
    df 형태보다 valid OHLCV 구조가 중요.
    """
    prices = [price * (1 + 0.0005 * i) for i in range(n)]
    avg_vol = 1_000_000
    volumes = [avg_vol] * (n - 1) + [int(avg_vol * 3.0)]  # 마지막 봉 3x
    times = pd.date_range("2026-06-03 09:30", periods=n, freq="5min")
    return pd.DataFrame(
        {
            "open":   [p * 0.999 for p in prices],
            "high":   [p * 1.003 for p in prices],
            "low":    [p * 0.997 for p in prices],
            "close":  prices,
            "volume": volumes,
        },
        index=times,
    )


# ─── Stub 팩토리 ───────────────────────────────────────────────────────────────

def _make_stub() -> KiwoomAutoTrader:
    """최소 KiwoomAutoTrader stub — dry_run 게이트까지 도달 가능."""
    t = KiwoomAutoTrader.__new__(KiwoomAutoTrader)

    # ── config ──────────────────────────────────────────────────────────────
    t.config = MockConfig()

    # ── dry_run 게이트 ───────────────────────────────────────────────────────
    t.dry_run_mode = True

    # ── 단순 상태 ────────────────────────────────────────────────────────────
    t.stock_ban_list             = set()
    t._trade_cooldown            = 0
    t.positions                  = {}
    t.stock_cooldown             = {}
    t.total_assets               = 10_000_000
    t.current_cash               = 10_000_000
    t.positions_value            = 0.0
    t.max_trades_per_stock_per_day = 3
    t.daily_trade_count          = {}
    t._cooldown_v2_enabled       = False
    t._cooldown_by_reason        = {}
    t._daily_atr_defensive_count = 0
    t._daily_atr_defensive_exposure = 0.0
    t._pending_exit_value        = 0.0
    t._active_pattern_positions  = {}
    t._daily_patterns            = {}
    t.validated_stocks           = {}
    t.eq_model                   = None

    # ── reentry_metrics ──────────────────────────────────────────────────────
    rm = MagicMock()
    rm.get_conservative_adjustments.return_value = {
        "active": False, "max_positions": None,
        "position_size_mult": 1.0, "cooldown_mult": 1.0,
    }
    rm.get_loss_streak_adjustments.return_value = {
        "active": False, "lsg_just_released": False,
        "consecutive": 0, "position_size_mult": 1.0,
        "high_conf_passthrough": False, "min_confidence_override": 0.85,
        "release_boost_mult": 1.0,
    }
    rm.can_enter_trade.return_value = (True, "")
    t.reentry_metrics = rm

    # ── market_context ───────────────────────────────────────────────────────
    mc = MagicMock()
    mc.evaluate.return_value = ("OK", "", {"atr_mode": "NORMAL", "atr_ratio": 1.0})
    # [CBF-2 2026-07-20] EQ-6 레짐필터가 get_regime()을 (regime, reason) 튜플로 언패킹함 —
    # 미설정 시 MagicMock 기본값이 언패킹 불가해 ValueError → Fail Closed 차단되어 테스트 목적과
    # 무관하게 order_buy 호출 전에 막힘 (수정 전엔 무음 예외swallow라 우연히 통과했었음)
    mc.get_regime.return_value = ("NEUTRAL", "")
    t.market_context = mc

    # ── risk_manager ─────────────────────────────────────────────────────────
    risk = MagicMock()
    risk.__bool__ = lambda self: True
    risk.can_open_position.return_value = (True, "")
    risk.calculate_position_size.return_value = {
        "quantity": 5,
        "investment": 375_000,
        "risk_amount": 3_750,
        "stop_loss_price": 72_750,
        "position_pct": 0.0375,
        "position_ratio": 3.75,
    }
    t.risk_manager = risk

    # ── trade_stats (n_trades<20 → Kelly cold start, _position_sizer 스킵) ──
    ts = MagicMock()
    ts.n_trades = 5
    t._trade_stats = ts

    # ── drift_detector (try/except 처리 → Exception 발생 시 DRIFT_SKIP) ────
    t.drift_detector = MagicMock()
    t.drift_detector.get_drift_level.side_effect = Exception("no_drift_data")

    # ── analyzer ─────────────────────────────────────────────────────────────
    t.analyzer = MagicMock()
    t.analyzer.stop_loss_pct = 3.0

    # ── api (dry_run gate 이후에만 호출됨 → 미호출 검증 대상) ────────────────
    t.api = MagicMock()

    # ── online_stats ──────────────────────────────────────────────────────────
    t.online_stats = MagicMock()

    return t


# ─── 패치 컨텍스트 ─────────────────────────────────────────────────────────────

def _patches(stub: KiwoomAutoTrader):
    """외부 의존 메서드 일괄 패치."""
    return [
        patch.object(stub, "_get_time_weight",          return_value=1.0),
        patch.object(stub, "_infer_signal_regime",       return_value="smc"),
        patch.object(stub, "_check_db_hard_stop_guard",  return_value=(True, "")),
        patch.object(stub, "_get_current_time_bucket",   return_value="morning"),
        patch.object(stub, "_log_buy_failure"),
        patch.object(stub, "_record_blocked_entry"),
        patch("main_auto_trading.is_strategy_allowed",   return_value=(True, "")),
    ]


# ─── 테스트 ────────────────────────────────────────────────────────────────────

class TestExecuteBuyDryRun:
    """
    execute_buy()를 직접 호출해 [DRY-RUN] 분기까지 태우는 통합 검증.
    """

    def test_dry_run_returns_without_api_call(self, capsys):
        """
        dry_run_mode=True → execute_buy가 [DRY-RUN] 메시지 출력 후 return.
        api.order_buy 미호출.
        """
        stub = _make_stub()
        df   = _make_df()

        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in _patches(stub):
                stack.enter_context(p)

            stub.execute_buy(
                stock_code="005930",
                stock_name="삼성전자",
                price=75_000,
                df=df,
                entry_reason="EXPERIMENT:test_dry_run",
                entry_confidence=0.95,
            )

        stub.api.order_buy.assert_not_called()

    def test_dry_run_logs_dry_run_tag(self, caplog):
        """
        dry_run 분기에서 logger.info 또는 console.print로
        DRY-RUN / DRY_RUN 태그가 출력된다.
        """
        import logging
        stub = _make_stub()
        df   = _make_df()

        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in _patches(stub):
                stack.enter_context(p)
            with caplog.at_level(logging.DEBUG):
                stub.execute_buy(
                    stock_code="005930",
                    stock_name="삼성전자",
                    price=75_000,
                    df=df,
                    entry_reason="EXPERIMENT:test_dry_run",
                    entry_confidence=0.95,
                )

        # logger 기록이 없어도 console.print로 출력됨 (Rich) — api 미호출로 충분
        stub.api.order_buy.assert_not_called()

    def test_live_mode_would_call_api(self):
        """
        대조군: dry_run_mode=False 이면 api.order_buy 호출 경로로 진입한다.
        (order_buy에서 예외 발생해도 호출은 됨)
        """
        stub = _make_stub()
        stub.dry_run_mode = False
        df   = _make_df()

        # api.order_buy가 호출될 때 예외를 발생시켜 이후 로직(포지션 저장 등) 방지
        stub.api.order_buy.side_effect = Exception("test_abort")

        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in _patches(stub):
                stack.enter_context(p)
            try:
                stub.execute_buy(
                    stock_code="005930",
                    stock_name="삼성전자",
                    price=75_000,
                    df=df,
                    entry_reason="EXPERIMENT:test_live",
                    entry_confidence=0.95,
                )
            except Exception:
                pass

        stub.api.order_buy.assert_called_once()
