"""
WebSocket 연결 관리 모듈

중복 코드 제거:
- main_auto_trading.py의 IntegratedTradingSystem WebSocket 로직
- main_condition_filter.py의 KiwoomVWAPPipeline WebSocket 로직
"""
import asyncio
import websockets
import json
from typing import Optional, Callable, Any, Dict
from rich.console import Console

console = Console()


class WebSocketManager:
    """WebSocket 연결 관리자"""

    def __init__(self, url: str, verbose: bool = True):
        """
        Args:
            url: WebSocket URL
            verbose: 로그 출력 여부
        """
        self.url = url
        self.verbose = verbose
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False
        self.is_authenticated = False

    def _log(self, message: str, style: str = "cyan"):
        """로그 출력"""
        if self.verbose:
            console.print(f"[{style}]{message}[/{style}]")

    async def connect(self, timeout: int = 10) -> bool:
        """
        WebSocket 연결

        Args:
            timeout: 연결 타임아웃 (초)

        Returns:
            연결 성공 여부

        Example:
            >>> ws_manager = WebSocketManager(url)
            >>> if await ws_manager.connect():
            >>>     print("연결 성공")
        """
        try:
            self._log(f"🔌 WebSocket 연결 중: {self.url}")

            self.ws = await asyncio.wait_for(
                websockets.connect(self.url),
                timeout=timeout
            )

            self.is_connected = True
            self._log("✅ WebSocket 연결 성공", "green")
            return True

        except asyncio.TimeoutError:
            self._log(f"❌ WebSocket 연결 타임아웃 ({timeout}초)", "red")
            return False

        except Exception as e:
            self._log(f"❌ WebSocket 연결 실패: {e}", "red")
            return False

    async def disconnect(self):
        """WebSocket 연결 종료"""
        if self.ws:
            try:
                await self.ws.close()
                self._log("🔌 WebSocket 연결 종료", "yellow")
            except Exception as e:
                self._log(f"⚠️  WebSocket 종료 오류: {e}", "yellow")
            finally:
                self.ws = None
                self.is_connected = False
                self.is_authenticated = False

    async def send_message(self, message: Dict[str, Any]) -> bool:
        """
        메시지 전송

        Args:
            message: 전송할 메시지 (dict)

        Returns:
            전송 성공 여부

        Example:
            >>> await ws_manager.send_message({
            >>>     "header": {"function": "login"},
            >>>     "body": {"appkey": "xxx", "appsecret": "yyy"}
            >>> })
        """
        if not self.is_connected or not self.ws:
            self._log("❌ WebSocket 연결되지 않음", "red")
            return False

        try:
            message_str = json.dumps(message, ensure_ascii=False)
            await self.ws.send(message_str)

            if self.verbose:
                func = message.get('header', {}).get('function', 'unknown')
                self._log(f"📤 메시지 전송: {func}", "dim")

            return True

        except Exception as e:
            self._log(f"❌ 메시지 전송 실패: {e}", "red")
            return False

    async def receive_message(self, timeout: int = 30) -> Optional[Dict[str, Any]]:
        """
        메시지 수신

        Args:
            timeout: 수신 타임아웃 (초)

        Returns:
            수신된 메시지 (dict) 또는 None

        Example:
            >>> response = await ws_manager.receive_message()
            >>> if response:
            >>>     print(response)
        """
        if not self.is_connected or not self.ws:
            self._log("❌ WebSocket 연결되지 않음", "red")
            return None

        try:
            message_str = await asyncio.wait_for(
                self.ws.recv(),
                timeout=timeout
            )

            message = json.loads(message_str)

            if self.verbose:
                func = message.get('header', {}).get('function', 'unknown')
                self._log(f"📥 메시지 수신: {func}", "dim")

            return message

        except asyncio.TimeoutError:
            self._log(f"⚠️  메시지 수신 타임아웃 ({timeout}초)", "yellow")
            return None

        except json.JSONDecodeError as e:
            self._log(f"❌ JSON 파싱 실패: {e}", "red")
            return None

        except Exception as e:
            self._log(f"❌ 메시지 수신 실패: {e}", "red")
            return None

    async def login(self, credentials: Dict[str, str], timeout: int = 10) -> bool:
        """
        Kiwoom API 로그인

        Args:
            credentials: 인증 정보
                - appkey: 앱 키
                - appsecret: 앱 시크릿
            timeout: 타임아웃 (초)

        Returns:
            로그인 성공 여부

        Example:
            >>> credentials = {"appkey": "xxx", "appsecret": "yyy"}
            >>> if await ws_manager.login(credentials):
            >>>     print("로그인 성공")
        """
        if not self.is_connected:
            self._log("❌ WebSocket 연결 필요", "red")
            return False

        try:
            # 로그인 메시지
            login_msg = {
                "header": {
                    "function": "login"
                },
                "body": {
                    "appkey": credentials.get('appkey'),
                    "appsecret": credentials.get('appsecret')
                }
            }

            # 전송
            if not await self.send_message(login_msg):
                return False

            # 응답 수신
            response = await self.receive_message(timeout=timeout)

            if not response:
                self._log("❌ 로그인 응답 없음", "red")
                return False

            # 응답 확인
            body = response.get('body', {})
            result = body.get('result')

            if result == 'success':
                self.is_authenticated = True
                self._log("✅ 로그인 성공", "green")
                return True
            else:
                error_msg = body.get('msg', 'Unknown error')
                self._log(f"❌ 로그인 실패: {error_msg}", "red")
                return False

        except Exception as e:
            self._log(f"❌ 로그인 오류: {e}", "red")
            return False

    async def send_and_receive(
        self,
        message: Dict[str, Any],
        timeout: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        메시지 전송 및 응답 수신 (원자적 작업)

        Args:
            message: 전송할 메시지
            timeout: 수신 타임아웃

        Returns:
            응답 메시지 또는 None

        Example:
            >>> request = {"header": {"function": "search"}, "body": {...}}
            >>> response = await ws_manager.send_and_receive(request)
        """
        if not await self.send_message(message):
            return None

        return await self.receive_message(timeout=timeout)

    async def keep_alive(self, interval: int = 60):
        """
        연결 유지 (ping)

        Args:
            interval: ping 간격 (초)

        Note:
            별도 태스크에서 실행 필요
            ```python
            task = asyncio.create_task(ws_manager.keep_alive())
            ```
        """
        while self.is_connected:
            try:
                if self.ws:
                    await self.ws.ping()
                    self._log("💓 Ping 전송", "dim")

                await asyncio.sleep(interval)

            except Exception as e:
                self._log(f"⚠️  Ping 실패: {e}", "yellow")
                break

    async def __aenter__(self):
        """비동기 컨텍스트 매니저 진입"""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """비동기 컨텍스트 매니저 종료"""
        await self.disconnect()


class KiwoomWebSocketManager(WebSocketManager):
    """Kiwoom 전용 WebSocket 관리자 (확장)"""

    def __init__(self, url: str, credentials: Dict[str, str], verbose: bool = True):
        """
        Args:
            url: WebSocket URL
            credentials: Kiwoom 인증 정보
            verbose: 로그 출력 여부
        """
        super().__init__(url, verbose)
        self.credentials = credentials

    async def start(self) -> bool:
        """
        WebSocket 시작 (연결 + 로그인)

        Returns:
            시작 성공 여부

        Example:
            >>> manager = KiwoomWebSocketManager(url, credentials)
            >>> if await manager.start():
            >>>     print("준비 완료")
        """
        # 연결
        if not await self.connect():
            return False

        # 로그인
        if not await self.login(self.credentials):
            await self.disconnect()
            return False

        return True

    async def search_condition(
        self,
        condition_name: str,
        timeout: int = 30
    ) -> Optional[Dict[str, Any]]:
        """
        조건식 검색

        Args:
            condition_name: 조건식 이름
            timeout: 타임아웃

        Returns:
            검색 결과 또는 None
        """
        if not self.is_authenticated:
            self._log("❌ 로그인 필요", "red")
            return None

        message = {
            "header": {
                "function": "condition_search"
            },
            "body": {
                "condition_name": condition_name
            }
        }

        return await self.send_and_receive(message, timeout=timeout)

    async def subscribe_price(
        self,
        stock_code: str,
        callback: Optional[Callable] = None
    ) -> bool:
        """
        실시간 가격 구독

        Args:
            stock_code: 종목 코드
            callback: 가격 업데이트 콜백 함수

        Returns:
            구독 성공 여부
        """
        if not self.is_authenticated:
            self._log("❌ 로그인 필요", "red")
            return False

        message = {
            "header": {
                "function": "subscribe"
            },
            "body": {
                "type": "price",
                "code": stock_code
            }
        }

        return await self.send_message(message)
