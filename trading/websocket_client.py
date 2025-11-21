"""
Kiwoom WebSocket 클라이언트

WebSocket 연결 및 메시지 송수신 관리
"""
import asyncio
import json
import websockets
from typing import Optional, Dict, Any
from rich.console import Console
from datetime import datetime

from exceptions import (
    handle_api_errors,
    retry_on_error,
    ConnectionError as TradingConnectionError,
    TimeoutError as TradingTimeoutError,
    AuthenticationError,
    APIException
)

console = Console()


class KiwoomWebSocketClient:
    """Kiwoom WebSocket 연결 관리"""

    def __init__(self, uri: str, access_token: str):
        """
        Args:
            uri: WebSocket URI (예: wss://api.kiwoom.com:10000/api/dostk/websocket)
            access_token: 접근 토큰
        """
        self.uri = uri
        self.access_token = access_token
        self.websocket: Optional[websockets.WebSocketClientProtocol] = None
        self.connected = False

    @retry_on_error(max_retries=2, delay=2.0, backoff=2.0, exceptions=(TradingConnectionError,))
    @handle_api_errors(raise_on_auth_error=True, log_errors=True)
    async def connect(self) -> bool:
        """
        WebSocket 연결

        Returns:
            연결 성공 여부

        Raises:
            ConnectionError: 연결 실패 시
            APIException: WebSocket 오류 시
        """
        try:
            self.websocket = await websockets.connect(self.uri)
            self.connected = True

            console.print("=" * 120, style="bold green")
            console.print(f"{'키움 통합 자동매매 시스템':^120}", style="bold green")
            console.print("=" * 120, style="bold green")
            console.print()
            console.print(f"[green]✅ WebSocket 연결 성공: {self.uri}[/green]")

            return True

        except Exception as e:
            self.connected = False
            raise TradingConnectionError(f"WebSocket 연결 실패: {str(e)}") from e

    async def disconnect(self):
        """WebSocket 연결 해제"""
        if self.websocket and self.connected:
            try:
                await self.websocket.close()
                console.print("[yellow]✓ WebSocket 연결 종료[/yellow]")
            except Exception as e:
                console.print(f"[dim red]⚠️  WebSocket 종료 중 오류: {e}[/dim red]")
            finally:
                self.connected = False
                self.websocket = None

    @handle_api_errors(raise_on_auth_error=True, log_errors=True)
    async def send_message(self, trnm: str, data: Dict[str, Any] = None) -> None:
        """
        WebSocket 메시지 전송

        Args:
            trnm: 거래명 (예: 'LOGIN', 'COND_SRCH')
            data: 추가 데이터

        Raises:
            APIException: WebSocket 미연결 또는 전송 실패 시
        """
        if not self.websocket or not self.connected:
            raise APIException("WebSocket이 연결되지 않았습니다.")

        message = {"trnm": trnm}
        if data:
            message.update(data)

        try:
            await self.websocket.send(json.dumps(message))
        except Exception as e:
            raise APIException(f"메시지 전송 실패: {str(e)}") from e

    @handle_api_errors(default_return=None, log_errors=True)
    async def receive_message(self, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
        """
        WebSocket 메시지 수신

        Args:
            timeout: 타임아웃 시간 (초)

        Returns:
            수신한 메시지 (dict) 또는 None (타임아웃 시)

        Raises:
            APIException: WebSocket 미연결 시
            TimeoutError: 타임아웃 시 (log_errors=True이므로 로깅만 됨)
        """
        if not self.websocket or not self.connected:
            raise APIException("WebSocket이 연결되지 않았습니다.")

        try:
            message = await asyncio.wait_for(self.websocket.recv(), timeout=timeout)
            return json.loads(message)

        except asyncio.TimeoutError as e:
            raise TradingTimeoutError(
                f"WebSocket 메시지 수신 타임아웃 ({timeout}초)",
                timeout_seconds=int(timeout)
            ) from e
        except json.JSONDecodeError as e:
            raise APIException(f"메시지 파싱 실패: {str(e)}") from e
        except Exception as e:
            raise APIException(f"메시지 수신 실패: {str(e)}") from e

    @retry_on_error(max_retries=1, delay=2.0, exceptions=(TradingConnectionError, TradingTimeoutError))
    @handle_api_errors(raise_on_auth_error=True, log_errors=True)
    async def login(self) -> bool:
        """
        WebSocket 로그인

        Returns:
            로그인 성공 여부

        Raises:
            AuthenticationError: 로그인 실패 시
            APIException: WebSocket 오류 시
        """
        console.print(f"[{datetime.now().strftime('%H:%M:%S')}] WebSocket 로그인 중...")

        if not self.websocket or not self.connected:
            raise APIException("WebSocket이 연결되지 않았습니다. connect()를 먼저 호출하세요.")

        # 로그인 패킷 전송
        login_packet = {
            'trnm': 'LOGIN',
            'token': self.access_token
        }

        try:
            await self.websocket.send(json.dumps(login_packet))
        except Exception as e:
            raise APIException(f"로그인 요청 전송 실패: {str(e)}") from e

        # 응답 수신
        response = await self.receive_message(timeout=15.0)

        if response is None:
            raise TradingTimeoutError("로그인 응답 타임아웃", timeout_seconds=15)

        return_code = response.get("return_code")
        return_msg = response.get("return_msg", "")

        if return_code == 0:
            console.print("✅ 로그인 성공", style="green")

            # 인증 완료 대기 (서버 처리 시간)
            console.print("[yellow]⏳ 서버 인증 처리 대기 중... (3초)[/yellow]")
            await asyncio.sleep(3.0)
            console.print("[green]✅ 인증 완료[/green]")
            console.print()

            return True
        else:
            raise AuthenticationError(
                f"WebSocket 로그인 실패: {return_msg}",
                response_data=response
            )

    async def is_connected(self) -> bool:
        """
        WebSocket 연결 상태 확인

        Returns:
            연결 여부
        """
        if not self.websocket or not self.connected:
            return False

        try:
            # Ping/Pong으로 연결 상태 확인
            pong = await self.websocket.ping()
            await asyncio.wait_for(pong, timeout=5.0)
            return True
        except:
            self.connected = False
            return False

    async def __aenter__(self):
        """비동기 컨텍스트 매니저 진입"""
        await self.connect()
        await self.login()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """비동기 컨텍스트 매니저 종료"""
        await self.disconnect()
