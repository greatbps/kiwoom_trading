"""
RAE (Re-Acceleration Entry) Detector — v1.3

목적: 초입 impulse 이후 눌림(pullback) → 재가속(reacceleration) 구간 포착
철학: "늦은 진입"이 아닌 "2nd wave 진입" — 구조 유지 확인 후 참여

상태머신 (stock_code별):
  IMPULSE  → 첫 impulse 진행 중 (CHoCH 직후 가격 상승)
  PULLBACK → 0.5R 이상 되돌림 진행 중 (거래량 수축 확인)
  REACCEL  → 재가속 시작 (거래량 재확장 + 가격 반등)
  ENTRY    → 진입 조건 충족 (외부에서 처리 후 제거)
  EXPIRED  → 타임아웃 / 구조 붕괴

진입 조건 (REACCEL → ENTRY):
  1. 가격 > broken_level (SMC 구조 유지)
  2. pullback 깊이 0.5R ~ 2.0R (너무 얕으면 impulse 미완, 너무 깊으면 구조 붕괴)
  3. VWAP 또는 EMA20 지지
  4. 거래량 contraction 후 re-expansion (pullback_vol < impulse_vol, current_vol expanding)
  5. 고점 대비 현재 위치 ≥ pullback_low (가격 반등 중)
  6. Trend Expansion Detector 통과 (optional validation)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional, Dict, Tuple

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class RAECandidate:
    stock_code: str
    stock_name: str
    choch_level: float        # CHoCH broken_level (지지선)
    choch_price: float        # CHoCH 감지 시 현재가
    impulse_high: float       # impulse 최고점
    detected_at: datetime
    grade: str                # A / B / C
    position_size_mult: float
    confidence: float
    phase: str = 'IMPULSE'    # IMPULSE / PULLBACK / REACCEL / EXPIRED
    pullback_low: float = 0.0
    pullback_vol_avg: float = 0.0  # pullback 구간 평균 거래량
    reaccel_started_at: Optional[datetime] = None
    r_unit: float = 0.0       # R = impulse_high - choch_level
    timeout_minutes: int = 60


class RAEDetector:
    """
    종목별 RAE 상태를 추적하고 진입 신호를 생성하는 모듈.

    사용법:
        detector = RAEDetector(config)
        detector.register(stock_code, stock_name, choch_level, choch_price, impulse_high, ...)
        signal, reason, details = detector.check(stock_code, df, current_price)
        detector.expire(stock_code)   # 구조 붕괴 / 타임아웃
    """

    def __init__(self, config: dict):
        self.config = config.get('rae', {})
        self._candidates: Dict[str, RAECandidate] = {}

    # ── 등록 ───────────────────────────────────────────────────────────────────

    def register(
        self,
        stock_code: str,
        stock_name: str,
        choch_level: float,
        choch_price: float,
        impulse_high: float,
        grade: str = 'B',
        position_size_mult: float = 0.5,
        confidence: float = 0.6,
    ) -> None:
        """CHoCH 감지 직후 RAE 후보로 등록 (IMPULSE 단계 시작)"""
        if not self.config.get('enabled', False):
            return
        r_unit = max(impulse_high - choch_level, choch_level * 0.005)
        timeout = int(self.config.get('timeout_minutes', 60))
        self._candidates[stock_code] = RAECandidate(
            stock_code=stock_code,
            stock_name=stock_name,
            choch_level=choch_level,
            choch_price=choch_price,
            impulse_high=impulse_high,
            detected_at=datetime.now(),
            grade=grade,
            position_size_mult=float(position_size_mult),
            confidence=float(confidence),
            r_unit=float(r_unit),
            timeout_minutes=timeout,
        )
        logger.info(
            f"[RAE_REGISTER] {stock_code} {stock_name}: "
            f"choch={choch_level:.0f} impulse_high={impulse_high:.0f} "
            f"R={r_unit:.0f} grade={grade}"
        )

    def expire(self, stock_code: str, reason: str = "") -> None:
        if stock_code in self._candidates:
            logger.debug(f"[RAE_EXPIRE] {stock_code} {reason}")
            del self._candidates[stock_code]

    def has_candidate(self, stock_code: str) -> bool:
        return stock_code in self._candidates

    def get_candidate(self, stock_code: str) -> Optional[RAECandidate]:
        return self._candidates.get(stock_code)

    def all_codes(self):
        return list(self._candidates.keys())

    # ── 상태 업데이트 + 진입 신호 체크 ──────────────────────────────────────────

    def check(
        self,
        stock_code: str,
        df: pd.DataFrame,
        current_price: float,
    ) -> Tuple[bool, str, dict]:
        """
        RAE 상태를 업데이트하고 진입 조건 충족 여부를 반환.

        Returns:
            (should_enter, reason, details)
        """
        cand = self._candidates.get(stock_code)
        if cand is None:
            return False, "RAE 후보 없음", {}

        details: dict = {
            'phase': cand.phase,
            'choch_level': cand.choch_level,
            'impulse_high': cand.impulse_high,
            'r_unit': cand.r_unit,
            'pullback_low': cand.pullback_low,
        }

        # ── 타임아웃 체크 (180분 이상 stale) ────────────────────────────────
        elapsed = (datetime.now() - cand.detected_at).total_seconds() / 60
        stale_limit = max(float(cand.timeout_minutes), 180.0)
        if elapsed > stale_limit:
            logger.info(f"[RAE_RESET_STALE] {stock_code}: {elapsed:.0f}분 초과 → stale 정리")
            self.expire(stock_code, f"stale({elapsed:.0f}분)")
            return False, f"RAE stale ({elapsed:.0f}분)", details
        if elapsed > cand.timeout_minutes:
            self.expire(stock_code, f"타임아웃({elapsed:.0f}분)")
            return False, f"RAE 타임아웃 ({elapsed:.0f}분)", details

        # ── 구조 붕괴 체크 (broken_level 아래로 종가 이탈) ───────────────────
        if current_price < cand.choch_level * 0.997:
            logger.info(
                f"[RAE_RESET_INVALIDATED] {stock_code}: "
                f"price={current_price:.0f} < choch_level={cand.choch_level:.0f}×0.997 → 구조 무효"
            )
            self.expire(stock_code, f"구조붕괴(price={current_price:.0f}<level={cand.choch_level:.0f})")
            return False, "SMC 구조 붕괴 → RAE 무효", details

        # ── 데이터 준비 ────────────────────────────────────────────────────────
        if df is None or len(df) < 10:
            return False, "데이터 부족", details

        try:
            close = float(df['close'].iloc[-1])
            vol   = float(df['volume'].iloc[-1])
        except Exception:
            return False, "데이터 오류", details

        # ── IMPULSE 단계: impulse_high 갱신 ─────────────────────────────────
        if cand.phase == 'IMPULSE':
            if current_price > cand.impulse_high:
                cand.impulse_high = current_price
                cand.r_unit = max(current_price - cand.choch_level, cand.choch_level * 0.005)

            # impulse 완료 조건: 최고점 대비 0.5R 이상 하락 시 PULLBACK 전환
            pullback_r = float(self.config.get('pullback_min_r', 0.5))
            if current_price < cand.impulse_high - cand.r_unit * pullback_r:
                cand.phase = 'PULLBACK'
                cand.pullback_low = current_price
                logger.info(
                    f"[RAE_PULLBACK] {stock_code}: "
                    f"impulse_high={cand.impulse_high:.0f} → pullback 시작 "
                    f"depth={(cand.impulse_high - current_price)/cand.r_unit:.1f}R"
                )

            return False, f"RAE IMPULSE 단계 (elapsed={elapsed:.0f}m)", details

        # ── PULLBACK 단계 ────────────────────────────────────────────────────
        elif cand.phase == 'PULLBACK':
            # 최저점 갱신
            if current_price < cand.pullback_low:
                cand.pullback_low = current_price

            # 너무 깊은 pullback (> max_r) → 구조 위협, 취소
            max_r = float(self.config.get('pullback_max_r', 2.0))
            depth_r = (cand.impulse_high - cand.pullback_low) / cand.r_unit if cand.r_unit > 0 else 0
            if depth_r > max_r:
                self.expire(stock_code, f"pullback 너무 깊음({depth_r:.1f}R > {max_r}R)")
                return False, f"RAE: pullback {depth_r:.1f}R 초과 → 무효", details

            # pullback 거래량 평균 누적
            try:
                avg_pb_vol = float(df['volume'].tail(5).mean())
                cand.pullback_vol_avg = avg_pb_vol
            except Exception:
                pass

            # 재가속 감지: 가격이 pullback_low에서 반등 + 거래량 증가
            reaccel_price_bounce_pct = float(self.config.get('reaccel_bounce_pct', 0.3))
            reaccel_vol_ratio        = float(self.config.get('reaccel_vol_ratio', 1.2))

            price_bouncing = current_price > cand.pullback_low * (1 + reaccel_price_bounce_pct / 100)
            vol_expanding  = (cand.pullback_vol_avg > 0 and vol >= cand.pullback_vol_avg * reaccel_vol_ratio)

            if price_bouncing and vol_expanding:
                cand.phase = 'REACCEL'
                cand.reaccel_started_at = datetime.now()
                logger.info(
                    f"[RAE_REACCEL] {stock_code}: "
                    f"pullback_low={cand.pullback_low:.0f} → 재가속 시작 "
                    f"bounce={reaccel_price_bounce_pct}% vol×{vol/cand.pullback_vol_avg:.1f}"
                )

            return False, f"RAE PULLBACK 단계 (depth={depth_r:.1f}R, elapsed={elapsed:.0f}m)", details

        # ── REACCEL 단계: 진입 조건 평가 ────────────────────────────────────
        elif cand.phase == 'REACCEL':
            return self._evaluate_entry(cand, df, current_price, elapsed, details)

        return False, f"RAE 알 수 없는 상태({cand.phase})", details

    # ── 진입 평가 ─────────────────────────────────────────────────────────────

    def _evaluate_entry(
        self,
        cand: RAECandidate,
        df: pd.DataFrame,
        current_price: float,
        elapsed: float,
        details: dict,
    ) -> Tuple[bool, str, dict]:
        """REACCEL 단계에서 진입 조건 최종 평가"""
        checks = {}
        score = 0

        # 조건 1: SMC 구조 유지 (broken_level 위 유지)
        checks['structure_intact'] = current_price > cand.choch_level
        if checks['structure_intact']:
            score += 2

        # 조건 2: pullback 깊이 (0.5R ~ 2.0R 범위)
        depth_r = (cand.impulse_high - cand.pullback_low) / cand.r_unit if cand.r_unit > 0 else 0
        checks['pullback_depth_ok'] = (
            float(self.config.get('pullback_min_r', 0.5)) <= depth_r <= float(self.config.get('pullback_max_r', 2.0))
        )
        if checks['pullback_depth_ok']:
            score += 2
        details['pullback_depth_r'] = round(depth_r, 2)

        # 조건 3: VWAP 또는 EMA20 지지
        checks['vwap_or_ema_support'] = False
        try:
            if 'vwap' in df.columns:
                vwap = float(df['vwap'].iloc[-1])
                checks['vwap_or_ema_support'] = current_price >= vwap * 0.998
                details['vwap'] = round(vwap, 0)
            if not checks['vwap_or_ema_support'] and 'ema20' in df.columns:
                ema20 = float(df['ema20'].iloc[-1])
                checks['vwap_or_ema_support'] = current_price >= ema20 * 0.998
                details['ema20'] = round(ema20, 0)
        except Exception:
            pass
        if checks['vwap_or_ema_support']:
            score += 2

        # 조건 4: 거래량 재확장 (현재 > pullback 평균 × ratio)
        reaccel_vol_ratio = float(self.config.get('reaccel_vol_ratio', 1.2))
        try:
            cur_vol = float(df['volume'].iloc[-1])
            checks['vol_expanding'] = (
                cand.pullback_vol_avg > 0
                and cur_vol >= cand.pullback_vol_avg * reaccel_vol_ratio
            )
            details['vol_ratio'] = round(cur_vol / cand.pullback_vol_avg, 2) if cand.pullback_vol_avg > 0 else 0
        except Exception:
            checks['vol_expanding'] = False
        if checks['vol_expanding']:
            score += 2

        # 조건 5: 가격이 pullback_low에서 반등 중 (상승 추세 확인)
        try:
            recent_closes = df['close'].tail(3).values
            checks['price_rising'] = (
                len(recent_closes) >= 2
                and float(recent_closes[-1]) > float(recent_closes[-2])
            )
        except Exception:
            checks['price_rising'] = False
        if checks['price_rising']:
            score += 1

        # RAESignal 스펙 필드: confidence_score(0~100), status, size_multiplier, tag
        max_score = 9  # 구조(2)+깊이(2)+VWAP(2)+거래량(2)+반등(1)
        confidence_score = round(score / max_score * 100)
        details.update({
            'checks': checks,
            'score': score,
            'elapsed_min': round(elapsed, 1),
            'grade': cand.grade,
            'confidence_score': confidence_score,
            'size_multiplier': float(self.config.get('rae_size_mult', 0.7)),
            'tag': 'SMC_RAE',
        })

        min_score = int(self.config.get('min_entry_score', 7))
        if score >= min_score:
            details['status'] = 'ACTIVE'
            reason = (
                f"[REACCEL_ENTRY] {cand.stock_code} {cand.grade}급: "
                f"score={score}/{min_score} confidence={confidence_score} "
                f"pullback={depth_r:.1f}R "
                f"struct={'✅' if checks['structure_intact'] else '❌'} "
                f"vwap={'✅' if checks['vwap_or_ema_support'] else '❌'} "
                f"vol={'✅' if checks['vol_expanding'] else '❌'} "
                f"elapsed={elapsed:.0f}m"
            )
            logger.info(f"[RAE_SIG] {reason}")
            return True, reason, details

        details['status'] = 'INVALID'
        return False, (
            f"RAE 조건 미충족 score={score}/{min_score} confidence={confidence_score} "
            f"(struct={checks['structure_intact']}, vwap={checks['vwap_or_ema_support']}, "
            f"vol={checks['vol_expanding']})"
        ), details
