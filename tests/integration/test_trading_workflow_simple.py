"""
간소화된 통합 테스트: TradingOrchestrator 핵심 플로우

실제 의존성 대신 Mock을 사용한 간단한 통합 테스트
"""
import pytest
from unittest.mock import Mock, MagicMock
from datetime import datetime
import pandas as pd

from trading import TradingOrchestrator, PositionTracker


@pytest.fixture
def simple_orchestrator():
    """간소화된 TradingOrchestrator 생성"""
    # Mock 의존성들
    api = MagicMock()
    api.account_number = "1234567890"

    config = MagicMock()
    config.get_section.return_value = {
        'max_position_size_pct': 10.0,
        'max_total_exposure_pct': 80.0,
        'max_positions': 5
    }
    config.get.return_value = 10.0

    risk_manager = MagicMock()
    risk_manager.calculate_position_size.return_value = 100000
    risk_manager.can_open_position.return_value = (True, "OK")

    validator = MagicMock()
    analyzer = MagicMock()
    db = MagicMock()

    # TradingOrchestrator 생성
    orchestrator = TradingOrchestrator(
        api=api,
        config=config,
        risk_manager=risk_manager,
        validator=validator,
        analyzer=analyzer,
        db=db
    )

    return orchestrator


class TestTradingOrchestrator:
    """TradingOrchestrator 통합 테스트"""

    def test_orchestrator_initialization(self, simple_orchestrator):
        """테스트 1: Orchestrator 초기화 확인"""
        # Then
        assert simple_orchestrator is not None
        assert simple_orchestrator.position_tracker is not None
        assert simple_orchestrator.account_manager is not None
        assert simple_orchestrator.signal_detector is not None
        assert simple_orchestrator.order_executor is not None
        assert simple_orchestrator.market_monitor is not None
        assert simple_orchestrator.condition_scanner is not None
        assert simple_orchestrator.running is False
        assert len(simple_orchestrator.watchlist) == 0

    def test_position_tracker_operations(self, simple_orchestrator):
        """테스트 2: PositionTracker 기본 동작"""
        tracker = simple_orchestrator.position_tracker

        # Given - 초기 상태
        assert tracker.get_position_count() == 0
        assert tracker.get_total_invested() == 0
        assert tracker.get_total_profit() == 0

        # When - 포지션 추가
        tracker.add_position(
            stock_code='005930',
            stock_name='삼성전자',
            entry_price=70000,
            quantity=10,
            entry_time=datetime.now()
        )

        # Then - 포지션 확인
        assert tracker.get_position_count() == 1
        assert tracker.get_total_invested() == 700000

        position = tracker.get_position('005930')
        assert position is not None
        assert position.stock_code == '005930'
        assert position.entry_price == 70000
        assert position.quantity == 10

        # When - 가격 업데이트 (수익)
        tracker.update_price('005930', 72000)

        # Then - 수익 확인
        assert tracker.get_total_profit() == 20000

        # When - 포지션 제거
        tracker.remove_position('005930')

        # Then - 제거 확인
        assert tracker.get_position_count() == 0
        assert tracker.get_position('005930') is None

    def test_position_partial_sell(self, simple_orchestrator):
        """테스트 3: 부분 청산"""
        tracker = simple_orchestrator.position_tracker

        # Given - 포지션 추가
        tracker.add_position(
            stock_code='000660',
            stock_name='SK하이닉스',
            entry_price=130000,
            quantity=10,
            entry_time=datetime.now()
        )

        position = tracker.get_position('000660')

        # When - 부분 청산 (stage 1: 40%)
        position.record_partial_sell(stage=1, quantity=4, price=135000)

        # Then - remaining_quantity 감소 확인
        assert position.remaining_quantity == 6  # quantity는 유지, remaining_quantity만 변경
        assert position.quantity == 10  # 원래 수량 유지
        assert len(position.partial_sells) == 1
        assert position.partial_sells[0]['quantity'] == 4

        # When - 부분 청산 (stage 2: 40%)
        position.record_partial_sell(stage=2, quantity=4, price=138000)

        # Then - 최종 수량 확인
        assert position.remaining_quantity == 2
        assert position.quantity == 10  # 원래 수량 유지
        assert len(position.partial_sells) == 2

    def test_watchlist_management(self, simple_orchestrator):
        """테스트 4: 감시 종목 관리"""
        # Given - 초기 빈 watchlist
        assert len(simple_orchestrator.watchlist) == 0

        # When - watchlist 추가
        simple_orchestrator.watchlist = {'005930', '000660', '035720'}

        # Then - 추가 확인
        assert len(simple_orchestrator.watchlist) == 3
        assert '005930' in simple_orchestrator.watchlist
        assert '000660' in simple_orchestrator.watchlist

        # When - 종목 제거
        simple_orchestrator.watchlist.remove('035720')

        # Then - 제거 확인
        assert len(simple_orchestrator.watchlist) == 2
        assert '035720' not in simple_orchestrator.watchlist

    def test_validated_stocks_storage(self, simple_orchestrator):
        """테스트 5: 검증된 종목 정보 저장"""
        # When - 검증된 종목 추가
        simple_orchestrator.validated_stocks = {
            '005930': {
                'name': '삼성전자',
                'stats': {
                    'win_rate': 55.0,
                    'avg_profit_pct': 2.5,
                    'total_trades': 15
                },
                'market': 'KOSPI'
            },
            '000660': {
                'name': 'SK하이닉스',
                'stats': {
                    'win_rate': 48.0,
                    'avg_profit_pct': 1.8,
                    'total_trades': 12
                },
                'market': 'KOSPI'
            }
        }

        # Then - 검증
        assert len(simple_orchestrator.validated_stocks) == 2
        assert '005930' in simple_orchestrator.validated_stocks

        samsung_info = simple_orchestrator.validated_stocks['005930']
        assert samsung_info['name'] == '삼성전자'
        assert samsung_info['stats']['win_rate'] == 55.0

    def test_system_status(self, simple_orchestrator):
        """테스트 6: 시스템 상태 조회"""
        # Given - 초기 상태
        simple_orchestrator.running = False
        simple_orchestrator.watchlist = {'005930', '000660'}

        # When - 상태 조회
        status = simple_orchestrator.get_system_status()

        # Then - 상태 확인
        assert 'running' in status
        assert 'market_open' in status
        assert 'watchlist_count' in status
        assert 'position_count' in status
        assert 'total_invested' in status
        assert 'total_value' in status
        assert 'total_profit' in status
        assert 'available_cash' in status
        assert 'total_assets' in status

        assert status['running'] is False
        assert status['watchlist_count'] == 2
        assert status['position_count'] == 0

    def test_shutdown(self, simple_orchestrator):
        """테스트 7: 시스템 종료"""
        # Given - 시스템 실행 중
        simple_orchestrator.running = True

        # When - 종료
        simple_orchestrator.shutdown()

        # Then - 종료 상태 확인
        assert simple_orchestrator.running is False

    def test_multiple_positions(self, simple_orchestrator):
        """테스트 8: 다수 포지션 관리"""
        tracker = simple_orchestrator.position_tracker

        # Given - 여러 포지션 추가
        stocks = [
            ('005930', '삼성전자', 70000, 10),
            ('000660', 'SK하이닉스', 130000, 5),
            ('035720', '카카오', 55000, 15),
            ('005380', '현대차', 180000, 3),
            ('051910', 'LG화학', 450000, 2)
        ]

        for code, name, price, qty in stocks:
            tracker.add_position(
                stock_code=code,
                stock_name=name,
                entry_price=price,
                quantity=qty,
                entry_time=datetime.now()
            )

        # Then - 포지션 확인
        assert tracker.get_position_count() == 5

        expected_invested = sum(price * qty for _, _, price, qty in stocks)
        assert tracker.get_total_invested() == expected_invested

        # When - 가격 업데이트 (모두 +10% 수익)
        for code, _, price, _ in stocks:
            new_price = int(price * 1.1)
            tracker.update_price(code, new_price)

        # Then - 총 수익 확인 (약 10%)
        total_profit = tracker.get_total_profit()
        assert total_profit > 0
        profit_rate = (total_profit / expected_invested) * 100
        assert 9.0 < profit_rate < 11.0  # 10% 근처

    def test_position_profit_calculation(self, simple_orchestrator):
        """테스트 9: 포지션별 손익 계산"""
        tracker = simple_orchestrator.position_tracker

        # Given - 포지션 추가
        tracker.add_position(
            stock_code='005930',
            stock_name='삼성전자',
            entry_price=70000,
            quantity=10,
            entry_time=datetime.now()
        )

        position = tracker.get_position('005930')

        # When - 수익 상태 (75000원)
        position.update_price(75000)

        # Then - 수익률 확인 (+7.14%)
        assert 7.0 < position.profit_pct < 7.2

        # When - 손실 상태 (65000원)
        position.update_price(65000)

        # Then - 손실률 확인 (-7.14%)
        assert -7.2 < position.profit_pct < -7.0


class TestEdgeCases:
    """엣지 케이스 테스트"""

    def test_remove_nonexistent_position(self, simple_orchestrator):
        """존재하지 않는 포지션 제거 시도"""
        tracker = simple_orchestrator.position_tracker

        # When/Then - 에러 없이 처리되어야 함
        tracker.remove_position('NONEXISTENT')
        assert tracker.get_position_count() == 0

    def test_update_price_nonexistent_position(self, simple_orchestrator):
        """존재하지 않는 포지션 가격 업데이트"""
        tracker = simple_orchestrator.position_tracker

        # When/Then - 에러 없이 처리되어야 함
        tracker.update_price('NONEXISTENT', 100000)

    def test_zero_quantity_position(self, simple_orchestrator):
        """0 수량 포지션"""
        tracker = simple_orchestrator.position_tracker

        # When - 0 수량 포지션 추가 시도
        tracker.add_position(
            stock_code='005930',
            stock_name='삼성전자',
            entry_price=70000,
            quantity=0,
            entry_time=datetime.now()
        )

        # Then - 추가되지 않아야 함 (또는 에러)
        # (실제 구현에 따라 다를 수 있음)

    def test_empty_watchlist_status(self, simple_orchestrator):
        """빈 watchlist로 상태 조회"""
        # Given - 빈 watchlist
        simple_orchestrator.watchlist = set()

        # When
        status = simple_orchestrator.get_system_status()

        # Then
        assert status['watchlist_count'] == 0


class TestMarketMonitor:
    """MarketMonitor 기능 테스트"""

    def test_market_status_check(self, simple_orchestrator):
        """장 상태 체크"""
        monitor = simple_orchestrator.market_monitor

        # When
        status = monitor.get_market_status()

        # Then
        assert 'is_open' in status
        assert 'status_message' in status
        assert isinstance(status['is_open'], bool)
        assert isinstance(status['status_message'], str)

    def test_is_market_open(self, simple_orchestrator):
        """장 오픈 여부 확인"""
        monitor = simple_orchestrator.market_monitor

        # When
        is_open = monitor.is_market_open()

        # Then - boolean 반환
        assert isinstance(is_open, bool)
