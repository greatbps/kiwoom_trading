"""
SMC 유틸리티 함수

- 스윙 포인트 탐지 (실시간 호환)
- 유동성 스윕 탐지
"""

from dataclasses import dataclass
from typing import List, Optional
import pandas as pd
import numpy as np
import logging

_sweep_logger = logging.getLogger('sweep_attempt')
from .smc_decision_logger import get_smc_logger


@dataclass
class SwingPoint:
    """스윙 포인트 데이터"""
    index: int           # DataFrame 인덱스 위치
    price: float         # 가격
    type: str            # 'high' | 'low'
    timestamp: Optional[pd.Timestamp] = None

    def __repr__(self):
        return f"SwingPoint({self.type}@{self.price:.0f}, idx={self.index})"


@dataclass
class LiquiditySweep:
    """유동성 스윕 데이터"""
    index: int
    swept_level: float   # 스윕된 레벨
    sweep_high: float    # 스윕 고점
    sweep_low: float     # 스윕 저점
    direction: str       # 'bullish' | 'bearish' (스윕 후 예상 방향)
    sweep_type: str = 'penetration'  # 🔧 2026-03-09: 'penetration' | 'equal_level'

    def __repr__(self):
        return f"LiquiditySweep({self.direction}@{self.swept_level:.0f}, type={self.sweep_type})"


def find_swing_points(
    df: pd.DataFrame,
    lookback: int = 5,
    min_swing_size_pct: float = 0.0
) -> List[SwingPoint]:
    """
    스윙 고점/저점 탐지 (실시간 호환)

    Args:
        df: OHLCV DataFrame (컬럼: open, high, low, close)
        lookback: 좌우 비교 봉 수 (기본 5)
        min_swing_size_pct: 최소 스윙 크기 (%) - 노이즈 제거용

    Returns:
        SwingPoint 리스트 (시간순 정렬)

    Note:
        - shift(-1) 미사용 (실시간 호환)
        - 마지막 봉은 미확정으로 제외
        - 과거 데이터만 참조
    """
    if df is None or len(df) < lookback * 2 + 1:
        return []

    # 컬럼명 소문자 통일
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    if 'high' not in df.columns or 'low' not in df.columns:
        return []

    swings: List[SwingPoint] = []
    high_values = df['high'].values
    low_values = df['low'].values

    # 마지막 봉은 미확정이므로 제외
    end_idx = len(df) - 1

    for i in range(lookback, end_idx):
        # 스윙 고점 체크: 좌우 lookback개 봉보다 높아야 함
        is_swing_high = True
        current_high = high_values[i]

        for j in range(1, lookback + 1):
            # 좌측 비교
            if i - j >= 0 and current_high <= high_values[i - j]:
                is_swing_high = False
                break
            # 우측 비교 (확정된 봉만, 마지막 봉 제외)
            if i + j < end_idx and current_high <= high_values[i + j]:
                is_swing_high = False
                break

        # 스윙 저점 체크: 좌우 lookback개 봉보다 낮아야 함
        is_swing_low = True
        current_low = low_values[i]

        for j in range(1, lookback + 1):
            # 좌측 비교
            if i - j >= 0 and current_low >= low_values[i - j]:
                is_swing_low = False
                break
            # 우측 비교 (확정된 봉만)
            if i + j < end_idx and current_low >= low_values[i + j]:
                is_swing_low = False
                break

        # 최소 크기 필터
        if min_swing_size_pct > 0:
            avg_price = (current_high + current_low) / 2
            swing_size = abs(current_high - current_low) / avg_price * 100
            if swing_size < min_swing_size_pct:
                is_swing_high = False
                is_swing_low = False

        # 스윙 포인트 추가
        timestamp = df.index[i] if isinstance(df.index, pd.DatetimeIndex) else None

        if is_swing_high:
            swings.append(SwingPoint(
                index=i,
                price=current_high,
                type='high',
                timestamp=timestamp
            ))

        if is_swing_low:
            swings.append(SwingPoint(
                index=i,
                price=current_low,
                type='low',
                timestamp=timestamp
            ))

    # 시간순 정렬
    swings.sort(key=lambda x: x.index)

    return swings


def detect_liquidity_sweep(
    df: pd.DataFrame,
    swing_points: List[SwingPoint],
    lookback: int = 20,
    sweep_threshold_pct: float = 0.1,
    end_idx: int = None,  # 🔧 2026-03-07: 탐색 종료 인덱스 (CHoCH index-1 전달 시 순서 강제)
    # 🔧 2026-03-09: Equal Level Sweep (Tier 2) — 한국 시장 1~2틱 터치 후 반전 패턴
    equal_level_tolerance_pct: float = 0.0,   # 0.0=비활성, 0.03=허용 ±0.03% 이내 터치
    equal_level_reaction_body: float = 0.5,   # 반응 캔들 최소 body 비율
    equal_level_volume_mult: float = 1.5,     # 볼륨 스파이크 배율
    symbol: str = '',                          # 🔧 2026-03-10: Sweep Attempt Log용 종목코드
    equal_level_distance_min_pct: float = 0.0,  # 🔧 2026-03-18: 거리 하한 (0.0=비활성)
    equal_level_distance_max_pct: float = 999.0,  # 🔧 2026-03-18: 거리 상한 (999=비활성)
) -> Optional[LiquiditySweep]:
    """
    유동성 스윕 탐지 (스탑헌팅) — 2-tier 구조

    Tier 1 (Penetration Sweep):
    - 이전 스윙 고점/저점을 잠깐 돌파(wick)
    - 봉 종가는 레벨 안으로 복귀
    - 최소 돌파율: sweep_threshold_pct (0.1%)

    Tier 2 (Equal Level Sweep):
    - 스윙 레벨을 아주 살짝 터치 (±equal_level_tolerance_pct 이내)
    - 강한 불리시 반응 캔들 (body ≥ equal_level_reaction_body)
    - 볼륨 스파이크 (≥ 20봉 평균 × equal_level_volume_mult)
    - equal_level_tolerance_pct=0.0 → Tier 2 비활성

    Args:
        df: OHLCV DataFrame
        swing_points: 기존 스윙 포인트들
        lookback: 최근 N봉 내 스윕 탐지
        sweep_threshold_pct: Tier1 스윕 인정 최소 돌파율 (%)
        end_idx: 탐색 종료 인덱스 (None=마지막 확정봉만 체크)
                 CHoCH index-1 전달 시 Sweep → CHoCH 순서 강제됨
        equal_level_tolerance_pct: Tier2 허용 터치 범위 (%) — 0.0=비활성
        equal_level_reaction_body: Tier2 반응 캔들 최소 body 비율
        equal_level_volume_mult: Tier2 볼륨 스파이크 배율

    Returns:
        최근 발생한 LiquiditySweep 또는 None (Tier1 우선, 없으면 Tier2)

    Note:
        end_idx=None: 기존 동작 (last_candle 1개만 체크)
        end_idx=N: N봉부터 과거 방향으로 lookback 범위 내 탐색 (가장 최근 스윕 반환)
    """
    if df is None or len(df) < 2 or len(swing_points) < 2:
        return None

    # 컬럼명 소문자 통일
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    # 🔧 2026-03-07: end_idx 지정 시 해당 봉부터 과거 방향 탐색 (순서 강제)
    # None이면 기존 동작 유지 (마지막 확정봉만 체크)
    if end_idx is None:
        search_indices = [len(df) - 2]
    else:
        if end_idx < 0:
            return None
        # end_idx부터 과거 방향으로 lookback 범위 탐색 (가장 최근 스윕 우선)
        search_start = max(0, end_idx - lookback)
        search_indices = range(end_idx, search_start - 1, -1)

    for check_idx in search_indices:
        if check_idx < 0 or check_idx >= len(df):
            continue

        check_candle = df.iloc[check_idx]

        # 해당 봉 이전 스윙 포인트들 (lookback 범위 내)
        recent_swings = [
            sp for sp in swing_points
            if sp.index < check_idx and check_idx - sp.index <= lookback
        ]

        if not recent_swings:
            continue

        # 저점 스윕 체크 (Bullish Liquidity Sweep)
        recent_lows = [sp for sp in recent_swings if sp.type == 'low']
        if recent_lows:
            last_swing_low = max(recent_lows, key=lambda x: x.index)
            swing_low_price = last_swing_low.price
            swing_age = check_idx - last_swing_low.index

            if check_candle['low'] < swing_low_price and check_candle['close'] > swing_low_price:
                sweep_depth_pct = (swing_low_price - check_candle['low']) / swing_low_price * 100

                if sweep_depth_pct >= sweep_threshold_pct:
                    # 🔧 2026-03-10: Sweep Attempt Log
                    _sweep_logger.debug(
                        f'[SWEEP_DETECTED] sym={symbol} type=equal_low level={swing_low_price:.0f} '
                        f'distance={sweep_depth_pct:.3f}% swing_age={swing_age} tier=penetration'
                    )
                    _sweep_logger.info(
                        f'[SWEEP_RESULT] sym={symbol} type=penetration dist={sweep_depth_pct:.2f}%'
                    )
                    get_smc_logger().log_sweep(symbol, 'penetration', sweep_depth_pct)
                    return LiquiditySweep(
                        index=check_idx,
                        swept_level=swing_low_price,
                        sweep_high=check_candle['high'],
                        sweep_low=check_candle['low'],
                        direction='bullish',  # 저점 스윕 후 상승 예상
                        sweep_type='penetration'
                    )
                else:
                    # 🔧 2026-03-10: Sweep Attempt Log — distance 미달
                    _sweep_logger.debug(
                        f'[SWEEP_MISS] sym={symbol} type=equal_low level={swing_low_price:.0f} '
                        f'distance={sweep_depth_pct:.3f}% < threshold={sweep_threshold_pct}% '
                        f'swing_age={swing_age} reason=distance_too_small'
                    )
            elif check_candle['low'] >= swing_low_price:
                # 저점 미돌파 — 가장 가까운 거리 계산
                near_pct = abs(check_candle['low'] - swing_low_price) / swing_low_price * 100
                _sweep_logger.debug(
                    f'[SWEEP_MISS] sym={symbol} type=equal_low level={swing_low_price:.0f} '
                    f'distance={near_pct:.3f}% swing_age={swing_age} reason=no_penetration'
                )

        # 고점 스윕 체크 (Bearish Liquidity Sweep)
        recent_highs = [sp for sp in recent_swings if sp.type == 'high']
        if recent_highs:
            last_swing_high = max(recent_highs, key=lambda x: x.index)
            swing_high_price = last_swing_high.price
            swing_age = check_idx - last_swing_high.index

            if check_candle['high'] > swing_high_price and check_candle['close'] < swing_high_price:
                sweep_depth_pct = (check_candle['high'] - swing_high_price) / swing_high_price * 100

                if sweep_depth_pct >= sweep_threshold_pct:
                    # 🔧 2026-03-10: Sweep Attempt Log
                    _sweep_logger.debug(
                        f'[SWEEP_DETECTED] sym={symbol} type=equal_high level={swing_high_price:.0f} '
                        f'distance={sweep_depth_pct:.3f}% swing_age={swing_age} tier=penetration'
                    )
                    _sweep_logger.info(
                        f'[SWEEP_RESULT] sym={symbol} type=penetration dist={sweep_depth_pct:.2f}%'
                    )
                    get_smc_logger().log_sweep(symbol, 'penetration', sweep_depth_pct)
                    return LiquiditySweep(
                        index=check_idx,
                        swept_level=swing_high_price,
                        sweep_high=check_candle['high'],
                        sweep_low=check_candle['low'],
                        direction='bearish',  # 고점 스윕 후 하락 예상
                        sweep_type='penetration'
                    )
                else:
                    # 🔧 2026-03-10: Sweep Attempt Log — distance 미달
                    _sweep_logger.debug(
                        f'[SWEEP_MISS] sym={symbol} type=equal_high level={swing_high_price:.0f} '
                        f'distance={sweep_depth_pct:.3f}% < threshold={sweep_threshold_pct}% '
                        f'swing_age={swing_age} reason=distance_too_small'
                    )
            elif check_candle['high'] <= swing_high_price:
                near_pct = abs(check_candle['high'] - swing_high_price) / swing_high_price * 100
                _sweep_logger.debug(
                    f'[SWEEP_MISS] sym={symbol} type=equal_high level={swing_high_price:.0f} '
                    f'distance={near_pct:.3f}% swing_age={swing_age} reason=no_penetration'
                )

        # 🔧 2026-03-09: Tier 2 — Equal Level Sweep (한국 시장 1~2틱 터치 후 반전)
        # Tier 1(penetration) 미충족 시에만 체크, equal_level_tolerance_pct > 0 이면 활성
        if equal_level_tolerance_pct > 0:
            # 🔧 2026-03-18: 거리 범위 파라미터 (distance 하한/상한)
            el_distance_min = equal_level_distance_min_pct
            el_distance_max = equal_level_distance_max_pct

            # 볼륨 평균 (20봉 이전, check_idx 기준)
            vol_start = max(0, check_idx - 20)
            has_volume = 'volume' in df.columns
            avg_vol = df['volume'].iloc[vol_start:check_idx].mean() if has_volume and check_idx > vol_start else None

            # 반응 캔들 body 계산
            c_body = check_candle['close'] - check_candle['open']
            c_range = check_candle['high'] - check_candle['low']
            body_ratio = abs(c_body) / c_range if c_range > 0 else 0

            # --- Bullish Equal Level Sweep ---
            if recent_lows:
                last_swing_low = max(recent_lows, key=lambda x: x.index)
                swing_low_price = last_swing_low.price
                # 🔧 2026-03-18: 거리 계산은 스윙 기준 (swing-based distance)
                distance_pct = (check_candle['low'] - swing_low_price) / swing_low_price * 100
                touch_pct = abs(distance_pct)
                _sweep_logger.debug(
                    f'[EQUAL_LEVEL_HIT] sym={symbol} swing_low={swing_low_price:.0f} '
                    f'candle_low={check_candle["low"]:.0f} distance={distance_pct:.3f}% '
                    f'tolerance={equal_level_tolerance_pct}% range=[{el_distance_min},{el_distance_max}]%'
                )
                # 🔧 2026-03-18: 거리 상한/하한 추가 — 5.6% 쓰레기 신호 차단
                if not (el_distance_min <= touch_pct <= el_distance_max):
                    _sweep_logger.debug(
                        f'[FILTERED_OUT] sym={symbol} reason=distance_out_of_range '
                        f'distance={touch_pct:.3f}% range=[{el_distance_min},{el_distance_max}]%'
                    )
                    _sweep_logger.info(
                        f'[SWEEP_RESULT] sym={symbol} type=filtered dist={touch_pct:.2f}%'
                    )
                    get_smc_logger().log_sweep(symbol, 'filtered', touch_pct, 'distance_out_of_range')
                elif (touch_pct <= equal_level_tolerance_pct          # 레벨 근접 터치
                        and check_candle['close'] > swing_low_price  # 종가 레벨 위 복귀
                        and c_body > 0                                # 불리시 캔들
                        and body_ratio >= equal_level_reaction_body   # 강한 반응 body
                        and (avg_vol is None or                       # 볼륨 스파이크
                             check_candle['volume'] >= avg_vol * equal_level_volume_mult)):
                    _sweep_logger.info(
                        f'[SWEEP_RESULT] sym={symbol} type=equal_level dist={touch_pct:.2f}%'
                    )
                    get_smc_logger().log_sweep(symbol, 'equal_level', touch_pct)
                    return LiquiditySweep(
                        index=check_idx,
                        swept_level=swing_low_price,
                        sweep_high=check_candle['high'],
                        sweep_low=check_candle['low'],
                        direction='bullish',
                        sweep_type='equal_level'
                    )

            # --- Bearish Equal Level Sweep (참고용, long_only 모드에서는 무시됨) ---
            if recent_highs:
                last_swing_high = max(recent_highs, key=lambda x: x.index)
                swing_high_price = last_swing_high.price
                # 🔧 2026-03-18: 거리 계산은 스윙 기준
                distance_pct = (swing_high_price - check_candle['high']) / swing_high_price * 100
                touch_pct = abs(distance_pct)
                # 🔧 2026-03-18: 거리 상한/하한 추가
                if not (el_distance_min <= touch_pct <= el_distance_max):
                    _sweep_logger.debug(
                        f'[FILTERED_OUT] sym={symbol} reason=distance_out_of_range(bearish) '
                        f'distance={touch_pct:.3f}% range=[{el_distance_min},{el_distance_max}]%'
                    )
                    _sweep_logger.info(
                        f'[SWEEP_RESULT] sym={symbol} type=filtered dist={touch_pct:.2f}%'
                    )
                    get_smc_logger().log_sweep(symbol, 'filtered', touch_pct, 'distance_out_of_range')
                elif (touch_pct <= equal_level_tolerance_pct
                        and check_candle['close'] < swing_high_price
                        and c_body < 0                                # 베어리시 캔들
                        and body_ratio >= equal_level_reaction_body
                        and (avg_vol is None or
                             check_candle['volume'] >= avg_vol * equal_level_volume_mult)):
                    _sweep_logger.info(
                        f'[SWEEP_RESULT] sym={symbol} type=equal_level dist={touch_pct:.2f}%'
                    )
                    get_smc_logger().log_sweep(symbol, 'equal_level', touch_pct)
                    return LiquiditySweep(
                        index=check_idx,
                        swept_level=swing_high_price,
                        sweep_high=check_candle['high'],
                        sweep_low=check_candle['low'],
                        direction='bearish',
                        sweep_type='equal_level'
                    )

    return None


def get_recent_swing_levels(
    swing_points: List[SwingPoint],
    current_idx: int,
    lookback: int = 20
) -> dict:
    """
    최근 스윙 레벨 조회

    Args:
        swing_points: 스윙 포인트 리스트
        current_idx: 현재 인덱스
        lookback: 탐색 범위

    Returns:
        {'recent_highs': [prices], 'recent_lows': [prices]}
    """
    recent = [
        sp for sp in swing_points
        if sp.index < current_idx and current_idx - sp.index <= lookback
    ]

    return {
        'recent_highs': [sp.price for sp in recent if sp.type == 'high'],
        'recent_lows': [sp.price for sp in recent if sp.type == 'low']
    }
