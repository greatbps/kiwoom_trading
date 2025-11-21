"""
WebSocketManager 테스트
"""
import pytest
import json
from unittest.mock import AsyncMock, MagicMock, patch
from core.websocket.websocket_manager import WebSocketManager, KiwoomWebSocketManager


class TestWebSocketManager:
    """WebSocketManager 단위 테스트"""

    @pytest.fixture
    def ws_url(self):
        """WebSocket URL"""
        return "wss://test.example.com/websocket"

    @pytest.fixture
    def manager(self, ws_url):
        """WebSocketManager 인스턴스"""
        return WebSocketManager(ws_url, verbose=False)

    @pytest.mark.asyncio
    async def test_connect_success(self, manager):
        """WebSocket 연결 성공"""
        # Given
        mock_ws = AsyncMock()

        with patch('websockets.connect', return_value=mock_ws):
            # When
            result = await manager.connect()

            # Then
            assert result is True
            assert manager.is_connected is True
            assert manager.ws == mock_ws

    @pytest.mark.asyncio
    async def test_connect_timeout(self, manager):
        """WebSocket 연결 타임아웃"""
        # Given
        with patch('websockets.connect', side_effect=TimeoutError):
            # When
            result = await manager.connect(timeout=1)

            # Then
            assert result is False
            assert manager.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect(self, manager):
        """WebSocket 연결 종료"""
        # Given
        mock_ws = AsyncMock()
        manager.ws = mock_ws
        manager.is_connected = True

        # When
        await manager.disconnect()

        # Then
        assert mock_ws.close.called
        assert manager.is_connected is False
        assert manager.ws is None

    @pytest.mark.asyncio
    async def test_send_message_success(self, manager):
        """메시지 전송 성공"""
        # Given
        mock_ws = AsyncMock()
        manager.ws = mock_ws
        manager.is_connected = True

        message = {"header": {"function": "test"}, "body": {}}

        # When
        result = await manager.send_message(message)

        # Then
        assert result is True
        assert mock_ws.send.called

    @pytest.mark.asyncio
    async def test_send_message_not_connected(self, manager):
        """연결되지 않은 상태에서 메시지 전송 실패"""
        # Given
        message = {"header": {"function": "test"}}

        # When
        result = await manager.send_message(message)

        # Then
        assert result is False

    @pytest.mark.asyncio
    async def test_receive_message_success(self, manager):
        """메시지 수신 성공"""
        # Given
        mock_ws = AsyncMock()
        response_data = {"header": {"function": "response"}, "body": {"result": "ok"}}
        mock_ws.recv.return_value = json.dumps(response_data)

        manager.ws = mock_ws
        manager.is_connected = True

        # When
        result = await manager.receive_message()

        # Then
        assert result is not None
        assert result['body']['result'] == 'ok'

    @pytest.mark.asyncio
    async def test_receive_message_timeout(self, manager):
        """메시지 수신 타임아웃"""
        # Given
        mock_ws = AsyncMock()
        mock_ws.recv.side_effect = TimeoutError

        manager.ws = mock_ws
        manager.is_connected = True

        # When
        result = await manager.receive_message(timeout=1)

        # Then
        assert result is None

    @pytest.mark.asyncio
    async def test_receive_message_json_error(self, manager):
        """JSON 파싱 오류"""
        # Given
        mock_ws = AsyncMock()
        mock_ws.recv.return_value = "invalid json"

        manager.ws = mock_ws
        manager.is_connected = True

        # When
        result = await manager.receive_message()

        # Then
        assert result is None

    @pytest.mark.asyncio
    async def test_login_success(self, manager):
        """로그인 성공"""
        # Given
        mock_ws = AsyncMock()
        response = {"body": {"result": "success"}}
        mock_ws.recv.return_value = json.dumps(response)

        manager.ws = mock_ws
        manager.is_connected = True

        credentials = {"appkey": "test_key", "appsecret": "test_secret"}

        # When
        result = await manager.login(credentials)

        # Then
        assert result is True
        assert manager.is_authenticated is True

    @pytest.mark.asyncio
    async def test_login_failure(self, manager):
        """로그인 실패"""
        # Given
        mock_ws = AsyncMock()
        response = {"body": {"result": "failure", "msg": "Invalid credentials"}}
        mock_ws.recv.return_value = json.dumps(response)

        manager.ws = mock_ws
        manager.is_connected = True

        credentials = {"appkey": "wrong_key", "appsecret": "wrong_secret"}

        # When
        result = await manager.login(credentials)

        # Then
        assert result is False
        assert manager.is_authenticated is False

    @pytest.mark.asyncio
    async def test_send_and_receive(self, manager):
        """메시지 전송 및 응답 수신"""
        # Given
        mock_ws = AsyncMock()
        response_data = {"body": {"result": "ok"}}
        mock_ws.recv.return_value = json.dumps(response_data)

        manager.ws = mock_ws
        manager.is_connected = True

        message = {"header": {"function": "test"}, "body": {}}

        # When
        result = await manager.send_and_receive(message)

        # Then
        assert result is not None
        assert result['body']['result'] == 'ok'

    @pytest.mark.asyncio
    async def test_context_manager(self, ws_url):
        """비동기 컨텍스트 매니저"""
        # Given
        mock_ws = AsyncMock()

        with patch('websockets.connect', return_value=mock_ws):
            # When
            async with WebSocketManager(ws_url, verbose=False) as manager:
                # Then
                assert manager.is_connected is True

            # After context exit
            assert mock_ws.close.called


class TestKiwoomWebSocketManager:
    """KiwoomWebSocketManager 단위 테스트"""

    @pytest.fixture
    def credentials(self):
        """Kiwoom 인증 정보"""
        return {"appkey": "test_key", "appsecret": "test_secret"}

    @pytest.fixture
    def manager(self, credentials):
        """KiwoomWebSocketManager 인스턴스"""
        return KiwoomWebSocketManager(
            "wss://test.example.com/websocket",
            credentials,
            verbose=False
        )

    @pytest.mark.asyncio
    async def test_start_success(self, manager):
        """WebSocket 시작 성공"""
        # Given
        with patch.object(manager, 'connect', return_value=True):
            with patch.object(manager, 'login', return_value=True):
                # When
                result = await manager.start()

                # Then
                assert result is True

    @pytest.mark.asyncio
    async def test_start_connect_failure(self, manager):
        """연결 실패 시 시작 실패"""
        # Given
        with patch.object(manager, 'connect', return_value=False):
            # When
            result = await manager.start()

            # Then
            assert result is False

    @pytest.mark.asyncio
    async def test_start_login_failure(self, manager):
        """로그인 실패 시 시작 실패 및 연결 종료"""
        # Given
        with patch.object(manager, 'connect', return_value=True):
            with patch.object(manager, 'login', return_value=False):
                with patch.object(manager, 'disconnect') as mock_disconnect:
                    # When
                    result = await manager.start()

                    # Then
                    assert result is False
                    assert mock_disconnect.called

    @pytest.mark.asyncio
    async def test_search_condition(self, manager):
        """조건식 검색"""
        # Given
        manager.is_authenticated = True

        response_data = {"body": {"stocks": ["005930", "000660"]}}

        with patch.object(manager, 'send_and_receive', return_value=response_data):
            # When
            result = await manager.search_condition("test_condition")

            # Then
            assert result is not None
            assert len(result['body']['stocks']) == 2

    @pytest.mark.asyncio
    async def test_search_condition_not_authenticated(self, manager):
        """인증되지 않은 상태에서 조건식 검색 실패"""
        # Given
        manager.is_authenticated = False

        # When
        result = await manager.search_condition("test_condition")

        # Then
        assert result is None

    @pytest.mark.asyncio
    async def test_subscribe_price(self, manager):
        """실시간 가격 구독"""
        # Given
        manager.is_authenticated = True

        with patch.object(manager, 'send_message', return_value=True):
            # When
            result = await manager.subscribe_price('005930')

            # Then
            assert result is True

    @pytest.mark.asyncio
    async def test_subscribe_price_not_authenticated(self, manager):
        """인증되지 않은 상태에서 구독 실패"""
        # Given
        manager.is_authenticated = False

        # When
        result = await manager.subscribe_price('005930')

        # Then
        assert result is False
