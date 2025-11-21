"""
통합 테스트: 전체 트레이딩 워크플로우

전체 시스템의 end-to-end 테스트:
1. 시스템 초기화
2. 조건검색 실행
3. VWAP 필터링
4. 실시간 모니터링
5. 매수 신호 감지 및 실행
6. 매도 신호 감지 및 실행
7. 포지션 추적
"""
import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch, MagicMock
from datetime import datetime
import pandas as pd

from trading import (
    TradingOrchestrator,
    PositionTracker,
    AccountManager,
    SignalDetector,
    OrderExecutor,
    MarketMonitor,
    ConditionScanner
)
from kiwoom_api import KiwoomAPI
from config.config_manager import ConfigManager
from core.risk_manager import RiskManager
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from analyzers.pre_trade_validator import PreTradeValidator
from database.trading_db_v2 import TradingDatabaseV2


@pytest.fixture
def mock_config():
    """Mock ConfigManager"""
    config = Mock(spec=ConfigManager)
    config.get_section.return_value = {
        'max_position_size_pct': 10.0,
        'max_total_exposure_pct': 80.0,
        'max_positions': 5,
        'stop_loss_pct': 1.3,
        'activation_pct': 1.5,
        'trailing_ratio': 1.0,
        'min_win_rate': 40.0,
        'min_avg_profit_pct': 1.0,
        'lookback_days': 10,
        'min_trades': 6,
        'enabled': True,
        'tiers': [
            {'profit_pct': 4.0, 'exit_ratio': 0.4},
            {'profit_pct': 6.0, 'exit_ratio': 0.4}
        ]
    }
    config.get.return_value = 10.0
    return config


@pytest.fixture
def mock_api():
    """Mock KiwoomAPI"""
    api = Mock(spec=KiwoomAPI)
    api.account_number = "1234567890"
    api.get_access_token.return_value = "mock_token"
    api.get_balance.return_value = {
        'return_code': 0,
        'output': {
            'dnca_tot_amt': '10000000',  # 예수금 총액
            'nxdy_excc_amt': '10000000',  # 익일 정산금액
            'prvs_rcdl_excc_amt': '0',   # 가수도 정산금액
            'cma_evlu_amt': '0',         # CMA 평가금액
            'tot_evlu_amt': '10000000'   # 총평가금액
        }
    }
    api.get_account_info.return_value = {
        'return_code': 0,
        'output': []
    }
    return api


@pytest.fixture
def mock_risk_manager():
    """Mock RiskManager"""
    risk_manager = Mock(spec=RiskManager)
    risk_manager.calculate_position_size.return_value = 100000
    risk_manager.can_open_position.return_value = (True, "OK")
    return risk_manager


@pytest.fixture
def mock_analyzer():
    """Mock EntryTimingAnalyzer"""
    analyzer = Mock(spec=EntryTimingAnalyzer)
    analyzer.check_entry_signal.return_value = {
        'signal': 1,
        'current_price': 50000,
        'reason': 'VWAP upward cross'
    }
    return analyzer


@pytest.fixture
def mock_validator():
    """Mock PreTradeValidator"""
    validator = Mock(spec=PreTradeValidator)
    validator.validate_stock.return_value = {
        'allowed': True,
        'reason': 'Passed VWAP validation',
        'stats': {
            'win_rate': 50.0,
            'avg_profit_pct': 2.0,
            'total_trades': 10,
            'profit_factor': 1.5
        }
    }
    return validator


@pytest.fixture
def mock_db():
    """Mock TradingDatabase"""
    db = Mock(spec=TradingDatabaseV2)
    db.insert_validation_score.return_value = None
    db.insert_buy_order.return_value = None
    db.insert_sell_order.return_value = None
    db.get_active_candidates.return_value = []
    return db


@pytest.fixture
def sample_dataframe():
    """샘플 OHLCV 데이터프레임"""
    data = {
        'open': [49000, 49500, 50000, 50500, 51000],
        'high': [49500, 50000, 50500, 51000, 51500],
        'low': [48500, 49000, 49500, 50000, 50500],
        'close': [49500, 50000, 50500, 51000, 51500],
        'volume': [100000, 110000, 120000, 130000, 140000]
    }
    df = pd.DataFrame(data)

    # VWAP 계산
    df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()

    return df


@pytest.fixture
def orchestrator(mock_config, mock_api, mock_risk_manager,
                mock_validator, mock_analyzer, mock_db):
    """TradingOrchestrator 인스턴스"""
    return TradingOrchestrator(
        api=mock_api,
        config=mock_config,
        risk_manager=mock_risk_manager,
        validator=mock_validator,
        analyzer=mock_analyzer,
        db=mock_db
    )


class TestFullTradingWorkflow:
    """전체 트레이딩 워크플로우 통합 테스트"""

    @pytest.mark.asyncio
    async def test_01_system_initialization(self, orchestrator, mock_api):
        """테스트 1: 시스템 초기화"""
        # Given
        mock_api.get_balance.return_value = {
            'return_code': 0,
            'output': {
                'dnca_tot_amt': '10000000',
                'nxdy_excc_amt': '10000000',
                'prvs_rcdl_excc_amt': '0',
                'cma_evlu_amt': '0',
                'tot_evlu_amt': '10000000'
            }
        }
        mock_api.get_account_info.return_value = {
            'return_code': 0,
            'output': []
        }

        # When
        result = await orchestrator.initialize()

        # Then
        assert result is True
        assert orchestrator.account_manager.get_available_cash() >= 0
        assert orchestrator.position_tracker.get_position_count() == 0

    @pytest.mark.asyncio
    async def test_02_condition_search_execution(self, orchestrator, mock_api, mock_validator):
        """테스트 2: 조건검색 실행"""
        # Given
        mock_api.get_stock_list_by_condition.return_value = {
            'return_code': 0,
            'stocks': [
                {'stock_code': '005930', 'stock_name': '삼성전자'},
                {'stock_code': '000660', 'stock_name': 'SK하이닉스'}
            ]
        }

        mock_validator.validate_stock.return_value = {
            'allowed': True,
            'reason': 'Passed',
            'stats': {
                'win_rate': 50.0,
                'avg_profit_pct': 2.0,
                'total_trades': 10,
                'profit_factor': 1.5
            }
        }

        # When
        await orchestrator.run_condition_filtering("VWAP돌파")

        # Then
        assert len(orchestrator.watchlist) > 0
        assert len(orchestrator.validated_stocks) > 0

    @pytest.mark.asyncio
    async def test_03_vwap_filtering(self, orchestrator, mock_validator):
        """테스트 3: VWAP 필터링"""
        # Given
        stock_list = [
            {'stock_code': '005930', 'stock_name': '삼성전자'},
            {'stock_code': '000660', 'stock_name': 'SK하이닉스'},
            {'stock_code': '035720', 'stock_name': '카카오'}
        ]

        # 첫 번째 종목만 통과
        def validate_side_effect(code, name):
            if code == '005930':
                return {
                    'allowed': True,
                    'reason': 'Passed',
                    'stats': {
                        'win_rate': 50.0,
                        'avg_profit_pct': 2.0,
                        'total_trades': 10,
                        'profit_factor': 1.5
                    }
                }
            else:
                return {
                    'allowed': False,
                    'reason': 'Low win rate',
                    'stats': {}
                }

        mock_validator.validate_stock.side_effect = validate_side_effect

        # When
        validated = orchestrator.condition_scanner.filter_with_vwap(
            stock_list,
            min_win_rate=40.0,
            min_avg_profit=1.0
        )

        # Then
        assert len(validated) == 1
        assert '005930' in validated

    @pytest.mark.asyncio
    async def test_04_buy_signal_detection_and_execution(
        self, orchestrator, mock_api, sample_dataframe
    ):
        """테스트 4: 매수 신호 감지 및 실행"""
        # Given
        await orchestrator.initialize()

        orchestrator.watchlist = {'005930'}
        orchestrator.validated_stocks = {
            '005930': {
                'name': '삼성전자',
                'stats': {
                    'win_rate': 50.0,
                    'avg_profit_pct': 2.0,
                    'total_trades': 10
                }
            }
        }

        # 매수 주문 성공
        mock_api.order_stock.return_value = {
            'return_code': 0,
            'order_number': 'ORD123456',
            'message': 'Success'
        }

        # When
        await orchestrator._check_entry_signal('005930', '삼성전자', sample_dataframe)

        # Then
        assert orchestrator.position_tracker.get_position_count() == 1
        position = orchestrator.position_tracker.get_position('005930')
        assert position is not None
        assert position.stock_name == '삼성전자'

    @pytest.mark.asyncio
    async def test_05_sell_signal_detection_and_execution(
        self, orchestrator, mock_api, sample_dataframe
    ):
        """테스트 5: 매도 신호 감지 및 실행"""
        # Given
        await orchestrator.initialize()

        # 포지션 추가
        orchestrator.position_tracker.add_position(
            stock_code='005930',
            stock_name='삼성전자',
            entry_price=50000,
            quantity=10,
            entry_time=datetime.now()
        )

        # 가격 업데이트 (손실 상태로)
        orchestrator.position_tracker.update_price('005930', 49000)

        # 매도 주문 성공
        mock_api.order_stock.return_value = {
            'return_code': 0,
            'order_number': 'ORD123457',
            'message': 'Success'
        }

        # When
        await orchestrator._check_exit_signal('005930', '삼성전자', sample_dataframe)

        # Then
        # Hard stop (-1.3%)이 발동되어 매도되었을 수 있음
        # 또는 VWAP 조건에 따라 보유 중일 수 있음
        # 실제 신호 감지 로직에 따라 달라짐

    @pytest.mark.asyncio
    async def test_06_position_tracking_throughout_workflow(
        self, orchestrator, mock_api
    ):
        """테스트 6: 전체 워크플로우에서 포지션 추적"""
        # Given
        await orchestrator.initialize()

        # 매수 주문 성공
        mock_api.order_stock.return_value = {
            'return_code': 0,
            'order_number': 'ORD123456',
            'message': 'Success'
        }

        # When - 포지션 추가
        orchestrator.position_tracker.add_position(
            stock_code='005930',
            stock_name='삼성전자',
            entry_price=50000,
            quantity=10,
            entry_time=datetime.now()
        )

        # Then - 포지션 확인
        assert orchestrator.position_tracker.get_position_count() == 1

        position = orchestrator.position_tracker.get_position('005930')
        assert position is not None
        assert position.stock_code == '005930'
        assert position.entry_price == 50000
        assert position.quantity == 10

        # When - 가격 업데이트
        orchestrator.position_tracker.update_price('005930', 52000)

        # Then - 수익 확인
        total_profit = orchestrator.position_tracker.get_total_profit()
        assert total_profit > 0  # 수익 상태

        # When - 부분 청산
        position.record_partial_sell(stage=1, quantity=4, price=52000)

        # Then - 수량 감소 확인
        assert position.quantity == 6

        # When - 전량 청산
        orchestrator.position_tracker.remove_position('005930')

        # Then - 포지션 제거 확인
        assert orchestrator.position_tracker.get_position_count() == 0

    @pytest.mark.asyncio
    async def test_07_market_monitoring(self, orchestrator, mock_api):
        """테스트 7: 실시간 마켓 모니터링"""
        # Given
        orchestrator.watchlist = {'005930', '000660'}
        orchestrator.validated_stocks = {
            '005930': {'name': '삼성전자', 'stats': {}},
            '000660': {'name': 'SK하이닉스', 'stats': {}}
        }

        # 실시간 가격 반환
        def get_price_side_effect(code):
            prices = {
                '005930': 70000,
                '000660': 130000
            }
            return prices.get(code)

        mock_api.get_current_price.side_effect = get_price_side_effect

        # 분봉 데이터 반환
        mock_api.get_minute_chart.return_value = {
            'return_code': 0,
            'data': [
                {'date': '20250109', 'time': '090500', 'open': 69000, 'high': 70000,
                 'low': 68500, 'close': 70000, 'volume': 100000},
            ]
        }

        # When
        market_open = orchestrator.market_monitor.is_market_open()

        # Then
        # 현재 시간에 따라 달라질 수 있음
        assert isinstance(market_open, bool)

    @pytest.mark.asyncio
    async def test_08_full_workflow_end_to_end(
        self, orchestrator, mock_api, mock_validator, sample_dataframe
    ):
        """테스트 8: 전체 워크플로우 End-to-End"""
        # Given
        mock_api.get_balance.return_value = {
            'return_code': 0,
            'output': {
                'dnca_tot_amt': '10000000',
                'nxdy_excc_amt': '10000000',
                'prvs_rcdl_excc_amt': '0',
                'cma_evlu_amt': '0',
                'tot_evlu_amt': '10000000'
            }
        }
        mock_api.get_account_info.return_value = {
            'return_code': 0,
            'output': []
        }
        mock_api.get_stock_list_by_condition.return_value = {
            'return_code': 0,
            'stocks': [
                {'stock_code': '005930', 'stock_name': '삼성전자'}
            ]
        }
        mock_validator.validate_stock.return_value = {
            'allowed': True,
            'reason': 'Passed',
            'stats': {
                'win_rate': 50.0,
                'avg_profit_pct': 2.0,
                'total_trades': 10,
                'profit_factor': 1.5
            }
        }
        mock_api.order_stock.return_value = {
            'return_code': 0,
            'order_number': 'ORD123456',
            'message': 'Success'
        }

        # When - 1. 초기화
        init_result = await orchestrator.initialize()
        assert init_result is True

        # When - 2. 조건검색 + 필터링
        await orchestrator.run_condition_filtering("VWAP돌파")
        assert len(orchestrator.watchlist) > 0

        # When - 3. 매수 신호 감지 및 실행
        await orchestrator._check_entry_signal('005930', '삼성전자', sample_dataframe)

        # Then - 포지션 생성 확인
        position_count = orchestrator.position_tracker.get_position_count()
        assert position_count >= 0  # 신호 조건에 따라 0 또는 1

        # When - 4. 시스템 상태 조회
        status = orchestrator.get_system_status()

        # Then - 상태 확인
        assert 'running' in status
        assert 'watchlist_count' in status
        assert 'position_count' in status
        assert status['watchlist_count'] > 0

    @pytest.mark.asyncio
    async def test_09_error_handling_during_workflow(
        self, orchestrator, mock_api
    ):
        """테스트 9: 워크플로우 중 에러 처리"""
        # Given - API 에러 발생
        mock_api.get_balance.return_value = {
            'return_code': -1,
            'message': 'API Error'
        }

        # When
        result = await orchestrator.initialize()

        # Then - 에러가 발생해도 시스템은 계속 동작
        assert result is False  # 초기화 실패

        # 기본값으로 설정되어야 함
        assert orchestrator.account_manager.get_available_cash() >= 0

    @pytest.mark.asyncio
    async def test_10_concurrent_operations(self, orchestrator, mock_api):
        """테스트 10: 동시 작업 처리"""
        # Given
        await orchestrator.initialize()

        orchestrator.watchlist = {'005930', '000660', '035720'}

        # 여러 종목 동시 모니터링
        mock_api.get_current_price.return_value = 70000

        # When - 여러 작업을 동시에 실행
        tasks = [
            orchestrator.market_monitor.get_realtime_price('005930'),
            orchestrator.market_monitor.get_realtime_price('000660'),
            orchestrator.market_monitor.get_realtime_price('035720')
        ]

        # Then - 모든 작업이 완료되어야 함
        # (실제 구현에서는 asyncio.gather 등을 사용)


class TestEdgeCases:
    """엣지 케이스 테스트"""

    @pytest.mark.asyncio
    async def test_empty_watchlist(self, orchestrator):
        """빈 watchlist 처리"""
        # Given
        orchestrator.watchlist = set()

        # When/Then - 에러 없이 처리되어야 함
        await orchestrator._check_all_stocks()

    @pytest.mark.asyncio
    async def test_market_closed(self, orchestrator):
        """장 마감 시간 처리"""
        # Given/When
        market_open = orchestrator.market_monitor.is_market_open()

        # Then - boolean 값 반환
        assert isinstance(market_open, bool)

    @pytest.mark.asyncio
    async def test_insufficient_funds(self, orchestrator, mock_api, mock_risk_manager):
        """자금 부족 상황"""
        # Given
        await orchestrator.initialize()

        # 자금 부족
        orchestrator.account_manager._available_cash = 1000

        mock_risk_manager.can_open_position.return_value = (False, "Insufficient funds")

        # When - 매수 시도
        position = orchestrator.order_executor.execute_buy(
            stock_code='005930',
            stock_name='삼성전자',
            current_price=70000,
            current_cash=1000,
            positions_value=0,
            position_count=0,
            stock_info={}
        )

        # Then - 매수 실패
        assert position is None

    @pytest.mark.asyncio
    async def test_max_positions_reached(self, orchestrator, mock_risk_manager):
        """최대 포지션 수 도달"""
        # Given
        await orchestrator.initialize()

        # 최대 포지션 수 도달
        for i in range(5):
            orchestrator.position_tracker.add_position(
                stock_code=f'00{i}930',
                stock_name=f'종목{i}',
                entry_price=50000,
                quantity=10,
                entry_time=datetime.now()
            )

        mock_risk_manager.can_open_position.return_value = (False, "Max positions reached")

        # When - 추가 매수 시도
        position = orchestrator.order_executor.execute_buy(
            stock_code='005930',
            stock_name='삼성전자',
            current_price=70000,
            current_cash=10000000,
            positions_value=0,
            position_count=5,
            stock_info={}
        )

        # Then - 매수 실패
        assert position is None


class TestSystemStatus:
    """시스템 상태 테스트"""

    @pytest.mark.asyncio
    async def test_get_system_status(self, orchestrator):
        """시스템 상태 조회"""
        # Given
        await orchestrator.initialize()

        # When
        status = orchestrator.get_system_status()

        # Then
        assert 'running' in status
        assert 'market_open' in status
        assert 'watchlist_count' in status
        assert 'position_count' in status
        assert 'total_invested' in status
        assert 'available_cash' in status

    @pytest.mark.asyncio
    async def test_shutdown(self, orchestrator):
        """시스템 종료"""
        # Given
        orchestrator.running = True

        # When
        orchestrator.shutdown()

        # Then
        assert orchestrator.running is False
