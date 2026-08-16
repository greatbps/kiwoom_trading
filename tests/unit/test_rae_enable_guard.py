"""
tests/unit/test_rae_enable_guard.py

RAE-ENABLE-GUARD 검증 (2026-08-17).

배경(§2 사전 조사 — 코드로 확인):
  - config/strategy_hybrid.yaml:1793 `rae.enabled: false`
  - main_auto_trading.py:743 `self.rae_detector = RAEDetector(_rae_full_cfg)` — 무조건 생성,
    enabled 여부와 무관.
  - main_auto_trading.py:6167 RAE 진입 조건:
        if (entry_mode == 'smc'
                and self.rae_detector.has_candidate(stock_code)
                and stock_code not in self.smc_pending
                and not _rae_blocked_reason):
    → `rae.enabled`을 읽는 코드가 어디에도 없었다(grep 결과 0건, 2026-08-17 확인).
  - RAE → execute_buy 연결 경로: check_entry_signal()의 RAE 성공 분기가
    self._emit_signal(..., strategy='SMC_RAE', ...) 호출 후 즉시 `return`
    (main_auto_trading.py:6247) → _pending_signals에 큐잉 → 다음 루프에서
    check_all_stocks()가 _flush_pending_signals() 호출 → execute_buy().
    즉 _emit_signal(strategy='SMC_RAE')이 RAE→execute_buy의 유일한 관문이다.
  - 기존 RAE 테스트(tests/test_position_sizing_matrix.py)는 순수 사이징 계산만
    검증하고, tests/unit/test_check_entry_signal_silent_drop.py는
    rae_detector.has_candidate를 항상 False로 mock — 즉 어떤 기존 테스트도
    "RAE 조건이 실제로 충족된 상태에서 enabled=false가 진입을 막는지"를
    검증한 적이 없었다.

전략: tests/unit/test_check_entry_signal_silent_drop.py에서 이미 검증된 stub
(SmcEntryMockConfig, _make_entry_signal_stub, _FixedDateTime)을 재사용하고,
RAEDetector는 MagicMock 대신 실제 인스턴스로 교체 + REACCEL 단계 candidate를
직접 주입해 RAEDetector.check()가 한 번에 _evaluate_entry()로 진입하게 한다
(IMPULSE→PULLBACK→REACCEL 상태전이를 여러 번 호출로 재현할 필요 없음).
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests.unit.test_check_entry_signal_silent_drop import (
    SmcEntryMockConfig,
    _make_entry_signal_stub,
    _FixedDateTime,
)
from analyzers.smc.rae_detector import RAECandidate, RAEDetector


# ─── Golden Input: RAE 조건을 모두 충족하는 df + candidate ──────────────────

def _make_rae_ready_df(n: int = 300, last_close: float = 75_000.0) -> pd.DataFrame:
    """상승추세 + 마지막 봉 직전 대비 상승 + vwap 지지 + 넉넉한 거래량."""
    prices = [last_close * 0.98 + i * (last_close * 0.02 / n) for i in range(n)]
    prices[-1] = last_close
    prices[-2] = last_close * 0.999  # price_rising 조건용(직전봉보다 상승)
    times = pd.date_range("2026-08-11 09:00", periods=n, freq="1min")
    return pd.DataFrame(
        {
            "open":   [p * 0.9995 for p in prices],
            "high":   [p * 1.002 for p in prices],
            "low":    [p * 0.998 for p in prices],
            "close":  prices,
            "volume": [50_000] * n,
            "vwap":   [p * 0.999 for p in prices],  # vwap_or_ema_support 조건용
        },
        index=times,
    )


def _inject_reaccel_candidate(detector: RAEDetector, stock_code: str,
                               current_price: float = 75_000.0) -> RAECandidate:
    """REACCEL 단계 candidate를 직접 주입 — check() 1회 호출로 _evaluate_entry()
    도달. depth_r=(73000-72000)/1000=1.0R(0.5~2.0R 범위 내), structure_intact
    (75000>70000), vol_expanding(50000 ≥ 10000×1.2)까지 5개 조건 전부 충족하도록
    설계된 순수 Golden Input(다른 RAE 파라미터 최적화 아님)."""
    # RAEDetector.check()는 analyzers/smc/rae_detector.py 자체의 datetime.now()
    # (실제 wall-clock, main_auto_trading.datetime 패치와 무관)로 elapsed를 계산하므로
    # detected_at은 반드시 "지금"을 기준으로 상대적으로 설정해야 stale 타임아웃에
    # 안 걸린다.
    _now = datetime.now()
    cand = RAECandidate(
        stock_code=stock_code, stock_name="삼성전자",
        choch_level=70_000.0, choch_price=71_000.0,
        impulse_high=73_000.0, detected_at=_now - timedelta(minutes=30),
        grade="A", position_size_mult=1.0, confidence=0.9,
        phase="REACCEL", pullback_low=72_000.0, pullback_vol_avg=10_000.0,
        reaccel_started_at=_now - timedelta(minutes=10),
        r_unit=1_000.0, timeout_minutes=60,
    )
    detector._candidates[stock_code] = cand
    return cand


def _make_rae_config(rae_enabled: bool) -> SmcEntryMockConfig:
    cfg = SmcEntryMockConfig()
    cfg._DATA = {**SmcEntryMockConfig._DATA, "rae": {"enabled": rae_enabled}}
    return cfg


def _make_rae_stub(stock_code: str = "005930", rae_enabled: bool = False):
    """check_entry_signal 전용 stub + 실제 RAEDetector(REACCEL candidate 주입) +
    TrendExpansionDetector mock(soft-pass) + 신호 큐잉 속성 초기화."""
    stub = _make_entry_signal_stub(stock_code)
    stub.config = _make_rae_config(rae_enabled)

    real_detector = RAEDetector({"rae": stub.config.get("rae", {})})
    _inject_reaccel_candidate(real_detector, stock_code)
    stub.rae_detector = real_detector

    stub.ted = MagicMock()
    stub.ted.check.return_value = (True, 0.8, {})  # TED soft-pass, RAE 사이징에 영향 없음

    # RAE가 차단되면 PRIMARY(SMC) 경로로 폴스루된다 — 그 경로가 요구하는
    # 속성이 없어 무관한 AttributeError로 오염되지 않도록 최소한만 채운다
    # (SMC 로직 자체는 건드리지 않음 — 단순 존재 보장용 stub).
    stub.w_pattern_filter = MagicMock()
    stub.w_pattern_filter.enabled = False

    stub._executed_signal_ids = set()
    stub._pending_signals = []
    stub._dry_run = False

    return stub


def _rae_emit_calls(spy: MagicMock):
    """spy(=_emit_signal 스파이) 호출 중 strategy='SMC_RAE'인 것만 필터."""
    calls = []
    for c in spy.call_args_list:
        args, kwargs = c
        strategy = kwargs.get("strategy") if "strategy" in kwargs else (args[7] if len(args) > 7 else None)
        if strategy == "SMC_RAE":
            calls.append(c)
    return calls


# ============================================================
# Test A — Disabled Invariant
# ============================================================
class TestRaeDisabledInvariant:
    def test_rae_conditions_are_genuinely_satisfiable(self):
        """전제 확인: Golden Input이 RAEDetector 자체 조건(score≥7/9)은 충족한다
        (게이트와 무관하게) — 이래야 '차단됐다'는 결과가 의미를 가진다."""
        detector = RAEDetector({"rae": {}})
        cand = _inject_reaccel_candidate(detector, "005930")
        df = _make_rae_ready_df()
        should_enter, reason, details = detector.check("005930", df, 75_000.0)
        assert should_enter is True, f"Golden Input이 RAE 자체 조건을 충족 못함: {reason}"
        assert details["score"] >= 7

    def test_rae_enabled_false_blocks_before_emit_signal(self):
        """rae.enabled=false → check_entry_signal() 실행 중 strategy='SMC_RAE'로
        _emit_signal()이 절대 호출되지 않아야 한다(=RAE→execute_buy의 유일한
        관문이 닫혀 있음)."""
        stub = _make_rae_stub(rae_enabled=False)
        df = _make_rae_ready_df()
        spy = MagicMock(wraps=stub._emit_signal)

        with patch.object(stub, "_check_global_risk_gates", return_value=(True, "")), \
             patch.object(stub, "_emit_signal", spy), \
             patch("main_auto_trading.datetime", _FixedDateTime):
            asyncio.run(stub.check_entry_signal("005930", kiwoom_df=df))

        rae_calls = _rae_emit_calls(spy)
        assert rae_calls == [], (
            f"rae.enabled=false인데 SMC_RAE 신호가 emit됨: {rae_calls}"
        )
        assert all(s.get("strategy") != "SMC_RAE" for s in stub._pending_signals), (
            "rae.enabled=false인데 _pending_signals에 SMC_RAE 신호가 큐잉됨"
        )
        # RAE candidate 자체는 여전히 살아있어야 한다(게이트가 후보 소멸/오염을
        # 일으키면 안 됨 — 순수 진입 차단만 해야 함).
        assert stub.rae_detector.has_candidate("005930") is True


# ============================================================
# Test B — Enabled Golden Path
# ============================================================
class TestRaeEnabledGoldenPath:
    def test_rae_enabled_true_reaches_emit_signal_with_expected_payload(self):
        stub = _make_rae_stub(rae_enabled=True)
        df = _make_rae_ready_df()
        spy = MagicMock(wraps=stub._emit_signal)

        with patch.object(stub, "_check_global_risk_gates", return_value=(True, "")), \
             patch.object(stub, "_emit_signal", spy), \
             patch("main_auto_trading.datetime", _FixedDateTime):
            asyncio.run(stub.check_entry_signal("005930", kiwoom_df=df))

        rae_calls = _rae_emit_calls(spy)
        assert len(rae_calls) == 1, f"rae.enabled=true인데 SMC_RAE 신호가 emit 안 됨: {spy.call_args_list}"
        args, kwargs = rae_calls[0]
        assert kwargs.get("choch_grade") == "A" or (len(args) > 8 and args[8] == "A")

        assert len(stub._pending_signals) == 1
        assert stub._pending_signals[0]["strategy"] == "SMC_RAE"
        assert stub._pending_signals[0]["price"] > 0

    def test_rae_enabled_true_reaches_execute_buy_via_flush(self):
        """RAE 성공 분기는 _emit_signal 직후 즉시 return하므로(코드확인,
        main_auto_trading.py:6247) 이 시나리오에서 _pending_signals는 RAE
        신호 1건만 담긴다 — 다른 경로 오염 없이 flush→execute_buy까지 안전하게
        끝까지 확인 가능."""
        stub = _make_rae_stub(rae_enabled=True)
        df = _make_rae_ready_df()

        with patch.object(stub, "_check_global_risk_gates", return_value=(True, "")), \
             patch("main_auto_trading.datetime", _FixedDateTime):
            asyncio.run(stub.check_entry_signal("005930", kiwoom_df=df))

        assert len(stub._pending_signals) == 1
        assert stub._pending_signals[0]["strategy"] == "SMC_RAE"

        with patch.object(stub, "execute_buy") as mock_execute_buy, \
             patch.object(stub, "_check_global_risk_gates", return_value=(True, "")):
            stub._flush_pending_signals([{"code": "005930", "price": 75_000.0}])

        assert mock_execute_buy.call_count == 1, "rae.enabled=true 골든패스가 execute_buy까지 도달 못함"


# ============================================================
# Test C — Mutation Test
# ============================================================
class TestRaeGateMutationCaught:
    def test_source_level_mutation_removed_gate_fails(self):
        """RAE enable gate 조건을 실제 소스에서 제거(뮤테이션) → 모듈 재로드 후
        Test A(disabled invariant)와 동일한 시나리오가 반드시 FAIL해야 한다 —
        테스트가 실제로 이 결함을 잡는지 증명한다. 끝나면 원본으로 복원."""
        """main_auto_trading.py의 RAE enable gate 라인을 실제로 제거한 뒤
        모듈을 재로드해서 Test A가 FAIL하는지 확인하고, 즉시 원복한다."""
        import importlib
        import main_auto_trading as mat_mod

        src_path = Path(mat_mod.__file__)
        original_src = src_path.read_text(encoding="utf-8")
        gated_block = (
            "            if (entry_mode == 'smc'\n"
            "                    and self.rae_detector.has_candidate(stock_code)\n"
            "                    and stock_code not in self.smc_pending\n"
            "                    and not _rae_blocked_reason\n"
            "                    and self.config.get('rae', {}).get('enabled', False)):\n"
        )
        unguarded_block = (
            "            if (entry_mode == 'smc'\n"
            "                    and self.rae_detector.has_candidate(stock_code)\n"
            "                    and stock_code not in self.smc_pending\n"
            "                    and not _rae_blocked_reason):\n"
        )
        assert gated_block in original_src, "RAE enable gate 블록을 찾지 못함 — 수정 위치가 바뀌었는지 확인 필요"

        mutated_src = original_src.replace(gated_block, unguarded_block, 1)
        assert mutated_src != original_src
        try:
            src_path.write_text(mutated_src, encoding="utf-8")
            importlib.reload(mat_mod)

            from tests.unit.test_check_entry_signal_silent_drop import (
                _make_entry_signal_stub as _mutated_stub_factory,
            )
            # reload 이후이므로 KiwoomAutoTrader 참조도 새 모듈 기준으로 다시 가져와야 한다.
            import tests.unit.test_check_entry_signal_silent_drop as silent_drop_mod
            importlib.reload(silent_drop_mod)

            stub = silent_drop_mod._make_entry_signal_stub("005930")
            stub.config = _make_rae_config(rae_enabled=False)  # 여전히 false
            real_detector = RAEDetector({"rae": stub.config.get("rae", {})})
            _inject_reaccel_candidate(real_detector, "005930")
            stub.rae_detector = real_detector
            stub.ted = MagicMock()
            stub.ted.check.return_value = (True, 0.8, {})
            stub.w_pattern_filter = MagicMock()
            stub.w_pattern_filter.enabled = False
            stub._executed_signal_ids = set()
            stub._pending_signals = []
            stub._dry_run = False

            df = _make_rae_ready_df()
            spy = MagicMock(wraps=stub._emit_signal)

            with patch.object(stub, "_check_global_risk_gates", return_value=(True, "")), \
                 patch.object(stub, "_emit_signal", spy), \
                 patch.object(mat_mod, "datetime", _FixedDateTime):
                asyncio.run(stub.check_entry_signal("005930", kiwoom_df=df))

            rae_calls = _rae_emit_calls(spy)
            # 뮤테이션(게이트 제거) 상태에서는 rae.enabled=false여도 신호가 나가야 한다.
            assert len(rae_calls) == 1, (
                "뮤테이션이 무력화되지 않음 — 게이트를 지웠는데도 여전히 차단됨 "
                "(테스트가 결함을 못 잡고 있다는 뜻)"
            )
        finally:
            src_path.write_text(original_src, encoding="utf-8")
            importlib.reload(mat_mod)
            import tests.unit.test_check_entry_signal_silent_drop as silent_drop_mod
            importlib.reload(silent_drop_mod)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
