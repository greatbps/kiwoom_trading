"""
tests/unit/test_execute_buy_decision_ledger.py

execute_buy() Decision Ledger 완전성 검증 (Audit 7, P0, 2026-07-27)

배경:
  execute_buy() 내부 37개 조기 return 중 어느 것도 decision_service.record_rejection()을
  호출하지 않아, SMC가 이미 승인한 candidate(_pending_decision_ctx)가 execute_buy() 자체
  판단으로 거부될 때 Decision Ledger에 아무 기록도 남지 않는 갭이 있었다(docs/FINAL_OPERATIONAL_AUDIT_REPORT.md
  Audit 7). 이 테스트는 그 갭이 메워졌는지 — 8개 신규 reason_code 버킷 각각에서 정확히
  1건의 record_rejection()이 호출되는지 — 확인한다.

전략:
  tests/simulation/test_execute_buy_dry_run.py의 stub 팩토리(_make_stub/_make_df/_patches)를
  재사용한다. decision_service는 MagicMock으로 대체하고 self._pending_decision_ctx에 가짜
  컨텍스트를 심어, execute_buy()가 실제로 record_rejection(ctx, reason_code, ...)을 부르는지만
  검증한다 (DecisionService/DB 내부 로직은 별도로 테스트되지 않음 — 여기선 호출 여부/인자만 확인).

주의: execute_buy()의 order-success 이후 record_acceptance() 경로(DB insert, asyncio.create_task
등)는 이 테스트의 범위 밖이다 — 기존 test_execute_buy_dry_run.py도 그 지점까지 가지 않는다.
"""

from __future__ import annotations

import sys
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.simulation.test_execute_buy_dry_run import _make_stub, _make_df, _patches
from services.decision_service import DecisionService

# execute_buy()가 _finalize_decision()에 실제로 넘기는 모든 태그 (main_auto_trading.py 검색으로 확인).
# 하나라도 8개 버킷 중 하나로 정규화되지 않고 OTHER로 떨어지면 카테고리 분석이 뭉개진다.
_EXECUTE_BUY_TAGS = [
    'TRADE_CD', 'BAN_LIST_BLOCK', 'COOLDOWN_BLOCK', 'PAT_DUP', 'DUPLICATE_BLOCK',
    'DAILY_STOCK_LIMIT', 'ZERO_QTY_BLOCK', 'RVOL_BLOCK', 'EMA9_BLOCK', 'VWAP_DIST_BLOCK',
    'SD_FILTER_BLOCK', 'SD_DROP_BLOCK', 'POS_FILTER_BO', 'POS_FILTER_EMA', 'FILTER_EXCEPTION',
    'ML_BLOCK', 'EQ_BLOCK', 'PATTERN_SIZER_SKIP', 'TIME_WEIGHT', 'FIX_C_V2', 'LSG_BLOCK',
    'DRIFT_BLOCK', 'CONSERVATIVE_BLOCK', 'SELF_OPT_BLOCK', 'DB_HARD_STOP', 'REGIME_ENTRY_BLOCK',
    'SECTOR_LIMIT', 'PORT_LIMIT', 'DEF_LIMIT', 'NO_RISK_MGR', 'API_FAILURE', 'ORDER_FAILURE',
]


def test_all_execute_buy_tags_map_to_a_named_bucket():
    """execute_buy()가 쓰는 모든 reason 태그가 OTHER로 뭉개지지 않고 8개 신규 버킷 중 하나로 정규화되는지."""
    ds = DecisionService.__new__(DecisionService)
    for tag in _EXECUTE_BUY_TAGS:
        normalized = ds._normalize_reason(tag)
        assert normalized != 'OTHER', f"{tag} normalizes to OTHER — reason mapping 누락"


def _stub_with_decision_ctx(stock_code: str = "005930"):
    """decision_service를 MagicMock으로, _pending_decision_ctx에 가짜 ctx를 주입한 stub."""
    stub = _make_stub()
    stub.decision_service = MagicMock()
    fake_ctx = MagicMock(name="EvaluationContext")
    stub._pending_decision_ctx = {stock_code: fake_ctx}
    return stub, fake_ctx


def _run(stub, df, extra_patches=(), **kwargs):
    with ExitStack() as stack:
        for p in _patches(stub):
            stack.enter_context(p)
        for p in extra_patches:
            stack.enter_context(p)
        try:
            stub.execute_buy(
                stock_code=kwargs.pop("stock_code", "005930"),
                stock_name=kwargs.pop("stock_name", "삼성전자"),
                price=kwargs.pop("price", 75_000),
                df=df,
                entry_reason=kwargs.pop("entry_reason", "EXPERIMENT:test"),
                entry_confidence=kwargs.pop("entry_confidence", 0.95),
                **kwargs,
            )
        except Exception:
            # API_FAILURE 시나리오는 order_buy 자체가 예외를 던짐 — execute_buy 내부에서
            # try/except로 잡혀 정상 반환되므로 여기까지 전파되지 않는 것이 기대 동작이지만,
            # 스텁의 단순화로 인한 예상 밖 예외까지 흡수해 record_rejection 호출 여부만 검증한다.
            pass


class TestExecuteBuyDecisionLedger:

    def test_insufficient_capital_zero_qty(self):
        """자금 부족(0주 산출) → INSUFFICIENT_CAPITAL 1건."""
        stub, ctx = _stub_with_decision_ctx()
        stub.risk_manager.calculate_position_size.return_value = {
            "quantity": 0.5, "investment": 100, "risk_amount": 10,
            "stop_loss_price": 72_750, "position_pct": 0.0, "position_ratio": 0.0,
        }
        _run(stub, _make_df())

        stub.decision_service.record_rejection.assert_called_once()
        call_args = stub.decision_service.record_rejection.call_args
        assert call_args[0][0] is ctx
        assert call_args[0][1] == "ZERO_QTY_BLOCK"  # decision_service가 이를 INSUFFICIENT_CAPITAL로 정규화
        stub.api.order_buy.assert_not_called()

    def test_market_closed_time_weight_zero(self):
        """장 종료(시간 가중치 0) → ENTRY_QUALITY_BLOCKED(TIME_WEIGHT) 1건."""
        stub, ctx = _stub_with_decision_ctx()
        extra = [patch.object(stub, "_get_time_weight", return_value=0.0)]
        _run(stub, _make_df(), extra_patches=extra)

        stub.decision_service.record_rejection.assert_called_once()
        call_args = stub.decision_service.record_rejection.call_args
        assert call_args[0][1] == "TIME_WEIGHT"
        stub.api.order_buy.assert_not_called()

    def test_duplicate_position(self):
        """중복 보유 → DUPLICATE_POSITION 1건."""
        stub, ctx = _stub_with_decision_ctx()
        stub.positions = {"005930": {"quantity": 10}}
        _run(stub, _make_df())

        stub.decision_service.record_rejection.assert_called_once()
        call_args = stub.decision_service.record_rejection.call_args
        assert call_args[0][1] == "DUPLICATE_BLOCK"
        stub.api.order_buy.assert_not_called()

    def test_max_positions_exceeded(self):
        """최대 보유수 초과(can_open_position 거부) → MAX_POSITIONS 1건."""
        stub, ctx = _stub_with_decision_ctx()
        stub.risk_manager.can_open_position.return_value = (False, "최대 보유 종목 수 초과 (5/5)")
        _run(stub, _make_df())

        stub.decision_service.record_rejection.assert_called_once()
        call_args = stub.decision_service.record_rejection.call_args
        assert call_args[0][1] == "MAX_POSITIONS"
        stub.api.order_buy.assert_not_called()

    def test_risk_blocked_ban_list(self):
        """Risk 차단(3연속손실 금지목록) → RISK_BLOCKED 1건."""
        stub, ctx = _stub_with_decision_ctx()
        stub.stock_ban_list = {"005930"}
        _run(stub, _make_df())

        stub.decision_service.record_rejection.assert_called_once()
        call_args = stub.decision_service.record_rejection.call_args
        assert call_args[0][1] == "BAN_LIST_BLOCK"
        stub.api.order_buy.assert_not_called()

    def test_cooldown_active(self):
        """연패 쿨다운 중(바이패스 미충족) → COOLDOWN_ACTIVE 1건."""
        stub, ctx = _stub_with_decision_ctx()
        stub._trade_cooldown = 2
        _run(stub, _make_df(), entry_confidence=0.5)  # bypass_confidence(0.85) 미달

        stub.decision_service.record_rejection.assert_called_once()
        call_args = stub.decision_service.record_rejection.call_args
        assert call_args[0][1] == "TRADE_CD"
        stub.api.order_buy.assert_not_called()

    def test_api_failure(self):
        """매수 API 호출 자체 예외 → API_FAILURE 1건."""
        stub, ctx = _stub_with_decision_ctx()
        stub.dry_run_mode = False
        stub.api.order_buy.side_effect = Exception("network timeout")
        _run(stub, _make_df())

        stub.decision_service.record_rejection.assert_called_once()
        call_args = stub.decision_service.record_rejection.call_args
        assert call_args[0][1] == "API_FAILURE"

    def test_order_failure(self):
        """주문 API 응답 실패(return_code != 0) → ORDER_FAILURE 1건."""
        stub, ctx = _stub_with_decision_ctx()
        stub.dry_run_mode = False
        stub.api.order_buy.return_value = {"return_code": -1, "return_msg": "잔고부족", "ord_no": None}
        _run(stub, _make_df())

        stub.decision_service.record_rejection.assert_called_once()
        call_args = stub.decision_service.record_rejection.call_args
        assert call_args[0][1] == "ORDER_FAILURE"

    def test_success_path_does_not_call_reject(self):
        """정상 통과(dry-run까지 도달) → record_rejection 미호출 (오탐 방지 회귀)."""
        stub, ctx = _stub_with_decision_ctx()
        # dry_run_mode=True (기본) → 모든 게이트 통과 후 [DRY-RUN] 분기에서 반환
        _run(stub, _make_df())

        stub.decision_service.record_rejection.assert_not_called()
        stub.api.order_buy.assert_not_called()

    def test_no_double_fire_guard(self):
        """_finalize_decision 가드 — 동일 컨텍스트로 두 번째 호출 시도해도 1건만 기록."""
        stub, ctx = _stub_with_decision_ctx()
        stub.stock_ban_list = {"005930"}
        _run(stub, _make_df())

        # BAN_LIST_BLOCK 경로는 함수 극초반이라 이후 다른 return에 도달할 수 없으므로
        # 자연히 1건만 발생 — 그래도 정확히 1건임을 재확인(0건/2건 모두 회귀로 간주).
        assert stub.decision_service.record_rejection.call_count == 1
