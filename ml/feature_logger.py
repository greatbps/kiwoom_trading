"""
ml/feature_logger.py — 진입 피처 로깅 + 결과 업데이트

흐름:
    execute_buy()  → log_entry()  : 진입 시 피처 저장
    execute_sell() → log_outcome(): 청산 시 결과 업데이트

테이블: data/trades.db → entry_features
"""

import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "data" / "trades.db"


class FeatureLogger:
    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._init_table()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_table(self):
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entry_features (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    stock_code      TEXT NOT NULL,
                    stock_name      TEXT,
                    entry_price     REAL,

                    -- 신호 품질
                    choch_grade     TEXT,
                    eq_grade        TEXT,
                    entry_confidence REAL,
                    r_pct           REAL,

                    -- 시장 구조
                    htf_trend       INTEGER,   -- 1/0
                    sweep           INTEGER,   -- 1/0
                    regime          TEXT,

                    -- 기술 지표
                    atr_pct         REAL,      -- atr / entry_price * 100
                    volume_ratio    REAL,      -- vol / 20MA_vol
                    rsi             REAL,
                    squeeze_on      INTEGER,   -- 1/0
                    time_slot       INTEGER,   -- 진입 시각 (분, 09:00=0)

                    -- 시스템 상태
                    guard_state     TEXT,      -- normal / lsg / conservative

                    -- 결과 (청산 시 업데이트)
                    outcome_pnl_pct REAL,
                    outcome_win     INTEGER,   -- 1/0
                    exit_reason     TEXT,
                    exit_timestamp  TEXT,

                    created_at      TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ef_code
                ON entry_features (stock_code)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ef_ts
                ON entry_features (timestamp)
            """)

    # ── 진입 시 호출 ──────────────────────────────────────────────

    def log_entry(
        self,
        stock_code: str,
        stock_name: str,
        entry_price: float,
        features: dict,
    ) -> int:
        """
        진입 피처 저장.

        features 키:
            choch_grade, eq_grade, entry_confidence, r_pct,
            htf_trend, sweep, regime,
            atr_pct, volume_ratio, rsi, squeeze_on,
            time_slot, guard_state
        Returns: 삽입된 row id
        """
        ts = datetime.now().isoformat()
        try:
            with self._connect() as conn:
                cur = conn.execute("""
                    INSERT INTO entry_features
                        (timestamp, stock_code, stock_name, entry_price,
                         choch_grade, eq_grade, entry_confidence, r_pct,
                         htf_trend, sweep, regime,
                         atr_pct, volume_ratio, rsi, squeeze_on,
                         time_slot, guard_state)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    ts, stock_code, stock_name, entry_price,
                    features.get('choch_grade'),
                    features.get('eq_grade'),
                    features.get('entry_confidence'),
                    features.get('r_pct'),
                    int(bool(features.get('htf_trend'))),
                    int(bool(features.get('sweep'))),
                    features.get('regime', 'UNKNOWN'),
                    features.get('atr_pct'),
                    features.get('volume_ratio'),
                    features.get('rsi'),
                    int(bool(features.get('squeeze_on'))),
                    features.get('time_slot'),
                    features.get('guard_state', 'normal'),
                ))
                row_id = cur.lastrowid
            logger.debug(f"[FEAT_LOG] {stock_code} entry logged id={row_id}")
            return row_id
        except Exception as e:
            logger.warning(f"[FEAT_LOG] log_entry 실패 {stock_code}: {e}")
            return 0

    # ── 청산 시 호출 ──────────────────────────────────────────────

    def log_outcome(
        self,
        stock_code: str,
        pnl_pct: float,
        exit_reason: str,
    ):
        """
        가장 최근 미완료 entry_features 행에 결과 업데이트.
        outcome_win = 1 if pnl_pct > 0 else 0
        """
        try:
            exit_ts = datetime.now().isoformat()
            with self._connect() as conn:
                conn.execute("""
                    UPDATE entry_features
                    SET outcome_pnl_pct = ?,
                        outcome_win     = ?,
                        exit_reason     = ?,
                        exit_timestamp  = ?
                    WHERE stock_code = ?
                      AND outcome_win IS NULL
                    ORDER BY id DESC
                    LIMIT 1
                """, (
                    round(pnl_pct, 4),
                    1 if pnl_pct > 0 else 0,
                    exit_reason,
                    exit_ts,
                    stock_code,
                ))
            logger.debug(f"[FEAT_LOG] {stock_code} outcome pnl={pnl_pct:+.2f}%")
        except Exception as e:
            logger.warning(f"[FEAT_LOG] log_outcome 실패 {stock_code}: {e}")

    # ── 학습용 데이터 조회 ────────────────────────────────────────

    def load_labeled(self, min_samples: int = 50) -> "list[dict]":
        """결과가 기록된 행만 반환 (EQ 모델 학습용)"""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM entry_features
                WHERE outcome_win IS NOT NULL
                ORDER BY id
            """).fetchall()
        data = [dict(r) for r in rows]
        if len(data) < min_samples:
            logger.info(f"[FEAT_LOG] 학습 데이터 부족: {len(data)}/{min_samples}")
            return []
        return data

    def stats(self) -> dict:
        """현재 로그 통계"""
        with self._connect() as conn:
            total   = conn.execute("SELECT COUNT(*) FROM entry_features").fetchone()[0]
            labeled = conn.execute("SELECT COUNT(*) FROM entry_features WHERE outcome_win IS NOT NULL").fetchone()[0]
            wins    = conn.execute("SELECT COUNT(*) FROM entry_features WHERE outcome_win=1").fetchone()[0]
        return {
            'total': total,
            'labeled': labeled,
            'unlabeled': total - labeled,
            'win_rate': round(wins / labeled * 100, 1) if labeled else 0,
        }
