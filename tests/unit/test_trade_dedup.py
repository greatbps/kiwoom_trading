"""
거래 중복 방어 + 주문번호 보존 회귀 테스트 (Iteration 8-3)

━━━ 왜 필요한가 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  2025-11 에 동일 청산이 74건 중복 기록됐다 (신테카바이오 58건,
  30초 간격). 감시 루프 주기와 같은 간격이라 execute_sell 이 반복
  호출되며 매번 INSERT 된 것으로 보인다.

  execute_sell 에 [SELL_DEDUP] 가드가 있으나
  `if _entry_time_str and self.db.has_sell_for_entry(...)` 이라
  **entry_time 이 없으면 통째로 건너뛴다.** entry_time 은 411행 중
  27행에만 있다.

  ⚠️ 이 결함은 예외를 내지 않는다. 원장이 조용히 부풀 뿐이다.
"""
from __future__ import annotations

import ast
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

DB = os.path.join(ROOT, 'database', 'trading_db.py')
SRC = open(DB, encoding='utf-8').read()


def test_dedup_guard_exists_in_storage_layer():
    """
    방어는 저장 계층에 있어야 한다. 호출부가 여러 곳이고
    호출부 가드는 entry_time 이 있을 때만 동작한다.
    """
    assert '_is_duplicate_trade' in SRC
    i = SRC.index('def insert_trade')
    body = SRC[i:i + 1500]
    assert '_is_duplicate_trade' in body, 'insert_trade 가 중복 검사를 안 한다'


def test_dedup_has_time_window():
    """
    ⚠️ 시간 창이 없으면 정상 거래를 막는다.
       같은 종목을 같은 가격·수량으로 하루 두 번 거래하는 건 정상이다.
    """
    assert '_DUP_WINDOW_SEC' in SRC
    from database.trading_db import TradingDatabase
    assert 0 < TradingDatabase._DUP_WINDOW_SEC <= 3600


def test_dedup_fails_open_not_closed():
    """
    ⚠️ 중복 검사가 실패하면 **통과**시켜야 한다.
       거래 기록을 잃는 것이 중복 하나보다 나쁘다.
    """
    i = SRC.index('def _is_duplicate_trade')
    body = SRC[i:i + 1800]
    assert 'except Exception' in body
    assert 'return False' in body.split('except Exception')[1][:300], (
        '검사 실패 시 True 를 반환하면 정상 거래가 차단된다'
    )


def test_order_no_preserved():
    """
    execute_buy 가 order_no 를 보내는데 INSERT 가 버리고 있었다.
    entry_context(JSONB)에 접어 넣어 컬럼 추가 없이 보존한다.
    """
    assert '_fold_order_no' in SRC
    i = SRC.index('def _fold_order_no')
    body = SRC[i:i + 1200]
    assert "'exit_order_no'" in body and "'order_no'" in body
    assert 'setdefault' in body, '기존 값을 덮어쓰면 안 된다'


def test_existing_entry_context_not_discarded():
    """
    ⚠️ 조건검색 출처(Iteration 8-1)가 entry_context 에 들어 있다.
       order_no 를 넣으면서 그걸 버리면 8-1 작업이 무효가 된다.
    """
    i = SRC.index('def _fold_order_no')
    body = SRC[i:i + 1200]
    assert 'ctx = trade_data.get(' in body
    assert '_original' in body, '비-dict 기존값도 보존해야 한다'


def test_logger_defined():
    """
    ⚠️ 이 모듈에 logger 가 없었다. 경고 로그가 NameError 를 내면
       거래 기록이 통째로 실패한다.
    """
    assert 'logger = logging.getLogger' in SRC
    assert 'import logging' in SRC


def test_no_ddl_executed():
    """§5 — ALTER TABLE / ADD COLUMN / CREATE TABLE 금지."""
    i = SRC.index('def _fold_order_no')
    j = SRC.index('def insert_trade')
    seg = SRC[min(i, j):max(i, j) + 3000].upper()
    for bad in ('ALTER TABLE', 'ADD COLUMN'):
        assert bad not in seg, f'DDL 실행: {bad}'


def test_blocked_returns_zero_not_exception():
    """
    중복 차단은 예외가 아니라 0 을 돌려준다.
    예외를 던지면 호출부(execute_sell)가 중단되어 청산 흐름이 깨진다.
    """
    i = SRC.index('def insert_trade')
    body = SRC[i:i + 1500]
    assert 'return 0' in body
    assert 'raise' not in body.split('_is_duplicate_trade')[1][:400]
