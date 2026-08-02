"""
Phase 1 — 후보 시점 피처 · 진입 필터 · 연속 스코어

한 곳에 모으는 이유: Experiment 2(필터)와 Experiment 3(스코어)이 같은
값을 봐야 한다. 두 군데서 따로 계산하면 나중에 "필터는 통과했는데
스코어는 낮다" 같은 결과가 나왔을 때 그게 현상인지 버그인지 알 수 없다.

━━━ 모든 값은 신호일 t 까지만 본다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  `df.iloc[:i+1]` 로 자른 뒤 계산한다. 전체 df 를 넘기면 `.iloc[-1]` 이
  미래를 가리킨다 — Phase 0 에서 ScoreEngine 에 같은 함정이 있었다.

━━━ VWAP 은 일봉 근사다 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  운영은 장중 VWAP 을 쓰지만 일봉에는 그런 것이 없다. 여기서는
  20일 누적 (전형가격 × 거래량) / 거래량 으로 근사한다. **다른 지표다.**
  이 필터 결과를 운영 VWAP 게이트의 성능으로 읽으면 안 된다.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── 피처 ─────────────────────────────────────────────────────────────────
def compute(df: pd.DataFrame, i: int) -> dict:
    """신호일 i 시점의 피처. 데이터가 모자라면 None 을 담는다."""
    s = df.iloc[:i + 1]
    if len(s) < 60:
        return {}
    c, h, l, v = s['close'], s['high'], s['low'], s['volume']
    px = float(c.iloc[-1])

    ema20 = c.ewm(span=20, adjust=False).mean()
    ema20_slope = (float(ema20.iloc[-1]) - float(ema20.iloc[-6])) / \
        float(ema20.iloc[-6]) if float(ema20.iloc[-6]) else 0.0

    ma50 = c.rolling(50).mean()
    ma50_now, ma50_prev = float(ma50.iloc[-1]), float(ma50.iloc[-11])
    ma50_slope = (ma50_now - ma50_prev) / ma50_prev if ma50_prev else 0.0

    vol_avg = float(v.iloc[-21:-1].mean())
    rvol = float(v.iloc[-1]) / vol_avg if vol_avg else 0.0
    vol_avg_p = float(v.iloc[-22:-2].mean())
    rvol_prev = float(v.iloc[-2]) / vol_avg_p if vol_avg_p else 0.0

    # 20일 VWAP 근사 (일봉 — 장중 VWAP 과 다른 지표다)
    tp = (h + l + c) / 3.0
    w = v.iloc[-20:]
    vwap20 = float((tp.iloc[-20:] * w).sum() / w.sum()) if w.sum() else px

    # ATR%
    hh, ll, cc = h.values[-15:], l.values[-15:], c.values[-15:]
    tr = np.maximum(hh[1:] - ll[1:],
                    np.maximum(np.abs(hh[1:] - cc[:-1]),
                               np.abs(ll[1:] - cc[:-1])))
    atr_pct = float(np.mean(tr)) / px if px else 0.0

    # 캔들 품질 — 몸통 비중 (꼬리 돌파와 실체 돌파를 가른다)
    row = s.iloc[-1]
    rng = float(row['high'] - row['low'])
    body = abs(float(row['close'] - row['open']))
    body_ratio = body / rng if rng > 0 else 0.0

    # 최근 20봉 내 위치 (0=저점 1=고점) — 이미 많이 오른 뒤 추격인지
    r20 = s.iloc[-20:]
    rl, rh = float(r20['low'].min()), float(r20['high'].max())
    pos20 = (px - rl) / (rh - rl) if rh > rl else 0.5

    return {
        'close': px, 'ema20_slope': ema20_slope,
        'above_ema20': px > float(ema20.iloc[-1]),
        'ma50_slope': ma50_slope, 'above_ma50': px > ma50_now,
        'rvol': rvol, 'rvol_prev': rvol_prev,
        'rvol_rising': rvol > rvol_prev,
        'vwap20': vwap20, 'above_vwap': px > vwap20,
        'vwap_dist': (px - vwap20) / vwap20 if vwap20 else 0.0,
        'atr_pct': atr_pct, 'body_ratio': body_ratio, 'pos20': pos20,
    }


# ── 시장 레짐 (Experiment 2-D) ───────────────────────────────────────────
def market_series(data: dict[str, pd.DataFrame]) -> pd.Series:
    """
    유니버스 동일가중 지수.

    ⚠️ 운영의 `core/regime_detector.py` 를 쓰지 않는다. 그 모듈은 장중
       상태를 들고 도는 실시간 구현이라 일봉 배치에 그대로 못 얹고,
       Phase 1 은 운영 코드를 건드리지 않는다. 여기 지수는 **근사**이고,
       운영 레짐 게이트의 성능을 재는 것이 아니다.
    """
    norm = []
    for df in data.values():
        c = df['close']
        if len(c) > 60:
            norm.append(c / c.iloc[0])
    return pd.concat(norm, axis=1).mean(axis=1).dropna()


def regime_ok(mkt: pd.Series, day, strict: bool = False) -> bool:
    """지수가 MA20 위 (strict 면 MA20 우상향까지)."""
    s = mkt.loc[:day]
    if len(s) < 25:
        return False
    ma = s.rolling(20).mean()
    now, prev = float(ma.iloc[-1]), float(ma.iloc[-6])
    if np.isnan(now) or np.isnan(prev):
        return False
    if float(s.iloc[-1]) <= now:
        return False
    return (now > prev) if strict else True


# ── 진입 필터 (Experiment 2) ─────────────────────────────────────────────
FILTERS = {
    'A. EMA20 Slope > 0': lambda f, m: f.get('ema20_slope', -1) > 0,
    'B. Price > VWAP20': lambda f, m: bool(f.get('above_vwap')),
    'C. RVOL 상승 확인': lambda f, m: bool(f.get('rvol_rising')),
    'D. Regime 강화': lambda f, m: bool(m),
}


# ── 연속 스코어 0~100 (Experiment 3) ─────────────────────────────────────
def _clip(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def score100(f: dict, regime_strict: bool) -> dict:
    """
    0~100 연속 스코어.

    Phase 0 의 ScoreEngine 은 정수 3값(2/3/4)만 내서 절반의 날에 동점이
    났다. 여기서는 각 축을 연속값으로 만든다 — 동점이 안 생겨야
    '순위' 라는 말이 성립한다.

        SMC        0~30   몸통 비중 · 20봉 내 위치
        Volume     0~25   RVOL · 증가 여부
        Trend      0~25   MA50 기울기 · EMA20 기울기 · 지수 레짐
        Volatility 0~20   ATR% 가 목표대역 중앙에 가까울수록 높다
    """
    if not f:
        return {'total': 0.0}

    # SMC — 실체 돌파일수록, 아직 덜 올랐을수록
    smc = 30 * (0.6 * _clip(f['body_ratio']) +
                0.4 * _clip(1.0 - f['pos20']))

    # Volume — RVOL 1.0~3.0 을 0~1 로, 증가 중이면 가산
    vol = 25 * (0.75 * _clip((f['rvol'] - 1.0) / 2.0) +
                0.25 * (1.0 if f['rvol_rising'] else 0.0))

    # Trend — 기울기는 ±5% 를 만점 폭으로 본다
    trend = 25 * (0.45 * _clip((f['ma50_slope'] + 0.02) / 0.07) +
                  0.35 * _clip((f['ema20_slope'] + 0.02) / 0.07) +
                  0.20 * (1.0 if regime_strict else 0.0))

    # Volatility — 목표대역 2~8% 의 중앙(5%)에서 최고, 벗어날수록 감점
    v = 20 * _clip(1.0 - abs(f['atr_pct'] - 0.05) / 0.05)

    return {'total': round(smc + vol + trend + v, 3),
            'smc': round(smc, 2), 'volume': round(vol, 2),
            'trend': round(trend, 2), 'volatility': round(v, 2)}
