"""
tests/unit/test_trade_decision_audit.py — SQS scoring unit tests

Coverage:
  T1  TradePair properties (hold_min, profit_pct, mfe_capture_rate)
  T2  _score_entry — grade/regime/rvol combinations + None fallbacks
  T3  _score_hold — bucket boundary values
  T4  _score_exit — base scores + MFE capture adjustment
  T5  _assign_flags — each flag condition
  T6  score_pair — composite, range 0-100
  T7  _aggregate — distributions, swing_rate, grade_dist
  T8  _make_alerts — threshold triggers
  T9  _load_pairs — FIFO matching, MANUAL exclusion, reversed-ts skip
  T10 TradeDecisionAudit.run — empty DB, full run
"""

import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from analysis.trade_decision_audit import (
    AuditReport,
    TradePair,
    TradeScore,
    _assign_flags,
    _load_pairs,
    _make_alerts,
    _score_entry,
    _score_exit,
    _score_hold,
    _aggregate,
    score_pair,
    TradeDecisionAudit,
)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────

def _pair(
    hold_hours: float = 4.0,
    entry_price: float = 10_000,
    exit_price: float = 10_500,
    exit_reason: str = "Trailing Stop",
    exit_category: str = "trailing_stop",
    choch_grade: str = "A",
    market_regime: str = "TREND",
    rvol: float = 2.5,
    mfe_pct: float = None,
    mae_pct: float = None,
    strategy: str = "SMC",
    buy_ts: datetime = None,
    sell_ts: datetime = None,
) -> TradePair:
    now = buy_ts or datetime(2026, 5, 1, 10, 0)
    return TradePair(
        stock_code="000001",
        stock_name="테스트",
        buy_ts=now,
        sell_ts=sell_ts or (now + timedelta(hours=hold_hours)),
        entry_price=entry_price,
        exit_price=exit_price,
        realized_pnl=float(exit_price - entry_price),
        exit_reason=exit_reason,
        exit_category=exit_category,
        strategy=strategy,
        choch_grade=choch_grade,
        market_regime=market_regime,
        rvol_at_entry=rvol,
        mfe_pct=mfe_pct,
        mae_pct=mae_pct,
    )


def _score(pair: TradePair = None, **kwargs) -> TradeScore:
    return score_pair(pair or _pair(**kwargs))


def _make_db(tmp_path: Path) -> Path:
    """빈 trades.db를 tmp_path에 생성. 스키마만 있음."""
    db = tmp_path / "trades.db"
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_date TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            stock_code TEXT NOT NULL,
            stock_name TEXT NOT NULL,
            trade_type TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            price REAL NOT NULL,
            amount REAL NOT NULL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            reason TEXT,
            strategy TEXT,
            choch_grade TEXT,
            market_regime TEXT,
            rvol_at_entry REAL,
            mfe_pct REAL,
            mae_pct REAL,
            created_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)
    conn.commit()
    conn.close()
    return db


def _insert_trade(db: Path, **kwargs):
    defaults = dict(
        trade_date="2026-05-01",
        timestamp="2026-05-01T10:00:00",
        stock_code="000001",
        stock_name="테스트",
        trade_type="BUY",
        quantity=10,
        price=10000.0,
        amount=100000.0,
        realized_pnl=0.0,
        reason="TREND BREAKOUT",
        strategy="SMC",
        choch_grade=None,
        market_regime=None,
        rvol_at_entry=None,
        mfe_pct=None,
        mae_pct=None,
    )
    defaults.update(kwargs)
    conn = sqlite3.connect(str(db))
    conn.execute("""
        INSERT INTO trades
          (trade_date, timestamp, stock_code, stock_name,
           trade_type, quantity, price, amount, realized_pnl, reason, strategy,
           choch_grade, market_regime, rvol_at_entry, mfe_pct, mae_pct)
        VALUES
          (:trade_date, :timestamp, :stock_code, :stock_name,
           :trade_type, :quantity, :price, :amount, :realized_pnl, :reason, :strategy,
           :choch_grade, :market_regime, :rvol_at_entry, :mfe_pct, :mae_pct)
    """, defaults)
    conn.commit()
    conn.close()


# ──────────────────────────────────────────────────────────────
# T1 — TradePair properties
# ──────────────────────────────────────────────────────────────

class TestTradePairProperties:

    def test_hold_min_basic(self):
        buy = datetime(2026, 5, 1, 10, 0)
        sell = datetime(2026, 5, 1, 12, 30)
        p = _pair(buy_ts=buy, sell_ts=sell)
        assert p.hold_min == pytest.approx(150.0)

    def test_hold_min_multi_day(self):
        buy = datetime(2026, 5, 1, 10, 0)
        sell = datetime(2026, 5, 3, 10, 0)
        p = _pair(buy_ts=buy, sell_ts=sell)
        assert p.hold_min == pytest.approx(2880.0)

    def test_profit_pct_gain(self):
        p = _pair(entry_price=10_000, exit_price=11_000)
        assert p.profit_pct == pytest.approx(10.0)

    def test_profit_pct_loss(self):
        p = _pair(entry_price=10_000, exit_price=9_500)
        assert p.profit_pct == pytest.approx(-5.0)

    def test_profit_pct_zero_entry(self):
        p = _pair(entry_price=0, exit_price=10_000)
        assert p.profit_pct == 0.0

    def test_mfe_capture_rate_normal(self):
        p = _pair(entry_price=10_000, exit_price=10_500, mfe_pct=10.0)
        # profit_pct = 5%, mfe_pct = 10%  → capture = 0.5
        assert p.mfe_capture_rate == pytest.approx(0.5)

    def test_mfe_capture_rate_none_when_mfe_missing(self):
        p = _pair(mfe_pct=None)
        assert p.mfe_capture_rate is None

    def test_mfe_capture_rate_none_when_mfe_near_zero(self):
        p = _pair(mfe_pct=0.03)
        assert p.mfe_capture_rate is None

    def test_mfe_capture_rate_over_one(self):
        """손실 발생해도 mfe > 0이면 capture < 0"""
        p = _pair(entry_price=10_000, exit_price=9_800, mfe_pct=2.0)
        assert p.mfe_capture_rate == pytest.approx(-1.0)


# ──────────────────────────────────────────────────────────────
# T2 — _score_entry
# ──────────────────────────────────────────────────────────────

class TestScoreEntry:

    def test_best_combination(self):
        # A+, TREND, 3.0x rvol → 15 + 10 + 15 = 40
        p = _pair(choch_grade="A+", market_regime="TREND", rvol=3.0)
        assert _score_entry(p) == 40.0

    def test_worst_with_data(self):
        # C, REVERSAL, 0.5x rvol → 3 + 0 + 0 = 3
        p = _pair(choch_grade="C", market_regime="REVERSAL", rvol=0.5)
        assert _score_entry(p) == 3.0

    def test_grade_a_scores(self):
        assert _score_entry(_pair(choch_grade="A", market_regime="NEUTRAL", rvol=2.0)) == pytest.approx(12 + 5 + 10)

    def test_grade_b_scores(self):
        assert _score_entry(_pair(choch_grade="B", market_regime="NEUTRAL", rvol=1.5)) == pytest.approx(6 + 5 + 6)

    def test_none_fallbacks_equal_neutral(self):
        # None grade → 5, None regime → 5, None rvol → 7 = 17
        p = _pair(choch_grade=None, market_regime=None, rvol=None)
        p.rvol_at_entry = None
        assert _score_entry(p) == pytest.approx(5 + 5 + 7)

    def test_unknown_grade_gets_fallback(self):
        p = _pair(choch_grade="X")
        # unknown grade → fallback 4 in _grade_pts.get(, 4)
        score = _score_entry(p)
        assert score >= 4

    def test_rvol_boundary_2x(self):
        p = _pair(choch_grade=None, market_regime=None, rvol=2.0)
        p.choch_grade = None
        p.market_regime = None
        # rvol 2.0 → 10pts
        score = _score_entry(p)
        assert score == pytest.approx(5 + 5 + 10)

    def test_rvol_boundary_1x(self):
        p = _pair(choch_grade=None, market_regime=None, rvol=1.0)
        p.choch_grade = None
        p.market_regime = None
        assert _score_entry(p) == pytest.approx(5 + 5 + 3)

    def test_score_capped_at_40(self):
        p = _pair(choch_grade="A+", market_regime="TREND", rvol=99.0)
        assert _score_entry(p) <= 40.0


# ──────────────────────────────────────────────────────────────
# T3 — _score_hold (bucket boundaries)
# ──────────────────────────────────────────────────────────────

class TestScoreHold:

    @pytest.mark.parametrize("minutes,expected_pts,expected_label", [
        (2,     0,  "SCALP(<5m)"),
        (4.9,   0,  "SCALP(<5m)"),
        (5,     5,  "SHORT(5-15m)"),
        (14.9,  5,  "SHORT(5-15m)"),
        (15,   12,  "MARGINAL(15-30m)"),
        (29,   12,  "MARGINAL(15-30m)"),
        (30,   22,  "INTRADAY(30m-2h)"),
        (119,  22,  "INTRADAY(30m-2h)"),
        (120,  30,  "SWING(2-8h)"),
        (479,  30,  "SWING(2-8h)"),
        (480,  28,  "MULTI_SESSION(8h-2d)"),
        (2879, 28,  "MULTI_SESSION(8h-2d)"),
        (2880, 22,  "LONGHOLD(>2d)"),
        (10000, 22, "LONGHOLD(>2d)"),
    ])
    def test_bucket(self, minutes, expected_pts, expected_label):
        buy = datetime(2026, 5, 1, 10, 0)
        sell = buy + timedelta(minutes=minutes)
        p = _pair(buy_ts=buy, sell_ts=sell)
        pts, label = _score_hold(p)
        assert pts == expected_pts, f"hold={minutes}m → expected {expected_pts}, got {pts}"
        assert label == expected_label


# ──────────────────────────────────────────────────────────────
# T4 — _score_exit
# ──────────────────────────────────────────────────────────────

class TestScoreExit:

    def test_trailing_stop_no_mfe(self):
        p = _pair(exit_category="trailing_stop", mfe_pct=None)
        assert _score_exit(p) == 30.0

    def test_hard_stop_no_mfe(self):
        p = _pair(exit_category="hard_stop", mfe_pct=None)
        assert _score_exit(p) == 0.0

    def test_mfe_capture_bonus_high(self):
        # trailing_stop base=30 + mfe_cap>=70% +5 → 35, capped at 30
        p = _pair(exit_category="trailing_stop",
                  entry_price=10_000, exit_price=10_800, mfe_pct=10.0)
        # profit=8%, mfe=10% → cap=0.8 → +5 → 35 → capped 30
        assert _score_exit(p) == 30.0

    def test_mfe_capture_penalty_low(self):
        # stop_loss base=5, mfe_cap<20% → -10 → -5 → clamped 0
        p = _pair(exit_category="stop_loss",
                  entry_price=10_000, exit_price=9_700, mfe_pct=10.0)
        # profit=-3%, mfe=10% → cap=-0.3 (< 0.20) → base 5 - 10 = -5 → 0
        assert _score_exit(p) == 0.0

    def test_ef_no_demand_base(self):
        p = _pair(exit_category="ef_no_demand", mfe_pct=None)
        assert _score_exit(p) == 3.0

    def test_unknown_category_default_10(self):
        p = _pair(exit_category="nonexistent_cat", mfe_pct=None)
        assert _score_exit(p) == 10.0

    def test_score_clamped_above_zero(self):
        p = _pair(exit_category="ef_no_demand",
                  entry_price=10_000, exit_price=9_000, mfe_pct=5.0)
        assert _score_exit(p) >= 0.0

    def test_mfe_capture_neutral_range(self):
        # overnight_close base=10, cap 0.5 (40-70%) → +0 → 10
        p = _pair(exit_category="overnight_close",
                  entry_price=10_000, exit_price=10_500, mfe_pct=10.0)
        assert _score_exit(p) == 10.0

    def test_mfe_capture_medium_penalty(self):
        # time_exit base=15, cap 0.3 (20-40%) → -5 → 10
        p = _pair(exit_category="time_exit",
                  entry_price=10_000, exit_price=10_300, mfe_pct=10.0)
        assert _score_exit(p) == 10.0


# ──────────────────────────────────────────────────────────────
# T5 — _assign_flags
# ──────────────────────────────────────────────────────────────

class TestAssignFlags:

    def _ts(self, pair: TradePair) -> TradeScore:
        e = _score_entry(pair)
        h, b = _score_hold(pair)
        x = _score_exit(pair)
        return TradeScore(pair=pair, total=e+h+x, entry_score=e,
                          hold_score=h, exit_score=x, hold_bucket=b)

    def test_scalp_flag(self):
        p = _pair(hold_hours=0.1)  # 6 minutes
        s = self._ts(p)
        flags = _assign_flags(p, s)
        assert "SCALP" in flags

    def test_no_scalp_flag_at_15m(self):
        buy = datetime(2026, 5, 1, 10, 0)
        sell = buy + timedelta(minutes=15)
        p = _pair(buy_ts=buy, sell_ts=sell)
        s = self._ts(p)
        flags = _assign_flags(p, s)
        assert "SCALP" not in flags

    def test_overnight_hardstop_flag(self):
        p = _pair(hold_hours=8, exit_category="hard_stop")
        s = self._ts(p)
        flags = _assign_flags(p, s)
        assert "OVERNIGHT_HARDSTOP" in flags

    def test_overnight_hardstop_not_flagged_short(self):
        p = _pair(hold_hours=1, exit_category="hard_stop")
        s = self._ts(p)
        flags = _assign_flags(p, s)
        assert "OVERNIGHT_HARDSTOP" not in flags

    def test_swing_success_flag(self):
        p = _pair(exit_category="trailing_stop",
                  entry_price=10_000, exit_price=10_500)
        s = self._ts(p)
        flags = _assign_flags(p, s)
        assert "SWING_SUCCESS" in flags

    def test_entry_was_wrong_flag(self):
        p = _pair(exit_category="ef_no_demand")
        s = self._ts(p)
        flags = _assign_flags(p, s)
        assert "ENTRY_WAS_WRONG" in flags

    def test_strong_entry_flag(self):
        p = _pair(choch_grade="A", market_regime="TREND")
        s = self._ts(p)
        flags = _assign_flags(p, s)
        assert "STRONG_ENTRY" in flags

    def test_strong_entry_a_plus(self):
        p = _pair(choch_grade="A+", market_regime="TREND")
        s = self._ts(p)
        assert "STRONG_ENTRY" in _assign_flags(p, s)

    def test_strong_entry_not_for_b(self):
        p = _pair(choch_grade="B", market_regime="TREND")
        s = self._ts(p)
        assert "STRONG_ENTRY" not in _assign_flags(p, s)

    def test_mfe_squander_flag(self):
        p = _pair(entry_price=10_000, exit_price=9_900, mfe_pct=5.0)
        # profit_pct=-1%, mfe=5% → cap=-0.2 < 0.20 and mfe>1%
        s = self._ts(p)
        flags = _assign_flags(p, s)
        assert "MFE_SQUANDER" in flags

    def test_mfe_squander_not_for_tiny_mfe(self):
        p = _pair(entry_price=10_000, exit_price=9_900, mfe_pct=0.5)
        s = self._ts(p)
        assert "MFE_SQUANDER" not in _assign_flags(p, s)

    def test_swing_qualified_flag(self):
        p = _pair(hold_hours=3, exit_category="trailing_stop")
        s = self._ts(p)
        assert "SWING_QUALIFIED" in _assign_flags(p, s)

    def test_swing_qualified_blocked_by_ef(self):
        p = _pair(hold_hours=3, exit_category="ef_no_follow")
        s = self._ts(p)
        assert "SWING_QUALIFIED" not in _assign_flags(p, s)

    def test_swing_qualified_blocked_by_hard_stop(self):
        p = _pair(hold_hours=3, exit_category="hard_stop")
        s = self._ts(p)
        assert "SWING_QUALIFIED" not in _assign_flags(p, s)


# ──────────────────────────────────────────────────────────────
# T6 — score_pair composite
# ──────────────────────────────────────────────────────────────

class TestScorePair:

    def test_total_in_range(self):
        for cat in ("trailing_stop", "hard_stop", "ef_no_demand", "time_exit"):
            s = _score(exit_category=cat)
            assert 0 <= s.total <= 100

    def test_components_sum_to_total(self):
        s = _score()
        assert s.total == pytest.approx(s.entry_score + s.hold_score + s.exit_score, abs=0.1)

    def test_grade_a_needs_80(self):
        # A+, TREND, 3x, swing 5h, trailing_stop → 40 + 30 + 30 = 100
        p = _pair(choch_grade="A+", market_regime="TREND", rvol=3.0,
                  hold_hours=5, exit_category="trailing_stop")
        s = score_pair(p)
        assert s.grade == "A"
        assert s.total >= 80

    def test_grade_d_low_score(self):
        buy = datetime(2026, 5, 1, 10, 0)
        sell = buy + timedelta(minutes=3)
        p = _pair(choch_grade="C", market_regime="REVERSAL", rvol=0.5,
                  exit_category="hard_stop", buy_ts=buy, sell_ts=sell)
        s = score_pair(p)
        assert s.grade == "D"

    def test_flags_assigned(self):
        p = _pair(hold_hours=5, exit_category="trailing_stop",
                  entry_price=10_000, exit_price=10_500)
        s = score_pair(p)
        assert "SWING_QUALIFIED" in s.flags
        assert "SWING_SUCCESS" in s.flags


# ──────────────────────────────────────────────────────────────
# T7 — _aggregate
# ──────────────────────────────────────────────────────────────

class TestAggregate:

    def _make_scores(self, configs: list[dict]) -> list[TradeScore]:
        return [score_pair(_pair(**c)) for c in configs]

    def test_empty_scores(self):
        r = _aggregate([])
        assert r.algo_pairs == 0
        assert r.avg_sqs == 0

    def test_avg_sqs(self):
        scores = self._make_scores([
            {"hold_hours": 5, "exit_category": "trailing_stop"},
            {"hold_hours": 0.1, "exit_category": "hard_stop"},
        ])
        r = _aggregate(scores)
        expected = sum(s.total for s in scores) / 2
        assert r.avg_sqs == pytest.approx(expected, abs=0.1)

    def test_grade_dist_counts(self):
        scores = self._make_scores([
            {"choch_grade": "A+", "market_regime": "TREND", "rvol": 3.0,
             "hold_hours": 5, "exit_category": "trailing_stop"},  # A
            {"choch_grade": "C", "market_regime": "REVERSAL", "rvol": 0.5,
             "exit_category": "hard_stop",
             "buy_ts": datetime(2026,5,1,10,0),
             "sell_ts": datetime(2026,5,1,10,3)},                  # D
        ])
        r = _aggregate(scores)
        assert r.grade_dist["A"] >= 1
        assert r.grade_dist["D"] >= 1

    def test_swing_rate_all_qualified(self):
        scores = self._make_scores([
            {"hold_hours": 3, "exit_category": "trailing_stop"},
            {"hold_hours": 5, "exit_category": "take_profit"},
        ])
        r = _aggregate(scores)
        assert r.swing_rate == 100.0

    def test_swing_rate_none_qualified(self):
        scores = self._make_scores([
            {"exit_category": "ef_no_follow", "hold_hours": 3},
            {"exit_category": "hard_stop", "hold_hours": 3},
        ])
        r = _aggregate(scores)
        assert r.swing_rate == 0.0

    def test_exit_dist_counts(self):
        scores = self._make_scores([
            {"exit_category": "trailing_stop"},
            {"exit_category": "trailing_stop"},
            {"exit_category": "hard_stop"},
        ])
        r = _aggregate(scores)
        assert r.exit_dist["trailing_stop"] == 2
        assert r.exit_dist["hard_stop"] == 1

    def test_sqs_by_grade_groups(self):
        scores = self._make_scores([
            {"choch_grade": "A"},
            {"choch_grade": "B"},
            {"choch_grade": "A"},
        ])
        r = _aggregate(scores)
        assert "A" in r.sqs_by_grade
        assert "B" in r.sqs_by_grade

    def test_mfe_capture_avg_none_when_no_mfe(self):
        scores = self._make_scores([{"mfe_pct": None}])
        r = _aggregate(scores)
        assert r.mfe_capture_avg is None


# ──────────────────────────────────────────────────────────────
# T8 — _make_alerts
# ──────────────────────────────────────────────────────────────

class TestMakeAlerts:

    def _alerts(self, scores, avg_sqs=None, swing_rate=None, exit_dist=None):
        avg = avg_sqs if avg_sqs is not None else sum(s.total for s in scores) / max(1, len(scores))
        sw = swing_rate if swing_rate is not None else 0
        ed = exit_dist or {}
        return _make_alerts(scores, avg, sw, ed)

    def _scalp_score(self) -> TradeScore:
        buy = datetime(2026, 5, 1, 10, 0)
        sell = buy + timedelta(minutes=5)
        return score_pair(_pair(buy_ts=buy, sell_ts=sell, exit_category="ef_no_follow"))

    def test_hard_stop_alert(self):
        scores = [score_pair(_pair()) for _ in range(10)]
        alerts = _make_alerts(scores, 55, 60, {"hard_stop": 4})
        assert any("Hard Stop" in a for a in alerts)

    def test_no_hard_stop_alert_below_threshold(self):
        scores = [score_pair(_pair()) for _ in range(10)]
        alerts = _make_alerts(scores, 55, 60, {"hard_stop": 2})
        assert not any("Hard Stop" in a for a in alerts)

    def test_ef_alert(self):
        scores = [score_pair(_pair()) for _ in range(10)]
        alerts = _make_alerts(scores, 55, 60, {"ef_no_demand": 3, "ef_no_follow": 2})
        assert any("EF 청산" in a for a in alerts)

    def test_scalp_alert(self):
        scalp_scores = [self._scalp_score() for _ in range(3)]
        normal_scores = [score_pair(_pair()) for _ in range(8)]
        all_scores = scalp_scores + normal_scores
        alerts = _make_alerts(all_scores, 55, 60, {})
        assert any("SCALP" in a for a in alerts)

    def test_swing_rate_alert(self):
        scores = [score_pair(_pair())]
        alerts = _make_alerts(scores, 55, 40, {})
        assert any("Swing Qualified" in a for a in alerts)

    def test_low_sqs_alert(self):
        scores = [score_pair(_pair())]
        alerts = _make_alerts(scores, 40, 70, {})
        assert any("재검토" in a for a in alerts)

    def test_moderate_sqs_warning(self):
        scores = [score_pair(_pair())]
        alerts = _make_alerts(scores, 60, 70, {})
        assert any("미세조정" in a for a in alerts)

    def test_ok_when_all_good(self):
        scores = [score_pair(_pair(choch_grade="A+", market_regime="TREND", rvol=3.0,
                                    hold_hours=5, exit_category="trailing_stop"))]
        alerts = _make_alerts(scores, 90, 80, {"trailing_stop": 1})
        assert any("✅" in a for a in alerts)

    def test_overnight_alert(self):
        scores = [score_pair(_pair()) for _ in range(10)]
        alerts = _make_alerts(scores, 75, 80, {"overnight_close": 5})
        assert any("overnight_close" in a for a in alerts)


# ──────────────────────────────────────────────────────────────
# T9 — _load_pairs (DB 기반)
# ──────────────────────────────────────────────────────────────

class TestLoadPairs:

    def test_empty_db(self, tmp_path):
        db = _make_db(tmp_path)
        total, n, pairs = _load_pairs(db, 90)
        assert total == 0
        assert n == 0
        assert pairs == []

    def test_basic_fifo_pair(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_trade(db, trade_type="BUY",  timestamp="2026-05-01T10:00:00", price=10000)
        _insert_trade(db, trade_type="SELL", timestamp="2026-05-01T14:00:00", price=10500,
                      realized_pnl=5000, reason="Trailing Stop",
                      strategy="EXIT", mfe_pct=6.0)
        total, n, pairs = _load_pairs(db, 90)
        assert n == 1
        assert len(pairs) == 1
        p = pairs[0]
        assert p.entry_price == 10_000
        assert p.exit_price == 10_500
        assert p.hold_min == pytest.approx(240.0)

    def test_manual_excluded(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_trade(db, strategy="MANUAL", trade_type="BUY",  timestamp="2026-05-01T10:00:00")
        _insert_trade(db, strategy="MANUAL", trade_type="SELL", timestamp="2026-05-01T14:00:00",
                      realized_pnl=1000, reason="HTS_IMPORT")
        _, n, pairs = _load_pairs(db, 90)
        assert n == 0

    def test_kiwoom_excluded(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_trade(db, strategy="kiwoom", trade_type="BUY",  timestamp="2026-05-01T10:00:00")
        _insert_trade(db, strategy="kiwoom", trade_type="SELL", timestamp="2026-05-01T14:00:00",
                      realized_pnl=500, reason="수동")
        _, n, pairs = _load_pairs(db, 90)
        assert n == 0

    def test_reversed_timestamp_skipped(self, tmp_path):
        db = _make_db(tmp_path)
        # SELL timestamp earlier than BUY → reversed → skip
        _insert_trade(db, trade_type="BUY",  timestamp="2026-05-01T14:00:00")
        _insert_trade(db, trade_type="SELL", timestamp="2026-05-01T10:00:00",
                      realized_pnl=0, reason="익절")
        _, n, pairs = _load_pairs(db, 90)
        assert n == 0

    def test_fifo_order_multiple_same_stock(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_trade(db, trade_type="BUY",  timestamp="2026-05-01T10:00:00", price=10000)
        _insert_trade(db, trade_type="BUY",  timestamp="2026-05-01T11:00:00", price=11000)
        _insert_trade(db, trade_type="SELL", timestamp="2026-05-01T15:00:00", price=12000,
                      realized_pnl=2000, reason="Trailing Stop", strategy="EXIT")
        _, n, pairs = _load_pairs(db, 90)
        assert n == 1
        # First BUY (10000) is paired with the SELL
        assert pairs[0].entry_price == 10_000

    def test_ml_features_propagated(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_trade(db, trade_type="BUY",  timestamp="2026-05-01T10:00:00",
                      choch_grade="A", market_regime="TREND", rvol_at_entry=2.5)
        _insert_trade(db, trade_type="SELL", timestamp="2026-05-01T14:00:00",
                      realized_pnl=3000, reason="Trailing Stop",
                      strategy="EXIT", mfe_pct=8.5, mae_pct=-1.2)
        _, _, pairs = _load_pairs(db, 90)
        p = pairs[0]
        assert p.choch_grade == "A"
        assert p.market_regime == "TREND"
        assert p.rvol_at_entry == pytest.approx(2.5)
        assert p.mfe_pct == pytest.approx(8.5)

    def test_different_stocks_dont_cross_pair(self, tmp_path):
        db = _make_db(tmp_path)
        # stock A: BUY only (no SELL)
        _insert_trade(db, stock_code="000001", trade_type="BUY",
                      timestamp="2026-05-01T10:00:00")
        # stock B: SELL only (no BUY)
        _insert_trade(db, stock_code="000002", trade_type="SELL",
                      timestamp="2026-05-01T14:00:00", realized_pnl=1000, reason="stop")
        _, n, pairs = _load_pairs(db, 90)
        assert n == 0


# ──────────────────────────────────────────────────────────────
# T10 — TradeDecisionAudit.run
# ──────────────────────────────────────────────────────────────

class TestTradeDecisionAudit:

    def test_run_empty_db(self, tmp_path):
        db = _make_db(tmp_path)
        audit = TradeDecisionAudit(db_path=str(db), days=90)
        r = audit.run()
        assert r.algo_pairs == 0
        assert "알고리즘 거래 없음" in r.alerts[0]

    def test_run_with_pairs(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_trade(db, trade_type="BUY",  timestamp="2026-05-01T10:00:00", price=10000,
                      choch_grade="A", market_regime="TREND", rvol_at_entry=2.0)
        _insert_trade(db, trade_type="SELL", timestamp="2026-05-01T14:00:00", price=10500,
                      realized_pnl=5000, reason="Trailing Stop",
                      strategy="EXIT", mfe_pct=6.0)
        audit = TradeDecisionAudit(db_path=str(db), days=90)
        r = audit.run()
        assert r.algo_pairs == 1
        assert r.avg_sqs > 0
        assert len(r.scores) == 1

    def test_run_excludes_manual(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_trade(db, strategy="MANUAL", trade_type="BUY",  timestamp="2026-05-01T10:00:00")
        _insert_trade(db, strategy="MANUAL", trade_type="SELL", timestamp="2026-05-01T14:00:00",
                      realized_pnl=1000, reason="HTS_IMPORT")
        _insert_trade(db, strategy="SMC",    trade_type="BUY",  timestamp="2026-05-01T10:30:00",
                      stock_code="000002", stock_name="테스트2", price=9000)
        _insert_trade(db, strategy="EXIT",   trade_type="SELL", timestamp="2026-05-01T15:00:00",
                      stock_code="000002", stock_name="테스트2", price=9500,
                      realized_pnl=5000, reason="Trailing Stop")
        audit = TradeDecisionAudit(db_path=str(db), days=90)
        r = audit.run()
        assert r.algo_pairs == 1  # only the SMC pair

    def test_report_total_pairs_set(self, tmp_path):
        db = _make_db(tmp_path)
        _insert_trade(db, trade_type="BUY",  timestamp="2026-05-01T10:00:00")
        _insert_trade(db, trade_type="SELL", timestamp="2026-05-01T14:00:00",
                      realized_pnl=0, reason="stop", strategy="EXIT")
        audit = TradeDecisionAudit(db_path=str(db), days=90)
        r = audit.run()
        assert r.total_pairs >= 2  # at least BUY + SELL row counted
