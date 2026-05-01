"""
청산 로직 테스트 (trading/exit_logic_optimized.py)

검증 대상 (돈과 직결):
  - Hard Stop / 긴급 Hard Stop
  - 구조 손절 (structure stop)
  - BE 스탑 (TP2 이후로 지연됨 — 2026-05-01 변경)
  - R-TP1 (2R/25%), R-TP2 (4R/25%) — 변경된 배수 검증
  - trailing floor (stage별 원금/BE 보호)
  - ATR 트레일링
  - EOD 시간 청산
"""

import pytest
import pandas as pd
from datetime import datetime, timedelta
from trading.exit_logic_optimized import OptimizedExitLogic


# ─────────────────────────── 헬퍼 ───────────────────────────────────

class DotConfig:
    """dot-notation config — OptimizedExitLogic이 기대하는 인터페이스"""
    def __init__(self, data: dict):
        self._data = data

    def get(self, key_path: str, default=None):
        keys = key_path.split('.')
        cur = self._data
        for k in keys:
            if isinstance(cur, dict) and k in cur:
                cur = cur[k]
            else:
                return default
        return cur

    def get_section(self, section: str) -> dict:
        return self._data.get(section, {})


def make_config(overrides: dict = None) -> DotConfig:
    """기본 테스트 config 생성. overrides 딕셔너리로 특정 값만 덮어쓸 수 있다."""
    base = {
        'risk_control': {
            'min_hold_minutes': 5,
            'emergency_stop_pct': 6.0,
            'emergency_stop_candle_confirm': False,   # 테스트 단순화
            'structure_based_stop': {'enabled': True, 'max_stop_pct': 5.0},
            'be_stop_buffer_pct': 0.2,
            'a_grade_stop_buffer_pct': 0.5,
            'a_grade_stop_buffer_min_confidence': 0.7,
            'trailing_activation_pct': 2.0,
            'trailing_tiers': {
                'base_mult': 3.0,
                'tier1_profit': 5.0,
                'tier1_mult': 2.5,
                'tier2_profit': 8.0,
                'tier2_mult': 2.0,
            },
            'time_exit': {'bars': 10},
        },
        'eod_policy': {'enabled': False},
        'time_based_exit': {
            'loss_breakeven_exit_time': '15:00:00',
            'final_force_exit_time':    '15:10:00',
            'loss_breakeven_threshold_pct': 0.3,
        },
        'smc': {
            'a_grade_hold_extension': {
                'enabled': False,         # 필요한 테스트에서만 켬
                'max_bars_mult': 4,
                'no_progress_mfe_r': 2.0,
                'no_progress_bars': 30,
                'profit_lock_tiers': [
                    {'mfe_r': 2.0, 'floor_r': 0.5},
                    {'mfe_r': 3.5, 'floor_r': 1.5},
                    {'mfe_r': 5.0, 'floor_r': 3.0},
                ],
                'structure_exit': True,
            }
        },
    }
    if overrides:
        _deep_merge(base, overrides)
    return DotConfig(base)


def _deep_merge(base: dict, override: dict):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def make_position(
    entry_price: float,
    *,
    stop_price: float = None,
    minutes_ago: int = 10,
    partial_stage: int = 0,
    trailing_active: bool = False,
    highest_price: float = None,
    r_tp1_price: float = None,
    r_tp2_price: float = None,
    r_pct: float = 0.0,
    choch_grade: str = 'B',
    eq_grade: str = 'B',
    entry_confidence: float = 1.0,
    allow_overnight: bool = False,
) -> dict:
    entry_time = datetime.now() - timedelta(minutes=minutes_ago)
    return {
        'entry_price': entry_price,
        'avg_price': entry_price,
        'entry_time': entry_time,
        'highest_price': highest_price if highest_price is not None else entry_price,
        'trailing_active': trailing_active,
        'trailing_stop_price': None,
        'partial_exit_stage': partial_stage,
        'structure_stop_price': stop_price,
        'choch_grade': choch_grade,
        'choch_grade_log': choch_grade,
        'eq_grade': eq_grade,
        'entry_confidence': entry_confidence,
        'r_tp1_price': r_tp1_price,
        'r_tp2_price': r_tp2_price,
        'r_pct': r_pct,
        'allow_overnight_final_confirm': allow_overnight,
    }


def make_df(current_price: float, *, atr: float = 200.0, rows: int = 5) -> pd.DataFrame:
    """ATR이 고정된 최소 OHLCV DataFrame"""
    return pd.DataFrame({
        'open':   [current_price] * rows,
        'high':   [current_price * 1.005] * rows,
        'low':    [current_price * 0.995] * rows,
        'close':  [current_price] * rows,
        'volume': [100_000] * rows,
        'atr':    [atr] * rows,
        'vwap':   [current_price * 0.99] * rows,
    })


def make_df_with_candle_confirm(current_price: float, *, breakdown: bool, atr: float = 200.0):
    """긴급 Hard Stop 캔들 확인 테스트용 DataFrame.
    breakdown=True  → 종가 < 직전봉 저가 (붕괴 확인)
    breakdown=False → 종가 ≥ 직전봉 저가 (스파이크, 유예)
    """
    prev_low = current_price * 1.01   # 직전봉 저가를 현재가보다 높게
    last_close = current_price * (0.99 if breakdown else 1.02)
    return pd.DataFrame({
        'open':   [current_price] * 3,
        'high':   [current_price * 1.01] * 3,
        'low':    [current_price * 0.995, prev_low, current_price * 0.99],
        'close':  [current_price, current_price, last_close],
        'volume': [100_000] * 3,
        'atr':    [atr] * 3,
        'vwap':   [current_price * 0.99] * 3,
    })


# ─────────────────────── 픽스처 ─────────────────────────────────────

@pytest.fixture
def logic():
    return OptimizedExitLogic(make_config())


# ═══════════════════════════════════════════════════════════════════
# 1. 최소 락 (min_hold_minutes)
# ═══════════════════════════════════════════════════════════════════

class TestMinHoldLock:
    def test_no_exit_within_5min(self, logic):
        pos = make_position(10_000, stop_price=9_500, minutes_ago=3)
        should, reason, _ = logic.check_exit_signal(pos, 9_400, make_df(9_400))
        assert not should, "5분 이내에는 청산하면 안 된다"

    def test_exit_allowed_after_5min(self):
        cfg = make_config({'risk_control': {'emergency_stop_candle_confirm': False}})
        lg = OptimizedExitLogic(cfg)
        pos = make_position(10_000, stop_price=9_500, minutes_ago=10)
        should, reason, _ = lg.check_exit_signal(pos, 9_400, make_df(9_400))
        assert should, "5분 이후 구조 손절은 발동해야 한다"


# ═══════════════════════════════════════════════════════════════════
# 2. 긴급 Hard Stop (-6%)
# ═══════════════════════════════════════════════════════════════════

class TestEmergencyHardStop:
    def test_fires_at_minus6pct(self):
        cfg = make_config({'risk_control': {'emergency_stop_candle_confirm': False}})
        lg = OptimizedExitLogic(cfg)
        entry = 10_000
        price = int(entry * 0.93)   # -7% → 긴급 손절
        pos = make_position(entry, minutes_ago=10)
        should, reason, info = lg.check_exit_signal(pos, price, make_df(price))
        assert should
        assert 'HARD_STOP_EMERGENCY' in reason
        assert info['use_market_order'] is True, "긴급 손절은 반드시 시장가"

    def test_market_order_true(self):
        cfg = make_config({'risk_control': {'emergency_stop_candle_confirm': False}})
        lg = OptimizedExitLogic(cfg)
        pos = make_position(10_000, minutes_ago=10)
        _, _, info = lg.check_exit_signal(pos, 9_300, make_df(9_300))
        assert info['use_market_order'] is True

    def test_deferred_when_candle_not_confirmed(self):
        """캔들 확인 모드: 종가가 직전봉 저가 이상이면 1봉 유예"""
        # max_stop_pct=10% → fallback hard stop이 -7%에서 발동하지 않아야 함
        cfg = make_config({
            'risk_control': {
                'emergency_stop_candle_confirm': True,
                'structure_based_stop': {'enabled': True, 'max_stop_pct': 10.0},
            }
        })
        lg = OptimizedExitLogic(cfg)
        entry = 10_000
        price = int(entry * 0.93)   # -7%: 긴급(-6%) 임계 초과, 캔들 미확인 → 유예
        df = make_df_with_candle_confirm(price, breakdown=False)
        pos = make_position(entry, minutes_ago=10)
        should, _, _ = lg.check_exit_signal(pos, price, df)
        assert not should, "캔들이 미확인이면 긴급 손절을 유예해야 한다"

    def test_fires_when_candle_confirmed(self):
        cfg = make_config({'risk_control': {'emergency_stop_candle_confirm': True}})
        lg = OptimizedExitLogic(cfg)
        entry = 10_000
        price = int(entry * 0.93)
        df = make_df_with_candle_confirm(price, breakdown=True)
        pos = make_position(entry, minutes_ago=10)
        should, reason, _ = lg.check_exit_signal(pos, price, df)
        assert should
        assert 'HARD_STOP_EMERGENCY' in reason

    def test_not_triggered_at_minus5pct(self):
        """긴급 손절 기준(-6%)에 미달하면 발동하지 않는다"""
        cfg = make_config({'risk_control': {'emergency_stop_candle_confirm': False}})
        lg = OptimizedExitLogic(cfg)
        entry = 10_000
        price = int(entry * 0.945)   # -5.5% → 긴급(-6%) 미달, 구조 손절로 처리
        pos = make_position(entry, stop_price=9_500, minutes_ago=10)
        should, reason, _ = lg.check_exit_signal(pos, price, make_df(price))
        # 긴급 손절은 아니어야 함 (구조 손절 or 미발동)
        if should:
            assert 'HARD_STOP_EMERGENCY' not in reason


# ═══════════════════════════════════════════════════════════════════
# 3. 구조 손절 (structure stop)
# ═══════════════════════════════════════════════════════════════════

class TestStructureStop:
    def test_fires_when_price_below_stop(self, logic):
        entry = 10_000
        stop  = 9_700   # -3%
        pos = make_position(entry, stop_price=stop, minutes_ago=10)
        should, reason, info = logic.check_exit_signal(pos, 9_680, make_df(9_680))
        assert should
        assert 'STRUCTURE_STOP' in reason
        assert info['use_market_order'] is True

    def test_no_exit_above_stop(self, logic):
        entry = 10_000
        stop  = 9_700
        pos = make_position(entry, stop_price=stop, minutes_ago=10)
        should, _, _ = logic.check_exit_signal(pos, 9_750, make_df(9_750))
        assert not should

    def test_pct_hardstop_when_no_structure(self, logic):
        """구조 손절 없을 때 -5% 기반 Hard Stop 발동"""
        entry = 10_000
        pos = make_position(entry, stop_price=None, minutes_ago=10)
        should, reason, _ = logic.check_exit_signal(pos, 9_450, make_df(9_450))
        assert should
        assert 'HARD_STOP' in reason

    def test_structure_stop_capped_at_5pct(self, logic):
        """-8% 구조 손절은 -5%로 cap 되어야 한다"""
        entry = 10_000
        stop  = 9_100   # -9%: cap 이후 9_500 (-5%)이 실제 손절가
        pos = make_position(entry, stop_price=stop, minutes_ago=10)
        # -5% cap → 9_500 → 9_450에서 발동해야 함
        should, reason, _ = logic.check_exit_signal(pos, 9_450, make_df(9_450))
        assert should
        assert 'STRUCTURE_STOP' in reason

    def test_a_grade_stop_buffer_relaxes_stop(self):
        """A급 + confidence≥0.7 + TP1 전: 손절 0.5% 완화"""
        cfg = make_config({'risk_control': {
            'a_grade_stop_buffer_pct': 0.5,
            'a_grade_stop_buffer_min_confidence': 0.7,
        }})
        lg = OptimizedExitLogic(cfg)
        entry = 10_000
        stop  = 9_700   # -3%
        # 완화 후 실제 손절가 ≈ 9_700 × 0.995 = 9_651.5
        pos = make_position(entry, stop_price=stop, choch_grade='A',
                            eq_grade='A', entry_confidence=0.8, minutes_ago=10)
        # 9_680: 원래 손절가(9_700) 아래지만 완화 후 손절가(≈9_651) 위 → 미발동
        should, _, _ = lg.check_exit_signal(pos, 9_680, make_df(9_680))
        assert not should, "A급 완화 버퍼 내에서는 손절하지 않는다"


# ═══════════════════════════════════════════════════════════════════
# 4. BE 스탑 — TP2 이후로 지연 (2026-05-01 핵심 변경)
# ═══════════════════════════════════════════════════════════════════

class TestBeStop:
    def test_no_be_stop_after_tp1(self, logic):
        """TP1(stage=1) 이후에는 BE 스탑이 발동하면 안 된다"""
        entry = 10_000
        pos = make_position(entry, stop_price=9_700, partial_stage=1, minutes_ago=10)
        # entry + 0.1% — 구버전(TP1 후 BE)이면 손절이지만 신버전은 아님
        price = int(entry * 1.001)
        should, reason, _ = logic.check_exit_signal(pos, price, make_df(price))
        # BE_STOP이 발동되면 버그
        if should:
            assert 'BE_STOP' not in reason, \
                "TP1 이후(stage=1) BE 스탑은 발동하면 안 된다 — TP2 이후로 지연됨"

    def test_be_stop_fires_after_tp2(self, logic):
        """TP2(stage=2) 이후에는 BE 스탑이 발동해야 한다"""
        entry = 10_000
        pos = make_position(entry, stop_price=9_700, partial_stage=2,
                            trailing_active=True, highest_price=12_000, minutes_ago=10)
        # entry + 0.1%: entry+0.2%(BE) 미달 → 손절
        price = int(entry * 1.001)
        should, reason, _ = logic.check_exit_signal(pos, price, make_df(price))
        assert should
        assert 'BE_STOP' in reason

    def test_be_stop_not_fire_above_buffer(self, logic):
        """entry + 0.3% (버퍼 0.2% 초과) → BE 스탑 미발동"""
        entry = 10_000
        pos = make_position(entry, stop_price=9_700, partial_stage=2,
                            trailing_active=True, highest_price=12_000, minutes_ago=10)
        price = int(entry * 1.003)
        should, reason, _ = logic.check_exit_signal(pos, price, make_df(price))
        if should:
            assert 'BE_STOP' not in reason


# ═══════════════════════════════════════════════════════════════════
# 5. R-기반 부분 익절 (변경된 2R/4R, 25%)
# ═══════════════════════════════════════════════════════════════════

class TestRBasedPartialExit:
    @pytest.fixture
    def rtp_position(self):
        """2R/4R TP가 설정된 기본 포지션. 1R = 2% (entry 10000, stop 9800)"""
        entry = 10_000
        stop  = 9_800   # 1R = 200원 = 2%
        # TP1(2R) = 10_400, TP2(4R) = 10_800
        return make_position(
            entry, stop_price=stop, minutes_ago=10,
            r_tp1_price=10_400,
            r_tp2_price=10_800,
            r_pct=2.0,
        )

    def test_tp1_fires_at_2r(self, logic, rtp_position):
        """2R(10_400) 도달 시 TP1 발동 → 25% 부분 청산"""
        should, reason, info = logic.check_exit_signal(rtp_position, 10_400, make_df(10_400))
        assert not should, "부분 청산은 should_exit=False"
        assert info is not None
        assert info.get('partial_exit') is True
        assert info.get('exit_ratio') == pytest.approx(0.25), "TP1 청산 비율은 25%"
        assert info.get('stage') == 1
        assert 'R_TP1' in reason

    def test_tp1_does_not_fire_at_1r(self, logic, rtp_position):
        """1R(10_200) = 구버전(1.5R) 보다 낮은 위치 — 절대 발동하면 안 됨"""
        should, reason, info = logic.check_exit_signal(rtp_position, 10_200, make_df(10_200))
        # 부분 청산 발동 여부와 무관하게, 발동됐다면 stage 1 확인 불필요
        if info and info.get('partial_exit'):
            pytest.fail(f"1R에서 TP1이 발동됐다 — 2R로 올라간 기준이 적용되지 않음: {reason}")

    def test_tp1_does_not_fire_at_1_5r(self, logic, rtp_position):
        """1.5R(10_300) = 구버전 TP1 지점 — 신버전에서는 미발동"""
        price = 10_300   # 1.5R
        should, reason, info = logic.check_exit_signal(rtp_position, price, make_df(price))
        if info and info.get('partial_exit'):
            pytest.fail(f"1.5R에서 TP1이 발동됐다 — 구버전 로직이 남아있음: {reason}")

    def test_tp2_fires_at_4r(self, logic, rtp_position):
        """4R(10_800) 도달 시 TP2 발동 → 25% 부분 청산 + trailing ON"""
        rtp_position['partial_exit_stage'] = 1   # TP1 이미 완료
        should, reason, info = logic.check_exit_signal(rtp_position, 10_800, make_df(10_800))
        assert not should
        assert info is not None
        assert info.get('partial_exit') is True
        assert info.get('exit_ratio') == pytest.approx(0.25), "TP2 청산 비율은 25%"
        assert info.get('stage') == 2
        assert 'R_TP2' in reason
        assert rtp_position.get('trailing_active') is True, "TP2 후 trailing 자동 ON"

    def test_tp2_does_not_fire_at_3r(self, logic, rtp_position):
        """3R(10_600) = 구버전 TP2 지점 — 신버전에서는 미발동"""
        rtp_position['partial_exit_stage'] = 1
        price = 10_600   # 3R
        should, reason, info = logic.check_exit_signal(rtp_position, price, make_df(price))
        if info and info.get('partial_exit'):
            pytest.fail(f"3R에서 TP2가 발동됐다 — 구버전 로직이 남아있음: {reason}")

    def test_tp2_not_fire_if_tp1_not_done(self, logic, rtp_position):
        """stage=0 상태에서 4R 도달: TP1 먼저 처리 (stage 건너뛰기 방지)"""
        # stage=0이면 TP1이 먼저 발동해야지 TP2가 발동되면 안 됨
        rtp_position['partial_exit_stage'] = 0
        should, reason, info = logic.check_exit_signal(rtp_position, 10_800, make_df(10_800))
        if info and info.get('partial_exit'):
            assert info.get('stage') == 1, "stage=0에서 4R 도달 → TP1(stage 1)이 먼저 처리"

    def test_partial_stage_sequence_0_1_2(self, logic):
        """0 → 1 → 2 순서 검증 (중복 실행 방지)"""
        entry = 10_000
        # stage=1인 상태에서 TP1 가격: 부분 청산 발동 안 해야 함
        pos_stage1 = make_position(entry, stop_price=9_800, partial_stage=1,
                                   minutes_ago=10, r_tp1_price=10_400, r_tp2_price=10_800)
        should, reason, info = logic.check_exit_signal(pos_stage1, 10_450, make_df(10_450))
        if info and info.get('partial_exit'):
            assert info.get('stage') != 1, "이미 stage=1인데 또 stage=1 청산 발동 — 중복"


# ═══════════════════════════════════════════════════════════════════
# 6. ATR 트레일링 (trailing stop + floor)
# ═══════════════════════════════════════════════════════════════════

class TestTrailingStop:
    def test_trailing_activates_at_2pct_profit(self, logic):
        """profit이 한때 ≥ 2% (highest=+2.5%): trailing 켜진 뒤 pullback 발동"""
        entry = 10_000
        highest = 10_250   # +2.5% 달성 → 이미 trailing_active=True 상태로 시뮬레이션
        pos = make_position(entry, highest_price=highest, trailing_active=True, minutes_ago=10)
        # ATR=200, base_mult=3.0 → trailing_stop = 10250 - 200×3 = 9650
        # 현재가 9650 → trailing 발동
        price = 9_650
        df = make_df(price, atr=200)
        should, reason, _ = logic.check_exit_signal(pos, price, df)
        assert should
        assert 'TRAILING_STOP' in reason

    def test_trailing_floor_at_entry_after_tp1(self, logic):
        """stage=1(TP1 완료): trailing floor = entry (원금 손실 방지)"""
        entry = 10_000
        highest = 10_500   # +5%
        pos = make_position(entry, highest_price=highest, partial_stage=1,
                            trailing_active=True, minutes_ago=10)
        # ATR=500 → trailing = 10500 - 500×2.5 = 9250 < entry → floor=entry 적용
        # 현재가 entry (10_000) → trailing 발동해야 함
        df = make_df(entry, atr=500)
        should, reason, _ = logic.check_exit_signal(pos, entry, df)
        assert should
        assert 'TRAILING_STOP' in reason

    def test_trailing_floor_be_after_tp2(self, logic):
        """stage=2(TP2 완료): trailing floor = entry+0.2%"""
        entry = 10_000
        floor = int(entry * 1.002)   # 10020: BE 라인
        highest = 11_000
        pos = make_position(entry, highest_price=highest, partial_stage=2,
                            trailing_active=True, minutes_ago=10)
        # 현재가 floor-1 = 10019: BE 이하 → trailing 발동
        df = make_df(floor - 1, atr=500)
        should, reason, _ = logic.check_exit_signal(pos, floor - 1, df)
        assert should

    def test_trailing_tightens_above_8pct(self, logic):
        """수익 +8% 이상: ATR×2.0 (타이트)"""
        entry = 10_000
        highest = 11_000   # +10% 고점
        pos = make_position(entry, highest_price=highest,
                            trailing_active=True, minutes_ago=10)
        # profit=+8% → tier2(8%+) → ATR×2.0
        # trailing_stop = 11000 - 100×2.0 = 10800 = current → 발동
        df = make_df(10_800, atr=100)
        should, reason, _ = logic.check_exit_signal(pos, 10_800, df)
        assert should
        assert 'ATR×2.0' in reason

    def test_no_trailing_below_2pct(self, logic):
        """profit < 2% → trailing 비활성, 발동 안 함"""
        entry = 10_000
        pos = make_position(entry, minutes_ago=10)
        # profit = +1.5% → trailing 미발동
        price = 10_150
        should, reason, _ = logic.check_exit_signal(pos, price, make_df(price))
        if should:
            assert 'TRAILING_STOP' not in reason


# ═══════════════════════════════════════════════════════════════════
# 7. EOD 시간 청산
# ═══════════════════════════════════════════════════════════════════

class TestTimeExit:
    def test_no_eod_exit_when_disabled(self, logic):
        """time_based_exit_enabled=False(기본) 시 시간 청산 없음"""
        # 시간을 직접 조작할 수 없으므로: 기본 설정에서 eod_policy.enabled=False → 미발동
        entry = 10_000
        pos = make_position(entry, minutes_ago=10)
        should, reason, _ = logic.check_exit_signal(pos, entry, make_df(entry))
        if should:
            assert '시간 기반 청산' not in (reason or '')

    def test_eod_enabled_fires_at_1500(self, monkeypatch):
        """time_based_exit_enabled=True + 현재시각 ≥ 15:00 → 청산"""
        cfg = make_config({'eod_policy': {'enabled': True}})
        lg = OptimizedExitLogic(cfg)

        import trading.exit_logic_optimized as exit_mod
        fake_now = datetime(2026, 5, 1, 15, 5, 0)
        monkeypatch.setattr(
            exit_mod, 'datetime',
            type('FakeDT', (), {
                'now': staticmethod(lambda: fake_now),
                'fromisoformat': datetime.fromisoformat,
            })
        )
        entry = 10_000
        pos = make_position(entry, minutes_ago=10)
        # entry_time을 fake_now 기준 10분 전으로 명시 (real time과 무관)
        pos['entry_time'] = datetime(2026, 5, 1, 14, 55, 0)
        should, reason, _ = lg.check_exit_signal(pos, entry, make_df(entry))
        assert should
        assert '시간 기반 청산' in reason

    def test_overnight_approved_skips_eod(self, monkeypatch):
        """오버나이트 승인 포지션은 15:00 청산에서 제외"""
        cfg = make_config({'eod_policy': {'enabled': True}})
        lg = OptimizedExitLogic(cfg)

        import trading.exit_logic_optimized as exit_mod
        fake_now = datetime(2026, 5, 1, 15, 5, 0)
        monkeypatch.setattr(
            exit_mod, 'datetime',
            type('FakeDT', (), {
                'now': staticmethod(lambda: fake_now),
                'fromisoformat': datetime.fromisoformat,
            })
        )
        entry = 10_000
        pos = make_position(entry, minutes_ago=10, allow_overnight=True)
        pos['entry_time'] = datetime(2026, 5, 1, 14, 55, 0)
        should, _, _ = lg.check_exit_signal(pos, entry, make_df(entry))
        assert not should, "overnight 승인 포지션은 시간 청산 대상 아님"
