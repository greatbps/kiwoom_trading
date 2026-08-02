"""
tests/unit/test_regime_scenarios.py

Regime Gate 운영 시뮬레이션 — 합성 데이터 기반 (Audit 4 대체, 2026-07-27).

실제 main_auto_trading.py 라이브 프로세스를 돌리지 않고, tools/backtest/
label_market_regime_for_trades.py의 HistoricalRegimeCalculator(백테스트 검증에도
쓰이는 동일 로직)를 합성 KOSPI/KOSDAQ 시계열에 태워 5가지 시장 시나리오에서
Regime 판정이 설계대로 나오는지 검증한다. Score 계산식/threshold는 건드리지 않고
순수 검증만 수행.

장시작/장종료/재시작/API실패/DB실패 시나리오는 코드 추적 기반 서술 분석으로
reports/OPERATION_SIMULATION.md에 별도 기술한다(이 파일은 Regime 판정 로직만).
"""
import sys
import os
from datetime import date, timedelta

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from tools.backtest.label_market_regime_for_trades import HistoricalRegimeCalculator, DEFAULT_CFG


def _make_series(pattern: str, n: int = 40, base: float = 1000.0) -> pd.DataFrame:
    """날짜 인덱스 + close 컬럼을 가진 합성 일봉 DataFrame 생성."""
    dates = [date(2026, 1, 1) + timedelta(days=i) for i in range(n)]
    closes = []
    price = base
    for i in range(n):
        if pattern == 'uptrend':
            price = base * (1 + 0.01 * i)                      # 꾸준한 상승
        elif pattern == 'flat_up':
            price = base * (1 + 0.01 * i)                       # sideways 시나리오의 KOSPI측(완만 상승)
        elif pattern == 'flat_down':
            price = base * (1 - 0.01 * i)                       # sideways 시나리오의 KOSDAQ측(완만 하락)
        elif pattern == 'downtrend':
            price = base * (1 - 0.01 * i)                       # 꾸준한 하락 (RISK_OFF)
        elif pattern == 'crash':
            price = base * (1 - 0.005 * i)                      # 완만한 하락이다가
            if i == n - 1:
                price = closes[-1] * 0.90                       # 마지막 날 -10% 급락
        elif pattern == 'melt_up':
            price = base * (1 + 0.005 * i)                      # 완만한 상승이다가
            if i == n - 1:
                price = closes[-1] * 1.10                       # 마지막 날 +10% 급등
        closes.append(price)

    df = pd.DataFrame({'close': closes}, index=pd.Index(dates, name='date'))
    return df


def _regime_for(pattern: str, kosdaq_pattern: str = None) -> dict:
    kospi_df = _make_series(pattern)
    kosdaq_df = _make_series(kosdaq_pattern or pattern)
    calc = HistoricalRegimeCalculator(kospi_df, kosdaq_df, DEFAULT_CFG)
    cutoff = kospi_df.index[-1]  # 마지막 날 기준 평가
    return calc._compute_regime(cutoff)


def test_uptrend_scenario_is_trend_up():
    """상승장: 종가가 계속 EMA20 위 + EMA20 상승 → TREND_UP."""
    r = _regime_for('uptrend')
    assert r['regime'] == 'TREND_UP', r
    assert r['score'] >= DEFAULT_CFG['score_thresholds']['trend_up_min']


def test_sideways_scenario_is_neutral():
    """혼조장(KOSPI 상승 vs KOSDAQ 하락): 개별 지수는 항상 above+slope_up(+2) 또는
    below+slope_down(-2)만 가능하다(EMA 수식상 above+slope_down 조합은 단일스텝에서
    불가능 — new_ema는 항상 price와 이전ema의 가중평균이라 slope_down이면 price<=new_ema가
    강제됨). 따라서 진짜 NEUTRAL은 두 지수가 서로 엇갈릴 때만 나온다(+2-2=0)."""
    r = _regime_for('flat_up', kosdaq_pattern='flat_down')
    assert r['regime'] == 'NEUTRAL', r
    assert r['score'] == 0, r


def test_downtrend_scenario_is_risk_off():
    """지속 하락장: 종가가 계속 EMA20 아래 + EMA20 하락 → RISK_OFF."""
    r = _regime_for('downtrend')
    assert r['regime'] == 'RISK_OFF', r
    assert r['score'] <= DEFAULT_CFG['score_thresholds']['risk_off_max']


def test_crash_scenario_triggers_day_drop_penalty():
    """급락장: 완만한 하락 중 마지막날 -10% → day_drop 피처까지 발동해 RISK_OFF + reasons에 day_drop 포함."""
    r = _regime_for('crash')
    assert r['regime'] == 'RISK_OFF', r
    assert any('day_drop' in reason for reason in r['reasons']), r['reasons']


def test_single_index_score_is_always_plus_or_minus_two_never_zero():
    """구조적 발견(2026-07-27 감사): 단일 지수의 below/above + slope 조합에서
    'above_EMA20 AND slope_down' 또는 'below_EMA20 AND slope_up'은 EWM 산식상
    단일 스텝에서 원천적으로 불가능하다 — new_ema는 price와 prev_ema의 가중평균이라
    slope_down(new_ema<prev_ema)이면 price<prev_ema가 전제되고, 이는 곧
    price<=new_ema(above 불가)를 강제한다. 즉 지수 하나의 두 피처 합은 항상 -2 또는
    +2뿐, 0이 나올 수 없다 — NEUTRAL은 반드시 KOSPI/KOSDAQ가 서로 엇갈릴 때만 발생한다.
    (day_drop 피처까지 포함하면 -3까지도 가능 — 이 테스트는 above/slope 두 피처만 검증)"""
    for pattern in ['uptrend', 'downtrend']:
        r = _regime_for(pattern)
        # day_drop 피처가 안 끼는 완만한 추세이므로 above+slope 두 피처만의 합만 존재
        kospi_reasons = [x for x in r['reasons'] if x.startswith('KOSPI') and 'day_drop' not in x]
        assert len(kospi_reasons) == 2, kospi_reasons
        both_up = 'KOSPI_above_EMA20' in kospi_reasons and 'KOSPI_EMA_slope_up' in kospi_reasons
        both_down = 'KOSPI_below_EMA20' in kospi_reasons and 'KOSPI_EMA_slope_down' in kospi_reasons
        assert both_up or both_down, f"above+slope_down 또는 below+slope_up(불가능 조합) 발생: {kospi_reasons}"


def test_melt_up_scenario_has_no_symmetric_day_gain_bonus():
    """급등장: 마지막날 +10% 급등해도 '급등 보너스' 피처는 설계상 존재하지 않음
    (day_drop만 페널티, 대칭되는 day_gain 가점 없음) — TREND_UP은 above+slope만으로 달성,
    reasons에 day_drop류 문자열이 없어야 함(비대칭 설계 확인용 회귀 테스트)."""
    r = _regime_for('melt_up')
    assert r['regime'] == 'TREND_UP', r
    assert not any('day_drop' in reason for reason in r['reasons']), r['reasons']
    # 급등 전용 가점 피처가 없으므로 above+slope_up 조합인 스코어 이상으로 튀지 않음(각 지수 최대 +2)
    assert r['score'] <= 4, f"KOSPI+KOSDAQ 각 최대 +2점 초과 — 예상치 못한 가점 피처 존재 여부 확인 필요: {r}"
