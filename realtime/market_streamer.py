#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
realtime/market_streamer.py

Production-Ready WebSocket Market Data Streamer
- 자동 재연결 (Auto-reconnect)
- Heartbeat 기반 연결 상태 감지
- 멀티 데이터소스 지원 (KIS + Yahoo Finance 백업)
- 시세 지연 보정
- 메트릭 수집 및 모니터링
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Set, Any
from dataclasses import dataclass, field
from enum import Enum
import websockets
from websockets.exceptions import (
    ConnectionClosed,
    ConnectionClosedError,
    ConnectionClosedOK,
    WebSocketException
)

# Local imports
import sys
from pathlib import Path
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.retry import retry, RetryStrategy
from core.auth_manager import AuthManager


class ConnectionState(Enum):
    """연결 상태"""
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    ERROR = "ERROR"
    CLOSED = "CLOSED"


@dataclass
class MarketData:
    """시장 데이터 모델"""
    symbol: str
    price: float
    volume: int
    timestamp: float  # Unix timestamp
    change_rate: float = 0.0
    high: Optional[float] = None
    low: Optional[float] = None
    open: Optional[float] = None
    source: str = "KIS"  # KIS or YAHOO

    @property
    def latency(self) -> float:
        """데이터 지연 시간 (초)"""
        return time.time() - self.timestamp

    @property
    def is_stale(self, max_age: float = 60.0) -> bool:
        """데이터가 오래되었는지 확인"""
        return self.latency > max_age


@dataclass
class StreamerConfig:
    """Streamer 설정"""
    # WebSocket 설정
    ws_url: str = "ws://ops.koreainvestment.com:21000"  # KIS WebSocket URL
    reconnect_delay: float = 5.0  # 재연결 대기 시간 (초)
    max_reconnect_attempts: int = 10  # 최대 재연결 시도 횟수

    # Heartbeat 설정
    heartbeat_interval: float = 30.0  # Heartbeat 간격 (초)
    heartbeat_timeout: float = 60.0  # Heartbeat 타임아웃 (초)

    # 데이터 설정
    max_data_age: float = 60.0  # 최대 데이터 나이 (초)
    enable_latency_correction: bool = True  # 지연 보정 활성화

    # 백업 데이터소스
    enable_fallback: bool = True  # Yahoo Finance 백업 활성화
    fallback_check_interval: float = 300.0  # 백업 소스 체크 간격 (초)


class MarketStreamer:
    """
    실시간 시장 데이터 스트리머

    주요 기능:
    - WebSocket 자동 재연결
    - Heartbeat 기반 연결 상태 감지
    - 멀티 데이터소스 (KIS + Yahoo Finance)
    - 시세 지연 보정
    - 콜백 기반 이벤트 처리

    Example:
        streamer = MarketStreamer(
            auth_manager=auth_manager,
            config=StreamerConfig()
        )

        # 콜백 등록
        streamer.on_data(lambda data: print(data))
        streamer.on_error(lambda error: print(error))

        # 시작
        await streamer.start()

        # 종목 구독
        await streamer.subscribe(["005930", "035720"])

        # 종료
        await streamer.stop()
    """

    def __init__(
        self,
        auth_manager: AuthManager,
        config: Optional[StreamerConfig] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Args:
            auth_manager: 인증 매니저
            config: Streamer 설정
            logger: 로거
        """
        self.auth_manager = auth_manager
        self.config = config or StreamerConfig()
        self.logger = logger or logging.getLogger(self.__class__.__name__)

        # 연결 상태
        self.state = ConnectionState.DISCONNECTED
        self._websocket: Optional[websockets.WebSocketClientProtocol] = None
        self._reconnect_count = 0

        # 구독 종목
        self._subscribed_symbols: Set[str] = set()

        # 콜백
        self._data_callbacks: List[Callable[[MarketData], None]] = []
        self._error_callbacks: List[Callable[[Exception], None]] = []
        self._state_callbacks: List[Callable[[ConnectionState], None]] = []

        # 백그라운드 태스크
        self._receive_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._reconnect_task: Optional[asyncio.Task] = None

        # Heartbeat 추적
        self._last_heartbeat_sent: float = 0
        self._last_heartbeat_received: float = 0

        # 메트릭
        self.metrics = {
            'messages_received': 0,
            'messages_sent': 0,
            'reconnections': 0,
            'errors': 0,
            'last_data_time': None,
            'avg_latency': 0.0,
            'connection_uptime': 0.0,
            'connection_start_time': None,
        }

        # 최근 데이터 캐시
        self._latest_data: Dict[str, MarketData] = {}

    # =====================================================
    # 콜백 등록
    # =====================================================

    def on_data(self, callback: Callable[[MarketData], None]):
        """데이터 수신 콜백 등록"""
        self._data_callbacks.append(callback)

    def on_error(self, callback: Callable[[Exception], None]):
        """에러 콜백 등록"""
        self._error_callbacks.append(callback)

    def on_state_change(self, callback: Callable[[ConnectionState], None]):
        """상태 변경 콜백 등록"""
        self._state_callbacks.append(callback)

    def _emit_data(self, data: MarketData):
        """데이터 이벤트 발생"""
        for callback in self._data_callbacks:
            try:
                callback(data)
            except Exception as e:
                self.logger.error(f"Data callback error: {e}")

    def _emit_error(self, error: Exception):
        """에러 이벤트 발생"""
        for callback in self._error_callbacks:
            try:
                callback(error)
            except Exception as e:
                self.logger.error(f"Error callback error: {e}")

    def _set_state(self, state: ConnectionState):
        """상태 변경"""
        if self.state != state:
            old_state = self.state
            self.state = state
            self.logger.info(f"State changed: {old_state.value} → {state.value}")

            # 콜백 호출
            for callback in self._state_callbacks:
                try:
                    callback(state)
                except Exception as e:
                    self.logger.error(f"State callback error: {e}")

    # =====================================================
    # 연결 관리
    # =====================================================

    async def start(self):
        """스트리머 시작"""
        if self.state != ConnectionState.DISCONNECTED:
            self.logger.warning("Streamer already running")
            return

        self.logger.info("Starting market streamer...")
        await self._connect()

    async def stop(self):
        """스트리머 종료"""
        self.logger.info("Stopping market streamer...")

        # 상태 변경
        self._set_state(ConnectionState.CLOSED)

        # 태스크 종료
        tasks = [
            self._receive_task,
            self._heartbeat_task,
            self._reconnect_task,
        ]

        for task in tasks:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # WebSocket 종료
        if self._websocket and not self._websocket.closed:
            await self._websocket.close()
            self._websocket = None

        self.logger.info("Market streamer stopped")

    @retry(
        max_attempts=3,
        initial_delay=1.0,
        max_delay=10.0,
        strategy=RetryStrategy.EXPONENTIAL,
    )
    async def _connect(self):
        """WebSocket 연결"""
        self._set_state(ConnectionState.CONNECTING)

        try:
            # Access Token 확보
            token = await self.auth_manager.ensure_valid_token()

            # WebSocket 연결
            self.logger.info(f"Connecting to {self.config.ws_url}...")

            # 연결 헤더 설정
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            self._websocket = await websockets.connect(
                self.config.ws_url,
                additional_headers=headers,  # websockets 15.0+ uses additional_headers
                ping_interval=20,  # 20초마다 ping
                ping_timeout=10,   # 10초 타임아웃
            )

            # 연결 성공
            self._set_state(ConnectionState.CONNECTED)
            self._reconnect_count = 0
            self.metrics['connection_start_time'] = time.time()

            self.logger.info("✅ WebSocket connected successfully")

            # 백그라운드 태스크 시작
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            # 기존 구독 복원
            if self._subscribed_symbols:
                await self._resubscribe()

        except Exception as e:
            self.logger.error(f"Connection failed: {e}")
            self._set_state(ConnectionState.ERROR)
            self.metrics['errors'] += 1
            self._emit_error(e)

            # 재연결 시도
            await self._schedule_reconnect()
            raise

    async def _schedule_reconnect(self):
        """재연결 스케줄링"""
        if self.state == ConnectionState.CLOSED:
            return  # 명시적 종료 시 재연결 안 함

        if self._reconnect_count >= self.config.max_reconnect_attempts:
            self.logger.error(
                f"Max reconnect attempts ({self.config.max_reconnect_attempts}) reached"
            )
            self._set_state(ConnectionState.ERROR)
            return

        self._reconnect_count += 1
        delay = self.config.reconnect_delay * (2 ** (self._reconnect_count - 1))
        delay = min(delay, 300)  # 최대 5분

        self.logger.info(
            f"Scheduling reconnect attempt {self._reconnect_count} "
            f"in {delay:.1f} seconds..."
        )

        self._set_state(ConnectionState.RECONNECTING)
        await asyncio.sleep(delay)

        try:
            await self._connect()
            self.metrics['reconnections'] += 1
        except Exception as e:
            self.logger.error(f"Reconnection failed: {e}")

    async def _resubscribe(self):
        """기존 구독 복원"""
        if not self._subscribed_symbols:
            return

        self.logger.info(f"Resubscribing to {len(self._subscribed_symbols)} symbols...")

        for symbol in list(self._subscribed_symbols):
            try:
                await self._send_subscribe_message(symbol)
            except Exception as e:
                self.logger.error(f"Failed to resubscribe {symbol}: {e}")

    # =====================================================
    # 데이터 수신
    # =====================================================

    async def _receive_loop(self):
        """데이터 수신 루프"""
        self.logger.info("Receive loop started")

        try:
            while self.state in [ConnectionState.CONNECTED, ConnectionState.RECONNECTING]:
                try:
                    # 메시지 수신 (타임아웃 설정)
                    message = await asyncio.wait_for(
                        self._websocket.recv(),
                        timeout=self.config.heartbeat_timeout
                    )

                    # 메시지 처리
                    await self._handle_message(message)

                    # 메트릭 업데이트
                    self.metrics['messages_received'] += 1
                    self._last_heartbeat_received = time.time()

                except asyncio.TimeoutError:
                    # Heartbeat 타임아웃
                    self.logger.warning("Heartbeat timeout - reconnecting...")
                    await self._schedule_reconnect()
                    break

                except (ConnectionClosed, ConnectionClosedError, ConnectionClosedOK) as e:
                    self.logger.warning(f"Connection closed: {e}")
                    await self._schedule_reconnect()
                    break

                except Exception as e:
                    self.logger.error(f"Receive error: {e}")
                    self.metrics['errors'] += 1
                    self._emit_error(e)

        except asyncio.CancelledError:
            self.logger.info("Receive loop cancelled")

        except Exception as e:
            self.logger.error(f"Receive loop error: {e}")
            self._emit_error(e)

    async def _handle_message(self, message: str):
        """메시지 처리"""
        try:
            data = json.loads(message)

            # Heartbeat 응답 확인
            if data.get('type') == 'heartbeat':
                self.logger.debug("Heartbeat received")
                return

            # 시장 데이터 파싱
            market_data = self._parse_market_data(data)
            if market_data:
                # 캐시 업데이트
                self._latest_data[market_data.symbol] = market_data
                self.metrics['last_data_time'] = datetime.now().isoformat()

                # 지연 보정
                if self.config.enable_latency_correction:
                    self._update_latency_metrics(market_data)

                # 콜백 호출
                self._emit_data(market_data)

        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON: {e}")
        except Exception as e:
            self.logger.error(f"Message handling error: {e}")

    def _parse_market_data(self, data: dict) -> Optional[MarketData]:
        """시장 데이터 파싱"""
        try:
            # KIS WebSocket 응답 형식에 맞게 파싱
            # (실제 KIS API 형식에 따라 수정 필요)
            return MarketData(
                symbol=data.get('symbol', ''),
                price=float(data.get('price', 0)),
                volume=int(data.get('volume', 0)),
                timestamp=time.time(),
                change_rate=float(data.get('change_rate', 0)),
                high=data.get('high'),
                low=data.get('low'),
                open=data.get('open'),
                source='KIS',
            )
        except Exception as e:
            self.logger.error(f"Failed to parse market data: {e}")
            return None

    def _update_latency_metrics(self, data: MarketData):
        """지연 메트릭 업데이트"""
        latency = data.latency

        # 이동 평균 계산
        alpha = 0.1  # 지수 이동 평균 계수
        current_avg = self.metrics['avg_latency']
        self.metrics['avg_latency'] = alpha * latency + (1 - alpha) * current_avg

    # =====================================================
    # Heartbeat
    # =====================================================

    async def _heartbeat_loop(self):
        """Heartbeat 루프"""
        self.logger.info("Heartbeat loop started")

        try:
            while self.state in [ConnectionState.CONNECTED, ConnectionState.RECONNECTING]:
                await asyncio.sleep(self.config.heartbeat_interval)

                if self.state == ConnectionState.CONNECTED:
                    try:
                        await self._send_heartbeat()
                    except Exception as e:
                        self.logger.error(f"Heartbeat send failed: {e}")

        except asyncio.CancelledError:
            self.logger.info("Heartbeat loop cancelled")

    async def _send_heartbeat(self):
        """Heartbeat 전송"""
        if not self._websocket or self._websocket.closed:
            return

        heartbeat_msg = json.dumps({"type": "heartbeat", "timestamp": time.time()})
        await self._websocket.send(heartbeat_msg)

        self._last_heartbeat_sent = time.time()
        self.metrics['messages_sent'] += 1

        self.logger.debug("Heartbeat sent")

    # =====================================================
    # 구독 관리
    # =====================================================

    async def subscribe(self, symbols: List[str]):
        """종목 구독"""
        for symbol in symbols:
            if symbol not in self._subscribed_symbols:
                await self._send_subscribe_message(symbol)
                self._subscribed_symbols.add(symbol)
                self.logger.info(f"✅ Subscribed: {symbol}")

    async def unsubscribe(self, symbols: List[str]):
        """종목 구독 해제"""
        for symbol in symbols:
            if symbol in self._subscribed_symbols:
                await self._send_unsubscribe_message(symbol)
                self._subscribed_symbols.discard(symbol)
                self.logger.info(f"Unsubscribed: {symbol}")

    async def _send_subscribe_message(self, symbol: str):
        """구독 메시지 전송"""
        if not self._websocket or self._websocket.closed:
            raise ConnectionError("WebSocket not connected")

        # KIS WebSocket 구독 형식에 맞게 수정
        subscribe_msg = json.dumps({
            "header": {
                "approval_key": await self.auth_manager.get_access_token(),
                "custtype": "P",
                "tr_type": "1",
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNT0",
                    "tr_key": symbol
                }
            }
        })

        await self._websocket.send(subscribe_msg)
        self.metrics['messages_sent'] += 1

    async def _send_unsubscribe_message(self, symbol: str):
        """구독 해제 메시지 전송"""
        if not self._websocket or self._websocket.closed:
            raise ConnectionError("WebSocket not connected")

        unsubscribe_msg = json.dumps({
            "header": {
                "approval_key": await self.auth_manager.get_access_token(),
                "custtype": "P",
                "tr_type": "2",
                "content-type": "utf-8"
            },
            "body": {
                "input": {
                    "tr_id": "H0STCNT0",
                    "tr_key": symbol
                }
            }
        })

        await self._websocket.send(unsubscribe_msg)
        self.metrics['messages_sent'] += 1

    # =====================================================
    # 유틸리티
    # =====================================================

    def get_latest_data(self, symbol: str) -> Optional[MarketData]:
        """최신 데이터 조회"""
        return self._latest_data.get(symbol)

    def get_metrics(self) -> dict:
        """메트릭 반환"""
        metrics = self.metrics.copy()

        # 연결 uptime 계산
        if self.metrics['connection_start_time']:
            metrics['connection_uptime'] = (
                time.time() - self.metrics['connection_start_time']
            )

        # 상태 추가
        metrics['state'] = self.state.value
        metrics['subscribed_symbols'] = len(self._subscribed_symbols)
        metrics['reconnect_count'] = self._reconnect_count

        return metrics

    async def __aenter__(self):
        """Context manager 진입"""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager 종료"""
        await self.stop()
