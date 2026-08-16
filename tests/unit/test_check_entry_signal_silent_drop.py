"""
tests/unit/test_check_entry_signal_silent_drop.py

check_entry_signal() → begin_evaluation() Silent Drop 재현 테스트 (2026-08-16 조사 후속).

배경:
  research.candidates/decision_ledger가 2026-08-11 14:00:28 이후 완전히 정지된 채로
  2026-08-12~14 사흘간 0건이었다. 정적 코드분석(5774~6636 전체 완독)으로는
  "성공한 begin_evaluation() 호출은 원래 무음(로그 없음)"이라는 설계 때문에
  로그만으로 원인을 확정할 수 없었다 — 그래서 실제로 함수를 실행해서 확인한다.

전략(tests/simulation/test_execute_buy_dry_run.py의 확립된 stub 패턴 재사용):
  1. IntegratedTradingSystem.__new__()로 stub 생성 — __init__ 생략
  2. 외부 의존성(DB, API, WebSocket, regime_analyzer 등) = MagicMock 또는 우회
  3. decision_service = MagicMock() — begin_evaluation() 호출 여부/횟수만 검증
     (DecisionService/DB 내부 로직은 services/test_decision_service.py 별도 커버)
  4. entry_mode="smc" 강제 — 실제 운영 config(config/strategy_hybrid.yaml: entry_mode: "smc")와
     동일 경로를 태운다.

실제 8/11~8/14 raw OHLCV 데이터는 어디에도 저장돼 있지 않다(DB는 decision_ledger/candidates만
갖고 있고 원본 kiwoom_df는 저장 안 됨) — 그래서 "실제 8/11 신호 그대로 재생"은 불가능하고,
구조적으로 동일한 조건(정상 종목정보 + 정상 봉데이터 + 정상 게이트 통과)의 합성 신호로
"이 경로가 정상 입력에서 Candidate/Decision 없이 조용히 사라지는가"만 검증한다.
이 한계는 명시적으로 SKIP 사유로 기록한다(§5 8/12~14 Replay는 원본데이터 부재로 수행 불가).

Silent Drop Oracle(§6): 매 실행은 반드시 다음 중 하나로 분류된다.
  ACCEPT          — begin_evaluation() 호출됨(성공/거절 불문, DB 기록 시도가 있었음)
  EXPLICIT_REJECT — 함수가 return했고 로그/사유가 있는 것으로 코드상 확인된 개별 게이트
  SILENT_DROP     — 함수가 return했는데 begin_evaluation()도 호출 안 되고 이유도 안 남음
ACCEPT나 EXPLICIT_REJECT가 아니면 전부 FAIL 처리한다.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.simulation.test_execute_buy_dry_run import MockConfig
from main_auto_trading import IntegratedTradingSystem as KiwoomAutoTrader


# ─── config: 실제 운영값(entry_mode="smc") 강제 ─────────────────────────────
class SmcEntryMockConfig(MockConfig):
    _DATA = {
        **MockConfig._DATA,
        "squeeze_momentum": {"entry_mode": "smc"},
        "smc": {"mtf_bias": {"enabled": False}},  # 30분봉 리샘플 생략(테스트 단순화)
        "time_filter": {
            "early_window_block": {"enabled": False},
            "smc_afternoon_cutoff": "12:30",
        },
        "market_regime": {"enabled": False},  # regime_analyzer 미설정과 함께 Gate 2 skip
    }

    def get_section(self, name: str) -> dict:
        return self._DATA.get(name, {})

    def get_signal_generation_config(self) -> dict:
        return {}


# ─── 1분봉 합성 데이터 (SMC 분기가 요구하는 최소 250+행, DatetimeIndex) ──────
def _make_1min_df(n: int = 300, price: float = 75_000.0) -> pd.DataFrame:
    prices = [price * (1 + 0.0001 * i) for i in range(n)]
    times = pd.date_range("2026-08-11 09:00", periods=n, freq="1min")
    return pd.DataFrame(
        {
            "open":   [p * 0.9995 for p in prices],
            "high":   [p * 1.002 for p in prices],
            "low":    [p * 0.998 for p in prices],
            "close":  prices,
            "volume": [50_000] * n,
        },
        index=times,
    )


# ─── stub 팩토리(check_entry_signal 전용) ──────────────────────────────────
def _make_entry_signal_stub(stock_code: str = "005930") -> KiwoomAutoTrader:
    t = KiwoomAutoTrader.__new__(KiwoomAutoTrader)
    t.config = SmcEntryMockConfig()

    t._ema9_blocks = {}
    t.validated_stocks = {
        stock_code: {"name": "삼성전자", "market": "KOSPI", "strategy": "DEFAULT"},
    }
    t.default_strategy_tag = "DEFAULT"

    t.state_manager = MagicMock()
    t.state_manager.can_enter.return_value = (True, "")

    # regime_analyzer 속성 자체를 안 만든다 → hasattr()=False → Gate 2 통째로 skip(§코드주석 5904)
    if hasattr(t, "regime_analyzer"):
        delattr(t, "regime_analyzer")

    t.positions = {}
    t.smc_pending = {}
    t._primary_fail_ts = {}
    t._rae_daily_failed = set()
    t.rae_detector = MagicMock()
    t.rae_detector.has_candidate.return_value = False

    t.analyzer = MagicMock()
    t.analyzer.calculate_vwap = lambda df, **kw: df
    t.analyzer.calculate_atr = lambda df: df  # 'atr' 컬럼 미추가 → ATR_BLOCK 자연 skip
    t.analyzer.generate_signals = lambda df, **kw: df

    t.market_context = MagicMock()
    t.market_context.get_regime.return_value = ("NEUTRAL", "")

    t.bb30_observer = MagicMock()

    t.decision_service = MagicMock()

    return t


def _patches(stub: KiwoomAutoTrader):
    return [
        patch.object(stub, "_check_global_risk_gates", return_value=(True, "")),
    ]


import datetime as _dt_module  # noqa: E402


class _FixedDateTime(_dt_module.datetime):
    """main_auto_trading의 datetime.now()만 오전 10:30으로 고정한다.
    실제 실행 시각이 오후면 [Gate 3] SMC 오후 컷오프(13:30)에 항상 걸려버려서
    "정상 신호가 사라지는가"라는 이 테스트의 목적과 무관한 이유로 FAIL한다
    (2026-08-16 최초 실행에서 이 실수로 실제로 오탐이 났다 — 기록으로 남긴다)."""

    @classmethod
    def now(cls, tz=None):
        return _dt_module.datetime(2026, 8, 11, 10, 30, 0)


def _classify(stub: KiwoomAutoTrader) -> str:
    """Silent Drop Oracle(§6) — begin_evaluation 호출 여부만으로 1차 판정.
    ACCEPT: begin_evaluation 호출됨(성공/거절 DB기록 시도 있었음).
    그 외(호출 자체가 없음)는 이 테스트 시나리오(모든 조기게이트 PASS로 설계)에서는
    코드상 명시적 REJECT 지점이 하나도 없으므로 SILENT_DROP으로 판정한다."""
    if stub.decision_service.begin_evaluation.call_count >= 1:
        return "ACCEPT"
    return "SILENT_DROP"


class TestCheckEntrySignalSilentDrop:
    def test_valid_signal_reaches_begin_evaluation_or_explicit_reject(self):
        """§4 Golden Path — 모든 조기 게이트를 통과하도록 설계한 정상 입력이
        begin_evaluation() 없이 조용히 사라지면 FAIL."""
        stub = _make_entry_signal_stub()
        df = _make_1min_df()

        with patch.object(stub, "_check_global_risk_gates", return_value=(True, "")), \
             patch("main_auto_trading.datetime", _FixedDateTime):
            asyncio.run(stub.check_entry_signal("005930", kiwoom_df=df))

        outcome = _classify(stub)
        assert outcome == "ACCEPT", (
            f"SILENT DROP 재현: begin_evaluation() 호출 0회. "
            f"call_count={stub.decision_service.begin_evaluation.call_count}"
        )

    def test_begin_evaluation_receives_positive_price_on_smc_path(self):
        """SMC 분기(line 6636)까지 도달했다면 price=0.0이 아니라 실제 현재가로
        호출돼야 한다(§5 — price=0.0 자체는 버그가 아님을 재확인하되, 도달 여부를 검증)."""
        stub = _make_entry_signal_stub()
        df = _make_1min_df()

        with patch.object(stub, "_check_global_risk_gates", return_value=(True, "")), \
             patch("main_auto_trading.datetime", _FixedDateTime):
            asyncio.run(stub.check_entry_signal("005930", kiwoom_df=df))

        assert stub.decision_service.begin_evaluation.call_count >= 1, "begin_evaluation 미호출"
        _, kwargs = stub.decision_service.begin_evaluation.call_args
        assert kwargs.get("price", 0.0) > 0, (
            f"SMC 경로 도달했는데 price<=0으로 호출됨: {kwargs}"
        )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
