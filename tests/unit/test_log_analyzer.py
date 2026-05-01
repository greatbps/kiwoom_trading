"""
tests/unit/test_log_analyzer.py

analysis/log_analyzer.py 순수 파싱 함수 테스트

케이스:
  1. parse_orchestrator_events — ACCEPT/REJECT 파싱
  2. parse_choch_events        — CHoCH 고유 종목 집계
  3. parse_mkt_ctx             — NO_TRADE_DAY 감지
  4. parse_trend_signals       — [TREND_SIG] 태그 파싱
  5. summarize                 — 일일 요약 정확성
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from analysis.log_analyzer import (
    parse_orchestrator_events,
    parse_choch_events,
    parse_mkt_ctx,
    parse_trend_signals,
    summarize,
)


# ─── 샘플 로그 라인 ──────────────────────────────────────────────────────────

_ORCHESTRATOR_LINES = [
    "✅ ACCEPT 015260 @954원 | PID:112082 | conf=0.51 alpha=+1.63 pos_mult=0.61",
    "2026-03-10 10:00:29,559 - INFO - ✅ ACCEPT 015260 @954원 | PID:112082 | conf=0.51 alpha=+1.63 pos_mult=0.61",
    "❌ REJECT 327260 | PID:112082 | L0 | 진입 시간 외 (09:14, 10:00 이전)",
    "2026-03-10 09:14:20,703 - INFO - ❌ REJECT 327260 | PID:112082 | L0 | 진입 시간 외 (09:14, 10:00 이전)",
    "❌ REJECT 232680 | PID:112082 | L2 | VWAP 위 3.2% 초과",
    "⏰ 222080: 🚫 SMC 12:30 이후 진입 차단 (12:30:36)",
    "Some irrelevant log line",
]

_CHOCH_LINES = [
    "10:35:14 [CHOCH] 036930 | bullish | level=64100 | wick=76600 | close=75800 | penetration=19.50%",
    "10:36:15 [CHOCH] 036930 | bullish | level=64100 | wick=76600 | close=75800 | penetration=19.50%",
    "11:05:18 [CHOCH] 036930 | bullish | level=64100 | wick=78400 | close=76500 | penetration=22.31%",
    "10:40:00 [CHOCH] 123456 | bearish | level=50000 | wick=48000 | close=48500 | penetration=5.00%",
    "random line without choch",
]

_MKT_CTX_LINES = [
    "2026-03-20 09:30:26,996 - INFO - [MKT_CTX] 🚫 NO_TRADE_DAY: KOSPI❌ LH+LL 하락구조 | KOSDAQ❌ 횡보/불명확",
    "[MKT_CTX] 🚫 NO_TRADE_DAY: KOSPI❌ LH+LL 하락구조 | EMA_BEAR❌ gap=-0.76%",
    "2026-03-20 09:15:06,524 - INFO - [MKT_CTX] 캐시 리셋",
]

_NORMAL_MKT_LINES = [
    "2026-03-11 09:15:06,524 - INFO - [MKT_CTX] 캐시 리셋",
    "Some normal trading line",
]

_TREND_LINES = [
    "[TREND_SIG] TREND BREAKOUT[STRONG]: 고점돌파(9900→10100) Vol×2.5 몸통78% 점수5/5 이격1.2%",
    "2026-03-22 11:30:00,000 - INFO - [TREND_SIG] TREND PULLBACK[WEAK]: EMA20눌림(9800, 이격0.5%) 기울기0.15% Vol×1.3 점수3/5",
    "[TREND_NO_SIG] 거래량부족(1.2x<1.5x) / 돌파실패",
    "Unrelated line",
]


# ─── 테스트 케이스 ───────────────────────────────────────────────────────────

class TestParseOrchestratorEvents:
    """Case 1: ACCEPT / REJECT / 시간차단 파싱."""

    def test_accept_count(self):
        events = parse_orchestrator_events(_ORCHESTRATOR_LINES)
        accepts = [e for e in events if e["type"] == "ACCEPT"]
        # 중복 라인(timestamp + plain)은 dedup — stock_code 기준 unique
        assert len(accepts) >= 1

    def test_reject_count(self):
        events = parse_orchestrator_events(_ORCHESTRATOR_LINES)
        rejects = [e for e in events if e["type"] == "REJECT"]
        assert len(rejects) >= 2

    def test_accept_has_required_fields(self):
        events = parse_orchestrator_events(_ORCHESTRATOR_LINES)
        acc = next(e for e in events if e["type"] == "ACCEPT")
        assert "stock_code" in acc
        assert "price"      in acc
        assert acc["stock_code"] == "015260"
        assert acc["price"] == 954

    def test_reject_has_reason(self):
        events = parse_orchestrator_events(_ORCHESTRATOR_LINES)
        rej = next(e for e in events if e["type"] == "REJECT" and e["stock_code"] == "327260")
        assert "reason" in rej
        assert len(rej["reason"]) > 0

    def test_time_block_detected(self):
        events = parse_orchestrator_events(_ORCHESTRATOR_LINES)
        blocks = [e for e in events if e["type"] == "TIME_BLOCK"]
        assert len(blocks) >= 1
        assert blocks[0]["stock_code"] == "222080"


class TestParseChochEvents:
    """Case 2: CHoCH 고유 종목 집계."""

    def test_unique_stocks(self):
        result = parse_choch_events(_CHOCH_LINES)
        codes = {e["stock_code"] for e in result}
        assert "036930" in codes
        assert "123456" in codes

    def test_dedup_same_choch(self):
        """동일 종목 반복 CHoCH는 첫 감지 1회로 집계."""
        result = parse_choch_events(_CHOCH_LINES)
        codes = [e["stock_code"] for e in result]
        assert codes.count("036930") == 1

    def test_direction_parsed(self):
        result = parse_choch_events(_CHOCH_LINES)
        r = next(e for e in result if e["stock_code"] == "036930")
        assert r["direction"] == "bullish"

    def test_irrelevant_lines_ignored(self):
        result = parse_choch_events(["random line without choch"])
        assert result == []


class TestParseMktCtx:
    """Case 3: NO_TRADE_DAY 감지."""

    def test_no_trade_day_detected(self):
        ctx = parse_mkt_ctx(_MKT_CTX_LINES)
        assert ctx["no_trade_day"] is True

    def test_reason_extracted(self):
        ctx = parse_mkt_ctx(_MKT_CTX_LINES)
        assert len(ctx["reason"]) > 0
        assert "KOSPI" in ctx["reason"] or "EMA" in ctx["reason"]

    def test_normal_day_not_blocked(self):
        ctx = parse_mkt_ctx(_NORMAL_MKT_LINES)
        assert ctx["no_trade_day"] is False
        assert ctx["reason"] == ""


class TestParseTrendSignals:
    """Case 4: [TREND_SIG] 태그 파싱."""

    def test_breakout_detected(self):
        signals = parse_trend_signals(_TREND_LINES)
        bos = [s for s in signals if s["entry_type"] == "breakout"]
        assert len(bos) == 1
        assert bos[0]["grade"] == "STRONG"

    def test_pullback_detected(self):
        signals = parse_trend_signals(_TREND_LINES)
        pbs = [s for s in signals if s["entry_type"] == "pullback"]
        assert len(pbs) == 1
        assert pbs[0]["grade"] == "WEAK"

    def test_no_sig_not_included(self):
        """[TREND_NO_SIG]는 signals에 포함되지 않음."""
        signals = parse_trend_signals(_TREND_LINES)
        assert all(s["entry_type"] in ("breakout", "pullback") for s in signals)

    def test_empty_lines(self):
        assert parse_trend_signals([]) == []


class TestSummarize:
    """Case 5: 일일 요약 정확성."""

    def test_summary_keys_present(self):
        events = parse_orchestrator_events(_ORCHESTRATOR_LINES)
        chochs = parse_choch_events(_CHOCH_LINES)
        mkt    = parse_mkt_ctx(_MKT_CTX_LINES)
        trends = parse_trend_signals(_TREND_LINES)

        result = summarize(events=events, chochs=chochs, mkt_ctx=mkt, trend_signals=trends)

        assert "accept_count"    in result
        assert "reject_count"    in result
        assert "choch_count"     in result
        assert "no_trade_day"    in result
        assert "trend_count"     in result
        assert "time_block_count" in result

    def test_no_trade_day_flag(self):
        mkt = parse_mkt_ctx(_MKT_CTX_LINES)
        result = summarize(events=[], chochs=[], mkt_ctx=mkt, trend_signals=[])
        assert result["no_trade_day"] is True

    def test_trend_count(self):
        trends = parse_trend_signals(_TREND_LINES)
        result = summarize(events=[], chochs=[], mkt_ctx={"no_trade_day": False, "reason": ""}, trend_signals=trends)
        assert result["trend_count"] == 2
