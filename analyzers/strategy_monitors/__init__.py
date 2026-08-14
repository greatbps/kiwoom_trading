"""
Work Instruction 10 — HTS 조건검색식(seq 32~39)별 Strategy Monitor.

⚠️ 이 패키지는 순수 관측/로깅 레이어다. 어떤 클래스도 execute_buy/Ranking/Gate/Slot
판단에 직접 쓰이지 않는다(main_auto_trading.py에서는 로그 기록에만 사용한다).

각 Monitor는 기존 코드(analyzers/trend/trend_breakout.py, analyzers/squeeze_*.py,
trading/bottom_pullback_manager.py, analyzers/indicators.py, analyzers/
entry_timing_analyzer.py)를 무수정으로 재사용한다 — 새 지표 계산 로직을 만들지
않는다. 대응 코드가 없는 3개(EOD/Trend=Supertrend+EMA+RSI/ITS)는 NOT_IMPLEMENTED
스텁이다(WI-10 Phase 1 코드감사 결과, `phase1/reports/strategy_monitoring/
strategy_monitor_architecture.md` 참조).
"""
from .base import StrategyMonitorResult, StrategyMonitor
from .router import route_to_monitor, route_from_chart_data, MONITOR_MAP

__all__ = ['StrategyMonitorResult', 'StrategyMonitor', 'route_to_monitor',
           'route_from_chart_data', 'MONITOR_MAP']
