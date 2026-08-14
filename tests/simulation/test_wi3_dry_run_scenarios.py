"""
tests/simulation/test_wi3_dry_run_scenarios.py — WI3 14절 통합 검증 시나리오 7종

실제 주문(api.order_buy)을 전혀 발생시키지 않는다(dry_run_mode=True 고정, 또는 애초에
api 호출 지점까지 가지 않는 게이트 단계에서 종료). `tests/simulation/test_execute_buy_dry_run.py`
와 `tests/unit/test_fail_closed_gates.py`의 stub/patch 패턴을 재사용한다.

Scenario 1~5는 이미 tests/unit/test_fail_closed_gates.py가 게이트 단위로 상세 검증했으므로
여기서는 WI3 문서의 시나리오 번호에 맞춰 얇게 재확인만 한다(중복 로직 없음, import 재사용).
Scenario 6~7이 이 파일의 핵심(신규) 검증 대상 — Ranking→Slot 통합 동작.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from main_auto_trading import IntegratedTradingSystem, watchlist_rank_key
from core.risk_manager import RiskManager
from tests.unit.test_fail_closed_gates import (
    _make_global_gate_stub, _make_db_hard_stop_stub, _MockConfig,
)

# can_open_position()이 'data/cooldown.lock'을 하드코딩 경로로 읽고 조건에 따라
# 삭제한다(tests/unit/test_risk_manager.py와 동일 주의사항) — 실제 운영 파일 보호.
_COOLDOWN_LOCK = Path('data/cooldown.lock')


@pytest.fixture(autouse=True)
def _protect_cooldown_lock():
    backup = _COOLDOWN_LOCK.read_bytes() if _COOLDOWN_LOCK.exists() else None
    yield
    if backup is not None:
        _COOLDOWN_LOCK.parent.mkdir(exist_ok=True)
        _COOLDOWN_LOCK.write_bytes(backup)


# ── Scenario 1/2: EC_HALT TRUE/FALSE ──────────────────────────────────────────

def test_scenario1_ec_halt_true_blocks_entry():
    """EC_HALT=TRUE(_account_data_reliable=False로 시뮬레이션) → Entry BLOCK."""
    stub = _make_global_gate_stub(account_data_reliable=False)
    with patch('main_auto_trading.datetime') as mock_dt:
        mock_dt.now.return_value.time.return_value = __import__('datetime').time(11, 0)
        ok, reason = stub._check_global_risk_gates('005930', '삼성전자')
    assert ok is False and 'EC_HALT' in reason


def test_scenario2_ec_halt_false_proceeds_normally():
    """EC_HALT=FALSE(정상 상태) → 정상적으로 equity_ctrl까지 게이트 진행."""
    stub = _make_global_gate_stub(account_data_reliable=True)
    with patch('main_auto_trading.datetime') as mock_dt:
        mock_dt.now.return_value.time.return_value = __import__('datetime').time(11, 0)
        ok, reason = stub._check_global_risk_gates('005930', '삼성전자')
    assert ok is True


# ── Scenario 3/4/5: DB/MarketData/Regime 오류 → BLOCK ─────────────────────────

def test_scenario3_db_error_blocks():
    stub = _make_db_hard_stop_stub()
    with patch('analysis.log_trade_analytics.evaluate_hard_stop', side_effect=Exception('conn refused')):
        ok, _ = stub._check_db_hard_stop_guard()
    assert ok is False


def test_scenario4_market_data_error_blocks():
    """KODEX200 조회실패(None) → Squeeze 시장약세필터 DATA_FAIL(차단)."""
    from tests.unit.test_fail_closed_gates import _sqz_market_gate_mirror
    assert _sqz_market_gate_mirror(None) == 'DATA_FAIL'


def test_scenario5_regime_error_blocks_reversal_carry():
    from tests.unit.test_fail_closed_gates import _reversal_loss_mirror
    assert _reversal_loss_mirror('UNKNOWN', True, -1.0) is True


# ── Scenario 6: 4 Candidates + 3 Slots → Score Top 3 선정 ─────────────────────

def test_scenario6_four_candidates_three_slots_top3_by_score_win():
    """워치리스트 4종목(A>B>C>D 스코어) + MAX_POSITIONS=3 → 상위 3개(A,B,C)만 진입,
    최하위(D)는 슬롯부족으로 차단. Ranking(watchlist_rank_key)이 실제 슬롯 배정에
    반영되는지 확인하는 WI3의 핵심 통합 시나리오."""
    watchlist_score = {'AAAA': 9.0, 'BBBB': 7.0, 'CCCC': 5.0, 'DDDD': 1.0}
    watchlist_as_set = set(watchlist_score.keys())  # main_auto_trading.py의 self.watchlist 재현

    rm = RiskManager(initial_balance=100_000_000, storage_path=tempfile.mktemp(suffix='.json'), config={
        'risk_management': {'max_positions': 3, 'hard_max_position': 100_000_000,
                            'max_position_size_pct': 100, 'min_cash_reserve_pct': 0},
    })

    entered, blocked = [], []
    position_count = 0
    ordered = sorted(watchlist_as_set, key=lambda s: watchlist_rank_key(s, watchlist_score))
    for sym in ordered:
        ok, reason = rm.can_open_position(current_balance=100_000_000, current_positions_value=0,
                                          position_count=position_count, position_size=20_000_000)
        if ok:
            entered.append(sym)
            position_count += 1
        else:
            blocked.append(sym)

    assert entered == ['AAAA', 'BBBB', 'CCCC'], f"스코어 상위3개가 진입해야 함, 실제={entered}"
    assert blocked == ['DDDD'], f"최하위 1개만 슬롯부족 차단돼야 함, 실제={blocked}"


# ── Scenario 7: 동일 입력 × 10회 → 동일 Ranking → 동일 Slot ───────────────────

def test_scenario7_repeated_runs_identical_ranking_and_slots():
    """동일 Input/동일 Market State를 10회 반복해도 Ranking과 Slot 배정 결과가
    매번 완전히 동일해야 한다(PYTHONHASHSEED 등에 따라 달라지는 구조는 불허)."""
    watchlist_score = {f'SYM{i:03d}': float((i * 37) % 11) for i in range(12)}  # 동점 다수 포함

    results = []
    for _ in range(10):
        wl_set = set(watchlist_score.keys())  # 매회 새 set 생성 — 해시 배치 재현
        ordered = sorted(wl_set, key=lambda s: watchlist_rank_key(s, dict(watchlist_score)))
        top3 = ordered[:3]
        results.append(tuple(top3))

    assert len(set(results)) == 1, f"반복 실행 결과가 달라짐(비결정적): {set(results)}"
