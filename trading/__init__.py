"""
Trading Package

모듈화된 자동 매매 시스템
"""

from trading.websocket_client import KiwoomWebSocketClient
from trading.position_tracker import PositionTracker, Position, ExitStage
from trading.account_manager import AccountManager
from trading.signal_detector import SignalDetector
from trading.order_executor import OrderExecutor
from trading.market_monitor import MarketMonitor
from trading.condition_scanner import ConditionScanner
from trading.trading_orchestrator import TradingOrchestrator

# Trade Intent 분류 시스템 (신규)
from trading.trade_intent import (
    TradeSignal, TradeIntent, TradeIntentClassifier, OrderRouter,
    SqueezeIndicators, TimeframeContext, NewsScore, FlowScore, NewsPersistence
)

# Trend Exit Engine (신규 - 중기 전략용)
from trading.trend_exit_engine import (
    TrendExitEngine, TrendPositionManager, TrendPosition,
    TrendExitAction, TrendExitReason, TrendExitConfig
)

# Dual Account Orchestrator (신규 - 계좌 분리 운용)
from trading.dual_account_orchestrator import (
    DualAccountOrchestrator, DualAccountConfig,
    enhance_signal_with_squeeze, should_use_trend_account
)

# Trend Account Monitor (신규 - 중기 계좌 모니터링)
from trading.trend_account_monitor import (
    TrendAccountMonitor, TrendHolding
)

__all__ = [
    # WebSocket
    'KiwoomWebSocketClient',

    # Position Management
    'PositionTracker',
    'Position',
    'ExitStage',

    # Account Management
    'AccountManager',

    # Signal Detection
    'SignalDetector',

    # Order Execution
    'OrderExecutor',

    # Market Monitoring
    'MarketMonitor',

    # Condition Scanning
    'ConditionScanner',

    # System Orchestration
    'TradingOrchestrator',

    # Trade Intent Classification (NEW)
    'TradeSignal',
    'TradeIntent',
    'TradeIntentClassifier',
    'OrderRouter',
    'SqueezeIndicators',
    'TimeframeContext',
    'NewsScore',
    'FlowScore',
    'NewsPersistence',

    # Trend Exit Engine (NEW)
    'TrendExitEngine',
    'TrendPositionManager',
    'TrendPosition',
    'TrendExitAction',
    'TrendExitReason',
    'TrendExitConfig',

    # Dual Account Orchestrator (NEW)
    'DualAccountOrchestrator',
    'DualAccountConfig',
    'enhance_signal_with_squeeze',
    'should_use_trend_account',

    # Trend Account Monitor (NEW)
    'TrendAccountMonitor',
    'TrendHolding',
]
