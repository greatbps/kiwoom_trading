"""
계좌 관리

계좌 잔고, 보유 종목, 주문 가능 금액 등을 관리
"""
from typing import Dict, List, Optional
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich import box

from kiwoom_api import KiwoomAPI
from exceptions import handle_api_errors, APIException

console = Console()


class AccountManager:
    """계좌 관리자"""

    def __init__(self, api: KiwoomAPI):
        """
        Args:
            api: KiwoomAPI 인스턴스
        """
        self.api = api

        # 계좌 정보
        self.current_cash: float = 0.0
        self.positions_value: float = 0.0
        self.total_assets: float = 0.0

        # 보유 종목 (기존 IntegratedTradingSystem 호환)
        self.holdings: Dict[str, Dict] = {}

    @handle_api_errors(default_return=False, log_errors=True)
    async def initialize(self) -> bool:
        """
        계좌 정보 초기화 (시스템 시작 시)

        Returns:
            초기화 성공 여부
        """
        console.print()
        console.print("=" * 120, style="bold cyan")
        console.print(f"{'계좌 정보 조회':^120}", style="bold cyan")
        console.print("=" * 120, style="bold cyan")

        try:
            # 1. 계좌 잔고 조회
            balance_info = self.api.get_balance()

            if balance_info is None:
                raise APIException("잔고 조회 실패")

            # 예수금 파싱 (15자리 문자열 → 숫자)
            cash_str = balance_info.get('entr', '000000000000000')
            self.current_cash = float(cash_str)

            # 2. 보유 종목 조회
            account_info = self.api.get_account_info()

            if account_info is None:
                raise APIException("계좌 정보 조회 실패")

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

            # 5. 계좌 정보 출력
            table = Table(title="💰 계좌 현황", box=box.ROUNDED, show_header=True, header_style="bold magenta")
            table.add_column("항목", style="cyan", width=20)
            table.add_column("금액", style="yellow", justify="right", width=20)

            table.add_row("계좌번호", self.api.account_number or "N/A")
            table.add_row("예수금", f"{self.current_cash:,.0f}원")
            table.add_row("보유종목 평가", f"{self.positions_value:,.0f}원")
            table.add_row("총 자산", f"{self.total_assets:,.0f}원")
            table.add_row("보유종목 수", f"{len(positions)}개")

            console.print(table)
            console.print()

            # 6. 보유 포지션 로드
            if positions:
                console.print("[bold]보유 포지션:[/bold]")
                for pos in positions:
                    stock_code = pos.get('stk_cd', '')
                    if not stock_code or stock_code == '':
                        continue

                    stock_name = pos.get('stk_nm', '')
                    quantity = int(pos.get('rmnd_qty', 0)) if pos.get('rmnd_qty') else 0
                    avg_price = int(pos.get('buy_uv', 0)) if pos.get('buy_uv') else 0
                    current_price = int(pos.get('cur_prc', 0)) if pos.get('cur_prc') else 0
                    profit_rate = float(pos.get('prft_rt', 0)) if pos.get('prft_rt') else 0.0

                    self.holdings[stock_code] = {
                        'stock_name': stock_name,
                        'name': stock_name,  # 하위 호환성
                        'quantity': quantity,
                        'avg_price': avg_price,
                        'entry_price': avg_price,  # 하위 호환성
                        'current_price': current_price,
                        'profit_rate': profit_rate,
                        'eval_amount': quantity * current_price,
                        'entry_date': datetime.now()
                    }

                    console.print(f"  • {stock_name}({stock_code}): {quantity}주 @ {current_price:,}원 "
                                f"[{'green' if profit_rate >= 0 else 'red'}]{profit_rate:+.2f}%[/]")
                console.print()

            return True

        except Exception as e:
            console.print(f"[red]❌ 계좌 정보 조회 실패: {e}[/red]")
            console.print("[yellow]⚠️  기본값으로 초기화합니다 (10,000,000원)[/yellow]")

            # 기본값으로 초기화
            self.current_cash = 10000000
            self.positions_value = 0
            self.total_assets = 10000000
            console.print()

            return False

    @handle_api_errors(default_return=False, log_errors=True)
    async def update_balance(self) -> bool:
        """
        거래 후 실시간 잔고 업데이트

        Returns:
            업데이트 성공 여부
        """
        try:
            # 1. 계좌 잔고 조회
            balance_info = self.api.get_balance()

            if balance_info is None:
                return False

            cash_str = balance_info.get('entr', str(int(self.current_cash)).zfill(15))
            self.current_cash = float(cash_str)

            # 2. 보유 종목 조회
            account_info = self.api.get_account_info()

            if account_info is None:
                return False

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

            console.print(f"[dim]💰 잔고 업데이트: {self.current_cash:,.0f}원 (총자산: {self.total_assets:,.0f}원)[/dim]")

            return True

        except Exception as e:
            console.print(f"[yellow]⚠️  잔고 업데이트 실패: {e}[/yellow]")
            return False

    def get_available_cash(self) -> float:
        """
        주문 가능 금액 조회

        Returns:
            주문 가능 금액 (원)
        """
        return self.current_cash

    def has_holding(self, stock_code: str) -> bool:
        """
        특정 종목 보유 여부 확인

        Args:
            stock_code: 종목 코드

        Returns:
            보유 여부
        """
        return stock_code in self.holdings

    def get_holding(self, stock_code: str) -> Optional[Dict]:
        """
        특정 종목 보유 정보 조회

        Args:
            stock_code: 종목 코드

        Returns:
            보유 정보 dict (없으면 None)
        """
        return self.holdings.get(stock_code)

    def get_all_holdings(self) -> List[Dict]:
        """
        모든 보유 종목 조회

        Returns:
            보유 종목 리스트
        """
        return list(self.holdings.values())

    def add_holding(self, stock_code: str, stock_name: str, quantity: int,
                   avg_price: float) -> None:
        """
        보유 종목 추가 (매수 후)

        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            quantity: 수량
            avg_price: 평균 단가
        """
        if stock_code in self.holdings:
            # 기존 보유 종목에 추가 매수
            existing = self.holdings[stock_code]
            total_qty = existing['quantity'] + quantity
            total_cost = existing['avg_price'] * existing['quantity'] + avg_price * quantity
            new_avg_price = total_cost / total_qty

            existing['quantity'] = total_qty
            existing['avg_price'] = new_avg_price
            existing['entry_price'] = new_avg_price
        else:
            # 신규 매수
            self.holdings[stock_code] = {
                'stock_name': stock_name,
                'name': stock_name,
                'quantity': quantity,
                'avg_price': avg_price,
                'entry_price': avg_price,
                'current_price': avg_price,
                'profit_rate': 0.0,
                'eval_amount': avg_price * quantity,
                'entry_date': datetime.now()
            }

    def remove_holding(self, stock_code: str, quantity: int = None) -> None:
        """
        보유 종목 제거 또는 수량 감소 (매도 후)

        Args:
            stock_code: 종목 코드
            quantity: 감소할 수량 (None이면 전량 제거)
        """
        if stock_code not in self.holdings:
            return

        if quantity is None:
            # 전량 매도
            del self.holdings[stock_code]
        else:
            # 부분 매도
            self.holdings[stock_code]['quantity'] -= quantity

            if self.holdings[stock_code]['quantity'] <= 0:
                del self.holdings[stock_code]

    def update_cash(self, amount: float) -> None:
        """
        현금 잔고 업데이트 (매수/매도 후)

        Args:
            amount: 변경 금액 (매수: 음수, 매도: 양수)
        """
        self.current_cash += amount
        self.total_assets = self.current_cash + self.positions_value

    def get_total_assets(self) -> float:
        """
        총 자산 조회

        Returns:
            총 자산 (원)
        """
        return self.total_assets

    def get_positions_value(self) -> float:
        """
        보유 종목 평가액 조회

        Returns:
            평가액 (원)
        """
        return self.positions_value
