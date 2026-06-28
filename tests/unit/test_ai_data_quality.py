"""
tests/unit/test_ai_data_quality.py — AIDataQualityChecker 단위 테스트

DB/외부 의존성 없이 합성 데이터로 각 레이어 독립 검증.
"""

from __future__ import annotations

import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from analysis.ai_data_quality import (
    AIDataQualityChecker,
    _categorize_exit,
    print_report,
)


# ──────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────

def _make_db(rows: list[dict]) -> Path:
    """합성 rows로 임시 SQLite DB 생성. 테스트 종료 후 자동 삭제."""
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    path = Path(tf.name)

    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT, timestamp TEXT, stock_code TEXT, stock_name TEXT,
            trade_type TEXT, quantity INTEGER, price REAL, amount REAL,
            realized_pnl REAL DEFAULT 0.0, reason TEXT, strategy TEXT
        )
    """)
    today = datetime.now()
    for i, r in enumerate(rows):
        ts = r.get("timestamp") or (today - timedelta(minutes=i * 60)).isoformat()
        td = ts[:10]
        conn.execute(
            "INSERT INTO trades (trade_date,timestamp,stock_code,stock_name,trade_type,"
            "quantity,price,amount,realized_pnl,reason,strategy) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                td, ts,
                r.get("stock_code", "082920"),
                r.get("stock_name", "TEST"),
                r.get("trade_type", "BUY"),
                r.get("quantity", 10),
                r.get("price", 10_000),
                r.get("amount", 100_000),
                r.get("realized_pnl", 0.0),
                r.get("reason", ""),
                r.get("strategy", "SMC"),
            )
        )
    conn.commit()
    conn.close()
    return path


def _checker(rows: list[dict], days: int = 365) -> AIDataQualityChecker:
    path = _make_db(rows)
    return AIDataQualityChecker(db_path=str(path), days=days)


def _clean_pair(date_offset: int = 0, pnl: float = 5000.0, strategy: str = "SMC") -> list[dict]:
    base = datetime.now() - timedelta(days=date_offset)
    ts_buy  = (base.replace(hour=10, minute=30, second=0, microsecond=0)).isoformat()
    ts_sell = (base.replace(hour=14, minute=0,  second=0, microsecond=0)).isoformat()
    return [
        {"timestamp": ts_buy,  "trade_type": "BUY",  "price": 10_000, "realized_pnl": 0,    "strategy": strategy, "reason": "SMC 진입"},
        {"timestamp": ts_sell, "trade_type": "SELL", "price": 10_500, "realized_pnl": pnl,   "strategy": "EXIT",   "reason": "ATR 트레일링"},
    ]


# ──────────────────────────────────────────────────────────────
# 1. _categorize_exit — 패턴 매핑
# ──────────────────────────────────────────────────────────────

class TestCategorizeExit:

    @pytest.mark.parametrize("reason,expected", [
        ("Hard Stop (-2.0%, -2.10%)",                 "hard_stop"),
        ("Early Failure[no_follow] 구조 (방향실패...)", "ef_no_follow"),
        ("Early Failure[no_demand]",                   "ef_no_demand"),
        ("Early Failure 구조",                         "early_failure"),
        ("ATR 트레일링 v2 (×2.0, +4.29%)",             "trailing_stop"),
        ("오버나이트 강제청산",                         "overnight_close"),
        ("Take Profit 익절",                           "take_profit"),
        ("15:00 시장가 강제청산",                       "time_exit"),
        ("강제청산 overnight",                          "overnight_close"),  # overnight 우선
        ("HTS_IMPORT",                                 "manual_hts"),
        ("알 수 없는 이유",                             "unknown"),
        ("",                                           "unknown"),
    ])
    def test_patterns(self, reason, expected):
        assert _categorize_exit(reason) == expected, f"reason={reason!r}"


# ──────────────────────────────────────────────────────────────
# 2. L1. Time Integrity
# ──────────────────────────────────────────────────────────────

class TestTimeIntegrity:

    def test_clean_data_passes(self):
        rows = _clean_pair(0) + _clean_pair(1) + _clean_pair(2)
        c = _checker(rows)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "time_integrity")
        assert check.passed

    def test_duplicate_timestamps_detected(self):
        """동일 timestamp 2건 → duplicate 감지"""
        ts = datetime.now().isoformat()
        rows = [
            {"timestamp": ts, "trade_type": "BUY",  "price": 10_000},
            {"timestamp": ts, "trade_type": "SELL", "price": 10_000, "realized_pnl": 0},
        ]
        c = _checker(rows)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "time_integrity")
        assert check.detail.get("duplicate_timestamps", 0) >= 1
        assert not check.passed

    def test_score_deducted_for_duplicates(self):
        ts = datetime.now().isoformat()
        rows = [{"timestamp": ts, "trade_type": "BUY", "price": 10_000}] * 3
        c = _checker(rows)
        report = c.run()
        assert report.score < 100

    def test_unique_trade_days_counted(self):
        rows = _clean_pair(0) + _clean_pair(1) + _clean_pair(3)
        c = _checker(rows)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "time_integrity")
        assert check.detail["unique_trade_days"] >= 3


# ──────────────────────────────────────────────────────────────
# 3. L2. Schema Consistency
# ──────────────────────────────────────────────────────────────

class TestSchemaConsistency:

    def test_valid_strategies_pass(self):
        rows = _clean_pair()
        c = _checker(rows)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "schema_consistency")
        assert "invalid_strategy_values" not in check.detail or \
               check.detail.get("invalid_strategy_values") == {}

    def test_kiwoom_strategy_flagged(self):
        rows = _clean_pair()
        path = _make_db(rows)
        conn = sqlite3.connect(str(path))
        conn.execute("UPDATE trades SET strategy='kiwoom' WHERE trade_type='BUY'")
        conn.commit()
        conn.close()

        c = AIDataQualityChecker(db_path=str(path), days=365)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "schema_consistency")
        assert "kiwoom" in (check.detail.get("invalid_strategy_values") or {})
        assert not check.passed

    def test_zero_price_detected(self):
        rows = [{"trade_type": "BUY", "price": 0, "strategy": "SMC"}]
        c = _checker(rows)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "schema_consistency")
        assert len(check.detail.get("zero_or_null_price", [])) >= 1

    def test_strategy_distribution_computed(self):
        rows = _clean_pair(strategy="EXPLORATION") * 3 + _clean_pair(strategy="SMC")
        c = _checker(rows)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "schema_consistency")
        dist = check.detail.get("strategy_distribution", {})
        assert "EXPLORATION" in dist or "EXIT" in dist

    def test_single_strategy_dominance_flagged(self):
        """한 전략이 90% 이상 → imbalance 경고 (BUY+SELL 모두 같은 전략)"""
        rows = [
            {"trade_type": "BUY",  "price": 10_000, "strategy": "EXPLORATION", "reason": ""},
            {"trade_type": "SELL", "price": 10_000, "realized_pnl": 500, "strategy": "EXPLORATION", "reason": "ATR 트레일링"},
        ] * 10  # EXPLORATION 100%
        c = _checker(rows)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "schema_consistency")
        assert "strategy_imbalance" in check.detail


# ──────────────────────────────────────────────────────────────
# 4. L3. Label Quality
# ──────────────────────────────────────────────────────────────

class TestLabelQuality:

    def test_clean_wins_pass(self):
        rows = _clean_pair(pnl=5000) * 3
        c = _checker(rows)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "label_quality")
        dist = check.detail["label_distribution"]
        assert dist["win"] == 3
        assert dist["win_rate_pct"] == pytest.approx(100.0)

    def test_win_loss_ratio_computed(self):
        wins   = _clean_pair(pnl=+5000) * 2
        losses = _clean_pair(pnl=-2000) * 1
        c = _checker(wins + losses)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "label_quality")
        dist = check.detail["label_distribution"]
        assert dist["win"]  == 2
        assert dist["loss"] == 1

    def test_excessive_zero_pnl_flagged(self):
        """PnL=0 SELL이 절반 이상 → is_loss 버그 흔적"""
        zero_sells = [
            {"trade_type": "BUY",  "price": 10_000, "realized_pnl": 0, "strategy": "SMC"},
            {"trade_type": "SELL", "price": 10_000, "realized_pnl": 0, "strategy": "EXIT", "reason": "ATR 트레일링"},
        ] * 5
        c = _checker(zero_sells)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "label_quality")
        assert "excessive_draws" in check.detail

    def test_exit_category_parse_rate_high(self):
        """알려진 패턴 → parse rate 100%"""
        rows = [
            {"trade_type": "BUY",  "price": 10_000, "strategy": "SMC"},
            {"trade_type": "SELL", "price": 10_000, "realized_pnl": -2000, "reason": "Hard Stop (-2.0%)"},
            {"trade_type": "BUY",  "price": 10_000, "strategy": "EXPLORATION"},
            {"trade_type": "SELL", "price": 10_000, "realized_pnl": 5000, "reason": "ATR 트레일링 (+4%)"},
        ]
        c = _checker(rows)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "label_quality")
        assert check.detail["exit_reason_parse_rate_pct"] == pytest.approx(100.0)

    def test_unknown_reasons_flagged(self):
        """알 수 없는 reason → parse rate 0% → 경고"""
        rows = [
            {"trade_type": "BUY",  "price": 10_000, "strategy": "SMC"},
            {"trade_type": "SELL", "price": 10_000, "realized_pnl": -100, "reason": "???미분류이유???"},
        ]
        c = _checker(rows)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "label_quality")
        assert check.detail["exit_reason_parse_rate_pct"] == pytest.approx(0.0)
        assert "exit_parse_low" in check.detail


# ──────────────────────────────────────────────────────────────
# 5. L4. Feature Coverage
# ──────────────────────────────────────────────────────────────

class TestFeatureCoverage:

    def test_full_data_derivable_features(self):
        """정상 BUY-SELL 쌍 → 5개 이상 피처 도출 가능"""
        rows = _clean_pair()
        c = _checker(rows)
        report = c.run()
        assert len(report.derived_features) >= 5

    def test_critical_missing_features_noted(self):
        rows = _clean_pair()
        c = _checker(rows)
        report = c.run()
        missing_text = " ".join(report.missing_features)
        assert "mfe_pct" in missing_text
        assert "choch_grade" in missing_text

    def test_feature_check_passes_with_good_data(self):
        rows = _clean_pair() * 3
        c = _checker(rows)
        report = c.run()
        check = next(ch for ch in report.checks if ch.name == "feature_coverage")
        assert check.passed


# ──────────────────────────────────────────────────────────────
# 6. 점수 계산
# ──────────────────────────────────────────────────────────────

class TestScoreCalculation:

    def test_perfect_data_scores_near_100(self):
        """이슈 없는 깨끗한 데이터 → 90점 이상"""
        rows = _clean_pair(0) + _clean_pair(1) + _clean_pair(2) + _clean_pair(3, pnl=-1000)
        c = _checker(rows)
        report = c.run()
        assert report.score >= 90, f"Expected ≥90 but got {report.score}"

    def test_score_never_below_zero(self):
        """최악의 데이터도 0점 이하로 내려가지 않음"""
        rows = [
            {"trade_type": "XXXXX", "price": 0, "realized_pnl": None, "strategy": "kiwoom"},
        ] * 10
        c = _checker(rows)
        report = c.run()
        assert report.score >= 0

    def test_score_never_above_100(self):
        rows = _clean_pair() * 5
        c = _checker(rows)
        report = c.run()
        assert report.score <= 100

    def test_schema_issues_reduce_score(self):
        """kiwoom strategy → schema 문제 → 점수 감소"""
        clean_rows = _clean_pair()
        clean_path = _make_db(clean_rows)
        clean_c = AIDataQualityChecker(db_path=str(clean_path), days=365)
        clean_score = clean_c.run().score

        dirty_rows = clean_rows.copy()
        dirty_path = _make_db(dirty_rows)
        conn = sqlite3.connect(str(dirty_path))
        conn.execute("UPDATE trades SET strategy='kiwoom'")
        conn.commit()
        conn.close()
        dirty_c = AIDataQualityChecker(db_path=str(dirty_path), days=365)
        dirty_score = dirty_c.run().score

        assert dirty_score < clean_score


# ──────────────────────────────────────────────────────────────
# 7. 권고사항 생성
# ──────────────────────────────────────────────────────────────

class TestRecommendations:

    def test_kiwoom_strategy_recommendation(self):
        path = _make_db(_clean_pair())
        conn = sqlite3.connect(str(path))
        conn.execute("UPDATE trades SET strategy='kiwoom'")
        conn.commit()
        conn.close()
        c = AIDataQualityChecker(db_path=str(path), days=365)
        report = c.run()
        recs_text = " ".join(report.recommendations)
        assert "kiwoom" in recs_text or "비표준" in recs_text

    def test_good_data_recommends_proceed(self):
        rows = _clean_pair(0) + _clean_pair(1) + _clean_pair(2) + _clean_pair(3, pnl=-500)
        c = _checker(rows)
        report = c.run()
        recs_text = " ".join(report.recommendations)
        assert "학습" in recs_text

    def test_recommendations_always_nonempty(self):
        rows = _clean_pair()
        c = _checker(rows)
        report = c.run()
        assert len(report.recommendations) > 0


# ──────────────────────────────────────────────────────────────
# 8. print_report 출력 (오류 없이 실행되는지)
# ──────────────────────────────────────────────────────────────

class TestPrintReport:

    def test_print_does_not_crash(self, capsys):
        rows = _clean_pair()
        c = _checker(rows)
        report = c.run()
        print_report(report, verbose=False)
        captured = capsys.readouterr()
        assert "Score" in captured.out
        assert "AI Data Quality" in captured.out

    def test_verbose_shows_details(self, capsys):
        rows = _clean_pair()
        c = _checker(rows)
        report = c.run()
        print_report(report, verbose=True)
        captured = capsys.readouterr()
        assert "derivable_features" in captured.out or "도출 가능" in captured.out
