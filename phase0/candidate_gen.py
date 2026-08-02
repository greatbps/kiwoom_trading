"""
Phase 0.2 — 파라미터화된 후보 생성기

━━━ 왜 감싸는가 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  `backtest/adapter.py` 의 거래량 배수는 `vol_avg * 1.5` 로 상수이고,
  MA50 필터는 기울기 양수만 본다. 완화 실험을 하려면 이 두 값을 바꿔야
  하는데, **운영 코드는 최적 Case 가 정해지기 전까지 손대지 않는다.**
  (작업지시서: "선정된 경우에만 운영 코드 반영")

  그래서 SMCAdapter 를 상속하지 않고 감싼다.

      CHoCH 핵심 판정 + ATR 필터  → 원본 SMCAdapter 그대로 (파라미터 지원)
      거래량 · MA50               → 여기서 파라미터화해 다시 검사

━━━ 재구현은 반드시 패리티 검사를 통과해야 한다 ━━━━━━━━━━━━━━━━

  거래량/MA50 을 여기서 다시 쓴 이상, 원본과 한 글자라도 다르면 Case A
  부터 어긋나고 A~E 비교 전체가 무의미해진다. `verify_parity()` 가
  운영 설정으로 돌렸을 때 캐시된 신호 집합과 **완전히 일치**하는지 본다.
  불일치면 예외를 던지고 멈춘다 — 조용히 진행하면 안 되는 종류의 실패다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from backtest.adapter import SMCAdapter
from backtest.daily_scan import BEST_CONFIG

# 원본 adapter 의 상수 — 운영 기준값
OPS_RVOL = 1.5
OPS_ATR_MIN = 0.02
OPS_ATR_MAX = 0.08
OPS_MA50_SLOPE_BARS = 10
VOL_LOOKBACK = 21          # 원본과 동일: iloc[-21:-1] 평균
MA50_MIN_BARS = 55


class ParamCandidateGen:
    """
    Args:
        rvol_min:        거래량 / 20봉평균 하한 (운영 1.5)
        atr_min/max:     ATR% 대역 (운영 0.02 ~ 0.08)
        ma50_slope_min:  MA50 기울기 하한.
                         None  = 운영과 동일한 '엄격히 상승' (now > prev)
                         -0.02 = 10봉간 -2% 까지 허용
        require_above_ma50: 종가 > MA50 조건 유지 여부.
                         ⚠️ Case B 의 '완화' 는 기울기만 푸는 것으로 읽는다.
                            종가가 MA50 위라는 조건까지 풀면 '상승 전환 초기'
                            가 아니라 하락 추세 한복판을 사게 된다.
    """

    def __init__(self, rvol_min=OPS_RVOL, atr_min=OPS_ATR_MIN,
                 atr_max=OPS_ATR_MAX, ma50_slope_min=None,
                 require_above_ma50=True,
                 ma50_slope_bars=OPS_MA50_SLOPE_BARS):
        self.rvol_min = rvol_min
        self.ma50_slope_min = ma50_slope_min
        self.require_above_ma50 = require_above_ma50
        self.ma50_slope_bars = ma50_slope_bars

        # CHoCH + ATR 는 원본에 맡긴다. 거래량/MA50 은 끄고 여기서 검사.
        self._ad = SMCAdapter(
            BEST_CONFIG,
            require_sweep=False,
            require_volume=False,
            require_ma50_trend=False,
            atr_pct_min=atr_min,
            atr_pct_max=atr_max,
        )
        self.window = self._ad.window

    # ── 원본과 같은 창을 본다 ────────────────────────────────────────────
    def _window(self, df: pd.DataFrame, i: int) -> pd.DataFrame:
        return df.iloc[max(0, i - self.window): i]

    def _pass_volume(self, w: pd.DataFrame) -> bool:
        vols = w['volume']
        if len(vols) < VOL_LOOKBACK:
            return False
        vol_avg = vols.iloc[-VOL_LOOKBACK:-1].mean()
        if vol_avg > 0 and float(w.iloc[-1]['volume']) < vol_avg * self.rvol_min:
            return False
        return True

    def _pass_ma50(self, df: pd.DataFrame, i: int, w: pd.DataFrame) -> bool:
        full = df['close'].iloc[:i]
        if len(full) < MA50_MIN_BARS:
            return False
        ma50 = full.rolling(50).mean()
        now = float(ma50.iloc[-1])
        prev = float(ma50.iloc[-self.ma50_slope_bars - 1])
        if np.isnan(now) or np.isnan(prev):
            return False

        if self.require_above_ma50 and not (float(w.iloc[-1]['close']) > now):
            return False

        if self.ma50_slope_min is None:
            return now > prev                      # 운영과 동일 (엄격)
        if prev <= 0:
            return False
        return (now - prev) / prev >= self.ma50_slope_min

    def get_signal(self, df: pd.DataFrame, i: int) -> str | None:
        if self._ad.get_signal(df, i) != 'BUY':
            return None
        w = self._window(df, i)
        if len(w) < 20:
            return None
        if not self._pass_volume(w):
            return None
        if not self._pass_ma50(df, i, w):
            return None
        return 'BUY'

    def scan(self, data: dict[str, pd.DataFrame]) -> dict[str, set]:
        out = {}
        for sym, df in data.items():
            out[sym] = {df.index[i] for i in range(len(df))
                        if self.get_signal(df, i) == 'BUY'}
        return out


# ── 실험군 정의 (작업지시서 Case A~E) ─────────────────────────────────────
CASES = {
    'A. Baseline (현행)': dict(),
    'B. MA50 완화 (>=-2%)': dict(ma50_slope_min=-0.02),
    'C. Volume 완화 (RVOL>=1.2)': dict(rvol_min=1.2),
    'D. ATR 확대 (1.5~10%)': dict(atr_min=0.015, atr_max=0.10),
    'E. 조합 (B+C+D)': dict(rvol_min=1.2, atr_min=0.015, atr_max=0.10,
                            ma50_slope_min=-0.02),
}


# ── 보조 실험군 ───────────────────────────────────────────────────────────
#
# ⚠️ 작업지시서에 없는 조합이다. §후보 품질 분석에서 C(Volume 완화)의 신규
#    후보 PF 가 1.108 로 거의 이익을 내지 못한다는 결과가 나와, C 를 빼거나
#    약하게 준 조합을 확인하려고 추가했다.
#
#    G 의 RVOL 1.35 는 **같은 데이터를 보고 정한 값**이다. 과최적화 위험이
#    있으므로 채택 전 out-of-sample 확인이 필요하다. `split_check.py` 의
#    전후반 분할은 그 1차 방어선일 뿐 out-of-sample 이 아니다.
SUPPLEMENTARY = {
    'F. B+D (C 제외)': dict(ma50_slope_min=-0.02, atr_min=0.015, atr_max=0.10),
    'G. B+D + RVOL 1.35': dict(ma50_slope_min=-0.02, atr_min=0.015,
                               atr_max=0.10, rvol_min=1.35),
}


def verify_parity(data: dict[str, pd.DataFrame],
                  cached: dict[str, set]) -> tuple[bool, str]:
    """
    Case A 가 운영 신호와 정확히 같은가.

    같지 않으면 거래량/MA50 재구현이 원본에서 벗어났다는 뜻이고,
    그 상태의 A~E 비교는 읽을 가치가 없다.
    """
    mine = ParamCandidateGen().scan(data)
    syms = set(mine) | set(cached)
    extra = missing = 0
    bad = []
    for s in syms:
        a, b = mine.get(s, set()), cached.get(s, set())
        if a != b:
            extra += len(a - b)
            missing += len(b - a)
            bad.append(s)
    ok = not bad
    msg = ('일치' if ok else
           f'불일치 {len(bad)}종목 — 재구현이 원본과 다르다 '
           f'(추가 {extra} / 누락 {missing}): {bad[:5]}')
    return ok, msg
