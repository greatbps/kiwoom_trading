"""
MA Cross 복기 시스템 및 거래내역 비교
"""
from datetime import datetime, time
from typing import Dict, List, Optional, Tuple
import pandas as pd
from rich.console import Console

console = Console()


class TradeReviewAnalyzer:
    """MA Cross 전략 거래 복기 분석"""

    def __init__(self, db):
        """
        Args:
            db: TradingDatabase 인스턴스
        """
        self.db = db

    def analyze_ma_cross_entry(
        self,
        stock_code: str,
        entry_time: datetime,
        entry_price: float,
        df: pd.DataFrame
    ) -> Dict:
        """
        진입 시점 MA Cross 분석

        Args:
            stock_code: 종목 코드
            entry_time: 진입 시각
            entry_price: 진입 가격
            df: OHLCV + 지표 DataFrame

        Returns:
            Dict: {
                'ma_cross_timing': 'immediate' or 'delayed',
                'ma_cross_delay_bars': int,
                'entry_candle_type': 'strong_bull' | 'weak_bull' | 'doji' | 'bear',
                'price_location': 'breakout' | 'box_top' | 'box_middle' | 'box_bottom',
                'time_slot': '09:00_09:30' | ...
                'volume_ratio': float,
                'late_entry': bool,
                'no_volume': bool,
                'near_resistance': bool,
                'chasing_entry': bool
            }
        """
        try:
            # 진입 시점 데이터 찾기
            entry_df = df[df.index <= entry_time].tail(10)
            if len(entry_df) < 2:
                return self._get_default_entry_analysis()

            latest = entry_df.iloc[-1]
            prev = entry_df.iloc[-2]

            # MA Cross 타이밍 판단
            ma5_current = latest.get('ma5', latest.get('MA5', 0))
            ma10_current = latest.get('ma10', latest.get('MA10', 0))
            ma5_prev = prev.get('ma5', prev.get('MA5', 0))
            ma10_prev = prev.get('ma10', prev.get('MA10', 0))

            # 골든크로스 직후인지 확인
            just_crossed = (ma5_prev <= ma10_prev) and (ma5_current > ma10_current)
            ma_cross_timing = 'immediate' if just_crossed else 'delayed'

            # Cross 후 몇 봉 경과했는지 확인
            delay_bars = 0
            if not just_crossed:
                for i in range(len(entry_df) - 1, 0, -1):
                    candle = entry_df.iloc[i]
                    prev_candle = entry_df.iloc[i - 1]

                    c_ma5 = candle.get('ma5', candle.get('MA5', 0))
                    c_ma10 = candle.get('ma10', candle.get('MA10', 0))
                    p_ma5 = prev_candle.get('ma5', prev_candle.get('MA5', 0))
                    p_ma10 = prev_candle.get('ma10', prev_candle.get('MA10', 0))

                    if (p_ma5 <= p_ma10) and (c_ma5 > c_ma10):
                        delay_bars = len(entry_df) - 1 - i
                        break

            # 캔들 타입 판단
            open_price = latest.get('open', latest.get('Open', 0))
            close_price = latest.get('close', latest.get('Close', 0))
            high_price = latest.get('high', latest.get('High', 0))
            low_price = latest.get('low', latest.get('Low', 0))

            candle_body = abs(close_price - open_price)
            candle_range = high_price - low_price

            if close_price < open_price:
                entry_candle_type = 'bear'
            elif candle_range > 0 and candle_body / candle_range < 0.3:
                entry_candle_type = 'doji'
            elif candle_body / open_price > 0.02:  # 2% 이상 상승
                entry_candle_type = 'strong_bull'
            else:
                entry_candle_type = 'weak_bull'

            # 가격 위치 판단
            recent_high = entry_df['high'].max() if 'high' in entry_df else entry_df['High'].max()
            recent_low = entry_df['low'].min() if 'low' in entry_df else entry_df['Low'].min()
            price_range = recent_high - recent_low

            if price_range > 0:
                position_in_range = (entry_price - recent_low) / price_range

                if entry_price >= recent_high * 0.99:
                    price_location = 'breakout'
                elif position_in_range > 0.7:
                    price_location = 'box_top'
                elif position_in_range > 0.3:
                    price_location = 'box_middle'
                else:
                    price_location = 'box_bottom'
            else:
                price_location = 'box_middle'

            # 시간대 구분
            entry_hour = entry_time.hour
            entry_minute = entry_time.minute

            if entry_hour == 9 and entry_minute < 30:
                time_slot = '09:00_09:30'
            elif entry_hour == 9 or (entry_hour == 10 and entry_minute < 30):
                time_slot = '09:30_10:30'
            elif entry_hour < 13 or (entry_hour == 13 and entry_minute < 30):
                time_slot = '10:30_13:30'
            elif entry_hour < 14 or (entry_hour == 14 and entry_minute < 30):
                time_slot = '13:30_14:30'
            else:
                time_slot = 'after_14:30'

            # 거래량 비율
            volume_current = latest.get('volume', latest.get('Volume', 0))
            volume_avg = entry_df['volume'].mean() if 'volume' in entry_df else entry_df['Volume'].mean()
            volume_ratio = volume_current / volume_avg if volume_avg > 0 else 1.0

            # 실패 패턴 플래그
            late_entry = delay_bars >= 2
            no_volume = volume_ratio < 1.2
            near_resistance = price_location == 'box_top'
            chasing_entry = entry_candle_type == 'strong_bull' and delay_bars > 0

            return {
                'ma_cross_timing': ma_cross_timing,
                'ma_cross_delay_bars': delay_bars,
                'entry_candle_type': entry_candle_type,
                'price_location': price_location,
                'time_slot': time_slot,
                'volume_ratio': round(volume_ratio, 2),
                'late_entry': late_entry,
                'no_volume': no_volume,
                'near_resistance': near_resistance,
                'chasing_entry': chasing_entry
            }

        except Exception as e:
            console.print(f"[red]❌ MA Cross 진입 분석 실패: {e}[/red]")
            return self._get_default_entry_analysis()

    def _get_default_entry_analysis(self) -> Dict:
        """기본 진입 분석 (실패 시)"""
        return {
            'ma_cross_timing': 'immediate',
            'ma_cross_delay_bars': 0,
            'entry_candle_type': 'weak_bull',
            'price_location': 'box_middle',
            'time_slot': '09:30_10:30',
            'volume_ratio': 1.0,
            'late_entry': False,
            'no_volume': False,
            'near_resistance': False,
            'chasing_entry': False
        }

    def save_trade_review(
        self,
        trade_id: str,
        symbol: str,
        trade_date: str,
        timeframe: str,
        entry_time: datetime,
        entry_price: float,
        entry_analysis: Dict,
        exit_time: Optional[datetime] = None,
        exit_price: Optional[float] = None,
        exit_type: Optional[str] = None,
        pnl_pct: Optional[float] = None,
        max_adverse_excursion_pct: Optional[float] = None,
        golden_cross_duration_bars: Optional[int] = None
    ) -> bool:
        """
        복기 데이터 저장

        Args:
            trade_id: 거래 ID (YYYYMMDD_HHMMSS_SYMBOL)
            symbol: 종목 코드
            trade_date: 거래 날짜 (YYYY-MM-DD)
            timeframe: 봉 주기 ('1m', '3m', '5m')
            entry_time: 진입 시각
            entry_price: 진입 가격
            entry_analysis: analyze_ma_cross_entry() 결과
            exit_time: 청산 시각 (선택)
            exit_price: 청산 가격 (선택)
            exit_type: 청산 유형 ('dead_cross', 'hard_stop')
            pnl_pct: 손익률 (%)
            max_adverse_excursion_pct: 최대 역행폭 (MAE)
            golden_cross_duration_bars: 골든크로스 유지 봉 수

        Returns:
            bool: 저장 성공 여부
        """
        # 결과 판단
        result = None
        if pnl_pct is not None:
            if pnl_pct > 0.5:
                result = 'profit'
            elif pnl_pct < -0.5:
                result = 'loss'
            else:
                result = 'breakeven'

        review_data = {
            'trade_id': trade_id,
            'symbol': symbol,
            'trade_date': trade_date,
            'timeframe': timeframe,
            'entry_time': entry_time,
            'entry_price': entry_price,
            'exit_time': exit_time,
            'exit_price': exit_price,
            'pnl_pct': pnl_pct,
            'exit_type': exit_type,
            'ma_cross_timing': entry_analysis['ma_cross_timing'],
            'ma_cross_delay_bars': entry_analysis['ma_cross_delay_bars'],
            'golden_cross_duration_bars': golden_cross_duration_bars,
            'max_adverse_excursion_pct': max_adverse_excursion_pct,
            'entry_candle_type': entry_analysis['entry_candle_type'],
            'price_location': entry_analysis['price_location'],
            'time_slot': entry_analysis['time_slot'],
            'volume_ratio': entry_analysis['volume_ratio'],
            'late_entry': entry_analysis['late_entry'],
            'no_volume': entry_analysis['no_volume'],
            'near_resistance': entry_analysis['near_resistance'],
            'chasing_entry': entry_analysis['chasing_entry'],
            'sudden_drop_before_dead': False,  # TODO: 청산 시 분석
            'result': result
        }

        return self.db.insert_trade_review(review_data)


class TradeReconciliationService:
    """키움 API vs DB 거래내역 비교 서비스"""

    def __init__(self, db, kiwoom_api):
        """
        Args:
            db: TradingDatabase 인스턴스
            kiwoom_api: KiwoomService 인스턴스
        """
        self.db = db
        self.kiwoom_api = kiwoom_api

    def compare_daily_trades(self, trade_date: str = None) -> Dict:
        """
        일일 거래내역 비교

        Args:
            trade_date: 비교 날짜 (YYYYMMDD, None이면 당일)

        Returns:
            Dict: {
                'is_matched': bool,
                'db_trades': List[Dict],
                'api_trades': List[Dict],
                'missing_trades': List[Dict],  # API에는 있지만 DB에 없음
                'extra_trades': List[Dict],  # DB에는 있지만 API에 없음
                'summary': Dict
            }
        """
        if not trade_date:
            trade_date = datetime.now().strftime("%Y%m%d")

        trade_date_formatted = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"

        console.print(f"\n[cyan]📊 거래내역 비교 시작: {trade_date_formatted}[/cyan]")

        # 1. DB에서 거래내역 조회
        db_trades = self.db.get_trades(
            start_date=trade_date_formatted,
            end_date=trade_date_formatted
        )

        # 2. 키움 API에서 거래내역 조회
        api_trades = self.kiwoom_api.get_daily_trade_history(trade_date)

        # 3. 집계
        db_buy_count = sum(1 for t in db_trades if t['trade_type'] == 'BUY')
        db_sell_count = sum(1 for t in db_trades if t['trade_type'] == 'SELL')
        db_total_amount = sum(t.get('amount', 0) for t in db_trades)

        api_buy_count = sum(1 for t in api_trades if t['trade_type'] == 'BUY')
        api_sell_count = sum(1 for t in api_trades if t['trade_type'] == 'SELL')
        api_total_amount = sum(t.get('amount', 0) for t in api_trades)

        # 4. 차이 분석
        missing_trades = []
        extra_trades = []

        # API에 있는데 DB에 없는 거래 (누락)
        for api_trade in api_trades:
            found = False
            for db_trade in db_trades:
                if (db_trade['stock_code'] == api_trade['stock_code'] and
                    db_trade['trade_type'] == api_trade['trade_type'] and
                    abs(db_trade['price'] - api_trade['price']) < 10 and
                    db_trade['quantity'] == api_trade['quantity']):
                    found = True
                    break

            if not found:
                missing_trades.append(api_trade)

        # DB에 있는데 API에 없는 거래 (잘못된 기록)
        for db_trade in db_trades:
            found = False
            for api_trade in api_trades:
                if (db_trade['stock_code'] == api_trade['stock_code'] and
                    db_trade['trade_type'] == api_trade['trade_type'] and
                    abs(db_trade['price'] - api_trade['price']) < 10 and
                    db_trade['quantity'] == api_trade['quantity']):
                    found = True
                    break

            if not found:
                extra_trades.append(db_trade)

        is_matched = len(missing_trades) == 0 and len(extra_trades) == 0

        # 5. 결과 출력
        if is_matched:
            console.print(f"[green]✅ 거래내역 일치 (총 {len(api_trades)}건)[/green]")
        else:
            console.print(f"[red]❌ 거래내역 불일치 감지![/red]")
            if missing_trades:
                console.print(f"[yellow]  - 누락된 거래: {len(missing_trades)}건[/yellow]")
                for trade in missing_trades:
                    console.print(f"    • {trade['stock_name']} {trade['trade_type']} "
                                f"{trade['quantity']}주 @ {trade['price']:,}원")

            if extra_trades:
                console.print(f"[yellow]  - 불일치 거래: {len(extra_trades)}건[/yellow]")
                for trade in extra_trades:
                    console.print(f"    • {trade['stock_name']} {trade['trade_type']} "
                                f"{trade['quantity']}주 @ {trade['price']:,}원")

        # 6. DB에 비교 결과 저장
        discrepancy_detail = ""
        if not is_matched:
            discrepancy_detail = f"누락 {len(missing_trades)}건, 불일치 {len(extra_trades)}건"

        reconcile_data = {
            'trade_date': trade_date_formatted,
            'db_trade_count': len(db_trades),
            'db_buy_count': db_buy_count,
            'db_sell_count': db_sell_count,
            'db_total_amount': db_total_amount,
            'api_trade_count': len(api_trades),
            'api_buy_count': api_buy_count,
            'api_sell_count': api_sell_count,
            'api_total_amount': api_total_amount,
            'is_matched': is_matched,
            'missing_trades': missing_trades,
            'extra_trades': extra_trades,
            'discrepancy_detail': discrepancy_detail
        }

        self.db.insert_trade_reconciliation(reconcile_data)

        return {
            'is_matched': is_matched,
            'db_trades': db_trades,
            'api_trades': api_trades,
            'missing_trades': missing_trades,
            'extra_trades': extra_trades,
            'summary': {
                'db': {'total': len(db_trades), 'buy': db_buy_count, 'sell': db_sell_count},
                'api': {'total': len(api_trades), 'buy': api_buy_count, 'sell': api_sell_count}
            }
        }
