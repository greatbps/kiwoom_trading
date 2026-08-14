"""strategy_entry/adapter.py — WI-25 §4/§9~§12/§21 StrategyEntryAdapter.

Strategy Signal(seq32~39) 1건을 Common Ranking/Gate/Risk까지 평가한다.
기존 ScoreEngine/RegimeAnalyzer/RiskManager를 그대로(무수정) 재사용한다
(§2/§10/§11/§12 — 로직 변경 금지).

Fail-Closed(§21): 필수 필드 누락/알 수 없는 전략/평가 중 예외 발생 시
전부 allowed=False로 즉시 종료하고, 그 이후 단계(Ranking/Gate/Risk)를
아예 호출하지 않는다.
"""
from __future__ import annotations

import logging
from typing import Callable, Optional

from strategy_entry.enable_registry import StrategyEnableRegistry, DISABLED, ENABLED
from strategy_entry.types import (
    StrategyEntryRequest, StrategyEntryDecision, VALID_STRATEGY_SEQS,
    STAGE_MISSING_FIELD, STAGE_UNKNOWN_STRATEGY, STAGE_ADAPTER_ERROR,
    STAGE_STRATEGY_DISABLED, STAGE_RANK_BLOCKED, STAGE_GATE_BLOCKED,
    STAGE_RISK_BLOCKED, STAGE_ALLOWED,
)

logger = logging.getLogger(__name__)

ASSUMED_POSITION_SIZE_PCT = 0.10  # WI-24와 동일 가정치 — 실제 EDT Sizer 미사용


class StrategyEntryAdapter:
    def __init__(
        self,
        score_engine,
        regime_analyzer,
        risk_manager,
        enable_registry: StrategyEnableRegistry,
        ohlcv_provider: Optional[Callable[[str], object]] = None,
        account_state_provider: Optional[Callable[[], dict]] = None,
    ):
        self.score_engine = score_engine
        self.regime_analyzer = regime_analyzer
        self.risk_manager = risk_manager
        self.enable_registry = enable_registry
        self.ohlcv_provider = ohlcv_provider or (lambda symbol: None)
        self.account_state_provider = account_state_provider or (
            lambda: {'current_balance': 0.0, 'positions_value': 0.0, 'position_count': 0,
                     'total_assets': 0.0}
        )

    def evaluate(
        self,
        request: StrategyEntryRequest,
        _shadow_bypass_enable_gate: bool = False,
    ) -> StrategyEntryDecision:
        """실제 매매 연결에 쓰일 유일한 진입점. `_shadow_bypass_enable_gate`는
        Shadow 검증(strategy_entry/shadow.py)에서만 True로 호출한다 — DISABLED
        상태에서도 Ranking/Gate/Risk 연결성을 확인하기 위함이며, 실제 주문
        경로는 이 스크립트를 호출하지 않으므로 안전하다(§14/§27 체크리스트
        "Common Ranking/Gate/Risk 연결 가능성 확인" 대응). 기본값은 항상
        False(Fail-Closed 원칙)."""
        base = dict(
            strategy_seq=request.strategy_seq, strategy_name=request.strategy_name,
            symbol=request.symbol, candidate_id=request.candidate_id,
            signal_id=request.signal_id, signal_timestamp=request.signal_timestamp,
            signal_price=request.signal_price, signal_reason=request.signal_reason,
        )

        # §21 Fail-Closed — 필수 필드
        missing = request.missing_fields()
        if missing:
            return StrategyEntryDecision(
                allowed=False, decision_stage=STAGE_MISSING_FIELD,
                reason=f'필수 필드 누락: {missing}', **base,
            )

        # §21 Fail-Closed — 알 수 없는 전략
        if request.strategy_seq not in VALID_STRATEGY_SEQS:
            return StrategyEntryDecision(
                allowed=False, decision_stage=STAGE_UNKNOWN_STRATEGY,
                reason=f'알 수 없는 strategy_seq: {request.strategy_seq}', **base,
            )

        # §20 Strategy Enable Registry — 기본 DISABLED
        enable_status = self.enable_registry.status(request.strategy_seq)
        if enable_status != ENABLED and not _shadow_bypass_enable_gate:
            return StrategyEntryDecision(
                allowed=False, decision_stage=STAGE_STRATEGY_DISABLED,
                reason=f'seq{request.strategy_seq}는 현재 {enable_status} 상태',
                enable_status=enable_status, **base,
            )

        try:
            ranking_result = self._rank(request)
        except Exception as e:
            logger.warning(f'[ENTRY_ADAPTER] Ranking 예외 seq{request.strategy_seq} '
                            f'{request.symbol}: {e}')
            return StrategyEntryDecision(
                allowed=False, decision_stage=STAGE_ADAPTER_ERROR,
                reason=f'Ranking 예외: {e}', enable_status=enable_status, **base,
            )
        if not ranking_result['pass']:
            return StrategyEntryDecision(
                allowed=False, decision_stage=STAGE_RANK_BLOCKED,
                reason=f"Ranking 미통과 (score={ranking_result['score']} < "
                       f"min={ranking_result['min_score']})",
                enable_status=enable_status, ranking_result=ranking_result, **base,
            )

        try:
            gate_result = self._gate(request)
        except Exception as e:
            logger.warning(f'[ENTRY_ADAPTER] Gate 예외 seq{request.strategy_seq} '
                            f'{request.symbol}: {e}')
            return StrategyEntryDecision(
                allowed=False, decision_stage=STAGE_ADAPTER_ERROR,
                reason=f'Gate 예외: {e}', enable_status=enable_status,
                ranking_result=ranking_result, **base,
            )
        if not gate_result['pass']:
            return StrategyEntryDecision(
                allowed=False, decision_stage=STAGE_GATE_BLOCKED,
                reason=f"Gate 차단: regime={gate_result['regime']} "
                       f"allow_new_entries={gate_result['pass']}",
                enable_status=enable_status, ranking_result=ranking_result,
                gate_result=gate_result, **base,
            )

        try:
            risk_result = self._risk(request)
        except Exception as e:
            logger.warning(f'[ENTRY_ADAPTER] Risk 예외 seq{request.strategy_seq} '
                            f'{request.symbol}: {e}')
            return StrategyEntryDecision(
                allowed=False, decision_stage=STAGE_ADAPTER_ERROR,
                reason=f'Risk 예외: {e}', enable_status=enable_status,
                ranking_result=ranking_result, gate_result=gate_result, **base,
            )
        if not risk_result['pass']:
            return StrategyEntryDecision(
                allowed=False, decision_stage=STAGE_RISK_BLOCKED,
                reason=f"Risk 차단: {risk_result['reason']}",
                enable_status=enable_status, ranking_result=ranking_result,
                gate_result=gate_result, risk_result=risk_result, **base,
            )

        return StrategyEntryDecision(
            allowed=True, decision_stage=STAGE_ALLOWED, reason='OK',
            enable_status=enable_status, ranking_result=ranking_result,
            gate_result=gate_result, risk_result=risk_result, **base,
        )

    # ── §10 Ranking ──────────────────────────────────────────────────────────
    def _rank(self, request: StrategyEntryRequest) -> dict:
        ohlcv = self.ohlcv_provider(request.symbol)
        score = self.score_engine.score(request.symbol, ohlcv)
        min_score = self.score_engine.min_score
        return {'pass': score['total'] >= min_score, 'score': score['total'],
                'min_score': min_score, 'detail': score}

    # ── §11 Gate ─────────────────────────────────────────────────────────────
    def _gate(self, request: StrategyEntryRequest) -> dict:
        decision = self.regime_analyzer.evaluate(force=True)
        return {'pass': bool(decision.allow_new_entries), 'regime': decision.regime,
                'allowed_min_grade': decision.allowed_min_grade,
                'size_multiplier': decision.size_multiplier, 'reasons': decision.reasons}

    # ── §12 Risk ─────────────────────────────────────────────────────────────
    def _risk(self, request: StrategyEntryRequest) -> dict:
        acct = self.account_state_provider()
        total_assets = acct.get('total_assets',
                                 acct['current_balance'] + acct['positions_value'])
        position_size = total_assets * ASSUMED_POSITION_SIZE_PCT
        can_enter, reason = self.risk_manager.can_open_position(
            current_balance=acct['current_balance'],
            current_positions_value=acct['positions_value'],
            position_count=acct['position_count'],
            position_size=position_size,
        )
        return {'pass': bool(can_enter), 'reason': reason,
                'requested_quantity_value': position_size,
                'strategy_seq': request.strategy_seq, 'symbol': request.symbol}
