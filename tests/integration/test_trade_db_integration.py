"""
tests/integration/test_trade_db_integration.py — 2차 통합 테스트

대상:
  - TradeDB (core/trade_db.py) : 실제 SQLite(:memory:) 사용
  - session_verify L1/L2/L3  : 실 파일 패치 후 검증

Kiwoom API / 외부 네트워크 없이 실행 가능.
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from core.trade_db import TradeDB, _extract_strategy
import analysis.session_verify as sv


# ──────────────────────────────────────────────────────────────
# 공통 헬퍼
# ──────────────────────────────────────────────────────────────

def _make_trade(
    stock_code: str = "082920",
    stock_name: str = "비츠로셀",
    trade_type: str = "BUY",
    price: float = 44_700,
    quantity: int = 10,
    realized_pnl: float = 0.0,
    reason: str = "SMC 매수",
    ts_offset_sec: int = 0,
    trade_date: str = "2026-05-02",
) -> dict:
    ts = f"{trade_date}T10:30:00"
    if ts_offset_sec:
        base = datetime.fromisoformat(ts) + timedelta(seconds=ts_offset_sec)
        ts = base.isoformat()
    return {
        "timestamp":    ts,
        "stock_code":   stock_code,
        "stock_name":   stock_name,
        "type":         trade_type,
        "price":        price,
        "quantity":     quantity,
        "amount":       price * quantity,
        "realized_pnl": realized_pnl,
        "reason":       reason,
    }


@pytest.fixture
def db(tmp_path):
    # ":memory:" creates a new DB per connection — use a temp file instead
    return TradeDB(db_path=str(tmp_path / "test.db"))


# ──────────────────────────────────────────────────────────────
# 1. TradeDB 기본 동작
# ──────────────────────────────────────────────────────────────

class TestTradeDBBasic:

    def test_insert_returns_positive_row_id(self, db):
        row_id = db.insert(_make_trade())
        assert row_id > 0

    def test_insert_and_query_retrieves_record(self, db):
        trade = _make_trade(stock_code="082920", price=44_700)
        db.insert(trade)
        rows = db.query(days=30, stock_code="082920")
        assert len(rows) == 1
        assert rows[0]["price"] == 44_700
        assert rows[0]["trade_type"] == "BUY"

    def test_query_respects_date_window(self, db):
        """30일 이내 거래만 반환"""
        old_date = (datetime.now() - timedelta(days=60)).strftime("%Y-%m-%d")
        recent_date = datetime.now().strftime("%Y-%m-%d")
        db.insert(_make_trade(trade_date=old_date))
        db.insert(_make_trade(stock_code="000660", trade_date=recent_date))
        rows = db.query(days=30)
        assert all(r["stock_code"] == "000660" for r in rows)

    def test_multiple_different_trades_stored(self, db):
        db.insert(_make_trade(stock_code="082920", trade_type="BUY",  ts_offset_sec=0))
        db.insert(_make_trade(stock_code="082920", trade_type="SELL", ts_offset_sec=3600,
                               price=55_000, realized_pnl=103_000))
        rows = db.query(days=30, stock_code="082920")
        assert len(rows) == 2
        types = {r["trade_type"] for r in rows}
        assert types == {"BUY", "SELL"}


# ──────────────────────────────────────────────────────────────
# 2. 중복 방지 (멱등성)
# ──────────────────────────────────────────────────────────────

class TestTradeDBDeduplication:

    def test_exact_duplicate_returns_zero(self, db):
        trade = _make_trade()
        first  = db.insert(trade)
        second = db.insert(trade)
        assert first > 0
        assert second == 0

    def test_same_code_different_type_not_duplicate(self, db):
        buy  = _make_trade(trade_type="BUY",  ts_offset_sec=0)
        sell = _make_trade(trade_type="SELL", ts_offset_sec=3600)
        r1 = db.insert(buy)
        r2 = db.insert(sell)
        assert r1 > 0
        assert r2 > 0

    def test_same_type_different_timestamp_not_duplicate(self, db):
        t1 = _make_trade(stock_code="082920", ts_offset_sec=0)
        t2 = _make_trade(stock_code="082920", ts_offset_sec=1)  # 1초 뒤
        r1 = db.insert(t1)
        r2 = db.insert(t2)
        assert r1 > 0
        assert r2 > 0

    def test_insert_twice_only_one_row_in_db(self, db):
        trade = _make_trade()
        db.insert(trade)
        db.insert(trade)
        rows = db.query(days=30)
        assert len(rows) == 1


# ──────────────────────────────────────────────────────────────
# 3. strategy 추출 로직
# ──────────────────────────────────────────────────────────────

class TestStrategyExtraction:

    @pytest.mark.parametrize("reason,expected", [
        ("SMC 돌파 진입",          "SMC"),
        ("EXPLORATION 랠리",       "EXPLORATION"),
        ("TREND 강세 진입",        "TREND"),
        ("오버나이트 강제청산",     "EXIT"),
        ("Hard Stop 발동",         "EXIT"),
        ("Trailing Stop 익절",     "EXIT"),
        ("",                       "UNKNOWN"),
        ("전혀 관계없는 이유",      "UNKNOWN"),
    ])
    def test_extract_strategy(self, reason, expected):
        assert _extract_strategy(reason) == expected

    def test_strategy_stored_in_db(self, db):
        db.insert(_make_trade(reason="SMC 진입 확정"))
        rows = db.query(days=30)
        assert rows[0]["strategy"] == "SMC"


# ──────────────────────────────────────────────────────────────
# 4. session_verify L1 — DB 무결성 검사
# ──────────────────────────────────────────────────────────────

@pytest.fixture
def temp_db_with_data(tmp_path):
    """실제 SQLite 파일에 알려진 데이터 삽입 → session_verify.check_db_integrity 테스트용"""
    db_path = tmp_path / "test_trades.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT, timestamp TEXT, stock_code TEXT, stock_name TEXT,
            trade_type TEXT, quantity INTEGER, price REAL, amount REAL,
            realized_pnl REAL DEFAULT 0.0, reason TEXT, strategy TEXT
        )
    """)
    conn.commit()
    return db_path, conn


def _insert_row(conn, date, code, trade_type, price=10000, pnl=0.0):
    ts = f"{date}T10:30:00"
    conn.execute(
        "INSERT INTO trades (trade_date,timestamp,stock_code,stock_name,trade_type,"
        "quantity,price,amount,realized_pnl) VALUES (?,?,?,?,?,?,?,?,?)",
        (date, ts, code, "테스트", trade_type, 10, price, price*10, pnl)
    )
    conn.commit()


class TestSessionVerifyL1:

    def test_clean_data_no_issues(self, temp_db_with_data):
        db_path, conn = temp_db_with_data
        date = "2026-05-02"
        _insert_row(conn, date, "082920", "BUY",  44_700)
        _insert_row(conn, date, "082920", "SELL", 55_000, pnl=103_000)

        with patch.object(sv, '_DB_PATH', db_path):
            result = sv.check_db_integrity(date)

        assert result['duplicate_signals'] == []
        assert result['null_prices'] == []
        assert result['unclosed_positions'] == []
        assert result['orphan_sells']       == []

    def test_detects_unclosed_position(self, temp_db_with_data):
        db_path, conn = temp_db_with_data
        date = "2026-05-02"
        _insert_row(conn, date, "082920", "BUY")   # BUY만, SELL 없음

        with patch.object(sv, '_DB_PATH', db_path):
            result = sv.check_db_integrity(date)

        assert "082920" in result['unclosed_positions']

    def test_detects_null_price(self, temp_db_with_data):
        db_path, conn = temp_db_with_data
        date = "2026-05-02"
        conn.execute(
            "INSERT INTO trades (trade_date,timestamp,stock_code,stock_name,trade_type,"
            "quantity,price,amount) VALUES (?,?,?,?,?,?,?,?)",
            (date, f"{date}T10:30:00", "082920", "테스트", "BUY", 10, 0, 0)
        )
        conn.commit()

        with patch.object(sv, '_DB_PATH', db_path):
            result = sv.check_db_integrity(date)

        assert len(result['null_prices']) == 1
        assert result['null_prices'][0]['stock_code'] == "082920"

    def test_detects_duplicate_signal(self, temp_db_with_data):
        db_path, conn = temp_db_with_data
        date = "2026-05-02"
        # 동일 종목 동일 타입 2건
        _insert_row(conn, date, "082920", "BUY")
        _insert_row(conn, date, "082920", "BUY")

        with patch.object(sv, '_DB_PATH', db_path):
            result = sv.check_db_integrity(date)

        assert len(result['duplicate_signals']) == 1
        assert result['duplicate_signals'][0]['cnt'] == 2

    def test_daily_summary_pnl(self, temp_db_with_data):
        db_path, conn = temp_db_with_data
        date = "2026-05-02"
        _insert_row(conn, date, "082920", "BUY",  44_700)
        _insert_row(conn, date, "082920", "SELL", 55_000, pnl=103_000)

        with patch.object(sv, '_DB_PATH', db_path):
            result = sv.check_db_integrity(date)

        summary = result['daily_summary']
        assert summary['SELL']['pnl'] == pytest.approx(103_000)
        assert summary['BUY']['count'] == 1
        assert summary['SELL']['count'] == 1


# ──────────────────────────────────────────────────────────────
# 5. session_verify L2 — drift_detector 일관성
# ──────────────────────────────────────────────────────────────

class TestSessionVerifyL2:

    def _make_drift(self, tmp_path, date: str, stocks: list[str]) -> Path:
        drift_path = tmp_path / "drift_detector_state.json"
        drift_data = {
            "drift_level": "OK",
            "recent_trades": [
                {"timestamp": f"{date}T11:00:00", "stock": code}
                for code in stocks
            ]
        }
        drift_path.write_text(json.dumps(drift_data))
        return drift_path

    def test_consistent_when_db_and_drift_match(self, tmp_path, temp_db_with_data):
        db_path, conn = temp_db_with_data
        date = "2026-05-02"
        _insert_row(conn, date, "082920", "SELL", pnl=103_000)
        drift_path = self._make_drift(tmp_path, date, ["082920"])

        with patch.object(sv, '_DB_PATH', db_path), \
             patch.object(sv, '_DRIFT',   drift_path):
            result = sv.check_drift_consistency(date)

        assert result['consistent'] is True
        assert result['missing_in_drift'] == []

    def test_detects_missing_in_drift(self, tmp_path, temp_db_with_data):
        db_path, conn = temp_db_with_data
        date = "2026-05-02"
        _insert_row(conn, date, "082920", "SELL", pnl=103_000)
        # drift에는 다른 종목만 기록
        drift_path = self._make_drift(tmp_path, date, ["000660"])

        with patch.object(sv, '_DB_PATH', db_path), \
             patch.object(sv, '_DRIFT',   drift_path):
            result = sv.check_drift_consistency(date)

        assert result['consistent'] is False
        assert "082920" in result['missing_in_drift']

    def test_missing_drift_file_returns_error(self, tmp_path, temp_db_with_data):
        db_path, conn = temp_db_with_data
        nonexistent = tmp_path / "no_such_file.json"

        with patch.object(sv, '_DB_PATH', db_path), \
             patch.object(sv, '_DRIFT',   nonexistent):
            result = sv.check_drift_consistency("2026-05-02")

        assert 'error' in result


# ──────────────────────────────────────────────────────────────
# 6. session_verify L3 — 로그 신호 카운팅
# ──────────────────────────────────────────────────────────────

class TestSessionVerifyL3:

    def _write_log(self, log_dir: Path, date: str, lines: list[str]) -> None:
        log_file = log_dir / f"auto_trading_{date.replace('-','')}.log"
        log_file.write_text("\n".join(lines))

    def test_counts_buy_and_sell(self, tmp_path):
        date = "20260502"
        lines = [
            "10:30:00 [SMC_SIG] 082920 진입 신호",
            "10:31:00 매수완료 082920 44700원",
            "14:00:00 매도완료 082920 55000원 PnL=+23.0%",
        ]
        log_dir = tmp_path
        self._write_log(log_dir, date, lines)

        with patch.object(sv, '_LOG_DIR', tmp_path):
            result = sv.check_log_signals(date)

        assert result['counters']['smc_sig']      == 1
        assert result['counters']['buy_complete']  == 1
        assert result['counters']['sell_complete'] == 1

    def test_detects_kill_switch(self, tmp_path):
        date = "20260502"
        lines = [
            "14:52:00 [KILL_SWITCH_ON] 토큰 3회 실패",
            "14:52:00 [SELL_TOKEN_FAIL_3X] 082920",
        ]
        self._write_log(tmp_path, date, lines)

        with patch.object(sv, '_LOG_DIR', tmp_path):
            result = sv.check_log_signals(date)

        assert result['counters']['kill_switch_on']    == 1
        assert result['counters']['sell_token_fail3x'] == 1
        assert any("KILL_SWITCH" in a for a in result['anomalies'])

    def test_no_log_file_returns_error(self, tmp_path):
        with patch.object(sv, '_LOG_DIR', tmp_path):
            result = sv.check_log_signals("20991231")

        assert 'error' in result

    def test_signal_exec_ratio_calculated(self, tmp_path):
        date = "20260502"
        lines = (
            ["[SMC_SIG] 진입"] * 4
            + ["매수완료"] * 2
        )
        self._write_log(tmp_path, date, lines)

        with patch.object(sv, '_LOG_DIR', tmp_path):
            result = sv.check_log_signals(date)

        assert result['signal_exec_ratio'] == pytest.approx(50.0)


# ──────────────────────────────────────────────────────────────
# 7. run_verification 통합 — 이슈 없는 정상 세션
# ──────────────────────────────────────────────────────────────

class TestRunVerificationEndToEnd:

    def test_clean_session_returns_ok(self, tmp_path):
        db_path = tmp_path / "trades.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT, timestamp TEXT, stock_code TEXT, stock_name TEXT,
                trade_type TEXT, quantity INTEGER, price REAL, amount REAL,
                realized_pnl REAL DEFAULT 0.0, reason TEXT, strategy TEXT
            )
        """)
        date = "2026-05-02"
        _insert_row(conn, date, "082920", "BUY",  44_700)
        _insert_row(conn, date, "082920", "SELL", 55_000, pnl=103_000)

        drift_path = tmp_path / "drift.json"
        drift_path.write_text(json.dumps({
            "drift_level": "OK",
            "recent_trades": [{"timestamp": f"{date}T11:00:00", "stock": "082920"}]
        }))

        # run_verification passes date="2026-05-02" to check_log_signals, so filename keeps dashes
        log_file = tmp_path / f"auto_trading_{date}.log"
        log_file.write_text(
            "[SMC_SIG] 082920\n매수완료 082920\n매도완료 082920"
        )

        with patch.object(sv, '_DB_PATH', db_path), \
             patch.object(sv, '_DRIFT',   drift_path), \
             patch.object(sv, '_LOG_DIR', tmp_path):
            result = sv.run_verification(date)

        assert result['status'] == "✅ OK"
        assert result['issues'] == []

    def test_issues_detected_returns_issue_status(self, tmp_path):
        db_path = tmp_path / "trades.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
            CREATE TABLE trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_date TEXT, timestamp TEXT, stock_code TEXT, stock_name TEXT,
                trade_type TEXT, quantity INTEGER, price REAL, amount REAL,
                realized_pnl REAL DEFAULT 0.0, reason TEXT, strategy TEXT
            )
        """)
        date = "2026-05-02"
        # BUY만 있고 SELL 없음 → 미청산 이슈
        _insert_row(conn, date, "082920", "BUY")

        drift_path = tmp_path / "drift.json"
        drift_path.write_text(json.dumps({"drift_level": "OK", "recent_trades": []}))

        # run_verification passes date as-is ("2026-05-02") to check_log_signals
        (tmp_path / f"auto_trading_{date}.log").write_text("[KILL_SWITCH_ON]")

        with patch.object(sv, '_DB_PATH', db_path), \
             patch.object(sv, '_DRIFT',   drift_path), \
             patch.object(sv, '_LOG_DIR', tmp_path):
            result = sv.run_verification(date)

        assert result['status'] == "⚠️ ISSUE"
        assert len(result['issues']) >= 2  # 미청산 + KILL_SWITCH
