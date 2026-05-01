"""
데이터 로더 — yfinance에서 일봉 OHLCV 다운로드
"""
import pandas as pd
import yfinance as yf
import logging

logger = logging.getLogger(__name__)

SUFFIX_MAP = {
    'KOSPI':  '.KS',
    'KOSDAQ': '.KQ',
}

KOSPI_CODES = {'005930', '000660', '035420', '373220', '006400', '051910',
               '005380', '000270', '105560', '055550'}


def load_daily(code: str, start: str, end: str) -> pd.DataFrame:
    """
    yfinance에서 일봉 데이터 로드.

    Returns:
        columns: open, high, low, close, volume (소문자)
        index: DatetimeIndex
    """
    suffix = '.KS' if code in KOSPI_CODES else '.KQ'
    ticker = f'{code}{suffix}'

    df = yf.download(ticker, start=start, end=end,
                     interval='1d', progress=False, auto_adjust=True)

    if df.empty:
        # 반대 suffix 시도
        alt = '.KQ' if suffix == '.KS' else '.KS'
        df = yf.download(f'{code}{alt}', start=start, end=end,
                         interval='1d', progress=False, auto_adjust=True)

    if df.empty:
        logger.warning(f'[LOADER] {code}: 데이터 없음')
        return pd.DataFrame()

    # MultiIndex 컬럼 처리 (yfinance 최신 버전)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    df.columns = [c.lower() for c in df.columns]
    df = df[['open', 'high', 'low', 'close', 'volume']].dropna()
    df.index = pd.to_datetime(df.index)

    logger.info(f'[LOADER] {code}{suffix}: {len(df)}봉 ({df.index[0].date()} ~ {df.index[-1].date()})')
    return df


def load_multi(codes: list[str], start: str, end: str) -> dict[str, pd.DataFrame]:
    """여러 종목 일괄 로드."""
    result = {}
    for code in codes:
        df = load_daily(code, start, end)
        if not df.empty:
            result[code] = df
    return result
