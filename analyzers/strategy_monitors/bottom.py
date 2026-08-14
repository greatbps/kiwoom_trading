"""seq 38 — Bottom. WI-10 Phase 1: `trading/bottom_pullback_manager.py`의
`BottomPullbackManager`가 완전구현돼 있고 WI-9에서 seq38 라우팅도 수정 완료됐다
(condition_strategies.bottom_pullback.condition_indices=[38]). 무수정 재사용한다.

[WI-19 Rev.1 Repair, BUG-WI19-1] 원래는 호출마다 새 ephemeral 인스턴스를 만들어
등록+체크를 "한 번만" 했었다. 그런데 `check_pullback()`은 최소 2회 분리 호출을
전제로 설계돼 있다(1차: VWAP 이탈 감지만 하고 무조건 False 반환 / 2차: 이미
이탈 감지된 상태에서만 재돌파를 확인해 True 도달 가능) — ephemeral 인스턴스는
매번 버려지므로 "2차 호출"이 절대 발생하지 않아 구조적으로 SIGNAL=True가
불가능했다(재현: tests/unit/test_strategy_monitors.py 회귀가드 참조).

수정: **Monitor 전용 별도 지속 인스턴스**(`_observation_manager`, 모듈 레벨
singleton)를 스캔 사이클 간에 유지한다. `BottomPullbackManager.signals`는
`stock_code`별 dict라 원래부터 여러 종목을 한 인스턴스가 동시에 추적하도록
설계돼 있다 — 그 설계 그대로 쓰는 것뿐이다. **여전히 라이브
`main_auto_trading.py`의 `self.bottom_manager`와는 별개의 객체**라서 실거래
신호 등록/추적에는 전혀 영향을 주지 않는다(순수 관측 경로 원칙 유지).
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from trading.bottom_pullback_manager import BottomPullbackManager

from .base import StrategyMonitor, StrategyMonitorResult

MIN_BARS = 21

# [WI-19 Rev.1 Repair] 관측 전용 지속 인스턴스 — main_auto_trading.py의
# self.bottom_manager(라이브 매매용)와 완전히 분리된, 이 Monitor만의 상태.
_observation_manager: Optional[BottomPullbackManager] = None


class BottomMonitor(StrategyMonitor):
    seq = 38
    strategy_name = 'Bottom'

    def __init__(self, config: dict):
        # config/strategy_hybrid.yaml의 condition_strategies.bottom_pullback 그대로(WI-9 재사용)
        self._bottom_config = (config.get('condition_strategies', {}) or {}).get('bottom_pullback', {}) or {}
        self._vwap_analyzer = EntryTimingAnalyzer()

    def _get_observation_manager(self) -> BottomPullbackManager:
        global _observation_manager
        if _observation_manager is None:
            _observation_manager = BottomPullbackManager(self._bottom_config, state_manager=None)
        return _observation_manager

    def evaluate(self, symbol: str, df: Optional[pd.DataFrame]) -> StrategyMonitorResult:
        if df is None or len(df) < MIN_BARS:
            return self._insufficient_data_result(
                symbol, f'데이터 부족(<{MIN_BARS}봉) — VWAP/Pullback 판정 요구량 미달')
        try:
            d = self._vwap_analyzer.calculate_vwap(df, use_rolling=True, rolling_window=20)
            signal_price = float(d['close'].iloc[-1])
            signal_low = float(d['low'].iloc[-1])
            signal_vwap = float(d['vwap'].iloc[-1])
            recent_volume = float(d['volume'].iloc[-1])
            avg_volume_5 = float(d['volume'].iloc[-5:].mean())

            _mgr = self._get_observation_manager()
            _mgr.register_signal(stock_code=symbol, stock_name=symbol,
                                  signal_price=signal_price, signal_low=signal_low,
                                  signal_vwap=signal_vwap, market='KOSDAQ')
            ready, reason = _mgr.check_pullback(
                stock_code=symbol, current_price=signal_price, current_vwap=signal_vwap,
                current_low=signal_low, recent_volume=recent_volume,
                avg_volume_5=avg_volume_5, df=d,
            )
        except Exception as e:
            return self._error_result(symbol, e)

        # trading/bottom_pullback_manager.py:check_pullback()의 실제 반환 문구를 그대로
        # 매칭한다(순서 중요 — "저가 이탈"/"시간 초과"만 실패, "VWAP 이탈 대기 중"은
        # 아직 대기 상태라 substring이 겹치므로 구체적인 문구를 먼저 검사한다).
        r = str(reason)
        if ready:
            state = 'BOUNCE_CONFIRMED'
        elif '저가 이탈' in r or '시간 초과' in r:
            state = 'BOTTOM_FAILED'
        elif 'VWAP 재돌파 대기' in r or '거래량 부족' in r:
            state = 'BOTTOM_CONFIRMING'
        else:
            state = 'BOTTOM_CANDIDATE'  # VWAP 이탈 대기 중 / 진입 시간 외 / 기타

        return StrategyMonitorResult(
            condition_seq=self.seq, strategy_name=self.strategy_name, symbol=symbol,
            monitor_state=state, signal=bool(ready), reasons=[str(reason)],
        )
