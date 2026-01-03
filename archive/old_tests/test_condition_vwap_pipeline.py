"""
키움 조건식 검색 → VWAP 2차 필터링 → 시뮬레이션 파이프라인

전체 플로우:
1. 키움 API 로그인 (REST + WebSocket)
2. 조건식 6개로 종목 검색 (1차 필터링)
3. VWAP 전략으로 2차 필터링 (사전 검증)
4. 최종 선정 종목 백테스트
5. 결과 리포트
"""
import asyncio
import websockets
import json
import sys
import os
from datetime import datetime, timedelta
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.pre_trade_validator import PreTradeValidator
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from utils.config_loader import load_config
from dotenv import load_dotenv
import yfinance as yf
import pandas as pd
import time
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

# 환경변수 로드
load_dotenv()

# WebSocket URL
SOCKET_URL = 'wss://api.kiwoom.com:10000/api/dostk/websocket'

console = Console()


def download_stock_data_for_validation(ticker: str, days: int = 7):
    """VWAP 검증용 데이터 다운로드"""
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=f"{days}d", interval="5m")

        if df.empty:
            return None

        df.reset_index(inplace=True)
        df.columns = [col.lower() for col in df.columns]
        return df

    except Exception as e:
        console.print(f"[red]❌ {ticker} 다운로드 실패: {e}[/red]")
        return None


def validate_single_stock(stock_code: str, stock_name: str, validator: PreTradeValidator):
    """단일 종목 VWAP 검증"""
    try:
        # 야후 파이낸스 형식으로 변환
        ticker = f"{stock_code}.KS"

        # 데이터 다운로드
        df = download_stock_data_for_validation(ticker, days=7)

        if df is None or len(df) < 100:
            return {
                'success': False,
                'stock_code': stock_code,
                'stock_name': stock_name,
                'reason': '데이터 부족'
            }

        # 사전 검증
        current_price = df['close'].iloc[-1]
        current_time = datetime.now()

        allowed, reason, stats = validator.validate_trade(
            stock_code=stock_code,
            stock_name=stock_name,
            historical_data=df,
            current_price=current_price,
            current_time=current_time
        )

        return {
            'success': True,
            'allowed': allowed,
            'stock_code': stock_code,
            'stock_name': stock_name,
            'reason': reason,
            'stats': stats,
            'ticker': ticker
        }

    except Exception as e:
        return {
            'success': False,
            'stock_code': stock_code,
            'stock_name': stock_name,
            'reason': f'오류: {str(e)}'
        }


class KiwoomVWAPPipeline:
    """키움 조건검색 + VWAP 검증 파이프라인"""

    def __init__(self, access_token: str, api: KiwoomAPI):
        self.uri = SOCKET_URL
        self.access_token = access_token
        self.api = api

        # 설정 로드
        self.config = load_config("config/strategy_hybrid.yaml")

        # VWAP 검증기 초기화
        self.validator = PreTradeValidator(
            config=self.config,
            lookback_days=5,
            min_trades=2,
            min_win_rate=50.0,
            min_avg_profit=0.5,
            min_profit_factor=1.2
        )

        self.websocket = None
        self.connected = False

        # 결과 저장
        self.condition_list = []
        self.condition_stocks = {}  # {seq: [stock_codes]}
        self.validated_stocks = []  # VWAP 검증 통과 종목
        self.validation_results = {}  # 전체 검증 결과

    async def connect(self):
        """WebSocket 연결"""
        try:
            self.websocket = await websockets.connect(self.uri)
            self.connected = True
            console.print("=" * 120, style="bold green")
            console.print(f"{'키움 조건식 → VWAP 필터링 파이프라인':^120}", style="bold green")
            console.print("=" * 120, style="bold green")
            console.print()
        except Exception as e:
            console.print(f"[red]❌ WebSocket 연결 실패: {e}[/red]")
            raise

    async def send_message(self, trnm: str, data: dict = None):
        """WebSocket 메시지 전송"""
        if not self.websocket or not self.connected:
            raise Exception("WebSocket이 연결되지 않았습니다.")

        message = {"trnm": trnm}

        if data:
            message.update(data)

        await self.websocket.send(json.dumps(message))

    async def receive_message(self):
        """WebSocket 메시지 수신"""
        if not self.websocket or not self.connected:
            raise Exception("WebSocket이 연결되지 않았습니다.")

        message = await self.websocket.recv()
        return json.loads(message)

    async def login(self):
        """WebSocket 로그인"""
        console.print(f"[{datetime.now().strftime('%H:%M:%S')}] WebSocket 로그인")

        # 로그인 패킷 (send_message 사용하지 않고 직접 전송)
        login_packet = {
            'trnm': 'LOGIN',
            'token': self.access_token
        }
        await self.websocket.send(json.dumps(login_packet))

        response = await self.receive_message()

        if response.get("return_code") == 0:
            console.print("✅ 로그인 성공", style="green")
            console.print()
            return True
        else:
            console.print(f"[red]❌ 로그인 실패: {response.get('return_msg')}[/red]")
            return False

    async def get_condition_list(self):
        """조건검색식 목록 조회"""
        console.print("[4] 조건검색식 목록 조회")
        console.print()

        await self.send_message("CNSRLST")
        response = await self.receive_message()

        if response.get("return_code") == 0:
            self.condition_list = response.get("data", [])

            # 테이블 출력
            table = Table(title="조건검색식 목록", box=box.DOUBLE)
            table.add_column("번호", style="cyan", justify="right")
            table.add_column("인덱스", style="yellow", justify="right")
            table.add_column("조건검색식명", style="green")

            for i, condition in enumerate(self.condition_list, 1):
                # condition is [seq, name]
                seq = condition[0]
                nm = condition[1]
                table.add_row(str(i), seq, nm)

            console.print(table)
            console.print()
            console.print(f"✅ 총 {len(self.condition_list)}개 조건검색식", style="green")
            console.print()

            return True
        else:
            console.print(f"[red]❌ 조건검색식 조회 실패: {response.get('return_msg')}[/red]")
            return False

    async def search_condition(self, seq: str, name: str):
        """조건검색 실행"""
        console.print(f"[{datetime.now().strftime('%H:%M:%S')}] 조건검색 실행")
        console.print(f"  조건식 번호: {seq}")
        console.print(f"  조건식명: {name}")
        console.print()

        await self.send_message("CNSRREQ", {
            "seq": seq,
            "search_type": "1",  # 조회타입
            "stex_tp": "K"  # 거래소구분 (K: 코스피/코스닥)
        })
        response = await self.receive_message()

        if response.get("return_code") == 0:
            stock_list = response.get("data", [])
            stock_codes = [s.get("jmcode", "").replace("A", "") for s in stock_list]
            stock_codes = [code for code in stock_codes if code]

            self.condition_stocks[seq] = stock_codes

            console.print("=" * 120, style="cyan")
            console.print(f"{'조건검색 결과 (1차 필터링)':^120}", style="bold cyan")
            console.print("=" * 120, style="cyan")
            console.print(f"조건식 번호: {seq}")
            console.print(f"발견 종목: {len(stock_codes)}개", style="green")
            console.print()

            if stock_codes:
                console.print("1차 필터링 종목 리스트:")
                console.print("─" * 120)
                for i, code in enumerate(stock_codes[:10], 1):
                    console.print(f"  {i}. {code}")
                if len(stock_codes) > 10:
                    console.print(f"  ... 외 {len(stock_codes) - 10}개")
                console.print("─" * 120)
                console.print()

            return stock_codes
        else:
            console.print(f"[red]❌ 조건검색 실패: {response.get('return_msg')}[/red]")
            return []

    def run_vwap_validation(self, stock_codes: List[str]):
        """VWAP 2차 검증 (배치 처리 with Rate Limiting)"""
        console.print("=" * 120, style="yellow")
        console.print(f"{'2차 필터링: VWAP 사전 검증':^120}", style="bold yellow")
        console.print("=" * 120, style="yellow")
        console.print()
        console.print(f"검증 기준:")
        console.print(f"  - 최소 거래: {self.validator.min_trades}회")
        console.print(f"  - 최소 승률: {self.validator.min_win_rate}%")
        console.print(f"  - 최소 평균 수익률: {self.validator.min_avg_profit:+.2f}%")
        console.print(f"  - 최소 Profit Factor: {self.validator.min_profit_factor}")
        console.print()

        # 배치 설정
        BATCH_SIZE = 5  # 5개씩 처리
        DELAY_BETWEEN_REQUESTS = 0.2  # 요청 간 200ms 대기
        DELAY_BETWEEN_BATCHES = 1.0  # 배치 간 1초 대기

        stock_info_list = []

        # 종목명 조회 (배치 처리)
        console.print(f"[cyan]📋 종목명 조회 중... (총 {len(stock_codes)}개)[/cyan]")
        for i, code in enumerate(stock_codes, 1):
            try:
                result = self.api.get_stock_info(stock_code=code)
                if result.get('return_code') == 0:
                    stock_name = result.get('stk_nm_kr', code)
                else:
                    stock_name = code
                stock_info_list.append((code, stock_name))

                console.print(f"  {i}/{len(stock_codes)} {code}: {stock_name}", style="dim")

                # Rate limiting
                time.sleep(DELAY_BETWEEN_REQUESTS)

                # 배치마다 추가 대기
                if i % BATCH_SIZE == 0:
                    console.print(f"  [yellow]⏸️  배치 완료, {DELAY_BETWEEN_BATCHES}초 대기...[/yellow]")
                    time.sleep(DELAY_BETWEEN_BATCHES)

            except Exception as e:
                console.print(f"  [red]❌ {code}: 조회 실패 ({str(e)})[/red]")
                stock_info_list.append((code, code))  # 코드를 이름으로 사용
                time.sleep(DELAY_BETWEEN_REQUESTS)

        console.print()
        console.print(f"[green]✅ 종목명 조회 완료: {len(stock_info_list)}개[/green]")
        console.print()

        # VWAP 검증 (배치 처리)
        console.print(f"[cyan]🔍 VWAP 검증 시작...[/cyan]")
        console.print()

        results = []
        for i, (code, name) in enumerate(stock_info_list, 1):
            console.print(f"[{i}/{len(stock_info_list)}] {name} ({code}) 검증 중...", style="dim")
            result = validate_single_stock(code, name, self.validator)
            results.append(result)

            # 배치마다 대기
            if i % BATCH_SIZE == 0:
                console.print(f"  [yellow]⏸️  {i}개 완료, 잠시 대기...[/yellow]")
                time.sleep(DELAY_BETWEEN_BATCHES)

        console.print()

        # 결과 분석
        for result in results:
            stock_code = result['stock_code']
            stock_name = result['stock_name']
            self.validation_results[stock_code] = result

            if result.get('allowed'):
                self.validated_stocks.append(result)
                stats = result.get('stats', {})
                console.print(
                    f"  ✅ {stock_name}: 승률 {stats.get('win_rate', 0):.1f}%, "
                    f"수익 {stats.get('avg_profit_pct', 0):+.1f}%",
                    style="green"
                )
            else:
                # 거부 사유는 로그에만 기록
                pass

        console.print()
        console.print(f"✅ 검증 통과: {len(self.validated_stocks)}개", style="green")
        console.print(f"❌ 검증 실패: {len(results) - len(self.validated_stocks)}개", style="red")
        console.print()

        return self.validated_stocks

    def generate_report(self):
        """최종 리포트 생성"""
        console.print()
        console.print("=" * 120, style="bold cyan")
        console.print(f"{'최종 리포트':^120}", style="bold cyan")
        console.print("=" * 120, style="bold cyan")
        console.print()

        # 1. 조건식 검색 결과
        console.print("1️⃣  조건식 검색 결과", style="bold")
        console.print()
        for seq, stocks in self.condition_stocks.items():
            # 조건식 이름 찾기 (condition은 [seq, name])
            cond_name = next((c[1] for c in self.condition_list if c[0] == seq), f"조건식{seq}")
            console.print(f"  📋 {cond_name}: {len(stocks)}개 종목")
        console.print()

        # 2. VWAP 검증 결과
        console.print("2️⃣  VWAP 2차 검증 결과", style="bold")
        console.print()
        console.print(f"  ✅ 통과: {len(self.validated_stocks)}개 종목")
        console.print()

        if self.validated_stocks:
            # 통과 종목 테이블
            table = Table(title="2차 검증 통과 종목", box=box.DOUBLE)
            table.add_column("종목명", style="cyan")
            table.add_column("코드", style="yellow")
            table.add_column("거래수", justify="right")
            table.add_column("승률", justify="right")
            table.add_column("평균수익률", justify="right", style="green")

            for stock in sorted(self.validated_stocks, key=lambda x: x['stats']['avg_profit_pct'], reverse=True):
                stats = stock['stats']
                table.add_row(
                    stock['stock_name'],
                    stock['stock_code'],
                    f"{stats['total_trades']}회",
                    f"{stats['win_rate']:.1f}%",
                    f"{stats['avg_profit_pct']:+.2f}%"
                )

            console.print(table)
            console.print()

        console.print("=" * 120, style="bold cyan")

    async def run_pipeline(self, condition_indices: List[int] = [31, 32, 33]):
        """전체 파이프라인 실행"""
        try:
            # WebSocket 연결
            await self.connect()

            # 로그인
            if not await self.login():
                return

            # 조건검색식 목록 조회
            if not await self.get_condition_list():
                return

            # 조건검색 실행
            console.print(f"[5] 조건검색식 실행 ({len(condition_indices)}개)")
            console.print("=" * 120, style="cyan")
            console.print()

            all_stocks = set()
            for idx in condition_indices:
                if idx < len(self.condition_list):
                    condition = self.condition_list[idx]
                    seq = condition[0]  # [seq, name]
                    name = condition[1]

                    stocks = await self.search_condition(seq, name)
                    all_stocks.update(stocks)

                    # 다음 조건 검색 전 대기
                    await asyncio.sleep(1)

            # 중복 제거
            unique_stocks = list(all_stocks)
            console.print(f"📊 중복 제거 후 총 {len(unique_stocks)}개 종목", style="bold green")
            console.print()

            # VWAP 2차 검증
            if unique_stocks:
                validated = self.run_vwap_validation(unique_stocks)

                # 최종 리포트
                self.generate_report()
            else:
                console.print("[yellow]⚠️  1차 필터링 종목이 없습니다.[/yellow]")

        finally:
            if self.websocket:
                await self.websocket.close()
                console.print()
                console.print("✅ WebSocket 연결 종료", style="green")


async def main():
    """메인 실행"""
    console.print()
    console.print("=" * 120, style="bold green")
    console.print(f"{'키움 조건식 검색 → VWAP 2차 필터링 파이프라인':^120}", style="bold green")
    console.print("=" * 120, style="bold green")
    console.print()

    # API 클라이언트 생성
    console.print("[1] 시스템 초기화")
    api = KiwoomAPI()
    console.print("  ✓ API 클라이언트 생성")
    console.print()

    # AccessToken 발급
    console.print("[2] AccessToken 발급")
    api.get_access_token()

    if not api.access_token:
        console.print("[red]❌ 토큰 발급 실패[/red]")
        return

    access_token = api.access_token
    console.print("✓ 접근 토큰 발급 성공", style="green")
    console.print()

    # WebSocket 클라이언트 생성
    console.print("[3] WebSocket 클라이언트 생성")
    pipeline = KiwoomVWAPPipeline(access_token, api)
    console.print()

    # 파이프라인 실행
    # 조건식 인덱스: Momentum(31), Breakout(32), EOD(33)
    await pipeline.run_pipeline(condition_indices=[17, 18, 19])  # GREAT_1016(17), 신고가_1023(18), 10분단타_1104(19)


if __name__ == "__main__":
    asyncio.run(main())
