"""
tests/unit/test_fail_closed_gates.py — WI3-D Fail-open → Fail-closed 전환 검증

3개 게이트 각각 "필수 데이터 없음/조회실패 → BLOCK"으로 동작하는지 확인한다.

- DB Hard Stop(`main_auto_trading.py:_check_db_hard_stop_guard`)은 인스턴스 메서드가
  self.config 몇 개 속성만 있으면 독립 호출 가능해 실제 메서드를 직접 호출해 검증한다
  (`tests/simulation/test_execute_buy_dry_run.py`의 __new__() stub 패턴 재사용).
- KODEX200 시장약세필터(consumer, check_entry_signal 내부)와 오버나잇 REVERSAL 게이트
  (force_close_overnight 내부)는 각각 거대한 단일 함수 안쪽 깊숙이 임베드돼 있어(다수의
  키움 API/시장데이터 의존) 안전하게 독립 호출하기 어렵다 — 대신 수정된 판정식을 소스
  라인과 1:1로 대조 가능한 형태로 재현(mirror)하여 진리표를 검증한다. 소스가 바뀌면 이
  테스트도 함께 갱신해야 한다(각 테스트 docstring에 정확한 source 라인 명시).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from unittest.mock import MagicMock

from main_auto_trading import IntegratedTradingSystem


# ── DB Hard Stop (실제 메서드 호출) ────────────────────────────────────────────

class _MockConfig(dict):
    def get(self, key, default=None):
        parts = key.split('.', 1)
        val = dict.get(self, parts[0], default)
        if len(parts) == 2 and isinstance(val, dict):
            return val.get(parts[1], default)
        return val if val is not None else default


def _make_db_hard_stop_stub():
    t = IntegratedTradingSystem.__new__(IntegratedTradingSystem)
    t.config = _MockConfig({'risk_control': {'db_hard_stop': {'enabled': True}}})
    t._db_hard_stop_checked_at = 0.0
    t._db_hard_stop_cache_ttl_sec = 60
    t._db_hard_stop_state = {'halted': False, 'loss_streak': 0, 'disabled_regimes': [], 'reasons': []}
    return t


def test_db_hard_stop_query_failure_blocks():
    """analysis.log_trade_analytics.evaluate_hard_stop()가 예외를 던지면(DB 조회 실패)
    이전엔 (True, "")로 조용히 통과했다 — 이제 (False, ...)로 차단돼야 한다."""
    stub = _make_db_hard_stop_stub()
    with patch('analysis.log_trade_analytics.evaluate_hard_stop', side_effect=Exception('DB connection refused')):
        ok, reason = stub._check_db_hard_stop_guard()
    assert ok is False
    assert 'DB_HARD_STOP_CHECK_FAILED' in reason
    assert 'Fail Closed' in reason


def test_db_hard_stop_normal_operation_unaffected():
    """정상 조회 성공 시(halted=False)는 그대로 통과 — 이번 수정이 정상 경로를 건드리지 않았는지 확인."""
    stub = _make_db_hard_stop_stub()
    with patch('analysis.log_trade_analytics.evaluate_hard_stop',
              return_value={'halted': False, 'loss_streak': 0, 'disabled_regimes': [], 'reasons': []}):
        ok, reason = stub._check_db_hard_stop_guard()
    assert ok is True


def test_db_hard_stop_disabled_skips_check():
    """risk_control.db_hard_stop.enabled=False면 DB 조회 자체를 안 하고 통과(기존 동작 유지)."""
    stub = _make_db_hard_stop_stub()
    stub.config = _MockConfig({'risk_control': {'db_hard_stop': {'enabled': False}}})
    with patch('analysis.log_trade_analytics.evaluate_hard_stop') as m:
        ok, reason = stub._check_db_hard_stop_guard()
    assert ok is True
    m.assert_not_called()


# ── KODEX200 시장약세필터 (source: main_auto_trading.py:_kodex200_change_pct 소비부) ────

def _sqz_market_gate_mirror(mkt_chg, weak_threshold=-0.5):
    """main_auto_trading.py의 'Gate B: 시장 약세 필터' 판정식을 그대로 재현(WI3-D2).
    반환: 'DATA_FAIL' | 'WEAK_BLOCK' | 'PASS'"""
    if mkt_chg is None:
        return 'DATA_FAIL'
    elif mkt_chg <= weak_threshold:
        return 'WEAK_BLOCK'
    return 'PASS'


def test_kodex200_data_failure_blocks():
    assert _sqz_market_gate_mirror(None) == 'DATA_FAIL'


def test_kodex200_weak_market_blocks():
    assert _sqz_market_gate_mirror(-0.8) == 'WEAK_BLOCK'


def test_kodex200_normal_market_passes():
    assert _sqz_market_gate_mirror(0.3) == 'PASS'


def test_kodex200_producer_sets_none_on_missing_data():
    """core/market_context.py의 실제 producer 로직(WI3-D2)을 그대로 재현해 None 처리를 확인."""
    def _producer(df200_5m):
        try:
            if df200_5m is not None and len(df200_5m) >= 2:
                _last_cl = float(df200_5m['close'].iloc[-1])
                _first_op = float(df200_5m['open'].iloc[0])
                return (_last_cl - _first_op) / _first_op * 100 if _first_op > 0 else None
            return None
        except Exception:
            return None

    assert _producer(None) is None  # 조회 실패(None 반환)
    import pandas as pd
    empty_df = pd.DataFrame({'close': [100.0], 'open': [100.0]})  # len<2
    assert _producer(empty_df) is None


# ── 오버나잇 REVERSAL 게이트 (source: main_auto_trading.py:force_close_overnight) ──────

def _reversal_loss_mirror(mkt_regime, regime_unknown, profit_pct, blk_rev=True):
    """`_is_reversal_loss = _blk_rev and (_mkt_regime=='REVERSAL' or _regime_unknown) and
    (profit_pct<0)` — WI3-D3 수정 후 판정식 그대로 재현."""
    return blk_rev and (mkt_regime == 'REVERSAL' or regime_unknown) and (profit_pct < 0)


def test_regime_query_failure_with_loss_blocks_carry():
    """get_regime() 예외(_regime_unknown=True) + 손실 포지션 → 강제청산 후보에 포함(차단)."""
    assert _reversal_loss_mirror('UNKNOWN', True, -1.5) is True


def test_regime_query_failure_with_profit_does_not_force_close():
    """예외가 났어도 수익 포지션까지 강제청산하지는 않는다(과보호 방지, profit_pct<0 조건 유지)."""
    assert _reversal_loss_mirror('UNKNOWN', True, 2.0) is False


def test_regime_reversal_normal_operation_blocks():
    assert _reversal_loss_mirror('REVERSAL', False, -1.0) is True


def test_regime_neutral_normal_operation_allows_carry():
    assert _reversal_loss_mirror('NEUTRAL', False, -1.0) is False


# ── EC_HALT 게이트 호출부 신규 가드 (WI3-A, main_auto_trading.py:_check_global_risk_gates) ──

def _make_global_gate_stub(account_data_reliable: bool):
    """_check_global_risk_gates()의 앞선 게이트(시간/KillSwitch/DailyRisk/MarketSensor/
    Drawdown)를 전부 통과시키고 Equity Curve HALT 게이트까지 도달시키는 최소 stub."""
    t = IntegratedTradingSystem.__new__(IntegratedTradingSystem)
    t.config = _MockConfig({
        'risk_control': {'daily_risk': {'enabled': False}},
        'drawdown_engine': {'enabled': False},
        'equity_control': {'enabled': True},
    })
    t._kill_switch_active = False
    t._orphan_halt = False
    t._daily_loss_halted = False
    t._daily_buy_count = 0
    rm = MagicMock()
    rm.can_enter_trade.return_value = (True, '')
    t.reentry_metrics = rm
    t.equity_ctrl = MagicMock()
    t.equity_ctrl.can_enter.return_value = (True, 'EC_OK: dd=+0.0%')
    t.total_assets = 10_000_000
    t._account_data_reliable = account_data_reliable
    return t


def test_ec_halt_gate_blocks_when_account_data_unreliable():
    """[WI3-A] 계좌조회 실패로 total_assets가 폴백값(10,000,000)일 수 있는 상태
    (_account_data_reliable=False)에서는 equity_ctrl.can_enter()를 호출하기 전에
    이미 차단돼야 한다 — 폴백값 때문에 dd가 항상 양수로 계산되어 EC_HALT가
    조용히 우회되는 것을 막는 신규 가드."""
    stub = _make_global_gate_stub(account_data_reliable=False)
    with patch('main_auto_trading.datetime') as mock_dt:
        mock_dt.now.return_value.time.return_value = __import__('datetime').time(11, 0)
        ok, reason = stub._check_global_risk_gates('005930', '삼성전자')
    assert ok is False
    assert 'EC_HALT_UNRELIABLE_DATA' in reason
    stub.equity_ctrl.can_enter.assert_not_called()


def test_ec_halt_gate_proceeds_when_account_data_reliable():
    """정상 상태(_account_data_reliable=True)에서는 기존과 동일하게 equity_ctrl.can_enter()가
    실제로 호출되고 그 결과를 따른다 — 이번 수정이 정상 경로를 막지 않는지 확인."""
    stub = _make_global_gate_stub(account_data_reliable=True)
    with patch('main_auto_trading.datetime') as mock_dt:
        mock_dt.now.return_value.time.return_value = __import__('datetime').time(11, 0)
        ok, reason = stub._check_global_risk_gates('005930', '삼성전자')
    assert ok is True
    stub.equity_ctrl.can_enter.assert_called_once_with(10_000_000)
