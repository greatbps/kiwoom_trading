"""
core/trade_capture.py

진입/청산 시점 지표 자동 캡처 모듈

execute_buy() 직후:
    from core.trade_capture import capture_entry
    capture_entry(stock_code, stock_name, price, df, entry_reason, regime)

execute_sell() 직후:
    from core.trade_capture import capture_exit
    capture_exit(stock_code, stock_name, exit_price, entry_price,
                 pnl_pct, exit_reason, entry_time)
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

CAPTURE_FILE      = Path(__file__).parent.parent / "strategy" / "entry_capture.jsonl"
EXIT_CAPTURE_FILE = Path(__file__).parent.parent / "strategy" / "exit_capture.jsonl"


def _safe_ema(series, span: int) -> float | None:
    try:
        return float(series.ewm(span=span, adjust=False).mean().iloc[-1])
    except Exception:
        return None


def _safe_rvol(df) -> float | None:
    try:
        if "volume" not in df.columns or len(df) < 21:
            return None
        curr_vol = float(df["volume"].iloc[-1])
        avg_vol  = float(df["volume"].iloc[-21:-1].mean())
        return round(curr_vol / avg_vol, 3) if avg_vol > 0 else None
    except Exception:
        return None


def _safe_vwap(df) -> float | None:
    try:
        if "vwap" in df.columns:
            return float(df["vwap"].iloc[-1])
        # vwap 컬럼 없으면 직접 계산 (OHLCV 기준)
        if all(c in df.columns for c in ["high", "low", "close", "volume"]):
            tp  = (df["high"] + df["low"] + df["close"]) / 3
            vol = df["volume"]
            return round(float((tp * vol).sum() / vol.sum()), 1) if vol.sum() > 0 else None
        return None
    except Exception:
        return None


def capture_entry(
    stock_code: str,
    stock_name: str,
    price: float,
    df,                    # 5분봉 DataFrame
    entry_reason: str = "",
    regime: str = "",
    signal_meta: dict | None = None,
) -> None:
    """
    진입 시점 지표를 entry_capture.jsonl 에 기록.
    예외가 발생해도 매매 흐름에 영향 없음.
    signal_meta: choch_grade, htf_bias, sweep, guard_state 등 신호 품질 메타
    """
    try:
        CAPTURE_FILE.parent.mkdir(parents=True, exist_ok=True)

        ema9  = _safe_ema(df["close"], 9)  if "close" in df.columns else None
        ema20 = _safe_ema(df["close"], 20) if "close" in df.columns else None
        rvol  = _safe_rvol(df)
        vwap  = _safe_vwap(df)
        atr   = None
        if "high" in df.columns and "low" in df.columns and len(df) >= 14:
            tr   = (df["high"] - df["low"]).abs().tail(14)
            atr  = round(float(tr.mean()), 1)

        record = {
            "ts":         datetime.now().isoformat(timespec="seconds"),
            "date":       datetime.now().strftime("%Y%m%d"),
            "stock_code": stock_code,
            "stock_name": stock_name,
            "price":      price,
            "ema9":       round(ema9,  1) if ema9  else None,
            "ema20":      round(ema20, 1) if ema20 else None,
            "rvol":       rvol,
            "vwap":       vwap,
            "atr":        atr,
            "regime":     regime,
            "entry_reason": entry_reason[:100],
            "signal_meta": signal_meta or {},
        }

        with CAPTURE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.debug(
            "[CAPTURE] %s: price=%s ema9=%s rvol=%s vwap=%s",
            stock_code, price, ema9, rvol, vwap,
        )

    except Exception as e:
        # 절대 매매 흐름 방해하지 않음
        logger.warning("[CAPTURE] 캡처 실패 (무시): %s", e)


def _parse_exit_category(reason: str) -> str:
    r = reason.lower()
    if "hard stop" in r:      return "hard_stop"
    if "early failure" in r:  return "early_failure"
    if "구조 손절" in r:      return "stop_loss"
    if "trailing" in r:       return "trailing_stop"
    if "시간" in r or "time" in r: return "time_exit"
    if "익절" in r or "take profit" in r: return "take_profit"
    if "overnight" in r or "강제청산" in r: return "overnight"
    return "other"


def capture_exit(
    stock_code: str,
    stock_name: str,
    exit_price: float,
    entry_price: float | None,
    pnl_pct: float,
    exit_reason: str = "",
    entry_time=None,          # datetime | None
) -> None:
    """
    청산 시점 데이터를 exit_capture.jsonl 에 기록.
    예외가 발생해도 매매 흐름에 영향 없음.
    """
    try:
        EXIT_CAPTURE_FILE.parent.mkdir(parents=True, exist_ok=True)

        now = datetime.now()
        duration_min: float | None = None
        if entry_time is not None:
            try:
                duration_min = round((now - entry_time).total_seconds() / 60, 1)
            except Exception:
                pass

        record = {
            "ts":           now.isoformat(timespec="seconds"),
            "date":         now.strftime("%Y%m%d"),
            "stock_code":   stock_code,
            "stock_name":   stock_name,
            "exit_price":   exit_price,
            "entry_price":  entry_price,
            "pnl_pct":      round(pnl_pct, 3),
            "won":          pnl_pct > 0,
            "exit_reason":  exit_reason[:120],
            "exit_category": _parse_exit_category(exit_reason),
            "duration_min": duration_min,
        }

        with EXIT_CAPTURE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

        logger.debug(
            "[CAPTURE_EXIT] %s: pnl=%+.2f%% cat=%s dur=%s min",
            stock_code, pnl_pct, record["exit_category"], duration_min,
        )

    except Exception as e:
        logger.warning("[CAPTURE_EXIT] 캡처 실패 (무시): %s", e)
