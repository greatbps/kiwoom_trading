"""
기존 trades 레코드 → ml_dataset 백필

trades 테이블의 BUY/SELL 쌍을 매칭하여 ml_dataset에 삽입.
exit_context의 mfe_pct, exit_reason, profit_rate 활용.
entry_signal_id → trade_signals에서 RSI/VWAP 등 지표 추출.

사용법:
    python3 analysis/ml_backfill.py [--dry-run] [--since 2026-01-01]
"""
import os
import sys
import json
import re
import argparse
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2
import psycopg2.extras

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

_PG_DSN = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "port":     int(os.getenv("POSTGRES_PORT", "5432")),
    "dbname":   os.getenv("POSTGRES_DB", "trading_system"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}

try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent.parent / ".env")
    _PG_DSN["password"] = os.getenv("POSTGRES_PASSWORD", "")
except ImportError:
    pass


def _label_quality(pnl_pct):
    if pnl_pct is None:
        return 0
    if pnl_pct > 2.0:
        return 2
    if pnl_pct > 0:
        return 1
    return 0


def _extract_expl_entry_type(entry_reason: str) -> str:
    if not entry_reason:
        return None
    if "[강확인]" in entry_reason or "[1봉확인]" in entry_reason:
        return "CONFIRM"
    if "[약확인]" in entry_reason:
        return "SOFT"
    if "[조기진입]" in entry_reason:
        return "EARLY"
    if "EXPLORATION" in entry_reason:
        return "IMMEDIATE"
    return None


def _extract_choch_grade(entry_reason: str) -> str:
    if not entry_reason:
        return None
    m = re.search(r'CHoCH.*?([ABC])급', entry_reason)
    if m:
        return m.group(1)
    m = re.search(r'grade[=:\s]+([ABC])', entry_reason, re.IGNORECASE)
    if m:
        return m.group(1)
    return None


def _extract_strategy(entry_reason: str) -> str:
    if not entry_reason:
        return "UNKNOWN"
    if "EXPLORATION" in entry_reason:
        return "EXPLORATION"
    if "TREND" in entry_reason:
        return "TREND"
    if "SMC" in entry_reason or "CHoCH" in entry_reason or "Sweep" in entry_reason:
        return "SMC"
    return "UNKNOWN"


def run(dry_run: bool = False, since: str = None):
    conn = psycopg2.connect(**_PG_DSN)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 이미 ml_dataset에 있는 trade_id 조회
    cur.execute("SELECT DISTINCT trade_id FROM ml_dataset WHERE trade_id IS NOT NULL")
    existing = {r['trade_id'] for r in cur.fetchall()}
    logger.info(f"기존 ml_dataset: {len(existing)}건 (skip 대상)")

    # BUY 레코드 조회
    since_clause = f"AND t.trade_time >= '{since}'" if since else ""
    cur.execute(f"""
        SELECT
            t.trade_id, t.stock_code, t.stock_name,
            t.price AS buy_price, t.quantity,
            t.entry_reason, t.entry_time, t.entry_signal_id,
            t.entry_context
        FROM trades t
        WHERE t.trade_type = 'BUY'
        {since_clause}
        ORDER BY t.trade_time
    """)
    buys = {r['trade_id']: dict(r) for r in cur.fetchall()}

    # SELL 레코드 조회 (trade_id FK 기준 매칭)
    cur.execute(f"""
        SELECT
            t.trade_id AS sell_trade_id,
            t.stock_code, t.price AS sell_price, t.quantity,
            t.exit_reason, t.exit_time, t.exit_signal_id,
            t.realized_profit, t.profit_rate, t.holding_minutes,
            t.exit_context,
            ts_buy.trade_id AS buy_trade_id
        FROM trades t
        -- entry_signal_id 기준으로 BUY 연결 시도
        LEFT JOIN trades ts_buy ON (
            ts_buy.trade_type = 'BUY'
            AND ts_buy.stock_code = t.stock_code
            AND ts_buy.trade_time < t.trade_time
            AND ts_buy.trade_time >= t.trade_time - INTERVAL '8 hours'
        )
        WHERE t.trade_type = 'SELL'
        {since_clause}
        ORDER BY t.trade_time
    """)
    sells_raw = cur.fetchall()

    # BUY-SELL 매칭: 종목별 가장 가까운 BUY 연결
    used_buys = set()
    pairs = []
    for sell in sells_raw:
        sell = dict(sell)
        # 이미 ml_dataset에 있으면 skip
        if sell['buy_trade_id'] and sell['buy_trade_id'] in existing:
            continue
        if sell['buy_trade_id'] and sell['buy_trade_id'] not in used_buys:
            buy = buys.get(sell['buy_trade_id'])
            if buy:
                used_buys.add(sell['buy_trade_id'])
                pairs.append((buy, sell))

    logger.info(f"매칭된 BUY-SELL 쌍: {len(pairs)}건 (기존 제외)")

    inserted = 0
    skipped = 0

    for buy, sell in pairs:
        trade_id = buy['trade_id']
        if trade_id in existing:
            skipped += 1
            continue

        stock_code = buy['stock_code']
        entry_reason = buy.get('entry_reason') or ""
        exit_reason = sell.get('exit_reason') or ""

        # MFE/MAE: exit_context JSONB에서 추출
        mfe_pct = None
        mae_pct = None
        exit_ctx = sell.get('exit_context')
        if exit_ctx:
            if isinstance(exit_ctx, str):
                try:
                    exit_ctx = json.loads(exit_ctx)
                except Exception:
                    exit_ctx = {}
            mfe_pct = exit_ctx.get('mfe_pct')
            # MAE는 exit_context에 없으므로 None 유지

        profit_rate = float(sell.get('profit_rate') or 0)
        realized_profit = float(sell.get('realized_profit') or 0)

        lq = _label_quality(profit_rate)
        lb = 1 if profit_rate > 0 else 0

        # 피처 구성
        features: dict = {}
        strategy = _extract_strategy(entry_reason)
        features['strategy'] = strategy
        choch_grade = _extract_choch_grade(entry_reason)
        if choch_grade:
            features['choch_grade'] = choch_grade

        expl_type = _extract_expl_entry_type(entry_reason)
        if expl_type:
            features['entry_type'] = expl_type

        # RVOL 파싱 (entry_reason에 "RVOL=X.Xx" 패턴)
        m_rvol = re.search(r'RVOL=(\d+\.?\d*)', entry_reason)
        if m_rvol:
            features['rvol'] = float(m_rvol.group(1))

        # entry signal에서 지표
        if buy.get('entry_signal_id'):
            sig_cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            sig_cur.execute("""
                SELECT rsi, vwap, ema9, ema60, atr_ratio, market_regime,
                       market_context, choch_grade
                FROM trade_signals WHERE signal_id = %s
            """, (buy['entry_signal_id'],))
            sig = sig_cur.fetchone()
            if sig:
                for k in ('rsi', 'vwap', 'ema9', 'ema60', 'atr_ratio',
                          'market_regime', 'market_context'):
                    if sig[k] is not None:
                        features[k] = float(sig[k]) if sig[k] and k not in ('market_regime', 'market_context') else sig[k]
                if sig['choch_grade'] and 'choch_grade' not in features:
                    features['choch_grade'] = sig['choch_grade']

        if dry_run:
            logger.info(f"[DRY] trade_id={trade_id} {stock_code} pnl={profit_rate:.2f}% "
                        f"mfe={mfe_pct} strategy={strategy} features={list(features.keys())}")
            inserted += 1
            continue

        insert_cur = conn.cursor()
        try:
            insert_cur.execute("""
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
                trade_id,
                sell.get('exit_signal_id'),
                stock_code,
                buy.get('entry_time'),
                json.dumps(features),
                realized_profit, profit_rate, lb,
                lb, lq, mae_pct,
                mae_pct, mfe_pct,
                sell.get('holding_minutes'),
                exit_reason,
                'backfill',
            ))
            conn.commit()
            inserted += 1
        except Exception as e:
            conn.rollback()
            logger.warning(f"INSERT 실패 trade_id={trade_id}: {e}")
            skipped += 1

    conn.close()
    logger.info(f"완료: inserted={inserted}, skipped={skipped}")
    return inserted, skipped


def main():
    parser = argparse.ArgumentParser(description="ml_dataset 백필")
    parser.add_argument("--dry-run", action="store_true", help="실제 INSERT 없이 확인만")
    parser.add_argument("--since", default=None, help="시작일 (YYYY-MM-DD), 없으면 전체")
    args = parser.parse_args()

    inserted, skipped = run(dry_run=args.dry_run, since=args.since)
    print(f"\n{'[DRY-RUN] ' if args.dry_run else ''}백필 완료: {inserted}건 삽입, {skipped}건 스킵")


if __name__ == "__main__":
    main()
