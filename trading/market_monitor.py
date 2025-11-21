"""
시장 모니터링

실시간 종목 감시, 가격 데이터 조회, 시장 시간 체크
"""
from typing import Dict, List, Optional, Set
from datetime import datetime, time
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich import box

from kiwoom_api import KiwoomAPI
from exceptions import handle_api_errors, DataValidationError
from utils.stock_data_fetcher import download_stock_data_sync

console = Console()


class MarketMonitor:
    """시장 및 종목 실시간 모니터링"""

    def __init__(self, api: KiwoomAPI):
        """
        Args:
            api: KiwoomAPI 인스턴스
        """
        self.api = api

        # 시장 시간 설정 (기본값)
        self.market_open_time = time(9, 0)   # 09:00
        self.market_close_time = time(15, 30)  # 15:30

    def is_market_open(self) -> bool:
        """
        장 운영 시간 체크

        Returns:
            장 운영 여부
        """
        now = datetime.now()

        # 주말 체크
        if now.weekday() >= 5:  # 토요일(5), 일요일(6)
            return False

        # 시간 체크 (09:00 ~ 15:30)
        current_time = now.time()
        return self.market_open_time <= current_time <= self.market_close_time

    def get_market_status(self) -> Dict:
        """
        시장 상태 정보 조회

        Returns:
            {
                'is_open': bool,
                'current_time': str,
                'status_message': str,
                'time_until_open': int  # 초 (장 닫혀있을 때)
            }
        """
        now = datetime.now()
        is_open = self.is_market_open()
        current_time = now.strftime('%H:%M:%S')

        if is_open:
            status_message = "✅ 장 운영 중"
            time_until_open = 0
        else:
            if now.weekday() >= 5:
                status_message = "💤 주말 휴장"
                # 다음 월요일 09:00 까지 시간 계산
                days_until_monday = 7 - now.weekday()
                next_open = datetime.combine(
                    now.date(),
                    self.market_open_time
                ) + pd.Timedelta(days=days_until_monday)
            else:
                current_time_obj = now.time()
                if current_time_obj < self.market_open_time:
                    status_message = "⏰ 장 시작 전"
                    next_open = datetime.combine(now.date(), self.market_open_time)
                else:
                    status_message = "🔴 장 마감"
                    # 다음날 09:00
                    next_open = datetime.combine(
                        now.date(),
                        self.market_open_time
                    ) + pd.Timedelta(days=1)

            time_until_open = int((next_open - now).total_seconds())

        return {
            'is_open': is_open,
            'current_time': current_time,
            'status_message': status_message,
            'time_until_open': time_until_open
        }

    @handle_api_errors(default_return=None, log_errors=True)
    def get_realtime_price(self, stock_code: str) -> Optional[float]:
        """
        실시간 현재가 조회 (장중에만)

        Args:
            stock_code: 종목 코드

        Returns:
            현재가 (실패 시 None)
        """
        try:
            # 장 운영 시간 체크
            current_hour = datetime.now().hour
            current_minute = datetime.now().minute

            # 장중(9:00~15:30)이 아니면 None 반환
            if current_hour < 9 or current_hour > 15:
                return None
            if current_hour == 15 and current_minute >= 30:
                return None

            # 키움 API 호출
            price_result = self.api.get_stock_price(stock_code)

            if price_result.get('return_code') != 0:
                return None

            output = price_result.get('output') or price_result.get('output1')
            if not output:
                return None

            # 현재가 추출 (여러 키 시도)
            for key in ['stck_prpr', 'cur_prc', 'price', 'current_price']:
                if key in output:
                    price = float(output[key])
                    if price > 0:
                        return price

            return None

        except Exception as e:
            console.print(f"[dim]  ⚠️  {stock_code}: 실시간 가격 조회 실패 - {e}[/dim]")
            return None

    @handle_api_errors(default_return=None, log_errors=True)
    def get_stock_data(
        self,
        stock_code: str,
        stock_name: str,
        market: str = 'KOSPI'
    ) -> Optional[pd.DataFrame]:
        """
        종목 차트 데이터 조회 (키움 API → Yahoo Finance fallback)

        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            market: 시장 ('KOSPI' or 'KOSDAQ')

        Returns:
            OHLCV 데이터프레임 (실패 시 None)
        """
        current_hour = datetime.now().hour
        current_minute = datetime.now().minute

        df = None
        kiwoom_bars = 0

        # 1차: 장중(9:00~15:30)에만 5분봉 키움 API 호출
        if 9 <= current_hour < 16:
            try:
                result = self.api.get_minute_chart(
                    stock_code=stock_code,
                    tic_scope="5",
                    upd_stkpc_tp="1"
                )

                if result.get('return_code') == 0:
                    # 응답 데이터 키 탐색
                    data = None
                    for key in ['stk_min_pole_chart_qry', 'stk_mnut_pole_chart_qry',
                               'output', 'output1', 'output2', 'data']:
                        if key in result and result[key]:
                            data = result[key]
                            break

                    if data and len(data) > 0:
                        df = pd.DataFrame(data)

                        # 컬럼 매핑 (ka10080 API 기준)
                        column_mapping = {
                            'cur_prc': 'close',
                            'open_pric': 'open',
                            'high_pric': 'high',
                            'low_pric': 'low',
                            'trde_qty': 'volume',
                            # 다른 API 호환성
                            'stck_prpr': 'close', 'cur_price': 'close',
                            'stck_oprc': 'open', 'open_price': 'open',
                            'stck_hgpr': 'high', 'high_price': 'high',
                            'stck_lwpr': 'low', 'low_price': 'low',
                            'cntg_vol': 'volume', 'acml_vol': 'volume', 'vol': 'volume',
                            'acml_tr_pbmn': 'volume'
                        }
                        df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns}, inplace=True)

                        # 🔧 CRITICAL: 키움 API는 음수 부호로 하락을 표시 → 절대값 변환 필수!
                        for col in ['open', 'high', 'low', 'close', 'volume']:
                            if col in df.columns:
                                df[col] = pd.to_numeric(df[col], errors='coerce').abs()

                        # volume 컬럼이 없으면 기본값 추가
                        if 'volume' not in df.columns:
                            console.print(f"[yellow]  ⚠️  {stock_code}: volume 컬럼 없음, 기본값 사용[/yellow]")
                            df['volume'] = 1000

                        kiwoom_bars = len(df)
                        console.print(f"[dim]  ✓ {stock_code}: 키움 {kiwoom_bars}개 봉[/dim]")

            except Exception as e:
                console.print(f"[dim]  ⚠️  {stock_code}: 키움 API 오류 - {e}[/dim]")

        # 2차: 데이터 부족 시 Yahoo Finance로 보충
        if df is None or len(df) < 20:
            ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
            ticker = f"{stock_code}{ticker_suffix}"

            # 부족한 만큼 Yahoo에서 가져오기
            needed = 20 - (len(df) if df is not None else 0)
            days_needed = max(1, (needed // 70) + 1)  # 5분봉: 하루 약 70개

            yahoo_df = download_stock_data_sync(ticker, days=days_needed)

            if yahoo_df is not None and len(yahoo_df) > 0:
                if df is not None:
                    # 키움 + 야후 결합
                    df = pd.concat([yahoo_df, df], ignore_index=True).drop_duplicates()
                    console.print(f"[dim]  ✓ {stock_code}: 키움 {kiwoom_bars}개 + 야후 {len(yahoo_df)}개 = 총 {len(df)}개[/dim]")
                else:
                    df = yahoo_df
                    console.print(f"[dim]  ✓ {stock_code}: 야후 {len(df)}개 봉[/dim]")

        # 데이터 검증
        if df is None or len(df) < 20:
            console.print(f"[yellow]⚠️  {stock_code}: 데이터 부족 (len={len(df) if df is not None else 0})[/yellow]")
            return None

        return df

    def monitor_stocks(
        self,
        watchlist: Set[str],
        validated_stocks: Dict[str, Dict],
        positions: Dict[str, Dict]
    ) -> List[Dict]:
        """
        모든 종목 모니터링 및 데이터 수집

        Args:
            watchlist: 감시 종목 코드 set
            validated_stocks: 검증된 종목 정보 dict
            positions: 보유 포지션 dict

        Returns:
            종목 데이터 리스트
            [
                {
                    'code': str,
                    'name': str,
                    'current_price': float,
                    'dataframe': pd.DataFrame,  # 차트 데이터
                    'is_holding': bool
                },
                ...
            ]
        """
        current_time = datetime.now().strftime('%H:%M:%S')
        stock_data_list = []

        # 모니터링 대상: watchlist + 보유 종목 (중복 제거)
        all_stocks = set(watchlist) | set(positions.keys())

        for stock_code in all_stocks:
            try:
                # 종목 정보 가져오기
                if stock_code in validated_stocks:
                    stock_info = validated_stocks[stock_code]
                    stock_name = stock_info['name']
                    market = stock_info.get('market', 'KOSPI')
                elif stock_code in positions:
                    stock_name = positions[stock_code].get('name', stock_code)
                    market = 'KOSPI' if stock_code.startswith('0') else 'KOSDAQ'
                else:
                    console.print(f"[dim]⚠️  {stock_code}: 정보 없음[/dim]")
                    continue

                # 실시간 현재가 조회 (보유 종목 우선)
                realtime_price = None
                if stock_code in positions:
                    realtime_price = self.get_realtime_price(stock_code)

                # 차트 데이터 조회
                df = self.get_stock_data(stock_code, stock_name, market)

                if df is None or len(df) < 20:
                    # 데이터 조회 실패
                    stock_data_list.append({
                        'code': stock_code,
                        'name': stock_name,
                        'current_price': realtime_price or 0,
                        'dataframe': None,
                        'is_holding': stock_code in positions,
                        'data_available': False
                    })
                    continue

                # 현재가 결정 (실시간 우선, 없으면 차트 마지막 종가)
                current_price = realtime_price if realtime_price else df['close'].iloc[-1]

                # 가격 검증: 0 또는 음수면 스킵
                if current_price <= 0:
                    console.print(f"[yellow]⚠️  {stock_code}: 비정상 현재가 {current_price}[/yellow]")
                    continue

                stock_data_list.append({
                    'code': stock_code,
                    'name': stock_name,
                    'current_price': float(current_price),
                    'dataframe': df,
                    'is_holding': stock_code in positions,
                    'data_available': True,
                    'time': current_time
                })

            except Exception as e:
                import traceback
                error_msg = f"❌ {stock_code}: {e}\n{traceback.format_exc()}"
                console.print(f"[red]{error_msg}[/red]")
                continue

        return stock_data_list

    def display_monitoring_status(
        self,
        stock_data_list: List[Dict],
        positions: Dict[str, Dict]
    ) -> None:
        """
        모니터링 상태 간단 표시

        Args:
            stock_data_list: 종목 데이터 리스트
            positions: 보유 포지션 dict
        """
        market_status = self.get_market_status()

        console.print()
        console.print(f"[cyan]📊 시장 상태: {market_status['status_message']} ({market_status['current_time']})[/cyan]")
        console.print(f"[dim]모니터링 종목: {len(stock_data_list)}개 | 보유 포지션: {len(positions)}개[/dim]")

        # 데이터 조회 성공률
        available_count = sum(1 for s in stock_data_list if s.get('data_available', False))
        if stock_data_list:
            success_rate = (available_count / len(stock_data_list)) * 100
            console.print(f"[dim]데이터 조회 성공: {available_count}/{len(stock_data_list)} ({success_rate:.0f}%)[/dim]")

        console.print()

    def create_simple_status_table(
        self,
        stock_data_list: List[Dict]
    ) -> Table:
        """
        간단한 종목 현황 테이블 생성

        Args:
            stock_data_list: 종목 데이터 리스트

        Returns:
            Rich Table 객체
        """
        table = Table(
            title="📈 실시간 종목 현황",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan"
        )

        table.add_column("코드", style="yellow", width=8)
        table.add_column("종목명", style="white", width=12)
        table.add_column("현재가", justify="right", width=10)
        table.add_column("보유", justify="center", width=6)
        table.add_column("상태", justify="center", width=10)

        for data in stock_data_list:
            holding_status = "🔵" if data.get('is_holding') else ""
            data_status = "✅" if data.get('data_available') else "❌"

            table.add_row(
                data['code'],
                data['name'],
                f"{data['current_price']:,.0f}원" if data['current_price'] > 0 else "-",
                holding_status,
                data_status
            )

        return table
