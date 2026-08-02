"""
analyzers/market/regime_analyzer.py — Market Regime Gate (v1.4)

목적: "오늘 신규 스윙 진입을 허용할 장인가?"를 점수 기반으로 판단한다.
결과: TREND_UP / NEUTRAL / RISK_OFF 중 하나 + 진입 정책(allow/size_mult/allow_rae)

설계 원칙:
- 설명 가능성 우선: 어떤 피처가 어떤 점수를 부여했는지 reasons로 기록
- 규칙 기반 v1: ML/확률 모델 없음, YAML 임계값 기반 score 합산
- 안전 우선: API 실패 시 NEUTRAL fallback (진입 허용, 사이즈 축소)
- 일 1회 or 60분 캐싱: 장중 불필요한 API 반복 호출 방지

Score 계산 방식:
  각 피처 항목 → +1 (강세) or -1 (약세) or 0 (중립)
  총점 >= trend_up_min  → TREND_UP
  총점 <= risk_off_max  → RISK_OFF
  그 외              → NEUTRAL

2026-07-05 최초 작성
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# 출력 구조체
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class RegimeDecision:
    regime: str                          # TREND_UP / NEUTRAL / RISK_OFF
    score: int                           # 총 점수 (-N ~ +N)
    allow_new_entries: bool              # 신규 진입 허용 여부
    allowed_min_grade: Optional[str]     # 허용 최소 등급 (None = 전면 차단)
    size_multiplier: float               # 기존 사이즈에 곱할 배율
    allow_rae: bool                      # RAE 진입 허용 여부
    reasons: List[str] = field(default_factory=list)  # 점수 기여 이유 목록
    evaluated_at: datetime = field(default_factory=datetime.now)
    # 🔧 2026-07-27: Decision Ledger 관측성 강화 — 원시 지표 노출 (스코어링 로직 변경 없음)
    # {'kospi': {'close':.., 'ema20':.., 'ema20_prev':..}, 'kosdaq': {...}}
    index_metrics: Dict[str, Dict] = field(default_factory=dict)

    def to_log_str(self) -> str:
        return (
            f"regime={self.regime} score={self.score:+d} "
            f"allow={self.allow_new_entries} size_mult={self.size_multiplier:.1f} "
            f"allow_rae={self.allow_rae} reasons={self.reasons}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 헬퍼: 일봉 DataFrame 파싱
# ─────────────────────────────────────────────────────────────────────────────

_COL_MAP = {
    "cur_prc": "close", "stck_prpr": "close", "bstp_nmix_prpr": "close",
    "open_pric": "open",  "stck_oprc": "open",
    "high_pric": "high",  "stck_hgpr": "high",
    "low_pric": "low",    "stck_lwpr": "low",
    "trde_qty": "volume", "acml_vol": "volume", "cntg_vol": "volume",
    "stck_bsop_date": "date", "data_dt": "date", "dt": "date",
}


def _parse_daily_df(raw: Any) -> Optional[pd.DataFrame]:
    """kiwoom_api.get_daily_chart() 응답에서 OHLCV DataFrame 추출."""
    if not raw:
        return None
    data = None
    for key in ["stk_dt_pole_chart_qry", "output", "output1", "data"]:
        if key in raw and raw[key]:
            data = raw[key]
            break
    if not data:
        return None
    try:
        df = pd.DataFrame(data)
        df.rename(columns={k: v for k, v in _COL_MAP.items() if k in df.columns}, inplace=True)
        for col in ["open", "high", "low", "close", "volume"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce").abs()
        df = df.dropna(subset=["close"])
        df = df[df["close"] > 0]
        if len(df) == 0:
            return None
        # 최신 날짜가 첫 행인 경우 역순 정렬
        if "date" in df.columns:
            df = df.sort_values("date", ascending=True).reset_index(drop=True)
        return df
    except Exception as exc:
        logger.debug(f"[REGIME] _parse_daily_df 실패: {exc}")
        return None


def _calc_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


# ─────────────────────────────────────────────────────────────────────────────
# RegimeAnalyzer
# ─────────────────────────────────────────────────────────────────────────────

class RegimeAnalyzer:
    """
    시장 레짐 판단 엔진.

    사용 예:
        analyzer = RegimeAnalyzer(api=kiwoom_api, config=config)
        decision = analyzer.evaluate()
        if not decision.allow_new_entries:
            return  # 신규 진입 차단
    """

    def __init__(self, api: Any, config: Any) -> None:
        self.api = api
        self.config = config
        self._cache: Optional[RegimeDecision] = None
        self._cache_lock = threading.Lock()

    # ─── 퍼블릭 메서드 ──────────────────────────────────────────────────────

    def evaluate(self, force: bool = False) -> RegimeDecision:
        """
        레짐 판정. 캐시 유효 시 캐시 반환.

        Args:
            force: True면 캐시 무시하고 재계산
        """
        cfg = self._cfg()
        if not cfg.get("enabled", True):
            return self._neutral_decision("레짐 게이트 disabled")

        with self._cache_lock:
            cache_min = int(cfg.get("cache_minutes", 60))
            if (not force
                    and self._cache is not None
                    and cache_min > 0
                    and (datetime.now() - self._cache.evaluated_at).total_seconds() < cache_min * 60):
                return self._cache

            decision = self._compute()
            self._cache = decision
            return decision

    def invalidate_cache(self) -> None:
        with self._cache_lock:
            self._cache = None

    def evaluate_for_datetime(
        self,
        asof_datetime: datetime,
        use_only_completed_daily_bars: bool = True,
    ) -> RegimeDecision:
        """
        백테스트 전용: asof_datetime 시점의 레짐을 look-ahead 없이 계산한다.

        Args:
            asof_datetime: 진입 시각 (예: 2026-02-24 10:15)
            use_only_completed_daily_bars: True면 진입 당일 일봉 제외
                → 2026-02-24 진입 시 2026-02-23까지의 일봉만 사용

        Note:
            실전 캐시(self._cache)를 사용하지 않고 별도 계산.
            실전 RegimeAnalyzer 인스턴스에 접근하는 yfinance 기반 오프라인 버전은
            tools/backtest/label_market_regime_for_trades.py의
            HistoricalRegimeCalculator를 사용할 것.
            이 메서드는 kiwoom_api가 제공되는 경우 사용한다.
        """
        try:
            import yfinance as yf
            import pandas as pd

            cutoff_date: date = asof_datetime.date()
            if use_only_completed_daily_bars:
                cutoff_date = cutoff_date - timedelta(days=1)

            cfg = self._cfg()
            ema_period = int(cfg.get("ema_period", 20))
            score = 0
            reasons: List[str] = []

            for ticker, label, drop_key in [
                ("^KS11", "KOSPI",  "kospi_day_drop"),
                ("^KQ11", "KOSDAQ", "kosdaq_day_drop"),
            ]:
                hist_start = (asof_datetime - timedelta(days=90)).strftime("%Y-%m-%d")
                hist_end   = (asof_datetime + timedelta(days=1)).strftime("%Y-%m-%d")
                df_raw = yf.download(ticker, start=hist_start, end=hist_end,
                                     progress=False, auto_adjust=True)
                if df_raw is None or len(df_raw) < ema_period + 2:
                    continue
                if isinstance(df_raw.columns, pd.MultiIndex):
                    df_raw.columns = df_raw.columns.droplevel(1)
                df_raw = df_raw[['Close']].copy()
                df_raw.columns = ['close']
                df_raw.index = pd.to_datetime(df_raw.index).date

                # look-ahead 방지: cutoff_date 이하만 사용
                df = df_raw[df_raw.index <= cutoff_date]
                if len(df) < ema_period + 2:
                    continue

                close  = df['close'].astype(float)
                ema    = close.ewm(span=ema_period, adjust=False).mean()
                c_last = float(close.iloc[-1])
                e_last = float(ema.iloc[-1])
                e_prev = float(ema.iloc[-2])

                if c_last > e_last:
                    score += 1; reasons.append(f"{label}_above_EMA{ema_period}")
                else:
                    score -= 1; reasons.append(f"{label}_below_EMA{ema_period}")

                if e_last > e_prev:
                    score += 1; reasons.append(f"{label}_EMA_slope_up")
                else:
                    score -= 1; reasons.append(f"{label}_EMA_slope_down")

                if len(close) >= 2:
                    c_prev = float(close.iloc[-2])
                    if c_prev > 0:
                        day_ret = (c_last - c_prev) / c_prev
                        drop_thr = float(cfg.get("risk_off_rules", {}).get(drop_key, -0.015))
                        if day_ret <= drop_thr:
                            score -= 1
                            reasons.append(f"{label}_day_drop_{day_ret*100:.1f}pct")

            if not reasons:
                return self._neutral_decision("BT_INSUFFICIENT_DATA")

            thresholds = cfg.get("score_thresholds", {})
            if score >= int(thresholds.get("trend_up_min", 3)):
                regime = "TREND_UP"
            elif score <= int(thresholds.get("risk_off_max", -3)):
                regime = "RISK_OFF"
            else:
                regime = "NEUTRAL"

            dec = self._make_decision(regime, score, reasons)
            dec.evaluated_at = asof_datetime
            return dec

        except Exception as exc:
            logger.debug(f"[REGIME] evaluate_for_datetime 실패: {exc}")
            return self._neutral_decision(f"BT_ERROR_{type(exc).__name__}")

    # ─── 내부 계산 ──────────────────────────────────────────────────────────

    def _compute(self) -> RegimeDecision:
        cfg = self._cfg()
        score = 0
        reasons: List[str] = []
        fallback_regime = cfg.get("fallback_on_error", "NEUTRAL")

        kospi_ticker = cfg.get("index_tickers", {}).get("kospi", "069500")
        kosdaq_ticker = cfg.get("index_tickers", {}).get("kosdaq", "229200")
        ema_period = int(cfg.get("ema_period", 20))

        # ── KOSPI 데이터 수집 ────────────────────────────────────────────
        kospi_score, kospi_reasons, kospi_metrics = self._score_index(
            kospi_ticker, "KOSPI", ema_period, cfg
        )
        # ── KOSDAQ 데이터 수집 ───────────────────────────────────────────
        kosdaq_score, kosdaq_reasons, kosdaq_metrics = self._score_index(
            kosdaq_ticker, "KOSDAQ", ema_period, cfg
        )

        score += kospi_score + kosdaq_score
        reasons += kospi_reasons + kosdaq_reasons
        index_metrics = {'kospi': kospi_metrics, 'kosdaq': kosdaq_metrics}

        if not reasons:
            # 데이터 수집 완전 실패
            logger.warning("[REGIME] 데이터 수집 실패 — fallback 적용")
            return self._make_decision(fallback_regime, 0, ["API_FALLBACK"])

        # ── Score → Regime 판정 ──────────────────────────────────────────
        thresholds = cfg.get("score_thresholds", {})
        trend_min = int(thresholds.get("trend_up_min", 3))
        risk_max = int(thresholds.get("risk_off_max", -3))

        if score >= trend_min:
            regime = "TREND_UP"
        elif score <= risk_max:
            regime = "RISK_OFF"
        else:
            regime = "NEUTRAL"

        decision = self._make_decision(regime, score, reasons, index_metrics)
        logger.info(f"[REGIME] {decision.to_log_str()}")
        return decision

    def _score_index(
        self,
        ticker: str,
        label: str,
        ema_period: int,
        cfg: Dict,
    ) -> Tuple[int, List[str], Dict]:
        """단일 지수(KOSPI or KOSDAQ)의 점수, 이유, 원시 지표(close/ema20/ema20_prev) 반환."""
        score = 0
        reasons: List[str] = []
        metrics: Dict = {}

        try:
            raw = self.api.get_daily_chart(stock_code=ticker)
            df = _parse_daily_df(raw)
            if df is None or len(df) < ema_period + 2:
                logger.debug(f"[REGIME] {label} 데이터 부족 — skip")
                return 0, [], {}

            close = df["close"]
            ema = _calc_ema(close, ema_period)
            current_close = float(close.iloc[-1])
            current_ema = float(ema.iloc[-1])
            prev_ema = float(ema.iloc[-2])
            metrics = {'close': current_close, 'ema20': current_ema, 'ema20_prev': prev_ema}

            # 피처 1: 종가 > EMA
            if current_close > current_ema:
                score += 1
                reasons.append(f"{label}_above_EMA{ema_period}")
            else:
                score -= 1
                reasons.append(f"{label}_below_EMA{ema_period}")

            # 피처 2: EMA 기울기
            if current_ema > prev_ema:
                score += 1
                reasons.append(f"{label}_EMA{ema_period}_slope_up")
            else:
                score -= 1
                reasons.append(f"{label}_EMA{ema_period}_slope_down")

            # 피처 3: 일간 수익률 (최근 종가 vs 전일 종가)
            if len(close) >= 2:
                prev_close = float(close.iloc[-2])
                if prev_close > 0:
                    day_ret = (current_close - prev_close) / prev_close
                    risk_rules = cfg.get("risk_off_rules", {})
                    kospi_drop = float(risk_rules.get("kospi_day_drop", -0.015))
                    kosdaq_drop = float(risk_rules.get("kosdaq_day_drop", -0.020))
                    drop_thr = kospi_drop if label == "KOSPI" else kosdaq_drop
                    if day_ret <= drop_thr:
                        score -= 1
                        reasons.append(f"{label}_day_drop_{day_ret*100:.1f}pct")

        except Exception as exc:
            logger.debug(f"[REGIME] {label} 점수 계산 실패: {exc}")

        return score, reasons, metrics

    # ─── 정책 적용 ──────────────────────────────────────────────────────────

    def _make_decision(
        self, regime: str, score: int, reasons: List[str],
        index_metrics: Optional[Dict] = None,
    ) -> RegimeDecision:
        cfg = self._cfg()
        index_metrics = index_metrics or {}

        if regime == "TREND_UP":
            return RegimeDecision(
                regime=regime,
                score=score,
                allow_new_entries=True,
                allowed_min_grade="A",
                size_multiplier=float(cfg.get("trend_up_size_mult", 1.0)),
                allow_rae=bool(cfg.get("allow_rae_in_trend_up", True)),
                reasons=reasons,
                index_metrics=index_metrics,
            )
        elif regime == "RISK_OFF":
            return RegimeDecision(
                regime=regime,
                score=score,
                allow_new_entries=not bool(cfg.get("block_new_entries_on_risk_off", True)),
                allowed_min_grade=None,
                size_multiplier=float(cfg.get("risk_off_size_mult", 0.0)),
                allow_rae=bool(cfg.get("allow_rae_in_risk_off", False)),
                reasons=reasons,
                index_metrics=index_metrics,
            )
        else:  # NEUTRAL
            _neutral_mult = float(cfg.get("neutral_size_mult", 0.7))
            return RegimeDecision(
                regime=regime,
                score=score,
                allow_new_entries=_neutral_mult > 0.0,
                allowed_min_grade="A",
                size_multiplier=_neutral_mult,
                allow_rae=bool(cfg.get("allow_rae_in_neutral", False)),
                reasons=reasons,
                index_metrics=index_metrics,
            )

    def _neutral_decision(self, reason: str) -> RegimeDecision:
        return self._make_decision("NEUTRAL", 0, [reason])

    def _cfg(self) -> Dict:
        if hasattr(self.config, "get"):
            return self.config.get("market_regime", {})
        return {}
