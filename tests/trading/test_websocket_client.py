"""
Tests for KiwoomWebSocketClient
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime

from trading.websocket_client import KiwoomWebSocketClient
from exceptions import (
    TradingConnectionError,
    TradingTimeoutError,
    AuthenticationError
)


class TestKiwoomWebSocketClient:
    """KiwoomWebSocketClient 테스트"""

    @pytest.fixture
    def ws_client(self):
        """WebSocket 클라이언트 fixture"""
        uri = "wss://test.kiwoom.com/websocket"
        token = "test_token_123"
        return KiwoomWebSocketClient(uri, token)

    @pytest.mark.asyncio
    async def test_init(self, ws_client):
        """초기화 테스트"""
        assert ws_client.uri == "wss://test.kiwoom.com/websocket"
        assert ws_client.access_token == "test_token_123"
        assert ws_client.websocket is None
        assert ws_client.connected is False

    @pytest.mark.asyncio
    async def test_connect_success(self, ws_client):
        """WebSocket 연결 성공 테스트"""
        mock_websocket = AsyncMock()

        with patch('trading.websocket_client.websockets.connect', return_value=mock_websocket):
            result = await ws_client.connect()

            assert result is True
            assert ws_client.websocket == mock_websocket
            assert ws_client.connected is True

    @pytest.mark.asyncio
    async def test_connect_failure(self, ws_client):
        """WebSocket 연결 실패 테스트"""
        with patch('trading.websocket_client.websockets.connect', side_effect=Exception("Connection failed")):
            with pytest.raises(TradingConnectionError):
                await ws_client.connect()

    @pytest.mark.asyncio
    async def test_connect_with_retry(self, ws_client):
        """WebSocket 연결 재시도 테스트"""
        mock_websocket = AsyncMock()

        # 첫 번째 시도 실패, 두 번째 시도 성공
        with patch('trading.websocket_client.websockets.connect', side_effect=[
            Exception("Connection failed"),
            mock_websocket
        ]):
            result = await ws_client.connect()

            assert result is True
            assert ws_client.connected is True

    @pytest.mark.asyncio
    async def test_disconnect(self, ws_client):
        """WebSocket 연결 해제 테스트"""
        mock_websocket = AsyncMock()
        ws_client.websocket = mock_websocket
        ws_client.connected = True

        await ws_client.disconnect()

        mock_websocket.close.assert_called_once()
        assert ws_client.websocket is None
        assert ws_client.connected is False

    @pytest.mark.asyncio
    async def test_disconnect_when_not_connected(self, ws_client):
        """연결되지 않은 상태에서 disconnect 호출 테스트"""
        await ws_client.disconnect()
        # 에러 없이 정상 종료되어야 함
        assert ws_client.websocket is None
        assert ws_client.connected is False

    @pytest.mark.asyncio
    async def test_send_message_success(self, ws_client):
        """메시지 전송 성공 테스트"""
        mock_websocket = AsyncMock()
        ws_client.websocket = mock_websocket
        ws_client.connected = True

        await ws_client.send_message("test_trnm", {"key": "value"})

        mock_websocket.send.assert_called_once()
        # 전송된 메시지 확인
        call_args = mock_websocket.send.call_args[0][0]
        assert "test_trnm" in call_args
        assert "key" in call_args

    @pytest.mark.asyncio
    async def test_send_message_not_connected(self, ws_client):
        """연결되지 않은 상태에서 메시지 전송 시도 테스트"""
        ws_client.connected = False

        with pytest.raises(TradingConnectionError):
            await ws_client.send_message("test_trnm", {"key": "value"})

    @pytest.mark.asyncio
    async def test_receive_message_success(self, ws_client):
        """메시지 수신 성공 테스트"""
        mock_websocket = AsyncMock()
        mock_websocket.recv.return_value = '{"header": {"tr_id": "test"}, "body": {"data": "test"}}'

        ws_client.websocket = mock_websocket
        ws_client.connected = True

        result = await ws_client.receive_message(timeout=5.0)

        assert result is not None
        assert "header" in result
        assert result["header"]["tr_id"] == "test"

    @pytest.mark.asyncio
    async def test_receive_message_timeout(self, ws_client):
        """메시지 수신 타임아웃 테스트"""
        mock_websocket = AsyncMock()
        # recv가 타임아웃되도록 설정
        async def slow_recv():
            await asyncio.sleep(10)  # 10초 대기
            return "{}"

        mock_websocket.recv = slow_recv
        ws_client.websocket = mock_websocket
        ws_client.connected = True

        with pytest.raises(TradingTimeoutError):
            await ws_client.receive_message(timeout=0.1)

    @pytest.mark.asyncio
    async def test_receive_message_not_connected(self, ws_client):
        """연결되지 않은 상태에서 메시지 수신 시도 테스트"""
        ws_client.connected = False

        with pytest.raises(TradingConnectionError):
            await ws_client.receive_message()

    @pytest.mark.asyncio
    async def test_receive_message_invalid_json(self, ws_client):
        """잘못된 JSON 메시지 수신 테스트"""
        mock_websocket = AsyncMock()
        mock_websocket.recv.return_value = "invalid json"

        ws_client.websocket = mock_websocket
        ws_client.connected = True

        result = await ws_client.receive_message()

        # 잘못된 JSON은 None 반환
        assert result is None

    @pytest.mark.asyncio
    async def test_login_success(self, ws_client):
        """WebSocket 로그인 성공 테스트"""
        mock_websocket = AsyncMock()
        mock_websocket.recv.return_value = '{"header": {"tr_id": "PINGPONG", "rsp_cd": "0", "rsp_msg": "Success"}}'

        ws_client.websocket = mock_websocket
        ws_client.connected = True

        result = await ws_client.login()

        assert result is True
        mock_websocket.send.assert_called()  # PINGPONG 메시지 전송 확인

    @pytest.mark.asyncio
    async def test_login_failure(self, ws_client):
        """WebSocket 로그인 실패 테스트"""
        mock_websocket = AsyncMock()
        mock_websocket.recv.return_value = '{"header": {"tr_id": "PINGPONG", "rsp_cd": "1", "rsp_msg": "Auth failed"}}'

        ws_client.websocket = mock_websocket
        ws_client.connected = True

        with pytest.raises(AuthenticationError):
            await ws_client.login()

    @pytest.mark.asyncio
    async def test_is_connected_true(self, ws_client):
        """연결 상태 확인 테스트 (연결됨)"""
        mock_websocket = AsyncMock()
        mock_websocket.recv.return_value = '{"header": {"tr_id": "PINGPONG"}}'

        ws_client.websocket = mock_websocket
        ws_client.connected = True

        result = await ws_client.is_connected()

        assert result is True

    @pytest.mark.asyncio
    async def test_is_connected_false(self, ws_client):
        """연결 상태 확인 테스트 (연결 안됨)"""
        ws_client.connected = False

        result = await ws_client.is_connected()

        assert result is False

    @pytest.mark.asyncio
    async def test_is_connected_ping_timeout(self, ws_client):
        """연결 상태 확인 테스트 (Ping 타임아웃)"""
        mock_websocket = AsyncMock()

        async def slow_recv():
            await asyncio.sleep(10)
            return "{}"

        mock_websocket.recv = slow_recv
        ws_client.websocket = mock_websocket
        ws_client.connected = True

        result = await ws_client.is_connected()

        assert result is False
        assert ws_client.connected is False  # 타임아웃 시 연결 상태 False로 변경

    @pytest.mark.asyncio
    async def test_context_manager_success(self):
        """컨텍스트 매니저 정상 사용 테스트"""
        uri = "wss://test.kiwoom.com/websocket"
        token = "test_token_123"

        mock_websocket = AsyncMock()
        mock_websocket.recv.return_value = '{"header": {"tr_id": "PINGPONG", "rsp_cd": "0"}}'

        with patch('trading.websocket_client.websockets.connect', return_value=mock_websocket):
            async with KiwoomWebSocketClient(uri, token) as ws_client:
                assert ws_client.connected is True
                assert ws_client.websocket is not None

            # 컨텍스트 종료 후 연결 해제 확인
            mock_websocket.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_with_exception(self):
        """컨텍스트 매니저 예외 발생 시 테스트"""
        uri = "wss://test.kiwoom.com/websocket"
        token = "test_token_123"

        mock_websocket = AsyncMock()
        mock_websocket.recv.return_value = '{"header": {"tr_id": "PINGPONG", "rsp_cd": "0"}}'

        with patch('trading.websocket_client.websockets.connect', return_value=mock_websocket):
            try:
                async with KiwoomWebSocketClient(uri, token) as ws_client:
                    raise ValueError("Test exception")
            except ValueError:
                pass

            # 예외 발생해도 연결 해제되어야 함
            mock_websocket.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_connect_calls(self, ws_client):
        """중복 connect 호출 테스트"""
        mock_websocket = AsyncMock()

        with patch('trading.websocket_client.websockets.connect', return_value=mock_websocket):
            await ws_client.connect()
            first_websocket = ws_client.websocket

            # 두 번째 connect 호출
            await ws_client.connect()

            # 기존 연결이 닫히고 새 연결이 생성되어야 함
            assert ws_client.websocket == mock_websocket
            assert ws_client.connected is True

    @pytest.mark.asyncio
    async def test_send_message_with_empty_data(self, ws_client):
        """빈 데이터로 메시지 전송 테스트"""
        mock_websocket = AsyncMock()
        ws_client.websocket = mock_websocket
        ws_client.connected = True

        await ws_client.send_message("test_trnm", None)

        mock_websocket.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_receive_message_with_custom_timeout(self, ws_client):
        """커스텀 타임아웃으로 메시지 수신 테스트"""
        mock_websocket = AsyncMock()
        mock_websocket.recv.return_value = '{"data": "test"}'

        ws_client.websocket = mock_websocket
        ws_client.connected = True

        result = await ws_client.receive_message(timeout=30.0)

        assert result is not None
        assert "data" in result


# 통합 테스트
class TestKiwoomWebSocketClientIntegration:
    """KiwoomWebSocketClient 통합 테스트"""

    @pytest.mark.asyncio
    async def test_full_workflow(self):
        """전체 워크플로우 테스트 (connect → login → send → receive → disconnect)"""
        uri = "wss://test.kiwoom.com/websocket"
        token = "test_token_123"

        mock_websocket = AsyncMock()
        # 로그인 응답
        mock_websocket.recv.side_effect = [
            '{"header": {"tr_id": "PINGPONG", "rsp_cd": "0", "rsp_msg": "Success"}}',
            '{"header": {"tr_id": "TEST"}, "body": {"result": "success"}}'
        ]

        with patch('trading.websocket_client.websockets.connect', return_value=mock_websocket):
            ws_client = KiwoomWebSocketClient(uri, token)

            # 1. Connect
            await ws_client.connect()
            assert ws_client.connected is True

            # 2. Login
            login_result = await ws_client.login()
            assert login_result is True

            # 3. Send message
            await ws_client.send_message("TEST", {"query": "test"})
            mock_websocket.send.assert_called()

            # 4. Receive message
            response = await ws_client.receive_message()
            assert response is not None
            assert response["body"]["result"] == "success"

            # 5. Disconnect
            await ws_client.disconnect()
            assert ws_client.connected is False
            mock_websocket.close.assert_called_once()
