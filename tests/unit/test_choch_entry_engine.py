"""
CHoCH 진입 엔진 회귀 테스트 (Entry Parity Migration)

━━━ 무엇을 지키는가 ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  ① 기본값이 현행(pullback)이다 — 검증 전에 조용히 바뀌면 안 된다
  ② 오타는 예외를 낸다 — 알 수 없는 값이 fallback 되면 가장 위험하다
  ③ CHoCH 판정을 재구현하지 않는다 — backtest.adapter 를 그대로 쓴다
     (재구현하면 Live 와 백테스트가 또 갈라진다. 그게 이 문제의 원인이었다)
  ④ SignalEngine 과 같은 키를 돌려준다 — swing_runner 호출부 호환
  ⑤ stop 이 반드시 있다 — structure_stop_price 가 여기서 나온다
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from analyzers.swing.choch_engine import ChochSignalEngine  # noqa: E402


def _df(n=120, seed=0):
    rng = np.random.default_rng(seed)
    c = 10000 + np.cumsum(rng.normal(0, 120, n))
    return pd.DataFrame({
        'open': c, 'high': c * 1.01, 'low': c * 0.99, 'close': c,
        'volume': rng.integers(1e5, 1e6, n).astype(float),
    }, index=pd.date_range('2025-01-01', periods=n, freq='B'))


def test_default_engine_is_pullback():
    """⚠️ 기본값이 바뀌면 검증하지 않은 규칙이 실거래에 나간다."""
    import swing_runner as sr
    _, name = sr._entry_engine(_df(), {})
    assert name == 'pullback'


def test_choch_selected_only_when_explicit():
    import swing_runner as sr
    _, name = sr._entry_engine(_df(), {'swing': {'entry_engine': 'choch'}})
    assert name == 'choch'


def test_unknown_engine_raises():
    """오타로 조용히 다른 엔진이 도는 것이 가장 위험하다."""
    import swing_runner as sr
    with pytest.raises(ValueError):
        sr._entry_engine(_df(), {'swing': {'entry_engine': 'chcoh'}})


def test_no_duplicate_choch_implementation():
    """
    ⚠️ CHoCH 를 여기서 다시 구현하면 Live 와 백테스트가 또 갈라진다.
       backtest.adapter.SMCAdapter 를 호출하는지 소스로 고정한다.
    """
    src = open(os.path.join(ROOT, 'analyzers', 'swing', 'choch_engine.py'),
               encoding='utf-8').read()
    assert 'from backtest.adapter import SMCAdapter' in src
    assert 'BEST_ADAPTER_KWARGS' in src, '필터 파라미터도 백테스트 것을 써야 한다'
    for bad in ('def _find_pivots', 'def _is_bearish_structure', 'choch =' ):
        assert bad not in src, f'CHoCH 를 재구현하고 있다: {bad}'


def _first_real_signal():
    """캐시된 실데이터에서 첫 CHoCH 신호 하나."""
    try:
        from phase0 import data_cache as dc
        data = dc.load_ohlcv()
    except Exception:
        pytest.skip('phase0 캐시 없음')
    for sym, df in data.items():
        for i in range(60, min(len(df), 400)):
            s = ChochSignalEngine(df.iloc[:i + 1], {}).run()
            if s:
                return s
    return None


def test_signal_shape_matches_signal_engine():
    """swing_runner 호출부가 쓰는 키가 전부 있어야 한다."""
    from analyzers.swing.signal_engine import SignalEngine
    need = {'pattern', 'score', 'final_score', 'size', 'trigger', 'phase',
            'entry', 'stop', 'target', 'confidence', 'meta'}
    # ⚠️ 난수 시계열로는 CHoCH 가 안 나서 skip 되기 쉽다. 실제 캐시로
    #    한 건이라도 잡아 형태를 확인한다 — skip 되는 테스트는 지키는 게 없다.
    sig = _first_real_signal()
    assert sig is not None, '캐시 전 구간에서 CHoCH 신호가 하나도 없다'
    assert need <= set(sig), f'누락 키: {need - set(sig)}'
    assert need <= set(sig), f'누락 키: {need - set(sig)}'
    assert sig['final_score'] >= SignalEngine.MIN_FINAL_SCORE


def test_stop_is_below_entry():
    """
    ⚠️ stop 이 entry 이상이면 편입 즉시 전량청산된다.
       structure_stop_price 가 이 값에서 나온다.
    """
    for seed in range(30):
        sig = ChochSignalEngine(_df(seed=seed), {}).run()
        if sig:
            assert 0 < sig['stop'] < sig['entry'], sig
            assert sig['target'] > sig['entry']


def test_short_series_returns_none():
    """봉이 모자라면 신호를 만들지 않는다 (fail-closed)."""
    assert ChochSignalEngine(_df(n=30), {}).run() is None
    assert ChochSignalEngine(None, {}).run() is None


def test_score_is_documented_as_non_predictive():
    """
    점수에 예측력이 있다고 주장하면 안 된다.
    Phase 0.1 · Iter1 이 순위·연속스코어 모두 예측력 없음을 측정했다.
    """
    src = open(os.path.join(ROOT, 'analyzers', 'swing', 'choch_engine.py'),
               encoding='utf-8').read()
    assert '순위 신호가 아니다' in src
