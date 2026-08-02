"""
Trend Expansion Detector (TED) — v1.3

목적: RAE 또는 일반 SMC 진입 시 "진짜 추세 확장" 구간인지 검증하는 선택적 필터.
      실패 시 진입 차단이 아닌 score 감점 (soft gate).

검증 5개 조건:
  1. ATR 재확장: 최근 ATR > 직전 저점 ATR × threshold
  2. RVOL 1.2~2.0 재상승: RVOL이 min~max 범위에서 상승 중
  3. HH/HL 구조: 최근 N개 고점·저점이 순차 상승
  4. VWAP 위 유지: 현재가 ≥ VWAP
  5. EMA20 slope 양수: EMA20이 상향 기울기

출력:
  (passed: bool, score: int, details: dict)
  score = 통과 조건 수 (0~5), min_pass_count 이상이면 passed=True
"""

import logging
from typing import Tuple, Dict

import pandas as pd

logger = logging.getLogger(__name__)


class TrendExpansionDetector:
    """
    SMC/RAE 진입 직전 추세 확장 신호 검증.

    사용법:
        ted = TrendExpansionDetector(config)
        passed, score, details = ted.check(stock_code, df, current_price)
    """

    def __init__(self, config: dict):
        self.config = config.get('trend_expansion_detector', {})

    def check(
        self,
        stock_code: str,
        df: pd.DataFrame,
        current_price: float,
    ) -> Tuple[bool, int, Dict]:
        """
        추세 확장 조건 검증.

        Returns:
            (passed, score, details)
            passed: True if score >= min_pass_count
            score: 0~5 (통과 조건 수)
            details: 각 조건별 결과
        """
        if not self.config.get('enabled', True):
            return True, 5, {'ted_disabled': True}

        if df is None or len(df) < 15:
            return True, 3, {'ted_data_insufficient': True}  # 데이터 부족 시 soft pass

        details: dict = {}
        score = 0

        # ── 조건 1: ATR 재확장 ────────────────────────────────────────────────
        try:
            atr_col = 'atr' if 'atr' in df.columns else None
            if atr_col:
                atr_now   = float(df[atr_col].iloc[-1])
                atr_min5  = float(df[atr_col].tail(10).min())  # 최근 10봉 중 최저 ATR
                atr_thr   = float(self.config.get('atr_expansion_ratio', 1.05))
                cond1     = atr_now >= atr_min5 * atr_thr
                details['atr_expanding'] = cond1
                details['atr_now']       = round(atr_now, 1)
                details['atr_min5']      = round(atr_min5, 1)
                if cond1:
                    score += 1
            else:
                # ATR 컬럼 없으면 True High-Low range로 대체
                hl_now  = float(df['high'].iloc[-1]) - float(df['low'].iloc[-1])
                hl_avg5 = float((df['high'].tail(5) - df['low'].tail(5)).mean())
                cond1   = hl_now >= hl_avg5 * 0.9
                details['atr_expanding'] = cond1
                details['hl_now']        = round(hl_now, 1)
                details['hl_avg5']       = round(hl_avg5, 1)
                if cond1:
                    score += 1
        except Exception as e:
            details['atr_error'] = str(e)
            score += 0  # 조건 미충족으로 처리

        # ── 조건 2: RVOL 1.2~2.0 재상승 ─────────────────────────────────────
        try:
            rvol_col = 'rvol' if 'rvol' in df.columns else None
            rvol_min = float(self.config.get('rvol_min', 1.2))
            rvol_max = float(self.config.get('rvol_max', 3.0))
            if rvol_col:
                rvol_now  = float(df[rvol_col].iloc[-1])
                rvol_prev = float(df[rvol_col].iloc[-2]) if len(df) > 1 else rvol_now
                cond2     = rvol_min <= rvol_now <= rvol_max and rvol_now >= rvol_prev * 0.95
                details['rvol_rising']  = cond2
                details['rvol_now']     = round(rvol_now, 2)
                details['rvol_prev']    = round(rvol_prev, 2)
                if cond2:
                    score += 1
            else:
                # RVOL 없으면 volume 5-MA 대비 현재 거래량
                vol_now  = float(df['volume'].iloc[-1])
                vol_avg5 = float(df['volume'].tail(5).mean())
                raw_rvol = vol_now / vol_avg5 if vol_avg5 > 0 else 1.0
                cond2    = rvol_min <= raw_rvol <= rvol_max
                details['rvol_rising']   = cond2
                details['raw_rvol']      = round(raw_rvol, 2)
                if cond2:
                    score += 1
        except Exception as e:
            details['rvol_error'] = str(e)

        # ── 조건 3: HH/HL 구조 유지 ──────────────────────────────────────────
        try:
            lookback = int(self.config.get('hh_hl_lookback', 6))
            highs    = df['high'].tail(lookback).values
            lows     = df['low'].tail(lookback).values
            # N개 봉 중 절반 이상이 이전 봉보다 높아야 함 (엄격 HH 대신 추세 완화)
            hh_count = sum(1 for i in range(1, len(highs)) if highs[i] > highs[i-1])
            hl_count = sum(1 for i in range(1, len(lows))  if lows[i]  > lows[i-1])
            min_count = (lookback - 1) // 2
            cond3    = hh_count >= min_count and hl_count >= min_count
            details['hh_hl_ok']  = cond3
            details['hh_count']  = hh_count
            details['hl_count']  = hl_count
            details['hh_hl_min'] = min_count
            if cond3:
                score += 1
        except Exception as e:
            details['hh_hl_error'] = str(e)

        # ── 조건 4: VWAP 위 유지 ─────────────────────────────────────────────
        try:
            if 'vwap' in df.columns:
                vwap  = float(df['vwap'].iloc[-1])
                cond4 = current_price >= vwap * (1 - float(self.config.get('vwap_tolerance_pct', 0.2)) / 100)
                details['above_vwap'] = cond4
                details['vwap']       = round(vwap, 0)
                if cond4:
                    score += 1
            else:
                # VWAP 없으면 soft pass
                details['above_vwap'] = True
                score += 1
        except Exception as e:
            details['vwap_error'] = str(e)

        # ── 조건 5: EMA20 slope 양수 ──────────────────────────────────────────
        try:
            ema_col = 'ema20' if 'ema20' in df.columns else ('ema_20' if 'ema_20' in df.columns else None)
            if ema_col:
                ema_now  = float(df[ema_col].iloc[-1])
                ema_prev = float(df[ema_col].iloc[-3]) if len(df) >= 3 else ema_now
                slope_pct = (ema_now - ema_prev) / ema_prev * 100 if ema_prev > 0 else 0
                cond5    = slope_pct >= float(self.config.get('ema20_slope_min_pct', 0.0))
                details['ema20_slope_ok']  = cond5
                details['ema20_slope_pct'] = round(slope_pct, 3)
                if cond5:
                    score += 1
            else:
                # EMA20 없으면 soft pass
                details['ema20_slope_ok'] = True
                score += 1
        except Exception as e:
            details['ema20_error'] = str(e)

        # ── 최종 판정 ─────────────────────────────────────────────────────────
        min_pass = int(self.config.get('min_pass_count', 3))
        passed   = score >= min_pass
        details['ted_score']     = score
        details['ted_min_pass']  = min_pass
        details['ted_passed']    = passed
        # rejection_reason: 실패한 조건 열거 (TEDSignal 스펙 필드)
        if not passed:
            failed_conds = []
            if not details.get('atr_expanding', True):  failed_conds.append('atr_not_expanding')
            if not details.get('rvol_rising', True):    failed_conds.append('rvol_out_of_range')
            if not details.get('hh_hl_ok', True):       failed_conds.append('hh_hl_broken')
            if not details.get('above_vwap', True):     failed_conds.append('below_vwap')
            if not details.get('ema20_slope_ok', True): failed_conds.append('ema20_slope_flat')
            details['rejection_reason'] = ','.join(failed_conds) if failed_conds else 'score_too_low'
        else:
            details['rejection_reason'] = ''

        # strength_score: 0~100 (TEDSignal 스펙 필드)
        details['strength_score'] = round(score / 5 * 100)

        log_fn = logger.info if passed else logger.debug
        log_fn(
            f"[TED] {stock_code}: score={score}/{min_pass} strength={details['strength_score']} "
            f"passed={passed} "
            f"atr={details.get('atr_expanding', '?')} "
            f"rvol={details.get('rvol_rising', '?')} "
            f"hh_hl={details.get('hh_hl_ok', '?')} "
            f"vwap={details.get('above_vwap', '?')} "
            f"slope={details.get('ema20_slope_ok', '?')}"
            + (f" reject={details['rejection_reason']}" if not passed else "")
        )

        return passed, score, details
