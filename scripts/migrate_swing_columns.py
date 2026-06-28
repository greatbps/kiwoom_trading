"""
스윙 P0 마이그레이션 — trades 컬럼 확장 + swing_features 테이블 생성

실행:
    python3 scripts/migrate_swing_columns.py

멱등(idempotent): 이미 존재하면 건너뜀.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import psycopg2

DB_DSN = "dbname=trading_system user=postgres"


def run():
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # ── 1. trades 테이블 — 스윙 전용 컬럼 추가 ──────────────────────────
        new_cols = [
            ("swing_pattern",  "VARCHAR(50)"),
            ("swing_score",    "FLOAT"),
            ("mfe_pct",        "FLOAT"),
            ("mae_pct",        "FLOAT"),
            ("peak_price",     "NUMERIC(15,2)"),
            ("trough_price",   "NUMERIC(15,2)"),
            ("market_regime",  "VARCHAR(20)"),
            ("stop_price",     "NUMERIC(15,2)"),
            ("target_price",   "NUMERIC(15,2)"),
        ]
        for col, dtype in new_cols:
            cur.execute(f"""
                ALTER TABLE trades
                ADD COLUMN IF NOT EXISTS {col} {dtype}
            """)
            print(f"  trades.{col} {dtype} — OK")

        # ── 2. swing_features 테이블 — SignalEngine 스냅샷 ──────────────────
        cur.execute("""
            CREATE TABLE IF NOT EXISTS swing_features (
                id            BIGSERIAL PRIMARY KEY,
                trade_id      INTEGER REFERENCES trades(trade_id) ON DELETE SET NULL,
                stock_code    VARCHAR(12) NOT NULL,
                entry_date    DATE,
                pattern       VARCHAR(50),
                raw_score     FLOAT,
                final_score   FLOAT,
                phase         VARCHAR(20),
                trigger       BOOLEAN,
                confidence    FLOAT,
                entry_price   NUMERIC(15,2),
                stop_price    NUMERIC(15,2),
                target_price  NUMERIC(15,2),
                rr_ratio      FLOAT,
                size          FLOAT,
                market_regime VARCHAR(20),
                meta          JSONB,
                created_at    TIMESTAMP DEFAULT now()
            )
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_sf_trade_id
                ON swing_features(trade_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_sf_stock_date
                ON swing_features(stock_code, entry_date)
        """)
        print("  swing_features — OK")

        conn.commit()
        print("\n마이그레이션 완료.")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] 마이그레이션 실패: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run()
