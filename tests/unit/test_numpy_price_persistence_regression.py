"""
tests/unit/test_numpy_price_persistence_regression.py

Root Cause 회귀 테스트 (2026-08-17) — main_auto_trading.py:6646 numpy.int64 수정.

배경:
  research.candidates/decision_ledger가 2026-08-12~14 사흘간 0건이었던 원인은
  SMC 경로의 begin_evaluation() 호출이 `price=current_price`(df['close'].iloc[-1],
  실제 운영 데이터에서 numpy.int64)를 그대로 넘겨서, decision_repository.py의
  create_candidate() 내부 psycopg2 INSERT가 "can't adapt type 'numpy.int64'"로
  실패하고 있었기 때문이다(자체 try/except로 흡수돼 WARNING 로그만 남고 상위
  호출부에는 조용한 None 반환으로 보임).

  수정: main_auto_trading.py:6646 `price=current_price` → `price=float(current_price)`.

안전 원칙(CLAUDE.md — 실계좌 프로젝트):
  이 프로젝트에는 별도 테스트 DB가 없다(.env의 POSTGRES_DB에 'test'가 없고
  ALLOW_DESTRUCTIVE_RESEARCH_TESTS도 미설정 — tests/test_decision_service.py의
  database_guard가 이미 이 상태에서 모든 DB 테스트를 SkipTest로 건너뛰고 있다).
  따라서 이 파일은 **실제 라이브 DB에 INSERT/COMMIT을 절대 하지 않는다.**
  psycopg2의 타입 어댑테이션 자체는 cursor.mogrify()(실행/커밋 없이 SQL 문자열만
  렌더링)로 검증한다 — 이건 create_candidate()가 겪는 것과 동일한 어댑터 계층을
  타면서도 DB에 아무것도 쓰지 않는다.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from dotenv import load_dotenv
load_dotenv()

import psycopg2

from utils.database_guard import DestructiveOperationBlocked, assert_destructive_allowed


def _get_readonly_conn():
    """mogrify 전용 — 실제 실행/커밋 없음. 그래도 라이브 DB 보호 원칙을 동일하게 지킨다
    (연결 자체는 하되, 아래 모든 테스트는 execute()/commit()을 호출하지 않는다)."""
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


# ─── §3/§6 — psycopg2 타입 어댑테이션 자체 (create_candidate와 동일 계층) ──────
class TestPsycopg2TypeAdaptation(unittest.TestCase):
    """create_candidate()가 실제로 겪는 psycopg2 파라미터 바인딩 계층을 그대로
    재현한다. mogrify()는 SQL을 문자열로 만들 뿐 실행하지 않으므로 DB에 어떤
    영향도 주지 않는다 — INSERT/COMMIT 없음."""

    @classmethod
    def setUpClass(cls):
        try:
            cls.conn = _get_readonly_conn()
        except Exception as e:
            raise unittest.SkipTest(f"DB 연결 불가(오프라인 환경 등): {e}")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "conn"):
            cls.conn.close()

    def _mogrify_price(self, price):
        with self.conn.cursor() as cur:
            return cur.mogrify("SELECT %s::numeric", (price,))

    def test_A_python_float_adapts(self):
        self._mogrify_price(123.0)  # 예외 없으면 PASS

    def test_B_numpy_float64_adapts(self):
        self._mogrify_price(np.float64(123.0))

    def test_C_numpy_int64_FAILS_TO_ADAPT(self):
        """이게 이번 결함을 재현하는 핵심 테스트다(§3 C).
        수정 전/후 무관하게 psycopg2 자체의 근본 제약이므로 항상 실패해야
        정상이다 — 그래서 이 테스트는 '실패를 기대'한다. 이게 바로 §7/§9가
        요구한 '호출부에서 항상 캐스팅해야 하는 이유'의 증거다."""
        with self.assertRaises(psycopg2.ProgrammingError) as ctx:
            self._mogrify_price(np.int64(123))
        self.assertIn("can't adapt type", str(ctx.exception))
        self.assertIn("numpy.int64", str(ctx.exception))

    def test_D_python_int_adapts(self):
        self._mogrify_price(123)

    def test_E_zero_adapts(self):
        self._mogrify_price(0.0)

    def test_F_none_adapts_as_null(self):
        self._mogrify_price(None)


# ─── §5 — SMC 실제 경로 회귀: df['close']가 int64여도 begin_evaluation은 float로 받는다 ──
from tests.unit.test_check_entry_signal_silent_drop import (  # noqa: E402
    _make_entry_signal_stub, _FixedDateTime,
)


def _make_1min_df_int64_close(n: int = 300, price: int = 75_000) -> pd.DataFrame:
    """실제 운영 데이터를 재현: 원화 정수가 컬럼 dtype을 자연스럽게 int64로 만든다."""
    prices = [price + i for i in range(n)]  # 정수 연산 → int64 유지
    times = pd.date_range("2026-08-11 09:00", periods=n, freq="1min")
    df = pd.DataFrame(
        {
            "open":   prices,
            "high":   [p + 100 for p in prices],
            "low":    [p - 100 for p in prices],
            "close":  prices,
            "volume": [50_000] * n,
        },
        index=times,
    )
    assert df["close"].dtype == np.int64, f"fixture 자체가 int64가 아님: {df['close'].dtype}"
    return df


class TestSmcPathNumpyInt64Regression(unittest.TestCase):
    """이게 실제 버그를 재현했어야 할 시나리오다 — 수정 전이었다면
    begin_evaluation(price=<numpy.int64>)로 호출돼 create_candidate()가
    'can't adapt type' 로 실패했을 상황과 동일 입력이다."""

    def test_smc_path_passes_native_float_even_with_int64_close_column(self):
        stub = _make_entry_signal_stub()
        df = _make_1min_df_int64_close()

        with patch.object(stub, "_check_global_risk_gates", return_value=(True, "")), \
             patch("main_auto_trading.datetime", _FixedDateTime):
            import asyncio
            asyncio.run(stub.check_entry_signal("005930", kiwoom_df=df))

        self.assertGreaterEqual(
            stub.decision_service.begin_evaluation.call_count, 1,
            "begin_evaluation 미호출 — SMC 경로 회귀",
        )
        _, kwargs = stub.decision_service.begin_evaluation.call_args
        price_passed = kwargs.get("price")
        self.assertIsInstance(
            price_passed, float,
            f"price가 float로 캐스팅되지 않은 채 전달됨: {type(price_passed)} = {price_passed!r} "
            f"— main_auto_trading.py:6646 수정이 되돌아갔을 가능성",
        )
        # float임을 확인하는 것만으로는 numpy.float64도 통과할 수 있다(bool(isinstance)는
        # numpy.float64가 float의 서브클래스라 True) — 진짜 native Python float인지 별도 확인.
        self.assertIs(
            type(price_passed), float,
            f"price가 numpy 서브클래스({type(price_passed)})로 전달됨 — "
            f"psycopg2 어댑테이션 실패 재발 위험 (native float만 안전)",
        )


if __name__ == "__main__":
    unittest.main()
