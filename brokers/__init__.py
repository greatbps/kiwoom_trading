"""
브로커 추상화 레이어
==================

전략 코드가 증권사를 모르게 만드는 구조

사용법:
    from brokers import get_broker, BrokerType

    # 한투 국내
    broker = get_broker(BrokerType.KIS_DOMESTIC)
    broker.initialize()
    positions = broker.get_positions()
    broker.place_market_sell("371450", 1)

    # 한투 해외
    broker = get_broker(BrokerType.KIS_OVERSEAS)
    broker.place_market_buy("SOFI", 1)

    # 키움 국내
    broker = get_broker(BrokerType.KIWOOM)
"""

from enum import Enum
from typing import Dict, Optional

from brokers.broker_base import (
    BrokerBase, Market, Position, Balance,
    OrderSide, OrderType, OrderStatus, OrderResult
)
from brokers.kiwoom_broker import KiwoomBroker
from brokers.korea_invest_broker import (
    KoreaInvestDomesticBroker,
    KoreaInvestOverseasBroker
)


class BrokerType(Enum):
    """브로커 타입"""
    KIWOOM = "KIWOOM"                    # 키움 국내
    KIS_DOMESTIC = "KIS_DOMESTIC"        # 한투 국내
    KIS_OVERSEAS = "KIS_OVERSEAS"        # 한투 해외


# 브로커 인스턴스 캐시
_broker_cache: Dict[BrokerType, BrokerBase] = {}


def get_broker(broker_type: BrokerType, force_new: bool = False) -> BrokerBase:
    """
    브로커 인스턴스 가져오기

    Args:
        broker_type: 브로커 타입
        force_new: True면 새 인스턴스 생성

    Returns:
        브로커 인스턴스
    """
    global _broker_cache

    if not force_new and broker_type in _broker_cache:
        return _broker_cache[broker_type]

    if broker_type == BrokerType.KIWOOM:
        broker = KiwoomBroker()
    elif broker_type == BrokerType.KIS_DOMESTIC:
        broker = KoreaInvestDomesticBroker()
    elif broker_type == BrokerType.KIS_OVERSEAS:
        broker = KoreaInvestOverseasBroker()
    else:
        raise ValueError(f"Unknown broker type: {broker_type}")

    _broker_cache[broker_type] = broker
    return broker


def get_all_brokers() -> Dict[BrokerType, BrokerBase]:
    """모든 브로커 인스턴스 가져오기"""
    return {
        BrokerType.KIWOOM: get_broker(BrokerType.KIWOOM),
        BrokerType.KIS_DOMESTIC: get_broker(BrokerType.KIS_DOMESTIC),
        BrokerType.KIS_OVERSEAS: get_broker(BrokerType.KIS_OVERSEAS),
    }


__all__ = [
    # Base
    'BrokerBase',
    'Market',
    'Position',
    'Balance',
    'OrderSide',
    'OrderType',
    'OrderStatus',
    'OrderResult',

    # Implementations
    'KiwoomBroker',
    'KoreaInvestDomesticBroker',
    'KoreaInvestOverseasBroker',

    # Factory
    'BrokerType',
    'get_broker',
    'get_all_brokers',
]
