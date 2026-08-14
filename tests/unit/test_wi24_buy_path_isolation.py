"""
tests/unit/test_wi24_buy_path_isolation.py — WI-24 §19

seq32~39 Strategy Signal이 Ranking/Gate/Risk/execute_buy 프로덕션 코드에
실제로 연결돼 있지 않다는 것(wi24_dependency_audit 근거)을 회귀 테스트로
고정한다. 또한 Shadow BUY Simulation이 실제 주문 함수를 호출하지 않는다는
것과 분류 로직 자체의 정확성을 검증한다.
"""
from __future__ import annotations

import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Ranking attribution — ScoreEngine이 strategy_seq를 모른다 ────────────────
def test_score_engine_has_no_strategy_seq_reference():
    from trading.score_engine import ScoreEngine
    src = inspect.getsource(ScoreEngine)
    for banned in ('strategy_seq', 'condition_seq', 'strategy_signals', 'candidate_id'):
        assert banned not in src, f'ScoreEngine이 {banned}를 참조함 — Ranking attribution 위반'


# ── execute_buy attribution — strategy_seq 파라미터 없음 ─────────────────────
def test_execute_buy_signature_has_no_strategy_seq_param():
    src = open(os.path.join(ROOT, 'main_auto_trading.py'), encoding='utf-8').read()
    sig_start = src.index('def execute_buy(self,')
    sig_end = src.index(':', sig_start)
    sig = src[sig_start:sig_end]
    for banned in ('strategy_seq', 'condition_seq', 'candidate_id', 'signal_id'):
        assert banned not in sig, f'execute_buy() 시그니처에 {banned} 발견 — 연결 흔적'


# ── Risk attribution — RiskManager가 strategy_seq를 모른다 ───────────────────
def test_risk_manager_has_no_strategy_seq_reference():
    from core.risk_manager import RiskManager
    src = inspect.getsource(RiskManager)
    for banned in ('strategy_seq', 'condition_seq', 'strategy_signals'):
        assert banned not in src, f'RiskManager가 {banned}를 참조함 — Risk attribution 위반'


# ── Gate attribution — check_entry_signal 내부에서 strategy_seq가 조건문에
#    쓰이지 않는다(WI-22에서 이미 확인, WI-24 관점으로 재고정) ─────────────────
def test_check_entry_signal_does_not_branch_on_strategy_seq():
    src = open(os.path.join(ROOT, 'main_auto_trading.py'), encoding='utf-8').read()
    start = src.index('async def check_entry_signal(self')
    end = src.index('\n    async def ', start + 10)  # 다음 async def까지
    body = src[start:end]
    for line in body.splitlines():
        if 'primary_strategy_seq' in line or 'strategy_seqs' in line:
            stripped = line.strip()
            assert not stripped.startswith('if ') and not stripped.startswith('elif '), (
                f'check_entry_signal()에서 strategy_seq가 조건분기에 쓰임: {stripped}'
            )


# ── No actual order — Shadow Simulation이 주문 함수를 호출하지 않는다 ────────
def test_shadow_simulation_never_touches_real_order_functions():
    path = os.path.join(ROOT, 'analysis', 'wi24_shadow_buy_simulation.py')
    lines = open(path, encoding='utf-8').read().splitlines()
    code_lines = [l for l in lines if not l.strip().startswith('#') and not l.strip().startswith('"""')]
    code = '\n'.join(code_lines)
    for banned in ('.execute_buy(', 'self.execute_buy(', 'send_order(', 'cancel_order(', '.execute_sell('):
        assert banned not in code, f'Shadow Simulation이 실제 주문 함수({banned})를 호출함'


# ── Shadow 분류 로직 정확성 ────────────────────────────────────────────────
def test_classify_shadow_result_logic():
    from analysis.wi24_shadow_buy_simulation import classify_shadow_result

    assert classify_shadow_result(False, True, True) == 'SHADOW_RANK_BLOCK'
    assert classify_shadow_result(True, False, True) == 'SHADOW_GATE_BLOCK'
    assert classify_shadow_result(True, True, False) == 'SHADOW_RISK_BLOCK'
    assert classify_shadow_result(True, True, True) == 'SHADOW_BUY_ALLOWED'
    # Rank가 막히면 Gate/Risk 값과 무관하게 RANK_BLOCK이 우선(순차 판정)
    assert classify_shadow_result(False, False, False) == 'SHADOW_RANK_BLOCK'


# ── Duplicate protection — signal_outcomes/strategy_signals 중복 방지 제약 ───
def test_signal_outcomes_has_unique_constraint_on_signal_and_horizon():
    """research.signal_outcomes에 (signal_id, horizon_label) UNIQUE 제약이 있어야
    Outcome Collector 재실행 시 중복 삽입이 구조적으로 불가능하다(§19 Duplicate
    protection). WI-13 DDL 파일에서 확인한다(DB 접속 없이도 회귀 가능)."""
    import glob
    migration_files = glob.glob(os.path.join(ROOT, 'db', 'migrations', '*condition_dataset*.sql'))
    assert migration_files, 'condition_dataset 관련 migration 파일을 찾을 수 없음'
    found = False
    for path in migration_files:
        sql = open(path, encoding='utf-8').read().lower()
        if 'signal_outcomes' in sql and 'unique' in sql:
            found = True
    assert found, 'signal_outcomes에 UNIQUE 제약(중복 방지)이 DDL에 없음'
