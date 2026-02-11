"""
Dual Account Orchestrator - 단타/중기 계좌 분리 운용
=====================================================

기존 파이프라인을 유지하면서 Intent 분류에 따라
단타 계좌와 중기 계좌로 주문을 분리하는 오케스트레이터

핵심 개념:
- 진입 파이프라인은 하나 (기존 유지)
- Intent Classifier로 단타/중기 분류
- 계좌와 Exit Engine만 분리
"""

import os
import logging
from datetime import datetime, time
from typing import Dict, Set, Optional, Any, List
from dataclasses import dataclass

from dotenv import load_dotenv
from pathlib import Path
from rich.console import Console
from rich.table import Table

# 환경변수 로드
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)

# 내부 모듈
from trading.trade_intent import (
    TradeSignal, TradeIntent, TradeIntentClassifier, OrderRouter,
    SqueezeIndicators, TimeframeContext, NewsScore, FlowScore,
    NewsPersistence, create_signal_from_dict
)
from trading.trend_exit_engine import (
    TrendExitEngine, TrendPositionManager, TrendPosition,
    TrendExitAction, TrendExitReason
)

console = Console()
logger = logging.getLogger(__name__)


@dataclass
class DualAccountConfig:
    """듀얼 계좌 설정"""
    scalp_account: str = ""
    trend_account: str = ""

    # 자금 배분 (%)
    scalp_allocation: float = 60.0
    trend_allocation: float = 40.0

    # 최대 포지션 수
    scalp_max_positions: int = 5
    trend_max_positions: int = 3

    # EOD 평가 시간
    eod_evaluation_time: time = time(15, 20)

    def __post_init__(self):
        if not self.scalp_account:
            self.scalp_account = os.getenv('KIWOOM_SCALP_ACCOUNT', '6259-3479')
        if not self.trend_account:
            self.trend_account = os.getenv('KIWOOM_TREND_ACCOUNT', '5202-2235')


class DualAccountOrchestrator:
    """
    듀얼 계좌 오케스트레이터

    기존 TradingOrchestrator와 함께 동작하며
    Intent 분류에 따라 주문을 분리 라우팅
    """

    def __init__(self, config: Optional[DualAccountConfig] = None):
        self.config = config or DualAccountConfig()

        # Intent 분류 및 라우팅
        self.classifier = TradeIntentClassifier()
        self.router = OrderRouter()

        # 중기 포지션 관리
        self.trend_manager = TrendPositionManager()

        # 계좌별 포지션 추적
        self.scalp_positions: Dict[str, Dict] = {}
        self.trend_positions: Dict[str, Dict] = {}

        # 통계
        self.stats = {
            "total_signals": 0,
            "scalp_signals": 0,
            "trend_signals": 0,
            "scalp_trades": 0,
            "trend_trades": 0
        }

        logger.info("DualAccountOrchestrator 초기화 완료")
        logger.info(f"  단타 계좌: {self.config.scalp_account}")
        logger.info(f"  중기 계좌: {self.config.trend_account}")
        logger.info(f"  배분 비율: 단타 {self.config.scalp_allocation}% / 중기 {self.config.trend_allocation}%")

    def process_signal(self, signal_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        시그널 처리 - Intent 분류 및 라우팅

        Args:
            signal_data: 파이프라인에서 생성된 시그널 데이터

        Returns:
            라우팅 결과 및 실행 정보
        """
        self.stats["total_signals"] += 1

        # 1. TradeSignal 객체 생성
        signal = self._create_signal(signal_data)

        # 2. Intent 분류
        classified_signal = self.classifier.classify(signal)

        # 3. 라우팅 결정
        routing = self.router.route(classified_signal)

        # 4. 통계 업데이트
        if classified_signal.intent in [TradeIntent.SCALP, TradeIntent.INTRADAY]:
            self.stats["scalp_signals"] += 1
        else:
            self.stats["trend_signals"] += 1

        # 5. 결과 반환
        result = {
            "symbol": signal.symbol,
            "stock_name": signal.stock_name,
            "intent": classified_signal.intent.value,
            "confidence": classified_signal.intent_confidence,
            "reason": classified_signal.intent_reason,
            "account": routing["account"],
            "exit_engine": routing["exit_engine"],
            "should_execute": True,
            "original_signal": signal_data
        }

        # 로그 출력
        self._log_routing(result)

        return result

    def _create_signal(self, data: Dict[str, Any]) -> TradeSignal:
        """딕셔너리에서 TradeSignal 생성"""
        signal = TradeSignal(
            symbol=data.get("symbol", data.get("stock_code", "")),
            stock_name=data.get("stock_name", ""),
            timestamp=data.get("timestamp", datetime.now()),
            price=data.get("price", data.get("current_price", 0.0)),
            signal_source=data.get("signal_source", ""),
            signal_strength=data.get("signal_strength", 0.0),
            condition_name=data.get("condition_name", "")
        )

        # Squeeze 지표 설정
        if "squeeze" in data or "squeeze_on" in data:
            sq_data = data.get("squeeze", data)
            signal.indicators.squeeze = SqueezeIndicators(
                squeeze_on=sq_data.get("squeeze_on", False),
                momentum=sq_data.get("momentum", sq_data.get("squeeze_momentum", 0.0)),
                momentum_slope=sq_data.get("momentum_slope", 0.0),
            )

        # Timeframe Context
        signal.timeframe_context = TimeframeContext(
            htf_structure=data.get("htf_structure", "intact"),
            ltf_momentum=data.get("ltf_momentum", 0.0),
            ltf_vwap_distance=data.get("vwap_distance", 0.0)
        )

        # News Score
        news_persistence = data.get("news_persistence", "flash")
        if isinstance(news_persistence, str):
            try:
                news_persistence = NewsPersistence(news_persistence)
            except ValueError:
                news_persistence = NewsPersistence.FLASH

        signal.news_score = NewsScore(
            sentiment=data.get("news_sentiment", 0.0),
            persistence=news_persistence,
            impact_score=data.get("news_impact", 0.0)
        )

        # Flow Score
        signal.flow_score = FlowScore(
            institution_net=data.get("institution_net", 0.0),
            foreign_net=data.get("foreign_net", 0.0),
            score=data.get("flow_score", 0.0)
        )

        return signal

    def _log_routing(self, result: Dict[str, Any]):
        """라우팅 결과 로깅"""
        intent = result["intent"]
        account = result["account"]
        stock_name = result["stock_name"]

        if intent in ["scalp", "intraday"]:
            icon = "⚡"
            style = "yellow"
        else:
            icon = "📈"
            style = "cyan"

        console.print(
            f"[{style}]{icon} [{intent.upper()}] {stock_name} → {account}[/{style}]"
        )
        console.print(f"   사유: {result['reason']}")

    def execute_trend_entry(self, signal_data: Dict[str, Any],
                            quantity: int) -> Optional[TrendPosition]:
        """
        중기 포지션 진입 실행

        Args:
            signal_data: 시그널 데이터
            quantity: 매수 수량

        Returns:
            TrendPosition 또는 None
        """
        symbol = signal_data.get("symbol", signal_data.get("stock_code", ""))
        stock_name = signal_data.get("stock_name", "")
        price = signal_data.get("price", signal_data.get("current_price", 0))

        # 중기 매니저에 포지션 추가
        position = self.trend_manager.open_position(
            symbol=symbol,
            stock_name=stock_name,
            price=price,
            quantity=quantity,
            intent="squeeze_trend",
            entry_reason=signal_data.get("intent_reason", "")
        )

        self.trend_positions[symbol] = {
            "position": position,
            "entry_time": datetime.now()
        }

        self.stats["trend_trades"] += 1

        console.print(f"[cyan]📈 [중기] {stock_name} 진입: {price:,}원 x {quantity}주[/cyan]")

        return position

    def evaluate_trend_positions(self, market_data: Dict[str, Dict]) -> List[Dict]:
        """
        중기 포지션 EOD 평가

        Args:
            market_data: 종목별 시장 데이터
                {
                    "symbol": {
                        "current_price": float,
                        "squeeze_on": bool,
                        "momentum": float,
                        "momentum_prev": float,
                        "atr": float
                    }
                }

        Returns:
            청산 대상 목록
        """
        exit_signals = []

        for symbol, pos_data in self.trend_positions.items():
            if symbol not in market_data:
                continue

            mkt = market_data[symbol]

            # 보유일 계산
            entry_time = pos_data.get("entry_time", datetime.now())
            holding_days = (datetime.now() - entry_time).days

            # 데이터 업데이트
            self.trend_manager.update_daily_data(
                symbol=symbol,
                squeeze_on=mkt.get("squeeze_on", True),
                momentum=mkt.get("momentum", 0.0),
                momentum_prev=mkt.get("momentum_prev", 0.0),
                current_price=mkt.get("current_price", 0.0),
                holding_days=holding_days,
                atr=mkt.get("atr", 0.0)
            )

            # 청산 조건 평가
            result = self.trend_manager.evaluate_position(symbol)

            if result["action"] == "exit_all":
                exit_signals.append({
                    "symbol": symbol,
                    "action": result["action"],
                    "reason": result["reason"],
                    "description": result["description"]
                })

        return exit_signals

    def get_account_for_intent(self, intent: str) -> str:
        """Intent에 따른 계좌 조회"""
        if intent in ["scalp", "intraday"]:
            return self.config.scalp_account
        return self.config.trend_account

    def get_position_limits(self, intent: str) -> int:
        """Intent에 따른 최대 포지션 수"""
        if intent in ["scalp", "intraday"]:
            return self.config.scalp_max_positions
        return self.config.trend_max_positions

    def can_open_position(self, intent: str) -> bool:
        """포지션 오픈 가능 여부"""
        if intent in ["scalp", "intraday"]:
            return len(self.scalp_positions) < self.config.scalp_max_positions
        return len(self.trend_positions) < self.config.trend_max_positions

    def get_daily_report(self) -> str:
        """일일 리포트 생성"""
        lines = [
            "=" * 70,
            "📊 듀얼 계좌 일일 리포트",
            "=" * 70,
            f"리포트 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "📈 시그널 통계",
            f"  총 시그널: {self.stats['total_signals']}",
            f"  단타 시그널: {self.stats['scalp_signals']}",
            f"  중기 시그널: {self.stats['trend_signals']}",
            "",
            "💰 거래 통계",
            f"  단타 거래: {self.stats['scalp_trades']}건",
            f"  중기 거래: {self.stats['trend_trades']}건",
            "",
            "📦 포지션 현황",
            f"  단타 계좌 ({self.config.scalp_account}): {len(self.scalp_positions)}개",
            f"  중기 계좌 ({self.config.trend_account}): {len(self.trend_positions)}개",
        ]

        # 중기 포지션 상세
        if self.trend_positions:
            lines.append("")
            lines.append("📈 중기 포지션 상세")
            lines.append("-" * 50)

            report = self.trend_manager.get_daily_report()
            lines.append(report)

        lines.append("=" * 70)

        return "\n".join(lines)

    def display_status(self):
        """현재 상태 표시"""
        table = Table(title="듀얼 계좌 현황")
        table.add_column("항목", style="cyan")
        table.add_column("단타 계좌", style="yellow")
        table.add_column("중기 계좌", style="green")

        table.add_row(
            "계좌번호",
            self.config.scalp_account,
            self.config.trend_account
        )
        table.add_row(
            "배분 비율",
            f"{self.config.scalp_allocation}%",
            f"{self.config.trend_allocation}%"
        )
        table.add_row(
            "최대 포지션",
            str(self.config.scalp_max_positions),
            str(self.config.trend_max_positions)
        )
        table.add_row(
            "현재 포지션",
            str(len(self.scalp_positions)),
            str(len(self.trend_positions))
        )
        table.add_row(
            "오늘 시그널",
            str(self.stats["scalp_signals"]),
            str(self.stats["trend_signals"])
        )

        console.print(table)


# =============================================================================
# 헬퍼 함수 - 기존 시스템과 통합용
# =============================================================================

def enhance_signal_with_squeeze(signal_data: Dict, squeeze_data: Dict) -> Dict:
    """
    기존 시그널에 Squeeze 데이터 추가

    Args:
        signal_data: 기존 파이프라인 시그널
        squeeze_data: Daily Squeeze 데이터

    Returns:
        강화된 시그널 데이터
    """
    enhanced = signal_data.copy()
    enhanced["squeeze_on"] = squeeze_data.get("squeeze_on", False)
    enhanced["momentum"] = squeeze_data.get("momentum", 0.0)
    enhanced["momentum_slope"] = squeeze_data.get("momentum_slope", 0.0)
    enhanced["squeeze_momentum"] = squeeze_data.get("momentum", 0.0)

    return enhanced


def should_use_trend_account(signal_data: Dict) -> bool:
    """
    중기 계좌 사용 여부 판단 (간단 버전)

    Args:
        signal_data: 시그널 데이터

    Returns:
        True면 중기 계좌, False면 단타 계좌
    """
    # Daily Squeeze ON + Momentum 양수 + 기울기 양수
    squeeze_on = signal_data.get("squeeze_on", False)
    momentum = signal_data.get("momentum", signal_data.get("squeeze_momentum", 0.0))
    momentum_slope = signal_data.get("momentum_slope", 0.0)

    # Narrative 뉴스
    news_persistence = signal_data.get("news_persistence", "flash")
    is_narrative = news_persistence == "narrative"

    # 핵심 조건
    core_ok = squeeze_on and momentum > 0 and momentum_slope >= 0

    # 보조 조건
    support_ok = is_narrative or signal_data.get("htf_structure", "intact") == "intact"

    return core_ok and support_ok


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("Dual Account Orchestrator - Test")
    print("=" * 70)

    # 오케스트레이터 초기화
    orchestrator = DualAccountOrchestrator()

    # 상태 표시
    orchestrator.display_status()

    # 테스트 시그널 1: 중기 (Squeeze Trend)
    signal1 = {
        "symbol": "240810",
        "stock_name": "원익IPS",
        "current_price": 103500,
        "squeeze_on": True,
        "momentum": 0.8,
        "momentum_slope": 0.05,
        "news_persistence": "narrative",
        "htf_structure": "intact"
    }

    print("\n--- 시그널 1: 원익IPS (Squeeze Trend 예상) ---")
    result1 = orchestrator.process_signal(signal1)
    print(f"결과: {result1['intent']} → {result1['account']}")

    # 테스트 시그널 2: 단타 (Scalp)
    signal2 = {
        "symbol": "060280",
        "stock_name": "큐렉소",
        "current_price": 18660,
        "squeeze_on": False,
        "momentum": -0.2,
        "momentum_slope": -0.03,
        "news_persistence": "flash"
    }

    print("\n--- 시그널 2: 큐렉소 (Scalp 예상) ---")
    result2 = orchestrator.process_signal(signal2)
    print(f"결과: {result2['intent']} → {result2['account']}")

    # 중기 포지션 진입
    print("\n--- 중기 포지션 진입 ---")
    pos = orchestrator.execute_trend_entry(signal1, quantity=10)
    print(f"포지션: {pos}")

    # 일일 리포트
    print("\n" + orchestrator.get_daily_report())

    print("\n" + "=" * 70)
    print("Test Completed!")
    print("=" * 70)
