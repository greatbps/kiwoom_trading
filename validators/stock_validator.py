"""
주식 데이터 검증 모듈

중복 코드 제거:
- main_auto_trading.py의 validate_stock_for_trading
- main_condition_filter.py의 유사 검증 로직
"""
from dataclasses import dataclass
from typing import Optional, Dict, Any
import pandas as pd
from rich.console import Console

console = Console()


@dataclass
class ValidationResult:
    """검증 결과"""
    is_valid: bool
    reason: Optional[str] = None
    data: Optional[pd.DataFrame] = None
    metadata: Optional[Dict[str, Any]] = None

    def __bool__(self):
        """Boolean 컨텍스트에서 사용 가능"""
        return self.is_valid


class StockValidator:
    """주식 거래 검증 클래스"""

    def __init__(self, config: Optional[Dict[str, Any]] = None, verbose: bool = True):
        """
        Args:
            config: 검증 설정
                - min_data_points: 최소 데이터 포인트 (기본: 100)
                - min_volume: 최소 평균 거래량 (기본: 1000)
                - min_price: 최소 가격 (기본: 100)
                - max_price: 최대 가격 (기본: 1000000)
            verbose: 로그 출력 여부
        """
        self.config = config or {}
        self.verbose = verbose

        # 기본 설정
        self.min_data_points = self.config.get('min_data_points', 100)
        self.min_volume = self.config.get('min_volume', 1000)
        self.min_price = self.config.get('min_price', 100)
        self.max_price = self.config.get('max_price', 1000000)

    def _log(self, message: str, style: str = "yellow"):
        """로그 출력"""
        if self.verbose:
            console.print(f"[{style}]{message}[/{style}]")

    async def validate_for_trading(
        self,
        stock_code: str,
        data: pd.DataFrame
    ) -> ValidationResult:
        """
        거래 가능 여부 검증

        검증 항목:
        1. 데이터 충분성 (최소 데이터 포인트)
        2. 거래량 충족
        3. 가격 이상치 확인
        4. 필수 컬럼 존재
        5. VWAP 계산 가능성

        Args:
            stock_code: 종목 코드
            data: OHLCV 데이터

        Returns:
            ValidationResult

        Example:
            >>> validator = StockValidator()
            >>> result = await validator.validate_for_trading('005930', df)
            >>> if result.is_valid:
            >>>     print("검증 통과!")
        """
        # 1. None/Empty 체크
        if data is None or data.empty:
            return ValidationResult(
                is_valid=False,
                reason=f"{stock_code}: 데이터 없음"
            )

        # 2. 필수 컬럼 체크
        required_columns = ['open', 'high', 'low', 'close', 'volume']
        missing_columns = [col for col in required_columns if col not in data.columns]

        if missing_columns:
            return ValidationResult(
                is_valid=False,
                reason=f"{stock_code}: 필수 컬럼 누락 - {missing_columns}"
            )

        # 3. 데이터 충분성 체크
        if len(data) < self.min_data_points:
            return ValidationResult(
                is_valid=False,
                reason=f"{stock_code}: 데이터 부족 ({len(data)} < {self.min_data_points})"
            )

        # 4. 거래량 체크
        avg_volume = data['volume'].mean()
        if avg_volume < self.min_volume:
            return ValidationResult(
                is_valid=False,
                reason=f"{stock_code}: 거래량 부족 (평균 {avg_volume:.0f} < {self.min_volume})"
            )

        # 5. 가격 이상치 체크
        # 5-1. 음수/0 가격
        if (data['close'] <= 0).any():
            invalid_count = (data['close'] <= 0).sum()
            return ValidationResult(
                is_valid=False,
                reason=f"{stock_code}: 비정상 가격 {invalid_count}개 발견"
            )

        # 5-2. 가격 범위
        min_close = data['close'].min()
        max_close = data['close'].max()

        if min_close < self.min_price:
            return ValidationResult(
                is_valid=False,
                reason=f"{stock_code}: 최소 가격 미달 ({min_close:.0f} < {self.min_price})"
            )

        if max_close > self.max_price:
            return ValidationResult(
                is_valid=False,
                reason=f"{stock_code}: 최대 가격 초과 ({max_close:.0f} > {self.max_price})"
            )

        # 6. OHLC 논리 체크
        ohlc_valid = (
            (data['high'] >= data['low']) &
            (data['high'] >= data['open']) &
            (data['high'] >= data['close']) &
            (data['low'] <= data['open']) &
            (data['low'] <= data['close'])
        ).all()

        if not ohlc_valid:
            return ValidationResult(
                is_valid=False,
                reason=f"{stock_code}: OHLC 논리 오류"
            )

        # 7. NaN 체크
        if data[required_columns].isnull().any().any():
            nan_columns = data[required_columns].isnull().sum()
            nan_columns = nan_columns[nan_columns > 0].to_dict()
            return ValidationResult(
                is_valid=False,
                reason=f"{stock_code}: NaN 값 존재 - {nan_columns}"
            )

        # ✅ 모든 검증 통과
        metadata = {
            'data_points': len(data),
            'avg_volume': avg_volume,
            'price_range': (min_close, max_close),
            'date_range': (data.index[0], data.index[-1]) if hasattr(data.index, 'min') else None
        }

        return ValidationResult(
            is_valid=True,
            reason=None,
            data=data,
            metadata=metadata
        )

    async def validate_batch(
        self,
        stocks: Dict[str, pd.DataFrame]
    ) -> Dict[str, ValidationResult]:
        """
        여러 종목 일괄 검증

        Args:
            stocks: {stock_code: DataFrame}

        Returns:
            {stock_code: ValidationResult}

        Example:
            >>> validator = StockValidator()
            >>> stocks = {'005930': df1, '000660': df2}
            >>> results = await validator.validate_batch(stocks)
            >>> valid_stocks = {k: v for k, v in results.items() if v.is_valid}
        """
        results = {}

        for stock_code, data in stocks.items():
            result = await self.validate_for_trading(stock_code, data)
            results[stock_code] = result

            # 로그 출력
            if result.is_valid:
                self._log(f"✓ {stock_code}: 검증 통과", "green")
            else:
                self._log(f"✗ {stock_code}: {result.reason}", "red")

        return results

    def quick_validate(
        self,
        data: pd.DataFrame,
        min_rows: int = 10
    ) -> bool:
        """
        빠른 검증 (최소한의 체크)

        Args:
            data: DataFrame
            min_rows: 최소 행 수

        Returns:
            True if valid

        Example:
            >>> validator = StockValidator()
            >>> if validator.quick_validate(df):
            >>>     print("기본 검증 통과")
        """
        if data is None or data.empty:
            return False

        if len(data) < min_rows:
            return False

        required = ['close', 'volume']
        if not all(col in data.columns for col in required):
            return False

        if (data['close'] <= 0).any():
            return False

        return True
