"""
tests/unit/test_swing_holding_manager.py

HoldingManager.evaluate() 단위 테스트

커버 대상 (Fix 2, 3):
  1. MA5 1일 이탈       → EXIT 아님 (2일 연속 필요)
  2. MA5 2일 연속 이탈  → EXIT
  3. MA5 이탈 후 회복   → 카운터 리셋, HOLD 유지
  4. MA20 이탈(하드)    → 즉시 EXIT (1일도 충분)
  5. ADD: 양봉 + 전일比 상승 → ADD
  6. ADD: 음봉           → HOLD (ADD 차단)
  7. ADD: 양봉이나 전일比 하락 → HOLD (ADD 차단)
  8. 드로우다운 초과      → EXIT
  9. 최대 보유일 초과     → EXIT
  10. TRAIL 조건 충족    → TRAIL
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import pandas as pd
import numpy as np
from analyzers.swing.holding_manager import HoldingManager
from analyzers.swing.state_machine import SwingPosition, SwingState


_CFG = {
    "swing": {
        "hold": {
            "drawdown_exit_pct": 5.0,
            "max_hold_days": 30,
            "trail_start_days": 7,
            "trail_start_profit_pct": 7.0,
            "ma5_consecutive_days": 2,
            "ma20_hard_exit": True,
        }
    }
}


def _make_df(closes: list[float], opens: list[float] = None) -> pd.DataFrame:
    """OHLCV DataFrame 생성 헬퍼. opens 없으면 close와 동일."""
    n = len(closes)
    if opens is None:
        opens = closes[:]
    highs = [max(o, c) * 1.005 for o, c in zip(opens, closes)]
    lows  = [min(o, c) * 0.995 for o, c in zip(opens, closes)]
    return pd.DataFrame({
        "open":   opens,
        "high":   highs,
        "low":    lows,
        "close":  closes,
        "volume": [100_000] * n,
    })


def _pos(entry_price: float = 10_000, holding_days: int = 0, add_count: int = 0,
         score: float = 6.0, max_profit_pct: float = 0.0, ma5_below_days: int = 0) -> SwingPosition:
    """SwingPosition 테스트 픽스처."""
    p = SwingPosition(
        stock_code="000001",
        stock_name="테스트",
        state=SwingState.HOLD,
        entry_price=entry_price,
        score=score,
    )
    p.holding_days = holding_days
    p.add_count = add_count
    p.max_profit_pct = max_profit_pct
    p.ma5_below_days = ma5_below_days
    return p


def _df_uptrend_dip() -> pd.DataFrame:
    """
    MA5 아래이지만 MA20 위인 시나리오.

    구성: 25봉
      indices 0-19: 9,000 (20봉)
      indices 20-23: 10,000 (4봉)
      index 24: 9,300 (마지막)

    MA5  = (10,000×4 + 9,300) / 5 = 9,860 → close(9,300) < MA5 ✓
    MA20 사용 구간 = indices 5-24:
      [9,000]×15 + [10,000]×4 + 9,300
      sum = 135,000 + 40,000 + 9,300 = 184,300
      MA20 = 184,300 / 20 = 9,215 → close(9,300) > MA20 ✓ (마진 85p)
    entry_price = 9,000 → profit = +3.3%, drawdown = 0
    """
    closes = [9_000.0] * 20 + [10_000.0] * 4 + [9_300.0]
    opens  = closes[:]
    return _make_df(closes, opens)


def _df_flat_then_crash(crash_close: float = 8_500.0) -> pd.DataFrame:
    """
    MA5 & MA20 모두 아래인 시나리오 (하드 EXIT 테스트용):
      24봉: 10,000 / 마지막: crash_close
      MA5  ≈ (10,000*4 + crash)/5
      MA20 ≈ (10,000*19 + crash)/20 (→ 거의 10,000)
    entry_price=crash_close 기준으로 설정 시 drawdown=0
    """
    closes = [10_000.0] * 24 + [crash_close]
    opens  = closes[:]
    return _make_df(closes, opens)


class TestMA5Exit:
    """MA5 연속 이탈 EXIT 로직."""

    def _mgr(self):
        return HoldingManager(_CFG)

    def test_case1_single_day_below_ma5_no_exit(self):
        """Case 1: MA5 1일 이탈(MA20 위) → EXIT 아님 (2일 필요)."""
        mgr = self._mgr()
        # MA5=9,840 / MA20=9,160 / close=9,200 → MA5 아래, MA20 위
        df = _df_uptrend_dip()
        pos = _pos(entry_price=9_000.0, holding_days=5)
        result = mgr.evaluate(pos, df)
        assert result != 'EXIT', f"1일 이탈로는 EXIT 안 됨, 실제={result}"
        assert pos.ma5_below_days == 1

    def test_case2_two_consecutive_days_exit(self):
        """Case 2: MA5 2일 연속 이탈 → EXIT."""
        mgr = self._mgr()
        df = _df_uptrend_dip()
        pos = _pos(entry_price=9_000.0, holding_days=5, ma5_below_days=1)
        result = mgr.evaluate(pos, df)
        assert result == 'EXIT'
        assert pos.ma5_below_days == 2

    def test_case3_recovery_resets_counter(self):
        """Case 3: MA5 이탈 후 회복 → 카운터 0 리셋."""
        mgr = self._mgr()
        # 마지막 봉이 MA5 위 (10,100)
        closes = [10_000.0] * 25
        closes[-1] = 10_100.0
        df = _make_df(closes)
        pos = _pos(entry_price=10_000.0, holding_days=5, ma5_below_days=1)
        result = mgr.evaluate(pos, df)
        assert pos.ma5_below_days == 0
        assert result != 'EXIT'


class TestMA20HardExit:
    """MA20 이탈 즉시 강제 EXIT."""

    def _mgr(self):
        return HoldingManager(_CFG)

    def test_case4_ma20_below_exits_immediately(self):
        """Case 4: close < MA20 → 1일만에도 즉시 EXIT.

        _df_flat_then_crash(8500): MA20 ≈ 9,982, close=8,500 → 하드 EXIT.
        entry_price=8,400 (close 아래) → drawdown=0 → EXIT 이유=MA20 하드.
        """
        mgr = self._mgr()
        df = _df_flat_then_crash(crash_close=8_500.0)
        pos = _pos(entry_price=8_400.0, holding_days=3, ma5_below_days=0)
        result = mgr.evaluate(pos, df)
        assert result == 'EXIT'

    def test_case4b_ma20_disabled_no_hard_exit(self):
        """MA20 하드 EXIT 비활성화 시 → MA20 이탈해도 MA5 1일만이면 EXIT 아님.

        _df_flat_then_crash(9200):
          MA5 = (10,000*4 + 9,200)/5 = 9,840 → close < MA5 ✓
          MA20= (10,000*19 + 9,200)/20 = 9,960 → close < MA20 ✓
        entry_price=9,100 → profit≈1%, drawdown=0
        ma20_hard_exit=False → MA20 하드 미발동
        MA5 1일 이탈 → ma5_below_days=1, not EXIT.
        """
        cfg_no_ma20 = {
            "swing": {
                "hold": {
                    "drawdown_exit_pct": 5.0,
                    "max_hold_days": 30,
                    "trail_start_days": 7,
                    "trail_start_profit_pct": 7.0,
                    "ma5_consecutive_days": 2,
                    "ma20_hard_exit": False,
                }
            }
        }
        mgr = HoldingManager(cfg_no_ma20)
        df = _df_flat_then_crash(crash_close=9_200.0)
        pos = _pos(entry_price=9_100.0, holding_days=3)
        result = mgr.evaluate(pos, df)
        # MA20 하드 비활성화 + MA5 1일 이탈 → EXIT 아님
        assert result != 'EXIT', f"MA20 비활성화+MA5 1일이탈 → HOLD 예상, 실제={result}"


class TestAddCondition:
    """ADD 조건: MA5 근접 + 양봉 + 전일比 상승."""

    def _mgr(self):
        return HoldingManager(_CFG)

    def _df_near_ma5(self, near_close: float, near_open: float, prev_close_offset: float = 0) -> pd.DataFrame:
        """
        MA5 근접 ADD 조건 테스트용 DataFrame.

        구조:
          20봉: 10,000 / 4봉: 10,500 / 마지막: near_close
          MA5  = (10,500*4 + near_close) / 5 ≈ 10,490 (near_close=10,450 시)
          MA20 = (10,000*16 + 10,500*3 + prev + near) / 20 ≈ 10,100
          → close(10,450) > MA20(10,100) 보장 (MA20 하드 EXIT 방지)

        near_close ≈ 10,450 → MA5 근접 ±1% 범위
        prev_close_offset: 24번째 봉(10,500 기준) 조정 → 전일 대비 비교용
        """
        base_closes = [10_000.0] * 20 + [10_500.0] * 4
        base_opens  = base_closes[:]
        if prev_close_offset != 0:
            base_closes[-1] = base_closes[-1] + prev_close_offset
        closes = base_closes + [near_close]
        opens  = base_opens  + [near_open]
        return _make_df(closes, opens)

    def test_case5_add_bullish_and_rising(self):
        """Case 5: MA5 근접 + 양봉 + 전일比 상승 → ADD.

        MA5 ≈ 10,490, close=10,450 (−0.38%), open=10,420 (양봉),
        prev_close=10,400 (−100 offset) → close > prev_close (상승) ✓
        """
        mgr = self._mgr()
        df = self._df_near_ma5(near_close=10_450.0, near_open=10_420.0, prev_close_offset=-100.0)
        pos = _pos(entry_price=10_000.0, holding_days=5, add_count=0)
        result = mgr.evaluate(pos, df)
        assert result == 'ADD', f"양봉+전일比 상승 → ADD 예상, 실제={result}"

    def test_case6_add_blocked_bearish(self):
        """Case 6: MA5 근접이지만 음봉(open > close) → HOLD."""
        mgr = self._mgr()
        df = self._df_near_ma5(near_close=10_450.0, near_open=10_480.0, prev_close_offset=-100.0)
        pos = _pos(entry_price=10_000.0, holding_days=5, add_count=0)
        result = mgr.evaluate(pos, df)
        assert result != 'ADD', "음봉이면 ADD 차단"

    def test_case7_add_blocked_falling_prev_close(self):
        """Case 7: 양봉이지만 전일比 하락 → HOLD.

        prev_close = 10,600 (+100 offset) > close=10,450 → 전일比 하락.
        """
        mgr = self._mgr()
        df = self._df_near_ma5(near_close=10_450.0, near_open=10_420.0, prev_close_offset=100.0)
        pos = _pos(entry_price=10_000.0, holding_days=5, add_count=0)
        result = mgr.evaluate(pos, df)
        assert result != 'ADD', "양봉이라도 전일比 하락이면 ADD 차단"

    def test_add_max_count_reached(self):
        """ADD 횟수 한도 초과 → HOLD."""
        mgr = self._mgr()
        df = self._df_near_ma5(near_close=10_450.0, near_open=10_420.0, prev_close_offset=-100.0)
        pos = _pos(entry_price=10_000.0, holding_days=5, add_count=2)
        result = mgr.evaluate(pos, df)
        assert result != 'ADD', "최대 추가매수 2회 초과 → ADD 차단"


class TestOtherExits:
    """드로우다운, 최대 보유일, TRAIL."""

    def _mgr(self):
        return HoldingManager(_CFG)

    def _flat_df(self, close: float = 10_000.0, n: int = 25) -> pd.DataFrame:
        return _make_df([close] * n)

    def test_case8_drawdown_exit(self):
        """Case 8: 드로우다운 5%+ → EXIT."""
        mgr = self._mgr()
        df = self._flat_df(9_400.0)  # MA5 아래이기도 하지만 드로우다운 먼저
        pos = _pos(entry_price=10_000.0, holding_days=5, max_profit_pct=10.0)
        # max_profit=10%, current=(9400-10000)/10000=-6%, drawdown=10-(-6)=16% → 즉시 EXIT
        result = mgr.evaluate(pos, df)
        assert result == 'EXIT'

    def test_case9_max_hold_days_exit(self):
        """Case 9: 최대 보유일 30일 → EXIT."""
        mgr = self._mgr()
        df = self._flat_df(10_100.0)  # MA5 위
        pos = _pos(entry_price=10_000.0, holding_days=30)
        result = mgr.evaluate(pos, df)
        assert result == 'EXIT'

    def test_case10_trail_condition(self):
        """Case 10: 보유 7일+ + 최고수익 7%+ → TRAIL."""
        mgr = self._mgr()
        df = self._flat_df(10_100.0)
        pos = _pos(entry_price=9_500.0, holding_days=7, max_profit_pct=8.0)
        result = mgr.evaluate(pos, df)
        assert result == 'TRAIL'

    def test_add_lot_size_first(self):
        """add_lot_size: 1차=0.30."""
        mgr = self._mgr()
        pos = _pos(add_count=0)
        assert mgr.add_lot_size(pos) == 0.30

    def test_add_lot_size_second(self):
        """add_lot_size: 2차=0.20."""
        mgr = self._mgr()
        pos = _pos(add_count=1)
        assert mgr.add_lot_size(pos) == 0.20
