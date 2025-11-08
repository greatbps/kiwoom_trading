"""
매매 전략 엔진
- 진입/청산 시점 판단
- 목표가/손절가 계산
- 포지션 사이징
- 리스크 관리
"""
import pandas as pd
import numpy as np
from typing import Dict, List, Any, Tuple, Optional


class TradingStrategy:
    """매매 전략 엔진"""

    def __init__(self, risk_per_trade: float = 0.02, max_position_size: float = 0.3):
        """
        초기화

        Args:
            risk_per_trade: 거래당 리스크 비율 (기본 2%)
            max_position_size: 최대 포지션 크기 (기본 30%)
        """
        self.risk_per_trade = risk_per_trade
        self.max_position_size = max_position_size

        # 진입 신호 임계값
        self.entry_thresholds = {
            'strong_buy': 70,    # 적극 매수
            'buy': 60,           # 매수
            'hold': 50,          # 관망
            'sell': 40,          # 매도 고려
            'strong_sell': 30    # 적극 매도
        }

        # 리스크/리워드 비율 최소값
        self.min_risk_reward_ratio = 2.0  # 최소 1:2

    def generate_entry_signal(self, analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        진입 신호 생성

        Args:
            analysis_result: 통합 분석 결과 (analysis_engine 출력)

        Returns:
            진입 신호 정보
        """
        final_score = analysis_result.get('final_score', 50)
        action = analysis_result.get('action', 'HOLD')

        # 개별 엔진 점수
        technical_score = analysis_result.get('scores_breakdown', {}).get('technical', 50)
        supply_demand_score = analysis_result.get('scores_breakdown', {}).get('supply_demand', 50)

        # 진입 신호 판단
        if final_score >= self.entry_thresholds['strong_buy']:
            signal = 'STRONG_BUY'
            confidence = 'HIGH'
            entry_ratio = 1.0  # 100% 진입
        elif final_score >= self.entry_thresholds['buy']:
            signal = 'BUY'
            confidence = 'MEDIUM'
            entry_ratio = 0.7  # 70% 진입
        elif final_score >= self.entry_thresholds['hold']:
            signal = 'HOLD'
            confidence = 'LOW'
            entry_ratio = 0.0
        elif final_score >= self.entry_thresholds['sell']:
            signal = 'SELL'
            confidence = 'MEDIUM'
            entry_ratio = -0.5  # 50% 청산
        else:
            signal = 'STRONG_SELL'
            confidence = 'HIGH'
            entry_ratio = -1.0  # 100% 청산

        # 분할 매수 전략 판단
        split_strategy = self._determine_split_strategy(
            final_score, technical_score, supply_demand_score
        )

        # 조건부 진입 (추가 확인 필요 여부)
        conditions = []

        # 기술적 점수가 낮으면 주의
        if signal in ['BUY', 'STRONG_BUY'] and technical_score < 50:
            conditions.append("⚠️ 기술적 분석 약세 - 진입 주의")

        # 수급이 나쁘면 주의
        if signal in ['BUY', 'STRONG_BUY'] and supply_demand_score < 45:
            conditions.append("⚠️ 수급 약세 - 진입 주의")

        # 모든 신호가 일치하면 강력 추천
        if all(s >= 60 for s in analysis_result.get('scores_breakdown', {}).values()):
            conditions.append("✅ 전 영역 강세 - 강력 추천")

        return {
            'signal': signal,
            'confidence': confidence,
            'entry_ratio': entry_ratio,
            'split_strategy': split_strategy,
            'conditions': conditions,
            'final_score': final_score
        }

    def calculate_target_price(self, current_price: float,
                               chart_data: List[Dict[str, Any]],
                               analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        목표가 계산 (저항선, ATR, 볼린저 밴드 기반)

        Args:
            current_price: 현재가
            chart_data: 차트 데이터
            analysis_result: 통합 분석 결과

        Returns:
            목표가 정보
        """
        if not chart_data or len(chart_data) < 20:
            # 기본 목표가 (5%, 10%, 15%)
            return {
                'target1': round(current_price * 1.05),
                'target2': round(current_price * 1.10),
                'target3': round(current_price * 1.15),
                'method': 'default'
            }

        # DataFrame 변환
        df = self._prepare_dataframe(chart_data)

        # ATR 계산 (Average True Range)
        atr = self._calculate_atr(df)

        # 저항선 찾기
        resistance_levels = self._find_resistance_levels(df, current_price)

        # 볼린저 밴드 상단
        bb_upper = self._calculate_bollinger_upper(df)

        # 목표가 설정 전략
        targets = []

        # 1차 목표: ATR 기반 (1.5 ATR)
        target1_atr = current_price + (atr * 1.5)
        targets.append(('ATR 1.5배', target1_atr))

        # 2차 목표: ATR 기반 (2.5 ATR)
        target2_atr = current_price + (atr * 2.5)
        targets.append(('ATR 2.5배', target2_atr))

        # 3차 목표: ATR 기반 (4.0 ATR)
        target3_atr = current_price + (atr * 4.0)
        targets.append(('ATR 4.0배', target3_atr))

        # 저항선이 있으면 고려
        if resistance_levels:
            for i, resistance in enumerate(resistance_levels[:3]):
                targets.append((f'저항선 {i+1}', resistance))

        # 볼린저 밴드 상단
        if bb_upper and bb_upper > current_price:
            targets.append(('볼린저 상단', bb_upper))

        # 정렬 및 선택
        targets.sort(key=lambda x: x[1])

        # 현재가보다 높은 것만 선택
        valid_targets = [t for t in targets if t[1] > current_price]

        if len(valid_targets) >= 3:
            target1 = valid_targets[0]
            target2 = valid_targets[len(valid_targets)//2]
            target3 = valid_targets[-1]
        elif len(valid_targets) >= 1:
            # ATR 기반으로 보충
            target1 = valid_targets[0]
            target2 = ('ATR 2.5배', current_price + (atr * 2.5))
            target3 = ('ATR 4.0배', current_price + (atr * 4.0))
        else:
            # 기본값
            target1 = ('기본 5%', current_price * 1.05)
            target2 = ('기본 10%', current_price * 1.10)
            target3 = ('기본 15%', current_price * 1.15)

        return {
            'target1': round(target1[1]),
            'target1_method': target1[0],
            'target1_gain': round((target1[1] - current_price) / current_price * 100, 2),
            'target2': round(target2[1]),
            'target2_method': target2[0],
            'target2_gain': round((target2[1] - current_price) / current_price * 100, 2),
            'target3': round(target3[1]),
            'target3_method': target3[0],
            'target3_gain': round((target3[1] - current_price) / current_price * 100, 2),
            'atr': round(atr, 2),
            'current_price': current_price
        }

    def calculate_stop_loss(self, current_price: float,
                           chart_data: List[Dict[str, Any]],
                           analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        손절가 계산 (지지선, ATR 기반)

        Args:
            current_price: 현재가
            chart_data: 차트 데이터
            analysis_result: 통합 분석 결과

        Returns:
            손절가 정보
        """
        if not chart_data or len(chart_data) < 20:
            # 기본 손절가 (-5%)
            return {
                'stop_loss': round(current_price * 0.95),
                'method': 'default',
                'loss_rate': -5.0
            }

        # DataFrame 변환
        df = self._prepare_dataframe(chart_data)

        # ATR 계산
        atr = self._calculate_atr(df)

        # 지지선 찾기
        support_levels = self._find_support_levels(df, current_price)

        # 손절가 후보
        stop_candidates = []

        # ATR 기반 손절 (2 ATR 아래)
        stop_atr = current_price - (atr * 2.0)
        stop_candidates.append(('ATR 2배', stop_atr))

        # 지지선 기반
        if support_levels:
            nearest_support = support_levels[0]
            # 지지선보다 약간 아래
            stop_support = nearest_support * 0.98
            stop_candidates.append(('지지선', stop_support))

        # 최근 저점 기반
        recent_low = df['low'].tail(20).min()
        if recent_low < current_price:
            stop_recent = recent_low * 0.98
            stop_candidates.append(('최근 저점', stop_recent))

        # 가장 가까운 손절가 선택 (하지만 -10% 이상 손실은 방지)
        min_stop = current_price * 0.90  # 최대 -10%
        max_stop = current_price * 0.97  # 최소 -3%

        valid_stops = [s for s in stop_candidates if min_stop <= s[1] <= max_stop]

        if valid_stops:
            # 가장 가까운 것 선택
            selected_stop = max(valid_stops, key=lambda x: x[1])
        else:
            # 기본 -5%
            selected_stop = ('기본 -5%', current_price * 0.95)

        stop_loss = selected_stop[1]
        loss_rate = (stop_loss - current_price) / current_price * 100

        return {
            'stop_loss': round(stop_loss),
            'method': selected_stop[0],
            'loss_rate': round(loss_rate, 2),
            'atr': round(atr, 2),
            'current_price': current_price
        }

    def calculate_position_size(self, account_balance: float,
                                current_price: float,
                                stop_loss: float,
                                entry_signal: Dict[str, Any]) -> Dict[str, Any]:
        """
        포지션 사이징 (리스크 관리)

        Args:
            account_balance: 계좌 잔고
            current_price: 현재가
            stop_loss: 손절가
            entry_signal: 진입 신호

        Returns:
            포지션 사이즈 정보
        """
        # 진입 비율 적용
        entry_ratio = entry_signal.get('entry_ratio', 0.5)

        # 리스크 금액 (계좌의 2%)
        risk_amount = account_balance * self.risk_per_trade

        # 주당 리스크
        risk_per_share = abs(current_price - stop_loss)

        if risk_per_share == 0:
            risk_per_share = current_price * 0.05  # 기본 5%

        # 리스크 기반 수량
        risk_based_quantity = int(risk_amount / risk_per_share)

        # 최대 포지션 기반 수량
        max_investment = account_balance * self.max_position_size
        max_quantity = int(max_investment / current_price)

        # 둘 중 작은 값 선택
        optimal_quantity = min(risk_based_quantity, max_quantity)

        # 진입 비율 적용
        final_quantity = int(optimal_quantity * abs(entry_ratio))

        # 투자 금액
        investment = final_quantity * current_price

        # 포지션 비율
        position_ratio = (investment / account_balance * 100) if account_balance > 0 else 0

        return {
            'quantity': final_quantity,
            'investment': round(investment),
            'position_ratio': round(position_ratio, 2),
            'risk_amount': round(risk_amount),
            'max_loss': round(final_quantity * risk_per_share),
            'entry_ratio': entry_ratio
        }

    def calculate_risk_reward(self, current_price: float,
                             target_price: float,
                             stop_loss: float) -> Dict[str, Any]:
        """
        리스크/리워드 비율 계산

        Args:
            current_price: 현재가
            target_price: 목표가
            stop_loss: 손절가

        Returns:
            리스크/리워드 정보
        """
        potential_profit = target_price - current_price
        potential_loss = current_price - stop_loss

        if potential_loss == 0:
            risk_reward_ratio = 0
        else:
            risk_reward_ratio = potential_profit / potential_loss

        is_acceptable = risk_reward_ratio >= self.min_risk_reward_ratio

        return {
            'risk_reward_ratio': round(risk_reward_ratio, 2),
            'potential_profit': round(potential_profit),
            'potential_profit_rate': round(potential_profit / current_price * 100, 2),
            'potential_loss': round(potential_loss),
            'potential_loss_rate': round(potential_loss / current_price * 100, 2),
            'is_acceptable': is_acceptable,
            'min_required': self.min_risk_reward_ratio
        }

    def generate_trading_plan(self, stock_code: str, stock_name: str,
                             current_price: float,
                             account_balance: float,
                             chart_data: List[Dict[str, Any]],
                             analysis_result: Dict[str, Any]) -> Dict[str, Any]:
        """
        종합 매매 계획 생성

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            current_price: 현재가
            account_balance: 계좌 잔고
            chart_data: 차트 데이터
            analysis_result: 통합 분석 결과

        Returns:
            종합 매매 계획
        """
        # 1. 진입 신호
        entry_signal = self.generate_entry_signal(analysis_result)

        # 2. 목표가 계산
        target_info = self.calculate_target_price(current_price, chart_data, analysis_result)

        # 3. 손절가 계산
        stop_loss_info = self.calculate_stop_loss(current_price, chart_data, analysis_result)

        # 4. 포지션 사이징
        position_info = self.calculate_position_size(
            account_balance, current_price, stop_loss_info['stop_loss'], entry_signal
        )

        # 5. 리스크/리워드 비율 (1차 목표 기준)
        risk_reward_info = self.calculate_risk_reward(
            current_price, target_info['target1'], stop_loss_info['stop_loss']
        )

        # 6. 분할 매도 계획
        split_sell_plan = self._generate_split_sell_plan(
            position_info['quantity'],
            target_info
        )

        return {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'current_price': current_price,
            'entry_signal': entry_signal,
            'targets': target_info,
            'stop_loss': stop_loss_info,
            'position': position_info,
            'risk_reward': risk_reward_info,
            'split_sell_plan': split_sell_plan,
            'recommendation': self._generate_recommendation(
                entry_signal, risk_reward_info, analysis_result
            )
        }

    def _prepare_dataframe(self, chart_data: List[Dict[str, Any]]) -> pd.DataFrame:
        """차트 데이터를 DataFrame으로 변환"""
        df = pd.DataFrame(chart_data)

        # 컬럼명 변환
        if 'dt' in df.columns:
            df['date'] = pd.to_datetime(df['dt'], format='%Y%m%d')

        # OHLCV 변환
        for col_old, col_new in [('open_pric', 'open'), ('high_pric', 'high'),
                                   ('low_pric', 'low'), ('cur_prc', 'close'),
                                   ('trde_qty', 'volume')]:
            if col_old in df.columns:
                df[col_new] = pd.to_numeric(
                    df[col_old].astype(str).str.replace(',', '').str.replace('+', '').str.replace('-', ''),
                    errors='coerce'
                )

        return df

    def _calculate_atr(self, df: pd.DataFrame, period: int = 14) -> float:
        """ATR (Average True Range) 계산"""
        if len(df) < period:
            return df['high'].max() - df['low'].min()

        high_low = df['high'] - df['low']
        high_close = abs(df['high'] - df['close'].shift())
        low_close = abs(df['low'] - df['close'].shift())

        true_range = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean().iloc[-1]

        return atr if not pd.isna(atr) else (df['high'].max() - df['low'].min()) * 0.1

    def _find_resistance_levels(self, df: pd.DataFrame, current_price: float) -> List[float]:
        """저항선 찾기"""
        recent_highs = df['high'].tail(60).nlargest(10).values
        resistance = [h for h in recent_highs if h > current_price]
        return sorted(set([round(r) for r in resistance]))[:3]

    def _find_support_levels(self, df: pd.DataFrame, current_price: float) -> List[float]:
        """지지선 찾기"""
        recent_lows = df['low'].tail(60).nsmallest(10).values
        support = [l for l in recent_lows if l < current_price]
        return sorted(set([round(s) for s in support]), reverse=True)[:3]

    def _calculate_bollinger_upper(self, df: pd.DataFrame, period: int = 20, std: float = 2) -> float:
        """볼린저 밴드 상단 계산"""
        if len(df) < period:
            return None

        sma = df['close'].rolling(window=period).mean()
        rolling_std = df['close'].rolling(window=period).std()
        upper = sma + (rolling_std * std)

        return upper.iloc[-1] if not pd.isna(upper.iloc[-1]) else None

    def _determine_split_strategy(self, final_score: float,
                                  technical_score: float,
                                  supply_demand_score: float) -> Dict[str, Any]:
        """분할 매수 전략 결정"""
        if final_score >= 70 and technical_score >= 60:
            # 강력 매수 - 1회 전량 매수
            return {
                'type': 'single',
                'ratio': [1.0],
                'description': '전량 1회 매수'
            }
        elif final_score >= 60:
            # 매수 - 2분할
            return {
                'type': 'split_2',
                'ratio': [0.6, 0.4],
                'description': '60% + 40% 분할 매수 (조정시 추가)'
            }
        else:
            # 관망 또는 매도
            return {
                'type': 'wait',
                'ratio': [0],
                'description': '진입 보류'
            }

    def _generate_split_sell_plan(self, quantity: int,
                                  target_info: Dict[str, Any]) -> List[Dict[str, Any]]:
        """분할 매도 계획 생성"""
        if quantity == 0:
            return []

        return [
            {
                'target': target_info['target1'],
                'quantity': int(quantity * 0.3),
                'ratio': 30,
                'method': target_info.get('target1_method', '1차 목표')
            },
            {
                'target': target_info['target2'],
                'quantity': int(quantity * 0.4),
                'ratio': 40,
                'method': target_info.get('target2_method', '2차 목표')
            },
            {
                'target': target_info['target3'],
                'quantity': int(quantity * 0.3),
                'ratio': 30,
                'method': target_info.get('target3_method', '3차 목표')
            }
        ]

    def _generate_recommendation(self, entry_signal: Dict[str, Any],
                                 risk_reward_info: Dict[str, Any],
                                 analysis_result: Dict[str, Any]) -> str:
        """최종 추천 생성"""
        signal = entry_signal['signal']
        is_acceptable_rr = risk_reward_info['is_acceptable']

        if signal == 'STRONG_BUY' and is_acceptable_rr:
            return "✅ 적극 매수 추천 (리스크/리워드 양호)"
        elif signal == 'BUY' and is_acceptable_rr:
            return "✅ 매수 추천"
        elif signal == 'BUY' and not is_acceptable_rr:
            return "⚠️ 매수 고려 (리스크/리워드 낮음 - 신중)"
        elif signal == 'HOLD':
            return "⏸️ 관망 (진입 보류)"
        elif signal == 'SELL':
            return "❌ 매도 고려"
        else:
            return "❌ 적극 매도"
