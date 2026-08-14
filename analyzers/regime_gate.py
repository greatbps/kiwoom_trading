"""
Regime Gate Layer — HIGH_VOL 국면 진입 차단 게이트

Iteration 13(phase1/regime_v13.py)에서 Placebo 대조군 대비 인과적 효과가 확인된
HIGH_VOL 게이트를 재사용 가능한 형태로 구현한다. Iteration 14(phase1/gate_v14.py)가
동일 조건 하에 재검증한다.

⚠️ 이 모듈은 순수 판정 함수만 제공한다. Entry Logic·Exit Logic·Ranking·Position
   Sizing 어느 것도 건드리지 않는다 — "그날 신규 진입을 허용할지"만 판단한다.

⚠️ 아직 실거래 경로(main_auto_trading.py / swing_runner.py)에 연결되지 않았다.
   Iteration 14는 검증 단계이며, 실제 파이프라인 연결은 별도 승인 후 진행한다.

정의(Iteration 13 D_ExistingSystem과 동일, 고정):
    등가중 유니버스 일간수익률의 20일 표준편차가 기준 분위수(기본 75분위) 이상이면
    HIGH_VOL. 분위수는 반드시 과거 구간(예: Train)에서만 산출하고 고정해서 쓴다 —
    미래 데이터를 분위수 계산에 섞으면 안 된다(look-ahead 방지).
"""
from __future__ import annotations

import pandas as pd

VOL_WINDOW = 20              # 일간수익률 표준편차 롤링 창
DEFAULT_QUANTILE = 0.75      # Iteration 13에서 고정한 기준값 — 재최적화하지 않는다


def compute_universe_volatility(returns_by_symbol: dict[str, pd.Series],
                                window: int = VOL_WINDOW) -> pd.Series:
    """등가중 유니버스 일간수익률의 롤링 표준편차.

    Args:
        returns_by_symbol: {symbol: 일간수익률 Series(pct_change)}
        window: 표준편차 롤링 창(기본 20일)
    """
    df = pd.DataFrame(returns_by_symbol)
    universe_ret = df.mean(axis=1)
    return universe_ret.rolling(window).std()


def fit_threshold(vol_series: pd.Series, fit_end, quantile: float = DEFAULT_QUANTILE) -> float:
    """기준 분위수를 fit_end 이전 구간에서만 산출해 고정한다(Train 전용, look-ahead 방지).

    Iteration 14 Walk-Forward 지시: "Gate Threshold 재학습 없이 동일 Threshold 유지" —
    이 함수는 한 번만 호출하고, 그 결과값을 이후 Validation/Test 전 구간에 그대로 쓴다.
    """
    ref = vol_series.loc[:fit_end]
    return float(ref.quantile(quantile))


def is_high_vol(vol_series: pd.Series, day, threshold_value: float) -> bool:
    """day가 HIGH_VOL 국면인지 판정. threshold_value는 fit_threshold()로 미리 고정한 값."""
    v = vol_series.get(day)
    if pd.isna(v) or threshold_value is None:
        return False
    return bool(v >= threshold_value)


def gate_decision(vol_series: pd.Series, day, threshold_value: float) -> dict:
    """Entry 게이트 판정 + 로그용 메타데이터 (Paper Trading 신호 로그에 그대로 쓸 수 있는 형태)."""
    v = vol_series.get(day)
    blocked = is_high_vol(vol_series, day, threshold_value)
    return {
        'gate_type': 'HIGH_VOL_BLOCK',
        'blocked': blocked,
        'gate_reason': 'HIGH_VOL_REGIME' if blocked else 'NORMAL_REGIME',
        'vol_value': float(v) if pd.notna(v) else None,
        'threshold_value': threshold_value,
    }
