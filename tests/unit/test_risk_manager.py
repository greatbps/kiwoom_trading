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


# ── WI3-E: 3-Slot Allocation (can_open_position, 무수정 — 기존 동작 재확인용) ──────
#
# ⚠️ can_open_position()은 'data/cooldown.lock'을 하드코딩 경로로 직접 읽고 조건에
# 따라 삭제한다(storage_path와 무관, 이번 WI3 수정 대상 아님). 테스트가 실제 운영
# 파일을 건드리지 않도록 매 테스트 전후로 백업/복원한다.

import pytest as _pytest

_COOLDOWN_LOCK = Path('data/cooldown.lock')


@_pytest.fixture
def protect_cooldown_lock():
    backup = _COOLDOWN_LOCK.read_bytes() if _COOLDOWN_LOCK.exists() else None
    existed = _COOLDOWN_LOCK.exists()
    yield
    if backup is not None:
        _COOLDOWN_LOCK.parent.mkdir(exist_ok=True)
        _COOLDOWN_LOCK.write_bytes(backup)
    elif _COOLDOWN_LOCK.exists() and not existed:
        _COOLDOWN_LOCK.unlink()


def _rm(max_positions=3):
    path = _tmp_log_path()
    _write_log(path)
    # position_risk_pct/max_position_size_pct/hard_max_position/min_cash_reserve_pct를
    # 넉넉하게 열어둬서 슬롯 개수 규칙(#1) 하나만 격리해서 검증한다 — 다른 규칙(#4~6)이
    # 섞여 들어오면 "슬롯 때문에 차단"인지 "포지션 크기 때문에 차단"인지 구분이 안 된다.
    rm = RiskManager(initial_balance=100_000_000, storage_path=path, config={
        'risk_management': {
            'max_positions': max_positions,
            'hard_max_position': 100_000_000,
            'max_position_size_pct': 100,
            'min_cash_reserve_pct': 0,
        },
    })
    return rm


def test_slot_0_of_3_allows_entry(protect_cooldown_lock):
    rm = _rm(max_positions=3)
    ok, _ = rm.can_open_position(current_balance=100_000_000, current_positions_value=0,
                                 position_count=0, position_size=20_000_000)
    assert ok is True


def test_slot_1_of_3_allows_entry(protect_cooldown_lock):
    rm = _rm(max_positions=3)
    ok, _ = rm.can_open_position(current_balance=100_000_000, current_positions_value=20_000_000,
                                 position_count=1, position_size=20_000_000)
    assert ok is True


def test_slot_2_of_3_allows_entry(protect_cooldown_lock):
    rm = _rm(max_positions=3)
    ok, _ = rm.can_open_position(current_balance=100_000_000, current_positions_value=40_000_000,
                                 position_count=2, position_size=20_000_000)
    assert ok is True


def test_slot_3_of_3_blocks_entry(protect_cooldown_lock):
    """MAX_POSITIONS=3 도달 — 4번째 후보는 차단(실제 운영 config 기준값)."""
    rm = _rm(max_positions=3)
    ok, reason = rm.can_open_position(current_balance=100_000_000, current_positions_value=60_000_000,
                                      position_count=3, position_size=20_000_000)
    assert ok is False
    assert '최대 보유 종목' in reason


def test_more_than_3_candidates_all_beyond_slot_blocked(protect_cooldown_lock):
    """슬롯이 이미 3개 찬 상태에서 후보가 몇 개든(4개, 5개...) 전부 차단되어야 한다."""
    rm = _rm(max_positions=3)
    for _ in range(5):
        ok, _ = rm.can_open_position(current_balance=100_000_000, current_positions_value=60_000_000,
                                     position_count=3, position_size=20_000_000)
        assert ok is False


def test_max_positions_reads_from_config_not_hardcoded(protect_cooldown_lock):
    """config.risk_management.max_positions가 실제로 반영되는지(운영값=3) 확인 —
    RiskManager.DEFAULT_MAX_POSITIONS(5)로 조용히 되돌아가지 않는지 회귀 방지."""
    rm = _rm(max_positions=3)
    assert rm.MAX_POSITIONS == 3
