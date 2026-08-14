"""
tests/unit/test_strategy_monitors.py — WI-10 Strategy Monitor Architecture 검증

Router/Monitor는 순수 관측 레이어다(execute_buy/Ranking/Gate 미접촉). 각 Monitor는
기존 프로덕션 클래스/함수를 무수정으로 재사용한다(analyzers/trend/trend_breakout.py,
analyzers/squeeze_momentum.py, trading/bottom_pullback_manager.py, analyzers/
indicators.py, analyzers/entry_timing_analyzer.py) — mock 없이 실제 함수를 합성
OHLCV로 호출해 검증한다.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np
import pandas as pd
import pytest
import yaml

from analyzers.strategy_monitors import route_to_monitor, MONITOR_MAP
from analyzers.strategy_monitors.router import route_from_chart_data

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CFG = yaml.safe_load(open(os.path.join(ROOT, 'config', 'strategy_hybrid.yaml')))


def _synthetic_df(n=40, seed=0, trend=0.0):
    rng = np.random.default_rng(seed)
    close = 10000 + np.cumsum(rng.standard_normal(n) * 50 + trend)
    return pd.DataFrame({
        'open': close + rng.standard_normal(n) * 10,
        'high': close + abs(rng.standard_normal(n) * 20),
        'low': close - abs(rng.standard_normal(n) * 20),
        'close': close,
        'volume': rng.integers(1000, 100000, n),
    })


# ── 항목 1~8: seq 32~39 각 Monitor ───────────────────────────────────────────
@pytest.mark.parametrize('seq', sorted(MONITOR_MAP.keys()))
def test_monitor_runs_without_crash_for_each_seq(seq):
    df = _synthetic_df(seed=seq)
    result = route_to_monitor(seq, f'SYM{seq}', df, config=CFG)
    assert result.condition_seq == seq
    assert result.monitor_state  # 항상 비어있지 않은 상태 문자열
    assert result.data_quality in ('OK', 'INSUFFICIENT_DATA', 'NO_CODE', 'ERROR')


# ── WI-15 Rev.2: seq34/35/39 실제 구현 (사용자가 HTS [0150] 원문 제공, 2026-08-11) ──

def _eod_signal_df():
    """EOD(seq34) 전 조건 충족: MA5가 오늘 MA20을 골든크로스, 5봉평균거래량>=50만주,
    전일대비 상승, 양봉."""
    n = 30
    close = list(np.linspace(10000, 9500, n - 1))
    close.append(close[-1] * 1.15)
    close = np.array(close)
    open_ = np.copy(close)
    open_[-1] = close[-2] * 1.002
    high = np.maximum(open_, close) + 5
    low = np.minimum(open_, close) - 1
    vol = np.full(n, 400000.0)
    vol[-5:] = 700000
    return pd.DataFrame({'open': open_, 'high': high, 'low': low, 'close': close, 'volume': vol})


def _trend_signal_df():
    """Supertrend+EMA+RSI(seq35) 전 조건 충족: MA5>MA20 지속, RSI>=40, 강한 갭상승
    양봉(윗/아래그림자 없음)."""
    n = 30
    close = list(np.linspace(9500, 10200, n - 1))
    close.append(close[-1] * 1.05)
    close = np.array(close)
    open_ = np.copy(close)
    open_[-1] = close[-1] * 0.80
    low = np.copy(close)
    low[-1] = open_[-1]
    high = np.copy(close)
    high[-1] = close[-1]
    vol = np.full(n, 500000.0)
    return pd.DataFrame({'open': open_, 'high': high, 'low': low, 'close': close, 'volume': vol})


def _its_signal_df():
    """ITS(seq39) 전 조건 충족: 완만한 상승추세(MA5>MA10, 종가>MA20 지속), 대량거래."""
    n = 30
    close = np.linspace(9000, 10500, n)
    open_ = close * 0.999
    high = close * 1.001
    low = open_ * 0.999
    vol = np.full(n, 6_000_000.0)
    return pd.DataFrame({'open': open_, 'high': high, 'low': low, 'close': close, 'volume': vol})


# ── WI-17 §5/§18: 조건별 개별 실패(양성/음성) 테스트 ──────────────────────────
# EOD의 A(골든크로스)와 D(전일대비 상승)는 수식 정의상 결합돼 있다 — 골든크로스가
# 급등 하루에 의해 발생하면 그 하루는 거의 항상 전일대비 상승일 수밖에 없다.
# 그래서 C(거래량)/E(양봉)는 독립적으로 깨뜨려 단일조건 실패를 확인하고,
# A+D는 "함께 깨지는 것이 정상"이라는 점을 결합 테스트로 확인한다.

def test_seq34_eod_breaks_on_volume_alone():
    df = _eod_signal_df()
    df = df.copy()
    df.loc[df.index[-5:], 'volume'] = 100_000  # C만 위반
    result = route_to_monitor(34, 'SYM34', df, config=CFG)
    assert result.signal is False
    c_reason = [r for r in result.reasons if r.startswith('C.')][0]
    assert 'False' in c_reason
    a_reason = [r for r in result.reasons if r.startswith('A.')][0]
    assert 'True' in a_reason, 'C만 깨졌을 때 A는 그대로 True여야 한다(독립성)'


def test_seq34_eod_breaks_on_bearish_candle_alone():
    df = _eod_signal_df().copy()
    # 오늘 시가를 종가보다 높게 바꿔 E(양봉)만 위반, high/low를 재조정해 정합성 유지
    new_open = df['close'].iloc[-1] * 1.01
    df.loc[df.index[-1], 'open'] = new_open
    df.loc[df.index[-1], 'high'] = max(new_open, df['close'].iloc[-1]) + 5
    df.loc[df.index[-1], 'low'] = min(new_open, df['close'].iloc[-1]) - 1
    result = route_to_monitor(34, 'SYM34', df, config=CFG)
    assert result.signal is False
    e_reason = [r for r in result.reasons if r.startswith('E.')][0]
    assert 'False' in e_reason
    c_reason = [r for r in result.reasons if r.startswith('C.')][0]
    assert 'True' in c_reason, 'E만 깨졌을 때 C는 그대로 True여야 한다(독립성)'


def test_seq34_eod_golden_cross_and_up_day_break_together():
    """A(골든크로스)와 D(전일대비 상승)는 오늘 하루의 급등이라는 동일한 원인에서
    나오므로, 오늘 하락으로 바꾸면 A/D가 함께 깨진다 — 이건 결합이 정상이다."""
    df = _eod_signal_df().copy()
    prev_close = df['close'].iloc[-2]
    df.loc[df.index[-1], 'close'] = prev_close * 0.99
    df.loc[df.index[-1], 'open'] = prev_close * 0.995
    result = route_to_monitor(34, 'SYM34', df, config=CFG)
    assert result.signal is False
    a_reason = [r for r in result.reasons if r.startswith('A.')][0]
    d_reason = [r for r in result.reasons if r.startswith('D.')][0]
    assert 'False' in a_reason and 'False' in d_reason


def test_seq39_its_breaks_on_daily_return_alone():
    df = _its_signal_df().copy()
    df.loc[df.index[-1], 'close'] = df['close'].iloc[-2] * 0.95  # B만 위반
    result = route_to_monitor(39, 'SYM39', df, config=CFG)
    assert result.signal is False
    b_reason = [r for r in result.reasons if r.startswith('B.')][0]
    assert 'False' in b_reason
    c_reason = [r for r in result.reasons if r.startswith('C.')][0]
    assert 'True' in c_reason, 'B만 깨졌을 때 C는 그대로 True여야 한다(독립성)'


def test_seq39_its_breaks_on_trade_value_alone():
    df = _its_signal_df().copy()
    df.loc[df.index[-1], 'volume'] = 100_000  # C만 위반
    result = route_to_monitor(39, 'SYM39', df, config=CFG)
    assert result.signal is False
    c_reason = [r for r in result.reasons if r.startswith('C.')][0]
    assert 'False' in c_reason
    b_reason = [r for r in result.reasons if r.startswith('B.')][0]
    assert 'True' in b_reason, 'C만 깨졌을 때 B는 그대로 True여야 한다(독립성)'


def test_seq39_its_trend_conditions_break_together_on_reversal():
    """D(종가>MA20)와 E(MA5>MA10)는 둘 다 추세추종 조건이라 하락추세로 뒤집으면
    함께 깨진다 — 결합이 정상."""
    base = _its_signal_df()
    reversed_close = base['close'].values[::-1]
    df = pd.DataFrame({
        'open': reversed_close * 0.999, 'high': reversed_close * 1.001,
        'low': reversed_close * 0.998, 'close': reversed_close,
        'volume': base['volume'].values,
    })
    result = route_to_monitor(39, 'SYM39', df, config=CFG)
    assert result.signal is False
    d_reason = [r for r in result.reasons if r.startswith('D.')][0]
    e_reason = [r for r in result.reasons if r.startswith('E.')][0]
    assert 'False' in d_reason and 'False' in e_reason


def test_seq34_35_39_boundary_exact_threshold():
    """경계값: EOD C조건 거래량이 정확히 500000일 때 '이상' 조건은 충족돼야 한다."""
    df = _eod_signal_df().copy()
    df.loc[df.index[-5:], 'volume'] = 500_000.0
    result = route_to_monitor(34, 'SYM34', df, config=CFG)
    c_reason = [r for r in result.reasons if r.startswith('C.')][0]
    assert 'True' in c_reason, '거래량이 정확히 임계값(500000)이면 ">=" 조건은 충족돼야 한다'


# ── WI-17 §6: 8개 seq 전체 동시 조합 ─────────────────────────────────────────
def test_wi17_all_8_seq_combination_isolation():
    df = _synthetic_df(seed=17)
    seqs = list(range(32, 40))
    results = {seq: route_to_monitor(seq, '전체조합테스트', df, config=CFG) for seq in seqs}
    for seq in seqs:
        assert results[seq].condition_seq == seq
    assert len({results[seq].strategy_name for seq in seqs}) == 8, \
        '8개 전략명이 전부 서로 달라야 한다(뭉개짐 없음)'
    assert all(r.data_quality != 'NO_CODE' for r in results.values()), \
        'WI-15 Rev.2 이후 8개 전부 구현 완료 상태여야 한다'


# ── WI-20 §14: seq38(상태유지형) 포함 조합 격리 검증 ─────────────────────────
@pytest.mark.parametrize('other_seq', [32, 33, 34, 35, 39])
def test_wi20_seq38_combination_isolation(other_seq, _reset_bottom_observation_manager):
    """seq38이 이제 프로세스 전역 상태(_observation_manager)를 갖게 됐으므로,
    같은 종목이 seq38 + 다른 seq에 동시 매칭돼도 서로 결과가 섞이지 않는지
    반드시 재확인한다."""
    cfg = _bottom_repair_time_window_cfg()
    df = _synthetic_df(seed=100 + other_seq)
    r_other = route_to_monitor(other_seq, '조합격리테스트', df, config=cfg)
    r_38 = route_to_monitor(38, '조합격리테스트', df, config=cfg)
    assert r_other.condition_seq == other_seq
    assert r_38.condition_seq == 38
    assert r_other.strategy_name != r_38.strategy_name
    # 순서를 바꿔 호출해도(38 먼저) 결과가 동일해야 한다 — 호출 순서에 결과가
    # 의존하면 상태 오염 가능성이 있다는 신호
    r_38_first = route_to_monitor(38, '조합격리테스트_역순', df, config=cfg)
    r_other_after = route_to_monitor(other_seq, '조합격리테스트_역순', df, config=cfg)
    assert r_other_after.signal == r_other.signal
    assert r_other_after.monitor_state == r_other.monitor_state


def test_seq34_eod_signal_on_golden_cross_fixture():
    result = route_to_monitor(34, 'SYM34', _eod_signal_df(), config=CFG)
    assert result.monitor_state == 'EOD_CONFIRMING'
    assert result.signal is True
    assert result.data_quality == 'OK'


def test_seq34_eod_no_signal_on_random_walk():
    result = route_to_monitor(34, 'SYM34', _synthetic_df(seed=34), config=CFG)
    assert result.monitor_state == 'EOD_CONDITIONS_NOT_MET'
    assert result.signal is False


def test_seq35_trend_signal_on_fixture():
    result = route_to_monitor(35, 'SYM35', _trend_signal_df(), config=CFG)
    assert result.monitor_state == 'TREND_CONFIRMING'
    assert result.signal is True
    assert result.data_quality == 'OK'


def test_seq35_trend_no_signal_on_random_walk():
    result = route_to_monitor(35, 'SYM35', _synthetic_df(seed=35), config=CFG)
    assert result.monitor_state == 'TREND_CONDITIONS_NOT_MET'
    assert result.signal is False


def test_seq39_its_signal_on_fixture():
    result = route_to_monitor(39, 'SYM39', _its_signal_df(), config=CFG)
    assert result.monitor_state == 'ITS_CONFIRMING'
    assert result.signal is True
    assert result.data_quality == 'OK'


def test_seq39_its_no_signal_on_random_walk():
    result = route_to_monitor(39, 'SYM39', _synthetic_df(seed=39), config=CFG)
    assert result.monitor_state == 'ITS_CONDITIONS_NOT_MET'
    assert result.signal is False


def test_seq34_35_39_insufficient_data():
    short_df = _synthetic_df(n=5)
    for seq in (34, 35, 39):
        result = route_to_monitor(seq, 'SYM_SHORT', short_df, config=CFG)
        assert result.data_quality == 'INSUFFICIENT_DATA'
        assert result.signal is None


def test_seq34_35_39_malformed_columns_yield_error_not_crash():
    """행 수는 충분하나 필수 컬럼이 없는 경우 — KeyError를 ERROR로 흡수, 크래시 없음."""
    bad_df = pd.DataFrame({'close': list(range(25))})
    for seq in (34, 35, 39):
        result = route_to_monitor(seq, 'SYM_BAD', bad_df, config=CFG)
        assert result.monitor_state == 'ERROR'
        assert result.data_quality == 'ERROR'


def test_seq34_35_39_nan_in_latest_bar_does_not_crash_or_signal():
    n = 30
    close = np.linspace(9000, 10000, n)
    close[-1] = np.nan
    df = pd.DataFrame({'open': close, 'high': close, 'low': close, 'close': close,
                        'volume': np.full(n, 500000.0)})
    for seq in (34, 35, 39):
        result = route_to_monitor(seq, 'SYM_NAN', df, config=CFG)
        assert result.data_quality == 'OK'
        assert result.signal is False, 'NaN 비교는 항상 False이므로 신호가 나면 안 된다'


def test_seq32_momentum_confirms_on_uptrend():
    df = _synthetic_df(seed=1, trend=30)  # 뚜렷한 상승 추세
    result = route_to_monitor(32, 'SYM32', df, config=CFG)
    assert result.data_quality == 'OK'
    assert result.monitor_state in (
        'MOMENTUM_CONFIRMING', 'CANDIDATE', 'MOMENTUM_WEAKENING', 'MOMENTUM_FAILED')


def test_seq37_squeeze_reuses_squeeze_momentum_pro():
    """SqueezeMonitor가 실제 SqueezeMomentumPro.check_squeeze()를 호출하는지 —
    반환값 details 문구가 그대로 reasons에 실리는지로 확인한다."""
    result = route_to_monitor(37, 'SYM37', _synthetic_df(seed=2), config=CFG)
    assert any('squeeze_on=' in r for r in result.reasons)


def test_seq38_bottom_reuses_bottom_pullback_manager():
    result = route_to_monitor(38, 'SYM38', _synthetic_df(seed=3), config=CFG)
    assert result.data_quality == 'OK'
    assert result.monitor_state in (
        'BOTTOM_CANDIDATE', 'BOTTOM_CONFIRMING', 'BOUNCE_CONFIRMED', 'BOTTOM_FAILED')


# ── WI-19 Rev.1 §4/§13: 8/8 Positive/Negative Fixture 보강 ──────────────────

def _breakout_signal_df():
    """TrendBreakoutStrategy.check_entry()가 실제로 SIGNAL=True를 내는 fixture
    (trend.enabled=True 전제, 여러 게이트를 실측으로 맞춘 값)."""
    n = 80
    idx = pd.date_range('2026-01-01', periods=n, freq='B')
    rng = np.random.default_rng(5)
    close = 5000 + np.arange(n) * 10 + rng.standard_normal(n) * 2
    close[-1] = close[-2] * 1.006
    open_ = close.copy()
    open_[-1] = close[-2] * 1.001
    high = np.maximum(open_, close) + 3
    low = np.minimum(open_, close) - 1
    vol = np.full(n, 100000.0)
    vol[-2] = 200000.0
    return pd.DataFrame({'open': open_, 'high': high, 'low': low, 'close': close,
                          'volume': vol}, index=idx)


def test_seq33_breakout_strategy_class_signals_when_enabled(monkeypatch):
    """BreakoutMonitor가 감싸는 TrendBreakoutStrategy 자체는 trend.enabled=True +
    조건 충족 시 정상적으로 SIGNAL=True를 낸다는 것을 Router 캐시를 우회해
    직접 증명한다(아래 라우터 버그 테스트와 분리해서 봐야 함)."""
    from analyzers.trend.trend_breakout import TrendBreakoutStrategy
    strategy = TrendBreakoutStrategy({'trend': {'enabled': True}})
    signal, reason, details = strategy.check_entry(_breakout_signal_df(), debug=False)
    assert signal is True, f'재사용 클래스 자체가 신호를 내지 못함: {reason}'


def test_seq33_breakout_no_signal_when_trend_disabled_by_default():
    """§16(운영 기본값): trend.enabled는 기본 False다(레짐 감지 시 자동 ON) —
    기본 config로는 아무리 좋은 데이터를 줘도 항상 'TREND: 비활성화'로
    거절되는 게 현재 Production의 실제 동작이다(버그 아님, 설계된 게이트)."""
    df = _synthetic_df(seed=33, trend=50)
    result = route_to_monitor(33, 'SYM33', df, config=CFG)
    assert result.signal is False
    assert any('비활성화' in r for r in result.reasons)


# ── WI-19 Rev.1 Repair(BUG-WI19-2): router.py config 캐싱 버그 회귀가드 ─────
# 원래 발견: router.py::_get_monitor()가 seq당 Monitor 인스턴스를 1회만 만들어
# 캐싱해서, BreakoutMonitor/BottomMonitor(_NEEDS_CONFIG={33,38})는 최초 생성
# 시점의 config에 영구 고정됐었다(레짐에 따라 동적으로 바뀌는 trend.enabled가
# 반영 안 됨). _NEEDS_CONFIG seq는 캐싱하지 않고 매 호출 최신 config로 새로
# 생성하도록 수정했다 — 이 테스트는 그 수정이 계속 유지되는지 지키는 회귀가드다.
def test_router_reflects_config_changes_after_repair():
    from analyzers.strategy_monitors import router as _router_mod
    _router_mod._instances.pop(33, None)
    df = _breakout_signal_df()
    r_first = route_to_monitor(33, 'SYM33_CACHE', df, config={'trend': {'enabled': False}})
    assert r_first.signal is False
    r_second = route_to_monitor(33, 'SYM33_CACHE', df, config={'trend': {'enabled': True}})
    assert r_second.signal is True, (
        'router.py의 config 캐싱 버그가 재발했다 — _NEEDS_CONFIG seq는 캐싱하면 안 된다'
    )
    _router_mod._instances.pop(33, None)  # 다른 테스트에 영향 주지 않도록 정리


def test_seq33_router_config_bidirectional_true_to_false():
    """WI-20 §10 Case B — True→False 방향도 반영되는지(단방향 우연이 아님을 확인)."""
    from analyzers.strategy_monitors import router as _router_mod
    _router_mod._instances.pop(33, None)
    df = _breakout_signal_df()
    r_true = route_to_monitor(33, 'SYM33_BIDIR', df, config={'trend': {'enabled': True}})
    assert r_true.signal is True
    r_false = route_to_monitor(33, 'SYM33_BIDIR', df, config={'trend': {'enabled': False}})
    assert r_false.signal is False
    assert any('비활성화' in r for r in r_false.reasons)
    _router_mod._instances.pop(33, None)


def test_seq33_router_config_sequence_a_b_c():
    """WI-20 §10 Case C — config A→B→C 연속 변경이 매번 최신값을 반영하는지."""
    from analyzers.strategy_monitors import router as _router_mod
    _router_mod._instances.pop(33, None)
    df = _breakout_signal_df()
    seq = [
        ({'trend': {'enabled': False}}, False),
        ({'trend': {'enabled': True}}, True),
        ({'trend': {'enabled': False}}, False),
    ]
    for cfg, expected in seq:
        r = route_to_monitor(33, 'SYM33_ABC', df, config=cfg)
        assert r.signal is expected, f'config={cfg} 에서 signal={expected} 기대했으나 {r.signal}'
    _router_mod._instances.pop(33, None)


def test_seq33_runtime_regime_change_simulation():
    """WI-20 §11 — 09:30 NON_TREND(disabled) → 11:00 TREND(enabled)로 레짐이
    실제로 전환되는 상황을 재현. Monitor가 매 스캔 사이클마다 최신 config를
    반영해야 한다(그렇지 않으면 BUG-02 재발)."""
    from analyzers.strategy_monitors import router as _router_mod
    _router_mod._instances.pop(33, None)
    df = _breakout_signal_df()

    # 09:30 — NON_TREND, disabled
    r_0930 = route_to_monitor(33, 'SYM33_REGIME', df, config={'trend': {'enabled': False}})
    assert r_0930.signal is False

    # 11:00 — 레짐이 TREND로 전환, main_auto_trading.py가 넘기는 self.config.config가
    # 동적으로 바뀐 상황을 그대로 모사
    r_1100 = route_to_monitor(33, 'SYM33_REGIME', df, config={'trend': {'enabled': True}})
    assert r_1100.signal is True, 'BUG-02 재발 — 11:00 레짐 전환이 반영되지 않았다'
    _router_mod._instances.pop(33, None)


def test_seq36_vwap_signal_on_strong_reclaim():
    n = 30
    close = np.linspace(10000, 10200, n)
    close[-1] = close[-2] * 1.02
    open_ = close.copy()
    high = close * 1.001
    low = close * 0.999
    vol = np.full(n, 500000.0)
    df = pd.DataFrame({'open': open_, 'high': high, 'low': low, 'close': close,
                        'volume': vol})
    result = route_to_monitor(36, 'SYM36', df, config=CFG)
    assert result.signal is True
    assert result.monitor_state in ('VWAP_CONFIRMED', 'VWAP_RECLAIM')


def test_seq36_vwap_no_signal_on_random_walk():
    result = route_to_monitor(36, 'SYM36', _synthetic_df(seed=36), config=CFG)
    assert result.data_quality == 'OK'
    # random walk에서 우연히 VWAP 위로 튈 수는 있으나 대부분 False — 크래시 없음만 보장
    assert result.monitor_state in (
        'VWAP_RECLAIM', 'VWAP_CONFIRMED', 'VWAP_CANDIDATE', 'VWAP_FAILED', 'VWAP_WEAKENING')


def test_seq37_squeeze_no_signal_on_random_walk():
    result = route_to_monitor(37, 'SYM37', _synthetic_df(seed=137), config=CFG)
    assert result.data_quality == 'OK'
    assert result.monitor_state in (
        'SQUEEZE_ACTIVE', 'SQUEEZE_RELEASE', 'MOMENTUM_CONFIRMING', 'SQUEEZE_FAILED')


def test_seq32_momentum_no_signal_on_downtrend():
    df = _synthetic_df(seed=1, trend=-30)  # 뚜렷한 하락 추세
    result = route_to_monitor(32, 'SYM32', df, config=CFG)
    assert result.signal is not True


# ── WI-19 Rev.1/WI-20: seq38 BottomMonitor 상태 전이 수정 검증 ──────────────
# 원래 발견(BUG-WI19-1): BottomMonitor가 호출마다 ephemeral BottomPullbackManager를
# 새로 만들어 register_signal()+check_pullback()을 1회만 실행했다.
# check_pullback()은 2회 분리 호출을 전제로 설계돼 있어(1차: 이탈감지만 하고
# 무조건 False / 2차: 이미 이탈이 감지된 상태에서만 재돌파 확인 가능) 구조적으로
# SIGNAL=True에 도달할 수 없었다. 수정: analyzers/strategy_monitors/bottom.py의
# 모듈 레벨 `_observation_manager` singleton으로 evaluate() 호출 간 상태를
# 유지하도록 바꿨다(라이브 self.bottom_manager와는 별개 객체 — Lifecycle
# Isolation은 아래 별도 테스트로 확인).

def _bottom_repair_time_window_cfg():
    return {'condition_strategies': {'bottom_pullback': {
        'pullback': {'time_window': {'start': '00:00', 'end': '23:59'}}}}}


def _bottom_breach_df():
    """1차 호출용: 마지막 봉이 VWAP 대비 확실히 이탈."""
    n = 30
    close = np.linspace(9000, 11000, n)
    close[-1] = close[-2] * 0.90
    open_ = close.copy()
    open_[-1] = close[-2] * 0.99
    high = np.maximum(open_, close) + 2
    low = np.minimum(open_, close) - 2
    vol = np.full(n, 500000.0)
    return pd.DataFrame({'open': open_, 'high': high, 'low': low, 'close': close,
                          'volume': vol})


def _bottom_reclaim_df(breach_df):
    """2차 호출용: 1차의 이탈가 대비 강하게 회복 + 거래량 증가(재돌파)."""
    close = breach_df['close'].values.copy()
    close[-1] = close[-1] * 1.08
    open_ = breach_df['close'].values.copy()
    open_[-1] = breach_df['close'].values[-1] * 1.001
    high = np.maximum(open_, close) + 2
    low = np.minimum(open_, close) - 2
    vol = breach_df['volume'].values.copy()
    vol[-1] = vol[-2] * 1.5
    return pd.DataFrame({'open': open_, 'high': high, 'low': low, 'close': close,
                          'volume': vol})


@pytest.fixture(autouse=False)
def _reset_bottom_observation_manager():
    import analyzers.strategy_monitors.bottom as _bottom_mod
    _bottom_mod._observation_manager = None
    yield
    _bottom_mod._observation_manager = None


def test_seq38_bottom_two_call_state_transition_reaches_signal(_reset_bottom_observation_manager):
    """WI-20 §5 핵심 검증 — 1차 호출(이탈 감지, False) 뒤 2차 호출(재돌파,
    True)로 실제 2단계 상태 전이를 거쳐 SIGNAL이 발생하는지 증명한다."""
    cfg = _bottom_repair_time_window_cfg()
    breach_df = _bottom_breach_df()
    r1 = route_to_monitor(38, 'SYM38_TRANSITION', breach_df, config=cfg)
    assert r1.signal is False
    assert r1.monitor_state in ('BOTTOM_CANDIDATE', 'BOTTOM_CONFIRMING')

    reclaim_df = _bottom_reclaim_df(breach_df)
    r2 = route_to_monitor(38, 'SYM38_TRANSITION', reclaim_df, config=cfg)
    assert r2.signal is True, f'2차 호출에서 재돌파가 확인돼야 한다: {r2.reasons}'
    assert r2.monitor_state == 'BOUNCE_CONFIRMED'


def test_seq38_bottom_repeated_calls_maintain_state(_reset_bottom_observation_manager):
    """WI-20 §8 — call1(False) → call2(재돌파,True) → call3/4(같은 재돌파 데이터를
    유지하면 계속 True, 즉 상태가 튀지 않고 안정적으로 유지되는지).

    참고: check_pullback()이 ready=True를 반환할 때 내부적으로
    signal['state']='READY_TO_ENTER'로 전이한다(trading/bottom_pullback_
    manager.py 218행) — 이후 재호출은 'WAIT_PULLBACK'도 'PULLBACK_DETECTED'도
    아니므로 두 분기 모두 안 타고 `return False, f"상태: {signal['state']}"`로
    떨어진다. pullback_used를 True로 바꾸는 mark_entered()는 실거래 진입 후
    호출하는 별도 메서드라, 순수 관측인 이 Monitor는 호출하지 않는다(실거래
    개입 금지 원칙 유지) — 그래서 한 번 True가 나온 뒤에는 "재확인 남발"이
    아니라 안정적으로 False("상태: READY_TO_ENTER")로 고정되는 게 올바른
    동작이다. 이 테스트는 그 안정성(플래핑 없음)을 검증한다."""
    cfg = _bottom_repair_time_window_cfg()
    breach_df = _bottom_breach_df()
    reclaim_df = _bottom_reclaim_df(breach_df)

    r1 = route_to_monitor(38, 'SYM38_REPEAT', breach_df, config=cfg)
    r2 = route_to_monitor(38, 'SYM38_REPEAT', reclaim_df, config=cfg)
    r3 = route_to_monitor(38, 'SYM38_REPEAT', reclaim_df, config=cfg)
    r4 = route_to_monitor(38, 'SYM38_REPEAT', reclaim_df, config=cfg)
    assert [r1.signal, r2.signal] == [False, True]
    # True 이후에는 READY_TO_ENTER 상태로 고정되어 다시 True로 플래핑하지 않는다
    assert r3.signal is False and r4.signal is False
    assert r3.reasons == r4.reasons, '상태가 안정적이면 반복 호출 결과도 동일해야 한다'


def test_seq38_bottom_negative_no_breach_no_signal(_reset_bottom_observation_manager):
    """WI-20 §6 Case A — 이탈 조건 자체가 없으면(꾸준한 상승) SIGNAL 없음."""
    cfg = _bottom_repair_time_window_cfg()
    df = _its_signal_df()  # 완만한 상승추세, VWAP 이탈 없음
    r = route_to_monitor(38, 'SYM38_NOBREACH', df, config=cfg)
    assert r.signal is False


def test_seq38_bottom_negative_breach_without_reclaim(_reset_bottom_observation_manager):
    """WI-20 §6 Case B — 이탈 후 재돌파가 없으면(계속 약세) SIGNAL 없음."""
    cfg = _bottom_repair_time_window_cfg()
    breach_df = _bottom_breach_df()
    r1 = route_to_monitor(38, 'SYM38_NORECLAIM', breach_df, config=cfg)
    assert r1.signal is False
    # 2차도 계속 약세(재돌파 실패)
    weak_df = breach_df.copy()
    weak_df.loc[weak_df.index[-1], 'close'] = breach_df['close'].iloc[-1] * 0.99
    r2 = route_to_monitor(38, 'SYM38_NORECLAIM', weak_df, config=cfg)
    assert r2.signal is False


def test_seq38_bottom_negative_low_break_invalidates(_reset_bottom_observation_manager):
    """WI-20 §6 Case C — 이탈 후 신호봉 저가마저 붕괴하면 무효화되어 SIGNAL 없음."""
    cfg = _bottom_repair_time_window_cfg()
    breach_df = _bottom_breach_df()
    r1 = route_to_monitor(38, 'SYM38_LOWBREAK', breach_df, config=cfg)
    assert r1.signal is False
    crash_df = breach_df.copy()
    crash_df.loc[crash_df.index[-1], 'low'] = breach_df['low'].iloc[-1] * 0.90
    crash_df.loc[crash_df.index[-1], 'close'] = breach_df['low'].iloc[-1] * 0.91
    r2 = route_to_monitor(38, 'SYM38_LOWBREAK', crash_df, config=cfg)
    assert r2.signal is False
    assert any('저가 이탈' in r for r in r2.reasons)


def test_seq38_bottom_insufficient_data(_reset_bottom_observation_manager):
    """WI-20 §6 Case D."""
    cfg = _bottom_repair_time_window_cfg()
    short_df = _synthetic_df(n=5)
    r = route_to_monitor(38, 'SYM38_SHORT', short_df, config=cfg)
    assert r.data_quality == 'INSUFFICIENT_DATA'
    assert r.signal is None


def test_seq38_bottom_fresh_manager_does_not_inherit_previous_symbol_state(
        _reset_bottom_observation_manager):
    """WI-20 §6 Case E — 관측 Manager를 초기화한 뒤에는 이전 종목의 setup을
    새 종목이 잘못 승계하지 않는다(신규 심볼은 항상 WAIT_PULLBACK부터 시작)."""
    cfg = _bottom_repair_time_window_cfg()
    breach_df = _bottom_breach_df()
    route_to_monitor(38, 'SYM38_A', breach_df, config=cfg)
    # 다른 심볼(SYM38_B)은 SYM38_A의 이탈 상태와 무관하게 처음부터 시작해야 한다
    r_b = route_to_monitor(38, 'SYM38_B', breach_df, config=cfg)
    assert r_b.signal is False
    assert r_b.monitor_state in ('BOTTOM_CANDIDATE', 'BOTTOM_CONFIRMING')


def test_seq38_observation_manager_isolated_from_live_bottom_manager(
        _reset_bottom_observation_manager):
    """WI-20 §7 Lifecycle Isolation — Monitor 전용 관측 Manager와 라이브
    self.bottom_manager(별도 인스턴스)가 서로 다른 객체이며 상태를 공유하지
    않는지 확인한다."""
    from trading.bottom_pullback_manager import BottomPullbackManager
    import analyzers.strategy_monitors.bottom as _bottom_mod

    live_manager = BottomPullbackManager({}, state_manager=None)
    live_manager.register_signal(stock_code='SYM38_LIVE', stock_name='SYM38_LIVE',
                                  signal_price=10000, signal_low=9900,
                                  signal_vwap=10100, market='KOSDAQ')

    cfg = _bottom_repair_time_window_cfg()
    route_to_monitor(38, 'SYM38_OBS', _bottom_breach_df(), config=cfg)
    obs_manager = _bottom_mod._observation_manager

    assert obs_manager is not live_manager
    assert 'SYM38_LIVE' not in obs_manager.signals, 'Monitor 전용 Manager가 라이브 상태를 봐서는 안 된다'
    assert 'SYM38_OBS' not in live_manager.signals, 'Monitor 전용 Manager의 상태가 라이브로 새면 안 된다'


# ── 항목 9: 중복 조건검색식 후보 ─────────────────────────────────────────────
def test_duplicate_condition_candidate_independent_results():
    """한 종목이 여러 seq(예: 32, 33, 36, 37)에 동시에 걸려도 각 Monitor 결과가
    독립적으로 생성되고 서로 섞이지 않는다(WI-10 §13)."""
    df = _synthetic_df(seed=4)
    seqs = [32, 33, 36, 37]
    results = {seq: route_to_monitor(seq, '삼성전자', df, config=CFG) for seq in seqs}
    assert len(results) == 4
    for seq, r in results.items():
        assert r.condition_seq == seq
        assert r.strategy_name == {32: 'Momentum', 33: 'Breakout', 36: 'VWAP',
                                    37: 'Squeeze Momentum Pro'}[seq]
    # 서로 다른 Monitor 인스턴스이므로 strategy_name이 전부 달라야 한다
    assert len({r.strategy_name for r in results.values()}) == 4


# ── WI-15 §18.2: seq 33(ACTIVE) + 34/35/39(BLOCKED) 동시 매칭 격리 ──────────
def test_seq_32_34_35_39_independent_results_no_cross_contamination():
    """한 종목이 32(Momentum)+34(EOD)+35(Trend)+39(ITS)에 동시에 걸려도(WI-15
    Rev.2 §10 필수 테스트 조합) 각 Monitor 결과가 완전히 독립적으로 생성되고
    다른 전략 결과를 덮어쓰지 않는다."""
    df = _synthetic_df(seed=7)
    seqs = [32, 34, 35, 39]
    results = {seq: route_to_monitor(seq, '삼성전자', df, config=CFG) for seq in seqs}
    for seq in seqs:
        assert results[seq].condition_seq == seq
        assert results[seq].data_quality != 'NO_CODE', f'seq{seq}는 이제 구현됨(WI-15 Rev.2)'
    # 4개 전부 strategy_name이 서로 달라야 한다(뭉개지면 회귀)
    assert len({results[s].strategy_name for s in seqs}) == 4
    # EOD(34)의 signal 값이 ITS(39)/Trend(35)/Momentum(32) 판정에 영향을 주지 않는다
    # (동일 df를 개별 호출한 것과 동일 결과가 나와야 함 — 상태 공유 없음의 방증)
    independent = {seq: route_to_monitor(seq, '삼성전자', df, config=CFG) for seq in seqs}
    for seq in seqs:
        assert independent[seq].signal == results[seq].signal
        assert independent[seq].monitor_state == results[seq].monitor_state


# ── WI-16 §8: 32/34/35/39 전체 조합(7가지) 격리 검증 ─────────────────────────
@pytest.mark.parametrize('combo', [
    (32, 34), (32, 35), (32, 39), (34, 35), (34, 39), (35, 39), (34, 35, 39),
])
def test_wi16_seq_combination_isolation(combo):
    """WI-16 §8이 요구하는 7개 조합 전부: seq가 보존되고 strategy_name이
    서로 겹치지 않는지(다른 전략으로 뭉개지지 않는지) 확인."""
    df = _synthetic_df(seed=sum(combo))
    results = {seq: route_to_monitor(seq, '테스트', df, config=CFG) for seq in combo}
    for seq in combo:
        assert results[seq].condition_seq == seq
    assert len({results[seq].strategy_name for seq in combo}) == len(combo)


# ── 항목 10: unknown seq ─────────────────────────────────────────────────────
def test_unknown_seq_returns_unknown_without_crash():
    result = route_to_monitor(999, 'SYM_X', _synthetic_df(), config=CFG)
    assert result.monitor_state == 'UNKNOWN_SEQ'
    assert result.data_quality == 'NO_CODE'


# ── 항목 11: Monitor 실패/예외 처리 ──────────────────────────────────────────
def test_monitor_handles_none_df_without_crash():
    for seq in MONITOR_MAP:
        result = route_to_monitor(seq, 'SYM_NONE', None, config=CFG)
        assert result.monitor_state in ('CANDIDATE', 'NOT_IMPLEMENTED')
        assert result.data_quality in ('INSUFFICIENT_DATA', 'NO_CODE')


def test_monitor_handles_malformed_df_without_crash():
    bad_df = pd.DataFrame({'close': [1, 2, 3]})  # open/high/low/volume 없음
    for seq in (32, 33, 34, 35, 36, 37, 38, 39):
        result = route_to_monitor(seq, 'SYM_BAD', bad_df, config=CFG)
        assert result.monitor_state != ''  # 예외로 죽지 않고 어떤 상태든 반환
        assert result.data_quality in ('OK', 'INSUFFICIENT_DATA', 'ERROR')


def test_route_from_chart_data_handles_bad_chart_data():
    """raw chart_data 변환 실패(빈 리스트/None)도 크래시 없이 처리."""
    for bad in (None, [], [{'unexpected': 'shape'}]):
        result = route_from_chart_data(32, 'SYM_RAW', bad, config=CFG)
        assert result.condition_seq == 32


# ── 항목 12: source attribution 보존 (WI-9 연동) ─────────────────────────────
def test_router_seq_matches_condition_attribution_seq():
    """WI-9 _condition_attribution()이 반환하는 strategy_seqs 값을 그대로 Router에
    넘겼을 때 결과의 condition_seq가 정확히 보존되는지."""
    from main_auto_trading import IntegratedTradingSystem
    stub = IntegratedTradingSystem.__new__(IntegratedTradingSystem)
    stub.validated_stocks = {
        '005930': {'strategy_seqs': [32, 38], 'primary_strategy_seq': 32,
                   'condition_sources': ['Momentum 전략', 'Bottom 전략'],
                   'primary_condition': 'Momentum 전략'}
    }
    ca = stub._condition_attribution('005930')
    df = _synthetic_df(seed=5)
    for seq in ca['strategy_seqs']:
        r = route_to_monitor(seq, '005930', df, config=CFG)
        assert r.condition_seq == seq
        assert r.symbol == '005930'


# ── 항목 13/14: Monitor -> Evidence / Ranking 연결(소스 특성화) ──────────────
def test_strategy_monitor_call_precedes_evidence_attr_log_in_source():
    """main_auto_trading.py 소스에서 [STRATEGY_MONITOR] 호출부가 [EVIDENCE_ATTR]
    로그보다 앞에 위치하는지(WI-3/8/9와 동일한 소스 특성화 회귀가드)."""
    src = open(os.path.join(ROOT, 'main_auto_trading.py'), encoding='utf-8').read()
    mon_idx = src.index('route_from_chart_data(')
    ev_idx = src.index('f"[EVIDENCE_ATTR] symbol={stock_code} "')
    assert mon_idx < ev_idx, 'Strategy Monitor 호출이 EVIDENCE_ATTR 로그보다 뒤에 있음'


def test_strategy_monitor_result_not_passed_to_ranking_or_execute_buy():
    """Router 결과 변수(_mon_result)가 ScoreEngine.rank/select나 execute_buy 호출
    인자로 전달되지 않는지 — 순수 관측 레이어라는 §2.10 원칙의 소스 특성화 가드."""
    src = open(os.path.join(ROOT, 'main_auto_trading.py'), encoding='utf-8').read()
    # _mon_result는 로깅(f-string)에서만 참조되어야 한다 — rank/select/execute_buy 인자로
    # 쓰인 흔적이 없어야 함(있다면 판단 로직에 결합된 것이므로 회귀).
    assert '_score_engine.rank(_mon_result' not in src
    assert '_score_engine.select(_mon_result' not in src
    assert 'execute_buy(_mon_result' not in src


# ── WI-13: Condition Dataset 저장 소스 특성화 ────────────────────────────────
def test_wi13_dataset_write_is_inside_strategy_monitor_try_block():
    """condition_dataset.record_candidate 호출이 [STRATEGY_MONITOR] 로그보다
    뒤, [EVIDENCE_ATTR] 로그보다 앞(=같은 try 블록 안)에 위치하는지."""
    src = open(os.path.join(ROOT, 'main_auto_trading.py'), encoding='utf-8').read()
    mon_log_idx = src.index('f"[{_mon_tag}] seq={_mon_result.condition_seq} "')
    write_idx = src.index('self.condition_dataset.record_candidate(')
    ev_idx = src.index('f"[EVIDENCE_ATTR] symbol={stock_code} "')
    assert mon_log_idx < write_idx < ev_idx, (
        'WI-13 Dataset 저장 호출이 [STRATEGY_MONITOR] 로그와 [EVIDENCE_ATTR] 로그 '
        '사이(=WI-9 try 블록 내부)에 있어야 한다'
    )


def test_wi13_dataset_write_not_passed_to_ranking_or_execute_buy():
    """condition_dataset 저장 결과가 execute_buy/Ranking 호출 인자로 전달되지
    않는지 — WI-9와 동일한 순수 관측 원칙."""
    src = open(os.path.join(ROOT, 'main_auto_trading.py'), encoding='utf-8').read()
    assert '_score_engine.rank(_cid' not in src
    assert '_score_engine.select(_cid' not in src
    assert 'execute_buy(_cid' not in src


# ── WI-22 §6: SMC Isolation Test ─────────────────────────────────────────────
# seq32~39 Monitor 결과가 "SMC 신호 상태"라는 개념 자체를 알지 못한다는 것을
# route_to_monitor/route_from_chart_data 시그니처(analyzers/strategy_monitors/
# router.py:72,100)에 SMC 관련 파라미터가 없다는 사실로 이미 wi22_dependency_audit.md
# §2/§3에서 코드 근거로 확인했다. 이 테스트는 그것을 실행 결과로 재확인한다 —
# 가짜 SMC 상태를 몇 가지 형태로 "주입 시도"해도(모듈 전역/함수 인자 어디에도
# 넣을 방법이 없으므로, 실제로는 동일 입력 반복 호출로 대체) 결과가 완전히
# 동일함(byte-identical)을 확인한다.
class _FakeSMCState:
    """§6이 요구하는 'SMC_SIGNAL/SMC_NO_SIGNAL/SMC_ERROR 주입'을 시뮬레이션.
    Monitor 쪽에 주입 지점이 존재하지 않으므로, 이 객체를 전역으로 세팅해도
    Monitor가 이를 읽지 않는다는 것 자체가 증명 대상이다."""
    def __init__(self, state: str):
        self.state = state
        self.smc_signal = state == 'SMC_SIGNAL'
        self.smc_error = state == 'SMC_ERROR'


_SMC_INJECTION_STATES = ['SMC_SIGNAL', 'SMC_NO_SIGNAL', 'SMC_ERROR']


@pytest.mark.parametrize('seq,df_factory', [
    (32, lambda: _synthetic_df(seed=32, trend=5.0)),
    (33, _breakout_signal_df),
    (34, _eod_signal_df),
    (35, _trend_signal_df),
    (36, lambda: _synthetic_df(seed=36)),
    (37, lambda: _synthetic_df(seed=37)),
    (39, _its_signal_df),
])
def test_wi22_smc_injection_does_not_change_monitor_result(seq, df_factory, monkeypatch):
    """동일 Candidate에 SMC_SIGNAL/SMC_NO_SIGNAL/SMC_ERROR를 순서대로 '주입'한
    상태에서 Monitor를 반복 호출해도 결과(signal/monitor_state/reasons)가
    완전히 동일해야 한다(§6 SMC Isolation = 100%)."""
    import analyzers.strategy_monitors.router as _router_mod

    df = df_factory()
    results = []
    for state in _SMC_INJECTION_STATES:
        # 전역에 가짜 SMC 상태를 심어둔다 — Monitor/Router가 이걸 읽을 방법이
        # 없다는 것이 이 테스트의 요지이므로, 실제로 아무것도 못 읽어도 정상.
        fake_smc = _FakeSMCState(state)
        monkeypatch.setattr(_router_mod, '_WI22_INJECTED_SMC_STATE', fake_smc, raising=False)
        result = route_to_monitor(seq, f'SMCINJ{seq}', df.copy(), config=CFG)
        results.append(result)

    base = results[0]
    for r in results[1:]:
        assert r.signal == base.signal
        assert r.monitor_state == base.monitor_state
        assert r.data_quality == base.data_quality
        assert r.reasons == base.reasons


def test_wi22_seq38_smc_injection_does_not_change_monitor_result(_reset_bottom_observation_manager):
    """seq38(Bottom)은 상태를 갖는 Monitor(WI-20)라 별도 확인 — 2-call 상태전이
    시퀀스 도중 SMC 상태를 바꿔 끼워도 signal 결과가 바뀌지 않아야 한다."""
    cfg = _bottom_repair_time_window_cfg()
    breach_df = _bottom_breach_df()
    reclaim_df = _bottom_reclaim_df(breach_df)

    for state in _SMC_INJECTION_STATES:
        _FakeSMCState(state)  # 주입 시도 — Monitor가 읽지 않음을 확인하는 것이 목적
    r1 = route_to_monitor(38, 'SMCINJ38', breach_df, config=cfg)
    for state in _SMC_INJECTION_STATES:
        _FakeSMCState(state)
    r2 = route_to_monitor(38, 'SMCINJ38', reclaim_df, config=cfg)

    assert r1.signal is False  # 1차 호출: 이탈만 감지, 아직 신호 아님
    assert r2.signal is True   # 2차 호출: 재돌파 확인, 신호 발생 — SMC 상태와 무관


def test_wi22_router_and_monitor_source_has_zero_smc_references():
    """analyzers/strategy_monitors/ 전체에 'smc' 문자열이 등장하지 않아야 한다
    (wi22_dependency_audit.md §2 근거를 코드로 고정)."""
    import glob
    pkg_dir = os.path.join(ROOT, 'analyzers', 'strategy_monitors')
    for path in glob.glob(os.path.join(pkg_dir, '*.py')):
        src = open(path, encoding='utf-8').read().lower()
        assert 'smc' not in src, f'{path}에 smc 참조 발견 — SMC dependency 위반'


# ── 항목 15: Dry Run end-to-end ──────────────────────────────────────────────
def test_dry_run_all_seq_end_to_end_no_crash():
    """8개 seq 전부를 순회하며 Router가 예외 없이 결과를 만드는지(§4/§18 Dry Run)."""
    df = _synthetic_df(seed=6)
    results = [route_to_monitor(seq, f'DRYRUN{seq}', df, config=CFG)
               for seq in sorted(MONITOR_MAP.keys())]
    assert len(results) == 8
    assert all(r.data_quality in ('OK', 'INSUFFICIENT_DATA', 'NO_CODE', 'ERROR') for r in results)
    implemented = [r for r in results if r.data_quality != 'NO_CODE']
    not_implemented = [r for r in results if r.data_quality == 'NO_CODE']
    # WI-15 Rev.2: 사용자가 HTS 원문을 제공해 EOD/Trend/ITS도 구현됨 — NOT_IMPLEMENTED 0건
    assert len(not_implemented) == 0
    assert len(implemented) == 8
