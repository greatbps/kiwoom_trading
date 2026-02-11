"""
기술적 분석 엔진
- RSI, MACD, 이동평균선, 볼린저 밴드 등
- 가중치: 40% (전체 분석 엔진 중 가장 높음)
"""
import pandas as pd
import pandas_ta as ta
import numpy as np
from typing import Dict, List, Any, Tuple


class TechnicalAnalyzer:
    """기술적 분석 엔진"""

    def __init__(self):
        """초기화"""
        # 지표별 가중치 (합계 100)
        self.weights = {
            'trend': 30,      # 추세 (이동평균)
            'momentum': 25,   # 모멘텀 (RSI, MACD)
            'volatility': 20, # 변동성 (볼린저 밴드)
            'volume': 15,     # 거래량
            'pattern': 10     # 패턴 (골든크로스 등)
        }

    def prepare_dataframe(self, chart_data: List[Dict[str, Any]]) -> pd.DataFrame:
        """
        차트 데이터를 DataFrame으로 변환

        Args:
            chart_data: 일봉 또는 분봉 데이터 리스트

        Returns:
            pandas DataFrame (날짜 인덱스)
        """
        if not chart_data:
            raise ValueError("차트 데이터가 비어있습니다.")

        # DataFrame 생성
        df = pd.DataFrame(chart_data)

        # 필수 컬럼 확인 (키움 API 응답 기준)
        # 일봉: dt, open_pric, high_pric, low_pric, cur_prc, trde_qty
        # 분봉: tic_dt, tic_tm, opn_prc, hgh_pric, low_pric, cur_prc, trde_qty

        # 날짜/시간 컬럼 처리
        if 'dt' in df.columns:
            # 일봉
            df['date'] = pd.to_datetime(df['dt'], format='%Y%m%d')
        elif 'tic_dt' in df.columns and 'tic_tm' in df.columns:
            # 분봉
            df['date'] = pd.to_datetime(
                df['tic_dt'] + df['tic_tm'],
                format='%Y%m%d%H%M%S'
            )
        else:
            raise ValueError("날짜 컬럼을 찾을 수 없습니다.")

        # OHLCV 컬럼 이름 표준화
        df = df.rename(columns={
            'open_pric': 'open',
            'high_pric': 'high',
            'low_pric': 'low',
            'cur_prc': 'close',
            'trde_qty': 'volume'
        })

        # 문자열을 숫자로 변환 (부호 제거)
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace('+', '').str.replace('-', '').str.replace(',', '')
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # 날짜를 인덱스로 설정
        df = df.set_index('date')
        df = df.sort_index()

        # 필수 컬럼만 선택
        df = df[['open', 'high', 'low', 'close', 'volume']]

        return df

    def calculate_rsi(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """
        RSI (Relative Strength Index) 계산

        Args:
            df: OHLCV DataFrame
            period: RSI 기간 (기본 14일)

        Returns:
            RSI 값 Series
        """
        rsi = ta.rsi(df['close'], length=period)
        return rsi

    def calculate_macd(self, df: pd.DataFrame) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        MACD (Moving Average Convergence Divergence) 계산

        Args:
            df: OHLCV DataFrame

        Returns:
            (MACD, Signal, Histogram)
        """
        macd = ta.macd(df['close'], fast=12, slow=26, signal=9)

        if macd is not None and not macd.empty:
            # 컬럼명 확인 (버전에 따라 다를 수 있음)
            cols = macd.columns.tolist()

            # MACD, Signal, Histogram 찾기
            macd_col = [c for c in cols if 'MACD_' in c and 'h' not in c.lower() and 's' not in c.lower()][0] if any('MACD_' in c and 'h' not in c.lower() and 's' not in c.lower() for c in cols) else cols[0]
            signal_col = [c for c in cols if 'MACDs' in c or 'signal' in c.lower()][0] if any('MACDs' in c or 'signal' in c.lower() for c in cols) else cols[1]
            histogram_col = [c for c in cols if 'MACDh' in c or 'histogram' in c.lower()][0] if any('MACDh' in c or 'histogram' in c.lower() for c in cols) else cols[2]

            macd_line = macd[macd_col]
            signal_line = macd[signal_col]
            histogram = macd[histogram_col]
            return macd_line, signal_line, histogram
        else:
            return pd.Series(), pd.Series(), pd.Series()

    def calculate_moving_averages(self, df: pd.DataFrame) -> Dict[str, pd.Series]:
        """
        이동평균선 계산 (5, 20, 60, 120일)

        Args:
            df: OHLCV DataFrame

        Returns:
            이동평균선 딕셔너리
        """
        mas = {}
        for period in [5, 20, 60, 120]:
            # 데이터가 충분한 경우에만 계산
            if len(df) >= period:
                result = ta.sma(df['close'], length=period)
                mas[f'ma{period}'] = result if result is not None else None
            else:
                mas[f'ma{period}'] = None

        return mas

    def calculate_bollinger_bands(self, df: pd.DataFrame, period: int = 20, std: float = 2) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        볼린저 밴드 계산

        Args:
            df: OHLCV DataFrame
            period: 기간 (기본 20일)
            std: 표준편차 배수 (기본 2)

        Returns:
            (상단밴드, 중간밴드, 하단밴드)
        """
        bbands = ta.bbands(df['close'], length=period, std=std)

        if bbands is not None and not bbands.empty:
            # 컬럼명 확인 (버전에 따라 다를 수 있음)
            cols = bbands.columns.tolist()

            # 상단/중간/하단 밴드 찾기
            upper_col = [c for c in cols if 'BBU' in c or 'upper' in c.lower()][0] if any('BBU' in c or 'upper' in c.lower() for c in cols) else cols[0]
            middle_col = [c for c in cols if 'BBM' in c or 'middle' in c.lower()][0] if any('BBM' in c or 'middle' in c.lower() for c in cols) else cols[1]
            lower_col = [c for c in cols if 'BBL' in c or 'lower' in c.lower()][0] if any('BBL' in c or 'lower' in c.lower() for c in cols) else cols[2]

            upper = bbands[upper_col]
            middle = bbands[middle_col]
            lower = bbands[lower_col]
            return upper, middle, lower
        else:
            return pd.Series(), pd.Series(), pd.Series()

    def analyze_trend(self, df: pd.DataFrame, mas: Dict[str, pd.Series]) -> Dict[str, Any]:
        """
        추세 분석 (이동평균선 기반)

        Args:
            df: OHLCV DataFrame
            mas: 이동평균선 딕셔너리

        Returns:
            추세 분석 결과
        """
        current_price = df['close'].iloc[-1]
        score = 0
        signals = []

        # 현재가와 이동평균선 비교
        ma_scores = {
            'ma5': 0,
            'ma20': 0,
            'ma60': 0,
            'ma120': 0
        }

        for ma_name, ma_series in mas.items():
            if ma_series is not None and len(ma_series) > 0 and not pd.isna(ma_series.iloc[-1]):
                ma_value = ma_series.iloc[-1]

                if current_price > ma_value:
                    ma_scores[ma_name] = 1
                    signals.append(f"{ma_name} 위에 위치 (상승)")
                else:
                    ma_scores[ma_name] = -1
                    signals.append(f"{ma_name} 아래 위치 (하락)")

        # 정배열/역배열 체크 (ma5 > ma20 > ma60 > ma120)
        try:
            ma5 = mas['ma5'].iloc[-1]
            ma20 = mas['ma20'].iloc[-1]
            ma60 = mas['ma60'].iloc[-1]
            ma120 = mas['ma120'].iloc[-1]

            if ma5 > ma20 > ma60 > ma120:
                score += 30
                signals.append("완벽한 정배열 (강한 상승 추세)")
            elif ma5 > ma20 > ma60:
                score += 20
                signals.append("정배열 (상승 추세)")
            elif ma5 < ma20 < ma60 < ma120:
                score -= 30
                signals.append("완벽한 역배열 (강한 하락 추세)")
            elif ma5 < ma20 < ma60:
                score -= 20
                signals.append("역배열 (하락 추세)")
            else:
                score += sum(ma_scores.values()) * 5
                signals.append("혼조 추세")

        except Exception:
            score += sum(ma_scores.values()) * 5

        # 정규화 (0-100)
        score = max(0, min(100, score + 50))

        return {
            'score': score,
            'signals': signals,
            'ma_scores': ma_scores
        }

    def analyze_momentum(self, df: pd.DataFrame, rsi: pd.Series,
                        macd: pd.Series, signal: pd.Series) -> Dict[str, Any]:
        """
        모멘텀 분석 (RSI, MACD)

        Args:
            df: OHLCV DataFrame
            rsi: RSI Series
            macd: MACD Series
            signal: MACD Signal Series

        Returns:
            모멘텀 분석 결과
        """
        score = 0
        signals = []

        # RSI 분석
        if len(rsi) > 0 and not pd.isna(rsi.iloc[-1]):
            rsi_value = rsi.iloc[-1]

            if rsi_value >= 70:
                score -= 20
                signals.append(f"RSI 과매수 구간 ({rsi_value:.1f})")
            elif rsi_value >= 60:
                score += 10
                signals.append(f"RSI 강세 ({rsi_value:.1f})")
            elif rsi_value >= 40:
                score += 5
                signals.append(f"RSI 중립 ({rsi_value:.1f})")
            elif rsi_value >= 30:
                score += 10
                signals.append(f"RSI 약세 ({rsi_value:.1f})")
            else:
                score += 20
                signals.append(f"RSI 과매도 구간 ({rsi_value:.1f}) - 반등 기대")

        # MACD 분석
        if len(macd) > 1 and len(signal) > 1:
            if not pd.isna(macd.iloc[-1]) and not pd.isna(signal.iloc[-1]):
                macd_current = macd.iloc[-1]
                signal_current = signal.iloc[-1]
                macd_prev = macd.iloc[-2]
                signal_prev = signal.iloc[-2]

                # 골든크로스/데드크로스
                if macd_prev <= signal_prev and macd_current > signal_current:
                    score += 30
                    signals.append("MACD 골든크로스 (매수 신호)")
                elif macd_prev >= signal_prev and macd_current < signal_current:
                    score -= 30
                    signals.append("MACD 데드크로스 (매도 신호)")
                elif macd_current > signal_current:
                    score += 15
                    signals.append("MACD > Signal (상승 모멘텀)")
                else:
                    score -= 15
                    signals.append("MACD < Signal (하락 모멘텀)")

        # 정규화 (0-100)
        score = max(0, min(100, score + 50))

        return {
            'score': score,
            'signals': signals,
            'rsi_value': rsi.iloc[-1] if len(rsi) > 0 else None,
            'macd_value': macd.iloc[-1] if len(macd) > 0 else None
        }

    def analyze_volatility(self, df: pd.DataFrame, bb_upper: pd.Series,
                          bb_middle: pd.Series, bb_lower: pd.Series) -> Dict[str, Any]:
        """
        변동성 분석 (볼린저 밴드)

        Args:
            df: OHLCV DataFrame
            bb_upper: 볼린저 밴드 상단
            bb_middle: 볼린저 밴드 중간
            bb_lower: 볼린저 밴드 하단

        Returns:
            변동성 분석 결과
        """
        score = 50
        signals = []

        if len(bb_upper) > 0 and len(bb_lower) > 0:
            current_price = df['close'].iloc[-1]
            upper = bb_upper.iloc[-1]
            middle = bb_middle.iloc[-1]
            lower = bb_lower.iloc[-1]

            if not pd.isna(upper) and not pd.isna(lower):
                # 볼린저 밴드 폭
                band_width = upper - lower
                price_position = (current_price - lower) / band_width * 100

                if price_position >= 80:
                    score = 30
                    signals.append(f"볼린저 밴드 상단 근접 ({price_position:.1f}%) - 과매수")
                elif price_position >= 60:
                    score = 60
                    signals.append(f"볼린저 밴드 상단 ({price_position:.1f}%)")
                elif price_position >= 40:
                    score = 50
                    signals.append(f"볼린저 밴드 중간 ({price_position:.1f}%)")
                elif price_position >= 20:
                    score = 60
                    signals.append(f"볼린저 밴드 하단 ({price_position:.1f}%)")
                else:
                    score = 80
                    signals.append(f"볼린저 밴드 하단 근접 ({price_position:.1f}%) - 과매도")

                # 밴드 폭 분석
                avg_price = df['close'].rolling(window=20).mean().iloc[-1]
                if not pd.isna(avg_price):
                    relative_width = (band_width / avg_price) * 100

                    if relative_width > 10:
                        signals.append(f"밴드 폭 확대 ({relative_width:.1f}%) - 고변동성")
                    elif relative_width < 3:
                        signals.append(f"밴드 폭 축소 ({relative_width:.1f}%) - 저변동성, 변동 임박")

        return {
            'score': score,
            'signals': signals
        }

    def analyze_volume(self, df: pd.DataFrame) -> Dict[str, Any]:
        """
        거래량 분석

        Args:
            df: OHLCV DataFrame

        Returns:
            거래량 분석 결과
        """
        score = 50
        signals = []

        if len(df) >= 20:
            current_volume = df['volume'].iloc[-1]
            avg_volume_20 = df['volume'].rolling(window=20).mean().iloc[-1]

            if not pd.isna(avg_volume_20) and avg_volume_20 > 0:
                volume_ratio = current_volume / avg_volume_20

                if volume_ratio >= 2.0:
                    score = 80
                    signals.append(f"거래량 급증 ({volume_ratio:.1f}배) - 강한 관심")
                elif volume_ratio >= 1.5:
                    score = 70
                    signals.append(f"거래량 증가 ({volume_ratio:.1f}배)")
                elif volume_ratio >= 1.0:
                    score = 50
                    signals.append(f"평균 거래량 ({volume_ratio:.1f}배)")
                elif volume_ratio >= 0.5:
                    score = 40
                    signals.append(f"거래량 감소 ({volume_ratio:.1f}배)")
                else:
                    score = 30
                    signals.append(f"거래량 부진 ({volume_ratio:.1f}배)")

                # 가격과 거래량 추세 분석
                price_change = (df['close'].iloc[-1] - df['close'].iloc[-5]) / df['close'].iloc[-5] * 100
                volume_trend = df['volume'].iloc[-5:].mean() / df['volume'].iloc[-10:-5].mean()

                if price_change > 0 and volume_trend > 1.2:
                    score += 10
                    signals.append("상승 + 거래량 증가 (건강한 상승)")
                elif price_change < 0 and volume_trend > 1.2:
                    score -= 10
                    signals.append("하락 + 거래량 증가 (매도 압력)")

        return {
            'score': score,
            'signals': signals
        }

    def analyze_pattern(self, df: pd.DataFrame, mas: Dict[str, pd.Series]) -> Dict[str, Any]:
        """
        패턴 분석 (골든크로스, 데드크로스 등)

        Args:
            df: OHLCV DataFrame
            mas: 이동평균선 딕셔너리

        Returns:
            패턴 분석 결과
        """
        score = 50
        signals = []

        # 골든크로스/데드크로스 (단기 MA가 장기 MA를 돌파)
        if (mas.get('ma5') is not None and len(mas['ma5']) > 1 and
            mas.get('ma20') is not None and len(mas['ma20']) > 1):
            ma5_current = mas['ma5'].iloc[-1]
            ma5_prev = mas['ma5'].iloc[-2]
            ma20_current = mas['ma20'].iloc[-1]
            ma20_prev = mas['ma20'].iloc[-2]

            if not any(pd.isna([ma5_current, ma5_prev, ma20_current, ma20_prev])):
                # 5일선이 20일선 돌파
                if ma5_prev <= ma20_prev and ma5_current > ma20_current:
                    score += 30
                    signals.append("골든크로스 (5일 > 20일)")
                elif ma5_prev >= ma20_prev and ma5_current < ma20_current:
                    score -= 30
                    signals.append("데드크로스 (5일 < 20일)")

        # 20일선이 60일선 돌파
        if (mas.get('ma20') is not None and len(mas['ma20']) > 1 and
            mas.get('ma60') is not None and len(mas['ma60']) > 1):
            ma20_current = mas['ma20'].iloc[-1]
            ma20_prev = mas['ma20'].iloc[-2]
            ma60_current = mas['ma60'].iloc[-1]
            ma60_prev = mas['ma60'].iloc[-2]

            if not any(pd.isna([ma20_current, ma20_prev, ma60_current, ma60_prev])):
                if ma20_prev <= ma60_prev and ma20_current > ma60_current:
                    score += 20
                    signals.append("중기 골든크로스 (20일 > 60일)")
                elif ma20_prev >= ma60_prev and ma20_current < ma60_current:
                    score -= 20
                    signals.append("중기 데드크로스 (20일 < 60일)")

        # 정규화 (0-100)
        score = max(0, min(100, score))

        return {
            'score': score,
            'signals': signals
        }

    def detect_ema_breakdown(self, df: pd.DataFrame, ema_period: int = 20) -> Dict[str, Any]:
        """
        EMA + Volume Breakdown 감지 (5단계 매도 신호)

        추세 전환 감지:
        1. 가격이 EMA 아래로 이탈
        2. 거래량 급증 (평균 대비 1.5배 이상)
        3. 연속 2개 캔들 하락

        Args:
            df: OHLCV DataFrame
            ema_period: EMA 기간 (기본 20일)

        Returns:
            {
                'breakdown_detected': bool,
                'ema_value': float,
                'current_price': float,
                'volume_surge': float,
                'confidence': str,
                'reason': str
            }
        """
        if len(df) < max(ema_period + 5, 3):
            return {
                'breakdown_detected': False,
                'reason': '데이터 부족',
                'confidence': 'NONE'
            }

        try:
            # EMA 계산
            ema = df['close'].ewm(span=ema_period, adjust=False).mean()

            # 최근 3개 캔들 데이터
            recent_close = df['close'].iloc[-3:].values
            recent_ema = ema.iloc[-3:].values
            recent_volume = df['volume'].iloc[-3:].values

            # 평균 거래량 (최근 20일)
            avg_volume = df['volume'].iloc[-20:].mean()

            current_price = recent_close[-1]
            current_ema = recent_ema[-1]
            current_volume = recent_volume[-1]

            # 1. 가격이 EMA 아래로 이탈 체크
            price_below_ema = current_price < current_ema
            price_crossed_down = recent_close[-2] >= recent_ema[-2] and current_price < current_ema

            # 2. 거래량 급증 체크 (평균 대비 1.5배 이상)
            volume_surge_ratio = current_volume / avg_volume if avg_volume > 0 else 0
            volume_surged = volume_surge_ratio >= 1.5

            # 3. 연속 하락 체크
            consecutive_decline = recent_close[-1] < recent_close[-2] < recent_close[-3]

            # 4. EMA 이탈 강도 (%)
            ema_distance_pct = ((current_ema - current_price) / current_ema) * 100

            # Breakdown 판정
            breakdown_detected = False
            confidence = 'NONE'
            reason = ''

            if price_crossed_down and volume_surged and consecutive_decline:
                # 강력한 Breakdown
                breakdown_detected = True
                confidence = 'HIGH'
                reason = f'EMA 하향 돌파 + 거래량 급증({volume_surge_ratio:.1f}배) + 연속 하락'

            elif price_crossed_down and (volume_surged or consecutive_decline):
                # 중간 Breakdown
                breakdown_detected = True
                confidence = 'MEDIUM'
                reason = f'EMA 하향 돌파 + '
                reason += f'거래량 급증({volume_surge_ratio:.1f}배)' if volume_surged else '연속 하락'

            elif price_below_ema and ema_distance_pct > 2.0 and volume_surged:
                # 강한 이탈
                breakdown_detected = True
                confidence = 'MEDIUM'
                reason = f'EMA 대비 -{ema_distance_pct:.1f}% 이탈 + 거래량 급증'

            return {
                'breakdown_detected': breakdown_detected,
                'ema_value': float(current_ema),
                'current_price': float(current_price),
                'ema_distance_pct': float(ema_distance_pct),
                'volume_surge_ratio': float(volume_surge_ratio),
                'consecutive_decline': consecutive_decline,
                'confidence': confidence,
                'reason': reason if breakdown_detected else '정상 (Breakdown 없음)'
            }

        except Exception as e:
            return {
                'breakdown_detected': False,
                'reason': f'계산 오류: {e}',
                'confidence': 'NONE'
            }

    def analyze(self, chart_data: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        종합 기술적 분석

        Args:
            chart_data: 차트 데이터 (일봉 또는 분봉)

        Returns:
            기술적 분석 결과
            - total_score: 총점 (0-100)
            - trend: 추세 분석
            - momentum: 모멘텀 분석
            - volatility: 변동성 분석
            - volume: 거래량 분석
            - pattern: 패턴 분석
            - recommendation: 추천 (매수/관망/매도)
        """
        # 차트 데이터 None 체크
        if chart_data is None or not chart_data:
            return {
                'total_score': 50,
                'trend': {'score': 50, 'signal': '데이터 없음'},
                'momentum': {'score': 50, 'signal': '데이터 없음'},
                'volatility': {'score': 50, 'signal': '데이터 없음'},
                'volume': {'score': 50, 'signal': '데이터 없음'},
                'pattern': {'score': 50, 'signal': '데이터 없음'},
                'recommendation': '관망'
            }

        # DataFrame 준비
        try:
            df = self.prepare_dataframe(chart_data)
        except Exception as e:
            return {
                'total_score': 50,
                'trend': {'score': 50, 'signal': f'데이터 변환 오류: {str(e)}'},
                'momentum': {'score': 50, 'signal': '데이터 변환 오류'},
                'volatility': {'score': 50, 'signal': '데이터 변환 오류'},
                'volume': {'score': 50, 'signal': '데이터 변환 오류'},
                'pattern': {'score': 50, 'signal': '데이터 변환 오류'},
                'recommendation': '관망'
            }

        # 기술적 지표 계산
        try:
            rsi = self.calculate_rsi(df)
            macd, signal, histogram = self.calculate_macd(df)
            mas = self.calculate_moving_averages(df)
            bb_upper, bb_middle, bb_lower = self.calculate_bollinger_bands(df)
        except Exception as e:
            return {
                'total_score': 50,
                'trend': {'score': 50, 'signals': [f'지표 계산 오류: {str(e)}']},
                'momentum': {'score': 50, 'signals': ['지표 계산 오류']},
                'volatility': {'score': 50, 'signals': ['지표 계산 오류']},
                'volume': {'score': 50, 'signals': ['지표 계산 오류']},
                'pattern': {'score': 50, 'signals': ['지표 계산 오류']},
                'recommendation': '관망'
            }

        # 각 영역별 분석
        try:
            trend_result = self.analyze_trend(df, mas)
            momentum_result = self.analyze_momentum(df, rsi, macd, signal)
            volatility_result = self.analyze_volatility(df, bb_upper, bb_middle, bb_lower)
            volume_result = self.analyze_volume(df)
            pattern_result = self.analyze_pattern(df, mas)
        except Exception as e:
            return {
                'total_score': 50,
                'trend': {'score': 50, 'signals': [f'분석 오류: {str(e)}']},
                'momentum': {'score': 50, 'signals': ['분석 오류']},
                'volatility': {'score': 50, 'signals': ['분석 오류']},
                'volume': {'score': 50, 'signals': ['분석 오류']},
                'pattern': {'score': 50, 'signals': ['분석 오류']},
                'recommendation': '관망'
            }

        # 가중치 적용하여 총점 계산
        total_score = (
            trend_result['score'] * self.weights['trend'] +
            momentum_result['score'] * self.weights['momentum'] +
            volatility_result['score'] * self.weights['volatility'] +
            volume_result['score'] * self.weights['volume'] +
            pattern_result['score'] * self.weights['pattern']
        ) / 100

        # 추천 판단
        if total_score >= 70:
            recommendation = "매수"
        elif total_score >= 50:
            recommendation = "관망"
        else:
            recommendation = "매도"

        return {
            'total_score': round(total_score, 2),
            'recommendation': recommendation,
            'trend': trend_result,
            'momentum': momentum_result,
            'volatility': volatility_result,
            'volume': volume_result,
            'pattern': pattern_result,
            'weights': self.weights
        }
