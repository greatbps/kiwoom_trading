"""
사전 매수 검증 시스템 (Pre-Trade Validator)

실제 매수 전에 해당 종목의 최근 성과를 빠르게 시뮬레이션하여
매수 여부를 결정하는 시스템

개선사항 (2025-11-07):
- 윌슨 하한(Wilson Lower Bound) 통계 기법 적용
- 표본 크기 확대 (5일 → 10일)
- VWAP 전략 현실 승률 반영 (50% → 40%)
- PF·평균수익률 중심 평가로 전환
"""
import pandas as pd
import numpy as np
import math
from typing import Dict, List, Tuple, Optional
from datetime import datetime, timedelta
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from utils.config_loader import ConfigLoader


class PreTradeValidator:
    """사전 매수 검증기"""

    def __init__(
        self,
        config: ConfigLoader,
        lookback_days: int = 10,        # 5 → 10 (표본 확대)
        min_trades: int = 6,            # 2 → 6 (통계적 유의성 확보)
        min_win_rate: float = 40.0,    # 50 → 40 (VWAP 전략 현실 승률)
        min_avg_profit: float = 0.3,   # 0.5 → 0.3 (완화)
        min_profit_factor: float = 1.15 # 1.2 → 1.15 (완화)
    ):
        """
        초기화

        Args:
            config: 전략 설정
            lookback_days: 검증 기간 (일) - 기본 10일
            min_trades: 최소 거래 횟수 - 기본 6회
            min_win_rate: 최소 승률 (%) - 기본 40%
            min_avg_profit: 최소 평균 수익률 (%) - 기본 +0.3%
            min_profit_factor: 최소 Profit Factor - 기본 1.15
        """
        self.config = config
        self.lookback_days = lookback_days
        self.min_trades = min_trades
        self.min_win_rate = min_win_rate
        self.min_avg_profit = min_avg_profit
        self.min_profit_factor = min_profit_factor

    def validate_trade(
        self,
        stock_code: str,
        stock_name: str,
        historical_data: pd.DataFrame,
        current_price: float,
        current_time: datetime
    ) -> Tuple[bool, str, Dict]:
        """
        매수 전 검증

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            historical_data: 과거 데이터 (5분봉)
            current_price: 현재가
            current_time: 현재 시간

        Returns:
            (allowed, reason, validation_stats)
        """
        # 1. 데이터 충분성 체크
        if historical_data is None or len(historical_data) < 100:
            return False, "데이터 부족 (최소 100봉 필요)", {}

        # 2. 빠른 시뮬레이션 실행
        trades = self._run_quick_simulation(historical_data)

        # 3. 통계 계산
        stats = self._calculate_stats(trades)

        # 4. 검증 기준 체크
        validation_result = self._check_validation_criteria(stats)

        return validation_result

    def _run_quick_simulation(self, df: pd.DataFrame) -> List[Dict]:
        """빠른 시뮬레이션 실행"""

        # Analyzer 초기화
        analyzer_config = self.config.get_analyzer_config()
        analyzer = EntryTimingAnalyzer(**analyzer_config)

        # Signal generation config
        signal_config = self.config.get_signal_generation_config()
        trailing_config = self.config.get_trailing_config()
        partial_config = self.config.get_partial_exit_config()

        # 데이터 복사
        df = df.copy()

        # 데이터 검증: close 가격이 0이거나 너무 작은 경우 필터링
        if 'close' in df.columns:
            df = df[df['close'] > 0].copy()  # 0 제거
            df = df[df['close'] > df['close'].median() * 0.01].copy()  # 중앙값의 1% 이하 제거 (이상치)

        if len(df) < 100:
            # 데이터가 너무 적으면 빈 리스트 반환
            return []

        # VWAP 설정 가져오기
        vwap_config = self.config.get_section('vwap')
        use_rolling = vwap_config.get('use_rolling', True)
        rolling_window = vwap_config.get('rolling_window', 20)

        # VWAP, ATR 계산
        df = analyzer.calculate_vwap(df, use_rolling=use_rolling, rolling_window=rolling_window)
        df = analyzer.calculate_atr(df)

        # 시그널 생성
        df = analyzer.generate_signals(df, **signal_config)

        # 시뮬레이션
        trades = []
        position = None
        executed_tiers = []

        for idx in range(len(df)):
            row = df.iloc[idx]
            current_price = row['close']
            signal = row['signal']

            # 가격 검증: 0이거나 너무 작은 값 스킵
            if current_price <= 0 or pd.isna(current_price):
                continue

            # 진입
            if position is None and signal == 1:
                position = {
                    'entry_price': current_price,
                    'quantity': 100,
                    'highest_price': current_price,
                    'trailing_active': False,
                    'entry_idx': idx
                }
                executed_tiers = []

            # 청산
            elif position is not None:
                # 최고가 갱신
                if current_price > position['highest_price']:
                    position['highest_price'] = current_price

                # 수익률 계산
                profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

                # 청산 여부 판단
                should_exit = False
                exit_reason = ""

                # 1. Hard Stop (실거래와 동일)
                stop_loss_pct = trailing_config.get('stop_loss_pct', 1.3)
                if profit_pct <= -stop_loss_pct:
                    should_exit = True
                    exit_reason = f"Hard Stop (-{stop_loss_pct}%)"

                # 2. 부분 청산
                elif partial_config['enabled'] and partial_config['tiers']:
                    partial_should_exit, exit_qty, reason, new_executed = analyzer.check_partial_exit(
                        current_price=current_price,
                        avg_price=position['entry_price'],
                        current_quantity=position['quantity'],
                        exit_tiers=partial_config['tiers'],
                        executed_tiers=executed_tiers
                    )

                    if partial_should_exit:
                        # 안전장치: entry_price가 0이면 거래 기록 안 함
                        if position['entry_price'] <= 0:
                            continue

                        profit = exit_qty * (current_price - position['entry_price'])
                        profit_pct_calc = ((current_price - position['entry_price']) / position['entry_price']) * 100

                        # 비정상적인 수익률 필터링 (-300% ~ +1000% 범위 밖)
                        if -300 < profit_pct_calc < 1000:
                            trades.append({
                                'entry_price': position['entry_price'],
                                'exit_price': current_price,
                                'profit': profit,
                                'profit_pct': profit_pct_calc,
                                'holding_bars': idx - position['entry_idx']
                            })

                        position['quantity'] -= exit_qty
                        executed_tiers = new_executed

                        if position['quantity'] <= 0:
                            position = None
                            executed_tiers = []
                        continue

                # 3. VWAP 하향 돌파 (실거래와 동일)
                elif signal == -1:
                    should_exit = True
                    exit_reason = "VWAP 하향 돌파"

                # 4. 트레일링 스탑 (실거래와 동일)
                if not should_exit:
                    atr = row.get('atr', None)
                    trailing_should_exit, trailing_active, stop_price, trailing_reason = analyzer.check_trailing_stop(
                        current_price=current_price,
                        avg_price=position['entry_price'],
                        highest_price=position['highest_price'],
                        trailing_active=position['trailing_active'],
                        atr=atr,
                        **trailing_config
                    )

                    position['trailing_active'] = trailing_active

                    if trailing_should_exit:
                        should_exit = True
                        exit_reason = trailing_reason

                # 전량 청산 실행
                if should_exit:
                    # 안전장치: entry_price가 0이면 거래 기록 안 함
                    if position['entry_price'] <= 0:
                        position = None
                        executed_tiers = []
                        continue

                    profit = position['quantity'] * (current_price - position['entry_price'])
                    profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

                    # 비정상적인 수익률 필터링 (-300% ~ +1000% 범위 밖)
                    if -300 < profit_pct < 1000:
                        trades.append({
                            'entry_price': position['entry_price'],
                            'exit_price': current_price,
                            'profit': profit,
                            'profit_pct': profit_pct,
                            'holding_bars': idx - position['entry_idx']
                        })

                    position = None
                    executed_tiers = []

        return trades

    def _wilson_lower_bound(self, wins: int, total: int, z: float = 1.96) -> float:
        """
        윌슨 점수 구간의 하한 계산 (95% 신뢰수준)

        작은 표본에서 승률이 과대/과소평가되는 것을 방지하는 통계 기법

        Args:
            wins: 승리 횟수
            total: 전체 거래 횟수
            z: z-score (1.96 = 95% 신뢰수준)

        Returns:
            보수적 승률 하한 (0~1)

        Example:
            3승 3패 (50%) → 단순승률 50%, 윌슨하한 ~21%
            30승 30패 (50%) → 단순승률 50%, 윌슨하한 ~42%
        """
        if total == 0:
            return 0.0

        p = wins / total  # 단순 승률
        denom = 1 + z**2 / total
        centre = p + z*z / (2*total)
        margin = z * math.sqrt((p*(1-p) + z*z/(4*total)) / total)

        return (centre - margin) / denom

    def _calculate_stats(self, trades: List[Dict]) -> Dict:
        """통계 계산"""
        if not trades:
            return {
                'total_trades': 0,
                'winning_trades': 0,
                'losing_trades': 0,
                'win_rate': 0,
                'avg_profit_pct': 0,
                'max_profit_pct': 0,
                'max_loss_pct': 0,
                'profit_factor': 0,
                'max_consecutive_losses': 0,
                'avg_holding_bars': 0
            }

        total_trades = len(trades)
        win_trades = [t for t in trades if t['profit'] > 0]
        loss_trades = [t for t in trades if t['profit'] < 0]

        win_rate = (len(win_trades) / total_trades * 100) if total_trades > 0 else 0
        avg_profit_pct = sum(t['profit_pct'] for t in trades) / total_trades

        # 최대 수익/손실
        max_profit_pct = max([t['profit_pct'] for t in trades]) if trades else 0
        max_loss_pct = min([t['profit_pct'] for t in trades]) if trades else 0

        # Profit Factor
        total_wins = sum(t['profit'] for t in win_trades) if win_trades else 0
        total_losses = abs(sum(t['profit'] for t in loss_trades)) if loss_trades else 1
        profit_factor = total_wins / total_losses if total_losses > 0 else 0

        # 연속 손실
        max_consecutive_losses = 0
        current_losses = 0
        for trade in trades:
            if trade['profit'] < 0:
                current_losses += 1
                max_consecutive_losses = max(max_consecutive_losses, current_losses)
            else:
                current_losses = 0

        # 평균 보유 시간
        avg_holding = sum(t['holding_bars'] for t in trades) / total_trades

        return {
            'total_trades': total_trades,
            'winning_trades': len(win_trades),
            'losing_trades': len(loss_trades),
            'win_rate': win_rate,
            'avg_profit_pct': avg_profit_pct,
            'max_profit_pct': max_profit_pct,
            'max_loss_pct': max_loss_pct,
            'profit_factor': profit_factor,
            'max_consecutive_losses': max_consecutive_losses,
            'avg_holding_bars': avg_holding,
            'win_count': len(win_trades),
            'loss_count': len(loss_trades)
        }

    def _check_validation_criteria(self, stats: Dict) -> Tuple[bool, str, Dict]:
        """
        검증 기준 체크 (개선 버전)

        전략:
        1. 최소 거래수는 필수 조건
        2. 승률은 윌슨 하한으로 보수적 평가
        3. PF·평균수익률 중심으로 합격 판단
        4. 과도한 단건 손실(-3% 이하)은 PF 강화 요구
        """
        checks = []

        # 1) 최소 거래수: 필수 조건
        if stats['total_trades'] < self.min_trades:
            checks.append(f"❌ 거래 부족 ({stats['total_trades']}/{self.min_trades}회)")
            reason = "필수 기준 미달\n" + "\n".join(checks)
            return False, reason, stats
        else:
            checks.append(f"✅ 거래 충분 ({stats['total_trades']}회)")

        # 2) 윌슨 하한(승률의 보수적 추정치)
        win_count = stats.get('win_count', 0)
        wlb = self._wilson_lower_bound(win_count, stats['total_trades']) * 100.0
        wilson_threshold = max(self.min_win_rate - 5.0, 30.0)  # 예: 40% → 35% 하한 요구

        if wlb < wilson_threshold:
            checks.append(f"❌ 승률(윌슨하한) 부족 ({wlb:.1f}%/{wilson_threshold:.1f}%)")
        else:
            checks.append(f"✅ 승률(윌슨하한) 양호 ({wlb:.1f}%, 단순승률 {stats['win_rate']:.1f}%)")

        # 3) PF 체크
        pf_ok = stats['profit_factor'] >= self.min_profit_factor

        # 4) 평균 수익률 체크
        apr_ok = stats['avg_profit_pct'] >= self.min_avg_profit

        # 5) 과도한 단건 손실 방지 (큰 음봉 리스크 컷)
        big_loss_guard = stats['max_loss_pct'] <= -3.0
        if big_loss_guard and stats['profit_factor'] < (self.min_profit_factor + 0.15):
            checks.append(f"❌ 과도한 최대손실({stats['max_loss_pct']:+.2f}%) & PF 부족({stats['profit_factor']:.2f})")
            reason = "리스크 기준 미달\n" + "\n".join(checks)
            return False, reason, stats

        # 통과 판정 규칙 (아래 중 하나라도 true면 합격)
        pass_core = (
            (pf_ok and apr_ok) or  # PF도 좋고 평균수익률도 좋음
            (stats['profit_factor'] >= (self.min_profit_factor + 0.10)) or  # PF가 기준보다 충분히 높음
            (apr_ok and wlb >= self.min_win_rate)  # 평균수익 양호 + 윌슨하한 승률도 괜찮음
        )

        if pass_core:
            # 합격 처리
            if pf_ok:
                checks.append(f"✅ PF 양호 ({stats['profit_factor']:.2f})")
            else:
                checks.append(f"⚠️ PF 아슬아슬 ({stats['profit_factor']:.2f}/{self.min_profit_factor})")

            if apr_ok:
                checks.append(f"✅ 평균수익률 양호 ({stats['avg_profit_pct']:+.2f}%)")
            else:
                checks.append(f"⚠️ 평균수익률 낮음 ({stats['avg_profit_pct']:+.2f}%/{self.min_avg_profit:+.2f}%)")

            reason = "핵심 기준 통과\n" + "\n".join(checks)
            return True, reason, stats
        else:
            # 불합격 처리
            if not pf_ok:
                checks.append(f"❌ PF 부족 ({stats['profit_factor']:.2f}/{self.min_profit_factor})")
            if not apr_ok:
                checks.append(f"❌ 평균수익률 부족 ({stats['avg_profit_pct']:+.2f}%/{self.min_avg_profit:+.2f}%)")

            failed_count = len([c for c in checks if c.startswith('❌')])
            reason = f"{failed_count}개 기준 미달\n" + "\n".join(checks)
            return False, reason, stats

    def get_validation_summary(self, stats: Dict) -> str:
        """검증 요약 문자열"""
        win_count = stats.get('win_count', 0)
        loss_count = stats.get('loss_count', 0)

        return f"""
📊 사전 검증 결과
━━━━━━━━━━━━━━━━
거래 횟수: {stats['total_trades']}회
승률: {stats['win_rate']:.1f}% ({win_count}승 {loss_count}패)
평균 수익률: {stats['avg_profit_pct']:+.2f}%
Profit Factor: {stats['profit_factor']:.2f}
최대 연속 손실: {stats['max_consecutive_losses']}회
평균 보유: {stats['avg_holding_bars']:.0f}봉
━━━━━━━━━━━━━━━━
"""


class AdaptiveValidator(PreTradeValidator):
    """적응형 검증기 (시장 상황에 따라 기준 조정)"""

    def __init__(self, config: ConfigLoader, **kwargs):
        super().__init__(config, **kwargs)
        self.market_condition = "NORMAL"  # BULL, BEAR, NORMAL

    def set_market_condition(self, condition: str):
        """시장 상황 설정"""
        self.market_condition = condition

        # 시장 상황에 따라 기준 조정
        if condition == "BULL":
            # 상승장: 기준 완화
            self.min_win_rate = 45.0
            self.min_avg_profit = 0.3
            self.min_profit_factor = 1.0
        elif condition == "BEAR":
            # 하락장: 기준 강화
            self.min_win_rate = 60.0
            self.min_avg_profit = 0.8
            self.min_profit_factor = 1.5
        else:
            # 정상: 기본값
            self.min_win_rate = 50.0
            self.min_avg_profit = 0.5
            self.min_profit_factor = 1.2

    def detect_market_condition(self, market_data: pd.DataFrame) -> str:
        """시장 상황 자동 감지"""
        if market_data is None or len(market_data) < 20:
            return "NORMAL"

        # 단순 판정: 최근 MA20 대비 현재가
        ma20 = market_data['close'].rolling(20).mean().iloc[-1]
        current = market_data['close'].iloc[-1]

        diff_pct = ((current - ma20) / ma20) * 100

        if diff_pct > 2.0:
            return "BULL"
        elif diff_pct < -2.0:
            return "BEAR"
        else:
            return "NORMAL"
