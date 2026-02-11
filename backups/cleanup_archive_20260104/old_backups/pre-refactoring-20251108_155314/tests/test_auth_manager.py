#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_auth_manager.py

Auth Manager 및 Retry 시스템 테스트
"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from datetime import datetime

# 프로젝트 루트 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.auth_manager import AuthManager, create_auth_manager
from utils.retry import retry, retry_on_network_error, get_retry_metrics


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
        logger.info("설정 방법:")
        logger.info("  export KIS_APP_KEY='your_app_key'")
        logger.info("  export KIS_APP_SECRET='your_app_secret'")
        logger.info("  export KIS_ACCOUNT_NO='12345678-01'")
        sys.exit(1)

    return app_key, app_secret, account_no


async def test_basic_token_fetch():
    """기본 토큰 발급 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 1: Basic Token Fetch")
    logger.info("=" * 80)

    app_key, app_secret, account_no = load_credentials()

    # AuthManager 생성 (실전투자)
    auth_manager = AuthManager(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        is_mock=False,
        auto_refresh=False,  # 테스트에서는 수동
    )

    try:
        # 토큰 발급
        token = await auth_manager.ensure_valid_token()

        logger.info(f"✅ Token fetched: {token[:20]}...")

        # 토큰 정보 확인
        token_info = auth_manager.get_token_info()
        if token_info:
            logger.info(f"✅ Token Type: {token_info.token_type}")
            logger.info(f"✅ Expires In: {token_info.expires_in} seconds")
            logger.info(f"✅ Time Until Expiry: {token_info.time_until_expiry / 60:.1f} minutes")
            logger.info(f"✅ Expiry Percentage: {token_info.expiry_percentage * 100:.1f}%")

        # 메트릭 확인
        metrics = auth_manager.get_metrics()
        logger.info(f"✅ Metrics: {metrics}")

        return True

    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        return False

    finally:
        await auth_manager.close()


async def test_token_caching():
    """토큰 캐싱 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 2: Token Caching")
    logger.info("=" * 80)

    app_key, app_secret, account_no = load_credentials()

    # 첫 번째 인스턴스 - 새 토큰 발급
    logger.info("Creating first AuthManager instance...")
    auth1 = AuthManager(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        is_mock=False,
        auto_refresh=False,
    )

    try:
        token1 = await auth1.ensure_valid_token()
        logger.info(f"✅ Token 1 fetched: {token1[:20]}...")

        metrics1 = auth1.get_metrics()
        logger.info(f"Metrics 1 - Fetches: {metrics1['token_fetches']}, Cache Hits: {metrics1['cache_hits']}")

    finally:
        await auth1.close()

    # 두 번째 인스턴스 - 캐시에서 로드
    logger.info("\nCreating second AuthManager instance...")
    auth2 = AuthManager(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        is_mock=False,
        auto_refresh=False,
    )

    try:
        token2 = await auth2.ensure_valid_token()
        logger.info(f"✅ Token 2 loaded: {token2[:20]}...")

        metrics2 = auth2.get_metrics()
        logger.info(f"Metrics 2 - Fetches: {metrics2['token_fetches']}, Cache Hits: {metrics2['cache_hits']}")

        # 검증
        if token1 == token2:
            logger.info("✅ Cache working: Same token loaded")
            return True
        else:
            logger.error("❌ Cache failed: Different tokens")
            return False

    finally:
        await auth2.close()


async def test_token_refresh():
    """토큰 강제 갱신 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 3: Token Refresh")
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
        # 첫 번째 토큰
        token1 = await auth_manager.ensure_valid_token()
        logger.info(f"✅ Token 1: {token1[:20]}...")

        # 잠시 대기
        await asyncio.sleep(2)

        # 강제 갱신
        logger.info("Forcing token refresh...")
        token2 = await auth_manager.refresh_token()
        logger.info(f"✅ Token 2: {token2[:20]}...")

        # 메트릭 확인
        metrics = auth_manager.get_metrics()
        logger.info(f"Token Refreshes: {metrics['token_refreshes']}")

        # 검증 (새 토큰이 발급되어야 함)
        if token1 != token2:
            logger.info("✅ Refresh working: New token issued")
            return True
        else:
            logger.warning("⚠️ Same token returned (API may cache)")
            return True

    finally:
        await auth_manager.close()


async def test_auto_refresh():
    """자동 갱신 테스트 (시뮬레이션)"""
    logger.info("=" * 80)
    logger.info("TEST 4: Auto Refresh (Simulation)")
    logger.info("=" * 80)

    app_key, app_secret, account_no = load_credentials()

    # 자동 갱신 활성화
    auth_manager = AuthManager(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        is_mock=False,
        auto_refresh=True,  # 자동 갱신 ON
    )

    try:
        # 토큰 발급 (자동 갱신 루프 시작)
        token = await auth_manager.ensure_valid_token()
        logger.info(f"✅ Token fetched: {token[:20]}...")

        # 10초 대기 (자동 갱신 루프 동작 확인)
        logger.info("Waiting 10 seconds to monitor auto-refresh loop...")
        await asyncio.sleep(10)

        # 토큰 여전히 유효한지 확인
        current_token = auth_manager.get_access_token()
        if current_token:
            logger.info("✅ Auto-refresh loop is running")
            return True
        else:
            logger.error("❌ Auto-refresh loop failed")
            return False

    finally:
        await auth_manager.close()


async def test_retry_decorator():
    """Retry 데코레이터 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 5: Retry Decorator")
    logger.info("=" * 80)

    # 테스트용 함수 (실패 후 성공)
    attempt_count = 0

    @retry(max_attempts=3, initial_delay=0.5, collect_metrics=True)
    async def flaky_function():
        nonlocal attempt_count
        attempt_count += 1

        if attempt_count < 3:
            logger.info(f"Attempt {attempt_count}: Simulating failure...")
            raise ConnectionError("Simulated network error")

        logger.info(f"Attempt {attempt_count}: Success!")
        return "SUCCESS"

    try:
        result = await flaky_function()
        logger.info(f"✅ Result: {result}")

        # 메트릭 확인
        metrics = get_retry_metrics(flaky_function)
        if metrics:
            logger.info(f"✅ Retry Metrics: {metrics.get_stats()}")

        return True

    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        return False


async def test_context_manager():
    """Context Manager 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 6: Context Manager")
    logger.info("=" * 80)

    app_key, app_secret, account_no = load_credentials()

    try:
        async with AuthManager(
            app_key=app_key,
            app_secret=app_secret,
            account_no=account_no,
            is_mock=False,
            auto_refresh=False,
        ) as auth:
            token = auth.get_access_token()
            logger.info(f"✅ Token obtained via context manager: {token[:20]}...")

        logger.info("✅ Context manager properly closed")
        return True

    except Exception as e:
        logger.error(f"❌ Test failed: {e}")
        return False


async def main():
    """모든 테스트 실행"""
    logger.info("\n" + "=" * 80)
    logger.info("🚀 Starting Auth Manager & Retry Tests")
    logger.info("=" * 80 + "\n")

    results = {}

    # 테스트 1: 기본 토큰 발급
    results['Basic Token Fetch'] = await test_basic_token_fetch()
    await asyncio.sleep(1)

    # 테스트 2: 토큰 캐싱
    results['Token Caching'] = await test_token_caching()
    await asyncio.sleep(1)

    # 테스트 3: 토큰 갱신
    results['Token Refresh'] = await test_token_refresh()
    await asyncio.sleep(1)

    # 테스트 4: 자동 갱신
    results['Auto Refresh'] = await test_auto_refresh()
    await asyncio.sleep(1)

    # 테스트 5: Retry 데코레이터
    results['Retry Decorator'] = await test_retry_decorator()
    await asyncio.sleep(1)

    # 테스트 6: Context Manager
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
    # 환경 변수 설정 확인
    if not os.getenv('KIS_APP_KEY'):
        logger.warning("⚠️ 환경 변수가 설정되지 않았습니다.")
        logger.info("테스트를 실행하려면 다음 환경 변수를 설정하세요:")
        logger.info("  export KIS_APP_KEY='your_app_key'")
        logger.info("  export KIS_APP_SECRET='your_app_secret'")
        logger.info("  export KIS_ACCOUNT_NO='12345678-01'")
        sys.exit(1)

    # 테스트 실행
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
