"""
core/market_context.py — Market Context Layer (v1.0)

"오늘 싸워도 되는 날인가?" — YES / NO 단 하나만 판단.

판단 규칙 (단 하나라도 NO → NO_TRADE_DAY):
  1. 지수 구조  : KODEX200 + 코스닥150 30분봉 HH/HL → OK, 그 외 → NO
  2. OR 위치    : 현재가 < Opening Range(09:00~09:30) 중앙값 → NO
  3. 변동성     : ATR(14봉) < 20봉평균ATR × 0.8 → NO
  4. EMA 레짐   : KOSPI 5분봉 EMA20 > EMA60 (Bear Regime Hard Block)

설계 원칙:
  - 사후 감지(EF 기반) 아닌 사전 판단
  - 점수·가중치 없음 — 조건 위반 즉시 NO_TRADE_DAY
  - 당일 1회 평가 후 캐시 (재호출 비용 0)
  - API 실패 → 해당 조건 통과 (생존 우선, 무결함 불가)

2026-02-26 최초 작성
"""

import logging
from datetime import datetime, timedelta
from typing import Dict, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

KODEX200   = "069500"   # KODEX 200 (코스피 프록시)
KODEX_KOSDAQ = "229200"  # KODEX 코스닥150 (코스닥 프록시)


# ─────────────────────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────────────────────

def _fetch_minute_bars(api, stock_code: str, tic_scope: str) -> Optional[pd.DataFrame]:
    """분봉 조회 → 표준 컬럼(open/high/low/close/volume) DataFrame 반환."""
    try:
        result = api.get_minute_chart(
            stock_code=stock_code,
            tic_scope=tic_scope,
            upd_stkpc_tp="1",
        )
        if result.get("return_code") != 0:
            return None

        data = None
        for key in ["stk_min_pole_chart_qry", "stk_mnut_pole_chart_qry",
                    "output", "output1", "output2", "data"]:
            if key in result and result[key]:
                data = result[key]
                break
        if not data:
            return None

        df = pd.DataFrame(data)
        col_map = {
            "cur_prc": "close", "open_pric": "open",
            "high_pric": "high", "low_pric": "low", "trde_qty": "volume",
            "stck_prpr": "close", "stck_oprc": "open",
            "stck_hgpr": "high", "stck_lwpr": "low",
            "cntg_vol": "volume", "acml_vol": "volume",
        }
        df.rename(columns={k: v for k, v in col_map.items() if k in df.columns},
                  inplace=True)

        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").abs()

        if "cntr_tm" in df.columns:
            df["cntr_tm"] = pd.to_numeric(df["cntr_tm"], errors="coerce")
            df = df.sort_values("cntr_tm", ascending=True).reset_index(drop=True)

        needed = {"open", "high", "low", "close"}
        if not needed.issubset(df.columns):
            return None

        return df.dropna(subset=list(needed)).reset_index(drop=True)

    except Exception as e:
        logger.debug(f"[MKT_CTX] {stock_code} {tic_scope}분봉 조회 오류: {e}")
        return None


def _calc_atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """True Range 기반 ATR 계산."""
    prev_close = df["close"].shift(1)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - prev_close).abs(),
        (df["low"]  - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


# ─────────────────────────────────────────────────────────────────────────────
# 판단 함수 3개
# ─────────────────────────────────────────────────────────────────────────────

def _check_htf_structure(
    df: Optional[pd.DataFrame],
    block_on_bear: bool = True,
    allow_ranging: bool = True,
) -> Tuple[bool, str]:
    """30분봉 HH/HL 패턴 → 상승 구조 확인.

    Args:
        block_on_bear:  LH+LL 명확 하락 시 차단 (기본 True)
        allow_ranging:  횡보/불명확 시 통과 허용 (기본 True — 기존 False에서 완화)
    """
    if df is None or len(df) < 10:
        return True, "데이터 부족(통과)"  # 장 초반 30분봉 부족 → API 실패와 동일 처리

    recent = df.tail(20)
    highs  = recent["high"].values
    lows   = recent["low"].values

    swing_highs, swing_lows = [], []
    for i in range(1, len(highs) - 1):
        if highs[i] > highs[i - 1] and highs[i] > highs[i + 1]:
            swing_highs.append(highs[i])
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            swing_lows.append(lows[i])

    if len(swing_highs) < 2 or len(swing_lows) < 2:
        return True, f"스윙 부족(통과, H={len(swing_highs)},L={len(swing_lows)})"

    hh = swing_highs[-1] > swing_highs[-2]
    hl = swing_lows[-1]  > swing_lows[-2]
    lh = swing_highs[-1] < swing_highs[-2]
    ll = swing_lows[-1]  < swing_lows[-2]

    if lh and ll:
        # 명확한 하락 — block_on_bear 설정에 따라 차단
        if block_on_bear:
            return False, "LH+LL 하락구조"
        return True, "LH+LL 하락구조(차단 해제)"

    if hh or hl:
        # 하나라도 상승 조건 충족 → 기회 구간 (기존: 둘 다 필요)
        label = "HH+HL 상승구조" if (hh and hl) else f"부분상승({'HH' if hh else 'HL'}만 충족)"
        return True, label

    # 완전 횡보 (HH=False, HL=False, LH=False, LL=False → 거의 없음)
    if allow_ranging:
        return True, f"횡보(통과, HH={hh},HL={hl})"
    return False, f"횡보/불명확 (HH={hh},HL={hl},LH={lh},LL={ll})"


def _check_opening_range(df_5min: Optional[pd.DataFrame]) -> Tuple[bool, str]:
    """Opening Range(09:00~09:30) 중앙값 대비 현재가 위치."""
    if df_5min is None or "cntr_tm" not in df_5min.columns or len(df_5min) < 3:
        return True, "OR 데이터 부족(통과)"

    or_mask = (df_5min["cntr_tm"] >= 900) & (df_5min["cntr_tm"] <= 930)
    or_bars = df_5min[or_mask]
    if len(or_bars) < 2:
        return True, "OR 봉 부족(통과)"

    or_high = or_bars["high"].max()
    or_low  = or_bars["low"].min()
    or_mid  = (or_high + or_low) / 2
    current = df_5min["close"].iloc[-1]
    rng     = or_high - or_low
    pct     = ((current - or_low) / rng * 100) if rng > 0 else 50

    is_above = current >= or_mid
    reason = f"OR[{or_low:.0f}~{or_high:.0f}] mid={or_mid:.0f} 현재={current:.0f}({pct:.0f}%)"
    return is_above, reason


def _check_volatility(
    df_30min: Optional[pd.DataFrame],
    compress_ratio: float = 0.8,
) -> Tuple[bool, str]:
    """ATR 수축 판단 — 최근 14봉 ATR < 20봉평균 × compress_ratio → 수축."""
    if df_30min is None or len(df_30min) < 35:
        return True, "ATR 데이터 부족(통과)"

    atr = _calc_atr(df_30min, period=14)
    recent_atr = atr.iloc[-14:].mean()
    mean_atr   = atr.iloc[-34:-14].mean()

    if mean_atr <= 0 or pd.isna(mean_atr) or pd.isna(recent_atr):
        return True, "ATR 산출불가(통과)"

    ratio = recent_atr / mean_atr
    is_ok = ratio >= compress_ratio
    reason = f"ATR비율={ratio:.2f}(현{recent_atr:.1f}/평{mean_atr:.1f},기준>={compress_ratio})"
    return is_ok, reason


def _check_ema_regime(
    df_5min: Optional[pd.DataFrame],
    fast: int = 20,
    slow: int = 60,
    min_gap_pct: float = 0.0,
) -> Tuple[bool, str]:
    """EMA 레짐 필터 — 5분봉 EMA{fast} > EMA{slow} 상승 레짐 확인.

    Bear Regime(EMA20 < EMA60)에서 단타 SMC 진입 시 가짜 CHoCH 비율 급증.
    단 하나라도 NO → NO_TRADE_DAY.

    Args:
        min_gap_pct: 역배열 허용 임계치 (%). gap이 이 값보다 작으면 약역배열로 허용.
                     기본 0.0 (기존 동작 유지). 0.5 → gap -0.44%는 통과.
    """
    if df_5min is None or len(df_5min) < slow + 5:
        return True, f"EMA 데이터 부족(통과, n={0 if df_5min is None else len(df_5min)})"

    ema_fast = df_5min["close"].ewm(span=fast, adjust=False).mean().iloc[-1]
    ema_slow = df_5min["close"].ewm(span=slow, adjust=False).mean().iloc[-1]

    import math
    if math.isnan(ema_fast) or math.isnan(ema_slow) or ema_slow <= 0:
        return True, f"EMA 산출불가(통과, n={len(df_5min)})"
    is_bull = ema_fast > ema_slow
    gap_pct = (ema_fast - ema_slow) / ema_slow * 100
    reason  = f"EMA{fast}={ema_fast:.1f} {'>' if is_bull else '<'} EMA{slow}={ema_slow:.1f} (gap={gap_pct:+.2f}%)"

    # 🔧 2026-03-31: 약한 역배열 허용 — gap이 min_gap_pct 이내이면 통과
    # 예) min_gap_pct=0.5 → gap=-0.44% 는 통과, gap=-0.6% 는 차단
    if not is_bull and abs(gap_pct) < min_gap_pct:
        return True, reason + f" [약역배열허용<{min_gap_pct}%]"

    return is_bull, reason


# ─────────────────────────────────────────────────────────────────────────────
# MarketContextChecker
# ─────────────────────────────────────────────────────────────────────────────

class MarketContextChecker:
    """당일 Market Context 판단 (슬롯 기반 단방향 재평가 지원)."""

    def __init__(self, api, config: dict):
        self.api    = api
        self.config = config
        self._cache_date: Optional[object]    = None
        self._cache_result: Optional[Tuple]  = None
        self._last_eval_time: Optional[str]  = None      # "HH:MM" 형식
        self._last_eval_dt: Optional[datetime] = None    # OK→NO 재체크용
        self._re_eval_used: bool             = False     # NO→OK 슬롯 소진 여부

    def reset(self):
        """daily_routine 시작 시 호출 — 당일 캐시 초기화."""
        self._cache_date     = None
        self._cache_result   = None
        self._last_eval_time = None
        self._last_eval_dt   = None
        self._re_eval_used   = False
        msg = "[MKT_CTX] 캐시 리셋"
        logger.info(msg)
        print(msg, flush=True)

    def _should_re_evaluate(self, now_str: str) -> bool:
        """
        캐시 무효화(재평가) 여부 결정.

        규칙:
          - 현재 상태가 NO_TRADE_DAY일 때만 재평가 가능 (단방향)
          - re_evaluate_slots 중 last_eval_time < slot <= now 인 슬롯 존재 시 허용
          - 하루 최대 1회 소진 후 추가 재평가 금지
        """
        if self._cache_result is None:
            return False
        cached_status = self._cache_result[0]
        cfg = self.config.get("market_context", {})

        # ── Case: TRADE_OK → OK→NO 주기적 재체크 (구조 붕괴 감지) ──────────
        if cached_status == "TRADE_OK":
            recheck_min = cfg.get("ok_recheck_minutes", 30)
            if self._last_eval_dt is not None:
                elapsed = (datetime.now() - self._last_eval_dt).total_seconds() / 60
                if elapsed >= recheck_min:
                    return True   # 30분마다 재체크 → 붕괴 시 즉시 NO 반영
            return False

        # ── Case: NO_TRADE_DAY → 11:00 슬롯에서 1회만 NO→OK 허용 ────────────
        if self._re_eval_used:
            return False   # 하루 재평가 1회 소진

        slots = cfg.get("re_evaluate_slots", ["11:00"])
        last  = self._last_eval_time or "00:00"

        for slot in slots:
            if last < slot <= now_str:
                return True
        return False

    def evaluate(self) -> Tuple[str, str, Dict]:
        """
        Market Context 평가.

        Returns:
            (status, summary_reason, details_dict)
            status = "TRADE_OK" | "NO_TRADE_DAY"
        """
        today   = datetime.today().date()
        now_str = datetime.now().strftime("%H:%M")

        cfg = self.config.get("market_context", {})

        # ── first_eval_after: OR 완성 전 호출 → 캐시 없으면 통과 반환 ──────
        first_eval_after = cfg.get("first_eval_after", "09:30")
        if now_str < first_eval_after:
            if self._cache_result is not None and self._cache_date == today:
                return self._cache_result
            early_result = (
                "TRADE_OK",
                f"OR 미완성({now_str} < {first_eval_after}), 평가 대기(통과)",
                {"final": "TRADE_OK", "evaluated_at": now_str},
            )
            return early_result  # 캐시 저장 안 함 — 09:30 이후 첫 평가에서 갱신

        # ── 캐시 유효 + 재평가 슬롯 미도달 → 캐시 반환 ──────────────────────
        if self._cache_date == today and self._cache_result is not None:
            if not self._should_re_evaluate(now_str):
                return self._cache_result
            # 재평가 트리거 결정
            cached_status = self._cache_result[0]
            if cached_status == "NO_TRADE_DAY":
                # NO→OK 슬롯 도달: 소진 플래그 ON
                self._re_eval_used = True
                re_eval_msg = (
                    f"[MKT_CTX] [RE_EVAL] NO_TRADE_DAY → 재평가 시작 "
                    f"(슬롯 도달, last={self._last_eval_time}, now={now_str})"
                )
            else:
                # TRADE_OK 30분 재체크: OK→NO 감지용
                re_eval_msg = (
                    f"[MKT_CTX] [RECHECK] TRADE_OK 주기 재체크 (now={now_str})"
                )
            logger.info(re_eval_msg)
            print(re_eval_msg, flush=True)

        cfg = self.config.get("market_context", {})
        compress_ratio = cfg.get("volatility_compress_ratio", 0.8)
        enabled        = cfg.get("enabled", True)

        details = {}
        block_reasons = []

        if not enabled:
            result = ("TRADE_OK", "Market Context 비활성화", {"final": "TRADE_OK"})
            self._cache_date, self._cache_result = today, result
            return result

        # ── 1. 지수 구조 (30분봉) ──────────────────────────────────────────
        htf_cfg           = cfg.get("htf_structure", {})
        block_on_bear     = htf_cfg.get("block_on_bear", True)
        allow_ranging     = htf_cfg.get("allow_ranging", True)
        require_both      = htf_cfg.get("require_both_indices", False)

        df200    = _fetch_minute_bars(self.api, KODEX200,    "30")
        df_kq    = _fetch_minute_bars(self.api, KODEX_KOSDAQ,"30")

        htf_ok_200, htf_r_200 = _check_htf_structure(df200, block_on_bear, allow_ranging)
        htf_ok_kq,  htf_r_kq  = _check_htf_structure(df_kq, block_on_bear, allow_ranging)

        details["kodex200"]        = {"ok": htf_ok_200, "reason": htf_r_200}
        details["kodex_kosdaq"]    = {"ok": htf_ok_kq,  "reason": htf_r_kq}

        # require_both_indices: true → AND(기존), false → OR(완화)
        htf_blocked = (not htf_ok_200 and not htf_ok_kq) if not require_both \
                      else (not htf_ok_200 or not htf_ok_kq)
        if htf_blocked:
            fail_parts = []
            if not htf_ok_200: fail_parts.append(f"KOSPI❌ {htf_r_200}")
            if not htf_ok_kq:  fail_parts.append(f"KOSDAQ❌ {htf_r_kq}")
            block_reasons.extend(fail_parts)

        # ── 2. Opening Range 위치 (KODEX200 5분봉) ─────────────────────────
        df200_5m = _fetch_minute_bars(self.api, KODEX200, "5")
        or_ok, or_reason = _check_opening_range(df200_5m)
        details["opening_range"] = {"ok": or_ok, "reason": or_reason}

        if not or_ok:
            block_reasons.append(f"OR하단❌ {or_reason}")

        # ── 3. 변동성 (KODEX200 30분봉 ATR) ──────────────────────────────
        vol_ok, vol_reason = _check_volatility(df200, compress_ratio)
        details["volatility"] = {"ok": vol_ok, "reason": vol_reason}

        if not vol_ok:
            block_reasons.append(f"ATR수축❌ {vol_reason}")

        # ── 4. EMA 레짐 (KOSPI 5분봉) ────────────────────────────────────────
        ema_cfg    = cfg.get("ema_regime", {})
        ema_enabled = ema_cfg.get("enabled", True)
        if ema_enabled:
            fast_p       = ema_cfg.get("fast_period", 20)
            slow_p       = ema_cfg.get("slow_period", 60)
            min_gap_p    = ema_cfg.get("min_bear_gap_pct", 0.0)   # 약역배열 허용 임계치
            ema_ok, ema_reason = _check_ema_regime(df200_5m, fast=fast_p, slow=slow_p, min_gap_pct=min_gap_p)
            details["ema_regime"] = {"ok": ema_ok, "reason": ema_reason}
            if not ema_ok:
                block_reasons.append(f"EMA_BEAR❌ {ema_reason}")

        # ── 최종 판단 ──────────────────────────────────────────────────────
        if block_reasons:
            status  = "NO_TRADE_DAY"
            summary = " | ".join(block_reasons)
        else:
            status  = "TRADE_OK"
            ema_r = details.get("ema_regime", {}).get("reason", "EMA OFF")
            summary = (
                f"KOSPI {htf_r_200} | KOSDAQ {htf_r_kq} | "
                f"{or_reason} | {vol_reason} | {ema_r}"
            )

        details["final"]         = status
        details["block_reasons"] = block_reasons
        details["evaluated_at"]  = now_str

        icon = "✅" if status == "TRADE_OK" else "🚫"
        re_tag = " [RE_EVAL]" if self._re_eval_used and status == "TRADE_OK" else ""
        msg = f"[MKT_CTX]{re_tag} {icon} {status}: {summary}"
        logger.info(msg)
        print(msg, flush=True)  # stdout에도 출력 (로그 파일 기록용)

        result = (status, summary, details)
        self._cache_date, self._cache_result = today, result
        self._last_eval_time = now_str       # 재평가 슬롯 비교용
        self._last_eval_dt   = datetime.now()  # OK 재체크 인터벌 기준
        return result

    def get_regime(self) -> tuple:
        """
        현재 시장 레짐 반환.

        Returns:
            (regime, reason)
            regime = "TREND" | "REVERSAL" | "NEUTRAL"

        TREND    : EMA 갭 큼 + 강한 상승 → Trend Breakout 전략 유효
        REVERSAL : 횡보/하락 → SMC 리버설 전략 유효
        NEUTRAL  : 판단 불명확 → 양쪽 시도 (SMC 우선)
        """
        cfg = self.config.get("market_context", {})
        ema_cfg = cfg.get("ema_regime", {})
        fast_p  = ema_cfg.get("fast_period", 20)
        slow_p  = ema_cfg.get("slow_period", 60)

        trend_cfg     = self.config.get("trend", {})
        regime_cfg    = trend_cfg.get("regime_detection", {})
        gap_threshold = regime_cfg.get("trend_ema_gap_pct", 0.5)

        if self._cache_result is None:
            return "NEUTRAL", "캐시 없음(평가 미완)"

        details = self._cache_result[2]
        ema_detail = details.get("ema_regime", {})
        ema_reason = ema_detail.get("reason", "")

        # EMA 갭 파싱 (reason에 gap=+X.XX% 형식으로 기록됨)
        import re as _re
        gap_m = _re.search(r"gap=([+-]?\d+\.\d+)%", ema_reason)
        gap_pct = float(gap_m.group(1)) if gap_m else 0.0
        is_bull = ema_detail.get("ok", False)

        if is_bull and gap_pct >= gap_threshold:
            return "TREND", f"강한상승트렌드 EMA갭={gap_pct:+.2f}%>={gap_threshold}%"
        elif not is_bull:
            return "REVERSAL", f"하락/횡보 EMA갭={gap_pct:+.2f}%"
        else:
            return "NEUTRAL", f"약한추세 EMA갭={gap_pct:+.2f}%<{gap_threshold}%"
