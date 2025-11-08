#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
examples/strategy_example.py

조건검색 엔진 사용 예제
"""

import asyncio
import logging
import os
import sys
from pathlib import Path

# 프로젝트 루트 추가
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from strategy.condition_engine import (
    ConditionEngine,
    StrategyType,
    StrategyConfig,
)
from core.auth_manager import AuthManager


# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)


async def main():
    """메인 실행"""

    # 1. Auth Manager 초기화
    auth_manager = AuthManager(
        app_key=os.getenv('KIS_APP_KEY'),
        app_secret=os.getenv('KIS_APP_SECRET'),
        account_no=os.getenv('KIS_ACCOUNT_NO'),
        is_mock=False,
    )

    await auth_manager.ensure_valid_token()

    # 2. 조건검색 엔진 초기화
    strategies = [
        StrategyConfig(StrategyType.MOMENTUM, "3", initial_weight=1.0),
        StrategyConfig(StrategyType.BREAKOUT, "1", initial_weight=1.0),
        StrategyConfig(StrategyType.EOD, "2", initial_weight=0.8),
        StrategyConfig(StrategyType.SUPERTREND, "6", initial_weight=1.2),
        StrategyConfig(StrategyType.VWAP, "7", initial_weight=1.0),
        StrategyConfig(StrategyType.SCALPING_3M, "0", initial_weight=0.7),
    ]

    engine = ConditionEngine(
        auth_manager=auth_manager,
        strategies=strategies,
        performance_window_days=7,
        cache_ttl=300,  # 5분
    )

    logger.info("=" * 80)
    logger.info("📊 Condition Search Engine Example")
    logger.info("=" * 80)

    # 3. 전체 조건검색 실행 (병렬)
    logger.info("\n[Step 1] Running parallel condition search...")
    results = await engine.search_all(deduplicate=True)

    logger.info(f"\n✅ Total symbols found: {len(results)}")
    logger.info("\nTop 10 symbols by weight:")
    for i, result in enumerate(results[:10], 1):
        logger.info(
            f"  {i}. {result.symbol} | "
            f"Weight: {result.weight:.2f} | "
            f"Strategies: {', '.join(result.metadata.get('strategies', []))}"
        )

    # 4. 성과 시뮬레이션 (예시)
    logger.info("\n[Step 2] Simulating performance updates...")

    # 모멘텀 전략 - 성공
    engine.update_performance(
        StrategyType.MOMENTUM,
        successful=True,
        profit=0.05  # 5% 수익
    )

    # 돌파 전략 - 실패
    engine.update_performance(
        StrategyType.BREAKOUT,
        successful=False,
        profit=-0.02  # 2% 손실
    )

    # 5. 가중치 재조정
    logger.info("\n[Step 3] Rebalancing strategy weights...")
    engine.rebalance_weights()

    # 6. 통계 출력
    logger.info("\n[Step 4] Strategy Statistics:")
    stats = engine.get_stats()

    for strategy_name, strategy_data in stats['strategies'].items():
        logger.info(f"\n  📈 {strategy_name.upper()}")
        logger.info(f"     Enabled: {strategy_data['enabled']}")
        logger.info(f"     Weight: {strategy_data['weight']:.2f}")

        perf = strategy_data['performance']
        logger.info(f"     Win Rate: {perf['win_rate']:.1%}")
        logger.info(f"     Profit Factor: {perf['profit_factor']:.2f}")
        logger.info(f"     Net Profit: {perf['net_profit']:.2%}")
        logger.info(f"     Total Trades: {perf['successful_trades'] + perf['failed_trades']}")

    logger.info(f"\n📊 Engine Metrics:")
    logger.info(f"  Total Searches: {stats['total_searches']}")
    logger.info(f"  Total Results: {stats['total_results']}")
    logger.info(f"  Cache Hits: {stats['cache_hits']}")
    logger.info(f"  Avg Search Time: {stats['avg_search_time']:.2f}s")

    # 7. 정리
    await auth_manager.close()

    logger.info("\n" + "=" * 80)
    logger.info("✅ Example completed successfully!")
    logger.info("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
