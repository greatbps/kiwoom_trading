#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
로깅 설정 모듈
중앙집중식 로깅 관리
"""

import logging
import logging.handlers
import os
from pathlib import Path
from datetime import datetime


class TradingSystemLogger:
    """Trading System 로거"""

    def __init__(self, name='trading_system', log_dir='./logs'):
        self.name = name
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 로그 레벨 (환경 변수로 설정 가능)
        log_level_str = os.getenv('LOG_LEVEL', 'INFO')
        self.log_level = getattr(logging, log_level_str.upper(), logging.INFO)

        # 로거 생성
        self.logger = self._setup_logger()

    def _setup_logger(self):
        """로거 설정"""
        logger = logging.getLogger(self.name)
        logger.setLevel(self.log_level)

        # 기존 핸들러 제거 (중복 방지)
        if logger.handlers:
            logger.handlers.clear()

        # 포맷터 설정
        formatter = logging.Formatter(
            '[%(asctime)s] [%(name)s] [%(levelname)s] [%(filename)s:%(lineno)d] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # 1. 콘솔 핸들러
        console_handler = logging.StreamHandler()
        console_handler.setLevel(self.log_level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

        # 2. 파일 핸들러 (일반 로그)
        file_handler = logging.handlers.RotatingFileHandler(
            filename=self.log_dir / 'trading_system.log',
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=10,
            encoding='utf-8'
        )
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

        # 3. 에러 핸들러 (ERROR 이상만)
        error_handler = logging.handlers.RotatingFileHandler(
            filename=self.log_dir / 'errors.log',
            maxBytes=10 * 1024 * 1024,  # 10MB
            backupCount=5,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_handler.setFormatter(formatter)
        logger.addHandler(error_handler)

        # 4. 디버그 핸들러 (개발 모드)
        if os.getenv('DEBUG_MODE', 'false').lower() == 'true':
            debug_handler = logging.handlers.RotatingFileHandler(
                filename=self.log_dir / 'debug.log',
                maxBytes=50 * 1024 * 1024,  # 50MB
                backupCount=3,
                encoding='utf-8'
            )
            debug_handler.setLevel(logging.DEBUG)
            debug_handler.setFormatter(formatter)
            logger.addHandler(debug_handler)

        # 5. 거래 로그 핸들러 (별도 파일)
        trade_handler = logging.handlers.TimedRotatingFileHandler(
            filename=self.log_dir / 'trades.log',
            when='midnight',
            interval=1,
            backupCount=90,  # 90일 보관
            encoding='utf-8'
        )
        trade_handler.setLevel(logging.INFO)
        trade_handler.setFormatter(formatter)
        trade_handler.addFilter(TradeLogFilter())
        logger.addHandler(trade_handler)

        return logger

    def get_logger(self):
        """로거 반환"""
        return self.logger


class TradeLogFilter(logging.Filter):
    """거래 로그 필터"""

    def filter(self, record):
        # 'TRADE:' 로 시작하는 메시지만 필터링
        return record.getMessage().startswith('TRADE:')


# 전역 로거 인스턴스
_logger_instance = None


def get_logger(name='trading_system'):
    """
    로거 가져오기 (싱글톤)

    Args:
        name: 로거 이름

    Returns:
        logging.Logger
    """
    global _logger_instance

    if _logger_instance is None:
        _logger_instance = TradingSystemLogger(name)

    return _logger_instance.get_logger()


def log_trade(symbol, action, price, quantity, strategy):
    """
    거래 로깅 (별도 파일)

    Args:
        symbol: 종목 코드
        action: 거래 유형 (BUY/SELL)
        price: 가격
        quantity: 수량
        strategy: 전략
    """
    logger = get_logger()
    logger.info(
        f"TRADE: {symbol} | {action} | {price:,.0f} | {quantity} | {strategy} | {datetime.now().isoformat()}"
    )


def log_signal(symbol, strategy, score, confidence):
    """
    신호 로깅

    Args:
        symbol: 종목 코드
        strategy: 전략
        score: 점수
        confidence: 신뢰도
    """
    logger = get_logger()
    logger.info(
        f"SIGNAL: {symbol} | {strategy} | score={score:.3f} | confidence={confidence:.3f}"
    )


def log_performance(strategy, win_rate, sharpe, total_trades):
    """
    성능 로깅

    Args:
        strategy: 전략
        win_rate: 승률
        sharpe: Sharpe Ratio
        total_trades: 총 거래 수
    """
    logger = get_logger()
    logger.info(
        f"PERFORMANCE: {strategy} | win_rate={win_rate:.2%} | sharpe={sharpe:.2f} | trades={total_trades}"
    )


if __name__ == "__main__":
    # 테스트
    logger = get_logger()

    logger.debug("디버그 메시지")
    logger.info("정보 메시지")
    logger.warning("경고 메시지")
    logger.error("에러 메시지")
    logger.critical("크리티컬 메시지")

    # 거래 로그
    log_trade("005930", "BUY", 70000, 10, "squeeze_momentum")

    # 신호 로그
    log_signal("005930", "squeeze_momentum", 0.85, 0.90)

    # 성능 로그
    log_performance("squeeze_momentum", 0.75, 1.5, 100)

    print("로그 파일 확인: ./logs/")
