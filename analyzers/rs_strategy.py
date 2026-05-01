"""
analyzers/rs_strategy.py — Relative Strength (RS) 전략 (v1.1)

전략 컨셉:
  "상승장 or 횡보장에서 코스닥150 대비 강세 종목 조기 편승"

RS Score 계산:
  rs_5   = stock_5d_return / kosdaq150_5d_return
  rs_20  = stock_20d_return / kosdaq150_20d_return
  rs_base  = 0.6 * rs_5 + 0.4 * rs_20

  # v1.1: 장중 흐름 보정
  rs_intraday = (stock_cur/stock_prev_close) / (kq150_cur/kq150_prev_close)
  rs_final    = 0.7 * rs_base + 0.3 * rs_intraday

  # v1.1: 하락장 보정 — 코스닥150이 음수 수익이면 RS 과대평가 방지
  if kosdaq150_5d_return < 0:
      rs_final *= bear_dampen  (기본 0.7)

진입 조건 (AND):
  1. rs_final > threshold (기본 1.3)
  2. 거래량 스파이크 > 1.5x
  3. 현재가 >= 52주 고점 * 0.95 (고점 근접)
  4. 현재가 > EMA20 (추세 위)
  5. 현재가 > VWAP (장중 고점 후 하락 필터) ← v1.1 추가
  6. RSI < rsi_max (과열 방지, 기본 75)

청산 조건 (OR):
  1. rs_final < deteriorate_threshold — 상대강도 소멸
  2. 현재가 < EMA20 — 추세 이탈
  3. Trailing Stop (가변) — profit<2%: -2% / profit>=2%: -3.5% ← v1.1 개선

v1.1 변경사항 (2026-04-03):
  - 하락장 RS 보정 (bear_dampen)
  - 장중 rs_intraday 반영 (KOSDAQ150 실시간 캐시, 5분 TTL)
  - VWAP 위 조건 추가 (장초반 급등 후 하락 필터)
  - 가변 trailing stop (수익 2% 기준 단계 전환)
"""
import logging
from datetime import datetime, date, timedelta
from typing import Optional, Tuple, Dict

import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

KOSDAQ150_CODE = "229200"   # KODEX 코스닥150
_INTRADAY_CACHE_SEC = 300   # KOSDAQ150 현재가 캐시 TTL (5분)


class RSStrategy:
    """코스닥150 대비 상대강도 전략 (v1.1)."""

    def __init__(self, api, config):
        self.api    = api
        self.config = config

        # 당일 캐시 (일봉)
        self._cache_date: Optional[date]            = None
        self._benchmark_df: Optional[pd.DataFrame]  = None
        self._stock_daily: Dict[str, pd.DataFrame]  = {}

        # 장중 KOSDAQ150 현재가 캐시 (5분 TTL)
        self._kq150_cur_price: Optional[float]      = None
        self._kq150_cur_ts: Optional[datetime]      = None

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def reset_daily(self):
        """daily_routine 시작 시 캐시 초기화."""
        self._cache_date     = None
        self._benchmark_df   = None
        self._stock_daily    = {}
        self._kq150_cur_price = None
        self._kq150_cur_ts    = None
        logger.info("[RS] 일별 캐시 리셋 완료")

    def check_entry(
        self,
        stock_code: str,
        stock_name: str,
        df_5min: pd.DataFrame,
        realtime_price: float,
    ) -> Tuple[bool, str, Dict]:
        """RS 진입 조건 체크. Returns (signal, reason, details)."""
        try:
            cfg = self._get_cfg()
            if not cfg.get("enabled", False):
                return False, "RS 비활성화", {}

            # ── 1. 일봉 데이터 확보 ──────────────────────────────────
            df_d = self._get_daily_df(stock_code)
            if df_d is None or len(df_d) < 22:
                return False, f"일봉 부족 ({len(df_d) if df_d is not None else 0}봉)", {}

            benchmark = self._get_benchmark_df()
            if benchmark is None or len(benchmark) < 22:
                return False, "KOSDAQ150 데이터 부족", {}

            # ── 2. RS Score (일봉 기반) ───────────────────────────────
            rs_base, rs_5, rs_20, bench_5d_ret = self._calc_rs_base(df_d, benchmark)
            if rs_base is None:
                return False, "RS Score 계산 실패", {}

            # ── 3. 장중 RS 보정 (intraday) ───────────────────────────
            rs_intraday = self._calc_rs_intraday(realtime_price, df_d)
            if rs_intraday is not None:
                rs_score = 0.7 * rs_base + 0.3 * rs_intraday
            else:
                rs_score = rs_base

            # ── 4. 하락장 보정 ────────────────────────────────────────
            bear_dampen = cfg.get("bear_dampen", 0.7)
            if bench_5d_ret is not None and bench_5d_ret < 0:
                rs_score *= bear_dampen
                logger.debug(f"[RS] {stock_code}: 하락장 보정 x{bear_dampen} → rs={rs_score:.2f}")

            threshold = cfg.get("rs_threshold", 1.3)
            if rs_score < threshold:
                return False, (
                    f"RS={rs_score:.2f} < {threshold} "
                    f"(base={rs_base:.2f} intra={rs_intraday:.2f if rs_intraday else '?'})"
                ), {"rs_score": rs_score, "rs_5": rs_5, "rs_20": rs_20}

            # ── 5. 52주 고점 근접 ─────────────────────────────────────
            high_52w_ratio = cfg.get("high_52w_ratio", 0.95)
            high_52w = df_d["close"].tail(252).max() if len(df_d) >= 52 else df_d["close"].max()
            if high_52w > 0 and realtime_price < high_52w * high_52w_ratio:
                gap_pct = (realtime_price / high_52w - 1) * 100
                return False, f"52주 고점 이격 {gap_pct:.1f}% (기준 {(high_52w_ratio-1)*100:.0f}%)", {
                    "rs_score": rs_score
                }

            # ── 6. EMA20 위 ───────────────────────────────────────────
            ema20 = self._get_ema20(df_5min)
            if ema20 is None:
                return False, "EMA20 계산 불가", {"rs_score": rs_score}
            if realtime_price <= ema20:
                return False, f"가격({realtime_price:.0f}) <= EMA20({ema20:.0f})", {"rs_score": rs_score}

            # ── 7. VWAP 위 (장초반 급등 후 하락 필터) ────────────────
            vwap = self._get_vwap(df_5min)
            if vwap is not None and realtime_price < vwap:
                return False, f"가격({realtime_price:.0f}) < VWAP({vwap:.0f}) (장중 고점 후 하락)", {
                    "rs_score": rs_score
                }

            # ── 8. 거래량 스파이크 ─────────────────────────────────────
            vol_spike = self._get_volume_spike(df_5min)
            min_vol   = cfg.get("min_volume_spike", 1.5)
            if vol_spike < min_vol:
                return False, f"거래량 {vol_spike:.2f}x < {min_vol}x (스파이크 부족)", {
                    "rs_score": rs_score
                }

            # ── 9. RSI 과열 방지 ──────────────────────────────────────
            rsi    = self._get_rsi(df_5min)
            rsi_max = cfg.get("rsi_max", 75)
            if rsi is not None and rsi >= rsi_max:
                return False, f"RSI {rsi:.1f} >= {rsi_max} (과열)", {"rs_score": rs_score}

            # ── 진입 신호 ─────────────────────────────────────────────
            rsi_str = f"{rsi:.1f}" if rsi is not None else "?"
            intra_str = f"{rs_intraday:.2f}" if rs_intraday is not None else "?"
            reason = (
                f"RS_ENTRY: score={rs_score:.2f}(base={rs_base:.2f}/intra={intra_str}) "
                f"Vol={vol_spike:.1f}x RSI={rsi_str}"
            )
            details = {
                "rs_score": rs_score,
                "rs_base": rs_base,
                "rs_5": rs_5,
                "rs_20": rs_20,
                "rs_intraday": rs_intraday,
                "bench_5d_ret": bench_5d_ret,
                "vol_spike": vol_spike,
                "rsi": rsi,
                "ema20": ema20,
                "vwap": vwap,
                "high_52w": high_52w,
            }
            logger.info(f"[RS_SIG] {stock_code} {stock_name}: {reason}")
            return True, reason, details

        except Exception as e:
            logger.warning(f"[RS_ERR] {stock_code} check_entry: {e}")
            return False, f"RS 체크 오류: {e}", {}

    def check_exit(
        self,
        position: dict,
        current_price: float,
        df_5min: pd.DataFrame,
    ) -> Tuple[bool, str, Dict]:
        """RS 청산 조건 체크. Returns (should_exit, reason, details)."""
        try:
            cfg         = self._get_cfg()
            stock_code  = position.get("code", "")
            entry_price = position.get("entry_price", current_price)
            peak_price  = position.get("peak_price", current_price)
            profit_pct  = (current_price - entry_price) / entry_price * 100 if entry_price > 0 else 0.0

            # ── 가변 Trailing Stop ────────────────────────────────────
            # profit < 2%: -2% (초반 빠른 보호)
            # profit >= 2%: -3.5% (수익 확대 구간 여유)
            trail_tight  = cfg.get("trailing_stop_tight_pct", 2.0) / 100
            trail_wide   = cfg.get("trailing_stop_wide_pct", 3.5) / 100
            profit_pivot = cfg.get("trailing_profit_pivot_pct", 2.0)

            # peak_profit = 진입 후 최대 수익 (mfe_pct 또는 peak 기반)
            peak_profit = (peak_price - entry_price) / entry_price * 100 if entry_price > 0 else 0.0
            trail_pct   = trail_wide if peak_profit >= profit_pivot else trail_tight
            trail_stop  = peak_price * (1 - trail_pct)

            if current_price <= trail_stop:
                return True, (
                    f"RS_TRAIL({trail_pct*100:.1f}%): {current_price:.0f}<={trail_stop:.0f} "
                    f"peak={peak_price:.0f} pnl={profit_pct:+.2f}%"
                ), {"exit_type": "TRAIL", "pnl_pct": profit_pct, "trail_pct": trail_pct * 100}

            # ── EMA20 이탈 ────────────────────────────────────────────
            ema20 = self._get_ema20(df_5min)
            if ema20 is not None and current_price < ema20:
                return True, (
                    f"RS_EMA20_BREAK: {current_price:.0f}<EMA20({ema20:.0f}) pnl={profit_pct:+.2f}%"
                ), {"exit_type": "EMA20_BREAK", "pnl_pct": profit_pct}

            # ── RS Score 소멸 (일봉 캐시 재사용) ─────────────────────
            df_d      = self._stock_daily.get(stock_code)
            benchmark = self._benchmark_df
            if df_d is not None and benchmark is not None and len(df_d) >= 6 and len(benchmark) >= 6:
                rs_base, _, _, bench_5d_ret = self._calc_rs_base(df_d, benchmark)
                if rs_base is not None:
                    bear_dampen = cfg.get("bear_dampen", 0.7)
                    rs_exit_score = rs_base * (bear_dampen if (bench_5d_ret or 0) < 0 else 1.0)
                    det_thr = cfg.get("rs_deteriorate_threshold", 1.1)
                    if rs_exit_score < det_thr:
                        return True, (
                            f"RS_WEAK: score={rs_exit_score:.2f}<{det_thr} pnl={profit_pct:+.2f}%"
                        ), {"exit_type": "RS_WEAK", "rs_score": rs_exit_score, "pnl_pct": profit_pct}

            return False, "", {}

        except Exception as e:
            logger.warning(f"[RS_ERR] check_exit: {e}")
            return False, "", {}

    # ─────────────────────────────────────────────────────────────────
    # 내부 헬퍼
    # ─────────────────────────────────────────────────────────────────

    def _get_cfg(self) -> dict:
        return self.config.get("rs_strategy", {})

    def _refresh_cache_if_needed(self):
        today = date.today()
        if self._cache_date != today:
            self._cache_date    = today
            self._benchmark_df  = None
            self._stock_daily   = {}
            self._kq150_cur_price = None
            self._kq150_cur_ts    = None

    def _get_benchmark_df(self) -> Optional[pd.DataFrame]:
        self._refresh_cache_if_needed()
        if self._benchmark_df is None:
            df = self._fetch_daily_df(KOSDAQ150_CODE)
            if df is not None:
                self._benchmark_df = df
        return self._benchmark_df

    def _get_daily_df(self, stock_code: str) -> Optional[pd.DataFrame]:
        self._refresh_cache_if_needed()
        if stock_code not in self._stock_daily:
            df = self._fetch_daily_df(stock_code)
            if df is not None:
                self._stock_daily[stock_code] = df
        return self._stock_daily.get(stock_code)

    def _get_kq150_current_price(self) -> Optional[float]:
        """KOSDAQ150 현재가 (5분 TTL 캐시)."""
        now = datetime.now()
        if (
            self._kq150_cur_price is not None
            and self._kq150_cur_ts is not None
            and (now - self._kq150_cur_ts).total_seconds() < _INTRADAY_CACHE_SEC
        ):
            return self._kq150_cur_price

        try:
            result = self.api.get_stock_price(KOSDAQ150_CODE)
            if result and result.get("return_code") == 0:
                output = result.get("output") or result.get("output1")
                if output:
                    for key in ["stck_prpr", "cur_prc", "price", "current_price"]:
                        if key in output:
                            price = float(output[key])
                            if price > 0:
                                self._kq150_cur_price = price
                                self._kq150_cur_ts    = now
                                return price
        except Exception as e:
            logger.debug(f"[RS] KOSDAQ150 현재가 조회 실패: {e}")
        return None

    def _fetch_daily_df(self, stock_code: str) -> Optional[pd.DataFrame]:
        """Kiwoom API 일봉 30개 → DataFrame."""
        try:
            result = self.api.get_ohlcv_data(stock_code, period="D", count=30)
            if not result or result.get("return_code") != 0:
                return None

            data = None
            for key in ["data", "output", "output1", "output2"]:
                if key in result and result[key]:
                    data = result[key]
                    break
            if not data:
                return None

            df = pd.DataFrame(data)
            if df.empty:
                return None

            col_map = {
                "stck_bsop_date": "date",
                "stck_clpr": "close", "close": "close",
                "stck_oprc": "open",  "open": "open",
                "stck_hgpr": "high",  "high": "high",
                "stck_lwpr": "low",   "low": "low",
                "acml_vol":  "volume","volume": "volume",
            }
            df.rename(columns={k: v for k, v in col_map.items() if k in df.columns}, inplace=True)

            for col in ["open", "high", "low", "close", "volume"]:
                if col in df.columns:
                    df[col] = pd.to_numeric(df[col], errors="coerce").abs()

            df.dropna(subset=["close"], inplace=True)
            df = df[df["close"] > 0].copy()

            if "date" in df.columns:
                df.sort_values("date", inplace=True)
            df.reset_index(drop=True, inplace=True)
            return df

        except Exception as e:
            logger.debug(f"[RS] _fetch_daily_df({stock_code}): {e}")
            return None

    def _calc_rs_base(
        self,
        df_stock: pd.DataFrame,
        df_benchmark: pd.DataFrame,
    ) -> Tuple[Optional[float], Optional[float], Optional[float], Optional[float]]:
        """
        일봉 기반 RS Score 계산.
        Returns (rs_base, rs_5, rs_20, bench_5d_return)
        """
        try:
            s = df_stock["close"].values
            b = df_benchmark["close"].values
            n = min(len(s), len(b), 21)
            if n < 6:
                return None, None, None, None

            s = s[-n:]
            b = b[-n:]

            def ret(arr, period):
                if len(arr) <= period or arr[-period - 1] == 0:
                    return None
                return (arr[-1] - arr[-period - 1]) / arr[-period - 1]

            s5  = ret(s, 5)
            b5  = ret(b, 5)
            s20 = ret(s, min(20, n - 1))
            b20 = ret(b, min(20, n - 1))

            def ratio(sr, br):
                if sr is None or br is None:
                    return None
                if abs(br) < 0.0001:
                    return 1.0 + sr
                return sr / br

            rs_5  = ratio(s5, b5)
            rs_20 = ratio(s20, b20)

            if rs_5 is None and rs_20 is None:
                return None, None, None, b5

            if rs_5 is None:
                score = rs_20
            elif rs_20 is None:
                score = rs_5
            else:
                score = 0.6 * rs_5 + 0.4 * rs_20

            return round(score, 3), round(rs_5 or 0, 3), round(rs_20 or 0, 3), b5

        except Exception:
            return None, None, None, None

    def _calc_rs_intraday(
        self,
        stock_price: float,
        df_stock: pd.DataFrame,
    ) -> Optional[float]:
        """
        장중 상대강도 계산.
        rs_intraday = (stock_cur / stock_prev_close) / (kq150_cur / kq150_prev_close)
        """
        try:
            if df_stock is None or len(df_stock) < 2:
                return None

            stock_prev = float(df_stock["close"].iloc[-1])   # 가장 최근 일봉 종가 = 전일 종가
            if stock_prev <= 0:
                return None

            kq150_cur  = self._get_kq150_current_price()
            if kq150_cur is None:
                return None

            bm = self._benchmark_df
            if bm is None or len(bm) < 2:
                return None
            kq150_prev = float(bm["close"].iloc[-1])
            if kq150_prev <= 0:
                return None

            stock_intra = stock_price / stock_prev
            bench_intra = kq150_cur / kq150_prev

            if abs(bench_intra) < 0.0001:
                return None
            return round(stock_intra / bench_intra, 3)

        except Exception:
            return None

    def _get_ema20(self, df: pd.DataFrame) -> Optional[float]:
        for col in ["ema20", "EMA20", "ma20"]:
            if col in df.columns:
                val = df[col].iloc[-1]
                return float(val) if pd.notna(val) and val > 0 else None
        if "close" in df.columns and len(df) >= 20:
            return float(df["close"].ewm(span=20, adjust=False).mean().iloc[-1])
        return None

    def _get_vwap(self, df: pd.DataFrame) -> Optional[float]:
        for col in ["vwap", "VWAP"]:
            if col in df.columns:
                val = df[col].iloc[-1]
                return float(val) if pd.notna(val) and val > 0 else None
        return None

    def _get_rsi(self, df: pd.DataFrame, period: int = 14) -> Optional[float]:
        for col in ["rsi", "RSI", "rsi_14"]:
            if col in df.columns:
                val = df[col].iloc[-1]
                return float(val) if pd.notna(val) else None
        if "close" in df.columns and len(df) > period:
            delta = df["close"].diff()
            gain  = delta.clip(lower=0).rolling(period).mean()
            loss  = (-delta.clip(upper=0)).rolling(period).mean()
            rs    = gain / loss.replace(0, np.nan)
            val   = (100 - (100 / (1 + rs))).iloc[-1]
            return float(val) if pd.notna(val) else None
        return None

    def _get_volume_spike(self, df: pd.DataFrame, lookback: int = 20) -> float:
        if "volume" not in df.columns or len(df) < lookback + 1:
            return 0.0
        vol_now = float(df["volume"].iloc[-1])
        vol_avg = float(df["volume"].iloc[-lookback - 1: -1].mean())
        return round(vol_now / vol_avg, 2) if vol_avg > 0 else 0.0
