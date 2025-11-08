#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Async Data Loader 단위 테스트
"""

import pytest
import asyncio
import aiohttp
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../..')))

from data.async_data_loader import (
    AsyncDataLoader,
    DataSource,
    DataFetchResult,
    BatchFetchStats,
    run_async_fetch
)


@pytest.fixture
def async_loader():
    """AsyncDataLoader 픽스처"""
    return AsyncDataLoader(
        max_concurrent=5,
        timeout_seconds=5,
        retry_attempts=2,
        retry_delay=0.1
    )


@pytest.fixture
def mock_session():
    """Mock aiohttp ClientSession"""
    session = MagicMock()
    return session


class TestDataFetchResult:
    """DataFetchResult 데이터클래스 테스트"""

    def test_successful_result(self):
        """성공 결과 생성"""
        result = DataFetchResult(
            symbol="005930",
            source=DataSource.MARKET_DATA,
            success=True,
            data={"price": 70000},
            fetch_time_ms=150.5
        )

        assert result.symbol == "005930"
        assert result.source == DataSource.MARKET_DATA
        assert result.success is True
        assert result.data == {"price": 70000}
        assert result.fetch_time_ms == 150.5
        assert result.error is None

    def test_failed_result(self):
        """실패 결과 생성"""
        result = DataFetchResult(
            symbol="005930",
            source=DataSource.NEWS,
            success=False,
            error="Timeout",
            fetch_time_ms=5000.0
        )

        assert result.symbol == "005930"
        assert result.success is False
        assert result.error == "Timeout"
        assert result.data is None


class TestBatchFetchStats:
    """BatchFetchStats 데이터클래스 테스트"""

    def test_stats_creation(self):
        """통계 생성"""
        stats = BatchFetchStats(
            total_requests=10,
            successful=8,
            failed=2,
            total_time_ms=1000.0,
            avg_time_per_request_ms=100.0,
            requests_per_second=10.0
        )

        assert stats.total_requests == 10
        assert stats.successful == 8
        assert stats.failed == 2
        assert stats.total_time_ms == 1000.0
        assert stats.avg_time_per_request_ms == 100.0
        assert stats.requests_per_second == 10.0


class TestAsyncDataLoader:
    """AsyncDataLoader 클래스 테스트"""

    def test_initialization(self, async_loader):
        """초기화 테스트"""
        assert async_loader.max_concurrent == 5
        assert async_loader.timeout_seconds == 5
        assert async_loader.retry_attempts == 2
        assert async_loader.retry_delay == 0.1
        assert len(async_loader.fetch_history) == 0

    @pytest.mark.asyncio
    async def test_fetch_single_success(self, async_loader):
        """단일 데이터 수집 성공 테스트"""
        # Mock response
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"price": 70000})

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await async_loader.fetch_single(
            session=mock_session,
            symbol="005930",
            source=DataSource.MARKET_DATA,
            url="https://api.example.com/market/005930",
            params={"format": "json"},
            headers={}
        )

        assert result.success is True
        assert result.symbol == "005930"
        assert result.source == DataSource.MARKET_DATA
        assert result.data == {"price": 70000}
        assert result.error is None
        assert result.fetch_time_ms > 0

    @pytest.mark.asyncio
    async def test_fetch_single_http_error(self, async_loader):
        """HTTP 에러 처리 테스트"""
        # Mock response with error
        mock_response = AsyncMock()
        mock_response.status = 404
        mock_response.text = AsyncMock(return_value="Not Found")

        mock_session = AsyncMock()
        mock_session.get = AsyncMock(return_value=mock_response)
        mock_session.get.return_value.__aenter__ = AsyncMock(return_value=mock_response)
        mock_session.get.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await async_loader.fetch_single(
            session=mock_session,
            symbol="005930",
            source=DataSource.MARKET_DATA,
            url="https://api.example.com/market/005930",
            params={},
            headers={}
        )

        assert result.success is False
        assert result.error is not None
        assert "404" in result.error

    @pytest.mark.asyncio
    async def test_fetch_single_timeout(self, async_loader):
        """타임아웃 처리 테스트"""
        mock_session = AsyncMock()

        # Simulate timeout
        async def raise_timeout(*args, **kwargs):
            raise asyncio.TimeoutError()

        mock_session.get = AsyncMock(side_effect=raise_timeout)

        result = await async_loader.fetch_single(
            session=mock_session,
            symbol="005930",
            source=DataSource.MARKET_DATA,
            url="https://api.example.com/market/005930",
            params={},
            headers={}
        )

        assert result.success is False
        assert result.error == "Timeout"

    @pytest.mark.asyncio
    async def test_fetch_single_exception(self, async_loader):
        """예외 처리 테스트"""
        mock_session = AsyncMock()

        async def raise_exception(*args, **kwargs):
            raise Exception("Network error")

        mock_session.get = AsyncMock(side_effect=raise_exception)

        result = await async_loader.fetch_single(
            session=mock_session,
            symbol="005930",
            source=DataSource.MARKET_DATA,
            url="https://api.example.com/market/005930",
            params={},
            headers={}
        )

        assert result.success is False
        assert "Network error" in result.error

    @pytest.mark.asyncio
    async def test_fetch_batch_success(self, async_loader):
        """배치 수집 성공 테스트"""
        requests = [
            ("005930", DataSource.MARKET_DATA, "https://api.example.com/market/005930", {}, {}),
            ("000660", DataSource.MARKET_DATA, "https://api.example.com/market/000660", {}, {}),
            ("035420", DataSource.NEWS, "https://api.example.com/news/035420", {}, {})
        ]

        with patch.object(async_loader, 'fetch_single') as mock_fetch:
            # Mock successful results
            mock_fetch.side_effect = [
                DataFetchResult("005930", DataSource.MARKET_DATA, True, {"price": 70000}, None, 100.0),
                DataFetchResult("000660", DataSource.MARKET_DATA, True, {"price": 50000}, None, 110.0),
                DataFetchResult("035420", DataSource.NEWS, True, {"count": 5}, None, 120.0)
            ]

            results, stats = await async_loader.fetch_batch(requests)

            assert len(results) == 3
            assert stats.total_requests == 3
            assert stats.successful == 3
            assert stats.failed == 0

    @pytest.mark.asyncio
    async def test_fetch_batch_mixed_results(self, async_loader):
        """배치 수집 혼합 결과 테스트 (성공 + 실패)"""
        requests = [
            ("005930", DataSource.MARKET_DATA, "https://api.example.com/market/005930", {}, {}),
            ("000660", DataSource.MARKET_DATA, "https://api.example.com/market/000660", {}, {}),
            ("INVALID", DataSource.NEWS, "https://api.example.com/news/INVALID", {}, {})
        ]

        with patch.object(async_loader, 'fetch_single') as mock_fetch:
            mock_fetch.side_effect = [
                DataFetchResult("005930", DataSource.MARKET_DATA, True, {"price": 70000}, None, 100.0),
                DataFetchResult("000660", DataSource.MARKET_DATA, True, {"price": 50000}, None, 110.0),
                DataFetchResult("INVALID", DataSource.NEWS, False, None, "Not Found", 50.0)
            ]

            results, stats = await async_loader.fetch_batch(requests)

            assert len(results) == 3
            assert stats.total_requests == 3
            assert stats.successful == 2
            assert stats.failed == 1

    @pytest.mark.asyncio
    async def test_fetch_multi_source_for_symbols(self, async_loader):
        """멀티 소스 수집 테스트"""
        symbols = ["005930", "000660"]
        sources = [DataSource.MARKET_DATA, DataSource.NEWS]

        def url_builder(symbol, source):
            return f"https://api.example.com/{source.value}/{symbol}", {}, {}

        with patch.object(async_loader, 'fetch_batch') as mock_batch:
            # Mock batch results
            mock_results = [
                DataFetchResult("005930", DataSource.MARKET_DATA, True, {"price": 70000}, None, 100.0),
                DataFetchResult("005930", DataSource.NEWS, True, {"count": 3}, None, 110.0),
                DataFetchResult("000660", DataSource.MARKET_DATA, True, {"price": 50000}, None, 120.0),
                DataFetchResult("000660", DataSource.NEWS, True, {"count": 2}, None, 130.0)
            ]
            mock_stats = BatchFetchStats(4, 4, 0, 460.0, 115.0, 8.7)
            mock_batch.return_value = (mock_results, mock_stats)

            grouped = await async_loader.fetch_multi_source_for_symbols(
                symbols, sources, url_builder
            )

            assert len(grouped) == 2
            assert "005930" in grouped
            assert "000660" in grouped
            assert DataSource.MARKET_DATA in grouped["005930"]
            assert DataSource.NEWS in grouped["005930"]
            assert grouped["005930"][DataSource.MARKET_DATA].success is True

    def test_get_fetch_statistics_empty(self, async_loader):
        """빈 기록 통계 테스트"""
        stats = async_loader.get_fetch_statistics()
        assert stats == {"status": "no_data"}

    def test_get_fetch_statistics_with_data(self, async_loader):
        """데이터가 있는 통계 테스트"""
        # Add mock history
        async_loader.fetch_history = [
            DataFetchResult("005930", DataSource.MARKET_DATA, True, {}, None, 100.0),
            DataFetchResult("000660", DataSource.MARKET_DATA, True, {}, None, 110.0),
            DataFetchResult("035420", DataSource.NEWS, False, None, "Timeout", 5000.0),
            DataFetchResult("051910", DataSource.NEWS, True, {}, None, 120.0)
        ]

        stats = async_loader.get_fetch_statistics()

        assert stats["total_requests"] == 4
        assert stats["successful"] == 3
        assert stats["failed"] == 1
        assert stats["success_rate"] == 0.75
        assert "avg_fetch_time_ms" in stats
        assert "by_source" in stats
        assert DataSource.MARKET_DATA.value in stats["by_source"]
        assert stats["by_source"][DataSource.MARKET_DATA.value]["successful"] == 2

    def test_get_fetch_statistics_last_n(self, async_loader):
        """최근 N개 통계 테스트"""
        async_loader.fetch_history = [
            DataFetchResult("005930", DataSource.MARKET_DATA, True, {}, None, 100.0),
            DataFetchResult("000660", DataSource.MARKET_DATA, True, {}, None, 110.0),
            DataFetchResult("035420", DataSource.NEWS, True, {}, None, 120.0),
            DataFetchResult("051910", DataSource.NEWS, True, {}, None, 130.0)
        ]

        stats = async_loader.get_fetch_statistics(last_n=2)

        assert stats["total_requests"] == 2
        assert stats["successful"] == 2

    def test_clear_history(self, async_loader):
        """기록 초기화 테스트"""
        async_loader.fetch_history = [
            DataFetchResult("005930", DataSource.MARKET_DATA, True, {}, None, 100.0)
        ]

        async_loader.clear_history()
        assert len(async_loader.fetch_history) == 0


class TestRunAsyncFetch:
    """run_async_fetch 편의 함수 테스트"""

    def test_run_async_fetch(self):
        """동기 컨텍스트에서 비동기 실행 테스트"""
        loader = AsyncDataLoader(max_concurrent=2)
        requests = [
            ("005930", DataSource.MARKET_DATA, "https://api.example.com/market/005930", {}, {})
        ]

        with patch.object(loader, 'fetch_single') as mock_fetch:
            mock_fetch.return_value = DataFetchResult(
                "005930", DataSource.MARKET_DATA, True, {"price": 70000}, None, 100.0
            )

            results, stats = run_async_fetch(loader, requests)

            assert len(results) == 1
            assert stats.total_requests == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
