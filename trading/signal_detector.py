"""
매매 신호 감지

VWAP 기반 매수/매도 신호 감지 및 검증
"""
from typing import Dict, Optional
from datetime import datetime
import pandas as pd
from rich.console import Console

from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from exceptions import handle_api_errors, DataValidationError
from config.config_manager import ConfigManager

console = Console()


class SignalDetector:
    """VWAP 기반 매매 신호 감지기"""

    def __init__(self, config: ConfigManager, analyzer: EntryTimingAnalyzer):
        """
        Args:
            config: ConfigManager 인스턴스
            analyzer: EntryTimingAnalyzer 인스턴스
        """
        self.config = config
        self.analyzer = analyzer

    @handle_api_errors(default_return=None, log_errors=True)
    def check_entry_signal(
        self,
        stock_code: str,
        stock_name: str,
        df: pd.DataFrame
    ) -> Optional[Dict]:
        """
        매수 신호 체크

        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            df: OHLCV 데이터프레임 (최소 50봉 권장)

        Returns:
            신호 정보 dict (신호 없으면 None)
            {
                'signal': 1,                    # 매수 신호
                'current_price': float,         # 현재가
                'current_vwap': float,         # 현재 VWAP
                'reason': str,                  # 신호 이유
                'confidence': float             # 신뢰도 (0.0~1.0)
            }
        """
        try:
            # 데이터 검증
            if df is None or len(df) < 50:
                console.print(f"[yellow]⚠️  {stock_code}: 데이터 부족 (len={len(df) if df is not None else 0})[/yellow]")
                return None

            # 🔁 시간 필터: 장 초반/말 회피
            time_cfg = self.config.get_section('time_filter')
            if time_cfg.get('use_time_filter', False):
                from datetime import timedelta

                now = datetime.now()
                now_time = now.time()

                # 시장 오픈/마감 시간
                open_time = datetime.strptime(time_cfg.get('market_open', '09:00'), '%H:%M').time()
                close_time = datetime.strptime(time_cfg.get('market_close', '15:20'), '%H:%M').time()

                # 회피 구간 계산
                avoid_early = int(time_cfg.get('avoid_early_minutes', 10))
                avoid_late = int(time_cfg.get('avoid_late_minutes', 10))

                early_end = (datetime.combine(now.date(), open_time) + timedelta(minutes=avoid_early)).time()
                late_start = (datetime.combine(now.date(), close_time) - timedelta(minutes=avoid_late)).time()

                # 초반 구간 (09:00~09:10) 또는 말 구간 (15:10~15:20) 회피
                if (open_time <= now_time <= early_end) or (late_start <= now_time <= close_time):
                    console.print(f"[dim]⏰ {stock_code}: 시간 필터 회피 ({now_time})[/dim]")
                    return None

            # VWAP 설정 가져오기
            vwap_config = self.config.get_section('vwap')
            use_rolling = vwap_config.get('use_rolling', True)
            rolling_window = vwap_config.get('rolling_window', 20)

            # VWAP 계산 및 신호 생성
            df = self.analyzer.calculate_vwap(df, use_rolling=use_rolling, rolling_window=rolling_window)
            df = self.analyzer.calculate_atr(df)

            signal_config = self.config.get_signal_generation_config()
            df = self.analyzer.generate_signals(df, **signal_config)

            # 최신 신호 확인
            latest_signal = df['signal'].iloc[-1]
            current_price = df['close'].iloc[-1]
            current_vwap = df['vwap'].iloc[-1]

            # 🚨 음수 가격 검증 (데이터 오류 방지)
            if current_price <= 0:
                raise DataValidationError(
                    f"비정상 현재가: {current_price}",
                    details={'stock_code': stock_code, 'price': current_price}
                )

            if latest_signal == 1:  # 매수 신호
                console.print(f"[yellow]🔔 {stock_name} ({stock_code}): 매수 신호 감지![/yellow]")

                # 🔴 GPT 개선: VWAP 이격도 + 기울기 필터 (Noise Zone 회피)
                price_vs_vwap_pct = ((current_price - current_vwap) / current_vwap) * 100

                # 이격도 필터: VWAP에서 충분히 떨어져 있는가?
                MIN_VWAP_DISTANCE = 0.4  # 최소 0.4% 이격
                if abs(price_vs_vwap_pct) < MIN_VWAP_DISTANCE:
                    console.print(f"[dim]❌ {stock_code}: VWAP 이격도 부족 ({price_vs_vwap_pct:.2f}% < {MIN_VWAP_DISTANCE}%)[/dim]")
                    return None

                # 기울기 필터: VWAP이 상승 추세인가?
                if len(df) >= 5:
                    vwap_5bars_ago = df['vwap'].iloc[-5]
                    vwap_slope_pct = ((current_vwap - vwap_5bars_ago) / vwap_5bars_ago) * 100
                    MIN_VWAP_SLOPE = 0.05  # 5봉(5분) 동안 최소 +0.05% 상승

                    if vwap_slope_pct < MIN_VWAP_SLOPE:
                        console.print(f"[dim]❌ {stock_code}: VWAP 기울기 부족 ({vwap_slope_pct:.3f}% < {MIN_VWAP_SLOPE}%)[/dim]")
                        return None

                    console.print(f"[green]✓ {stock_code}: VWAP 필터 통과 (이격 {price_vs_vwap_pct:.2f}%, 기울기 {vwap_slope_pct:.3f}%)[/green]")

                # 신뢰도 계산 (간단한 로직)
                confidence = min(1.0, max(0.5, 1.0 - abs(price_vs_vwap_pct) / 10))  # 0.5~1.0

                return {
                    'signal': 1,
                    'current_price': float(current_price),
                    'current_vwap': float(current_vwap),
                    'reason': f"VWAP 상향 돌파 (+{price_vs_vwap_pct:.2f}%, 기울기 {vwap_slope_pct:.3f}%)",
                    'confidence': confidence,
                    'dataframe': df  # 백테스트용
                }

            return None

        except Exception as e:
            console.print(f"[red]❌ {stock_code} 매수 신호 체크 실패: {e}[/red]")
            raise

    @handle_api_errors(default_return=None, log_errors=True)
    def check_exit_signal(
        self,
        stock_code: str,
        stock_name: str,
        position: Dict,
        df: pd.DataFrame
    ) -> Optional[Dict]:
        """
        매도 신호 체크 (6단계 청산 로직)

        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            position: 포지션 정보 dict
                {
                    'entry_price': float,
                    'quantity': int,
                    'highest_price': float,
                    'trailing_active': bool,
                    'partial_exit_stage': int  # 0, 1, 2
                }
            df: OHLCV 데이터프레임 (최소 50봉 권장)

        Returns:
            청산 신호 dict (신호 없으면 None)
            {
                'should_exit': bool,           # 전량 청산 여부
                'exit_type': str,              # 'full' or 'partial'
                'exit_ratio': float,           # 청산 비율 (0.0~1.0)
                'current_price': float,        # 현재가
                'profit_pct': float,           # 수익률
                'reason': str,                 # 청산 이유
                'stage': int                   # 부분 청산 단계 (1, 2)
            }
        """
        try:
            console.print(f"[dim]🔍 {stock_code}: 매도 신호 체크 시작[/dim]")

            # 데이터 검증
            if df is None or len(df) < 50:
                console.print(f"[yellow]⚠️  {stock_code}: 데이터 부족 (len={len(df) if df is not None else 0})[/yellow]")
                return None

            # VWAP 설정 가져오기
            vwap_config = self.config.get_section('vwap')
            use_rolling = vwap_config.get('use_rolling', True)
            rolling_window = vwap_config.get('rolling_window', 20)

            # VWAP 계산 및 신호 생성
            df = self.analyzer.calculate_vwap(df, use_rolling=use_rolling, rolling_window=rolling_window)
            df = self.analyzer.calculate_atr(df)

            signal_config = self.config.get_signal_generation_config()
            df = self.analyzer.generate_signals(df, **signal_config)

            # 최신 신호 확인
            latest_signal = df['signal'].iloc[-1]
            current_price = df['close'].iloc[-1]

            # 🚨 음수 가격 검증 (데이터 오류 방지)
            if current_price <= 0:
                raise DataValidationError(
                    f"비정상 현재가: {current_price}",
                    details={'stock_code': stock_code, 'price': current_price}
                )

            # 수익률 계산
            entry_price = position.get('entry_price', 0)
            if entry_price == 0:
                console.print(f"[red]❌ {stock_code}: 진입가 정보 없음[/red]")
                return None

            profit_pct = ((current_price - entry_price) / entry_price) * 100

            console.print(f"[dim]  💰 {stock_code}: 현재가 {current_price:,.0f}원, "
                        f"진입가 {entry_price:,.0f}원, 수익률 {profit_pct:+.2f}%[/dim]")

            # ========================================
            # 개선된 매도 로직 (우선순위 재정렬)
            # ========================================

            trailing_config = self.config.get_trailing_config()
            trailing_kwargs = {
                'use_atr_based': trailing_config.get('use_atr_based', False),
                'atr_multiplier': trailing_config.get('atr_multiplier', 1.5),
                'use_profit_tier': trailing_config.get('use_profit_tier', False),
                'profit_tier_threshold': trailing_config.get('profit_tier_threshold', 3.0)
            }
            stop_loss_pct = trailing_config.get('stop_loss_pct', 1.0)
            emergency_stop_pct = trailing_config.get('emergency_stop_pct', 2.0)

            # 우선순위 1: 긴급 손절 (-2.0%) - 최종 방어선
            if profit_pct <= -emergency_stop_pct:
                import logging
                logger = logging.getLogger(__name__)
                logger.critical(f"EMERGENCY STOP: {stock_code} {profit_pct:.2f}%")
                console.print(f"[bold red]🚨 긴급 손절 발동: {profit_pct:.2f}% (기준: -{emergency_stop_pct}%)[/bold red]")
                return {
                    'should_exit': True,
                    'exit_type': 'full',
                    'exit_ratio': 1.0,
                    'current_price': float(current_price),
                    'profit_pct': profit_pct,
                    'reason': f"긴급 손절 (-{emergency_stop_pct}%)",
                    'order_type': 'market',  # 시장가 강제
                    'urgent': True,
                    'stage': None
                }

            # 우선순위 2: Hard Stop (-1.0%)
            if profit_pct <= -stop_loss_pct:
                console.print(f"[red]⚠️  Hard Stop 발동: {profit_pct:.2f}% (기준: -{stop_loss_pct}%)[/red]")
                return {
                    'should_exit': True,
                    'exit_type': 'full',
                    'exit_ratio': 1.0,
                    'current_price': float(current_price),
                    'profit_pct': profit_pct,
                    'reason': f"손절 (-{stop_loss_pct}%)",
                    'order_type': 'market',  # 시장가 강제
                    'urgent': True,
                    'stage': None
                }

            # 우선순위 3: 부분 청산 (+1.0%, +2.0%)
            partial_exit_enabled = self.config.config.get('partial_exit', {}).get('enabled', False)
            if partial_exit_enabled:
                partial_exit_stage = position.get('partial_exit_stage', 0)
                tiers = self.config.config.get('partial_exit', {}).get('tiers', [])

                # 2차 청산 체크 (+2.0%, 30%)
                if partial_exit_stage < 2 and len(tiers) >= 2:
                    tier2 = tiers[1]
                    if profit_pct >= tier2['profit_pct']:
                        console.print(f"[green]🎯 2차 부분 청산 조건 충족: {profit_pct:.2f}% >= {tier2['profit_pct']}%[/green]")
                        return {
                            'should_exit': False,  # 부분 청산
                            'exit_type': 'partial',
                            'exit_ratio': tier2['exit_ratio'],
                            'current_price': float(current_price),
                            'profit_pct': profit_pct,
                            'reason': f"2차 부분 청산 (+{tier2['profit_pct']}%, {int(tier2['exit_ratio']*100)}% 매도)",
                            'stage': 2
                        }

                # 1차 청산 체크 (+1.0%, 30%)
                if partial_exit_stage < 1 and len(tiers) >= 1:
                    tier1 = tiers[0]
                    if profit_pct >= tier1['profit_pct']:
                        console.print(f"[green]🎯 1차 부분 청산 조건 충족: {profit_pct:.2f}% >= {tier1['profit_pct']}%[/green]")
                        return {
                            'should_exit': False,  # 부분 청산
                            'exit_type': 'partial',
                            'exit_ratio': tier1['exit_ratio'],
                            'current_price': float(current_price),
                            'profit_pct': profit_pct,
                            'reason': f"1차 부분 청산 (+{tier1['profit_pct']}%, {int(tier1['exit_ratio']*100)}% 매도)",
                            'stage': 1
                        }

            # 우선순위 4: 트레일링 스탑
            atr = df['atr'].iloc[-1] if 'atr' in df.columns else None

            trailing_result = self.analyzer.check_trailing_stop(
                current_price=current_price,
                avg_price=entry_price,
                highest_price=position.get('highest_price', entry_price),
                trailing_active=position.get('trailing_active', False),
                atr=atr,
                **trailing_kwargs
            )

            if trailing_result[0]:  # should_exit
                exit_reason = trailing_result[3]
                console.print(f"[yellow]📊 트레일링 스탑 발동: {exit_reason}[/yellow]")
                return {
                    'should_exit': True,
                    'exit_type': 'full',
                    'exit_ratio': 1.0,
                    'current_price': float(current_price),
                    'profit_pct': profit_pct,
                    'reason': exit_reason,
                    'stage': None,
                    'trailing_active': trailing_result[1],  # 업데이트된 trailing_active
                    'trailing_stop_price': trailing_result[2]  # trailing_stop_price
                }

            # 우선순위 5: VWAP 하향 돌파
            if latest_signal == -1:
                console.print("[yellow]📉 VWAP 하향 돌파 감지[/yellow]")
                return {
                    'should_exit': True,
                    'exit_type': 'full',
                    'exit_ratio': 1.0,
                    'current_price': float(current_price),
                    'profit_pct': profit_pct,
                    'reason': "VWAP 하향 돌파",
                    'stage': None
                }

            # 우선순위 6: 시간 기반 강제 청산 (15:00) - 마지막
            current_time = datetime.now().strftime("%H:%M:%S")
            if current_time >= "15:00:00":
                console.print(f"[yellow]⏰ 장 마감 전 강제 청산: {current_time}[/yellow]")
                return {
                    'should_exit': True,
                    'exit_type': 'full',
                    'exit_ratio': 1.0,
                    'current_price': float(current_price),
                    'profit_pct': profit_pct,
                    'reason': "장 마감 전 강제 청산 (15:00)",
                    'stage': None
                }

            # 신호 없음 (보유 유지)
            console.print(f"[dim]  ✓ {stock_code}: 보유 유지 (신호 없음)[/dim]")
            return None

        except Exception as e:
            console.print(f"[red]❌ {stock_code} 매도 신호 체크 실패: {e}[/red]")
            raise

    def get_signal_strength(self, df: pd.DataFrame) -> str:
        """
        신호 강도 판정

        Args:
            df: 신호가 생성된 데이터프레임

        Returns:
            신호 강도 ('강', '중', '약')
        """
        latest = df.iloc[-1]

        # 1. VWAP 거리
        price_vs_vwap_pct = abs((latest['close'] - latest['vwap']) / latest['vwap'] * 100)

        # 2. 거래량 증가율
        volume_ratio = 1.0
        if 'volume_ma' in df.columns and latest['volume_ma'] > 0:
            volume_ratio = latest['volume'] / latest['volume_ma']

        # 3. 추세 강도 (MA20 기준)
        trend_strength = 0
        if 'ma' in df.columns and latest['ma'] > 0:
            trend_strength = (latest['close'] - latest['ma']) / latest['ma'] * 100

        # 강도 판정
        strong_conditions = 0
        if price_vs_vwap_pct < 1.0:  # VWAP 근접
            strong_conditions += 1
        if volume_ratio >= 1.5:  # 거래량 1.5배 이상
            strong_conditions += 1
        if trend_strength >= 2.0:  # 추세 강함
            strong_conditions += 1

        if strong_conditions >= 2:
            return '강'
        elif strong_conditions >= 1:
            return '중'
        else:
            return '약'
