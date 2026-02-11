#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils/cache.py

High-Performance Caching System
- 인메모리 캐시 (LRU, TTL 지원)
- SQLite 기반 영구 캐시
- 백테스트 결과 캐싱
- 종목 데이터 캐싱
- 자동 만료 및 정리
"""

import asyncio
import json
import logging
import pickle
import sqlite3
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock
from typing import Any, Optional, Dict, Callable, TypeVar, Generic
from dataclasses import dataclass
from functools import wraps
import hashlib

T = TypeVar('T')


@dataclass
class CacheEntry(Generic[T]):
    """캐시 엔트리"""
    key: str
    value: T
    created_at: float
    ttl: Optional[float]  # Time to live (초)
    access_count: int = 0
    last_accessed: float = 0

    @property
    def is_expired(self) -> bool:
        """만료 여부 확인"""
        if self.ttl is None:
            return False
        return (time.time() - self.created_at) > self.ttl

    @property
    def age(self) -> float:
        """캐시 나이 (초)"""
        return time.time() - self.created_at


class LRUCache(Generic[T]):
    """
    LRU (Least Recently Used) 캐시

    주요 기능:
    - 크기 제한
    - TTL 지원
    - 스레드 세이프
    - 메트릭 수집

    Example:
        cache = LRUCache(max_size=1000, default_ttl=3600)

        # 저장
        cache.set("key", "value", ttl=60)

        # 조회
        value = cache.get("key")

        # 삭제
        cache.delete("key")

        # 정리
        cache.clear()
    """

    def __init__(
        self,
        max_size: int = 1000,
        default_ttl: Optional[float] = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Args:
            max_size: 최대 캐시 크기
            default_ttl: 기본 TTL (초, None이면 무제한)
            logger: 로거
        """
        self.max_size = max_size
        self.default_ttl = default_ttl
        self.logger = logger or logging.getLogger(self.__class__.__name__)

        # 캐시 스토리지 (OrderedDict for LRU)
        self._cache: OrderedDict[str, CacheEntry[T]] = OrderedDict()
        self._lock = RLock()

        # 메트릭
        self.metrics = {
            'hits': 0,
            'misses': 0,
            'evictions': 0,
            'expirations': 0,
            'sets': 0,
            'deletes': 0,
        }

    def get(self, key: str, default: Optional[T] = None) -> Optional[T]:
        """
        값 조회

        Args:
            key: 캐시 키
            default: 기본값

        Returns:
            캐시된 값 (없으면 default)
        """
        with self._lock:
            entry = self._cache.get(key)

            if entry is None:
                self.metrics['misses'] += 1
                return default

            # 만료 확인
            if entry.is_expired:
                self.logger.debug(f"Cache expired: {key}")
                self._delete_entry(key)
                self.metrics['misses'] += 1
                self.metrics['expirations'] += 1
                return default

            # LRU 업데이트 (맨 뒤로 이동)
            self._cache.move_to_end(key)

            # 메트릭 업데이트
            entry.access_count += 1
            entry.last_accessed = time.time()
            self.metrics['hits'] += 1

            return entry.value

    def set(
        self,
        key: str,
        value: T,
        ttl: Optional[float] = None,
    ):
        """
        값 저장

        Args:
            key: 캐시 키
            value: 값
            ttl: TTL (초, None이면 default_ttl 사용)
        """
        with self._lock:
            # TTL 설정
            if ttl is None:
                ttl = self.default_ttl

            # 엔트리 생성
            entry = CacheEntry(
                key=key,
                value=value,
                created_at=time.time(),
                ttl=ttl,
            )

            # 기존 엔트리 업데이트 또는 추가
            if key in self._cache:
                self._cache[key] = entry
                self._cache.move_to_end(key)
            else:
                # 크기 제한 확인
                if len(self._cache) >= self.max_size:
                    self._evict_lru()

                self._cache[key] = entry

            self.metrics['sets'] += 1

    def delete(self, key: str) -> bool:
        """
        값 삭제

        Args:
            key: 캐시 키

        Returns:
            삭제 성공 여부
        """
        with self._lock:
            return self._delete_entry(key)

    def _delete_entry(self, key: str) -> bool:
        """내부 삭제 메서드"""
        if key in self._cache:
            del self._cache[key]
            self.metrics['deletes'] += 1
            return True
        return False

    def _evict_lru(self):
        """LRU 항목 제거"""
        if self._cache:
            # 가장 오래 사용되지 않은 항목 제거
            oldest_key = next(iter(self._cache))
            self._delete_entry(oldest_key)
            self.metrics['evictions'] += 1
            self.logger.debug(f"Evicted LRU entry: {oldest_key}")

    def clear(self):
        """모든 캐시 삭제"""
        with self._lock:
            self._cache.clear()
            self.logger.info("Cache cleared")

    def cleanup_expired(self):
        """만료된 항목 정리"""
        with self._lock:
            expired_keys = [
                key for key, entry in self._cache.items()
                if entry.is_expired
            ]

            for key in expired_keys:
                self._delete_entry(key)
                self.metrics['expirations'] += 1

            if expired_keys:
                self.logger.info(f"Cleaned up {len(expired_keys)} expired entries")

    def get_stats(self) -> dict:
        """통계 반환"""
        with self._lock:
            total_requests = self.metrics['hits'] + self.metrics['misses']
            hit_rate = (
                self.metrics['hits'] / total_requests
                if total_requests > 0 else 0
            )

            return {
                **self.metrics,
                'size': len(self._cache),
                'max_size': self.max_size,
                'hit_rate': hit_rate,
                'total_requests': total_requests,
            }

    def __len__(self) -> int:
        """캐시 크기"""
        return len(self._cache)

    def __contains__(self, key: str) -> bool:
        """키 존재 여부"""
        return key in self._cache and not self._cache[key].is_expired


class PersistentCache:
    """
    SQLite 기반 영구 캐시

    주요 기능:
    - 디스크 기반 영구 저장
    - TTL 지원
    - Pickle 기반 직렬화
    - 자동 정리

    Example:
        cache = PersistentCache(db_path="cache.db")

        # 저장
        cache.set("backtest:result", {"profit": 0.15}, ttl=86400)

        # 조회
        result = cache.get("backtest:result")

        # 정리
        cache.cleanup_expired()
    """

    def __init__(
        self,
        db_path: Path = None,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Args:
            db_path: 데이터베이스 파일 경로
            logger: 로거
        """
        if db_path is None:
            db_path = Path(__file__).parent.parent / "cache.db"

        self.db_path = db_path
        self.logger = logger or logging.getLogger(self.__class__.__name__)

        # DB 초기화
        self._init_db()

    def _init_db(self):
        """데이터베이스 초기화"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key TEXT PRIMARY KEY,
                    value BLOB NOT NULL,
                    created_at REAL NOT NULL,
                    ttl REAL,
                    access_count INTEGER DEFAULT 0,
                    last_accessed REAL
                )
            """)

            # 인덱스 생성
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_created_at
                ON cache(created_at)
            """)

            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ttl
                ON cache(ttl)
            """)

        self.logger.info(f"Cache database initialized: {self.db_path}")

    def get(self, key: str, default: Optional[Any] = None) -> Optional[Any]:
        """값 조회"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    SELECT value, created_at, ttl, access_count, last_accessed
                    FROM cache
                    WHERE key = ?
                    """,
                    (key,)
                )

                row = cursor.fetchone()

                if row is None:
                    return default

                value_blob, created_at, ttl, access_count, last_accessed = row

                # 만료 확인
                if ttl is not None:
                    age = time.time() - created_at
                    if age > ttl:
                        self.delete(key)
                        return default

                # 역직렬화
                value = pickle.loads(value_blob)

                # 액세스 카운트 업데이트
                conn.execute(
                    """
                    UPDATE cache
                    SET access_count = access_count + 1,
                        last_accessed = ?
                    WHERE key = ?
                    """,
                    (time.time(), key)
                )

                return value

        except Exception as e:
            self.logger.error(f"Cache get error: {e}")
            return default

    def set(
        self,
        key: str,
        value: Any,
        ttl: Optional[float] = None,
    ):
        """값 저장"""
        try:
            # 직렬화
            value_blob = pickle.dumps(value)

            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO cache
                    (key, value, created_at, ttl, access_count, last_accessed)
                    VALUES (?, ?, ?, ?, 0, ?)
                    """,
                    (key, value_blob, time.time(), ttl, time.time())
                )

        except Exception as e:
            self.logger.error(f"Cache set error: {e}")

    def delete(self, key: str):
        """값 삭제"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM cache WHERE key = ?", (key,))

        except Exception as e:
            self.logger.error(f"Cache delete error: {e}")

    def clear(self):
        """모든 캐시 삭제"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("DELETE FROM cache")

            self.logger.info("Persistent cache cleared")

        except Exception as e:
            self.logger.error(f"Cache clear error: {e}")

    def cleanup_expired(self):
        """만료된 항목 정리"""
        try:
            current_time = time.time()

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    DELETE FROM cache
                    WHERE ttl IS NOT NULL
                    AND (created_at + ttl) < ?
                    """,
                    (current_time,)
                )

                deleted_count = cursor.rowcount

            if deleted_count > 0:
                self.logger.info(f"Cleaned up {deleted_count} expired entries")

        except Exception as e:
            self.logger.error(f"Cache cleanup error: {e}")

    def get_stats(self) -> dict:
        """통계 반환"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT COUNT(*) FROM cache")
                total_count = cursor.fetchone()[0]

                cursor = conn.execute(
                    """
                    SELECT COUNT(*) FROM cache
                    WHERE ttl IS NOT NULL
                    AND (created_at + ttl) < ?
                    """,
                    (time.time(),)
                )
                expired_count = cursor.fetchone()[0]

                return {
                    'total_entries': total_count,
                    'expired_entries': expired_count,
                    'valid_entries': total_count - expired_count,
                }

        except Exception as e:
            self.logger.error(f"Stats error: {e}")
            return {}


# 데코레이터: 함수 결과 캐싱
def cached(
    cache: LRUCache,
    ttl: Optional[float] = None,
    key_func: Optional[Callable] = None,
):
    """
    함수 결과 캐싱 데코레이터

    Example:
        cache = LRUCache(max_size=100)

        @cached(cache, ttl=3600)
        def expensive_function(arg1, arg2):
            # 시간이 오래 걸리는 작업
            return result
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # 캐시 키 생성
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                # 기본 키: 함수명 + 인자 해시
                key_data = f"{func.__name__}:{args}:{kwargs}"
                cache_key = hashlib.md5(key_data.encode()).hexdigest()

            # 캐시 조회
            result = cache.get(cache_key)

            if result is not None:
                return result

            # 함수 실행
            result = func(*args, **kwargs)

            # 캐시 저장
            cache.set(cache_key, result, ttl=ttl)

            return result

        return wrapper

    return decorator


# 비동기 버전
def async_cached(
    cache: LRUCache,
    ttl: Optional[float] = None,
    key_func: Optional[Callable] = None,
):
    """비동기 함수 결과 캐싱 데코레이터"""

    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            # 캐시 키 생성
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                key_data = f"{func.__name__}:{args}:{kwargs}"
                cache_key = hashlib.md5(key_data.encode()).hexdigest()

            # 캐시 조회
            result = cache.get(cache_key)

            if result is not None:
                return result

            # 함수 실행
            result = await func(*args, **kwargs)

            # 캐시 저장
            cache.set(cache_key, result, ttl=ttl)

            return result

        return wrapper

    return decorator
