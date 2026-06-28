"""
P1 ML Feature 마이그레이션

trades 테이블에 ML/RL/메타라벨링에 필요한 피처 컬럼 추가.
ml_dataset 테이블에 entry/exit 스냅샷 JSONB 컬럼 추가.

실행:
    python3 scripts/migrate_ml_features.py

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
        # ── 1. trades 테이블 — ML 피처 컬럼 추가 ────────────────────────────
        trades_cols = [
            # 리스크/수익 지표
            ("r_multiple",           "FLOAT"),           # pnl / initial_risk
            ("risk_per_trade",       "FLOAT"),           # 진입 시 리스크 금액
            ("confidence",           "FLOAT"),           # 신호 신뢰도
            ("position_size_mult",   "FLOAT"),           # 적용된 사이즈 배수
            # 시장 상태
            ("exit_market_regime",   "VARCHAR(20)"),     # 청산 시 레짐
            # 미래 수익률 레이블 (사후 채움)
            ("future_return_1h",     "FLOAT"),
            ("future_return_3h",     "FLOAT"),
            ("future_return_eod",    "FLOAT"),
            # 의사결정 전체 스냅샷
            ("entry_features",       "JSONB"),           # 진입 시 전체 피처
            # MAE/MFE 도달 시각
            ("max_favorable_time",   "TIMESTAMP"),       # MFE 도달 시각
            ("max_adverse_time",     "TIMESTAMP"),       # MAE 도달 시각
        ]
        for col, dtype in trades_cols:
            cur.execute(f"""
                ALTER TABLE trades
                ADD COLUMN IF NOT EXISTS {col} {dtype}
            """)
            print(f"  trades.{col} {dtype} — OK")

        # ── 2. ml_dataset 테이블 — entry/exit 스냅샷 JSONB 컬럼 추가 ────────
        ml_cols = [
            ("entry_features",       "JSONB"),   # 진입 시 전체 피처 스냅샷
            ("rejection_features",   "JSONB"),   # 거절된 신호 피처 (buy_failures)
            ("r_multiple",           "FLOAT"),
            ("risk_per_trade",       "FLOAT"),
            ("confidence",           "FLOAT"),
            ("position_size_mult",   "FLOAT"),
            ("exit_market_regime",   "VARCHAR(20)"),
            ("future_return_1h",     "FLOAT"),
            ("future_return_3h",     "FLOAT"),
            ("future_return_eod",    "FLOAT"),
        ]
        for col, dtype in ml_cols:
            cur.execute(f"""
                ALTER TABLE ml_dataset
                ADD COLUMN IF NOT EXISTS {col} {dtype}
            """)
            print(f"  ml_dataset.{col} {dtype} — OK")

        # ── 3. 인덱스 — entry_features JSONB GIN 인덱스 ─────────────────────
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_trades_entry_features
                ON trades USING GIN (entry_features)
        """)
        print("  idx_trades_entry_features (GIN) — OK")

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_ml_entry_features
                ON ml_dataset USING GIN (entry_features)
        """)
        print("  idx_ml_entry_features (GIN) — OK")

        conn.commit()
        print("\n[P1 ML Feature 마이그레이션 완료]")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] 마이그레이션 실패: {e}")
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    run()
