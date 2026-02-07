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
from market_utils import is_trading_day, get_next_trading_day  # ✅ 휴장일 체크
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
            console.print(f"[dim]✓ {ticker_ks} 데이터 로드 성공 ({len(df)}개 봉)[/dim]")
            return df
    except Exception as e:
        console.print(f"[dim]{ticker_ks} 실패: {e}[/dim]")

    # .KQ 시도
    if try_kq:
        ticker_kq = f"{ticker}.KQ"
        try:
            df = await asyncio.to_thread(download_stock_data_sync, ticker_kq, days)
            if df is not None and not df.empty:
                console.print(f"[dim]✓ {ticker_kq} 데이터 로드 성공 ({len(df)}개 봉)[/dim]")
                return df
        except Exception as e:
            console.print(f"[dim]{ticker_kq} 실패: {e}[/dim]")

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
            console.print(f"  [dim]✓ 야후: {yahoo_bars}개 봉 수집[/dim]")

            # 키움 데이터와 Yahoo 데이터 병합
            if df is not None and not df.empty:
                # 기존 키움 데이터에 Yahoo 데이터를 앞에 추가
                df = pd.concat([yahoo_df, df], ignore_index=True)
                df = df.drop_duplicates(subset=['datetime', 'time'], keep='last').reset_index(drop=True)
                console.print(f"  [green]✓ 병합 완료: 키움({current_bars}) + 야후({yahoo_bars}) = 총 {len(df)}개 봉[/green]")
            else:
                df = yahoo_df
                console.print(f"  [cyan]✓ 야후 데이터만 사용: {len(df)}개 봉[/cyan]")

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
            reclaim_tolerance_pct=prefilter_config.get('reclaim_tolerance_pct', 0.3)
        )
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
        self.stock_info_cache: Dict[str, Dict] = {}  # {stock_code: {info, timestamp}}
        self.cache_expiry_seconds = 300  # 5분 캐시
        self.last_api_call_time = 0  # 마지막 API 호출 시각
        self.api_call_delay = 0.2  # API 호출 간 최소 딜레이 (초)

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

        # 🔧 2026-02-07: Re-entry Cooldown 운영 통계 + 차등화 v2
        from metrics.reentry_metrics import ReentryMetrics, categorize_exit_reason
        self.reentry_metrics = ReentryMetrics()
        self._categorize_exit_reason = categorize_exit_reason

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

        # 캐시 확인
        now = time.time()
        if stock_code in self.stock_info_cache:
            cached = self.stock_info_cache[stock_code]
            if now - cached['timestamp'] < self.cache_expiry_seconds:
                console.print(f"[dim]  💾 {stock_code} 캐시 사용[/dim]")
                return cached['info']

        # API 호출 딜레이 적용
        time_since_last_call = now - self.last_api_call_time
        if time_since_last_call < self.api_call_delay:
            sleep_time = self.api_call_delay - time_since_last_call
            console.print(f"[dim]  ⏳ API Rate Limit 방지 대기: {sleep_time:.2f}초[/dim]")
            time.sleep(sleep_time)

        # API 호출
        try:
            result = self.api.get_stock_info(stock_code=stock_code)
            self.last_api_call_time = time.time()

            if result and result.get('return_code') == 0:
                # 캐시 저장
                self.stock_info_cache[stock_code] = {
                    'info': result,
                    'timestamp': now
                }
                return result
            else:
                return None

        except Exception as e:
            console.print(f"[dim]  ⚠️  {stock_code} 정보 조회 실패: {e}[/dim]")
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
                    "ai_score": info.get('ai_score', 0),
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

            # 5. 리스크 관리자 잔고 업데이트
            if self.risk_manager:
                self.risk_manager.update_balance(self.current_cash)

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

            # 전체 응답 구조 확인 (디버깅)
            console.print(f"[dim]  응답 키: {list(response.keys())}[/dim]")
            console.print(f"[dim]  전체 응답: {response}[/dim]")
            console.print(f"[dim]  응답: {elapsed:.2f}초, return_code={return_code}, data 타입={type(data)}, data 길이={len(data) if data else 0}[/dim]")

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

            # 종목명 조회를 포함한 candidates 리스트 생성
            candidates = []
            for stock_code in all_stocks:
                try:
                    stock_name = stock_code  # 기본값
                    market = 'KOSPI'  # 기본값

                    # 종목명 조회 (캐시 사용)
                    try:
                        result = self._get_stock_info_with_cache(stock_code)
                        if result:
                            stock_name = self._extract_stock_name(result, stock_code)
                            # 시장 구분 (간단 로직: 코드로 판단)
                            market = 'KOSDAQ' if stock_code.startswith(('3', '4', '5', '6', '7')) else 'KOSPI'
                    except Exception:
                        pass

                    candidates.append({
                        'stock_code': stock_code,
                        'stock_name': stock_name,
                        'market': market
                    })
                except Exception:
                    continue

            console.print(f"[dim]RS 필터링 대상: {len(candidates)}개 종목[/dim]")

            # RS 필터링
            filtered_candidates = self.signal_orchestrator.check_l2_rs_filter(
                candidates,
                market='KOSPI'  # 기본 시장 (개별 종목은 candidates에 market 포함)
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
                analysis_engine = AnalysisEngine()

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

                        # 종목 기본 정보 조회 (캐시 사용)
                        basic_info = None
                        try:
                            result = self._get_stock_info_with_cache(stock_code)
                            if result:
                                # 키움 API ka10001은 데이터를 최상위에 직접 반환
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
                                # ka10059 API는 'stk_invsr_orgn' 키에 LIST 반환
                                investor_data = result.get("stk_invsr_orgn", [])
                                console.print(f"  [dim]✓ 투자자 동향 {len(investor_data) if investor_data else 0}개 수집[/dim]")
                        except Exception as e:
                            console.print(f"  [dim]⚠️  투자자 동향 조회 실패: {e}[/dim]")

                        # 종합 분석 실행
                        console.print(f"  [dim]🔍 AI 종합 분석 실행 중...[/dim]")
                        analysis_result = analysis_engine.analyze(
                            stock_code=stock_code,
                            stock_name=stock_name,
                            chart_data=chart_data,
                            investor_data=investor_data,
                            program_data=None,  # 프로그램 매매는 시장 전체 데이터
                            stock_info=basic_info
                        )

                        # 분석 결과 저장
                        stock_info['analysis'] = analysis_result

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
                    console.print(f"[dim]  DEBUG: {code} API응답 키={sample_keys}[/dim]")

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
        rescan_interval = 300  # 5분마다 조건검색 재실행
        kis_interval = 300  # 5분마다 한투 중기 체크

        last_check = datetime.now()
        last_rescan = datetime.now()
        last_sync = datetime.now()  # 거래 내역 동기화 마지막 시간
        last_status_update = datetime.now()
        last_kis_check = datetime.now() - timedelta(seconds=kis_interval)  # 즉시 첫 체크
        eod_executed = False  # ✅ EOD 프로세스 실행 여부 플래그

        # ✅ 초기 한투 포지션 조회 및 체크
        self.run_kis_pre_market_check()
        self.fetch_kis_positions()
        self.display_kis_positions()

        try:
            while self.running:
                current_time = datetime.now()

                # 🔧 CRITICAL FIX: 15:30 이후 자동 종료 (장마감 후 불필요한 활동 방지)
                shutdown_time = current_time.replace(hour=15, minute=30, second=0, microsecond=0)
                if current_time >= shutdown_time:
                    console.print()
                    console.print("[yellow]=" * 80 + "[/yellow]")
                    console.print(f"[bold yellow]🕐 15:30 장 종료 - 오늘 모니터링 종료[/bold yellow]")
                    console.print("[yellow]=" * 80 + "[/yellow]")

                    # 🔧 2026-02-07: Re-entry Cooldown 리포트 출력 + 저장
                    self.reentry_metrics.print_report()
                    self.reentry_metrics.save_daily()

                    # ✅ 한투 장 마감 후 체크리스트
                    self.run_kis_post_market_check()

                    console.print()
                    console.print(f"[cyan]✅ 오늘 거래 완료 ({current_time.strftime('%Y-%m-%d %H:%M:%S')})[/cyan]")
                    console.print(f"[dim cyan]💤 내일 08:50에 자동으로 다시 시작됩니다.[/dim cyan]")
                    console.print()
                    break  # 모니터링 루프만 종료 (run() 루프는 계속)

                # 장 시간인지 체크
                if self.is_market_open():
                    # ✅ EOD 프로세스 체크 (14:55-14:59 사이에 1회 실행)
                    if not eod_executed and current_time.hour == 14 and 55 <= current_time.minute <= 59:
                        await self.handle_eod()
                        eod_executed = True

                    # 5분마다 조건검색 재실행
                    if (current_time - last_rescan).seconds >= rescan_interval:
                        console.print()
                        console.print("[cyan]🔄 5분 경과 - 조건검색 재실행 중...[/cyan]")
                        await self.rescan_and_add_stocks()
                        last_rescan = current_time
                        console.print(f"[green]✅ 현재 모니터링 종목: {len(self.watchlist)}개[/green]")
                        console.print()

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

        # 에러 로그를 파일에 저장
        error_logger = logging.getLogger('error_logger')
        if not error_logger.handlers:
            fh = logging.FileHandler('/home/greatbps/projects/kiwoom_trading/logs/auto_trading_errors.log')
            fh.setLevel(logging.ERROR)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
            fh.setFormatter(formatter)
            error_logger.addHandler(fh)
            error_logger.setLevel(logging.ERROR)

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
                                            console.print(f"[dim]  ✓ {stock_code}: 실시간 현재가 {realtime_price:,.0f}원[/dim]")
                                            break
                    except Exception as e:
                        # API 실패는 정상 동작 (5분봉 데이터 사용)
                        pass

                # 장중(9:00~15:30)에만 5분봉 키움 API 호출
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
                            for key in ['stk_min_pole_chart_qry', 'stk_mnut_pole_chart_qry', 'output', 'output1', 'output2', 'data']:
                                if key in result and result[key]:
                                    data = result[key]
                                    break

                            if data and len(data) > 0:
                                import pandas as pd
                                df = pd.DataFrame(data)

                                # 디버깅: 실제 컬럼 출력
                                console.print(f"[dim]  키움 API 컬럼: {list(df.columns)}[/dim]")

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
                                    console.print(f"[yellow]  ⚠️  {stock_code}: volume 컬럼 없음, 기본값 사용[/yellow]")
                                    df['volume'] = 1000  # 기본 거래량

                                # 시간 정렬: cntr_tm으로 정렬 (최신 데이터가 마지막에 오도록)
                                if 'cntr_tm' in df.columns:
                                    df['cntr_tm'] = pd.to_numeric(df['cntr_tm'], errors='coerce')
                                    df = df.sort_values('cntr_tm', ascending=True).reset_index(drop=True)
                                    console.print(f"[dim]  ✓ {stock_code}: 키움 {len(df)}개 봉 (시간 정렬 완료, 최신: {df['cntr_tm'].iloc[-1]})[/dim]")
                                else:
                                    console.print(f"[dim]  ✓ {stock_code}: 키움 {len(df)}개 봉[/dim]")

                                kiwoom_bars = len(df)
                    except Exception as e:
                        console.print(f"[dim]  ⚠️  {stock_code}: 키움 API 오류 - {e}[/dim]")

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

                    yahoo_df = download_stock_data_sync(ticker, days=days_needed)

                    if yahoo_df is not None and len(yahoo_df) > 0:
                        if df is not None:
                            # 키움 + 야후 결합
                            df = pd.concat([yahoo_df, df], ignore_index=True).drop_duplicates()
                            console.print(f"[dim]  ✓ {stock_code}: 키움 {kiwoom_bars}개 + 야후 {len(yahoo_df)}개 = 총 {len(df)}개[/dim]")
                        else:
                            df = yahoo_df
                            console.print(f"[dim]  ✓ {stock_code}: 야후 {len(df)}개 봉[/dim]")

                if df is None or len(df) < 20:
                    # 데이터 조회 실패 시 fallback: DB 정보로 기본 표시
                    console.print(f"[yellow]⚠️  {stock_code}: 실시간 데이터 없음, DB 정보로 표시[/yellow]")

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
                            console.print(f"[dim yellow]⚠️  {stock_code}: get_stock_quote() 반환 None[/dim yellow]")
                        elif orderbook_data.get('return_code') != 0:
                            console.print(f"[dim yellow]⚠️  {stock_code}: API return_code={orderbook_data.get('return_code')}[/dim yellow]")

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
                        console.print(f"[dim red]⚠️  {stock_code} 호가창 계산 오류: {e}[/dim red]")
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
                if stock_code in self.positions:
                    self.check_exit_signal(stock_code, df)  # historical_df 전달
                else:
                    # 디버그: check_entry_signal 호출 전 로그
                    if orchestrator_status == "✅통과":
                        console.print(f"[cyan]→ {stock_code} ({stock_name}): ✅통과 확인 → check_entry_signal 호출[/cyan]")
                    await self.check_entry_signal(stock_code, df)  # 키움 데이터 전달 (async)

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

        if len(self.positions) > 0:
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

                if today_trades:
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
        # 4. 실시간 모니터링 테이블 (매수 조건)
        # ========================================
        # 보유 종목 개수 확인
        holding_count = sum(1 for data in stock_data if data.get('holding'))
        monitoring_count = len(stock_data) - holding_count

        table_title = f"📊 실시간 모니터링 ({current_time})"
        if holding_count > 0:
            table_title += f" | [bold green]보유종목 {holding_count}개[/bold green]"
        if monitoring_count > 0:
            table_title += f" | 모니터링 {monitoring_count}개"

        table = Table(title=table_title, box=box.ROUNDED, show_header=True, header_style="bold magenta")
        table.add_column("순번", style="cyan", justify="right", width=4)
        table.add_column("코드", style="yellow", width=8)
        table.add_column("종목명", style="white", width=14)
        table.add_column("보유", justify="center", width=6)
        table.add_column("현재가", justify="right", width=9)
        table.add_column("VWAP", justify="right", width=9)
        table.add_column("MA20", justify="right", width=9)
        table.add_column("거래량", justify="right", width=8)
        table.add_column("기술", justify="center", width=8)  # 기술적 조건
        table.add_column("스퀴즈", justify="center", width=8)  # 스퀴즈 모멘텀
        table.add_column("호가창", justify="center", width=8)  # 호가창 상태
        table.add_column("필터상태", justify="center", width=9)  # L0-L6 상태
        table.add_column("차단이유", style="dim", width=20)  # 상세 이유
        table.add_column("시간", style="dim", width=8)

        for i, data in enumerate(stock_data, 1):
            # VWAP 조건 색상
            vwap_str = f"{data['vwap']:,.0f}"
            if data['vwap_ok']:
                vwap_str = f"[green]{vwap_str} ✓[/green]"
            else:
                vwap_str = f"[red]{vwap_str} ✗[/red]"

            # MA20 조건 색상
            ma20_str = f"{data['ma20']:,.0f}"
            if data['ma20_ok']:
                ma20_str = f"[green]{ma20_str} ✓[/green]"
            else:
                ma20_str = f"[red]{ma20_str} ✗[/red]"

            # 거래량 증감 색상
            vol_change_str = f"{data['volume_change_pct']:+.1f}%"
            if data['volume_ok']:
                vol_change_str = f"[green]{vol_change_str} ✓[/green]"
            else:
                vol_change_str = f"[red]{vol_change_str} ✗[/red]"

            # 보유 종목일 때 종목명 강조
            stock_name = data['name']
            if data['holding']:
                stock_name = f"[bold green]{stock_name}[/bold green]"

            # 기술적 조건 (간단히)
            tech_str = f"{data['conditions_met']}/3"
            if data['conditions_met'] == 3:
                tech_str = f"[green]{tech_str}[/green]"
            elif data['conditions_met'] >= 2:
                tech_str = f"[yellow]{tech_str}[/yellow]"
            else:
                tech_str = f"[red]{tech_str}[/red]"

            # 필터 상태
            filter_status = data.get('orchestrator_status', '')
            if filter_status == "✅통과":
                filter_str = f"[green]{filter_status}[/green]"
            elif filter_status:
                filter_str = f"[red]{filter_status}[/red]"
            else:
                filter_str = "[dim]-[/dim]"

            # 스퀴즈 모멘텀 상태
            squeeze_str = data.get('squeeze_display', '[dim]-[/dim]')

            # 호가창 상태
            orderbook_str = data.get('orderbook_display', '[dim]-[/dim]')

            # 차단 이유
            rejection = data.get('rejection_info', '')

            table.add_row(
                str(i),
                data['code'],
                stock_name,  # 보유 종목은 강조
                data['holding'],  # 보유 여부 추가
                f"{data['price']:,.0f}",
                vwap_str,
                ma20_str,
                vol_change_str,
                tech_str,
                squeeze_str,  # 스퀴즈 추가
                orderbook_str,  # 호가창 추가
                filter_str,
                rejection,
                data['time']
            )

        # 실시간 모니터링 테이블 출력
        console.print(table)

        # 스퀴즈 모멘텀 범례 (enabled일 때만 표시)
        squeeze_config = self.config.get('squeeze_momentum', {})
        if squeeze_config.get('enabled', False):
            console.print("[dim]스퀴즈: [bold green]🟢BG[/bold green]=Bright Green(진입/보유) | [yellow]🟡DG[/yellow]=Dark Green(부분익절) | [red]🔴DR[/red]=Dark Red(청산) | [bold red]🟠BR[/bold red]=Bright Red(청산)[/dim]")

            # 호가창 범례 (squeeze_with_orderbook 모드일 때만 표시)
            entry_mode = squeeze_config.get('entry_mode', 'squeeze_only')
            if entry_mode == "squeeze_with_orderbook":
                console.print("[dim]호가창: [green]✓N/6[/green]=N개 조건 통과 | [red]✗N/6[/red]=N개만 통과 (차단) | 조건: ①Squeeze OFF, ②거래량1.05배, ③VWAP상단, ④매도호가감소, ⑤체결강도80%, ⑥가격정체[/dim]")

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

        # 🔧 명확한 설명: 혼동 방지
        console.print("=" * 120)
        console.print("[bold yellow]💡 컬럼 설명 (중요!)[/bold yellow]")
        console.print("=" * 120)
        console.print("[cyan]기술[/cyan]:     기술적 조건 통과 개수 (VWAP/MA20/거래량 중 몇 개 만족)")
        console.print("[magenta]스퀴즈[/magenta]:   Squeeze Momentum 상태 (🟢BG=진입/보유, 🟡DG=부분익절, 🔴DR/🟠BR=청산)")
        console.print("[yellow]필터상태[/yellow]: SignalOrchestrator L0-L6 필터 통과 여부")
        console.print("          • [green]✅통과[/green]: 모든 필터 통과 → 매수 대기 중")
        console.print("          • [red]L0❌/L3❌/ALPHA❌[/red]: 해당 필터에서 차단")
        console.print("[dim]차단이유[/dim]: 왜 매수 못하는지 상세 이유")
        console.print("[green]🔵 보유[/green]:   실제 매수 완료된 종목")
        console.print()
        console.print("[dim]※ 기술 3/3이어도 필터상태가 'L0❌'이면 매수 안 됨 (예: 장 마감 임박)[/dim]")
        console.print("[dim]다음 체크: 60초 후 | Ctrl+C: 종료[/dim]")
        console.print("=" * 120)

    async def check_entry_signal(self, stock_code: str, kiwoom_df: pd.DataFrame = None):
        """매수 신호 체크 (SignalOrchestrator 사용 - L0~L6 통합)"""
        try:
            # 진입 모드 확인 (시간 필터 조건부 적용)
            squeeze_config = self.config.get('squeeze_momentum', {})
            entry_mode = squeeze_config.get('entry_mode', 'squeeze_only')

            # 🔧 FIX: 모든 모드에서 14:59 이후 진입 차단 (15:00 강제 청산과 충돌 방지)
            from datetime import time as time_class
            current_time = datetime.now().time()
            LATE_ENTRY_CUTOFF = time_class(14, 59, 0)
            MORNING_CUTOFF = time_class(12, 0, 0)   # 🔧 FIX: 오전장 마감 12:00
            LUNCH_START = time_class(12, 0, 0)      # 🔧 NEW: 점심시간 시작 (11:30→12:00 완화)
            LUNCH_END = time_class(13, 0, 0)        # 🔧 NEW: 점심시간 종료
            GOLDEN_TIME_START = time_class(10, 0, 0)   # 🔧 NEW: 골든타임 시작
            GOLDEN_TIME_END = time_class(10, 30, 0)    # 🔧 NEW: 골든타임 종료

            if current_time > LATE_ENTRY_CUTOFF:
                console.print(f"[yellow]⏰ {stock_code}: 14:59 이후 신규 진입 차단 ({current_time.strftime('%H:%M:%S')})[/yellow]")
                return

            # 🔧 NEW: squeeze_2tf 모드는 점심시간(12:00~13:00) 진입 차단 (제이엔비 손실 분석 반영)
            if entry_mode == "squeeze_2tf":
                if LUNCH_START <= current_time <= LUNCH_END:
                    console.print(f"[yellow]⏰ {stock_code}: 점심시간 진입 차단 - 12:00~13:00 변동성 낮음 ({current_time.strftime('%H:%M:%S')})[/yellow]")
                    return

            # 🔧 NEW: 골든타임 여부 체크 (10:00~10:30) - 나중에 신뢰도 가중치로 사용
            is_golden_time = GOLDEN_TIME_START <= current_time <= GOLDEN_TIME_END

            # ma_cross, squeeze_2tf 모드는 점심시간 등 다른 시간 제한 없음
            if entry_mode not in ["ma_cross", "squeeze_2tf"]:
                # 🔧 FIX: 문서 기반의 안전 장치로, 모든 진입 평가 전 시간 필터 강제 적용
                time_ok, time_reason = self._is_valid_entry_time()
                if not time_ok:
                    # 장 시간이 아니면 조용히 종료 (로그 최소화)
                    console.print(f"[yellow]⏰ {stock_code}: {time_reason}[/yellow]")
                    return

            console.print(f"[green]🔍 {stock_code}: 매수 신호 체크 시작[/green]")

            stock_info = self.validated_stocks.get(stock_code)
            if not stock_info:
                return

            stock_name = stock_info.get('name', stock_code)
            market = stock_info.get('market', 'KOSPI')
            strategy_tag = stock_info.get('strategy', self.default_strategy_tag)  # ✅ 동적 기본값

            # ✅ TradeStateManager 진입 가능 여부 체크
            can_enter, reason = self.state_manager.can_enter(
                stock_code=stock_code,
                strategy_tag=strategy_tag,
                check_stoploss=True,
                check_invalidated=True,
                check_traded=True
            )

            if not can_enter:
                console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): {reason}[/yellow]")
                return

            # 1. 데이터 조회 (키움 우선, Yahoo Finance 폴백)
            if kiwoom_df is not None and len(kiwoom_df) >= 50:
                df = kiwoom_df.copy()
            else:
                # Yahoo Finance fallback
                ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
                ticker = f"{stock_code}{ticker_suffix}"
                df = download_stock_data_sync(ticker, days=1)

                if df is None or len(df) < 50:
                    # 반대 시장 시도
                    ticker_alt = f"{stock_code}.KQ" if market == 'KOSPI' else f"{stock_code}.KS"
                    df = download_stock_data_sync(ticker_alt, days=1)

                if df is None or len(df) < 50:
                    console.print(f"[yellow]⚠️  {stock_code}: 데이터 부족[/yellow]")
                    # 🔧 FIX: 데이터 품질 실패 처리 (문서 명세)
                    self._handle_data_quality_failure(
                        stock_code,
                        stock_name,
                        f"데이터 부족 (df={len(df) if df is not None else 0}봉 < 50봉)"
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
                    console.print(f"[yellow]⚠️  {stock_code}: {len(invalid_rows)}개 비정상 가격 제거[/yellow]")
                    df = df[df['close'] > 0].copy()

                if len(df) < 50:
                    console.print(f"[yellow]⚠️  {stock_code}: 필터링 후 데이터 부족[/yellow]")
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
                    console.print(f"[yellow]⚠️  {stock_code}: 변동성 과다 (ATR {atr_pct:.2f}% > 5%)[/yellow]")
                    return

            signal_config = self.config.get_signal_generation_config()
            df = self.analyzer.generate_signals(df, **signal_config)

            current_price = df['close'].iloc[-1]

            # 🚨 음수 가격 최종 검증
            if current_price <= 0:
                console.print(f"[red]❌ {stock_code}: 비정상 현재가 {current_price}[/red]")
                return

            # 2. 진입 조건 모드 확인
            squeeze_config = self.config.get('squeeze_momentum', {})
            entry_mode = squeeze_config.get('entry_mode', 'squeeze_only')  # 기본값: squeeze_only

            # 진입 이유 초기화 (각 모드에서 설정)
            entry_reason = None

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
                    console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): Squeeze 차단 - {sqz_reason}[/yellow]")
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
                            console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): {reason}[/yellow]")

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
                        console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): {reason}[/yellow]")
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
                        console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): {reason}[/yellow]")
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
                console.print(f"[cyan]📊 진입 모드: SMC (Smart Money Concepts)[/cyan]")

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

                    console.print(f"[dim]  ✓ 5분봉: {len(df_5min)}개[/dim]")

                    # 데이터 충분성 체크
                    if len(df_5min) < 50:
                        console.print(f"[yellow]⚠️  {stock_name}: 5분봉 데이터 부족 ({len(df_5min)}개 < 50개)[/yellow]")
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
                                console.print(f"[dim]  ✓ 30분봉: {len(df_30min)}개 (MTF Bias 필터용)[/dim]")
                            else:
                                console.print(f"[yellow]⚠️  30분봉 부족 ({len(df_30min)}개) - MTF Bias 비활성[/yellow]")
                                df_30min = None
                        except Exception as e:
                            console.print(f"[dim]⚠️  30분봉 생성 실패: {e} - MTF Bias 비활성[/dim]")
                            df_30min = None

                    # ✅ BB(30,1) 관측 (진입 X, 로깅만) - 5분봉 실데이터 검증
                    try:
                        from utils.squeeze_momentum import calculate_squeeze_momentum
                        df_5min_sqz = calculate_squeeze_momentum(df_5min.copy())
                        self.bb30_observer.observe(
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
                        df_htf=df_30min  # 30분봉 데이터 (MTF Bias 필터용)
                    )

                    if not signal:
                        console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): {reason}[/yellow]")
                        return
                    else:
                        console.print(f"[green]✅ {stock_name}: {reason}[/green]")

                    # 방향 확인 (롱온리 전략의 경우)
                    direction = details.get('direction', 'none')
                    if direction != 'long':
                        console.print(f"[yellow]⚠️  {stock_name}: 숏 신호 무시 (롱온리)[/yellow]")
                        return

                    # 🔧 2026-01-23: 신뢰도 + CHoCH 등급 기반 포지션 크기
                    entry_confidence = details.get('confidence', 0.7)
                    weight_multiplier = details.get('weight_multiplier', 1.0)  # 등급별 비중

                    # 신뢰도 기반 배율
                    confidence_mult = 0.8 if entry_confidence < 0.8 else 1.0

                    # 최종 포지션 크기 = 신뢰도 배율 × 등급 배율
                    position_size_mult = confidence_mult * weight_multiplier

                    # 등급 정보 로깅
                    choch_grade_info = details.get('choch_grade', {})
                    choch_grade = choch_grade_info.get('grade', 'B')
                    if weight_multiplier < 1.0:
                        console.print(f"[yellow]📊 CHoCH {choch_grade}급: 비중 {weight_multiplier*100:.0f}% 적용[/yellow]")

                    # 🔧 2026-02-06: 구조 기반 손절가 저장
                    structure_stop_price = details.get('structure_stop_price')
                    if structure_stop_price:
                        console.print(f"[cyan]📍 구조 손절가: {structure_stop_price:,.0f}원[/cyan]")

                    # 진입 이유 생성
                    choch_info = details.get('choch', {})
                    entry_reason = f"{datetime.now().strftime('%H:%M')} SMC {reason}"

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
                    console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): {level} 차단 - {reason}[/yellow]")
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
                    console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): {level} 차단 - {reason}[/yellow]")
                    return

                # 추가로 스퀴즈 모멘텀 체크
                if squeeze_config.get('enabled', False) and squeeze_config.get('entry_filter', {}).get('enabled', False):
                    from utils.squeeze_momentum_realtime import check_squeeze_momentum_filter

                    sqz_passed, sqz_reason, sqz_details = check_squeeze_momentum_filter(df, for_entry=True)

                    if not sqz_passed:
                        console.print(f"[yellow]⚠️  {stock_name} ({stock_code}): Squeeze 차단 - {sqz_reason}[/yellow]")
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

            # execute_buy 호출 (포지션 사이즈 + 진입 신뢰도 + 진입 이유 반영)
            self.execute_buy(stock_code, stock_name, current_price, df, position_size_mult, entry_confidence, entry_reason)

            # 🔧 2026-02-06: SMC 진입 시 추가 정보를 포지션에 저장
            if entry_mode == "smc" and stock_code in self.positions:
                try:
                    # 구조 기반 손절가 저장 (structure_stop_price는 SMC 분기에서 details로부터 추출됨)
                    if structure_stop_price is not None:
                        self.positions[stock_code]['structure_stop_price'] = structure_stop_price
                        console.print(f"[cyan]📍 {stock_name}: 구조 손절가 {structure_stop_price:,.0f}원 저장[/cyan]")

                    # HTF 추세 일치 여부 저장 (조건부 보유 시간 연장용)
                    mtf_bias_info = details.get('mtf_bias', {})
                    self.positions[stock_code]['htf_trend_aligned'] = mtf_bias_info.get('is_uptrend', False)
                    self.positions[stock_code]['direction'] = 'long'

                    # 🔧 2026-02-07: 진입 시 ATR 저장 (Early Failure Structure 필터용)
                    if 'atr' in df_5min.columns and len(df_5min) > 0:
                        self.positions[stock_code]['atr_at_entry'] = float(df_5min['atr'].iloc[-1])
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
            console.print(f"[dim]🔍 {stock_code}: 매도 신호 체크 시작[/dim]")

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
            if kiwoom_df is not None and len(kiwoom_df) >= 50:
                console.print(f"[dim]  ✓ {stock_code}: 키움 데이터 사용 ({len(kiwoom_df)}봉)[/dim]")
                df = kiwoom_df.copy()
            else:
                # 2순위: Yahoo Finance에서 보충
                market = None
                if stock_code in self.validated_stocks:
                    market = self.validated_stocks[stock_code].get('market')

                if not market:
                    market = 'KOSPI' if stock_code.startswith('0') else 'KOSDAQ'

                ticker_suffix = '.KS' if market == 'KOSPI' else '.KQ'
                ticker = f"{stock_code}{ticker_suffix}"

                console.print(f"[dim]  📊 {stock_code}: Yahoo 데이터 조회 중 ({ticker})...[/dim]")
                df = download_stock_data_sync(ticker, days=1)

                if df is None or len(df) < 50:
                    console.print(f"[yellow]⚠️  {stock_code}: 데이터 부족 (df={len(df) if df is not None else 0}봉)[/yellow]")
                    # 🔧 FIX: 데이터 품질 실패 처리 (문서 명세)
                    stock_name = position.get('name', stock_code)
                    self._handle_data_quality_failure(
                        stock_code,
                        stock_name,
                        f"청산 체크 시 데이터 부족 (df={len(df) if df is not None else 0}봉 < 50봉)"
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

            console.print(f"[dim]  💰 {stock_code}: 현재가 {current_price:,.0f}원, 진입가 {position['entry_price']:,.0f}원, 수익률 {profit_pct:+.2f}%{trailing_status}[/dim]")

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
        ENTRY_START = time_class(10, 0, 0)  # 10시 이후 매수 (장초반 가격 불안정)
        # ENTRY_END = time_class(14, 59, 0)  # ❌ 비활성화: 시간 제한 없음

        # 🔴 GPT 개선: 점심시간 완전 차단 (재진입 포함)
        MIDDAY_START = time_class(12, 0, 0)
        MIDDAY_END = time_class(14, 0, 0)

        if t < ENTRY_START:
            return False, f"❌ 10:00 이전 진입 차단 ({t.strftime('%H:%M:%S')})"

        # ❌ 14:59 진입 차단 비활성화
        # if t > ENTRY_END:
        #     return False, f"❌ 14:59 이후 진입 차단 ({t.strftime('%H:%M:%S')})"

        # ✅ 스퀴즈 모멘텀 모드: 점심시간 매수 허용
        squeeze_config = self.config.get('squeeze_momentum', {})
        entry_mode = squeeze_config.get('entry_mode', 'squeeze_only')  # 기본값: squeeze_only

        # 🔥 수정: squeeze_2tf, ma_cross, smc 모드도 점심시간 허용
        if entry_mode in ['squeeze_only', 'squeeze_with_orderbook', 'squeeze_2tf', 'ma_cross', 'smc']:
            # 스퀴즈/MA/SMC 기반 모드에서는 점심시간 매수 허용
            return True, ""

        # 🔴 점심시간 차단 (12:00-14:00) - legacy_only, hybrid 모드만
        if MIDDAY_START <= t < MIDDAY_END:
            return False, f"🚫 점심시간 진입 차단 ({t.strftime('%H:%M:%S')})"

        return True, ""

    def execute_buy(self, stock_code: str, stock_name: str, price: float, df: pd.DataFrame, position_size_mult: float = 1.0, entry_confidence: float = 1.0, entry_reason: str = None):
        """매수 실행 (실계좌 기반 리스크 관리 + SignalOrchestrator 포지션 조정)

        Args:
            entry_confidence: 진입 신뢰도 (0.0~1.0, TIER_1=1.0, TIER_2=0.7, TIER_3=0.5)
            entry_reason: 매수 이유 (예: "12:34 30분봉 MA5/MA20 골든크로스 + Squeeze OFF")
        """
        # 🔧 2026-02-07: 진입 시도 카운트 (쿨다운 체크 이전)
        self.reentry_metrics.record_entry_signal()

        # 🔧 FIX: 시간 필터 최우선 체크 (모든 경로 강제 적용)
        time_ok, time_reason = self._is_valid_entry_time()
        if not time_ok:
            console.print(f"[red]{time_reason}[/red]")
            return

        # 🔧 FIX: 금지 종목 체크 (3회 연속 손실 종목)
        if stock_code in self.stock_ban_list:
            console.print(f"[red]🚫 {stock_name}: 3회 연속 손실로 당일 진입 금지[/red]")
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

            # 쿨다운 0분 → 차단하지 않음 (take_profit 등)
            if cooldown_required > 0:
                elapsed = (datetime.now() - last_exit).total_seconds() / 60
                if elapsed < cooldown_required:
                    remaining = cooldown_required - elapsed
                    reason_label = self._categorize_exit_reason(exit_reason) if self._cooldown_v2_enabled else ("손절" if is_loss else "익절")
                    console.print(f"[yellow]⏸️  {stock_name}: [{reason_label}] 쿨다운 {remaining:.1f}분 남음 (총 {cooldown_required}분)[/yellow]")

                    # 쿨다운 차단 이벤트 기록
                    from metrics.reentry_metrics import ReentryBlockedEvent
                    self.reentry_metrics.record_blocked(ReentryBlockedEvent(
                        timestamp=datetime.now(),
                        symbol=stock_code,
                        symbol_name=stock_name,
                        direction='long',
                        elapsed_min=elapsed,
                        cooldown_min=cooldown_required,
                        is_loss_cooldown=is_loss,
                        exit_reason=exit_reason,
                    ))
                    return
            # 쿨다운 만료 또는 0분 → 제거
            del self.stock_cooldown[stock_code]

        # 🔧 CRITICAL FIX: 이미 포지션이 있으면 추가 매수 금지 (중복 매수 방지)
        if stock_code in self.positions:
            existing_qty = self.positions[stock_code].get('quantity', 0)
            if existing_qty > 0:
                console.print(f"[yellow]⚠️  {stock_name}: 이미 보유 중 ({existing_qty}주) - 추가 매수 금지[/yellow]")
                return

        # 🔴 GPT 개선: 종목별 일일 거래 제한 (과도한 집중 방지)
        today_trade_count = self.daily_trade_count.get(stock_code, 0)
        if today_trade_count >= self.max_trades_per_stock_per_day:
            console.print(f"[red]🚫 {stock_name}: 일일 거래 한도 초과 ({today_trade_count}/{self.max_trades_per_stock_per_day}회)[/red]")
            return

        console.print()
        console.print("=" * 80, style="green")
        console.print(f"🔔 매수 신호 발생: {stock_name} ({stock_code})", style="bold green")
        console.print(f"   가격: {price:,.0f}원")
        console.print(f"   시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

        # 실계좌 기반 포지션 크기 계산
        if not self.risk_manager:
            console.print("[red]❌ 리스크 관리자가 초기화되지 않았습니다.[/red]")
            return

        trailing_cfg = self.config.get_trailing_config()
        stop_loss_pct = trailing_cfg.get('stop_loss_pct', getattr(self.analyzer, 'stop_loss_pct', 3.0))

        # 손절가 계산 (설정 기반)
        stop_loss_price = price * (1 - stop_loss_pct / 100)

        # 🔧 FIX: 포지션 크기 계산 (진입 신뢰도 반영)
        position_calc = self.risk_manager.calculate_position_size(
            current_balance=self.current_cash,
            current_price=price,
            stop_loss_price=stop_loss_price,
            entry_confidence=entry_confidence
        )

        # SignalOrchestrator의 포지션 조정 반영
        # 🔧 FIX: 최소 1주 보장 (이중 축소 방지)
        raw_quantity = position_calc['quantity'] * position_size_mult
        quantity = int(max(1, int(raw_quantity))) if position_calc['quantity'] >= 1 else 0  # Python int() 명시
        amount = position_calc['investment'] * position_size_mult

        # 진입 가능 여부 확인
        can_enter, reason = self.risk_manager.can_open_position(
            current_balance=self.current_cash,
            current_positions_value=self.positions_value,
            position_count=len(self.positions),
            position_size=amount
        )

        if not can_enter:
            console.print(f"[yellow]⚠️  매수 불가: {reason}[/yellow]")
            console.print("=" * 80, style="yellow")
            return

        console.print(f"[dim]📊 포지션 계산:[/dim]")
        console.print(f"[dim]   - 투자금액: {amount:,.0f}원 (리스크: {position_calc['risk_amount']:,.0f}원)[/dim]")
        console.print(f"[dim]   - 매수수량: {quantity}주[/dim]")
        console.print(f"[dim]   - 포지션비율: {position_calc['position_ratio']:.1f}%[/dim]")
        console.print(f"[dim]   - 포지션 조정 배수: {position_size_mult*100:.0f}%[/dim]")

        # 🔧 CRITICAL FIX: 수량 검증 (0주 주문 방지)
        if quantity <= 0:
            console.print(f"[yellow]⚠️  매수 불가: 계산된 수량이 0주입니다.[/yellow]")
            console.print(f"[yellow]   잔고: {self.current_cash:,.0f}원, 가격: {price:,.0f}원[/yellow]")
            console.print(f"[yellow]   계산된 수량: {position_calc['quantity']:.2f} × {position_size_mult:.2f} = {quantity}주[/yellow]")
            console.print("=" * 80, style="yellow")
            return

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

        except Exception as e:
            console.print(f"[red]❌ 매수 API 호출 실패: {e}[/red]")
            return

        # ✅ EOD Manager Phase 1: 진입 시점 overnight 판단
        allow_overnight, overnight_score = self.should_allow_overnight(
            stock_code=stock_code,
            df=df,
            signal_result={},  # 필요 시 확장 가능
            entry_confidence=entry_confidence
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
        }

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

        trade_id = self.db.insert_trade(trade_data)
        self.positions[stock_code]['trade_id'] = trade_id

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

        # 🔴 GPT 개선: 종목별 일일 거래 카운트 증가
        self.daily_trade_count[stock_code] = self.daily_trade_count.get(stock_code, 0) + 1
        console.print(f"[dim]📊 {stock_name}: 오늘 {self.daily_trade_count[stock_code]}회 거래 (최대 {self.max_trades_per_stock_per_day}회)[/dim]")

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

    def should_allow_overnight(self, stock_code: str, df: pd.DataFrame, signal_result: Dict, entry_confidence: float) -> Tuple[bool, float]:
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
        """
        try:
            # EOD 정책 설정 확인
            eod_config = self.config.get_section('eod_policy')
            if not eod_config or not eod_config.get('enabled', False):
                return False, 0.0

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

    async def handle_eod(self):
        """
        EOD (End of Day) 프로세스 실행 (14:55)

        1. allow_overnight=True 포지션 중 익일 보유 대상 선정
        2. 나머지 포지션 청산 (15:05)
        3. 우선 감시 리스트 생성 (다음날 갭업 재진입용)
        """
        try:
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
                    # 리스크 체크 후 매수
                    can_buy, reason = self._is_valid_entry_time()
                    if not can_buy:
                        console.print(f"[yellow]⚠️  {candidate['stock_name']}: {reason}[/yellow]")
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
            holding_duration = (datetime.now() - entry_time).seconds if entry_time else 0

            partial_sell_trade = {
                'stock_code': stock_code,
                'stock_name': position['name'],
                'trade_type': 'SELL',
                'trade_time': datetime.now().isoformat(),
                'price': sell_price,  # 실제 체결 가격 사용
                'quantity': partial_quantity,
                'amount': sell_price * partial_quantity,  # 실제 체결 금액
                'exit_reason': f'부분청산 {stage}단계 (+{profit_pct:.1f}%)',
                'realized_profit': realized_profit,
                'profit_rate': profit_pct,
                'holding_duration': holding_duration
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

        # DB에 매도 정보 저장 (매수 시 생성한 trade 업데이트)
        trade_id = position.get('trade_id')
        if trade_id:
            # 매도 거래 추가 (SELL) - numpy 타입을 Python 기본 타입으로 변환
            sell_trade = {
                'stock_code': stock_code,
                'stock_name': position['name'],
                'trade_type': 'SELL',
                'trade_time': datetime.now().isoformat(),
                'price': float(price),
                'quantity': int(position['quantity']),
                'amount': float(price * position['quantity']),
                'exit_reason': reason,
                'realized_profit': float(realized_profit),
                'profit_rate': float(profit_pct),
                'holding_duration': int(holding_duration)
            }
            self.db.insert_trade(sell_trade)

        # 실제 키움 API 매도 주문
        order_result = None  # 🔧 FIX: 초기화 (NoneType 에러 방지)
        order_no = None
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
                target_price = price * 0.995  # 현재가 -0.5%
                sell_price = self._adjust_price_to_tick(target_price)  # 호가단위 조정
                console.print(f"[dim]  지정가 설정: {sell_price:,}원 (현재가 {price:,}원의 99.5% → 호가단위 조정)[/dim]")

                order_result = self.api.order_sell(
                    stock_code=stock_code,
                    quantity=position['quantity'],
                    price=sell_price,  # int(price) → sell_price
                    trade_type="0"  # 지정가
                )

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

        except Exception as e:
            console.print(f"[red]❌ 매도 API 호출 실패: {e}[/red]")
            console.print(f"[yellow]⚠️  포지션은 유지됩니다. 수동으로 처리하세요.[/yellow]")
            import traceback
            console.print(f"[dim]{traceback.format_exc()}[/dim]")
            return

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

        console.print(f"✅ 매도 완료 (주문번호: {order_no})")
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
                console.print("[dim]DEBUG: 09:00까지 대기 중...[/dim]")
                await self.wait_until_time(9, 0)
                console.print("[dim]DEBUG: 대기 완료![/dim]")
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
