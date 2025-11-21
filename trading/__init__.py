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
]
