"""
진입 타이밍 분석기 (Entry Timing Analyzer)

VWAP 기반 매수/매도 시그널 생성 + 트레일링 스탑
- 5분봉 데이터 사용
- VWAP 상향 돌파 → 매수 시그널
- 트레일링 스탑 → 수익 보호 청산
- VWAP 하향 돌파 → 비상 탈출
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Optional, Tuple
from datetime import datetime, timedelta


class EntryTimingAnalyzer:
    """VWAP 기반 진입 타이밍 분석 + 트레일링 스탑"""

    def __init__(
        self,
        trailing_activation_pct: float = 1.5,  # 트레일링 활성화 수익률 (%)
        trailing_ratio: float = 1.0,           # 트레일링 비율 (%)
        stop_loss_pct: float = 1.0,            # 기본 손절 (%)
        breakout_confirm_candles: int = 2,     # 돌파 지속 확인 캔들 수
        min_volume_value: float = 1000000000,  # 최소 거래대금 (10억원)
        re_entry_cooldown_minutes: int = 30,   # 재진입 대기 시간 (분)
        avoid_early_minutes: int = 10,         # 장 시작 후 회피 시간 (분)
        avoid_late_minutes: int = 10,          # 장 마감 전 회피 시간 (분)
        profit_tier_trailing_ratio: float = 0.5  # 목표가 도달 후 강화된 트레일링 비율
    ):
        """
        초기화

        Args:
            trailing_activation_pct: 트레일링 스탑 활성화 기준 수익률 (기본 1.5%)
            trailing_ratio: 고가 대비 트레일링 비율 (기본 1.0%)
            stop_loss_pct: 기본 손절 비율 (기본 1.0%)
            breakout_confirm_candles: VWAP 돌파 지속 확인 캔들 수 (기본 2)
            min_volume_value: 최소 거래대금 (기본 10억원)
            re_entry_cooldown_minutes: 재진입 대기 시간 (기본 30분)
            avoid_early_minutes: 장 시작 후 회피 시간 (기본 10분)
            avoid_late_minutes: 장 마감 전 회피 시간 (기본 10분)
            profit_tier_trailing_ratio: 목표가 도달 후 강화된 트레일링 비율 (기본 0.5%)
        """
        self.trailing_activation_pct = trailing_activation_pct
        self.trailing_ratio = trailing_ratio
        self.stop_loss_pct = stop_loss_pct
        self.breakout_confirm_candles = breakout_confirm_candles
        self.min_volume_value = min_volume_value
        self.re_entry_cooldown_minutes = re_entry_cooldown_minutes
        self.avoid_early_minutes = avoid_early_minutes
        self.avoid_late_minutes = avoid_late_minutes
        self.profit_tier_trailing_ratio = profit_tier_trailing_ratio

        # 종목별 마지막 청산 시간 추적
        self.last_exit_times: Dict[str, datetime] = {}

    def calculate_vwap(self, df: pd.DataFrame, use_rolling: bool = True, rolling_window: int = 20) -> pd.DataFrame:
        """
        VWAP (Volume Weighted Average Price) 계산

        Args:
            df: OHLCV 데이터프레임
                - 필수 컬럼: 'high', 'low', 'close', 'volume'
                - 선택 컬럼: 'date' 또는 'dt' (일자, 누적 VWAP 시 사용)
            use_rolling: True = 이동 VWAP, False = 누적 VWAP (일자별 리셋)
            rolling_window: 이동 VWAP 윈도우 (봉 개수, 기본 20봉)

        Returns:
            VWAP 컬럼이 추가된 데이터프레임
        """
        # Typical Price 계산: (고가 + 저가 + 종가) / 3
        df['typical_price'] = (df['high'] + df['low'] + df['close']) / 3

        # Price × Volume
        df['pv'] = df['typical_price'] * df['volume']

        if use_rolling:
            # ===== 이동 VWAP (Rolling VWAP) =====
            df['rolling_pv'] = df['pv'].rolling(window=rolling_window).sum()
            df['rolling_volume'] = df['volume'].rolling(window=rolling_window).sum()
            df['vwap'] = df['rolling_pv'] / df['rolling_volume']

            # 임시 컬럼 삭제
            df.drop(columns=['typical_price', 'pv', 'rolling_pv', 'rolling_volume'], inplace=True)
        else:
            # ===== 누적 VWAP (Cumulative VWAP, 일자별 리셋) =====
            # 일자 컬럼 확인
            date_column = None
            if 'date' in df.columns:
                date_column = 'date'
            elif 'dt' in df.columns:
                date_column = 'dt'
                # dt 컬럼을 날짜로 변환 (YYYYMMDD 형식)
                df['date'] = pd.to_datetime(df['dt'], format='%Y%m%d', errors='coerce')
                date_column = 'date'

            # 일자별 누적 합계 계산
            if date_column and not df[date_column].isna().all():
                # 날짜별로 그룹화하여 누적 합계
                df['cumulative_pv'] = df.groupby(df[date_column].dt.date if hasattr(df[date_column], 'dt') else df[date_column])['pv'].cumsum()
                df['cumulative_volume'] = df.groupby(df[date_column].dt.date if hasattr(df[date_column], 'dt') else df[date_column])['volume'].cumsum()
            else:
                # 일자 정보가 없으면 전체 누적 (단순 VWAP)
                df['cumulative_pv'] = df['pv'].cumsum()
                df['cumulative_volume'] = df['volume'].cumsum()

            # VWAP 계산: Cumulative (P×V) / Cumulative (V)
            df['vwap'] = df['cumulative_pv'] / df['cumulative_volume']

            # 임시 컬럼 삭제
            df.drop(columns=['typical_price', 'pv', 'cumulative_pv', 'cumulative_volume'], inplace=True)

        return df

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        RSI (Relative Strength Index) 계산

        Args:
            df: OHLC 데이터프레임
            period: RSI 계산 기간 (기본 14)

        Returns:
            RSI 컬럼이 추가된 데이터프레임
        """
        # 가격 변화량
        delta = df['close'].diff()

        # 상승/하락 분리
        gain = delta.where(delta > 0, 0)
        loss = -delta.where(delta < 0, 0)

        # 평균 상승/하락
        avg_gain = gain.ewm(span=period, adjust=False).mean()
        avg_loss = loss.ewm(span=period, adjust=False).mean()

        # RS = 평균 상승 / 평균 하락
        rs = avg_gain / avg_loss

        # RSI = 100 - (100 / (1 + RS))
        df['rsi'] = 100 - (100 / (1 + rs))

        return df

    def calculate_williams_r(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        Williams %R 계산 (과매수/과매도 지표)

        Args:
            df: OHLC 데이터프레임
            period: Williams %R 계산 기간 (기본 14)

        Returns:
            Williams %R 컬럼이 추가된 데이터프레임

        Williams %R:
            - 범위: -100 ~ 0
            - %R > -20: 과매수 (Overbought)
            - %R < -80: 과매도 (Oversold)
            - 계산식: -100 * (HH - Close) / (HH - LL)
        """
        import numpy as np

        # 최고가/최저가 (period 기간)
        highest_high = df['high'].rolling(window=period, min_periods=1).max()
        lowest_low = df['low'].rolling(window=period, min_periods=1).min()

        # Williams %R 계산
        denom = (highest_high - lowest_low).replace(0, np.nan)
        williams_r = -100.0 * (highest_high - df['close']) / denom

        # NaN 처리 및 범위 제한
        williams_r = williams_r.replace([np.inf, -np.inf], np.nan)
        williams_r = williams_r.fillna(method='ffill').fillna(-50.0)
        williams_r = williams_r.clip(-100.0, 0.0)

        df[f'williams_r_{period}'] = williams_r

        return df

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.DataFrame:
        """
        ATR (Average True Range) 계산

        Args:
            df: OHLC 데이터프레임
            period: ATR 계산 기간 (기본 14)

        Returns:
            ATR 컬럼이 추가된 데이터프레임
        """
        # True Range 계산
        df['h-l'] = df['high'] - df['low']
        df['h-pc'] = abs(df['high'] - df['close'].shift(1))
        df['l-pc'] = abs(df['low'] - df['close'].shift(1))

        df['tr'] = df[['h-l', 'h-pc', 'l-pc']].max(axis=1)

        # ATR = EMA of True Range
        df['atr'] = df['tr'].ewm(span=period, adjust=False).mean()

        # 임시 컬럼 삭제
        df.drop(columns=['h-l', 'h-pc', 'l-pc', 'tr'], inplace=True)

        return df

    def confirm_breakout(self, df: pd.DataFrame, index: int) -> bool:
        """
        VWAP 돌파 지속성 확인 (페이크 브레이크 방지)

        Args:
            df: 데이터프레임
            index: 체크할 인덱스

        Returns:
            지속성 확인 여부 (True: 유지됨, False: 가짜 돌파)
        """
        # 최근 n개 캔들 확인
        start_idx = max(0, index - self.breakout_confirm_candles + 1)
        recent = df.iloc[start_idx:index + 1]

        if len(recent) < self.breakout_confirm_candles:
            return False

        # 모든 캔들이 VWAP 위에서 유지되는지 확인
        above_vwap = (recent['close'] > recent['vwap']).all()
        return above_vwap

    def check_volume_value(self, df: pd.DataFrame, index: int) -> bool:
        """
        거래대금 절대값 체크 (유동성 확인)

        Args:
            df: 데이터프레임
            index: 체크할 인덱스

        Returns:
            거래대금 충족 여부
        """
        row = df.iloc[index]

        # 거래대금 = 현재가 × 거래량
        volume_value = row['close'] * row['volume']

        return volume_value >= self.min_volume_value

    def check_market_momentum(self, market_data: Optional[pd.DataFrame]) -> bool:
        """
        시장 모멘텀 체크 (코스피/나스닥 등)

        Args:
            market_data: 시장 지수 데이터 (OHLCV + VWAP)

        Returns:
            시장 상승 여부 (True: 상승장, False: 하락장)
        """
        if market_data is None or market_data.empty:
            return True  # 시장 데이터 없으면 통과

        latest = market_data.iloc[-1]

        # 시장 종가가 VWAP 위에 있는지 확인
        if 'vwap' in market_data.columns:
            return latest['close'] > latest['vwap']

        # VWAP 없으면 MA20 기준
        if 'ma' in market_data.columns or len(market_data) >= 20:
            if 'ma' not in market_data.columns:
                market_data['ma'] = market_data['close'].rolling(20).mean()
            return latest['close'] > market_data['ma'].iloc[-1]

        return True  # 판단 불가 시 통과

    def check_re_entry_allowed(
        self,
        stock_code: str,
        current_time: Optional[datetime] = None
    ) -> Tuple[bool, str]:
        """
        재진입 가능 여부 체크 (중복 진입 방지)

        Args:
            stock_code: 종목코드
            current_time: 현재 시간 (None이면 현재 시각 사용)

        Returns:
            (allowed, reason)
        """
        if current_time is None:
            current_time = datetime.now()

        # 마지막 청산 시간 확인
        if stock_code not in self.last_exit_times:
            return True, ""  # 첫 진입

        last_exit = self.last_exit_times[stock_code]
        time_diff = (current_time - last_exit).total_seconds() / 60  # 분 단위

        if time_diff < self.re_entry_cooldown_minutes:
            return False, f"재진입 대기 중 ({int(time_diff)}/{self.re_entry_cooldown_minutes}분)"

        return True, ""

    def record_exit(self, stock_code: str, exit_time: Optional[datetime] = None):
        """
        청산 시간 기록

        Args:
            stock_code: 종목코드
            exit_time: 청산 시간 (None이면 현재 시각)
        """
        if exit_time is None:
            exit_time = datetime.now()

        self.last_exit_times[stock_code] = exit_time

    def check_time_filter(
        self,
        current_time: datetime,
        market_open: str = "09:00",
        market_close: str = "15:20"
    ) -> Tuple[bool, str]:
        """
        시간 필터 체크 (장 초반/막판 회피)

        Args:
            current_time: 현재 시간
            market_open: 장 시작 시간 (HH:MM)
            market_close: 장 마감 시간 (HH:MM)

        Returns:
            (allowed, reason)
        """
        # pandas Timestamp를 datetime으로 변환 (타임존 제거)
        if hasattr(current_time, 'tz_localize'):
            current_time = current_time.tz_localize(None)
        if hasattr(current_time, 'to_pydatetime'):
            current_time = current_time.to_pydatetime().replace(tzinfo=None)

        current_hhmm = current_time.strftime("%H:%M")

        # 장 시작 시간 계산
        open_hour, open_min = map(int, market_open.split(":"))
        avoid_until = datetime(
            current_time.year,
            current_time.month,
            current_time.day,
            open_hour,
            open_min
        ) + timedelta(minutes=self.avoid_early_minutes)

        # 장 마감 시간 계산
        close_hour, close_min = map(int, market_close.split(":"))
        avoid_from = datetime(
            current_time.year,
            current_time.month,
            current_time.day,
            close_hour,
            close_min
        ) - timedelta(minutes=self.avoid_late_minutes)

        # 장 초반 회피
        if current_time < avoid_until:
            return False, f"장 초반 회피 중 ({current_hhmm} < {avoid_until.strftime('%H:%M')})"

        # 장 막판 회피
        if current_time > avoid_from:
            return False, f"장 막판 회피 중 ({current_hhmm} > {avoid_from.strftime('%H:%M')})"

        return True, ""

    def check_volatility_filter(
        self,
        df: pd.DataFrame,
        index: int,
        min_atr_pct: float = 0.5,
        max_atr_pct: float = 5.0
    ) -> Tuple[bool, str]:
        """
        변동성 필터 체크 (ATR 기반)

        Args:
            df: 데이터프레임 (ATR 컬럼 포함)
            index: 체크할 인덱스
            min_atr_pct: 최소 ATR (가격 대비 %, 기본 0.5%)
            max_atr_pct: 최대 ATR (가격 대비 %, 기본 5.0%)

        Returns:
            (allowed, reason)
        """
        if 'atr' not in df.columns:
            return True, ""  # ATR 없으면 통과

        row = df.iloc[index]
        atr = row['atr']
        price = row['close']

        if pd.isna(atr) or pd.isna(price) or price == 0:
            return True, ""  # 데이터 부족 시 통과

        # ATR을 가격 대비 퍼센트로 변환
        atr_pct = (atr / price) * 100

        # 너무 변동성이 작은 종목 (데드 존)
        if atr_pct < min_atr_pct:
            return False, f"변동성 부족 (ATR {atr_pct:.2f}% < {min_atr_pct}%)"

        # 너무 변동성이 큰 종목 (위험)
        if atr_pct > max_atr_pct:
            return False, f"변동성 과다 (ATR {atr_pct:.2f}% > {max_atr_pct}%)"

        return True, ""

    def check_partial_exit(
        self,
        current_price: float,
        avg_price: float,
        current_quantity: int,
        exit_tiers: List[Dict[str, float]],
        executed_tiers: Optional[List[int]] = None
    ) -> Tuple[bool, int, str, List[int]]:
        """
        부분 청산 체크

        Args:
            current_price: 현재가
            avg_price: 평균 매수가
            current_quantity: 현재 보유 수량
            exit_tiers: 청산 티어 리스트
                        [{'profit_pct': 1.5, 'exit_ratio': 0.5}, ...]
            executed_tiers: 이미 실행된 티어 인덱스 리스트

        Returns:
            (should_exit, exit_quantity, reason, new_executed_tiers)
        """
        if executed_tiers is None:
            executed_tiers = []

        profit_rate = ((current_price - avg_price) / avg_price) * 100

        # 각 티어 체크
        for tier_idx, tier in enumerate(exit_tiers):
            # 이미 실행된 티어는 스킵
            if tier_idx in executed_tiers:
                continue

            target_profit = tier['profit_pct']
            exit_ratio = tier['exit_ratio']

            # 목표 수익률 도달 시 부분 청산
            if profit_rate >= target_profit:
                exit_quantity = int(current_quantity * exit_ratio)

                if exit_quantity > 0:
                    new_executed_tiers = executed_tiers + [tier_idx]
                    reason = f"부분 청산 Tier {tier_idx + 1} ({profit_rate:+.2f}% 도달, {int(exit_ratio * 100)}% 청산)"
                    return True, exit_quantity, reason, new_executed_tiers

        return False, 0, "", executed_tiers

    def check_daily_trend_strength(
        self,
        daily_data: pd.DataFrame,
        use_ema_alignment: bool = True,
        use_rsi_filter: bool = True,
        rsi_threshold: float = 50.0
    ) -> bool:
        """
        일봉 추세 강도 체크 (EMA 정배열 + RSI)

        Args:
            daily_data: 일봉 데이터 (OHLCV)
            use_ema_alignment: EMA 정배열 체크 (기본 True)
            use_rsi_filter: RSI 필터 사용 (기본 True)
            rsi_threshold: RSI 최소값 (기본 50)

        Returns:
            추세 강도 충족 여부 (True: 강한 상승, False: 약한 상승 or 하락)
        """
        if daily_data is None or daily_data.empty or len(daily_data) < 50:
            return True  # 데이터 부족 시 통과

        df = daily_data.copy()
        latest = df.iloc[-1]

        # 1. EMA 정배열 체크 (EMA20 > EMA50)
        if use_ema_alignment:
            if 'ema20' not in df.columns:
                df['ema20'] = df['close'].ewm(span=20, adjust=False).mean()
            if 'ema50' not in df.columns:
                df['ema50'] = df['close'].ewm(span=50, adjust=False).mean()

            ema20 = df['ema20'].iloc[-1]
            ema50 = df['ema50'].iloc[-1]

            # 정배열: EMA20 > EMA50
            if ema20 <= ema50:
                return False

        # 2. RSI 필터 (과매수/과매도 제외)
        if use_rsi_filter:
            if 'rsi' not in df.columns:
                df = self.calculate_rsi(df)

            rsi = df['rsi'].iloc[-1]

            # RSI가 threshold 이상이어야 함 (상승 모멘텀)
            if pd.isna(rsi) or rsi < rsi_threshold:
                return False

            # RSI 과매수 (70 이상) 제외 - 고점 진입 방지
            if rsi > 70:
                return False

        return True

    def generate_signals(
        self,
        df: pd.DataFrame,
        use_trend_filter: bool = True,
        use_volume_filter: bool = True,
        use_breakout_confirm: bool = True,
        use_volume_value_filter: bool = True,
        market_data: Optional[pd.DataFrame] = None,
        daily_data: Optional[pd.DataFrame] = None,
        use_daily_trend_filter: bool = False,
        trend_period: int = 20,
        # 🔽 신규 추가: 거래량 배수 및 시장 모멘텀 설정
        volume_multiplier: float = 1.2,
        use_market_momentum: bool = False,
        # 🔽 진입 조건 완화 옵션
        vwap_tolerance_pct: float = 0.0,
        ma_tolerance_pct: float = 0.0,
        vwap_cross_only: bool = True,
        # 🔽 Williams %R 필터
        use_williams_r_filter: bool = False,
        williams_r_period: int = 14,
        williams_r_long_ceiling: float = -20.0,
        williams_r_short_floor: float = -80.0
    ) -> pd.DataFrame:
        """
        VWAP 기반 매수/매도 시그널 생성 (개선 버전)

        Signal:
            1: Buy (VWAP 상향 돌파 + 필터 통과)
            -1: Sell (VWAP 하향 돌파 + 필터 통과)
            0: Hold (유지)

        Args:
            df: VWAP이 계산된 데이터프레임
            use_trend_filter: 추세 필터 사용 여부 (기본 True)
            use_volume_filter: 거래량 필터 사용 여부 (기본 True)
            use_breakout_confirm: 돌파 지속성 확인 여부 (기본 True)
            use_volume_value_filter: 거래대금 필터 사용 여부 (기본 True)
            market_data: 시장 지수 데이터 (시장 모멘텀 체크용, optional)
            daily_data: 일봉 데이터 (일봉 추세 강도 체크용, optional)
            use_daily_trend_filter: 일봉 추세 강도 필터 사용 여부 (기본 False)
            trend_period: 추세 판단 기간 (기본 20봉)

        Returns:
            시그널 컬럼이 추가된 데이터프레임
        """
        # 시그널 컬럼 초기화
        df['signal'] = 0

        # VWAP이 없으면 종료
        if 'vwap' not in df.columns:
            print("[경고] VWAP 컬럼이 없습니다. calculate_vwap()을 먼저 실행하세요.")
            return df

        # 🔁 (1) 시장 모멘텀 체크: 설정이 True일 때만
        if use_market_momentum:
            market_ok = self.check_market_momentum(market_data)
            if not market_ok:
                return df  # 시장 하락장이면 모든 시그널 무효

        # 일봉 추세 강도 체크
        if use_daily_trend_filter:
            trend_ok = self.check_daily_trend_strength(daily_data)
            if not trend_ok:
                return df  # 일봉 추세 약하면 모든 시그널 무효

        # 🔁 (2) 추세 필터: 이동평균선 기준
        if use_trend_filter:
            df['ma'] = df['close'].rolling(window=trend_period).mean()
            uptrend = df['close'] > df['ma']
            downtrend = df['close'] < df['ma']
        else:
            uptrend = True
            downtrend = True

        # 🔁 (3) 거래량 필터: 평균 대비 배수를 설정에서 사용 + NaN 안전 처리
        if use_volume_filter:
            df['volume_ma'] = df['volume'].rolling(window=20).mean()
            # 초기 20봉 이전 NaN은 현재값으로 대체 (필터가 초기부터 무조건 실패하지 않도록)
            vol_ma = df['volume_ma'].fillna(df['volume'])
            volume_surge = df['volume'] > (vol_ma * float(volume_multiplier))
        else:
            volume_surge = True

        # 🔁 (4) Williams %R 필터: 과매수 진입 억제
        if use_williams_r_filter:
            # Williams %R 계산 (없으면 계산)
            wr_col = f'williams_r_{williams_r_period}'
            if wr_col not in df.columns:
                df = self.calculate_williams_r(df, period=williams_r_period)
        else:
            wr_col = None

        # 허용 오차 계산
        vwap_tol = float(vwap_tolerance_pct) / 100.0  # 예: 0.5 → 0.005
        ma_tol = float(ma_tolerance_pct) / 100.0      # 예: 0.3 → 0.003

        # 각 행별로 시그널 체크 (오프바이원 방지: range(1, len(df)))
        for idx in range(1, len(df)):
            current = df.iloc[idx]
            previous = df.iloc[idx - 1]

            # --- VWAP 판정 ---
            if vwap_cross_only:
                # 엄격 모드: 상향 돌파만 인정 (근접 허용 X)
                vwap_ok = (previous['close'] < previous['vwap']) and (current['close'] > current['vwap'])
            else:
                # 완화 모드: 근접 허용 또는 상향 돌파
                vwap_ok = (
                    current['close'] >= current['vwap'] * (1.0 - vwap_tol)
                    or ((previous['close'] < previous['vwap']) and (current['close'] > current['vwap']))
                )

            # --- MA(추세) 판정 ---
            if use_trend_filter:
                ma_val_curr = uptrend.iloc[idx] if isinstance(uptrend, pd.Series) else None
                if ma_val_curr is not None and isinstance(ma_val_curr, (int, float)):
                    # MA 근접 허용: MA*(1 - tol) 이상이면 통과
                    trend_ok = current['close'] >= ma_val_curr * (1.0 - ma_tol)
                else:
                    # uptrend가 boolean이면 그대로 사용
                    trend_ok = bool(uptrend.iloc[idx] if isinstance(uptrend, pd.Series) else uptrend)
            else:
                trend_ok = True

            # VWAP 상향 돌파 (Buy Signal)
            if (vwap_ok and
                trend_ok and
                (volume_surge.iloc[idx] if isinstance(volume_surge, pd.Series) else volume_surge)):

                # 추가 필터 체크
                filters_passed = True

                # 1. Williams %R 과매수 필터 (롱 진입 시)
                if use_williams_r_filter and wr_col:
                    wr_val = current[wr_col]
                    # %R이 ceiling(-20) 미만이어야 진입 (>-20은 과매수)
                    if wr_val >= williams_r_long_ceiling:
                        filters_passed = False

                # 2. 돌파 지속성 확인
                if use_breakout_confirm:
                    if not self.confirm_breakout(df, idx):
                        filters_passed = False

                # 3. 거래대금 절대값 확인 (amount 컬럼 자동 보강)
                if use_volume_value_filter:
                    if 'amount' not in df.columns:
                        df.loc[:, 'amount'] = df['close'] * df['volume']
                    if not self.check_volume_value(df, idx):
                        filters_passed = False

                if filters_passed:
                    df.loc[idx, 'signal'] = 1

            # VWAP 하향 돌파 (Sell Signal)
            elif (current['close'] < current['vwap'] and
                  previous['close'] > previous['vwap'] and
                  (downtrend.iloc[idx] if isinstance(downtrend, pd.Series) else downtrend) and
                  (volume_surge.iloc[idx] if isinstance(volume_surge, pd.Series) else volume_surge)):

                df.loc[idx, 'signal'] = -1

        return df

    def analyze_entry_timing(self, stock_code: str, chart_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        진입 타이밍 분석 (5분봉 기준)

        Args:
            stock_code: 종목코드
            chart_data: 5분봉 차트 데이터 (최소 20개 이상 권장)

        Returns:
            {
                'can_enter': bool,           # 진입 가능 여부
                'signal': int,               # 1: Buy, -1: Sell, 0: Hold
                'current_price': float,      # 현재가
                'vwap': float,              # 현재 VWAP
                'price_vs_vwap': float,     # 현재가 vs VWAP 비율 (%)
                'recommendation': str        # 추천 문구
            }
        """
        if not chart_data or len(chart_data) < 2:
            return {
                'can_enter': False,
                'signal': 0,
                'current_price': 0,
                'vwap': 0,
                'price_vs_vwap': 0,
                'recommendation': '데이터 부족'
            }

        # DataFrame 변환
        df = self._prepare_dataframe(chart_data)

        # VWAP 계산
        df = self.calculate_vwap(df)

        # 시그널 생성
        df = self.generate_signals(df)

        # 최신 데이터 추출
        latest = df.iloc[-1]
        current_price = latest['close']
        vwap = latest['vwap']
        signal = latest['signal']

        # 현재가 vs VWAP 비율
        price_vs_vwap = ((current_price - vwap) / vwap) * 100

        # 진입 가능 여부
        can_enter = (signal == 1)

        # 추천 문구
        if signal == 1:
            recommendation = f"✅ 매수 시그널 (VWAP 상향 돌파, +{price_vs_vwap:.2f}%)"
        elif signal == -1:
            recommendation = f"❌ 매도 시그널 (VWAP 하향 돌파, {price_vs_vwap:.2f}%)"
        elif current_price > vwap:
            recommendation = f"⏸️ 관망 (VWAP 상단, +{price_vs_vwap:.2f}%)"
        else:
            recommendation = f"⏸️ 관망 (VWAP 하단, {price_vs_vwap:.2f}%)"

        return {
            'can_enter': can_enter,
            'signal': int(signal),
            'current_price': float(current_price),
            'vwap': float(vwap),
            'price_vs_vwap': float(price_vs_vwap),
            'recommendation': recommendation
        }

    def check_exit_timing(self, stock_code: str, chart_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        청산 타이밍 분석 (5분봉 기준)

        Args:
            stock_code: 종목코드
            chart_data: 5분봉 차트 데이터

        Returns:
            {
                'should_exit': bool,         # 청산 여부
                'signal': int,               # -1: Sell, 0: Hold
                'current_price': float,      # 현재가
                'vwap': float,              # 현재 VWAP
                'recommendation': str        # 추천 문구
            }
        """
        if not chart_data or len(chart_data) < 2:
            return {
                'should_exit': False,
                'signal': 0,
                'current_price': 0,
                'vwap': 0,
                'recommendation': '데이터 부족'
            }

        # DataFrame 변환
        df = self._prepare_dataframe(chart_data)

        # VWAP 계산
        df = self.calculate_vwap(df)

        # 시그널 생성
        df = self.generate_signals(df)

        # 최신 데이터 추출
        latest = df.iloc[-1]
        current_price = latest['close']
        vwap = latest['vwap']
        signal = latest['signal']

        # 청산 여부 (VWAP 하향 돌파)
        should_exit = (signal == -1)

        # 추천 문구
        if signal == -1:
            recommendation = "❌ 청산 시그널 (VWAP 하향 돌파)"
        else:
            price_vs_vwap = ((current_price - vwap) / vwap) * 100
            if current_price > vwap:
                recommendation = f"✅ 보유 유지 (VWAP 상단, +{price_vs_vwap:.2f}%)"
            else:
                recommendation = f"⚠️ 주의 (VWAP 하단, {price_vs_vwap:.2f}%)"

        return {
            'should_exit': should_exit,
            'signal': int(signal),
            'current_price': float(current_price),
            'vwap': float(vwap),
            'recommendation': recommendation
        }

    def check_trailing_stop(
        self,
        current_price: float,
        avg_price: float,
        highest_price: float,
        trailing_active: bool,
        atr: Optional[float] = None,
        use_atr_based: bool = False,
        atr_multiplier: float = 1.5,
        use_profit_tier: bool = False,
        profit_tier_threshold: float = 3.0
    ) -> Tuple[bool, bool, float, str]:
        """
        트레일링 스탑 체크 (고정 비율 또는 ATR 기반 + 목표가 도달 후 강화)

        Args:
            current_price: 현재가
            avg_price: 평균 매수가
            highest_price: 매수 후 최고가
            trailing_active: 트레일링 스탑 활성화 여부
            atr: 현재 ATR 값 (ATR 기반 사용 시)
            use_atr_based: ATR 기반 트레일링 스탑 사용 여부
            atr_multiplier: ATR 배수 (기본 1.5)
            use_profit_tier: 목표가 도달 후 트레일링 강화 여부
            profit_tier_threshold: 강화 트레일링 시작 수익률 (기본 3.0%)

        Returns:
            (should_exit, new_trailing_active, trailing_stop_price, exit_reason)
        """
        profit_rate = ((current_price - avg_price) / avg_price) * 100

        # 트레일링 스탑 활성화 체크
        if not trailing_active and profit_rate >= self.trailing_activation_pct:
            trailing_active = True

            # ATR 기반 or 고정 비율
            if use_atr_based and atr is not None:
                trailing_stop_price = highest_price - (atr * atr_multiplier)
            else:
                trailing_stop_price = highest_price * (1 - self.trailing_ratio / 100)

            return False, True, trailing_stop_price, ""

        # 트레일링 스탑 업데이트 (고가 갱신 시)
        if trailing_active:
            # 목표가 도달 후 강화된 트레일링 적용
            active_trailing_ratio = self.trailing_ratio
            if use_profit_tier and profit_rate >= profit_tier_threshold:
                active_trailing_ratio = self.profit_tier_trailing_ratio

            # ATR 기반 or 고정 비율
            if use_atr_based and atr is not None:
                trailing_stop_price = highest_price - (atr * atr_multiplier)
            else:
                trailing_stop_price = highest_price * (1 - active_trailing_ratio / 100)

            # 트레일링 스탑 청산 체크
            if current_price <= trailing_stop_price:
                method = "ATR 트레일링" if use_atr_based else "트레일링 스탑"
                tier_msg = " (강화)" if use_profit_tier and profit_rate >= profit_tier_threshold else ""
                return True, trailing_active, trailing_stop_price, f"{method}{tier_msg} ({profit_rate:+.2f}%)"

            return False, trailing_active, trailing_stop_price, ""

        # 기본 손절 체크 (트레일링 활성화 전) - ATR 기반 가능
        if use_atr_based and atr is not None:
            stop_loss_price = avg_price - (atr * 2)  # ATR × 2 손절
            if current_price <= stop_loss_price:
                return True, trailing_active, stop_loss_price, f"ATR 손절 ({profit_rate:.2f}%)"
        else:
            if profit_rate <= -self.stop_loss_pct:
                return True, trailing_active, 0, f"손절 ({profit_rate:.2f}%)"

        return False, trailing_active, 0, ""

    def _prepare_dataframe(self, chart_data: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        차트 데이터를 DataFrame으로 변환

        Args:
            chart_data: Kiwoom API에서 받은 차트 데이터

        Returns:
            pandas DataFrame (컬럼: open, high, low, close, volume)
        """
        df = pd.DataFrame(chart_data)

        # 컬럼명 확인 및 변환
        column_mapping = {
            'open_pric': 'open',
            'high_pric': 'high',
            'low_pric': 'low',
            'cur_prc': 'close',
            'trde_qty': 'volume'
        }

        # 컬럼 변환
        for old_col, new_col in column_mapping.items():
            if old_col in df.columns:
                df[new_col] = pd.to_numeric(
                    df[old_col].astype(str).str.replace(',', '').str.replace('+', '').str.replace('-', ''),
                    errors='coerce'
                )

        # 필수 컬럼 확인
        required_columns = ['open', 'high', 'low', 'close', 'volume']
        for col in required_columns:
            if col not in df.columns:
                raise ValueError(f"필수 컬럼 '{col}'이 없습니다.")

        # 결측치 제거
        df = df.dropna(subset=required_columns)

        # 인덱스 리셋
        df = df.reset_index(drop=True)

        return df[required_columns]
