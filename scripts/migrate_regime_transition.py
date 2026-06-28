"""
P1 보완 마이그레이션 — regime_transition + mae_before_mfe 컬럼 추가

실행:
    python3 scripts/migrate_regime_transition.py

멱등(idempotent): ADD COLUMN IF NOT EXISTS 사용.
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
        for table in ("trades", "ml_dataset"):
            cur.execute(f"""
                ALTER TABLE {table}
                ADD COLUMN IF NOT EXISTS regime_transition VARCHAR(30)
            """)
            print(f"  {table}.regime_transition — OK")

            cur.execute(f"""
                ALTER TABLE {table}
                ADD COLUMN IF NOT EXISTS mae_before_mfe BOOLEAN
            """)
            print(f"  {table}.mae_before_mfe — OK")

        # 기존 레코드 백필: entry_market_regime + exit_market_regime → regime_transition
        cur.execute("""
            UPDATE trades
            SET regime_transition = market_regime || '→' || exit_market_regime
            WHERE market_regime IS NOT NULL
              AND exit_market_regime IS NOT NULL
              AND regime_transition IS NULL
        """)
        print(f"  trades regime_transition 백필: {cur.rowcount}건")

        conn.commit()
        print("\n[P1 보완 마이그레이션 완료]")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] 마이그레이션 실패: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run()
