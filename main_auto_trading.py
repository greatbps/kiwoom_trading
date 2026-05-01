"""
키움 조건식 → VWAP 필터링 → 실시간 자동매매 통합 시스템

전체 플로우:
1. 조건식 6개로 1차 필터링 (50~100개 종목)
2. VWAP 사전 검증으로 2차 필터링 (5~20개 종목)
3. 선정 종목 실시간 모니터링
4. VWAP 매수 신호 감지 → 사전 검증 → 자동 매수
5. 보유 중 모니터링 → VWAP 매도 신호 또는 트레일링 스탑 → 자동 매도
6. 무한 루프 (Ctrl+C로 중지)
"""
import asyncio
import websockets
import json
import sys
import os
import signal
import time
from datetime import datetime, timedelta, time as datetime_time
from typing import List, Dict, Set, Any, Optional, Tuple
from pathlib import Path

# 프로젝트 루트 경로
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from kiwoom_api import KiwoomAPI
from korea_invest_api import KoreaInvestAPI  # 🔧 2026-04-02: HTS 뉴스 조회
from analyzers.pre_trade_validator import PreTradeValidator
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from analyzers.signal_orchestrator import SignalOrchestrator
from utils.config_loader import load_config
from database.trading_db import TradingDatabase
from dotenv import load_dotenv
import yfinance as yf
import pandas as pd
from rich.console import Console
from rich.table import Table
from rich import box
from trading.exit_logic_optimized import OptimizedExitLogic
from trading.eod_manager import EODManager  # ✅ EOD Manager Phase 1
from trading.bottom_pullback_manager import BottomPullbackManager  # ✅ Bottom Pullback 전략
from trading.trade_state_manager import (  # ✅ Trade State Manager (중복 진입 방지)
    TradeStateManager,
    TradeAction
)
from core.trade_reconciliation import TradeReconciliation  # ✅ 거래 검증 및 동기화
from core.trade_capture import capture_entry, capture_exit # ✅ 진입/청산 지표 자동 캡처
from market_utils import is_trading_day, get_next_trading_day  # ✅ 휴장일 체크
from strategy.ai_rules_active import is_strategy_allowed
from analyzers.squeeze_with_orderbook import SqueezeWithOrderBook  # ✅ 스퀴즈 + 호가창 통합 전략

# ✅ 한투 브로커 통합 (국내/해외 중기)
from brokers import get_broker, BrokerType, Market, Position as BrokerPosition
from trading.mid_term_engine import (
    Action, PositionGroup, Position as MidTermPosition, MarketData,
    evaluate_position, STOCK_GROUP_MAP
)

# 환경변수 로드
load_dotenv()

# WebSocket URL
SOCKET_URL = 'wss://api.kiwoom.com:10000/api/dostk/websocket'

console = Console()

# 모듈 레벨 로거 — FileHandler 직접 추가 (쉘 리다이렉트 의존 제거)
import logging as _logging
_log_date = __import__('datetime').date.today().strftime('%Y%m%d')
_log_path = f'/home/greatbps/projects/kiwoom_trading/logs/auto_trading_{_log_date}.log'
_at_fh = _logging.FileHandler(_log_path, mode='a', encoding='utf-8')
_at_fh.setFormatter(_logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
_logging.basicConfig(
    level=_logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[_at_fh, _logging.StreamHandler(__import__('sys').stderr)],
)
logger = _logging.getLogger('auto_trading')

# 🔧 2026-03-10: Sweep Attempt Logger — 튜닝용 분석 파일
_sweep_attempt_logger = _logging.getLogger('sweep_attempt')
_sweep_attempt_logger.setLevel(_logging.DEBUG)
_sweep_attempt_logger.propagate = False
_sweep_fh = _logging.FileHandler(
    f'/home/greatbps/projects/kiwoom_trading/logs/sweep_attempt_{__import__("datetime").date.today().strftime("%Y%m%d")}.log'
)
_sweep_fh.setFormatter(_logging.Formatter('%(asctime)s %(message)s'))
_sweep_attempt_logger.addHandler(_sweep_fh)


def safe_float(value, default=0.0):
    """안전하게 float 변환 (bytes/string/None 처리)"""
    if value is None:
        return default
    if isinstance(value, bytes):
        try:
            value = value.decode('utf-8').strip()
        except (UnicodeDecodeError, AttributeError):
            return default
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def download_stock_data_sync(ticker: str, days: int = 7):
    """주식 데이터 다운로드 (5분봉) - 동기 버전"""
    try:
        import warnings
        warnings.filterwarnings('ignore')

        stock = yf.Ticker(ticker)
        df = stock.history(period=f"{days}d", interval="5m")

        if df.empty:
            return None

        df.reset_index(inplace=True)
        df.columns = [col.lower() for col in df.columns]

        # 🚨 음수/0 가격 필터링 (Yahoo Finance 버그 대응)
        if 'close' in df.columns:
            # 음수 또는 0인 행 제거
            invalid_rows = df[df['close'] <= 0]
            if len(invalid_rows) > 0:
                console.print(f"[yellow]⚠️  {ticker}: {len(invalid_rows)}개 비정상 가격 데이터 제거[/yellow]")
                df = df[df['close'] > 0].copy()

        # 데이터가 너무 적으면 None 반환
        if len(df) < 10:
            return None

        return df

    except Exception as e:
        return None


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
    except Exception as e:
        logger.debug(f"[YAHOO_FAIL] {ticker_ks}: {e}")

    # .KQ 시도
    if try_kq:
        ticker_kq = f"{ticker}.KQ"
        try:
            df = await asyncio.to_thread(download_stock_data_sync, ticker_kq, days)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            logger.debug(f"[YAHOO_FAIL] {ticker_kq}: {e}")

    return None


async def get_kiwoom_minute_data(api: KiwoomAPI, stock_code: str, required_bars: int = 100):
    """
    키움 API에서 분봉 데이터 조회 (5분봉)
    - 중복 volume 컬럼 자동 통합 (rename 시 충돌 방지)
    - 안정적인 numeric 변환 및 정렬 처리
    """
    try:
        result = api.get_minute_chart(
            stock_code=stock_code,
            tic_scope="5",
            upd_stkpc_tp="1"
        )

        # ✅ 1. 응답 코드 확인
        return_code = result.get('return_code')
        if return_code != 0:
            return_msg = result.get('return_msg', 'Unknown error')
            console.print(f"[dim]키움 API 오류 ({stock_code}): return_code={return_code}, msg={return_msg}[/dim]")
            return None

        # ✅ 2. 응답 데이터 키 탐색
        data = None
        for key in ['stk_min_pole_chart_qry', 'stk_mnut_pole_chart_qry', 'output', 'output1', 'output2', 'data']:
            if key in result and result[key]:
                data = result[key]
                break

        if not data or len(data) == 0:
            console.print(f"[dim]키움 API 데이터 없음 ({stock_code})[/dim]")
            return None

        df = pd.DataFrame(data)
        if df.empty:
            console.print(f"[yellow]⚠️ 변환된 DataFrame이 비어 있음 ({stock_code})[/yellow]")
            return None

        # ✅ 3. cntr_tm → 날짜/시간
        if 'cntr_tm' in df.columns:
            df['datetime'] = df['cntr_tm'].astype(str).str[:8]
            df['time'] = df['cntr_tm'].astype(str).str[8:14]
            df.drop(columns=['cntr_tm'], inplace=True, errors='ignore')

        # ✅ 4. 안전한 컬럼 매핑 (중복 이름 방지)
        column_mapping = {
            'dt': 'datetime', 'tm': 'time',
            'stck_bsop_date': 'datetime', 'stck_cntg_hour': 'time',
            'cur_prc': 'close', 'stck_prpr': 'close',
            'open_pric': 'open', 'stck_oprc': 'open',
            'high_pric': 'high', 'stck_hgpr': 'high',
            'low_pric': 'low', 'stck_lwpr': 'low',
            'trde_qty': 'volume1', 'cntg_vol': 'volume2', 'acc_trde_qty': 'volume3'
        }
        df.rename(columns={k: v for k, v in column_mapping.items() if k in df.columns}, inplace=True)

        # ✅ 5. volume 통합 (여러 열 → 평균)
        volume_cols = [c for c in ['volume1', 'volume2', 'volume3'] if c in df.columns]
        if volume_cols:
            df['volume'] = df[volume_cols].apply(pd.to_numeric, errors='coerce').abs().mean(axis=1)
            df.drop(columns=volume_cols, inplace=True, errors='ignore')

        # ✅ 6. 중복 제거 + 숫자 변환
        df = df.loc[:, ~df.columns.duplicated()]

        # 🔧 CRITICAL: 키움 API는 음수 부호로 하락을 표시 → 절대값 변환 필수!
        # 예: cur_prc="-78800" → 실제 가격은 78,800원
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df.loc[:, col] = pd.to_numeric(df[col], errors='coerce').abs()

        # ✅ 7. 결측치 제거 및 정렬
        df.dropna(subset=['close'], inplace=True)
        df = df.sort_values(by=['datetime', 'time']).reset_index(drop=True)

        return df

    except Exception as e:
        console.print(f"[yellow]❌ 키움 API 조회 실패 ({stock_code}): {e}[/yellow]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        return None


async def validate_stock_for_trading(stock_code: str, stock_name: str, validator: PreTradeValidator, api: KiwoomAPI):
    """
    종목 사전 검증 (매수 전) - v2 개선판

    1. 키움 API에서 5분봉 데이터 조회 (우선)
    2. 데이터 부족 시 Yahoo Finance로 보충 (.KS/.KQ 자동 전환)
    3. VWAP 검증 실행
    """
    try:
        required_bars = 100  # 필요한 최소 봉 개수
        df = None

        # 1단계: 키움 API 시도
        df = await get_kiwoom_minute_data(api, stock_code, required_bars)

        if df is not None and not df.empty:
            pass  # 조용히 성공
        else:
            console.print(f"  [dim]✗ 키움: 데이터 없음[/dim]")

        # 2단계: 데이터 부족 시 Yahoo로 보충
        if df is None or len(df) < required_bars:
            current_bars = len(df) if df is not None else 0
            console.print(f"  [yellow]⚠️  데이터 부족 ({current_bars}개/{required_bars}개) → Yahoo Finance 보충 시도[/yellow]")

            yahoo_df = await download_stock_data_yahoo(stock_code, days=7, try_kq=True)

            if yahoo_df is None or yahoo_df.empty:
                console.print(f"  [dim]✗ 야후: 데이터 없음[/dim]")
                return {'allowed': False, 'reason': f'데이터 없음 (키움:{current_bars}개, 야후:실패)'}

            yahoo_bars = len(yahoo_df)

            # 키움 데이터와 Yahoo 데이터 병합
            if df is not None and not df.empty:
                df = pd.concat([yahoo_df, df], ignore_index=True)
                df = df.drop_duplicates(subset=['datetime', 'time'], keep='last').reset_index(drop=True)
            else:
                df = yahoo_df

        # 최종 데이터 검증
        final_bars = len(df) if df is not None else 0

        if df is None or final_bars < required_bars:
            return {'allowed': False, 'reason': f'데이터 부족 ({final_bars}개 < {required_bars}개)'}

        current_price = df['close'].iloc[-1]
        current_time = datetime.now()

        # VWAP 검증 (조용히 실행)
        allowed, reason, stats = validator.validate_trade(
            stock_code=stock_code,
            stock_name=stock_name,
            historical_data=df,
            current_price=current_price,
            current_time=current_time
        )

        return {
            'allowed': allowed,
            'reason': reason,
            'stats': stats,
            'data': df
        }

    except Exception as e:
        console.print(f"  [red]❌ 검증 오류: {e}[/red]")
        import traceback
        console.print(f"  [dim]{traceback.format_exc()}[/dim]")
        return {'allowed': False, 'reason': f'오류: {str(e)}'}


def _save_account_snapshot_to_db(deposit: int, holding_value: int,
                                  total_assets: int, eval_profit: int,
                                  source: str = 'api') -> None:
    """계좌 스냅샷을 PostgreSQL account_snapshot 테이블에 저장. total_assets=0이면 스킵."""
    if total_assets <= 0:
        return
    try:
        import psycopg2 as _pg
        _conn = _pg.connect(
            host=os.getenv("POSTGRES_HOST", "localhost"),
            dbname=os.getenv("POSTGRES_DB", "trading_system"),
            user=os.getenv("POSTGRES_USER", "postgres"),
            password=os.getenv("POSTGRES_PASSWORD", ""),
        )
        _cur = _conn.cursor()
        _cur.execute("""
            INSERT INTO account_snapshot (deposit, holding_value, total_assets, eval_profit, source)
            VALUES (%s, %s, %s, %s, %s)
        """, (deposit, holding_value, total_assets, eval_profit, source))
        _conn.commit()
        _conn.close()
    except Exception as _e:
        logger.debug(f'[ACCT_SNAP_DB] 저장 실패: {_e}')


class IntegratedTradingSystem:
    """통합 자동매매 시스템"""

    def __init__(self, access_token: str, api: KiwoomAPI, condition_indices: List[int], skip_wait: bool = False):
        self.uri = SOCKET_URL
        self.access_token = access_token
        self.api = api
        self.condition_indices = condition_indices
        self.skip_wait = skip_wait  # 대기 시간 건너뛰기 옵션

        # 설정 로드
        self.config = load_config("config/strategy_hybrid.yaml")

        # 최적화된 청산 로직 초기화
        self.exit_logic = OptimizedExitLogic(self.config)

        # ✅ EOD Manager 초기화 (Phase 1: 익일 보유 관리)
        self.eod_manager = EODManager(self.config)
        console.print("[dim]✓ EODManager 초기화 완료 (익일 보유 관리)[/dim]")

        # ✅ TradeStateManager 초기화 (중복 진입/손절 재진입 방지)
        self.state_manager = TradeStateManager()
        console.print("[green]✓ TradeStateManager 초기화 완료 (중복 진입 방지)[/green]")

        # ✅ Bottom Pullback Manager 초기화 (state_manager 연동)
        try:
            condition_strategies = self.config.get_section('condition_strategies')
            bottom_config = condition_strategies.get('bottom_pullback', {}) if condition_strategies else {}
        except (KeyError, AttributeError):
            bottom_config = {}
        self.bottom_manager = BottomPullbackManager(bottom_config, state_manager=self.state_manager)

        # ✅ 조건 인덱스 → 전략 태그 매핑 생성 (하드코딩 제거)
        self.condition_to_strategy_map = {}
        self.default_strategy_tag = 'momentum'  # 기본값 (fallback용)

        try:
            condition_strategies = self.config.get_section('condition_strategies')
            if condition_strategies:
                for strategy_name, strategy_config in condition_strategies.items():
                    if isinstance(strategy_config, dict):
                        condition_indices = strategy_config.get('condition_indices', [])
                        strategy_tag = strategy_config.get('strategy_tag', strategy_name)

                        # 조건 인덱스 → 전략 태그 매핑
                        for idx in condition_indices:
                            self.condition_to_strategy_map[idx] = strategy_tag

                        console.print(f"[dim]✓ 전략 '{strategy_tag}': 조건 {condition_indices}[/dim]")

                # 기본 전략 태그 설정 (첫 번째 전략)
                if condition_strategies:
                    first_strategy = list(condition_strategies.values())[0]
                    if isinstance(first_strategy, dict):
                        self.default_strategy_tag = first_strategy.get('strategy_tag', 'momentum')

        except (KeyError, AttributeError) as e:
            console.print(f"[yellow]⚠️  전략 매핑 생성 실패, 기본값 사용: {e}[/yellow]")

        console.print(f"[green]✓ 전략 매핑 완료 (기본값: {self.default_strategy_tag})[/green]")

        # SignalOrchestrator 초기화 (L0-L6 시그널 파이프라인)
        self.signal_orchestrator = SignalOrchestrator(
            config=self.config,
            api=self.api
        )
        console.print("[dim]✓ SignalOrchestrator 초기화 완료 (L0-L6 파이프라인)[/dim]")

        # ✅ SqueezeWithOrderBook 초기화 (스퀴즈 + 호가창 통합 전략)
        squeeze_config = self.config.get('squeeze_momentum', {})
        enable_orderbook = squeeze_config.get('orderbook_filter', {}).get('enabled', False)
        self.squeeze_orderbook_strategy = SqueezeWithOrderBook(enable_orderbook=enable_orderbook)
        console.print(f"[green]✓ SqueezeWithOrderBook 초기화 완료 (호가창 필터: {'활성화' if enable_orderbook else '비활성화'})[/green]")

        # ✅ MACrossStrategy 초기화 (MA 골든크로스/데드크로스 전략)
        from analyzers.ma_cross_strategy import MACrossStrategy
        self.ma_cross_strategy = MACrossStrategy()
        console.print("[green]✓ MACrossStrategy 초기화 완료 (MA5/MA10 골든크로스)[/green]")

        # ✅ 2-타임프레임 전략 초기화 (30분봉 + Squeeze + 하위봉 진입)
        from analyzers.squeeze_momentum_lazybear import TwoTimeframeStrategy
        self.two_tf_strategy = TwoTimeframeStrategy(
            higher_tf='30min',
            lower_tf='5min',
            ma_short=5,
            ma_long=20
        )
        console.print("[green]✓ TwoTimeframeStrategy 초기화 완료 (30분봉 MA5/MA20 + Squeeze)[/green]")

        # ✅ SMC (Smart Money Concepts) 전략 초기화 (2026-01-23 CHoCH 등급 필터 추가)
        from analyzers.smc import SMCStrategy
        smc_config = self.config.get('smc', {})
        choch_grade_config = smc_config.get('choch_grade', {})
        additional_filters = smc_config.get('additional_filters', {})
        mtf_bias_config = smc_config.get('mtf_bias', {})  # 🔧 2026-01-29: MTF Bias 설정
        prefilter_config = smc_config.get('entry_prefilter', {})  # 🔧 2026-02-06: 프리필터

        self.smc_strategy = SMCStrategy(
            swing_lookback=smc_config.get('swing_lookback', 5),
            min_swing_size_pct=smc_config.get('min_swing_size_pct', 0.3),
            sweep_threshold_pct=smc_config.get('sweep_threshold_pct', 0.1),
            sweep_lookback=smc_config.get('sweep_lookback', 20),
            require_liquidity_sweep=smc_config.get('require_liquidity_sweep', True),
            long_only=smc_config.get('long_only', True),
            # 🔧 2026-01-23: CHoCH 등급 필터
            min_choch_grade=choch_grade_config.get('min_grade', 'B'),
            require_squeeze_on=additional_filters.get('require_squeeze_on', False),
            require_vwap_above=additional_filters.get('require_vwap_above', False),
            grade_b_weight=choch_grade_config.get('grade_b_weight', 0.5),
            # 🔧 2026-01-29: MTF Bias 필터 (30분봉 추세 체크)
            mtf_bias_enabled=mtf_bias_config.get('enabled', True),
            mtf_timeframe=mtf_bias_config.get('timeframe', '30min'),
            # 🔧 2026-02-06: 진입 프리필터 (품질 개선)
            prefilter_enabled=prefilter_config.get('enabled', True),
            prefilter_min_conditions=prefilter_config.get('min_conditions', 2),
            prefilter_require_htf_trend=prefilter_config.get('require_htf_trend', True),
            prefilter_require_liquidity_sweep=prefilter_config.get('require_liquidity_sweep', True),
            prefilter_require_reclaim=prefilter_config.get('require_reclaim', True),
            reclaim_lookback=prefilter_config.get('reclaim_lookback', 5),
            reclaim_tolerance_pct=prefilter_config.get('reclaim_tolerance_pct', 0.3),
            # 🔧 2026-03-20: Sweep Fallback
            sweep_fallback_enabled=smc_config.get('sweep_fallback_enabled', False),
            sweep_fallback_size_mult=smc_config.get('sweep_fallback_size_mult', 0.5),
            sweep_fallback_confidence=smc_config.get('sweep_fallback_confidence', 0.60)
        )
        # 🔧 2026-03-07: displacement_filter config 주입
        self.smc_strategy._raw_config = smc_config
        sweep_required = smc_config.get('require_liquidity_sweep', True)
        min_grade = choch_grade_config.get('min_grade', 'B')
        sweep_mode = "CHoCH + Sweep" if sweep_required else "CHoCH Only"
        grade_mode = f"등급>={min_grade}"
        mtf_mode = "MTF Bias ON" if mtf_bias_config.get('enabled', True) else "MTF Bias OFF"
        prefilter_mode = "프리필터 ON" if prefilter_config.get('enabled', True) else "프리필터 OFF"
        console.print(f"[green]✓ SMCStrategy 초기화 완료 ({sweep_mode}, {grade_mode}, {mtf_mode}, {prefilter_mode})[/green]")

        # ✅ BB(30,1) 관측기 초기화 (진입 X, 로깅만)
        from analyzers.bb30_observer import get_bb30_observer
        self.bb30_observer = get_bb30_observer()
        console.print("[dim]✓ BB(30,1) Observer 초기화 (관측 전용)[/dim]")

        # 🔧 2026-03-21: Trend Breakout 전략 초기화
        from analyzers.trend.trend_breakout import TrendBreakoutStrategy
        self.trend_strategy = TrendBreakoutStrategy(self.config)
        self._daily_trend_count: int = 0       # 일일 TREND 진입 카운터
        # 🔥 2026-04-03: EMA9 블록 추적 {stock_code: {blocked_price, blocked_time, grade}}
        self._ema9_blocks: dict = {}
        self._daily_defensive_count: int = 0  # 🔧 2026-03-31: 일일 DEFENSIVE 진입 카운터
        self._daily_atr_defensive_count: int = 0      # ATR DEFENSIVE 모드 진입 카운터
        self._daily_atr_defensive_exposure: float = 0.0  # ATR DEFENSIVE 누적 투자 비중
        self._active_pattern_positions: set = set()   # 동일 패턴 중복 진입 방지
        self._trade_cooldown: int = 0                  # 연패 쿨다운: 남은 스킵 횟수
        self._pending_exit_value: float = 0.0          # 청산 주문 중 포지션 가치 (레이스컨디션 방지)
        self._daily_short_count: int = 0       # 🔧 2026-03-31: 일일 SHORT 진입 카운터

        # 🔧 2026-04-03: RS (Relative Strength) 전략
        from analyzers.rs_strategy import RSStrategy
        self.rs_strategy = RSStrategy(self.api, self.config)
        self._daily_rs_count: int = 0
        self._daily_sqz_count: int = 0        # 🔧 2026-04-30: Squeeze Sub 일일 진입 카운터
        self._sqz_consecutive_losses: int = 0 # 🔧 2026-04-30: Squeeze 연패 카운터
        from analyzers.squeeze_pattern_stats import PatternStats
        self.sqz_pattern_stats = PatternStats()
        self.sqz_pattern_stats.load("logs/sqz_pattern_stats.json")
        _rs_status = "ON" if self.config.get("rs_strategy", {}).get("enabled", False) else "OFF"
        console.print(f"[dim]✓ RSStrategy 초기화 완료 — {_rs_status}[/dim]")

        # 🔧 2026-04-03: Drawdown 리스크 엔진
        from core.drawdown_engine import DrawdownEngine
        self.drawdown_engine = DrawdownEngine(self.config)
        _dd_status = "ON" if self.config.get("drawdown_engine", {}).get("enabled", True) else "OFF"
        console.print(f"[dim]✓ DrawdownEngine 초기화 완료 — {_dd_status}[/dim]")

        # 🔧 2026-04-24: 멀티데이 에쿼티 커브 기반 리스크 조절
        from trading.equity_controller import EquityController
        self.equity_ctrl = EquityController(self.config)
        _ec_status = "ON" if self.config.get("equity_control", {}).get("enabled", True) else "OFF"
        console.print(f"[dim]✓ EquityController 초기화 완료 — {_ec_status} (peak={self.equity_ctrl.peak:,.0f}원)[/dim]")

        # 🔧 2026-04-24: EMA 기반 온라인 통계 (실시간 expectancy → size 조절)
        from trading.online_stats import OnlineStats
        _ol_cfg = self.config.get('online_stats', {})
        self.online_stats = OnlineStats(
            alpha        = _ol_cfg.get('alpha',        0.05),
            min_trades   = _ol_cfg.get('min_trades',   30),
            effect_scale = _ol_cfg.get('effect_scale', 0.30),
        )
        _ol = self.online_stats.get_status()
        console.print(f"[dim]✓ OnlineStats 초기화 완료 — n={_ol['n']} E={_ol['expectancy']:.3f} "
                      f"{'(ready)' if _ol['ready'] else '(cold)'}[/dim]")

        self._daily_exploration_count: int = 0 # 🔧 2026-03-31: 일일 EXPLORATION 진입 카운터
        self._pending_exploration: dict = {}    # 1봉 확인 대기 중인 EXPLORATION 신호
        self._daily_a_plus_count: int = 0      # 🔧 2026-04-01: 일일 A+ 진입 카운터
        self._last_a_plus_time = None           # 🔧 2026-04-01: 마지막 A+ 진입 시각 (쿨다운용)
        # EXPLORATION 누적 통계 (세션 간 유지 — data/exploration_stats.json)
        self._exploration_stats: dict = {'count': 0, 'wins': 0, 'total_pnl': 0.0}
        self._exploration_killed: bool = False
        try:
            import json as _json_ex
            from pathlib import Path as _Path_ex
            _expl_path = _Path_ex('data/exploration_stats.json')
            if _expl_path.exists():
                _expl_d = _json_ex.loads(_expl_path.read_text())
                self._exploration_stats = _expl_d.get('stats', self._exploration_stats)
                self._exploration_killed = _expl_d.get('killed', False)
                if self._exploration_killed:
                    logger.warning("[EXPLORATION_KILLED] 이전 세션에서 자동 비활성화됨 (승률 부족)")
                    console.print("[red]⚠️  EXPLORATION: 이전 세션 승률 부족으로 자동 OFF 상태[/red]")
        except Exception:
            pass
        trend_cfg = self.config.get("trend", {})
        trend_status = "ON" if trend_cfg.get("enabled", False) else "OFF (레짐 감지 시 자동 ON)"
        console.print(f"[dim]✓ TrendBreakoutStrategy 초기화 완료 — {trend_status}[/dim]")

        # 데이터베이스 초기화 (PostgreSQL)
        self.db = TradingDatabase()
        console.print("[dim]✓ 데이터베이스 초기화 완료 (PostgreSQL)[/dim]")

        # VWAP 검증기 (문서 명세 복원)
        self.validator = PreTradeValidator(
            config=self.config,
            lookback_days=5,         # 🔧 FIX: 문서 명세 복원 (10 → 5)
            min_trades=2,            # 🔧 FIX: 문서 명세 복원 (6 → 2)
            min_win_rate=40.0,       # 50 → 40 (VWAP 전략 현실 승률)
            min_avg_profit=0.3,      # 0.5 → 0.3 (완화)
            min_profit_factor=1.15   # 1.2 → 1.15 (완화)
        )

        # VWAP 분석기
        analyzer_config = self.config.get_analyzer_config()
        self.analyzer = EntryTimingAnalyzer(**analyzer_config)

        # WebSocket
        self.websocket = None
        self.connected = False
        self.running = True

        # 종목 관리
        self.condition_list = []
        self.watchlist: Set[str] = set()  # 모니터링 대상
        self.validated_stocks: Dict[str, Dict] = {}  # 검증 통과 종목 상세 정보

        # 포지션 관리
        self.positions: Dict[str, Dict] = {}  # {stock_code: position_info}

        # 실시간 데이터 캐시
        self.price_cache: Dict[str, float] = {}

        # API 호출 캐시 (Rate Limit 방지)
        self.stock_info_cache: Dict[str, Dict] = {}  # {stock_code: {info, timestamp, failed}}
        self.cache_expiry_seconds = 3600  # 1시간 캐시 (종목명은 하루 안 바뀜)
        self.cache_fail_expiry_seconds = 300  # 조회 실패 시 5분 후 재시도
        self.last_api_call_time = 0  # 마지막 API 호출 시각
        self.api_call_delay = 1.0  # API 호출 간 최소 딜레이 (ka10001 rate limit 대응)
        self._ka10001_backoff_until: float = 0  # 429 발생 시 일시 차단

        # 계좌 정보 (실시간 업데이트)
        self.current_cash = 0.0
        self.total_assets = 0.0
        self.positions_value = 0.0

        # 리스크 관리자 (나중에 실계좌 기반으로 초기화)
        self.risk_manager = None

        # ✅ 한투 브로커 초기화 (국내/해외 중기 통합 모니터링)
        self.kis_domestic = None
        self.kis_overseas = None
        self.kis_domestic_positions = []
        self.kis_overseas_positions = []
        self.kis_domestic_results = []
        self.kis_overseas_results = []
        self.kis_last_update = None
        self._init_kis_brokers()

        # Watchdog 하트비트 (좀비 프로세스 감지용)
        self._heartbeat_path = Path('/tmp/kiwoom_heartbeat.json')

        # Dry-run 모드 (백테스트 검증용)
        self.dry_run_mode = False

        # 🔧 FIX: 쿨다운 + 연속 손실 차단 (거래 내역 분석 기반)
        # 🔧 2026-02-07 v2: (datetime, is_loss, exit_reason) 3-tuple, exit_reason 기반 차등 쿨다운
        self.stock_cooldown: Dict[str, tuple] = {}  # {stock_code: (last_exit_time, is_loss, exit_reason)}
        self.stock_loss_streak: Dict[str, int] = {}  # {stock_code: consecutive_losses}
        self.stock_ban_list: Set[str] = set()  # 당일 진입 금지 종목
        self.cooldown_minutes = 20  # fallback 일반 청산 쿨다운 (분)
        self.loss_cooldown_minutes = 30  # fallback 손절 쿨다운 30분
        self.max_consecutive_losses = 3  # 연속 손실 상한
        # 🔴 GPT 개선: 종목별 일일 거래 제한 (과도한 집중 방지)
        self.daily_trade_count: Dict[str, int] = {}  # {stock_code: count}
        self.max_trades_per_stock_per_day = 2  # 종목당 하루 최대 2회 거래
        # 🔧 2026-03-07: Daily Risk Controls (GPT 분석 기반)
        self._daily_buy_count: int = 0        # 당일 총 매수 횟수
        self._daily_pnl_pct: float = 0.0      # 당일 누적 profit_rate 합
        self._daily_loss_halted: bool = False  # 일일 손실 한도 초과 → 거래 종료
        self._db_hard_stop_checked_at: float = 0.0
        self._db_hard_stop_cache_ttl_sec: int = 60
        self._db_hard_stop_state: Dict[str, Any] = {
            "halted": False,
            "loss_streak": 0,
            "disabled_regimes": [],
            "reasons": [],
        }
        # 🔧 2026-03-08: OB Pullback Entry 대기 상태 (CHoCH 감지 후 OB zone pullback 대기)
        # {stock_code: {ob_high, ob_low, choch_price, detected_at, grade, position_size_mult, confidence}}
        self.smc_pending: dict = {}

        # 🔧 2026-03-18: SMC 신호 디스플레이 캐시 (모니터링 테이블용)
        # {stock_code: {sweep_type, sweep_dist, choch_grade, smc_state, last_updated}}
        self._smc_display_cache: dict = {}

        # 🔧 2026-03-18: 주기 카운터 + 포지션 스냅샷 (테이블 throttle용)
        self._cycle_count: int = 0
        self._last_positions_keys: set = set()
        self._table_refresh_cycles: int = 10  # 10주기(=10분)마다 sim/history 테이블 갱신
        self._entry_scan_cycles: int = 5    # 워치리스트 OHLCV+진입체크 주기 (=5분, 보유종목은 항상)
        self._prev_mkt_ctx_status: str = ""   # MKT_CTX 상태 변화 감지용

        # 🔧 2026-03-18: Signal 큐 (detect → execute 분리)
        self._pending_signals: list = []            # 이번 주기에 수집된 신호들
        self._executed_signal_ids: set = set()      # 중복 실행 방지
        self._dry_run: bool = bool(self.config.get('dry_run', False))  # DRY_RUN 모드

        # 🔧 2026-03-20: Sweep Fallback 당일 카운터 (과매매 방지)
        self._daily_fallback_count: int = 0
        self._daily_c_fallback_count: int = 0       # C급 전용 카운터
        self._last_c_fallback_time: Optional[datetime] = None  # C급 쿨다운

        # 🔧 2026-02-07: Re-entry Cooldown 운영 통계 + 차등화 v2
        from metrics.reentry_metrics import ReentryMetrics, categorize_exit_reason
        self.reentry_metrics = ReentryMetrics()
        self._categorize_exit_reason = categorize_exit_reason

        # 🔧 2026-02-26: Market Context Layer ("오늘 싸워도 되는 날인가?")
        from core.market_context import MarketContextChecker
        self.market_context = MarketContextChecker(api=self.api, config=self.config.config)

        # 🔧 2026-04-03: 레짐 자동 판단 엔진 (market_context 이후 초기화)
        from core.regime_engine import RegiemeEngine
        self.regime_engine = RegiemeEngine(self.market_context, self.config)
        _re_status = "ON" if self.config.get("regime_engine", {}).get("enabled", True) else "OFF"
        console.print(f"[dim]✓ RegiemeEngine 초기화 완료 — {_re_status}[/dim]")

        # 🔧 2026-05-01: EQ ML Filter (Entry Quality)
        from ml.feature_logger import FeatureLogger
        from ml.eq_model import EQModel
        self.eq_feature_logger = FeatureLogger()
        self.eq_model = EQModel(config=self.config.config if hasattr(self.config, 'config') else {})

        # 🔧 2026-03-31: DriftDetector + PositionSizer + TradeStats 초기화
        from analysis.drift_detector import DriftDetector
        from core.position_sizer import PositionSizer, TradeStats
        self.drift_detector = DriftDetector()
        self._position_sizer = PositionSizer(base_capital=10_000_000)   # 일 시작 시 reset_equity로 갱신
        self._trade_stats = TradeStats(window=20)
        console.print("[dim]✓ DriftDetector / PositionSizer / TradeStats 초기화 완료[/dim]")

        # 쿨다운 차등화 config 로드
        self._cooldown_by_reason = self.config.get('re_entry.reentry_cooldown.by_exit_reason', {})
        self._cooldown_v2_enabled = self.config.get('re_entry.reentry_cooldown.enabled', False)

        # ✅ DB에서 활성 모니터링 종목 복원
        self._load_monitoring_stocks_from_db()

    def _load_monitoring_stocks_from_db(self):
        """DB에서 활성 모니터링 종목 복원"""
        print("\n" + "="*60)
        print("🔍 DB 모니터링 종목 복원 시작...")
        print("="*60)

        try:
            print("📦 market_utils 임포트 중...")
            from market_utils import get_db_connection

            print("🔌 DB 연결 시도 중...")
            conn = get_db_connection()
            try:
                cur = conn.cursor()
                print("✅ DB 연결 성공")

                # monitoring_stocks에서 활성 종목 조회
                print("📊 monitoring_stocks 테이블 조회 중...")
                cur.execute("""
                    SELECT symbol, name, source, add_reason, created_at
                    FROM monitoring_stocks
                    WHERE monitoring_active = true
                    ORDER BY created_at DESC
                """)

                rows = cur.fetchall()
                print(f"✅ 쿼리 완료: {len(rows)}개 종목 발견")

                if rows:
                    console.print(f"\n[cyan]📥 DB에서 {len(rows)}개 모니터링 종목 복원 중...[/cyan]")

                    for symbol, name, source, add_reason, created_at in rows:
                        # watchlist에 추가
                        self.watchlist.add(symbol)

                        # validated_stocks에 추가 (간단한 정보만)
                        self.validated_stocks[symbol] = {
                            'name': name,
                            'source': source,
                            'add_reason': add_reason,
                            'created_at': created_at,
                            'market': 'KOSPI' if symbol.startswith('0') else 'KOSDAQ'
                        }

                    console.print(f"[green]✅ DB 복원 완료: {len(rows)}개 종목[/green]")
                    console.print(f"  🔍 조건검색: {sum(1 for v in self.validated_stocks.values() if v.get('source') == 'condition_search')}개")
                    console.print(f"  📦 StockGravity: {sum(1 for v in self.validated_stocks.values() if v.get('source') == 'stockgravity')}개")

                    print(f"📌 watchlist 크기: {len(self.watchlist)}")
                    print(f"📌 validated_stocks 크기: {len(self.validated_stocks)}")
                else:
                    console.print("[dim]ℹ️  DB에 활성 모니터링 종목이 없습니다[/dim]")
                    print("⚠️  rows가 비어있습니다!")

            finally:
                conn.close()
                print("🔌 DB 연결 종료")

        except ImportError as e:
            print(f"❌ 임포트 에러: {e}")
            import traceback
            traceback.print_exc()
        except Exception as e:
            print(f"❌ DB 복원 실패: {e}")
            import traceback
            traceback.print_exc()
            console.print(f"[yellow]⚠️  DB 복원 실패: {e}[/yellow]")
            console.print("[dim]조건 검색으로 새 종목을 추가하세요[/dim]")

        print("="*60)
        print("🔍 DB 모니터링 종목 복원 완료")
        print("="*60 + "\n")

    def _init_kis_brokers(self):
        """한투 브로커 초기화 (국내/해외)"""
        try:
            # 한투 국내
            self.kis_domestic = get_broker(BrokerType.KIS_DOMESTIC)
            if self.kis_domestic.initialize():
                console.print("[green]✓ 한투 국내 연결 완료[/green]")
            else:
                console.print("[yellow]⚠️  한투 국내 연결 실패[/yellow]")
                self.kis_domestic = None

            # 한투 해외
            self.kis_overseas = get_broker(BrokerType.KIS_OVERSEAS)
            if self.kis_overseas.initialize():
                console.print("[green]✓ 한투 해외 연결 완료[/green]")
            else:
                console.print("[yellow]⚠️  한투 해외 연결 실패[/yellow]")
                self.kis_overseas = None

        except Exception as e:
            console.print(f"[yellow]⚠️  한투 브로커 초기화 실패: {e}[/yellow]")

    def _build_eq_features(
        self,
        stock_code: str,
        price: float,
        df,
        entry_confidence: float,
        entry_reason: str,
    ) -> dict:
        """EQ ML Filter용 진입 피처 딕셔너리 구성"""
        features = {}

        # _pending_signal_meta (check_entry_signal에서 설정)
        _sm = getattr(self, '_pending_signal_meta', {}) or {}
        features['choch_grade']      = _sm.get('choch_grade')
        features['htf_trend']        = int(bool(_sm.get('htf_bias')))
        features['sweep']            = int(bool(_sm.get('sweep')))
        features['guard_state']      = _sm.get('guard_state', 'normal')
        features['entry_confidence'] = round(float(entry_confidence or 0.5), 3)

        # r_pct — 포지션에 저장된 값 우선
        pos = self.positions.get(stock_code, {})
        features['r_pct']    = pos.get('r_pct', 0.0)
        features['eq_grade'] = pos.get('eq_grade')

        # 시장 상태
        try:
            _rg = self.regime_engine.get_regime() if hasattr(self, 'regime_engine') else {}
            features['regime'] = _rg.get('regime', 'UNKNOWN') if isinstance(_rg, dict) else str(_rg)
        except Exception:
            features['regime'] = 'UNKNOWN'

        # df 기반 기술 지표
        try:
            if df is not None and len(df) > 5:
                _atr = float(df['atr'].iloc[-1]) if 'atr' in df.columns else price * 0.02
                features['atr_pct'] = round(_atr / price * 100, 3)

                _vol = df['volume'].iloc[-1] if 'volume' in df.columns else 0
                _vol_ma = df['volume'].rolling(20).mean().iloc[-1] if 'volume' in df.columns else 1
                features['volume_ratio'] = round(float(_vol / _vol_ma) if _vol_ma > 0 else 1.0, 2)

                features['rsi'] = round(float(df['rsi'].iloc[-1]), 1) if 'rsi' in df.columns else 50.0

                _sqz = df['squeeze'].iloc[-1] if 'squeeze' in df.columns else None
                features['squeeze_on'] = int(_sqz == 1 or str(_sqz).lower() in ('true', '1')) if _sqz is not None else 0
        except Exception:
            pass

        # 진입 시각 (분, 09:00=0 기준)
        _now = datetime.now()
        features['time_slot'] = (_now.hour - 9) * 60 + _now.minute

        return features

    def _infer_signal_regime(self, entry_reason: str) -> str:
        """entry_reason 텍스트 기반 전략 라벨 추정"""
        reason = (entry_reason or "").upper()

        if "DEFENSIVE" in reason:
            return "DEFENSIVE"
        if "EXPLORATION" in reason:
            return "EXPLORATION"
        if "A+" in reason or "A_PLUS" in reason:
            return "A_PLUS"
        if "TREND" in reason:
            return "TREND"
        if reason.startswith("RS") or " RS" in reason:
            return "RS"
        if "ACCEPT" in reason or "ORCHESTRATOR" in reason:
            return "ORCHESTRATOR"
        return "SMC"

    def _get_current_time_bucket(self) -> str:
        """log_trade_events와 동일한 HH:MM 버킷 포맷."""
        return datetime.now().strftime("%H:%M")

    def _record_blocked_entry(
        self,
        stock_code: str,
        stock_name: str,
        block_tag: str,
        block_detail: str,
        entry_reason: str = None,
    ) -> None:
        """차단 이벤트를 log_trade_events(kind='blocked')에 기록.
        실패해도 트레이딩 흐름에 영향 없음 (silent try/except).
        """
        try:
            import psycopg2 as _psycopg2
            _now = datetime.now()
            _regime = self._infer_signal_regime(entry_reason or "")
            _bucket = self._get_current_time_bucket()
            _conn = _psycopg2.connect(
                host=os.getenv("POSTGRES_HOST", "localhost"),
                port=int(os.getenv("POSTGRES_PORT", 5432)),
                dbname=os.getenv("POSTGRES_DB", "trading_system"),
                user=os.getenv("POSTGRES_USER", "postgres"),
                password=os.getenv("POSTGRES_PASSWORD", ""),
            )
            with _conn:
                with _conn.cursor() as _cur:
                    _cur.execute(
                        """
                        INSERT INTO log_trade_events
                            (timestamp, trade_date, time_bucket,
                             source_file, source_tag, kind,
                             ticker, symbol, regime, entry_reason, exit_reason)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            _now, _now.date(), _bucket,
                            "main_auto_trading.py", block_tag, "blocked",
                            stock_code, stock_name, _regime, entry_reason, block_detail,
                        ),
                    )
            _conn.close()
            logger.debug("[BLOCK_LOG] %s %s → %s", block_tag, stock_code, block_detail)
        except Exception as _e:
            logger.debug("[BLOCK_LOG] DB 기록 실패 (무시): %s", _e)

    def _save_daily_capital_snapshot(self) -> None:
        """당일 자본 스냅샷을 daily_capital_snapshot 테이블에 UPSERT.
        15:30 EOD 루틴에서 1회 호출. 실패해도 트레이딩 흐름에 영향 없음.
        """
        try:
            import psycopg2 as _psycopg2
            _rpt = self.reentry_metrics.generate_report()
            _ms  = _rpt.get("market_sensor", {})
            _block_count = 0
            try:
                import psycopg2 as _pc2
                _c = _pc2.connect(
                    host=os.getenv("POSTGRES_HOST", "localhost"),
                    port=int(os.getenv("POSTGRES_PORT", 5432)),
                    dbname=os.getenv("POSTGRES_DB", "trading_system"),
                    user=os.getenv("POSTGRES_USER", "postgres"),
                    password=os.getenv("POSTGRES_PASSWORD", ""),
                )
                with _c:
                    with _c.cursor() as _cr:
                        _cr.execute(
                            "SELECT COUNT(*) FROM log_trade_events "
                            "WHERE trade_date = CURRENT_DATE AND kind = 'blocked'"
                        )
                        _block_count = (_cr.fetchone() or [0])[0]
                _c.close()
            except Exception:
                pass

            _conn = _psycopg2.connect(
                host=os.getenv("POSTGRES_HOST", "localhost"),
                port=int(os.getenv("POSTGRES_PORT", 5432)),
                dbname=os.getenv("POSTGRES_DB", "trading_system"),
                user=os.getenv("POSTGRES_USER", "postgres"),
                password=os.getenv("POSTGRES_PASSWORD", ""),
            )
            with _conn:
                with _conn.cursor() as _cur:
                    _cur.execute(
                        """
                        INSERT INTO daily_capital_snapshot
                            (trade_date, capital_end, cash_end, daily_pnl_pct,
                             buy_count, hard_stop_count, ef_count,
                             no_trade_day, trading_halted, block_count)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (trade_date) DO UPDATE SET
                            capital_end    = EXCLUDED.capital_end,
                            cash_end       = EXCLUDED.cash_end,
                            daily_pnl_pct  = EXCLUDED.daily_pnl_pct,
                            buy_count      = EXCLUDED.buy_count,
                            hard_stop_count= EXCLUDED.hard_stop_count,
                            ef_count       = EXCLUDED.ef_count,
                            no_trade_day   = EXCLUDED.no_trade_day,
                            trading_halted = EXCLUDED.trading_halted,
                            block_count    = EXCLUDED.block_count
                        """,
                        (
                            datetime.now().date(),
                            int(self.total_assets) if self.total_assets > 0 else int(self.current_cash),
                            int(self.current_cash),
                            round(self._daily_pnl_pct, 4),
                            self._daily_buy_count,
                            _rpt.get("hard_stop_count", 0),
                            _ms.get("ef_total", _rpt.get("ef_triggered_count", 0)),
                            bool(_ms.get("no_trade_day", False)),
                            bool(_rpt.get("trading_halted", False)),
                            _block_count,
                        ),
                    )
            _conn.close()
            logger.info("[CAPITAL_SNAPSHOT] 자본 스냅샷 저장 완료: 총자산=%s 매수=%d 차단=%d",
                        f"{self.total_assets:,.0f}", self._daily_buy_count, _block_count)
        except Exception as _e:
            logger.warning("[CAPITAL_SNAPSHOT] 저장 실패 (무시): %s", _e)

    def _check_db_hard_stop_guard(self, signal_regime: str = "") -> tuple[bool, str]:
        """
        log_trade_events 기반 DB 하드스탑.

        - 최근 N시간 연속 손실 5회 이상 → 전체 신규 진입 차단
        - 특정 regime 손실 누적 → 해당 regime만 차단
        """
        cfg = self.config.get('risk_control.db_hard_stop', {})
        if not cfg.get('enabled', True):
            return True, ""

        now_ts = time.time()
        if now_ts - self._db_hard_stop_checked_at >= self._db_hard_stop_cache_ttl_sec:
            try:
                from analysis.log_trade_analytics import evaluate_hard_stop

                self._db_hard_stop_state = evaluate_hard_stop(
                    lookback_hours=int(cfg.get('lookback_hours', 24)),
                    consecutive_loss_limit=int(cfg.get('consecutive_loss_limit', 5)),
                    regime_loss_limit=float(cfg.get('regime_loss_limit_pct', -5.0)),
                    regime_min_trades=int(cfg.get('regime_min_trades', 2)),
                )
                self._db_hard_stop_checked_at = now_ts
            except Exception as e:
                logger.debug(f"[DB_HARD_STOP_SKIP] {e}")
                return True, ""

        state = self._db_hard_stop_state
        if not state.get("halted", False):
            return True, ""

        loss_limit = int(cfg.get('consecutive_loss_limit', 5))
        loss_streak = int(state.get("loss_streak", 0) or 0)
        disabled_regimes = set(state.get("disabled_regimes", []))

        if signal_regime and signal_regime in disabled_regimes:
            return False, f"DB_HARD_STOP regime={signal_regime} disabled"

        if loss_streak >= loss_limit:
            return False, f"DB_HARD_STOP recent loss streak={loss_streak}"

        return True, ""

    def fetch_kis_positions(self):
        """한투 포지션 조회 및 평가"""
        try:
            # 국내 포지션
            if self.kis_domestic:
                self.kis_domestic_positions = self.kis_domestic.get_positions()
                self._evaluate_kis_positions('domestic')

            # 해외 포지션
            if self.kis_overseas:
                self.kis_overseas_positions = self.kis_overseas.get_positions()
                self._evaluate_kis_positions('overseas')

            self.kis_last_update = datetime.now()

        except Exception as e:
            console.print(f"[yellow]⚠️  한투 포지션 조회 실패: {e}[/yellow]")

    def _evaluate_kis_positions(self, market_type: str):
        """한투 포지션 중기 평가"""
        if market_type == 'domestic':
            positions = self.kis_domestic_positions
            results_list = []
        else:
            positions = self.kis_overseas_positions
            results_list = []

        total_eval = sum(p.eval_amount for p in positions) if positions else 0

        for bp in positions:
            weight = (bp.eval_amount / total_eval * 100) if total_eval > 0 else 0

            pos = MidTermPosition(
                stock_code=bp.symbol,
                stock_name=bp.name,
                quantity=bp.quantity,
                avg_price=bp.avg_price,
                current_price=bp.current_price,
                profit_pct=bp.profit_pct,
                eval_amount=bp.eval_amount,
                group=STOCK_GROUP_MAP.get(bp.symbol, PositionGroup.B_TREND),
                weight_pct=weight
            )

            result = evaluate_position(pos, MarketData())
            results_list.append(result)

        if market_type == 'domestic':
            self.kis_domestic_results = results_list
        else:
            self.kis_overseas_results = results_list

    def _get_action_style(self, action_value: str) -> tuple:
        """Action 스타일 반환"""
        styles = {
            'STOP_LOSS': ('🔴', 'red bold'),
            'TRAILING_STOP': ('🟢', 'green'),
            'REDUCE': ('🟡', 'yellow'),
            'ADD_ON_PULLBACK': ('🔵', 'cyan'),
            'HOLD': ('⚪', 'white'),
            'TAKE_PROFIT': ('💰', 'green bold'),
        }
        return styles.get(action_value, ('⚪', 'white'))

    def display_kis_positions(self):
        """한투 포지션 대시보드 표시"""
        console.print()
        console.print("=" * 80, style="bold cyan")
        console.print(f"{'📊 한투 중기 모니터링':^80}", style="bold cyan")
        console.print("=" * 80, style="bold cyan")

        # 국내 포지션
        console.print(f"\n[bold yellow]━━━ 📊 한투 국내 (중기) ━━━[/bold yellow]")

        if self.kis_domestic_positions:
            table = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
            table.add_column("종목", width=16)
            table.add_column("수익률", justify="right", width=10)
            table.add_column("평가금", justify="right", width=12)
            table.add_column("Action", width=16)

            total_eval = 0
            total_invested = 0

            for i, p in enumerate(self.kis_domestic_positions):
                style = "green" if p.profit_pct >= 0 else "red"
                total_eval += p.eval_amount
                total_invested += p.avg_price * p.quantity

                action = "HOLD"
                if i < len(self.kis_domestic_results):
                    action = self.kis_domestic_results[i].action.value

                icon, action_style = self._get_action_style(action)

                table.add_row(
                    p.name[:14] if p.name else p.symbol,
                    f"[{style}]{p.profit_pct:+.1f}%[/{style}]",
                    f"{p.eval_amount:,.0f}",
                    f"{icon} [{action_style}]{action}[/{action_style}]"
                )

            console.print(table)
            profit_pct = ((total_eval - total_invested) / total_invested * 100) if total_invested > 0 else 0
            profit_style = "green" if profit_pct >= 0 else "red"
            console.print(f"  [bold]평가: {total_eval:,.0f}원[/bold] [{profit_style}]{profit_pct:+.1f}%[/{profit_style}]")
        else:
            console.print("[dim]  보유 없음[/dim]")

        # 해외 포지션
        console.print(f"\n[bold magenta]━━━ 🌍 한투 해외 (중기) ━━━[/bold magenta]")

        if self.kis_overseas_positions:
            table = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
            table.add_column("종목", width=8)
            table.add_column("현재가", justify="right", width=10)
            table.add_column("수익률", justify="right", width=10)
            table.add_column("Action", width=16)

            total_eval = 0
            total_invested = 0

            for i, p in enumerate(self.kis_overseas_positions):
                style = "green" if p.profit_pct >= 0 else "red"
                total_eval += p.eval_amount
                total_invested += p.avg_price * p.quantity

                action = "HOLD"
                if i < len(self.kis_overseas_results):
                    action = self.kis_overseas_results[i].action.value

                icon, action_style = self._get_action_style(action)

                table.add_row(
                    p.symbol,
                    f"${p.current_price:.2f}",
                    f"[{style}]{p.profit_pct:+.1f}%[/{style}]",
                    f"{icon} [{action_style}]{action}[/{action_style}]"
                )

            console.print(table)
            profit_pct = ((total_eval - total_invested) / total_invested * 100) if total_invested > 0 else 0
            profit_style = "green" if profit_pct >= 0 else "red"
            console.print(f"  [bold]평가: ${total_eval:,.2f}[/bold] [{profit_style}]{profit_pct:+.1f}%[/{profit_style}]")
        else:
            console.print("[dim]  보유 없음[/dim]")

        # STOP_LOSS 경고
        stop_loss_items = []

        for r in self.kis_domestic_results:
            if r.action.value == 'STOP_LOSS':
                stop_loss_items.append(f"{r.position.stock_name[:10]} ({r.position.profit_pct:+.1f}%)")

        for r in self.kis_overseas_results:
            if r.action.value == 'STOP_LOSS':
                stop_loss_items.append(f"{r.position.stock_code} ({r.position.profit_pct:+.1f}%)")

        if stop_loss_items:
            console.print(f"\n[bold red]🚨 한투 STOP_LOSS 대상 ({len(stop_loss_items)}건)[/bold red]")
            for item in stop_loss_items:
                console.print(f"   🔴 {item}")

        console.print()

    def execute_kis_stop_loss(self):
        """한투 STOP_LOSS 자동 실행"""
        # 안전 스위치
        AUTO_STOP_ENABLED = True  # False로 변경하면 시뮬레이션만
        AUTO_STOP_ALLOWED_GROUPS = [PositionGroup.B_TREND, PositionGroup.C_REBALANCE]
        MAX_DAILY_STOPS = 3
        # 장기 보유 종목 — 자동 손절 완전 제외 (수동 관리)
        LONGTERM_EXCLUDE = {"SOXL"}

        # 오늘 실행 횟수 추적
        if not hasattr(self, 'kis_stop_loss_count'):
            self.kis_stop_loss_count = {}
        today = datetime.now().strftime('%Y%m%d')
        if today not in self.kis_stop_loss_count:
            self.kis_stop_loss_count = {today: 0}

        executed = []

        # 국내 STOP_LOSS 체크
        for r in self.kis_domestic_results:
            if r.action != Action.STOP_LOSS:
                continue
            if r.position.group not in AUTO_STOP_ALLOWED_GROUPS:
                console.print(f"[yellow]⚠️ {r.position.stock_name}: 그룹 {r.position.group.value} → 수동 손절 필요[/yellow]")
                continue
            if self.kis_stop_loss_count[today] >= MAX_DAILY_STOPS:
                console.print(f"[yellow]⚠️ 1일 손절 한도 {MAX_DAILY_STOPS}회 도달[/yellow]")
                break

            if AUTO_STOP_ENABLED and self.kis_domestic:
                try:
                    from brokers import OrderSide, OrderType
                    result = self.kis_domestic.place_order(
                        symbol=r.position.stock_code,
                        side=OrderSide.SELL,
                        quantity=r.position.quantity,
                        order_type=OrderType.MARKET
                    )
                    if result.success:
                        console.print(f"[red]🔴 국내 손절 실행: {r.position.stock_name} {r.position.quantity}주[/red]")
                        self.kis_stop_loss_count[today] += 1
                        executed.append(r.position.stock_code)
                    else:
                        console.print(f"[red]❌ 손절 실패: {r.position.stock_name} - {result.message}[/red]")
                except Exception as e:
                    console.print(f"[red]❌ 손절 오류: {r.position.stock_name} - {e}[/red]")
            else:
                console.print(f"[cyan]🔵 [시뮬] 국내 손절: {r.position.stock_name} {r.position.quantity}주 ({r.position.profit_pct:+.1f}%)[/cyan]")

        # 해외 STOP_LOSS 체크
        for r in self.kis_overseas_results:
            if r.action != Action.STOP_LOSS:
                continue
            if r.position.stock_code in LONGTERM_EXCLUDE:
                console.print(f"[dim]⏸ {r.position.stock_code}: 장기 보유 제외 (자동 손절 스킵)[/dim]")
                continue
            if r.position.group not in AUTO_STOP_ALLOWED_GROUPS:
                console.print(f"[yellow]⚠️ {r.position.stock_code}: 그룹 {r.position.group.value} → 수동 손절 필요[/yellow]")
                continue
            if self.kis_stop_loss_count[today] >= MAX_DAILY_STOPS:
                console.print(f"[yellow]⚠️ 1일 손절 한도 {MAX_DAILY_STOPS}회 도달[/yellow]")
                break

            if AUTO_STOP_ENABLED and self.kis_overseas:
                try:
                    # 해외주식은 place_market_sell 사용 (현재가 지정가 주문)
                    result = self.kis_overseas.place_market_sell(
                        symbol=r.position.stock_code,
                        quantity=r.position.quantity
                    )
                    if result.success:
                        console.print(f"[red]🔴 해외 손절 실행: {r.position.stock_code} {r.position.quantity}주 @ ${result.price:.2f}[/red]")
                        self.kis_stop_loss_count[today] += 1
                        executed.append(r.position.stock_code)
                    else:
                        console.print(f"[red]❌ 손절 실패: {r.position.stock_code} - {result.message}[/red]")
                except Exception as e:
                    console.print(f"[red]❌ 손절 오류: {r.position.stock_code} - {e}[/red]")
            else:
                console.print(f"[cyan]🔵 [시뮬] 해외 손절: {r.position.stock_code} {r.position.quantity}주 ({r.position.profit_pct:+.1f}%)[/cyan]")

        return executed

    def run_kis_pre_market_check(self) -> bool:
        """한투 장 시작 전 체크리스트"""
        console.print()
        console.print("=" * 60, style="cyan")
        console.print("[bold]🌅 한투 장 시작 전 체크리스트[/bold]", style="cyan")
        console.print("=" * 60, style="cyan")

        checks = []

        # 1. API 연결 상태
        if self.kis_domestic and self.kis_domestic.is_initialized:
            checks.append(("한투 국내 API", True, "연결됨"))
        else:
            checks.append(("한투 국내 API", False, "연결 실패"))

        if self.kis_overseas and self.kis_overseas.is_initialized:
            checks.append(("한투 해외 API", True, "연결됨"))
        else:
            checks.append(("한투 해외 API", False, "연결 실패"))

        # 2. 포지션 조회 가능
        try:
            self.fetch_kis_positions()
            domestic_cnt = len(self.kis_domestic_positions)
            overseas_cnt = len(self.kis_overseas_positions)
            checks.append(("포지션 조회", True, f"국내 {domestic_cnt}종목, 해외 {overseas_cnt}종목"))
        except Exception as e:
            checks.append(("포지션 조회", False, str(e)[:30]))

        # 3. STOP_LOSS 대상 확인
        stop_targets = []
        for r in self.kis_domestic_results:
            if r.action == Action.STOP_LOSS:
                stop_targets.append(r.position.stock_name)
        for r in self.kis_overseas_results:
            if r.action == Action.STOP_LOSS:
                stop_targets.append(r.position.stock_code)

        if stop_targets:
            checks.append(("STOP_LOSS 대상", True, f"{len(stop_targets)}건: {', '.join(stop_targets)[:25]}"))
        else:
            checks.append(("STOP_LOSS 대상", True, "없음 (양호)"))

        # 4. 시장 시간
        now = datetime.now()
        if now.weekday() >= 5:
            checks.append(("시장 시간", False, "주말 휴장"))
        elif now.hour < 9:
            checks.append(("시장 시간", True, "장 시작 전"))
        elif now.hour < 16 or (now.hour == 15 and now.minute <= 30):
            checks.append(("시장 시간", True, "장중"))
        else:
            checks.append(("시장 시간", False, "장 마감"))

        # 결과 표시
        all_passed = True
        for name, passed, msg in checks:
            status = "[green]✅[/green]" if passed else "[red]❌[/red]"
            console.print(f"  {status} {name}: {msg}")
            if not passed:
                all_passed = False

        if all_passed:
            console.print("\n[green]✅ 한투 체크 완료 - 자동 손절 가능[/green]")
        else:
            console.print("\n[yellow]⚠️ 일부 항목 실패 - 확인 필요[/yellow]")

        return all_passed

    def run_kis_post_market_check(self):
        """한투 장 마감 후 체크리스트"""
        console.print()
        console.print("=" * 60, style="blue")
        console.print("[bold]🌙 한투 장 마감 후 체크리스트[/bold]", style="blue")
        console.print("=" * 60, style="blue")

        # 오늘 손절 실행 기록
        today = datetime.now().strftime('%Y%m%d')
        executed_today = self.kis_stop_loss_count.get(today, 0) if hasattr(self, 'kis_stop_loss_count') else 0
        console.print(f"  📊 오늘 손절 실행: {executed_today}건")

        # 현재 포지션 요약
        self.fetch_kis_positions()
        console.print(f"  📊 국내 보유: {len(self.kis_domestic_positions)}종목")
        console.print(f"  📊 해외 보유: {len(self.kis_overseas_positions)}종목")

        # 내일 주의 종목 (-10% ~ -12% 구간)
        warning_stocks = []
        for r in self.kis_domestic_results:
            if -12 < r.position.profit_pct <= -10:
                warning_stocks.append(f"{r.position.stock_name} ({r.position.profit_pct:+.1f}%)")
        for r in self.kis_overseas_results:
            if -12 < r.position.profit_pct <= -10:
                warning_stocks.append(f"{r.position.stock_code} ({r.position.profit_pct:+.1f}%)")

        if warning_stocks:
            console.print(f"\n[yellow]⚠️ 내일 손절 주의 종목:[/yellow]")
            for s in warning_stocks:
                console.print(f"   🟡 {s}")
        else:
            console.print(f"\n[green]✅ 내일 손절 주의 종목 없음[/green]")

        # 재진입 후보 체크
        self.check_kis_reentry()

    def check_kis_reentry(self):
        """한투 손절 후 재진입 체크"""
        COOLDOWN_DAYS = 5
        REENTRY_WEIGHT_PCT = 50

        # 손절 기록 로드
        if not hasattr(self, 'kis_stopped_stocks'):
            self.kis_stopped_stocks = {}

        log_dir = project_root / 'logs'
        for i in range(30):
            d = datetime.now() - timedelta(days=i)
            log_file = log_dir / f"kis_stop_loss_{d.strftime('%Y%m%d')}.json"
            if log_file.exists():
                try:
                    import json
                    with open(log_file, 'r') as f:
                        logs = json.load(f)
                    for log in logs:
                        if log.get('status') == 'executed':
                            symbol = log['symbol']
                            if symbol not in self.kis_stopped_stocks:
                                self.kis_stopped_stocks[symbol] = {
                                    'stop_date': d.date(),
                                    'original_qty': log['quantity'],
                                    'stock_name': log.get('stock_name', symbol)
                                }
                except:
                    pass

        if not self.kis_stopped_stocks:
            return

        console.print(f"\n[cyan]🔄 재진입 후보 평가 ({len(self.kis_stopped_stocks)}건)[/cyan]")

        today = datetime.now().date()
        for symbol, info in self.kis_stopped_stocks.items():
            days_since = (today - info['stop_date']).days
            reentry_qty = int(info['original_qty'] * REENTRY_WEIGHT_PCT / 100)

            if days_since < COOLDOWN_DAYS:
                console.print(f"   ⏳ {info['stock_name']}: 쿨다운 {COOLDOWN_DAYS - days_since}일 남음")
            else:
                # 재진입 조건 체크 (간단 버전)
                console.print(f"   🟢 {info['stock_name']}: 재진입 가능 ({reentry_qty}주, 원래의 {REENTRY_WEIGHT_PCT}%)")

    def _get_stock_info_with_cache(self, stock_code: str) -> Optional[Dict]:
        """
        캐시를 사용하여 종목 정보 조회 (Rate Limit 방지)

        Args:
            stock_code: 종목코드

        Returns:
            종목 정보 dict 또는 None
        """
        import time

        now = time.time()

        # 캐시 확인 (성공/실패 모두)
        if stock_code in self.stock_info_cache:
            cached = self.stock_info_cache[stock_code]
            expiry = self.cache_expiry_seconds if not cached.get('failed') else self.cache_fail_expiry_seconds
            if now - cached['timestamp'] < expiry:
                logger.debug(f"[CACHE_HIT] {stock_code} (failed={cached.get('failed')})")
                return cached.get('info')  # 실패 캐시는 None 반환

        # 429 백오프 중이면 스킵
        if now < self._ka10001_backoff_until:
            wait_left = self._ka10001_backoff_until - now
            logger.debug(f"[BACKOFF] ka10001 차단 중 ({wait_left:.0f}s 남음) - {stock_code} 스킵")
            return None

        # API 호출 딜레이 적용
        time_since_last_call = now - self.last_api_call_time
        if time_since_last_call < self.api_call_delay:
            sleep_time = self.api_call_delay - time_since_last_call
            logger.debug(f"[RATE_SLEEP] {sleep_time:.2f}s")
            time.sleep(sleep_time)

        # API 호출
        try:
            result = self.api.get_stock_info(stock_code=stock_code)
            self.last_api_call_time = time.time()

            if result and result.get('return_code') == 0:
                self.stock_info_cache[stock_code] = {'info': result, 'timestamp': now, 'failed': False}
                return result
            elif result and result.get('return_code') == 429:
                # 429 rate limit → 60초 backoff + 실패 캐시
                self._ka10001_backoff_until = time.time() + 60
                logger.warning(f"[BACKOFF] ka10001 429 → 60초 차단 시작 ({stock_code})")
                self.stock_info_cache[stock_code] = {'info': None, 'timestamp': now, 'failed': True}
                return None
            else:
                # 기타 실패도 캐시 (재호출 방지)
                self.stock_info_cache[stock_code] = {'info': None, 'timestamp': now, 'failed': True}
                return None

        except Exception as e:
            logger.debug(f"[INFO_FAIL] {stock_code}: {e}")
            self.stock_info_cache[stock_code] = {'info': None, 'timestamp': now, 'failed': True}
            return None

    @staticmethod
    def _extract_stock_name(payload: Optional[Any], default: str) -> str:
        """
        다양한 키움 REST 응답 구조에서 종목명을 최대한 추출한다.

        Args:
            payload: API 응답 객체 (dict, list, etc.)
            default: 추출 실패 시 반환할 기본값 (종목코드)
        """
        if not payload:
            return default

        candidates: List[Dict[str, Any]] = []

        def add_candidate(value: Any):
            if isinstance(value, dict):
                candidates.append(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        candidates.append(item)

        add_candidate(payload)
        if isinstance(payload, dict):
            for key in ['output', 'output1', 'output2', 'data', 'result', 'stock_info', 'body']:
                add_candidate(payload.get(key))

        name_keys = [
            'stk_nm', 'hts_kor_isnm', 'stock_name', 'itmsNm', 'hname',
            'prdt_name', 'prdt_abrv_name', 'issue_name', 'kor_name',
            'korSecnNm', 'kor_secn_nm', 'short_name'
        ]

        for candidate in candidates:
            for key in name_keys:
                name = candidate.get(key)
                if isinstance(name, str) and name.strip():
                    return name.strip()

        return default

    def _save_watchlist_to_json(self):
        """
        검증 통과한 종목 정보를 data/watchlist.json에 저장 (문서 명세)
        """
        try:
            import json
            from pathlib import Path

            watchlist_path = Path("data/watchlist.json")
            watchlist_path.parent.mkdir(parents=True, exist_ok=True)

            # validated_stocks를 watchlist 형식으로 변환
            watchlist_data = []
            for stock_code, info in self.validated_stocks.items():
                watchlist_data.append({
                    "stock_code": stock_code,
                    "stock_name": info.get('name', stock_code),
                    "market": info.get('market', 'KOSPI'),
                    "rs_rating": info.get('rs_rating', 0),
                    "ai_score": info.get('analysis', {}).get('total_score', 0) or info.get('ai_score', 0),
                    "win_rate": info.get('stats', {}).get('win_rate', 0),
                    "avg_profit_pct": info.get('stats', {}).get('avg_profit_pct', 0),
                    "total_trades": info.get('stats', {}).get('total_trades', 0),
                    "profit_factor": info.get('stats', {}).get('profit_factor', 0),
                    "last_check_time": datetime.now().isoformat()
                })

            # JSON 파일로 저장
            with open(watchlist_path, 'w', encoding='utf-8') as f:
                json.dump(watchlist_data, f, ensure_ascii=False, indent=2)

            console.print(f"[dim]✓ Watchlist 저장: data/watchlist.json ({len(watchlist_data)}개 종목)[/dim]")

        except Exception as e:
            console.print(f"[yellow]⚠️ Watchlist 저장 실패: {e}[/yellow]")

    def _save_monitoring_watchlist(self):
        """현재 self.watchlist(실시간 모니터링 종목)를 data/monitoring_watchlist.json에 저장."""
        try:
            data = []
            for code in self.watchlist:
                info = self.validated_stocks.get(code, {})
                data.append({
                    'stock_code': code,
                    'stock_name': info.get('name', code),
                })
            import json as _json
            from pathlib import Path as _Path
            _path = _Path('data/monitoring_watchlist.json')
            _path.parent.mkdir(parents=True, exist_ok=True)
            with open(_path, 'w', encoding='utf-8') as f:
                _json.dump({
                    'updated_at': datetime.now().isoformat(),
                    'symbols': data,
                }, f, ensure_ascii=False)
        except Exception:
            pass

    def _refresh_dashboard_cache(self):
        """매수/매도 완료 직후 대시보드 API 캐시 즉시 갱신."""
        try:
            import urllib.request as _req
            _req.urlopen('http://127.0.0.1:8765/api/refresh', data=b'', timeout=2)
        except Exception:
            pass

    def _handle_data_quality_failure(self, stock_code: str, stock_name: str, failure_reason: str):
        """
        데이터 품질 실패 처리 (문서 명세)

        1. watchlist에서 즉시 제거
        2. risk_log.json에 장애 기록

        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            failure_reason: 실패 사유
        """
        try:
            # 1. watchlist에서 제거
            removed_from_watchlist = False
            removed_from_validated = False

            if stock_code in self.watchlist:
                self.watchlist.discard(stock_code)
                removed_from_watchlist = True

            if stock_code in self.validated_stocks:
                del self.validated_stocks[stock_code]
                removed_from_validated = True
                # watchlist.json 재저장
                self._save_watchlist_to_json()

            # 2. risk_log.json에 기록
            import json
            from pathlib import Path

            risk_log_path = Path("data/risk_log.json")
            risk_log_path.parent.mkdir(parents=True, exist_ok=True)

            # 기존 로그 로드
            risk_logs = []
            if risk_log_path.exists():
                try:
                    with open(risk_log_path, 'r', encoding='utf-8') as f:
                        risk_logs = json.load(f)
                except (FileNotFoundError, json.JSONDecodeError, IOError):
                    risk_logs = []

            # 새 로그 추가
            risk_logs.append({
                'timestamp': datetime.now().isoformat(),
                'stock_code': stock_code,
                'stock_name': stock_name,
                'event_type': 'DATA_QUALITY_FAILURE',
                'failure_reason': failure_reason,
                'removed_from_watchlist': removed_from_watchlist,
                'removed_from_validated': removed_from_validated
            })

            # 최근 1000개만 유지 (로그 파일 비대화 방지)
            risk_logs = risk_logs[-1000:]

            # 저장
            with open(risk_log_path, 'w', encoding='utf-8') as f:
                json.dump(risk_logs, f, ensure_ascii=False, indent=2)

            console.print(
                f"[dim]  ⚠️  {stock_name}({stock_code}): watchlist 제거 및 risk_log 기록 완료 - {failure_reason}[/dim]"
            )

        except Exception as e:
            console.print(f"[yellow]⚠️ 데이터 품질 실패 처리 오류: {e}[/yellow]")

    def _get_daily_data(self, stock_code: str, market: Optional[str]) -> Optional[pd.DataFrame]:
        """
        일봉 데이터 조회 (일봉 추세 필터용)

        Args:
            stock_code: 종목 코드
            market: 시장 구분 (KOSPI/KOSDAQ)
        """
        suffix = '.KS' if market == 'KOSPI' else '.KQ'
        ticker = f"{stock_code}{suffix}"

        try:
            history = yf.Ticker(ticker).history(period="90d", interval="1d", auto_adjust=False)
            if history.empty:
                return None

            df = history.reset_index().rename(columns=lambda c: c.lower())
            required_cols = ['open', 'high', 'low', 'close', 'volume']
            if not set(required_cols).issubset(df.columns):
                return None

            return df[required_cols].copy()
        except Exception:
            return None

    async def connect(self):
        """WebSocket 연결"""
        try:
            # 연결 헤더 설정 (Kiwoom API 요구사항)
            headers = {
                "Authorization": f"Bearer {self.access_token}",
                "Content-Type": "application/json",
            }

            self.websocket = await websockets.connect(
                self.uri,
                additional_headers=headers,  # websockets 15.0+ uses additional_headers
                ping_interval=20,  # 20초마다 ping
                ping_timeout=10,   # 10초 타임아웃
            )
            self.connected = True
            console.print("=" * 120, style="bold green")
            console.print(f"{'키움 통합 자동매매 시스템':^120}", style="bold green")
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

    async def receive_message(self, timeout: float = 10.0, expected_trnm: str = None, expected_seq: str = None):
        """WebSocket 메시지 수신 (타임아웃 추가, PING 무시, 특정 trnm/seq 필터링)

        Args:
            timeout: 타임아웃 시간 (초)
            expected_trnm: 기대하는 trnm 값 (None이면 PING만 제외하고 모든 메시지 수신)
            expected_seq: 기대하는 seq 값 (None이면 seq 무시, trnm만 체크)
        """
        if not self.websocket or not self.connected:
            raise Exception("WebSocket이 연결되지 않았습니다.")

        try:
            start_time = time.time()
            while True:
                remaining_time = timeout - (time.time() - start_time)
                if remaining_time <= 0:
                    raise asyncio.TimeoutError()

                message = await asyncio.wait_for(self.websocket.recv(), timeout=remaining_time)
                data = json.loads(message)
                trnm = data.get('trnm')
                seq = data.get('seq')

                # 🔧 CRITICAL FIX: PING 메시지 무시
                if trnm == 'PING':
                    console.print(f"[dim]  ♥ PING (keep-alive)[/dim]")
                    continue  # PING 무시하고 다음 메시지 대기

                # 🔧 NEW: 특정 trnm을 기대하는 경우, 해당 메시지만 받음
                if expected_trnm and trnm != expected_trnm:
                    console.print(f"[dim]  ⚠ 무시: trnm={trnm} (기대값: {expected_trnm})[/dim]")
                    continue  # 기대하지 않은 메시지 무시

                # 🔧 CRITICAL FIX: seq 매칭 (조건검색 재실행 시 이전 응답 무시)
                if expected_seq and seq != expected_seq:
                    console.print(f"[dim]  ⚠ 무시: seq={seq} (기대값: {expected_seq}, trnm={trnm})[/dim]")
                    continue  # seq가 다른 응답 무시

                return data  # 원하는 응답만 리턴

        except asyncio.TimeoutError:
            console.print(f"[yellow]⚠️  응답 대기 시간 초과 ({timeout}초)[/yellow]")
            return None

    def _write_heartbeat(self, stage: str = "monitoring"):
        """Watchdog 하트비트 파일 갱신 (좀비 프로세스 감지용)

        stage: "init" | "connecting" | "filtering" | "monitoring" | "eod"
        watchdog.py가 이 파일의 갱신 여부로 생존 판단.
        """
        import json as _json
        try:
            data = {
                "pid": os.getpid(),
                "stage": stage,
                "time": datetime.now().isoformat(),
            }
            self._heartbeat_path.write_text(_json.dumps(data))
        except Exception:
            pass  # heartbeat 실패 시 무시 (주요 로직 방해 금지)

    # ─── 포지션 상태 영속화 (재시작 복원용) ─────────────────────────────────
    _POSITIONS_STATE_PATH = 'data/positions_state.json'

    def _save_positions_state(self):
        """self.positions를 JSON 파일로 저장 (재시작 복원용)"""
        import json as _json
        try:
            today = datetime.now().strftime('%Y-%m-%d')
            state = {}
            for code, pos in self.positions.items():
                entry = {}
                for k, v in pos.items():
                    if isinstance(v, datetime):
                        entry[k] = v.isoformat()
                    else:
                        try:
                            _json.dumps(v)  # serializable 검증
                            entry[k] = v
                        except (TypeError, ValueError):
                            entry[k] = str(v)
                entry['_saved_date'] = today
                state[code] = entry
            with open(self._POSITIONS_STATE_PATH, 'w', encoding='utf-8') as f:
                _json.dump(state, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"[POS_STATE] 저장 실패: {e}")

    def _restore_positions_state(self):
        """재시작 시 positions_state.json에서 self.positions 복원"""
        import json as _json
        import os as _os
        try:
            if not _os.path.exists(self._POSITIONS_STATE_PATH):
                return
            today = datetime.now().strftime('%Y-%m-%d')
            with open(self._POSITIONS_STATE_PATH, 'r', encoding='utf-8') as f:
                state = _json.load(f)
            restored = 0
            for code, entry in state.items():
                # entry_date 기준으로 당일 여부 판단 (재시작 날짜가 아닌 진입 날짜)
                entry_date_str = entry.get('entry_date', entry.get('entry_time', ''))
                if isinstance(entry_date_str, str) and not entry_date_str.startswith(today):
                    continue  # 당일 포지션만 복원
                if code in self.positions:
                    continue  # 이미 있으면 스킵
                # datetime 필드 복원
                for dt_key in ('entry_time', 'entry_date'):
                    if dt_key in entry and isinstance(entry[dt_key], str):
                        try:
                            entry[dt_key] = datetime.fromisoformat(entry[dt_key])
                        except ValueError:
                            pass
                entry.pop('_saved_date', None)
                self.positions[code] = entry
                restored += 1
                logger.info(f"[POS_RESTORE] {entry.get('stock_name', code)} 복원 완료 (진입가={entry.get('entry_price')})")
            if restored > 0:
                logger.info(f"[POS_RESTORE] 총 {restored}건 포지션 복원")
        except Exception as e:
            logger.warning(f"[POS_STATE] 복원 실패: {e}")
    # ────────────────────────────────────────────────────────────────────────

    def refresh_access_token(self):
        """
        Access Token 강제 재발급

        Returns:
            bool: 재발급 성공 여부
        """
        try:
            console.print("[yellow]🔄 Access Token 재발급 시도 중...[/yellow]")

            # 기존 토큰 정보 초기화 (재발급 강제)
            self.api.access_token = None
            self.api.token_expires_at = None

            # 새 토큰 발급
            new_token = self.api.get_access_token()

            if new_token:
                self.access_token = new_token
                console.print("[green]✅ Access Token 재발급 성공[/green]")
                return True
            else:
                console.print("[red]❌ Access Token 재발급 실패[/red]")
                return False

        except Exception as e:
            console.print(f"[red]❌ Access Token 재발급 중 오류: {e}[/red]")
            return False

    async def validate_token(self):
        """
        Access Token 유효성 검증 (REST API 호출 테스트)

        Returns:
            bool: 토큰 유효 여부
        """
        try:
            console.print("[cyan]🔍 Token 유효성 검증 중...[/cyan]")

            # 간단한 API 호출로 토큰 테스트 (계좌 잔고 조회)
            balance_info = self.api.get_balance()

            # return_code가 0이면 성공
            return_code = balance_info.get('return_code', -1)

            if return_code == 0:
                console.print("[green]✅ Token 유효성 확인 완료[/green]")
                return True
            elif return_code == 8005:  # Token invalid
                console.print("[yellow]⚠️  Token이 유효하지 않음 (Code: 8005)[/yellow]")
                return False
            else:
                console.print(f"[yellow]⚠️  API 응답 코드: {return_code} - {balance_info.get('return_msg')}[/yellow]")
                return False

        except Exception as e:
            console.print(f"[yellow]⚠️  Token 검증 실패: {e}[/yellow]")
            return False

    async def login(self, max_retries=3):
        """
        WebSocket 로그인 (재시도 및 토큰 갱신 포함)

        Args:
            max_retries: 최대 재시도 횟수 (기본값: 3)

        Returns:
            bool: 로그인 성공 여부
        """
        for attempt in range(1, max_retries + 1):
            try:
                # 15:20 이후 키움 WebSocket 서버가 신규 로그인을 거부함 → 재시도 불필요
                now_hm = datetime.now().strftime('%H:%M')
                if now_hm >= '15:20':
                    console.print(f"[yellow]⏩ WebSocket 로그인 스킵 — 장 마감 시간대({now_hm}), 재연결 불필요[/yellow]")
                    return False

                console.print()
                console.print(f"[{datetime.now().strftime('%H:%M:%S')}] WebSocket 로그인 시도 ({attempt}/{max_retries})")

                # 로그인 패킷 전송
                login_packet = {'trnm': 'LOGIN', 'token': self.access_token}
                await self.websocket.send(json.dumps(login_packet))

                # 응답 수신
                response = await self.receive_message(timeout=10.0)

                if not response:
                    console.print(f"[yellow]⚠️  응답 없음 (시도 {attempt}/{max_retries})[/yellow]")
                    if attempt < max_retries:
                        console.print("[cyan]💤 5초 후 재시도...[/cyan]")
                        await asyncio.sleep(5)
                        continue
                    else:
                        return False

                return_code = response.get("return_code")
                return_msg = response.get("return_msg", "")

                # 로그인 성공
                if return_code == 0:
                    console.print("✅ 로그인 성공", style="green")
                    # 인증 완료 대기 (조건검색 등 API 호출 전에 필수!)
                    console.print("[yellow]⏳ 서버 인증 처리 대기 중... (3초)[/yellow]")
                    await asyncio.sleep(3.0)
                    console.print("[green]✅ 인증 완료[/green]")
                    console.print()
                    return True

                # 토큰 오류 (8005)
                elif return_code == 8005:
                    console.print(f"[red]❌ 로그인 실패: 토큰 인증 오류 [CODE={return_code}][/red]")
                    console.print(f"[red]   메시지: {return_msg}[/red]")

                    if attempt < max_retries:
                        # 토큰 재발급 시도
                        console.print(f"[yellow]🔄 토큰 재발급 후 재시도 ({attempt}/{max_retries})...[/yellow]")

                        # WebSocket 재연결 (기존 연결 종료)
                        if self.websocket:
                            try:
                                await self.websocket.close()
                            except Exception:
                                pass  # Websocket close errors are non-critical

                        # 토큰 재발급
                        if self.refresh_access_token():
                            console.print("[cyan]💤 3초 대기 후 WebSocket 재연결...[/cyan]")
                            await asyncio.sleep(3)

                            # WebSocket 재연결
                            try:
                                await self.connect()
                                console.print("[green]✅ WebSocket 재연결 완료[/green]")
                                await asyncio.sleep(2)  # 안정화 대기
                                continue  # 다음 로그인 시도
                            except Exception as e:
                                console.print(f"[red]❌ WebSocket 재연결 실패: {e}[/red]")
                                if attempt < max_retries:
                                    await asyncio.sleep(5)
                                    continue
                        else:
                            console.print("[red]❌ 토큰 재발급 실패[/red]")
                            if attempt < max_retries:
                                await asyncio.sleep(5)
                                continue
                    else:
                        console.print(f"[red]💀 최대 재시도 횟수 초과 ({max_retries}회)[/red]")
                        return False

                # 기타 오류
                else:
                    console.print(f"[red]❌ 로그인 실패: [CODE={return_code}] {return_msg}[/red]")

                    if attempt < max_retries:
                        console.print(f"[cyan]💤 5초 후 재시도 ({attempt}/{max_retries})...[/cyan]")
                        await asyncio.sleep(5)
                        continue
                    else:
                        return False

            except Exception as e:
                console.print(f"[red]❌ 로그인 중 예외 발생: {e}[/red]")
                import traceback
                traceback.print_exc()

                if attempt < max_retries:
                    console.print(f"[cyan]💤 5초 후 재시도 ({attempt}/{max_retries})...[/cyan]")
                    await asyncio.sleep(5)
                    continue
                else:
                    return False

        # 모든 시도 실패
        console.print(f"[red]💀 로그인 실패: 모든 재시도 소진 ({max_retries}회)[/red]")
        return False

    async def initialize_account(self):
        """계좌 정보 초기화 (시스템 시작 시)"""
        from core.risk_manager import RiskManager

        console.print()
        console.print("=" * 120, style="bold cyan")
        console.print(f"{'계좌 정보 조회':^120}", style="bold cyan")
        console.print("=" * 120, style="bold cyan")

        try:
            # 1. 계좌 잔고 조회 (API-ID: kt00001)
            balance_info = self.api.get_balance()

            # 예수금 파싱 (15자리 문자열 → 숫자)
            cash_str = balance_info.get('entr', '000000000000000')
            self.current_cash = float(cash_str)

            # 2. 보유 종목 조회 (API-ID: ka01690)
            account_info = self.api.get_account_info()
            positions = account_info.get('day_bal_rt', [])

            # 3. 보유 포지션 평가액 계산
            self.positions_value = 0.0
            for pos in positions:
                # 빈 종목은 스킵
                if not pos.get('stk_cd') or pos.get('stk_cd') == '':
                    continue

                cur_prc = int(pos.get('cur_prc', 0)) if pos.get('cur_prc') else 0
                rmnd_qty = int(pos.get('rmnd_qty', 0)) if pos.get('rmnd_qty') else 0
                self.positions_value += cur_prc * rmnd_qty

            # 4. 총 자산
            self.total_assets = self.current_cash + self.positions_value

            # 4. 계좌 정보 출력
            table = Table(title="💰 계좌 현황", box=box.ROUNDED, show_header=True, header_style="bold magenta")
            table.add_column("항목", style="cyan", width=20)
            table.add_column("금액", style="yellow", justify="right", width=20)

            table.add_row("계좌번호", self.api.account_number)
            table.add_row("예수금", f"{self.current_cash:,.0f}원")
            table.add_row("보유종목 평가", f"{self.positions_value:,.0f}원")
            table.add_row("총 자산", f"{self.total_assets:,.0f}원")
            table.add_row("보유종목 수", f"{len(positions)}개")

            console.print(table)
            console.print()

            # 계좌 스냅샷 DB 저장 (시작 시 1회)
            _save_account_snapshot_to_db(
                deposit=int(self.current_cash),
                holding_value=int(self.positions_value),
                total_assets=int(self.total_assets),
                eval_profit=int(float(account_info.get('tot_evltv_prft', 0) or 0)),
                source='init',
            )

            # 5. 보유 포지션 로드
            if positions:
                console.print("[bold]보유 포지션:[/bold]")
                for pos in positions:
                    # 빈 종목은 스킵
                    stock_code = pos.get('stk_cd', '')
                    if not stock_code or stock_code == '':
                        continue

                    stock_name = pos.get('stk_nm', '')
                    quantity = int(pos.get('rmnd_qty', 0)) if pos.get('rmnd_qty') else 0
                    avg_price = int(pos.get('buy_uv', 0)) if pos.get('buy_uv') else 0
                    current_price = int(pos.get('cur_prc', 0)) if pos.get('cur_prc') else 0
                    profit_rate = float(pos.get('prft_rt', 0)) if pos.get('prft_rt') else 0.0

                    # 🔧 FIX: DB에서 실제 매수일자 조회
                    entry_date = None
                    try:
                        from database.trading_db import TradingDatabase
                        db = TradingDatabase()
                        # 완료된 매수 거래에서 해당 종목의 최근 매수일자 조회
                        trades = db.get_trades(stock_code=stock_code)
                        if trades and len(trades) > 0:
                            # 최근 매수 거래 찾기 (DESC 정렬이므로 첫 번째가 최신)
                            for trade in trades:
                                if trade.get('trade_type') == 'BUY':
                                    trade_time = trade.get('trade_time') or trade.get('created_at')
                                    if trade_time:
                                        if isinstance(trade_time, str):
                                            entry_date = datetime.fromisoformat(trade_time.replace(' ', 'T'))
                                        else:
                                            entry_date = trade_time
                                        break
                    except Exception as e:
                        console.print(f"[yellow]  ⚠️  {stock_code} 매수일자 조회 실패: {e}[/yellow]")
                        pass

                    # DB에서 못 가져왔으면 현재 시간으로 설정 (신규 감시 종목일 가능성)
                    if not entry_date:
                        entry_date = datetime.now()

                    # 🔧 FIX: 기존 position 데이터 보존 (모든 필드 유지)
                    if stock_code in self.positions:
                        # 기존 포지션이 있으면 업데이트만
                        self.positions[stock_code].update({
                            'stock_name': stock_name,
                            'name': stock_name,
                            'quantity': quantity,
                            'avg_price': avg_price,
                            'entry_price': avg_price,
                            'current_price': current_price,
                            'profit_rate': profit_rate,
                            'eval_amount': quantity * current_price,
                            'entry_date': entry_date
                        })
                    else:
                        # 신규 포지션이면 새로 생성
                        self.positions[stock_code] = {
                            'stock_name': stock_name,
                            'name': stock_name,
                            'quantity': quantity,
                            'avg_price': avg_price,
                            'entry_price': avg_price,
                            'current_price': current_price,
                            'profit_rate': profit_rate,
                            'eval_amount': quantity * current_price,
                            'entry_date': entry_date,
                            'highest_price': avg_price,
                            'trailing_active': False,
                            'trailing_stop_price': None,
                            'partial_exit_stage': 0,
                            'gap_reentered_today': False
                        }

                    console.print(f"  • {stock_name}({stock_code}): {quantity}주 @ {current_price:,}원 "
                                f"[{'green' if profit_rate >= 0 else 'red'}]{profit_rate:+.2f}%[/]")
                console.print()

                # 🔧 CRITICAL FIX: 기존 포지션 재평가 (allow_overnight 설정)
                console.print("[bold cyan]🔍 기존 포지션 익일 보유 재평가 중...[/bold cyan]")
                for stock_code, position in self.positions.items():
                    try:
                        # OHLCV 데이터 조회 (5분봉)
                        result = self.api.get_minute_chart(
                            stock_code=stock_code,
                            tic_scope="5",
                            upd_stkpc_tp="1"
                        )

                        df = None
                        if result.get('return_code') == 0:
                            # 응답 데이터 추출
                            data = None
                            for key in ['stk_min_pole_chart_qry', 'stk_mnut_pole_chart_qry', 'output', 'output1', 'data']:
                                if key in result and result[key]:
                                    data = result[key]
                                    break

                            if data and len(data) > 0:
                                import pandas as pd
                                df = pd.DataFrame(data)

                                # 컬럼 매핑 (ka10080 API 기준)
                                column_mapping = {
                                    'cur_prc': 'close',
                                    'open_pric': 'open',
                                    'high_pric': 'high',
                                    'low_pric': 'low',
                                    'trd_qty': 'volume',
                                    'trd_dt': 'date',
                                    'trd_tm': 'time'
                                }
                                df.rename(columns=column_mapping, inplace=True)

                                # 숫자 변환
                                for col in ['close', 'open', 'high', 'low', 'volume']:
                                    if col in df.columns:
                                        df[col] = pd.to_numeric(df[col], errors='coerce')

                        if df is not None and not df.empty:
                            # 재평가 수행
                            allow_overnight, overnight_score = self.should_allow_overnight(
                                stock_code=stock_code,
                                df=df,
                                signal_result={},  # 기존 포지션이므로 빈 dict 전달
                                entry_confidence=0.6  # 기존 포지션은 진입 시 승인되었다고 가정
                            )

                            # 포지션에 플래그 설정
                            position['allow_overnight'] = allow_overnight
                            position['overnight_score'] = overnight_score

                            # ── overnight_v2: 외국인 매도 시 포지션 50% 축소 ──
                            ov2_cfg = self.config.get('overnight_v2', {})
                            if (allow_overnight
                                    and ov2_cfg.get('reduce_on_foreign_sell', True)):
                                _sd_score = position.get('score_supply_demand', 50)
                                _fth = ov2_cfg.get('foreign_sell_threshold', 30)
                                if _sd_score < _fth:
                                    _qty = position.get('quantity', 0)
                                    _reduce_qty = int(_qty * 0.5)
                                    if _reduce_qty > 0:
                                        logger.info(
                                            f"[OV2_FOREIGN_REDUCE] {stock_code}: "
                                            f"수급점수 {_sd_score:.0f} < {_fth} "
                                            f"→ 오버나이트 포지션 50% 축소 ({_qty}→{_qty - _reduce_qty}주)"
                                        )
                                        # 절반 시장가 매도 (execute_sell의 경량 버전)
                                        try:
                                            self.api.send_order(
                                                stock_code=stock_code,
                                                order_type='2',  # 매도
                                                quantity=_reduce_qty,
                                                price=0,         # 시장가
                                                trade_type='01'  # 시장가
                                            )
                                            position['quantity'] = _qty - _reduce_qty
                                            console.print(
                                                f"  [yellow][OV2] {position['name']:12s} | "
                                                f"외국인 매도 감지(수급{_sd_score:.0f}) → {_reduce_qty}주 축소[/yellow]"
                                            )
                                        except Exception as _e:
                                            logger.warning(f"[OV2_REDUCE_FAIL] {stock_code}: {_e}")

                            status = "✅ 익일보유승인" if allow_overnight else "⚠️  익일보유불가"
                            console.print(
                                f"  {position['name']:15s} | {status} | "
                                f"Score: {overnight_score:.2f} (기준: 0.6)"
                            )
                        else:
                            # 데이터 조회 실패 시 보수적으로 False 설정
                            position['allow_overnight'] = False
                            position['overnight_score'] = 0.0
                            console.print(
                                f"  {position['name']:15s} | ⚠️  데이터 없음 → 익일보유불가"
                            )

                    except Exception as e:
                        # 오류 발생 시 보수적으로 False 설정
                        position['allow_overnight'] = False
                        position['overnight_score'] = 0.0
                        console.print(
                            f"  {position['name']:15s} | ❌ 재평가 오류 → 익일보유불가"
                        )
                        console.print(f"[dim red]     오류: {str(e)}[/dim red]")
                        import traceback
                        console.print(f"[dim]{traceback.format_exc()}[/dim]")

                console.print()

            # 6. 리스크 관리자 초기화 (실제 잔고 기반 + 설정 파일 연동)
            self.risk_manager = RiskManager(
                initial_balance=self.current_cash,
                storage_path='data/risk_log.json',
                config=self.config.config  # 🔧 REFACTOR: 설정 파일 전달 (수정: _config → config)
            )

            console.print(f"[green]✓ 리스크 관리자 초기화 완료 (초기 잔고: {self.current_cash:,.0f}원)[/green]")

            # 시작 시 account_snapshot.json 저장 (대시보드 실계좌 표시용)
            try:
                import json as _json_init
                from pathlib import Path as _Path_init
                _snap_init = {
                    'updated_at':    datetime.now().isoformat(),
                    'deposit':       int(self.current_cash),
                    'holding_value': int(self.positions_value),
                    'total_assets':  int(self.total_assets),
                    'holdings': [
                        {
                            'code':    p.get('stk_cd', ''),
                            'name':    p.get('stk_nm', ''),
                            'qty':     int(p.get('rmnd_qty', 0) or 0),
                            'cur_prc': int(p.get('cur_prc', 0) or 0),
                            'buy_prc': int(p.get('buy_uv', 0) or 0),
                            'pnl_pct': float(p.get('prft_rt', 0) or 0),
                        }
                        for p in positions
                        if p.get('stk_cd')
                    ],
                    'source':  'kiwoom_api',
                    'account': '6259-3479',
                }
                _Path_init('data').mkdir(exist_ok=True)
                _Path_init('data/account_snapshot.json').write_text(
                    _json_init.dumps(_snap_init, ensure_ascii=False, indent=2)
                )
                console.print(f"[dim]✓ account_snapshot.json 저장 완료[/dim]")
            except Exception as _se:
                console.print(f"[yellow]⚠️  account_snapshot 저장 실패 (무시): {_se}[/yellow]")

            # 🔧 2026-02-19: Loss Streak Guard 시작 시 체크
            self._check_loss_streak_guard()

            # 거래 내역 검증 및 동기화 시스템 (누락 방지)
            self.reconciliation = TradeReconciliation(
                api=self.api,
                risk_manager=self.risk_manager,
                db=self.db
            )
            console.print("[dim]✓ TradeReconciliation 초기화 완료 (자동 검증 & 동기화)[/dim]")

            console.print()

        except Exception as e:
            console.print(f"[red]❌ 계좌 정보 조회 실패: {e}[/red]")
            console.print("[yellow]⚠️  기본값으로 초기화합니다 (10,000,000원)[/yellow]")

            # 기본값으로 초기화
            self.current_cash = 10000000
            self.positions_value = 0
            self.total_assets = 10000000

            self.risk_manager = RiskManager(
                initial_balance=self.current_cash,
                storage_path='data/risk_log.json',
                config=self.config.config  # 🔧 REFACTOR: 설정 파일 전달 (수정: _config → config)
            )

            # 🔧 2026-02-19: Loss Streak Guard 시작 시 체크
            self._check_loss_streak_guard()

            # 거래 내역 검증 및 동기화 시스템 (누락 방지) - 기본값 경로
            self.reconciliation = TradeReconciliation(
                api=self.api,
                risk_manager=self.risk_manager,
                db=self.db
            )
            console.print("[dim]✓ TradeReconciliation 초기화 완료 (자동 검증 & 동기화)[/dim]")

            console.print()

    async def update_account_balance(self):
        """거래 후 실시간 잔고 업데이트"""
        try:
            # 1. 계좌 잔고 조회 (API-ID: kt00001)
            balance_info = self.api.get_balance()
            cash_str = balance_info.get('entr', str(int(self.current_cash)).zfill(15))
            self.current_cash = float(cash_str)

            # 2. 보유 종목 조회 (API-ID: ka01690)
            account_info = self.api.get_account_info()
            positions = account_info.get('day_bal_rt', [])

            # 3. 보유 포지션 평가액 계산
            self.positions_value = 0.0
            for pos in positions:
                if not pos.get('stk_cd') or pos.get('stk_cd') == '':
                    continue

                cur_prc = int(pos.get('cur_prc', 0)) if pos.get('cur_prc') else 0
                rmnd_qty = int(pos.get('rmnd_qty', 0)) if pos.get('rmnd_qty') else 0
                self.positions_value += cur_prc * rmnd_qty

            # 4. 총 자산
            self.total_assets = self.current_cash + self.positions_value

            # 4-1. 에쿼티 전고점 갱신 (멀티데이 DD 추적)
            if hasattr(self, 'equity_ctrl'):
                self.equity_ctrl.update_peak(self.total_assets)

            # 5. 리스크 관리자 잔고 업데이트
            if self.risk_manager:
                self.risk_manager.update_balance(self.current_cash)

            # 6. 대시보드용 account_snapshot.json 저장
            _snap = {
                'updated_at':    datetime.now().isoformat(),
                'deposit':       int(self.current_cash),
                'holding_value': int(self.positions_value),
                'total_assets':  int(self.total_assets),
                'holdings':      [
                    {
                        'code':    p.get('stk_cd', ''),
                        'name':    p.get('stk_nm', ''),
                        'qty':     int(p.get('rmnd_qty', 0) or 0),
                        'cur_prc': int(p.get('cur_prc', 0) or 0),
                        'buy_prc': int(p.get('buy_uv', 0) or 0),
                        'pnl_pct': float(p.get('prft_rt', 0) or 0),
                    }
                    for p in positions
                    if p.get('stk_cd')
                ],
                'source': 'kiwoom_api',
                'account': '6259-3479',
            }
            try:
                import json as _json_snap
                from pathlib import Path as _Path_snap
                _Path_snap('data').mkdir(exist_ok=True)
                _Path_snap('data/account_snapshot.json').write_text(
                    _json_snap.dumps(_snap, ensure_ascii=False, indent=2)
                )
            except Exception as _se:
                logger.debug(f'[ACCT_SNAP] 저장 실패 (무시): {_se}')

            # DB 저장 (total_assets > 0 일 때만 — API 0응답 방지)
            _save_account_snapshot_to_db(
                deposit=int(self.current_cash),
                holding_value=int(self.positions_value),
                total_assets=int(self.total_assets),
                eval_profit=int(float(account_info.get('tot_evltv_prft', 0) or 0)),
            )

            console.print(f"[dim]💰 잔고 업데이트: {self.current_cash:,.0f}원 (총자산: {self.total_assets:,.0f}원)[/dim]")

        except Exception as e:
            console.print(f"[yellow]⚠️  잔고 업데이트 실패: {e}[/yellow]")

    async def get_condition_list(self):
        """조건검색식 목록 조회"""
        console.print("[1] 조건검색식 목록 조회")
        console.print()

        await self.send_message("CNSRLST")
        response = await self.receive_message()

        if response.get("return_code") == 0:
            self.condition_list = response.get("data", [])
            console.print(f"✅ 총 {len(self.condition_list)}개 조건검색식 조회 완료", style="green")
            console.print()

            # 사용할 조건식 표시
            console.print(f"🎯 사용 조건식 인덱스: {self.condition_indices}", style="bold cyan")
            for idx in self.condition_indices:
                if idx < len(self.condition_list):
                    condition = self.condition_list[idx]
                    seq = condition[0] if len(condition) > 0 else "?"
                    name = condition[1] if len(condition) > 1 else "?"
                    console.print(f"  [{idx}] {name} (seq: {seq})", style="green")
            console.print()

            return True
        else:
            console.print(f"[red]❌ 조건검색식 조회 실패[/red]")
            return False

    async def search_condition(self, seq: str, name: str, retry_count: int = 0, max_retries: int = 2):
        """조건검색 실행"""
        try:
            # 요청 전송
            start_time = time.time()
            await self.send_message("CNSRREQ", {
                "seq": seq,
                "search_type": "1",
                "stex_tp": "K"
            })

            # 응답 수신 (타임아웃 30초 - 조건검색은 시간 소요가 길 수 있음)
            # 🔧 CRITICAL FIX: CNSRREQ 응답만 기다림 + seq 매칭 (재실행 시 이전 응답 무시)
            response = await self.receive_message(timeout=30.0, expected_trnm="CNSRREQ", expected_seq=seq)
            elapsed = time.time() - start_time

            if response is None:
                console.print(f"[yellow]⚠️  응답 없음 (타임아웃 30초 초과, 총 {elapsed:.1f}초 소요)[/yellow]")
                return []

            # 디버깅: 응답 확인
            return_code = response.get('return_code')
            data = response.get('data')

            # 전체 응답 구조 확인 — debug 레벨로 이동 (노이즈 제거)
            logger.debug(f"[API_RESP] elapsed={elapsed:.2f}s return_code={return_code} len={len(data) if data else 0}")

            # return_code가 None이거나 0이면 정상 처리
            if return_code is None or return_code == 0:
                stock_list = response.get("data", [])

                # None 체크
                if stock_list is None:
                    return []

                stock_codes = [s.get("jmcode", "").replace("A", "") for s in stock_list]
                stock_codes = [code for code in stock_codes if code]
                return stock_codes
            else:
                error_msg = response.get('return_msg', 'Unknown')
                console.print(f"[yellow]⚠️  오류: {error_msg} (응답시간: {elapsed:.1f}초)[/yellow]")
                return []
        except websockets.exceptions.ConnectionClosedOK as e:
            if retry_count >= max_retries:
                console.print(f"[red]❌ 재시도 횟수 초과 ({retry_count}/{max_retries}), 건너뜀[/red]")
                return []

            console.print(f"[yellow]⚠️  WebSocket 연결 종료됨, 재연결 시도 ({retry_count + 1}/{max_retries})...[/yellow]")
            # 재연결 시도
            try:
                await asyncio.sleep(1.0)  # 1초 대기 후 재연결
                await self.connect()
                # 재연결 성공 후 로그인 필수
                console.print(f"[green]✓ 재연결 성공, 로그인 중...[/green]")
                login_success = await self.login()
                if not login_success:
                    console.print(f"[red]❌ 재연결 후 로그인 실패[/red]")
                    return []
                # 🔧 CRITICAL FIX: 재연결 후 조건검색 목록 다시 조회 필수!
                console.print(f"[green]✓ 조건검색 목록 다시 조회 중...[/green]")
                await self.get_condition_list()
                console.print(f"[green]✓ 조건검색 재시도: {name}[/green]")
                return await self.search_condition(seq, name, retry_count + 1, max_retries)
            except Exception as reconnect_error:
                console.print(f"[red]❌ 재연결 실패: {reconnect_error}[/red]")
                return []
        except websockets.exceptions.ConnectionClosed as e:
            if retry_count >= max_retries:
                console.print(f"[red]❌ 재시도 횟수 초과 ({retry_count}/{max_retries}), 건너뜀[/red]")
                return []

            console.print(f"[red]❌ WebSocket 연결 끊김, 재연결 시도 ({retry_count + 1}/{max_retries})...[/red]")
            # 재연결 시도
            try:
                await asyncio.sleep(1.0)  # 1초 대기 후 재연결
                await self.connect()
                # 재연결 성공 후 로그인 필수
                console.print(f"[green]✓ 재연결 성공, 로그인 중...[/green]")
                login_success = await self.login()
                if not login_success:
                    console.print(f"[red]❌ 재연결 후 로그인 실패[/red]")
                    return []
                # 🔧 CRITICAL FIX: 재연결 후 조건검색 목록 다시 조회 필수!
                console.print(f"[green]✓ 조건검색 목록 다시 조회 중...[/green]")
                await self.get_condition_list()
                console.print(f"[green]✓ 조건검색 재시도: {name}[/green]")
                return await self.search_condition(seq, name, retry_count + 1, max_retries)
            except Exception as reconnect_error:
                console.print(f"[red]❌ 재연결 실패: {reconnect_error}[/red]")
                return []
        except Exception as e:
            console.print(f"[red]❌ 조건검색 오류: {e}[/red]")
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
            return []

    async def run_condition_filtering(self):
        """1차 + 2차 필터링 실행"""
        console.print()
        console.print("=" * 120, style="bold cyan")
        console.print(f"{'조건검색 + VWAP 필터링':^120}", style="bold cyan")
        console.print("=" * 120, style="bold cyan")
        console.print()

        try:
            # 🔧 FIX: StockGravity 종목은 유지하고, 조건검색 종목만 초기화
            stockgravity_stocks = {
                code: info for code, info in self.validated_stocks.items()
                if info.get('source') == 'stockgravity'
            }

            # 기존 조건검색 종목만 제거
            condition_codes = [
                code for code, info in self.validated_stocks.items()
                if info.get('source') == 'condition_search'
            ]
            for code in condition_codes:
                self.watchlist.discard(code)
                self.validated_stocks.pop(code, None)

            if stockgravity_stocks:
                console.print(f"[dim]✓ 조건검색 종목 초기화 ({len(condition_codes)}개 제거), StockGravity {len(stockgravity_stocks)}개 유지[/dim]")
            else:
                console.print(f"[dim]✓ 조건검색 종목 초기화 ({len(condition_codes)}개 제거)[/dim]")
            console.print()

            # 1차 필터: 조건검색
            console.print("[bold cyan]1차 필터: 조건검색 실행[/bold cyan]")
            console.print()

            # ✅ Bottom Pullback 조건 인덱스 확인
            try:
                condition_strategies = self.config.get_section('condition_strategies')
                bottom_pullback = condition_strategies.get('bottom_pullback', {}) if condition_strategies else {}
                bottom_indices = bottom_pullback.get('condition_indices', [])
            except (KeyError, AttributeError):
                bottom_indices = []

            all_stocks = set()
            bottom_stocks = {}  # {stock_code: condition_idx} (backward compatibility)
            stock_to_condition_map = {}  # ✅ 모든 종목의 조건 인덱스 추적

            # DEBUG 로그
            with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now()}] 사용 조건식 인덱스: {self.condition_indices}\n")
                f.write(f"  전체 조건식 수: {len(self.condition_list)}\n")
                f.flush()

            for idx in self.condition_indices:
                if idx < len(self.condition_list):
                    condition = self.condition_list[idx]
                    seq = condition[0]
                    name = condition[1]

                    console.print(f"[yellow]조건식 [{idx}] {name} 검색 중...[/yellow]")

                    stocks = await self.search_condition(seq, name)
                    console.print(f"  ✅ {len(stocks)}개 종목 발견")

                    # DEBUG 로그
                    with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                        f.write(f"[{datetime.now()}] 조건식 [{idx}] '{name}' → {len(stocks)}개 종목\n")
                        if stocks:
                            f.write(f"  종목코드: {list(stocks)[:5]}\n")  # 최대 5개만
                        f.flush()

                    # ✅ Bottom 전략 분기 처리
                    if idx in bottom_indices:
                        # Bottom 전략: 별도 저장 (L2/L3 필터 이후 신호 등록)
                        console.print(f"  [cyan]→ Bottom Pullback 전략: Pullback 대기 모드[/cyan]")
                        for stock_code in stocks:
                            bottom_stocks[stock_code] = idx  # backward compatibility
                            stock_to_condition_map[stock_code] = idx  # ✅ 조건 인덱스 저장
                            all_stocks.add(stock_code)  # L2/L3 필터 적용 위해 추가
                    else:
                        # 기존 Momentum 전략: 즉시 매수 대상
                        for stock_code in stocks:
                            stock_to_condition_map[stock_code] = idx  # ✅ 조건 인덱스 저장
                        all_stocks.update(stocks)

                    await asyncio.sleep(0.5)
                else:
                    # 인덱스가 범위를 벗어남
                    with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                        f.write(f"[{datetime.now()}] ⚠️ 조건식 인덱스 [{idx}] 범위 초과 (전체: {len(self.condition_list)}개)\n")
                        f.flush()
                    console.print(f"[red]⚠️ 조건식 인덱스 [{idx}] 범위 초과[/red]")

            console.print()
            console.print(f"[bold green]1차 필터 통과: 총 {len(all_stocks)}개 종목[/bold green]")

            # DEBUG 로그
            with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now()}] 1차 필터(조건검색) 결과: {len(all_stocks)}개 종목\n")
                if all_stocks:
                    f.write(f"  종목: {list(all_stocks)[:10]}\n")  # 최대 10개만 출력
                f.flush()

            if not all_stocks:
                console.print("[yellow]⚠️  조건검색 결과 없음[/yellow]")
                with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now()}] ⚠️ 조건검색 결과 없음 - 필터링 종료\n")
                    f.flush()
                return

            # L2: RS 필터 적용
            console.print()
            console.print("=" * 120, style="cyan")
            console.print("[bold cyan]L2 필터: RS (Relative Strength) 상대강도 분석[/bold cyan]")
            console.print("=" * 120, style="cyan")
            console.print()

            # candidates 리스트 생성 — 코드 기반으로 즉시 구성 (API 호출 없음)
            candidates = [
                {
                    'stock_code': sc,
                    'stock_name': self.validated_stocks.get(sc, {}).get('name', sc),
                    'market': 'KOSDAQ' if sc.startswith(('3', '4', '5', '6', '7')) else 'KOSPI'
                }
                for sc in all_stocks
            ]

            console.print(f"[dim]RS 필터링 대상: {len(candidates)}개 종목[/dim]")

            # RS 필터링
            filtered_candidates = self.signal_orchestrator.check_l2_rs_filter(
                candidates,
                market='KOSPI'
            )

            console.print(f"[green]✓ RS 필터링 완료: {len(filtered_candidates)}개 종목 선택 (상위 RS 종목)[/green]")
            console.print()

            # 필터링된 종목만 처리
            if not filtered_candidates:
                console.print("[yellow]⚠️  RS 필터 통과 종목 없음[/yellow]")
                return

            # all_stocks를 filtered 종목으로 교체
            all_stocks = {c['stock_code'] for c in filtered_candidates}

            # 2차 필터: VWAP 검증
            console.print()
            console.print("=" * 120, style="yellow")
            console.print("[bold yellow]2차 필터: VWAP 백테스트 검증[/bold yellow]")
            console.print("=" * 120, style="yellow")
            console.print()

            # RS 필터링된 종목의 정보를 dict로 변환 (빠른 조회용)
            filtered_dict = {c['stock_code']: c for c in filtered_candidates}

            validated_count = 0
            rejected_count = 0
            for stock_code in all_stocks:
                try:
                    # RS 필터링된 종목 정보 가져오기
                    candidate_info = filtered_dict.get(stock_code, {})
                    stock_name = candidate_info.get('stock_name', stock_code)
                    market = candidate_info.get('market', 'KOSPI')
                    rs_rating = candidate_info.get('rs_rating', 0)

                    # 종목명 재조회 (RS 필터에서 못 가져온 경우)
                    if stock_name == stock_code:
                        try:
                            result = self._get_stock_info_with_cache(stock_code)
                            if result:
                                stock_name = self._extract_stock_name(result, stock_code)
                        except Exception:
                            pass

                    console.print(f"[dim]검증 중: {stock_name} ({stock_code}) - RS {rs_rating:.0f}[/dim]")

                    # 하이브리드 VWAP 검증 (키움 API + Yahoo Finance)
                    validation_result = await validate_stock_for_trading(
                        stock_code=stock_code,
                        stock_name=stock_name,
                        validator=self.validator,
                        api=self.api
                    )

                    if not validation_result.get('allowed'):
                        rejected_count += 1
                        reason = validation_result.get('reason', '알 수 없음')
                        console.print(f"  [red]❌ 거부: {reason}[/red]")
                        continue

                    # 검증 통과
                    validated_count += 1
                    stats = validation_result.get('stats', {})
                    df = validation_result.get('data')

                    # ✅ 조건 인덱스로 전략 태그 동적 결정 (하드코딩 제거)
                    condition_idx = stock_to_condition_map.get(stock_code)
                    strategy_tag = self.condition_to_strategy_map.get(condition_idx, self.default_strategy_tag)

                    # ✅ Bottom 전략 vs Momentum 전략 분기
                    if stock_code in bottom_stocks:
                        # Bottom 전략: Bottom Manager에 신호 등록 (watchlist에는 추가 X)
                        # 현재가, 저가, VWAP 필요 → df에서 추출
                        if df is not None and len(df) > 0:
                            # ✅ FIX: VWAP 계산 (Bottom 신호 등록 전 필수)
                            vwap_config = self.config.get_section('vwap')
                            df = self.analyzer.calculate_vwap(
                                df,
                                use_rolling=vwap_config.get('use_rolling', True),
                                rolling_window=vwap_config.get('rolling_window', 20)
                            )

                            signal_price = df['close'].iloc[-1] if 'close' in df.columns else 0
                            signal_low = df['low'].iloc[-1] if 'low' in df.columns else 0
                            signal_vwap = df['vwap'].iloc[-1] if 'vwap' in df.columns else 0

                            # ✅ FIX: 신호 등록 return value 체크 (중복 방지)
                            signal_registered = self.bottom_manager.register_signal(
                                stock_code=stock_code,
                                stock_name=stock_name,
                                signal_price=signal_price,
                                signal_low=signal_low,
                                signal_vwap=signal_vwap,
                                market=market
                            )

                            # 신호 등록 실패 시 (중복 등) validated_stocks에 추가하지 않음
                            if not signal_registered:
                                rejected_count += 1
                                continue  # 다음 종목으로

                            # validated_stocks에도 저장 (분석 정보 보존)
                            win_rate = stats.get('win_rate', 0)
                            simplified_ai_score = min(100, win_rate * 1.2)

                            self.validated_stocks[stock_code] = {
                                'name': stock_name,
                                'market': market,
                                'rs_rating': rs_rating,
                                'stats': stats,
                                'data': df,
                                'analysis': {'total_score': simplified_ai_score},
                                'strategy': strategy_tag  # ✅ 동적 전략 태그
                            }
                    else:
                        # Momentum 전략: watchlist에 추가 (기존 로직)
                        self.watchlist.add(stock_code)

                        # 🔧 CRITICAL FIX: AI점수 추가 (간소화 버전: win_rate * 1.2)
                        # win_rate 기반으로 간단한 점수 계산 (0~100 범위)
                        win_rate = stats.get('win_rate', 0)
                        simplified_ai_score = min(100, win_rate * 1.2)

                        self.validated_stocks[stock_code] = {
                            'name': stock_name,
                            'market': market,
                            'rs_rating': rs_rating,
                            'stats': stats,
                            'data': df,
                            'analysis': {'total_score': simplified_ai_score},  # AI점수 필드 추가
                            'strategy': strategy_tag  # ✅ 동적 전략 태그
                        }

                    console.print(
                        f"[green]✅ {validated_count}. {stock_name} ({stock_code}) - "
                        f"승률 {stats.get('win_rate', 0):.1f}% | "
                        f"평균수익 {stats.get('avg_profit_pct', 0):.2f}% | "
                        f"거래수 {stats.get('total_trades', 0)}[/green]"
                    )

                except Exception as e:
                    rejected_count += 1
                    console.print(f"  [red]❌ 오류: {str(e)}[/red]")
                    continue

            console.print()
            console.print("=" * 120, style="bold green")
            console.print(f"{'📊 필터링 결과 요약':^120}", style="bold green")
            console.print("=" * 120, style="bold green")
            console.print()
            console.print(f"  1차 필터 (조건검색): {len(all_stocks)}개 종목 발견", style="cyan")
            console.print(f"  2차 필터 (VWAP):     {validated_count}개 종목 검증 통과", style="yellow")
            console.print(f"  최종 감시 종목:      {len(self.watchlist)}개", style="bold green" if len(self.watchlist) > 0 else "bold red")
            console.print()

            # DEBUG 로그
            with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now()}] 📊 필터링 결과 요약\n")
                f.write(f"  1차 필터(조건검색): {len(all_stocks)}개\n")
                f.write(f"  2차 필터(VWAP): {validated_count}개 통과\n")
                f.write(f"  최종 감시 종목: {len(self.watchlist)}개\n")
                if self.watchlist:
                    f.write(f"  Watchlist: {list(self.watchlist)}\n")
                f.flush()

            # 최종 선정 종목 표시
            if self.watchlist:
                from rich.table import Table
                from rich import box

                table = Table(title="최종 선정 종목 (모니터링 대상)", box=box.DOUBLE)
                table.add_column("순위", style="cyan", justify="right")
                table.add_column("종목명", style="yellow")
                table.add_column("코드", style="dim")
                table.add_column("승률", justify="right")
                table.add_column("평균수익률", justify="right", style="green")
                table.add_column("거래수", justify="right")

                # StockGravity 종목은 stats가 없을 수 있으므로 안전하게 처리
                sorted_stocks = sorted(
                    self.validated_stocks.items(),
                    key=lambda x: x[1].get('stats', {}).get('avg_profit_pct', 0) if x[1].get('stats') else 0,
                    reverse=True
                )

                for rank, (code, info) in enumerate(sorted_stocks, 1):
                    stats = info.get('stats', {})
                    # StockGravity 종목은 stats가 없을 수 있음
                    table.add_row(
                        str(rank),
                        info.get('name', code),
                        code,
                        f"{stats.get('win_rate', 0):.1f}%" if stats else "N/A",
                        f"{stats.get('avg_profit_pct', 0):+.2f}%" if stats else "N/A",
                        f"{stats.get('total_trades', 0)}회" if stats else "N/A"
                    )

                console.print(table)
                console.print()

                # 🔧 FIX: validated_stocks를 data/watchlist.json에 저장 (문서 명세)
                self._save_watchlist_to_json()

                # 3차 필터: 종합 분석 (뉴스 + 기술 + 수급 + 기본)
                console.print("=" * 120, style="magenta")
                console.print(f"{'3차 필터: 종합 분석 (뉴스 + 기술 + 수급 + 기본)':^120}", style="bold magenta")
                console.print("=" * 120, style="magenta")
                console.print()

                from analyzers.analysis_engine import AnalysisEngine
                import asyncio as _asyncio
                analysis_engine = AnalysisEngine()

                # YAML에서 종목당 분석 타임아웃 읽기 (기본 10초)
                _analysis_timeout = int(
                    self.config.get('monitoring', {}).get('analysis_timeout_seconds', 10)
                )

                for stock_code, stock_info in list(self.validated_stocks.items()):
                    stock_name = stock_info['name']
                    console.print(f"[cyan]분석 중: {stock_name} ({stock_code})[/cyan]")

                    try:
                        # 차트 데이터 조회 (일봉 30일)
                        chart_data = None
                        try:
                            result = self.api.get_ohlcv_data(stock_code, period='D', count=30)
                            if result and result.get("return_code") == 0:
                                chart_data = result.get("data", [])
                                console.print(f"  [dim]✓ 일봉 {len(chart_data) if chart_data else 0}개 수집[/dim]")
                        except Exception as e:
                            console.print(f"  [dim]⚠️  차트 데이터 조회 실패: {e}[/dim]")

                        # 이벤트 루프에 제어권 반환 (다른 태스크 실행 기회 부여)
                        await _asyncio.sleep(0)

                        # 종목 기본 정보 조회 (캐시 사용)
                        basic_info = None
                        try:
                            result = self._get_stock_info_with_cache(stock_code)
                            if result:
                                basic_info = result
                                console.print(f"  [dim]✓ 종목 정보 수집 (PER: {result.get('per', 'N/A')}, PBR: {result.get('pbr', 'N/A')})[/dim]")
                        except Exception as e:
                            console.print(f"  [dim]⚠️  종목 정보 조회 실패: {e}[/dim]")

                        # 투자자별 매매 동향 조회
                        investor_data = None
                        try:
                            from datetime import datetime as dt
                            today = dt.now().strftime('%Y%m%d')
                            result = self.api.get_investor_trend(stock_code, dt=today)
                            if result and result.get("return_code") == 0:
                                investor_data = result.get("stk_invsr_orgn", [])
                                console.print(f"  [dim]✓ 투자자 동향 {len(investor_data) if investor_data else 0}개 수집[/dim]")
                        except Exception as e:
                            console.print(f"  [dim]⚠️  투자자 동향 조회 실패: {e}[/dim]")

                        # AI 종합 분석 — 별도 스레드 + 타임아웃 (이벤트 루프 블로킹 방지)
                        console.print(f"  [dim]🔍 AI 종합 분석 실행 중... (최대 {_analysis_timeout}s)[/dim]")
                        try:
                            analysis_result = await _asyncio.wait_for(
                                _asyncio.to_thread(
                                    analysis_engine.analyze,
                                    stock_code=stock_code,
                                    stock_name=stock_name,
                                    chart_data=chart_data,
                                    investor_data=investor_data,
                                    program_data=None,
                                    stock_info=basic_info
                                ),
                                timeout=float(_analysis_timeout)
                            )
                        except _asyncio.TimeoutError:
                            logger.warning(
                                f"[ANALYSIS_TIMEOUT] {stock_code} {_analysis_timeout}s 초과 — 건너뜀"
                            )
                            console.print(f"  [yellow]⚠️  분석 타임아웃 ({_analysis_timeout}s) — 건너뜀[/yellow]")
                            await _asyncio.sleep(0)
                            continue

                        # 분석 결과 저장
                        stock_info['analysis'] = analysis_result

                        # 수급 점수 변화율 캐시 저장 (sd_score_cache.json)
                        try:
                            import json as _json
                            _sd_cache_path = BASE_DIR / 'data' / 'sd_score_cache.json'
                            _sd_cache = _json.loads(_sd_cache_path.read_text()) if _sd_cache_path.exists() else {}
                            _new_sd = float(analysis_result.get('scores', {}).get('supply_demand', 50))
                            _today_str = datetime.now().strftime('%Y-%m-%d')
                            _prev = _sd_cache.get(stock_code, {})
                            if _prev.get('date') != _today_str:
                                _sd_cache[stock_code] = {
                                    'date': _today_str,
                                    'score': _new_sd,
                                    'prev_date': _prev.get('date'),
                                    'prev_score': _prev.get('score'),
                                }
                            _sd_cache_path.write_text(_json.dumps(_sd_cache, ensure_ascii=False))
                        except Exception:
                            pass

                        # 분석 결과 출력
                        final_score = analysis_result.get('final_score', 0)
                        recommendation = analysis_result.get('recommendation', '관망')
                        scores = analysis_result.get('scores_breakdown', {})

                        score_color = "bold green" if final_score >= 70 else "green" if final_score >= 60 else "yellow"
                        console.print(f"  [dim]📊 종합점수: [{score_color}]{final_score:.1f}점[/{score_color}] | 추천: {recommendation}[/dim]")
                        console.print(f"  [dim]   뉴스: {scores.get('news', 50):.0f} | "
                                     f"기술: {scores.get('technical', 50):.0f} | "
                                     f"수급: {scores.get('supply_demand', 50):.0f} | "
                                     f"기본: {scores.get('fundamental', 50):.0f}[/dim]")
                        console.print()

                    except Exception as e:
                        console.print(f"  [red]❌ 분석 오류: {e}[/red]")
                        import traceback
                        console.print(f"  [dim]{traceback.format_exc()}[/dim]")
                        continue

                    # 종목 간 이벤트 루프 yield (check_all_stocks 등 다른 태스크 실행 기회)
                    await _asyncio.sleep(0)

                console.print("[green]✅ 종합 분석 완료[/green]")
                console.print()

        except Exception as e:
            console.print(f"[red]❌ 필터링 실행 오류: {e}[/red]")
            import traceback
            traceback.print_exc()

    async def run_condition_filtering_OLD(self):
        """[DEPRECATED] 기존 필터링 로직 - 참고용"""
        all_stocks = set()
        filter_time = datetime.now()

        for idx in self.condition_indices:
            if idx < len(self.condition_list):
                condition = self.condition_list[idx]
                seq = condition[0]
                name = condition[1]

                console.print(f"  🔍 {name} 검색 중...")
                stocks = await self.search_condition(seq, name)
                all_stocks.update(stocks)
                console.print(f"     → {len(stocks)}개 발견")

                # 1차 필터링 결과 DB에 저장
                filter_data = {
                    'filter_time': filter_time.isoformat(),
                    'filter_type': '1차',
                    'condition_name': name,
                    'stocks_found': len(stocks),
                    'stock_codes': list(stocks),
                    'stocks_passed': 0,
                    'stocks_failed': 0,
                    'passed_stocks': [],
                    'schedule_type': 'manual',
                    'is_new_stock': 0
                }
                self.db.insert_filter_history(filter_data)

                await asyncio.sleep(0.5)

        unique_stocks = list(all_stocks)

        console.print()
        console.print(f"📊 중복 제거 후 총 {len(unique_stocks)}개 종목", style="bold green")
        console.print()

        # 2차 필터링
        console.print("=" * 120, style="bold yellow")
        console.print(f"{'2단계: VWAP 사전 검증 (2차 필터링)':^120}", style="bold yellow")
        console.print("=" * 120, style="bold yellow")
        console.print()

        BATCH_SIZE = 5
        DELAY_BETWEEN_REQUESTS = 0.2
        DELAY_BETWEEN_BATCHES = 1.0

        # 종목명 조회
        stock_info_list = []
        console.print(f"[cyan]📋 종목명 조회 중... (총 {len(unique_stocks)}개)[/cyan]")

        for i, code in enumerate(unique_stocks, 1):
            try:
                result = self._get_stock_info_with_cache(code)
                stock_name = self._extract_stock_name(result, code) if result else code

                if stock_name == code:
                    cached_name = self.db.get_recent_stock_name(code)
                    if cached_name:
                        stock_name = cached_name

                if stock_name == code:
                    try:
                        price_result = self.api.get_stock_price(code)
                        stock_name = self._extract_stock_name(price_result, stock_name)
                    except Exception:
                        pass

                if i == 1 and isinstance(result, dict):
                    sample_keys = list(result.keys())[:5]
                    logger.debug(f"[API_KEYS] {code} {sample_keys}")

                stock_info_list.append((code, stock_name))

                if stock_name != code:
                    if code in self.validated_stocks:
                        self.validated_stocks[code]['name'] = stock_name
                    if code in self.positions:
                        self.positions[code]['name'] = stock_name

                if i % 10 == 0:
                    console.print(f"  {i}/{len(unique_stocks)} 완료...", style="dim")

                await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

                if i % BATCH_SIZE == 0:
                    await asyncio.sleep(DELAY_BETWEEN_BATCHES)

            except KeyboardInterrupt:
                console.print()
                console.print("[yellow]⚠️  사용자가 중지했습니다. 지금까지 조회한 종목으로 진행합니다.[/yellow]")
                break
            except Exception as e:
                stock_info_list.append((code, code))
                await asyncio.sleep(DELAY_BETWEEN_REQUESTS)

        console.print(f"[green]✅ 종목명 조회 완료[/green]")
        console.print()

        # VWAP 검증
        console.print(f"[cyan]🔍 VWAP 검증 시작... (키움 API 우선, Yahoo 보충)[/cyan]")
        console.print()

        validated_count = 0
        for i, (code, name) in enumerate(stock_info_list, 1):
            try:
                if i % 5 == 0:
                    console.print(f"  진행: {i}/{len(stock_info_list)}", style="dim")

                result = await validate_stock_for_trading(code, name, self.validator, self.api)

                # DB에 검증 점수 저장
                stats = result.get('stats', {})
                score_data = {
                    'stock_code': code,
                    'stock_name': name,
                    'validation_time': datetime.now().isoformat(),
                    'vwap_win_rate': stats.get('win_rate'),
                    'vwap_avg_profit': stats.get('avg_profit_pct'),
                    'vwap_trade_count': stats.get('total_trades'),
                    'vwap_profit_factor': stats.get('profit_factor'),
                    'vwap_max_profit': stats.get('max_profit_pct'),
                    'vwap_max_loss': stats.get('max_loss_pct'),
                    'news_sentiment_score': None,  # TODO: 뉴스 분석 연동
                    'news_impact_type': None,
                    'news_keywords': [],
                    'news_titles': [],
                    'news_count': 0,
                    'total_score': stats.get('avg_profit_pct', 0),  # 임시: VWAP 점수만
                    'weight_vwap': 1.0,  # 임시: VWAP만 사용
                    'weight_news': 0.0,
                    'is_passed': 1 if result.get('allowed') else 0
                }
                self.db.insert_validation_score(score_data)

                if result.get('allowed'):
                    self.watchlist.add(code)
                    self.validated_stocks[code] = {
                        'name': name,
                        'stats': stats,
                        'data': result.get('data')
                    }
                    validated_count += 1
                    console.print(
                        f"  ✅ {name}: 승률 {stats.get('win_rate', 0):.1f}%, "
                        f"수익 {stats.get('avg_profit_pct', 0):+.1f}%",
                        style="green"
                    )

                if i % BATCH_SIZE == 0:
                    await asyncio.sleep(DELAY_BETWEEN_BATCHES)

            except KeyboardInterrupt:
                console.print()
                console.print("[yellow]⚠️  사용자가 중지했습니다. 지금까지 검증한 종목으로 진행합니다.[/yellow]")
                break
            except Exception as e:
                console.print(f"[red]검증 오류 ({code}): {e}[/red]", style="dim")
                continue

        console.print()
        console.print("=" * 120, style="bold magenta")
        console.print(f"{'📊 필터링 결과 요약':^120}", style="bold magenta")
        console.print("=" * 120, style="bold magenta")
        console.print()
        console.print(f"  1차 필터링 (조건식 검색): {len(unique_stocks)}개 종목 발견", style="cyan")
        console.print(f"  2차 필터링 (VWAP 검증): {validated_count}개 통과, {len(stock_info_list) - validated_count}개 탈락", style="yellow")
        console.print(f"  최종 선정 종목: {validated_count}개", style="bold green" if validated_count > 0 else "bold red")
        console.print()

        # DEBUG: watchlist 내용 확인
        console.print(f"[dim]DEBUG: watchlist 크기 = {len(self.watchlist)}, validated_stocks 크기 = {len(self.validated_stocks)}[/dim]")

        # 최종 선정 종목 표시
        if self.watchlist:
            table = Table(title="최종 선정 종목 (모니터링 대상)", box=box.DOUBLE)
            table.add_column("순위", style="cyan", justify="right")
            table.add_column("종목명", style="yellow")
            table.add_column("코드", style="dim")
            table.add_column("승률", justify="right")
            table.add_column("평균수익률", justify="right", style="green")
            table.add_column("거래수", justify="right")

            # StockGravity 종목은 stats가 없을 수 있으므로 안전하게 처리
            sorted_stocks = sorted(
                self.validated_stocks.items(),
                key=lambda x: x[1].get('stats', {}).get('avg_profit_pct', 0) if x[1].get('stats') else 0,
                reverse=True
            )

            for rank, (code, info) in enumerate(sorted_stocks, 1):
                stats = info.get('stats', {})
                table.add_row(
                    str(rank),
                    info.get('name', code),
                    code,
                    f"{stats.get('win_rate', 0):.1f}%" if stats else "N/A",
                    f"{stats.get('avg_profit_pct', 0):+.2f}%" if stats else "N/A",
                    f"{stats.get('total_trades', 0)}회" if stats else "N/A"
                )

            console.print(table)
            console.print()

    def is_market_open(self) -> bool:
        """장 운영 시간 체크 (평일 09:00 ~ 15:30)"""
        now = datetime.now()

        # 주말 체크
        if now.weekday() >= 5:  # 토요일(5), 일요일(6)
            return False

        # 장 시간 체크 (09:00 ~ 15:30)
        market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)
        market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

        return market_open <= now <= market_close

    async def rescan_and_add_stocks(self):
        """조건검색 재실행 및 리밸런싱 (새 종목 추가 + 오래된 종목 제거)"""
        try:
            # 기존 watchlist 백업
            original_watchlist = self.watchlist.copy()
            original_validated = self.validated_stocks.copy()

            # 조건검색 + VWAP 필터링 실행 (자기 자신의 메서드 사용)
            # 주의: run_condition_filtering은 self.watchlist를 새로 덮어씀
            await self.run_condition_filtering()

            # 리밸런싱: 새 종목 추가 + 오래된 종목 제거
            truly_new_stocks = self.watchlist - original_watchlist
            removed_stocks = original_watchlist - self.watchlist

            # 새로 추가된 종목 표시
            if truly_new_stocks:
                console.print(f"[cyan]  ✅ 새로 발견된 종목: {len(truly_new_stocks)}개[/cyan]")
                for stock_code in truly_new_stocks:
                    stock_info = self.validated_stocks.get(stock_code)
                    if stock_info:
                        stats = stock_info.get('stats', {})
                        win_rate = stats.get('win_rate', 0) if stats else 0
                        console.print(f"[green]     + {stock_info.get('name', stock_code)} ({stock_code}) 추가 (승률 {win_rate:.1f}%)[/green]")
            else:
                console.print("[dim]  새로운 종목 없음[/dim]")

            # 제거된 종목 표시 (조건 미충족으로 탈락)
            if removed_stocks:
                console.print(f"[yellow]  🗑️  모니터링 제외된 종목: {len(removed_stocks)}개[/yellow]")
                for stock_code in removed_stocks:
                    stock_info = original_validated.get(stock_code)
                    stock_name = stock_info['name'] if stock_info else stock_code
                    console.print(f"[dim]     - {stock_name} ({stock_code}) 제거 (조건 미충족)[/dim]")

            # 요약
            console.print(f"[cyan]  📊 리밸런싱 완료: 총 {len(self.watchlist)}개 종목 모니터링 중[/cyan]")

        except Exception as e:
            console.print(f"[yellow]⚠️  재검색 중 오류: {e}[/yellow]")
            import traceback
            traceback.print_exc()

    async def monitor_and_trade(self):
        """실시간 모니터링 및 매매 (5분마다 조건검색 재실행)"""
        console.print("=" * 120, style="bold magenta")
        console.print(f"{'3단계: 실시간 모니터링 시작':^120}", style="bold magenta")
        console.print("=" * 120, style="bold magenta")
        console.print()

        console.print(f"🎯 초기 모니터링 대상: {len(self.watchlist)}개 종목")
        console.print(f"⏰ 시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        console.print(f"🔄 5분마다 조건검색 재실행 → 새 종목 자동 추가")

        if len(self.watchlist) == 0:
            console.print()
            console.print("[yellow]⚠️  감시 종목이 없습니다![/yellow]")
            console.print("[yellow]   - Menu [2]에서 조건검색 필터링을 먼저 실행하세요.[/yellow]")
            console.print("[yellow]   - 또는 장 시작 후 자동으로 조건검색이 실행됩니다.[/yellow]")

        console.print()

        # 초기 종목 테이블 표시 (장 시간 여부 무관)
        await self.check_all_stocks()
        console.print()

        # 장 시간 체크
        if not self.is_market_open():
            console.print("[yellow]⚠️  현재 장 운영 시간이 아닙니다.[/yellow]")
            console.print("[yellow]   평일 09:00 ~ 15:30에만 모니터링이 가능합니다.[/yellow]")
            console.print()
            console.print("[cyan]💡 장 시작 시간까지 대기합니다...[/cyan]")
            console.print("[dim]Ctrl+C를 눌러 종료할 수 있습니다.[/dim]")
            console.print()
        else:
            console.print("[cyan]✅ 장이 열려있습니다. 실시간 모니터링 시작...[/cyan]")
            console.print("[dim]Ctrl+C를 눌러 종료할 수 있습니다.[/dim]")
            console.print()

        check_interval = 60  # 1분마다 종목 체크
        rescan_interval = int(
            self.config.get('monitoring', {}).get('rescan_interval_seconds', 600)
        )  # YAML monitoring.rescan_interval_seconds (기본 10분)
        kis_interval = 300  # 5분마다 한투 중기 체크

        last_check = datetime.now()
        last_rescan = datetime.now()
        _rescan_task = None  # 🔥 2026-04-03: 비동기 리밸런싱 Task 핸들
        last_sync = datetime.now()  # 거래 내역 동기화 마지막 시간
        last_status_update = datetime.now()
        last_kis_check = datetime.now() - timedelta(seconds=kis_interval)  # 즉시 첫 체크
        eod_executed = False  # ✅ EOD 프로세스 실행 여부 플래그
        overnight_close_executed = False  # 🔧 2026-02-15: 오버나이트 강제 청산 실행 여부
        failsafe_close_executed = False   # 🔥 2026-04-03: 15:25 무조건 전량 청산 실행 여부

        # ✅ 초기 한투 포지션 조회 및 체크
        self.run_kis_pre_market_check()
        self.fetch_kis_positions()
        self.display_kis_positions()

        # 🔧 2026-03-25: 당일 포지션 복원 (재시작 시 exit_logic 유지)
        self._restore_positions_state()

        # 🔥 2026-04-03: STARTUP FAILSAFE - 재시작이 14:50 이후면 overnight_close 즉시 실행
        # (루프 블로킹으로 14:50 체크를 놓쳤다가 재시작된 경우 대비)
        _startup_now = datetime.now()
        _is_after_overnight_window = (
            (_startup_now.hour == 14 and _startup_now.minute >= 50) or
            (_startup_now.hour == 15 and _startup_now.minute < 25)
        )
        if self.positions and self.is_market_open() and _is_after_overnight_window:
            logger.warning(
                f"[OVERNIGHT_FAILSAFE] 재시작 감지 ({_startup_now.strftime('%H:%M')}) "
                f"포지션 {len(self.positions)}개 → overnight_close 즉시 실행"
            )
            console.print(
                f"[bold red][OVERNIGHT_FAILSAFE] {_startup_now.strftime('%H:%M')} 재시작 감지 "
                f"— 포지션 {len(self.positions)}개 즉시 overnight_close 실행[/bold red]"
            )
            await self.force_close_overnight()
            overnight_close_executed = True

        import time as _time_mod
        _loop_ts = _time_mod.monotonic()  # 🔥 2026-04-03: 루프 블로킹 감지용

        try:
            while self.running:
                current_time = datetime.now()

                # 🔥 2026-04-03: 루프 블로킹 감지 Watchdog
                _now_ts = _time_mod.monotonic()
                _loop_delay = _now_ts - _loop_ts
                if _loop_delay > 90.0:
                    logger.warning(
                        f"[LOOP_BLOCKED] 루프 지연 감지 delay={_loop_delay:.1f}s "
                        f"time={current_time.strftime('%H:%M:%S')} "
                        f"positions={len(self.positions)}"
                    )
                elif _loop_delay > 45.0:
                    logger.info(
                        f"[LOOP_SLOW] 루프 지연 감지 delay={_loop_delay:.1f}s "
                        f"time={current_time.strftime('%H:%M:%S')}"
                    )
                _loop_ts = _now_ts

                # 🔧 2026-02-23: Watchdog 하트비트 갱신
                self._write_heartbeat("monitoring")

                # 🔧 CRITICAL FIX: 15:30 이후 자동 종료 (장마감 후 불필요한 활동 방지)
                shutdown_time = current_time.replace(hour=15, minute=30, second=0, microsecond=0)
                if current_time >= shutdown_time:
                    console.print()
                    console.print("[yellow]=" * 80 + "[/yellow]")
                    console.print(f"[bold yellow]🕐 15:30 장 종료 - 오늘 모니터링 종료[/bold yellow]")
                    console.print("[yellow]=" * 80 + "[/yellow]")

                    # ── KPI 일일 요약 (4대 지표) ─────────────────────────────────────
                    try:
                        _kpi = getattr(self, '_kpi', {})
                        _tr  = _kpi.get('trades', [])
                        if _tr:
                            _n        = len(_tr)
                            _wins     = sum(1 for t in _tr if t['pnl'] > 0)
                            _tp1      = sum(1 for t in _tr if t['tp1'])
                            _avg_hold = sum(t['hold_m'] for t in _tr) / _n
                            _avg_mfe  = sum(t['mfe'] for t in _tr) / _n
                            _avg_pnl  = sum(t['pnl'] for t in _tr) / _n
                            _mfe_capture = (
                                sum(t['pnl'] / t['mfe'] for t in _tr if t['mfe'] > 0.1)
                                / max(1, sum(1 for t in _tr if t['mfe'] > 0.1)) * 100
                            )
                            # entry 분류: entry_ok(수익) / held_wrong(손실+EF) / no_demand(SD차단 포함)
                            _entry_ok    = sum(1 for t in _tr if t['pnl'] > 0)
                            _held_wrong  = sum(1 for t in _tr
                                               if t['pnl'] < 0 and 'Early Failure' not in t['exit_reason'])
                            _no_demand   = sum(1 for t in _tr
                                               if 'Early Failure' in t['exit_reason']
                                               or 'no_demand' in t['exit_reason'])
                            _ev_exits    = _kpi.get('ev_exits', 0)
                            _blocked_rg  = _kpi.get('blocked_regimes', 0)

                            console.print()
                            console.print("[bold cyan]━" * 60 + "[/bold cyan]")
                            console.print(
                                f"[bold cyan]📊 KPI 일일 요약  {datetime.now().strftime('%Y-%m-%d')}[/bold cyan]"
                            )
                            console.print(f"  거래 수       {_n}건  |  승률 {_wins/_n*100:.0f}%  |  평균 PnL {_avg_pnl:+.2f}%")
                            console.print(
                                f"  TP1 발생률    {_tp1}/{_n}  ({_tp1/_n*100:.0f}%)  "
                                f"← {'✅ 정상' if _tp1 > 0 else '❌ 0건 — 진입 아직 늦음'}"
                            )
                            console.print(
                                f"  평균 보유     {_avg_hold:.0f}분  "
                                f"← {'✅' if _avg_hold >= 30 else '⚠ 목표 30분+'}"
                            )
                            console.print(
                                f"  MFE 평균      {_avg_mfe:.2f}%  |  수익 포착률 {_mfe_capture:.0f}%  "
                                f"← {'✅' if _mfe_capture >= 40 else '⚠ 운영 개선 필요'}"
                            )
                            console.print(
                                f"  entry_ok {_entry_ok}건  |  held_wrong {_held_wrong}건  |  no_demand {_no_demand}건"
                            )
                            console.print(
                                f"  3봉조기이탈   {_ev_exits}건  |  레짐차단 {_blocked_rg}건"
                            )
                            console.print("[bold cyan]━" * 60 + "[/bold cyan]")

                            # 로그 파일 저장
                            logger.info(
                                f"[KPI_SUMMARY] trades={_n} | winrate={_wins/_n*100:.0f}% | "
                                f"tp1={_tp1/_n*100:.0f}% | hold={_avg_hold:.0f}m | "
                                f"mfe_avg={_avg_mfe:.2f}% | mfe_capture={_mfe_capture:.0f}% | "
                                f"entry_ok={_entry_ok} | held_wrong={_held_wrong} | no_demand={_no_demand} | "
                                f"ev_exits={_ev_exits} | regime_blocks={_blocked_rg}"
                            )
                        else:
                            console.print("[dim]KPI: 오늘 완료된 거래 없음[/dim]")
                    except Exception as _ke:
                        logger.debug(f"[KPI_ERR] {_ke}")

                    # 🔧 2026-02-07: Re-entry Cooldown 리포트 출력 + 저장
                    self.reentry_metrics.print_report()
                    self.reentry_metrics.save_daily()

                    # 🔧 2026-03-18: SMC 결정 로그 일일 요약
                    try:
                        from analyzers.smc.smc_decision_logger import get_smc_logger as _gsmc
                        _gsmc().print_daily_summary()
                    except Exception:
                        pass

                    # ✅ 한투 장 마감 후 체크리스트
                    self.run_kis_post_market_check()

                    # 🔧 2026-04-06: 당일 거래 → PostgreSQL 자동 적재
                    try:
                        import sys as _sys
                        _hermes = "/home/greatbps/projects/hermes-agent/skills/kiwoom-trading"
                        if _hermes not in _sys.path:
                            _sys.path.insert(0, _hermes)
                        from pg_loader import load_to_pg as _pg_load
                        _inserted = _pg_load(rebuild=True)
                        logger.info("[PG_LOADER] 당일 거래 DB 적재 완료: %d건", _inserted)
                        console.print(f"[dim]✓ DB 적재 완료: {_inserted}건 → log_trade_events[/dim]")
                    except Exception as _e:
                        logger.warning("[PG_LOADER] DB 적재 실패 (트레이딩 영향 없음): %s", _e)

                    # 🔧 2026-04-06: 일별 자본 스냅샷 → daily_capital_snapshot
                    self._save_daily_capital_snapshot()

                    console.print()
                    console.print(f"[cyan]✅ 오늘 거래 완료 ({current_time.strftime('%Y-%m-%d %H:%M:%S')})[/cyan]")
                    console.print(f"[dim cyan]💤 내일 08:50에 자동으로 다시 시작됩니다.[/dim cyan]")
                    console.print()
                    break  # 모니터링 루프만 종료 (run() 루프는 계속)

                # 장 시간인지 체크
                if self.is_market_open():
                    # 🔥 2026-04-03: HARD FAILSAFE - 15:25 전 포지션 무조건 강제 청산 (등급 불문)
                    if not failsafe_close_executed and current_time.hour == 15 and current_time.minute >= 25:
                        if self.positions:
                            logger.critical(
                                f"[OVERNIGHT_FAILSAFE_1525] 15:25 도달 — 잔여 포지션 "
                                f"{len(self.positions)}개 무조건 전량 강제청산"
                            )
                            console.print()
                            console.print("[bold red]" + "=" * 60 + "[/bold red]")
                            console.print(
                                f"[bold red][OVERNIGHT_FAILSAFE] 15:25 — 전 포지션 무조건 강제청산[/bold red]"
                            )
                            console.print("[bold red]" + "=" * 60 + "[/bold red]")
                            for _fc_code in list(self.positions.keys()):
                                # 중복 청산 방지: overnight_close가 이미 처리했으면 skip
                                if _fc_code not in self.positions:
                                    continue
                                _fc_pos = self.positions[_fc_code]
                                _fc_cur = _fc_pos.get('current_price', _fc_pos['entry_price'])
                                _fc_pnl = (_fc_cur - _fc_pos['entry_price']) / _fc_pos['entry_price'] * 100
                                _fc_reason = f"15:25 무조건 강제청산 [OVERNIGHT_FAILSAFE]"
                                logger.critical(
                                    f"[OVERNIGHT_FAILSAFE_1525] symbol={_fc_pos.get('stock_name', _fc_code)} "
                                    f"pnl={_fc_pnl:+.2f}%"
                                )
                                self.execute_sell(_fc_code, _fc_cur, _fc_pnl, _fc_reason, use_market_order=True)
                                # 🔧 2026-04-15: Kill Switch — 15:25 failsafe 청산 실패 감지
                                if _fc_code in self.positions:
                                    _fc_pos2 = self.positions[_fc_code]
                                    _ks_cfg3 = self.config.get('risk_control.kill_switch', {})
                                    _soft_pct3 = _ks_cfg3.get('soft_close_pct', 0.8)
                                    _ks_thresh3 = _ks_cfg3.get('threshold', 3)
                                    _init3 = _fc_pos2.get('initial_quantity', _fc_pos2.get('quantity', 1))
                                    _rem3 = _fc_pos2.get('quantity', 0)
                                    _cpct3 = (_init3 - _rem3) / _init3 if _init3 > 0 else 0
                                    if _cpct3 < _soft_pct3:
                                        _fc_pos2['_close_failures'] = _fc_pos2.get('_close_failures', 0) + 1
                                        logger.critical(
                                            f"[CLOSE_FAIL] {_fc_pos2.get('stock_name', _fc_code)} "
                                            f"15:25 failsafe 청산 실패 #{_fc_pos2['_close_failures']}회 ({_cpct3:.0%})"
                                        )
                                        if _fc_pos2['_close_failures'] >= _ks_thresh3:
                                            self._kill_switch_active = True
                                            self._kill_switch_triggered_at = datetime.now()
                                            self._kill_switch_reason = (
                                                f"{_fc_pos2.get('stock_name', _fc_code)}({_fc_code}) "
                                                f"청산 {_fc_pos2['_close_failures']}회 실패"
                                            )
                                            logger.critical(f"[KILL_SWITCH_ON] {self._kill_switch_reason} — 신규 진입 전면 차단")
                        failsafe_close_executed = True

                    # 🔧 2026-04-15: EOD 토큰 선제 갱신 (14:45 — 강제청산 5분 전)
                    # "토큰 만료 후 대응"이 아니라 "만료 전 선제 제거" 전략
                    if (not getattr(self, '_eod_token_refreshed', False) and
                            current_time.hour == 14 and current_time.minute >= 45):
                        import time as _time_mod
                        _tok_exp = getattr(self.api, 'token_expires_at', None)
                        _tok_ttl = (_tok_exp - _time_mod.time()) if _tok_exp else 0
                        if _tok_exp is None or _tok_ttl < 600:  # 10분 미만 또는 만료 불명
                            logger.info(f"[EOD_TOKEN_PRECHECK] 토큰 TTL={_tok_ttl:.0f}s — 선제 갱신 시도")
                            console.print(f"[yellow]🔄 [EOD_TOKEN_PRECHECK] 토큰 만료 임박({_tok_ttl:.0f}s) — 선제 갱신[/yellow]")
                            if not self.refresh_access_token():
                                # 🔧 2026-04-15: 토큰 갱신 실패 → Kill Switch 발동 (거래 불능 상태)
                                logger.critical("[EOD_TOKEN_FAIL] 토큰 갱신 실패 — Kill Switch 발동")
                                console.print("[bold red]🛑 [EOD_TOKEN_FAIL] 토큰 갱신 실패 → 신규 진입 차단[/bold red]")
                                self._kill_switch_active = True
                                self._kill_switch_triggered_at = datetime.now()
                                self._kill_switch_reason = "EOD 토큰 갱신 실패 (거래 불능)"
                        else:
                            logger.debug(f"[EOD_TOKEN_PRECHECK] 토큰 TTL={_tok_ttl:.0f}s — 갱신 불필요")
                        self._eod_token_refreshed = True

                    # 🔧 2026-02-15: 오버나이트 강제 청산 (14:50, CHoCH A급 제외)
                    if not overnight_close_executed and current_time.hour == 14 and current_time.minute >= 50:
                        await self.force_close_overnight()
                        overnight_close_executed = True

                    # ✅ EOD 프로세스 체크 (14:55-14:59 사이에 1회 실행)
                    if not eod_executed and current_time.hour == 14 and 55 <= current_time.minute <= 59:
                        await self.handle_eod()
                        eod_executed = True

                    # 🔥 2026-04-03: 15:29 최종 포지션 상태 로그 (디버깅용)
                    if current_time.hour == 15 and current_time.minute == 29 and current_time.second < 10:
                        if self.positions:
                            logger.warning(
                                f"[FINAL_POSITIONS_1529] 15:29 잔여 포지션 {len(self.positions)}개: "
                                + ", ".join(
                                    f"{p.get('stock_name', c)}({c}) "
                                    f"grade={p.get('choch_grade','?')} "
                                    f"overnight={p.get('allow_overnight', False)}"
                                    for c, p in self.positions.items()
                                )
                            )

                    # 🔧 2026-04-15: Kill Switch SOFT 자동 복구
                    # 조건: 포지션 전량 청산 + 토큰 유효 → Kill Switch 해제
                    if getattr(self, '_kill_switch_active', False):
                        _ks_cfg = self.config.get('risk_control.kill_switch', {})
                        if _ks_cfg.get('mode', 'SOFT') == 'SOFT' and not self.positions:
                            if self.validate_token():
                                # 🔧 2026-04-15: 쿨다운 검증 — 발동 후 N분 경과해야 해제
                                _ks_cooldown = _ks_cfg.get('recovery_cooldown_min', 10)
                                _ks_triggered_at = getattr(self, '_kill_switch_triggered_at', None)
                                _ks_elapsed = (
                                    (datetime.now() - _ks_triggered_at).total_seconds() / 60
                                    if _ks_triggered_at else _ks_cooldown + 1
                                )
                                if _ks_elapsed >= _ks_cooldown:
                                    self._kill_switch_active = False
                                    _ks_reason_was = getattr(self, '_kill_switch_reason', '')
                                    self._kill_switch_reason = ''
                                    logger.info(
                                        f"[KILL_SWITCH_OFF] 자동 해제 — 포지션 청산 완료 + 토큰 정상 "
                                        f"+ 쿨다운 {_ks_elapsed:.1f}min 경과 (이유: {_ks_reason_was})"
                                    )
                                    console.print(f"[green]✅ [KILL_SWITCH_OFF] 자동 해제 — 신규 진입 재개[/green]")
                                else:
                                    logger.debug(
                                        f"[KILL_SWITCH_COOLDOWN] 해제 대기 중 "
                                        f"({_ks_elapsed:.1f}/{_ks_cooldown}min)"
                                    )

                    # 🔥 2026-04-03: 비동기 리밸런싱 (메인 루프 블로킹 방지)
                    # create_task로 백그라운드 실행 — 직전 태스크 완료 여부 확인 후 새로 실행
                    if (current_time - last_rescan).seconds >= rescan_interval:
                        if _rescan_task is None or _rescan_task.done():
                            console.print()
                            console.print("[cyan]🔄 5분 경과 - 조건검색 재실행 (비동기)[/cyan]")
                            import asyncio as _asyncio
                            _rescan_task = _asyncio.create_task(self.rescan_and_add_stocks())
                            last_rescan = current_time
                        else:
                            logger.warning(
                                f"[RESCAN_SKIP] 이전 리밸런싱 아직 실행 중 — 이번 주기 건너뜀 "
                                f"({current_time.strftime('%H:%M:%S')})"
                            )

                    # 5분마다 거래 내역 동기화 (누락 체결 자동 복구)
                    if (current_time - last_sync).seconds >= 300:
                        today = current_time.strftime('%Y%m%d')
                        sync_result = await self.reconciliation.reconcile_trades(today)

                        if sync_result.get('synced'):
                            missing_count = sync_result.get('missing_trades', 0)
                            if missing_count > 0:
                                console.print(f"[yellow]⚠️  {missing_count}건 누락 거래 자동 동기화됨[/yellow]")
                                # 알림 생성
                                self.reconciliation.create_alert(
                                    missing_count=missing_count,
                                    trades=sync_result.get('synced_trades', [])
                                )

                        if sync_result.get('errors'):
                            for error in sync_result['errors']:
                                console.print(f"[red]❌ 동기화 오류: {error}[/red]")

                        last_sync = current_time

                    # 1분마다 종목 체크
                    elif (current_time - last_check).seconds >= check_interval:
                        await self.check_all_stocks()

                        # ✅ 5분마다 한투 중기 포지션 조회, 평가, STOP_LOSS 실행
                        if (current_time - last_kis_check).seconds >= kis_interval:
                            self.fetch_kis_positions()
                            self.display_kis_positions()
                            self.execute_kis_stop_loss()  # STOP_LOSS 자동 실행
                            last_kis_check = current_time

                        last_check = current_time
                    else:
                        # 남은 시간 카운트다운 (같은 줄에서 갱신)
                        elapsed = (current_time - last_check).seconds
                        remaining = check_interval - elapsed

                        # 다음 재검색까지 남은 시간도 표시
                        rescan_elapsed = (current_time - last_rescan).seconds
                        rescan_remaining = rescan_interval - rescan_elapsed
                        rescan_min = rescan_remaining // 60
                        rescan_sec = rescan_remaining % 60

                        import sys
                        sys.stdout.write(f"\r다음 체크: {remaining}초 후 | 다음 재검색: {rescan_min}분 {rescan_sec}초 후 | Ctrl+C: 종료   ")
                        sys.stdout.flush()
                else:
                    # 장 시간이 아니면 상태 업데이트 (5초마다만 갱신하여 덜 intrusive하게)
                    if (current_time - last_status_update).seconds >= 5:
                        import sys
                        sys.stdout.write(f"\r💤 장중 아님 | 대기 중... ({current_time.strftime('%H:%M:%S')})   ")
                        sys.stdout.flush()
                        last_status_update = current_time

                # asyncio.sleep 사용 (KeyboardInterrupt 감지 가능)
                await asyncio.sleep(1)

        except KeyboardInterrupt:
            print()  # 줄바꿈으로 ^C 다음 줄로 이동
            self.shutdown()
            return  # 즉시 종료

    async def check_all_stocks(self):
        """모든 종목 체크 및 실시간 테이블 갱신 (매수 조건 + 보유 종목 포함)"""
        from rich.table import Table
        from datetime import datetime
        import logging

        # 모니터링 종목 파일 저장 (대시보드 연동)
        self._save_monitoring_watchlist()

        # 🔥 2026-04-03: EMA9 블록 결과 추적 (15분 경과 종목 자동 평가)
        if self._ema9_blocks:
            _expired_codes = []
            for _ec, _ed in list(self._ema9_blocks.items()):
                _elapsed_s = (datetime.now() - _ed['blocked_time']).total_seconds()
                if _elapsed_s >= 900:  # 15분 경과
                    # 현재가는 다음 check_entry_signal 때 알 수 있으므로
                    # 여기서는 elapsed 플래그만 세팅 (가격은 check_entry_signal에서 비교)
                    _ed['result_due'] = True
            # result_due 처리는 check_entry_signal 내에서 수행

        # 주기 시작: 신호 큐 초기화 (이전 주기 잔여 신호 제거)
        self._pending_signals.clear()

        # 주기 카운터 + 포지션 변경 감지 (테이블 throttle용)
        self._cycle_count += 1
        _current_positions_keys = set(self.positions.keys())
        _positions_changed = (_current_positions_keys != self._last_positions_keys)
        if _positions_changed:
            self._last_positions_keys = _current_positions_keys.copy()
        _show_tables = _positions_changed or (self._cycle_count % self._table_refresh_cycles == 0)

        # Cycle 통계 (SUMMARY 라인용)
        _cycle_scanned = 0
        _cycle_signals = 0
        _cycle_filtered = 0

        # 에러 로그를 파일에 저장
        error_logger = _logging.getLogger('error_logger')
        if not error_logger.handlers:
            fh = _logging.FileHandler('/home/greatbps/projects/kiwoom_trading/logs/auto_trading_errors.log')
            fh.setLevel(_logging.ERROR)
            formatter = _logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            fh.setFormatter(formatter)
            error_logger.addHandler(fh)
            error_logger.setLevel(_logging.ERROR)

        current_time = datetime.now().strftime('%H:%M:%S')

        # 종목별 실시간 데이터 수집
        stock_data = []

        # 모니터링 대상: watchlist + 보유 종목 (중복 제거)
        all_stocks = set(self.watchlist) | set(self.positions.keys())

        for stock_code in all_stocks:
            try:
                # watchlist 종목은 validated_stocks에서, 보유 종목은 positions에서 정보 가져오기
                if stock_code in self.validated_stocks:
                    stock_info = self.validated_stocks[stock_code]
                    stock_name = stock_info['name']

                    # 종목명이 코드와 같으면 (조회 실패) 다시 조회
                    if stock_name == stock_code:
                        try:
                            result = self._get_stock_info_with_cache(stock_code)
                            if result:
                                stock_name = self._extract_stock_name(result, stock_code)
                                # validated_stocks 업데이트
                                stock_info['name'] = stock_name
                        except Exception:
                            pass  # 실패해도 코드로 표시

                elif stock_code in self.positions:
                    # 보유 종목인 경우
                    stock_info = None
                    stock_name = self.positions[stock_code].get('name', stock_code)

                    # 종목명이 코드와 같으면 (조회 실패) 다시 조회
                    if stock_name == stock_code:
                        try:
                            result = self._get_stock_info_with_cache(stock_code)
                            if result:
                                stock_name = self._extract_stock_name(result, stock_code)
                                # positions 업데이트
                                self.positions[stock_code]['name'] = stock_name
                        except Exception:
                            pass  # 실패해도 코드로 표시
                else:
                    console.print(f"[dim]⚠️  {stock_code}: 정보 없음[/dim]")
                    continue

                # ─── LOOP_LAG 완화: 보유 종목은 매 사이클, 워치리스트는 N사이클마다 ───
                _is_held = stock_code in self.positions
                _entry_scan_due = (self._cycle_count % self._entry_scan_cycles == 0)
                if not _is_held and not _entry_scan_due:
                    # 이번 사이클은 진입 스캔 제외 → OHLCV 조회/지표 계산 전체 스킵
                    logger.debug(f"[SCAN_SKIP] {stock_code} cycle={self._cycle_count} (비보유, 스캔 주기 아님)")
                    continue

                # 1차: 키움 API에서 5분봉 데이터 조회 (최근 900개)
                current_hour = datetime.now().hour
                current_minute = datetime.now().minute

                df = None
                kiwoom_bars = 0
                realtime_price = None  # 실시간 현재가

                # 모든 종목의 실시간 현재가 우선 조회 (장중에만)
                if 9 <= current_hour < 16:
                    try:
                        # 장마감 시간(15:30) 체크
                        is_market_open = not (current_hour == 15 and current_minute >= 30)

                        if is_market_open:
                            price_result = self.api.get_stock_price(stock_code)
                            if price_result and price_result.get('return_code') == 0:
                                output = price_result.get('output') or price_result.get('output1')
                                if output:
                                    # 현재가 추출 (여러 키 시도)
                                    for key in ['stck_prpr', 'cur_prc', 'price', 'current_price']:
                                        if key in output:
                                            realtime_price = float(output[key])
                                            pass  # 실시간 가격 수신 (노이즈 제거)
                                            break
                    except Exception as e:
                        # API 실패는 정상 동작 (5분봉 데이터 사용)
                        pass

                # NO_TRADE_DAY + 포지션 없는 종목은 5분봉 스킵 (루프 속도 개선)
                # DEFENSIVE 모드 활성화 시엔 스킵하지 않음 (RSI/EMA/VWAP 계산 필요)
                _mkt_status = getattr(self, '_market_context_status', 'TRADE_OK')
                _def_active = self.config.get('defensive_mode', {}).get('enabled', False)
                _skip_ohlcv = (
                    _mkt_status == 'NO_TRADE_DAY'
                    and stock_code not in self.positions
                    and not _def_active
                )
                if _skip_ohlcv:
                    stock_data.append({'code': stock_code, 'name': stock_name, 'df': None,
                                       'realtime_price': realtime_price})
                    continue

                # 장중(9:00~15:30)에만 5분봉 키움 API 호출
                if 9 <= current_hour < 16:
                    try:
                        import asyncio as _asyncio_chart
                        result = await _asyncio_chart.to_thread(
                            self.api.get_minute_chart,
                            stock_code=stock_code,
                            tic_scope="5",
                            upd_stkpc_tp="1"
                        )

                        if result.get('return_code') == 0:
                            # 응답 데이터 키 탐색
                            data = None
                            for key in ['stk_min_pole_chart_qry', 'stk_mnut_pole_chart_qry', 'output', 'output1', 'output2', 'data']:
                                if key in result and result[key]:
                                    data = result[key]
                                    break

                            if data and len(data) > 0:
                                import pandas as pd
                                df = pd.DataFrame(data)

                                logger.debug(f"[API_COLS] {stock_code} cols={list(df.columns)}")

                                # 컬럼 매핑 (ka10080 API 기준)
                                column_mapping = {
                                    'cur_prc': 'close',      # 현재가
                                    'open_pric': 'open',     # 시가
                                    'high_pric': 'high',     # 고가
                                    'low_pric': 'low',       # 저가
                                    'trde_qty': 'volume',    # 거래량
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
                                # 예: cur_prc="-78800" → 실제 가격은 78,800원
                                for col in ['open', 'high', 'low', 'close', 'volume']:
                                    if col in df.columns:
                                        # 문자열 → 숫자 변환 후 절대값 적용
                                        df[col] = pd.to_numeric(df[col], errors='coerce').abs()

                                # volume 컬럼이 없으면 기본값 추가
                                if 'volume' not in df.columns:
                                    logger.debug(f"[VOL_COL] {stock_code}: volume 컬럼 없음")
                                    df['volume'] = 1000  # 기본 거래량

                                # 시간 정렬: cntr_tm으로 정렬 (최신 데이터가 마지막에 오도록)
                                if 'cntr_tm' in df.columns:
                                    df['cntr_tm'] = pd.to_numeric(df['cntr_tm'], errors='coerce')
                                    df = df.sort_values('cntr_tm', ascending=True).reset_index(drop=True)
                                    logger.debug(f"[DATA] {stock_code} kiwoom {len(df)}봉")
                                else:
                                    logger.debug(f"[DATA] {stock_code} kiwoom {len(df)}봉")

                                kiwoom_bars = len(df)
                    except Exception as e:
                        logger.debug(f"[API_ERR] {stock_code}: {e}")

                # 2차: 데이터 부족 시 Yahoo Finance로 보충
                if df is None or len(df) < 20:
                    # 시장 정보 확인 (Yahoo Finance Ticker용)
                    market = None
                    if stock_code in self.validated_stocks:
                        market = self.validated_stocks[stock_code].get('market', None)

                    if not market:
                        market = 'KOSPI' if stock_code.startswith('0') else 'KOSDAQ'

                    ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
                    ticker = f"{stock_code}{ticker_suffix}"

                    # 부족한 만큼 Yahoo에서 가져오기
                    needed = 20 - (len(df) if df is not None else 0)
                    days_needed = max(1, (needed // 70) + 1)  # 5분봉: 하루 약 70개

                    yahoo_df = await asyncio.to_thread(download_stock_data_sync, ticker, days=days_needed)

                    if yahoo_df is not None and len(yahoo_df) > 0:
                        if df is not None:
                            # 키움 + 야후 결합
                            df = pd.concat([yahoo_df, df], ignore_index=True).drop_duplicates()
                            logger.debug(f"[DATA] {stock_code} kiwoom+yahoo {len(df)}봉")
                        else:
                            df = yahoo_df
                            logger.debug(f"[DATA] {stock_code} yahoo {len(df)}봉")

                if df is None or len(df) < 20:
                    # 데이터 조회 실패 시 fallback: DB 정보로 기본 표시
                    logger.debug(f"[RT_NONE] {stock_code}")

                    # DB 정보로 기본 데이터 생성
                    stock_data.append({
                        'code': stock_code,
                        'name': stock_name,
                        'holding': "🔵 보유" if stock_code in self.positions else "",
                        'price': 0,  # 실시간 가격 없음
                        'vwap': 0,
                        'vwap_ok': False,
                        'ma20': 0,
                        'ma20_ok': False,
                        'volume_change_pct': 0,
                        'volume_ok': False,
                        'z_score': 0,
                        'short_term_surge': False,
                        'statistical_spike': False,
                        'trade_value_surge': False,
                        'volume_signals_met': 0,
                        'signal': "❓ 데이터 없음",
                        'signal_color': "dim",
                        'conditions_met': 0,
                        'squeeze_display': '[dim]-[/dim]',  # 스퀴즈 모멘텀 상태
                        'orderbook_display': '[green]✓0/6[/green]',  # 호가창 상태 - 데이터 없어도 기본 표시
                        'time': current_time
                    })
                    continue

                # VWAP 설정 가져오기
                vwap_config = self.config.get_section('vwap')
                use_rolling = vwap_config.get('use_rolling', True)
                rolling_window = vwap_config.get('rolling_window', 20)

                # VWAP, MA20, ATR 계산
                df = self.analyzer.calculate_vwap(df, use_rolling=use_rolling, rolling_window=rolling_window)
                df['ma20'] = df['close'].rolling(window=20).mean()
                df['volume_ma5'] = df['volume'].rolling(window=5).mean()
                df['volume_ma20'] = df['volume'].rolling(window=20).mean()
                df['trade_value'] = df['close'] * df['volume']  # 거래대금

                # 보유 종목의 경우 실시간 가격 우선 사용
                if realtime_price is not None:
                    current_price = realtime_price
                else:
                    current_price = df['close'].iloc[-1]

                # 가격 검증: 0 또는 음수면 에러 로그
                if current_price <= 0:
                    error_msg = f"{stock_code}: 비정상 현재가 {current_price} (realtime={realtime_price}, df_close={df['close'].iloc[-1]})"
                    error_logger.error(error_msg)
                    console.print(f"[yellow]⚠️  {error_msg}[/yellow]")
                    continue

                current_vwap = df['vwap'].iloc[-1]
                current_ma20 = df['ma20'].iloc[-1]
                current_volume = df['volume'].iloc[-1]

                # ========================================
                # E안: 다단계 가중 기반 거래량 급등 탐지
                # ========================================

                # 1. 단기 폭발 탐지 (직전 25분 대비)
                recent_avg = df['volume'].iloc[-6:-1].mean() if len(df) >= 6 else df['volume'].mean()
                short_term_surge = (current_volume / recent_avg) > 1.8 if recent_avg > 0 else False

                # 2. 통계적 이상치 탐지 (Z-score)
                volume_mean = df['volume'].iloc[-20:].mean() if len(df) >= 20 else df['volume'].mean()
                volume_std = df['volume'].iloc[-20:].std() if len(df) >= 20 else 1
                z_score = (current_volume - volume_mean) / volume_std if volume_std > 0 else 0
                statistical_spike = z_score > 1.8

                # 3. 거래대금 강화 필터
                trade_value_ma20 = df['trade_value'].rolling(window=20).mean().iloc[-1] if len(df) >= 20 else df['trade_value'].mean()
                current_trade_value = df['trade_value'].iloc[-1]
                trade_value_surge = (current_trade_value / trade_value_ma20) > 1.5 if trade_value_ma20 > 0 else False

                # 4. 시간대 보정
                current_hour = datetime.now().hour
                if 9 <= current_hour < 10:
                    volume_threshold = 2.0  # 장초반 기준 강화
                elif 14 <= current_hour < 15:
                    volume_threshold = 1.3  # 장마감 기준 완화
                else:
                    volume_threshold = 1.5

                # 5. 최종 거래량 조건 (2개 이상 충족)
                volume_signals = [short_term_surge, statistical_spike, trade_value_surge]
                volume_signals_met = sum(volume_signals)
                condition_volume = volume_signals_met >= 2

                # 기존 volume_change_pct도 유지 (표시용)
                volume_ma20 = df['volume_ma20'].iloc[-1] if len(df) >= 20 else df['volume'].mean()
                if volume_ma20 > 0:
                    volume_change_pct = ((current_volume - volume_ma20) / volume_ma20 * 100)
                    # 거래량 비율이 -95% 미만이면 (거의 거래 없음) 0으로 표시
                    if volume_change_pct < -95:
                        volume_change_pct = 0.0
                else:
                    volume_change_pct = 0.0

                # 매수 조건 체크
                condition_vwap = current_price > current_vwap  # VWAP 위
                condition_ma20 = current_price > current_ma20  # MA20 위 (상승추세)

                # 🔧 시그널 판단 (간단한 기술적 조건만 체크 - 실제 매수 아님!)
                conditions_met = sum([condition_vwap, condition_ma20, condition_volume])

                if conditions_met == 3:
                    signal = "📊 기술조건"  # 기술적 조건만 만족 (실제 매수 아님!)
                    signal_color = "green"
                elif conditions_met >= 2:
                    signal = "⏳ 대기중"
                    signal_color = "yellow"
                else:
                    signal = "❌ 제외"
                    signal_color = "red"

                # 보유 여부 표시
                holding_status = "🔵 보유" if stock_code in self.positions else ""

                # SignalOrchestrator로 실제 필터 상태 확인 (보유 종목 제외)
                orchestrator_status = ""
                rejection_info = ""
                if stock_code not in self.positions and conditions_met >= 2:
                    # 기술적 조건을 만족하면 SignalOrchestrator 체크
                    try:
                        # market 정보 가져오기
                        stock_info = self.validated_stocks.get(stock_code)
                        market_info = stock_info.get('market', 'KOSPI') if stock_info else 'KOSPI'

                        signal_result = self.signal_orchestrator.evaluate_signal(
                            stock_code=stock_code,
                            stock_name=stock_name,
                            current_price=current_price,
                            df=df,
                            market=market_info,
                            current_cash=self.current_cash,
                            daily_pnl=self.calculate_daily_pnl()
                        )

                        if signal_result['allowed']:
                            orchestrator_status = "✅통과"
                            rejection_info = f"Score:{signal_result['aggregate_score']:+.1f}"
                        else:
                            level = signal_result['rejection_level']
                            reason = signal_result['rejection_reason']
                            orchestrator_status = f"{level}❌"
                            rejection_info = reason[:30]  # 30자로 제한
                    except Exception as e:
                        orchestrator_status = "오류"
                        rejection_info = str(e)[:30]

                # 스퀴즈 모멘텀 계산 (색상 표시)
                squeeze_display = "[dim]-[/dim]"
                squeeze_config = self.config.get('squeeze_momentum', {})
                if squeeze_config.get('enabled', False) and df is not None and len(df) >= 50:
                    try:
                        from utils.squeeze_momentum_realtime import calculate_squeeze_momentum, get_current_squeeze_signal

                        df_copy = df.copy()
                        df_copy = calculate_squeeze_momentum(df_copy)
                        signal = get_current_squeeze_signal(df_copy)

                        # 색상별 표시
                        color_map = {
                            'bright_green': ('🟢', 'BG', 'bold green'),
                            'dark_green': ('🟡', 'DG', 'yellow'),
                            'dark_red': ('🔴', 'DR', 'red'),
                            'bright_red': ('🟠', 'BR', 'bold red'),
                            'gray': ('⚪', '--', 'dim')
                        }

                        emoji, abbr, color = color_map.get(signal['color'], ('⚪', '--', 'dim'))
                        squeeze_display = f"[{color}]{emoji}{abbr}[/{color}]"
                    except Exception:
                        squeeze_display = "[dim]-[/dim]"

                # 호가창 상태 계산
                orderbook_display = "[dim]-[/dim]"  # 기본값
                entry_mode = squeeze_config.get('entry_mode', 'squeeze_only')

                if entry_mode == "squeeze_with_orderbook" and df is not None and len(df) >= 20:
                    try:
                        # 호가창 데이터 조회
                        orderbook_data = self.api.get_stock_quote(stock_code)

                        # 디버그: API 응답 확인
                        if orderbook_data is None:
                            logger.debug(f"[OB_NONE] {stock_code}")
                        elif orderbook_data.get('return_code') != 0:
                            logger.debug(f"[OB_CODE] {stock_code}: {orderbook_data.get('return_code')}")

                        if orderbook_data is not None and orderbook_data.get('return_code') == 0:
                            output = orderbook_data.get('output', {})

                            # 필요한 데이터 추출
                            sell_1st_qty = safe_float(output.get('sell_hoga_rem_qty_1', 0))
                            tot_buy_qty = safe_float(output.get('tot_buy_hoga_rem_qty', 0))
                            tot_sell_qty = safe_float(output.get('tot_sell_hoga_rem_qty', 0))

                            # 체결강도
                            execution_strength = (tot_buy_qty / tot_sell_qty * 100) if tot_sell_qty > 0 else 100.0

                            # VWAP
                            vwap = df['vwap'].iloc[-1] if 'vwap' in df.columns else current_price

                            # 거래량
                            recent_5min_volume = df['volume'].tail(5).sum() if len(df) >= 5 else 0
                            prev_5min_volume = df['volume'].iloc[-10:-5].sum() if len(df) >= 10 else recent_5min_volume * 0.8
                            recent_high_5min = df['high'].tail(5).max() if len(df) >= 5 else current_price

                            # 호가창 필터 체크 (간단 버전)
                            from analyzers.order_book_filter import OrderBookFilter
                            ob_filter = OrderBookFilter()

                            # Phase 1 진입 조건 체크
                            passed, reason, results = ob_filter.check_entry_conditions_phase1(
                                stock_code=stock_code,
                                current_price=current_price,
                                vwap=vwap,
                                squeeze_current=False,
                                squeeze_prev=True,
                                squeeze_off_count=1,
                                recent_5min_volume=recent_5min_volume,
                                prev_5min_volume=prev_5min_volume,
                                sell_1st_qty=sell_1st_qty,
                                sell_1st_avg_1min=sell_1st_qty,
                                execution_strength=execution_strength,
                                stock_avg_strength=100.0,
                                price_stable_sec=0.0,
                                recent_high_5min=recent_high_5min,
                                debug=False  # 테이블에서는 디버그 로그 생략
                            )

                            # 통과한 조건 개수 계산
                            passed_count = sum([1 for r in results.values() if r.get('pass', False)])
                            total_count = len(results)

                            if passed:
                                orderbook_display = f"[green]✓{passed_count}/{total_count}[/green]"
                            else:
                                orderbook_display = f"[red]✗{passed_count}/{total_count}[/red]"
                        else:
                            # API 조회 실패
                            orderbook_display = "[dim]-[/dim]"
                    except Exception as e:
                        # 에러 발생 시 디버그 로그
                        logger.debug(f"[OB_CALC_ERR] {stock_code}: {e}")
                        orderbook_display = "[dim]-[/dim]"

                stock_data.append({
                    'code': stock_code,
                    'name': stock_name,
                    'holding': holding_status,
                    'price': current_price,
                    'vwap': current_vwap,
                    'vwap_ok': condition_vwap,
                    'ma20': current_ma20,
                    'ma20_ok': condition_ma20,
                    'volume_change_pct': volume_change_pct,
                    'volume_ok': condition_volume,
                    # 거래량 상세 분석 (E안)
                    'z_score': z_score,
                    'short_term_surge': short_term_surge,
                    'statistical_spike': statistical_spike,
                    'trade_value_surge': trade_value_surge,
                    'volume_signals_met': volume_signals_met,
                    'signal': signal,
                    'signal_color': signal_color,
                    'conditions_met': conditions_met,
                    'squeeze_display': squeeze_display,  # 스퀴즈 모멘텀 상태
                    'orderbook_display': orderbook_display,  # 호가창 상태
                    'orchestrator_status': orchestrator_status,  # L0-L6 상태
                    'rejection_info': rejection_info,  # 차단 이유
                    'time': current_time,
                    'historical_df': df  # 백테스트용 히스토리 데이터 추가
                })

                # 매수/매도 신호 체크 (기존 로직)
                _cycle_scanned += 1
                if stock_code in self.positions:
                    self.check_exit_signal(stock_code, df)  # historical_df 전달
                    # 청산되지 않은 경우 → 등급 승격 + 피라미딩 체크
                    if stock_code in self.positions:
                        _pos = self.positions[stock_code]
                        self._maybe_upgrade_grade(stock_code, _pos, current_price, df=df)
                        self._maybe_pyramid_add(
                            stock_code=stock_code,
                            position=_pos,
                            current_price=current_price,
                            df=df,
                        )
                else:
                    # 디버그: check_entry_signal 호출 전 로그
                    if orchestrator_status == "✅통과":
                        logger.debug(f"[ORCH_PASS] {stock_code} {stock_name}")
                    await self.check_entry_signal(stock_code, df)  # 키움 데이터 전달 (async)
                    # Cycle 신호/필터 카운트
                    _smc_state = self._smc_display_cache.get(stock_code, {}).get('smc_state', '')
                    if _smc_state == 'SIGNAL':
                        _cycle_signals += 1
                    else:
                        _cycle_filtered += 1

            except Exception as e:
                import traceback
                error_msg = f"❌ {stock_code}: {e}\n{traceback.format_exc()}"

                # 파일에 로그 저장
                error_logger.error(error_msg)

                # 화면에는 간단히만 표시
                console.print(f"[red]❌ {stock_code}: {e} (상세 로그: logs/auto_trading_errors.log)[/red]")
                continue

        # 조건 충족 개수 순 → 매수 시그널 우선
        stock_data.sort(key=lambda x: x['conditions_met'], reverse=True)

        # ── Signal Flush: detect → execute ────────────────────────────────
        self._flush_pending_signals(stock_data)

        # 보유 종목의 AI 점수와 승률을 캐싱 (시뮬레이션 테이블에서 재사용)
        position_scores = {}  # {stock_code: {'ai_score': 0, 'win_rate': 0}}

        # 화면 클리어 (기존 테이블 지우고 업데이트)
        # 🔧 DISABLED: 사용자 요청으로 clear 비활성화 (에러 로그 확인 위해)
        # os.system('clear' if os.name == 'posix' else 'cls')
        console.print()

        # 종목 수 확인
        if len(stock_data) == 0:
            console.print("[yellow]⚠️  모니터링 중인 종목이 없습니다.[/yellow]")
            console.print(f"[dim]watchlist: {len(self.watchlist)}개[/dim]")
            console.print(f"[dim]validated_stocks: {len(self.validated_stocks)}개[/dim]")
            return

        # ========================================
        # 1. 시뮬레이션 통계 요약 테이블
        # ========================================
        sim_table = Table(title=f"📈 시뮬레이션 통계 요약 ({current_time})", box=box.ROUNDED, show_header=True, header_style="bold cyan")
        sim_table.add_column("순번", style="cyan", justify="right", width=4)
        sim_table.add_column("코드", style="yellow", width=8)
        sim_table.add_column("종목명", style="white", width=12)
        sim_table.add_column("AI점수", justify="right", width=7)
        sim_table.add_column("스퀴즈", justify="center", width=8)  # ✅ 스퀴즈 모멘텀 컬럼 추가
        sim_table.add_column("총거래", justify="right", width=7)
        sim_table.add_column("승률", justify="right", width=7)
        sim_table.add_column("평균수익", justify="right", width=9)
        sim_table.add_column("최대수익", justify="right", width=9)
        sim_table.add_column("최대손실", justify="right", width=9)

        for i, data in enumerate(stock_data, 1):
            stock_code = data['code']
            stock_info = self.validated_stocks.get(stock_code)

            # 보유 종목이지만 validated_stocks에 없는 경우 캐시에서 가져오기
            ai_score = 0  # 기본값
            if not stock_info:
                if stock_code in self.positions:
                    # 보유 포지션 테이블에서 계산한 값 사용
                    cached = position_scores.get(stock_code, {})
                    ai_score = cached.get('ai_score', 0)
                    cached_win_rate = cached.get('win_rate', 0)

                    # ✅ FIX: historical_df가 있으면 실제 백테스트 stats 사용
                    historical_df = None
                    for d in stock_data:
                        if d['code'] == stock_code and 'historical_df' in d:
                            historical_df = d['historical_df']
                            break

                    if historical_df is not None and len(historical_df) >= 100:
                        # 실시간 백테스트로 정확한 stats 계산
                        from analyzers.pre_trade_validator import PreTradeValidator
                        validator = PreTradeValidator(self.config)
                        trades = validator._run_quick_simulation(historical_df)
                        stats = validator._calculate_stats(trades)
                    else:
                        # 백테스트 불가능하면 기본값
                        stats = {
                            'total_trades': 0,
                            'winning_trades': 0,
                            'losing_trades': 0,
                            'win_rate': cached_win_rate,  # 캐시된 승률 사용
                            'avg_profit_pct': 0,
                            'max_profit_pct': 0,
                            'max_loss_pct': 0
                        }
                else:
                    continue
            else:
                # 실시간 백테스트로 최신 stats 계산
                historical_df = None
                for d in stock_data:
                    if d['code'] == stock_code and 'historical_df' in d:
                        historical_df = d['historical_df']
                        break

                if historical_df is not None and len(historical_df) >= 100:
                    # 실시간 데이터로 재계산
                    from analyzers.pre_trade_validator import PreTradeValidator
                    validator = PreTradeValidator(self.config)
                    trades = validator._run_quick_simulation(historical_df)
                    stats = validator._calculate_stats(trades)
                else:
                    # 저장된 stats 사용 (StockGravity 종목은 stats가 없을 수 있음)
                    stats = stock_info.get('stats', {})

                analysis = stock_info.get('analysis', {})
                # 🔧 CRITICAL FIX: 필드명 수정 (total_score → final_score 또는 total_score)
                ai_score = analysis.get('total_score') or analysis.get('final_score', 0) if analysis else 0

            total_trades = stats.get('total_trades', 0)
            win_rate = stats.get('win_rate', 0)
            avg_profit = stats.get('avg_profit_pct', 0)
            max_profit = stats.get('max_profit_pct', 0)
            max_loss = stats.get('max_loss_pct', 0)

            # AI 점수 안전 처리 (None 체크)
            ai_score = ai_score if ai_score is not None else 0

            # AI 점수 색상
            ai_color = "bold green" if ai_score >= 70 else "green" if ai_score >= 60 else "yellow" if ai_score >= 50 else "red"

            # 승률 색상
            wr_color = "green" if win_rate >= 60 else "yellow" if win_rate >= 40 else "red"

            # 평균수익 색상
            avg_color = "green" if avg_profit >= 2 else "yellow" if avg_profit >= 1 else "red"

            # ✅ 스퀴즈 모멘텀 상태 계산
            squeeze_display = "-"
            squeeze_config = self.config.get('squeeze_momentum', {})
            if squeeze_config.get('enabled', False) and historical_df is not None and len(historical_df) >= 50:
                try:
                    from utils.squeeze_momentum_realtime import calculate_squeeze_momentum, get_current_squeeze_signal

                    # 컬럼명 확인 및 변환
                    df_copy = historical_df.copy()
                    if isinstance(df_copy.columns, pd.MultiIndex):
                        df_copy.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df_copy.columns]
                    else:
                        df_copy.columns = df_copy.columns.str.lower()

                    # 스퀴즈 계산
                    df_copy = calculate_squeeze_momentum(df_copy)
                    signal = get_current_squeeze_signal(df_copy)

                    # 색상별 표시
                    color_map = {
                        'bright_green': ('🟢', 'BG', 'bold green'),
                        'dark_green': ('🟡', 'DG', 'yellow'),
                        'dark_red': ('🔴', 'DR', 'red'),
                        'bright_red': ('🟠', 'BR', 'bold red'),
                        'gray': ('⚪', '--', 'dim')
                    }

                    emoji, abbr, color = color_map.get(signal['color'], ('⚪', '--', 'dim'))
                    squeeze_display = f"[{color}]{emoji}{abbr}[/{color}]"

                except Exception:
                    squeeze_display = "[dim]ERR[/dim]"

            sim_table.add_row(
                str(i),
                data['code'],
                data['name'],
                f"[{ai_color}]{ai_score:.0f}[/{ai_color}]" if ai_score > 0 else "-",
                squeeze_display,  # ✅ 스퀴즈 모멘텀 상태
                str(total_trades),
                f"[{wr_color}]{win_rate:.1f}%[/{wr_color}]",
                f"[{avg_color}]{avg_profit:+.2f}%[/{avg_color}]",
                f"[green]{max_profit:+.2f}%[/green]",
                f"[red]{max_loss:+.2f}%[/red]"
            )

        if _show_tables:
            console.print(sim_table)
            console.print()
            # ✅ 스퀴즈 모멘텀 범례 (설정 활성화 시)
            squeeze_config = self.config.get('squeeze_momentum', {})
            if squeeze_config.get('enabled', False):
                console.print("[dim]스퀴즈: [bold green]✓T1/T2/T3[/bold green]=진입 가능(Tier) | [yellow]⏳[/yellow]=타이밍 지남 | --=스퀴즈 없음 | ✗=차단[/dim]")
                console.print()

        # ========================================
        # 2. 보유 포지션 상세 테이블
        # ========================================

        if len(self.positions) > 0 and _show_tables:
            holdings_table = Table(
                title=f"📊 보유 포지션 상세 ({current_time})",
                box=box.ROUNDED,
                show_header=True,
                header_style="bold cyan"
            )
            holdings_table.add_column("No", style="cyan", justify="right", width=4)
            holdings_table.add_column("코드", style="yellow", width=8)
            holdings_table.add_column("종목명", style="white", width=12)
            holdings_table.add_column("AI점수", justify="right", width=7)
            holdings_table.add_column("승률", justify="right", width=7)
            holdings_table.add_column("매수가", justify="right", width=9)
            holdings_table.add_column("수량", justify="right", width=6)
            holdings_table.add_column("현재가", justify="right", width=9)
            holdings_table.add_column("수익률", justify="right", width=9)
            holdings_table.add_column("손절가", justify="right", width=9)
            holdings_table.add_column("보유일", justify="right", width=7)

            for idx, (stock_code, position) in enumerate(self.positions.items(), 1):
                # 두 가지 키 형식 모두 지원 (name/stock_name, price/avg_price/entry_price)
                stock_name = position.get('stock_name') or position.get('name', stock_code)
                entry_price = position.get('avg_price') or position.get('entry_price') or position.get('price', 0)
                quantity = position.get('quantity', 0)

                # 전략 정보 (validated_stocks에서 가져오거나 새로 계산)
                db_candidate = None  # DB 후보 종목 (보유일 계산용)
                stock_info = self.validated_stocks.get(stock_code)

                if stock_info:
                    # StockGravity 종목은 stats가 없을 수 있음
                    stats = stock_info.get('stats', {})
                    win_rate = stats.get('win_rate', 0) if stats else 0
                    analysis = stock_info.get('analysis', {})
                    ai_score = analysis.get('total_score', 0)
                else:
                    # validated_stocks에 없으면 DB에서 조회
                    from database.trading_db import TradingDatabase
                    db = TradingDatabase()
                    candidates = db.get_active_candidates()

                    for c in candidates:
                        if c.get('stock_code') == stock_code:
                            db_candidate = c
                            break

                    if db_candidate:
                        # DB에서 가져오기
                        win_rate = db_candidate.get('vwap_win_rate', 0)
                        ai_score = db_candidate.get('total_score', 0)
                    else:
                        # DB에도 없으면 실시간 백테스트 실행
                        console.print(f"[yellow]  ⚠️  {stock_code}: 전략 정보 없음, 실시간 백테스트 실행[/yellow]")

                        # stock_data에서 해당 종목의 historical data 가져오기
                        historical_df = None
                        for data in stock_data:
                            if data['code'] == stock_code and 'historical_df' in data:
                                historical_df = data['historical_df']
                                break

                        if historical_df is not None and len(historical_df) >= 100:
                            # VWAP 백테스트 실행
                            from analyzers.pre_trade_validator import PreTradeValidator
                            validator = PreTradeValidator(self.config)
                            trades = validator._run_quick_simulation(historical_df)
                            stats = validator._calculate_stats(trades)

                            win_rate = stats.get('win_rate', 0)
                            ai_score = min(100, win_rate * 1.2)  # 간이 AI점수 (승률 * 1.2)

                            console.print(f"[dim]  ✓ {stock_code}: 백테스트 완료 - 승률 {win_rate:.1f}%[/dim]")
                        else:
                            win_rate = 0
                            ai_score = 0

                # AI 점수와 승률을 캐시에 저장
                position_scores[stock_code] = {
                    'ai_score': ai_score,
                    'win_rate': win_rate
                }

                # 현재가 조회 (우선순위: position > stock_data)
                current_price = position.get('current_price') or entry_price  # 기본값
                for data in stock_data:
                    if data['code'] == stock_code:
                        current_price = data['price']
                        break

                # 수익률 계산
                profit_loss_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0

                # 손절가 계산 (5% 손절 기준)
                stop_loss_price = entry_price * 0.95

                # 보유일 계산 (우선순위: position.entry_date > DB.date_detected)
                entry_date = position.get('entry_date')

                if entry_date:
                    if isinstance(entry_date, str):
                        try:
                            entry_date = datetime.fromisoformat(entry_date)
                        except:
                            entry_date = None

                # entry_date가 없으면 DB에서 date_detected 조회
                if not entry_date and db_candidate:
                    detected_date = db_candidate.get('date_detected')
                    if detected_date:
                        try:
                            if isinstance(detected_date, str):
                                entry_date = datetime.fromisoformat(detected_date)
                            else:
                                entry_date = detected_date
                        except:
                            pass

                # 보유일 계산
                if entry_date:
                    hold_days = (datetime.now() - entry_date).days
                else:
                    hold_days = 0

                # 수익률 색상
                if profit_loss_pct >= 2:
                    profit_color = "bold green"
                elif profit_loss_pct >= 0:
                    profit_color = "green"
                elif profit_loss_pct >= -2:
                    profit_color = "yellow"
                else:
                    profit_color = "red"

                # 승률 색상
                wr_color = "green" if win_rate >= 60 else "yellow" if win_rate >= 40 else "red"

                # AI 점수 색상
                ai_color = "bold green" if ai_score >= 70 else "green" if ai_score >= 60 else "yellow" if ai_score >= 50 else "red"

                holdings_table.add_row(
                    str(idx),
                    stock_code,
                    stock_name[:10],  # 최대 10자
                    f"[{ai_color}]{ai_score:.0f}[/{ai_color}]" if ai_score > 0 else "-",
                    f"[{wr_color}]{win_rate:.0f}%[/{wr_color}]" if win_rate > 0 else "-",
                    f"{entry_price:,.0f}",
                    str(quantity),
                    f"{current_price:,.0f}",
                    f"[{profit_color}]{profit_loss_pct:+.2f}%[/{profit_color}]",
                    f"{stop_loss_price:,.0f}",
                    f"{hold_days}일"
                )

            console.print(holdings_table)
            console.print()

        # ========================================
        # 3. 오늘 거래 내역 테이블
        # ========================================
        try:
            risk_log_path = Path("data/risk_log.json")
            if risk_log_path.exists():
                with open(risk_log_path, 'r', encoding='utf-8') as f:
                    risk_data = json.load(f)

                daily_trades = risk_data.get('daily_trades', [])

                # 오늘 날짜로 필터링 (지난 거래는 DB에만 저장, 화면 출력 X)
                today = datetime.now().date()
                today_trades = []
                for trade in daily_trades:
                    timestamp = trade.get('timestamp', '')
                    if timestamp:
                        try:
                            # ISO format 파싱: "2026-01-05T10:01:02"
                            trade_date = datetime.fromisoformat(timestamp).date()
                            if trade_date == today:
                                today_trades.append(trade)
                        except (ValueError, AttributeError):
                            pass  # 날짜 파싱 실패한 거래는 무시

                if today_trades and _show_tables:
                    trade_history_table = Table(
                        title=f"📝 오늘 거래 내역 ({len(today_trades)}건)",
                        box=box.ROUNDED,
                        show_header=True,
                        header_style="bold yellow"
                    )
                    trade_history_table.add_column("번호", style="cyan", justify="right", width=4)
                    trade_history_table.add_column("날짜", style="white", justify="center", width=14)
                    trade_history_table.add_column("종목명", style="yellow", width=12)
                    trade_history_table.add_column("종목코드", style="dim", width=8)
                    trade_history_table.add_column("매매", justify="center", width=6)
                    trade_history_table.add_column("수량", justify="right", width=6)
                    trade_history_table.add_column("평단가", justify="right", width=10)
                    trade_history_table.add_column("손익", justify="right", width=12)

                    for idx, trade in enumerate(today_trades, 1):
                        # 타임스탬프 파싱
                        timestamp = trade.get('timestamp', '')
                        if 'T' in timestamp:
                            date_part, time_part = timestamp.split('T')
                            # "2026-01-02T10:01:02" -> "01-02 10:01"
                            formatted_date = f"{date_part[5:]} {time_part[:5]}"
                        else:
                            formatted_date = timestamp[:14]

                        stock_name = trade.get('stock_name', '')
                        stock_code = trade.get('stock_code', '')
                        trade_type = trade.get('type', '')
                        quantity = trade.get('quantity', 0)
                        price = trade.get('price', 0)
                        realized_pnl = trade.get('realized_pnl', 0.0)

                        # 매매 타입 색상
                        if trade_type == 'BUY':
                            trade_type_str = "[red]매수[/red]"
                        else:
                            trade_type_str = "[blue]매도[/blue]"

                        # 손익 표시 (매도일 때만)
                        if trade_type == 'SELL' and realized_pnl != 0:
                            if realized_pnl > 0:
                                pnl_str = f"[green]+₩{realized_pnl:,.0f}[/green]"
                            else:
                                pnl_str = f"[red]₩{realized_pnl:,.0f}[/red]"
                        else:
                            pnl_str = "-"

                        trade_history_table.add_row(
                            str(idx),
                            formatted_date,
                            stock_name,
                            stock_code,
                            trade_type_str,
                            str(quantity),
                            f"₩{price:,.0f}",
                            pnl_str
                        )

                    console.print(trade_history_table)
                    console.print()
        except Exception as e:
            console.print(f"[dim yellow]⚠️  거래 내역 로드 오류: {e}[/dim yellow]")

        # ========================================
        # 4. 🔧 2026-03-18: SMC 결정형 모니터링 테이블 (핵심 7컬럼만)
        # ========================================
        holding_count = sum(1 for data in stock_data if data.get('holding'))
        entry_mode_cfg = self.config.get('entry_mode', 'smc')
        is_smc_mode = (entry_mode_cfg == 'smc')

        # Market Context 상태
        mc_icon = ''
        try:
            mc_status, _, _ = self.market_context.evaluate()
            mc_icon = "🌐✅" if mc_status == "TRADE_OK" else "🌐🚫"
        except Exception:
            pass

        # SWEEP_RESULT 집계 (당일 로그에서)
        sweep_summary = ''
        try:
            import re as _re
            _sweep_log = f"logs/sweep_attempt_{datetime.now().strftime('%Y%m%d')}.log"
            if hasattr(self, '_sweep_counts_cache'):
                sweep_summary = self._sweep_counts_cache
            if __import__('os').path.exists(_sweep_log):
                with open(_sweep_log) as _f:
                    _lines = _f.readlines()
                _p = sum(1 for l in _lines if 'SWEEP_RESULT' in l and 'type=penetration' in l)
                _e = sum(1 for l in _lines if 'SWEEP_RESULT' in l and 'type=equal_level' in l)
                _flt = sum(1 for l in _lines if 'SWEEP_RESULT' in l and 'type=filtered' in l)
                sweep_summary = f"Sweep {_p+_e} (P:{_p}/E:{_e}/F:{_flt})"
                self._sweep_counts_cache = sweep_summary
        except Exception:
            pass

        # Entry candidates 수 (smc_pending + 현재 SIGNAL 상태)
        entry_candidates = len(self.smc_pending)

        table_title = (
            f"🧠 SIGNAL MONITOR  {current_time}"
            + (f"  {mc_icon}" if mc_icon else '')
            + (f"  [{sweep_summary}]" if sweep_summary else '')
            + (f"  Entry Candidates: {entry_candidates}" if entry_candidates else '')
            + (f"  보유: {holding_count}" if holding_count else '')
        )

        table = Table(title=table_title, box=box.SIMPLE_HEAD, show_header=True, header_style="bold cyan")
        table.add_column("코드", style="yellow", width=8)
        table.add_column("종목명", width=14)
        table.add_column("현재가", justify="right", width=10)
        table.add_column("dist%", justify="right", width=7)
        table.add_column("유형", justify="center", width=5)
        table.add_column("거래량%", justify="right", width=8)
        table.add_column("상태", justify="center", width=14)

        _table_rows = 0
        _inactive_count = 0

        for data in stock_data:
            code = data['code']

            # SMC 캐시에서 sweep 정보 읽기
            _smc = self._smc_display_cache.get(code, {})
            sw_type = _smc.get('sweep_type', '-')
            sw_dist = _smc.get('sweep_dist', 0)

            is_holding = data.get('holding', False)
            is_pending = code in self.smc_pending
            has_sweep = sw_type in ('P', 'E')
            has_signal = _smc.get('smc_state') == 'SIGNAL'

            # ── 출력 조건: 의미 있는 종목만 ────────────────────────────
            # 보유 중, OB 대기, Sweep 감지, 신호 발생 중 하나라도 해당
            if not (is_holding or is_pending or has_sweep or has_signal):
                _inactive_count += 1
                continue

            _table_rows += 1
            price_str = f"{data['price']:,.0f}" if data['price'] else '-'
            dist_str = f"{sw_dist:.2f}%" if sw_dist else '[dim]-[/dim]'
            type_str = f"[green]P[/green]" if sw_type == 'P' else (f"[cyan]E[/cyan]" if sw_type == 'E' else '[dim]-[/dim]')

            # 거래량 %
            vol_pct = data.get('volume_change_pct', 0)
            if vol_pct >= 150:
                vol_str = f"[bold green]+{vol_pct:.0f}%[/bold green]"
            elif vol_pct >= 50:
                vol_str = f"[green]+{vol_pct:.0f}%[/green]"
            elif vol_pct >= 0:
                vol_str = f"[dim]+{vol_pct:.0f}%[/dim]"
            else:
                vol_str = f"[dim]{vol_pct:.0f}%[/dim]"

            # 상태 (계층형: 보유 > 신호 > OB대기 > WATCH)
            name_str = data['name']
            if is_holding:
                status_str = "[bold green]🟢 보유[/bold green]"
                name_str = f"[bold green]{name_str}[/bold green]"
            elif has_signal:
                grade = _smc.get('choch_grade', '?')
                status_str = f"[bold magenta]🔴 ENTRY({grade})[/bold magenta]"
                name_str = f"[bold]{name_str}[/bold]"
            elif is_pending:
                grade = self.smc_pending[code].get('grade', '?')
                status_str = f"[cyan]🟡 OB대기({grade})[/cyan]"
            else:
                # has_sweep = True (유일하게 남은 케이스)
                status_str = "[yellow]🟡 WATCH[/yellow]"

            table.add_row(code, name_str, price_str, dist_str, type_str, vol_str, status_str)

        # 활성 종목이 있을 때만 테이블 출력
        if _table_rows > 0:
            console.print(table)
            console.print()

        # ========================================
        # ✅ Bottom Pullback 신호 모니터링
        # ========================================
        signal_watchlist = self.bottom_manager.get_signal_watchlist()
        if signal_watchlist:
            console.print()
            console.print("=" * 120, style="bold cyan")
            console.print(f"{'🎯 Bottom Pullback 신호 대기 중':^120}", style="bold cyan")
            console.print("=" * 120, style="bold cyan")
            console.print()

            for stock_code, signal_info in signal_watchlist.items():
                stock_name = signal_info['stock_name']
                state = signal_info['state']

                try:
                    # 키움 API로 실시간 데이터 조회
                    result = self._get_stock_info_with_cache(stock_code)
                    if not result:
                        continue

                    current_price = result.get('price', 0)
                    current_low = result.get('day_low', 0)

                    # ✅ FIX: 가격 데이터 유효성 검증 (0이면 current_price로 fallback)
                    if current_low <= 0:
                        current_low = current_price

                    # 여전히 0이면 스킵 (유효하지 않은 데이터)
                    if current_price <= 0 or current_low <= 0:
                        console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): 유효하지 않은 가격 데이터 (price={current_price}, low={current_low})[/yellow]")
                        continue

                    # DataFrame 조회 (VWAP 계산용)
                    stock_info = self.validated_stocks.get(stock_code)
                    if not stock_info:
                        continue

                    df = stock_info.get('data')
                    if df is None or len(df) < 10:
                        continue

                    # 컬럼명 소문자 변환
                    if isinstance(df.columns, pd.MultiIndex):
                        df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
                    else:
                        df.columns = df.columns.str.lower()

                    # VWAP 재계산
                    vwap_config = self.config.get_section('vwap')
                    df = self.analyzer.calculate_vwap(df,
                                                       use_rolling=vwap_config.get('use_rolling', True),
                                                       rolling_window=vwap_config.get('rolling_window', 20))

                    current_vwap = df['vwap'].iloc[-1] if 'vwap' in df.columns else 0
                    current_volume = df['volume'].iloc[-1] if 'volume' in df.columns else 0

                    # 직전 5봉 평균 거래량
                    avg_volume_5 = df['volume'].iloc[-6:-1].mean() if len(df) >= 6 else df['volume'].mean()

                    # Pullback 조건 체크
                    ready, reason = self.bottom_manager.check_pullback(
                        stock_code=stock_code,
                        current_price=current_price,
                        current_vwap=current_vwap,
                        current_low=current_low,
                        recent_volume=current_volume,
                        avg_volume_5=avg_volume_5,
                        df=df
                    )

                    if ready:
                        # ✅ Pullback 조건 충족 → 매수 진입
                        console.print()
                        console.print("=" * 120, style="bold green")
                        console.print(
                            f"{'🚀 Bottom Pullback 매수 신호 발생!':^120}",
                            style="bold green"
                        )
                        console.print("=" * 120, style="bold green")
                        console.print()

                        # check_entry_signal 호출 (L0-L6 필터 체크)
                        await self.check_entry_signal(stock_code, kiwoom_df=df)

                        # 진입 표시
                        self.bottom_manager.mark_entered(stock_code)

                    else:
                        # 상태 표시
                        console.print(
                            f"  [cyan]{stock_name} ({stock_code}): {state} - {reason}[/cyan]"
                        )

                except Exception as e:
                    console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): Bottom 체크 오류 - {e}[/yellow]")
                    continue

            console.print()

        # ── MKT_CTX 주기 로그 + 상태 변화 감지 ────────────────────────────────
        try:
            _mc_status, _mc_reason, _ = self.market_context.evaluate()
            self._market_context_status = _mc_status  # 루프 속도 최적화용 캐시
            # 상태가 바뀐 순간 → 즉시 로그
            if _mc_status != self._prev_mkt_ctx_status:
                logger.info(
                    f"[MKT_CTX_CHANGE] {self._prev_mkt_ctx_status or 'INIT'} → {_mc_status}"
                    f" | {_mc_reason}"
                )
                self._prev_mkt_ctx_status = _mc_status
            # 10분마다 현황 로그 (변화 없어도)
            if self._cycle_count % self._table_refresh_cycles == 0:
                logger.info(
                    f"[MKT_CTX] 상태={_mc_status} | {_mc_reason}"
                    f" | 사이클={self._cycle_count} | 보유={len(self.positions)}"
                )
        except Exception:
            pass

        # ── Cycle Summary (매 주기 1~2줄) ────────────────────────────────────
        _holding_now = len(self.positions)
        if _cycle_signals > 0:
            # 이벤트 있음 → 굵게
            console.print(
                f"[bold]━ #{self._cycle_count} {current_time}[/bold]"
                f"[dim] scanned={_cycle_scanned} active={_table_rows}[/dim]"
                f" [bold green]signals={_cycle_signals}[/bold green]"
                f"[dim] holding={_holding_now}[/dim]"
            )
        else:
            # 이벤트 없음 → 최소 1줄
            console.print(
                f"[dim]━ #{self._cycle_count} {current_time}"
                f" | scanned={_cycle_scanned} active={_table_rows}"
                f" | no signal | holding={_holding_now}[/dim]"
            )
        console.print("=" * 120)

    def _get_time_weight(self, strategy: str = 'smc') -> float:
        """시간대별 포지션 사이즈 가중치 — 차단 없음, 순수 배율 반환.

        experiment / exploration 모드: 항상 1.0 (관측 목적, 시간 페널티 없음).
        squeeze / defensive: 오후에도 페널티 없음 (횡보장 전략).
        smc: 12:30 이후 배율 축소 (추세 소멸 반영).

        Returns:
            0.0~1.0 배율 (0.0 = execute_buy 건너뜀, but _check_global_risk_gates가 먼저 막음)
        """
        if strategy in ('experiment', 'exploration'):
            return 1.0

        from datetime import time as _t
        t = datetime.now().time()
        cfg = self.config.get('time_weight', {})
        if not cfg.get('enabled', True):
            return 1.0

        if t < _t(10, 0):    # _check_global_risk_gates가 차단 — 이 경로 정상적으로 없음
            return 0.0
        if t < _t(10, 30):   # 초입부 — 조심
            return float(cfg.get('early', 0.7))
        if t < _t(12, 30):   # 핵심 구간
            return float(cfg.get('core', 1.0))
        if t < _t(14, 30):   # 오후
            if strategy in ('squeeze', 'defensive', 'rs'):
                return float(cfg.get('afternoon_squeeze', 1.0))
            return float(cfg.get('afternoon_smc', 0.5))
        if t < _t(14, 59):   # 막판 — _check_global_risk_gates가 차단 — 이 경로 정상적으로 없음
            return float(cfg.get('late', 0.0))
        return 0.0

    def _check_global_risk_gates(self, stock_code: str, stock_name: str) -> tuple:
        """시스템 전역 리스크 게이트 — 신호 분석 전 조기 차단 (df 불필요).

        역할: 물리적 시간 제한 + 시스템 전역 리스크 상태 (종목별 상태는 TradeStateManager).
        이 함수가 False 반환 시 어떤 신호 연산도 실행하지 않는다.

        Returns:
            (can_enter: bool, reason: str)
        """
        # 0. 물리적 시간 제약 (시장 구조 — 전략과 무관)
        from datetime import time as _t
        _t_now = datetime.now().time()
        if _t_now < _t(10, 0):
            return False, f"[TIME_PHYSICAL] 10:00 이전 시가 노이즈 ({_t_now.strftime('%H:%M')})"
        if _t_now >= _t(14, 59):
            return False, f"[TIME_PHYSICAL] 14:59 이후 EOD 충돌 방지 ({_t_now.strftime('%H:%M')})"

        # 1. Kill Switch (미청산 포지션 등 긴급 차단)
        if getattr(self, '_kill_switch_active', False):
            _ks_reason = getattr(self, '_kill_switch_reason', '미청산 포지션')
            return False, f"[KILL_SWITCH] {_ks_reason}"

        # 2. Daily Risk Controls (일일 손실 한도 + 최대 거래 횟수)
        _dr_cfg = self.config.get('risk_control.daily_risk', {})
        if _dr_cfg.get('enabled', True):
            if self._daily_loss_halted:
                return False, f"[DAILY_LOSS_LIMIT] 일일 손실 {self._daily_pnl_pct:.2f}%"
            _max_trades = _dr_cfg.get('max_trades_per_day', 3)
            if self._daily_buy_count >= _max_trades:
                return False, f"[MAX_TRADES] {self._daily_buy_count}/{_max_trades}회"

        # 3. Market Sensor (EF 기반 시장 상태 — RISK_OFF_DAY / AFTERNOON_BLOCKED)
        ms_config = self.config.get('re_entry.reentry_cooldown.market_sensor', {})
        can_ms, ms_reason = self.reentry_metrics.can_enter_trade(ms_config)
        if not can_ms:
            return False, f"[MS_BLOCK] {ms_reason}"

        # 4. Drawdown Engine HALT (누적 손실 한도)
        if self.config.get("drawdown_engine", {}).get("enabled", True):
            try:
                _dd_ok, _dd_reason = self.drawdown_engine.can_enter()
                if not _dd_ok:
                    return False, f"[DD_HALT] {_dd_reason}"
            except Exception:
                pass

        # 5. Equity Curve HALT (멀티데이 DD ≤ -18%)
        if hasattr(self, 'equity_ctrl') and self.config.get("equity_control", {}).get("enabled", True):
            try:
                _ec_ok, _ec_halt_r = self.equity_ctrl.can_enter(self.total_assets)
                if not _ec_ok:
                    return False, f"[EC_HALT] {_ec_halt_r}"
            except Exception:
                pass

        return True, "OK"

    async def check_entry_signal(self, stock_code: str, kiwoom_df: pd.DataFrame = None):
        """매수 신호 체크 (SignalOrchestrator 사용 - L0~L6 통합)"""
        try:
            # 🔥 2026-04-03: EMA9 블록 결과 확인 (15분 경과 시 현재가 비교)
            if stock_code in self._ema9_blocks:
                _bd = self._ema9_blocks[stock_code]
                if _bd.get('result_due'):
                    try:
                        _df_tmp = kiwoom_df if kiwoom_df is not None else None
                        _cur_px = float(_df_tmp['close'].iloc[-1]) if _df_tmp is not None and len(_df_tmp) > 0 else None
                        if _cur_px is not None:
                            _elapsed_m = (datetime.now() - _bd['blocked_time']).total_seconds() / 60
                            _chg_pct = (_cur_px - _bd['blocked_price']) / _bd['blocked_price'] * 100
                            _outcome = "상승✅" if _chg_pct >= 0.5 else ("횡보" if _chg_pct >= -0.3 else "하락❌")
                            logger.info(
                                f"[EMA9_BLOCK_RESULT] {stock_code} {_bd['stock_name']}: "
                                f"블록후{_elapsed_m:.0f}분 | "
                                f"{_bd['blocked_price']:.0f}→{_cur_px:.0f} ({_chg_pct:+.2f}%) | "
                                f"결과={_outcome} | grade={_bd['grade']}"
                            )
                            del self._ema9_blocks[stock_code]
                    except Exception:
                        del self._ema9_blocks[stock_code]

            # 진입 모드 확인 (시간 필터 조건부 적용)
            squeeze_config = self.config.get('squeeze_momentum', {})
            entry_mode = squeeze_config.get('entry_mode', 'squeeze_only')

            # 종목 정보 (gate 로깅에 stock_name 필요하므로 먼저 로드)
            stock_info = self.validated_stocks.get(stock_code)
            if not stock_info:
                return

            stock_name = stock_info.get('name', stock_code)
            market = stock_info.get('market', 'KOSPI')
            strategy_tag = stock_info.get('strategy', self.default_strategy_tag)

            # ① Global Risk Gate — 시스템 전역 (물리적 시간 포함 / df 불필요 / 최우선)
            #    범위: 14:59/10:00 물리적 시간 + Kill Switch + Daily Loss + Market Sensor + DD + EC
            _gate_ok, _gate_reason = self._check_global_risk_gates(stock_code, stock_name)
            if not _gate_ok:
                logger.debug(f"[GLOBAL_GATE] {stock_code}: {_gate_reason}")
                return

            # ② Per-stock State Gate — 종목별 (손절 이력 / 당일 매매 이력 / 무효화 신호)
            #    범위: TradeStateManager — 전역 리스크와 완전 분리된 종목 단위 상태
            can_enter, reason = self.state_manager.can_enter(
                stock_code=stock_code,
                strategy_tag=strategy_tag,
                check_stoploss=True,
                check_invalidated=True,
                check_traded=True
            )
            if not can_enter:
                logger.debug(f"[STOCK_GATE] {stock_code}: {reason}")
                from analyzers.smc.smc_decision_logger import get_smc_logger as _gsmc
                _gsmc().log_reject(reason.split('(')[0].strip()[:30])
                return

            # 시간 보조 변수 (차단 없음 — time_weight가 execute_buy에서 사이즈 조절)
            from datetime import time as time_class
            current_time = datetime.now().time()
            is_golden_time = (time_class(10, 0) <= current_time < time_class(10, 30))

            logger.debug(f"[CHECK] {stock_code} 매수 신호 체크 시작")

            # 1. 데이터 조회 (키움 우선, Yahoo Finance 폴백)
            # 임계값 20봉: 키움 5분봉 20개 = 100분 분량, 지표 계산에 충분
            if kiwoom_df is not None and len(kiwoom_df) >= 20:
                df = kiwoom_df.copy()
            else:
                # Yahoo Finance fallback (to_thread로 이벤트 루프 블로킹 방지)
                ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
                ticker = f"{stock_code}{ticker_suffix}"
                df = await asyncio.to_thread(download_stock_data_sync, ticker, days=1)

                if df is None or len(df) < 20:
                    # 반대 시장 시도
                    ticker_alt = f"{stock_code}.KQ" if market == 'KOSPI' else f"{stock_code}.KS"
                    df = await asyncio.to_thread(download_stock_data_sync, ticker_alt, days=1)

                if df is None or len(df) < 20:
                    logger.debug(f"[DATA_LACK] {stock_code}")
                    # 🔧 FIX: 데이터 품질 실패 처리 (문서 명세)
                    self._handle_data_quality_failure(
                        stock_code,
                        stock_name,
                        f"데이터 부족 (df={len(df) if df is not None else 0}봉 < 20봉)"
                    )
                    return

            # 컬럼명 소문자 변환
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
            else:
                df.columns = df.columns.str.lower()

            # 🚨 음수/0 가격 필터링
            if 'close' in df.columns:
                invalid_rows = df[df['close'] <= 0]
                if len(invalid_rows) > 0:
                    logger.debug(f"[DATA_CLEAN] {stock_code}: {len(invalid_rows)}개 제거")
                    df = df[df['close'] > 0].copy()

                if len(df) < 50:
                    logger.debug(f"[DATA_LACK2] {stock_code}")
                    # 🔧 FIX: 데이터 품질 실패 처리 (문서 명세)
                    self._handle_data_quality_failure(
                        stock_code,
                        stock_name,
                        f"필터링 후 데이터 부족 ({len(df)}봉 < 50봉)"
                    )
                    return

            # VWAP 계산
            vwap_config = self.config.get_section('vwap')
            df = self.analyzer.calculate_vwap(df,
                                               use_rolling=vwap_config.get('use_rolling', True),
                                               rolling_window=vwap_config.get('rolling_window', 20))
            df = self.analyzer.calculate_atr(df)

            # 🔧 FIX: ATR 변동성 필터 (문서 명세: ATR ≤ 5%)
            if 'atr' in df.columns:
                current_price = df['close'].iloc[-1]
                atr = df['atr'].iloc[-1]
                atr_pct = (atr / current_price * 100) if current_price > 0 else 0
                if atr_pct > 5.0:
                    logger.debug(f"[ATR_BLOCK] {stock_code}: ATR {atr_pct:.2f}%")
                    return

            signal_config = self.config.get_signal_generation_config()
            df = self.analyzer.generate_signals(df, **signal_config)

            current_price = df['close'].iloc[-1]

            # 🔧 2026-03-08: OB Pullback Entry — 대기 중인 종목 OB zone 체크
            # CHoCH 감지 후 즉시 매수하지 않고 OB 레벨까지 pullback 대기
            if stock_code in self.smc_pending and entry_mode == 'smc':
                ob_cfg = self.config.get('smc.ob_pullback_entry', {})
                if ob_cfg.get('enabled', True):
                    pending = self.smc_pending[stock_code]
                    elapsed_min = (datetime.now() - pending['detected_at']).total_seconds() / 60
                    timeout_min = ob_cfg.get('timeout_minutes', 25)

                    # 타임아웃: 대기 취소
                    if elapsed_min > timeout_min:
                        console.print(f"[yellow]⏱️ [OB_TIMEOUT] {stock_name}: OB 대기 {elapsed_min:.0f}분 초과 → 취소[/yellow]")
                        logger.info(f"[OB_TIMEOUT] {stock_code} {stock_name}: {elapsed_min:.0f}분 초과")
                        del self.smc_pending[stock_code]
                        return

                    ob_high = pending['ob_high']
                    ob_low  = pending['ob_low']
                    choch_p = pending['choch_price']
                    tolerance = ob_cfg.get('ob_zone_tolerance_pct', 0.3) / 100

                    # CHoCH 고점 대비 +0.5% 돌파 시 무효화 (추격 상황)
                    if ob_cfg.get('invalidate_above_choch', True) and current_price > choch_p * 1.005:
                        console.print(f"[yellow]🚫 [OB_INVALID] {stock_name}: 현재가({current_price:,.0f}) > CHoCH({choch_p:,.0f})+0.5% → 무효화[/yellow]")
                        logger.info(f"[OB_INVALID] {stock_code} {stock_name}: price={current_price:.0f} > choch={choch_p:.0f}")
                        del self.smc_pending[stock_code]
                        return

                    # OB zone 진입 체크
                    # ob_entry_zone_pct: OB 하단 N% 이상에서만 진입 (0.5=상단 절반)
                    zone_pct = ob_cfg.get('ob_entry_zone_pct', 0.5)
                    ob_entry_floor = ob_low + (ob_high - ob_low) * zone_pct  # 진입 허용 하한선
                    in_ob_zone = ob_entry_floor <= current_price <= ob_high * (1 + tolerance)

                    if in_ob_zone:
                        # 반응 캔들 확인 (양봉 + body_ratio + OB_low 위 종가)
                        reaction_ok = True
                        reaction_fail_reasons = []
                        if ob_cfg.get('reaction_required', True):
                            last_candle = df.iloc[-2]
                            candle_range = last_candle['high'] - last_candle['low']
                            candle_body  = abs(last_candle['close'] - last_candle['open'])
                            body_min     = ob_cfg.get('reaction_body_ratio', 0.5)
                            is_bullish    = last_candle['close'] > last_candle['open']
                            above_ob_low  = last_candle['close'] > ob_low
                            body_ratio    = (candle_body / candle_range) if candle_range > 0 else 0
                            body_ratio_ok = body_ratio >= body_min
                            reaction_ok = is_bullish and above_ob_low and body_ratio_ok
                            if not is_bullish:    reaction_fail_reasons.append('not_bullish')
                            if not above_ob_low:  reaction_fail_reasons.append(f'close({last_candle["close"]:.0f})<OB_low({ob_low:.0f})')
                            if not body_ratio_ok: reaction_fail_reasons.append(f'body={body_ratio:.0%}<{body_min:.0%}')

                        if reaction_ok:
                            ob_entry_reason = (
                                f"{datetime.now().strftime('%H:%M')} SMC OB Pullback "
                                f"({pending['grade']}급, OB {ob_low:,.0f}~{ob_high:,.0f})"
                            )
                            size_mult = pending['position_size_mult']
                            conf = pending['confidence']
                            console.print(f"[bold green]🎯 [OB_ENTRY] {stock_name}: OB({ob_low:,.0f}~{ob_high:,.0f}) 진입! 현재가={current_price:,.0f}, 대기={elapsed_min:.0f}분[/bold green]")
                            logger.info(f"[OB_ENTRY] {stock_code} {stock_name}: OB({ob_low:.0f}~{ob_high:.0f}) @ {current_price:.0f}, {elapsed_min:.1f}분 대기")
                            del self.smc_pending[stock_code]
                            self._emit_signal(stock_code, stock_name, current_price, df, size_mult, conf, ob_entry_reason, stop_loss=None, strategy='SMC_OB')
                        else:
                            # 🔧 2026-03-09: REACTION_FAIL 상세 로그
                            fail_str = ', '.join(reaction_fail_reasons) if reaction_fail_reasons else '?'
                            logger.debug(f"[REACTION_FAIL] {stock_code}: {fail_str} {elapsed_min:.0f}m")
                    else:
                        # 🔧 2026-03-09: OB_WAIT에 zone 거리 표시
                        dist_pct = (ob_entry_floor - current_price) / current_price * 100
                        logger.debug(f"[OB_WAIT] {stock_code}: floor={ob_entry_floor:.0f} cur={current_price:.0f} -{dist_pct:.2f}% {elapsed_min:.0f}m")
                    return  # 일반 신호 체크 skip (OB 대기 상태)

            # 🚨 음수 가격 최종 검증
            if current_price <= 0:
                console.print(f"[red]❌ {stock_code}: 비정상 현재가 {current_price}[/red]")
                return

            # 2. 진입 조건 모드 확인
            squeeze_config = self.config.get('squeeze_momentum', {})
            entry_mode = squeeze_config.get('entry_mode', 'squeeze_only')  # 기본값: squeeze_only

            # 진입 이유 / 구조 손절가 초기화 (각 모드에서 설정)
            entry_reason = None
            structure_stop_price = None

            # 3. 모드별 진입 조건 체크
            if entry_mode == "squeeze_only":
                # ========================================
                # 모드 1: 스퀴즈 모멘텀만 사용 (기존 필터 무시)
                # ========================================
                console.print(f"[cyan]📊 진입 모드: 스퀴즈 모멘텀 전용[/cyan]")

                if not squeeze_config.get('enabled', False) or not squeeze_config.get('entry_filter', {}).get('enabled', False):
                    console.print(f"[red]❌ {stock_name}: 스퀴즈 모멘텀이 비활성화됨[/red]")
                    return

                from utils.squeeze_momentum_realtime import check_squeeze_momentum_filter
                sqz_passed, sqz_reason, sqz_details = check_squeeze_momentum_filter(df, for_entry=True)

                if not sqz_passed:
                    logger.debug(f"[SQZ_BLOCK] {stock_code}: {sqz_reason}")
                    console.print(f"[dim]  색상: {sqz_details.get('color', 'N/A')}, 모멘텀: {sqz_details.get('momentum', 0):.2f}[/dim]")
                    return
                else:
                    console.print(f"[green]✅ {stock_name}: Squeeze 통과 - {sqz_reason}[/green]")

                # 스퀴즈 모멘텀만 사용하므로 SignalOrchestrator 건너뛰기
                entry_confidence = 0.8  # 스퀴즈 전용 신뢰도
                position_size_mult = 1.0  # 풀 포지션

            elif entry_mode == "squeeze_with_orderbook":
                # ========================================
                # 모드 1.5: 스퀴즈 + 호가창 통합 전략
                # ========================================
                console.print(f"[cyan]📊 진입 모드: 스퀴즈 + 호가창 통합[/cyan]")

                if not squeeze_config.get('enabled', False):
                    console.print(f"[red]❌ {stock_name}: 스퀴즈 모멘텀이 비활성화됨[/red]")
                    return

                # 1. 호가창 데이터 수집
                try:
                    orderbook_data = self.api.get_stock_quote(stock_code)

                    # 🔥 FIX: None 체크 추가
                    if orderbook_data is None or orderbook_data.get('return_code') != 0:
                        console.print(f"[yellow]⚠️  {stock_name}: 호가 데이터 조회 실패, 스퀴즈만 사용[/yellow]")
                        # 호가 데이터 없으면 기존 스퀴즈만 사용
                        from utils.squeeze_momentum_realtime import check_squeeze_momentum_filter
                        sqz_passed, sqz_reason, sqz_details = check_squeeze_momentum_filter(df, for_entry=True)

                        if not sqz_passed:
                            console.print(f"[yellow]⚠️  {stock_name}: Squeeze 차단 - {sqz_reason}[/yellow]")
                            return

                        entry_confidence = 0.8
                        position_size_mult = 1.0
                    else:
                        # 2. 호가창 데이터 파싱
                        output = orderbook_data.get('output', {})

                        # 매도 1호가 정보
                        sell_1st_qty = float(output.get('sell_hoga_rem_qty_1', 0))
                        tot_sell_qty = float(output.get('tot_sell_hoga_rem_qty', 0))
                        tot_buy_qty = float(output.get('tot_buy_hoga_rem_qty', 0))

                        # 체결강도 계산 (매수 / 매도 비율)
                        if tot_sell_qty > 0:
                            execution_strength = (tot_buy_qty / tot_sell_qty) * 100
                        else:
                            execution_strength = 100.0  # 기본값

                        # 3. VWAP 계산 (5분/20분)
                        vwap = df['close'].rolling(20).mean().iloc[-1]
                        vwap_5min = df['close'].tail(5).mean() if len(df) >= 5 else vwap

                        # 4. 거래량 데이터
                        recent_5min_volume = df['volume'].tail(5).sum() if len(df) >= 5 else 0
                        prev_5min_volume = df['volume'].iloc[-10:-5].sum() if len(df) >= 10 else recent_5min_volume * 0.8

                        # 5. 최근 고가
                        recent_high_5min = df['high'].tail(5).max() if len(df) >= 5 else current_price

                        # 6. 통합 전략 진입 신호 체크
                        signal, reason, details = self.squeeze_orderbook_strategy.check_entry_signal(
                            stock_code=stock_code,
                            df=df,
                            current_price=current_price,
                            vwap=vwap,
                            vwap_5min=vwap_5min,
                            recent_5min_volume=recent_5min_volume,
                            prev_5min_volume=prev_5min_volume,
                            sell_1st_qty=sell_1st_qty,
                            sell_1st_avg_1min=sell_1st_qty,  # 간소화: 현재값 사용
                            sell_total_current=tot_sell_qty,
                            sell_total_avg=tot_sell_qty,  # 간소화: 현재값 사용
                            execution_strength=execution_strength,
                            stock_avg_strength=100.0,  # 기본값
                            price_stable_sec=0.0,  # TODO: 실시간 데이터에서 계산 필요
                            recent_high_5min=recent_high_5min
                        )

                        if not signal:
                            logger.debug(f"[STRATEGY_BLOCK] {stock_code}: {reason}")

                            # 상세 정보 출력
                            if 'squeeze' in details:
                                sq = details['squeeze']
                                console.print(f"[dim]  스퀴즈: {sq['reason']} (Tier {sq.get('tier', 0)})[/dim]")

                            if 'orderbook' in details:
                                console.print(f"[dim]  호가창 조건:[/dim]")
                                for cond, result in details['orderbook'].items():
                                    status = "✓" if result.get('pass') else "✗"
                                    console.print(f"[dim]    {status} {cond}: {result.get('reason', 'N/A')}[/dim]")

                            return
                        else:
                            console.print(f"[green]✅ {stock_name}: {reason}[/green]")

                            # 호가창 상세 정보 출력 (성공 시에도)
                            if 'orderbook' in details:
                                passed = sum([1 for r in details['orderbook'].values() if r.get('pass')])
                                total = len(details['orderbook'])
                                console.print(f"[green]  호가창: {passed}/{total} 통과[/green]")
                                for cond, result in details['orderbook'].items():
                                    status = "✓" if result.get('pass') else "✗"
                                    console.print(f"[dim]    {status} {cond}: {result.get('reason', 'N/A')}[/dim]")

                            # 티어 기반 신뢰도 조정
                            tier = details.get('squeeze', {}).get('tier', 1)
                            if tier >= 3:
                                entry_confidence = 0.95
                                position_size_mult = 1.2
                            elif tier >= 2:
                                entry_confidence = 0.85
                                position_size_mult = 1.0
                            else:
                                entry_confidence = 0.75
                                position_size_mult = 0.8

                            console.print(f"[green]  Tier {tier} 진입 (신뢰도: {entry_confidence*100:.0f}%, 포지션: {position_size_mult*100:.0f}%)[/green]")

                except Exception as e:
                    console.print(f"[red]❌ {stock_name}: 호가창 데이터 처리 실패 - {e}[/red]")
                    import traceback
                    traceback.print_exc()
                    return

            elif entry_mode == "ma_cross":
                # ========================================
                # 모드: MA 골든크로스/데드크로스 전략
                # - 5분봉 MA5/MA10 골든크로스만
                # - 추가 조건 없음
                # - 시간 제한 없음
                # - 호가창 필터 없음
                # ========================================
                console.print(f"[cyan]📊 진입 모드: 5분봉 MA 골든크로스 전략[/cyan]")

                # 1분봉 데이터 그대로 사용
                df_1min = df.copy()

                # 5분봉으로 리샘플링
                try:
                    # cntr_tm을 DatetimeIndex로 변환 (예: 20260109090500 → 2026-01-09 09:05:00)
                    if 'cntr_tm' in df_1min.columns:
                        df_1min['datetime'] = pd.to_datetime(df_1min['cntr_tm'], format='%Y%m%d%H%M%S', errors='coerce')
                        df_1min = df_1min.set_index('datetime')
                    elif not isinstance(df_1min.index, pd.DatetimeIndex):
                        console.print(f"[red]❌ {stock_name}: 시간 정보 없음 (cntr_tm 컬럼 없음)[/red]")
                        return

                    # 1분봉을 5분봉으로 변환
                    df_5min = df_1min.resample('5min').agg({
                        'open': 'first',
                        'high': 'max',
                        'low': 'min',
                        'close': 'last',
                        'volume': 'sum'
                    }).dropna()

                    # 인덱스 리셋 (MA 계산 시 인덱스 접근 편의성)
                    df_5min = df_5min.reset_index(drop=True)

                    console.print(f"[dim]  ✓ 5분봉 리샘플링 완료: {len(df_5min)}개 봉[/dim]")

                    # MA Cross 전략 진입 체크 (5분봉만)
                    signal, reason, details = self.ma_cross_strategy.check_entry_signal(
                        df_5min=df_5min,
                        debug=True
                    )

                    if not signal:
                        logger.debug(f"[STRATEGY_BLOCK] {stock_code}: {reason}")
                        return
                    else:
                        console.print(f"[green]✅ {stock_name}: {reason}[/green]")

                    # MA Cross는 고정 신뢰도
                    entry_confidence = 0.8
                    position_size_mult = 1.0

                    # 진입 이유 생성 (시간 + 전략 상세)
                    entry_reason = f"{datetime.now().strftime('%H:%M')} 5분봉 {reason}"

                except Exception as e:
                    console.print(f"[red]❌ {stock_name}: MA Cross 처리 실패 - {e}[/red]")
                    import traceback
                    traceback.print_exc()
                    return

            elif entry_mode == "squeeze_2tf":
                # ========================================
                # 모드: 2-타임프레임 전략 (30분봉 방향 + 하위봉 진입)
                # - 30분봉: MA5/MA20 골든크로스 + Squeeze OFF + 모멘텀 상승
                # - 5분봉 (또는 3분봉): 골든크로스 또는 눌림 후 반등
                # ========================================
                console.print(f"[cyan]📊 진입 모드: 2-타임프레임 전략 (30분봉 + {self.two_tf_strategy.lower_tf})[/cyan]")

                # 1분봉 데이터 준비
                df_1min = df.copy()

                try:
                    # cntr_tm을 DatetimeIndex로 변환
                    if 'cntr_tm' in df_1min.columns:
                        df_1min['datetime'] = pd.to_datetime(df_1min['cntr_tm'], format='%Y%m%d%H%M%S', errors='coerce')
                        df_1min = df_1min.set_index('datetime')
                    elif not isinstance(df_1min.index, pd.DatetimeIndex):
                        console.print(f"[red]❌ {stock_name}: 시간 정보 없음 (cntr_tm 컬럼 없음)[/red]")
                        return

                    # 30분봉으로 리샘플링
                    df_30min = df_1min.resample('30min').agg({
                        'open': 'first',
                        'high': 'max',
                        'low': 'min',
                        'close': 'last',
                        'volume': 'sum'
                    }).dropna()

                    # 5분봉으로 리샘플링 (또는 config에서 설정된 하위봉)
                    lower_tf = self.two_tf_strategy.lower_tf
                    df_lower = df_1min.resample(lower_tf).agg({
                        'open': 'first',
                        'high': 'max',
                        'low': 'min',
                        'close': 'last',
                        'volume': 'sum'
                    }).dropna()

                    console.print(f"[dim]  ✓ 30분봉: {len(df_30min)}개, {lower_tf}: {len(df_lower)}개[/dim]")

                    # 데이터 충분성 체크
                    if len(df_30min) < 25:
                        console.print(f"[yellow]⚠️  {stock_name}: 30분봉 데이터 부족 ({len(df_30min)}개 < 25개)[/yellow]")
                        return

                    if len(df_lower) < 25:
                        console.print(f"[yellow]⚠️  {stock_name}: {lower_tf} 데이터 부족 ({len(df_lower)}개 < 25개)[/yellow]")
                        return

                    # 2-타임프레임 전략 체크
                    signal, reason, details = self.two_tf_strategy.check_entry_signal(
                        df_higher=df_30min,
                        df_lower=df_lower,
                        debug=True
                    )

                    if not signal:
                        logger.debug(f"[STRATEGY_BLOCK] {stock_code}: {reason}")
                        return
                    else:
                        console.print(f"[green]✅ {stock_name}: {reason}[/green]")

                    # 신뢰도 설정 (상위봉 + 하위봉 모두 충족)
                    entry_confidence = 0.85
                    position_size_mult = 1.0

                    # 🔧 NEW: 골든타임 보너스 (10:00~10:30 진입 시 신뢰도 상향)
                    if is_golden_time:
                        entry_confidence = 0.95
                        position_size_mult = 1.2
                        console.print(f"[green]⭐ 골든타임 진입! 신뢰도 0.95, 포지션 1.2배[/green]")

                    # 진입 이유 생성 (시간 + 전략 상세)
                    higher_details = details.get('higher_tf', {})
                    momentum_color = higher_details.get('momentum_color', 'N/A')
                    golden_tag = " [골든타임]" if is_golden_time else ""
                    entry_reason = f"{datetime.now().strftime('%H:%M')} 30분봉 MA5/MA20 골든크로스 + Squeeze OFF + 모멘텀({momentum_color}) + {self.two_tf_strategy.lower_tf} 진입{golden_tag}"

                except Exception as e:
                    console.print(f"[red]❌ {stock_name}: 2-타임프레임 전략 처리 실패 - {e}[/red]")
                    import traceback
                    traceback.print_exc()
                    return

            elif entry_mode == "smc":
                # ========================================
                # 모드: SMC (Smart Money Concepts) 전략
                # - CHoCH (Change of Character) 감지
                # - Liquidity Sweep 확인
                # - Order Block 영역 체크
                # ========================================
                logger.debug(f"[MODE] SMC {stock_code}")

                # 1분봉 데이터 준비
                df_1min = df.copy()

                try:
                    # cntr_tm을 DatetimeIndex로 변환
                    if 'cntr_tm' in df_1min.columns:
                        df_1min['datetime'] = pd.to_datetime(df_1min['cntr_tm'], format='%Y%m%d%H%M%S', errors='coerce')
                        df_1min = df_1min.set_index('datetime')
                    elif not isinstance(df_1min.index, pd.DatetimeIndex):
                        console.print(f"[red]❌ {stock_name}: 시간 정보 없음 (cntr_tm 컬럼 없음)[/red]")
                        return

                    # 5분봉으로 리샘플링 (SMC 분석용)
                    df_5min = df_1min.resample('5min').agg({
                        'open': 'first',
                        'high': 'max',
                        'low': 'min',
                        'close': 'last',
                        'volume': 'sum'
                    }).dropna()

                    logger.debug(f"[DATA] {stock_code} 5분봉={len(df_5min)}개")

                    # 데이터 충분성 체크
                    if len(df_5min) < 50:
                        logger.debug(f"[DATA_LACK] {stock_code} 5min {len(df_5min)}봉 < 50")
                        return

                    # 🔧 2026-01-29: MTF Bias 필터용 30분봉 데이터 생성
                    df_30min = None
                    smc_config = self.config.get('smc', {})
                    mtf_config = smc_config.get('mtf_bias', {})
                    mtf_enabled = mtf_config.get('enabled', True)

                    if mtf_enabled:
                        try:
                            df_30min = df_1min.resample('30min').agg({
                                'open': 'first',
                                'high': 'max',
                                'low': 'min',
                                'close': 'last',
                                'volume': 'sum'
                            }).dropna()

                            if len(df_30min) >= 20:
                                logger.debug(f"[DATA] {stock_code} 30분봉={len(df_30min)}개")
                            else:
                                logger.debug(f"[DATA] {stock_code} 30분봉 부족({len(df_30min)}개) MTF비활성")
                                df_30min = None
                        except Exception as e:
                            logger.debug(f"[DATA] {stock_code} 30분봉 생성실패: {e}")
                            df_30min = None

                    # ✅ BB(30,1) 관측 + Squeeze Sub 신호 캡처
                    sqz_signal = None
                    try:
                        from utils.squeeze_momentum import calculate_squeeze_momentum
                        df_5min_sqz = calculate_squeeze_momentum(df_5min.copy())
                        sqz_signal = self.bb30_observer.observe(
                            stock_code=stock_code,
                            stock_name=stock_name,
                            df=df_5min_sqz,
                            current_price=current_price
                        )
                    except Exception as e:
                        pass  # 관측 실패 시 무시 (진입에 영향 없음)

                    # SMC 전략 체크 (🔧 2026-01-29: MTF Bias 필터 추가)
                    signal, reason, details = self.smc_strategy.check_entry_signal(
                        df=df_5min,
                        debug=True,
                        df_htf=df_30min,  # 30분봉 데이터 (MTF Bias 필터용)
                        symbol=stock_code  # 🔧 2026-03-10: Sweep Attempt Log
                    )

                    # 🔧 2026-03-18: SMC 디스플레이 캐시 업데이트
                    _sw = details.get('liquidity_sweep') or {}
                    _sw_type_raw = _sw.get('sweep_type', '')
                    _sw_type = 'P' if _sw_type_raw == 'penetration' else ('E' if _sw_type_raw == 'equal_level' else '-')
                    _sw_dist = _sw.get('swept_level', 0)
                    _cur = details.get('current_price', 0)
                    _dist_pct = abs(_cur - _sw_dist) / _sw_dist * 100 if _sw_dist > 0 else 0
                    self._smc_display_cache[stock_code] = {
                        'sweep_type': _sw_type,
                        'sweep_dist': _dist_pct,
                        'choch_grade': details.get('choch_grade', {}).get('grade', '-'),
                        'smc_state': 'SIGNAL' if signal else 'NO_SIG',
                        'last_updated': datetime.now().strftime('%H:%M'),
                    }

                    if not signal:
                        logger.info(f"[SMC_NO_SIG] {stock_code} {reason}")

                        # 🔧 2026-03-21: Trend Breakout Fallback
                        # SMC 신호 없을 때 → 레짐 확인 후 TREND 전략 시도
                        trend_cfg = self.config.get("trend", {})
                        auto_on   = trend_cfg.get("auto_enable_on_trend", True)
                        _max_td   = trend_cfg.get("size", {}).get("max_per_day", 2)

                        if auto_on and self._daily_trend_count < _max_td:
                            try:
                                # ── MKT_CTX 게이트 (NO_TRADE_DAY 시 TREND 차단) ──────────────
                                _mc_trend, _mc_trend_reason, _mc_trend_det = self.market_context.evaluate()
                                if _mc_trend == "NO_TRADE_DAY":
                                    logger.info(
                                        f"[TREND_MKT_BLOCK] {stock_code} {stock_name}: "
                                        f"NO_TRADE_DAY → TREND 진입 차단 | {_mc_trend_reason}"
                                    )
                                    return

                                # ── ATR 수축 시 BREAKOUT 추가 차단 ──────────────────────────
                                _vol_det = _mc_trend_det.get("volatility", {})
                                _vol_reason_str = _vol_det.get("reason", "")
                                _atr_ratio_val = 0.0
                                import re as _re_atr
                                _atr_m = _re_atr.search(r"ATR비율=([\d.]+)", _vol_reason_str)
                                if _atr_m:
                                    _atr_ratio_val = float(_atr_m.group(1))
                                _min_atr_for_breakout = trend_cfg.get("min_atr_ratio_for_breakout", 0.85)
                                if 0 < _atr_ratio_val < _min_atr_for_breakout:
                                    logger.info(
                                        f"[TREND_ATR_BLOCK] {stock_code} {stock_name}: "
                                        f"ATR비율={_atr_ratio_val:.2f} < {_min_atr_for_breakout} → 저변동성 돌파 차단"
                                    )
                                    return

                                regime, regime_reason = self.market_context.get_regime()
                                if regime == "TREND":
                                    # 시간 필터
                                    tf_cfg   = trend_cfg.get("time_filter", {})
                                    t_start  = tf_cfg.get("start", "10:30")
                                    t_end    = tf_cfg.get("end", "13:30")
                                    now_hm   = datetime.now().strftime("%H:%M")
                                    if t_start <= now_hm <= t_end:
                                        # 잠시 self.trend_strategy.enabled 덮어쓰기
                                        self.trend_strategy.enabled = True
                                        t_signal, t_reason, t_details = self.trend_strategy.check_entry(df_5min, debug=True)
                                        self.trend_strategy.enabled = trend_cfg.get("enabled", False)

                                        if t_signal:
                                            # ── Fake Breakout 필터 (최종 방어선) ────────────
                                            _fb_cfg      = trend_cfg.get("fake_breakout_filter", {})
                                            _fb_enabled  = _fb_cfg.get("enabled", True)
                                            _fb_vol_min  = _fb_cfg.get("min_volume_ratio", 2.0)
                                            _fb_atr_min  = _fb_cfg.get("min_atr_ratio", 0.9)
                                            _fb_body_min = _fb_cfg.get("min_body_ratio", 0.6)

                                            if _fb_enabled:
                                                _fb_vol  = t_details.get("volume_ratio", 0.0)
                                                _fb_body = t_details.get("body_ratio", 0.0)
                                                _fb_atr  = _atr_ratio_val  # Gate 2에서 계산된 값 재사용

                                                _fb_reason = None
                                                if _fb_vol < _fb_vol_min:
                                                    _fb_reason = f"WEAK_VOLUME(vol={_fb_vol:.1f}<{_fb_vol_min})"
                                                elif _fb_atr < _fb_atr_min:
                                                    _fb_reason = f"LOW_ATR(atr={_fb_atr:.2f}<{_fb_atr_min})"
                                                elif _fb_body < _fb_body_min:
                                                    _fb_reason = f"WEAK_CANDLE(body={_fb_body:.2f}<{_fb_body_min})"

                                                if _fb_reason:
                                                    logger.info(
                                                        f"[FAKE_BREAKOUT] {stock_code} {stock_name}: "
                                                        f"{_fb_reason} | vol={_fb_vol:.1f} "
                                                        f"atr={_fb_atr:.2f} body={_fb_body:.2f}"
                                                    )
                                                    return

                                            logger.info(f"[TREND_SIG] {stock_code} {stock_name}: {t_reason} | 레짐={regime_reason}")
                                            console.print(f"[cyan]📈 [TREND] {stock_name}: {t_reason}[/cyan]")

                                            # 등급별 포지션 사이즈
                                            _size_cfg = trend_cfg.get("size", {})
                                            _grade    = t_details.get("grade", "NORMAL")
                                            _sz_map   = {"STRONG": _size_cfg.get("strong", 1.0),
                                                         "NORMAL": _size_cfg.get("normal", 0.6),
                                                         "WEAK":   _size_cfg.get("weak",   0.4)}
                                            position_size_mult = _sz_map.get(_grade, 0.6)
                                            entry_confidence   = 0.75 if _grade == "STRONG" else 0.60
                                            entry_reason       = f"{datetime.now().strftime('%H:%M')} {t_reason}"
                                            self._daily_trend_count += 1

                                            self.execute_buy(
                                                stock_code=stock_code,
                                                stock_name=stock_name,
                                                price=current_price,
                                                df=df,
                                                position_size_mult=position_size_mult,
                                                entry_confidence=entry_confidence,
                                                entry_reason=entry_reason,
                                            )
                                        else:
                                            logger.debug(f"[TREND_NO_SIG] {stock_code}: {t_reason}")
                                            # 🔥 2026-04-03: MISSED_BY_EMA9 추적 시작
                                            _would_grade = t_details.get("would_be_grade")
                                            if _would_grade and stock_code not in self._ema9_blocks:
                                                self._ema9_blocks[stock_code] = {
                                                    'blocked_price': current_price,
                                                    'blocked_time': datetime.now(),
                                                    'grade': _would_grade,
                                                    'stock_name': stock_name,
                                                }
                                                logger.info(
                                                    f"[MISSED_BY_EMA9] {stock_code} {stock_name}: "
                                                    f"price={current_price:.0f} grade={_would_grade} "
                                                    f"→ 15분 추적 시작"
                                                )
                                    else:
                                        logger.debug(f"[TREND_TIME_BLOCK] {stock_code}: {now_hm} 범위밖 {t_start}~{t_end}")
                                else:
                                    logger.debug(f"[TREND_REGIME_SKIP] {stock_code}: {regime} ({regime_reason})")
                            except Exception as _te:
                                logger.debug(f"[TREND_ERR] {stock_code}: {_te}")

                        # 🔧 2026-04-30: Squeeze Sub (NEUTRAL 레짐 전용)
                        # SMC로 잃지 않고, Squeeze로 번다
                        # STRONG = squeeze_on + momentum_rising + BB(30,1) 돌파
                        _sqz_cfg = self.config.get('squeeze_sub', {})
                        if _sqz_cfg.get('enabled', False) and sqz_signal is not None:
                            _sqz_max = _sqz_cfg.get('max_per_day', 3)
                            if self._daily_sqz_count < _sqz_max and stock_code not in self.positions:
                                try:
                                    # ── Gate A: 연패 차단 ────────────────────────────────────
                                    _sqz_max_loss = _sqz_cfg.get('max_consecutive_losses', 2)
                                    if self._sqz_consecutive_losses >= _sqz_max_loss:
                                        logger.info(
                                            f"[SQZ_STREAK_BLOCK] {stock_code} {stock_name}: "
                                            f"연패 {self._sqz_consecutive_losses}회 → Squeeze 차단"
                                        )
                                    else:
                                        # ── Gate B: 시장 약세 필터 (-0.5% 이하만 차단) ─────────
                                        # NO_TRADE_DAY도 횡보 구간(NEUTRAL)에선 Squeeze 허용
                                        # 단, 급락(-0.5% 이하)은 차단
                                        _mkt_chg     = getattr(self.market_context, '_kodex200_change_pct', 0.0)
                                        _sqz_weak_th = _sqz_cfg.get('weak_market_threshold', -0.5)
                                        if _mkt_chg <= _sqz_weak_th:
                                            logger.info(
                                                f"[SQZ_MARKET_WEAK] {stock_code} {stock_name}: "
                                                f"KODEX200 {_mkt_chg:.2f}% ≤ {_sqz_weak_th}% → Squeeze 차단"
                                            )
                                        else:
                                            regime_sqz, _ = self.market_context.get_regime()
                                            if regime_sqz == "NEUTRAL":
                                                _sqz_tf  = _sqz_cfg.get('time_filter', {})
                                                _sqz_ts  = _sqz_tf.get('start', '10:00')
                                                _sqz_te  = _sqz_tf.get('end', '13:30')
                                                _now_sqz = datetime.now().strftime('%H:%M')
                                                if _sqz_ts <= _now_sqz <= _sqz_te:
                                                    if sqz_signal.get('signal_type') == 'STRONG':
                                                        # ── Gate C: BB width 품질 필터 ────────
                                                        _sqz_bbw    = sqz_signal.get('bb_width_pct', 999.0)
                                                        _max_bbw    = _sqz_cfg.get('max_bb_width_pct', 3.0)
                                                        _sqz_bbw_ok = (_sqz_bbw <= _max_bbw and
                                                                       sqz_signal['momentum'] > 0)
                                                        # ① 거래량 상대 필터 (대형/소형 구분)
                                                        _sqz_vol_cur   = df_5min['volume'].iloc[-1] if df_5min is not None and len(df_5min) >= 1 else 0
                                                        _sqz_vol_avg   = df_5min['volume'].iloc[-20:].mean() if df_5min is not None and len(df_5min) >= 20 else 0
                                                        _sqz_vol_ratio = _sqz_vol_cur / _sqz_vol_avg if _sqz_vol_avg > 0 else 0
                                                        _sqz_avg_val   = current_price * _sqz_vol_avg
                                                        _lc_threshold  = _sqz_cfg.get('large_cap_avg_value', 500_000_000)
                                                        _vol_min_lc    = _sqz_cfg.get('min_volume_ratio_large', 1.5)
                                                        _vol_min_sc    = _sqz_cfg.get('min_volume_ratio_small', 2.5)
                                                        _sqz_min_vol   = _vol_min_lc if _sqz_avg_val >= _lc_threshold else _vol_min_sc
                                                        _sqz_vol_ok    = _sqz_vol_ratio >= _sqz_min_vol
                                                        # ② 돌파 유지 확인 (휩쏘 제거)
                                                        _sqz_prev_hi  = df_5min['high'].iloc[-2] if df_5min is not None and len(df_5min) >= 2 else 0
                                                        _sqz_cur_low  = df_5min['low'].iloc[-1] if df_5min is not None and len(df_5min) >= 1 else 0
                                                        _holdback     = _sqz_cfg.get('holdback_ratio', 0.998)
                                                        _sqz_hb_ok    = (
                                                            sqz_signal['close'] > _sqz_prev_hi and
                                                            _sqz_cur_low >= _sqz_prev_hi * _holdback
                                                        ) if _sqz_prev_hi > 0 else False
                                                        if _sqz_bbw_ok and _sqz_vol_ok and _sqz_hb_ok:
                                                            _sqz_sz   = _sqz_cfg.get('size_mult', 0.3)
                                                            _sqz_stop = _sqz_cfg.get('stop_pct', 0.8)
                                                            _sqz_tp   = _sqz_cfg.get('tp_pct', 1.5)
                                                            _is_lc    = _sqz_avg_val >= _lc_threshold
                                                            # ── 패턴 Learning Gate ───────────────────
                                                            from analyzers.squeeze_pattern_stats import build_pattern_key
                                                            _pat_cfg  = _sqz_cfg.get('pattern', {})
                                                            _pat_key  = build_pattern_key(
                                                                sqz_signal, regime_sqz,
                                                                _sqz_vol_ratio, _sqz_avg_val,
                                                                _lc_threshold, _now_sqz
                                                            )
                                                            _pat_now = datetime.now()
                                                            _pat_ok, _pat_r = self.sqz_pattern_stats.pattern_allowed(_pat_key, _pat_cfg, _pat_now)
                                                            if not _pat_ok:
                                                                _explore_prob = _pat_cfg.get('exploration_prob', 0.10)
                                                                if self.sqz_pattern_stats.exploration_override(_explore_prob):
                                                                    _pat_ok = True
                                                                    _pat_r  = f"EXPLORE({int(_explore_prob*100)}%) {_pat_r}"
                                                                    logger.info(f"[SQZ_EXPLORE] {stock_code} {stock_name}: {_pat_key} | {_pat_r}")
                                                                else:
                                                                    logger.info(
                                                                        f"[SQZ_PATTERN_BLOCK] {stock_code} {stock_name}: "
                                                                        f"{_pat_key} | {_pat_r}"
                                                                    )
                                                            if _pat_ok:
                                                                _pat_mult  = self.sqz_pattern_stats.size_multiplier(_pat_key, _pat_cfg)
                                                                _sqz_sz_f  = _sqz_sz * _pat_mult
                                                                logger.info(
                                                                    f"[SQZ_SIG] {stock_code} {stock_name}: "
                                                                    f"STRONG {'대형' if _is_lc else '소형'} "
                                                                    f"bbw={_sqz_bbw:.2f}%(max={_max_bbw}) "
                                                                    f"vol={_sqz_vol_ratio:.1f}x(min={_sqz_min_vol}) "
                                                                    f"momentum={sqz_signal['momentum']:.2f} "
                                                                    f"pat={_pat_key} mult={_pat_mult:.2f} | {_pat_r} "
                                                                    f"stop={_sqz_stop}% tp={_sqz_tp}%"
                                                                )
                                                                console.print(f"[yellow]⚡ [SQUEEZE] {stock_name}: {'대형' if _is_lc else '소형'} bbw={_sqz_bbw:.2f}% vol={_sqz_vol_ratio:.1f}x ×{_pat_mult:.2f}[/yellow]")
                                                                self._daily_sqz_count += 1
                                                                self.execute_buy(
                                                                    stock_code=stock_code,
                                                                    stock_name=stock_name,
                                                                    price=current_price,
                                                                    df=df,
                                                                    position_size_mult=_sqz_sz_f,
                                                                    entry_confidence=0.70,
                                                                    entry_reason=f"SQZ:{datetime.now().strftime('%H:%M')} bbw={_sqz_bbw:.1f}% vol={_sqz_vol_ratio:.1f}x",
                                                                    stop_loss_pct_override=_sqz_stop,
                                                                    take_profit_pct=_sqz_tp,
                                                                )
                                                                # 패턴 키를 포지션에 저장 (청산 시 업데이트용)
                                                                if stock_code in self.positions:
                                                                    self.positions[stock_code]['sqz_pattern_key'] = _pat_key
                                                        else:
                                                            logger.info(
                                                                f"[SQZ_NO_SIG] {stock_code} {stock_name}: "
                                                                f"bbw_ok={_sqz_bbw_ok}({_sqz_bbw:.2f}%<={_max_bbw} mom={sqz_signal['momentum']:.2f}) "
                                                                f"vol_ok={_sqz_vol_ok}({_sqz_vol_ratio:.1f}x/min={_sqz_min_vol}) "
                                                                f"hb_ok={_sqz_hb_ok}"
                                                            )
                                except Exception as _sqze:
                                    logger.debug(f"[SQZ_ERR] {stock_code}: {_sqze}")

                        # 🔧 2026-04-03: RS (Relative Strength) Mode
                        # TRADE_OK 상태에서만 작동 (DEFENSIVE 반대)
                        _rs_cfg = self.config.get('rs_strategy', {})
                        if _rs_cfg.get('enabled', False):
                            _rs_max = _rs_cfg.get('max_per_day', 2)
                            if self._daily_rs_count < _rs_max and stock_code not in self.positions:
                                _mkt_rs, _, _ = self.market_context.evaluate()
                                if _mkt_rs == "TRADE_OK":
                                    _rs_tf   = _rs_cfg.get('time_filter', {})
                                    _rs_ts   = _rs_tf.get('start', '10:30')
                                    _rs_te   = _rs_tf.get('end', '13:30')
                                    _now_rs  = datetime.now().strftime('%H:%M')
                                    if _rs_ts <= _now_rs <= _rs_te:
                                        try:
                                            _rs_sig, _rs_reason, _rs_det = self.rs_strategy.check_entry(
                                                stock_code=stock_code,
                                                stock_name=stock_name,
                                                df_5min=df_5min if 'df_5min' in dir() else df,
                                                realtime_price=current_price,
                                            )
                                            if _rs_sig:
                                                console.print(f"[cyan]📈 [RS] {stock_name}: {_rs_reason}[/cyan]")
                                                # 🔧 2026-04-04: 전략별 Drawdown halt 체크 (counter 증가 전)
                                                _rs_dd_ok = True
                                                if self.config.get("drawdown_engine", {}).get("enabled", True):
                                                    _rs_dd_ok, _dd_strat_r = self.drawdown_engine.can_enter(strategy="rs")
                                                    if not _rs_dd_ok:
                                                        logger.warning(f"[DD_STRATEGY_HALT] {stock_code} RS 차단: {_dd_strat_r}")
                                                if _rs_dd_ok:
                                                    self._daily_rs_count += 1
                                                    # 🔧 2026-04-03: 레짐 엔진 + Drawdown size 조정
                                                    _rs_base_sz = _rs_cfg.get('position_size_mult', 0.15)
                                                    _rs_sz, _rs_sz_r = self.regime_engine.get_rs_size_mult(_rs_base_sz)
                                                    if self.config.get("drawdown_engine", {}).get("enabled", True):
                                                        _dd_mult, _dd_r = self.drawdown_engine.get_size_mult(strategy="rs")
                                                        if _dd_mult < 1.0:
                                                            logger.info(f"[DD_SIZE] {stock_code}: {_dd_r}")
                                                            _rs_sz = max(_rs_sz * _dd_mult, self.config.get("regime_engine", {}).get("size_floor", 0.5) * 0.5)
                                                    logger.info(f"[RS_SIZE] {stock_code}: {_rs_sz_r}")
                                                    self.execute_buy(
                                                        stock_code=stock_code,
                                                        stock_name=stock_name,
                                                        price=current_price,
                                                        df=df,
                                                        position_size_mult=_rs_sz,
                                                        entry_confidence=0.65,
                                                        entry_reason=f"RS:{datetime.now().strftime('%H:%M')} {_rs_reason}",
                                                    )
                                            else:
                                                logger.debug(f"[RS_NO_SIG] {stock_code}: {_rs_reason}")
                                        except Exception as _rse:
                                            logger.debug(f"[RS_ERR] {stock_code}: {_rse}")
                                    else:
                                        logger.debug(f"[RS_TIME_BLOCK] {stock_code}: {_now_rs} 범위밖 {_rs_ts}~{_rs_te}")
                                else:
                                    logger.debug(f"[RS_REGIME_SKIP] {stock_code}: {_mkt_rs} (TRADE_OK 아님)")

                        # 🔧 2026-03-31: DEFENSIVE Mode (NO_TRADE_DAY 전용 과매도 반등)
                        _def_cfg = self.config.get('defensive_mode', {})
                        if _def_cfg.get('enabled', False):
                            _def_max = _def_cfg.get('max_per_day', 2)
                            if self._daily_defensive_count < _def_max and stock_code not in self.positions:
                                # 시장 악화(NO_TRADE_DAY)에서만 발동
                                _mc_status, _mc_reason, _ = self.market_context.evaluate()
                                if _mc_status == "NO_TRADE_DAY":
                                    # 코스닥 낙폭 필터 (패닉 낙하 차단)
                                    _mkt_floor = _def_cfg.get('market_pct_floor', -2.5)
                                    _mkt_pct = getattr(self.market_context, '_kosdaq_pct', None)
                                    if _mkt_pct is not None and _mkt_pct < _mkt_floor:
                                        logger.debug(f"[DEF_MARKET_BLOCK] {stock_code}: 코스닥 {_mkt_pct:.2f}% < {_mkt_floor}%")
                                    else:
                                        # 시간 필터
                                        _def_tf  = _def_cfg.get('time_filter', {})
                                        _def_ts  = _def_tf.get('start', '10:30')
                                        _def_te  = _def_tf.get('end', '13:30')
                                        _now_hm  = datetime.now().strftime('%H:%M')
                                        if _def_ts <= _now_hm <= _def_te:
                                            _def_sig, _def_reason = self._check_defensive_entry(df_5min, _def_cfg)
                                            if _def_sig:
                                                logger.info(f"[DEF_SIG] {stock_code} {stock_name}: {_def_reason}")
                                                console.print(f"[magenta]🛡️ [DEFENSIVE] {stock_name}: {_def_reason}[/magenta]")
                                                # 🔧 2026-04-04: 전략별 Drawdown halt 체크 (counter 증가 전)
                                                _def_dd_ok = True
                                                if self.config.get("drawdown_engine", {}).get("enabled", True):
                                                    _def_dd_ok, _dd_strat_r = self.drawdown_engine.can_enter(strategy="def")
                                                    if not _def_dd_ok:
                                                        logger.warning(f"[DD_STRATEGY_HALT] {stock_code} DEF 차단: {_dd_strat_r}")
                                                if _def_dd_ok:
                                                    self._daily_defensive_count += 1
                                                    # 🔧 2026-04-03: 레짐 엔진 + Drawdown size 조정
                                                    _def_base_sz = _def_cfg.get('position_size_mult', 0.3)
                                                    _def_sz, _def_sz_r = self.regime_engine.get_def_size_mult(_def_base_sz)
                                                    if self.config.get("drawdown_engine", {}).get("enabled", True):
                                                        _dd_mult, _dd_r = self.drawdown_engine.get_size_mult(strategy="def")
                                                        if _dd_mult < 1.0:
                                                            logger.info(f"[DD_SIZE] {stock_code}: {_dd_r}")
                                                            _def_sz = max(_def_sz * _dd_mult, self.config.get("regime_engine", {}).get("size_floor", 0.5) * 0.5)
                                                    logger.info(f"[DEF_SIZE] {stock_code}: {_def_sz_r}")
                                                    self.execute_buy(
                                                        stock_code=stock_code,
                                                        stock_name=stock_name,
                                                        price=current_price,
                                                        df=df,
                                                        position_size_mult=_def_sz,
                                                        entry_confidence=0.5,
                                                        entry_reason=f"DEFENSIVE:{datetime.now().strftime('%H:%M')} {_def_reason}",
                                                        stop_loss_pct_override=_def_cfg.get('stop_loss_pct', 0.8),
                                                        take_profit_pct=_def_cfg.get('take_profit_pct', 1.0),
                                                        max_hold_minutes=_def_cfg.get('max_hold_minutes', 10),
                                                    )
                                            else:
                                                logger.debug(f"[DEF_NO_SIG] {stock_code}: {_def_reason}")
                                        else:
                                            logger.debug(f"[DEF_TIME_BLOCK] {stock_code}: {_now_hm} 범위밖 {_def_ts}~{_def_te}")

                        # 🔧 2026-03-31: EXPLORATION Mode (2단계+3단계 탐색 진입)
                        # 조건: SMC_NO_SIG + Orchestrator ACCEPT + NOT NO_TRADE_DAY
                        # 2단계: RVOL≥min_rvol + breakout+volume → size×0.3
                        # 3단계: explore_always=true → 구조없음 탐색 → size×0.15
                        _expl_cfg = self.config.get('exploration', {})
                        if _expl_cfg.get('enabled', False) and not self._exploration_killed:
                            _expl_max = _expl_cfg.get('max_per_day', 2)
                            if self._daily_exploration_count < _expl_max and stock_code not in self.positions:
                                # NO_TRADE_DAY 상태 확인
                                try:
                                    _mc_status_e, _, _ = self.market_context.evaluate()
                                    _is_no_trade_day = (_mc_status_e == "NO_TRADE_DAY")
                                except Exception:
                                    _is_no_trade_day = True  # 실패 시 보수적으로 차단

                                _ignore_ntd = _expl_cfg.get('ignore_no_trade_day', False)
                                if not _is_no_trade_day or _ignore_ntd:
                                    if _is_no_trade_day and _ignore_ntd:
                                        logger.debug(f"[EXPLORATION_NTD_OVERRIDE] {stock_code}: NO_TRADE_DAY 무시 (ignore_no_trade_day=true)")
                                    # 시간 필터
                                    _expl_tf = _expl_cfg.get('time_filter', {})
                                    _expl_ts = _expl_tf.get('start', '10:30')
                                    _expl_te = _expl_tf.get('end', '13:00')
                                    _now_hm_e = datetime.now().strftime('%H:%M')

                                    if _expl_ts <= _now_hm_e <= _expl_te:
                                        logger.debug(f"[EXPLORATION_TRY] {stock_code} {stock_name}: 탐색 진입 체크")

                                        _expl_ok = False
                                        _expl_reason = ""
                                        _expl_size = _expl_cfg.get('position_size_mult', 0.3)
                                        _bo_win = _expl_cfg.get('breakout_window', 10)
                                        _min_rvol = _expl_cfg.get('min_rvol', 1.2)
                                        _confirm_cfg = _expl_cfg.get('confirm_entry', {})
                                        _confirm_enabled = _confirm_cfg.get('enabled', False)
                                        _imm_rvol = _confirm_cfg.get('immediate_rvol_threshold', 3.0)
                                        _max_pend_sec = _confirm_cfg.get('max_pending_seconds', 90)
                                        _tol_hard = _confirm_cfg.get('price_tolerance_hard', 0.002)
                                        _tol_soft = _confirm_cfg.get('price_tolerance_soft', 0.005)
                                        _early_rvol = _confirm_cfg.get('early_entry_rvol', 4.0)

                                        # ── 1봉 확인 대기 중인 신호 처리 ────────────────────
                                        if _confirm_enabled and stock_code in self._pending_exploration:
                                            _pend = self._pending_exploration[stock_code]
                                            _age = (datetime.now() - _pend['detected_at']).total_seconds()
                                            if _age > _max_pend_sec:
                                                del self._pending_exploration[stock_code]
                                                logger.info(f"[EXPL_PEND_EXPIRED] {stock_code}: {_age:.0f}s 초과 → 폐기")
                                            else:
                                                _bp = _pend['breakout_price']
                                                _cur_rvol = getattr(self, '_last_expl_rvol', 0.0)

                                                # ── 스냅샷 공통 계산 ──────────────────────────
                                                def _snap(entry_type):
                                                    try:
                                                        _vw = df_5min['vwap'].iloc[-1] if 'vwap' in df_5min.columns else 0
                                                        _vwap_dist = round((current_price - _vw) / _vw * 100, 2) if _vw else 0
                                                        _h0 = df_5min['high'].iloc[-1]
                                                        _h1 = df_5min['high'].iloc[-2] if len(df_5min) >= 2 else _h0
                                                        _vol_trend = "up" if df_5min['volume'].iloc[-1] > df_5min['volume'].iloc[-2] else "down"
                                                    except Exception:
                                                        _vwap_dist, _vol_trend = 0, "?"
                                                    logger.info(
                                                        f"[EXPL_SNAP] {stock_code} type={entry_type} "
                                                        f"rvol={_cur_rvol:.1f} "
                                                        f"price_vs_bp={round((current_price-_bp)/_bp*100,2)}% "
                                                        f"vwap_dist={_vwap_dist}% "
                                                        f"vol={_vol_trend}"
                                                    )

                                                # ① 조기 진입: RVOL 급증 + 가격 ≥ breakout + VWAP 위
                                                try:
                                                    _vwap_now = df_5min['vwap'].iloc[-1] if 'vwap' in df_5min.columns else 0
                                                    _early_price_ok = current_price >= _bp and (_vwap_now <= 0 or current_price > _vwap_now)
                                                except Exception:
                                                    _early_price_ok = current_price >= _bp
                                                if _cur_rvol >= _early_rvol and _early_price_ok:
                                                    _expl_ok = True
                                                    _expl_reason = _pend['reason'] + " [조기진입]"
                                                    _expl_size = _pend['size']
                                                    _expl_conf = _pend['conf']
                                                    del self._pending_exploration[stock_code]
                                                    _snap("EARLY")
                                                    logger.info(
                                                        f"[EXPL_PEND_EARLY] {stock_code}: "
                                                        f"RVOL={_cur_rvol:.1f}≥{_early_rvol} 가격유지 → 조기 진입"
                                                    )
                                                elif _cur_rvol >= _early_rvol and not _early_price_ok:
                                                    del self._pending_exploration[stock_code]
                                                    logger.info(
                                                        f"[EXPL_PEND_REJECT] {stock_code}: "
                                                        f"RVOL={_cur_rvol:.1f} 충족but 가격이탈/VWAP미달 → 폐기"
                                                    )
                                                # ② 강확인: 가격 -0.2% 이내
                                                elif current_price >= _bp * (1 - _tol_hard):
                                                    _expl_ok = True
                                                    _expl_reason = _pend['reason'] + " [강확인]"
                                                    _expl_size = _pend['size']
                                                    _expl_conf = _pend['conf']
                                                    del self._pending_exploration[stock_code]
                                                    _snap("CONFIRM")
                                                    logger.info(
                                                        f"[EXPL_PEND_CONFIRM] {stock_code}: "
                                                        f"강확인 {current_price:,}≥{_bp*(1-_tol_hard):,.0f} → 진입"
                                                    )
                                                # ③ 약확인: 가격 -0.5% 이내 + VWAP↑ + RVOL + HH방향
                                                elif current_price >= _bp * (1 - _tol_soft):
                                                    try:
                                                        _vwap_val = df_5min['vwap'].iloc[-1] if 'vwap' in df_5min.columns else 0
                                                        _vwap_ok = current_price > _vwap_val > 0
                                                        _rvol_ok = _cur_rvol >= _min_rvol
                                                        # 방향성: 최근 2봉 HH 유지
                                                        _hh_ok = (len(df_5min) >= 2 and
                                                                  df_5min['high'].iloc[-1] >= df_5min['high'].iloc[-2])
                                                    except Exception:
                                                        _vwap_ok, _rvol_ok, _hh_ok = False, False, False
                                                    if _vwap_ok and _rvol_ok and _hh_ok:
                                                        _expl_ok = True
                                                        _expl_reason = _pend['reason'] + " [약확인]"
                                                        _expl_size = _pend['size']
                                                        _expl_conf = _pend['conf']
                                                        del self._pending_exploration[stock_code]
                                                        _snap("SOFT")
                                                        logger.info(
                                                            f"[EXPL_PEND_SOFT] {stock_code}: "
                                                            f"약확인 가격{current_price:,} VWAP↑ RVOL={_cur_rvol:.1f} HH↑ → 진입"
                                                        )
                                                    else:
                                                        del self._pending_exploration[stock_code]
                                                        logger.info(
                                                            f"[EXPL_PEND_REJECT] {stock_code}: "
                                                            f"약확인 실패 VWAP={_vwap_ok} RVOL={_rvol_ok} HH={_hh_ok} → 폐기"
                                                        )
                                                else:
                                                    del self._pending_exploration[stock_code]
                                                    logger.info(
                                                        f"[EXPL_PEND_REJECT] {stock_code}: "
                                                        f"가격이탈 {current_price:,}<{_bp*(1-_tol_soft):,.0f} → 폐기"
                                                    )

                                        try:
                                            if len(df_5min) >= _bo_win + 2:
                                                _vol_ma_e = df_5min['volume'].iloc[-_bo_win-1:-1].mean()
                                                _last_vol_e = df_5min['volume'].iloc[-1]
                                                _rvol = (_last_vol_e / _vol_ma_e) if _vol_ma_e > 0 else 0.0
                                                self._last_expl_rvol = _rvol  # 즉시/pending 분기용

                                                # RVOL 사전 필터: 진짜 수급 있는 종목만
                                                if _rvol < _min_rvol:
                                                    logger.debug(
                                                        f"[EXPLORATION_SKIP_RVOL] {stock_code}: "
                                                        f"RVOL={_rvol:.2f} < {_min_rvol}"
                                                    )
                                                else:
                                                    # 2단계: breakout + volume 확인
                                                    _req_bo = _expl_cfg.get('require_breakout_signal', True)
                                                    if _req_bo:
                                                        _vol_thr = _expl_cfg.get('volume_mult', 1.5)
                                                        _prev_hi = df_5min['high'].iloc[-_bo_win-1:-1].max()
                                                        _last_cl = df_5min['close'].iloc[-1]
                                                        _prev_cl = df_5min['close'].iloc[-2] if len(df_5min) >= 2 else _last_cl
                                                        if _last_cl > _prev_hi and _rvol >= _vol_thr:
                                                            # ── 추가 필터 3개 ──────────────────────
                                                            # F1: 돌파 신선도 — 직전봉이 이미 _prev_hi 위면 "3봉 지난 돌파"
                                                            if _prev_cl > _prev_hi:
                                                                logger.debug(f"[EXPLORATION_STALE] {stock_code}: 직전봉 이미 돌파, 3봉+ 경과 추정")
                                                            # F2: 최근 3봉 급등 차단 (꼭대기 추격 방지)
                                                            elif len(df_5min) >= 4 and (
                                                                _last_cl / df_5min['close'].iloc[-4] - 1
                                                            ) > _expl_cfg.get('max_3bar_rise', 0.04):
                                                                logger.debug(f"[EXPLORATION_CHASE] {stock_code}: 3봉 급등 >{_expl_cfg.get('max_3bar_rise',0.04)*100:.0f}% 추격 차단")
                                                            else:
                                                                _bo_pct = (_last_cl - _prev_hi) / _prev_hi * 100
                                                                _expl_ok = True
                                                                _expl_reason = (
                                                                    f"돌파({_prev_hi:.0f}→{_last_cl:.0f} +{_bo_pct:.2f}%) "
                                                                    f"RVOL={_rvol:.1f}x"
                                                                )
                                                                # conf 점수 계산
                                                                _expl_conf = self._calc_entry_confidence(
                                                                    df_5min, rvol=_rvol, breakout_pct=_bo_pct
                                                                )
                                                                _conf_threshold = _expl_cfg.get('conf_threshold', 0.55)
                                                                if _expl_conf < _conf_threshold:
                                                                    logger.debug(
                                                                        f"[EXPLORATION_LOW_CONF] {stock_code}: "
                                                                        f"conf={_expl_conf:.2f} < {_conf_threshold} → 차단"
                                                                    )
                                                                    _expl_ok = False
                                                    else:
                                                        # 3단계: explore_always=true → RVOL만 통과하면 진입
                                                        if _expl_cfg.get('explore_always', False):
                                                            _expl_conf = self._calc_entry_confidence(df_5min, rvol=_rvol)
                                                            _expl_ok = True
                                                            _expl_size = _expl_cfg.get('no_signal_size_mult', 0.15)
                                                            _expl_reason = f"탐색진입(RVOL={_rvol:.1f}x conf={_expl_conf:.2f})"

                                        except Exception as _expl_e2:
                                            logger.debug(f"[EXPLORATION_ERR] {stock_code}: {_expl_e2}")

                                        if _expl_ok:
                                            # ── 1봉 확인 모드: 이미 pending 확인된 건은 즉시 진입
                                            # 새 신호는 RVOL 기준으로 즉시 or pending 분기
                                            _is_confirmed = "[1봉확인]" in _expl_reason
                                            _rvol_for_imm = getattr(self, '_last_expl_rvol', 0.0)
                                            _do_immediate = (
                                                not _confirm_enabled
                                                or _is_confirmed
                                                or _rvol_for_imm >= _imm_rvol
                                            )

                                            if _confirm_enabled and not _is_confirmed and not _do_immediate:
                                                # pending 등록 (1봉 후 재확인)
                                                self._pending_exploration[stock_code] = {
                                                    'breakout_price': current_price,
                                                    'rvol':           _rvol_for_imm,
                                                    'reason':         _expl_reason,
                                                    'size':           _expl_size,
                                                    'conf':           _expl_conf,
                                                    'detected_at':    datetime.now(),
                                                }
                                                logger.info(
                                                    f"[EXPL_PEND] {stock_code} {stock_name}: "
                                                    f"RVOL={_rvol_for_imm:.1f}<{_imm_rvol} → 1봉 대기 "
                                                    f"(가격:{current_price:,})"
                                                )
                                                console.print(
                                                    f"[dim yellow]⏳ [EXPLORATION] {stock_name}: "
                                                    f"1봉 확인 대기 (RVOL={_rvol_for_imm:.1f}x)[/dim yellow]"
                                                )
                                            else:
                                                _imm_tag = "즉시" if not _is_confirmed else "확인"
                                                logger.info(
                                                    f"[EXPLORATION_ENTRY] {stock_code} {stock_name}: "
                                                    f"{_expl_reason} conf={_expl_conf:.2f} | size={_expl_size} [{_imm_tag}진입]"
                                                )
                                                console.print(
                                                    f"[yellow]🔍 [EXPLORATION] {stock_name}: "
                                                    f"{_expl_reason} conf={_expl_conf:.2f} size={_expl_size}[/yellow]"
                                                )
                                                _expl_pos_before = len(self.positions)
                                                # EXPLORATION entry type 결정 (ML 피처용)
                                                _expl_entry_type = "IMMEDIATE"
                                                if "[강확인]" in _expl_reason:
                                                    _expl_entry_type = "CONFIRM"
                                                elif "[약확인]" in _expl_reason:
                                                    _expl_entry_type = "SOFT"
                                                elif "[조기진입]" in _expl_reason:
                                                    _expl_entry_type = "EARLY"
                                                self.execute_buy(
                                                    stock_code=stock_code,
                                                    stock_name=stock_name,
                                                    price=current_price,
                                                    df=df,
                                                    position_size_mult=_expl_size,
                                                    entry_confidence=_expl_conf,
                                                    entry_reason=f"EXPLORATION:{datetime.now().strftime('%H:%M')} {_expl_reason}",
                                                    stop_loss_pct_override=_expl_cfg.get('stop_loss_pct', 1.0),
                                                )
                                                # 포지션에 ML 피처 저장
                                                if stock_code in self.positions:
                                                    try:
                                                        _vwap_now = df_5min['vwap'].iloc[-1] if 'vwap' in df_5min.columns else 0
                                                        _vd = (current_price - _vwap_now) / _vwap_now * 100 if _vwap_now else 0
                                                        self.positions[stock_code]['ml_features'] = {
                                                            'strategy': 'EXPLORATION',
                                                            'entry_type': _expl_entry_type,
                                                            'rvol': round(_rvol_for_imm, 2),
                                                            'price_vs_breakout': round(_bo_pct if '_bo_pct' in dir() else 0, 3),
                                                            'pending_duration_sec': None,
                                                            'vwap_distance': round(_vd, 3),
                                                        }
                                                    except Exception:
                                                        pass
                                                if stock_code in self.positions or len(self.positions) > _expl_pos_before:
                                                    self._daily_exploration_count += 1
                                                    logger.info(f"[EXPLORATION_COUNT] {stock_code}: {self._daily_exploration_count}/{_expl_max}")
                                                else:
                                                    logger.debug(f"[EXPLORATION_BLOCKED] {stock_code}: execute_buy 내부 차단 → 카운터 유지 ({self._daily_exploration_count})")
                                        else:
                                            logger.debug(f"[EXPLORATION_NO_SIG] {stock_code}: 돌파/RVOL 조건 미충족")
                                    else:
                                        logger.debug(f"[EXPLORATION_TIME_BLOCK] {stock_code}: {_now_hm_e} 범위밖 {_expl_ts}~{_expl_te}")
                                else:
                                    logger.debug(f"[EXPLORATION_NO_TRADE_BLOCK] {stock_code}: NO_TRADE_DAY → 탐색 차단")

                        # 🔥 2026-03-26: EXPERIMENT 이중 차단 (YAML enabled:false → 즉시 return)
                        _exp_cfg_top = self.config.get('experiment', {})
                        if not _exp_cfg_top.get('enabled', False):
                            return  # EXPERIMENT 전면 차단 — 설정과 코드 모두 막음
                        _fea = _exp_cfg_top.get('forced_entry_on_accept', {})
                        if _fea.get('enabled', False):
                            _fea_max  = _fea.get('max_per_day', 2)
                            _fea_size = _fea.get('size_mult', 0.15)
                            if not hasattr(self, '_daily_forced_entry_count'):
                                self._daily_forced_entry_count = 0
                            # 🔧 2026-03-26: 10:00 이전 Opening Noise 구간 EXPERIMENT 차단
                            # (이전에는 09:00~09:41 재시작 시 max_per_day 소진 버그 있었음)
                            _exp_min = time_class(10, 0, 0)
                            if current_time < _exp_min:
                                logger.debug(f"[EXPERIMENT_NOISE_BLOCK] {stock_code}: {current_time.strftime('%H:%M')} < 10:00")
                            elif self._daily_forced_entry_count < _fea_max:
                                _fea_conf = 0.5  # 실험 진입은 고정 신뢰도
                                logger.info(
                                    f"[EXPERIMENT] {stock_code} {stock_name}: "
                                    f"SMC 신호 없음→강제진입 | size={_fea_size} "
                                    f"| {reason[:40]}"
                                )
                                console.print(
                                    f"[magenta]🧪 [EXPERIMENT] {stock_name}: "
                                    f"강제진입(SMC 없음) size={_fea_size}[/magenta]"
                                )
                                self._daily_forced_entry_count += 1
                                self.execute_buy(
                                    stock_code=stock_code,
                                    stock_name=stock_name,
                                    price=current_price,
                                    df=df,
                                    position_size_mult=_fea_size,
                                    entry_confidence=_fea_conf,
                                    entry_reason=f"EXPERIMENT:{datetime.now().strftime('%H:%M')}",
                                )

                        return
                    else:
                        logger.info(f"[SMC_SIG] {stock_code} {stock_name}: {reason}")

                    # 방향 확인 (롱온리 전략의 경우)
                    direction = details.get('direction', 'none')
                    if direction != 'long':
                        logger.debug(f"[SHORT_IGNORE] {stock_code}")
                        return

                    # 🔧 2026-01-23: 신뢰도 + CHoCH 등급 기반 포지션 크기
                    entry_confidence = details.get('confidence', 0.7)
                    weight_multiplier = details.get('weight_multiplier', 1.0)  # 등급별 비중

                    # 신뢰도 기반 배율
                    confidence_mult = 0.8 if entry_confidence < 0.8 else 1.0

                    # 최종 포지션 크기 = 신뢰도 배율 × 등급 배율
                    position_size_mult = confidence_mult * weight_multiplier

                    # 🔧 2026-03-20: Sweep Fallback — OB 있지만 sweep 없는 경우 추가 축소
                    if details.get('sweep_fallback'):
                        _smc_cfg_fb = self.config.get('smc', {})
                        _max_fb = _smc_cfg_fb.get('max_fallback_per_day', 3)
                        _is_c_fallback = details.get('grade_c_fallback', False)

                        # 가드 1: 일일 fallback 한도 (B/C 통합 + C 전용)
                        if self._daily_fallback_count >= _max_fb:
                            logger.info(f"[FALLBACK_LIMIT] {stock_code} | 일일 한도 도달 ({self._daily_fallback_count}/{_max_fb})")
                            return
                        if _is_c_fallback:
                            _max_c_fb = _smc_cfg_fb.get('max_c_fallback_per_day', 2)
                            if self._daily_c_fallback_count >= _max_c_fb:
                                logger.info(f"[C_FALLBACK_LIMIT] {stock_code} | C급 일일 한도 ({self._daily_c_fallback_count}/{_max_c_fb})")
                                return

                        # 가드 1b: C급 쿨다운 (연속 진입 방지)
                        if _is_c_fallback and self._last_c_fallback_time is not None:
                            _c_cd_min = _smc_cfg_fb.get('c_fallback_cooldown_min', 15)
                            _elapsed = (datetime.now() - self._last_c_fallback_time).total_seconds() / 60
                            if _elapsed < _c_cd_min:
                                logger.info(f"[C_FALLBACK_CD] {stock_code} | 쿨다운 {_elapsed:.0f}분 < {_c_cd_min}분")
                                return

                        # 가드 2: 거래량 스파이크 확인 (최근 봉 >= 20봉 평균 × 1.5)
                        try:
                            _df_fb = df.copy()
                            _df_fb.columns = [c.lower() for c in _df_fb.columns]
                            _vol_avg = _df_fb['volume'].iloc[-21:-1].mean()
                            _vol_cur = _df_fb['volume'].iloc[-2]
                            _vol_ok = (_vol_avg > 0) and (_vol_cur >= _vol_avg * 1.5)
                        except Exception:
                            _vol_ok = True  # 데이터 부족 시 통과

                        if not _vol_ok:
                            logger.info(f"[FALLBACK_VOL] {stock_code} | 거래량 스파이크 없음 → 차단")
                            return

                        # 가드 2b: C급 — reclaim 필수 (최소 1개 품질 조건)
                        if _is_c_fallback:
                            _reclaim_ok = details.get('prefilter', {}).get('reclaim_detected', False)
                            if not _reclaim_ok:
                                logger.info(f"[C_FALLBACK_RECLAIM] {stock_code} | reclaim 없음 → 차단 (C급 최소 조건)")
                                return

                        # 가드 3: HTF 추세 정렬
                        # 🔧 2026-03-20: C급 fallback은 HTF 없어도 통과 (HTF 없음 = C급 이유이므로)
                        _htf_alive = details.get('htf_trend_alive', True)
                        if not _htf_alive and not _is_c_fallback:
                            logger.info(f"[FALLBACK_HTF] {stock_code} | HTF 추세 불일치 → 차단")
                            return

                        _fb_mult = details.get('sweep_fallback_size_mult', 0.5)
                        position_size_mult *= _fb_mult

                        # 🔧 2026-03-20: C급 fallback 추가 size 감소 + 카운터
                        if _is_c_fallback:
                            _c_mult = _smc_cfg_fb.get('grade_c_fallback_size_mult', 0.6)
                            position_size_mult *= _c_mult
                            self._daily_c_fallback_count += 1
                            self._last_c_fallback_time = datetime.now()
                            logger.info(
                                f"[C_FALLBACK_SIZE] {stock_code} | ×{_c_mult} "
                                f"(htf={_htf_alive}, reclaim=True, c_daily={self._daily_c_fallback_count})"
                            )

                        self._daily_fallback_count += 1
                        _fallback_tag = "[C_GRADE_FALLBACK]" if _is_c_fallback else "[SWEEP_FALLBACK]"
                        logger.info(
                            f"{_fallback_tag} {stock_code} | CHoCH+OB | "
                            f"size_mult={position_size_mult:.2f} | vol_ok={_vol_ok} | htf={_htf_alive} | "
                            f"daily={self._daily_fallback_count}/{_max_fb}"
                            + (f" | c_daily={self._daily_c_fallback_count}" if _is_c_fallback else "")
                        )
                        console.print(
                            f"[yellow]⚡ {_fallback_tag} {stock_name}: CHoCH+OB 진입 "
                            f"({'C급 reclaim✓' if _is_c_fallback else 'sweep없음'}, "
                            f"size×{position_size_mult:.2f}, {self._daily_fallback_count}/{_max_fb})[/yellow]"
                        )

                    # 등급 정보 로깅
                    choch_grade_info = details.get('choch_grade', {})
                    choch_grade = choch_grade_info.get('grade', 'B')
                    if weight_multiplier < 1.0:
                        logger.debug(f"[GRADE_WEIGHT] {stock_code} {choch_grade}급 {weight_multiplier*100:.0f}%")

                    # ── 2026-04-01: A+ 분류 + A급 품질 필터 ─────────────────────
                    _aplus_cfg  = self.config.get('smc.choch_grade.grade_a_plus',  {})
                    _afilt_cfg  = self.config.get('smc.choch_grade.grade_a_filter', {})
                    _has_sweep  = bool(details.get('has_sweep', False))
                    _is_a_plus  = False

                    # RVOL + 3봉 수익률 계산 (5분봉 기준)
                    _rvol_ag      = 0.0
                    _3bar_rise_ag = 0.0
                    try:
                        _df_ag = df.copy()
                        _df_ag.columns = [c.lower() for c in _df_ag.columns]
                        if 'volume' in _df_ag.columns and len(_df_ag) >= 21:
                            _vol_avg_ag = _df_ag['volume'].iloc[-21:-1].mean()
                            _vol_cur_ag = _df_ag['volume'].iloc[-1]
                            if _vol_avg_ag > 0:
                                _rvol_ag = _vol_cur_ag / _vol_avg_ag
                        if 'close' in _df_ag.columns and len(_df_ag) >= 4:
                            _c_now = float(_df_ag['close'].iloc[-1])
                            _c_3b  = float(_df_ag['close'].iloc[-4])
                            if _c_3b > 0:
                                _3bar_rise_ag = (_c_now / _c_3b) - 1.0
                    except Exception:
                        pass

                    if choch_grade == 'A' and _aplus_cfg.get('enabled', True):
                        _ap_conf_thr  = _aplus_cfg.get('conf_threshold', 0.75)
                        _ap_rvol_thr  = _aplus_cfg.get('min_rvol', 1.8)
                        _ap_sweep_req = _aplus_cfg.get('require_sweep', True)
                        _is_a_plus = (
                            entry_confidence >= _ap_conf_thr
                            and _rvol_ag >= _ap_rvol_thr
                            and (not _ap_sweep_req or _has_sweep)
                        )
                        if _is_a_plus:
                            # ── A+ 가드 1: 최근 3봉 급등 → 꼭대기 물림 방지 ──────
                            _ap_max_rise = _aplus_cfg.get('max_3bar_rise', 0.04)
                            if _3bar_rise_ag > _ap_max_rise:
                                _is_a_plus = False
                                logger.info(
                                    f"[A_PLUS_REJECT_OVEREXTENDED] {stock_code} | "
                                    f"3봉수익={_3bar_rise_ag*100:.1f}% > {_ap_max_rise*100:.0f}% → A로 강등"
                                )
                                console.print(
                                    f"[yellow]⚠️ [A+→A] {stock_name}: "
                                    f"3봉급등 {_3bar_rise_ag*100:.1f}% → 꼭대기 물림 방지, A급 처리[/yellow]"
                                )

                        if _is_a_plus and _aplus_cfg.get('require_bull_market', True):
                            # ── A+ 가드 2: 시장 레짐 TREND 아니면 강등 ──────────
                            try:
                                _ap_regime, _ap_regime_reason = self.market_context.get_regime()
                            except Exception:
                                _ap_regime = "NEUTRAL"
                                _ap_regime_reason = "오류"
                            if _ap_regime != "TREND":
                                _is_a_plus = False
                                logger.info(
                                    f"[A_PLUS_REJECT_MARKET] {stock_code} | "
                                    f"regime={_ap_regime} ({_ap_regime_reason}) → A로 강등"
                                )
                                console.print(
                                    f"[yellow]⚠️ [A+→A] {stock_name}: "
                                    f"시장 {_ap_regime} (TREND 아님) → A급 처리[/yellow]"
                                )

                        if _is_a_plus:
                            # ── A+ 쿨다운: RVOL 기반 동적 조정 ──────────────────
                            # 시장 속도(RVOL)에 따라 쿨다운 길이 조절
                            _ap_cd_default  = _aplus_cfg.get('cooldown_min', 15)
                            _ap_cd_high_vol = _aplus_cfg.get('cooldown_min_high_vol', 10)  # RVOL≥2.0
                            _ap_cd_low_vol  = _aplus_cfg.get('cooldown_min_low_vol', 25)   # RVOL≤1.2
                            if _rvol_ag >= 2.0:
                                _ap_cooldown_min = _ap_cd_high_vol
                            elif _rvol_ag <= 1.2:
                                _ap_cooldown_min = _ap_cd_low_vol
                            else:
                                _ap_cooldown_min = _ap_cd_default

                            if self._last_a_plus_time is not None:
                                _ap_elapsed = (datetime.now() - self._last_a_plus_time).total_seconds() / 60
                                if _ap_elapsed < _ap_cooldown_min:
                                    _is_a_plus = False
                                    logger.info(
                                        f"[A_PLUS_COOLDOWN] {stock_code} | "
                                        f"직전 A+ 후 {_ap_elapsed:.0f}분 < {_ap_cooldown_min}분 "
                                        f"(rvol={_rvol_ag:.1f}x) → A로 강등"
                                    )
                                    console.print(
                                        f"[yellow]⚠️ [A+→A] {stock_name}: "
                                        f"쿨다운 {_ap_elapsed:.0f}/{_ap_cooldown_min}분 (RVOL={_rvol_ag:.1f}x)[/yellow]"
                                    )

                        if _is_a_plus:
                            # ── A+ 일일 한도 초과 → A로 강등 ────────────────────
                            _ap_max_per_day = _aplus_cfg.get('max_per_day', 2)
                            if self._daily_a_plus_count >= _ap_max_per_day:
                                _is_a_plus = False
                                logger.info(
                                    f"[A_PLUS_DAILY_LIMIT] {stock_code} | "
                                    f"일일 A+ 한도 ({self._daily_a_plus_count}/{_ap_max_per_day}) → A로 강등"
                                )
                                console.print(
                                    f"[yellow]⚠️ [A+→A] {stock_name}: "
                                    f"A+ 일일 한도 {_ap_max_per_day}회 → 과열 방지, A급 처리[/yellow]"
                                )

                        if _is_a_plus:
                            choch_grade = 'A+'
                            position_size_mult = 1.0   # Tier 1: Full Size
                            # 카운터는 execute_buy 성공 후 증가 (entry_reason A+ prefix로 판단)
                            logger.info(
                                f"[A_PLUS] {stock_code} | conf={entry_confidence:.2f} "
                                f"rvol={_rvol_ag:.1f}x sweep={_has_sweep} "
                                f"3bar={_3bar_rise_ag*100:.1f}% → A+ 1.0R "
                                f"(today={self._daily_a_plus_count}/{_ap_max_per_day})"
                            )
                            console.print(
                                f"[bold green]🔥 [A+] {stock_name}: "
                                f"conf={entry_confidence:.2f} RVOL={_rvol_ag:.1f}x → 1.0R FULL SIZE[/bold green]"
                            )
                        else:
                            # A급 품질 필터 → A / A- 분리
                            _a_min_rvol = _afilt_cfg.get('min_rvol', 1.5)
                            if _rvol_ag > 0 and _rvol_ag < _a_min_rvol:
                                logger.info(
                                    f"[A_DOWNGRADE] {stock_code} | RVOL={_rvol_ag:.1f}x < {_a_min_rvol} → B급 강등"
                                )
                                choch_grade = 'B'
                            elif not _has_sweep:
                                # sweep 없는 A급 → A- (0.3R, skip 아님, 흐름 유지)
                                _no_sweep_mult = _afilt_cfg.get('no_sweep_size_mult', 0.3)
                                position_size_mult = _no_sweep_mult
                                choch_grade = 'A-'
                                logger.info(
                                    f"[A_MINUS] {stock_code} | sweep없음 → A- {_no_sweep_mult*100:.0f}%"
                                )
                            else:
                                # A / A- 분리: conf + RVOL 기준
                                _a_tier_conf  = _afilt_cfg.get('tier_a_conf_threshold', 0.70)
                                _a_tier_rvol  = _afilt_cfg.get('tier_a_rvol_threshold', 1.5)
                                _is_real_a = (entry_confidence >= _a_tier_conf and _rvol_ag >= _a_tier_rvol)
                                if _is_real_a:
                                    position_size_mult = _afilt_cfg.get('tier_a_size_mult', 0.5)
                                    logger.info(
                                        f"[A_TIER2] {stock_code} | conf={entry_confidence:.2f} "
                                        f"rvol={_rvol_ag:.1f}x → A 0.5R"
                                    )
                                else:
                                    position_size_mult = _afilt_cfg.get('no_sweep_size_mult', 0.3)
                                    choch_grade = 'A-'
                                    logger.info(
                                        f"[A_MINUS] {stock_code} | conf={entry_confidence:.2f} "
                                        f"rvol={_rvol_ag:.1f}x (기준 미달) → A- 0.3R"
                                    )

                    # ── A급이지만 수급 절대값 낮거나 변화율 급락 → B급 강등 ──────────
                    if choch_grade in ('A', 'A-') and not _is_a_plus:
                        _sd_for_grade = float(
                            self.validated_stocks.get(stock_code, {})
                            .get('analysis', {}).get('scores', {}).get('supply_demand', 50)
                        )
                        _afilt_cfg_g  = self.config.get('smc.choch_grade.grade_a_filter', {})
                        _grade_sd_min = float(_afilt_cfg_g.get('min_supply_demand_for_a', 50))
                        _grade_sd_drop_thr = float(_afilt_cfg_g.get('downgrade_sd_drop', -10))

                        # ① 절대값 낮음
                        _downgrade = _sd_for_grade < _grade_sd_min
                        # ② 변화율 급락: sd_score_cache에서 전일 대비 낙폭 체크
                        if not _downgrade:
                            try:
                                import json as _gj
                                _gc_p = BASE_DIR / 'data' / 'sd_score_cache.json'
                                if _gc_p.exists():
                                    _gc = _gj.loads(_gc_p.read_text())
                                    _gce = _gc.get(stock_code, {})
                                    _gprev = _gce.get('prev_score')
                                    if _gprev is not None:
                                        _sd_drop_g = _sd_for_grade - float(_gprev)
                                        if _sd_drop_g <= _grade_sd_drop_thr:
                                            _downgrade = True
                                            logger.info(
                                                f"[A_SD_DOWNGRADE] {stock_code} | 수급 변화율 {_sd_drop_g:+.0f} "
                                                f"<= {_grade_sd_drop_thr} → B급 강등"
                                            )
                            except Exception:
                                pass

                        if _downgrade:
                            logger.info(
                                f"[A_SD_DOWNGRADE] {stock_code} | {choch_grade}급 + 수급{_sd_for_grade:.0f} "
                                f"(절대값<{_grade_sd_min} 또는 변화율 급락) → B급 강등 (수급 우선)"
                            )
                            choch_grade = 'B'

                    # B급 Tier 3: 0.2R (흐름 유지 + 데이터 축적)
                    if choch_grade == 'B':
                        _b_tier_mult = _afilt_cfg.get('tier_b_size_mult', 0.2)
                        position_size_mult = _b_tier_mult
                        logger.info(f"[B_TIER3] {stock_code} | B급 Tier3 → {_b_tier_mult*100:.0f}% size")
                    # ─────────────────────────────────────────────────────────────

                    # 🔧 2026-02-19: B급 CHoCH 시간 제한
                    if choch_grade == 'B':
                        grade_b_cutoff_str = self.config.get('smc.choch_grade.grade_b_cutoff', '11:00')
                        h_cut, m_cut = map(int, grade_b_cutoff_str.split(':'))
                        from datetime import time as time_class
                        if datetime.now().time() >= time_class(h_cut, m_cut, 0):
                            logger.debug(f"[B_CUTOFF] {stock_code} {grade_b_cutoff_str} 이후 차단")
                            return

                    # 🔧 2026-02-26: HTF❌ + B급 CHoCH → 진입 금지 (관찰 전환)
                    if choch_grade == 'B' and self.config.get('smc.choch_grade.htf_b_block', True):
                        htf_alive = details.get('htf_trend_alive', True)
                        if not htf_alive:
                            logger.debug(f"[HTF_B_BLOCK] {stock_code}")
                            logger.info(f"[HTF_B_BLOCK] {stock_code} {stock_name}: B급 CHoCH + HTF 미정렬 → 진입 차단")
                            self._record_blocked_entry(stock_code, stock_name, "HTF_B_BLOCK", "B급 CHoCH + HTF 미정렬", "SMC")
                            return

                    # 🔧 2026-02-06: 구조 기반 손절가 저장
                    structure_stop_price = details.get('structure_stop_price')
                    if structure_stop_price:
                        console.print(f"[cyan]📍 구조 손절가: {structure_stop_price:,.0f}원[/cyan]")

                    # 진입 이유 생성 (A+ → prefix로 execute_buy bypass 전달)
                    choch_info = details.get('choch', {})
                    _reason_base = f"{datetime.now().strftime('%H:%M')} SMC {reason}"
                    entry_reason = f"A+:{_reason_base}" if _is_a_plus else _reason_base

                except Exception as e:
                    console.print(f"[red]❌ {stock_name}: SMC 전략 처리 실패 - {e}[/red]")
                    import traceback
                    traceback.print_exc()
                    return

            elif entry_mode == "legacy_only":
                # ========================================
                # 모드 2: 기존 필터만 사용 (스퀴즈 무시)
                # ========================================
                console.print(f"[cyan]📊 진입 모드: 기존 필터 (L0-L6)[/cyan]")

                signal_result = self.signal_orchestrator.evaluate_signal(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    current_price=current_price,
                    df=df,
                    market=market,
                    current_cash=self.current_cash,
                    daily_pnl=self.calculate_daily_pnl()
                )

                if not signal_result['allowed']:
                    level = signal_result['rejection_level']
                    reason = signal_result['rejection_reason']
                    logger.debug(f"[LEVEL_BLOCK] {stock_code}: {level} - {reason[:40]}")
                    return

                entry_confidence = signal_result['confidence']
                position_size_mult = signal_result['position_size_multiplier']

            else:  # hybrid (기본값)
                # ========================================
                # 모드 3: 하이브리드 (기존 필터 + 스퀴즈)
                # ========================================
                console.print(f"[cyan]📊 진입 모드: 하이브리드 (기존 + 스퀴즈)[/cyan]")

                # 기존 필터 체크
                signal_result = self.signal_orchestrator.evaluate_signal(
                    stock_code=stock_code,
                    stock_name=stock_name,
                    current_price=current_price,
                    df=df,
                    market=market,
                    current_cash=self.current_cash,
                    daily_pnl=self.calculate_daily_pnl()
                )

                if not signal_result['allowed']:
                    level = signal_result['rejection_level']
                    reason = signal_result['rejection_reason']
                    logger.debug(f"[LEVEL_BLOCK] {stock_code}: {level} - {reason[:40]}")
                    return

                # 추가로 스퀴즈 모멘텀 체크
                if squeeze_config.get('enabled', False) and squeeze_config.get('entry_filter', {}).get('enabled', False):
                    from utils.squeeze_momentum_realtime import check_squeeze_momentum_filter

                    sqz_passed, sqz_reason, sqz_details = check_squeeze_momentum_filter(df, for_entry=True)

                    if not sqz_passed:
                        logger.debug(f"[SQZ_BLOCK] {stock_code}: {sqz_reason}")
                        console.print(f"[dim]  색상: {sqz_details.get('color', 'N/A')}, 모멘텀: {sqz_details.get('momentum', 0):.2f}[/dim]")
                        return
                    else:
                        console.print(f"[green]✅ {stock_name}: Squeeze 통과 - {sqz_reason}[/green]")

                entry_confidence = signal_result['confidence']
                position_size_mult = signal_result['position_size_multiplier']

            # 4. 매수 실행 (Phase 1: Confidence-based)
            console.print(f"[green]✅ {stock_name} ({stock_code}): 매수 시그널 발생![/green]")
            console.print(f"  신뢰도: {entry_confidence*100:.0f}%, 포지션 조정: {position_size_mult*100:.0f}%")

            # entry_reason이 설정되지 않은 모드는 기본값 사용
            if entry_reason is None:
                entry_reason = f"{datetime.now().strftime('%H:%M')} {entry_mode} 모드 진입"

            # 🔧 2026-03-08: SMC OB Pullback Entry — 즉시 매수 대신 OB zone 대기
            # CHoCH 신호 확인 후 OB(Order Block) 레벨까지 pullback 대기 → 반응 확인 후 진입
            # 데이터 근거: 10~30분 승률 7.2% (CHoCH 직후 추격) vs 30~60분 승률 61.5% (OB retest)
            if entry_mode == 'smc':
                ob_cfg = self.config.get('smc.ob_pullback_entry', {})
                ob_info = details.get('order_block') if details else None
                if ob_cfg.get('enabled', True) and ob_info and stock_code not in self.smc_pending:
                    # OB 정보 저장 후 대기 (execute_buy 미호출)
                    self.smc_pending[stock_code] = {
                        'ob_high':           ob_info['high'],
                        'ob_low':            ob_info['low'],
                        'choch_price':       details.get('choch', {}).get('price', current_price),
                        'detected_at':       datetime.now(),
                        'grade':             choch_grade,
                        'position_size_mult': position_size_mult,
                        'confidence':        entry_confidence,
                    }
                    console.print(
                        f"[cyan]⏳ [SMC_PENDING] {stock_name}: {choch_grade}급 CHoCH 확인, "
                        f"OB({ob_info['low']:,.0f}~{ob_info['high']:,.0f}) pullback 대기[/cyan]"
                    )
                    logger.info(
                        f"[SMC_PENDING] {stock_code} {stock_name}: {choch_grade}급, "
                        f"OB({ob_info['low']:.0f}~{ob_info['high']:.0f}), "
                        f"CHoCH={details.get('choch',{}).get('price',0):.0f}"
                    )
                    return  # execute_buy 호출 안 함 (OB 대기)
                # OB 없거나 이미 pending 중이면 기존 즉시 진입

            # 🔧 2026-04-06: 신호 품질 메타 — capture_entry 에서 signal_meta JSONB로 저장
            _cm_rpt = self.reentry_metrics.generate_report()
            self._pending_signal_meta = {
                "choch_grade":   choch_grade if 'choch_grade' in dir() else None,
                "htf_bias":      (details.get('mtf_bias', {}).get('is_uptrend') if details else None),
                "sweep":         bool(details.get('liquidity_sweep')) if details else None,
                "guard_state":   (
                    "lsg"          if _cm_rpt.get("loss_streak_guard_active") else
                    "conservative" if _cm_rpt.get("conservative_mode_active") else
                    "normal"
                ),
                "conf":          round(entry_confidence, 3),
                "size_mult":     round(position_size_mult, 3),
            }

            # 신호 큐에 추가 (detect→execute 분리)
            self._emit_signal(stock_code, stock_name, current_price, df, position_size_mult, entry_confidence, entry_reason, stop_loss=structure_stop_price, strategy=entry_mode.upper())

            # 🔧 2026-03-18: SMC ENTRY 결정 로그
            if entry_mode == "smc" and stock_code in self.positions:
                try:
                    from analyzers.smc.smc_decision_logger import get_smc_logger as _gsmc
                    _ob = self.smc_pending.get(stock_code, {})
                    _gsmc().log_entry(
                        stock_code, stock_name, current_price,
                        ob_low=_ob.get('ob_low', 0), ob_high=_ob.get('ob_high', 0),
                        grade=choch_grade if 'choch_grade' in dir() else '',
                    )
                except Exception:
                    pass

            # 🔧 2026-02-06: SMC 진입 시 추가 정보를 포지션에 저장
            if entry_mode == "smc" and stock_code in self.positions:
                try:
                    # 구조 기반 손절가 저장 (structure_stop_price는 SMC 분기에서 details로부터 추출됨)
                    if structure_stop_price is not None:
                        self.positions[stock_code]['structure_stop_price'] = structure_stop_price
                        console.print(f"[cyan]📍 {stock_name}: 구조 손절가 {structure_stop_price:,.0f}원 저장[/cyan]")

                    # HTF 추세 일치 여부 저장 (조건부 보유 시간 연장 + 오버나이트 허용 판단용)
                    mtf_bias_info = details.get('mtf_bias', {})
                    self.positions[stock_code]['htf_trend_aligned'] = mtf_bias_info.get('is_uptrend', False)
                    # 🔧 2026-04-17: htf_trend_alive 저장 (오버나이트 3조건 중 하나)
                    self.positions[stock_code]['htf_trend_alive'] = details.get('htf_trend_alive', False)
                    self.positions[stock_code]['direction'] = 'long'

                    # 🔧 2026-02-15: CHoCH 등급 저장 (오버나이트 강제 청산 판단용)
                    self.positions[stock_code]['choch_grade'] = choch_grade  # 'A' or 'B'

                    # 🔧 2026-02-07: 진입 시 ATR 저장 (Early Failure Structure 필터용)
                    if 'atr' in df_5min.columns and len(df_5min) > 0:
                        self.positions[stock_code]['atr_at_entry'] = float(df_5min['atr'].iloc[-1])

                    # 🔧 2026-05-01: R-기반 TP 계산 (SL=1R, TP1=2R/25%, TP2=4R/25%, 잔여50%=trailing)
                    if structure_stop_price is not None and current_price > structure_stop_price:
                        _r_tp_cfg = self.config.get('smc.r_tp', {})
                        _tp1_mult = _r_tp_cfg.get('tp1_r_mult', 1.5)
                        _tp2_mult = _r_tp_cfg.get('tp2_r_mult', 3.0)
                        _r_pct = (current_price - structure_stop_price) / current_price * 100
                        _r_tp1 = current_price * (1 + _tp1_mult * _r_pct / 100)
                        _r_tp2 = current_price * (1 + _tp2_mult * _r_pct / 100)
                        self.positions[stock_code]['r_pct'] = round(_r_pct, 3)
                        self.positions[stock_code]['r_tp1_price'] = round(_r_tp1)
                        self.positions[stock_code]['r_tp2_price'] = round(_r_tp2)

                    # 🔧 2026-04-16: ENTRY QUALITY 로그 (EMA9 눌림 메트릭)
                    _eq = details.get('ema9_wait', {})
                    if _eq.get('passed'):
                        _r_pct_log   = self.positions[stock_code].get('r_pct', 0)
                        _tp1_log     = self.positions[stock_code].get('r_tp1_price', 0)
                        _tp2_log     = self.positions[stock_code].get('r_tp2_price', 0)
                        _atr_dist    = _eq.get('atr_distance', 999)
                        _depth       = _eq.get('depth_ratio', 0)
                        _vol_r       = _eq.get('vol_ratio', 999)
                        if _atr_dist <= 0.15 and _depth >= 0.4 and _vol_r < 0.6:
                            _eq_grade = 'A'
                        elif _atr_dist <= 0.25 and _depth >= 0.3:
                            _eq_grade = 'B'
                        else:
                            _eq_grade = 'C'
                        # position에 저장 → execute_sell 시 TRADE_RESULT 로그에 사용
                        self.positions[stock_code]['eq_grade'] = _eq_grade
                        self.positions[stock_code]['choch_grade_log'] = choch_grade
                        logger.info(
                            f"[ENTRY_QUALITY] {stock_code} {stock_name} | "
                            f"choch={choch_grade} eq={_eq_grade} | "
                            f"bars_since={_eq.get('bars_since_choch', '?')} | "
                            f"atr_dist={_atr_dist} | "
                            f"depth={_depth:.0%} | "
                            f"vol_ratio={_vol_r} | "
                            f"1R={_r_pct_log:.2f}% | "
                            f"TP1={_tp1_log:,.0f} TP2={_tp2_log:,.0f}"
                        )
                except Exception:
                    self.positions[stock_code]['htf_trend_aligned'] = False
                    self.positions[stock_code]['direction'] = 'long'

        except Exception as e:
            console.print(f"[red]❌ {stock_code} 매수 신호 체크 실패: {e}[/red]")
            import traceback
            traceback.print_exc()

    def check_exit_signal(self, stock_code: str, kiwoom_df: pd.DataFrame = None):
        """매도 신호 체크 - 최적화된 청산 로직 사용"""
        try:
            logger.debug(f"[EXIT_CHECK] {stock_code} 매도 신호 체크 시작")

            position = self.positions.get(stock_code)
            if not position:
                console.print(f"[yellow]⚠️  {stock_code}: 포지션 정보 없음[/yellow]")
                return

            # 포지션 기본값 설정
            position.setdefault('entry_price', position.get('avg_price', 0))
            position.setdefault('highest_price', position['entry_price'])
            position.setdefault('trailing_active', False)
            position.setdefault('trailing_stop_price', None)
            position.setdefault('partial_exit_stage', 0)

            # 1순위: 키움 API 데이터 사용 (이미 조회된 데이터)
            # 임계값 20봉: 키움 5분봉 20개 = 100분 분량, 지표 계산에 충분
            if kiwoom_df is not None and len(kiwoom_df) >= 20:
                logger.debug(f"[EXIT] {stock_code}: 키움 데이터 사용 ({len(kiwoom_df)}봉)")
                df = kiwoom_df.copy()
            else:
                # 2순위: Yahoo Finance에서 보충 (sync 함수이므로 직접 호출 — 정상 영업시간엔 거의 미발동)
                market = None
                if stock_code in self.validated_stocks:
                    market = self.validated_stocks[stock_code].get('market')

                if not market:
                    market = 'KOSPI' if stock_code.startswith('0') else 'KOSDAQ'

                ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
                ticker = f"{stock_code}{ticker_suffix}"

                logger.warning(f"[EXIT_YF_FALLBACK] {stock_code}: Kiwoom 데이터 부족 → Yahoo 조회 (루프 블로킹 발생 가능)")
                df = download_stock_data_sync(ticker, days=1)

                if df is None or len(df) < 20:
                    console.print(f"[yellow]⚠️  {stock_code}: 데이터 부족 (df={len(df) if df is not None else 0}봉)[/yellow]")
                    # 🔧 FIX: 데이터 품질 실패 처리 (문서 명세)
                    stock_name = position.get('name', stock_code)
                    self._handle_data_quality_failure(
                        stock_code,
                        stock_name,
                        f"청산 체크 시 데이터 부족 (df={len(df) if df is not None else 0}봉 < 20봉)"
                    )
                    return

            # VWAP 설정 및 계산
            vwap_config = self.config.get_section('vwap')
            use_rolling = vwap_config.get('use_rolling', True)
            rolling_window = vwap_config.get('rolling_window', 20)

            df = self.analyzer.calculate_vwap(df, use_rolling=use_rolling, rolling_window=rolling_window)
            df = self.analyzer.calculate_atr(df)

            signal_config = self.config.get_signal_generation_config()
            df = self.analyzer.generate_signals(df, **signal_config)

            current_price = df['close'].iloc[-1]

            # 🚨 음수 가격 검증
            if current_price <= 0:
                console.print(f"[red]❌ {stock_code}: 비정상 현재가 {current_price}[/red]")
                return

            # ── 갭다운 비상청산: overnight 포지션 장 시작 후 갭하락 감지 ──────────
            # 시스템은 장중 기준이지만 손실은 장외(갭)에서 난다
            _gd_cfg = self.config.get('pyramiding', {})
            if _gd_cfg.get('gap_down_emergency_exit', True):
                try:
                    _entry_date = position.get('entry_date')
                    _today_str  = datetime.now().strftime('%Y%m%d')
                    _is_overnight = False
                    if _entry_date:
                        _ed_str = str(_entry_date)[:10].replace('-', '')
                        _is_overnight = not _ed_str.startswith(_today_str)

                    if _is_overnight and df is not None and len(df) >= 2:
                        _today_open = float(df['open'].iloc[-1])
                        _today_close = float(df['close'].iloc[-1])
                        _prev_close = float(df['close'].iloc[-2])
                        if _prev_close > 0:
                            _gap_pct = (_today_open - _prev_close) / _prev_close
                            _gap_thr = -float(_gd_cfg.get('gap_down_exit_threshold', 0.015))
                            if _gap_pct <= _gap_thr:
                                _stock_name = position.get('name', stock_code)
                                # 장 시작 후 반등 여부: 현재가 > 갭다운 open (첫 5분 반등 감지)
                                _first_bar_recovery = _today_close > _today_open
                                _partial_on_recovery = _gd_cfg.get('gap_down_partial_on_recovery', True)

                                if _first_bar_recovery and _partial_on_recovery:
                                    # 반등 감지 → 50% 부분 청산 (남은 50%는 계속 운용)
                                    _qty = position.get('quantity', 0)
                                    _partial_qty = max(1, _qty // 2)
                                    logger.warning(
                                        f"[GAP_DOWN_PARTIAL] {stock_code} {_stock_name}: "
                                        f"갭다운 {_gap_pct:.1%} + 반등감지 → {_partial_qty}주 부분청산 (50%)"
                                    )
                                    console.print(
                                        f"[bold yellow]⚠ [GAP_DOWN_PARTIAL] {_stock_name}: "
                                        f"갭다운 {_gap_pct:.1%} + 반등 → 50% 부분청산[/bold yellow]"
                                    )
                                    self.execute_sell(
                                        stock_code=stock_code,
                                        reason=f"Gap-Down 부분청산 반등감지 ({_gap_pct:.1%})",
                                        current_price=current_price,
                                        df=df,
                                        sell_all=False,
                                        quantity=_partial_qty,
                                    )
                                else:
                                    # 반등 없음 → 전량 시장가 청산
                                    logger.warning(
                                        f"[GAP_DOWN_EXIT] {stock_code} {_stock_name}: "
                                        f"갭다운 {_gap_pct:.1%} (임계: {_gap_thr:.1%}) → 전량 비상 청산"
                                    )
                                    console.print(
                                        f"[bold red]⚠ [GAP_DOWN] {_stock_name}: "
                                        f"갭다운 {_gap_pct:.1%} → 전량 비상청산[/bold red]"
                                    )
                                    self.execute_sell(
                                        stock_code=stock_code,
                                        reason=f"Gap-Down 비상청산 ({_gap_pct:.1%})",
                                        current_price=current_price,
                                        df=df,
                                        sell_all=True,
                                    )
                                return
                except Exception as _gde:
                    logger.debug(f"[GAP_DOWN_CHECK_ERR] {stock_code}: {_gde}")

            # ── 3봉 조기 청산 검증: 진입 후 N봉 이내 역방향 → 즉시 이탈 ──────────
            # 핵심: 좋은 자리 진입 = 즉시 움직임. 안 가면 틀린 자리
            _ev_cfg = self.config.get('entry_verification', {})
            if _ev_cfg.get('enabled', True):
                try:
                    _ev_bars    = int(_ev_cfg.get('max_bars', 3))
                    _ev_min_pnl = float(_ev_cfg.get('min_pnl_pct', -0.3))  # -0.3% 이상 손실 시 이탈
                    _ev_ep      = position.get('entry_price', 0)
                    _ev_time    = position.get('entry_time')
                    if _ev_ep > 0 and _ev_time:
                        _ev_pnl = (current_price - _ev_ep) / _ev_ep * 100
                        # bars_since_entry: entry_time 이후 몇 분봉 경과 (5분봉 기준)
                        _elapsed_min = (datetime.now() - _ev_time).total_seconds() / 60
                        _bars_since  = int(_elapsed_min / 5)
                        if _bars_since <= _ev_bars and _ev_pnl < _ev_min_pnl:
                            _stock_name = position.get('name', stock_code)
                            logger.info(
                                f"[ENTRY_VERIFY_EXIT] {stock_code} {_stock_name}: "
                                f"진입 {_bars_since}봉 후 pnl={_ev_pnl:.2f}% < {_ev_min_pnl}% — "
                                f"즉시 이탈 (자리 틀림)"
                            )
                            console.print(
                                f"[bold red]❌ [EV_EXIT] {_stock_name}: "
                                f"{_bars_since}봉 후 {_ev_pnl:.2f}% → 조기 이탈[/bold red]"
                            )
                            try:
                                getattr(self, '_kpi', {})['ev_exits'] = \
                                    getattr(self, '_kpi', {}).get('ev_exits', 0) + 1
                            except Exception:
                                pass
                            self.execute_sell(
                                stock_code=stock_code,
                                reason=f"3봉검증 조기이탈 ({_bars_since}봉 {_ev_pnl:.2f}%)",
                                current_price=current_price,
                                df=df,
                                sell_all=True,
                            )
                            return
                except Exception:
                    pass

            # 🔧 2026-04-03: MFE/MAE 실시간 갱신 (DEFENSIVE 성능 분석용)
            if 'peak_price' in position:
                position['peak_price'] = max(position['peak_price'], current_price)
                position['trough_price'] = min(position['trough_price'], current_price)
                _ep = position['entry_price']
                if _ep > 0:
                    position['mfe_pct'] = round((position['peak_price'] - _ep) / _ep * 100, 3)
                    position['mae_pct'] = round((_ep - position['trough_price']) / _ep * 100, 3)

            # MA Cross 모드: 데드크로스 우선 체크
            squeeze_config = self.config.get('squeeze_momentum', {})
            entry_mode = squeeze_config.get('entry_mode', 'squeeze_only')

            if entry_mode == "ma_cross":
                try:
                    # cntr_tm을 DatetimeIndex로 변환
                    df_temp = df.copy()
                    if 'cntr_tm' in df_temp.columns:
                        df_temp['datetime'] = pd.to_datetime(df_temp['cntr_tm'], format='%Y%m%d%H%M%S', errors='coerce')
                        df_temp = df_temp.set_index('datetime')

                    # DatetimeIndex가 있어야 리샘플링 가능
                    if isinstance(df_temp.index, pd.DatetimeIndex):
                        # 1분봉을 5분봉으로 리샘플링
                        df_5min = df_temp.resample('5min').agg({
                            'open': 'first',
                            'high': 'max',
                            'low': 'min',
                            'close': 'last',
                            'volume': 'sum'
                        }).dropna()

                        df_5min = df_5min.reset_index(drop=True)

                        # 데드크로스 체크 (5분봉)
                        should_exit_ma, exit_reason_ma, exit_details_ma = self.ma_cross_strategy.check_exit_signal(
                            df_5min=df_5min,
                            debug=True
                        )

                        if should_exit_ma:
                            # 데드크로스 즉시 청산
                            profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
                            exit_reason_with_time = f"{datetime.now().strftime('%H:%M')} {exit_reason_ma}"
                            self.execute_sell(stock_code, current_price, profit_pct, exit_reason_with_time, use_market_order=False)
                            return
                    else:
                        console.print(f"[yellow]⚠️  {stock_code}: 시간 정보 없음, MA Cross 데드크로스 체크 스킵[/yellow]")

                except Exception as e:
                    console.print(f"[yellow]⚠️  MA Cross 데드크로스 체크 실패: {e}[/yellow]")

            elif entry_mode == "squeeze_2tf":
                # 2-타임프레임 전략 청산 체크 (30분봉 기준)
                try:
                    df_temp = df.copy()
                    if 'cntr_tm' in df_temp.columns:
                        df_temp['datetime'] = pd.to_datetime(df_temp['cntr_tm'], format='%Y%m%d%H%M%S', errors='coerce')
                        df_temp = df_temp.set_index('datetime')

                    if isinstance(df_temp.index, pd.DatetimeIndex):
                        # 30분봉으로 리샘플링
                        df_30min = df_temp.resample('30min').agg({
                            'open': 'first',
                            'high': 'max',
                            'low': 'min',
                            'close': 'last',
                            'volume': 'sum'
                        }).dropna()

                        # 청산 조건 체크 (30분봉 데드크로스)
                        should_exit_2tf, exit_reason_2tf, exit_details_2tf = self.two_tf_strategy.check_exit_signal(
                            df_higher=df_30min,
                            debug=True
                        )

                        if should_exit_2tf:
                            profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
                            exit_reason_with_time = f"{datetime.now().strftime('%H:%M')} {exit_reason_2tf}"
                            self.execute_sell(stock_code, current_price, profit_pct, exit_reason_with_time, use_market_order=False)
                            return
                    else:
                        console.print(f"[yellow]⚠️  {stock_code}: 시간 정보 없음, 2-타임프레임 청산 체크 스킵[/yellow]")

                except Exception as e:
                    console.print(f"[yellow]⚠️  2-타임프레임 청산 체크 실패: {e}[/yellow]")

            # 🔧 2026-04-03: RS 모드 청산 체크 (trail/EMA20이탈/RS소멸)
            if str(position.get('entry_reason', '')).startswith('RS:'):
                try:
                    _rs_exit, _rs_exit_reason, _rs_exit_det = self.rs_strategy.check_exit(
                        position=position,
                        current_price=current_price,
                        df_5min=df,
                    )
                    if _rs_exit:
                        _rs_pnl = ((current_price - position['entry_price']) / position['entry_price']) * 100
                        _rs_exit_time = f"{datetime.now().strftime('%H:%M')} {_rs_exit_reason}"
                        _rs_elapsed = (datetime.now() - position['entry_time']).total_seconds() / 60
                        logger.info(f"[RS_EXIT] {stock_code}: {_rs_exit_time}")
                        # 🔧 2026-04-03: RS 청산 상세 로그 [RS_RESULT]
                        logger.info(
                            f"[RS_RESULT] {stock_code} | "
                            f"exit={_rs_exit_det.get('exit_type', '?')} | "
                            f"pnl={_rs_pnl:.2f}% | hold={_rs_elapsed:.1f}m | "
                            f"MFE={position.get('mfe_pct', 0):.3f}% MAE={position.get('mae_pct', 0):.3f}% | "
                            f"rs_score={_rs_exit_det.get('rs_score', '?')} | "
                            f"entry={position.get('entry_reason', '')}"
                        )
                        self.execute_sell(stock_code, current_price, _rs_pnl, _rs_exit_time, use_market_order=False)
                        return
                except Exception as _rse:
                    logger.debug(f"[RS_EXIT_ERR] {stock_code}: {_rse}")

            # 🔧 2026-03-31: DEFENSIVE 모드 전용 강제 청산 체크 (최우선)
            if position.get('defensive_mode'):
                _def_stop = position.get('defensive_stop_price')
                _def_tp   = position.get('defensive_tp_price')
                _def_max  = position.get('defensive_max_hold_minutes')
                _def_profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

                def _log_def_result(exit_type: str):
                    """🔧 2026-04-03: DEFENSIVE 청산 상세 로그 [DEF_RESULT]"""
                    _elapsed = (datetime.now() - position['entry_time']).total_seconds() / 60
                    _entry_r = position.get('entry_reason', '')
                    _mfe = position.get('mfe_pct', 0.0)
                    _mae = position.get('mae_pct', 0.0)
                    logger.info(
                        f"[DEF_RESULT] {stock_code} | exit={exit_type} | "
                        f"pnl={_def_profit_pct:.2f}% | hold={_elapsed:.1f}m | "
                        f"MFE={_mfe:.3f}% MAE={_mae:.3f}% | "
                        f"entry={_entry_r}"
                    )

                if _def_stop and current_price <= _def_stop:
                    exit_reason_with_time = f"{datetime.now().strftime('%H:%M')} DEFENSIVE 손절 ({_def_stop:,.0f}원, {_def_profit_pct:.2f}%)"
                    logger.info(f"[DEF_STOP] {stock_code}: {exit_reason_with_time}")
                    _log_def_result("STOP")
                    self.execute_sell(stock_code, current_price, _def_profit_pct, exit_reason_with_time, use_market_order=True)
                    return

                if _def_tp and current_price >= _def_tp:
                    exit_reason_with_time = f"{datetime.now().strftime('%H:%M')} DEFENSIVE 익절 ({_def_tp:,.0f}원, {_def_profit_pct:.2f}%)"
                    logger.info(f"[DEF_TP] {stock_code}: {exit_reason_with_time}")
                    _log_def_result("TP")
                    self.execute_sell(stock_code, current_price, _def_profit_pct, exit_reason_with_time, use_market_order=False)
                    return

                if _def_max:
                    _elapsed_min = (datetime.now() - position['entry_time']).total_seconds() / 60
                    if _elapsed_min >= _def_max:
                        exit_reason_with_time = f"{datetime.now().strftime('%H:%M')} DEFENSIVE 시간초과 ({_elapsed_min:.0f}분, {_def_profit_pct:.2f}%)"
                        logger.info(f"[DEF_TIMEOUT] {stock_code}: {exit_reason_with_time}")
                        _log_def_result("TIMEOUT")
                        self.execute_sell(stock_code, current_price, _def_profit_pct, exit_reason_with_time, use_market_order=True)
                        return

            # ── 2026-04-01: A+ TP 체크 → 절반 익절 + trailing tighten ─────
            _a_plus_tp_price = position.get('a_plus_tp_price')
            if _a_plus_tp_price and not position.get('a_plus_tp_hit', False) and current_price >= _a_plus_tp_price:
                _a_plus_tp_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
                logger.info(
                    f"[A_PLUS_TP_HIT] {stock_code} | {current_price:,.0f}원 >= TP{_a_plus_tp_price:,.0f}원 "
                    f"(+{_a_plus_tp_pct:.2f}%) → 절반 익절 + trailing tighten"
                )
                console.print(
                    f"[bold green]🎯 [A+ TP] {position.get('name', stock_code)}: "
                    f"+{_a_plus_tp_pct:.2f}% 도달 → 50% 익절, 나머지 트레일링[/bold green]"
                )
                # TP hit 플래그 + trailing tighten (0.8% → 0.5%)
                position['a_plus_tp_hit'] = True
                position['a_plus_tp_price'] = None   # 재발동 방지
                # 부분 청산 50%
                self.execute_partial_sell(
                    stock_code=stock_code,
                    price=current_price,
                    profit_pct=_a_plus_tp_pct,
                    exit_ratio=0.5,
                    stage=8   # A+ TP 전용 stage (기존 1/2차와 구분)
                )
                return
            # ─────────────────────────────────────────────────────────────────

            # 최적화된 청산 로직 호출
            should_exit, exit_reason, exit_info = self.exit_logic.check_exit_signal(
                position=position,
                current_price=current_price,
                df=df
            )

            # 수익률 계산
            profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

            # 🔧 FIX: 트레일링 스탑 상태 로그 추가
            trailing_status = ""
            if position.get('trailing_active'):
                highest = position.get('highest_price', 0)
                stop_price = position.get('trailing_stop_price', 0)
                max_profit = ((highest - position['entry_price']) / position['entry_price']) * 100
                trailing_status = f" | 트레일링활성 (최고:{highest:,.0f}원 +{max_profit:.2f}%, 스탑:{stop_price:,.0f}원)"

            logger.debug(f"[POSITION] {stock_code} pnl={profit_pct:+.2f}% cur={current_price:,.0f}{trailing_status}")

            # ✅ TradeStateManager에 최고 수익률 업데이트
            self.state_manager.update_max_profit(stock_code, profit_pct)

            # 부분 청산 처리
            if exit_info and exit_info.get('partial_exit'):
                self.execute_partial_sell(
                    stock_code=stock_code,
                    price=current_price,
                    profit_pct=profit_pct,
                    exit_ratio=exit_info.get('exit_ratio', 0.3),
                    stage=exit_info.get('stage', 1)
                )
                return

            # 전량 청산 실행
            if should_exit:
                use_market_order = exit_info.get('use_market_order', False) if exit_info else False
                exit_reason_with_time = f"{datetime.now().strftime('%H:%M')} {exit_reason}"
                self.execute_sell(stock_code, current_price, profit_pct, exit_reason_with_time, use_market_order)

        except Exception as e:
            console.print(f"[red]❌ {stock_code} 매도 신호 체크 실패: {e}[/red]")

    def calculate_daily_pnl(self) -> float:
        """금일 손익 계산 (L0 시스템 필터용)"""
        try:
            # DB에서 오늘 거래 조회
            today = datetime.now().strftime('%Y-%m-%d')

            trades_today = self.db.get_trades()  # 전체 조회 후 필터

            total_pnl = 0.0
            for trade in trades_today:
                trade_time = trade.get('trade_time', '')

                # 🔧 FIX: datetime 객체를 문자열로 변환
                if isinstance(trade_time, datetime):
                    trade_time_str = trade_time.strftime('%Y-%m-%d')
                else:
                    trade_time_str = str(trade_time) if trade_time else ''

                if trade_time_str.startswith(today):
                    realized_profit = trade.get('realized_profit', 0)
                    # 🔧 CRITICAL FIX: bytes/string 안전 변환
                    total_pnl += safe_float(realized_profit)

            return total_pnl

        except Exception as e:
            console.print(f"[dim]⚠️  일일 손익 계산 실패: {e}[/dim]")
            return 0.0

    def _check_defensive_entry(self, df: pd.DataFrame, cfg: dict) -> tuple:
        """🔧 2026-03-31: DEFENSIVE 모드 진입 조건 체크

        NO_TRADE_DAY(시장 악화) 상황에서 과매도 반등 스캘핑 신호 탐지.

        조건:
          1. RSI < max_rsi (과매도)
          2. 현재가 EMA20 대비 min_ema20_deviation_pct% 이하 (눌림)
          3. 거래량 스파이크 (현재봉 거래량 / 20봉 평균 >= min_volume_ratio)
          4. (require_vwap_reclaim=true) 직전 캔들이 VWAP 아래였다가 현재 캔들이 VWAP 위로 회복

        Returns:
            (signal: bool, reason: str)
        """
        try:
            if df is None or len(df) < 22:
                return False, "데이터 부족"

            max_rsi      = cfg.get('max_rsi', 30)
            ema_dev_min  = cfg.get('min_ema20_deviation_pct', -1.5) / 100
            vol_ratio    = cfg.get('min_volume_ratio', 2.0)
            need_reclaim = cfg.get('require_vwap_reclaim', True)

            close  = df['close'].iloc[-1]
            high   = df['high'].iloc[-1]

            # RSI 체크
            rsi_col = None
            for col in ['rsi', 'RSI', 'rsi_14']:
                if col in df.columns:
                    rsi_col = col
                    break
            if rsi_col is None:
                return False, "RSI 컬럼 없음"
            rsi_val = float(df[rsi_col].iloc[-1])
            if rsi_val >= max_rsi:
                return False, f"RSI {rsi_val:.1f} >= {max_rsi} (과매도 아님)"

            # EMA20 이격 체크
            ema20_col = None
            for col in ['ema20', 'EMA20', 'ma20']:
                if col in df.columns:
                    ema20_col = col
                    break
            if ema20_col is None:
                return False, "EMA20 컬럼 없음"
            ema20 = float(df[ema20_col].iloc[-1])
            if ema20 <= 0:
                return False, "EMA20 이상값"
            deviation = (close - ema20) / ema20
            if deviation > ema_dev_min:
                return False, f"EMA20 이격 {deviation*100:.2f}% > {ema_dev_min*100:.1f}% (눌림 부족)"

            # 거래량 스파이크 체크
            vol_now = float(df['volume'].iloc[-1])
            vol_avg = float(df['volume'].iloc[-21:-1].mean())
            if vol_avg <= 0:
                return False, "거래량 평균 계산 불가"
            v_ratio = vol_now / vol_avg
            if v_ratio < vol_ratio:
                return False, f"거래량 비율 {v_ratio:.2f}x < {vol_ratio}x (스파이크 없음)"

            # VWAP 회복 체크
            if need_reclaim:
                if 'vwap' not in df.columns or len(df) < 2:
                    return False, "VWAP 데이터 없음"
                vwap_now  = float(df['vwap'].iloc[-1])
                vwap_prev = float(df['vwap'].iloc[-2])
                close_prev = float(df['close'].iloc[-2])
                # 직전 봉이 VWAP 아래 + 현재 봉 고가가 VWAP 위 = 회복
                if not (close_prev < vwap_prev and high >= vwap_now):
                    return False, f"VWAP 회복 미확인 (prev_close={close_prev:.0f} < prev_vwap={vwap_prev:.0f}, cur_high={high:.0f} vs vwap={vwap_now:.0f})"

            # 반등 캔들 확인 (떨어지는 칼날 방지)
            if len(df) >= 2:
                close_prev = float(df['close'].iloc[-2])
                if close <= close_prev:
                    return False, f"반등 캔들 아님 (cur={close:.0f} <= prev={close_prev:.0f})"

            reason = (
                f"DEFENSIVE: RSI={rsi_val:.1f}(<{max_rsi}) "
                f"이격={deviation*100:.1f}% "
                f"거래량={v_ratio:.1f}x"
            )
            if need_reclaim:
                reason += " VWAP회복✓"
            return True, reason

        except Exception as e:
            return False, f"DEFENSIVE 체크 오류: {e}"

    def _check_short_entry(self, df: pd.DataFrame, cfg: dict) -> tuple:
        """🔧 2026-03-31: SHORT 모드 진입 조건 체크 (v2 — 승률 필터 4개 추가)

        NO_TRADE_DAY + DEFENSIVE 신호 없을 때 인버스 ETF 매수 신호 탐지.

        v1 조건:
          1. EMA20 < EMA60 (약세 구조)
          2. 최근 저점 돌파 (breakdown)
          3. 거래량 확인

        v2 추가 (승률 60→70% 개선):
          4. 리테스트 실패 — 되돌림 후 재차 못 오름 (휩쏘 50% 제거)
          5. VWAP 아래 — 기관 평균단가 아래서만 하락 신뢰 가능
          6. RSI 반등 아님 — 이미 바닥 찍고 오르는 구간 차단
          7. 음봉 캔들 — 몸통 > 레인지 50% (진짜 밀리는 캔들 확인)

        Returns:
            (signal: bool, reason: str)
        """
        try:
            if df is None or len(df) < 30:
                return False, "데이터 부족"

            close  = float(df['close'].iloc[-1])
            open_  = float(df['open'].iloc[-1])
            high_  = float(df['high'].iloc[-1])
            low_   = float(df['low'].iloc[-1])

            # ── 1. EMA 약세 구조 ─────────────────────────────────────────
            if cfg.get('require_ema_bear', True):
                ema20_col = next((c for c in ['ema20', 'EMA20', 'ma20'] if c in df.columns), None)
                ema60_col = next((c for c in ['ema60', 'EMA60', 'ma60'] if c in df.columns), None)
                if ema20_col is None or ema60_col is None:
                    return False, "EMA20/EMA60 컬럼 없음"
                ema20 = float(df[ema20_col].iloc[-1])
                ema60 = float(df[ema60_col].iloc[-1])
                if ema20 >= ema60:
                    return False, f"EMA 약세 아님 (EMA20={ema20:.0f} >= EMA60={ema60:.0f})"

            # ── 2. 직전 저점 이탈 (breakdown) ────────────────────────────
            if len(df) < 6:
                return False, "저점 계산용 데이터 부족"
            recent_low = float(df['low'].iloc[-6:-1].min())
            if recent_low <= 0:
                return False, "저점 이상값"
            breakdown_pct = (close - recent_low) / recent_low * 100
            threshold = cfg.get('min_breakdown_pct', -0.5)
            if breakdown_pct > threshold:
                return False, f"하락 돌파 부족 ({breakdown_pct:.2f}% > {threshold}%)"

            # ── 3. 거래량 확인 ────────────────────────────────────────────
            vol_now = float(df['volume'].iloc[-1])
            vol_avg = float(df['volume'].iloc[-21:-1].mean()) if len(df) >= 21 else 0
            if vol_avg <= 0:
                return False, "거래량 평균 계산 불가"
            v_ratio = vol_now / vol_avg
            min_vol = cfg.get('min_volume_ratio', 1.2)
            if v_ratio < min_vol:
                return False, f"거래량 부족 ({v_ratio:.2f}x < {min_vol}x)"

            # ── 4. 리테스트 실패 (휩쏘 방지 ⭐) ────────────────────────────
            # 하락 이탈 후 되돌림 시도 → 최근 5봉 고점 못 돌파 확인
            if len(df) >= 6:
                recent_high = float(df['high'].iloc[-6:-1].max())
                if close >= recent_high:
                    return False, f"리테스트 성공 ({close:.0f} >= {recent_high:.0f}) — 하락 아님"

            # ── 5. VWAP 아래 ─────────────────────────────────────────────
            if 'vwap' in df.columns:
                vwap_val = float(df['vwap'].iloc[-1])
                if vwap_val > 0 and close >= vwap_val:
                    return False, f"VWAP 위 ({close:.0f} >= {vwap_val:.0f}) — 숏 금지"

            # ── 6. RSI 반등 아님 ─────────────────────────────────────────
            rsi_col = next((c for c in ['rsi', 'RSI', 'rsi_14'] if c in df.columns), None)
            if rsi_col and len(df) >= 2:
                rsi_now  = float(df[rsi_col].iloc[-1])
                rsi_prev = float(df[rsi_col].iloc[-2])
                if rsi_now > rsi_prev:
                    return False, f"RSI 반등 중 ({rsi_prev:.1f}→{rsi_now:.1f}) — 숏 금지"

            # ── 7. 음봉 캔들 (몸통 > 레인지 50%) ────────────────────────
            candle_range = high_ - low_
            body = abs(close - open_)
            if candle_range > 0:
                body_ratio = body / candle_range
                if not (close < open_ and body_ratio >= 0.5):
                    return False, f"음봉 미충족 (close={close:.0f} open={open_:.0f} body={body_ratio:.0%})"

            reason = (
                f"SHORT: EMA↓ breakdown={breakdown_pct:.2f}% "
                f"vol={v_ratio:.1f}x retest✓ VWAP↓ RSI↓ 음봉✓"
            )
            return True, reason

        except Exception as e:
            return False, f"SHORT 체크 오류: {e}"

    def _select_inverse_symbol(self, symbols: list) -> str:
        """🔧 2026-03-31: 인버스 ETF 종목 선택

        현재: 252670(2X) 우선 → 없으면 첫 번째
        추후: 거래량/스프레드/변동성 기반 동적 선택으로 고도화 가능
        """
        if "252670" in symbols:
            return "252670"
        return symbols[0] if symbols else "114800"

    def _get_inverse_etf_price(self, symbol: str) -> float | None:
        """🔧 2026-03-31: 인버스 ETF 현재가 조회"""
        try:
            price_result = self.api.get_stock_price(symbol)
            if price_result and price_result.get('return_code') == 0:
                output = price_result.get('output') or price_result.get('output1') or {}
                for key in ['stck_prpr', 'cur_prc', 'price', 'current_price']:
                    if key in output:
                        val = float(output[key])
                        if val > 0:
                            return val
        except Exception as e:
            logger.warning(f"[SHORT] 인버스 ETF {symbol} 현재가 조회 실패: {e}")
        return None

    def _check_loss_streak_guard(self):
        """🔧 2026-02-19: Loss Streak Guard 상태 확인 및 활성화/해제"""
        lsg_config = self.config.get('risk_control.loss_streak_guard', {})
        if not self.risk_manager or not lsg_config.get('enabled', False):
            return
        # 🔧 2026-04-02: 재시작 후 activated_date 복원을 위해 persisted 값 전달
        lsg_result = self.reentry_metrics.activate_loss_streak_guard(
            self.risk_manager.consecutive_losses, lsg_config,
            persisted_activated_date=self.risk_manager.lsg_activated_date,
        )
        if lsg_result['changed']:
            color = 'red' if lsg_result['active'] else 'green'
            console.print(f"[bold {color}]🛡️ {lsg_result['message']}[/bold {color}]")
            # 🔧 2026-04-02: 활성화/해제 시 risk_manager에 날짜 동기화
            lsg_adj = self.reentry_metrics.get_loss_streak_adjustments(lsg_config)
            if lsg_adj['active'] and self.reentry_metrics._loss_streak_activated_date:
                self.risk_manager.lsg_activated_date = str(
                    self.reentry_metrics._loss_streak_activated_date
                )
                self.risk_manager.save()
            elif not lsg_adj['active']:
                self.risk_manager.lsg_activated_date = None
                self.risk_manager.save()
        # EF threshold override 설정/해제
        lsg_adj = self.reentry_metrics.get_loss_streak_adjustments(lsg_config)
        self.exit_logic.ef_threshold_override = lsg_adj['ef_score_threshold']  # None when inactive

        # 🔧 2026-04-20: 연패 트레이드 쿨다운 트리거
        _cd_cfg = self.config.get('risk_control', {}).get('trade_cooldown', {})
        if _cd_cfg.get('enabled', True):
            _cd_threshold = _cd_cfg.get('consecutive_loss_threshold', 3)
            _cd_trades    = _cd_cfg.get('cooldown_trades', 2)
            _cons_loss    = self.risk_manager.consecutive_losses
            if _cons_loss >= _cd_threshold and self._trade_cooldown == 0:
                self._trade_cooldown = _cd_trades
                msg = (
                    f'[TRADE_CD] 연패 {_cons_loss}회 → '
                    f'신규 진입 {_cd_trades}회 쿨다운 설정'
                )
                logger.warning(msg)
                console.print(f'[bold yellow]⏸️ {msg}[/bold yellow]')

    def _calc_entry_confidence(
        self,
        df_5min,
        rvol: float = 0.0,
        breakout_pct: float = 0.0,
    ) -> float:
        """
        진입 신호 확률 점수 (0.0 ~ 1.0)
        승률 70% 구조: 사후 리스크 조절 → 사전 진입 필터링
        구성: ①시장구조(0.25) ②거래량(0.25) ③돌파강도(0.25) ④시간대(0.15) ⑤노이즈(-0.20)
        """
        conf = 0.0

        # ① 시장 구조 (최대 0.25)
        try:
            mc_status, mc_reason, _ = self.market_context.evaluate()
            if mc_status == "TRADE_OK":
                if "HH+HL" in mc_reason:
                    conf += 0.10
                # EMA gap 파싱 "gap=+X.XX%"
                import re as _re
                _gap_m = _re.search(r'gap=\+(\d+\.\d+)%', mc_reason)
                if _gap_m and float(_gap_m.group(1)) >= 1.2:
                    conf += 0.15
        except Exception:
            pass

        # ② 거래량 (최대 0.25)
        if rvol >= 1.5:
            conf += 0.15
        if rvol >= 2.0:
            conf += 0.10

        # ③ 돌파 강도 (최대 0.25)
        if breakout_pct >= 0.5:
            conf += 0.15
        # 종가 기준 돌파 (직전봉 고가 대비 현재 종가 위)
        if df_5min is not None and len(df_5min) >= 3:
            try:
                _lc = df_5min['close'].iloc[-1]
                _ph = df_5min['high'].iloc[-2]
                if _lc > _ph:
                    conf += 0.10
            except Exception:
                pass

        # ④ 시간대 보너스 (최대 0.15)
        _now_min = datetime.now().hour * 60 + datetime.now().minute
        if 9 * 60 + 30 <= _now_min <= 10 * 60 + 30:
            conf += 0.10
        elif 12 * 60 <= _now_min <= 13 * 60:
            conf += 0.05

        # ⑤ 노이즈 필터 (최대 -0.20)
        if df_5min is not None and len(df_5min) >= 4:
            try:
                _last = df_5min.iloc[-1]
                _body = abs(_last['close'] - _last['open'])
                _upper = _last['high'] - max(_last['close'], _last['open'])
                if _body > 0 and _upper > _body:
                    conf -= 0.10   # 윗꼬리 > 몸통 → 매도세
                # 직전 2봉 변동성 과도
                _recent_range = (df_5min['high'].iloc[-3:-1] - df_5min['low'].iloc[-3:-1]).mean()
                _avg_range = (df_5min['high'] - df_5min['low']).iloc[-21:-3].mean() if len(df_5min) >= 24 else 0
                if _avg_range > 0 and _recent_range > _avg_range * 2:
                    conf -= 0.10   # 직전 2봉 과도 변동성
            except Exception:
                pass

        return max(0.0, min(1.0, conf))

    def _is_valid_entry_time(self, current_time: datetime = None) -> Tuple[bool, str]:
        """
        시간 필터 강제 체크 (모든 진입 경로에서 체크)
        🔴 GPT 개선: 점심시간 완전 차단 (12:00-14:00)
        ✅ 스퀴즈 모멘텀 모드: 점심시간 매수 허용

        Returns:
            (허용 여부, 사유)
        """
        if current_time is None:
            current_time = datetime.now()

        t = current_time.time()

        # Hard-coded 시간 체크 (설정 파일 무관)
        from datetime import time as time_class
        # 🔧 2026-03-05 데이터 기반: Opening Noise Hard Block
        # 09:30~10:00: 6건 0% 승률 → 구조적 실패 구간 → Hard Block
        # 10:00 이후: 정상 진입 허용
        ENTRY_START = time_class(10, 0, 0)   # 10:00 이전 진입 금지 (09:20 롤백)

        # 🔴 점심시간 완전 차단 (재진입 포함)
        MIDDAY_START = time_class(12, 0, 0)
        MIDDAY_END = time_class(14, 0, 0)

        if t < ENTRY_START:
            return False, f"❌ [OPENING_NOISE] 10:00 이전 진입 차단 ({t.strftime('%H:%M:%S')})"

        # ❌ 14:59 진입 차단 비활성화
        # if t > ENTRY_END:
        #     return False, f"❌ 14:59 이후 진입 차단 ({t.strftime('%H:%M:%S')})"

        # ✅ 스퀴즈 모멘텀 모드: 점심시간 매수 허용
        squeeze_config = self.config.get('squeeze_momentum', {})
        entry_mode = squeeze_config.get('entry_mode', 'squeeze_only')  # 기본값: squeeze_only

        # 🔧 2026-02-19: SMC 시간 필터 강화 (YAML 설정 기반, 기존 14:00 → 12:30)
        smc_cutoff_str = self.config.get('time_filter.smc_afternoon_cutoff', '12:30')
        h_cut, m_cut = map(int, smc_cutoff_str.split(':'))
        SMC_AFTERNOON_CUT = time_class(h_cut, m_cut, 0)
        if entry_mode == 'smc' and t >= SMC_AFTERNOON_CUT:
            return False, f"🚫 SMC {smc_cutoff_str} 이후 진입 차단 ({t.strftime('%H:%M:%S')})"

        # 🔥 수정: squeeze_2tf, ma_cross, smc 모드도 점심시간 허용
        if entry_mode in ['squeeze_only', 'squeeze_with_orderbook', 'squeeze_2tf', 'ma_cross', 'smc']:
            # 스퀴즈/MA/SMC 기반 모드에서는 점심시간 매수 허용
            return True, ""

        # 🔴 점심시간 차단 (12:00-14:00) - legacy_only, hybrid 모드만
        if MIDDAY_START <= t < MIDDAY_END:
            return False, f"🚫 점심시간 진입 차단 ({t.strftime('%H:%M:%S')})"

        return True, ""

    # ── Signal Queue (detect → execute 분리) ──────────────────────────────

    def _emit_signal(
        self,
        stock_code: str, stock_name: str, price: float, df,
        position_size_mult: float, confidence: float, entry_reason: str,
        stop_loss: float = None, strategy: str = 'SMC'
    ):
        """신호를 detect하여 pending 큐에 추가 (중복/만료 선체크)"""
        import json as _json
        from datetime import datetime as _dt, timedelta as _td

        now = _dt.now()
        signal_id = f"{stock_code}_{now.strftime('%Y%m%d_%H%M%S')}"

        # 중복 방지
        if signal_id in self._executed_signal_ids:
            logger.warning(f"[DUP_SIGNAL] {signal_id} 이미 처리됨")
            return

        valid_until = (now + _td(seconds=60)).isoformat(timespec='seconds')

        signal = {
            'signal_id': signal_id,
            'timestamp': now.isoformat(timespec='seconds'),
            'valid_until': valid_until,
            'action': 'BUY',
            'code': stock_code,
            'name': stock_name,
            'price': price,
            'strategy': strategy,
            'confidence': round(confidence, 4),
            'position_size_mult': round(position_size_mult, 4),
            'stop_loss': round(stop_loss, 0) if stop_loss else None,
            'max_slippage_pct': 0.3,
            'entry_reason': entry_reason or '',
            '_df': df,  # 실행용 (JSON 출력 시 제외)
        }

        self._pending_signals.append(signal)
        logger.info(f"[SIGNAL_QUEUED] {signal_id} price={price:,.0f} conf={confidence:.2f} sl={stop_loss}")

    def _flush_pending_signals(self, stock_data: list = None):
        """pending 신호 → 유효성 검증 → 실행 (주기 1회 호출)"""
        import json as _json
        from datetime import datetime as _dt

        if not self._pending_signals:
            return

        stock_price_map = {d['code']: d.get('price', 0) for d in (stock_data or [])}

        for sig in self._pending_signals:
            try:
                sid = sig['signal_id']
                code = sig['code']

                # 1. 유효시간 체크
                valid_until = _dt.fromisoformat(sig['valid_until'])
                if _dt.now() > valid_until:
                    logger.warning(f"[EXPIRED] {sid}: 유효시간 초과")
                    continue

                # 2. max_slippage 체크 (현재가 vs 신호가)
                current_price = stock_price_map.get(code, 0)
                if current_price > 0 and sig['price'] > 0:
                    slippage_pct = abs(current_price - sig['price']) / sig['price'] * 100
                    if slippage_pct > sig['max_slippage_pct']:
                        logger.warning(
                            f"[SLIPPAGE] {sid}: {slippage_pct:.2f}% > {sig['max_slippage_pct']}% → 취소"
                        )
                        continue

                # 3. DRY_RUN: JSON 출력만
                clean = {k: v for k, v in sig.items() if not k.startswith('_')}
                if self._dry_run:
                    console.print(
                        f"[cyan bold][DRY_RUN][/cyan bold] "
                        f"[bold]{code} {sig['name']}[/bold] "
                        f"BUY @ {sig['price']:,.0f}원 | conf={sig['confidence']:.2f} | sl={sig['stop_loss']}"
                    )
                    logger.info(f"[DRY_RUN_SIGNAL] {_json.dumps(clean, ensure_ascii=False)}")
                    self._executed_signal_ids.add(sid)
                    continue

                # 4. Global Gate 재확인 + 실제 실행
                _fg_ok, _fg_reason = self._check_global_risk_gates(code, sig['name'])
                if not _fg_ok:
                    logger.debug(f"[FLUSH_GATE] {code}: {_fg_reason} → 신호 취소")
                    continue
                self._executed_signal_ids.add(sid)
                self.execute_buy(
                    code, sig['name'], sig['price'], sig['_df'],
                    sig['position_size_mult'], sig['confidence'], sig['entry_reason']
                )

            except Exception as e:
                logger.error(f"[SIGNAL_EXEC_ERR] {sig.get('signal_id','?')}: {e}")

        self._pending_signals.clear()

    def execute_buy(self, stock_code: str, stock_name: str, price: float, df: pd.DataFrame, position_size_mult: float = 1.0, entry_confidence: float = 1.0, entry_reason: str = None, stop_loss_pct_override: float = None, take_profit_pct: float = None, max_hold_minutes: int = None):
        """매수 실행 (실계좌 기반 리스크 관리 + SignalOrchestrator 포지션 조정)

        Args:
            entry_confidence: 진입 신뢰도 (0.0~1.0, TIER_1=1.0, TIER_2=0.7, TIER_3=0.5)
            entry_reason: 매수 이유 (예: "12:34 30분봉 MA5/MA20 골든크로스 + Squeeze OFF")
            stop_loss_pct_override: 손절 % 재정의 (DEFENSIVE 전용, 없으면 config 기본값)
            take_profit_pct: TP % (DEFENSIVE 전용, 없으면 일반 청산 로직 사용)
            max_hold_minutes: 최대 보유 분 (DEFENSIVE 전용, 없으면 무제한)
        """
        # 원본 사이즈 배율 캡처 (SIZE 로그용 — 이후 여러 배율이 곱해지므로 여기서만 저장)
        _initial_mult = position_size_mult

        # 🔧 2026-02-07: 진입 시도 카운트 (쿨다운 체크 이전)
        self.reentry_metrics.record_entry_signal()

        # [PATTERN] 로그: 진입 시 패턴 정보 기록 (score_enabled=false 동안 로그만)
        try:
            _pat_cfg = self.config.get('pattern_recognition', {})
            if _pat_cfg.get('enabled', True):
                _daily_pats = getattr(self, '_daily_patterns', {})
                _pat = _daily_pats.get(stock_code)
                if _pat:
                    _phase_icon = {'breakout': '🔥', 'confirmed': '✅', 'forming': '🔍'}.get(_pat.get('phase', ''), '?')
                    logger.info(
                        f"[PATTERN] {stock_code} {stock_name} | "
                        f"{_pat_cfg.get('score_enabled', False) and 'SCORE_ON' or 'LOG_ONLY'} | "
                        f"{_pat.get('pattern')}({_pat.get('phase')}) "
                        f"conf={_pat.get('confidence', 0):.2f} "
                        f"RR={_pat.get('rr', 0)} "
                        f"entry={_pat.get('entry', 0):.0f} "
                        f"stop={_pat.get('stop', 0):.0f} "
                        f"target={_pat.get('target', 0):.0f} "
                        f"{_phase_icon}"
                    )
                else:
                    logger.debug(f"[PATTERN] {stock_code}: 패턴 없음")
        except Exception:
            pass

        # 🔧 2026-04-30: 시간대 가중치 (Gate 아님 — 사이즈 배율)
        # 물리적 시간 차단(10:00/14:59)은 _check_global_risk_gates()에서 이미 처리됨
        _er_str = str(entry_reason or '')
        _entry_strat_tag = ('experiment' if _er_str.startswith("EXPERIMENT:") else
                            'exploration' if _er_str.startswith("EXPLORATION:") else
                            'squeeze'     if _er_str.startswith("SQZ:")         else
                            'defensive'   if _er_str.startswith("DEFENSIVE:")   else
                            'rs'          if _er_str.startswith("RS:")           else
                            'smc')
        _time_weight = self._get_time_weight(strategy=_entry_strat_tag)
        if _time_weight == 0.0:
            logger.debug(f"[TIME_WEIGHT] {stock_code}: weight=0.0 ({datetime.now().strftime('%H:%M')}) → 건너뜀")
            return
        if _time_weight < 1.0:
            position_size_mult *= _time_weight
            logger.info(f"[TIME_WEIGHT] {stock_code} {stock_name}: strategy={_entry_strat_tag} "
                        f"weight={_time_weight:.2f} → size×{_time_weight:.2f}")

        # 🔧 2026-03-05: Midday Boost (12:00~12:30, 실거래 45% 승률/승리의 32%)
        # entry_confidence가 L6(0.6×BT+0.4×RSVI) 반영 → 0.55 이상이면 RSVI 양호
        from datetime import time as _t
        _now_t = datetime.now().time()
        if _t(12, 0) <= _now_t <= _t(12, 30):
            if entry_confidence >= 0.55:
                position_size_mult = position_size_mult * 1.2
                console.print(f"[green]🚀 [MIDDAY_BOOST] {stock_name}: 12:00~12:30 + conf {entry_confidence:.2f} → size×1.2[/green]")
                logger.info(f"[MIDDAY_BOOST] {stock_code} {stock_name}: size×1.2 (conf={entry_confidence:.2f})")
            else:
                console.print(f"[dim]⏱️ [MIDDAY] {stock_name}: 12:00~12:30 but conf {entry_confidence:.2f} < 0.55 → boost 미적용[/dim]")

        # 🔧 FIX: 금지 종목 체크 (3회 연속 손실 종목)
        if stock_code in self.stock_ban_list:
            logger.info(f"[BAN_LIST_BLOCK] {stock_code} {stock_name}: 3회 연속 손실로 당일 진입 금지")
            console.print(f"[red]🚫 {stock_name}: 3회 연속 손실로 당일 진입 금지[/red]")
            try:
                from database.decision_trace import insert_blocked_trade
                insert_blocked_trade(stock_code, stock_name, 'BAN_LIST_BLOCK', df, price, entry_reason)
            except Exception:
                pass
            return

        # 🔧 2026-04-20: 연패 쿨다운 체크 — edge(score_enabled) 또는 confidence 기반 바이패스
        if self._trade_cooldown > 0:
            _cd_cfg_inner = self.config.get('risk_control', {}).get('trade_cooldown', {})
            _pat_cfg_cd   = self.config.get('pattern_recognition', {})
            _cd_bypass    = False
            _bypass_reason = ''

            if _pat_cfg_cd.get('score_enabled', False):
                # edge 기반 — pattern_sizer와 동일 공식으로 사전 계산
                try:
                    from trading.pattern_sizer import _load_weight as _ps_lw
                    _cd_pat = getattr(self, '_daily_patterns', {}).get(stock_code) or {}
                    _cd_pk  = f"{_cd_pat.get('pattern','')}({_cd_pat.get('phase','')})"
                    _cd_w   = _ps_lw(_cd_pk)
                    _cd_edge = float(_cd_pat.get('confidence', 0.0)) * _cd_w * entry_confidence
                    _cd_thr  = _cd_cfg_inner.get('bypass_edge', 0.20)
                    _cd_bypass = _cd_edge >= _cd_thr
                    _bypass_reason = f'edge={_cd_edge:.3f}>={_cd_thr}'
                except Exception:
                    _cd_thr = _cd_cfg_inner.get('bypass_confidence', 0.85)
                    _cd_bypass = entry_confidence >= _cd_thr
                    _bypass_reason = f'conf={entry_confidence:.2f}(edge산출실패)'
            else:
                # score_enabled=False → entry_confidence 기반
                _cd_thr = _cd_cfg_inner.get('bypass_confidence', 0.85)
                _cd_bypass = entry_confidence >= _cd_thr
                _bypass_reason = f'conf={entry_confidence:.2f}>={_cd_thr}'

            if _cd_bypass:
                logger.info(
                    f'[TRADE_CD_BYPASS] {stock_code}: {_bypass_reason} → '
                    f'쿨다운 바이패스 (남은={self._trade_cooldown}회)'
                )
            else:
                self._trade_cooldown -= 1
                logger.info(
                    f'[TRADE_CD] {stock_code} {stock_name}: 연패 쿨다운 스킵 '
                    f'(남은={self._trade_cooldown}회)'
                )
                console.print(
                    f'[yellow]⏸️ [TRADE_CD] {stock_name}: 연패 쿨다운 중 → 진입 차단 '
                    f'(남은 {self._trade_cooldown}회)[/yellow]'
                )
                return

        # 🔧 2026-03-24: Market Context 태깅 + 실험 모드 비중 적용
        # NO_TRADE_DAY → BAD_MARKET 태그 + 비중 축소 (완전 차단 없음)
        # 🔧 2026-04-20: ATR 3단계 모드 추가 (AGGRESSIVE/NORMAL/DEFENSIVE)
        mc_status, mc_reason, mc_details = self.market_context.evaluate()
        _exp_cfg = self.config.get('experiment', {})
        _bad_mult = _exp_cfg.get('bad_market_size_mult', 0.3)
        mc_tag = "BAD_MARKET" if mc_status == "NO_TRADE_DAY" else "GOOD_MARKET"
        if mc_status == "NO_TRADE_DAY":
            position_size_mult = position_size_mult * _bad_mult
            entry_confidence = max(entry_confidence - 0.1, 0.1)
            console.print(f"[yellow]🌐 [MKT_CTX] {stock_name}: BAD_MARKET → size×{_bad_mult}, conf-0.1 | {mc_reason}[/yellow]")
            logger.info(f"[MKT_CTX] context=BAD_MARKET size_mult={_bad_mult} {stock_code} {stock_name}: {mc_reason}")

        # ATR 3단계 모드 사이징
        _atr_mode  = mc_details.get("atr_mode", "NORMAL")
        _atr_ratio = mc_details.get("atr_ratio", 1.0)
        _atr_tier_cfg = self.config.get('market_context', {}).get('atr_tiers', {})
        if _atr_mode == "AGGRESSIVE":
            _atr_mult = float(_atr_tier_cfg.get('aggressive_mult', 1.5))
            position_size_mult *= _atr_mult
            console.print(f"[green]🔥 [MKT_MODE] {stock_name}: AGGRESSIVE(ratio={_atr_ratio:.2f}) → size×{_atr_mult}[/green]")
            logger.info(f"[MKT_MODE] AGGRESSIVE ratio={_atr_ratio:.2f} size×{_atr_mult} {stock_code} {stock_name}")
        elif _atr_mode == "DEFENSIVE":
            _atr_mult = float(_atr_tier_cfg.get('defensive_mult', 0.3))
            position_size_mult *= _atr_mult
            console.print(f"[yellow]🛡️ [MKT_MODE] {stock_name}: DEFENSIVE(ratio={_atr_ratio:.2f}) → size×{_atr_mult}[/yellow]")
            logger.info(f"[MKT_MODE] DEFENSIVE ratio={_atr_ratio:.2f} size×{_atr_mult} {stock_code} {stock_name}")
        else:
            logger.info(f"[MKT_MODE] NORMAL ratio={_atr_ratio:.2f} {stock_code} {stock_name}")

        # ========================================
        # 🔧 2026-03-05: Entry Quality 필터 (RVOL + EMA9 Micro Pullback)
        # 실거래 분석: 60% 역방향 진입 → 가짜 모멘텀 + 타이밍 문제
        # ========================================

        # [EQ-1] RVOL Expansion Filter: 현재 5분봉 거래량 / 20봉 평균 >= 1.7
        # 거래량 없는 가짜 돌파 차단 (대우건설 케이스: MFE -0.18% 직후 손절)
        # 🔧 2026-03-25: EXPERIMENT 모드는 RVOL 차단 없이 측정만 (신호 검증 목적)
        eq_config = self.config.get('entry_quality', {})
        rvol_enabled = eq_config.get('rvol_filter.enabled', True)
        rvol_threshold = eq_config.get('rvol_filter.threshold', 1.7)
        _is_experiment   = str(entry_reason).startswith("EXPERIMENT:")
        _is_exploration  = str(entry_reason).startswith("EXPLORATION:")
        _is_a_plus       = str(entry_reason).startswith("A+:")
        # EXPLORATION: 5분봉 RVOL 이미 검증 완료 → EQ-1 완전 스킵 (timeframe 독립성)
        if not _is_exploration and rvol_enabled and not df.empty and 'volume' in df.columns and len(df) >= 21:
            curr_vol = float(df['volume'].iloc[-1])
            avg_vol  = float(df['volume'].iloc[-21:-1].mean())
            rvol     = curr_vol / avg_vol if avg_vol > 0 else 0.0
            if rvol < rvol_threshold:
                if _is_experiment:
                    logger.info(f"[EXPERIMENT_RVOL_LOW] {stock_code} {stock_name}: RVOL={rvol:.2f} < {rvol_threshold} (진입 허용)")
                else:
                    logger.info(f"[RVOL_BLOCK] {stock_code} {stock_name}: RVOL={rvol:.2f} < {rvol_threshold}")
                    console.print(f"[yellow]📊 [RVOL_BLOCK] {stock_name}: RVOL {rvol:.2f} < {rvol_threshold} → 거래량 부족, 차단[/yellow]")
                    return

        # [EQ-2] EMA9 Micro Pullback Filter: 현재가 > EMA9 AND 직전봉 저가 ≤ EMA9×1.002
        # 추격 매수 제거 (한전산업: MFE +1.18% → 결국 손절, EMA9 반등 없이 진입)
        ema9_enabled = eq_config.get('ema9_pullback.enabled', True)
        if ema9_enabled and not df.empty and 'close' in df.columns and len(df) >= 10:
            try:
                ema9 = float(df['close'].ewm(span=9, adjust=False).mean().iloc[-1])
                curr_close = float(df['close'].iloc[-1])
                prev_low   = float(df['low'].iloc[-2]) if 'low' in df.columns else curr_close
                # 현재가 > EMA9 (롱 방향)
                # 직전봉 저가 ≤ EMA9 × 1.002 (EMA9 터치 또는 근접 후 반등)
                above_ema9   = curr_close > ema9
                touched_ema9 = prev_low <= ema9 * 1.002
                if not (above_ema9 and touched_ema9):
                    if _is_experiment:
                        logger.info(f"[EXPERIMENT_EMA9_LOW] {stock_code} {stock_name}: close={curr_close:.0f} EMA9={ema9:.0f} (진입 허용)")
                    else:
                        logger.info(f"[EMA9_BLOCK] {stock_code} {stock_name}: close={curr_close:.0f} EMA9={ema9:.0f} prev_low={prev_low:.0f}")
                        console.print(f"[yellow]📈 [EMA9_BLOCK] {stock_name}: EMA9 pullback 미확인 (close={curr_close:.0f}, EMA9={ema9:.0f}) → 차단[/yellow]")
                        return
            except Exception:
                pass  # EMA9 계산 실패 시 통과

        # [EQ-3] VWAP Distance Filter: (price - vwap) / vwap <= max_pct
        # Mean Reversion Risk 차단 — VWAP 위 1.8% 이상 추격 진입 금지
        vwap_dist_enabled = eq_config.get('vwap_distance.enabled', True)
        vwap_dist_max = eq_config.get('vwap_distance.max_pct', 1.8) / 100
        if vwap_dist_enabled and not df.empty and 'vwap' in df.columns:
            try:
                vwap_val = float(df['vwap'].iloc[-1])
                curr_price = float(df['close'].iloc[-1])
                if vwap_val > 0:
                    vwap_dist = (curr_price - vwap_val) / vwap_val
                    if vwap_dist > vwap_dist_max:
                        logger.info(f"[VWAP_DIST_BLOCK] {stock_code} {stock_name}: dist={vwap_dist*100:.2f}% > {vwap_dist_max*100:.1f}%")
                        console.print(f"[yellow]📏 [VWAP_DIST] {stock_name}: VWAP 위 {vwap_dist*100:.2f}% > {vwap_dist_max*100:.1f}% → 추격 차단[/yellow]")
                        return
            except Exception:
                pass

        # [EQ-4] 수급 필터 (supply_demand_filter) — 절대값 + 변화율 이중 체크
        sd_filter_cfg = self.config.get('supply_demand_filter', {})
        if sd_filter_cfg.get('enabled', False):
            _sd_analysis = self.validated_stocks.get(stock_code, {}).get('analysis', {})
            _sd_scores   = _sd_analysis.get('scores', {})
            _sd_score    = float(_sd_scores.get('supply_demand', 50))
            _sd_min      = float(sd_filter_cfg.get('min_score', 20))
            _sd_warn     = float(sd_filter_cfg.get('warn_below', 40))
            # ① 절대값 차단
            if _sd_score < _sd_min:
                logger.info(f"[SD_FILTER_BLOCK] {stock_code} {stock_name}: supply_demand={_sd_score:.0f} < {_sd_min} → 진입 차단")
                console.print(f"[bold red]📉 [SD_BLOCK] {stock_name}: 수급점수 {_sd_score:.0f} < {_sd_min} → 차단[/bold red]")
                return
            elif _sd_score < _sd_warn:
                logger.info(f"[SD_FILTER_WARN] {stock_code} {stock_name}: supply_demand={_sd_score:.0f} < {_sd_warn} → 수급 주의")
            # ② 변화율 체크: 전일 대비 -N점 이상 하락 → 차단 (급락 수급 = 이미 끝난 종목)
            _sd_drop_thr = float(sd_filter_cfg.get('drop_block_threshold', -15))
            try:
                import json as _sj
                _cache_p = BASE_DIR / 'data' / 'sd_score_cache.json'
                if _cache_p.exists():
                    _cache = _sj.loads(_cache_p.read_text())
                    _entry = _cache.get(stock_code, {})
                    _prev_score = _entry.get('prev_score')
                    if _prev_score is not None:
                        _drop = _sd_score - float(_prev_score)
                        if _drop < _sd_drop_thr:
                            # ── Shakeout 예외: 가격 회복 + 강한 캔들 → 털기 후 재상승 패턴 ──
                            _shakeout_ok = False
                            if sd_filter_cfg.get('shakeout_exception', True):
                                try:
                                    _df_now = self.get_price_data(stock_code, count=10)
                                    if _df_now is not None and len(_df_now) >= 4:
                                        # 시간 필터: 수급 급락 후 N봉 이내 반등만 진짜 (질질 끄는 반등 = 의미 없음)
                                        _max_bars = int(sd_filter_cfg.get('shakeout_max_bars', 3))
                                        _price_recovery = float(_df_now['close'].iloc[-1]) > float(_df_now['close'].iloc[-3])
                                        _body = abs(float(_df_now['close'].iloc[-1]) - float(_df_now['open'].iloc[-1]))
                                        _avg_body = (_df_now['close'] - _df_now['open']).abs().iloc[-6:-1].mean()
                                        _strong_candle = (_body > float(_avg_body) * 1.2) if _avg_body > 0 else False
                                        # 거래량 방향성: 반등 봉의 거래량 > 직전 봉 거래량 (수급 동반 반등만 진짜)
                                        _vol_increasing = False
                                        if 'volume' in _df_now.columns and len(_df_now) >= 2:
                                            _vol_increasing = float(_df_now['volume'].iloc[-1]) > float(_df_now['volume'].iloc[-2])
                                        # 지지선 위치: EMA20 근처 반등만 의미 있음 (아무 위치 반등 = 데드캣 가능)
                                        _near_support = False
                                        _support_tol = float(sd_filter_cfg.get('shakeout_support_pct', 0.01))
                                        try:
                                            if 'ema_20' in _df_now.columns:
                                                _e20 = float(_df_now['ema_20'].iloc[-1])
                                            else:
                                                _e20 = float(_df_now['close'].ewm(span=20, adjust=False).mean().iloc[-1])
                                            _cur = float(_df_now['close'].iloc[-1])
                                            if _e20 > 0:
                                                _near_support = abs(_cur - _e20) / _e20 < _support_tol
                                        except Exception:
                                            _near_support = True  # EMA 계산 실패 시 보수적으로 허용
                                        # 시간 조건: 현재 봉이 저점(최저가 봉)으로부터 max_bars 이내
                                        _bars_since_low = _max_bars  # 기본 통과 (계산 실패 시 관대하게)
                                        try:
                                            _low_idx = int(_df_now['low'].iloc[-_max_bars*2:].idxmin()) if len(_df_now) >= _max_bars*2 else 0
                                            _bars_since_low = len(_df_now) - 1 - _low_idx
                                        except Exception:
                                            pass
                                        _quick_bounce = _bars_since_low <= _max_bars

                                        if _price_recovery and _strong_candle and _vol_increasing and _near_support and _quick_bounce:
                                            _shakeout_ok = True
                                            logger.info(
                                                f"[SD_DROP_SHAKEOUT] {stock_code} {stock_name}: "
                                                f"수급 {_drop:+.0f}점 급락이나 Shakeout 패턴 감지 (가격회복+강봉+거래량+지지+{_bars_since_low}봉반등) → 진입 허용"
                                            )
                                            console.print(
                                                f"[bold yellow]🔄 [SHAKEOUT] {stock_name}: 수급급락 예외 — 털기 후 재상승 패턴[/bold yellow]"
                                            )
                                except Exception:
                                    pass
                            if not _shakeout_ok:
                                logger.info(
                                    f"[SD_DROP_BLOCK] {stock_code} {stock_name}: "
                                    f"수급 {_prev_score:.0f} → {_sd_score:.0f} ({_drop:+.0f}) < {_sd_drop_thr} → 급락 차단"
                                )
                                console.print(
                                    f"[bold red]📉 [SD_DROP] {stock_name}: 수급 {_drop:+.0f}점 급락 → 차단[/bold red]"
                                )
                                return
            except Exception:
                pass

        # [EQ-5] 진입 전 위치 필터 — "이미 끝난 자리" 진입 차단
        # 핵심: EF 100%, TP1 없음의 근본 원인 = 3~5% 오른 자리 진입
        # ① 돌파점 이후 max_breakout_dist 이상 추격 금지
        # ② EMA20 이격 max_ema20_ext 이상 금지
        _pos_filter_cfg = self.config.get('entry_position_filter', {})
        if _pos_filter_cfg.get('enabled', True):
            _is_trend_or_expl = (
                str(entry_reason or '').startswith('EXPLORATION:')
                or str(entry_reason or '').startswith('TREND:')
                or str(entry_reason or '').startswith('RS:')
            )
            if not _is_trend_or_expl and not df.empty and len(df) >= 10:
                try:
                    _cur_p     = float(df['close'].iloc[-1])
                    # ① 돌파점: 최근 N봉 고가 (= 직전 저항 / 돌파 기준)
                    _lookback  = int(_pos_filter_cfg.get('breakout_lookback', 10))
                    _bo_high   = float(df['high'].iloc[-_lookback-1:-1].max())
                    _max_bo_dist = float(_pos_filter_cfg.get('max_distance_from_breakout', 0.02))
                    if _bo_high > 0:
                        _bo_dist = (_cur_p - _bo_high) / _bo_high
                        if _bo_dist > _max_bo_dist:
                            logger.info(
                                f"[POS_FILTER_BO] {stock_code} {stock_name}: "
                                f"돌파점 이격 {_bo_dist:.1%} > {_max_bo_dist:.0%} → 추격 진입 차단"
                            )
                            console.print(
                                f"[bold yellow]📍 [POS_BLOCK] {stock_name}: "
                                f"돌파점 +{_bo_dist:.1%} → 이미 지난 자리[/bold yellow]"
                            )
                            return
                    # ② EMA20 이격
                    _max_e20_ext = float(_pos_filter_cfg.get('max_extension_from_ema20', 0.03))
                    if 'ema_20' in df.columns:
                        _e20 = float(df['ema_20'].iloc[-1])
                        if _e20 > 0:
                            _e20_dist = (_cur_p - _e20) / _e20
                            if _e20_dist > _max_e20_ext:
                                logger.info(
                                    f"[POS_FILTER_EMA] {stock_code} {stock_name}: "
                                    f"EMA20 이격 {_e20_dist:.1%} > {_max_e20_ext:.0%} → 고점 진입 차단"
                                )
                                console.print(
                                    f"[bold yellow]📍 [POS_BLOCK] {stock_name}: "
                                    f"EMA20 +{_e20_dist:.1%} → 과도 이격[/bold yellow]"
                                )
                                return
                except Exception:
                    pass

        # [EQ-6] 레짐 기반 진입 차단 — CHOP/REVERSAL 구간 = 개인만 매매하는 털림 구간
        # EXPLORATION / TREND Breakout / A+ 진입은 자체 레짐 로직 있으므로 제외
        _regime_filter_cfg = self.config.get('entry_regime_filter', {})
        if _regime_filter_cfg.get('enabled', True):
            _exempt_reasons = ('EXPLORATION:', 'TREND:', 'A+:', 'RS:')
            _is_exempt = any(str(entry_reason or '').startswith(r) for r in _exempt_reasons)
            if not _is_exempt:
                try:
                    _cur_regime, _cur_regime_reason = self.market_context.get_regime()
                    _allowed = _regime_filter_cfg.get('allowed_regimes', ['TREND', 'NEUTRAL'])
                    if _cur_regime not in _allowed:
                        logger.info(
                            f"[REGIME_ENTRY_BLOCK] {stock_code} {stock_name}: "
                            f"레짐 {_cur_regime} 진입 차단 (허용: {_allowed})"
                        )
                        console.print(
                            f"[bold red]🌐 [REGIME_BLOCK] {stock_name}: "
                            f"{_cur_regime} — SMC 진입 차단[/bold red]"
                        )
                        self._record_blocked_entry(
                            stock_code, stock_name, "REGIME_BLOCK",
                            f"레짐={_cur_regime}", entry_reason
                        )
                        try:
                            getattr(self, '_kpi', {})['blocked_regimes'] = \
                                getattr(self, '_kpi', {}).get('blocked_regimes', 0) + 1
                        except Exception:
                            pass
                        return
                except Exception:
                    pass

        # 🔧 2026-04-30: Kill Switch / Market Sensor / DD HALT / EC HALT / Daily Loss / Max Trades
        # → _check_global_risk_gates()로 단일화. execute_buy에서 중복 제거.
        # 직접 호출 경로(_flush_pending_signals, gap-up reentry)도 호출 전 gate 통과 보장.

        # EC DD 5-tier 사이즈 보정 (차단 아님 — 사이즈만 조절)
        if hasattr(self, 'equity_ctrl') and self.config.get("equity_control", {}).get("enabled", True):
            _ec_mult, _ec_reason = self.equity_ctrl.get_drawdown_mult(self.total_assets)

            if _ec_mult < 1.0:
                # ③ Volatility × DD 결합: 고변동 구간에서 DD 페널티 강화
                try:
                    if df is not None and len(df) >= 20 and 'close' in df.columns:
                        _atr_cur = df['close'].diff().abs().tail(5).mean()
                        _atr_avg = df['close'].diff().abs().tail(20).mean()
                        if _atr_avg > 0:
                            _vol_adj = _atr_cur / _atr_avg
                            _vol_thr  = self.config.get('equity_control', {}).get('vol_dd_threshold', 1.5)
                            _vol_damp = self.config.get('equity_control', {}).get('vol_dd_damper',    0.8)
                            if _vol_adj > _vol_thr:
                                _ec_mult *= _vol_damp
                                logger.info(f"[EC_VOL] {stock_code}: vol_adj={_vol_adj:.2f}>{_vol_thr} → dd_mult×{_vol_damp}")
                except Exception:
                    pass

                # ④ Confidence 연속 boost (경계값 점프 없는 선형 함수)
                _ec_boosted = self.equity_ctrl.confidence_boost(_ec_mult, entry_confidence)
                if _ec_boosted > _ec_mult:
                    logger.info(f"[EC_BOOST] {stock_code}: conf={entry_confidence:.2f} → "
                                f"ec_mult {_ec_mult:.3f}→{_ec_boosted:.3f}")
                    _ec_mult = _ec_boosted

                position_size_mult *= _ec_mult
                logger.info(f"[EC_DD] {stock_code}: {_ec_reason} final_mult={position_size_mult:.3f}")
                console.print(f"[yellow]📉 [EC_DD] {stock_name}: {_ec_reason}[/yellow]")

        # 🔧 2026-04-05: PostgreSQL log_trade_events 기반 하드스탑
        signal_regime = self._infer_signal_regime(entry_reason)
        db_guard_ok, db_guard_reason = self._check_db_hard_stop_guard(signal_regime=signal_regime)
        if not db_guard_ok:
            logger.warning(f"[DB_HARD_STOP] {stock_code} {stock_name}: {db_guard_reason}")
            console.print(f"[bold red]🛑 [DB_HARD_STOP] {stock_name}: {db_guard_reason}[/bold red]")
            self._record_blocked_entry(stock_code, stock_name, "DB_HARD_STOP", db_guard_reason, entry_reason)
            return

        strategy_allowed, strategy_reason = is_strategy_allowed(
            regime=signal_regime,
            entry_reason=entry_reason or "",
            time_bucket=self._get_current_time_bucket(),
        )
        if not strategy_allowed:
            logger.warning(f"[SELF_OPT_BLOCK] {stock_code} {stock_name}: {strategy_reason}")
            console.print(f"[yellow]⛔ [SELF_OPT] {stock_name}: {strategy_reason}[/yellow]")
            self._record_blocked_entry(stock_code, stock_name, "SELF_OPT_BLOCK", strategy_reason, entry_reason)
            return

        # 🔧 2026-02-16: Conservative Mode 적용값 로드
        cm_config = self.config.get('risk_control.conservative_mode', {})
        cm_adj = self.reentry_metrics.get_conservative_adjustments(cm_config)
        if cm_adj['active'] and cm_adj['max_positions'] is not None:
            if len(self.positions) >= cm_adj['max_positions']:
                logger.info(f"[CONSERVATIVE_BLOCK] {stock_code} {stock_name}: 보유 {len(self.positions)}/{cm_adj['max_positions']} — 추가 진입 차단")
                console.print(
                    f"[bold yellow]⚠️ [CONSERVATIVE] {stock_name}: "
                    f"보유 {len(self.positions)}/{cm_adj['max_positions']} — 추가 진입 차단[/bold yellow]"
                )
                return

        # 🔧 2026-02-07 v2: exit_reason 기반 차등 쿨다운
        if stock_code in self.stock_cooldown:
            cooldown_data = self.stock_cooldown[stock_code]
            last_exit, is_loss = cooldown_data[0], cooldown_data[1]
            exit_reason = cooldown_data[2] if len(cooldown_data) > 2 else ''

            # v2: exit_reason → 표준 카테고리 → config 기반 쿨다운 시간 결정
            if self._cooldown_v2_enabled and self._cooldown_by_reason:
                reason_category = self._categorize_exit_reason(exit_reason)
                cooldown_required = self._cooldown_by_reason.get(
                    reason_category,
                    self._cooldown_by_reason.get('default', 30)
                )
            else:
                # fallback: 기존 v1 로직
                cooldown_required = self.loss_cooldown_minutes if is_loss else self.cooldown_minutes

            # 🔧 2026-02-16: Conservative Mode — 쿨다운 배수 적용
            if cm_adj['active'] and cm_adj['cooldown_mult'] != 1.0:
                cooldown_required = int(cooldown_required * cm_adj['cooldown_mult'])

            # 쿨다운 0분 → 차단하지 않음 (take_profit 등)
            if cooldown_required > 0:
                elapsed = (datetime.now() - last_exit).total_seconds() / 60
                if elapsed < cooldown_required:
                    reason_label = self._categorize_exit_reason(exit_reason) if self._cooldown_v2_enabled else ("손절" if is_loss else "익절")

                    # 쿨다운 차단 이벤트 기록 (override 판단 전에 먼저 기록 — R2 분모용)
                    from metrics.reentry_metrics import ReentryBlockedEvent
                    blocked_event = ReentryBlockedEvent(
                        timestamp=datetime.now(),
                        symbol=stock_code,
                        symbol_name=stock_name,
                        direction='long',
                        elapsed_min=elapsed,
                        cooldown_min=cooldown_required,
                        is_loss_cooldown=is_loss,
                        exit_reason=exit_reason,
                    )
                    self.reentry_metrics.record_blocked(blocked_event)

                    # 🔧 2026-02-07: 쿨다운 Override 체크 (강신호 bypass)
                    # 🔧 2026-02-08 R2: override_disabled_today 체크 추가
                    override_config = self.config.get('re_entry.reentry_cooldown.override_rules', {})
                    if (override_config.get('enabled', False)
                            and not self.reentry_metrics.override_disabled_today):
                        from metrics.reentry_metrics import check_cooldown_override
                        reason_cat = self._categorize_exit_reason(exit_reason)
                        can_override, override_reason = check_cooldown_override(df, reason_cat, override_config)
                        if can_override:
                            console.print(
                                f"[green]⚡ {stock_name}: 쿨다운 Override! "
                                f"[{reason_label}] {override_reason}[/green]"
                            )
                            self.reentry_metrics.record_override()

                            # 🔧 R2: Override 남용 방지 체크
                            abuse_config = override_config.get('override_abuse_guard', {})
                            is_abused, abuse_msg = self.reentry_metrics.check_override_abuse(abuse_config)
                            if is_abused:
                                console.print(f"[red]⚠️  {abuse_msg}[/red]")

                            del self.stock_cooldown[stock_code]
                            # fall through to buy logic below
                        else:
                            remaining = cooldown_required - elapsed
                            logger.info(f"[COOLDOWN_BLOCK] {stock_code} {stock_name}: [{reason_label}] 쿨다운 {remaining:.1f}분 남음 (총 {cooldown_required}분)")
                            console.print(f"[yellow]⏸️  {stock_name}: [{reason_label}] 쿨다운 {remaining:.1f}분 남음 (총 {cooldown_required}분)[/yellow]")
                            try:
                                from database.decision_trace import insert_blocked_trade
                                insert_blocked_trade(stock_code, stock_name, 'COOLDOWN_BLOCK', df, price, entry_reason)
                            except Exception:
                                pass
                            return
                    else:
                        remaining = cooldown_required - elapsed
                        disabled_tag = " [override 비활성화]" if self.reentry_metrics.override_disabled_today else ""
                        logger.info(f"[COOLDOWN_BLOCK] {stock_code} {stock_name}: [{reason_label}] 쿨다운 {remaining:.1f}분 남음 (총 {cooldown_required}분){disabled_tag}")
                        console.print(f"[yellow]⏸️  {stock_name}: [{reason_label}] 쿨다운 {remaining:.1f}분 남음 (총 {cooldown_required}분){disabled_tag}[/yellow]")
                        try:
                            from database.decision_trace import insert_blocked_trade
                            insert_blocked_trade(stock_code, stock_name, 'COOLDOWN_BLOCK', df, price, entry_reason)
                        except Exception:
                            pass
                        return
            # 쿨다운 만료 또는 0분 → 제거
            del self.stock_cooldown[stock_code]

        # 🔧 CRITICAL FIX: 이미 포지션이 있으면 추가 매수 금지 (중복 매수 방지)
        if stock_code in self.positions:
            existing_qty = self.positions[stock_code].get('quantity', 0)
            if existing_qty > 0:
                logger.info(f"[DUPLICATE_BLOCK] {stock_code} {stock_name}: 이미 보유 중 ({existing_qty}주) — 추가 매수 금지")
                console.print(f"[yellow]⚠️  {stock_name}: 이미 보유 중 ({existing_qty}주) - 추가 매수 금지[/yellow]")
                return

        # 🔴 GPT 개선: 종목별 일일 거래 제한 (과도한 집중 방지)
        today_trade_count = self.daily_trade_count.get(stock_code, 0)
        if today_trade_count >= self.max_trades_per_stock_per_day:
            logger.info(f"[DAILY_STOCK_LIMIT] {stock_code} {stock_name}: 일일 거래 한도 초과 ({today_trade_count}/{self.max_trades_per_stock_per_day}회)")
            console.print(f"[red]🚫 {stock_name}: 일일 거래 한도 초과 ({today_trade_count}/{self.max_trades_per_stock_per_day}회)[/red]")
            return

        console.print()
        console.print("=" * 80, style="green")
        console.print(f"🔔 매수 신호 발생: {stock_name} ({stock_code})", style="bold green")
        console.print(f"   가격: {price:,.0f}원")
        console.print(f"   시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # 실계좌 기반 포지션 크기 계산
        if not self.risk_manager:
            logger.error(f"[NO_RISK_MGR] {stock_code} {stock_name}: 리스크 관리자 미초기화 — 매수 불가")
            console.print("[red]❌ 리스크 관리자가 초기화되지 않았습니다.[/red]")
            return

        trailing_cfg = self.config.get_trailing_config()
        stop_loss_pct = trailing_cfg.get('stop_loss_pct', getattr(self.analyzer, 'stop_loss_pct', 3.0))

        # 🔧 2026-03-31: DEFENSIVE 모드 — 타이트 손절로 재정의
        _is_defensive = str(entry_reason or '').startswith('DEFENSIVE:')
        if _is_defensive and stop_loss_pct_override is not None:
            stop_loss_pct = stop_loss_pct_override

        # 손절가 계산 (설정 기반)
        stop_loss_price = price * (1 - stop_loss_pct / 100)

        # 🔧 FIX: 포지션 크기 계산 (진입 신뢰도 반영)
        position_calc = self.risk_manager.calculate_position_size(
            current_balance=self.current_cash,
            current_price=price,
            stop_loss_price=stop_loss_price,
            entry_confidence=entry_confidence
        )

        # 🔧 2026-02-16: Conservative Mode — 포지션 사이즈 축소
        if cm_adj['active']:
            position_size_mult *= cm_adj['position_size_mult']
            console.print(f"[yellow]⚠️ [CONSERVATIVE] 포지션 사이즈 {cm_adj['position_size_mult']*100:.0f}% 적용[/yellow]")

        # 🔧 2026-02-19: Loss Streak Guard — 포지션 사이즈 축소
        # 🔧 2026-03-19: 고확신 passthrough + 탈출 부스트
        # 🔧 2026-04-01: A+ — LSG 완전 우회 (시스템 최상위 신호)
        lsg_config = self.config.get('risk_control.loss_streak_guard', {})
        lsg_adj = self.reentry_metrics.get_loss_streak_adjustments(lsg_config)
        _a_plus_bypass_lsg = _is_a_plus and self.config.get('smc.choch_grade.grade_a_plus.bypass_lsg', True)
        if _a_plus_bypass_lsg:
            logger.info(f"[A_PLUS_LSG_BYPASS] {stock_code} | A+ 신호 → LSG 우회 (streak={lsg_adj.get('consecutive', 0)})")
        elif lsg_adj['active']:
            _lsg_min_conf = lsg_adj['min_confidence_override']
            _lsg_passthrough = lsg_adj['high_conf_passthrough']
            if _lsg_passthrough and entry_confidence < _lsg_min_conf:
                logger.info(f"[LSG_BLOCK] {stock_code} | conf={entry_confidence:.2f} < {_lsg_min_conf} | streak={lsg_adj['consecutive']}")
                self._record_blocked_entry(stock_code, stock_name, "LSG_BLOCK", f"conf={entry_confidence:.2f}<{_lsg_min_conf} streak={lsg_adj['consecutive']}", entry_reason)
                return
            position_size_mult *= lsg_adj['position_size_mult']
            logger.info(f"[LSG_PASS] {stock_code} | conf={entry_confidence:.2f} >= {_lsg_min_conf} | size={lsg_adj['position_size_mult']*100:.0f}% | streak={lsg_adj['consecutive']}")
            console.print(f"[red]🛡️ [LOSS STREAK] 포지션 {lsg_adj['position_size_mult']*100:.0f}% "
                         f"({lsg_adj['consecutive']}연패 | conf={entry_confidence:.2f})[/red]")
        elif lsg_adj.get('lsg_just_released'):
            # 🔧 2026-03-19: LSG 탈출 직후 1회 부스트 — 70% 사이즈로 소프트 재진입
            _boost_mult = lsg_adj['release_boost_mult']
            position_size_mult *= _boost_mult
            logger.info(f"[LSG_BOOST] {stock_code} | 탈출 부스트 size={_boost_mult*100:.0f}% | conf={entry_confidence:.2f}")
            console.print(f"[green]🛡️→✅ [LSG 탈출] 부스트 진입 {_boost_mult*100:.0f}% | {stock_name}[/green]")

        # ── 2026-03-31: DriftDetector 게이트 ─────────────────────────────────
        # EMERGENCY_STOP → 진입 차단 / REDUCE_SIZE|RETRAIN → size_mult 감산
        try:
            _d_level, _d_reason = self.drift_detector.get_drift_level()
            from analysis.drift_detector import DriftLevel as _DL
            if _d_level == _DL.EMERGENCY_STOP:
                logger.info(f"[DRIFT_BLOCK] {stock_code}: {_d_reason}")
                console.print(f"[bold red]🚨 [DRIFT_BLOCK] {stock_name}: {_d_reason} — 진입 차단[/bold red]")
                return
            elif _d_level in (_DL.REDUCE_SIZE, _DL.RETRAIN):
                _d_mult = self.drift_detector.get_size_mult()
                position_size_mult *= _d_mult
                logger.info(f"[DRIFT_REDUCE] {stock_code}: size×{_d_mult:.1f} ({_d_reason})")
                console.print(f"[yellow]📉 [DRIFT] {stock_name}: {_d_level.value} → size×{_d_mult:.1f}[/yellow]")
        except Exception as _de:
            logger.debug(f"[DRIFT_SKIP] {stock_code}: {_de}")

        # ── 2026-03-31: Regime + Kelly 사이즈 보정 ───────────────────────────
        # EXPLORATION: 탐색 데이터 생성용 → fixed size 유지, Kelly 완전 스킵
        # A+: 시스템 최상위 신호 → Kelly 우회, size = 1.0R (YAML bypass_kelly 기준)
        # 그 외: 레짐 BEAR 또는 Kelly 낮을 때 size 감산 (증폭 없음)
        _a_plus_bypass_kelly = _is_a_plus and self.config.get('smc.choch_grade.grade_a_plus.bypass_kelly', True)
        if _is_exploration:
            logger.info(f"[SIZE_SKIP] {stock_code}: EXPLORATION fixed size, Kelly 스킵")
        elif _a_plus_bypass_kelly:
            logger.info(f"[A_PLUS_KELLY_BYPASS] {stock_code}: A+ 신호 → Kelly 우회, size={position_size_mult:.2f}")
            console.print(f"[bold green]🔥 [A+] {stock_name}: Kelly 우회 → size×{position_size_mult:.2f}[/bold green]")
        else:
            try:
                from core.regime_detector import classify_regime_from_df as _clf_regime
                _regime, _regime_reason = _clf_regime(df) if df is not None and len(df) >= 65 else ("SIDE", "데이터부족")
                _n_trades = self._trade_stats.n_trades

                # ① Cold Start: 거래 데이터 부족 → Kelly 신뢰 불가, 고정 배율 사용
                if _n_trades < 20:
                    _kelly_mult = 0.6
                    logger.info(
                        f"[SIZE_COLD] {stock_code}: trades={_n_trades}<20 → cold start Kelly×{_kelly_mult:.1f}"
                    )
                else:
                    _wr, _aw, _al = self._trade_stats.get_kelly_inputs()
                    self._position_sizer.update_capital(self.current_cash)
                    _ps_result = self._position_sizer.get_size_for_strategy(
                        strategy="smc",
                        regime=_regime,
                        atr=float(df['close'].diff().abs().mean()) if df is not None and 'close' in df.columns else 0.0,
                        price=price,
                        win_rate=_wr,
                        avg_win=_aw,
                        avg_loss=_al,
                    )
                    _kelly_mult = min(_ps_result.fraction / 0.5, 1.0)
                    logger.info(
                        f"[SIZE] {stock_code} regime={_regime} kelly={_ps_result.kelly_raw:.3f} "
                        f"fraction={_ps_result.fraction:.3f} → mult×{_kelly_mult:.2f} "
                        f"(trades={_n_trades})"
                    )

                if _kelly_mult < 0.8:
                    position_size_mult *= _kelly_mult
                    console.print(
                        f"[dim]📐 [SIZE] {stock_name}: Kelly×{_kelly_mult:.2f} "
                        f"regime={_regime} trades={_n_trades}[/dim]"
                    )

                # ② Confidence 결합: final_size = base × Kelly × (0.5 + conf)
                # conf=1.0 → ×1.5(최대), conf=0.5 → ×1.0(중립), conf=0.0 → ×0.5(반감)
                _conf_mult = 0.5 + float(entry_confidence)
                _conf_mult = max(0.5, min(1.5, _conf_mult))   # 클램핑
                position_size_mult *= _conf_mult
                logger.info(
                    f"[SIZE_CONF] {stock_code}: conf={entry_confidence:.2f} → ×{_conf_mult:.2f} "
                    f"(final_mult={position_size_mult:.3f})"
                )

            except Exception as _se:
                logger.debug(f"[SIZE_SKIP] {stock_code}: {_se}")

        # ── [PAT_DUP] 동일 패턴 중복 진입 방지 ───────────────────────────────────
        _pat_raw_dedup = getattr(self, '_daily_patterns', {}).get(stock_code) or {}
        _pat_dedup_key = (
            f"{stock_code}:{_pat_raw_dedup.get('pattern','')}({_pat_raw_dedup.get('phase','')})"
            if _pat_raw_dedup else ""
        )
        if _pat_dedup_key and _pat_dedup_key in self._active_pattern_positions:
            logger.info(f'[PAT_DUP] {stock_code}: {_pat_dedup_key} 동일 패턴 이미 보유 → 중복 차단')
            console.print(f'[yellow]📐 [PAT_DUP] {stock_name}: 동일 패턴 중복 진입 차단[/yellow]')
            return

        # ── [PATTERN_SIZER] edge 기반 사이징 보정 (score_enabled=true 시 활성) ──
        _ps_edge = 0.0   # [POS] 로그용 — try 블록 밖에서 참조
        _ps_conf = 0.0
        try:
            _pat_cfg = self.config.get('pattern_recognition', {})
            if _pat_cfg.get('score_enabled', False):
                from trading.pattern_sizer import compute as _ps_compute

                # DEFENSIVE 첫 진입: confidence 하한 완화 (0.85 → 0.80) — 데이터 확보
                _min_conf_ov = None
                if _atr_mode == "DEFENSIVE" and self._daily_atr_defensive_count == 0:
                    _min_conf_ov = 0.80
                    logger.info(f'[PATTERN_SIZER] DEFENSIVE 첫 진입 완화: min_conf 0.85→0.80 {stock_code}')

                _ps = _ps_compute(
                    pat_dict          = getattr(self, '_daily_patterns', {}).get(stock_code),
                    entry_confidence  = entry_confidence,
                    balance           = self.current_cash,
                    entry             = price,
                    stop              = float(
                        self.positions.get(stock_code, {}).get('stop_loss_price', 0)
                        or price * 0.97
                    ),
                    market_mode       = _atr_mode,
                    min_conf_override = _min_conf_ov,
                )
                _ps_edge = _ps.get('edge', 0.0)
                _pat_raw = getattr(self, '_daily_patterns', {}).get(stock_code) or {}
                _ps_conf = float(_pat_raw.get('confidence', 0.0))

                if _ps['skip']:
                    logger.info(
                        f'[PATTERN_SIZER_SKIP] {stock_code}: {_ps["reason"]} → 진입 차단'
                    )
                    console.print(
                        f'[yellow]📐 [PAT_SIZE] {stock_name}: edge 부족 → 진입 차단[/yellow]'
                    )
                    return
                if _ps['mult'] != 1.0 and _ps['edge'] > 0:
                    position_size_mult *= _ps['mult']
                    logger.info(
                        f'[PATTERN_SIZER] {stock_code}: {_ps["reason"]} '
                        f'| qty_hint={_ps["qty_hint"]} | final_mult={position_size_mult:.3f}'
                    )
                    console.print(
                        f'[magenta]📐 [PAT_SIZE] {stock_name}: '
                        f'edge={_ps["edge"]:.3f} → ×{_ps["mult"]:.2f} '
                        f'(hint={_ps["qty_hint"]}주)[/magenta]'
                    )
        except Exception as _pse:
            logger.debug(f'[PATTERN_SIZER_ERR] {stock_code}: {_pse}')

        # 🔧 2026-04-24: Pattern Quality 필터 (expectancy 기반)
        # avg_win/avg_loss → expectancy < 0 인 패턴은 size × 0.7
        try:
            if hasattr(self, 'equity_ctrl') and self.config.get("equity_control", {}).get("enabled", True):
                _pat_info = getattr(self, '_daily_patterns', {}).get(stock_code) or {}
                if _pat_info:
                    _pq_phase = f'{_pat_info.get("pattern","")}({_pat_info.get("phase","")})'
                    _pq_mult, _pq_reason = self.equity_ctrl.get_pattern_quality_mult(_pq_phase)
                    if _pq_mult < 1.0:
                        position_size_mult *= _pq_mult
                        logger.info(f'[PQ_FILTER] {stock_code}: {_pq_reason} → final={position_size_mult:.3f}')
                        console.print(f'[dim]📉 [PQ] {stock_name}: {_pq_reason}[/dim]')
        except Exception as _pqe:
            logger.debug(f'[PQ_FILTER_ERR] {stock_code}: {_pqe}')

        # 🔧 2026-04-24: OnlineStats 실시간 expectancy 기반 사이징
        try:
            if hasattr(self, 'online_stats') and self.config.get('online_stats', {}).get('enabled', True):
                _ol_mult, _ol_reason = self.online_stats.get_size_mult()
                if _ol_mult != 1.0:
                    position_size_mult *= _ol_mult
                    logger.info(f'[OL_SIZE] {stock_code}: {_ol_reason} → final={position_size_mult:.3f}')
                    if _ol_mult < 0.9:
                        console.print(f'[dim]📉 [OL] {stock_name}: {_ol_reason}[/dim]')
        except Exception as _ole2:
            logger.debug(f'[OL_SIZE_ERR] {stock_code}: {_ole2}')
        # ─────────────────────────────────────────────────────────────────────

        # [SIZE] 사이즈 구성 요소 로그 — 성과 분석 시 time_weight 영향 검증용
        logger.info(
            f"[SIZE] {stock_code} {stock_name} | "
            f"strategy={_entry_strat_tag} "
            f"base={_initial_mult:.3f} "
            f"× time({_entry_strat_tag})={_time_weight:.2f} "
            f"→ final={position_size_mult:.3f} "
            f"| base_qty={position_calc['quantity']:.1f}"
        )

        # SignalOrchestrator의 포지션 조정 반영
        # 🔧 FIX: 최소 1주 보장 (이중 축소 방지)
        raw_quantity = position_calc['quantity'] * position_size_mult
        quantity = int(max(1, int(raw_quantity))) if position_calc['quantity'] >= 1 else 0  # Python int() 명시
        amount = position_calc['investment'] * position_size_mult

        # [POS] 최종 리스크 로그 (ATR 모드 / MDD 원인 분석용)
        _invest_ratio  = amount / self.current_cash if self.current_cash > 0 else 0.0
        _total_exp_now = (self.positions_value + amount) / self.total_assets if self.total_assets > 0 else 0.0
        logger.info(
            f"[POS] {stock_code} {stock_name} "
            f"mode={_atr_mode} mult={position_size_mult:.3f} "
            f"edge={_ps_edge:.3f} conf={_ps_conf:.2f} "
            f"qty={quantity} amount={int(amount):,} "
            f"invest_ratio={_invest_ratio:.4f} total_exp={_total_exp_now:.4f}"
        )
        # DEFENSIVE 모드에서 투자 비중이 5% 초과하면 경고 (이중 사이징 검증용)
        if _atr_mode == "DEFENSIVE" and _invest_ratio > 0.05:
            logger.warning(
                f"[POS_WARN] DEFENSIVE invest_ratio={_invest_ratio:.3f} > 5% "
                f"— risk_manager 설정 확인 필요 {stock_code}"
            )

        # [DEF_LIMIT] DEFENSIVE 일일 누적 투자 비중 캡 (계좌의 10%)
        if _atr_mode == "DEFENSIVE":
            _def_max_exp = self.config.get('market_context', {}).get(
                'atr_tiers', {}
            ).get('defensive_max_daily_exposure', 0.10)
            _cum_exp = self._daily_atr_defensive_exposure + _invest_ratio
            if _cum_exp > _def_max_exp:
                logger.info(
                    f'[DEF_LIMIT] {stock_code}: '
                    f'누적={self._daily_atr_defensive_exposure:.3f}+{_invest_ratio:.3f}'
                    f'={_cum_exp:.3f} > {_def_max_exp} → 차단'
                )
                console.print(
                    f'[yellow]🛡️ [DEF_LIMIT] {stock_name}: '
                    f'DEFENSIVE 일일 비중 한도 초과({_cum_exp:.1%} > {_def_max_exp:.0%}) → 차단[/yellow]'
                )
                return

        # [PORT_LIMIT] 포트폴리오 총 노출도 캡 (청산 중 포지션 제외로 레이스컨디션 보정)
        _max_total_exp = self.config.get('risk_control', {}).get('max_total_exposure', 0.30)
        if self.total_assets > 0:
            _effective_pos_val = max(0.0, self.positions_value - self._pending_exit_value)
            _cur_port_exp = _effective_pos_val / self.total_assets
            _new_port_exp = amount / self.total_assets
            if _cur_port_exp + _new_port_exp > _max_total_exp:
                logger.info(
                    f'[PORT_LIMIT] {stock_code}: '
                    f'현재={_cur_port_exp:.3f}(pending_exit={self._pending_exit_value:.0f}제외)'
                    f'+신규={_new_port_exp:.3f}={_cur_port_exp+_new_port_exp:.3f} > {_max_total_exp} → 차단'
                )
                console.print(
                    f'[yellow]📊 [PORT_LIMIT] {stock_name}: '
                    f'총 노출도 한도 초과 '
                    f'({_cur_port_exp+_new_port_exp:.1%} > {_max_total_exp:.0%}) → 차단[/yellow]'
                )
                return

        # [SECTOR_LIMIT] 섹터 집중 리스크 캡 — ATR 모드별 동적 조정
        try:
            _base_sector_exp = self.config.get('risk_control', {}).get('max_sector_exposure', 0.20)
            _max_sector_exp = {
                "AGGRESSIVE": _base_sector_exp * 1.25,  # 상승장 → 집중 허용
                "NORMAL":     _base_sector_exp,
                "DEFENSIVE":  _base_sector_exp * 0.75,  # 횡보장 → 분산 강제
            }.get(_atr_mode, _base_sector_exp)
            _sector_map_path = Path('data/sector_map.json')
            if _sector_map_path.exists() and self.total_assets > 0:
                import json as _json
                _smap_raw = _json.loads(_sector_map_path.read_text(encoding='utf-8'))
                # code → sector 역방향 조회
                _code_to_sector = {
                    code: sec
                    for sec, codes in _smap_raw.get('sectors', {}).items()
                    for code in codes
                }
                _my_sector = _code_to_sector.get(stock_code)
                if _my_sector:
                    # 현재 동일 섹터 포지션 가치 합산
                    _sec_val = sum(
                        float(pos.get('quantity', 0)) * float(pos.get('entry_price', 0))
                        for sc, pos in self.positions.items()
                        if _code_to_sector.get(sc) == _my_sector
                    )
                    _sec_exp     = _sec_val / self.total_assets
                    _sec_new_exp = amount / self.total_assets
                    if _sec_exp + _sec_new_exp > _max_sector_exp:
                        logger.info(
                            f'[SECTOR_LIMIT] {stock_code} 섹터={_my_sector} '
                            f'mode={_atr_mode} 한도={_max_sector_exp:.2f}: '
                            f'현재={_sec_exp:.3f}+신규={_sec_new_exp:.3f}'
                            f'={_sec_exp+_sec_new_exp:.3f} → 차단'
                        )
                        console.print(
                            f'[yellow]🏭 [SECTOR_LIMIT] {stock_name}: '
                            f'섹터({_my_sector}) 집중 한도 초과({_atr_mode}) '
                            f'({_sec_exp+_sec_new_exp:.1%} > {_max_sector_exp:.0%}) → 차단[/yellow]'
                        )
                        return
        except Exception as _se:
            logger.debug(f'[SECTOR_LIMIT_ERR] {stock_code}: {_se}')

        # 진입 가능 여부 확인
        can_enter, reason = self.risk_manager.can_open_position(
            current_balance=self.current_cash,
            current_positions_value=self.positions_value,
            position_count=len(self.positions),
            position_size=amount
        )

        if not can_enter:
            logger.info(f"[CAN_ENTER_BLOCK] {stock_code} {stock_name}: {reason}")
            console.print(f"[yellow]⚠️  매수 불가: {reason}[/yellow]")
            console.print("=" * 80, style="yellow")
            try:
                from database.decision_trace import insert_blocked_trade
                insert_blocked_trade(stock_code, stock_name, 'CAN_ENTER_BLOCK', df, price, entry_reason)
            except Exception:
                pass
            return

        console.print(f"[dim]📊 포지션 계산:[/dim]")
        console.print(f"[dim]   - 투자금액: {amount:,.0f}원 (리스크: {position_calc['risk_amount']:,.0f}원)[/dim]")
        console.print(f"[dim]   - 매수수량: {quantity}주[/dim]")
        console.print(f"[dim]   - 포지션비율: {position_calc['position_ratio']:.1f}%[/dim]")
        console.print(f"[dim]   - 포지션 조정 배수: {position_size_mult*100:.0f}%[/dim]")

        # 🔧 CRITICAL FIX: 수량 검증 (0주 주문 방지)
        if quantity <= 0:
            logger.warning(f"[ZERO_QTY_BLOCK] {stock_code} {stock_name}: 계산된 수량 0주 (잔고={self.current_cash:,.0f}, 가격={price:,.0f}, 배수={position_size_mult:.2f})")
            console.print(f"[yellow]⚠️  매수 불가: 계산된 수량이 0주입니다.[/yellow]")
            console.print(f"[yellow]   잔고: {self.current_cash:,.0f}원, 가격: {price:,.0f}원[/yellow]")
            console.print(f"[yellow]   계산된 수량: {position_calc['quantity']:.2f} × {position_size_mult:.2f} = {quantity}주[/yellow]")
            console.print("=" * 80, style="yellow")
            try:
                from database.decision_trace import insert_blocked_trade
                insert_blocked_trade(stock_code, stock_name, 'ZERO_QTY_BLOCK', df, price, entry_reason)
            except Exception:
                pass
            return

        # ── ML 진입 필터 게이트 ─────────────────────────────────────────
        # shadow_mode=true  : 로그만 남기고 진입 허용 (검증 단계)
        # shadow_mode=false : prob < threshold → 차단
        _ml_decision_id = None
        try:
            _ml_cfg = self.config.get('ml_filter', {})
            if _ml_cfg.get('enabled', False):
                from analysis.ml_pipeline import predict_entry_quality, load_latest_model
                from database.decision_trace import log_ml_decision, _extract_features_from_df

                _ml_shadow    = bool(_ml_cfg.get('shadow_mode', True))
                _ml_threshold = float(_ml_cfg.get('threshold', 0.40))

                _, _ml_meta   = load_latest_model()
                _ml_version   = (_ml_meta.get('trained_at', '')[:10] if _ml_meta else 'none')

                _ml_etype = (
                    'EXPLORATION' if 'EXPLORATION' in (entry_reason or '')
                    else 'TREND'  if 'TREND'       in (entry_reason or '')
                    else 'SMC'    if 'SMC'          in (entry_reason or '')
                    else 'DEFENSIVE' if _is_defensive
                    else 'OTHER'
                )
                _ml_result = predict_entry_quality(df, price, extra={
                    'entry_type':     _ml_etype,
                    'choch_grade':    'N/A',
                    'market_context': mc_tag or 'UNKNOWN',
                })
                _ml_prob = _ml_result.get('win_prob')   # None = 모델 없음

                # 피처 스냅샷 (로그 품질 핵심)
                _ml_feats = _extract_features_from_df(df, price) if df is not None else {}
                _ml_feats['entry_type'] = _ml_etype

                _ml_rollout  = int(_ml_cfg.get('rollout_pct', 100))
                _in_rollout  = (
                    _ml_prob is not None
                    and not _ml_shadow
                    and _ml_prob < _ml_threshold
                    and (__import__('random').random() * 100 <= _ml_rollout)
                )
                _ml_blocked = _in_rollout

                _ml_decision_id = log_ml_decision(
                    stock_code=stock_code,
                    prob=_ml_prob,
                    threshold=_ml_threshold,
                    model_version=_ml_version,
                    shadow_mode=_ml_shadow,
                    blocked=_ml_blocked,
                    features=_ml_feats,
                    trade_id=None,      # trade_id 아직 없음 → BUY 후 UPDATE
                    entry_type=_ml_etype,
                )

                if _ml_blocked:
                    logger.info(
                        f"[ML_BLOCK] {stock_code} {stock_name}: "
                        f"prob={_ml_prob:.3f} < thr={_ml_threshold} "
                        f"rollout={_ml_rollout}% → 진입 차단"
                    )
                    console.print(
                        f"[red]🤖 ML 차단: {stock_name} "
                        f"prob={_ml_prob:.3f} < {_ml_threshold}[/red]"
                    )
                    try:
                        from database.decision_trace import insert_blocked_trade
                        insert_blocked_trade(
                            stock_code, stock_name, 'ML_BLOCK',
                            df, price, entry_reason
                        )
                    except Exception:
                        pass
                    return
                elif not _ml_shadow and _ml_prob is not None and _ml_prob < _ml_threshold:
                    # rollout 확률에서 살아남은 경우 — 로그만
                    logger.info(
                        f"[ML_ROLLOUT_BYPASS] {stock_code}: "
                        f"prob={_ml_prob:.3f} < thr={_ml_threshold} "
                        f"but rollout_pct={_ml_rollout}% → 통과"
                    )
        except Exception as _ml_e:
            logger.debug(f"[ML_GATE] {stock_code} 오류 (무시): {_ml_e}")

        # ── EQ ML Filter (Entry Quality, 2026-05-01) ─────────────────────
        _eq_pwin_final = None   # size scaling에서 사용
        try:
            if getattr(self, 'eq_model', None) and self.eq_model.enabled:
                _eq_feat = self._build_eq_features(stock_code, price, df, entry_confidence, entry_reason)
                _eq_block, _eq_pwin = self.eq_model.should_block(_eq_feat)
                _eq_pwin_final = _eq_pwin
                _eq_tag = '[EQ_SHADOW]' if self.eq_model.shadow_mode else '[EQ_FILTER]'
                logger.info(
                    f"{_eq_tag} {stock_code} {stock_name}: "
                    f"P(win)={_eq_pwin:.2f} thr={self.eq_model.threshold:.2f} "
                    f"choch={_eq_feat.get('choch_grade','?')} "
                    f"regime={_eq_feat.get('regime','?')}"
                )
                if _eq_block:
                    logger.info(f"[EQ_BLOCK] {stock_code}: P(win)={_eq_pwin:.2f} → 진입 차단")
                    return
        except Exception as _eq_e:
            logger.debug(f"[EQ_GATE] {stock_code} 오류 (무시): {_eq_e}")

        # ── EQ Size Scaling (P(win) → 수량 조정, shadow_mode=False 전용) ────
        try:
            _eq_scale_cfg = (self.config.get('eq_ml_filter') or {}).get('size_scaling', {})
            if (
                _eq_scale_cfg.get('enabled', False)
                and _eq_pwin_final is not None
                and getattr(self, 'eq_model', None)
                and self.eq_model.enabled
                and not self.eq_model.shadow_mode
                and self.eq_model._model is not None
            ):
                _pwin = _eq_pwin_final
                _thr  = self.eq_model.threshold
                if _pwin >= _eq_scale_cfg.get('high_threshold', 0.60):
                    _eq_sz_mult = float(_eq_scale_cfg.get('high_mult', 1.2))
                elif _pwin < _thr:
                    _eq_sz_mult = float(_eq_scale_cfg.get('low_mult', 0.8))
                else:
                    _eq_sz_mult = 1.0

                if abs(_eq_sz_mult - 1.0) > 0.01:
                    _old_qty = quantity
                    quantity = max(1, int(quantity * _eq_sz_mult))
                    amount   = amount * _eq_sz_mult
                    logger.info(
                        f"[EQ_SIZE] {stock_code} P(win)={_pwin:.2f} "
                        f"×{_eq_sz_mult:.1f} {_old_qty}→{quantity}주"
                    )
        except Exception as _eq_sz_e:
            logger.debug(f"[EQ_SIZE_ERR] {stock_code}: {_eq_sz_e}")

        # Dry-run 모드 체크
        if self.dry_run_mode:
            console.print()
            console.print("[cyan]🔍 [DRY-RUN] 백테스트 모드: 실제 주문 생략[/cyan]")
            console.print(f"[cyan]   → 매수 시그널 확인 완료: {stock_name} ({stock_code})[/cyan]")
            console.print(f"[cyan]   → 예상 수량: {quantity}주, 예상 금액: {amount:,.0f}원[/cyan]")
            console.print("=" * 80, style="cyan")
            return

        # 실제 키움 API 매수 주문
        try:
            console.print(f"[yellow]📡 키움 API 매수 주문 전송 중...[/yellow]")

            # 🔧 호가단위 적용
            buy_price = self._adjust_price_to_tick(price)
            console.print(f"[dim]  지정가 설정: {buy_price:,}원 (호가단위 조정)[/dim]")

            order_result = self.api.order_buy(
                stock_code=stock_code,
                quantity=quantity,
                price=buy_price,  # int(price) → buy_price (호가단위)
                trade_type="0"  # 지정가 주문
            )

            if order_result.get('return_code') != 0:
                console.print(f"[red]❌ 매수 주문 실패: {order_result.get('return_msg')}[/red]")
                return

            order_no = order_result.get('ord_no')
            console.print(f"[green]✓ 매수 주문 성공 - 주문번호: {order_no}[/green]")
            if _atr_mode == "DEFENSIVE":
                self._daily_atr_defensive_count += 1
                self._daily_atr_defensive_exposure += _invest_ratio
            if _pat_dedup_key:
                self._active_pattern_positions.add(_pat_dedup_key)

        except Exception as e:
            console.print(f"[red]❌ 매수 API 호출 실패: {e}[/red]")
            return

        # ✅ EOD Manager Phase 1: 진입 시점 overnight 판단
        # choch_grade는 호출자(check_entry_signal)에서 kwargs로 전달되거나 포지션 dict에서 읽힘
        _entry_choch_grade = kwargs.get('choch_grade') if 'kwargs' in dir() else None
        allow_overnight, overnight_score = self.should_allow_overnight(
            stock_code=stock_code,
            df=df,
            signal_result={},  # 필요 시 확장 가능
            entry_confidence=entry_confidence,
            choch_grade=_entry_choch_grade,
        )

        # 포지션 생성
        entry_time = datetime.now()
        self.positions[stock_code] = {
            'stock_name': stock_name,  # 잔고 조회와 통일
            'name': stock_name,  # 하위 호환성
            'avg_price': price,  # 잔고 조회와 통일
            'entry_price': price,  # 하위 호환성
            'entry_time': entry_time,
            'entry_date': entry_time,  # 보유일 계산용
            'quantity': quantity,
            'initial_quantity': quantity,  # 초기 수량 (부분 청산 추적용)
            'current_price': price,  # 초기 현재가
            'highest_price': price,
            'trailing_active': False,
            'trailing_stop_price': None,
            'trade_id': None,  # DB trade_id 저장용
            'partial_exit_stage': 0,  # 부분 청산 단계 (0: 미진행, 1: 1차 완료, 2: 2차 완료)
            'total_realized_profit': 0.0,  # 누적 실현 손익
            'order_no': order_no,  # 주문번호 저장

            # ✅ Phase 3: 시장 정보 (갭업 재진입용)
            'market': self.validated_stocks.get(stock_code, {}).get('market', 'KOSDAQ'),

            # ✅ EOD Manager Phase 1: 익일 보유 관련 필드
            'strategy_tag': self.validated_stocks.get(stock_code, {}).get('strategy', self.default_strategy_tag),  # ✅ 동적 전략 태그
            'allow_overnight': allow_overnight,  # 익일 보유 허용 여부 (진입 시점 판단)
            'allow_overnight_final_confirm': False,  # EOD 시점 최종 확인
            'overnight_score': overnight_score,  # 진입 시점 overnight 점수 (0.0-1.0)
            'eod_score': 0.0,  # EOD 시점 재계산 점수 (0.0-1.0)
            'eod_forced_exit': False,  # EOD 강제 청산 여부 (분석/우선감시 리스트용)
            'gap_reentered_today': False,  # 🔥 ChatGPT Fix: 갭업 재진입 중복 방지
            'structure_stop_price': None,  # 🔧 2026-02-06: 구조 기반 손절가 (SMC)
            'atr_at_entry': None,           # 🔧 2026-02-07: 진입 시 ATR (Early Failure Structure 필터용)
            'mkt_context': mc_tag,          # 🔧 2026-03-24: 진입 시 시장 컨텍스트 (BAD_MARKET / GOOD_MARKET)

            # 🔧 2026-03-31: DEFENSIVE 모드 전용 필드
            'defensive_mode': _is_defensive,
            'defensive_stop_price': price * (1 - stop_loss_pct / 100) if _is_defensive else None,
            'defensive_tp_price': price * (1 + (take_profit_pct or 1.0) / 100) if _is_defensive else None,
            'defensive_max_hold_minutes': max_hold_minutes if _is_defensive else None,

            # 🔧 2026-04-03: MFE/MAE 추적 (DEFENSIVE 성능 분석용)
            'mfe_pct': 0.0,   # 진입 후 최대 유리 움직임 (%)
            'mae_pct': 0.0,   # 진입 후 최대 불리 움직임 (%)
            'peak_price': price,   # 최고가 (MFE 계산용)
            'trough_price': price, # 최저가 (MAE 계산용)

            # 🔧 수급 필터 + 피라미딩 (overnight_v2, pyramiding)
            'score_supply_demand': float(scores.get('supply_demand', 50)),
            'pyramid_added': False,    # 피라미딩 추가 여부 (1회 한도)
            'pyramid_add_count': 0,    # 추가 진입 횟수
        }

        # 포지션 상태 저장 (재시작 복원용)
        self._save_positions_state()

        # 🔧 2026-04-01: A+ 실제 진입 — 카운터·쿨다운·TP 저장
        if _is_a_plus:
            self._daily_a_plus_count += 1
            self._last_a_plus_time = datetime.now()
            _ap_max = self.config.get('smc.choch_grade.grade_a_plus.max_per_day', 2)
            # A+ TP 가격 저장 (2.0% 목표)
            _ap_tp_pct  = self.config.get('smc.choch_grade.grade_a_plus.take_profit_pct', 2.0)
            self.positions[stock_code]['a_plus_mode'] = True
            self.positions[stock_code]['a_plus_tp_price'] = price * (1 + _ap_tp_pct / 100)
            logger.info(
                f"[A_PLUS_COUNT] {stock_code}: {self._daily_a_plus_count}/{_ap_max} "
                f"TP={self.positions[stock_code]['a_plus_tp_price']:,.0f}원 (+{_ap_tp_pct:.1f}%)"
            )

        # 진입 시 overnight 판단 결과 로깅
        if allow_overnight:
            console.print(f"[cyan]✅ 익일 보유 허용 (점수: {overnight_score:.2f})[/cyan]")

        # DB에 매수 거래 저장
        stock_info = self.validated_stocks.get(stock_code, {})
        stats = stock_info.get('stats', {})
        analysis = stock_info.get('analysis', {})  # 종합 분석 결과
        scores = analysis.get('scores', {})

        trade_data = {
            'stock_code': stock_code,
            'stock_name': stock_name,
            'trade_type': 'BUY',
            'trade_time': entry_time.isoformat(),
            'price': float(price),  # 🔧 numpy → Python
            'quantity': int(quantity),  # 🔧 numpy → Python
            'amount': float(amount),  # 🔧 numpy → Python
            'process_id': os.getpid(),  # 🔧 프로세스 ID 추가
            'order_no': order_no,  # 🔧 주문번호 추가
            'condition_name': 'VWAP+AI',
            'strategy_config': 'hybrid',
            'entry_reason': entry_reason or f"{entry_time.strftime('%H:%M')} 진입 (신뢰도: {entry_confidence:.0%})",

            # VWAP 백테스트 결과 (🔧 numpy → Python 변환)
            'vwap_validation_score': float(stats.get('avg_profit_pct', 0)) if stats.get('avg_profit_pct') is not None else 0,
            'sim_win_rate': float(stats.get('win_rate')) if stats.get('win_rate') is not None else None,
            'sim_avg_profit': float(stats.get('avg_profit_pct')) if stats.get('avg_profit_pct') is not None else None,
            'sim_trade_count': int(stats.get('total_trades')) if stats.get('total_trades') is not None else None,
            'sim_profit_factor': float(stats.get('profit_factor')) if stats.get('profit_factor') is not None else None,

            # 종합 분석 결과 (AI) (🔧 numpy → Python 변환)
            'total_score': float(analysis.get('total_score', 0)),
            'score_news': float(scores.get('news', 50)),
            'score_technical': float(scores.get('technical', 50)),
            'score_supply_demand': float(scores.get('supply_demand', 50)),
            'score_fundamental': float(scores.get('fundamental', 50)),
            'recommendation': analysis.get('recommendation', '관망'),

            # 뉴스 분석
            'news_sentiment': analysis.get('news_sentiment', 'neutral'),
            'news_impact': analysis.get('news_impact', 0),
            'news_keywords': [],
            'news_titles': []
        }

        try:
            trade_id = self.db.insert_trade(trade_data)
            self.positions[stock_code]['trade_id'] = trade_id
        except Exception as _db_e:
            logger.error(f"[BUY_DB_ERROR] {stock_code} PostgreSQL 저장 실패 (거래는 정상): {_db_e}")
            trade_id = None

        # 의사결정 추적 — 진입 신호 기록
        try:
            from database.decision_trace import record_entry_signal
            _mc_tag = "BAD_MARKET" if mc_tag == "BAD_MARKET" else "GOOD_MARKET"
            _signal_id = record_entry_signal(
                stock_code=stock_code,
                stock_name=stock_name,
                entry_reason=entry_reason or '',
                price=price,
                df=df,
                market_regime=getattr(self, '_last_regime', None),
                market_context=_mc_tag,
                choch_grade=self.positions[stock_code].get('choch_grade'),
            )
            if _signal_id:
                self.positions[stock_code]['entry_signal_id'] = _signal_id
                # trades 테이블에 signal_id / strategy_name 반영
                try:
                    from database.decision_trace import _extract_strategy
                    import psycopg2
                    _conn = psycopg2.connect(
                        host=os.getenv("POSTGRES_HOST", "localhost"),
                        dbname=os.getenv("POSTGRES_DB", "trading_system"),
                        user=os.getenv("POSTGRES_USER", "postgres"),
                        password=os.getenv("POSTGRES_PASSWORD", ""),
                    )
                    _cur = _conn.cursor()
                    _cur.execute(
                        "UPDATE trades SET entry_signal_id=%s, strategy_name=%s WHERE trade_id=%s",
                        (_signal_id, _extract_strategy(entry_reason or ''), trade_id)
                    )
                    _conn.commit(); _conn.close()
                except Exception:
                    pass
        except Exception as _dte:
            pass

        # ML 진입 피처 기록 (SELL 시 _update_ml_exit로 라벨 채움)
        try:
            if trade_id:
                from database.decision_trace import insert_ml_entry
                _entry_type_ml = (
                    'EXPLORATION' if 'EXPLORATION' in (entry_reason or '')
                    else 'TREND'  if 'TREND'       in (entry_reason or '')
                    else 'SMC'    if 'SMC'          in (entry_reason or '')
                    else 'DEFENSIVE' if _is_defensive
                    else 'OTHER'
                )
                _pend_dur = None
                _smeta = getattr(self, '_pending_signal_meta', None)
                if _smeta and isinstance(_smeta, dict):
                    _pend_dur = _smeta.get('pending_duration') or _smeta.get('elapsed_sec')
                insert_ml_entry(
                    trade_id=trade_id,
                    stock_code=stock_code,
                    entry_time=entry_time,
                    df=df,
                    entry_type=_entry_type_ml,
                    pending_duration=int(_pend_dur) if _pend_dur else None,
                    entry_reason=entry_reason or '',
                )
        except Exception as _mle:
            logger.debug(f"[DTRACE] insert_ml_entry 실패 (무시): {_mle}")

        # ml_decisions에 trade_id 연결 (게이트에서 decision_id 받은 경우)
        if _ml_decision_id and trade_id:
            try:
                from database.decision_trace import _get_conn as _dt_conn
                _mc = _dt_conn(); _mcu = _mc.cursor()
                _mcu.execute(
                    "UPDATE ml_decisions SET trade_id=%s WHERE id=%s",
                    (trade_id, _ml_decision_id),
                )
                _mc.commit(); _mc.close()
            except Exception:
                pass

        # 리스크 관리자에 거래 기록
        self.risk_manager.record_trade(
            stock_code=stock_code,
            stock_name=stock_name,
            trade_type='BUY',
            quantity=quantity,
            price=price,
            realized_pnl=0,
            reason=entry_reason  # 매수 이유 전달
        )

        console.print(f"✅ 매수 완료 (DB ID: {trade_id})")
        logger.info(
            f"[BUY_COMPLETE] {stock_code} {stock_name} | "
            f"price={price:,} qty={quantity} amount={amount:,.0f} | "
            f"reason={entry_reason or ''} | trade_id={trade_id}"
        )
        self._refresh_dashboard_cache()

        # ── EQ Feature Logger 진입 기록 ──────────────────────────────────
        try:
            if getattr(self, 'eq_feature_logger', None):
                _eq_feat = self._build_eq_features(stock_code, price, df, entry_confidence, entry_reason)
                # SMC 메타는 포지션에 아직 없을 수 있음 — _pending_signal_meta 사용
                _sm = getattr(self, '_pending_signal_meta', {}) or {}
                _eq_feat.setdefault('choch_grade', _sm.get('choch_grade'))
                _eq_feat.setdefault('htf_trend', _sm.get('htf_bias'))
                _eq_feat.setdefault('sweep', _sm.get('sweep'))
                self.eq_feature_logger.log_entry(stock_code, stock_name, price, _eq_feat)
        except Exception as _eqle:
            logger.debug(f"[EQ_LOG] 진입 기록 실패: {_eqle}")
        # ─────────────────────────────────────────────────────────────────

        # ── TradeLogger 진입 기록 ─────────────────────────────────────────
        try:
            if getattr(self, '_trade_logger', None):
                _engine = getattr(self, '_score_engine', None)
                _sinfo  = _engine.score(stock_code) if _engine else {}
                self._trade_logger.log_entry(
                    symbol       = stock_code,
                    price        = float(price),
                    score        = _sinfo.get('total', 0),
                    score_detail = _sinfo,
                    reason       = entry_reason or 'smc',
                )
        except Exception as _le:
            logger.debug(f'[TRADE_LOG] 진입 기록 실패 (무시): {_le}')
        # ─────────────────────────────────────────────────────────────────

        # ✅ 진입 지표 캡처 (백테스트/AI 학습용)
        _regime_tag   = self._infer_signal_regime(entry_reason or '')
        _signal_meta  = getattr(self, '_pending_signal_meta', None)
        self._pending_signal_meta = {}
        capture_entry(stock_code, stock_name, price, df, entry_reason or '', _regime_tag, signal_meta=_signal_meta)

        # 🔴 GPT 개선: 종목별 일일 거래 카운트 증가
        self.daily_trade_count[stock_code] = self.daily_trade_count.get(stock_code, 0) + 1
        console.print(f"[dim]📊 {stock_name}: 오늘 {self.daily_trade_count[stock_code]}회 거래 (최대 {self.max_trades_per_stock_per_day}회)[/dim]")
        # 🔧 2026-03-07: 일일 총 매수 카운트 증가
        self._daily_buy_count += 1
        console.print(f"[dim]📊 오늘 총 매수 {self._daily_buy_count}회 (한도 {self.config.get('risk_control.daily_risk.max_trades_per_day', 3)}회)[/dim]")

        # ✅ TradeStateManager에 매수 기록
        strategy_tag = self.validated_stocks.get(stock_code, {}).get('strategy', self.default_strategy_tag)  # ✅ 동적 기본값
        self.state_manager.mark_traded(
            stock_code=stock_code,
            stock_name=stock_name,
            action=TradeAction.BUY,
            price=price,
            quantity=quantity,
            strategy_tag=strategy_tag,
            reason=f"VWAP 진입 (신뢰도: {entry_confidence*100:.0f}%)"
        )

        console.print("=" * 80, style="green")
        console.print()

        # 잔고 업데이트 (비동기 실행)
        asyncio.create_task(self.update_account_balance())

    def _maybe_upgrade_grade(self, stock_code: str, position: dict, current_price: float, df=None) -> bool:
        """
        포지션 등급 승격 시스템.
        진입 시 결정된 등급이 운용 중 업그레이드 될 수 있음.

        조건: pnl >= 3% AND supply_demand >= 60
        과열 필터: EMA20 이격률 >= max_ema20_extension(5%) → 승격 차단 (갭하락 방어)
        효과:
          - choch_grade → 'A' (overnight 허용 범위 확대)
          - allow_overnight = True (재평가 없이 즉시 승인)
        YAML: grade_upgrade.enabled, profit_threshold_pct, min_supply_demand, max_ema20_extension
        """
        upg_cfg = self.config.get('grade_upgrade', {})
        if not upg_cfg.get('enabled', True):
            return False

        current_grade = position.get('choch_grade', '')
        if current_grade in ('A', 'A+'):
            return False  # 이미 최고 등급

        entry_price = position.get('avg_price') or position.get('entry_price', 0)
        if entry_price <= 0:
            return False

        profit_pct = (current_price - entry_price) / entry_price * 100
        upg_profit  = float(upg_cfg.get('profit_threshold_pct', 3.0))
        upg_sd      = float(upg_cfg.get('min_supply_demand', 60))
        sd_score    = position.get('score_supply_demand', 50)

        if profit_pct >= upg_profit and sd_score >= upg_sd:
            # ── 과열 필터 (3단계) ─────────────────────────────────────────────────
            # 규칙 ①: EMA5 단독 ≥ ema5_solo_block(5%) → "단기 급등" 단독 차단
            # 규칙 ②: EMA20 ≥5% AND EMA5 ≥3% 동시 → "추세 과열" 차단
            # (추세 초입 = EMA20 낮음 + EMA5 3~5% = 살아있는 구간 → 규칙①로만 차단)
            max_ext          = float(upg_cfg.get('max_ema20_extension',   0.05))
            max_short_ext    = float(upg_cfg.get('max_ema5_extension',    0.03))
            ema5_solo_block  = float(upg_cfg.get('ema5_solo_block_pct',  0.05))
            _ema20_ext_val   = 0.0
            _ema5_ext_val    = 0.0
            try:
                if df is not None and 'ema_20' in df.columns and len(df) > 0:
                    _ema20 = float(df['ema_20'].iloc[-1])
                    if _ema20 > 0:
                        _ema20_ext_val = (current_price - _ema20) / _ema20
            except Exception:
                pass
            try:
                if df is not None and len(df) > 0:
                    if 'ema_5' in df.columns:
                        _ema5 = float(df['ema_5'].iloc[-1])
                    else:
                        _ema5 = float(df['close'].ewm(span=5, adjust=False).mean().iloc[-1])
                    if _ema5 > 0:
                        _ema5_ext_val = (current_price - _ema5) / _ema5
            except Exception:
                pass

            if _ema5_ext_val >= ema5_solo_block:
                logger.info(
                    f"[UPGRADE_BLOCK] {stock_code}: EMA5 단독 과열 {_ema5_ext_val:.1%} >= {ema5_solo_block:.0%} — 승격 차단"
                )
                return False
            if _ema20_ext_val >= max_ext and _ema5_ext_val >= max_short_ext:
                logger.info(
                    f"[UPGRADE_BLOCK] {stock_code}: EMA20({_ema20_ext_val:.1%})+EMA5({_ema5_ext_val:.1%}) 동시 과열 — 승격 차단"
                )
                return False

            old_grade = current_grade
            position['choch_grade']    = 'A'
            position['allow_overnight'] = True
            position['grade_upgraded']  = True

            logger.info(
                f"[GRADE_UPGRADE] {stock_code}: {old_grade} → A | "
                f"+{profit_pct:.1f}%, 수급{sd_score:.0f} — overnight 허용"
            )
            console.print(
                f"[bold cyan]⬆ [UPGRADE] {stock_code}: {old_grade}→A "
                f"(+{profit_pct:.1f}%, 수급{sd_score:.0f})[/bold cyan]"
            )
            return True
        return False

    def _maybe_pyramid_add(self, stock_code: str, position: dict, current_price: float, df) -> bool:
        """
        2단계 피라미딩: 수익 중인 포지션에 단계적 추가 진입.

        STEP 1 — 조기 추가 (더 자주 발동):
          - 수익 >= step1_profit_pct (기본 1.5%)
          - 현재가 > EMA20 (추세 방향 확인)
          - 리스크 캡 이하

        STEP 2 — 강한 확장 (확신 후 추가):
          - 수익 >= step2_profit_pct (기본 3.0%)
          - 현재가 > EMA20 + ATR 확장
          - 수급 >= min_supply_demand_score
          - 리스크 캡 이하

        리스크 캡: total_qty > initial_qty × risk_cap_mult (기본 1.5x) → 차단
        """
        pyr_cfg = self.config.get('pyramiding', {})
        if not pyr_cfg.get('enabled', False):
            return False

        # ── 연속 손실 시 피라미딩 비활성화 (시장이 틀렸다고 말할 때 공격 중단) ──
        if pyr_cfg.get('disable_on_loss_streak', True):
            try:
                _lsg_config = self.config.get('risk_control.loss_streak_guard', {})
                _lsg_adj    = self.reentry_metrics.get_loss_streak_adjustments(_lsg_config)
                if _lsg_adj.get('active', False):
                    logger.info(
                        f"[PYRAMID_BLOCK] {stock_code}: Loss Streak Guard 활성 → 피라미딩 비활성화 "
                        f"(streak={_lsg_adj.get('consecutive', 0)})"
                    )
                    return False
            except Exception:
                pass

        # ── 레짐 기반 피라미딩 스위칭 ───────────────────────────────────────────
        # TREND       → STEP1 + STEP2 전체 허용
        # CHOP+돌파   → STEP1만 허용 (추세 시작점 포착 기회 살림)
        # REVERSAL    → 전면 차단
        if pyr_cfg.get('regime_filter', True):
            try:
                _regime, _    = self.market_context.get_regime()
                _add_count_now = position.get('pyramid_add_count', 0)

                if _regime == 'REVERSAL':
                    logger.debug(f"[PYRAMID_BLOCK] {stock_code}: REVERSAL 레짐 → 피라미딩 전면 차단")
                    return False
                elif _regime == 'CHOP':
                    # CHOP에서 STEP2(add_count>=1)는 항상 차단
                    if _add_count_now >= 1:
                        logger.debug(f"[PYRAMID_BLOCK] {stock_code}: CHOP 레짐 → STEP2 차단")
                        return False
                    # STEP1: RVOL >= 1.5 AND price > VWAP (거래량 + 가격위치 둘 다)
                    # 거래량만 보면 뉴스/개미 몰림 가짜 돌파에 속음 → VWAP 위 = 수급 실체 확인
                    _chop_step1_ok = False
                    _chop_rvol_thr = float(pyr_cfg.get('chop_breakout_rvol', 1.5))
                    try:
                        if df is not None and 'volume' in df.columns and len(df) >= 21:
                            _vol_avg = float(df['volume'].iloc[-21:-1].mean())
                            _rvol    = float(df['volume'].iloc[-1]) / _vol_avg if _vol_avg > 0 else 0
                            _rvol_ok = _rvol >= _chop_rvol_thr

                            # VWAP 위치 확인
                            _above_vwap = False
                            if 'vwap' in df.columns:
                                _vwap_val   = float(df['vwap'].iloc[-1])
                                _above_vwap = current_price > _vwap_val if _vwap_val > 0 else True
                            else:
                                _above_vwap = True  # VWAP 없으면 관대하게 허용

                            if _rvol_ok and _above_vwap:
                                _chop_step1_ok = True
                    except Exception:
                        pass
                    if not _chop_step1_ok:
                        logger.debug(
                            f"[PYRAMID_BLOCK] {stock_code}: CHOP 레짐 + 돌파 미감지 (RVOL 또는 VWAP 미달) → STEP1 차단"
                        )
                        return False
                    logger.info(
                        f"[PYRAMID_CHOP_STEP1] {stock_code}: CHOP 레짐 돌파 감지 (RVOL≥{_chop_rvol_thr}x + VWAP 위) → STEP1 허용"
                    )
                # TREND 또는 기타 레짐: 전체 허용
            except Exception:
                pass

        entry_price = position.get('avg_price') or position.get('entry_price', 0)
        if entry_price <= 0:
            return False

        profit_pct   = (current_price - entry_price) / entry_price * 100
        initial_qty  = position.get('initial_quantity', position.get('quantity', 0))
        current_qty  = position.get('quantity', 0)
        add_count    = position.get('pyramid_add_count', 0)
        risk_cap     = float(pyr_cfg.get('risk_cap_mult', 1.5))
        max_adds     = int(pyr_cfg.get('max_per_position', 2))  # 2단계

        # 리스크 캡: 총 보유량이 초기 수량 × risk_cap_mult 초과 시 전면 차단
        if current_qty >= initial_qty * risk_cap:
            return False

        # 최대 추가 횟수 초과
        if add_count >= max_adds:
            return False

        # ── 공통 체크: 현재가 > EMA20 ─────────────────────────────────────────
        above_ema20 = False
        try:
            if df is not None and 'ema_20' in df.columns and len(df) > 0:
                above_ema20 = float(df['close'].iloc[-1]) > float(df['ema_20'].iloc[-1])
        except Exception:
            pass

        # ── STEP 1: 조기 추가 (add_count == 0) ───────────────────────────────
        if add_count == 0:
            # A급 이상: 고확신 진입 → 더 빠른 피라미딩 (0.5%)
            # 단, "가짜 A급" 방지: 수급 >= a_grade_min_sd(60) 조건 추가
            _grade  = position.get('choch_grade', 'B')
            _sd_now = position.get('score_supply_demand', 50)
            _a_grade_min_sd = float(pyr_cfg.get('a_grade_min_supply_demand', 60))
            if _grade in ('A', 'A+', 'A-') and _sd_now >= _a_grade_min_sd:
                step1_thr = float(pyr_cfg.get('step1_profit_pct_a_grade', 0.5))
            else:
                step1_thr = float(pyr_cfg.get('step1_profit_pct', 1.0))
            step1_mult = float(pyr_cfg.get('step1_size_mult', 0.15))

            # strong_candle: 현재 캔들 바디 > 최근 봉 평균 바디
            strong_candle = False
            try:
                if df is not None and 'open' in df.columns and 'close' in df.columns and len(df) >= 6:
                    _body = abs(float(df['close'].iloc[-1]) - float(df['open'].iloc[-1]))
                    _avg_body = (df['close'] - df['open']).abs().iloc[-6:-1].mean()
                    strong_candle = _body > float(_avg_body) if _avg_body > 0 else False
            except Exception:
                pass

            if profit_pct >= step1_thr and above_ema20 and strong_candle:
                add_qty = max(1, int(initial_qty * step1_mult))
                return self._do_pyramid_order(
                    stock_code, position, current_price, entry_price,
                    add_qty, 1, profit_pct, df=df
                )

        # ── STEP 2: 강한 확장 (add_count == 1) ──────────────────────────────
        elif add_count == 1:
            step2_thr  = float(pyr_cfg.get('step2_profit_pct', 3.0))
            step2_mult = float(pyr_cfg.get('step2_size_mult', 0.20))

            # ATR 확장 체크
            atr_expanding = False
            try:
                if df is not None and 'atr' in df.columns and len(df) >= 2:
                    atr_expanding = float(df['atr'].iloc[-1]) > float(df['atr'].iloc[-2])
            except Exception:
                pass

            # 수급 체크
            sd_ok = True
            if pyr_cfg.get('require_supply_demand', True):
                sd_score = position.get('score_supply_demand', 50)
                min_sd   = float(pyr_cfg.get('min_supply_demand_score', 40))
                if sd_score < min_sd:
                    logger.info(f"[PYRAMID_BLOCK] {stock_code}: STEP2 수급 {sd_score:.0f} < {min_sd}")
                    sd_ok = False

            if profit_pct >= step2_thr and above_ema20 and atr_expanding and sd_ok:
                add_qty = max(1, int(initial_qty * step2_mult))
                return self._do_pyramid_order(
                    stock_code, position, current_price, entry_price,
                    add_qty, 2, profit_pct, df=df
                )

        return False

    def _do_pyramid_order(
        self, stock_code: str, position: dict, current_price: float,
        entry_price: float, add_qty: int, step: int, profit_pct: float,
        df=None,
    ) -> bool:
        """피라미딩 주문 실행 + 포지션 업데이트 + 손절 BE 이동"""
        try:
            order_result = self.api.send_order(
                stock_code=stock_code,
                order_type='1',   # 매수
                quantity=add_qty,
                price=0,          # 시장가
                trade_type='01',
            )
            if order_result.get('return_code') != 0:
                logger.warning(f"[PYRAMID_FAIL] {stock_code} STEP{step}: {order_result.get('return_msg')}")
                return False

            old_qty = position.get('quantity', 0)
            new_qty = old_qty + add_qty
            new_avg = (entry_price * old_qty + current_price * add_qty) / max(new_qty, 1)
            position['quantity']          = new_qty
            position['avg_price']         = new_avg
            position['pyramid_added']     = True
            position['pyramid_add_count'] = position.get('pyramid_add_count', 0) + 1

            # ── 손절 상향: BE 이동 (추가 진입 = 공격, 손절 이동 = 방어) ─────────────
            pyr_cfg       = self.config.get('pyramiding', {})
            stop_mode     = pyr_cfg.get('stop_after_pyramid', 'be')   # 'be' | 'atr' | 'none'
            old_stop      = position.get('structure_stop_price')

            if stop_mode == 'be':
                # 완화된 BE: 진입가 - ATR*0.3 (즉시 BE보다 여유 있어 정상 눌림에 안 흔들림)
                try:
                    _atr_val = float(df['atr'].iloc[-1]) if df is not None and 'atr' in df.columns else 0
                    _be_atr_mult = float(pyr_cfg.get('be_atr_buffer', 0.3))
                    new_stop = entry_price - _atr_val * _be_atr_mult if _atr_val > 0 else entry_price
                except Exception:
                    new_stop = entry_price
            elif stop_mode == 'atr':
                # ATR: 추가 가격 - ATR × 0.5
                try:
                    _atr = float(df['atr'].iloc[-1]) if df is not None and 'atr' in df.columns else 0
                    new_stop = current_price - _atr * 0.5
                except Exception:
                    new_stop = entry_price
            else:
                new_stop = None

            # ── 절대 손실 제한: ATR이 커도 최대 2% 손실까지만 허용 ───────────────
            if new_stop is not None:
                _max_stop_pct = float(pyr_cfg.get('max_stop_loss_pct', 0.02))
                _floor_stop   = entry_price * (1.0 - _max_stop_pct)
                new_stop      = max(new_stop, _floor_stop)

            if new_stop is not None and (old_stop is None or new_stop > old_stop):
                position['structure_stop_price'] = new_stop
                logger.info(
                    f"[PYR_STOP_RAISE] {stock_code} STEP{step}: "
                    f"손절 {old_stop or 'N/A'} → {new_stop:,.0f}원 ({stop_mode.upper()})"
                )

            logger.info(
                f"[PYRAMID_ADD] {stock_code} STEP{step}: +{profit_pct:.1f}% "
                f"→ {add_qty}주 추가 (총 {new_qty}주, 평단 {new_avg:,.0f}원)"
            )
            console.print(
                f"[bold green]🔺 [PYR STEP{step}] {stock_code}: +{profit_pct:.1f}% "
                f"→ {add_qty}주 추가 | 손절 BE 이동[/bold green]"
            )
            return True
        except Exception as e:
            logger.warning(f"[PYRAMID_ERROR] {stock_code}: {e}")
            return False

    def should_allow_overnight(self, stock_code: str, df: pd.DataFrame, signal_result: Dict, entry_confidence: float, choch_grade: str = None) -> Tuple[bool, float]:
        """
        진입 시점에 익일 보유 허용 여부 판단

        Args:
            stock_code: 종목 코드
            df: OHLCV + 지표 데이터프레임
            signal_result: SignalOrchestrator 평가 결과
            entry_confidence: 진입 신뢰도 (0.0-1.0)

        Returns:
            (allow_overnight, overnight_score): (보유 허용 여부, 0.0-1.0 점수)

        판단 기준 (EOD 개선 계획 Phase 1):
            1. 신뢰도: entry_confidence >= 0.6
            2. 추세: 현재가 > EMA5
            3. 거래량: vol_z20 >= 1.0
            4. 뉴스: news_score >= 50
        overnight_v2 추가 게이트:
            5. CHoCH A급 필수 (overnight_v2.require_a_grade)
            6. REVERSAL 레짐 차단 (overnight_v2.block_reversal_regime)
        """
        try:
            # EOD 정책 설정 확인
            eod_config = self.config.get_section('eod_policy')
            if not eod_config or not eod_config.get('enabled', False):
                return False, 0.0

            # ── overnight_v2 게이트 ─────────────────────────────────────────────
            ov2_cfg = self.config.get('overnight_v2', {})

            # Gate 1: CHoCH 등급 기반 overnight 허용 판단
            if ov2_cfg.get('require_a_grade', True):
                grade = choch_grade or self.positions.get(stock_code, {}).get('choch_grade')
                if grade:
                    if grade in ('A', 'A+', 'A-'):
                        pass  # A급: 무조건 허용
                    elif grade == 'B' and ov2_cfg.get('allow_b_with_conditions', True):
                        # B급 조건부 허용: supply_demand ≥ 50 + EMA20 위 + 모멘텀(가고 있음)
                        _pos_sd   = self.positions.get(stock_code, {}).get('score_supply_demand', 50)
                        _min_b_sd = float(ov2_cfg.get('b_grade_min_supply_demand', 50))
                        _trend_strong  = False
                        _momentum_ok   = False
                        try:
                            if df is not None and len(df) >= 2:
                                _cls   = df['close']
                                _vol   = df['volume'] if 'volume' in df.columns else None
                                # 추세: 현재가 > EMA20
                                if 'ema_20' in df.columns:
                                    _trend_strong = float(_cls.iloc[-1]) > float(df['ema_20'].iloc[-1])
                                # 모멘텀: close > prev_close AND volume > avg_volume(20)
                                _price_up = float(_cls.iloc[-1]) > float(_cls.iloc[-2])
                                _vol_ok   = True
                                if _vol is not None and len(_vol) >= 21:
                                    _vol_ok = float(_vol.iloc[-1]) > float(_vol.iloc[-21:-1].mean())
                                _momentum_ok = _price_up and _vol_ok
                        except Exception:
                            pass
                        if _pos_sd >= _min_b_sd and _trend_strong and _momentum_ok:
                            logger.info(
                                f"[OV2_B_ALLOW] {stock_code}: B급 + 수급{_pos_sd:.0f} + 추세↑ + 모멘텀↑ → overnight 허용"
                            )
                        else:
                            logger.info(
                                f"[OV2_GRADE_BLOCK] {stock_code}: B급 조건 미충족 "
                                f"(수급{_pos_sd:.0f}/{_min_b_sd}, 추세={_trend_strong}, 모멘텀={_momentum_ok}) → 차단"
                            )
                            return False, 0.0
                    else:
                        # C급 이하, 또는 B급 조건부 허용 비활성화
                        logger.info(f"[OV2_GRADE_BLOCK] {stock_code}: choch_grade={grade} → overnight 차단")
                        return False, 0.0

            # Gate 2: REVERSAL 레짐 차단
            if ov2_cfg.get('block_reversal_regime', True):
                try:
                    regime, regime_reason = self.market_context.get_regime()
                    if regime == 'REVERSAL':
                        logger.info(f"[OV2_REGIME_BLOCK] {stock_code}: regime={regime} ({regime_reason}) — REVERSAL overnight 차단")
                        return False, 0.0
                except Exception:
                    pass  # regime 판단 실패 시 패스 (보수적 허용)

            overnight_criteria = eod_config.get('overnight_criteria', {})

            # 기본 점수 초기화
            score = 0.0
            weights = {
                'trend': overnight_criteria.get('trend_weight', 0.4),
                'volume': overnight_criteria.get('volume_weight', 0.3),
                'news': overnight_criteria.get('news_weight', 0.3),
            }

            # 1. 신뢰도 체크 (최소 0.6 이상)
            if entry_confidence < 0.6:
                return False, 0.0

            # 2. 추세 점수 (price > EMA5)
            trend_score = 0.0
            if len(df) >= 5:
                current_price = df['close'].iloc[-1]

                # EMA5 체크
                if 'ema_5' in df.columns:
                    ema5 = df['ema_5'].iloc[-1]
                    if current_price > ema5:
                        trend_score = 1.0
                    else:
                        # 필수 조건 미달
                        min_ema_state = overnight_criteria.get('min_ema_state', True)
                        if min_ema_state:
                            return False, 0.0
                        trend_score = 0.5

                # EMA20 추가 체크 (보너스)
                if 'ema_20' in df.columns:
                    ema20 = df['ema_20'].iloc[-1]
                    if current_price > ema20:
                        trend_score = min(1.0, trend_score + 0.2)

            score += trend_score * weights['trend']

            # 3. 거래량 점수 (vol_z20 >= 1.0)
            volume_score = 0.0
            min_vol_z20 = overnight_criteria.get('min_vol_z20', 1.0)

            if 'volume_zscore' in df.columns:
                vol_z20 = df['volume_zscore'].iloc[-1]
                if vol_z20 >= min_vol_z20:
                    # Z-score에 따라 점수 차등 부여
                    if vol_z20 >= 2.5:
                        volume_score = 1.0  # 매우 강한 거래량
                    elif vol_z20 >= 2.0:
                        volume_score = 0.9
                    elif vol_z20 >= 1.5:
                        volume_score = 0.7
                    else:
                        volume_score = 0.5
                else:
                    # 필수 조건 미달
                    return False, 0.0
            else:
                # volume_zscore 없으면 기본 점수
                volume_score = 0.5

            score += volume_score * weights['volume']

            # 4. 뉴스 점수 (>= 50)
            news_score_value = 50  # 기본값
            min_news_score = overnight_criteria.get('min_news_score', 50)

            # validated_stocks에서 뉴스 점수 조회
            stock_info = self.validated_stocks.get(stock_code, {})
            analysis = stock_info.get('analysis', {})
            scores = analysis.get('scores', {})

            if 'news' in scores:
                news_score_value = scores['news']

            news_score = 0.0
            if news_score_value >= min_news_score:
                # 뉴스 점수에 따라 차등 부여
                if news_score_value >= 80:
                    news_score = 1.0  # 매우 긍정적
                elif news_score_value >= 70:
                    news_score = 0.9
                elif news_score_value >= 60:
                    news_score = 0.7
                else:
                    news_score = 0.5
            else:
                # 필수 조건 미달
                return False, 0.0

            score += news_score * weights['news']

            # 5. 최종 점수 판단
            min_overnight_score = eod_config.get('min_overnight_score', 0.6)
            allow_overnight = score >= min_overnight_score

            return allow_overnight, score

        except Exception as e:
            console.print(f"[yellow]⚠️  should_allow_overnight 오류: {e}[/yellow]")
            import traceback
            traceback.print_exc()
            return False, 0.0

    async def force_close_overnight(self):
        """🔧 2026-02-15: 오버나이트 강제 청산 (CHoCH A급 제외)"""
        config = self.config.get_section('overnight_close')
        if not config or not config.get('enabled', True):
            console.print("[dim]overnight_close 비활성화[/dim]")
            return

        if not self.positions:
            console.print("[dim]보유 포지션 없음 - 오버나이트 체크 스킵[/dim]")
            return

        # 🔧 2026-04-15: 토큰 선제 검증 (강제청산 실패 방지 — 비츠로셀 8005 재발 방지)
        if not self.validate_token():
            logger.warning("[OVERNIGHT_CLOSE] 토큰 유효하지 않음 — 선제 재발급 시도")
            console.print("[yellow]🔄 [OVERNIGHT_CLOSE] 토큰 만료 감지 — 선제 재발급[/yellow]")
            if self.refresh_access_token():
                logger.info("[OVERNIGHT_CLOSE] 토큰 재발급 성공 — 청산 진행")
                console.print("[green]✅ 토큰 재발급 성공 — 강제청산 진행[/green]")
            else:
                logger.critical("[OVERNIGHT_CLOSE] 토큰 재발급 실패 — execute_sell 내부 재시도로 fallback")
                console.print("[red]⚠️  토큰 재발급 실패 — 개별 주문에서 재시도 예정[/red]")

        exempt_grades        = config.get('exempt_grades', ['A', 'A+'])
        min_profit_overnight = config.get('min_profit_for_overnight', 1.5)
        require_htf          = config.get('require_htf_trend', True)
        use_market           = config.get('use_market_order', True)

        console.print()
        console.print("=" * 60, style="bold yellow")
        console.print("오버나이트 강제 청산 체크", style="bold yellow")
        console.print("=" * 60, style="bold yellow")

        closed_count = 0
        allowed_count = 0
        for stock_code in list(self.positions.keys()):
            # 중복 청산 방지: 루프 내부에서 이미 제거됐을 수 있음
            if stock_code not in self.positions:
                continue
            pos = self.positions[stock_code]
            stock_name = pos.get('stock_name', stock_code)
            has_grade = 'choch_grade' in pos
            grade = pos.get('choch_grade', 'B')  # 미저장 시 B로 간주

            if not has_grade:
                # choch_grade 없음 = 재시작으로 메타데이터 소실 → 안전하게 스킵
                logger.info(f"[OVERNIGHT_CLOSE] SKIP NO_GRADE (재시작 후 메타소실): {stock_name} ({stock_code})")
                allowed_count += 1
                continue

            # 현재가 조회 (조건 판단에 필요)
            current_price = pos.get('current_price', pos['entry_price'])
            entry_price   = pos['entry_price']
            profit_pct    = (current_price - entry_price) / entry_price * 100

            # 🔧 2026-04-17: 조건부 오버나이트 허용 (3조건 모두 충족해야 ALLOW)
            #   ① 등급: A 또는 A+
            #   ② HTF 추세: htf_trend_alive == True (진입 시 저장)
            #   ③ 수익 쿠션: profit_pct >= min_profit_for_overnight (+1.5%)
            grade_ok  = grade in exempt_grades
            htf_ok    = (not require_htf) or pos.get('htf_trend_alive', False)
            profit_ok = profit_pct >= min_profit_overnight

            if grade_ok and htf_ok and profit_ok:
                pos['allow_overnight_final_confirm'] = True
                console.print(
                    f"[green]  ✓ {stock_name}: {grade}급 + HTF✓ + pnl={profit_pct:+.2f}% → 오버나이트 허용[/green]"
                )
                logger.info(
                    f"[OVERNIGHT_POLICY] symbol={stock_name} grade={grade} "
                    f"htf={htf_ok} pnl={profit_pct:+.2f}% action=ALLOW"
                )
                allowed_count += 1
                continue

            # 허용 조건 미달 → 강제 청산 (사유 로그)
            _deny_reason = []
            if not grade_ok:    _deny_reason.append(f"등급={grade}")
            if not htf_ok:      _deny_reason.append("HTF❌")
            if not profit_ok:   _deny_reason.append(f"수익={profit_pct:+.2f}%<{min_profit_overnight}%")
            reason = f"{datetime.now().strftime('%H:%M')} 오버나이트 차단 ({', '.join(_deny_reason)})"
            console.print(f"[red]  X {stock_name}: CHoCH {grade}급 - 강제 청산 ({profit_pct:+.2f}%)[/red]")
            logger.info(f"[OVERNIGHT_POLICY] symbol={stock_name} grade={grade} action=FORCE_CLOSE pnl={profit_pct:+.2f}% time={datetime.now().strftime('%H:%M')}")

            self.execute_sell(stock_code, current_price, profit_pct, reason, use_market_order=use_market)

            # 🔧 2026-04-15: Kill Switch — overnight 강제청산 실패 감지
            # soft_close_pct: 초기 수량의 N% 이상 청산됐으면 성공 (부분체결 허용)
            if stock_code in self.positions:
                _ks_cfg2 = self.config.get('risk_control.kill_switch', {})
                _soft_pct = _ks_cfg2.get('soft_close_pct', 0.8)
                _ks_thresh = _ks_cfg2.get('threshold', 3)
                _init_qty = pos.get('initial_quantity', pos.get('quantity', 1))
                _rem_qty = pos.get('quantity', 0)
                _closed_pct = (_init_qty - _rem_qty) / _init_qty if _init_qty > 0 else 0
                if _closed_pct >= _soft_pct:
                    # 80%+ 청산 → 성공 간주 (부분체결 허용)
                    closed_count += 1
                    logger.info(f"[CLOSE_PARTIAL_OK] {stock_name} {_closed_pct:.0%} 청산 → 성공 간주")
                else:
                    pos['_close_failures'] = pos.get('_close_failures', 0) + 1
                    logger.critical(
                        f"[CLOSE_FAIL] {stock_name}({stock_code}) overnight 청산 실패 "
                        f"#{pos['_close_failures']}회 ({_closed_pct:.0%} 청산)"
                    )
                    if pos['_close_failures'] >= _ks_thresh:
                        self._kill_switch_active = True
                        self._kill_switch_triggered_at = datetime.now()
                        self._kill_switch_reason = f"{stock_name}({stock_code}) 청산 {pos['_close_failures']}회 실패"
                        logger.critical(f"[KILL_SWITCH_ON] {self._kill_switch_reason} — 신규 진입 전면 차단")
                        console.print(f"[bold red]🛑 [KILL_SWITCH_ON] {self._kill_switch_reason}[/bold red]")
            else:
                closed_count += 1

        console.print(f"\n  결과: 강제 청산 {closed_count}건 / 오버나이트 허용 {allowed_count}건")
        console.print("=" * 60, style="bold yellow")

    async def handle_eod(self):
        """
        EOD (End of Day) 프로세스 실행 (14:55)

        1. allow_overnight=True 포지션 중 익일 보유 대상 선정
        2. 나머지 포지션 청산 (15:05)
        3. 우선 감시 리스트 생성 (다음날 갭업 재진입용)
        """
        try:
            # 🔧 2026-04-24: EOD 확정 peak 갱신 (장중 가짜 peak 방지 — 종가 기준)
            if hasattr(self, 'equity_ctrl') and self.total_assets > 0:
                self.equity_ctrl.update_peak_eod(self.total_assets)
                logger.info(f"[EC_EOD] EOD peak 확정: {self.equity_ctrl.peak:,.0f}원")

            console.print()
            console.print("=" * 80, style="bold yellow")
            console.print("🌅 EOD 프로세스 시작 (14:55)", style="bold yellow")
            console.print("=" * 80, style="bold yellow")

            # 1. EOD 정책 활성화 체크
            eod_config = self.config.get_section('eod_policy')
            if not eod_config or not eod_config.get('enabled', False):
                console.print("[dim]ℹ️  EOD 정책이 비활성화되어 있습니다.[/dim]")
                return

            # 2. 현재 포지션이 없으면 종료
            if not self.positions:
                console.print("[dim]ℹ️  보유 포지션이 없습니다.[/dim]")
                return

            # 3. 계좌 정보 조회
            account_value = self.total_assets if self.total_assets > 0 else self.current_cash
            console.print(f"[dim]📊 계좌 평가금액: {account_value:,.0f}원[/dim]")

            # 4. EOD Manager 실행
            to_hold, to_close, priority_watchlist = self.eod_manager.run_eod_check(
                positions=self.positions,
                api=self.api,
                news_fetcher=None,  # TODO: 뉴스 조회 기능 추가 시 연동
                account_value=account_value
            )

            # 5. 결과 출력
            console.print()
            console.print(f"[green]✅ 익일 보유 종목 ({len(to_hold)}개):[/green]")
            for code in to_hold:
                pos = self.positions[code]
                console.print(
                    f"  - {pos['stock_name']} ({code}): "
                    f"EOD점수 {pos.get('eod_score', 0):.2f}, "
                    f"진입점수 {pos.get('overnight_score', 0):.2f}"
                )

            console.print()
            console.print(f"[yellow]⚠️  15:05 청산 예정 ({len(to_close)}개):[/yellow]")
            for code in to_close:
                pos = self.positions[code]
                console.print(
                    f"  - {pos['stock_name']} ({code}): "
                    f"EOD점수 {pos.get('eod_score', 0):.2f} (기준 미달)"
                )

            console.print()
            console.print(f"[cyan]📋 우선 감시 리스트 ({len(priority_watchlist)}개):[/cyan]")
            for candidate in priority_watchlist:
                console.print(
                    f"  - {candidate['stock_name']} ({candidate['stock_code']}): "
                    f"점수 {candidate['score']:.2f}"
                )

            # 6. 우선 감시 리스트 저장 (다음날 갭업 재진입용)
            if priority_watchlist:
                self._save_priority_watchlist(priority_watchlist)

            console.print()
            console.print("=" * 80, style="bold green")
            console.print("✅ EOD 프로세스 완료", style="bold green")
            console.print("=" * 80, style="bold green")
            console.print()

        except Exception as e:
            console.print(f"[red]❌ EOD 프로세스 오류: {e}[/red]")
            import traceback
            traceback.print_exc()

    def _save_priority_watchlist(self, watchlist: List[Dict]):
        """우선 감시 리스트를 파일로 저장"""
        try:
            import json
            from pathlib import Path

            # data 디렉토리 생성
            data_dir = Path("data")
            data_dir.mkdir(exist_ok=True)

            # 오늘 날짜로 파일명 생성
            today = datetime.now().strftime("%Y-%m-%d")
            filepath = data_dir / f"priority_watchlist_{today}.json"

            # 저장
            with open(filepath, 'w', encoding='utf-8') as f:
                json.dump(watchlist, f, ensure_ascii=False, indent=2)

            console.print(f"[dim]✓ 우선 감시 리스트 저장: {filepath}[/dim]")

        except Exception as e:
            console.print(f"[yellow]⚠️  우선 감시 리스트 저장 실패: {e}[/yellow]")

    def _load_priority_watchlist(self) -> List[Dict]:
        """
        전날 저장된 우선 감시 리스트 로딩 (갭업 재진입용)

        Returns:
            List[Dict]: 우선 감시 리스트 (없으면 빈 리스트)
        """
        try:
            import json
            from pathlib import Path
            from datetime import timedelta

            data_dir = Path("data")

            # 전날 날짜로 파일명 생성 (장은 전날 저장된 것 사용)
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            filepath = data_dir / f"priority_watchlist_{yesterday}.json"

            # 파일이 없으면 빈 리스트 반환
            if not filepath.exists():
                console.print(f"[dim]ℹ️  우선 감시 리스트 없음: {filepath}[/dim]")
                return []

            # 파일 로드
            with open(filepath, 'r', encoding='utf-8') as f:
                watchlist = json.load(f)

            console.print(f"[green]✓ 우선 감시 리스트 로드: {len(watchlist)}개 종목[/green]")
            for candidate in watchlist:
                console.print(
                    f"  - {candidate['stock_name']} ({candidate['stock_code']}): "
                    f"EOD점수 {candidate['score']:.2f}"
                )

            return watchlist

        except Exception as e:
            console.print(f"[yellow]⚠️  우선 감시 리스트 로드 실패: {e}[/yellow]")
            return []

    async def check_gap_reentry_candidates(self, priority_watchlist: List[Dict]):
        """
        우선 감시 리스트에서 갭업 재진입 후보 체크

        Args:
            priority_watchlist: 전날 EOD에서 생성한 우선 감시 리스트

        갭업 재진입 조건 (config/strategy_hybrid.yaml):
            1. 갭업 >= 3.0%
            2. 거래량 Z-score >= 2.0
            3. 장 시작 후 30분 이내
            4. 3-5분봉 고점 돌파
        """
        try:
            # 갭업 재진입 설정 로드
            gap_config = self.config.get_section('gap_reentry')
            if not gap_config or not gap_config.get('enabled', False):
                return

            gap_threshold = gap_config.get('gap_threshold_pct', 3.0)
            volume_z_threshold = gap_config.get('volume_z_threshold', 2.0)
            check_window_minutes = gap_config.get('check_window_minutes', 30)
            first_candle_window = gap_config.get('first_candle_window', 5)
            reentry_confidence = gap_config.get('reentry_confidence', 0.7)

            console.print()
            console.print("=" * 80, style="bold cyan")
            console.print("🔍 갭업 재진입 후보 체크", style="bold cyan")
            console.print("=" * 80, style="bold cyan")

            # 🔥 ChatGPT Fix: 장 시작 시간 체크 (09:05 이후부터 체크)
            current_time = datetime.now()
            market_open = current_time.replace(hour=9, minute=5, second=0)  # 09:00 → 09:05
            check_end = current_time.replace(hour=9, minute=30, second=0)

            # 09:05 이전이면 대기
            if current_time < market_open:
                console.print(f"[dim]ℹ️  갭업 체크 대기 중 (09:05 이후 시작)[/dim]")
                return

            # 09:30 이후면 종료
            if current_time > check_end:
                console.print(f"[dim]ℹ️  갭업 체크 시간 경과 (30분 제한)[/dim]")
                return

            reentry_candidates = []

            for candidate in priority_watchlist:
                stock_code = candidate['stock_code']
                stock_name = candidate['stock_name']
                prev_close = candidate.get('prev_close', 0)

                # 🔥 ChatGPT Fix: 이미 보유 중이거나 오늘 재진입한 종목 제외
                if stock_code in self.positions:
                    pos = self.positions[stock_code]
                    if pos.get('gap_reentered_today', False):
                        console.print(f"[dim]  ⚠️  {stock_name}: 이미 오늘 갭업 재진입함[/dim]")
                    continue

                # 현재가 조회
                try:
                    # 5분봉 데이터 조회
                    market = candidate.get('market', 'KOSDAQ')
                    ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
                    ticker = f"{stock_code}{ticker_suffix}"

                    import yfinance as yf
                    df = yf.download(ticker, period='1d', interval='5m', progress=False)

                    # 🔥 Fix: DataFrame empty/None 체크 강화
                    if df is None or df.empty or len(df) < 2:
                        console.print(f"[dim]  ⚠️  {stock_name}: 데이터 부족 ({len(df) if df is not None else 0}봉)[/dim]")
                        continue

                    # 첫 5분봉 데이터
                    first_candle = df.iloc[0]
                    current_candle = df.iloc[-1]

                    open_price = first_candle['Open']
                    current_price = current_candle['Close']
                    first_high = first_candle['High']

                    # 1. 갭업 % 계산
                    if prev_close <= 0:
                        continue

                    gap_pct = ((open_price - prev_close) / prev_close) * 100

                    if gap_pct < gap_threshold:
                        continue  # 갭업 기준 미달

                    console.print(f"[cyan]📈 {stock_name} ({stock_code}): 갭업 {gap_pct:+.2f}%[/cyan]")

                    # 2. 거래량 체크 (volume_zscore 계산)
                    df['volume_ma20'] = df['Volume'].rolling(window=20).mean()
                    df['volume_std20'] = df['Volume'].rolling(window=20).std()
                    df['volume_zscore'] = (df['Volume'] - df['volume_ma20']) / df['volume_std20']

                    latest_vol_z = df['volume_zscore'].iloc[-1] if len(df) >= 20 else 0

                    if latest_vol_z < volume_z_threshold:
                        console.print(f"[dim]  ⚠️  거래량 부족 (Z-score: {latest_vol_z:.2f} < {volume_z_threshold})[/dim]")
                        continue

                    console.print(f"[green]  ✓ 거래량 양호 (Z-score: {latest_vol_z:.2f})[/green]")

                    # 3. 3-5분봉 고점 돌파 체크
                    # first_candle_window=5이면 첫 5분봉 (1개) 고점 사용
                    breakout_high = first_high

                    if current_price > breakout_high:
                        console.print(f"[green]  ✓ 고점 돌파: {current_price:,.0f}원 > {breakout_high:,.0f}원[/green]")

                        # 재진입 후보 추가
                        reentry_candidates.append({
                            'stock_code': stock_code,
                            'stock_name': stock_name,
                            'gap_pct': gap_pct,
                            'current_price': current_price,
                            'vol_z': latest_vol_z,
                            'confidence': reentry_confidence,
                            'df': df
                        })
                    else:
                        console.print(f"[dim]  ⚠️  고점 미돌파: {current_price:,.0f}원 <= {breakout_high:,.0f}원[/dim]")

                except Exception as e:
                    console.print(f"[yellow]⚠️  {stock_code} 갭업 체크 실패: {e}[/yellow]")
                    continue

            # 4. 재진입 실행
            if reentry_candidates:
                console.print()
                console.print(f"[green]✅ 갭업 재진입 후보: {len(reentry_candidates)}개[/green]")

                for candidate in reentry_candidates:
                    # Global Gate 확인 (시간/리스크 통합 체크)
                    _gap_ok, _gap_reason = self._check_global_risk_gates(
                        candidate['stock_code'], candidate['stock_name']
                    )
                    if not _gap_ok:
                        console.print(f"[yellow]⚠️  {candidate['stock_name']}: {_gap_reason}[/yellow]")
                        continue

                    # 🔥 ChatGPT Fix: 갭업 재진입 실행 및 플래그 설정
                    console.print(f"[green]🚀 갭업 재진입: {candidate['stock_name']} ({candidate['stock_code']})[/green]")
                    gap_entry_reason = f"{datetime.now().strftime('%H:%M')} 갭업 재진입 (신뢰도: {candidate['confidence']:.0%})"
                    self.execute_buy(
                        stock_code=candidate['stock_code'],
                        stock_name=candidate['stock_name'],
                        price=candidate['current_price'],
                        df=candidate['df'],
                        position_size_mult=1.0,
                        entry_confidence=candidate['confidence'],
                        entry_reason=gap_entry_reason
                    )

                    # 재진입 플래그 설정 (중복 방지)
                    if candidate['stock_code'] in self.positions:
                        self.positions[candidate['stock_code']]['gap_reentered_today'] = True
            else:
                console.print("[dim]ℹ️  갭업 재진입 후보 없음[/dim]")

            console.print("=" * 80, style="bold cyan")
            console.print()

        except Exception as e:
            console.print(f"[red]❌ 갭업 재진입 체크 오류: {e}[/red]")
            import traceback
            traceback.print_exc()

    def _adjust_price_to_tick(self, price: float) -> int:
        """
        호가단위에 맞게 가격 조정

        호가단위:
        - 1,000원 미만: 1원
        - 1,000~5,000원: 5원
        - 5,000~10,000원: 10원
        - 10,000~50,000원: 50원
        - 50,000원 이상: 100원
        """
        price = int(price)

        if price < 1000:
            return price  # 1원 단위
        elif price < 5000:
            return (price // 5) * 5  # 5원 단위
        elif price < 10000:
            return (price // 10) * 10  # 10원 단위
        elif price < 50000:
            return (price // 50) * 50  # 50원 단위
        else:
            return (price // 100) * 100  # 100원 단위

    def execute_partial_sell(self, stock_code: str, price: float, profit_pct: float, exit_ratio: float, stage: int):
        """부분 청산 실행

        Args:
            stock_code: 종목코드
            price: 매도가
            profit_pct: 수익률
            exit_ratio: 청산 비율 (0.4 = 40%)
            stage: 청산 단계 (1, 2)
        """
        position = self.positions.get(stock_code)
        if not position:
            return

        # 🔧 CRITICAL FIX: 장 시간 체크 (장 종료 후 주문 방지)
        if not self.is_market_open():
            current_time = datetime.now().strftime('%H:%M:%S')
            console.print(f"[red]❌ 장 종료 시간입니다 ({current_time})[/red]")
            console.print(f"[red]   종목 {stock_code} ({position.get('name', '')}): 부분청산 주문 불가[/red]")
            console.print(f"[yellow]⚠️  내일 장 시작 시 수동으로 처리하세요.[/yellow]")
            return

        # 🔧 FIX: 점심시간 수익 청산 차단 (12:00-14:00)
        # 부분 청산은 항상 수익 실현이므로 점심시간에는 차단
        from datetime import time as time_class
        current_time = datetime.now().time()
        MIDDAY_START = time_class(12, 0, 0)
        MIDDAY_END = time_class(14, 0, 0)

        if MIDDAY_START <= current_time < MIDDAY_END:
            console.print(f"[yellow]🚫 점심시간 부분청산 차단 ({current_time.strftime('%H:%M:%S')})[/yellow]")
            console.print(f"[yellow]   {position.get('name', '')} ({stock_code}): 14:00 이후 재시도[/yellow]")
            return

        # 청산할 수량 계산 (초기 수량 대비)
        initial_quantity = position.get('initial_quantity', position['quantity'])
        partial_quantity = int(initial_quantity * exit_ratio)

        # 최소 1주는 청산해야 함
        if partial_quantity < 1:
            return

        # 현재 보유 수량보다 많이 팔 수 없음
        if partial_quantity > position['quantity']:
            partial_quantity = position['quantity']

        # 실현 손익 계산
        realized_profit = (price - position['entry_price']) * partial_quantity

        if stage >= 2:
            trailing_cfg = self.config.get_trailing_config()
            ratio = trailing_cfg.get('ratio', getattr(self.analyzer, 'trailing_ratio', 1.0))
            ratio = max(ratio, 0.1)
            position['highest_price'] = max(position.get('highest_price', price), price)
            position['trailing_active'] = True
            position['trailing_stop_price'] = position['highest_price'] * (1 - ratio / 100)

        console.print()
        console.print("=" * 80, style="yellow")
        console.print(f"🎯 부분 청산 {stage}단계: {position['name']} ({stock_code})", style="bold yellow")
        console.print(f"   매수가: {position['entry_price']:,.0f}원")
        console.print(f"   매도가: {price:,.0f}원")
        console.print(f"   수익률: {profit_pct:+.2f}%")
        console.print(f"   청산비율: {exit_ratio*100:.0f}% ({partial_quantity}/{initial_quantity}주)")
        console.print(f"   실현손익: {realized_profit:+,.0f}원")
        console.print(f"   남은수량: {position['quantity'] - partial_quantity}주")

        # 실제 키움 API 부분 매도 주문 (DB 저장 전에 실행!)
        try:
            console.print(f"[yellow]📡 키움 API 부분 매도 주문 전송 중...[/yellow]")

            # 🔧 CRITICAL FIX: 시장가 → 지정가 변경 + 호가단위 적용
            target_price = price * 0.995  # 현재가 -0.5%
            sell_price = self._adjust_price_to_tick(target_price)  # 호가단위 조정
            console.print(f"[dim]  지정가 설정: {sell_price:,}원 (현재가 {price:,}원의 99.5% → 호가단위 조정)[/dim]")

            order_result = self.api.order_sell(
                stock_code=stock_code,
                quantity=partial_quantity,
                price=sell_price,  # 0 → sell_price (지정가)
                trade_type="0"  # "3" → "0" (지정가 - 보통)
            )

            if order_result.get('return_code') != 0:
                console.print(f"[red]❌ 부분 매도 주문 실패: {order_result.get('return_msg')}[/red]")
                console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
                return

            order_no = order_result.get('ord_no')
            console.print(f"[green]✓ 부분 매도 주문 성공 - 주문번호: {order_no}[/green]")

        except Exception as e:
            console.print(f"[red]❌ 부분 매도 API 호출 실패: {e}[/red]")
            console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
            return

        # 🔧 FIX: 주문 성공 후에만 DB에 저장
        trade_id = position.get('trade_id')
        if trade_id:
            entry_time = position.get('entry_time') or position.get('entry_date')
            partial_exit_time = datetime.now()
            holding_duration = (partial_exit_time - entry_time).seconds if entry_time else 0

            partial_sell_trade = {
                'stock_code': stock_code,
                'stock_name': position['name'],
                'trade_type': 'SELL',
                'trade_time': partial_exit_time.isoformat(),
                'price': sell_price,  # 실제 체결 가격 사용
                'quantity': partial_quantity,
                'amount': sell_price * partial_quantity,  # 실제 체결 금액
                'exit_reason': f'부분청산 {stage}단계 (+{profit_pct:.1f}%)',
                'realized_profit': realized_profit,
                'profit_rate': profit_pct,
                'holding_duration': holding_duration,
                # 🔧 2026-03-08: trade_duration 분석용 필드 추가
                'entry_time': entry_time.isoformat() if entry_time else None,
                'exit_time': partial_exit_time.isoformat(),
                'holding_minutes': int(holding_duration // 60)
            }
            self.db.insert_trade(partial_sell_trade)

        # 리스크 관리자에 거래 기록
        partial_sell_reason = f"{datetime.now().strftime('%H:%M')} 부분청산 {stage}단계 (+{profit_pct:.1f}%)"
        self.risk_manager.record_trade(
            stock_code=stock_code,
            stock_name=position['name'],
            trade_type='SELL',
            quantity=partial_quantity,
            price=price,
            realized_pnl=realized_profit,
            reason=partial_sell_reason
        )

        # ✅ TradeStateManager에 부분 청산 기록
        strategy_tag = position.get('strategy_tag', self.default_strategy_tag)  # ✅ 동적 기본값
        self.state_manager.mark_traded(
            stock_code=stock_code,
            stock_name=position['name'],
            action=TradeAction.PARTIAL_SELL,
            price=price,
            quantity=partial_quantity,
            strategy_tag=strategy_tag,
            reason=f"부분청산 {stage}단계 (+{profit_pct:.1f}%)"
        )

        # 포지션 업데이트
        position['quantity'] -= partial_quantity
        position['partial_exit_stage'] = stage
        position['total_realized_profit'] += realized_profit

        # 🔧 CRITICAL FIX: 부분 청산 후에도 쿨다운 설정 (재진입 방지)
        if position['quantity'] > 0:
            # 아직 포지션이 남아있지만 쿨다운 시작 (부분 청산은 익절이므로 is_loss=False)
            self.stock_cooldown[stock_code] = (datetime.now(), False, '부분청산')
            partial_cd = self._cooldown_by_reason.get('partial_exit', self.cooldown_minutes) if self._cooldown_v2_enabled else self.cooldown_minutes
            console.print(f"[yellow]⏸️  {position['name']}: [partial_exit] 쿨다운 {partial_cd}분 시작[/yellow]")

        console.print(f"✅ 부분 청산 완료 (주문번호: {order_no})")
        console.print("=" * 80, style="yellow")
        console.print()

    def execute_sell(self, stock_code: str, price: float, profit_pct: float, reason: str, use_market_order: bool = False):
        """매도 실행 (전량 청산)"""
        position = self.positions.get(stock_code)
        if not position:
            return

        # 🔧 CRITICAL FIX: 장 시간 체크 (장 종료 후 주문 방지)
        if not self.is_market_open():
            current_time = datetime.now().strftime('%H:%M:%S')
            console.print(f"[red]❌ 장 종료 시간입니다 ({current_time})[/red]")
            console.print(f"[red]   종목 {stock_code} ({position.get('name', '')}): 주문 불가[/red]")
            console.print(f"[yellow]⚠️  내일 장 시작 시 수동으로 처리하세요.[/yellow]")
            return

        # 🔧 FIX: 점심시간 수익 청산 차단 (12:00-14:00)
        # 손절(profit_pct < 0)은 허용, 수익 청산만 차단
        from datetime import time as time_class
        current_time = datetime.now().time()
        MIDDAY_START = time_class(12, 0, 0)
        MIDDAY_END = time_class(14, 0, 0)

        if MIDDAY_START <= current_time < MIDDAY_END and profit_pct > 0:
            console.print(f"[yellow]🚫 점심시간 수익 청산 차단 ({current_time.strftime('%H:%M:%S')})[/yellow]")
            console.print(f"[yellow]   {position.get('name', '')} ({stock_code}): 수익률 {profit_pct:+.2f}%[/yellow]")
            console.print(f"[yellow]   14:00 이후 재시도 또는 손절 시에만 허용[/yellow]")
            return

        # 🔧 FIX: 실제 보유 수량 확인 (부분 청산 후 불일치 방지)
        try:
            account_info = self.api.get_account_info()
            if account_info and account_info.get('return_code') == 0:
                # 🔧 CRITICAL FIX: 올바른 API 응답 키 사용 (ka01690 명세)
                holdings = account_info.get('day_bal_rt', [])  # 'holdings' → 'day_bal_rt'
                actual_qty = 0
                for holding in holdings:
                    # 🔧 FIX: 올바른 필드명 사용
                    if holding.get('stk_cd') == stock_code:  # 'stock_code' → 'stk_cd'
                        actual_qty = int(holding.get('rmnd_qty', 0))  # 'quantity' → 'rmnd_qty'
                        break

                if actual_qty > 0 and actual_qty != position['quantity']:
                    console.print(f"[yellow]⚠️  수량 불일치 감지: 시스템 {position['quantity']}주 → 실제 {actual_qty}주[/yellow]")
                    position['quantity'] = actual_qty
                elif actual_qty == 0:
                    console.print(f"[red]❌ 보유 수량 0주: 이미 전량 청산됨[/red]")
                    del self.positions[stock_code]
                    return
        except Exception as e:
            console.print(f"[yellow]⚠️  보유 수량 확인 실패, 시스템 수량 사용: {e}[/yellow]")

        # entry_time이 없을 수 있으므로 안전하게 처리
        entry_time = position.get('entry_time') or position.get('entry_date')
        if entry_time:
            holding_duration = (datetime.now() - entry_time).seconds
        else:
            holding_duration = 0  # entry_time 없으면 0으로 설정

        realized_profit = (price - position['entry_price']) * position['quantity']

        console.print()
        console.print("=" * 80, style="red")
        console.print(f"🔔 매도 신호 발생: {position['name']} ({stock_code})", style="bold red")
        console.print(f"   매수가: {position['entry_price']:,.0f}원")
        console.print(f"   매도가: {price:,.0f}원")
        console.print(f"   매도수량: {position['quantity']}주")
        console.print(f"   수익률: {profit_pct:+.2f}%")
        console.print(f"   실현손익: {realized_profit:+,.0f}원")
        console.print(f"   사유: {reason}")
        console.print(f"   보유시간: {holding_duration // 60}분")

        # DB에 매도 정보 저장 — trade_id 유무 관계없이 항상 SELL 레코드 저장
        trade_id = position.get('trade_id')
        exit_time_dt = datetime.now()
        sell_trade = {
            'stock_code': stock_code,
            'stock_name': position['name'],
            'trade_type': 'SELL',
            'trade_time': exit_time_dt.isoformat(),
            'price': float(price),
            'quantity': int(position['quantity']),
            'amount': float(price * position['quantity']),
            'exit_reason': reason,
            'realized_profit': float(realized_profit),
            'profit_rate': float(profit_pct),
            'holding_duration': int(holding_duration),
            'entry_time': entry_time.isoformat() if entry_time else None,
            'exit_time': exit_time_dt.isoformat(),
            'holding_minutes': int(holding_duration // 60),
            'exit_context': {
                'mfe_pct': round((position.get('highest_price', position['entry_price']) - position['entry_price']) / position['entry_price'] * 100, 3),
                'exit_vs_mfe': round((price - position.get('highest_price', price)) / position['entry_price'] * 100, 3)
            }
        }
        try:
            self.db.insert_trade(sell_trade)
            logger.info(
                f"[SELL_COMPLETE] {stock_code} {position['name']} | "
                f"price={price:,} qty={position['quantity']} pnl={profit_pct:+.2f}% "
                f"realized={realized_profit:+,.0f} | reason={reason} | trade_id={trade_id}"
            )
        except Exception as _sell_db_e:
            logger.error(f"[SELL_DB_ERROR] {stock_code} DB 저장 실패: {_sell_db_e}")

        # ── EQ Feature Logger 결과 기록 ──────────────────────────────────
        try:
            if getattr(self, 'eq_feature_logger', None):
                self.eq_feature_logger.log_outcome(stock_code, profit_pct, reason)
            if getattr(self, 'eq_model', None):
                self.eq_model.maybe_retrain(self.eq_feature_logger)
        except Exception as _eqoe:
            logger.debug(f"[EQ_LOG] 결과 기록 실패: {_eqoe}")
        # ─────────────────────────────────────────────────────────────────

        # 의사결정 추적 — 청산 신호 기록 + ml_dataset 자동 생성
        try:
            from database.decision_trace import record_exit_signal
            _mfe = position.get('mfe_pct', 0.0)
            _mae = position.get('mae_pct', 0.0)
            # EXPLORATION 피처 수집 (entry 시 저장된 값 활용)
            _expl_feats = position.get('ml_features')
            if _expl_feats is None and position.get('entry_reason', ''):
                _er = position.get('entry_reason', '')
                if 'EXPLORATION' in _er:
                    _expl_feats = {
                        'strategy': 'EXPLORATION',
                        'entry_type': position.get('expl_entry_type', 'IMMEDIATE'),
                        'rvol': position.get('expl_rvol'),
                        'price_vs_breakout': position.get('expl_price_vs_bp'),
                        'pending_duration_sec': position.get('expl_pending_sec'),
                        'vwap_distance': position.get('expl_vwap_dist'),
                    }
            record_exit_signal(
                stock_code=stock_code,
                stock_name=position['name'],
                exit_reason=reason,
                price=float(price),
                trade_id=position.get('trade_id'),
                entry_price=float(position['entry_price']),
                quantity=int(position['quantity']),
                holding_minutes=int(holding_duration // 60),
                mfe_pct=_mfe if _mfe else None,
                mae_pct=_mae if _mae else None,
                extra_features=_expl_feats,
            )
        except Exception:
            pass

        # 실제 키움 API 매도 주문
        # 🔧 2026-04-15: 토큰 만료(8005) 시 재발급 후 1회 재시도 [EOD 강제청산 보호]
        order_result = None  # 🔧 FIX: 초기화 (NoneType 에러 방지)
        order_no = None
        sell_price = None    # 스코프 선언 — 재시도 시 재계산 방지

        # [FIX1] 청산 중 포지션 가치 pending 등록 — current_price 기준으로 실제 노출에 일치
        _pending_val = float(position.get('quantity', 0)) * float(
            position.get('current_price') or price  # 현재가 우선, 없으면 매도 호가
        )
        _pending_val = min(_pending_val, max(0.0, self.positions_value))  # 음수/과다 방지
        self._pending_exit_value = min(
            self.positions_value,
            self._pending_exit_value + _pending_val,
        )

        for _sell_attempt in range(2):
            try:
                if use_market_order:
                    # Emergency Hard Stop: 시장가 주문
                    console.print(f"[red]📡 긴급 시장가 매도 주문 전송 중...[/red]")
                    order_result = self.api.order_sell(
                        stock_code=stock_code,
                        quantity=position['quantity'],
                        price=0,  # 시장가
                        trade_type="3"  # 시장가
                    )
                else:
                    # 일반 청산: 현재가 -0.5% 지정가 주문
                    console.print(f"[yellow]📡 키움 API 매도 주문 전송 중...[/yellow]")

                    # 🔧 CRITICAL FIX: 현재가 그대로 → 현재가 -0.5% + 호가단위 적용
                    if sell_price is None:
                        target_price = price * 0.995  # 현재가 -0.5%
                        sell_price = self._adjust_price_to_tick(target_price)  # 호가단위 조정
                        console.print(f"[dim]  지정가 설정: {sell_price:,}원 (현재가 {price:,}원의 99.5% → 호가단위 조정)[/dim]")

                    order_result = self.api.order_sell(
                        stock_code=stock_code,
                        quantity=position['quantity'],
                        price=sell_price,  # int(price) → sell_price
                        trade_type="0"  # 지정가
                    )
                break  # 주문 성공 — 루프 탈출

            except Exception as e:
                if _sell_attempt == 0 and ('8005' in str(e) or 'Token이 유효하지 않습니다' in str(e)):
                    # 🔧 2026-04-15: 토큰 만료 감지 → 재발급 후 재시도
                    logger.warning(f"[SELL_TOKEN_EXPIRED] {stock_code} 토큰 만료(8005) — 재발급 후 재시도")
                    console.print(f"[yellow]🔄 토큰 만료 감지 — 재발급 후 매도 재시도[/yellow]")
                    if not self.refresh_access_token():
                        logger.critical(f"[SELL_TOKEN_REFRESH_FAIL] {stock_code} 토큰 재발급 실패 — 수동 처리 필요")
                        console.print(f"[red]❌ 토큰 재발급 실패 — 수동 처리 필요[/red]")
                        return
                    # _sell_attempt=1 로 재시도
                else:
                    logger.error(f"[SELL_ERROR] {stock_code}: {e}")
                    console.print(f"[red]❌ 매도 API 호출 실패: {e}[/red]")
                    console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
                    import traceback
                    console.print(f"[dim]{traceback.format_exc()}[/dim]")
                    return

        # 🔧 FIX: order_result가 None인 경우 처리
        if order_result is None:
            console.print(f"[red]❌ 매도 주문 응답 없음 (API 오류)[/red]")
            console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
            return

        if order_result.get('return_code') != 0:
            console.print(f"[red]❌ 매도 주문 실패: {order_result.get('return_msg')}[/red]")
            console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
            return

        order_no = order_result.get('ord_no')
        console.print(f"[green]✓ 매도 주문 성공 - 주문번호: {order_no}[/green]")
        self._pending_exit_value = max(0.0, self._pending_exit_value - _pending_val)

        # 리스크 관리자에 거래 기록
        self.risk_manager.record_trade(
            stock_code=stock_code,
            stock_name=position['name'],
            trade_type='SELL',
            quantity=position['quantity'],
            price=price,
            realized_pnl=realized_profit,
            reason=reason  # 매도 이유 전달
        )

        # 🔧 2026-03-07: Daily Loss Limit 누적
        # 🔥 2026-03-27: include_overnight:false → 오버나이트 포지션은 당일 daily_pnl 제외
        _dr_cfg = self.config.get('risk_control.daily_risk', {})
        _include_overnight = _dr_cfg.get('include_overnight', True)
        _is_overnight = False
        if not _include_overnight:
            _entry_time = position.get('entry_time') or position.get('entry_date')
            if _entry_time:
                _entry_date = _entry_time.date() if hasattr(_entry_time, 'date') else None
                if _entry_date and _entry_date < datetime.now().date():
                    _is_overnight = True
                    logger.info(f"[OVERNIGHT_PNL_SKIP] {stock_code} 오버나이트 손익 {profit_pct:+.2f}% → daily_pnl 제외")
        if not _is_overnight:
            self._daily_pnl_pct += profit_pct
        if _dr_cfg.get('enabled', True) and not self._daily_loss_halted:
            _limit = _dr_cfg.get('daily_loss_limit_pct', -3.0)
            if self._daily_pnl_pct <= _limit:
                self._daily_loss_halted = True
                logger.critical(
                    f"[DAILY_LOSS_LIMIT] 일일 누적 손실 {self._daily_pnl_pct:.2f}% <= {_limit}% "
                    f"→ 당일 거래 종료"
                )
                console.print(
                    f"[bold red]🚨 [DAILY_LOSS_LIMIT] 일일 손실 {self._daily_pnl_pct:.2f}% — 오늘 거래 종료![/bold red]"
                )

        # 🔧 FIX: 손실 스트릭 업데이트 및 쿨다운 설정
        is_win = profit_pct > 0

        if is_win:
            # 승리 → 스트릭 리셋
            self.stock_loss_streak[stock_code] = 0
            console.print(f"[green]✅ {position['name']}: 수익 거래로 손실 스트릭 초기화[/green]")
        else:
            # 손실 → 스트릭 증가
            self.stock_loss_streak[stock_code] = self.stock_loss_streak.get(stock_code, 0) + 1
            current_streak = self.stock_loss_streak[stock_code]

            # 🔥 ChatGPT Fix: 손절 시 allow_overnight False 처리
            # (당일 손절된 종목은 EOD 보유 대상에서 제외)
            position['allow_overnight'] = False

            console.print(f"[yellow]📉 {position['name']}: 연속 손실 {current_streak}회 (손실률: {profit_pct:.2f}%)[/yellow]")

            # 🔧 강화된 금지 로직 (2025-11-28 추가)
            should_ban = False
            ban_reason = ""

            # 1. 일일 -5% 이상 → 즉시 당일 금지
            if profit_pct <= -5.0:
                should_ban = True
                ban_reason = f"단일 거래 대손실 ({profit_pct:.2f}%)"
                console.print(f"[red]🚨 {position['name']}: 대손실 {profit_pct:.2f}% 발생![/red]")

            # 2. 2회 연속 -3% 이상 → 당일 금지
            elif current_streak >= 2 and profit_pct <= -3.0:
                should_ban = True
                ban_reason = f"{current_streak}회 연속 -3% 이상 손실"
                console.print(f"[red]🚨 {position['name']}: {current_streak}회 연속 중손실![/red]")

            # 3. 3회 연속 손실 → 당일 진입 금지 + 쿨다운 파일 생성
            elif current_streak >= self.max_consecutive_losses:
                should_ban = True
                ban_reason = f'{current_streak}회 연속 손실'

            # 금지 실행
            if should_ban:
                self.stock_ban_list.add(stock_code)
                console.print(f"[red]🚫 {position['name']}: {ban_reason}로 당일 진입 금지[/red]")

                # 🔧 쿨다운 파일 생성 (프로세스 간 공유)
                from pathlib import Path
                import json
                from datetime import timedelta

                cooldown_file = Path('data/cooldown.lock')
                cooldown_file.parent.mkdir(exist_ok=True)

                cooldown_until = (datetime.now() + timedelta(days=1)).isoformat()

                cooldown_data = {
                    'stock_code': stock_code,
                    'stock_name': position['name'],
                    'triggered_at': datetime.now().isoformat(),
                    'cooldown_until': cooldown_until,
                    'consecutive_losses': current_streak,
                    'loss_rate': profit_pct,
                    'reason': ban_reason
                }

                cooldown_file.write_text(json.dumps(cooldown_data, indent=2, ensure_ascii=False))
                console.print(f"[red]🔒 쿨다운 활성화: {cooldown_until[:10]}까지 모든 거래 중지[/red]")

            # 🔧 2026-02-07 v2: exit_reason 기반 차등 쿨다운
            is_loss = profit_pct < 0
            self.stock_cooldown[stock_code] = (datetime.now(), is_loss, reason)

            # 🔧 2026-02-10: Market Sensor — EF 발동 시 시장 상태 업데이트
            reason_cat = self._categorize_exit_reason(reason)
            if reason_cat in ('ef_no_follow', 'ef_no_demand'):
                ef_subtype = 'no_follow' if reason_cat == 'ef_no_follow' else 'no_demand'
                ms_config = self.config.get('re_entry.reentry_cooldown.market_sensor', {})
                ms_result = self.reentry_metrics.record_ef_event(ef_subtype, ms_config)
                if ms_result.get('message'):
                    console.print(f"[bold red]🔴 {ms_result['message']}[/bold red]")

            # 🔧 2026-02-16: Conservative Mode — Hard Stop 발동 시 보수 모드 활성화
            if reason_cat == 'hard_stop':
                cm_config = self.config.get('risk_control.conservative_mode', {})
                cm_result = self.reentry_metrics.record_hard_stop_event(
                    cm_config, symbol=position['name'], pnl_pct=profit_pct
                )
                if cm_result.get('message'):
                    console.print(f"[bold red]{cm_result['message']}[/bold red]")
                if cm_result.get('trading_halted'):
                    console.print(f"[bold red]🚨 당일 거래 종료! (Hard Stop {cm_result.get('hard_stop_count', 0)}회)[/bold red]")

            # 🔧 2026-02-19: Loss Streak Guard 상태 업데이트 (매도 후 연패 수 변동)
            self._check_loss_streak_guard()

            # v2: config 기반 쿨다운 시간 표시
            if self._cooldown_v2_enabled and self._cooldown_by_reason:
                reason_category = self._categorize_exit_reason(reason)
                cooldown_time = self._cooldown_by_reason.get(
                    reason_category,
                    self._cooldown_by_reason.get('default', 30)
                )
                console.print(f"[yellow]⏸️  {position['name']}: [{reason_category}] 쿨다운 {cooldown_time}분 시작[/yellow]")
            else:
                cooldown_time = self.loss_cooldown_minutes if is_loss else self.cooldown_minutes
                console.print(f"[yellow]⏸️  {position['name']}: 쿨다운 {cooldown_time}분 시작 ({'손절' if is_loss else '익절'})[/yellow]")

        # ✅ TradeStateManager에 매도 기록
        strategy_tag = position.get('strategy_tag', self.default_strategy_tag)  # ✅ 동적 기본값

        # 손절 여부 판단 (손실 + 특정 사유)
        is_stoploss = is_loss and any(keyword in reason.lower() for keyword in ['손절', 'stop', '하락', 'emergency'])

        if is_stoploss:
            # 손절 기록
            self.state_manager.mark_stoploss(
                stock_code=stock_code,
                stock_name=position['name'],
                entry_price=position['entry_price'],
                exit_price=price,
                reason=reason
            )
        else:
            # 일반 매도 기록
            self.state_manager.mark_traded(
                stock_code=stock_code,
                stock_name=position['name'],
                action=TradeAction.SELL,
                price=price,
                quantity=position['quantity'],
                strategy_tag=strategy_tag,
                reason=reason
            )

        # 포지션 제거
        del self.positions[stock_code]
        self._save_positions_state()  # 재시작 복원용 상태 갱신

        # ── 2026-03-31: DriftDetector + TradeStats 기록 ──────────────────────
        try:
            _entry_reason_str = position.get('entry_reason', '') or ''
            if _entry_reason_str.startswith('DEFENSIVE:'):
                _strat_tag = 'defensive'
            elif _entry_reason_str.startswith('SHORT:'):
                _strat_tag = 'short'
            elif _entry_reason_str.startswith('SQZ:'):
                _strat_tag = 'sqz'
            elif 'TREND' in _entry_reason_str.upper():
                _strat_tag = 'trend'
            elif _entry_reason_str.startswith('RS:'):
                _strat_tag = 'rs'
            else:
                _strat_tag = 'smc'

            # 🔧 2026-04-30: Squeeze 연패 추적 + 패턴 학습
            if _strat_tag == 'sqz':
                if profit_pct < 0:
                    self._sqz_consecutive_losses += 1
                else:
                    self._sqz_consecutive_losses = 0
                logger.info(
                    f"[SQZ_STREAK] {stock_code} pnl={profit_pct:+.2f}% "
                    f"consecutive_losses={self._sqz_consecutive_losses}"
                )
                _sqz_pat_key = position.get('sqz_pattern_key', '')
                if _sqz_pat_key:
                    self.sqz_pattern_stats.update(_sqz_pat_key, profit_pct)
                    self.sqz_pattern_stats.save("logs/sqz_pattern_stats.json")
                    _pm = self.sqz_pattern_stats.metrics(_sqz_pat_key)
                    if _pm:
                        logger.info(
                            f"[PATTERN_UPDATE] {_sqz_pat_key} "
                            f"WR={_pm[0]:.2f} E={_pm[1]:+.3f} N={_pm[2]} streak={_pm[3]}"
                        )

            # 🔧 2026-04-03: 레짐 엔진 성과 기록 (DEF/RS 동적 사이징용)
            if _strat_tag == 'defensive':
                self.regime_engine.record_trade('def', profit_pct)
            elif _strat_tag == 'rs':
                self.regime_engine.record_trade('rs', profit_pct)

            # 🔧 2026-04-03: Drawdown 엔진 — 당일 누적 PnL 갱신 (전략별 분리)
            if self.config.get("drawdown_engine", {}).get("enabled", True):
                self.drawdown_engine.record_pnl(profit_pct, strategy=_strat_tag)

            self.drift_detector.record_trade(
                pnl_pct=profit_pct,
                strategy=_strat_tag,
                stock=stock_code,
                equity=self.current_cash,
            )
            self._trade_stats.record(profit_pct)
            logger.info(
                f"[DRIFT_REC] {stock_code} pnl={profit_pct:+.2f}% strat={_strat_tag} "
                f"drift={self.drift_detector.get_drift_level()[0].value}"
            )
        except Exception as _re:
            logger.debug(f"[DRIFT_REC_ERR] {stock_code}: {_re}")

        # ── EQ 모델 드리프트 기반 재학습 ─────────────────────────────────────
        try:
            if (getattr(self, 'drift_detector', None)
                    and self.drift_detector.needs_retrain()
                    and getattr(self, 'eq_model', None)
                    and self.eq_model.enabled):
                _drift_data = self.eq_feature_logger.load_labeled(
                    min_samples=self.eq_model.min_samples
                )
                if _drift_data:
                    logger.info(
                        f"[EQ_DRIFT_RETRAIN] 드리프트 RETRAIN 감지 → EQ 재학습 "
                        f"n={len(_drift_data)}"
                    )
                    if self.eq_model.train(_drift_data):
                        self.drift_detector.ack_retrain()
                        logger.info("[EQ_DRIFT_RETRAIN] 완료, retrain 플래그 해제")
        except Exception as _dre:
            logger.debug(f"[EQ_DRIFT_RETRAIN_ERR] {_dre}")

        # 🔧 2026-04-24: OnlineStats EMA 업데이트
        try:
            if hasattr(self, 'online_stats'):
                self.online_stats.update(profit_pct)
        except Exception as _ole:
            logger.debug(f"[OL_UPDATE_ERR] {stock_code}: {_ole}")
        # ─────────────────────────────────────────────────────────────────────

        # ── 2026-04-01: A+ 결과 추적 ─────────────────────────────────────────
        try:
            if position.get('a_plus_mode', False) or _entry_reason_str.startswith('A+:'):
                _ap_result = 'WIN' if profit_pct > 0.3 else ('LOSS' if profit_pct < -0.3 else 'BE')
                logger.info(
                    f"[A_PLUS_RESULT:{_ap_result}] {stock_code} | "
                    f"pnl={profit_pct:+.2f}% | reason={reason[:30]} | "
                    f"today_count={self._daily_a_plus_count}"
                )
                console.print(
                    f"[{'green' if _ap_result == 'WIN' else 'red' if _ap_result == 'LOSS' else 'yellow'}]"
                    f"🎯 [A+ {_ap_result}] {position.get('name', stock_code)}: "
                    f"{profit_pct:+.2f}%[/]"
                )
        except Exception:
            pass
        # ─────────────────────────────────────────────────────────────────────

        # ── 2026-03-31: EXPLORATION 전용 통계 + 자동 kill ────────────────────
        try:
            if _entry_reason_str.startswith('EXPLORATION:'):
                _kill_cfg = self.config.get('exploration', {})
                _kill_wr_thr = _kill_cfg.get('auto_kill_winrate', 0.30)
                _kill_min_n  = _kill_cfg.get('auto_kill_min_count', 10)
                self._exploration_stats['count'] += 1
                if profit_pct > 0:
                    self._exploration_stats['wins'] += 1
                self._exploration_stats['total_pnl'] = (
                    self._exploration_stats.get('total_pnl', 0.0) + profit_pct
                )
                _expl_cnt = self._exploration_stats['count']
                _expl_wr  = self._exploration_stats['wins'] / _expl_cnt
                _expl_avg = self._exploration_stats['total_pnl'] / _expl_cnt
                logger.info(
                    f"[EXPLORATION_STATS] {stock_code} count={_expl_cnt} "
                    f"WR={_expl_wr:.0%} avg={_expl_avg:+.2f}%"
                )
                # 자동 kill 조건: 10건 이상 + 승률 30% 미만
                if (_expl_cnt >= _kill_min_n
                        and _expl_wr < _kill_wr_thr
                        and not self._exploration_killed):
                    self._exploration_killed = True
                    logger.critical(
                        f"[EXPLORATION_KILLED] 승률 {_expl_wr:.0%} < {_kill_wr_thr:.0%} "
                        f"({_expl_cnt}건) → EXPLORATION 자동 비활성화"
                    )
                    console.print(
                        f"[red bold]💀 [EXPLORATION_KILLED] 승률 {_expl_wr:.0%} "
                        f"→ 자동 OFF (YAML 확인 필요)[/red bold]"
                    )
                # 파일 저장 (세션 간 유지)
                try:
                    import json as _json_es
                    from pathlib import Path as _Path_es
                    _Path_es('data').mkdir(exist_ok=True)
                    _Path_es('data/exploration_stats.json').write_text(
                        _json_es.dumps({
                            'stats': self._exploration_stats,
                            'killed': self._exploration_killed,
                            'updated': datetime.now().isoformat(),
                        }, indent=2, ensure_ascii=False)
                    )
                except Exception:
                    pass
        except Exception as _expl_rec_e:
            logger.debug(f"[EXPLORATION_STATS_ERR] {stock_code}: {_expl_rec_e}")
        # ─────────────────────────────────────────────────────────────────────

        # ── capture_exit: 청산 지표 기록 ─────────────────────────────────────
        _cap_entry_time = position.get('entry_time') or position.get('entry_date')
        capture_exit(
            stock_code=stock_code,
            stock_name=position.get('name', ''),
            exit_price=price,
            entry_price=position.get('entry_price'),
            pnl_pct=profit_pct,
            exit_reason=reason,
            entry_time=_cap_entry_time,
        )
        # ─────────────────────────────────────────────────────────────────────

        # 🔧 2026-04-16: TRADE_RESULT 로그 (eq/choch 등급 포함 → entry_quality_analyzer 파싱용)
        try:
            import re as _re
            _reason_tag = 'OTHER'
            for _tag in ('TIME_EXIT', 'R_TP2', 'R_TP1', 'NO_MOVE_EXIT', 'HARD_STOP',
                         'TRAILING', 'VWAP', 'TIME', 'Early Failure'):
                if _tag.lower() in reason.lower():
                    _reason_tag = _tag
                    break
            logger.info(
                f"[TRADE_RESULT] {stock_code} | "
                f"pnl={profit_pct:+.2f}% | "
                f"reason_tag={_reason_tag} | "
                f"eq={position.get('eq_grade', '-')} | "
                f"choch={position.get('choch_grade_log', position.get('choch_grade', '-'))} | "
                f"hold={int(holding_duration // 60)}m | "
                f"mfe={position.get('mfe_pct', 0.0):.2f} | "
                f"mae={position.get('mae_pct', 0.0):.2f} | "
                f"overnight={1 if position.get('allow_overnight', False) else 0} | "
                f"r_pct={position.get('r_pct', 0):.2f}"
            )
        except Exception:
            pass

        # KPI 누적
        try:
            _kpi = getattr(self, '_kpi', None)
            if _kpi is not None:
                _tp1_hit = position.get('partial_exit_stage', 0) >= 1
                _kpi['trades'].append({
                    'pnl':          profit_pct,
                    'hold_m':       int(holding_duration // 60),
                    'mfe':          position.get('mfe_pct', 0.0),
                    'tp1':          _tp1_hit,
                    'exit_reason':  reason,
                    'entry_reason': position.get('entry_reason', ''),
                })
        except Exception:
            pass

        console.print(f"✅ 매도 완료 (주문번호: {order_no})")
        self._refresh_dashboard_cache()

        # ── TradeLogger 청산 기록 ─────────────────────────────────────────
        try:
            if getattr(self, '_trade_logger', None):
                _ep = float(position.get('entry_price', 0) or position.get('avg_price', 0))
                self._trade_logger.log_exit(
                    symbol      = stock_code,
                    entry_price = _ep,
                    exit_price  = float(price),
                    exit_reason = reason,
                )
        except Exception as _le:
            logger.debug(f'[TRADE_LOG] 청산 기록 실패 (무시): {_le}')
        # ─────────────────────────────────────────────────────────────────

        console.print("=" * 80, style="red")
        console.print()

        # 잔고 업데이트 (비동기 실행)
        asyncio.create_task(self.update_account_balance())

    def load_candidates_from_db(self):
        """DB에서 활성 감시 종목 로드"""
        try:
            candidates = self.db.get_active_candidates(limit=100)

            if not candidates:
                console.print("  ⚠️  DB에 활성 감시 종목이 없습니다. 조건검색을 먼저 실행하세요.", style="yellow")
                return

            console.print(f"  ✅ DB에서 {len(candidates)}개 활성 종목 로드", style="green")

            # watchlist 및 validated_stocks 구성
            for candidate in candidates:
                stock_code = candidate['stock_code']
                stock_name = candidate['stock_name']

                self.watchlist.add(stock_code)

                # 🔧 CRITICAL FIX: AI점수 계산 (간소화 버전: win_rate * 1.2)
                # DB에 total_score가 없으면 win_rate 기반으로 계산
                win_rate = candidate.get('vwap_win_rate')
                if win_rate is None:
                    win_rate = 0
                db_total_score = candidate.get('total_score')
                if db_total_score is None:
                    db_total_score = 0
                calculated_score = min(100, float(win_rate) * 1.2)
                final_ai_score = max(float(db_total_score), float(calculated_score))  # DB 값과 계산 값 중 큰 값 사용

                self.validated_stocks[stock_code] = {
                    'name': stock_name,
                    'market': candidate.get('market', 'KOSPI'),  # 시장 정보 추가
                    'stats': {
                        'win_rate': win_rate,
                        'avg_profit_pct': candidate.get('vwap_avg_profit', 0),
                        'total_trades': candidate.get('vwap_trade_count', 0),
                        'profit_factor': candidate.get('vwap_profit_factor', 0)
                    },
                    # 종합 분석 결과 (조건검색 필터에서 추가된 데이터)
                    'analysis': {
                        'total_score': final_ai_score,  # ✅ 간소화 AI점수 사용
                        'recommendation': candidate.get('recommendation', '관망'),
                        'action': candidate.get('action', 'HOLD'),
                        'scores': {
                            'news': candidate.get('score_news', 50),
                            'technical': candidate.get('score_technical', 50),
                            'supply_demand': candidate.get('score_supply_demand', 50),
                            'fundamental': candidate.get('score_fundamental', 50),
                            'vwap': candidate.get('score_vwap', 0)
                        },
                        'news_sentiment': candidate.get('news_sentiment', 'neutral'),
                        'news_impact': candidate.get('news_impact', 0)
                    },
                    'ticker': f"{stock_code}.KS",
                    'db_id': candidate['id']  # DB ID 저장
                }

            console.print(f"  📋 감시 종목: {', '.join([self.validated_stocks[c]['name'] for c in list(self.watchlist)[:5]])}{'...' if len(self.watchlist) > 5 else ''}", style="dim")
            console.print()

        except Exception as e:
            console.print(f"  ❌ DB 로드 실패: {e}", style="red")
            import traceback
            traceback.print_exc()

    def shutdown(self):
        """시스템 종료"""
        self.running = False

        console.print()
        console.print("[yellow]⚠️  종료 신호 감지... 안전하게 종료합니다.[/yellow]")
        console.print()

        # 미청산 포지션 표시
        if self.positions:
            console.print(f"[yellow]⚠️  미청산 포지션: {len(self.positions)}개[/yellow]")

            for code, pos in self.positions.items():
                console.print(f"  - {pos['name']} ({code}): {pos['entry_price']:,.0f}원에 매수")

            console.print()

        console.print("[green]✅ 자동 매매 종료 완료[/green]")
        console.print()

    async def wait_until_time(self, target_hour: int, target_minute: int):
        """특정 시각까지 대기"""
        import sys
        import select

        # stdin 버퍼 비우기 (남아있는 입력 제거)
        try:
            while sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                sys.stdin.readline()
        except Exception:
            pass  # Windows 등에서 select 미지원 시 무시

        # 목표 시간 계산
        now = datetime.now()

        if now.weekday() >= 5:  # 토요일(5), 일요일(6)
            # 다음 월요일 계산
            days_until_monday = 7 - now.weekday()
            next_monday = now + timedelta(days=days_until_monday)
            target_time = next_monday.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
        else:
            # 평일
            target_time = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)

            # 이미 목표 시간을 지났으면 다음날
            if now >= target_time:
                target_time += timedelta(days=1)
                # 금요일이면 다음 월요일로
                if target_time.weekday() >= 5:
                    days_until_monday = 7 - target_time.weekday()
                    target_time += timedelta(days=days_until_monday)

        # 처음 한 번만 목표 시간 출력 (순수 print 사용 - Rich와 충돌 방지)
        print(f"⏰ 목표: {target_time.strftime('%m/%d %H:%M')} ({target_time.strftime('%A')})")
        print(f"💡 [Enter] 키를 누르면 대기를 건너뛰고 즉시 시작합니다.")
        print()  # 한 줄 띄우기
        sys.stdout.flush()  # 🔧 FIX: nohup 환경에서 즉시 출력되도록 flush

        # 대기 루프
        while self.running:
            now = datetime.now()
            time_diff = (target_time - now).total_seconds()

            if time_diff <= 0:
                # 줄바꿈 후 완료 메시지
                print()
                sys.stdout.flush()
                console.print(f"[green]✓ {target_hour:02d}:{target_minute:02d} 도달![/green]")
                break

            # 종료 신호 확인
            if not self.running:
                print()
                sys.stdout.flush()
                console.print("[yellow]⚠️  대기 중 종료 신호 수신[/yellow]")
                break

            hours = int(time_diff // 3600)
            minutes = int((time_diff % 3600) // 60)

            # 같은 줄에서 업데이트 (carriage return 사용)
            sys.stdout.write(f"\r⏰ 대기 중... 남은 시간: {hours:02d}시간 {minutes:02d}분 ([Enter]로 즉시시작)   ")
            sys.stdout.flush()

            # Enter 키 입력 확인 (non-blocking)
            try:
                # 적응형 대기 간격 (남은 시간에 따라 조정)
                if time_diff > 3600:      # 1시간 이상 남음
                    check_interval = 3600  # 1시간 간격 체크
                elif time_diff > 600:     # 10분 이상 남음
                    check_interval = 600   # 10분 간격 체크
                elif time_diff > 60:      # 1분 이상 남음
                    check_interval = 60    # 1분 간격 체크
                else:
                    check_interval = 10    # 마지막 1분은 10초 간격

                # check_interval 동안 1초씩 대기하면서 Enter 키 감지
                for _ in range(int(check_interval)):
                    if not self.running:
                        break

                    # stdin에 데이터가 있는지 확인 (Unix/Linux)
                    if sys.stdin in select.select([sys.stdin], [], [], 0)[0]:
                        line = sys.stdin.readline()
                        if line:  # Enter 키 감지
                            print()
                            sys.stdout.flush()
                            console.print("[cyan]⏩ 대기 건너뛰기 - 즉시 시작합니다.[/cyan]")
                            break  # 대기만 중단, 프로그램은 계속 실행

                    await asyncio.sleep(1)  # 1초 대기
            except Exception:
                # select가 작동하지 않는 환경 (Windows 등)에서는 기본 sleep
                # 적응형 간격 적용
                if time_diff > 3600:
                    await asyncio.sleep(3600)
                elif time_diff > 600:
                    await asyncio.sleep(600)
                elif time_diff > 60:
                    await asyncio.sleep(60)
                else:
                    await asyncio.sleep(10)

    async def daily_routine(self):
        """일일 루틴 실행 (하루에 한 번만)"""
        console.print()
        console.print("=" * 120, style="bold yellow")
        console.print(f"{'📅 일일 자동매매 루틴 시작':^120}", style="bold yellow")
        console.print("=" * 120, style="bold yellow")
        console.print()

        # 1. 08:50까지 대기 (이미 지났으면 바로 시작)
        now = datetime.now()
        target_time = now.replace(hour=8, minute=50, second=0, microsecond=0)

        if self.skip_wait:
            # 테스트 모드: 대기 건너뛰기
            console.print(f"[cyan]⏩ 테스트 모드: 대기 시간 건너뛰기 (즉시 시작)[/cyan]")
            console.print()
        elif now < target_time:
            # 아직 08:50 전이면 대기
            await self.wait_until_time(8, 50)
        else:
            # 이미 08:50 지났으면 바로 시작
            console.print(f"[cyan]⏰ 현재 시간: {now.strftime('%H:%M')} - 바로 필터링 시작합니다.[/cyan]")
            console.print()

        try:
            # 🔧 FIX: DB 로드 제거 (오래된 데이터 사용 방지)
            # 조건검색으로 매일 최신 종목만 사용

            # 2. WebSocket 연결 및 로그인
            console.print("\n[1단계] 시스템 초기화")
            # 🔧 2026-03-08: SYSTEM_START 로그 (운영 환경 추적용)
            _smc_cfg = self.config.get('smc', {})
            _tf_cfg = self.config.get('time_filter', {})
            _risk_cfg = self.config.get('risk_management', {})
            logger.info(
                f"[SYSTEM_START] "
                f"date={datetime.now().strftime('%Y-%m-%d')} "
                f"capital={_risk_cfg.get('initial_capital', 0):,} "
                f"max_positions={self.config.get('risk_management.max_positions', 3)} "
                f"time_window={_tf_cfg.get('smc_start_time','10:30')}~{_tf_cfg.get('smc_afternoon_cutoff','12:30')} "
                f"min_grade={_smc_cfg.get('choch_grade', {}).get('min_grade','B')} "
                f"ob_pullback={_smc_cfg.get('ob_pullback_entry', {}).get('enabled', True)} "
                f"daily_loss_limit={_risk_cfg.get('daily_max_loss_pct', 2.0)}%"
            )
            self._write_heartbeat("init")  # 🔧 2026-02-23: Watchdog
            self.market_context.reset()    # 🔧 2026-02-26: Market Context 일일 리셋
            # 🔧 2026-03-07: Daily Risk Controls 리셋
            self._daily_buy_count = 0
            self._daily_pnl_pct = 0.0
            self._daily_loss_halted = False
            self._db_hard_stop_checked_at = 0.0
            self._db_hard_stop_state = {
                "halted": False,
                "loss_streak": 0,
                "disabled_regimes": [],
                "reasons": [],
            }
            # 🔧 2026-03-08: OB Pullback 대기 상태 일일 리셋 (전일 미발동 OB 제거)
            self.smc_pending = {}
            # 🔧 2026-03-20: Sweep Fallback 일일 카운터 리셋
            self._daily_fallback_count = 0
            self._daily_c_fallback_count = 0
            self._last_c_fallback_time = None
            self._daily_trend_count = 0       # 🔧 2026-03-21: TREND 진입 카운터 리셋
            self._daily_defensive_count = 0   # 🔧 2026-03-31: DEFENSIVE 진입 카운터 리셋
            self._daily_atr_defensive_count = 0       # ATR DEFENSIVE 모드 카운터 리셋
            self._daily_atr_defensive_exposure = 0.0  # DEFENSIVE 누적 비중 리셋
            self._active_pattern_positions.clear()    # 패턴 중복 방지 세트 리셋
            self._trade_cooldown = 0                   # 연패 쿨다운 리셋
            self._pending_exit_value = 0.0             # 청산 대기 가치 리셋
            self._daily_rs_count = 0           # 🔧 2026-04-03: RS 진입 카운터 리셋
            self._daily_sqz_count = 0          # 🔧 2026-04-30: Squeeze Sub 카운터 리셋
            self._sqz_consecutive_losses = 0   # 🔧 2026-04-30: Squeeze 연패 카운터 리셋
            self.sqz_pattern_stats.save("logs/sqz_pattern_stats.json")
            logger.info(self.sqz_pattern_stats.summary())
            self.rs_strategy.reset_daily()     # 🔧 2026-04-03: RS 일봉 캐시 초기화
            self.regime_engine.reset_daily()   # 🔧 2026-04-03: 레짐 캐시 초기화
            self.drawdown_engine.reset_daily() # 🔧 2026-04-03: 드로우다운 일일 리셋
            if hasattr(self, 'online_stats'):  # 🔧 2026-04-24: OnlineStats 일일 저장
                self.online_stats.save()
            self._ema9_blocks.clear()         # 🔥 2026-04-03: EMA9 블록 추적 일일 리셋
            self._daily_short_count = 0        # 🔧 2026-03-31: SHORT 진입 카운터 리셋
            self._daily_exploration_count = 0  # 🔧 2026-03-31: EXPLORATION 진입 카운터 리셋
            self._daily_a_plus_count = 0        # 🔧 2026-04-01: A+ 진입 카운터 리셋
            self._last_a_plus_time = None        # 🔧 2026-04-01: A+ 쿨다운 리셋
            # KPI 트래커: TP1발생률 / 평균보유시간 / MFE비율 / entry분류
            self._kpi = {
                'trades': [],       # {pnl, hold_m, mfe, tp1, exit_reason, entry_reason}
                'blocked_regimes': 0,
                'blocked_position': 0,
                'ev_exits': 0,      # 3봉 조기이탈
                'sd_blocks': 0,     # 수급 차단
            }

            # 🔧 2026-04-02: 장 시작 전 HTS 뉴스/공시 헤드라인 조회
            try:
                _news_cfg = self.config.get('news_feed', {})
                if _news_cfg.get('enabled', True):
                    _news_api = KoreaInvestAPI()
                    _news_count = _news_cfg.get('count', 20)
                    _headlines = _news_api.get_news_titles(count=_news_count)
                    if _headlines:
                        logger.info(f"[NEWS_HEADLINES] 장 시작 전 뉴스 {len(_headlines)}건 수집")
                        for _i, _n in enumerate(_headlines, 1):
                            logger.info(
                                f"[NEWS] {_i:02d}. [{_n['date']} {_n['time'][:4]}] "
                                f"{_n['title']}"
                            )
                        console.print(f"[cyan]📰 장 전 뉴스 헤드라인 {len(_headlines)}건 로그 기록 완료[/cyan]")
                    else:
                        logger.info("[NEWS_HEADLINES] 뉴스 없음 또는 조회 실패")
            except Exception as _e:
                logger.warning(f"[NEWS_HEADLINES] 조회 오류 (무시): {_e}")

            # 🔧 FIX: Token 유효성 사전 검증
            token_valid = await self.validate_token()
            if not token_valid:
                console.print("[yellow]⚠️  Token이 유효하지 않음 - 재발급 시도[/yellow]")
                if not self.refresh_access_token():
                    console.print("[red]❌ Token 재발급 실패. 10분 후 재시도합니다.[/red]")
                    console.print("[yellow]💤 10분 대기 중...[/yellow]")
                    await asyncio.sleep(600)  # 10분 대기

                    # 2차 시도
                    if not self.refresh_access_token():
                        console.print("[red]❌ Token 재발급 2차 실패. 내일 다시 시도합니다.[/red]")
                        return

            # WebSocket 연결
            await self.connect()
            self._write_heartbeat("connecting")  # 🔧 2026-02-23: Watchdog

            # WebSocket 로그인 (최대 3회 재시도, 내부에서 토큰 갱신 포함)
            if not await self.login(max_retries=3):
                console.print()
                console.print("[red]" + "=" * 80 + "[/red]")
                console.print("[red]❌ WebSocket 로그인 최종 실패[/red]")
                console.print("[red]" + "=" * 80 + "[/red]")
                console.print()
                console.print("[yellow]⚠️  가능한 원인:[/yellow]")
                console.print("[yellow]   1. API 서버 일시 장애[/yellow]")
                console.print("[yellow]   2. 네트워크 연결 불안정[/yellow]")
                console.print("[yellow]   3. API 키/시크릿 오류[/yellow]")
                console.print("[yellow]   4. 계정 사용 제한[/yellow]")
                console.print()
                console.print("[cyan]💡 권장 조치:[/cyan]")
                console.print("[cyan]   - API 키/시크릿 재확인[/cyan]")
                console.print("[cyan]   - 키움증권 API 서비스 상태 확인[/cyan]")
                console.print("[cyan]   - 네트워크 연결 확인[/cyan]")
                console.print()
                console.print("[yellow]⏰ 1시간 후 자동 재시도합니다...[/yellow]")
                await asyncio.sleep(3600)  # 1시간 대기

                # 최종 재시도
                console.print("\n[bold cyan]🔄 최종 재시도 중...[/bold cyan]")
                await self.connect()
                if not await self.login(max_retries=2):
                    console.print("[red]❌ 최종 로그인 실패. 내일 다시 시도합니다.[/red]")
                    return

            # 4. 계좌 정보 초기화
            await self.initialize_account()

            # 4. 조건식 목록 조회
            if not await self.get_condition_list():
                console.print("[red]❌ 조건식 조회 실패. 내일 다시 시도합니다.[/red]")
                return

            # 5. 1차 + 2차 필터링 (08:50 ~ 09:00)
            console.print("\n[2단계] 필터링 시작 (08:50)")

            # DEBUG 로그 파일에 기록
            import sys
            with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                f.write(f"\n[{datetime.now()}] 조건검색 실행 전...\n")
                f.flush()
            console.print("[dim]DEBUG: 조건검색 실행 전...[/dim]")
            sys.stdout.flush()

            try:
                await self.run_condition_filtering()
            except Exception as e:
                error_msg = f"조건검색 중 에러: {e}"
                with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now()}] ❌ {error_msg}\n")
                    import traceback
                    f.write(traceback.format_exc())
                    f.flush()
                console.print(f"[red]❌ {error_msg}[/red]")
                import traceback
                traceback.print_exc()
                sys.stdout.flush()
                raise

            with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now()}] 조건검색 완료!\n")
                f.flush()
            console.print("[dim]DEBUG: 조건검색 완료![/dim]")
            sys.stdout.flush()
            self._write_heartbeat("filtering")  # 🔧 2026-02-23: Watchdog

            # ── 점수 기반 종목 선별 (SCORE ENGINE) ───────────────────────────
            # 1) 일봉 SMC 신호 로드
            # 2) 전체 watchlist에 점수 계산 → 상위 5개 선별
            # 3) score < 2 → 버림
            try:
                from backtest.daily_scan   import load_daily_watchlist
                from trading.score_engine  import ScoreEngine
                from trading.trade_logger  import TradeLogger

                daily_smc_symbols = set(load_daily_watchlist())

                # 패턴 인식 레이어: daily_patterns.json 로드 (점수 반영은 별도 플래그)
                _pat_cfg      = self.config.get('pattern_recognition', {})
                _pat_score_on = _pat_cfg.get('score_enabled', False)
                try:
                    from backtest.daily_scan import load_daily_patterns
                    self._daily_patterns = load_daily_patterns()
                    if _pat_cfg.get('enabled', True) and self._daily_patterns:
                        logger.info(
                            f'[PATTERN_LOAD] {len(self._daily_patterns)}개 패턴 로드 '
                            f'(score_enabled={_pat_score_on})'
                        )
                        console.print(
                            f'[magenta]  [PATTERN] {len(self._daily_patterns)}개 패턴 로드 '
                            f'— score_enabled={_pat_score_on}[/magenta]'
                        )
                    else:
                        self._daily_patterns = {}
                except Exception as _pe:
                    logger.debug(f'[PATTERN_LOAD] 실패 (무시): {_pe}')
                    self._daily_patterns = {}

                # ScoreEngine 초기화 (패턴 score_enabled 플래그 전달)
                self._score_engine = ScoreEngine(
                    daily_smc_symbols=daily_smc_symbols,
                    score_pattern=_pat_score_on,
                )
                self._trade_logger = TradeLogger()

                # watchlist 종목별 점수 계산 (패턴 dict 함께 전달)
                ranked = self._score_engine.rank(
                    {sym: None for sym in self.watchlist},
                    patterns=self._daily_patterns,
                )
                selected = self._score_engine.select(ranked)

                # 로그
                logger.info(self._score_engine.log_summary(ranked))
                console.print(
                    f'[cyan]  [SCORE_ENGINE] {len(self.watchlist)}개 → '
                    f'score≥2: {len(selected)}개 선별[/cyan]'
                )
                console.print(f'[cyan]  → {selected}[/cyan]')

                # watchlist를 score 통과 종목으로 교체
                if selected:
                    self.watchlist = set(selected)
                elif daily_smc_symbols:
                    # score 기준 통과 없어도 일봉 신호 종목은 유지 (안전망)
                    self.watchlist = {s for s in self.watchlist if s in daily_smc_symbols}
                # else: daily_smc 없고 selected 없으면 기존 watchlist 유지

            except Exception as _e:
                logger.warning(f'[SCORE_ENGINE] 실패 (기존 watchlist 유지): {_e}')
                self._score_engine = None
                self._trade_logger = None
            # ─────────────────────────────────────────────────────────────────

            # 선정 종목이 없으면 오늘은 종료 (✅ Bottom Pullback 신호도 체크)
            with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now()}] bottom_signals 조회 중...\n")
                f.flush()
            console.print("[dim]DEBUG: bottom_signals 조회 중...[/dim]")
            sys.stdout.flush()

            try:
                bottom_signals = self.bottom_manager.get_signal_watchlist() if hasattr(self, 'bottom_manager') else {}
            except Exception as e:
                error_msg = f"bottom_signals 조회 중 에러: {e}"
                with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                    f.write(f"[{datetime.now()}] ❌ {error_msg}\n")
                    import traceback
                    f.write(traceback.format_exc())
                    f.flush()
                console.print(f"[red]❌ {error_msg}[/red]")
                import traceback
                traceback.print_exc()
                sys.stdout.flush()
                raise

            with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now()}] bottom_signals 완료 ({len(bottom_signals)}개)\n")
                f.flush()
            console.print(f"[dim]DEBUG: bottom_signals 완료 ({len(bottom_signals)}개)[/dim]")
            sys.stdout.flush()

            with open('data/debug_log.txt', 'a', encoding='utf-8') as f:
                f.write(f"[{datetime.now()}] watchlist 체크: {len(self.watchlist)}개\n")
                f.flush()

            if not self.watchlist and not bottom_signals:
                # 🔧 FIX: 장중에는 return하지 않고 빈 watchlist로 모니터링 계속
                now = datetime.now()
                market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)

                if now < market_close:
                    # 아직 장중이면 빈 watchlist로 모니터링 계속 (보유 종목 관리)
                    console.print("[yellow]⚠️  선정된 종목이 없지만, 장중이므로 모니터링 계속합니다.[/yellow]")
                    console.print("[dim]  (보유 종목이 있다면 청산 관리가 진행됩니다)[/dim]")
                else:
                    # 장 마감 후에는 종료
                    console.print("[yellow]⚠️  선정된 종목이 없습니다. 오늘 거래 없음.[/yellow]")
                    return
            elif not self.watchlist and bottom_signals:
                console.print(f"[cyan]ℹ️  Momentum 종목: 0개, Bottom Pullback 신호: {len(bottom_signals)}개[/cyan]")

            # 6. WebSocket 종료 (REST API만 사용)
            console.print("[dim]DEBUG: WebSocket 종료 중...[/dim]")
            if self.websocket:
                await self.websocket.close()
            console.print("[dim]DEBUG: WebSocket 종료 완료[/dim]")

            # 7. 09:00까지 대기 (이미 지났으면 바로 시작)
            now = datetime.now()
            market_open = now.replace(hour=9, minute=0, second=0, microsecond=0)

            if self.skip_wait:
                # 테스트 모드: 대기 건너뛰기
                console.print(f"[cyan]⏩ 테스트 모드: 09:00 대기 건너뛰기[/cyan]")
                console.print()
            elif now < market_open:
                # 아직 09:00 전이면 대기
                await self.wait_until_time(9, 0)
            else:
                # 이미 09:00 지났으면 바로 시작
                console.print(f"[cyan]⏰ 현재 시간: {now.strftime('%H:%M')} - 바로 모니터링 시작합니다.[/cyan]")
                console.print()

            # 🔥 ChatGPT Fix: 갭업 재진입 플래그 리셋 (하루 시작 시)
            console.print(f"[dim]DEBUG: gap_reentered_today 리셋 중 (positions: {len(self.positions)}개)...[/dim]")
            for pos in self.positions.values():
                pos['gap_reentered_today'] = False
            console.print("[dim]DEBUG: gap_reentered_today 리셋 완료![/dim]")

            # ✅ Phase 3: 우선 감시 리스트 로드 및 갭업 재진입 체크
            console.print("\n[2.5단계] 우선 감시 리스트 체크 (갭업 재진입)")
            priority_watchlist = self._load_priority_watchlist()

            # 우선 감시 리스트가 있으면 갭업 재진입 체크 (장 시작 후 30분 이내)
            if priority_watchlist:
                await self.check_gap_reentry_candidates(priority_watchlist)

            # 8. 실시간 모니터링 및 매매 (09:00 ~ 15:30)
            console.print("\n[3단계] 실시간 모니터링 시작")
            await self.monitor_and_trade()

        except Exception as e:
            console.print(f"[red]❌ 루틴 실행 오류: {e}[/red]")
            import traceback
            traceback.print_exc()
        finally:
            # 🔴 자동 거래 분석 실행 (장 종료 시)
            console.print()
            console.print("[bold cyan]{'='*80}[/bold cyan]")
            console.print("[bold cyan]📊 오늘 거래 자동 분석 중...[/bold cyan]")
            console.print("[bold cyan]{'='*80}[/bold cyan]")

            try:
                import subprocess
                result = subprocess.run(
                    ['python3', 'analyze_daily_trades_detailed.py'],
                    capture_output=True,
                    text=True,
                    timeout=30
                )

                # 분석 결과 출력
                if result.stdout:
                    console.print(result.stdout)

                if result.returncode != 0 and result.stderr:
                    console.print(f"[yellow]⚠️  분석 중 경고: {result.stderr}[/yellow]")

            except subprocess.TimeoutExpired:
                console.print("[yellow]⚠️  분석 시간 초과 (30초)[/yellow]")
            except Exception as e:
                console.print(f"[yellow]⚠️  자동 분석 실패: {e}[/yellow]")

            console.print()

            # WebSocket 정리
            if self.websocket:
                await self.websocket.close()

    async def run(self):
        """전체 시스템 실행 (무한 반복)"""
        console.print()
        console.print("=" * 120, style="bold green")
        console.print(f"{'🚀 자동매매 시스템 시작 (스케줄링 모드)':^120}", style="bold green")
        console.print("=" * 120, style="bold green")
        console.print()
        console.print("[cyan]매일 08:50 필터링 → 09:00 모니터링 시작[/cyan]")
        console.print("[dim]Ctrl+C를 눌러 종료할 수 있습니다.[/dim]")
        console.print()

        try:
            while self.running:
                # 일일 루틴 실행 (08:50부터 15:30까지)
                await self.daily_routine()

                # 종료 신호 확인
                if not self.running:
                    break

                # 루틴 종료 후 다음날 08:50까지 대기
                console.print()
                console.print("[green]✅ 오늘 거래 종료[/green]")
                console.print("[cyan]💤 내일 08:50까지 대기합니다...[/cyan]")
                console.print()

                # 다음날 08:50까지 대기
                now = datetime.now()
                tomorrow = now + timedelta(days=1)
                next_run = tomorrow.replace(hour=8, minute=50, second=0, microsecond=0)

                wait_seconds = (next_run - now).total_seconds()
                console.print(f"[dim]다음 실행 시각: {next_run.strftime('%Y-%m-%d %H:%M')} (약 {wait_seconds/3600:.1f}시간 후)[/dim]")

                # 적응형 대기 (남은 시간에 따라 간격 조정)
                while self.running and datetime.now() < next_run:
                    remaining_seconds = (next_run - datetime.now()).total_seconds()
                    if remaining_seconds <= 0:
                        break

                    # 남은 시간에 따라 체크 간격 조정
                    if remaining_seconds > 3600:      # 1시간 이상 남음
                        sleep_interval = 3600         # → 1시간 간격
                    elif remaining_seconds > 600:     # 10분 이상 남음
                        sleep_interval = 600          # → 10분 간격
                    elif remaining_seconds > 60:      # 1분 이상 남음
                        sleep_interval = 60           # → 1분 간격
                    else:
                        sleep_interval = 10           # 마지막 1분은 10초 간격

                    await asyncio.sleep(min(sleep_interval, remaining_seconds))
                    if not self.running:
                        break

        except KeyboardInterrupt:
            console.print()
            console.print("[yellow]⚠️  사용자가 중지했습니다.[/yellow]")
        except Exception as e:
            # 🔧 FIX: Rich markup 에러 방지 - markup=False로 출력
            console.print(f"❌ 시스템 오류: {e}", style="red", markup=False)
            import traceback
            import sys
            traceback.print_exc()
            sys.stderr.flush()  # 🔧 FIX: nohup 환경에서 에러 로그 즉시 출력
        finally:
            if self.websocket:
                await self.websocket.close()


def check_and_create_pid_lock():
    """
    PID lock file로 중복 실행 방지

    Returns:
        True if lock created successfully, False otherwise
    """
    from pathlib import Path
    import os

    pid_file = Path('/tmp/kiwoom_trading.pid')

    # 기존 PID 파일 확인
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            # 프로세스가 실제로 실행 중인지 확인
            os.kill(old_pid, 0)  # 프로세스 존재 확인 (신호 전송 없음)

            # 프로세스가 살아있음
            console.print(f"[red]❌ 이미 실행 중입니다! (PID: {old_pid})[/red]")
            console.print(f"[yellow]기존 프로세스를 종료하려면: kill {old_pid}[/yellow]")
            console.print(f"[yellow]또는: pkill -f 'main_auto_trading.py'[/yellow]")
            return False

        except (ProcessLookupError, ValueError):
            # 프로세스가 죽었거나 PID 파일이 손상됨
            console.print(f"[yellow]⚠️  이전 PID 파일 정리 중...[/yellow]")
            pid_file.unlink()

    # 현재 PID 저장
    current_pid = os.getpid()
    pid_file.write_text(str(current_pid))
    console.print(f"[green]✓ PID lock 생성 완료 (PID: {current_pid})[/green]")

    # 종료 시 PID 파일 삭제
    import atexit
    atexit.register(lambda: pid_file.unlink() if pid_file.exists() else None)

    return True


async def main(skip_wait: bool = False):
    """메인 실행

    Args:
        skip_wait: True면 대기 시간을 건너뛰고 즉시 실행 (테스트 모드)
    """
    import argparse
    import sys
    import traceback

    # Argparse 처리 (커맨드라인 실행 시)
    args = None
    condition_indices = None

    if len(sys.argv) > 1:
        # 커맨드라인 인자가 있으면 argparse 사용
        if True:
            parser = argparse.ArgumentParser(
                description='키움 조건식 자동매매 시스템 (SignalOrchestrator L0-L6 통합)',
                formatter_class=argparse.RawDescriptionHelpFormatter,
                epilog="""
사용 예시:
  # 백테스트 검증 (일부 조건식만 사용)
  python3 main_auto_trading.py --dry-run --conditions 17,18,19

  # 실전 투입 (전체 조건식 사용)
  python3 main_auto_trading.py --live --conditions 17,18,19,20,21,22

  # 테스트 모드 (대기 시간 건너뛰기)
  python3 main_auto_trading.py --skip-wait --conditions 17,18,19
                """
            )
            parser.add_argument('--skip-wait', action='store_true',
                               help='테스트 모드: 대기 시간을 건너뛰고 즉시 실행')
            parser.add_argument('--dry-run', action='store_true',
                               help='백테스트 검증 모드 (실제 매매 없이 시그널만 확인)')
            parser.add_argument('--live', action='store_true',
                               help='실전 투입 모드 (실제 매매 실행)')
            parser.add_argument('--conditions', type=str, default='17,18,19,20,21,22',
                               help='사용할 조건식 인덱스 (쉼표로 구분, 기본값: 17,18,19,20,21,22)')
            args = parser.parse_args()

            # conditions 파싱
            try:
                condition_indices = [int(x.strip()) for x in args.conditions.split(',')]
            except:
                console.print("[red]❌ --conditions 파라미터 오류: 쉼표로 구분된 숫자를 입력하세요 (예: 17,18,19)[/red]")
                return

    # args 객체 생성 (main_menu.py 호출 시)
    if args is None:
        class Args:
            pass
        args = Args()
        args.skip_wait = skip_wait
        args.dry_run = False
        args.live = False
        args.conditions = '17,18,19,20,21,22'

        # conditions 파싱
        try:
            condition_indices = [int(x.strip()) for x in args.conditions.split(',')]
        except:
            console.print("[red]❌ --conditions 파라미터 오류[/red]")
            return

    console.print()
    console.print("=" * 120, style="bold green")
    console.print(f"{'키움 조건식 → VWAP 필터링 → 자동매매 통합 시스템 (L0-L6)':^120}", style="bold green")
    console.print("=" * 120, style="bold green")
    console.print()

    # 모드 표시
    if args.dry_run:
        console.print("[cyan]🔍 백테스트 검증 모드: 실제 매매 없이 시그널만 확인[/cyan]")
        console.print()
    elif args.live:
        console.print("[red]🚀 실전 투입 모드: 실제 매매 실행![/red]")
        console.print()

    # ========== 휴장일 체크 ==========
    is_trading, reason = is_trading_day()
    if not is_trading:
        import time as time_module
        from datetime import datetime as dt, time as time_class

        next_trading = get_next_trading_day()
        next_str = next_trading.strftime('%Y-%m-%d (%a)') if next_trading else 'N/A'

        # 다음 거래일 09:00까지 대기
        if next_trading:
            target_time = dt.combine(next_trading, time_class(9, 0, 0))
        else:
            target_time = None

        console.print()
        console.print("=" * 120, style="bold yellow")
        console.print(f"{'⚠️  시장 휴장일 - 대기 모드':^120}", style="bold yellow")
        console.print("=" * 120, style="bold yellow")
        console.print()
        console.print(f"[red]오늘은 {reason}입니다.[/red]")
        console.print(f"[yellow]다음 거래일: {next_str}[/yellow]")

        if target_time:
            console.print(f"[cyan]다음 거래일 09:00까지 대기합니다... (Ctrl+C로 메인 메뉴)[/cyan]")
        else:
            console.print(f"[cyan]거래일 정보 없음. 1시간마다 재확인합니다... (Ctrl+C로 메인 메뉴)[/cyan]")
        console.print()

        try:
            last_check_time = dt.now()
            check_interval = 3600  # 1시간마다 거래일 체크

            while True:
                now = dt.now()

                # 1시간마다 거래일 재확인
                if (now - last_check_time).total_seconds() >= check_interval:
                    is_trading_now, _ = is_trading_day()

                    if is_trading_now:
                        # 거래일이 되면 루프 종료하고 계속 진행
                        console.print()
                        console.print()
                        console.print("[green]✅ 거래일이 시작되었습니다![/green]")
                        console.print()
                        break

                    last_check_time = now

                # 남은 시간 계산 및 표시 (매초 업데이트)
                if target_time:
                    remaining = target_time - now

                    if remaining.total_seconds() > 0:
                        hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                        minutes, seconds = divmod(remainder, 60)

                        # 다음 체크까지 남은 시간
                        next_check = check_interval - (now - last_check_time).total_seconds()
                        next_check_min = int(next_check // 60)

                        # \r로 줄 처음으로, \033[K로 줄 끝까지 지우고 새로 씀
                        print(f"\r\033[K남은 시간: {hours:02d}:{minutes:02d}:{seconds:02d} | 다음 체크: {next_check_min}분 후", end="", flush=True)
                    else:
                        # 목표 시간이 지났으면 즉시 거래일 체크
                        is_trading_now, _ = is_trading_day()
                        if is_trading_now:
                            print()  # 줄바꿈
                            console.print()
                            console.print("[green]✅ 거래일이 시작되었습니다![/green]")
                            console.print()
                            break
                        print(f"\r\033[K거래일 확인 중...", end="", flush=True)
                else:
                    print(f"\r\033[K1시간마다 거래일 확인 중... (Ctrl+C로 메인 메뉴)", end="", flush=True)

                # 1초 대기 (실시간 카운트다운) - asyncio.sleep 사용하여 KeyboardInterrupt 감지 가능
                await asyncio.sleep(1)

        except (KeyboardInterrupt, asyncio.CancelledError):
            console.print()
            console.print()
            console.print("[yellow]⚠️  대기 모드를 종료하고 메인 메뉴로 돌아갑니다.[/yellow]")
            console.print()
            return

    # 거래일 확인 메시지
    console.print("[green]✅ 거래일 확인 완료[/green]")
    console.print()

    if args.skip_wait:
        console.print("[yellow]⚡ 테스트 모드 활성화: 대기 시간 건너뛰기[/yellow]")
        console.print()

    # 조건식 표시
    console.print(f"[dim]사용 조건식 인덱스: {condition_indices}[/dim]")
    console.print()

    # API 클라이언트 생성
    console.print("[초기화] API 클라이언트 생성")
    api = KiwoomAPI()
    console.print("  ✓ 완료")
    console.print()

    # AccessToken 발급
    console.print("[초기화] AccessToken 발급")
    api.get_access_token()

    if not api.access_token:
        console.print("[red]❌ 토큰 발급 실패[/red]")
        return

    console.print("  ✓ 완료")
    console.print()

    # 통합 시스템 생성 및 실행
    console.print(f"[초기화] 통합 시스템 생성 (조건식 {len(condition_indices)}개)")
    try:
        system = IntegratedTradingSystem(api.access_token, api, condition_indices, skip_wait=args.skip_wait)
        console.print("  ✓ 완료")
        console.print()
    except Exception as e:
        error_msg = f"시스템 초기화 오류: {e}\n{traceback.format_exc()}"
        console.print(f"[red]❌ {error_msg}[/red]")
        # 에러 로그 파일에 저장
        with open('data/error_log.txt', 'a', encoding='utf-8') as f:
            from datetime import datetime
            f.write(f"\n{'='*80}\n")
            f.write(f"[{datetime.now()}] 시스템 초기화 오류\n")
            f.write(f"{'='*80}\n")
            f.write(error_msg)
            f.write(f"\n{'='*80}\n")
        raise

    # dry-run 모드 설정
    if args.dry_run:
        system.dry_run_mode = True
        console.print("[cyan]💡 백테스트 모드: 실제 매매 없이 시그널만 로그로 기록합니다.[/cyan]")
        console.print()

    # Ctrl+C 핸들러 등록 (연속 2번으로 강제 종료)
    ctrl_c_count = 0
    import time as time_module
    last_ctrl_c_time = 0

    def signal_handler(sig, frame):
        nonlocal ctrl_c_count, last_ctrl_c_time
        current_time = time_module.time()

        # 3초 이내 연속 Ctrl+C 체크
        if current_time - last_ctrl_c_time < 3:
            ctrl_c_count += 1
        else:
            ctrl_c_count = 1

        last_ctrl_c_time = current_time

        console.print()
        console.print(f"[yellow]⚠️  종료 신호 수신 ({ctrl_c_count}번)[/yellow]")

        if ctrl_c_count >= 2:
            console.print("[red]🛑 강제 종료합니다...[/red]")
            import sys
            sys.exit(0)
        else:
            console.print("[dim]정상 종료 중... (다시 Ctrl+C를 누르면 강제 종료)[/dim]")
            system.running = False

    signal.signal(signal.SIGINT, signal_handler)

    # 시스템 실행
    try:
        await system.run()
    except Exception as e:
        error_msg = f"시스템 실행 오류: {e}\n{traceback.format_exc()}"
        console.print(f"[red]❌ {error_msg}[/red]")
        # 에러 로그 파일에 저장
        with open('data/error_log.txt', 'a', encoding='utf-8') as f:
            from datetime import datetime
            f.write(f"\n{'='*80}\n")
            f.write(f"[{datetime.now()}] 시스템 실행 오류\n")
            f.write(f"{'='*80}\n")
            f.write(error_msg)
            f.write(f"\n{'='*80}\n")
        raise


if __name__ == "__main__":
    # 🔧 중복 프로세스 방지
    if not check_and_create_pid_lock():
        import sys
        sys.exit(1)

    # 직접 실행 시 argparse가 처리하므로 skip_wait=False로 시작
    asyncio.run(main(skip_wait=False))
