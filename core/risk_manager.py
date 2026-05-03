"""
리스크 관리자 - 포트폴리오 전체 리스크 관리

trading_system의 실제 성과 데이터를 기반으로 한 검증된 리스크 관리 시스템:
- 거래당 2% 리스크 제한
- 포지션당 최대 30% 투자
- 하드 리밋: 포지션 20만원, 일일 손실 50만원
- 동적 한도 조정: 실시간 잔고 기반 계산
"""
from dataclasses import dataclass
from datetime import datetime, date
from typing import List, Optional
import json
import os


@dataclass
class DailyTradeLog:
    """일일 거래 로그"""
    date: str
    trades: List[dict]
    realized_pnl: float  # 실현 손익
    unrealized_pnl: float  # 미실현 손익
    total_pnl: float  # 총 손익
    trade_count: int
    win_count: int
    loss_count: int


class RiskManager:
    """
    리스크 관리자

    포트폴리오 전체의 리스크를 관리하고 일일 한도를 추적합니다.
    trading_system의 검증된 리스크 관리 전략을 기반으로 합니다.
    """

    # 🔧 REFACTOR: 기본값 (하위 호환성)
    DEFAULT_RISK_PER_TRADE = 0.02
    DEFAULT_MAX_POSITION_SIZE = 0.30
    DEFAULT_HARD_MAX_POSITION = 200000
    DEFAULT_HARD_MAX_DAILY_LOSS_PCT = 0.05
    DEFAULT_HARD_MAX_WEEKLY_LOSS_PCT = 0.03
    DEFAULT_HARD_MAX_DAILY_TRADES = 10
    DEFAULT_MAX_POSITIONS = 5
    DEFAULT_MIN_CASH_RESERVE = 0.20
    DEFAULT_CONSECUTIVE_LOSS_LIMIT = 3

    def __init__(
        self,
        initial_balance: float,
        storage_path: str = 'data/risk_log.json',
        config: dict = None
    ):
        """
        초기화

        Args:
            initial_balance: 초기 잔고
            storage_path: 리스크 로그 저장 경로
            config: 전략 설정 (strategy_hybrid.yaml)
        """
        self.initial_balance = initial_balance
        self.storage_path = storage_path

        # 🔧 REFACTOR: 설정 파일 연동 (config 우선, 없으면 기본값)
        if config:
            risk_mgmt = config.get('risk_management', {})
            risk_ctrl = config.get('risk_control', {})

            self.MAX_POSITIONS = risk_mgmt.get('max_positions', self.DEFAULT_MAX_POSITIONS)
            self.HARD_MAX_DAILY_TRADES = risk_mgmt.get('max_trades_per_day', self.DEFAULT_HARD_MAX_DAILY_TRADES)
            self.HARD_MAX_DAILY_LOSS_PCT = risk_mgmt.get('daily_max_loss_pct', self.DEFAULT_HARD_MAX_DAILY_LOSS_PCT) / 100
            self.HARD_MAX_WEEKLY_LOSS_PCT = risk_ctrl.get('max_weekly_loss_pct', self.DEFAULT_HARD_MAX_WEEKLY_LOSS_PCT * 100) / 100
            self.CONSECUTIVE_LOSS_LIMIT = risk_mgmt.get('max_consecutive_losses', self.DEFAULT_CONSECUTIVE_LOSS_LIMIT)
            self.MIN_CASH_RESERVE = risk_mgmt.get('min_cash_reserve_pct', self.DEFAULT_MIN_CASH_RESERVE * 100) / 100

            # 🔧 Phase 3: 연속 손실 대응 정책
            self.CONSECUTIVE_LOSS_ACTION = risk_mgmt.get('on_consecutive_loss_action', 'halt_day')
            self.LOSS_SIZE_REDUCTION = risk_mgmt.get('loss_size_reduction', 0.5)

            # 포지션 크기 제한
            self.RISK_PER_TRADE = risk_mgmt.get('position_risk_pct', self.DEFAULT_RISK_PER_TRADE * 100) / 100
            self.MAX_POSITION_SIZE = risk_mgmt.get('max_position_size_pct', self.DEFAULT_MAX_POSITION_SIZE * 100) / 100
            self.HARD_MAX_POSITION = risk_mgmt.get('hard_max_position', self.DEFAULT_HARD_MAX_POSITION)
        else:
            # 기본값 사용 (하위 호환성)
            self.MAX_POSITIONS = self.DEFAULT_MAX_POSITIONS
            self.HARD_MAX_DAILY_TRADES = self.DEFAULT_HARD_MAX_DAILY_TRADES
            self.HARD_MAX_DAILY_LOSS_PCT = self.DEFAULT_HARD_MAX_DAILY_LOSS_PCT
            self.HARD_MAX_WEEKLY_LOSS_PCT = self.DEFAULT_HARD_MAX_WEEKLY_LOSS_PCT
            self.CONSECUTIVE_LOSS_LIMIT = self.DEFAULT_CONSECUTIVE_LOSS_LIMIT
            self.MIN_CASH_RESERVE = self.DEFAULT_MIN_CASH_RESERVE
            self.RISK_PER_TRADE = self.DEFAULT_RISK_PER_TRADE
            self.MAX_POSITION_SIZE = self.DEFAULT_MAX_POSITION_SIZE
            self.HARD_MAX_POSITION = self.DEFAULT_HARD_MAX_POSITION

        # 일일 추적
        self.today = date.today().isoformat()
        self.daily_trades: List[dict] = []
        self.daily_realized_pnl = 0.0  # 오늘 실현 손익

        # 🔧 FIX: 주간 추적 (문서 명세)
        from datetime import timedelta
        self.week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        self.weekly_trades: List[dict] = []
        self.weekly_realized_pnl = 0.0  # 이번 주 실현 손익

        # 🔧 FIX: 연속 손실 추적 (문서 명세)
        self.consecutive_losses = 0  # 연속 손실 카운터
        self.cooldown_until = None  # 쿨다운 종료 날짜
        self.lsg_activated_date: Optional[str] = None  # 🔧 2026-04-02: LSG 발동일 (YYYY-MM-DD)
        self.position_size_multiplier = 1.0  # 🔧 Phase 3: 포지션 사이즈 축소용 multiplier

        # 로그 로드
        self.load()

    def can_open_position(
        self,
        current_balance: float,
        current_positions_value: float,
        position_count: int,
        position_size: float
    ) -> tuple[bool, str]:
        """
        신규 포지션 진입 가능 여부 확인

        Args:
            current_balance: 현재 잔고
            current_positions_value: 현재 보유 포지션 총 평가액
            position_count: 현재 보유 종목 수
            position_size: 진입하려는 포지션 크기 (금액)

        Returns:
            (가능 여부, 사유)
        """
        # 🔧 CRITICAL FIX: 0-1. 날짜 롤오버 체크 (일일 거래 초기화)
        from pathlib import Path
        import json
        from datetime import datetime, date

        current_date = date.today().isoformat()
        if current_date != self.today:
            # 날짜가 바뀜 → 일일 거래 초기화
            self.today = current_date
            self.daily_trades = []
            self.daily_realized_pnl = 0.0

            # 주간 롤오버 체크
            from datetime import timedelta
            current_week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
            if current_week_start != self.week_start:
                # 새로운 주 시작 → 주간 거래 초기화
                self.week_start = current_week_start
                self.weekly_trades = []
                self.weekly_realized_pnl = 0.0

            self.save()  # 즉시 저장

        # 🔧 FIX: 0-2. 연속 손실 쿨다운 체크 (3회 이상만 적용)
        # 📌 손실 1-2회: 개별 종목만 쿨다운 (main_auto_trading.py의 stock_cooldown 처리)
        # 📌 손실 3회 이상: 전체 거래 차단 (글로벌 쿨다운)
        cooldown_file = Path('data/cooldown.lock')

        # 메모리 쿨다운도 체크 (3회 이상 연속 손실만)
        if self.cooldown_until and self.consecutive_losses >= self.CONSECUTIVE_LOSS_LIMIT:
            if datetime.now().date() <= datetime.fromisoformat(self.cooldown_until).date():
                return False, f"연속 손실 {self.consecutive_losses}회 - 쿨다운 중 (해제: {self.cooldown_until})"

        # 파일 기반 쿨다운 체크 (프로세스 간 공유, 3회 이상만)
        if cooldown_file.exists():
            try:
                cooldown_data = json.loads(cooldown_file.read_text())
                cooldown_until = cooldown_data.get('cooldown_until')
                consecutive_losses = cooldown_data.get('consecutive_losses', 0)

                # 3회 이상 연속 손실일 때만 글로벌 쿨다운 적용
                if cooldown_until and consecutive_losses >= self.CONSECUTIVE_LOSS_LIMIT:
                    until_dt = datetime.fromisoformat(cooldown_until)

                    if datetime.now() <= until_dt:
                        return False, f"연속 손실 {consecutive_losses}회 - 쿨다운 중 (해제: {cooldown_until[:10]})"
                    else:
                        # 쿨다운 기간 만료 → 파일 삭제
                        cooldown_file.unlink()
                        # 메모리 쿨다운도 해제
                        self.cooldown_until = None
                        self.consecutive_losses = 0
                else:
                    # 3회 미만은 파일 삭제 (개별 종목 쿨다운만 적용)
                    cooldown_file.unlink()

            except Exception as e:
                # 손상된 파일 삭제
                print(f"⚠️  쿨다운 파일 읽기 실패: {e}")
                try:
                    cooldown_file.unlink()
                except (OSError, PermissionError):
                    pass  # 파일 삭제 실패는 무시

        # 1. 보유 종목 수 제한
        if position_count >= self.MAX_POSITIONS:
            return False, f"최대 보유 종목 수 초과 ({position_count}/{self.MAX_POSITIONS})"

        # 2. 일일 거래 횟수 제한
        if len(self.daily_trades) >= self.HARD_MAX_DAILY_TRADES:
            return False, f"일일 최대 거래 횟수 초과 ({len(self.daily_trades)}/{self.HARD_MAX_DAILY_TRADES})"

        # 🔧 FIX: 3. 일일 손실 한도 확인 (퍼센트 기반, 문서 명세)
        total_assets = current_balance + current_positions_value
        daily_loss_pct = (self.daily_realized_pnl / self.initial_balance) if self.initial_balance > 0 else 0
        if daily_loss_pct < -self.HARD_MAX_DAILY_LOSS_PCT:
            return False, f"일일 손실 한도 초과 ({daily_loss_pct:.2%} / -{self.HARD_MAX_DAILY_LOSS_PCT:.1%})"

        # 🔧 FIX: 3-1. 주간 손실 경고 (문서 명세: -3% 시 entry_ratio 50% 축소, -5% 시 완전 차단)
        total_assets = current_balance + current_positions_value
        weekly_loss_pct = (self.weekly_realized_pnl / self.initial_balance) if self.initial_balance > 0 else 0

        # -5% 도달 시 완전 차단 (hard stop)
        if weekly_loss_pct < -0.05:
            return False, f"주간 손실 한도 초과 ({weekly_loss_pct:.2%} / -5.0%)"

        # -3% ~ -5% 구간은 entry_ratio 조정으로 처리 (완전 차단 X)

        # 4. 하드 포지션 크기 제한
        if position_size > self.HARD_MAX_POSITION:
            return False, f"하드 포지션 크기 제한 초과 ({position_size:,.0f}원 / {self.HARD_MAX_POSITION:,.0f}원)"

        # 5. 총 자산 대비 포지션 크기 제한 (30%)
        max_position_value = total_assets * self.MAX_POSITION_SIZE

        if position_size > max_position_value:
            return False, f"포지션 크기 제한 초과 ({position_size:,.0f}원 / {max_position_value:,.0f}원)"

        # 6. 현금 보유 비율 확인
        remaining_cash = current_balance - position_size
        cash_ratio = remaining_cash / total_assets

        if cash_ratio < self.MIN_CASH_RESERVE:
            return False, f"현금 보유 비율 부족 ({cash_ratio:.1%} / 최소 {self.MIN_CASH_RESERVE:.1%})"

        return True, "OK"

    def get_weekly_loss_adjustment(self) -> float:
        """
        주간 손실에 따른 entry_ratio 조정 계수 계산 (문서 명세)

        Returns:
            조정 계수 (0.0 ~ 1.0)
            - 주간 손실 < -3%: 0.5 (50% 축소)
            - 주간 손실 >= -3%: 1.0 (조정 없음)
        """
        weekly_loss_pct = (self.weekly_realized_pnl / self.initial_balance) if self.initial_balance > 0 else 0

        # -3% 이하 손실 시 50% 축소 (문서 명세)
        if weekly_loss_pct < -self.HARD_MAX_WEEKLY_LOSS_PCT:
            return 0.5

        # 정상 범위
        return 1.0

    def calculate_position_size(
        self,
        current_balance: float,
        current_price: float,
        stop_loss_price: float,
        entry_confidence: float = 1.0,
        structure_stop_price: float = None
    ) -> dict:
        """
        포지션 크기 계산 (리스크 기반)

        Args:
            current_balance: 현재 잔고
            current_price: 진입 가격
            stop_loss_price: 손절가
            entry_confidence: 진입 신뢰도 (0.0 ~ 1.0)
            structure_stop_price: 구조 기반 손절가 (SMC, 있으면 우선 사용)

        Returns:
            {
                'quantity': 매수 수량,
                'investment': 투자 금액,
                'risk_amount': 리스크 금액,
                'position_ratio': 포지션 비율,
                'max_loss': 최대 손실
            }
        """
        # 🔧 2026-02-06: 구조 기반 손절가 우선 사용
        if structure_stop_price is not None and structure_stop_price > 0:
            # 안전장치: 구조 손절이 -3% 초과하면 -3%로 cap
            max_stop_pct = 0.03
            if (current_price - structure_stop_price) / current_price > max_stop_pct:
                stop_loss_price = current_price * (1 - max_stop_pct)
            else:
                stop_loss_price = structure_stop_price

        # 1. 리스크 기반 계산
        risk_amount = current_balance * self.RISK_PER_TRADE
        risk_per_share = abs(current_price - stop_loss_price)

        if risk_per_share > 0:
            risk_based_quantity = int(risk_amount / risk_per_share)
        else:
            risk_based_quantity = 0

        # 2. 최대 포지션 크기 기반 계산
        max_investment = min(
            current_balance * self.MAX_POSITION_SIZE,
            self.HARD_MAX_POSITION
        )
        max_quantity = int(max_investment / current_price)

        # 3. 신뢰도 조정 (낮은 신뢰도면 포지션 축소)
        confidence_factor = max(0.5, entry_confidence)  # 최소 50%

        # 🔧 FIX: 3-1. 주간 손실 조정 (문서 명세: -3% 이하 시 50% 축소)
        weekly_adjustment = self.get_weekly_loss_adjustment()

        # 4. 최종 수량 결정 (더 작은 값 선택)
        final_quantity = min(risk_based_quantity, max_quantity)
        # 🔧 FIX: 주간 손실 조정 + Phase 3: 연속 손실 시 포지션 축소
        final_quantity = int(final_quantity * confidence_factor * weekly_adjustment * self.position_size_multiplier)

        # 🔧 CRITICAL FIX: 최소 1주 보장 (잔고가 충분하고 시그널이 발생했으면)
        # confidence가 낮아서 0주가 되는 것을 방지
        if final_quantity == 0 and max_quantity >= 1:
            final_quantity = 1

        # 5. 결과 계산
        investment = final_quantity * current_price
        position_ratio = (investment / current_balance * 100) if current_balance > 0 else 0
        max_loss = final_quantity * risk_per_share

        return {
            'quantity': final_quantity,
            'investment': investment,
            'risk_amount': risk_amount,
            'position_ratio': position_ratio,
            'max_loss': max_loss,
            'weekly_adjustment': weekly_adjustment  # 🔧 FIX: 주간 손실 조정 계수 (문서 명세)
        }

    def record_trade(
        self,
        stock_code: str,
        stock_name: str,
        trade_type: str,  # 'BUY' or 'SELL'
        quantity: int,
        price: float,
        realized_pnl: float = 0.0,
        reason: str = None,  # 매수/매도 이유
        **extras,            # ML 피처: choch_grade, market_regime, rvol_at_entry, mfe_pct, mae_pct
    ):
        """
        거래 기록

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            trade_type: 거래 유형 (BUY/SELL)
            quantity: 수량
            price: 가격
            realized_pnl: 실현 손익 (매도시만)
            reason: 매수/매도 이유 (예: "12:34 30분봉 MA5/MA20 골든크로스")
        """
        # 날짜가 바뀌면 초기화
        today = date.today().isoformat()
        if today != self.today:
            self._new_day()

        # 🔧 FIX: 주가 바뀌면 주간 데이터 초기화
        from datetime import timedelta
        current_week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        if current_week_start != self.week_start:
            self._new_week()

        # numpy 타입을 Python 기본 타입으로 변환 (JSON 직렬화 위해)
        trade = {
            'timestamp': datetime.now().isoformat(),
            'stock_code': stock_code,
            'stock_name': stock_name,
            'type': trade_type,
            'quantity': int(quantity),
            'price': float(price),
            'amount': float(quantity * price),
            'realized_pnl': float(realized_pnl) if realized_pnl is not None else 0.0,
            'reason': reason,  # 매수/매도 이유
            # ML 피처 (extras로 전달된 경우에만 포함)
            **{k: v for k, v in extras.items()
               if k in ('choch_grade', 'market_regime', 'rvol_at_entry', 'mfe_pct', 'mae_pct')
               and v is not None},
        }

        self.daily_trades.append(trade)
        self.weekly_trades.append(trade)  # 🔧 FIX: 주간 거래 추적

        # 영구 DB 저장
        try:
            from core.trade_db import TradeDB
            TradeDB().insert(trade)
        except Exception as _tdb_err:
            pass  # DB 실패해도 거래는 계속

        # 실현 손익 업데이트 (매도시)
        if trade_type == 'SELL':
            pnl = float(realized_pnl) if realized_pnl is not None else 0.0
            self.daily_realized_pnl += pnl
            self.weekly_realized_pnl += pnl  # 🔧 FIX: 주간 손익 추적

            # 🔧 FIX: 연속 손실 추적 (문서 명세)
            if pnl < 0:
                self.consecutive_losses += 1
                # 연속 손실 한도 도달 시 정책 적용
                if self.consecutive_losses >= self.CONSECUTIVE_LOSS_LIMIT:
                    if self.CONSECUTIVE_LOSS_ACTION == 'halt_day':
                        # 🔧 Phase 3: 당일 거래 중지 (장 마감까지)
                        self.cooldown_until = datetime.now().replace(hour=15, minute=30, second=0, microsecond=0).isoformat()
                        print("🚫 3연패 발생 - 당일 거래 중지 (해제: 15:30)")
                    elif self.CONSECUTIVE_LOSS_ACTION == 'reduce_size':
                        # 🔧 Phase 3: 포지션 사이즈 축소
                        self.position_size_multiplier = self.LOSS_SIZE_REDUCTION
                        print(f"⏸ 3연패 발생 - 포지션 사이즈 {int(self.LOSS_SIZE_REDUCTION * 100)}% 축소")
                    else:
                        # 기본값: 다음 날까지 쿨다운 (하위 호환성)
                        from datetime import timedelta
                        self.cooldown_until = (date.today() + timedelta(days=1)).isoformat()
            else:
                # 🔧 2026-03-19: 연패 Soft Reset — 완전 0이 아니라 점진적 회복
                # 1승 → max(0, N-2) 감소. 예: 7연패 → 5, 5연패 → 3, 2연패 → 0
                self.consecutive_losses = max(0, self.consecutive_losses - 2)
                self.cooldown_until = None
                self.position_size_multiplier = 1.0  # 포지션 사이즈 복구

        self.save()

    def get_daily_summary(self, unrealized_pnl: float = 0.0) -> DailyTradeLog:
        """
        일일 거래 요약

        Args:
            unrealized_pnl: 미실현 손익

        Returns:
            일일 거래 로그
        """
        win_count = sum(1 for t in self.daily_trades if t['type'] == 'SELL' and t['realized_pnl'] > 0)
        loss_count = sum(1 for t in self.daily_trades if t['type'] == 'SELL' and t['realized_pnl'] < 0)

        return DailyTradeLog(
            date=self.today,
            trades=self.daily_trades.copy(),
            realized_pnl=self.daily_realized_pnl,
            unrealized_pnl=unrealized_pnl,
            total_pnl=self.daily_realized_pnl + unrealized_pnl,
            trade_count=len(self.daily_trades),
            win_count=win_count,
            loss_count=loss_count
        )

    def check_emergency_stop(self, unrealized_pnl: float = 0.0) -> tuple[bool, str]:
        """
        긴급 중지 조건 확인

        Args:
            unrealized_pnl: 미실현 손익

        Returns:
            (중지 여부, 사유)
        """
        total_pnl = self.daily_realized_pnl + unrealized_pnl

        # 🔧 FIX: 1. 일일 손실 한도 초과 (퍼센트 기반, 문서 명세)
        daily_loss_pct = (total_pnl / self.initial_balance) if self.initial_balance > 0 else 0
        if daily_loss_pct < -self.HARD_MAX_DAILY_LOSS_PCT:
            return True, f"일일 손실 한도 초과 ({daily_loss_pct:.2%} / -{self.HARD_MAX_DAILY_LOSS_PCT:.1%})"

        # 2. 일일 거래 횟수 초과
        if len(self.daily_trades) >= self.HARD_MAX_DAILY_TRADES:
            return True, f"일일 최대 거래 횟수 초과 ({len(self.daily_trades)}/{self.HARD_MAX_DAILY_TRADES})"

        return False, "OK"

    def get_risk_metrics(self, current_balance: float, positions_value: float, unrealized_pnl: float) -> dict:
        """
        리스크 지표 계산

        Args:
            current_balance: 현재 잔고
            positions_value: 보유 포지션 평가액
            unrealized_pnl: 미실현 손익

        Returns:
            리스크 지표 딕셔너리
        """
        total_assets = current_balance + positions_value
        total_pnl = self.daily_realized_pnl + unrealized_pnl

        cash_ratio = (current_balance / total_assets * 100) if total_assets > 0 else 0
        position_ratio = (positions_value / total_assets * 100) if total_assets > 0 else 0

        # 🔧 FIX: 일일 손실 한도까지 남은 비율 (퍼센트 기반, 문서 명세)
        daily_loss_pct = (total_pnl / self.initial_balance) if self.initial_balance > 0 else 0
        remaining_loss_allowance_pct = self.HARD_MAX_DAILY_LOSS_PCT + daily_loss_pct  # 음수이므로 +로 계산

        # 일일 수익률
        daily_return = ((total_assets - self.initial_balance) / self.initial_balance * 100) if self.initial_balance > 0 else 0

        return {
            'total_assets': total_assets,
            'current_balance': current_balance,
            'positions_value': positions_value,
            'cash_ratio': cash_ratio,
            'position_ratio': position_ratio,
            'daily_realized_pnl': self.daily_realized_pnl,
            'daily_unrealized_pnl': unrealized_pnl,
            'daily_total_pnl': total_pnl,
            'daily_return': daily_return,
            'daily_loss_pct': daily_loss_pct,  # 🔧 FIX: 일일 손실 퍼센트 추가
            'remaining_loss_allowance_pct': remaining_loss_allowance_pct,  # 🔧 FIX: 퍼센트 기반
            'daily_trade_count': len(self.daily_trades),
            'max_daily_trades': self.HARD_MAX_DAILY_TRADES,
            'remaining_trades': self.HARD_MAX_DAILY_TRADES - len(self.daily_trades)
        }

    def update_balance(self, new_balance: float):
        """
        실시간 잔고 업데이트

        Args:
            new_balance: 업데이트된 현금 잔고
        """
        self.initial_balance = new_balance

    def _new_day(self):
        """새로운 날 초기화"""
        self.today = date.today().isoformat()
        self.daily_trades = []
        self.daily_realized_pnl = 0.0

    def _new_week(self):
        """🔧 FIX: 새로운 주 초기화 (문서 명세)"""
        from datetime import timedelta
        self.week_start = (date.today() - timedelta(days=date.today().weekday())).isoformat()
        self.weekly_trades = []
        self.weekly_realized_pnl = 0.0

    def save(self):
        """리스크 로그 저장 (원자적 쓰기)"""
        os.makedirs(os.path.dirname(self.storage_path), exist_ok=True)

        # daily_realized_pnl도 float 변환 (안전장치)
        data = {
            'initial_balance': float(self.initial_balance),
            'today': self.today,
            'daily_trades': self.daily_trades,
            'daily_realized_pnl': float(self.daily_realized_pnl),
            # 🔧 FIX: 주간 데이터 저장 (문서 명세)
            'week_start': self.week_start,
            'weekly_trades': self.weekly_trades,
            'weekly_realized_pnl': float(self.weekly_realized_pnl),
            # 🔧 FIX: 연속 손실 데이터 저장 (문서 명세)
            'consecutive_losses': self.consecutive_losses,
            'cooldown_until': self.cooldown_until,
            'lsg_activated_date': self.lsg_activated_date,  # 🔧 2026-04-02: auto_reset 영속화
        }

        # 원자적 쓰기: 임시 파일에 쓴 후 rename
        temp_path = self.storage_path + '.tmp'
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            # rename은 원자적 연산
            os.replace(temp_path, self.storage_path)
        except Exception as e:
            # 에러 발생 시 임시 파일 삭제
            if os.path.exists(temp_path):
                os.remove(temp_path)
            raise e

    def load(self):
        """리스크 로그 로드"""
        try:
            with open(self.storage_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

                # 날짜가 같으면 복원
                if data.get('today') == self.today:
                    self.daily_trades = data.get('daily_trades', [])
                    self.daily_realized_pnl = data.get('daily_realized_pnl', 0.0)
                else:
                    # 날짜가 다르면 초기화
                    self._new_day()

                # 🔧 FIX: 주간 데이터 로드 (문서 명세)
                if data.get('week_start') == self.week_start:
                    self.weekly_trades = data.get('weekly_trades', [])
                    self.weekly_realized_pnl = data.get('weekly_realized_pnl', 0.0)
                else:
                    # 주가 다르면 초기화
                    self._new_week()

                # 🔧 FIX: 연속 손실 데이터 로드 (문서 명세)
                self.consecutive_losses = data.get('consecutive_losses', 0)
                self.cooldown_until = data.get('cooldown_until', None)
                self.lsg_activated_date = data.get('lsg_activated_date', None)  # 🔧 2026-04-02

        except FileNotFoundError:
            self._new_day()
            self._new_week()
