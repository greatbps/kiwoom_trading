"""seq → Monitor 라우팅. WI-10 §12.

router 자체는 로직을 갖지 않는다 — 각 seq에 해당하는 Monitor 인스턴스에 위임만 한다.
알 수 없는 seq는 크래시 없이 UNKNOWN_SEQ 결과를 반환한다(§14 항목 10).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from analyzers.technical_analyzer import TechnicalAnalyzer

from .base import StrategyMonitorResult
from .momentum import MomentumMonitor
from .breakout import BreakoutMonitor
from .eod import EODMonitor
from .trend import TrendMonitor
from .vwap import VWAPMonitor
from .squeeze import SqueezeMonitor
from .bottom import BottomMonitor
from .its import ITSMonitor

MONITOR_MAP = {
    32: MomentumMonitor,
    33: BreakoutMonitor,
    34: EODMonitor,
    35: TrendMonitor,
    36: VWAPMonitor,
    37: SqueezeMonitor,
    38: BottomMonitor,
    39: ITSMonitor,
}

_NEEDS_CONFIG = {33, 38}  # BreakoutMonitor/BottomMonitor는 config가 필요하다
_instances: dict = {}
_ta = TechnicalAnalyzer()  # analyzers/technical_analyzer.py — chart_data 변환 전용 재사용


def _to_dataframe(chart_data) -> Optional[pd.DataFrame]:
    """analyzers/technical_analyzer.py:25 prepare_dataframe() 무수정 재사용 — 키움
    raw chart_data(list[dict])를 OHLCV DataFrame으로 변환(WI-8 Evidence 단계와 동일
    데이터, 신규 API 호출 없음)."""
    if not chart_data:
        return None
    try:
        return _ta.prepare_dataframe(chart_data)
    except Exception:
        return None


def _get_monitor(seq: int, config: dict):
    # [WI-19 Rev.1 Repair, BUG-WI19-2] _NEEDS_CONFIG(33/38)는 캐싱하지 않는다.
    # main_auto_trading.py가 매 호출마다 최신 config(레짐에 따라 동적으로
    # 바뀌는 trend.enabled 등)를 넘기는데, 인스턴스를 캐싱하면 최초 생성
    # 시점의 config가 프로세스 생애주기 동안 고정돼버린다(재현: 이전엔
    # config=False로 한 번 부르면 이후 config=True를 넘겨도 계속 False).
    # 생성 비용이 가벼운 config 파싱뿐이라(API 호출/DB 연결 없음) 캐싱
    # 이점보다 정확성이 우선이다.
    cls = MONITOR_MAP.get(seq)
    if cls is None:
        return None
    if seq in _NEEDS_CONFIG:
        return cls(config)
    if seq in _instances:
        return _instances[seq]
    monitor = cls()
    _instances[seq] = monitor
    return monitor


def route_to_monitor(cond_seq: int, symbol: str, df: Optional[pd.DataFrame],
                      config: Optional[dict] = None) -> StrategyMonitorResult:
    """[WI-10] seq -> 해당 Monitor로 위임. 순수 관측용 — 반환값은 로깅에만 쓴다."""
    try:
        monitor = _get_monitor(cond_seq, config or {})
    except Exception as e:
        return StrategyMonitorResult(
            condition_seq=cond_seq, strategy_name='UNKNOWN', symbol=symbol,
            monitor_state='ERROR', signal=None,
            reasons=[f'Monitor 초기화 실패: {type(e).__name__}: {e}'], data_quality='ERROR',
        )
    if monitor is None:
        return StrategyMonitorResult(
            condition_seq=cond_seq, strategy_name='UNKNOWN', symbol=symbol,
            monitor_state='UNKNOWN_SEQ', signal=None,
            reasons=[f'seq={cond_seq}에 대응하는 Monitor 없음(MONITOR_MAP 범위 밖)'],
            data_quality='NO_CODE',
        )
    try:
        return monitor.evaluate(symbol, df)
    except Exception as e:
        return StrategyMonitorResult(
            condition_seq=cond_seq, strategy_name=getattr(monitor, 'strategy_name', 'UNKNOWN'),
            symbol=symbol, monitor_state='ERROR', signal=None,
            reasons=[f'{type(e).__name__}: {e}'], data_quality='ERROR',
        )


def route_from_chart_data(cond_seq: int, symbol: str, chart_data,
                           config: Optional[dict] = None) -> StrategyMonitorResult:
    """[WI-10] main_auto_trading.py 연결용 — WI-8 Evidence 단계에서 이미 조회한 raw
    chart_data(키움 API 응답 list)를 그대로 받아 변환+라우팅까지 한 번에 처리한다.
    신규 API 호출 없음(사용자 확인, 기존 chart_data 재사용)."""
    return route_to_monitor(cond_seq, symbol, _to_dataframe(chart_data), config)
