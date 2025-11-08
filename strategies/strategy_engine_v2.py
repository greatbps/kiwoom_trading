# strategy_engine_v2.py
"""
v2 버전: 개선된 신호 융합 및 리스크 기반 포지션 사이징

주요 기능:
- 기술적 점수 + 감성 점수 통합
- ATR 기반 손절가 계산
- 리스크 비율 기반 포지션 사이징
- 거래량 필터 (EWMA z-score)
- 쿨다운 메커니즘
"""
import logging
import pandas as pd
import numpy as np
from datetime import datetime
from typing import Dict, Optional, Any

# 지표 모듈 import
try:
    from analyzers.indicators import add_momentum_indicators, calculate_improved_squeeze_momentum
except ImportError:
    from indicators import add_momentum_indicators, calculate_improved_squeeze_momentum

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    'buy_conf_threshold': 50.0,      # 매수 신호 임계값
    'sell_conf_threshold': 50.0,     # 매도 신호 임계값
    'signal_confidence_min': 8.0,    # 최소 신호 신뢰도
    'volume_z_threshold': 0.7,       # 거래량 z-score 임계값
    'cooldown_bars': 3,              # 신호 후 쿨다운 기간
    'sentiment_buy_block': -0.4,     # 감성 점수가 이보다 낮으면 매수 차단
    'sentiment_sell_block': 0.4,     # 감성 점수가 이보다 높으면 매도 차단
    'atr_multiplier': 2.0,           # ATR 손절 배수
    'risk_percent': 1.0,             # 거래당 리스크 비율 (%)
}


class StrategyEngineV2:
    """개선된 전략 엔진 (v2)"""

    def __init__(self, streamer, manager, symbol: str, config: Dict = None, sentiment_analyzer=None):
        """
        초기화

        Args:
            streamer: MarketDataStreamerV2 인스턴스
            manager: OrderManager 인스턴스 (매수/매도 메서드 구현 필요)
            symbol: 종목 코드
            config: 설정 딕셔너리
            sentiment_analyzer: 감성 분석기 (선택적)
        """
        self.streamer = streamer
        self.manager = manager
        self.symbol = symbol
        self.sentiment = sentiment_analyzer

        self.config = DEFAULT_CONFIG.copy()
        if config:
            self.config.update(config)

        self.historical = pd.DataFrame(columns=['open', 'high', 'low', 'close', 'volume'])
        self.signal_cooldown = 0
        self.last_timeframe_key = None

    def _compute_technical_score(self, df: pd.DataFrame) -> pd.DataFrame:
        """기술적 점수 계산 (0-100 범위)

        Args:
            df: 지표가 포함된 DataFrame

        Returns:
            buy_score, sell_score가 추가된 DataFrame
        """
        df = df.copy()

        # 필수 컬럼 확인
        required = ['rsi', 'stoch_k', 'squeeze_signal', 'volume_momentum', 'price_velocity', 'volume']
        for col in required:
            if col not in df.columns:
                df[col] = 0.0

        n = len(df)
        buy = np.zeros(n)
        sell = np.zeros(n)

        # Squeeze 신호
        squeeze_text = df['squeeze_signal'].astype(str)
        buy += np.where(squeeze_text.str.contains('STRONG_BUY', na=False), 30, 0)
        buy += np.where(squeeze_text.str.contains('BUY', na=False) & ~squeeze_text.str.contains('STRONG', na=False), 15, 0)
        sell += np.where(squeeze_text.str.contains('STRONG_SELL', na=False), 30, 0)
        sell += np.where(squeeze_text.str.contains('SELL', na=False) & ~squeeze_text.str.contains('STRONG', na=False), 15, 0)

        # RSI
        buy += np.where(df['rsi'] < 30, 15, 0)
        sell += np.where(df['rsi'] > 70, 15, 0)

        # Stochastic
        buy += np.where(df['stoch_k'] < 20, 10, 0)
        sell += np.where(df['stoch_k'] > 80, 10, 0)

        # Volume momentum
        buy += np.where(df['volume_momentum'] > 20, 15, 0)
        sell += np.where(df['volume_momentum'] < -10, 15, 0)

        # Price velocity
        buy += np.where(df['price_velocity'] > 2, 10, 0)
        sell += np.where(df['price_velocity'] < -2, 10, 0)

        df['buy_score'] = np.clip(buy, 0, 100)
        df['sell_score'] = np.clip(sell, 0, 100)
        df['tech_score'] = df['buy_score'] - df['sell_score']
        df['signal_confidence'] = np.abs(df['tech_score'])

        return df

    def _integrate_sentiment(self, tech_row: pd.Series, sentiment_value: float) -> pd.Series:
        """기술적 점수와 감성 점수 통합

        감성 점수는 승수로 작용: final_score = tech_score * (1 + sentiment_value)

        Args:
            tech_row: 기술적 분석 row
            sentiment_value: 감성 점수 (-1.0 ~ 1.0)

        Returns:
            final_score가 추가된 Series
        """
        tech = float(tech_row.get('tech_score', 0.0))
        final = tech * (1.0 + float(sentiment_value))
        tech_row['final_score'] = final
        return tech_row

    def _compute_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """ATR 계산

        Args:
            df: OHLCV DataFrame
            period: ATR 기간

        Returns:
            ATR 값
        """
        if len(df) < period:
            return 0.0

        high_low = df['high'] - df['low']
        high_prev_close = (df['high'] - df['close'].shift(1)).abs()
        low_prev_close = (df['low'] - df['close'].shift(1)).abs()

        tr = pd.concat([high_low, high_prev_close, low_prev_close], axis=1).max(axis=1)
        atr = tr.rolling(period).mean().iloc[-1]

        return float(atr if not np.isnan(atr) else 0.0)

    def _position_size(self, price: float, atr: float) -> int:
        """리스크 기반 포지션 사이징

        Args:
            price: 현재가
            atr: ATR 값

        Returns:
            매수/매도 수량
        """
        # 계좌 잔고 (manager에서 가져오거나 기본값)
        equity = getattr(self.manager, 'account_equity', 10000000.0)  # 기본 1000만원

        # 리스크 금액
        risk_amount = equity * (self.config['risk_percent'] / 100.0)

        # 손절 거리 (ATR * multiplier)
        stop_distance = max(atr * self.config['atr_multiplier'], 0.01 * price)

        # 수량 계산
        qty = int(max(1, risk_amount / stop_distance))

        return qty

    async def process_candle(self, candle: Dict):
        """캔들 데이터 처리 및 매매 신호 생성

        Args:
            candle: 캔들 딕셔너리 (timeframe_key, open, high, low, close, volume 포함)
        """
        key = candle.get('timeframe_key')
        if key == self.last_timeframe_key:
            return
        self.last_timeframe_key = key

        # 데이터 추가
        new = pd.DataFrame([candle])[['open', 'high', 'low', 'close', 'volume']]
        if self.historical.empty:
            self.historical = new
        else:
            self.historical = pd.concat([self.historical, new], ignore_index=True)

        # 데이터 제한 (최대 500개)
        if len(self.historical) > 500:
            self.historical = self.historical.iloc[-500:].reset_index(drop=True)

        # 지표 계산
        try:
            proc = add_momentum_indicators(self.historical)
            ind = calculate_improved_squeeze_momentum(proc)
        except Exception as e:
            logger.exception('지표 계산 실패')
            return

        if ind.empty:
            logger.debug(f"{self.symbol} 지표 데이터 부족")
            return

        # 기술적 점수
        enhanced = self._compute_technical_score(ind)
        latest = enhanced.iloc[-1]

        # 감성 점수
        sentiment_value = 0.0
        if self.sentiment:
            sentiment_value = self.sentiment.get_sentiment_score(self.symbol)

        # 통합 점수
        latest = self._integrate_sentiment(latest, sentiment_value)

        # 신호 신뢰도 필터
        if abs(latest['final_score']) < self.config['signal_confidence_min']:
            logger.debug(f"{self.symbol} 신호 신뢰도 부족: {latest['final_score']:.2f}")
            return

        # 거래량 필터
        try:
            vol_z = self.streamer.compute_ewma_volume_z(self.historical).iloc[-1]
        except Exception:
            vol_z = 0.0

        # 쿨다운
        if self.signal_cooldown > 0:
            self.signal_cooldown -= 1
            return

        # 매수 신호
        if latest['final_score'] > 0:
            # 거래량 확인
            if vol_z < self.config['volume_z_threshold']:
                logger.debug(f"{self.symbol} 거래량 부족 (z={vol_z:.2f})")
                return

            # 감성 필터
            if sentiment_value < self.config['sentiment_buy_block']:
                logger.info(f"{self.symbol} 부정적 감성으로 매수 차단 ({sentiment_value:.2f})")
                return

            # 포지션 사이징
            atr = self._compute_atr(self.historical)
            qty = self._position_size(candle['close'], atr)

            logger.info(f"[{self.symbol}] 매수 신호: 수량={qty}, 점수={latest['final_score']:.2f}, 감성={sentiment_value:.2f}")

            # 매수 주문
            if hasattr(self.manager, 'buy_order'):
                await self.manager.buy_order(candle['close'], qty)

            self.signal_cooldown = self.config['cooldown_bars']

        # 매도 신호
        elif latest['final_score'] < 0:
            # 거래량 확인
            if vol_z > -self.config['volume_z_threshold']:
                logger.debug(f"{self.symbol} 거래량 부족 (z={vol_z:.2f})")
                return

            # 감성 필터
            if sentiment_value > self.config['sentiment_sell_block']:
                logger.info(f"{self.symbol} 긍정적 감성으로 매도 차단 ({sentiment_value:.2f})")
                return

            # 포지션 사이징
            atr = self._compute_atr(self.historical)
            qty = self._position_size(candle['close'], atr)

            logger.info(f"[{self.symbol}] 매도 신호: 수량={qty}, 점수={latest['final_score']:.2f}, 감성={sentiment_value:.2f}")

            # 매도 주문
            if hasattr(self.manager, 'sell_order'):
                await self.manager.sell_order(candle['close'], qty)

            self.signal_cooldown = self.config['cooldown_bars']


if __name__ == "__main__":
    # 간단한 테스트
    import asyncio
    logging.basicConfig(level=logging.INFO)

    class MockManager:
        """테스트용 Mock Manager"""
        account_equity = 10000000

        async def buy_order(self, price, qty):
            print(f"매수 주문: 가격={price}, 수량={qty}")

        async def sell_order(self, price, qty):
            print(f"매도 주문: 가격={price}, 수량={qty}")

    async def test():
        from analyzers.market_streamer_v2 import MarketDataStreamerV2

        streamer = MarketDataStreamerV2()
        manager = MockManager()
        engine = StrategyEngineV2(streamer, manager, "005930")

        # 데이터 로드
        df = await streamer.get_price_data("KRX", "005930", limit=100)
        if df.empty:
            print("데이터 없음")
            return

        # 캔들 시뮬레이션
        for i in range(len(df)):
            candle = {
                'timeframe_key': f"1234{i:02d}",
                'open': df.iloc[i]['open'],
                'high': df.iloc[i]['high'],
                'low': df.iloc[i]['low'],
                'close': df.iloc[i]['close'],
                'volume': df.iloc[i]['volume']
            }
            await engine.process_candle(candle)
            await asyncio.sleep(0.1)

    asyncio.run(test())
