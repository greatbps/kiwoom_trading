"""
패턴 인식 기반 인터페이스 (B-Phase 설계)

모든 패턴 클래스는 이 파일의 타입/ABC를 따른다.
새 패턴 추가 = PatternDetector 서브클래스 하나 추가.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd


# ─────────────────────────────────────────────
# 1. 변곡점 (ZigZag 출력 단위)
# ─────────────────────────────────────────────

@dataclass
class Pivot:
    """
    ZigZag 변곡점 (패턴 매칭 입력 단위).

    smc_utils.SwingPoint와 1:1 대응하되,
    패턴 연산에 필요한 volume 필드를 추가.
    """
    idx: int                        # DataFrame 위치 인덱스
    price: float                    # 봉 고점(high) 또는 저점(low)
    kind: str                       # 'high' | 'low'
    timestamp: Optional[pd.Timestamp] = None
    volume: float = 0.0             # 해당 봉 거래량

    def __repr__(self) -> str:
        ts = self.timestamp.strftime('%m/%d') if self.timestamp else f'idx={self.idx}'
        return f"Pivot({self.kind}@{self.price:.0f}, {ts})"

    @property
    def is_high(self) -> bool:
        return self.kind == 'high'

    @property
    def is_low(self) -> bool:
        return self.kind == 'low'


# ─────────────────────────────────────────────
# 2. 패턴 인식 결과
# ─────────────────────────────────────────────

@dataclass
class PatternResult:
    """
    패턴 감지 결과 (모든 PatternDetector의 공통 출력).

    phase 의미:
        "forming"   - 패턴 구조는 완성됐지만 아직 돌파 전. entry = 예상 진입가.
                      ScoreEngine에서는 패턴 점수의 70%만 적용.
        "breakout"  - 이번 봉에서 돌파 발생. entry = 현재가.
                      ScoreEngine에서 패턴 점수 100% 적용.
        "confirmed" - 이전 봉이 넥라인 위 마감. 약간 늦지만 안전한 진입.
                      ScoreEngine에서 패턴 점수 90% 적용.

    confidence 계산 기준:
        base_rate × (저점 유사도) × (거래량 보정) × (phase 보정)
        → cap at 0.97 (확실한 패턴은 없다)
    """
    pattern: str            # "double_bottom" | "bull_flag" | "ascending_triangle" | ...
    confidence: float       # 0.0 ~ 1.0  (동적 계산값, 고정값 절대 금지)
    entry: float            # 추천 진입가 (forming이면 예상 진입가, breakout이면 현재가)
    stop: float             # 패턴 무효화 지점 (= 손절가 상한)
    target: float           # 측정 이동 목표가 (Measured Move)
    timeframe: str          # "daily" | "60m" | "5m"
    phase: str = "breakout" # "forming" | "breakout" | "confirmed"  ← 타이밍 핵심
    pivots_used: list[Pivot] = field(default_factory=list)
    meta: dict = field(default_factory=dict)
    # meta 예시:
    #   double_bottom : {neckline, trough1, trough2, trough_diff_pct, bar_gap, vol_ratio}
    #   bull_flag     : {pole_base, pole_top, pole_height, flag_low, retracement_pct}
    #   triangle      : {upper_slope, lower_slope, apex, width_at_start}

    @property
    def risk(self) -> float:
        """entry 기준 리스크 (entry - stop)."""
        return max(0.0, self.entry - self.stop)

    @property
    def reward(self) -> float:
        """entry 기준 보상 (target - entry)."""
        return max(0.0, self.target - self.entry)

    @property
    def rr(self) -> float:
        """Risk/Reward 비율 (reward ÷ risk)."""
        return round(self.reward / self.risk, 2) if self.risk > 0 else 0.0

    def __repr__(self) -> str:
        return (
            f"PatternResult({self.pattern} | conf={self.confidence:.2f} | "
            f"entry={self.entry:.0f} stop={self.stop:.0f} target={self.target:.0f} | "
            f"RR={self.rr})"
        )


# ─────────────────────────────────────────────
# 3. 패턴 탐지기 추상 기반 클래스
# ─────────────────────────────────────────────

class PatternDetector(ABC):
    """
    모든 개별 패턴 클래스의 추상 베이스.

    새 패턴 추가 방법:
        1. 이 클래스를 상속
        2. BASE_CONFIDENCE, MIN_PIVOTS 클래스 변수 설정
        3. detect() 구현
        4. PatternManager에 register()

    절대 규칙:
        - detect()는 PatternResult 또는 None만 반환
        - PatternResult의 구조는 절대 바꾸지 않는다
        - 예외는 detect() 내부에서 잡거나 위로 전파하지 않는다
    """

    BASE_CONFIDENCE: float = 0.5    # 서브클래스에서 Bulkowski 승률로 override
    MIN_PIVOTS: int = 4             # 이 패턴을 감지하기 위한 최소 변곡점 수

    @property
    def name(self) -> str:
        """패턴 식별자 (클래스명 기본값, override 가능)."""
        return self.__class__.__name__

    @abstractmethod
    def detect(
        self,
        df: pd.DataFrame,
        pivots: list[Pivot],
    ) -> Optional[PatternResult]:
        """
        패턴 감지 핵심 로직.

        Args:
            df:      일봉 OHLCV DataFrame (컬럼 소문자: open high low close volume)
            pivots:  ZigZag 변곡점 리스트 (시간순, 과거 → 최신)
                     len(pivots) >= MIN_PIVOTS 보장됨 (PatternManager가 사전 체크)

        Returns:
            패턴 감지 성공  → PatternResult
            패턴 미감지     → None
        """
        ...

    def _normalize_df(self, df: pd.DataFrame) -> Optional[pd.DataFrame]:
        """컬럼명 소문자 통일 + 최소 길이 검증 (공통 헬퍼)."""
        if df is None or len(df) < 5:
            return None
        result = df.copy()
        result.columns = [c.lower() for c in result.columns]
        if 'close' not in result.columns:
            return None
        return result

    def _vol_multiplier(self, current_vol: float, avg_vol: float) -> float:
        """
        거래량 확인 보정 계수 (공통 헬퍼).

        ratio >= 1.5 → 1.00  (강한 돌파 확인)
        ratio >= 1.0 → 0.90  (보통)
        ratio <  1.0 → 0.75  (거래량 없는 돌파 = 신뢰 낮음)
        """
        if avg_vol <= 0:
            return 0.90
        ratio = current_vol / avg_vol
        if ratio >= 1.5:
            return 1.00
        if ratio >= 1.0:
            return 0.90
        return 0.75

    def _avg_volume(self, df: pd.DataFrame, window: int = 20) -> float:
        """최근 window봉 평균 거래량."""
        if 'volume' not in df.columns or len(df) < window:
            return 0.0
        return float(df['volume'].iloc[-window:].mean())
