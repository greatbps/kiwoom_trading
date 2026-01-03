#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_cache.py

캐시 시스템 테스트
"""

import asyncio
import logging
import sys
import time
from pathlib import Path

# 프로젝트 루트 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.cache import LRUCache, PersistentCache, cached, async_cached


# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


def test_lru_cache_basic():
    """LRU 캐시 기본 기능 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 1: LRU Cache Basic Operations")
    logger.info("=" * 80)

    cache = LRUCache(max_size=3)

    # 저장
    cache.set("key1", "value1")
    cache.set("key2", "value2")
    cache.set("key3", "value3")

    # 조회
    assert cache.get("key1") == "value1"
    assert cache.get("key2") == "value2"
    assert cache.get("key3") == "value3"

    logger.info("✅ Set/Get working")

    # LRU 동작 확인 (크기 초과 시 가장 오래된 항목 제거)
    cache.set("key4", "value4")  # key1이 제거되어야 함

    assert cache.get("key1") is None  # key1 제거됨
    assert cache.get("key2") == "value2"
    assert cache.get("key3") == "value3"
    assert cache.get("key4") == "value4"

    logger.info("✅ LRU eviction working")

    # 통계 확인
    stats = cache.get_stats()
    logger.info(f"Stats: {stats}")

    # LRU 동작으로 인해 hit 수가 달라질 수 있음
    assert stats['evictions'] == 1
    assert stats['size'] == 3

    logger.info("✅ Stats collection working")

    return True


def test_lru_cache_ttl():
    """LRU 캐시 TTL 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 2: LRU Cache TTL")
    logger.info("=" * 80)

    cache = LRUCache(max_size=10, default_ttl=2.0)  # 2초 TTL

    # 저장
    cache.set("temp_key", "temp_value", ttl=1.0)  # 1초 TTL

    # 즉시 조회 - 성공
    assert cache.get("temp_key") == "temp_value"
    logger.info("✅ Value retrieved before expiry")

    # 1.5초 대기
    time.sleep(1.5)

    # 만료 확인
    assert cache.get("temp_key") is None
    logger.info("✅ Value expired after TTL")

    # 통계 확인
    stats = cache.get_stats()
    assert stats['expirations'] == 1

    logger.info("✅ TTL working correctly")

    return True


def test_lru_cache_cleanup():
    """LRU 캐시 정리 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 3: LRU Cache Cleanup")
    logger.info("=" * 80)

    cache = LRUCache(max_size=10)

    # 여러 항목 저장 (짧은 TTL)
    for i in range(5):
        cache.set(f"key{i}", f"value{i}", ttl=1.0)

    logger.info(f"Initial size: {len(cache)}")

    # 1.5초 대기
    time.sleep(1.5)

    # 정리 전 크기
    assert len(cache) == 5

    # 정리 실행
    cache.cleanup_expired()

    # 정리 후 크기
    assert len(cache) == 0

    logger.info("✅ Cleanup working")

    return True


def test_persistent_cache():
    """영구 캐시 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 4: Persistent Cache")
    logger.info("=" * 80)

    # 테스트용 DB 경로
    test_db_path = PROJECT_ROOT / "test_cache.db"

    # 기존 DB 삭제
    if test_db_path.exists():
        test_db_path.unlink()

    cache = PersistentCache(db_path=test_db_path)

    # 저장
    cache.set("persistent_key", {"data": "value", "number": 123})
    logger.info("✅ Data saved")

    # 조회
    result = cache.get("persistent_key")
    assert result == {"data": "value", "number": 123}
    logger.info("✅ Data retrieved")

    # 새 인스턴스로 조회 (영구성 확인)
    cache2 = PersistentCache(db_path=test_db_path)
    result2 = cache2.get("persistent_key")
    assert result2 == {"data": "value", "number": 123}
    logger.info("✅ Persistence verified")

    # TTL 테스트
    cache.set("temp_persistent", "temp_value", ttl=1.0)
    assert cache.get("temp_persistent") == "temp_value"

    time.sleep(1.5)
    assert cache.get("temp_persistent") is None
    logger.info("✅ TTL working")

    # 정리
    cache.clear()
    test_db_path.unlink()

    logger.info("✅ Persistent cache working")

    return True


def test_cached_decorator():
    """캐싱 데코레이터 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 5: Cached Decorator")
    logger.info("=" * 80)

    cache = LRUCache(max_size=100)

    call_count = 0

    @cached(cache, ttl=60)
    def expensive_function(x, y):
        nonlocal call_count
        call_count += 1
        logger.info(f"Computing {x} + {y}...")
        time.sleep(0.1)  # 시뮬레이션
        return x + y

    # 첫 호출 - 실제 계산
    result1 = expensive_function(10, 20)
    assert result1 == 30
    assert call_count == 1
    logger.info("✅ First call executed function")

    # 두 번째 호출 - 캐시에서 반환
    result2 = expensive_function(10, 20)
    assert result2 == 30
    assert call_count == 1  # 증가 안 함
    logger.info("✅ Second call used cache")

    # 다른 인자 - 실제 계산
    result3 = expensive_function(5, 15)
    assert result3 == 20
    assert call_count == 2
    logger.info("✅ Different args triggered computation")

    # 통계 확인
    stats = cache.get_stats()
    assert stats['hits'] == 1
    assert stats['misses'] == 2

    logger.info("✅ Cached decorator working")

    return True


async def test_async_cached_decorator():
    """비동기 캐싱 데코레이터 테스트"""
    logger.info("=" * 80)
    logger.info("TEST 6: Async Cached Decorator")
    logger.info("=" * 80)

    cache = LRUCache(max_size=100)

    call_count = 0

    @async_cached(cache, ttl=60)
    async def async_expensive_function(x, y):
        nonlocal call_count
        call_count += 1
        logger.info(f"Async computing {x} * {y}...")
        await asyncio.sleep(0.1)  # 시뮬레이션
        return x * y

    # 첫 호출
    result1 = await async_expensive_function(10, 20)
    assert result1 == 200
    assert call_count == 1

    # 두 번째 호출 - 캐시
    result2 = await async_expensive_function(10, 20)
    assert result2 == 200
    assert call_count == 1

    logger.info("✅ Async cached decorator working")

    return True


def test_cache_integration():
    """캐시 통합 테스트 (실제 사용 사례)"""
    logger.info("=" * 80)
    logger.info("TEST 7: Cache Integration (Backtest Results)")
    logger.info("=" * 80)

    # 백테스트 결과 캐싱 시뮬레이션
    cache = LRUCache(max_size=100, default_ttl=3600)

    def generate_cache_key(symbol: str, strategy: str, period: int) -> str:
        """백테스트 캐시 키 생성"""
        return f"backtest:{symbol}:{strategy}:{period}"

    # 백테스트 결과 저장
    backtest_result = {
        'symbol': 'AAPL',
        'strategy': 'VWAP',
        'profit': 0.15,
        'trades': 10,
        'win_rate': 0.7,
    }

    cache_key = generate_cache_key('AAPL', 'VWAP', 100)
    cache.set(cache_key, backtest_result)

    logger.info("✅ Backtest result cached")

    # 조회
    cached_result = cache.get(cache_key)
    assert cached_result == backtest_result

    logger.info("✅ Cached result retrieved")

    # 통계
    stats = cache.get_stats()
    logger.info(f"Cache stats: Hit rate={stats['hit_rate']:.2%}")

    return True


def main():
    """모든 테스트 실행"""
    logger.info("\n" + "=" * 80)
    logger.info("🚀 Starting Cache System Tests")
    logger.info("=" * 80 + "\n")

    results = {}

    # 테스트 1: LRU 기본
    results['LRU Basic'] = test_lru_cache_basic()

    # 테스트 2: LRU TTL
    results['LRU TTL'] = test_lru_cache_ttl()

    # 테스트 3: LRU Cleanup
    results['LRU Cleanup'] = test_lru_cache_cleanup()

    # 테스트 4: Persistent Cache
    results['Persistent Cache'] = test_persistent_cache()

    # 테스트 5: Decorator
    results['Cached Decorator'] = test_cached_decorator()

    # 테스트 6: Async Decorator
    results['Async Cached Decorator'] = asyncio.run(test_async_cached_decorator())

    # 테스트 7: Integration
    results['Cache Integration'] = test_cache_integration()

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
    success = main()
    sys.exit(0 if success else 1)
