#!/usr/bin/env python3
# test_v2_integration.py
"""
v2 시스템 통합 테스트

테스트 항목:
1. 뉴스 감성 분석 (NewsSentimentAnalyzerV2)
2. 시장 데이터 스트리밍 (MarketDataStreamerV2)
3. 전략 엔진 (StrategyEngineV2)
4. 전체 파이프라인 통합
"""
import asyncio
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 경로에 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from analyzers.news_sentiment_v2 import NewsSentimentAnalyzerV2
from analyzers.market_streamer_v2 import MarketDataStreamerV2
from strategies.strategy_engine_v2 import StrategyEngineV2

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s'
)
logger = logging.getLogger(__name__)


class MockOrderManager:
    """테스트용 주문 관리자"""

    def __init__(self):
        self.account_equity = 10000000  # 1000만원
        self.orders = []
        self.exchange_code = "KRX"

    async def buy_order(self, price: float, qty: int):
        """매수 주문"""
        order = {
            'type': 'BUY',
            'price': price,
            'qty': qty,
            'timestamp': asyncio.get_event_loop().time()
        }
        self.orders.append(order)
        logger.info(f"✅ 매수 주문: {price:,.0f}원 x {qty}주 = {price * qty:,.0f}원")

    async def sell_order(self, price: float, qty: int):
        """매도 주문"""
        order = {
            'type': 'SELL',
            'price': price,
            'qty': qty,
            'timestamp': asyncio.get_event_loop().time()
        }
        self.orders.append(order)
        logger.info(f"✅ 매도 주문: {price:,.0f}원 x {qty}주 = {price * qty:,.0f}원")


async def test_news_sentiment():
    """뉴스 감성 분석 테스트"""
    logger.info("=" * 60)
    logger.info("1. 뉴스 감성 분석 테스트")
    logger.info("=" * 60)

    symbols = ['삼성전자', 'SK하이닉스', 'NAVER']
    analyzer = NewsSentimentAnalyzerV2(symbols=symbols)

    # 전체 업데이트
    await analyzer.update_all()

    # 결과 출력
    print("\n📊 감성 분석 결과:")
    for symbol in symbols:
        score = analyzer.get_sentiment_score(symbol)
        emoji = "🟢" if score > 0.2 else "🔴" if score < -0.2 else "⚪"
        print(f"  {emoji} {symbol}: {score:+.2f}")

    return analyzer


async def test_market_streamer():
    """시장 데이터 스트리밍 테스트"""
    logger.info("=" * 60)
    logger.info("2. 시장 데이터 스트리밍 테스트")
    logger.info("=" * 60)

    streamer = MarketDataStreamerV2()

    # 종목 리스트
    symbols = ["005930", "000660", "035420"]  # 삼성전자, SK하이닉스, NAVER

    for symbol in symbols:
        # 현재가 조회
        price = await streamer.get_current_price("KRX", symbol)
        print(f"\n{symbol} 현재가: {price:,.0f}원")

        # 가격 데이터 조회
        df = await streamer.get_price_data("KRX", symbol, limit=100)
        if not df.empty:
            print(f"  데이터: {len(df)}개 (최신: {df.iloc[-1]['close']:,.0f}원)")

            # 거래량 z-score
            vol_z = streamer.compute_ewma_volume_z(df)
            print(f"  거래량 z-score: {vol_z.iloc[-1]:.2f}")
        else:
            print(f"  ⚠️ 데이터 없음")

    return streamer


async def test_strategy_engine(streamer, sentiment_analyzer):
    """전략 엔진 테스트"""
    logger.info("=" * 60)
    logger.info("3. 전략 엔진 테스트")
    logger.info("=" * 60)

    symbol = "005930"  # 삼성전자
    manager = MockOrderManager()

    # 전략 엔진 생성
    engine = StrategyEngineV2(
        streamer=streamer,
        manager=manager,
        symbol=symbol,
        sentiment_analyzer=sentiment_analyzer
    )

    # 데이터 로드
    df = await streamer.get_price_data("KRX", symbol, limit=200)
    if df.empty:
        logger.error(f"{symbol} 데이터 없음")
        return

    print(f"\n{symbol} 전략 테스트 시작...")
    print(f"  데이터: {len(df)}개")

    # 캔들 시뮬레이션 (최근 50개만)
    for i in range(max(0, len(df) - 50), len(df)):
        candle = {
            'timeframe_key': f"{i:04d}",
            'open': df.iloc[i]['open'],
            'high': df.iloc[i]['high'],
            'low': df.iloc[i]['low'],
            'close': df.iloc[i]['close'],
            'volume': df.iloc[i]['volume']
        }
        await engine.process_candle(candle)

    # 주문 내역
    print(f"\n📋 주문 내역: 총 {len(manager.orders)}건")
    for i, order in enumerate(manager.orders, 1):
        print(f"  {i}. {order['type']}: {order['price']:,.0f}원 x {order['qty']}주")

    return engine, manager


async def test_full_pipeline():
    """전체 파이프라인 통합 테스트"""
    logger.info("=" * 60)
    logger.info("4. 전체 파이프라인 통합 테스트")
    logger.info("=" * 60)

    # 1. 감성 분석
    sentiment_analyzer = await test_news_sentiment()
    await asyncio.sleep(1)

    # 2. 시장 데이터
    streamer = await test_market_streamer()
    await asyncio.sleep(1)

    # 3. 전략 엔진
    engine, manager = await test_strategy_engine(streamer, sentiment_analyzer)

    # 종합 결과
    logger.info("=" * 60)
    logger.info("✅ 통합 테스트 완료")
    logger.info("=" * 60)

    print("\n" + "=" * 60)
    print("📊 최종 결과 요약")
    print("=" * 60)

    print("\n1️⃣ 감성 분석:")
    for symbol in sentiment_analyzer.symbols:
        score = sentiment_analyzer.get_sentiment_score(symbol)
        print(f"  - {symbol}: {score:+.2f}")

    print("\n2️⃣ 시장 데이터:")
    for symbol, price in streamer.latest_prices.items():
        print(f"  - {symbol}: {price:,.0f}원")

    print(f"\n3️⃣ 거래 결과:")
    print(f"  - 총 주문 수: {len(manager.orders)}건")
    if manager.orders:
        buy_orders = [o for o in manager.orders if o['type'] == 'BUY']
        sell_orders = [o for o in manager.orders if o['type'] == 'SELL']
        print(f"  - 매수: {len(buy_orders)}건")
        print(f"  - 매도: {len(sell_orders)}건")


async def main():
    """메인 함수"""
    print("\n" + "=" * 60)
    print("🚀 kiwoom_trading v2 시스템 통합 테스트")
    print("=" * 60)

    try:
        await test_full_pipeline()
        print("\n✅ 모든 테스트 통과!")

    except Exception as e:
        logger.exception("테스트 실패")
        print(f"\n❌ 테스트 실패: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
