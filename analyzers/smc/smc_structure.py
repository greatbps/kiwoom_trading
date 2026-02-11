"""
SMC Market Structure 분석

- Market Structure (HH/HL/LH/LL)
- BOS (Break of Structure) - 추세 지속
- CHoCH (Change of Character) - 추세 전환
"""

from dataclasses import dataclass, field
from typing import List, Optional, Dict
from enum import Enum
import pandas as pd

from .smc_utils import SwingPoint, find_swing_points


class MarketTrend(Enum):
    """시장 추세"""
    BULLISH = "bullish"    # 상승 (HH + HL)
    BEARISH = "bearish"    # 하락 (LH + LL)
    RANGING = "ranging"    # 횡보


class StructureBreak(Enum):
    """구조 변화 타입"""
    BOS = "bos"            # Break of Structure (추세 지속)
    CHOCH = "choch"        # Change of Character (추세 전환)
    NONE = "none"


@dataclass
class MarketStructure:
    """시장 구조 상태"""
    trend: MarketTrend
    last_hh: Optional[SwingPoint] = None  # 마지막 Higher High
    last_hl: Optional[SwingPoint] = None  # 마지막 Higher Low
    last_lh: Optional[SwingPoint] = None  # 마지막 Lower High
    last_ll: Optional[SwingPoint] = None  # 마지막 Lower Low
    swing_points: List[SwingPoint] = field(default_factory=list)
    classified_swings: List[Dict] = field(default_factory=list)


@dataclass
class StructureBreakEvent:
    """BOS/CHoCH 이벤트"""
    type: StructureBreak
    index: int
    price: float
    broken_level: float    # 돌파된 레벨
    direction: str         # 'bullish' | 'bearish'
    timestamp: Optional[pd.Timestamp] = None

    def __repr__(self):
        return f"{self.type.value.upper()}({self.direction}@{self.price:.0f})"


class SMCStructureAnalyzer:
    """
    SMC Market Structure 분석기

    기능:
    1. 스윙 고점/저점 (HH/HL/LH/LL) 분류
    2. BOS (Break of Structure) 탐지 - 추세 지속
    3. CHoCH (Change of Character) 탐지 - 추세 전환
    """

    def __init__(
        self,
        swing_lookback: int = 5,
        min_swing_size_pct: float = 0.3,
        bos_confirm_candles: int = 1
    ):
        """
        Args:
            swing_lookback: 스윙 탐지 lookback
            min_swing_size_pct: 최소 스윙 크기 (%)
            bos_confirm_candles: BOS 확인 봉 수
        """
        self.swing_lookback = swing_lookback
        self.min_swing_size_pct = min_swing_size_pct
        self.bos_confirm_candles = bos_confirm_candles

        # 캐시 (성능 최적화)
        self._cache = {
            'last_df_len': 0,
            'swing_points': [],
            'structure': None
        }

    def analyze_structure(self, df: pd.DataFrame) -> MarketStructure:
        """
        시장 구조 분석 (HH/HL/LH/LL 분류)

        Args:
            df: OHLCV DataFrame

        Returns:
            MarketStructure 상태
        """
        if df is None or len(df) < self.swing_lookback * 2 + 5:
            return MarketStructure(
                trend=MarketTrend.RANGING,
                swing_points=[]
            )

        # 스윙 포인트 탐지
        swing_points = find_swing_points(
            df,
            lookback=self.swing_lookback,
            min_swing_size_pct=self.min_swing_size_pct
        )

        if len(swing_points) < 3:
            return MarketStructure(
                trend=MarketTrend.RANGING,
                swing_points=swing_points
            )

        # 스윙 분류 (HH/HL/LH/LL)
        classified = self._classify_swings(swing_points)

        # 현재 추세 판단
        trend = self._determine_trend(classified)

        # 마지막 각 타입의 스윙
        last_hh = self._get_last_swing_by_label(classified, 'HH')
        last_hl = self._get_last_swing_by_label(classified, 'HL')
        last_lh = self._get_last_swing_by_label(classified, 'LH')
        last_ll = self._get_last_swing_by_label(classified, 'LL')

        structure = MarketStructure(
            trend=trend,
            last_hh=last_hh,
            last_hl=last_hl,
            last_lh=last_lh,
            last_ll=last_ll,
            swing_points=swing_points,
            classified_swings=classified
        )

        # 캐시 업데이트
        self._cache['last_df_len'] = len(df)
        self._cache['swing_points'] = swing_points
        self._cache['structure'] = structure

        return structure

    def _classify_swings(self, swing_points: List[SwingPoint]) -> List[Dict]:
        """
        스윙 포인트를 HH/HL/LH/LL로 분류

        HH (Higher High): 이전 고점보다 높은 고점
        HL (Higher Low): 이전 저점보다 높은 저점
        LH (Lower High): 이전 고점보다 낮은 고점
        LL (Lower Low): 이전 저점보다 낮은 저점
        """
        if len(swing_points) < 2:
            return []

        classified = []
        last_high = None
        last_low = None

        for sp in swing_points:
            label = None

            if sp.type == 'high':
                if last_high is not None:
                    if sp.price > last_high.price:
                        label = 'HH'  # Higher High
                    else:
                        label = 'LH'  # Lower High
                else:
                    label = 'H'  # 첫 번째 고점
                last_high = sp

            elif sp.type == 'low':
                if last_low is not None:
                    if sp.price > last_low.price:
                        label = 'HL'  # Higher Low
                    else:
                        label = 'LL'  # Lower Low
                else:
                    label = 'L'  # 첫 번째 저점
                last_low = sp

            classified.append({
                'swing': sp,
                'label': label
            })

        return classified

    def _determine_trend(self, classified: List[Dict]) -> MarketTrend:
        """
        최근 스윙 패턴으로 추세 판단

        상승 추세: HH + HL 패턴
        하락 추세: LH + LL 패턴
        """
        if len(classified) < 4:
            return MarketTrend.RANGING

        # 최근 4개 스윙의 라벨
        recent_labels = [c['label'] for c in classified[-4:]]

        # 상승 패턴: HH와 HL이 최근에 있음
        has_hh = 'HH' in recent_labels
        has_hl = 'HL' in recent_labels

        # 하락 패턴: LH와 LL이 최근에 있음
        has_lh = 'LH' in recent_labels
        has_ll = 'LL' in recent_labels

        if has_hh and has_hl and not (has_lh or has_ll):
            return MarketTrend.BULLISH
        elif has_lh and has_ll and not (has_hh or has_hl):
            return MarketTrend.BEARISH
        else:
            return MarketTrend.RANGING

    def _get_last_swing_by_label(
        self,
        classified: List[Dict],
        label: str
    ) -> Optional[SwingPoint]:
        """특정 라벨의 마지막 스윙 반환"""
        for c in reversed(classified):
            if c['label'] == label:
                return c['swing']
        return None

    def detect_bos(
        self,
        df: pd.DataFrame,
        structure: MarketStructure
    ) -> Optional[StructureBreakEvent]:
        """
        BOS (Break of Structure) 탐지 - 추세 지속

        상승 BOS: 상승 추세 중 이전 HH를 상향 돌파
        하락 BOS: 하락 추세 중 이전 LL을 하향 돌파

        Args:
            df: OHLCV DataFrame
            structure: 현재 MarketStructure

        Returns:
            StructureBreakEvent 또는 None
        """
        if df is None or len(df) < 2:
            return None

        # 컬럼 소문자
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        # 마지막 확정 봉
        last_idx = len(df) - 2
        if last_idx < 0:
            return None

        last_candle = df.iloc[last_idx]
        timestamp = df.index[last_idx] if isinstance(df.index, pd.DatetimeIndex) else None

        # 상승 BOS: 상승 추세 중 HH 돌파
        if structure.trend == MarketTrend.BULLISH and structure.last_hh:
            hh_level = structure.last_hh.price

            # 종가가 HH를 상향 돌파
            if last_candle['close'] > hh_level:
                return StructureBreakEvent(
                    type=StructureBreak.BOS,
                    index=last_idx,
                    price=last_candle['close'],
                    broken_level=hh_level,
                    direction='bullish',
                    timestamp=timestamp
                )

        # 하락 BOS: 하락 추세 중 LL 돌파
        if structure.trend == MarketTrend.BEARISH and structure.last_ll:
            ll_level = structure.last_ll.price

            # 종가가 LL을 하향 돌파
            if last_candle['close'] < ll_level:
                return StructureBreakEvent(
                    type=StructureBreak.BOS,
                    index=last_idx,
                    price=last_candle['close'],
                    broken_level=ll_level,
                    direction='bearish',
                    timestamp=timestamp
                )

        return None

    def detect_choch(
        self,
        df: pd.DataFrame,
        structure: MarketStructure
    ) -> Optional[StructureBreakEvent]:
        """
        CHoCH (Change of Character) 탐지 - 추세 전환 (핵심!)

        상승 CHoCH: 하락 추세 중 LH를 상향 돌파 -> 상승 전환
        하락 CHoCH: 상승 추세 중 HL을 하향 돌파 -> 하락 전환

        CHoCH는 추세 전환의 첫 신호로, SMC 전략의 핵심입니다.

        Args:
            df: OHLCV DataFrame
            structure: 현재 MarketStructure

        Returns:
            StructureBreakEvent 또는 None

        Note:
            - shift(-1) 미사용 (실시간 호환)
            - 봉마감 기준 확정된 돌파만 인정
        """
        if df is None or len(df) < 2:
            return None

        # 컬럼 소문자
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        # 마지막 확정 봉
        last_idx = len(df) - 2
        if last_idx < 0:
            return None

        last_candle = df.iloc[last_idx]
        timestamp = df.index[last_idx] if isinstance(df.index, pd.DatetimeIndex) else None

        # 상승 CHoCH: 하락 추세 또는 횡보 중 LH를 상향 돌파
        # (하락에서 상승으로 전환)
        if structure.trend in [MarketTrend.BEARISH, MarketTrend.RANGING]:
            if structure.last_lh:
                lh_level = structure.last_lh.price

                # 종가가 LH를 상향 돌파
                if last_candle['close'] > lh_level:
                    return StructureBreakEvent(
                        type=StructureBreak.CHOCH,
                        index=last_idx,
                        price=last_candle['close'],
                        broken_level=lh_level,
                        direction='bullish',
                        timestamp=timestamp
                    )

        # 하락 CHoCH: 상승 추세 또는 횡보 중 HL을 하향 돌파
        # (상승에서 하락으로 전환)
        if structure.trend in [MarketTrend.BULLISH, MarketTrend.RANGING]:
            if structure.last_hl:
                hl_level = structure.last_hl.price

                # 종가가 HL을 하향 돌파
                if last_candle['close'] < hl_level:
                    return StructureBreakEvent(
                        type=StructureBreak.CHOCH,
                        index=last_idx,
                        price=last_candle['close'],
                        broken_level=hl_level,
                        direction='bearish',
                        timestamp=timestamp
                    )

        return None

    def get_structure_summary(self, structure: MarketStructure) -> Dict:
        """구조 요약 반환"""
        return {
            'trend': structure.trend.value,
            'swing_count': len(structure.swing_points),
            'last_hh': structure.last_hh.price if structure.last_hh else None,
            'last_hl': structure.last_hl.price if structure.last_hl else None,
            'last_lh': structure.last_lh.price if structure.last_lh else None,
            'last_ll': structure.last_ll.price if structure.last_ll else None
        }
