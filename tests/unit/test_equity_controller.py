"""
tests/unit/test_equity_controller.py — WI3-A EC_HALT 검증

목적: `trading.equity_controller.EquityController`가 실제 계좌보호 서킷브레이커로서
정상 동작하는지 확인한다(로직 자체는 이번 WI3에서 변경하지 않음 — 검증 전용).

⚠️ 이 파일이 인스턴스화하는 EquityController는 모듈 레벨 경로 상수
(_STATE_PATH/_BACKUP_PATH/...)에 실제 운영 상태파일(data/equity_state.json)을 쓴다.
반드시 monkeypatch로 임시 경로로 리다이렉트한 뒤 테스트한다 — 실제 계좌 보호 상태를
절대 건드리지 않는다.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import trading.equity_controller as ec_mod
from trading.equity_controller import EquityController


@pytest.fixture
def redirected_paths(tmp_path, monkeypatch):
    """상태파일 경로를 전부 임시 디렉토리로 리다이렉트 — 실제 운영 파일 보호.
    DB 복구 경로도 기본적으로 비활성화한다(None 반환) — 그렇지 않으면 Primary/Backup이
    없을 때 _recover_peak_from_db()가 실제 운영 PostgreSQL DB에 접속해버린다. DB 복구
    자체를 테스트하는 케이스는 개별적으로 다시 monkeypatch해서 덮어쓴다."""
    primary = tmp_path / 'equity_state.json'
    backup = tmp_path / 'equity_state.json.bak'
    tmp1 = tmp_path / 'equity_state.json.tmp'
    tmp2 = tmp_path / 'equity_state.json.bak.tmp'
    monkeypatch.setattr(ec_mod, '_STATE_PATH', primary)
    monkeypatch.setattr(ec_mod, '_BACKUP_PATH', backup)
    monkeypatch.setattr(ec_mod, '_STATE_TMP', tmp1)
    monkeypatch.setattr(ec_mod, '_BACKUP_TMP', tmp2)
    monkeypatch.setattr(EquityController, '_recover_peak_from_db', lambda self: None)
    return primary, backup


def _cfg(max_dd_halt=-0.18, enabled=True, eod_only_peak=False):
    return {'equity_control': {'enabled': enabled, 'max_dd_halt': max_dd_halt,
                               'eod_only_peak': eod_only_peak}}


# ── 정상 상태 ────────────────────────────────────────────────────────────────
def test_normal_no_peak_allows_entry(redirected_paths):
    ctl = EquityController(_cfg())
    ok, reason = ctl.can_enter(10_000_000)
    assert ok is True
    assert reason == 'no_peak'


def test_normal_small_drawdown_allows_entry(redirected_paths):
    ctl = EquityController(_cfg())
    ctl.update_peak_eod(10_000_000)
    ok, reason = ctl.can_enter(9_500_000)  # dd=-5%
    assert ok is True


# ── Threshold 도달/초과 ───────────────────────────────────────────────────────
def test_threshold_exactly_at_halt_blocks(redirected_paths):
    """dd == halt_pct(경계값)는 '<=' 조건이라 차단되어야 한다."""
    ctl = EquityController(_cfg(max_dd_halt=-0.18))
    ctl.update_peak_eod(10_000_000)
    ok, reason = ctl.can_enter(8_200_000)  # dd = exactly -18%
    assert ok is False
    assert 'EC_HALT' in reason


def test_threshold_exceeded_blocks(redirected_paths):
    ctl = EquityController(_cfg(max_dd_halt=-0.18))
    ctl.update_peak_eod(10_000_000)
    ok, reason = ctl.can_enter(8_000_000)  # dd=-20%
    assert ok is False
    assert 'EC_HALT' in reason
    assert 'dd=-20' in reason


def test_threshold_just_above_allows(redirected_paths):
    ctl = EquityController(_cfg(max_dd_halt=-0.18))
    ctl.update_peak_eod(10_000_000)
    ok, reason = ctl.can_enter(8_300_000)  # dd=-17%
    assert ok is True


# ── Restart 복원 ──────────────────────────────────────────────────────────────
def test_restart_restores_peak_from_primary(redirected_paths):
    primary, _backup = redirected_paths
    ctl1 = EquityController(_cfg())
    ctl1.update_peak_eod(12_345_678)

    ctl2 = EquityController(_cfg())  # 재시작 시뮬레이션 — 새 인스턴스
    assert ctl2.peak == 12_345_678
    assert ctl2.state_integrity == 'OK'


# ── 잘못된 State ──────────────────────────────────────────────────────────────
def test_corrupted_primary_falls_back_to_backup(redirected_paths):
    primary, backup = redirected_paths
    ctl1 = EquityController(_cfg())
    ctl1.update_peak_eod(9_999_999)
    assert backup.exists()

    primary.write_text('{ this is not valid json', encoding='utf-8')

    ctl2 = EquityController(_cfg())
    assert ctl2.peak == 9_999_999
    assert ctl2.state_integrity == 'BACKUP_USED'


def test_both_files_missing_attempts_db_recovery(redirected_paths, monkeypatch):
    monkeypatch.setattr(EquityController, '_recover_peak_from_db', lambda self: 7_777_777)
    ctl = EquityController(_cfg())
    assert ctl.peak == 7_777_777
    assert ctl.state_integrity == 'RECOVERED_FROM_DB'


# ── DB 오류(복구도 실패) ───────────────────────────────────────────────────────
def test_db_recovery_failure_leaves_peak_zero_and_flags_failure(redirected_paths, monkeypatch):
    """Primary/Backup/DB 전부 실패 → peak=0, RECOVERY_FAILED로 명시(무음 실패 금지)."""
    monkeypatch.setattr(EquityController, '_recover_peak_from_db', lambda self: None)
    ctl = EquityController(_cfg())
    assert ctl.peak == 0.0
    assert ctl.state_integrity == 'RECOVERY_FAILED'
    # peak<=0이면 can_enter는 'no_peak'로 통과(서킷브레이커 자체가 무의미해지는 상태) —
    # 이 상태를 감춘 채 정상처럼 보이면 안 되므로 state_integrity로 반드시 드러나야 한다.
    ok, reason = ctl.can_enter(5_000_000)
    assert ok is True and reason == 'no_peak'


# ── NaN 방어(기존 CBF-1 수정 재확인, 회귀 방지) ──────────────────────────────────
def test_nan_equity_fails_closed(redirected_paths):
    ctl = EquityController(_cfg())
    ctl.update_peak_eod(10_000_000)
    ok, reason = ctl.can_enter(float('nan'))
    assert ok is False
    assert 'NaN' in reason
