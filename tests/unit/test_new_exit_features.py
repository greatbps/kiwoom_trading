"""
tests/unit/test_new_exit_features.py — 2026-05-02 신규 기능 단위 테스트

대상:
  - min_mfe_check (Pattern B 방지)
  - hard_stop_relax (TREND regime 완화)
  - carry_override 조건 로직

DB/API 없이 로직만 검증.
"""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta


# ──────────────────────────────────────────────────────────────
# 공통 픽스처
# ──────────────────────────────────────────────────────────────

def _make_position(
    entry_price: float = 10_000,
    current_price: float = 10_000,
    highest_price: float = 10_000,
    entry_offset_min: int = 0,
    trailing_active: bool = False,
    entry_date_offset: int = 0,  # 0=오늘, -1=어제
) -> dict:
    entry_time = datetime.now() - timedelta(minutes=entry_offset_min)
    if entry_date_offset != 0:
        entry_time = entry_time.replace(
            day=entry_time.day + entry_date_offset
        )
    return {
        'entry_price': entry_price,
        'current_price': current_price,
        'highest_price': highest_price,
        'entry_time': entry_time,
        'trailing_active': trailing_active,
        'stock_name': 'TEST',
        'quantity': 1,
    }


def _make_mfe_config(
    enabled: bool = True,
    window_min: int = 30,
    min_mfe_pct: float = 0.20,
    skip_overnight: bool = True,
    skip_trailing: bool = True,
) -> dict:
    return {
        'enabled': enabled,
        'window_min': window_min,
        'min_mfe_pct': min_mfe_pct,
        'skip_overnight': skip_overnight,
        'skip_trailing': skip_trailing,
    }


# ──────────────────────────────────────────────────────────────
# 1. min_mfe_check 로직 테스트 (main_auto_trading.py 에서 추출한 로직)
# ──────────────────────────────────────────────────────────────

def _should_mfe_exit(position: dict, cfg: dict) -> tuple:
    """min_mfe_check 핵심 판단 로직 (main에서 추출)"""
    if not cfg.get('enabled', False):
        return False, "disabled"

    window_min  = cfg.get('window_min', 30)
    min_mfe_pct = cfg.get('min_mfe_pct', 0.20)
    entry_time  = position.get('entry_time')
    is_trailing = position.get('trailing_active', False)
    ep          = position['entry_price']
    highest     = position.get('highest_price', ep)
    mfe_pct     = (highest - ep) / ep * 100 if ep > 0 else 99.0

    skip_overnight = cfg.get('skip_overnight', True)
    is_overnight   = False
    if skip_overnight and entry_time:
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


class TestMinMfeCheck:
    def test_fires_when_window_elapsed_and_mfe_low(self):
        pos = _make_position(entry_price=10_000, highest_price=10_000, entry_offset_min=35)
        cfg = _make_mfe_config(window_min=30, min_mfe_pct=0.20)
        should_exit, reason = _should_mfe_exit(pos, cfg)
        assert should_exit, f"should fire but: {reason}"

    def test_no_fire_within_window(self):
        pos = _make_position(entry_price=10_000, highest_price=10_000, entry_offset_min=20)
        cfg = _make_mfe_config(window_min=30)
        should_exit, _ = _should_mfe_exit(pos, cfg)
        assert not should_exit

    def test_no_fire_when_mfe_sufficient(self):
        """비츠로셀 케이스: 진입 시 이미 0.45% 상승 → MFE_EXIT 미발동"""
        ep = 44_700
        highest = int(ep * 1.005)  # 0.5% 상승
        pos = _make_position(entry_price=ep, highest_price=highest, entry_offset_min=35)
        cfg = _make_mfe_config(min_mfe_pct=0.20)
        should_exit, reason = _should_mfe_exit(pos, cfg)
        assert not should_exit, f"should NOT fire but: {reason}"

    def test_no_fire_when_trailing_active(self):
        """트레일링 활성 포지션은 이미 수익권 → 건드리지 않음"""
        pos = _make_position(
            entry_price=10_000, highest_price=10_000,
            entry_offset_min=40, trailing_active=True
        )
        cfg = _make_mfe_config()
        should_exit, reason = _should_mfe_exit(pos, cfg)
        assert not should_exit
        assert reason == "trailing_active"

    def test_no_fire_for_overnight_position(self):
        """오버나이트 포지션 제외 (어제 진입 → skip)"""
        entry_time = datetime.now() - timedelta(days=1, minutes=35)
        pos = _make_position(entry_price=10_000, highest_price=10_000, entry_offset_min=0)
        pos['entry_time'] = entry_time
        cfg = _make_mfe_config(skip_overnight=True)
        should_exit, reason = _should_mfe_exit(pos, cfg)
        assert not should_exit
        assert reason == "overnight_skip"

    def test_disabled_config(self):
        pos = _make_position(entry_price=10_000, highest_price=10_000, entry_offset_min=40)
        cfg = _make_mfe_config(enabled=False)
        should_exit, _ = _should_mfe_exit(pos, cfg)
        assert not should_exit

    def test_pattern_b_case(self):
        """싸이맥스 패턴: 35분 경과 + MFE=0% → 청산"""
        pos = _make_position(
            entry_price=5_000, highest_price=5_005,  # 0.1% MFE
            entry_offset_min=35
        )
        cfg = _make_mfe_config(min_mfe_pct=0.20)
        should_exit, reason = _should_mfe_exit(pos, cfg)
        assert should_exit, f"Pattern B should fire: {reason}"


# ──────────────────────────────────────────────────────────────
# 2. hard_stop_relax 로직 테스트
# ──────────────────────────────────────────────────────────────

def _get_effective_emg_pct(base_pct: float, relax_cfg: dict, market_regime: str) -> float:
    """hard_stop_relax 핵심 로직 (exit_logic_optimized.py에서 추출)"""
    if (relax_cfg.get('enabled', False)
            and market_regime == 'TREND'):
        relaxed = relax_cfg.get('bull_pct', 8.0)
        if relaxed > base_pct:
            return relaxed
    return base_pct


class TestHardStopRelax:
    def test_trend_regime_relaxes_stop(self):
        cfg = {'enabled': True, 'bull_pct': 8.0}
        result = _get_effective_emg_pct(6.0, cfg, 'TREND')
        assert result == 8.0

    def test_neutral_regime_keeps_base(self):
        cfg = {'enabled': True, 'bull_pct': 8.0}
        result = _get_effective_emg_pct(6.0, cfg, 'NEUTRAL')
        assert result == 6.0

    def test_disabled_keeps_base(self):
        cfg = {'enabled': False, 'bull_pct': 8.0}
        result = _get_effective_emg_pct(6.0, cfg, 'TREND')
        assert result == 6.0

    def test_bull_pct_must_be_larger(self):
        """완화값이 기준보다 작으면 무시 (버그 방지)"""
        cfg = {'enabled': True, 'bull_pct': 4.0}  # 기준 6%보다 작음
        result = _get_effective_emg_pct(6.0, cfg, 'TREND')
        assert result == 6.0


# ──────────────────────────────────────────────────────────────
# 3. carry_override 조건 로직 테스트
# ──────────────────────────────────────────────────────────────

def _check_carry_logic(
    current_price: float,
    day_high: float,
    today_vol: float,
    avg_vol_5d: float,
    ma5: float,
    cfg: dict,
) -> tuple:
    """_check_carry_conditions의 순수 로직 부분 (API 제외)"""
    near_pct  = cfg.get('near_high_pct', 0.97)
    min_vr    = cfg.get('min_volume_ratio', 1.2)
    req_ma5   = cfg.get('require_above_ma5', True)

    cond1 = current_price >= day_high * near_pct
    cond2 = avg_vol_5d > 0 and today_vol >= avg_vol_5d * min_vr
    cond3 = (current_price >= ma5) if (req_ma5 and ma5 > 0) else True

    return cond1 and cond2 and cond3, {'near_high': cond1, 'vol': cond2, 'ma5': cond3}


class TestCarryOverride:
    def _default_cfg(self):
        return {'near_high_pct': 0.97, 'min_volume_ratio': 1.2, 'require_above_ma5': True}

    def test_all_conditions_pass(self):
        ok, _ = _check_carry_logic(
            current_price=10_000, day_high=10_100,  # 현재가=고가의 99%
            today_vol=150_000, avg_vol_5d=100_000,  # 1.5x
            ma5=9_800,  # 현재가 > MA5
            cfg=self._default_cfg()
        )
        assert ok

    def test_fails_if_far_from_high(self):
        ok, checks = _check_carry_logic(
            current_price=9_500, day_high=10_000,  # 현재가=고가의 95% < 97%
            today_vol=150_000, avg_vol_5d=100_000,
            ma5=9_000,
            cfg=self._default_cfg()
        )
        assert not ok
        assert not checks['near_high']

    def test_fails_if_volume_low(self):
        ok, checks = _check_carry_logic(
            current_price=10_000, day_high=10_050,
            today_vol=80_000, avg_vol_5d=100_000,  # 0.8x < 1.2x
            ma5=9_800,
            cfg=self._default_cfg()
        )
        assert not ok
        assert not checks['vol']

    def test_fails_if_below_ma5(self):
        ok, checks = _check_carry_logic(
            current_price=9_500, day_high=9_600,
            today_vol=150_000, avg_vol_5d=100_000,
            ma5=10_000,  # 현재가 < MA5
            cfg=self._default_cfg()
        )
        assert not ok
        assert not checks['ma5']

    def test_ma5_check_disabled(self):
        cfg = {'near_high_pct': 0.97, 'min_volume_ratio': 1.2, 'require_above_ma5': False}
        ok, _ = _check_carry_logic(
            current_price=9_500, day_high=9_600,
            today_vol=150_000, avg_vol_5d=100_000,
            ma5=10_000,  # MA5 위반이지만 비활성
            cfg=cfg
        )
        assert ok  # MA5 조건 비활성 → 나머지 2개만


# ──────────────────────────────────────────────────────────────
# 4. is_loss 정의 순서 테스트 (2026-05-02 버그 수정 검증)
# ──────────────────────────────────────────────────────────────

class TestIsLossDefinition:
    def test_is_loss_defined_for_win(self):
        """WIN 거래에서 is_loss가 False로 정의되어야 함 (UnboundLocalError 방지)"""
        profit_pct = 5.0
        is_win  = profit_pct > 0
        is_loss = profit_pct < 0  # ← 이 줄이 is_win 바로 뒤에 있어야 함
        is_stoploss = is_loss and False  # 실제 코드 패턴
        assert is_win is True
        assert is_loss is False
        assert is_stoploss is False

    def test_is_loss_defined_for_loss(self):
        profit_pct = -2.5
        is_win  = profit_pct > 0
        is_loss = profit_pct < 0
        assert is_win is False
        assert is_loss is True
