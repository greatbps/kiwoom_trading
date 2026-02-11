"""
시장 모니터 - 실시간 종목 모니터링

관심 종목의 실시간 가격과 분석을 수행합니다.
"""
from dataclasses import dataclass
from datetime import datetime, time
from typing import List, Dict, Optional
import json
import os

from kiwoom_api import KiwoomAPI
from analyzers.analysis_engine import AnalysisEngine
from strategies.trading_strategy import TradingStrategy


@dataclass
class StockToMonitor:
    """모니터링할 종목 정보"""
    stock_code: str
    stock_name: str
    last_check_time: Optional[datetime] = None
    last_score: float = 0.0
    last_signal: str = "HOLD"


class MarketMonitor:
    """
    시장 모니터

    관심 종목 리스트를 모니터링하고 매수 신호를 감지합니다.
    """

    # 장 운영 시간
    MARKET_OPEN_TIME = time(9, 0)  # 09:00
    MARKET_CLOSE_TIME = time(15, 30)  # 15:30

    # 모니터링 간격 (초)
    MONITOR_INTERVAL = 60  # 1분마다

    def __init__(
        self,
        api: KiwoomAPI,
        analysis_engine: AnalysisEngine,
        trading_strategy: TradingStrategy,
        watchlist_path: str = 'data/watchlist.json'
    ):
        """
        초기화

        Args:
            api: Kiwoom API 인스턴스
            analysis_engine: 분석 엔진
            trading_strategy: 매매 전략
            watchlist_path: 관심 종목 리스트 경로
        """
        self.api = api
        self.analysis_engine = analysis_engine
        self.trading_strategy = trading_strategy
        self.watchlist_path = watchlist_path

        self.watchlist: List[StockToMonitor] = []
        self.load_watchlist()

    def is_market_open(self, current_time: datetime = None) -> bool:
        """
        장 운영 시간 확인

        Args:
            current_time: 확인할 시간 (None이면 현재 시간)

        Returns:
            장 운영 중이면 True
        """
        if current_time is None:
            current_time = datetime.now()

        # 주말 체크
        if current_time.weekday() >= 5:  # 토요일(5), 일요일(6)
            return False

        current_time_only = current_time.time()
        return self.MARKET_OPEN_TIME <= current_time_only <= self.MARKET_CLOSE_TIME

    def add_to_watchlist(self, stock_code: str, stock_name: str):
        """
        관심 종목 추가

        Args:
            stock_code: 종목코드
            stock_name: 종목명
        """
        # 중복 체크
        if any(s.stock_code == stock_code for s in self.watchlist):
            return

        self.watchlist.append(StockToMonitor(
            stock_code=stock_code,
            stock_name=stock_name
        ))
        self.save_watchlist()

    def remove_from_watchlist(self, stock_code: str):
        """
        관심 종목 제거

        Args:
            stock_code: 종목코드
        """
        self.watchlist = [s for s in self.watchlist if s.stock_code != stock_code]
        self.save_watchlist()

    def scan_for_buy_signals(self, account_balance: float) -> List[dict]:
        """
        매수 신호 스캔

        Args:
            account_balance: 계좌 잔고

        Returns:
            매수 추천 종목 리스트
        """
        buy_candidates = []

        for stock in self.watchlist:
            try:
                # 데이터 수집
                chart_result = self.api.get_daily_chart(stock_code=stock.stock_code)
                chart_data = chart_result.get('stk_dt_pole_chart_qry') if chart_result.get('return_code') == 0 else None

                if not chart_data:
                    continue

                investor_result = self.api.get_investor_trend(stock_code=stock.stock_code, amt_qty_tp="1", trde_tp="0", unit_tp="1")
                investor_data = investor_result.get('stk_invsr_orgn') if investor_result.get('return_code') == 0 else None

                program_result = self.api.get_program_trading(mrkt_tp="P00101", stex_tp="1")
                program_data = program_result.get('stk_prm_trde_prst') if program_result.get('return_code') == 0 else None

                stock_info_result = self.api.get_stock_info(stock_code=stock.stock_code)
                stock_info = stock_info_result if stock_info_result.get('return_code') == 0 else None

                # 현재가 추출
                current_price = None
                if stock_info and stock_info.get('cur_prc'):
                    current_price = float(str(stock_info['cur_prc']).replace(',', '').replace('+', '').replace('-', ''))

                if not current_price and chart_data:
                    latest_candle = chart_data[0]
                    current_price = float(str(latest_candle.get('cur_prc', 0)).replace(',', '').replace('+', '').replace('-', ''))

                if not current_price:
                    continue

                # 통합 분석
                analysis_result = self.analysis_engine.analyze(
                    stock_code=stock.stock_code,
                    stock_name=stock.stock_name,
                    chart_data=chart_data,
                    investor_data=investor_data,
                    program_data=program_data,
                    stock_info=stock_info
                )

                # 매매 계획 생성
                trading_plan = self.trading_strategy.generate_trading_plan(
                    stock_code=stock.stock_code,
                    stock_name=stock.stock_name,
                    current_price=current_price,
                    account_balance=account_balance,
                    chart_data=chart_data,
                    analysis_result=analysis_result
                )

                # 매수 신호 체크
                entry_signal = trading_plan['entry_signal']['signal']
                if entry_signal in ['BUY', 'STRONG_BUY'] and trading_plan['position']['quantity'] > 0:
                    buy_candidates.append({
                        'stock_code': stock.stock_code,
                        'stock_name': stock.stock_name,
                        'current_price': current_price,
                        'signal': entry_signal,
                        'score': analysis_result['final_score'],
                        'trading_plan': trading_plan,
                        'analysis_result': analysis_result
                    })

                # 상태 업데이트
                stock.last_check_time = datetime.now()
                stock.last_score = analysis_result['final_score']
                stock.last_signal = entry_signal

            except Exception as e:
                print(f"  [경고] {stock.stock_name} 스캔 실패: {e}")
                continue

        # 점수 순으로 정렬
        buy_candidates.sort(key=lambda x: x['score'], reverse=True)

        return buy_candidates

    def get_current_price(self, stock_code: str) -> Optional[float]:
        """
        현재가 조회

        Args:
            stock_code: 종목코드

        Returns:
            현재가 (조회 실패시 None)
        """
        try:
            # 기본정보에서 현재가 추출
            stock_info = self.api.get_stock_info(stock_code=stock_code)
            if stock_info.get('return_code') == 0 and stock_info.get('cur_prc'):
                price = float(str(stock_info['cur_prc']).replace(',', '').replace('+', '').replace('-', ''))
                return price

            # 차트에서 최신가 추출
            chart_result = self.api.get_daily_chart(stock_code=stock_code)
            if chart_result.get('return_code') == 0 and 'stk_dt_pole_chart_qry' in chart_result:
                chart_data = chart_result['stk_dt_pole_chart_qry']
                if chart_data:
                    latest = chart_data[0]
                    price = float(str(latest.get('cur_prc', 0)).replace(',', '').replace('+', '').replace('-', ''))
                    return price

        except Exception as e:
            print(f"[경고] {stock_code} 현재가 조회 실패: {e}")

        return None

    def save_watchlist(self):
        """관심 종목 저장"""
        os.makedirs(os.path.dirname(self.watchlist_path), exist_ok=True)

        data = [
            {
                'stock_code': s.stock_code,
                'stock_name': s.stock_name,
                'last_check_time': s.last_check_time.isoformat() if s.last_check_time else None,
                'last_score': s.last_score,
                'last_signal': s.last_signal
            }
            for s in self.watchlist
        ]

        with open(self.watchlist_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_watchlist(self):
        """관심 종목 로드"""
        try:
            with open(self.watchlist_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                self.watchlist = [
                    StockToMonitor(
                        stock_code=item['stock_code'],
                        stock_name=item['stock_name'],
                        last_check_time=datetime.fromisoformat(item['last_check_time']) if item.get('last_check_time') else None,
                        last_score=item.get('last_score', 0.0),
                        last_signal=item.get('last_signal', 'HOLD')
                    )
                    for item in data
                ]
        except FileNotFoundError:
            # 기본 관심 종목 설정
            self.watchlist = [
                StockToMonitor(stock_code="005930", stock_name="삼성전자"),
                StockToMonitor(stock_code="000660", stock_name="SK하이닉스"),
                StockToMonitor(stock_code="035420", stock_name="NAVER"),
            ]
            self.save_watchlist()

    def get_watchlist_summary(self) -> List[dict]:
        """관심 종목 요약"""
        return [
            {
                'stock_code': s.stock_code,
                'stock_name': s.stock_name,
                'last_check_time': s.last_check_time.isoformat() if s.last_check_time else None,
                'last_score': s.last_score,
                'last_signal': s.last_signal
            }
            for s in self.watchlist
        ]
