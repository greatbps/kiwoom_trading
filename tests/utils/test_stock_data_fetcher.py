"""
StockDataFetcher 테스트
"""
import pytest
import pandas as pd
from unittest.mock import AsyncMock, MagicMock, patch
from utils.stock_data_fetcher import StockDataFetcher, download_stock_data_sync


class TestStockDataFetcher:
    """StockDataFetcher 단위 테스트"""

    @pytest.fixture
    def mock_kiwoom_api(self):
        """Mock Kiwoom API"""
        api = MagicMock()
        api.get_minute_chart = AsyncMock(return_value={
            'stk_min_pole_chart_qry': [
                {
                    'stck_bsop_date': '20251108',
                    'stck_cntg_hour': '090000',
                    'stck_oprc': '70000',
                    'stck_hgpr': '71000',
                    'stck_lwpr': '69500',
                    'stck_prpr': '70500',
                    'cntg_vol': '1000'
                }
            ]
        })
        return api

    @pytest.fixture
    def fetcher(self, mock_kiwoom_api):
        """Fetcher 인스턴스"""
        return StockDataFetcher(kiwoom_api=mock_kiwoom_api, verbose=False)

    @pytest.fixture
    def sample_dataframe(self):
        """샘플 DataFrame"""
        return pd.DataFrame({
            'open': [70000, 70500, 71000],
            'high': [71000, 71500, 72000],
            'low': [69500, 70000, 70500],
            'close': [70500, 71000, 71500],
            'volume': [1000, 1500, 2000]
        })

    @pytest.mark.asyncio
    async def test_fetch_from_yahoo_success(self, fetcher):
        """Yahoo Finance에서 데이터 수집 성공"""
        # Given
        stock_code = '005930'

        with patch.object(fetcher, '_download_yahoo_sync') as mock_download:
            # Mock DataFrame 반환
            mock_df = pd.DataFrame({
                'close': [70000, 70500, 71000],
                'volume': [1000, 1500, 2000]
            })
            mock_download.return_value = mock_df

            # When
            result = await fetcher.fetch(stock_code, days=7, source='yahoo')

            # Then
            assert result is not None
            assert len(result) == 3
            assert 'close' in result.columns

    @pytest.mark.asyncio
    async def test_fetch_from_yahoo_fallback_to_kq(self, fetcher):
        """Yahoo Finance .KS 실패 시 .KQ로 fallback"""
        # Given
        stock_code = '005930'

        with patch.object(fetcher, '_download_yahoo_sync') as mock_download:
            # .KS는 None, .KQ는 성공
            mock_df = pd.DataFrame({
                'close': [70000],
                'volume': [1000]
            })
            mock_download.side_effect = [None, mock_df]

            # When
            result = await fetcher._fetch_from_yahoo(stock_code, 7, '5m')

            # Then
            assert result is not None
            assert mock_download.call_count == 2

    @pytest.mark.asyncio
    async def test_fetch_auto_prefers_kiwoom(self, fetcher):
        """Auto 모드에서 Kiwoom API 우선 사용"""
        # Given
        stock_code = '005930'

        with patch.object(fetcher, '_fetch_from_kiwoom') as mock_kiwoom:
            mock_df = pd.DataFrame({'close': [70000], 'volume': [1000]})
            mock_kiwoom.return_value = mock_df

            # When
            result = await fetcher.fetch(stock_code, source='auto')

            # Then
            assert result is not None
            assert mock_kiwoom.called

    @pytest.mark.asyncio
    async def test_clean_price_data_removes_invalid(self, fetcher):
        """음수/0 가격 데이터 제거"""
        # Given
        df = pd.DataFrame({
            'close': [70000, 0, -1000, 71000],
            'volume': [1000, 1000, 1000, 1000]
        })

        # When
        cleaned = fetcher._clean_price_data(df, '005930')

        # Then
        assert len(cleaned) == 2  # 유효한 데이터만 남음
        assert (cleaned['close'] > 0).all()

    def test_fetch_sync(self, fetcher):
        """동기 버전 fetch 테스트"""
        # Given
        stock_code = '005930'

        with patch.object(fetcher, 'fetch') as mock_fetch:
            mock_df = pd.DataFrame({'close': [70000]})

            # asyncio.run을 mock
            with patch('asyncio.run') as mock_run:
                mock_run.return_value = mock_df

                # When
                result = fetcher.fetch_sync(stock_code)

                # Then
                assert mock_run.called


class TestLegacyFunctions:
    """레거시 함수 테스트"""

    @patch('yfinance.Ticker')
    def test_download_stock_data_sync_success(self, mock_ticker):
        """download_stock_data_sync 성공"""
        # Given
        mock_history = pd.DataFrame({
            'Open': [70000],
            'High': [71000],
            'Low': [69500],
            'Close': [70500],
            'Volume': [1000]
        })
        mock_ticker.return_value.history.return_value = mock_history

        # When
        result = download_stock_data_sync('005930.KS', days=7)

        # Then
        assert result is not None
        assert 'close' in result.columns  # 소문자 변환 확인

    @patch('yfinance.Ticker')
    def test_download_stock_data_sync_filters_negative_prices(self, mock_ticker):
        """음수 가격 필터링"""
        # Given
        mock_history = pd.DataFrame({
            'Close': [70000, -1000, 0, 71000],
            'Volume': [1000, 1000, 1000, 1000]
        })
        mock_ticker.return_value.history.return_value = mock_history

        # When
        result = download_stock_data_sync('005930.KS', days=7)

        # Then
        assert result is not None
        assert len(result) == 2  # 음수/0 제거
        assert (result['close'] > 0).all()

    @patch('yfinance.Ticker')
    def test_download_stock_data_sync_returns_none_on_error(self, mock_ticker):
        """오류 시 None 반환"""
        # Given
        mock_ticker.side_effect = Exception("API Error")

        # When
        result = download_stock_data_sync('005930.KS')

        # Then
        assert result is None
