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

# 프로젝트 루트 경로 추가
if __name__ == "__main__":
    project_root = Path(__file__).parent
    sys.path.insert(0, str(project_root))

from kiwoom_api import KiwoomAPI
from analyzers.pre_trade_validator import PreTradeValidator
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from analyzers.analysis_engine import AnalysisEngine
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


def download_stock_data_sync(ticker: str, days: int = 7):
    """Yahoo Finance에서 데이터 다운로드 (동기 버전)"""
    import warnings
    import logging
    import sys
    from io import StringIO

    # stderr 임시 리다이렉트 (yfinance가 stderr로 출력하는 경우 대비)
    old_stderr = sys.stderr
    sys.stderr = StringIO()

    # 모든 yfinance 관련 로거 비활성화
    yf_loggers = [
        logging.getLogger('yfinance'),
        logging.getLogger('yfinance.base_downloader'),
        logging.getLogger('yfinance.data'),
        logging.getLogger('yfinance.utils'),
        logging.getLogger('peewee')
    ]
    original_levels = [(logger, logger.level, logger.disabled) for logger in yf_loggers]

    for logger in yf_loggers:
        logger.setLevel(logging.CRITICAL)
        logger.disabled = True

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            warnings.filterwarnings("ignore", category=FutureWarning)
            warnings.filterwarnings("ignore", category=DeprecationWarning)

            stock = yf.Ticker(ticker)
            df = stock.history(period=f"{days}d", interval="5m", progress=False)

        if df.empty:
            return None

        df.reset_index(inplace=True)
        df.columns = [col.lower() for col in df.columns]
        return df

    except Exception:
        return None
    finally:
        # 원래 설정 복원
        sys.stderr = old_stderr

        # 로거 상태 복원
        for logger, level, disabled in original_levels:
            logger.setLevel(level)
            logger.disabled = disabled


async def download_stock_data_yahoo(ticker: str, days: int = 7, try_kq: bool = True):
    """
    Yahoo Finance에서 데이터 다운로드 (비동기, .KS/.KQ 자동 전환)

    Args:
        ticker: 종목 코드 (6자리)
        days: 조회 기간
        try_kq: .KS 실패 시 .KQ 시도 여부

    Returns:
        DataFrame or None
    """
    # .KS 시도
    ticker_ks = f"{ticker}.KS"
    try:
        df = await asyncio.to_thread(download_stock_data_sync, ticker_ks, days)
        if df is not None and not df.empty:
            return df
    except Exception:
        pass

    # .KQ 시도
    if try_kq:
        ticker_kq = f"{ticker}.KQ"
        try:
            df = await asyncio.to_thread(download_stock_data_sync, ticker_kq, days)
            if df is not None and not df.empty:
                return df
        except Exception:
            pass

    return None


def download_stock_data_for_validation(ticker: str, days: int = 7):
    """VWAP 검증용 데이터 다운로드 (레거시 동기 버전, 하위 호환성 유지)"""
    return download_stock_data_sync(ticker, days)


def get_kiwoom_minute_data_sync(api: KiwoomAPI, stock_code: str, required_bars: int = 100):
    """
    키움 API에서 분봉 데이터 조회 (5분봉) - 동기 버전

    Args:
        api: KiwoomAPI 인스턴스
        stock_code: 종목코드 (6자리)
        required_bars: 필요한 최소 봉 개수

    Returns:
        DataFrame or None
    """
    try:
        result = api.get_minute_chart(
            stock_code=stock_code,
            tic_scope="5",
            upd_stkpc_tp="1"
        )

        # 응답 코드 확인
        return_code = result.get('return_code')
        if return_code != 0:
            return None

        # 응답 데이터 키 탐색
        data = None
        for key in ['stk_min_pole_chart_qry', 'stk_mnut_pole_chart_qry', 'output', 'output1', 'output2', 'data']:
            if key in result and result[key]:
                data = result[key]
                break

        if not data or len(data) == 0:
            return None

        df = pd.DataFrame(data)
        if df.empty:
            return None

        # cntr_tm → 날짜/시간
        if 'cntr_tm' in df.columns:
            df['datetime'] = df['cntr_tm'].astype(str).str[:8]
            df['time'] = df['cntr_tm'].astype(str).str[8:14]
            df.drop(columns=['cntr_tm'], inplace=True, errors='ignore')

        # 안전한 컬럼 매핑 (ka10080 API 기준)
        column_mapping = {
            'dt': 'datetime', 'tm': 'time',
            'stck_bsop_date': 'datetime', 'stck_cntg_hour': 'time',
            'cur_prc': 'close', 'stck_prpr': 'close',      # 현재가
            'open_pric': 'open', 'stck_oprc': 'open',      # 시가
            'high_pric': 'high', 'stck_hgpr': 'high',      # 고가
            'low_pric': 'low', 'stck_lwpr': 'low',         # 저가
            'trde_qty': 'volume1', 'cntg_vol': 'volume2', 'acc_trde_qty': 'volume3'
        }
        df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns}, inplace=True)

        # volume 통합
        volume_cols = [col for col in df.columns if 'volume' in col.lower()]
        if volume_cols:
            df['volume'] = df[volume_cols].apply(pd.to_numeric, errors='coerce').abs().mean(axis=1, skipna=True)
            df.drop(columns=volume_cols, inplace=True, errors='ignore')

        # 필수 컬럼 확인
        required_cols = ['datetime', 'time', 'open', 'high', 'low', 'close', 'volume']
        if not all(col in df.columns for col in required_cols):
            return None

        # 🔧 CRITICAL: 키움 API는 음수 부호로 하락을 표시 → 절대값 변환 필수!
        # 예: cur_prc="-78800" → 실제 가격은 78,800원
        for col in ['open', 'high', 'low', 'close', 'volume']:
            df[col] = pd.to_numeric(df[col], errors='coerce').abs()

        # 정렬
        df.sort_values(by=['datetime', 'time'], ascending=True, inplace=True, ignore_index=True)

        return df

    except Exception:
        return None


async def get_kiwoom_minute_data(api: KiwoomAPI, stock_code: str, required_bars: int = 100):
    """키움 API에서 분봉 데이터 조회 (비동기 래퍼)"""
    return await asyncio.to_thread(get_kiwoom_minute_data_sync, api, stock_code, required_bars)


async def validate_single_stock_hybrid(stock_code: str, stock_name: str, validator: PreTradeValidator, api: KiwoomAPI):
    """
    단일 종목 VWAP 검증 (하이브리드 데이터 소스)

    1. 키움 API에서 5분봉 데이터 조회 (우선)
    2. 데이터 부족 시 Yahoo Finance로 보충 (.KS/.KQ 자동 전환)
    3. VWAP 검증 실행

    Args:
        stock_code: 종목코드 (6자리)
        stock_name: 종목명
        validator: PreTradeValidator 인스턴스
        api: KiwoomAPI 인스턴스

    Returns:
        검증 결과 딕셔너리
    """
    try:
        required_bars = 100  # 필요한 최소 봉 개수
        df = None

        # 1단계: 키움 API 시도
        df = await get_kiwoom_minute_data(api, stock_code, required_bars)

        # 2단계: 데이터 부족 시 Yahoo로 보충
        if df is None or len(df) < required_bars:
            current_bars = len(df) if df is not None else 0

            yahoo_df = await download_stock_data_yahoo(stock_code, days=7, try_kq=True)

            if yahoo_df is None or yahoo_df.empty:
                return {
                    'success': False,
                    'allowed': False,
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'reason': f'데이터 없음 (키움:{current_bars}개, 야후:실패)'
                }

            # 키움 데이터와 Yahoo 데이터 병합
            if df is not None and not df.empty:
                # 기존 키움 데이터에 Yahoo 데이터를 앞에 추가
                df = pd.concat([yahoo_df, df], ignore_index=True)
                # 중복 제거 (datetime, time 기준)
                if 'datetime' in df.columns and 'time' in df.columns:
                    df = df.drop_duplicates(subset=['datetime', 'time'], keep='last').reset_index(drop=True)
            else:
                df = yahoo_df

        # 최종 데이터 검증
        if df is None or len(df) < required_bars:
            return {
                'success': False,
                'allowed': False,
                'stock_code': stock_code,
                'stock_name': stock_name,
                'reason': f'데이터 부족 ({len(df) if df is not None else 0}개 < {required_bars}개)'
            }

        # VWAP 검증
        current_price = df['close'].iloc[-1]
        current_time = datetime.now()

        allowed, reason, stats = validator.validate_trade(
            stock_code=stock_code,
            stock_name=stock_name,
            historical_data=df,
            current_price=current_price,
            current_time=current_time
        )

        # 시장 정보 기반으로 티커 결정 (.KS/.KQ 자동 전환)
        market = 'KOSPI' if stock_code.startswith('0') else 'KOSDAQ'
        ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
        ticker = f"{stock_code}{ticker_suffix}"

        return {
            'success': True,
            'allowed': allowed,
            'stock_code': stock_code,
            'stock_name': stock_name,
            'reason': reason,
            'stats': stats,
            'ticker': ticker,
            'market': market
        }

    except Exception as e:
        return {
            'success': False,
            'allowed': False,
            'stock_code': stock_code,
            'stock_name': stock_name,
            'reason': f'오류: {str(e)}'
        }


def validate_single_stock(stock_code: str, stock_name: str, validator: PreTradeValidator):
    """
    단일 종목 VWAP 검증 (개선: StockDataFetcher 사용, .KS/.KQ 자동 전환)
    """
    try:
        from utils.stock_data_fetcher import StockDataFetcher

        # StockDataFetcher 사용 (.KS/.KQ 자동 전환)
        fetcher = StockDataFetcher(verbose=False)
        df = fetcher.fetch_sync(
            stock_code,
            days=7,
            source='yahoo',  # 레거시 함수는 야후만 사용
            interval='5m'
        )

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

        # VWAP 검증기 초기화 (최적화된 기준값 적용)
        self.validator = PreTradeValidator(
            config=self.config,
            lookback_days=10,        # 5 → 10 (표본 확대)
            min_trades=6,            # 2 → 6 (통계적 유의성)
            min_win_rate=40.0,       # 50 → 40 (VWAP 전략 현실 승률)
            min_avg_profit=0.3,      # 0.5 → 0.3 (완화)
            min_profit_factor=1.15   # 1.2 → 1.15 (완화)
        )

        # 종합 분석 엔진 초기화
        console.print("  [cyan]📊 종합 분석 엔진 초기화 중...[/cyan]")
        self.analysis_engine = AnalysisEngine()
        console.print("  [green]✓ 분석 엔진 준비 완료 (뉴스 + 기술 + 수급 + 기본)[/green]")

        # DB 초기화
        from database.trading_db import TradingDatabase
        self.db = TradingDatabase("data/trading.db")

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
            # 로그인 완료 후 충분한 대기 시간 (서버 인증 처리 완료 대기)
            # 키움 서버가 인증을 완전히 처리하기까지 시간이 필요
            await asyncio.sleep(3.0)  # 1.5초 → 3초로 증가
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
            console.print(f"✅ 총 {len(self.condition_list)}개 조건검색식 조회 완료", style="green")
            console.print()
            # 조건검색 실행 전 대기 (서버 준비 시간 확보)
            await asyncio.sleep(2.0)  # 1초 → 2초로 증가
            return True
        else:
            console.print(f"[red]❌ 조건검색식 조회 실패: {response.get('return_msg')}[/red]")
            return False

    def get_stock_names(self, stock_codes: List[str]) -> Dict[str, str]:
        """종목코드로 종목명 조회 (배치)"""
        import sys
        stock_names = {}
        total = len(stock_codes)

        # 초기 메시지 출력
        sys.stdout.write(f"종목명 조회 중... (총 {total}개) ")
        sys.stdout.flush()

        for idx, code in enumerate(stock_codes, 1):
            try:
                info = self.api.get_stock_info(code)
                if info and info.get("return_code") == 0:
                    data = info.get("data", {})
                    stock_names[code] = data.get("stk_nm", code)
                else:
                    stock_names[code] = code  # 조회 실패 시 코드 사용

                # 진행률 표시 (같은 줄에, 즉시 출력)
                sys.stdout.write(f"\r종목명 조회 중... ({idx}/{total}) ")
                sys.stdout.flush()

                # API Rate Limit 고려
                if idx % 5 == 0:
                    time.sleep(0.1)

            except Exception as e:
                stock_names[code] = code  # 에러 시 코드 사용

        sys.stdout.write("\n")  # 줄바꿈
        sys.stdout.flush()
        return stock_names

    async def search_condition(self, seq: str, name: str, max_retries: int = 2):
        """조건검색 실행 (재시도 로직 포함)"""
        console.print(f"[{datetime.now().strftime('%H:%M:%S')}] 조건검색 실행")
        console.print(f"  조건식 번호: {seq}")
        console.print(f"  조건식명: {name}")
        console.print()

        for attempt in range(max_retries):
            try:
                await self.send_message("CNSRREQ", {
                    "seq": seq,
                    "search_type": "1",  # 조회타입
                    "stex_tp": "K"  # 거래소구분 (K: 코스피/코스닥)
                })

                # 응답 대기 (타임아웃 5초)
                response = await asyncio.wait_for(self.receive_message(), timeout=5.0)

                return_code = response.get("return_code")

                if return_code == 0:
                    stock_list = response.get("data", [])
                    # None 체크 추가
                    if stock_list is None:
                        stock_list = []
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
                        # 종목명 조회
                        stock_names = self.get_stock_names(stock_codes)

                        console.print("1차 필터링 종목 리스트:")
                        console.print("─" * 120)
                        for i, code in enumerate(stock_codes, 1):
                            stock_name = stock_names.get(code, code)
                            console.print(f"  {i:2d}. [{code}] {stock_name}")
                        console.print("─" * 120)
                        console.print()

                    return stock_codes

                elif return_code == 100013:
                    # 인증 오류 - 재로그인 시도
                    console.print(f"[yellow]⚠️  인증 오류 발생 (시도 {attempt + 1}/{max_retries})[/yellow]")
                    if attempt < max_retries - 1:
                        console.print("[yellow]재로그인 시도 중...[/yellow]")
                        await asyncio.sleep(2.0)
                        if await self.login():
                            console.print("[green]재로그인 성공, 조건검색 재시도[/green]")
                            continue
                        else:
                            console.print("[red]재로그인 실패[/red]")
                            return []
                    else:
                        console.print(f"[red]❌ 최대 재시도 횟수 초과[/red]")
                        return []
                else:
                    console.print(f"[red]❌ 조건검색 실패: {response.get('return_msg')} (코드: {return_code})[/red]")
                    return []

            except asyncio.TimeoutError:
                console.print(f"[yellow]⚠️  응답 타임아웃 (시도 {attempt + 1}/{max_retries})[/yellow]")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2.0)
                    continue
                else:
                    console.print(f"[red]❌ 조건검색 타임아웃[/red]")
                    return []
            except websockets.exceptions.ConnectionClosed:
                console.print(f"[yellow]⚠️  WebSocket 연결 끊김 (시도 {attempt + 1}/{max_retries})[/yellow]")
                if attempt < max_retries - 1:
                    console.print("[yellow]재연결 시도 중...[/yellow]")
                    await self.connect()
                    console.print(f"[green]✓ 재연결 성공, 로그인 재시도...[/green]")
                    if not await self.login():
                        console.print(f"[red]❌ 로그인 재시도 실패[/red]")
                        return []
                    console.print(f"[green]✓ 인증 완료, 조건검색 재시도: {name}[/green]")
                    continue
                else:
                    console.print(f"[red]❌ 최대 재시도 횟수 초과[/red]")
                    return []
            except Exception as e:
                console.print(f"[red]❌ 조건검색 오류: {e}[/red]")
                if attempt < max_retries - 1 and "connection" in str(e).lower():
                    console.print("[yellow]연결 문제 감지, 재연결 시도...[/yellow]")
                    await self.connect()
                    console.print(f"[green]✓ 재연결 성공, 로그인 재시도...[/green]")
                    if not await self.login():
                        console.print(f"[red]❌ 로그인 재시도 실패[/red]")
                        return []
                    console.print(f"[green]✓ 인증 완료, 조건검색 재시도: {name}[/green]")
                    continue
                return []

        return []

    async def run_vwap_validation(self, stock_codes: List[str]):
        """VWAP 2차 검증 (하이브리드 데이터: 키움 + 야후)"""
        console.print("=" * 120, style="yellow")
        console.print(f"{'2차 필터링: VWAP 사전 검증 (키움 API + Yahoo Finance 하이브리드)':^120}", style="bold yellow")
        console.print("=" * 120, style="yellow")
        console.print()
        console.print(f"검증 기준:")
        console.print(f"  - 최소 거래: {self.validator.min_trades}회")
        console.print(f"  - 최소 승률: {self.validator.min_win_rate}%")
        console.print(f"  - 최소 평균 수익률: {self.validator.min_avg_profit:+.2f}%")
        console.print(f"  - 최소 Profit Factor: {self.validator.min_profit_factor}")
        console.print()
        console.print(f"[cyan]💡 데이터 전략: 키움 API 우선 → 부족시 Yahoo Finance 보충[/cyan]")
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
                await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

                # 배치마다 추가 대기
                if i % BATCH_SIZE == 0:
                    console.print(f"  [yellow]⏸️  배치 완료, {DELAY_BETWEEN_BATCHES}초 대기...[/yellow]")
                    await asyncio.sleep(DELAY_BETWEEN_BATCHES)

            except Exception as e:
                console.print(f"  [red]❌ {code}: 조회 실패 ({str(e)})[/red]")
                stock_info_list.append((code, code))  # 코드를 이름으로 사용
                await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

        console.print()
        console.print(f"[green]✅ 종목명 조회 완료: {len(stock_info_list)}개[/green]")
        console.print()

        # VWAP 검증 (하이브리드 데이터 소스 사용)
        console.print(f"[cyan]🔍 VWAP 검증 시작 (하이브리드 모드)...[/cyan]")
        console.print()

        results = []
        for i, (code, name) in enumerate(stock_info_list, 1):
            console.print(f"[{i}/{len(stock_info_list)}] {name} ({code}) 검증 중...", style="dim")

            # 하이브리드 검증 사용
            result = await validate_single_stock_hybrid(code, name, self.validator, self.api)
            results.append(result)

            # 배치마다 대기
            if i % BATCH_SIZE == 0:
                console.print(f"  [yellow]⏸️  {i}개 완료, 잠시 대기...[/yellow]")
                await asyncio.sleep(DELAY_BETWEEN_BATCHES)

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

        # DB에 저장
        self.save_candidates_to_db()

        return self.validated_stocks

    def run_comprehensive_analysis(self):
        """VWAP 통과 종목 종합 분석 (뉴스 + 기술 + 수급 + 기본)"""
        if not self.validated_stocks:
            console.print("[yellow]분석할 종목이 없습니다.[/yellow]")
            return

        console.print("=" * 120, style="magenta")
        console.print(f"{'3차 필터링: 종합 분석 (뉴스 + 기술 + 수급 + 기본)':^120}", style="bold magenta")
        console.print("=" * 120, style="magenta")
        console.print()

        analyzed_stocks = []

        for idx, stock in enumerate(self.validated_stocks, 1):
            stock_code = stock['stock_code']
            stock_name = stock['stock_name']

            console.print(f"\n[{idx}/{len(self.validated_stocks)}] {stock_name} ({stock_code}) 종합 분석 중...")
            console.print("─" * 120)

            try:
                # 차트 데이터 조회 (일봉 30일)
                chart_data = None
                try:
                    info = self.api.get_ohlcv_data(stock_code, period='D', count=30)
                    if info and info.get("return_code") == 0:
                        chart_data = info.get("data", [])
                        console.print(f"  ✓ 일봉 {len(chart_data)}개 수집")
                except Exception as e:
                    console.print(f"  [dim]차트 데이터 조회 실패: {e}[/dim]")

                # 종목 기본 정보 조회
                stock_info = None
                try:
                    info = self.api.get_stock_info(stock_code)
                    console.print(f"  [dim]종목정보 API 응답: return_code={info.get('return_code') if info else 'None'}[/dim]")
                    if info and info.get("return_code") == 0:
                        # 키움 API ka10001은 데이터를 최상위에 직접 반환
                        stock_info = info  # 전체 응답이 곧 stock_info
                        console.print(f"  ✓ 종목 정보 수집 (PER: {info.get('per', 'N/A')}, PBR: {info.get('pbr', 'N/A')})")
                    else:
                        console.print(f"  ⚠️  종목 정보 조회 실패: {info.get('return_msg', 'Unknown error') if info else 'No response'}", style="yellow")
                except Exception as e:
                    console.print(f"  ⚠️  종목 정보 오류: {e}", style="yellow")

                # 투자자별 매매 동향 조회
                investor_data = None
                try:
                    from datetime import datetime, timedelta
                    today = datetime.now().strftime('%Y%m%d')
                    info = self.api.get_investor_trend(stock_code, dt=today)
                    console.print(f"  [dim]투자자 동향 API 응답: return_code={info.get('return_code') if info else 'None'}[/dim]")
                    if info and info.get("return_code") == 0:
                        # ka10059 API는 'stk_invsr_orgn' 키에 LIST 반환
                        investor_data = info.get("stk_invsr_orgn", [])
                        console.print(f"  ✓ 투자자 동향 {len(investor_data) if investor_data else 0}개 수집")
                        if investor_data and len(investor_data) > 0:
                            # 최근 데이터 샘플 출력
                            latest = investor_data[0]
                            console.print(f"     외국인: {latest.get('frgnr_invsr', 'N/A')}, 기관: {latest.get('orgn', 'N/A')}")
                    else:
                        console.print(f"  ⚠️  투자자 동향 조회 실패: {info.get('return_msg', 'Unknown error') if info else 'No response'}", style="yellow")
                except Exception as e:
                    console.print(f"  ⚠️  투자자 동향 오류: {e}", style="yellow")

                # 프로그램 매매는 시장 전체 데이터이므로 종목별 분석에는 사용하지 않음
                program_data = None

                # 종합 분석 실행
                console.print(f"  🔍 AI 종합 분석 실행 중...")
                analysis_result = self.analysis_engine.analyze(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    chart_data=chart_data,
                    investor_data=investor_data,
                    program_data=program_data,
                    stock_info=stock_info
                )

                # 결과 저장
                stock['analysis_result'] = analysis_result
                analyzed_stocks.append(stock)

                # 주요 결과 출력
                final_score = analysis_result.get('final_score', 0)
                recommendation = analysis_result.get('recommendation', '관망')
                action = analysis_result.get('action', 'HOLD')

                console.print(f"  📊 최종 점수: {final_score:.1f}/100", style="bold cyan")
                console.print(f"  💡 투자 추천: {recommendation} ({action})", style="bold green" if action == "BUY" else "bold yellow")

                # 개별 점수
                scores = analysis_result.get('scores_breakdown', {})
                console.print(f"    - 뉴스: {scores.get('news', 50):.0f}/100")
                console.print(f"    - 기술: {scores.get('technical', 50):.0f}/100")
                console.print(f"    - 수급: {scores.get('supply_demand', 50):.0f}/50")
                console.print(f"    - 기본: {scores.get('fundamental', 50):.0f}/50")

                # API Rate Limit 고려
                time.sleep(0.5)

            except Exception as e:
                console.print(f"  [red]❌ 분석 실패: {e}[/red]")
                import traceback
                traceback.print_exc()

        # 점수순 정렬
        analyzed_stocks.sort(key=lambda x: x.get('analysis_result', {}).get('final_score', 0), reverse=True)
        self.validated_stocks = analyzed_stocks

        console.print("\n" + "=" * 120)
        console.print(f"✅ 종합 분석 완료: {len(analyzed_stocks)}개", style="green")
        console.print("=" * 120)
        console.print()

    def save_candidates_to_db(self):
        """검증 통과 종목을 DB에 저장"""
        if not self.validated_stocks:
            return

        console.print("\n[DB 저장] 검증 통과 종목을 데이터베이스에 저장 중...", style="yellow")

        saved_count = 0
        for stock in self.validated_stocks:
            try:
                stock_code = stock['stock_code']
                stock_name = stock['stock_name']
                stats = stock.get('stats', {})

                # 조건검색 통과 조건식 목록
                source_signals = []
                for seq, stocks in self.condition_stocks.items():
                    if stock_code in stocks:
                        cond_name = next((c[1] for c in self.condition_list if c[0] == seq), f"조건식{seq}")
                        source_signals.append(cond_name)

                # 종합 분석 결과 추출
                analysis_result = stock.get('analysis_result', {})
                final_score = analysis_result.get('final_score', stats.get('win_rate', 0))
                scores_breakdown = analysis_result.get('scores_breakdown', {})
                news_result = analysis_result.get('news', {})

                # DB 저장용 데이터 구성
                candidate_data = {
                    'date_detected': datetime.now().isoformat(),
                    'stock_code': stock_code,
                    'stock_name': stock_name,
                    'strategy_tag': 'VWAP+AI',
                    'source_signal': source_signals,
                    'market': 'KOSPI',  # TODO: 실제 시장 구분 추가

                    # VWAP 백테스트 결과
                    'vwap_win_rate': stats.get('win_rate'),
                    'vwap_avg_profit': stats.get('avg_profit_pct'),
                    'vwap_trade_count': stats.get('total_trades'),
                    'vwap_profit_factor': stats.get('profit_factor'),

                    # 종합 분석 결과
                    'score_news': scores_breakdown.get('news', 50),
                    'score_technical': scores_breakdown.get('technical', 50),
                    'score_supply_demand': scores_breakdown.get('supply_demand', 50),
                    'score_fundamental': scores_breakdown.get('fundamental', 50),
                    'score_vwap': stats.get('win_rate', 0),
                    'total_score': final_score,

                    # 뉴스 분석 세부 (DB 스키마에 맞춰)
                    'news_sentiment': news_result.get('sentiment', 'neutral'),
                    'news_impact': news_result.get('impact', 0),
                    'news_keywords': news_result.get('material_analysis', {}).get('keywords', []),
                    'news_count': news_result.get('news_count', 0),

                    # 필터링 결과
                    'pass_condition1': 1,
                    'pass_condition2': 1 if analysis_result else 0,
                    'filter_reason': f"VWAP {stats.get('win_rate', 0):.1f}% | 종합 {final_score:.1f}점",

                    # 투자 추천
                    'recommendation': analysis_result.get('recommendation', '관망'),
                    'action': analysis_result.get('action', 'HOLD'),

                    # 상태
                    'monitoring_status': 'watching' if analysis_result.get('action') == 'BUY' else 'filtered'
                }

                # DB 저장
                candidate_id = self.db.insert_candidate(candidate_data)
                saved_count += 1

            except Exception as e:
                console.print(f"  ❌ {stock_code} 저장 실패: {e}", style="red")

        console.print(f"  ✅ {saved_count}/{len(self.validated_stocks)}개 종목 DB 저장 완료", style="green")
        console.print()

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
            # 통과 종목 테이블 (종합 분석 결과 포함)
            table = Table(title="최종 선정 종목 (VWAP + AI 종합 분석)", box=box.DOUBLE)
            table.add_column("순위", justify="right", style="bold", width=4)
            table.add_column("종목명", style="cyan", width=14)
            table.add_column("코드", style="yellow", width=8)
            table.add_column("종합점수", justify="right", style="bold magenta", width=8)
            table.add_column("추천", style="green", width=10)
            table.add_column("VWAP\n승률", justify="right", width=7)
            table.add_column("VWAP\n거래수", justify="right", width=7)
            table.add_column("VWAP\n수익률", justify="right", width=8)
            table.add_column("뉴스", justify="right", width=6)
            table.add_column("기술", justify="right", width=6)
            table.add_column("수급", justify="right", width=6)
            table.add_column("기본", justify="right", width=6)

            # 종합 점수순으로 정렬
            sorted_stocks = sorted(
                self.validated_stocks,
                key=lambda x: x.get('analysis_result', {}).get('final_score', x['stats']['win_rate']),
                reverse=True
            )

            for rank, stock in enumerate(sorted_stocks, 1):
                stats = stock['stats']
                analysis = stock.get('analysis_result', {})
                scores = analysis.get('scores_breakdown', {})

                # 추천 액션에 따라 스타일 변경
                action = analysis.get('action', 'HOLD')
                recommendation = analysis.get('recommendation', '관망')
                rec_style = "bold green" if action == "BUY" else "yellow" if action == "HOLD" else "red"

                # 점수에 따른 색상
                final_score = analysis.get('final_score', stats['win_rate'])
                score_color = "bold green" if final_score >= 70 else "green" if final_score >= 60 else "yellow"

                table.add_row(
                    f"{rank}",
                    stock['stock_name'],
                    stock['stock_code'],
                    f"[{score_color}]{final_score:.1f}[/{score_color}]",
                    f"[{rec_style}]{recommendation}[/{rec_style}]",
                    f"{stats['win_rate']:.0f}%",
                    f"{stats['total_trades']}회",
                    f"{stats['avg_profit_pct']:+.2f}%",
                    f"{scores.get('news', 50):.0f}",
                    f"{scores.get('technical', 50):.0f}",
                    f"{scores.get('supply_demand', 25):.0f}",
                    f"{scores.get('fundamental', 25):.0f}"
                )

            console.print(table)
            console.print()

            # BUY 추천 종목만 별도 표시
            buy_stocks = [s for s in sorted_stocks if s.get('analysis_result', {}).get('action') == 'BUY']
            if buy_stocks:
                console.print(f"\n💎 매수 추천 종목: {len(buy_stocks)}개", style="bold green")
                for stock in buy_stocks:
                    analysis = stock.get('analysis_result', {})
                    console.print(f"  • {stock['stock_name']} ({stock['stock_code']}) - {analysis.get('final_score', 0):.1f}점", style="green")
                console.print()

        console.print("=" * 120, style="bold cyan")

    async def run_pipeline(self, condition_indices: List[int] = [17, 18, 19, 20, 21, 22]):
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

                    # 다음 조건 검색 전 대기 (서버 부하 방지)
                    await asyncio.sleep(2.0)

            # 중복 제거
            unique_stocks = list(all_stocks)
            console.print(f"📊 중복 제거 후 총 {len(unique_stocks)}개 종목", style="bold green")
            console.print()

            # VWAP 2차 검증 (하이브리드 데이터)
            if unique_stocks:
                validated = await self.run_vwap_validation(unique_stocks)

                # 종합 분석 (뉴스 + 기술 + 수급 + 기본)
                if validated:
                    self.run_comprehensive_analysis()

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

    # 공통 시스템 사용 (main_auto_trading.py와 동일)
    console.print("[3] 조건검색 + VWAP 필터링 실행")
    from main_auto_trading import IntegratedTradingSystem

    # 사용할 조건식 인덱스 선택 (condition_list의 인덱스, 0부터 시작)
    # seq 31~36 전략 = 리스트 인덱스 17~22
    CONDITION_INDICES = [17, 18, 19, 20, 21, 22]  # Momentum, Breakout, EOD, Supertrend+EMA+RSI, VWAP, Squeeze Momentum Pro

    # IntegratedTradingSystem 인스턴스 생성 (자동매매와 동일한 클래스)
    system = IntegratedTradingSystem(access_token, api, CONDITION_INDICES)
    console.print()

    # WebSocket 연결 및 로그인
    await system.connect()
    if not await system.login():
        console.print("[red]❌ 로그인 실패[/red]")
        return

    # 조건검색식 목록 조회
    if not await system.get_condition_list():
        console.print("[red]❌ 조건검색식 조회 실패[/red]")
        return

    # 필터링 실행 (조건검색 + VWAP)
    await system.run_condition_filtering()

    # DB 저장 (system.validated_stocks에 결과가 있음)
    from database.trading_db import TradingDatabase
    db = TradingDatabase("data/trading.db")

    console.print()
    console.print("=" * 120, style="cyan")
    console.print("[cyan]DB 저장 중...[/cyan]")

    import json
    saved_count = 0
    failed_count = 0

    for stock_code, stock_info in system.validated_stocks.items():
        try:
            # 데이터 구조 확인
            if not stock_info or not isinstance(stock_info, dict):
                console.print(f"[yellow]  ⚠️  {stock_code}: 잘못된 데이터 구조[/yellow]")
                failed_count += 1
                continue

            # 필수 필드 추출
            stock_name = stock_info.get('name', '알 수 없음')
            stats = stock_info.get('stats', {})
            data = stock_info.get('data')

            # 시장 판단 (KOSPI: 0으로 시작, KOSDAQ: 그 외)
            market = 'KOSPI' if stock_code.startswith('0') else 'KOSDAQ'

            candidate_data = {
                'date_detected': datetime.now().isoformat(),
                'stock_code': stock_code,
                'stock_name': stock_name,
                'market': market,  # 시장 정보 추가
                'strategy_tag': 'VWAP',
                'source_signal': json.dumps(['조건검색_통합']),
                'close_price': data['close'].iloc[-1] if data is not None and 'close' in data.columns and len(data) > 0 else None,
                'vwap_win_rate': stats.get('win_rate', 0),
                'vwap_avg_profit': stats.get('avg_profit_pct', 0),
                'vwap_trade_count': stats.get('total_trades', 0),
                'vwap_profit_factor': stats.get('profit_factor', 0),
                'total_score': stats.get('win_rate', 0),  # 임시로 승률을 점수로 사용
                'pass_condition1': True,
                'pass_condition2': True,
                'is_active': True,
                'monitoring_status': 'watching'
            }

            # DB 저장
            result_id = db.insert_candidate(candidate_data)
            if result_id:
                saved_count += 1
                console.print(f"[dim]  ✓ {stock_name}({stock_code}) DB 저장 완료[/dim]")
            else:
                failed_count += 1
                console.print(f"[yellow]  ⚠️  {stock_name}({stock_code}) DB 저장 실패 (insert 반환값 없음)[/yellow]")

        except Exception as e:
            failed_count += 1
            console.print(f"[red]  ❌ {stock_code} DB 저장 실패: {e}[/red]")
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")

    # 결과 요약
    console.print()
    console.print("=" * 120, style="bold green")
    console.print(f"[bold green]최종 결과:[/bold green]")
    console.print(f"  최종 검증 통과: {len(system.validated_stocks)}개 종목")
    console.print(f"  DB 저장 성공:   {saved_count}개 종목")
    if failed_count > 0:
        console.print(f"  DB 저장 실패:   {failed_count}개 종목", style="yellow")
    console.print("=" * 120, style="bold green")


if __name__ == "__main__":
    asyncio.run(main())
