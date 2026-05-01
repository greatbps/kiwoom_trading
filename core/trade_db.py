"""
core/trade_db.py — 영구 거래 이력 DB (SQLite)

risk_log.json은 일일 리셋 → 이 DB는 전체 이력 보존.
스키마는 현재 시스템(risk_log.json) 필드 기준.

사용법:
    from core.trade_db import TradeDB
    db = TradeDB()
    db.insert(trade_dict)   # risk_log.json trade dict 그대로 전달
"""

import sqlite3
import re
from pathlib import Path
from datetime import datetime

DB_PATH = Path(__file__).parent.parent / "data" / "trades.db"

_STRATEGY_PATTERN = re.compile(r'^(EXPLORATION|TREND|SMC|MOMENTUM|SWEEP)', re.IGNORECASE)
_EXIT_KEYWORDS = ("Early Failure", "오버나이트", "Hard Stop", "Trailing", "Stop Loss",
                  "Time Exit", "Take Profit", "강제청산", "손절", "익절")


def _extract_strategy(reason: str) -> str:
    """reason 문자열에서 전략 태그 추출"""
    if not reason:
        return "UNKNOWN"
    m = _STRATEGY_PATTERN.match(reason)
    if m:
        return m.group(1).upper()
    if any(k in reason for k in _EXIT_KEYWORDS):
        return "EXIT"
    # "10:44 TREND BREAKOUT..." 형태 — 앞에 시각이 붙은 경우
    if "TREND" in reason.upper():
        return "TREND"
    if "SMC" in reason.upper():
        return "SMC"
    return "UNKNOWN"


class TradeDB:
    """영구 거래 이력 SQLite DB"""

    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path) if db_path else DB_PATH
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        """테이블 없으면 생성"""
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_date  TEXT NOT NULL,
                    timestamp   TEXT NOT NULL,
                    stock_code  TEXT NOT NULL,
                    stock_name  TEXT NOT NULL,
                    trade_type  TEXT NOT NULL,
                    quantity    INTEGER NOT NULL,
                    price       REAL NOT NULL,
                    amount      REAL NOT NULL,
                    realized_pnl REAL DEFAULT 0.0,
                    reason      TEXT,
                    strategy    TEXT,
                    created_at  TEXT DEFAULT (datetime('now','localtime'))
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_date
                ON trades (trade_date)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_trades_code
                ON trades (stock_code)
            """)

    def insert(self, trade: dict) -> int:
        """
        trade dict 삽입. risk_log.json의 daily_trades 항목을 그대로 받는다.
        이미 동일 timestamp+stock_code가 있으면 무시 (멱등).
        Returns: inserted row id (0 if skipped)
        """
        ts = trade.get("timestamp", "")
        trade_date = ts[:10] if ts else datetime.now().strftime("%Y-%m-%d")
        strategy = _extract_strategy(trade.get("reason", ""))

        with self._connect() as conn:
            # 중복 방지
            exists = conn.execute(
                "SELECT id FROM trades WHERE timestamp=? AND stock_code=? AND trade_type=?",
                (ts, trade["stock_code"], trade["type"])
            ).fetchone()
            if exists:
                return 0

            cur = conn.execute("""
                INSERT INTO trades
                    (trade_date, timestamp, stock_code, stock_name,
                     trade_type, quantity, price, amount, realized_pnl, reason, strategy)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                trade_date,
                ts,
                trade["stock_code"],
                trade.get("stock_name", ""),
                trade["type"],
                int(trade.get("quantity", 0)),
                float(trade.get("price", 0)),
                float(trade.get("amount", 0)),
                float(trade.get("realized_pnl", 0) or 0),
                trade.get("reason", ""),
                strategy,
            ))
            return cur.lastrowid

    def query(self, days: int = 30, stock_code: str = None) -> list[dict]:
        """최근 N일 거래 조회"""
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        sql = "SELECT * FROM trades WHERE trade_date >= ?"
        params = [cutoff]
        if stock_code:
            sql += " AND stock_code = ?"
            params.append(stock_code)
        sql += " ORDER BY timestamp"

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def migrate_from_risk_log(self, risk_log_path: str = None) -> int:
        """risk_log.json의 weekly_trades를 DB로 마이그레이션. Returns: 삽입 건수."""
        import json
        path = Path(risk_log_path) if risk_log_path else Path(__file__).parent.parent / "data" / "risk_log.json"
        if not path.exists():
            return 0
        data = json.loads(path.read_text())
        count = 0
        for t in data.get("weekly_trades", []):
            if self.insert(t):
                count += 1
        return count
