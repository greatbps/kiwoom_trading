"""
tests/unit/test_risk_manager.py

core/risk_manager.py — RiskManager.load()의 쿨다운 만료 리셋 로직 회귀 테스트.

배경 (2026-07-27 발견/수정): load()가 daily_trades/daily_realized_pnl은
날짜 체크 후 리셋하면서, consecutive_losses/cooldown_until은 날짜/만료
체크 없이 무기한 복원했음 — 재시작마다 이미 끝난 쿨다운의 consecutive_losses가
되살아나 trade_cooldown이 근거 없이 재발동하는 버그였음.
"""
import sys
import os
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.risk_manager import RiskManager


def _tmp_log_path() -> str:
    return tempfile.mktemp(suffix='.json')


def _write_log(path: str, **overrides):
    base = {
        "initial_balance": 10_000_000.0,
        "today": datetime.now().date().isoformat(),
        "daily_trades": [], "daily_realized_pnl": 0.0,
        "week_start": datetime.now().date().isoformat(),
        "weekly_trades": [], "weekly_realized_pnl": 0.0,
        "consecutive_losses": 0, "cooldown_until": None, "lsg_activated_date": None,
    }
    base.update(overrides)
    Path(path).write_text(json.dumps(base), encoding='utf-8')


def test_expired_cooldown_resets_consecutive_losses():
    """cooldown_until이 과거(만료) → consecutive_losses/cooldown_until 모두 리셋되어야 함."""
    path = _tmp_log_path()
    expired = (datetime.now() - timedelta(days=1)).isoformat()
    _write_log(path, consecutive_losses=3, cooldown_until=expired)

    rm = RiskManager(initial_balance=10_000_000, storage_path=path)

    assert rm.consecutive_losses == 0, "만료된 쿨다운의 consecutive_losses는 리셋돼야 함"
    assert rm.cooldown_until is None


def test_still_valid_cooldown_preserved():
    """cooldown_until이 미래(유효) → consecutive_losses/cooldown_until 값 유지되어야 함."""
    path = _tmp_log_path()
    future = (datetime.now() + timedelta(days=1)).isoformat()
    _write_log(path, consecutive_losses=3, cooldown_until=future)

    rm = RiskManager(initial_balance=10_000_000, storage_path=path)

    assert rm.consecutive_losses == 3, "아직 유효한 쿨다운은 값을 유지해야 함"
    assert rm.cooldown_until == future


def test_no_cooldown_set_defaults_to_zero():
    """cooldown_until이 애초에 None이면 정상적으로 0/None 유지."""
    path = _tmp_log_path()
    _write_log(path, consecutive_losses=0, cooldown_until=None)

    rm = RiskManager(initial_balance=10_000_000, storage_path=path)

    assert rm.consecutive_losses == 0
    assert rm.cooldown_until is None


def test_missing_file_defaults_cleanly():
    """risk_log.json 파일 자체가 없어도(최초 실행) 예외 없이 기본값으로 시작."""
    path = _tmp_log_path()
    assert not Path(path).exists()

    rm = RiskManager(initial_balance=10_000_000, storage_path=path)

    assert rm.consecutive_losses == 0
    assert rm.cooldown_until is None
