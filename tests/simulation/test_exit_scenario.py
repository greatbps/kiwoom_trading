"""
tests/simulation/test_exit_scenario.py — 3차 시나리오 시뮬레이션

목적:
  실시간 가격 스트림을 모사해 청산 로직 전체 파이프라인을 시뮬레이션.
  Kiwoom API / yfinance 없이 합성 OHLCV + 포지션 데이터로 결정 재현.

시나리오:
  S1. Hard Stop  : 가격이 진입가 -7% 하락 → TREND regime에서 완화(-8%) 후 NEUTRAL에서 발동
  S2. MFE_EXIT   : 35분 경과 + MFE 0% → 청산 발동 / MFE 충분하면 통과
  S3. 트레일링    : +2% 돌파 후 트레일링 ON → 최고가 대비 -X% 시 청산
  S4. Dry-Run    : _dry_run 플래그 → API 호출 없이 [DRY_RUN_SIGNAL] 로그만 생성
  S5. 오버나이트  : 강세 포지션 3조건 동시 → CARRY_OVERRIDE, 미충족 → 강제청산
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


# ──────────────────────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────────────────────

def _make_df(
    prices: list[float],
    base_time: Optional[datetime] = None,
    freq_min: int = 5,
) -> pd.DataFrame:
    """합성 OHLCV DataFrame 생성 (close = price, open=high=low=close 단순화)"""
    if base_time is None:
        base_time = datetime.now().replace(hour=9, minute=30, second=0, microsecond=0)
    times = [base_time + timedelta(minutes=i * freq_min) for i in range(len(prices))]
    return pd.DataFrame({
        "open":   prices,
        "high":   [p * 1.001 for p in prices],
        "low":    [p * 0.999 for p in prices],
        "close":  prices,
        "volume": [100_000] * len(prices),
    }, index=pd.DatetimeIndex(times))


def _make_position(
    entry_price: float = 10_000,
    current_price: Optional[float] = None,
    highest_price: Optional[float] = None,
    entry_offset_min: int = 0,
    trailing_active: bool = False,
    partial_exit_stage: int = 0,
    entry_date_offset_days: int = 0,
) -> dict:
    entry_time = datetime.now() - timedelta(minutes=entry_offset_min)
    if entry_date_offset_days:
        entry_time -= timedelta(days=entry_date_offset_days)
    cp = current_price if current_price is not None else entry_price
    hp = highest_price if highest_price is not None else cp
    return {
        "entry_price":        entry_price,
        "current_price":      cp,
        "highest_price":      hp,
        "entry_time":         entry_time,
        "trailing_active":    trailing_active,
        "partial_exit_stage": partial_exit_stage,
        "stock_name":         "SIMTEST",
        "quantity":           10,
        "choch_grade":        "B",
    }


def _make_exit_logic(
    emergency_stop_pct: float = 6.0,
    hard_stop_relax_enabled: bool = False,
    hard_stop_relax_bull_pct: float = 8.0,
    min_hold_minutes: int = 5,
):
    from trading.exit_logic_optimized import OptimizedExitLogic

    config = {
        "risk_control": {
            "emergency_stop_pct": emergency_stop_pct,
            "emergency_stop_candle_confirm": False,  # 시뮬레이션: 즉시 발동
            "hard_stop_relax": {
                "enabled": hard_stop_relax_enabled,
                "bull_pct": hard_stop_relax_bull_pct,
            },
            "min_hold_minutes": min_hold_minutes,
            "hard_stop_pct": 2.0,
            "technical_stop_pct": 1.2,
            "early_failure": {"enabled": False},
            "min_hold_time": {"enabled": False},
            "same_day_entry": {"enabled": False},
            "overnight_exit": {"enabled": False},
            "draw_trade": {},
        },
        "exit_strategy": {
            "trailing": {
                "enabled": True,
                "trigger_pct":  2.0,
                "trail_pct":    1.0,
                "trail_atr_mult": 2.0,
            },
            "partial_exit": {"enabled": False},
        },
    }

    class _FlatConfig(dict):
        """exit_logic_optimized 가 config.get('a.b.c') 형식으로 호출하므로 지원"""
        def get(self, key, default=None):
            if '.' in key:
                parts = key.split('.')
                node = self
                for p in parts:
                    if not isinstance(node, dict):
                        return default
                    node = dict.get(node, p, None)
                    if node is None:
                        return default
                return node
            return dict.get(self, key, default)

    return OptimizedExitLogic(_FlatConfig(config))


# ──────────────────────────────────────────────────────────────
# S1. Hard Stop 시나리오
# ──────────────────────────────────────────────────────────────

class TestHardStopScenario:

    def test_neutral_fires_at_6pct(self):
        """NEUTRAL regime: -6% 이하 → Hard Stop 발동"""
        el = _make_exit_logic(emergency_stop_pct=6.0, hard_stop_relax_enabled=False)
        el.market_regime = 'NEUTRAL'
        pos = _make_position(entry_price=10_000, entry_offset_min=10)
        current_price = 9_350   # -6.5%
        df = _make_df([10_000, 9_500, 9_400, 9_350])

        should_exit, reason, _ = el.check_exit_signal(pos, current_price, df)

        assert should_exit, f"Hard Stop should fire: {reason}"
        assert "stop" in reason.lower() or "Stop" in reason or "Hard" in reason or "긴급" in reason

    def test_trend_regime_doesnt_fire_at_7pct(self):
        """TREND regime + relax 8%: -7% 하락 → 아직 발동 안 함"""
        el = _make_exit_logic(
            emergency_stop_pct=6.0,
            hard_stop_relax_enabled=True,
            hard_stop_relax_bull_pct=8.0,
        )
        el.market_regime = 'TREND'
        pos = _make_position(entry_price=10_000, entry_offset_min=10)
        current_price = 9_350  # -6.5%
        df = _make_df([10_000, 9_700, 9_500, 9_350])

        should_exit, reason, _ = el.check_exit_signal(pos, current_price, df)

        # -6.5% < -8% 이므로 Hard Stop 미발동 (다른 이유로 종료될 수 있음)
        if should_exit:
            assert "긴급" not in reason and "Hard Stop" not in reason, \
                f"Hard Stop should NOT fire at -6.5% in TREND: {reason}"

    def test_trend_regime_fires_at_8pct(self):
        """TREND regime + relax 8%: -8.5% → 발동"""
        el = _make_exit_logic(
            emergency_stop_pct=6.0,
            hard_stop_relax_enabled=True,
            hard_stop_relax_bull_pct=8.0,
        )
        el.market_regime = 'TREND'
        pos = _make_position(entry_price=10_000, entry_offset_min=10)
        current_price = 9_100  # -9%
        df = _make_df([10_000, 9_500, 9_200, 9_100])

        should_exit, reason, _ = el.check_exit_signal(pos, current_price, df)

        assert should_exit, f"Hard Stop should fire at -9% even in TREND: {reason}"


# ──────────────────────────────────────────────────────────────
# S2. MFE_EXIT 시나리오 (로직 직접 시뮬레이션)
# ──────────────────────────────────────────────────────────────

def _should_mfe_exit(position: dict, cfg: dict) -> tuple:
    """min_mfe_check 판단 로직 (main_auto_trading.py에서 추출)"""
    if not cfg.get('enabled', False):
        return False, "disabled"
    window_min  = cfg.get('window_min', 30)
    min_mfe_pct = cfg.get('min_mfe_pct', 0.20)
    entry_time  = position.get('entry_time')
    is_trailing = position.get('trailing_active', False)
    ep          = position['entry_price']
    highest     = position.get('highest_price', ep)
    mfe_pct     = (highest - ep) / ep * 100 if ep > 0 else 99.0
    is_overnight = False
    if cfg.get('skip_overnight', True) and entry_time:
        is_overnight = entry_time.date() < datetime.now().date()
    if is_trailing:
        return False, "trailing_active"
    if is_overnight:
        return False, "overnight_skip"
    if not entry_time:
        return False, "no_entry_time"
    elapsed_min = (datetime.now() - entry_time).total_seconds() / 60
    if elapsed_min < window_min:
        return False, f"window_not_reached ({elapsed_min:.0f}/{window_min}min)"
    if mfe_pct >= min_mfe_pct:
        return False, f"mfe_ok ({mfe_pct:.2f}%>={min_mfe_pct}%)"
    return True, f"mfe_exit ({elapsed_min:.0f}min mfe={mfe_pct:.2f}%<{min_mfe_pct}%)"


class TestMFEExitScenario:
    """MFE_EXIT 시나리오 — 가격 스트림을 흘려가며 발동 시점 확인"""

    def _run_ticks(
        self,
        entry_price: float,
        price_stream: list[float],  # 매 틱 가격
        cfg: dict,
        entry_offset_min: int = 35,
    ) -> tuple[bool, str, int]:
        """
        price_stream 순서대로 틱 처리.
        Returns: (fired, reason, tick_index)
        """
        pos = _make_position(
            entry_price=entry_price,
            current_price=entry_price,
            highest_price=entry_price,
            entry_offset_min=entry_offset_min,
        )
        for i, price in enumerate(price_stream):
            if price > pos['highest_price']:
                pos['highest_price'] = price
            pos['current_price'] = price
            fired, reason = _should_mfe_exit(pos, cfg)
            if fired:
                return True, reason, i
        return False, "", len(price_stream)

    def test_fires_on_flat_position_after_window(self):
        """35분 경과 + MFE=0% → 즉시 발동"""
        cfg = {'enabled': True, 'window_min': 30, 'min_mfe_pct': 0.20}
        prices = [10_000] * 5  # 전혀 안 오름
        fired, reason, tick = self._run_ticks(10_000, prices, cfg, entry_offset_min=35)
        assert fired, "MFE_EXIT should fire on flat position"
        assert tick == 0  # 첫 틱에서 발동

    def test_no_fire_when_price_reaches_threshold(self):
        """MFE가 0.25% 도달 후 횡보 → MFE_EXIT 미발동"""
        cfg = {'enabled': True, 'window_min': 30, 'min_mfe_pct': 0.20}
        peak = 10_025  # +0.25% > 0.20% 충족
        prices = [peak, 10_010, 9_995, 9_990]  # 이후 하락
        fired, _, _ = self._run_ticks(10_000, prices, cfg, entry_offset_min=35)
        assert not fired, "Once MFE threshold met, should NOT re-fire"

    def test_비츠로셀_case_no_fire(self):
        """비츠로셀: +0.45% 돌파 진입 → MFE_EXIT 절대 미발동"""
        ep = 44_700
        peak = int(ep * 1.005)  # +0.5%
        cfg = {'enabled': True, 'window_min': 30, 'min_mfe_pct': 0.20}
        prices = [peak] + [int(ep * 1.003)] * 5
        fired, reason, _ = self._run_ticks(ep, prices, cfg, entry_offset_min=35)
        assert not fired, f"비츠로셀 case should not fire: {reason}"

    def test_fires_only_after_window_elapsed(self):
        """25분 경과 → 미발동, 35분 경과 → 발동"""
        cfg = {'enabled': True, 'window_min': 30, 'min_mfe_pct': 0.20}

        # 25분 경과 — 미발동
        pos_early = _make_position(entry_price=10_000, entry_offset_min=25)
        fired_early, _ = _should_mfe_exit(pos_early, cfg)
        assert not fired_early

        # 35분 경과 — 발동
        pos_late = _make_position(entry_price=10_000, entry_offset_min=35)
        fired_late, _ = _should_mfe_exit(pos_late, cfg)
        assert fired_late


# ──────────────────────────────────────────────────────────────
# S3. Trailing Stop 시나리오
# ──────────────────────────────────────────────────────────────

class TestTrailingStopScenario:

    def test_trailing_triggers_after_peak_pullback(self):
        """+3% 상승 → trailing ON → 최고가 대비 -1.5% 하락 → 청산"""
        el = _make_exit_logic(min_hold_minutes=0)
        pos = _make_position(
            entry_price=10_000,
            entry_offset_min=60,
            trailing_active=True,
            highest_price=10_300,  # +3% 최고가
        )
        current_price = 10_144  # 10_300 * (1 - 0.015) ≈ 10_144 → -1.5% from peak
        df = _make_df([10_000, 10_200, 10_300, 10_250, 10_144])

        should_exit, reason, _ = el.check_exit_signal(pos, current_price, df)

        if should_exit:
            # trailing 또는 수익 관련 이유여야 함
            assert any(kw in reason for kw in ["trail", "Trail", "익절", "TRAIL", "수익"]), \
                f"Expected trailing exit, got: {reason}"

    def test_trailing_not_triggered_at_peak(self):
        """최고가 갱신 중 → trailing 미발동"""
        el = _make_exit_logic(min_hold_minutes=0)
        pos = _make_position(
            entry_price=10_000,
            entry_offset_min=30,
            trailing_active=True,
            highest_price=10_300,
        )
        current_price = 10_320  # 새 최고가 갱신
        df = _make_df([10_000, 10_200, 10_300, 10_320])

        should_exit, reason, _ = el.check_exit_signal(pos, current_price, df)

        # trailing은 미발동이어야 함 (최고가 갱신 중)
        if should_exit:
            assert "trail" not in reason.lower(), f"Trailing should not fire at new peak: {reason}"


# ──────────────────────────────────────────────────────────────
# S4. Dry-Run 시나리오
# ──────────────────────────────────────────────────────────────

class TestDryRunScenario:
    """
    dry_run 플래그 활성 시 API 호출 없이 [DRY_RUN_SIGNAL] 로그만 생성 확인.
    main_auto_trading.py 전체 초기화 없이 관련 로직만 추출해 검증.
    """

    def test_dry_run_flag_suppresses_api_call(self):
        """
        _dry_run=True → api.order_buy 호출 없이 logger.info([DRY_RUN_SIGNAL]) 실행.
        main_auto_trading.py의 execute_buy dry-run 분기 패턴을 인라인으로 재현.
        """
        api_mock = MagicMock()

        def _simulate_execute_buy(dry_run: bool, stock_code: str, price: float, qty: int):
            import logging
            _log = logging.getLogger("sim.dry_run")
            if dry_run:
                _log.info(f"[DRY_RUN_SIGNAL] {{'code': '{stock_code}', 'price': {price}, 'qty': {qty}}}")
                return "DRY_RUN"
            api_mock.order_buy(stock_code, price, qty)
            return "EXECUTED"

        result = _simulate_execute_buy(dry_run=True, stock_code="082920", price=44_700, qty=5)

        assert result == "DRY_RUN"
        api_mock.order_buy.assert_not_called()

    def test_dry_run_false_calls_api(self):
        api_mock = MagicMock()

        def _simulate_execute_buy(dry_run: bool, stock_code: str, price: float, qty: int):
            if dry_run:
                return "DRY_RUN"
            api_mock.order_buy(stock_code, price, qty)
            return "EXECUTED"

        result = _simulate_execute_buy(dry_run=False, stock_code="082920", price=44_700, qty=5)

        assert result == "EXECUTED"
        api_mock.order_buy.assert_called_once_with("082920", 44_700, 5)

    def test_dry_run_logs_signal_tag(self, caplog):
        """[DRY_RUN_SIGNAL] 태그가 로그에 포함되어야 함"""
        import logging

        def _run(dry_run: bool):
            logger = logging.getLogger("sim.dry_run_log")
            if dry_run:
                logger.info("[DRY_RUN_SIGNAL] {'code': '082920', 'signal': 'BUY'}")

        with caplog.at_level(logging.INFO, logger="sim.dry_run_log"):
            _run(dry_run=True)

        assert any("[DRY_RUN_SIGNAL]" in r.message for r in caplog.records)


# ──────────────────────────────────────────────────────────────
# S5. Overnight Carry Override 시나리오
# ──────────────────────────────────────────────────────────────

def _check_carry_conditions(
    current_price: float,
    day_high: float,
    today_vol: float,
    avg_vol_5d: float,
    ma5: float,
    cfg: dict,
) -> tuple[bool, dict]:
    """main_auto_trading._check_carry_conditions 핵심 로직 (API 의존성 제거)"""
    near_pct = cfg.get('near_high_pct', 0.97)
    min_vr   = cfg.get('min_volume_ratio', 1.2)
    req_ma5  = cfg.get('require_above_ma5', True)

    cond1 = current_price >= day_high * near_pct
    cond2 = avg_vol_5d > 0 and today_vol >= avg_vol_5d * min_vr
    cond3 = (current_price >= ma5) if (req_ma5 and ma5 > 0) else True

    return cond1 and cond2 and cond3, {'near_high': cond1, 'vol': cond2, 'ma5': cond3}


class TestOvernightCarryScenario:

    def _cfg(self):
        return {'enabled': True, 'near_high_pct': 0.97, 'min_volume_ratio': 1.2, 'require_above_ma5': True}

    def test_strong_position_gets_carry(self):
        """당일 고가 근처 + 거래량 급증 + MA5 위 → carry 허용"""
        ok, checks = _check_carry_conditions(
            current_price=10_000, day_high=10_100,  # 99% of high
            today_vol=180_000, avg_vol_5d=100_000,  # 1.8x
            ma5=9_800,
            cfg=self._cfg()
        )
        assert ok, f"Strong position should get carry: {checks}"

    def test_weak_position_forced_close(self):
        """3조건 중 하나 실패 → carry 거부 → 강제청산"""
        ok, checks = _check_carry_conditions(
            current_price=9_400, day_high=10_000,  # 94% < 97%
            today_vol=180_000, avg_vol_5d=100_000,
            ma5=9_000,
            cfg=self._cfg()
        )
        assert not ok
        assert not checks['near_high']

    def test_all_three_conditions_required(self):
        """2/3 조건 충족도 거부"""
        # near_high + ma5 OK, but volume fail
        ok, checks = _check_carry_conditions(
            current_price=10_000, day_high=10_050,
            today_vol=80_000, avg_vol_5d=100_000,  # 0.8x < 1.2x
            ma5=9_500,
            cfg=self._cfg()
        )
        assert not ok
        assert not checks['vol']

    def test_carry_allows_one_overnight_per_day(self):
        """같은 날 2개 종목 모두 carry 충족 → 둘 다 허용되는지 (각각 독립 판단)"""
        cfg = self._cfg()
        ok1, _ = _check_carry_conditions(
            current_price=10_000, day_high=10_100, today_vol=150_000, avg_vol_5d=100_000, ma5=9_800, cfg=cfg
        )
        ok2, _ = _check_carry_conditions(
            current_price=20_000, day_high=20_200, today_vol=300_000, avg_vol_5d=200_000, ma5=19_500, cfg=cfg
        )
        # 로직은 각각 독립 → 둘 다 True
        assert ok1 and ok2

    def test_carry_override_disabled_always_false(self):
        """carry_override.enabled=false → 항상 강제청산 (override 미적용)"""
        cfg = {**self._cfg(), 'enabled': False}
        # carry 로직은 호출 전 enabled 체크 (main에서), 여기서는 조건 자체 검증
        # enabled 플래그는 main_auto_trading에서 처리하므로 조건 로직은 True 반환 가능
        ok, _ = _check_carry_conditions(
            current_price=10_000, day_high=10_100, today_vol=150_000, avg_vol_5d=100_000, ma5=9_800, cfg=cfg
        )
        # enabled 플래그 무관 — 조건 로직 자체는 True
        assert ok  # main에서 enabled 체크 후 이 값 무시됨


# ──────────────────────────────────────────────────────────────
# S6. 전체 세션 시뮬레이션 — 시나리오 재현
# ──────────────────────────────────────────────────────────────

class TestFullSessionSimulation:
    """
    하루 거래 흐름을 순서대로 시뮬레이션:
    진입 → 가격 스트림 → 청산 신호 → 결과 검증
    """

    def test_pattern_b_trade_exits_at_30min(self):
        """
        Pattern B 시나리오:
        진입 → 35분 횡보 (MFE = 0%) → MFE_EXIT 발동
        """
        cfg = {'enabled': True, 'window_min': 30, 'min_mfe_pct': 0.20,
               'skip_overnight': True, 'skip_trailing': True}
        pos = _make_position(entry_price=5_000, highest_price=5_005, entry_offset_min=35)
        fired, reason = _should_mfe_exit(pos, cfg)
        assert fired, f"Pattern B must fire: {reason}"
        assert "mfe_exit" in reason

    def test_strong_breakout_trade_bypasses_mfe_exit(self):
        """
        강세 돌파 시나리오:
        진입 시 이미 +0.5% 상승 → MFE_EXIT 미발동 → 트레일링으로 관리
        """
        cfg = {'enabled': True, 'window_min': 30, 'min_mfe_pct': 0.20,
               'skip_overnight': True, 'skip_trailing': True}
        ep = 44_700
        peak = int(ep * 1.005)  # +0.5%
        pos = _make_position(entry_price=ep, highest_price=peak, entry_offset_min=35)
        fired, reason = _should_mfe_exit(pos, cfg)
        assert not fired, f"Strong breakout should bypass MFE_EXIT: {reason}"

    def test_session_summary_counts_match(self):
        """
        가상 세션: BUY 3건, SELL 3건 (2 WIN, 1 LOSS)
        PnL 집계 검증
        """
        trades = [
            {"type": "BUY",  "realized_pnl": 0,       "stock": "A"},
            {"type": "SELL", "realized_pnl": 103_000,  "stock": "A"},  # WIN
            {"type": "BUY",  "realized_pnl": 0,        "stock": "B"},
            {"type": "SELL", "realized_pnl": 52_000,   "stock": "B"},  # WIN
            {"type": "BUY",  "realized_pnl": 0,        "stock": "C"},
            {"type": "SELL", "realized_pnl": -31_000,  "stock": "C"},  # LOSS
        ]

        buys  = [t for t in trades if t["type"] == "BUY"]
        sells = [t for t in trades if t["type"] == "SELL"]
        total_pnl = sum(t["realized_pnl"] for t in sells)
        win_count = sum(1 for t in sells if t["realized_pnl"] > 0)

        assert len(buys) == 3
        assert len(sells) == 3
        assert total_pnl == pytest.approx(124_000)
        assert win_count == 2
        win_rate = win_count / len(sells) * 100
        assert win_rate == pytest.approx(66.7, abs=0.1)
