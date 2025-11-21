"""
StockValidator 테스트
"""
import pytest
import pandas as pd
import numpy as np
from validators.stock_validator import StockValidator, ValidationResult


class TestStockValidator:
    """StockValidator 단위 테스트"""

    @pytest.fixture
    def validator(self):
        """Validator 인스턴스"""
        return StockValidator(verbose=False)

    @pytest.fixture
    def valid_dataframe(self):
        """유효한 DataFrame"""
        return pd.DataFrame({
            'open': [70000] * 150,
            'high': [71000] * 150,
            'low': [69500] * 150,
            'close': [70500] * 150,
            'volume': [10000] * 150
        })

    @pytest.mark.asyncio
    async def test_validate_valid_stock(self, validator, valid_dataframe):
        """유효한 주식 데이터 검증 통과"""
        # When
        result = await validator.validate_for_trading('005930', valid_dataframe)

        # Then
        assert result.is_valid is True
        assert result.reason is None
        assert result.metadata is not None
        assert result.metadata['data_points'] == 150

    @pytest.mark.asyncio
    async def test_validate_none_data(self, validator):
        """None 데이터 검증 실패"""
        # When
        result = await validator.validate_for_trading('005930', None)

        # Then
        assert result.is_valid is False
        assert '데이터 없음' in result.reason

    @pytest.mark.asyncio
    async def test_validate_empty_dataframe(self, validator):
        """빈 DataFrame 검증 실패"""
        # Given
        empty_df = pd.DataFrame()

        # When
        result = await validator.validate_for_trading('005930', empty_df)

        # Then
        assert result.is_valid is False

    @pytest.mark.asyncio
    async def test_validate_missing_columns(self, validator):
        """필수 컬럼 누락 검증 실패"""
        # Given
        df = pd.DataFrame({
            'close': [70000],
            # volume 누락
        })

        # When
        result = await validator.validate_for_trading('005930', df)

        # Then
        assert result.is_valid is False
        assert '필수 컬럼 누락' in result.reason

    @pytest.mark.asyncio
    async def test_validate_insufficient_data(self, validator):
        """데이터 부족 검증 실패"""
        # Given
        df = pd.DataFrame({
            'open': [70000] * 50,
            'high': [71000] * 50,
            'low': [69500] * 50,
            'close': [70500] * 50,
            'volume': [10000] * 50
        })

        # When
        result = await validator.validate_for_trading('005930', df)

        # Then
        assert result.is_valid is False
        assert '데이터 부족' in result.reason

    @pytest.mark.asyncio
    async def test_validate_low_volume(self, validator):
        """거래량 부족 검증 실패"""
        # Given
        df = pd.DataFrame({
            'open': [70000] * 150,
            'high': [71000] * 150,
            'low': [69500] * 150,
            'close': [70500] * 150,
            'volume': [100] * 150  # 매우 낮은 거래량
        })

        # When
        result = await validator.validate_for_trading('005930', df)

        # Then
        assert result.is_valid is False
        assert '거래량 부족' in result.reason

    @pytest.mark.asyncio
    async def test_validate_negative_prices(self, validator):
        """음수 가격 검증 실패"""
        # Given
        df = pd.DataFrame({
            'open': [70000] * 150,
            'high': [71000] * 150,
            'low': [69500] * 150,
            'close': [-1000] * 150,  # 음수 가격
            'volume': [10000] * 150
        })

        # When
        result = await validator.validate_for_trading('005930', df)

        # Then
        assert result.is_valid is False
        assert '비정상 가격' in result.reason

    @pytest.mark.asyncio
    async def test_validate_ohlc_logic_error(self, validator):
        """OHLC 논리 오류 검증 실패"""
        # Given
        df = pd.DataFrame({
            'open': [70000] * 150,
            'high': [69000] * 150,  # High < Low (논리 오류)
            'low': [71000] * 150,
            'close': [70500] * 150,
            'volume': [10000] * 150
        })

        # When
        result = await validator.validate_for_trading('005930', df)

        # Then
        assert result.is_valid is False
        assert 'OHLC 논리 오류' in result.reason

    @pytest.mark.asyncio
    async def test_validate_nan_values(self, validator):
        """NaN 값 검증 실패"""
        # Given
        df = pd.DataFrame({
            'open': [70000] * 150,
            'high': [71000] * 150,
            'low': [69500] * 150,
            'close': [70500] * 148 + [np.nan, np.nan],  # NaN 포함
            'volume': [10000] * 150
        })

        # When
        result = await validator.validate_for_trading('005930', df)

        # Then
        assert result.is_valid is False
        assert 'NaN 값 존재' in result.reason

    @pytest.mark.asyncio
    async def test_validate_batch(self, validator, valid_dataframe):
        """여러 종목 일괄 검증"""
        # Given
        invalid_df = pd.DataFrame({'close': [70000] * 50})  # 데이터 부족
        stocks = {
            '005930': valid_dataframe,
            '000660': invalid_df
        }

        # When
        results = await validator.validate_batch(stocks)

        # Then
        assert len(results) == 2
        assert results['005930'].is_valid is True
        assert results['000660'].is_valid is False

    def test_quick_validate_success(self, validator, valid_dataframe):
        """빠른 검증 성공"""
        # When
        result = validator.quick_validate(valid_dataframe)

        # Then
        assert result is True

    def test_quick_validate_failure(self, validator):
        """빠른 검증 실패"""
        # Given
        df = pd.DataFrame({'close': [70000] * 5})  # 행 수 부족

        # When
        result = validator.quick_validate(df, min_rows=10)

        # Then
        assert result is False

    def test_validation_result_bool_context(self):
        """ValidationResult Boolean 컨텍스트"""
        # Given
        valid_result = ValidationResult(is_valid=True)
        invalid_result = ValidationResult(is_valid=False)

        # Then
        assert bool(valid_result) is True
        assert bool(invalid_result) is False

    def test_custom_config(self):
        """커스텀 설정 적용"""
        # Given
        config = {
            'min_data_points': 200,
            'min_volume': 5000
        }
        validator = StockValidator(config=config, verbose=False)

        # Then
        assert validator.min_data_points == 200
        assert validator.min_volume == 5000
