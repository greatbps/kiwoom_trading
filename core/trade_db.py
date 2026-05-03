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
                  "Time Exit", "Take Profit", "강제청산", "손절", "익절",
                  "트레일링", "ATR", "MFE_EXIT")   # 한글 트레일링 + ATR 추가
_MANUAL_KEYWORDS = ("HTS_IMPORT", "API_SYNC")      # HTS/외부 수동 체결


def _extract_strategy(reason: str) -> str:
    """reason 문자열에서 전략 태그 추출"""
    if not reason:
        return "UNKNOWN"
    # 수동 HTS/API 체결은 MANUAL로 분류
    if any(k in reason for k in _MANUAL_KEYWORDS):
        return "MANUAL"
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

    # ML 피처 컬럼 목록 (기존 DB 자동 마이그레이션 대상)
    _ML_COLUMNS = [
        ("choch_grade",    "TEXT"),     # CHoCH 등급 (A/A+/A-/B/C) — 진입 품질
        ("market_regime",  "TEXT"),     # 진입 시 시장 레짐 (TREND/NEUTRAL/REVERSAL)
        ("rvol_at_entry",  "REAL"),     # 진입 시 상대거래량 (1.0 = 평균)
        ("mfe_pct",        "REAL"),     # 보유 기간 중 최대 유리 움직임 (%)
        ("mae_pct",        "REAL"),     # 보유 기간 중 최대 불리 움직임 (%)
    ]

    def _init_db(self):
        """테이블 없으면 생성, 기존 테이블은 ML 컬럼 마이그레이션"""
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
            # 기존 DB에 ML 컬럼 없으면 추가 (멱등 — 이미 있으면 무시)
            existing = {row[1] for row in conn.execute("PRAGMA table_info(trades)")}
            for col_name, col_type in self._ML_COLUMNS:
                if col_name not in existing:
                    conn.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")

    def insert(self, trade: dict) -> int:
        """
        trade dict 삽입. risk_log.json의 daily_trades 항목을 그대로 받는다.
        이미 동일 timestamp+stock_code가 있으면 무시 (멱등).

        ML 피처 필드 (선택):
            choch_grade    — CHoCH 등급 (A/A+/B/C)
            market_regime  — 진입 시 레짐 (TREND/NEUTRAL/REVERSAL)
            rvol_at_entry  — 진입 시 상대거래량
            mfe_pct        — 보유 중 최고 수익률 (%)
            mae_pct        — 보유 중 최대 손실폭 (%)

        Returns: inserted row id (0 if skipped)
        """
        ts = trade.get("timestamp", "")
        trade_date = ts[:10] if ts else datetime.now().strftime("%Y-%m-%d")
        strategy = _extract_strategy(trade.get("reason", ""))

        # ML 피처 (None이면 NULL 저장)
        def _f(key):
            v = trade.get(key)
            return float(v) if v is not None else None

        choch_grade   = trade.get("choch_grade") or None
        market_regime = trade.get("market_regime") or None
        rvol          = _f("rvol_at_entry")
        mfe           = _f("mfe_pct")
        mae           = _f("mae_pct")

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
                     trade_type, quantity, price, amount, realized_pnl, reason, strategy,
                     choch_grade, market_regime, rvol_at_entry, mfe_pct, mae_pct)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                trade_date, ts,
                trade["stock_code"],
                trade.get("stock_name", ""),
                trade["type"],
                int(trade.get("quantity", 0)),
                float(trade.get("price", 0)),
                float(trade.get("amount", 0)),
                float(trade.get("realized_pnl", 0) or 0),
                trade.get("reason", ""),
                strategy,
                choch_grade, market_regime, rvol, mfe, mae,
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
