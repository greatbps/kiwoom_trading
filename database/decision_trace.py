"""
database/decision_trace.py — 의사결정 추적 DB 모듈

"결과 기록" → "판단→실행→결과→학습" 루프 지원.

연결 포인트:
  - execute_buy()  → record_entry_signal() → trade_signals + filter_feature_snapshot
  - execute_sell() → record_exit_signal()  → trade_signals + ml_dataset 자동 생성
  - check_entry_signal() → save_feature_snapshot() (선택적, 호출 비용 주의)

모든 함수는 예외를 삼켜서 거래 흐름을 방해하지 않는다.
"""

import os
import re
import json
import logging
from datetime import datetime
from typing import Optional
import psycopg2
import psycopg2.extras

logger = logging.getLogger(__name__)

_PG_DSN = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname":   os.getenv("POSTGRES_DB", "trading_system"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}

# 현재 전략 버전 (strategy_hybrid.yaml 읽거나 수동 관리)
STRATEGY_VERSION = "v3.2026-04"

_STRATEGY_RE = re.compile(r'^(EXPLORATION|TREND|SMC|MOMENTUM|SWEEP|RS|EXPERIMENT)', re.I)
_EXIT_KW = ("Early Failure", "Hard Stop", "Trailing", "Time Exit", "오버나이트", "강제청산")


def _extract_strategy(reason: str) -> str:
    if not reason:
        return "UNKNOWN"
    m = _STRATEGY_RE.match(reason)
    if m:
        return m.group(1).upper()
    if "TREND" in reason.upper():
        return "TREND"
    if "SMC" in reason.upper():
        return "SMC"
    if any(k in reason for k in _EXIT_KW):
        return "EXIT"
    return "UNKNOWN"


def _get_conn():
    return psycopg2.connect(**_PG_DSN)


# ─────────────────────────────────────────────────────────────
# 1. filter_pipeline_runs
# ─────────────────────────────────────────────────────────────

def start_filter_run(market_phase: str = None, stock_count: int = 0) -> Optional[int]:
    """필터 사이클 시작 → run_id 반환. 실패 시 None."""
    from datetime import datetime, time
    if market_phase is None:
        h = datetime.now().hour
        if h < 10:
            market_phase = "pre"
        elif h < 12:
            market_phase = "open"
        elif h < 14:
            market_phase = "midday"
        else:
            market_phase = "close"
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO filter_pipeline_runs (market_phase, strategy_version, stock_count)
            VALUES (%s, %s, %s) RETURNING run_id
        """, (market_phase, STRATEGY_VERSION, stock_count))
        run_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return run_id
    except Exception as e:
        logger.debug(f"[DTRACE] start_filter_run 실패: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# 2. filter_feature_snapshot
# ─────────────────────────────────────────────────────────────

def save_feature_snapshot(
    stock_code: str,
    stock_name: str,
    price: float,
    df,                      # pandas DataFrame (5분봉)
    run_id: int = None,
    market_regime: str = None,
    market_context: str = None,
) -> Optional[int]:
    """
    df: 최소 ['close','volume','high','low'] 컬럼 필요.
    실패해도 None 반환, 거래 흐름 방해 없음.
    """
    try:
        import numpy as np
        import pandas as pd

        def _safe(val):
            if val is None:
                return None
            if isinstance(val, float) and (np.isnan(val) or np.isinf(val)):
                return None
            return float(val)

        close  = df['close']
        volume = df['volume']

        # RSI (14)
        delta = close.diff()
        gain  = delta.clip(lower=0).rolling(14).mean()
        loss  = (-delta.clip(upper=0)).rolling(14).mean()
        rsi   = (100 - 100 / (1 + gain / loss)).iloc[-1]

        # EMA
        ema9  = close.ewm(span=9,  adjust=False).mean().iloc[-1]
        ema20 = close.ewm(span=20, adjust=False).mean().iloc[-1]
        ema60 = close.ewm(span=60, adjust=False).mean().iloc[-1]

        # VWAP
        typical = (df['high'] + df['low'] + close) / 3
        vwap = (typical * volume).cumsum() / volume.cumsum()
        vwap_val = vwap.iloc[-1]

        # ATR
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - close.shift()).abs(),
            (df['low']  - close.shift()).abs()
        ], axis=1).max(axis=1)
        atr     = tr.rolling(14).mean().iloc[-1]
        atr_avg = tr.rolling(60).mean().iloc[-1]
        atr_ratio = atr / atr_avg if atr_avg else None

        # Volume ratio (현재 / 20봉 평균)
        curr_vol = float(volume.iloc[-2]) if len(volume) >= 2 else float(volume.iloc[-1])
        avg_vol  = float(volume.iloc[-22:-2].mean()) if len(volume) >= 22 else float(volume.mean())
        vol_ratio = curr_vol / avg_vol if avg_vol else None

        # Gap
        if len(close) >= 2:
            prev_close = float(close.iloc[-2])
            open_price = float(df['open'].iloc[-1]) if 'open' in df.columns else float(close.iloc[-1])
            gap_pct = (open_price - prev_close) / prev_close * 100 if prev_close else None
        else:
            gap_pct = None

        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO filter_feature_snapshot
                (run_id, stock_code, stock_name, price, volume, vol_ratio,
                 vwap, rsi, ema9, ema20, ema60, atr, atr_ratio, gap_pct,
                 market_regime, market_context)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
        """, (
            run_id, stock_code, stock_name,
            _safe(price), int(curr_vol) if curr_vol else None, _safe(vol_ratio),
            _safe(vwap_val), _safe(rsi), _safe(ema9), _safe(ema20), _safe(ema60),
            _safe(atr), _safe(atr_ratio), _safe(gap_pct),
            market_regime, market_context,
        ))
        snap_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        return snap_id
    except Exception as e:
        logger.debug(f"[DTRACE] save_feature_snapshot 실패 {stock_code}: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# 3. filter_stage_results
# ─────────────────────────────────────────────────────────────

def record_filter_stage(
    run_id: int,
    stage: int,
    stock_code: str,
    stock_name: str,
    passed: bool,
    reason_tags: list = None,
    scores: dict = None,
) -> None:
    """
    stage: 1=1차필터, 2=2차필터, 3=진입직전
    scores: {'news': 0.0, 'supply': 0.0, 'technical': 0.0, 'volume': 0.0}
    reason_tags: ['RVOL_LOW', 'HTF_FAIL', ...]
    """
    if run_id is None:
        return
    scores = scores or {}
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO filter_stage_results
                (run_id, stage, stock_code, stock_name, passed,
                 news_score, supply_score, technical_score, volume_score, reason_tags)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (
            run_id, stage, stock_code, stock_name, passed,
            scores.get('news'), scores.get('supply'),
            scores.get('technical'), scores.get('volume'),
            reason_tags or [],
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"[DTRACE] record_filter_stage 실패 {stock_code}: {e}")


# ─────────────────────────────────────────────────────────────
# 4. trade_signals — 진입 신호
# ─────────────────────────────────────────────────────────────

def record_entry_signal(
    stock_code: str,
    stock_name: str,
    entry_reason: str,
    price: float,
    df=None,
    market_regime: str = None,
    market_context: str = None,
    choch_grade: str = None,
) -> Optional[int]:
    """
    execute_buy() 직전 호출.
    Returns: signal_id (trades.entry_signal_id에 저장)
    """
    strategy = _extract_strategy(entry_reason)

    # df가 있으면 간단한 지표 계산
    rsi = vwap_val = ema9_val = ema60_val = atr_ratio = None
    volume = None
    try:
        if df is not None and len(df) > 14:
            import pandas as pd
            close = df['close']
            delta = close.diff()
            gain  = delta.clip(lower=0).rolling(14).mean()
            loss  = (-delta.clip(upper=0)).rolling(14).mean()
            rsi   = float((100 - 100 / (1 + gain / loss)).iloc[-1])
            ema9_val  = float(close.ewm(span=9,  adjust=False).mean().iloc[-1])
            ema60_val = float(close.ewm(span=60, adjust=False).mean().iloc[-1])
            typical = (df['high'] + df['low'] + close) / 3
            vol = df['volume']
            vwap_val = float((typical * vol).cumsum().iloc[-1] / vol.cumsum().iloc[-1])
            tr = pd.concat([
                df['high'] - df['low'],
                (df['high'] - close.shift()).abs(),
                (df['low']  - close.shift()).abs()
            ], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]
            atr_avg = tr.rolling(60).mean().iloc[-1]
            atr_ratio = float(atr / atr_avg) if atr_avg else None
            curr_vol = float(vol.iloc[-2]) if len(vol) >= 2 else float(vol.iloc[-1])
            volume = int(curr_vol)
    except Exception:
        pass

    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO trade_signals
                (stock_code, stock_name, signal_type, strategy_name, strategy_version,
                 trigger_reason, price, volume, rsi, vwap, ema9, ema60, atr_ratio,
                 market_regime, market_context, choch_grade)
            VALUES (%s,%s,'entry',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING signal_id
        """, (
            stock_code, stock_name,
            strategy, STRATEGY_VERSION, entry_reason,
            price, volume, rsi, vwap_val, ema9_val, ema60_val, atr_ratio,
            market_regime, market_context, choch_grade,
        ))
        signal_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        logger.debug(f"[DTRACE] entry_signal {signal_id} {stock_code} {strategy}")
        return signal_id
    except Exception as e:
        logger.debug(f"[DTRACE] record_entry_signal 실패 {stock_code}: {e}")
        return None


# ─────────────────────────────────────────────────────────────
# 5. trade_signals — 청산 신호 + ml_dataset 자동 생성
# ─────────────────────────────────────────────────────────────

def record_exit_signal(
    stock_code: str,
    stock_name: str,
    exit_reason: str,
    price: float,
    trade_id: int = None,
    entry_price: float = None,
    quantity: int = None,
    holding_minutes: int = None,
    mfe_pct: float = None,
    mae_pct: float = None,
    extra_features: dict = None,
) -> Optional[int]:
    """
    execute_sell() 직전 호출.
    Returns: signal_id
    extra_features: 진입 당시 수집된 추가 피처 dict (EXPLORATION entry_type, rvol, choch_grade 등)
    """
    strategy = _extract_strategy(exit_reason) or "EXIT"

    realized_profit = None
    pnl_pct = None
    if entry_price and price and quantity:
        realized_profit = (price - entry_price) * quantity
        pnl_pct = (price - entry_price) / entry_price * 100

    try:
        conn = _get_conn()
        cur = conn.cursor()

        # exit signal 삽입
        cur.execute("""
            INSERT INTO trade_signals
                (stock_code, stock_name, signal_type, strategy_name, strategy_version,
                 trigger_reason, price)
            VALUES (%s,%s,'exit',%s,%s,%s,%s)
            RETURNING signal_id
        """, (stock_code, stock_name, strategy, STRATEGY_VERSION, exit_reason, price))
        signal_id = cur.fetchone()[0]

        # trades 업데이트
        if trade_id:
            cur.execute("""
                UPDATE trades
                SET exit_signal_id = %s,
                    exit_time      = NOW(),
                    exit_reason    = %s,
                    realized_profit = %s,
                    profit_rate    = %s,
                    holding_minutes = %s
                WHERE trade_id = %s
                  AND exit_signal_id IS NULL
            """, (signal_id, exit_reason, realized_profit, pnl_pct, holding_minutes, trade_id))

        conn.commit()

        # ml_dataset UPDATE (BUY 시 INSERT된 row에 label 채우기)
        if trade_id and realized_profit is not None:
            updated = _update_ml_exit(
                cur, conn, trade_id, realized_profit, pnl_pct,
                mae_pct, mfe_pct, holding_minutes, exit_reason, signal_id,
            )
            # row가 없으면 (구 데이터) 기존 INSERT 방식 fallback
            if not updated:
                _insert_ml_dataset(
                    cur, conn, trade_id, signal_id, stock_code,
                    realized_profit, pnl_pct,
                    mae_pct=mae_pct, mfe_pct=mfe_pct,
                    extra_features=extra_features,
                    holding_minutes=holding_minutes,
                    exit_reason=exit_reason,
                )

        # ml_decisions later_outcome 채우기 ("막았던 거래가 실제로 어땠는지")
        if trade_id and pnl_pct is not None:
            try:
                update_ml_decision_outcome(trade_id, pnl_pct)
            except Exception:
                pass

        conn.close()
        logger.debug(f"[DTRACE] exit_signal {signal_id} {stock_code} pnl={realized_profit}")
        return signal_id
    except Exception as e:
        logger.debug(f"[DTRACE] record_exit_signal 실패 {stock_code}: {e}")
        return None


def _label_quality(pnl_pct: float) -> int:
    """수익률 → 품질 라벨. 2=GOOD(+2%↑) / 1=NORMAL(0~2%) / 0=BAD(<0)"""
    if pnl_pct is None:
        return 0
    if pnl_pct > 2.0:
        return 2
    if pnl_pct > 0:
        return 1
    return 0


def _extract_features_from_df(df, price: float) -> dict:
    """df(5분봉)에서 ML 피처 추출. 실패 시 빈 dict 반환."""
    try:
        import numpy as np
        import pandas as pd

        close  = df['close']
        volume = df['volume']

        def _c(v):
            if v is None:
                return None
            try:
                f = float(v)
                return None if (np.isnan(f) or np.isinf(f)) else round(f, 6)
            except Exception:
                return None

        # RVOL: 현재봉 / 직전 20봉 평균
        curr_vol = float(volume.iloc[-1])
        avg_vol  = float(volume.iloc[-21:-1].mean()) if len(volume) >= 21 else float(volume.mean())
        rvol = _c(curr_vol / avg_vol if avg_vol else None)

        # VWAP distance: (price - vwap) / price * 100
        typical  = (df['high'] + df['low'] + close) / 3
        vwap_val = float((typical * volume).cumsum().iloc[-1] / volume.cumsum().iloc[-1])
        vwap_distance = _c((price - vwap_val) / price * 100 if price else None)

        # price_vs_breakout: 직전 20봉 최고가 대비 현재가 위치 (%)
        n_high = float(df['high'].iloc[-21:-1].max()) if len(df) >= 21 else float(df['high'].max())
        price_vs_breakout = _c((price - n_high) / n_high * 100 if n_high else None)

        # EMA slope: (ema20[-1] - ema20[-3]) / ema20[-1] * 100
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema_slope = _c(
            (float(ema20.iloc[-1]) - float(ema20.iloc[-3])) / float(ema20.iloc[-1]) * 100
            if len(ema20) >= 3 and float(ema20.iloc[-1]) != 0 else None
        )

        # ATR ratio: atr(14) / atr_avg(60)
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - close.shift()).abs(),
            (df['low']  - close.shift()).abs(),
        ], axis=1).max(axis=1)
        atr     = float(tr.rolling(14).mean().iloc[-1])
        atr_avg = float(tr.rolling(60).mean().iloc[-1])
        atr_ratio = _c(atr / atr_avg if atr_avg and atr_avg > 0 else None)

        # volume_trend: 직전 2봉 vs 그 이전 3봉 평균 비율
        if len(volume) >= 5:
            mid  = float(volume.iloc[-5:-2].mean())
            last = float(volume.iloc[-2:].mean())
            ratio = last / mid if mid > 0 else 1.0
            volume_trend = 'rising' if ratio >= 1.2 else ('falling' if ratio <= 0.8 else 'flat')
        else:
            volume_trend = None

        return {
            'rvol':              rvol,
            'vwap_distance':     vwap_distance,
            'price_vs_breakout': price_vs_breakout,
            'ema_slope':         ema_slope,
            'atr_ratio':         atr_ratio,
            'volume_trend':      volume_trend,
        }
    except Exception as e:
        logger.debug(f"[DTRACE] _extract_features_from_df 실패: {e}")
        return {}


def _insert_ml_dataset(cur, conn, trade_id, signal_id, stock_code,
                       realized_profit, pnl_pct, mae_pct=None, mfe_pct=None,
                       extra_features: dict = None,
                       holding_minutes: int = None,
                       exit_reason: str = None,
                       source_type: str = 'trade'):
    """
    거래 완료 후 ml_dataset 자동 적재.
    filter_feature_snapshot 의존 제거 — trade_signals(entry) + extra_features 직접 사용.
    라벨 3종: label_binary(1=수익) / label_quality(2/1/0) / label_risk(MAE)
    """
    try:
        lq = _label_quality(pnl_pct)
        lb = 1 if (pnl_pct or 0) > 0 else 0

        # entry signal에서 지표 가져오기
        sig_features: dict = {}
        cur.execute("""
            SELECT ts.rsi, ts.vwap, ts.ema9, ts.ema60, ts.atr_ratio,
                   ts.market_regime, ts.market_context, ts.choch_grade
            FROM trades t
            JOIN trade_signals ts ON ts.signal_id = t.entry_signal_id
            WHERE t.trade_id = %s
        """, (trade_id,))
        row = cur.fetchone()
        if row:
            sig_features = {
                'rsi': float(row[0]) if row[0] is not None else None,
                'vwap': float(row[1]) if row[1] is not None else None,
                'ema9': float(row[2]) if row[2] is not None else None,
                'ema60': float(row[3]) if row[3] is not None else None,
                'atr_ratio': float(row[4]) if row[4] is not None else None,
                'market_regime': row[5],
                'market_context': row[6],
                'choch_grade': row[7],
            }

        # entry_reason에서 전략/등급 파싱
        cur.execute(
            "SELECT entry_reason, COALESCE(entry_time, trade_time) FROM trades WHERE trade_id = %s",
            (trade_id,),
        )
        trade_row = cur.fetchone()
        entry_reason = trade_row[0] if trade_row else ""
        entry_time   = trade_row[1] if trade_row else None

        # extra_features 병합 (EXPLORATION 피처 등 실시간 수집값 우선)
        features = {**sig_features}
        if extra_features:
            features.update(extra_features)

        # entry_reason 파싱으로 피처 보강
        if entry_reason and 'strategy' not in features:
            if 'EXPLORATION' in entry_reason:
                features.setdefault('strategy', 'EXPLORATION')
            elif 'SMC' in entry_reason or 'CHoCH' in entry_reason:
                features.setdefault('strategy', 'SMC')
            elif 'TREND' in entry_reason:
                features.setdefault('strategy', 'TREND')
        if entry_reason and 'choch_grade' not in features:
            import re as _re
            _g = _re.search(r'CHoCH(?:_등급)?[=:\s]+([ABC])', entry_reason)
            if _g:
                features['choch_grade'] = _g.group(1)

        cur.execute("""
            INSERT INTO ml_dataset
                (trade_id, signal_id, stock_code, entry_time, features,
                 label_pnl, label_pnl_pct, label_binary,
                 label_updown, label_quality, label_risk,
                 mae_pct, mfe_pct, holding_minutes, exit_reason, source_type)
            VALUES (%s, %s, %s, %s, %s::jsonb,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s, %s, %s)
            ON CONFLICT DO NOTHING
        """, (
            trade_id, signal_id, stock_code, entry_time,
            json.dumps(features),
            realized_profit, pnl_pct, lb,
            lb, lq, mae_pct,
            mae_pct, mfe_pct, holding_minutes, exit_reason,
            source_type,
        ))
        conn.commit()
        logger.debug(f"[DTRACE] ml_dataset 생성 trade_id={trade_id} features={list(features.keys())}")
    except Exception as e:
        logger.debug(f"[DTRACE] ml_dataset 생성 실패 trade_id={trade_id}: {e}")


def _update_ml_exit(
    cur, conn,
    trade_id: int,
    realized_profit: float,
    pnl_pct: float,
    mae_pct: float,
    mfe_pct: float,
    holding_minutes: int,
    exit_reason: str,
    signal_id: int,
) -> bool:
    """
    ml_dataset의 trade_id 행에 SELL 결과 라벨을 채운다.
    INSERT-first(insert_ml_entry) → UPDATE-later(이 함수) 패턴.
    Returns True if 업데이트된 행이 있었음.
    """
    try:
        lq = _label_quality(pnl_pct)
        lb = 1 if (pnl_pct or 0) > 0 else 0
        cur.execute("""
            UPDATE ml_dataset
            SET label_pnl       = %s,
                label_pnl_pct   = %s,
                label_binary    = %s,
                label_updown    = %s,
                label_quality   = %s,
                label_risk      = %s,
                mae_pct         = %s,
                mfe_pct         = %s,
                holding_minutes = %s,
                exit_reason     = %s,
                signal_id       = COALESCE(signal_id, %s)
            WHERE trade_id = %s
              AND label_pnl IS NULL
        """, (
            realized_profit, pnl_pct, lb, lb, lq, mae_pct,
            mae_pct, mfe_pct, holding_minutes, exit_reason, signal_id,
            trade_id,
        ))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.debug(f"[DTRACE] _update_ml_exit 실패 trade_id={trade_id}: {e}")
        return False


# ─────────────────────────────────────────────────────────────
# ML dataset BUY/SELL 쌍 + 차단 기록
# ─────────────────────────────────────────────────────────────

def insert_ml_entry(
    trade_id: int,
    stock_code: str,
    entry_time,
    df,
    entry_type: str = None,
    pending_duration: int = None,
    entry_reason: str = None,
) -> None:
    """
    BUY 완료 직후 호출 — ml_dataset에 피처만 INSERT (라벨 컬럼은 NULL).
    SELL 시 _update_ml_exit()가 라벨을 채운다.
    """
    try:
        price = float(df['close'].iloc[-1]) if df is not None and len(df) > 0 else 0.0
        feats = _extract_features_from_df(df, price) if df is not None else {}
        feats_json = json.dumps({**feats, 'entry_reason': entry_reason or ''})

        conn = _get_conn()
        cur = conn.cursor()

        # 중복 방지: 이미 같은 trade_id 존재하면 skip
        cur.execute("SELECT 1 FROM ml_dataset WHERE trade_id = %s", (trade_id,))
        if cur.fetchone():
            conn.close()
            return

        cur.execute("""
            INSERT INTO ml_dataset
                (trade_id, stock_code, entry_time,
                 rvol, price_vs_breakout, vwap_distance,
                 ema_slope, atr_ratio, volume_trend,
                 entry_type, pending_duration,
                 features, source_type)
            VALUES (%s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s,
                    %s::jsonb, 'trade')
        """, (
            trade_id, stock_code, entry_time,
            feats.get('rvol'), feats.get('price_vs_breakout'), feats.get('vwap_distance'),
            feats.get('ema_slope'), feats.get('atr_ratio'), feats.get('volume_trend'),
            entry_type, pending_duration,
            feats_json,
        ))
        conn.commit()
        conn.close()
        logger.debug(f"[DTRACE] ml_entry INSERT trade_id={trade_id} entry_type={entry_type} feats={list(feats.keys())}")
    except Exception as e:
        logger.debug(f"[DTRACE] insert_ml_entry 실패 trade_id={trade_id}: {e}")


def insert_blocked_trade(
    stock_code: str,
    stock_name: str,
    block_reason: str,
    df,
    price: float,
    entry_reason: str = None,
    entry_type: str = None,
    pending_duration: int = None,
) -> None:
    """
    진입 차단 시 호출 — blocked_trades에 차단 컨텍스트 + 피처 INSERT.
    ML이 "왜 안 들어갔는지"도 학습하도록.
    """
    try:
        feats = _extract_features_from_df(df, price) if df is not None else {}
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO blocked_trades
                (stock_code, stock_name, block_reason,
                 rvol, price_vs_breakout, vwap_distance,
                 ema_slope, atr_ratio, volume_trend,
                 entry_type, pending_duration, entry_reason)
            VALUES (%s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s,
                    %s, %s, %s)
        """, (
            stock_code, stock_name, block_reason,
            feats.get('rvol'), feats.get('price_vs_breakout'), feats.get('vwap_distance'),
            feats.get('ema_slope'), feats.get('atr_ratio'), feats.get('volume_trend'),
            entry_type, pending_duration, entry_reason,
        ))
        conn.commit()
        conn.close()
        logger.debug(f"[DTRACE] blocked_trade INSERT {stock_code} reason={block_reason}")
    except Exception as e:
        logger.debug(f"[DTRACE] insert_blocked_trade 실패 {stock_code}: {e}")


# ─────────────────────────────────────────────────────────────
# filtered_out / signal_rejected 데이터 저장
# ─────────────────────────────────────────────────────────────

def save_rejected_candidate(
    stock_code: str,
    stock_name: str,
    features: dict,
    reason_tags: list,
    source_type: str = 'filtered_out',   # 'filtered_out' | 'signal_rejected'
    run_id: int = None,
) -> None:
    """
    필터에서 탈락한 종목도 ml_dataset에 저장 (label=0, source_type='filtered_out').
    "왜 버렸는지"까지 학습해야 진짜 AI가 됨.

    호출 위치:
      - record_filter_stage(..., passed=False) 이후
      - check_entry_signal 에서 REJECT 반환 시
    """
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO ml_dataset
                (stock_code, entry_time, features,
                 label_pnl, label_pnl_pct, label_binary,
                 label_updown, label_quality, label_risk,
                 source_type)
            VALUES (%s, NOW(), %s,
                    0, 0, 0,
                    0, 0, NULL,
                    %s)
        """, (
            stock_code,
            psycopg2.extras.Json({**features, 'reason_tags': reason_tags}),
            source_type,
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.debug(f"[DTRACE] save_rejected_candidate 실패 {stock_code}: {e}")


# ─────────────────────────────────────────────────────────────
# 6. strategy_change_log
# ─────────────────────────────────────────────────────────────

def log_strategy_change(
    strategy_name: str,
    param_key: str,
    value_before,
    value_after,
    change_type: str = "threshold_change",
    description: str = None,
    expected_effect: str = None,
) -> None:
    """전략 파라미터 변경 기록. 수동 호출 또는 YAML diff 감지 시 호출."""
    try:
        conn = _get_conn()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO strategy_change_log
                (strategy_name, param_key, value_before, value_after,
                 change_type, description, expected_effect)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
        """, (
            strategy_name, param_key,
            str(value_before), str(value_after),
            change_type, description, expected_effect,
        ))
        conn.commit()
        conn.close()
        logger.info(f"[DTRACE] strategy_change {strategy_name}.{param_key}: {value_before}→{value_after}")
    except Exception as e:
        logger.debug(f"[DTRACE] log_strategy_change 실패: {e}")


# ─────────────────────────────────────────────────────────────
# 7. ML 판단 로그 — log_ml_decision / update_ml_decision_outcome
# ─────────────────────────────────────────────────────────────

def log_ml_decision(
    stock_code: str,
    prob: Optional[float],
    threshold: float,
    model_version: str,
    shadow_mode: bool,
    blocked: bool,
    features: dict,
    trade_id: int = None,
    entry_type: str = None,
) -> Optional[int]:
    """
    모든 진입 시점에 무조건 호출 — prob·입력 피처·차단 여부를 ml_decisions에 기록.
    나중에 update_ml_decision_outcome()으로 later_outcome 채움.
    Returns: decision_id (포지션 dict에 저장해 두면 SELL 시 업데이트 가능)
    """
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            INSERT INTO ml_decisions
                (trade_id, stock_code, prob, threshold, model_version,
                 shadow_mode, blocked, entry_type,
                 rvol, price_vs_breakout, vwap_distance,
                 ema_slope, atr_ratio, volume_trend)
            VALUES (%s,%s,%s,%s,%s, %s,%s,%s, %s,%s,%s, %s,%s,%s)
            RETURNING id
        """, (
            trade_id, stock_code,
            prob, threshold, model_version,
            shadow_mode, blocked,
            entry_type or features.get('entry_type'),
            features.get('rvol'),
            features.get('price_vs_breakout'),
            features.get('vwap_distance'),
            features.get('ema_slope'),
            features.get('atr_ratio'),
            features.get('volume_trend'),
        ))
        decision_id = cur.fetchone()[0]
        conn.commit()
        conn.close()
        tag      = '[ML_SHADOW_BLOCK]' if (blocked and shadow_mode) else ('[ML_BLOCK]' if blocked else '[ML_PASS]')
        prob_str = f'{prob:.3f}' if prob is not None else 'N/A'
        logger.info(
            f"{tag} {stock_code} prob={prob_str} thr={threshold} "
            f"shadow={shadow_mode} rvol={features.get('rvol')} "
            f"vwap_d={features.get('vwap_distance')}"
        )
        return decision_id
    except Exception as e:
        logger.debug(f"[DTRACE] log_ml_decision 실패 {stock_code}: {e}")
        return None


def update_ml_decision_outcome(
    trade_id: int,
    later_outcome: float,
) -> bool:
    """
    SELL 완료 후 호출 — ml_decisions.later_outcome 채움.
    "막은 거래가 실제로 어땠는지" 추적 → threshold 튜닝 근거.
    Returns True if updated.
    """
    try:
        conn = _get_conn()
        cur  = conn.cursor()
        cur.execute("""
            UPDATE ml_decisions
            SET later_outcome = %s
            WHERE trade_id = %s
              AND later_outcome IS NULL
        """, (later_outcome, trade_id))
        conn.commit()
        updated = cur.rowcount > 0
        conn.close()
        return updated
    except Exception as e:
        logger.debug(f"[DTRACE] update_ml_decision_outcome 실패 trade_id={trade_id}: {e}")
        return False
