"""
매매 로그 시스템 (Trade Logger)

모든 매매 이벤트를 구조화하여 기록
- 시그널 발생
- 필터 통과/차단
- 진입/청산 결정
- 리스크 체크
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional, Any
from pathlib import Path


class TradeLogger:
    """매매 로그 시스템"""

    def __init__(
        self,
        log_dir: str = "logs",
        session_name: Optional[str] = None
    ):
        """
        초기화

        Args:
            log_dir: 로그 저장 디렉토리
            session_name: 세션 이름 (None이면 타임스탬프 사용)
        """
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)

        # 세션 이름
        if session_name is None:
            session_name = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_name = session_name

        # 로그 파일 경로
        self.log_file = self.log_dir / f"trade_log_{session_name}.jsonl"
        self.summary_file = self.log_dir / f"summary_{session_name}.json"

        # 메모리 버퍼 (세션 요약용)
        self.events: List[Dict] = []
        self.session_stats = {
            'start_time': datetime.now().isoformat(),
            'signals': 0,
            'entries': 0,
            'exits': 0,
            'filters_blocked': 0,
            'risk_blocked': 0
        }

    def log_event(
        self,
        event_type: str,
        stock_code: str,
        data: Dict[str, Any],
        level: str = "INFO"
    ):
        """
        이벤트 로그 기록

        Args:
            event_type: 이벤트 타입 (SIGNAL, FILTER, ENTRY, EXIT, RISK 등)
            stock_code: 종목코드
            data: 이벤트 데이터
            level: 로그 레벨 (DEBUG, INFO, WARNING, ERROR)
        """
        event = {
            'timestamp': datetime.now().isoformat(),
            'level': level,
            'type': event_type,
            'stock_code': stock_code,
            'data': data
        }

        # 파일에 JSONL 형식으로 기록
        with open(self.log_file, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')

        # 메모리 버퍼에 추가
        self.events.append(event)

        # 통계 업데이트
        if event_type == 'SIGNAL':
            self.session_stats['signals'] += 1
        elif event_type == 'ENTRY':
            self.session_stats['entries'] += 1
        elif event_type == 'EXIT':
            self.session_stats['exits'] += 1
        elif event_type == 'FILTER_BLOCKED':
            self.session_stats['filters_blocked'] += 1
        elif event_type == 'RISK_BLOCKED':
            self.session_stats['risk_blocked'] += 1

    def log_signal(
        self,
        stock_code: str,
        signal: int,
        price: float,
        vwap: float,
        indicators: Dict[str, Any]
    ):
        """
        매수/매도 시그널 로그

        Args:
            stock_code: 종목코드
            signal: 시그널 (1: 매수, -1: 매도, 0: 관망)
            price: 현재가
            vwap: VWAP 값
            indicators: 기타 지표 (RSI, ATR 등)
        """
        signal_name = "BUY" if signal == 1 else "SELL" if signal == -1 else "HOLD"

        self.log_event(
            event_type='SIGNAL',
            stock_code=stock_code,
            data={
                'signal': signal_name,
                'price': price,
                'vwap': vwap,
                'price_vs_vwap': ((price - vwap) / vwap * 100),
                'indicators': indicators
            }
        )

    def log_filter_check(
        self,
        stock_code: str,
        filter_name: str,
        passed: bool,
        reason: str = "",
        details: Optional[Dict] = None
    ):
        """
        필터 체크 로그

        Args:
            stock_code: 종목코드
            filter_name: 필터명 (breakout_confirm, volume_value, market_momentum 등)
            passed: 통과 여부
            reason: 차단 사유 (차단 시)
            details: 상세 정보
        """
        event_type = 'FILTER_PASSED' if passed else 'FILTER_BLOCKED'

        self.log_event(
            event_type=event_type,
            stock_code=stock_code,
            data={
                'filter': filter_name,
                'passed': passed,
                'reason': reason,
                'details': details or {}
            },
            level='INFO' if passed else 'WARNING'
        )

    def log_entry(
        self,
        stock_code: str,
        quantity: int,
        price: float,
        risk_amount: float,
        stop_loss: float,
        strategy: str = ""
    ):
        """
        진입 로그

        Args:
            stock_code: 종목코드
            quantity: 수량
            price: 진입가
            risk_amount: 리스크 금액
            stop_loss: 손절가
            strategy: 전략명
        """
        self.log_event(
            event_type='ENTRY',
            stock_code=stock_code,
            data={
                'quantity': quantity,
                'price': price,
                'total_cost': quantity * price,
                'risk_amount': risk_amount,
                'stop_loss': stop_loss,
                'stop_loss_pct': ((stop_loss - price) / price * 100),
                'strategy': strategy
            },
            level='INFO'
        )

    def log_exit(
        self,
        stock_code: str,
        quantity: int,
        entry_price: float,
        exit_price: float,
        reason: str,
        highest_price: Optional[float] = None,
        trailing_active: bool = False
    ):
        """
        청산 로그

        Args:
            stock_code: 종목코드
            quantity: 수량
            entry_price: 진입가
            exit_price: 청산가
            reason: 청산 사유
            highest_price: 최고가 (optional)
            trailing_active: 트레일링 활성화 여부
        """
        profit = quantity * (exit_price - entry_price)
        profit_pct = ((exit_price - entry_price) / entry_price * 100)

        data = {
            'quantity': quantity,
            'entry_price': entry_price,
            'exit_price': exit_price,
            'profit': profit,
            'profit_pct': profit_pct,
            'reason': reason,
            'trailing_active': trailing_active
        }

        if highest_price is not None:
            data['highest_price'] = highest_price
            data['highest_profit_pct'] = ((highest_price - entry_price) / entry_price * 100)
            data['profit_preservation'] = (profit_pct / data['highest_profit_pct'] * 100) if data['highest_profit_pct'] > 0 else 0

        level = 'INFO' if profit >= 0 else 'WARNING'

        self.log_event(
            event_type='EXIT',
            stock_code=stock_code,
            data=data,
            level=level
        )

    def log_risk_check(
        self,
        can_trade: bool,
        reason: str,
        risk_stats: Optional[Dict] = None
    ):
        """
        리스크 체크 로그

        Args:
            can_trade: 거래 가능 여부
            reason: 사유
            risk_stats: 리스크 통계 (optional)
        """
        event_type = 'RISK_OK' if can_trade else 'RISK_BLOCKED'

        self.log_event(
            event_type=event_type,
            stock_code='SYSTEM',
            data={
                'can_trade': can_trade,
                'reason': reason,
                'stats': risk_stats or {}
            },
            level='INFO' if can_trade else 'ERROR'
        )

    def log_error(
        self,
        stock_code: str,
        error_type: str,
        message: str,
        exception: Optional[Exception] = None
    ):
        """
        에러 로그

        Args:
            stock_code: 종목코드
            error_type: 에러 타입
            message: 에러 메시지
            exception: 예외 객체 (optional)
        """
        data = {
            'error_type': error_type,
            'message': message
        }

        if exception is not None:
            data['exception'] = str(exception)
            data['exception_type'] = type(exception).__name__

        self.log_event(
            event_type='ERROR',
            stock_code=stock_code,
            data=data,
            level='ERROR'
        )

    def save_summary(self):
        """세션 요약 저장"""
        self.session_stats['end_time'] = datetime.now().isoformat()
        self.session_stats['total_events'] = len(self.events)

        # 종목별 통계
        stocks_stats = {}
        for event in self.events:
            stock = event['stock_code']
            if stock not in stocks_stats:
                stocks_stats[stock] = {
                    'signals': 0,
                    'entries': 0,
                    'exits': 0,
                    'total_profit': 0.0
                }

            if event['type'] == 'SIGNAL':
                stocks_stats[stock]['signals'] += 1
            elif event['type'] == 'ENTRY':
                stocks_stats[stock]['entries'] += 1
            elif event['type'] == 'EXIT':
                stocks_stats[stock]['exits'] += 1
                if 'profit' in event['data']:
                    stocks_stats[stock]['total_profit'] += event['data']['profit']

        self.session_stats['stocks'] = stocks_stats

        # JSON 파일로 저장
        with open(self.summary_file, 'w', encoding='utf-8') as f:
            json.dump(self.session_stats, f, ensure_ascii=False, indent=2)

    def get_events(
        self,
        event_type: Optional[str] = None,
        stock_code: Optional[str] = None,
        level: Optional[str] = None
    ) -> List[Dict]:
        """
        이벤트 조회

        Args:
            event_type: 이벤트 타입 필터 (optional)
            stock_code: 종목코드 필터 (optional)
            level: 로그 레벨 필터 (optional)

        Returns:
            필터링된 이벤트 리스트
        """
        events = self.events

        if event_type:
            events = [e for e in events if e['type'] == event_type]

        if stock_code:
            events = [e for e in events if e['stock_code'] == stock_code]

        if level:
            events = [e for e in events if e['level'] == level]

        return events

    def print_summary(self):
        """세션 요약 출력"""
        print("\n" + "="*80)
        print(f"세션 요약: {self.session_name}")
        print("="*80)
        print(f"총 이벤트: {len(self.events)}개")
        print(f"시그널: {self.session_stats['signals']}개")
        print(f"진입: {self.session_stats['entries']}개")
        print(f"청산: {self.session_stats['exits']}개")
        print(f"필터 차단: {self.session_stats['filters_blocked']}개")
        print(f"리스크 차단: {self.session_stats['risk_blocked']}개")
        print("="*80 + "\n")
