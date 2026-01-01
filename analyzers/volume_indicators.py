"""
RSVI (Relative Volume Strength Index) 지표 계산 모듈

Phase 1: L6 Filter Enhancement
- vol_z20: Volume Z-score (20-period)
- vroc10: Volume Rate of Change (10-period)
- rsvi_score: Combined metric (0.0-1.0)

작성일: 2025-11-30
"""

from typing import Final
import numpy as np
import pandas as pd

# RSVI 설정
VOL_MA_WINDOW: Final[int] = 20
VROC_LAG: Final[int] = 10
EPS: Final[float] = 1e-9


def attach_rsvi_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Attach RSVI-related volume indicators to an OHLCV DataFrame.

    Adds the following columns:
        - vol_ma20: rolling mean of volume (20 periods)
        - vol_std20: rolling std of volume (20 periods)
        - vol_z20: volume Z-score based on vol_ma20/vol_std20
        - vroc10: Volume Rate of Change over 10 periods

    Args:
        df: DataFrame with at least a 'volume' column (numeric).

    Returns:
        The same DataFrame with new RSVI columns attached.

    Notes:
        - Uses min_periods=1 for rolling stats to avoid all-NaN prefix.
        - Protects against division by zero using EPS.

    Example:
        >>> df = pd.DataFrame({'volume': [100, 120, 150, 180, 200]})
        >>> df = attach_rsvi_indicators(df)
        >>> print(df[['volume', 'vol_z20', 'vroc10']])
    """
    if "volume" not in df.columns:
        raise ValueError("DataFrame must contain a 'volume' column to compute RSVI indicators.")

    # Ensure volume is numeric
    df = df.copy()
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)

    # Rolling mean (20-period)
    df["vol_ma20"] = (
        df["volume"]
        .rolling(window=VOL_MA_WINDOW, min_periods=1)
        .mean()
    )

    # Rolling std with ddof=0 for population std (more stable for small windows)
    df["vol_std20"] = (
        df["volume"]
        .rolling(window=VOL_MA_WINDOW, min_periods=1)
        .std(ddof=0)
        .fillna(0.0)
    )

    # Z-score: (v - mean) / (std + EPS)
    df["vol_z20"] = (df["volume"] - df["vol_ma20"]) / (df["vol_std20"].abs() + EPS)

    # VROC: volume / volume.shift(VROC_LAG) - 1
    prev_vol = df["volume"].shift(VROC_LAG)
    prev_vol = prev_vol.replace(0, np.nan)  # 0은 NaN으로 처리
    raw_vroc = df["volume"] / (prev_vol.abs() + EPS) - 1.0

    # ChatGPT 제안 (개선):
    # 1. inf 처리
    # 2. NaN → -1.0 (volume 0 = 유동성 없음)
    # 3. 극단값 클리핑 (-5 ~ 5, Phase 2 Alpha 안정성)
    df["vroc10"] = (
        raw_vroc
        .replace([np.inf, -np.inf], np.nan)
        .fillna(-1.0)  # 0.0 → -1.0 (유동성 없음으로 처리)
        .clip(lower=-5.0, upper=5.0)  # 극단값 클리핑 강화
    )

    # Z-score도 극단값 처리 강화
    df["vol_z20"] = (
        df["vol_z20"]
        .replace([np.inf, -np.inf], np.nan)
        .fillna(0.0)
        .clip(lower=-5.0, upper=5.0)  # 극단값 클리핑 추가
    )

    return df


def calculate_rsvi_score(vol_z20: float, vroc10: float) -> float:
    """
    Calculate RSVI score from volume Z-score and VROC.

    Scoring rule:
        - Z-score weight: 60%
        - VROC   weight: 40%
        - Output range: 0.0 ~ 1.0

    Args:
        vol_z20: Volume Z-score (20-period).
        vroc10:  Volume Rate of Change (10-period).

    Returns:
        RSVI score between 0.0 and 1.0.

    Example:
        >>> score = calculate_rsvi_score(vol_z20=2.5, vroc10=3.0)
        >>> print(f"RSVI Score: {score:.2f}")  # 1.00 (매우 강함)

        >>> score = calculate_rsvi_score(vol_z20=-1.5, vroc10=-0.3)
        >>> print(f"RSVI Score: {score:.2f}")  # 0.00 (매우 약함)
    """
    # Handle NaN inputs gracefully
    if np.isnan(vol_z20):
        vol_z20 = 0.0
    if np.isnan(vroc10):
        vroc10 = 0.0

    score = 0.0

    # --- Z-score contribution (max 0.6) ---
    if vol_z20 >= 2.0:
        score += 0.6
    elif vol_z20 >= 1.5:
        score += 0.5
    elif vol_z20 >= 1.0:
        score += 0.4
    elif vol_z20 >= 0.0:
        score += 0.2
    else:
        # clearly below average → no contribution
        score += 0.0

    # --- VROC contribution (max 0.4) ---
    if vroc10 >= 3.0:
        score += 0.4
    elif vroc10 >= 2.0:
        score += 0.35
    elif vroc10 >= 1.0:
        score += 0.3
    elif vroc10 >= 0.0:
        score += 0.1
    else:
        score += 0.0

    # Clamp to [0.0, 1.0]
    score = float(max(0.0, min(1.0, score)))
    return score


def alpha_volume_strength(df: pd.DataFrame) -> float:
    """
    Compute volume strength alpha based on RSVI indicators.

    This alpha is designed for the Multi-Alpha engine and returns
    a score in the range [-1.0, +1.0].

    Args:
        df: OHLCV DataFrame. If vol_z20/vroc10 are missing,
            they will be computed using attach_rsvi_indicators().

    Returns:
        Alpha score between -1.0 (very weak volume) and
        +1.0 (very strong, explosive volume).

    Example:
        >>> df = pd.DataFrame({'volume': [100, 120, 150, 200, 300]})
        >>> alpha = alpha_volume_strength(df)
        >>> print(f"Volume Strength Alpha: {alpha:+.2f}")
    """
    if df.empty:
        # No data → neutral-ish small negative to be conservative
        return -0.1

    if "vol_z20" not in df.columns or "vroc10" not in df.columns:
        df = attach_rsvi_indicators(df)

    latest = df.iloc[-1]
    vol_z20 = float(latest.get("vol_z20", 0.0))
    vroc10 = float(latest.get("vroc10", 0.0))

    if np.isnan(vol_z20):
        vol_z20 = 0.0
    if np.isnan(vroc10):
        vroc10 = 0.0

    score = 0.0

    # Z-score side (directional)
    if vol_z20 >= 2.5:
        score += 0.6
    elif vol_z20 >= 1.5:
        score += 0.4
    elif vol_z20 >= 0.5:
        score += 0.2
    elif vol_z20 >= 0.0:
        score += 0.0
    else:
        score -= 0.3  # clearly below average → 부담되는 상황

    # VROC side (acceleration)
    if vroc10 >= 3.0:
        score += 0.4
    elif vroc10 >= 1.5:
        score += 0.3
    elif vroc10 >= 0.5:
        score += 0.1
    elif vroc10 >= 0.0:
        score += 0.0
    else:
        score -= 0.2

    # Clamp to [-1.0, +1.0]
    score = float(max(-1.0, min(1.0, score)))
    return score


if __name__ == "__main__":
    """테스트 코드"""
    import pandas as pd
    from rich.console import Console

    console = Console()

    print("=" * 80)
    print("🧪 RSVI Indicators 테스트")
    print("=" * 80)

    # 테스트 데이터 생성
    test_data = pd.DataFrame({
        'volume': [
            100, 110, 105, 120, 115,  # 평균 수준
            130, 140, 150, 180, 200,  # 점진적 증가
            250, 300, 350, 400, 450,  # 급격한 증가
            420, 400, 380, 350, 320,  # 감소
            300, 280, 260, 240, 220   # 계속 감소
        ]
    })

    # RSVI 지표 추가
    test_data = attach_rsvi_indicators(test_data)

    # 결과 출력
    console.print("\n[bold cyan]📊 RSVI 지표 계산 결과[/bold cyan]")
    console.print(test_data[['volume', 'vol_ma20', 'vol_std20', 'vol_z20', 'vroc10']].tail(10))

    # 최근 봉 분석
    latest = test_data.iloc[-1]
    vol_z20 = latest['vol_z20']
    vroc10 = latest['vroc10']
    rsvi = calculate_rsvi_score(vol_z20, vroc10)
    alpha = alpha_volume_strength(test_data)

    console.print(f"\n[bold yellow]📈 최근 봉 분석[/bold yellow]")
    console.print(f"  - Volume: {latest['volume']:.0f}")
    console.print(f"  - vol_z20: {vol_z20:+.2f}")
    console.print(f"  - vroc10: {vroc10:+.2f}")
    console.print(f"  - RSVI Score: {rsvi:.2f}")
    console.print(f"  - Volume Strength Alpha: {alpha:+.2f}")

    # 시나리오 테스트
    console.print(f"\n[bold magenta]🧪 시나리오 테스트[/bold magenta]")

    scenarios = [
        ("매우 강한 거래량", 2.5, 3.0),
        ("강한 거래량", 1.5, 2.0),
        ("보통 거래량", 0.5, 0.5),
        ("약한 거래량", -0.5, -0.3),
        ("매우 약한 거래량", -1.5, -0.8),
    ]

    for name, z, vroc in scenarios:
        rsvi = calculate_rsvi_score(z, vroc)
        console.print(f"  {name:15s} | vol_z20={z:+.1f}, vroc10={vroc:+.1f} → RSVI={rsvi:.2f}")
