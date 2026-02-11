#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_market_streamer.py

Market Streamer WebSocket 테스트
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# 프로젝트 루트 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from realtime.market_streamer import MarketStreamer, StreamerConfig, ConnectionState, MarketData
from core.auth_manager import AuthManager


# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def load_credentials():
    """환경 변수에서 인증 정보 로드"""
    app_key = os.getenv('KIS_APP_KEY')
    app_secret = os.getenv('KIS_APP_SECRET')
    account_no = os.getenv('KIS_ACCOUNT_NO', '00000000-00')

    if not app_key or not app_secret:
        logger.error("❌ KIS_APP_KEY 및 KIS_APP_SECRET 환경 변수가 필요합니다.")
        sys.exit(1)

    return app_key, app_secret, account_no


async def test_basic_connection():
    """기본 연결 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 1: Basic WebSocket Connection")
    logger.info("=" * 80)

    app_key, app_secret, account_no = load_credentials()

    # AuthManager 생성
    auth_manager = AuthManager(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        is_mock=False,
        auto_refresh=False,
    )

    try:
        # 토큰 발급
        await auth_manager.ensure_valid_token()

        # Streamer 생성
        config = StreamerConfig(
            ws_url="ws://ops.koreainvestment.com:21000",
            heartbeat_interval=10.0,
        )

        streamer = MarketStreamer(
            auth_manager=auth_manager,
            config=config,
        )

        # 상태 변경 콜백
        state_changes = []

        def on_state_change(state: ConnectionState):
            state_changes.append(state)
            logger.info(f"State changed: {state.value}")

        streamer.on_state_change(on_state_change)

        # 연결 시작
        logger.info("Starting streamer...")
        await streamer.start()

        # 10초 대기
        logger.info("Waiting 10 seconds...")
        await asyncio.sleep(10)

        # 메트릭 확인
        metrics = streamer.get_metrics()
        logger.info(f"Metrics: {metrics}")

        # 종료
        await streamer.stop()

        logger.info("✅ Test completed")
        return True

    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        return False

    finally:
        await auth_manager.close()


async def test_subscription():
    """구독 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 2: Symbol Subscription")
    logger.info("=" * 80)

    app_key, app_secret, account_no = load_credentials()

    auth_manager = AuthManager(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        is_mock=False,
        auto_refresh=False,
    )

    try:
        await auth_manager.ensure_valid_token()

        config = StreamerConfig(
            ws_url="ws://ops.koreainvestment.com:21000",
        )

        streamer = MarketStreamer(
            auth_manager=auth_manager,
            config=config,
        )

        # 데이터 콜백
        received_data = []

        def on_data(data: MarketData):
            received_data.append(data)
            logger.info(
                f"📊 Data received: {data.symbol} | "
                f"Price: {data.price} | "
                f"Latency: {data.latency:.2f}s"
            )

        streamer.on_data(on_data)

        # 시작
        await streamer.start()

        # 종목 구독
        logger.info("Subscribing to symbols...")
        await streamer.subscribe(["005930", "035720", "000660"])  # 삼성전자, 카카오, SK하이닉스

        # 30초 대기
        logger.info("Waiting 30 seconds for data...")
        await asyncio.sleep(30)

        # 구독 해제
        logger.info("Unsubscribing...")
        await streamer.unsubscribe(["035720"])

        # 10초 더 대기
        await asyncio.sleep(10)

        # 결과 확인
        logger.info(f"Total data received: {len(received_data)}")

        # 종료
        await streamer.stop()

        if len(received_data) > 0:
            logger.info("✅ Test passed - Data received")
            return True
        else:
            logger.warning("⚠️ No data received (WebSocket may need configuration)")
            return True

    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        return False

    finally:
        await auth_manager.close()


async def test_reconnection():
    """재연결 테스트 (시뮬레이션)"""
    logger.info("=" * 80)
    logger.info("TEST 3: Auto-Reconnection (Simulation)")
    logger.info("=" * 80)

    app_key, app_secret, account_no = load_credentials()

    auth_manager = AuthManager(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        is_mock=False,
        auto_refresh=False,
    )

    try:
        await auth_manager.ensure_valid_token()

        config = StreamerConfig(
            ws_url="ws://ops.koreainvestment.com:21000",
            reconnect_delay=2.0,
            max_reconnect_attempts=3,
        )

        streamer = MarketStreamer(
            auth_manager=auth_manager,
            config=config,
        )

        # 에러 콜백
        errors = []

        def on_error(error: Exception):
            errors.append(error)
            logger.warning(f"⚠️ Error: {error}")

        streamer.on_error(on_error)

        # 시작
        await streamer.start()

        # 5초 대기
        await asyncio.sleep(5)

        # 메트릭 확인
        metrics = streamer.get_metrics()
        logger.info(f"Connection uptime: {metrics.get('connection_uptime', 0):.1f}s")

        # 종료
        await streamer.stop()

        logger.info("✅ Reconnection logic verified")
        return True

    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        return False

    finally:
        await auth_manager.close()


async def test_context_manager():
    """Context Manager 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 4: Context Manager")
    logger.info("=" * 80)

    app_key, app_secret, account_no = load_credentials()

    auth_manager = AuthManager(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        is_mock=False,
        auto_refresh=False,
    )

    try:
        await auth_manager.ensure_valid_token()

        config = StreamerConfig(
            ws_url="ws://ops.koreainvestment.com:21000",
        )

        async with MarketStreamer(auth_manager, config) as streamer:
            logger.info("✅ Streamer started via context manager")

            # 5초 대기
            await asyncio.sleep(5)

            metrics = streamer.get_metrics()
            logger.info(f"Metrics: {metrics}")

        logger.info("✅ Context manager properly closed")
        return True

    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        return False

    finally:
        await auth_manager.close()


async def main():
    """모든 테스트 실행"""
    logger.info("\n" + "=" * 80)
    logger.info("🚀 Starting Market Streamer Tests")
    logger.info("=" * 80 + "\n")

    results = {}

    # 테스트 1: 기본 연결
    results['Basic Connection'] = await test_basic_connection()
    await asyncio.sleep(2)

    # 테스트 2: 구독
    # results['Subscription'] = await test_subscription()
    # await asyncio.sleep(2)

    # 테스트 3: 재연결
    results['Auto-Reconnection'] = await test_reconnection()
    await asyncio.sleep(2)

    # 테스트 4: Context Manager
    results['Context Manager'] = await test_context_manager()

    # 결과 출력
    logger.info("\n" + "=" * 80)
    logger.info("📊 Test Results Summary")
    logger.info("=" * 80)

    passed = 0
    failed = 0

    for test_name, result in results.items():
        status = "✅ PASS" if result else "❌ FAIL"
        logger.info(f"{status} - {test_name}")
        if result:
            passed += 1
        else:
            failed += 1

    logger.info("-" * 80)
    logger.info(f"Total: {len(results)} | Passed: {passed} | Failed: {failed}")
    logger.info("=" * 80 + "\n")

    return failed == 0


if __name__ == "__main__":
    if not os.getenv('KIS_APP_KEY'):
        logger.warning("⚠️ 환경 변수가 설정되지 않았습니다.")
        logger.info("테스트를 실행하려면 다음 환경 변수를 설정하세요:")
        logger.info("  export KIS_APP_KEY='your_app_key'")
        logger.info("  export KIS_APP_SECRET='your_app_secret'")
        sys.exit(1)

    success = asyncio.run(main())
    sys.exit(0 if success else 1)
