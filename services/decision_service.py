"""
DecisionService — Research Layer Facade.

main_auto_trading.py는 이 클래스만 알면 된다.
Repository / EventStore / EvaluationContext 는 내부에서 처리.

설계 원칙:
- 모든 public 메서드는 실패해도 예외를 밖으로 내보내지 않는다.
- ctx=None 이면 전 메서드가 즉시 no-op 반환.
- Latency(P50/P95/P99) 는 메서드 내부에서 측정, 임계 초과 시 경고 로그.
- Feature Flag: config/research_config.yaml 에서 per-layer 제어.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml

from repositories.decision_repository import DecisionRepository
from .evaluation_context import EvaluationContext

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).parent.parent / 'config' / 'research_config.yaml'


def _load_config() -> Dict[str, Any]:
    try:
        return yaml.safe_load(_CONFIG_PATH.read_text(encoding='utf-8'))
    except Exception as e:
        logger.warning(f"[RESEARCH] research_config.yaml 로드 실패: {e} — 전체 비활성")
        return {}


class DecisionService:
    """
    사용 예시 (main_auto_trading.py):

        # __init__
        self.decision_service = DecisionService(self.db)

        # 평가 시작
        ctx = self.decision_service.begin_evaluation(
            symbol=stock_code, price=current_price, features=features,
            stock_name=stock_name, sector=sector, market=market,
        )

        # 거절 시
        self.decision_service.record_rejection(ctx, 'CHOCH_MISSING', features)
        return

        # 통과 시 (Step 2)
        self.decision_service.record_acceptance(ctx, features)
    """

    def __init__(self, db) -> None:
        self._db = db
        cfg = _load_config()
        r = cfg.get('research', {})
        self._enabled: bool         = r.get('enabled', False)
        self._dl_enabled: bool      = r.get('decision_logging', False)
        self._ev_enabled: bool      = r.get('event_logging', False)
        self._policy_version: str   = r.get('policy_version', 'SMC_v2.3')
        self._warn_ms: float        = float(r.get('latency_warn_ms', 15))
        self._error_ms: float       = float(r.get('latency_error_ms', 50))

        self._repo: Optional[DecisionRepository] = None
        if self._enabled:
            try:
                self._repo = DecisionRepository(db)
                logger.info(
                    f"[RESEARCH] DecisionService ON "
                    f"(decision_logging={self._dl_enabled}, event_logging={self._ev_enabled}, "
                    f"policy={self._policy_version})"
                )
            except Exception as e:
                logger.error(f"[RESEARCH] DecisionRepository 초기화 실패: {e} — 전체 비활성")
                self._enabled = False

    # ─── Public API ───────────────────────────────────────────────

    def begin_evaluation(
        self,
        symbol: str,
        price: float,
        features: Optional[Dict] = None,
        stock_name: str = '',
        sector: str = '',
        market: str = 'KOSPI',
        strategy_type: str = 'SMC_INTRADAY',
    ) -> Optional[EvaluationContext]:
        """
        평가 세션을 시작한다. Candidate 레코드를 생성하고 EvaluationContext를 반환.
        실패 시 None 반환 (모든 후속 호출 no-op).
        """
        if not self._enabled or not self._dl_enabled:
            return None

        observed_at = datetime.now()
        features = features or {}

        return self._safe(
            'begin_evaluation',
            self._begin_evaluation_impl,
            symbol, price, features, stock_name, sector, market, strategy_type, observed_at,
        )

    def record_rejection(
        self,
        ctx: Optional[EvaluationContext],
        reason_code: str,
        features: Optional[Dict] = None,
        confidence: float = 0.0,
        expected_rr: float = 0.0,
        risk_score: float = 0.0,
    ) -> bool:
        """거절 결정을 Decision Ledger에 기록. ctx=None 이면 즉시 False 반환."""
        if not ctx or not self._enabled or not self._dl_enabled:
            return False
        result = self._safe(
            'record_rejection',
            self._finalize,
            ctx, 'REJECT', reason_code, features or {}, confidence, expected_rr, risk_score,
        )
        return result is not None

    def record_acceptance(
        self,
        ctx: Optional[EvaluationContext],
        features: Optional[Dict] = None,
        confidence: float = 0.0,
        expected_rr: float = 0.0,
        risk_score: float = 0.0,
    ) -> Optional[str]:
        """
        PASS 결정 기록. (Phase 4A Step 2)
        반환: decision_id (str) — record_order / record_exit에 전달용.
              None = 비활성 또는 실패.
        """
        if not ctx or not self._enabled or not self._dl_enabled:
            return None
        return self._safe(
            'record_acceptance',
            self._finalize,
            ctx, 'PASS', 'PASS', features or {}, confidence, expected_rr, risk_score,
        )

    def record_order(
        self,
        decision_id: Optional[str],
        trade_id: Optional[int] = None,
        order_no: Optional[str] = None,
        executed_price: Optional[float] = None,
        trace_id: Optional[str] = None,
    ) -> bool:
        """주문 체결 기록 (Phase 4A Step 3). FROZEN → EXECUTED.

        [Pipeline Health Chain 결함 수정, 2026-08-17] trade_id가 없으면(None 또는
        0 — public.trades INSERT가 실패했다는 뜻. SERIAL PK는 1부터 시작하므로
        0은 실제 trade_id로 존재할 수 없는 값) 더 이상 EXECUTED로 위장해서 기록
        하지 않는다. 이전에는 execution_result.trade_id=0이라는 sentinel만 남긴
        채 lifecycle_status=EXECUTED로 기록돼, trades 테이블 INSERT 실패가
        Pipeline Health Chain 밖에서는 전혀 보이지 않았다. 이제 trade_id가 없으면
        EXECUTION_FAILED로 기록해 그 실패를 명시적으로 남긴다. trade_id가 정상
        (양의 정수)으로 넘어오는 기존 EXECUTED 경로는 그대로 보존한다.
        """
        if not decision_id or not self._enabled or not self._dl_enabled:
            return False
        if not trade_id:
            return bool(self._safe(
                'record_order_trade_persist_failure',
                self._repo.mark_execution_failed,
                decision_id,
                f'TRADE_PERSIST_FAILURE order_no={order_no}',
                trace_id,
            ))
        return bool(self._safe(
            'record_order',
            self._repo.mark_executed,
            decision_id,
            trade_id,
            order_no,
            executed_price,
            None,   # slippage_pct — 현재 미계산
            None,   # executed_at — mark_executed 내부에서 now() 사용
            trace_id,
        ))

    def record_order_failure(
        self,
        ctx: Optional[EvaluationContext],
        reason: str,
        features: Optional[Dict] = None,
    ) -> Optional[str]:
        """
        [Pipeline Health Chain 결함 수정, 2026-08-17] SMC가 이미 PASS로 승인한
        candidate가 브로커 주문 단계(키움 API 매수 주문)에서 실패한 경우 전용.

        기존에는 execute_buy()가 이 경우를 record_rejection()으로 기록해 decision=
        'REJECT'(SMC가 애초에 거부했다는 뜻)로 남았다 — 실제로는 SMC가 이미 승인
        (PASS)했는데 브로커 단계에서 실패한 것이라 의미가 다르다. 이 메서드는
        decision='PASS' + lifecycle_status=EXECUTION_FAILED로 정확히 기록한다
        (mark_execution_failed()는 그동안 구현만 되어 있고 호출부가 없었다).

        주문/진입 판단 로직 자체는 건드리지 않는다 — 이미 실패가 확정된 뒤의
        기록(persistence) 경로만 바꾼다.

        반환: decision_id (str) | None — 실패 또는 비활성 시 None.
        """
        if not ctx or not self._enabled or not self._dl_enabled:
            return None
        return self._safe(
            'record_order_failure',
            self._record_order_failure_impl,
            ctx, reason, features or {},
        )

    def record_exit(
        self,
        decision_id: Optional[str],
        exit_price: Optional[float] = None,
        exit_reason: Optional[str] = None,
        pnl_pct: Optional[float] = None,
        trace_id: Optional[str] = None,
    ) -> bool:
        """포지션 청산 기록 (Phase 4A Step 4). EXECUTED → OUTCOME_RECORDED."""
        if not decision_id or not self._enabled or not self._dl_enabled:
            return False
        return bool(self._safe(
            'record_exit',
            self._repo.mark_exited,
            decision_id,
            float(exit_price or 0.0),
            str(exit_reason or ''),
            float(pnl_pct or 0.0),
            trace_id,
        ))

    # ─── Internal Implementation ──────────────────────────────────

    def _begin_evaluation_impl(
        self,
        symbol: str,
        price: float,
        features: Dict,
        stock_name: str,
        sector: str,
        market: str,
        strategy_type: str,
        observed_at: datetime,
    ) -> Optional[EvaluationContext]:
        candidate_id, trace_id = self._repo.create_candidate(
            stock_code=symbol,
            stock_name=stock_name or None,
            observed_at=observed_at,
            price=price,
            sector=sector or None,
            market=market or None,
            rs_score=features.get('rs_score'),
            rvol=features.get('rvol'),
            atr_pct=features.get('atr_pct'),
            regime=features.get('regime') or features.get('market_regime'),
            strategy_type=strategy_type,
            created_by='decision_service',
        )
        if candidate_id is None:
            return None

        return EvaluationContext(
            trace_id=trace_id,
            candidate_id=candidate_id,
            symbol=symbol,
            observed_at=observed_at,
            policy_version=self._policy_version,
            price=price,
            strategy_type=strategy_type,
        )

    def _finalize(
        self,
        ctx: EvaluationContext,
        decision: str,
        reason_code: str,
        features: Dict,
        confidence: float,
        expected_rr: float,
        risk_score: float,
    ) -> Optional[str]:
        """
        PASS / REJECT 공통 결정 기록 경로.
        반환: decision_id (str) — PASS 시 record_order/record_exit에 전달용.
              None = 실패.
        """
        decided_at = datetime.now()
        feature_snapshot = self._build_snapshot(features)

        decision_id = self._repo.freeze_decision(
            candidate_id=ctx.candidate_id,
            stock_code=ctx.symbol,
            decision=decision,
            decision_reason_code=self._normalize_reason(reason_code),
            policy_version=ctx.policy_version,
            feature_snapshot=feature_snapshot,
            observed_at=ctx.observed_at,
            decided_at=decided_at,
            confidence=confidence or features.get('confidence', 0.0),
            expected_rr=expected_rr or features.get('expected_rr', 0.0),
            risk_score=risk_score or features.get('risk_score', 0.0),
            strategy_type=getattr(ctx, 'strategy_type', 'SMC_INTRADAY'),
            trace_id=ctx.trace_id,
            created_by='decision_service',
        )
        if decision_id is None:
            return None

        logger.info(
            f"[RESEARCH] {decision} recorded "
            f"trace={ctx.trace_id} sym={ctx.symbol} reason={reason_code}"
        )
        return decision_id

    def _record_order_failure_impl(
        self,
        ctx: EvaluationContext,
        reason: str,
        features: Dict,
    ) -> Optional[str]:
        """record_order_failure()의 실제 구현. PASS로 freeze한 뒤 즉시
        EXECUTION_FAILED로 전이한다 — 두 단계지만 한 번의 호출 내에서
        원자적으로(같은 트레이딩 스레드 안에서 연속) 수행된다."""
        decided_at = datetime.now()
        feature_snapshot = self._build_snapshot(features)

        decision_id = self._repo.freeze_decision(
            candidate_id=ctx.candidate_id,
            stock_code=ctx.symbol,
            decision='PASS',
            decision_reason_code='PASS',
            policy_version=ctx.policy_version,
            feature_snapshot=feature_snapshot,
            observed_at=ctx.observed_at,
            decided_at=decided_at,
            strategy_type=getattr(ctx, 'strategy_type', 'SMC_INTRADAY'),
            trace_id=ctx.trace_id,
            created_by='decision_service',
        )
        if decision_id is None:
            return None

        self._repo.mark_execution_failed(decision_id, reason[:200], ctx.trace_id)
        logger.info(
            f"[RESEARCH] ORDER_EXECUTION_FAILED recorded "
            f"trace={ctx.trace_id} sym={ctx.symbol} reason={reason}"
        )
        return decision_id

    # ─── Helper ───────────────────────────────────────────────────

    _REASON_MAP: Dict[str, str] = {
        # main_auto_trading.py 게이트별 reason → reason_dictionary 코드
        'GLOBAL_GATE':              'GLOBAL_GATE_BLOCKED',
        'STOCK_GATE':               'STOCK_GATE_BLOCKED',
        'MARKET_SENSOR':            'MARKET_SENSOR_BLOCKED',
        # 🔧 2026-07-13: REGIME_BLOCK 관측성 보완 (기존엔 미매핑 → OTHER로 뭉뚱그려짐)
        'REGIME_BLOCK':             'REGIME_BLOCKED',
        'DATA_INSUFFICIENT':        'DATA_INSUFFICIENT',
        'SMC_NO_SIG':               'CHOCH_MISSING',
        'NO_CHOCH':                 'CHOCH_MISSING',
        'NO_SWEEP':                 'SWEEP_MISSING',
        'NO_FVG':                   'FVG_MISSING',
        'LOW_VOLUME':               'VOLUME_INSUFFICIENT',
        'LOW_CONFIDENCE':           'CONFIDENCE_LOW',
        'HIGH_RISK':                'RISK_SCORE_HIGH',
        'POLICY_MISMATCH':          'POLICY_MISMATCH',
        # 🔧 2026-07-26: W Pattern Filter (Entry Quality 추가 게이트)
        'W_PATTERN':                'W_PATTERN_MISSING',

        # 🔧 2026-07-27: execute_buy() 내부 실행단계 REJECT (Audit 7, P0)
        # main_auto_trading.py의 기존 로그 태그를 reason_code로 그대로 넘겨도
        # 여기서 8개 버킷 중 하나로 정규화됨 — 호출부에서 버킷명을 직접 계산할 필요 없음.
        'TRADE_CD':                 'COOLDOWN_ACTIVE',
        'COOLDOWN_BLOCK':           'COOLDOWN_ACTIVE',
        'PAT_DUP':                  'COOLDOWN_ACTIVE',
        'DUPLICATE_BLOCK':          'DUPLICATE_POSITION',
        'DAILY_STOCK_LIMIT':        'MAX_POSITIONS',
        'ZERO_QTY_BLOCK':           'INSUFFICIENT_CAPITAL',
        'RVOL_BLOCK':               'ENTRY_QUALITY_BLOCKED',
        'EMA9_BLOCK':               'ENTRY_QUALITY_BLOCKED',
        'VWAP_DIST_BLOCK':          'ENTRY_QUALITY_BLOCKED',
        'SD_FILTER_BLOCK':          'ENTRY_QUALITY_BLOCKED',
        'SD_DROP_BLOCK':            'ENTRY_QUALITY_BLOCKED',
        'POS_FILTER_BO':            'ENTRY_QUALITY_BLOCKED',
        'POS_FILTER_EMA':           'ENTRY_QUALITY_BLOCKED',
        'FILTER_EXCEPTION':         'ENTRY_QUALITY_BLOCKED',
        'ML_BLOCK':                 'ENTRY_QUALITY_BLOCKED',
        'EQ_BLOCK':                 'ENTRY_QUALITY_BLOCKED',
        'PATTERN_SIZER_SKIP':       'ENTRY_QUALITY_BLOCKED',
        'TIME_WEIGHT':              'ENTRY_QUALITY_BLOCKED',
        'FIX_C_V2':                 'ENTRY_QUALITY_BLOCKED',
        'BAN_LIST_BLOCK':           'RISK_BLOCKED',
        'LSG_BLOCK':                'RISK_BLOCKED',
        'DRIFT_BLOCK':              'RISK_BLOCKED',
        'CONSERVATIVE_BLOCK':       'RISK_BLOCKED',
        'SELF_OPT_BLOCK':           'RISK_BLOCKED',
        'DB_HARD_STOP':             'RISK_BLOCKED',
        'REGIME_ENTRY_BLOCK':       'RISK_BLOCKED',
        'SECTOR_LIMIT':             'RISK_BLOCKED',
        'PORT_LIMIT':               'RISK_BLOCKED',
        'DEF_LIMIT':                'RISK_BLOCKED',
        'NO_RISK_MGR':              'RISK_BLOCKED',
        'API_FAILURE':              'API_FAILURE',
        'ORDER_FAILURE':            'ORDER_FAILURE',
    }

    _VALID_REASON_CODES = {
        'PASS', 'CHOCH_MISSING', 'SWEEP_MISSING', 'FVG_MISSING',
        'VOLUME_INSUFFICIENT', 'CONFIDENCE_LOW', 'RISK_SCORE_HIGH',
        'GLOBAL_GATE_BLOCKED', 'STOCK_GATE_BLOCKED', 'MARKET_SENSOR_BLOCKED',
        'DATA_INSUFFICIENT', 'POLICY_MISMATCH', 'OTHER', 'REGIME_BLOCKED',
        'W_PATTERN_MISSING',
        # 🔧 2026-07-27: execute_buy() 실행단계 REJECT (Audit 7, P0)
        'COOLDOWN_ACTIVE', 'RISK_BLOCKED', 'DUPLICATE_POSITION',
        'MAX_POSITIONS', 'INSUFFICIENT_CAPITAL', 'ENTRY_QUALITY_BLOCKED',
        'API_FAILURE', 'ORDER_FAILURE',
    }

    def _normalize_reason(self, raw: str) -> str:
        """stage/reason 문자열 → reason_dictionary 코드."""
        if raw in self._VALID_REASON_CODES:
            return raw
        upper = raw.upper()
        for k, v in self._REASON_MAP.items():
            if k in upper:
                return v
        # CHoCH 관련 키워드 감지
        for kw in ('CHOCH', 'CHO_CH', 'STRUCTURE', 'CHC'):
            if kw in upper:
                return 'CHOCH_MISSING'
        for kw in ('SWEEP', 'LIQUIDIT'):
            if kw in upper:
                return 'SWEEP_MISSING'
        if 'FVG' in upper:
            return 'FVG_MISSING'
        if 'W_PATTERN' in upper:
            return 'W_PATTERN_MISSING'
        for kw in ('VOLUME', 'RVOL', '거래량'):
            if kw in upper:
                return 'VOLUME_INSUFFICIENT'
        return 'OTHER'

    @staticmethod
    def _build_snapshot(features: Dict) -> Dict:
        """
        feature_snapshot JSONB — 결정 당시의 현실.
        존재하지 않는 키는 None으로 기록 (명시적 부재).
        """
        keys = [
            'choch_detected', 'choch_grade', 'sweep_detected', 'sweep_type',
            'sweep_distance_pct', 'fvg_detected', 'rvol', 'atr_pct', 'regime',
            'rs_score', 'ema_gap_pct', 'htf_trend', 'structure_trend',
            'risk_score', 'expected_rr', 'confidence', 'sector',
            'market_regime', 'vwap_distance_pct',
        ]
        snap: Dict = {k: features.get(k) for k in keys}
        # 추가 키 보존 (SMC details 등)
        for k, v in features.items():
            if k not in snap and isinstance(v, (int, float, str, bool, type(None))):
                snap[k] = v
        return snap

    # ─── Shadow Wrapper ───────────────────────────────────────────

    def _safe(self, name: str, fn, *args, **kwargs):
        """
        Trading flow 보호 wrapper.
        - 실패 시 None/False 반환, 예외 절대 전파 안 함.
        - 지연 > warn_ms: WARNING 로그.
        - 지연 > error_ms: ERROR 로그 (KPI 위반).
        """
        t0 = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
            elapsed = (time.perf_counter() - t0) * 1000
            if elapsed > self._error_ms:
                logger.error(
                    f"[RESEARCH_LATENCY] {name} {elapsed:.1f}ms "
                    f"(KPI={self._error_ms}ms 위반)"
                )
            elif elapsed > self._warn_ms:
                logger.warning(
                    f"[RESEARCH_LATENCY] {name} {elapsed:.1f}ms "
                    f"(warn>{self._warn_ms}ms)"
                )
            return result
        except Exception:
            elapsed = (time.perf_counter() - t0) * 1000
            logger.exception(
                f"[RESEARCH] {name} 실패 ({elapsed:.1f}ms) — 거래 영향 없음"
            )
            return None
