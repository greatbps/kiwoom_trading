"""
동적 포지션 사이징 (Position Sizer)

Kelly Criterion + 레짐 보정 + 변동성 보정

산식:
    kelly = win_rate - (1 - win_rate) / RR
    fraction = clip(kelly, 0.2, 1.0)
    size = base_capital * fraction * regime_mult * vol_mult

레짐 보정:
    BULL → × 1.0
    SIDE → × 0.7
    BEAR → × 0.5

변동성 보정:
    ATR_ratio > HIGH_VOL_THRESHOLD → × 0.7
    ATR_ratio < LOW_VOL_THRESHOLD  → × 0.9  (횡보 — 수익 제한)
    그 외                          → × 1.0

사용:
    from core.position_sizer import PositionSizer

    sizer = PositionSizer(base_capital=10_000_000)
    size = sizer.calc_size(
        win_rate=0.55,
        avg_win_pct=1.2,
        avg_loss_pct=0.8,
        regime="BULL",
        atr=150.0,
        price=50_000,
    )
    # → 포지션 금액 (원)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np


# ── 파라미터 ──────────────────────────────────────────────────────────

KELLY_MIN   = 0.20   # Kelly 하한 (최소 20% 베팅)
KELLY_MAX   = 1.00   # Kelly 상한 (풀베팅 방지 → 실전 50% 사용 시 0.5로 낮춤)
KELLY_SCALE = 0.50   # Half-Kelly: 실전 변동성 완충

REGIME_MULT = {
    "BULL": 1.0,
    "SIDE": 0.7,
    "BEAR": 0.5,
}

HIGH_VOL_THRESHOLD = 0.03   # ATR/price > 3% → 고변동
LOW_VOL_THRESHOLD  = 0.005  # ATR/price < 0.5% → 저변동(횡보)
HIGH_VOL_MULT      = 0.70
LOW_VOL_MULT       = 0.90


# ── 사이징 결과 ───────────────────────────────────────────────────────

@dataclass
class SizeResult:
    amount:       float   # 포지션 금액 (원)
    fraction:     float   # Kelly fraction (보정 후)
    kelly_raw:    float   # 원시 Kelly 값
    regime_mult:  float
    vol_mult:     float
    reason:       str


# ── 핵심 계산 ────────────────────────────────────────────────────────

def calc_kelly(win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> float:
    """Kelly Criterion

    Args:
        win_rate:    승률 0~1
        avg_win_pct: 평균 수익률 % (양수)
        avg_loss_pct: 평균 손실률 % (양수)

    Returns:
        kelly fraction (클리핑 전 raw 값)
    """
    if avg_loss_pct <= 0:
        return KELLY_MIN

    rr = avg_win_pct / avg_loss_pct          # Reward-to-Risk ratio
    kelly = win_rate - (1.0 - win_rate) / rr
    return float(kelly)


def calc_vol_mult(atr: float, price: float) -> Tuple[float, str]:
    """ATR 기반 변동성 보정 계수"""
    if price <= 0:
        return 1.0, "price=0"

    atr_ratio = atr / price
    if atr_ratio > HIGH_VOL_THRESHOLD:
        return HIGH_VOL_MULT, f"고변동(ATR/P={atr_ratio*100:.1f}%)"
    if atr_ratio < LOW_VOL_THRESHOLD:
        return LOW_VOL_MULT, f"저변동(ATR/P={atr_ratio*100:.1f}%)"
    return 1.0, f"정상변동(ATR/P={atr_ratio*100:.1f}%)"


# ── PositionSizer 클래스 ──────────────────────────────────────────────

class PositionSizer:
    """Kelly + 레짐 + 변동성 기반 동적 포지션 사이저"""

    def __init__(
        self,
        base_capital:   float = 10_000_000,   # 기본 운용 자본 (원)
        kelly_scale:    float = KELLY_SCALE,   # Half-Kelly 비율
        kelly_min:      float = KELLY_MIN,
        kelly_max:      float = KELLY_MAX,
        max_position_pct: float = 0.20,        # 단일 포지션 최대 20%
    ):
        self.base_capital     = base_capital
        self.kelly_scale      = kelly_scale
        self.kelly_min        = kelly_min
        self.kelly_max        = kelly_max
        self.max_position_pct = max_position_pct

    def calc_size(
        self,
        win_rate:     float,
        avg_win_pct:  float,
        avg_loss_pct: float,
        regime:       str   = "SIDE",
        atr:          float = 0.0,
        price:        float = 0.0,
        override_mult: Optional[float] = None,   # 외부 강제 배율 (DEFENSIVE 0.3 등)
    ) -> SizeResult:
        """포지션 금액 계산

        Args:
            win_rate:      최근 N거래 승률
            avg_win_pct:   평균 수익% (양수)
            avg_loss_pct:  평균 손실% (양수)
            regime:        "BULL" | "BEAR" | "SIDE"
            atr:           ATR (가격 단위)
            price:         현재가 (변동성 비율 계산용)
            override_mult: 전략별 강제 배율 (None → Kelly 계산)

        Returns:
            SizeResult
        """
        # ① Kelly 계산
        kelly_raw = calc_kelly(win_rate, avg_win_pct, avg_loss_pct)

        # ② Kelly 클리핑 + Half-Kelly 스케일
        kelly_clipped = float(np.clip(kelly_raw, self.kelly_min, self.kelly_max))
        kelly_scaled  = kelly_clipped * self.kelly_scale   # 기본 Half-Kelly

        # ③ 레짐 보정
        r_mult = REGIME_MULT.get(regime, 0.7)

        # ④ 변동성 보정
        v_mult, vol_reason = calc_vol_mult(atr, price)

        # ⑤ 최종 fraction
        if override_mult is not None:
            # 전략 강제 배율 (DEFENSIVE: 0.3, SHORT: 0.5)
            fraction = override_mult * r_mult * v_mult
            reason   = (
                f"override={override_mult:.2f} × regime({regime})×{r_mult:.1f} "
                f"× vol×{v_mult:.1f} ({vol_reason})"
            )
        else:
            fraction = kelly_scaled * r_mult * v_mult
            reason   = (
                f"kelly_raw={kelly_raw:.3f} → scaled={kelly_scaled:.3f} "
                f"× regime({regime})×{r_mult:.1f} × vol×{v_mult:.1f} ({vol_reason})"
            )

        # ⑥ 최대 포지션 제한
        max_frac = self.max_position_pct
        if fraction > max_frac:
            reason  += f" → cap@{max_frac*100:.0f}%"
            fraction = max_frac

        fraction = max(fraction, 0.01)   # 최소 1%

        amount = self.base_capital * fraction

        return SizeResult(
            amount      = round(amount),
            fraction    = round(fraction, 4),
            kelly_raw   = round(kelly_raw, 4),
            regime_mult = r_mult,
            vol_mult    = v_mult,
            reason      = reason,
        )

    def update_capital(self, new_capital: float):
        """자본 업데이트 (일 시작 시 현재 잔고로 갱신)"""
        self.base_capital = new_capital

    def get_size_for_strategy(
        self,
        strategy:  str,
        regime:    str,
        atr:       float,
        price:     float,
        win_rate:  float = 0.50,
        avg_win:   float = 1.0,
        avg_loss:  float = 0.8,
        cfg:       Optional[dict] = None,
    ) -> SizeResult:
        """전략별 사이즈 계산 헬퍼

        Args:
            strategy: "defensive" | "short" | "smc" | "default"
            cfg:      strategy_hybrid.yaml 해당 섹션 (position_size_mult 읽기용)
        """
        override = None
        if cfg is not None:
            override = cfg.get("position_size_mult")

        # cfg 없으면 전략 기본값
        if override is None:
            override = {"defensive": 0.30, "short": 0.50}.get(strategy)

        return self.calc_size(
            win_rate      = win_rate,
            avg_win_pct   = avg_win,
            avg_loss_pct  = avg_loss,
            regime        = regime,
            atr           = atr,
            price         = price,
            override_mult = override,
        )


# ── 통계 추적기 (최근 N거래 기반 Kelly 입력 자동 계산) ────────────────

class TradeStats:
    """최근 N거래 승률 / 평균손익 추적 → Kelly 입력 자동 산출"""

    def __init__(self, window: int = 20):
        self.window = window
        self._pnls: list[float] = []   # % 손익 (양수=수익, 음수=손실)

    def record(self, pnl_pct: float):
        """거래 결과 기록"""
        self._pnls.append(pnl_pct)
        if len(self._pnls) > self.window:
            self._pnls.pop(0)

    def get_kelly_inputs(self) -> Tuple[float, float, float]:
        """(win_rate, avg_win_pct, avg_loss_pct)

        데이터 부족 시 보수적 기본값 반환
        """
        if len(self._pnls) < 5:
            return 0.50, 1.0, 0.8   # 기본값

        wins   = [p for p in self._pnls if p > 0]
        losses = [p for p in self._pnls if p <= 0]

        win_rate    = len(wins) / len(self._pnls)
        avg_win     = float(np.mean(wins))   if wins   else 1.0
        avg_loss    = float(abs(np.mean(losses))) if losses else 0.8

        # 최소값 보호
        avg_win  = max(avg_win, 0.1)
        avg_loss = max(avg_loss, 0.1)

        return win_rate, avg_win, avg_loss

    @property
    def n_trades(self) -> int:
        return len(self._pnls)

    def reset(self):
        self._pnls.clear()


# ── CLI / 빠른 확인 ───────────────────────────────────────────────────

if __name__ == "__main__":
    print("\n  ── PositionSizer 테스트 ──")

    sizer = PositionSizer(base_capital=10_000_000)

    scenarios = [
        ("BULL 강세",  0.60, 1.2, 0.6, "BULL", 300, 50_000),
        ("SIDE 횡보",  0.50, 1.0, 0.8, "SIDE", 200, 50_000),
        ("BEAR 약세",  0.45, 1.0, 1.0, "BEAR", 400, 50_000),
        ("고변동 BULL", 0.55, 1.5, 0.8, "BULL", 2000, 50_000),
        ("DEFENSIVE",  0.55, 1.0, 0.8, "SIDE",  200, 50_000),
    ]

    for name, wr, aw, al, reg, atr, price in scenarios:
        override = 0.3 if name == "DEFENSIVE" else None
        r = sizer.calc_size(wr, aw, al, reg, atr, price, override_mult=override)
        print(
            f"  [{name:10s}] "
            f"kelly_raw={r.kelly_raw:+.3f}  fraction={r.fraction:.3f}  "
            f"amount={r.amount:,.0f}원\n"
            f"             {r.reason}"
        )

    # TradeStats 테스트
    print("\n  ── TradeStats 테스트 (20거래) ──")
    stats = TradeStats(window=20)
    import random
    random.seed(42)
    for _ in range(20):
        pnl = random.choice([1.2, 1.0, -0.8, -0.6, 0.5, -0.7])
        stats.record(pnl)
    wr, aw, al = stats.get_kelly_inputs()
    print(f"  WR={wr*100:.0f}%  avg_win={aw:.2f}%  avg_loss={al:.2f}%")
    r = sizer.calc_size(wr, aw, al, "SIDE")
    print(f"  → fraction={r.fraction:.3f}  amount={r.amount:,.0f}원")
    print()
