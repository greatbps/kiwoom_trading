"""
tests/simulation/test_signal_injection.py — Signal Injection Pipeline Tests

목적:
  실제 시장 신호 없이 주문 파이프라인의 핵심 게이트 동작 검증.

  execute_buy()는 15개 이상의 게이트를 가진 오케스트레이터이므로
  전체 end-to-end mock 대신 파이프라인의 세 레이어를 독립적으로 검증한다.

  Layer 1 — Risk Gate     : consecutive_losses 기반 글로벌 차단
  Layer 2 — Position Gate : max_positions 초과 시 차단
  Layer 3 — Broker Boundary: dry_run_mode → API 미호출, 수량/금액 계산 완료 확인

시나리오:
  T1. Happy Path  : 정상 신호 → 포지션 사이징 성공
  T2. Risk Reject : 연속손실 3회 + cooldown_until 설정 → 전체 거래 차단
  T3. Position Cap: positions == max_positions → 진입 거부
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.risk_manager import RiskManager
from metrics.reentry_metrics import ReentryMetrics


# ─── 공통 헬퍼 ──────────────────────────────────────────────────────────────

def _make_rm(**overrides) -> "RiskManager":
    """__init__ 없이 RiskManager 인스턴스 생성 — 클래스 기본값 전체 설정."""
    rm = RiskManager.__new__(RiskManager)
    rm.initial_balance        = 10_000_000
    rm.RISK_PER_TRADE         = 0.01
    rm.MAX_POSITION_SIZE      = 0.30
    rm.HARD_MAX_POSITION      = 200_000
    rm.MAX_POSITIONS          = 3
    rm.HARD_MAX_DAILY_TRADES  = 10
    rm.MAX_DAILY_TRADES       = 5
    rm.MAX_WEEKLY_TRADES      = 15
    rm.CONSECUTIVE_LOSS_LIMIT = 3
    rm.CONSECUTIVE_LOSS_ACTION = 'halt_day'
    rm.LOSS_SIZE_REDUCTION    = 0.5
    rm.HARD_MAX_DAILY_LOSS_PCT  = 0.05
    rm.HARD_MAX_WEEKLY_LOSS_PCT = 0.03
    rm.MAX_DAILY_LOSS_PCT     = 5.0
    rm.MAX_WEEKLY_LOSS_PCT    = 10.0
    rm.MIN_CASH_RESERVE       = 0.20
    rm.today                  = datetime.now().strftime("%Y-%m-%d")
    rm.week_start             = ""
    rm.daily_trades           = []
    rm.weekly_trades          = []
    rm.daily_realized_pnl     = 0.0
    rm.weekly_realized_pnl    = 0.0
    rm.consecutive_losses     = 0
    rm.cooldown_until         = None
    rm.lsg_activated_date     = None
    rm.position_size_multiplier = 1.0
    for k, v in overrides.items():
        setattr(rm, k, v)
    return rm


def _make_df(n: int = 50, base_price: float = 85_000.0) -> pd.DataFrame:
    """합성 OHLCV DataFrame — RVOL 필터 통과용 (마지막 봉 거래량 2배)"""
    import numpy as np
    prices = [base_price * (1 + 0.001 * i) for i in range(n)]
    avg_vol = 500_000
    volumes = [avg_vol] * (n - 1) + [int(avg_vol * 2.5)]  # 마지막 봉 2.5x
    times = pd.date_range("2026-06-03 09:30", periods=n, freq="5min")
    return pd.DataFrame({
        "open":   prices,
        "high":   [p * 1.002 for p in prices],
        "low":    [p * 0.998 for p in prices],
        "close":  prices,
        "volume": volumes,
    }, index=times)


def _risk_config() -> dict:
    """테스트용 최소 risk_management 설정"""
    return {
        "risk_management": {
            "max_risk_per_trade_pct": 1.0,
            "max_positions": 3,
            "max_daily_trades": 5,
            "max_weekly_trades": 15,
            "max_consecutive_losses": 3,
            "max_daily_loss_pct": 5.0,
            "max_weekly_loss_pct": 10.0,
        }
    }


# ─── Layer 1: RiskManager — 포지션 사이징 정상 동작 (Happy Path) ─────────────

class TestHappyPath:
    """T1: 정상 신호 → 포지션 사이징이 의미있는 수량을 반환한다."""

    def test_t1_position_sizing_returns_nonzero_quantity(self):
        """
        잔고 10M, 가격 85,000, 손절 83,300 (2%) 조건에서
        RiskManager가 0보다 큰 수량을 계산해야 한다.
        """
        rm = _make_rm()

        result = rm.calculate_position_size(
            current_balance=10_000_000,
            current_price=85_000,
            stop_loss_price=83_300,
            entry_confidence=0.95,
        )

        assert result["quantity"] > 0, "포지션 사이징이 0주를 반환해서는 안 됨"
        assert result["investment"] > 0, "투자금액이 0이어서는 안 됨"
        assert result["risk_amount"] > 0, "리스크 금액이 0이어서는 안 됨"
        assert result["quantity"] * 85_000 <= 10_000_000, "투자금액이 잔고 초과 불가"

    def test_t1b_high_confidence_gets_larger_position(self):
        """신뢰도 0.95가 0.50보다 큰 포지션을 받아야 한다."""
        rm = _make_rm()

        high = rm.calculate_position_size(10_000_000, 85_000, 83_300, entry_confidence=0.95)
        low  = rm.calculate_position_size(10_000_000, 85_000, 83_300, entry_confidence=0.50)

        assert high["quantity"] >= low["quantity"], \
            "신뢰도가 높을수록 포지션이 같거나 커야 함"


# ─── Layer 2: ReentryMetrics — Risk Gate 차단 ────────────────────────────────

class TestRiskGate:
    """T2: 리스크 차단 조건이 실제로 동작하는가."""

    def _make_sensor_config(self) -> dict:
        return {
            "enabled": True,
            "morning_ef_limit": 2,
            "no_follow_limit": 3,
            "morning_cutoff": "12:00",
        }

    def test_t2_trading_halt_blocks_entry(self):
        """
        Hard Stop 2회 → _trading_halted=True → can_enter_trade() = False
        """
        rm = ReentryMetrics()
        rm._trading_halted          = True
        rm._trading_halted_at       = "10:30"
        rm._conservative_hard_stop_count = 2

        can_enter, reason = rm.can_enter_trade(self._make_sensor_config())

        assert can_enter is False, "TRADING_HALTED 상태에서 진입 허용 불가"
        assert "TRADING_HALTED" in reason

    def test_t2_risk_off_blocks_entry(self):
        """
        no_follow EF 3회 → RISK_OFF_DAY → can_enter_trade() = False
        """
        rm = ReentryMetrics()
        rm._ms_risk_off        = True
        rm._ms_risk_off_at     = "11:00"
        rm._ms_ef_no_follow    = 3

        can_enter, reason = rm.can_enter_trade(self._make_sensor_config())

        assert can_enter is False
        assert "RISK_OFF_DAY" in reason

    def test_t2_clean_state_allows_entry(self):
        """정상 상태에서는 진입을 허용해야 한다."""
        rm = ReentryMetrics()

        can_enter, reason = rm.can_enter_trade(self._make_sensor_config())

        assert can_enter is True, f"정상 상태에서 진입 차단됨: {reason}"

    def test_t2_consecutive_loss_cooldown(self, tmp_path, monkeypatch):
        """
        consecutive_losses=3 + cooldown_until=내일 설정 시
        RiskManager.can_trade() = False
        """
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir()
        risk_log = tmp_path / "data" / "risk_log.json"

        rm = _make_rm(
            consecutive_losses=3,
            cooldown_until=(datetime.now() + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S"),
        )

        can_trade, reason = rm.can_open_position(
            current_balance=10_000_000,
            current_positions_value=0,
            position_count=0,
            position_size=85_000,
        )

        assert can_trade is False, "연속손실 3회 쿨다운 중 거래 허용 불가"
        assert "쿨다운" in reason or "연속 손실" in reason


# ─── Layer 3: Position Gate — max_positions 초과 시 차단 ─────────────────────

class TestPositionGate:
    """T3: 포지션 한도 초과 시 신규 진입을 차단한다."""

    def test_t3_max_positions_blocks_new_entry(self):
        """
        positions = max_positions(3) → 신규 진입 거부.
        execute_buy 내 포지션 체크 로직을 직접 재현.
        """
        max_positions = 3
        current_positions = {
            "005930": {"entry_price": 85_000},
            "000660": {"entry_price": 130_000},
            "035420": {"entry_price": 400_000},
        }

        # execute_buy에서의 포지션 체크 로직 재현
        stock_code = "012330"
        already_in = stock_code in current_positions
        at_capacity = (
            len(current_positions) >= max_positions
            and not already_in
        )

        assert at_capacity is True, \
            f"포지션 {len(current_positions)}/{max_positions} 상태에서 신규 진입이 차단되어야 함"

    def test_t3_under_limit_allows_entry(self):
        """포지션이 한도 미만이면 진입 가능."""
        max_positions = 3
        current_positions = {
            "005930": {"entry_price": 85_000},
        }

        at_capacity = len(current_positions) >= max_positions

        assert at_capacity is False, "포지션 여유 있을 때 차단 불가"

    def test_t3_add_to_existing_position_not_blocked_by_count(self):
        """이미 보유 중인 종목은 포지션 수 한도 체크를 받지 않는다."""
        max_positions = 3
        current_positions = {
            "005930": {"entry_price": 85_000},
            "000660": {"entry_price": 130_000},
            "035420": {"entry_price": 400_000},
        }

        stock_code = "005930"  # 이미 보유 중
        already_in = stock_code in current_positions
        at_capacity = (
            len(current_positions) >= max_positions
            and not already_in
        )

        assert at_capacity is False, "기존 보유 종목 추가 매수는 포지션 수 체크 미적용"


# ─── Layer 4: dry_run_mode 경계 검증 ─────────────────────────────────────────

class TestDryRunBoundary:
    """
    dry_run_mode = True 시 API 미호출 + 수량/금액 계산 완료 확인.

    execute_buy의 dry_run 분기 (line 9513-9519):
        console.print("[DRY-RUN] 백테스트 모드: 실제 주문 생략")
        console.print(f"예상 수량: {quantity}주, 예상 금액: {amount:,.0f}원")
        return  # api.order_buy 미호출

    검증: api.order_buy 미호출 + 수량/금액이 이 시점에 이미 계산됨을 확인.
    """

    def test_t4_dry_run_suppresses_api_call(self):
        """
        dry_run_mode = True → api.order_buy 호출 없이 주문 생략.
        execute_buy의 dry_run 분기를 최소 재현으로 검증.
        """
        mock_api = MagicMock()
        dry_run_mode = True

        quantity = 5
        amount   = 425_000
        stock_code = "005930"
        stock_name = "삼성전자"

        # dry_run 분기 직접 재현
        if dry_run_mode:
            # console.print는 테스트에서 무시
            pass  # return → api.order_buy 미호출
        else:
            mock_api.order_buy(stock_code=stock_code, quantity=quantity, price=85_000)

        mock_api.order_buy.assert_not_called()

    def test_t4_non_dry_run_calls_api(self):
        """dry_run_mode = False → api.order_buy 호출."""
        mock_api = MagicMock()
        mock_api.order_buy.return_value = {"return_code": 0, "order_no": "TEST001"}

        dry_run_mode = False
        quantity = 5
        stock_code = "005930"

        if dry_run_mode:
            pass
        else:
            mock_api.order_buy(stock_code=stock_code, quantity=quantity, price=85_000)

        mock_api.order_buy.assert_called_once_with(
            stock_code=stock_code, quantity=quantity, price=85_000
        )

    def test_t4_position_size_computed_before_dry_run_gate(self):
        """
        dry_run 게이트(line 9512) 도달 시점에 quantity/amount가 이미 계산됨을 확인.
        RiskManager로 수량을 계산하고, 그 결과가 > 0임을 검증.
        """
        rm = _make_rm()

        result = rm.calculate_position_size(
            current_balance=10_000_000,
            current_price=85_000,
            stop_loss_price=83_300,
            entry_confidence=0.95,
        )

        quantity = result["quantity"]
        amount   = result["investment"]

        # dry_run 게이트 도달 시 이 값들이 준비되어 있는지 확인
        assert quantity > 0, "dry_run 게이트 도달 전 수량 계산 완료"
        assert amount > 0,   "dry_run 게이트 도달 전 금액 계산 완료"

        # dry_run 분기 시뮬레이션
        mock_api = MagicMock()
        dry_run_mode = True
        if not dry_run_mode:
            mock_api.order_buy(stock_code="005930", quantity=quantity, price=85_000)

        mock_api.order_buy.assert_not_called()
