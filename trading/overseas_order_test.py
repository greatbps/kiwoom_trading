#!/usr/bin/env python3
"""
해외주식 실거래 테스트
====================

1주 소액 주문으로 전체 플로우 검증

안전장치:
1. 시장시간 체크
2. 1일 최대 주문 횟수 제한
3. 동일 종목 중복 주문 방지
4. 주문 실패 시 자동 재시도 금지
5. REAL_TRADING 스위치
"""

import os
import json
import logging
from datetime import datetime, time as dtime
from typing import Dict, Optional
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# 프로젝트 루트 경로 추가
import sys
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 환경변수 로드
env_path = project_root / '.env'
load_dotenv(dotenv_path=env_path)

console = Console()
logger = logging.getLogger(__name__)

# ============================================================================
# 🔒 안전장치 설정
# ============================================================================

# ⚠️ 실거래 스위치 - False면 주문 전송 안함
REAL_TRADING = False

# 시장시간 체크 우회 (테스트용)
BYPASS_MARKET_HOURS = False

# 1일 최대 주문 횟수
MAX_DAILY_ORDERS = 5

# 진행 중인 주문 추적
open_orders = set()
daily_order_count = 0


# ============================================================================
# 시장 시간 체크
# ============================================================================

def is_us_market_open() -> tuple[bool, str]:
    """
    미국 시장 개장 여부 확인

    정규장: 09:30~16:00 ET (한국시간 23:30~06:00, 썸머타임시 22:30~05:00)

    Returns:
        (개장여부, 상태메시지)
    """
    # 뉴욕 시간대
    ny_tz = ZoneInfo("America/New_York")
    now_ny = datetime.now(ny_tz)

    # 주말 체크
    if now_ny.weekday() >= 5:  # 토(5), 일(6)
        return False, f"주말 휴장 (뉴욕: {now_ny.strftime('%Y-%m-%d %H:%M')})"

    # 정규장 시간 (09:30 ~ 16:00 ET)
    market_open = dtime(9, 30)
    market_close = dtime(16, 0)
    current_time = now_ny.time()

    if market_open <= current_time <= market_close:
        return True, f"정규장 개장 중 (뉴욕: {now_ny.strftime('%H:%M')})"

    # 프리마켓 (04:00 ~ 09:30 ET)
    premarket_open = dtime(4, 0)
    if premarket_open <= current_time < market_open:
        return True, f"프리마켓 (뉴욕: {now_ny.strftime('%H:%M')})"

    # 애프터마켓 (16:00 ~ 20:00 ET)
    aftermarket_close = dtime(20, 0)
    if market_close < current_time <= aftermarket_close:
        return True, f"애프터마켓 (뉴욕: {now_ny.strftime('%H:%M')})"

    return False, f"장외시간 (뉴욕: {now_ny.strftime('%H:%M')})"


# ============================================================================
# 주문 테스트 클래스
# ============================================================================

class OverseasOrderTester:
    """해외주식 주문 테스터"""

    def __init__(self):
        from korea_invest_api import KoreaInvestAPI

        self.api = KoreaInvestAPI()
        self.order_log = []

        # 로그 파일
        log_dir = Path(__file__).parent.parent / 'logs'
        log_dir.mkdir(exist_ok=True)
        self.log_file = log_dir / f"overseas_order_{datetime.now().strftime('%Y%m%d')}.json"

    def initialize(self) -> bool:
        """API 초기화"""
        token = self.api.get_access_token()
        return token is not None

    def get_current_price(self, symbol: str, exchange: str = "NAS") -> Optional[float]:
        """현재가 조회 (API 실패 시 yfinance fallback)"""
        result = self.api.get_overseas_price(symbol, exchange)
        if result['success'] and result.get('price'):
            return result['price']
        # fallback: yfinance (장외시간, API 오류 시)
        try:
            import yfinance as yf
            info = yf.Ticker(symbol).info
            price = info.get('regularMarketPrice') or info.get('currentPrice')
            if not price:
                hist = yf.Ticker(symbol).history(period='2d')
                price = float(hist['Close'].iloc[-1]) if not hist.empty else None
            if price:
                console.print(f"   [dim](yfinance fallback: ${float(price):.2f})[/dim]")
                return float(price)
        except Exception as e:
            logger.debug(f"yfinance fallback 실패 ({symbol}): {e}")
        return None

    def log_order(self, order_info: Dict):
        """주문 로그 저장"""
        self.order_log.append(order_info)

        # JSON 파일에 추가
        try:
            if self.log_file.exists():
                with open(self.log_file, 'r') as f:
                    logs = json.load(f)
            else:
                logs = []

            logs.append(order_info)

            with open(self.log_file, 'w') as f:
                json.dump(logs, f, indent=2, ensure_ascii=False, default=str)

        except Exception as e:
            logger.error(f"로그 저장 실패: {e}")

    def test_buy_order(
        self,
        symbol: str,
        qty: int = 1,
        price_offset_pct: float = -0.5  # 현재가 대비 % (음수=낮게)
    ) -> Dict:
        """
        매수 주문 테스트

        Args:
            symbol: 종목코드 (SOFI, ROBO 등)
            qty: 수량 (기본 1주)
            price_offset_pct: 현재가 대비 가격 오프셋 %

        Returns:
            주문 결과
        """
        global daily_order_count

        console.print()
        console.print("=" * 60)
        console.print(f"[bold cyan]📤 해외주식 매수 테스트: {symbol}[/bold cyan]")
        console.print("=" * 60)

        # ─────────────────────────────────────────────────────────
        # 안전장치 1: 시장시간 체크
        # ─────────────────────────────────────────────────────────
        is_open, market_status = is_us_market_open()
        console.print(f"\n🕐 시장 상태: {market_status}")

        if not is_open:
            if BYPASS_MARKET_HOURS:
                console.print("[yellow]⚠️ 장외시간이지만 BYPASS_MARKET_HOURS=True로 진행[/yellow]")
            else:
                console.print("[yellow]⚠️ 장외시간 - 주문 불가[/yellow]")
                return {'success': False, 'reason': 'market_closed', 'status': market_status}

        # ─────────────────────────────────────────────────────────
        # 안전장치 2: 1일 최대 주문 횟수
        # ─────────────────────────────────────────────────────────
        if daily_order_count >= MAX_DAILY_ORDERS:
            console.print(f"[red]❌ 1일 최대 주문 횟수 초과 ({MAX_DAILY_ORDERS}회)[/red]")
            return {'success': False, 'reason': 'daily_limit_exceeded'}

        # ─────────────────────────────────────────────────────────
        # 안전장치 3: 동일 종목 중복 주문 방지
        # ─────────────────────────────────────────────────────────
        if symbol in open_orders:
            console.print(f"[red]❌ 이미 진행 중인 주문 있음: {symbol}[/red]")
            return {'success': False, 'reason': 'duplicate_order'}

        # ─────────────────────────────────────────────────────────
        # 현재가 조회
        # ─────────────────────────────────────────────────────────
        console.print(f"\n📊 현재가 조회 중...")
        current_price = self.get_current_price(symbol)

        if not current_price:
            console.print("[red]❌ 현재가 조회 실패[/red]")
            return {'success': False, 'reason': 'price_fetch_failed'}

        console.print(f"   현재가: ${current_price:.2f}")

        # 주문 가격 계산 (현재가 ± offset%)
        order_price = round(current_price * (1 + price_offset_pct / 100), 2)
        console.print(f"   주문가: ${order_price:.2f} ({price_offset_pct:+.1f}%)")

        # ─────────────────────────────────────────────────────────
        # 주문 정보 구성
        # ─────────────────────────────────────────────────────────
        order_info = {
            'timestamp': datetime.now().isoformat(),
            'action': 'BUY',
            'symbol': symbol,
            'qty': qty,
            'current_price': current_price,
            'order_price': order_price,
            'price_offset_pct': price_offset_pct,
            'market_status': market_status,
            'real_trading': REAL_TRADING
        }

        console.print(f"\n📋 주문 요청:")
        console.print(f"   종목: {symbol}")
        console.print(f"   수량: {qty}주")
        console.print(f"   가격: ${order_price:.2f}")
        console.print(f"   예상금액: ${order_price * qty:.2f}")

        # ─────────────────────────────────────────────────────────
        # 안전장치 5: REAL_TRADING 스위치
        # ─────────────────────────────────────────────────────────
        if not REAL_TRADING:
            console.print("\n[yellow]🔒 REAL_TRADING=False - 시뮬레이션 모드[/yellow]")
            console.print("[dim]실제 주문이 전송되지 않았습니다.[/dim]")

            order_info['status'] = 'simulated'
            order_info['order_no'] = 'SIM_' + datetime.now().strftime('%H%M%S')
            self.log_order(order_info)

            return {
                'success': True,
                'simulated': True,
                'order_info': order_info
            }

        # ─────────────────────────────────────────────────────────
        # 실제 주문 전송
        # ─────────────────────────────────────────────────────────
        console.print("\n[bold red]🚀 실제 주문 전송 중...[/bold red]")

        # 중복 주문 방지용 플래그
        open_orders.add(symbol)

        try:
            result = self.api.order_overseas_stock(
                symbol=symbol,
                side="BUY",
                qty=qty,
                price=order_price,
                exchange="NASD"
            )

            # 응답 기록
            order_info['api_response'] = result
            order_info['status'] = 'success' if result['success'] else 'failed'

            if result['success']:
                order_info['order_no'] = result.get('order_no', '')
                order_info['order_time'] = result.get('order_time', '')

                daily_order_count += 1

                console.print(f"\n[green]✅ 주문 성공![/green]")
                console.print(f"   주문번호: {order_info['order_no']}")
                console.print(f"   주문시간: {order_info['order_time']}")

            else:
                order_info['error'] = result.get('error', '')
                order_info['error_code'] = result.get('code', '')

                console.print(f"\n[red]❌ 주문 실패[/red]")
                console.print(f"   에러: {order_info['error']}")
                console.print(f"   코드: {order_info['error_code']}")

                # ─────────────────────────────────────────────────
                # 안전장치 4: 자동 재시도 금지
                # ─────────────────────────────────────────────────
                console.print("\n[yellow]⚠️ 자동 재시도 하지 않음 (안전장치)[/yellow]")

        except Exception as e:
            order_info['status'] = 'exception'
            order_info['error'] = str(e)
            console.print(f"\n[red]❌ 예외 발생: {e}[/red]")

        finally:
            # 주문 완료 후 플래그 해제
            open_orders.discard(symbol)

        # 로그 저장
        self.log_order(order_info)

        console.print(f"\n📁 로그 저장: {self.log_file}")

        return {
            'success': order_info.get('status') == 'success',
            'order_info': order_info
        }

    def test_sell_order(
        self,
        symbol: str,
        qty: int = 1,
        price_offset_pct: float = 0.5  # 현재가 대비 % (양수=높게)
    ) -> Dict:
        """
        매도 주문 테스트

        Args:
            symbol: 종목코드
            qty: 수량 (기본 1주)
            price_offset_pct: 현재가 대비 가격 오프셋 %
        """
        global daily_order_count

        console.print()
        console.print("=" * 60)
        console.print(f"[bold magenta]📥 해외주식 매도 테스트: {symbol}[/bold magenta]")
        console.print("=" * 60)

        # 안전장치 1: 시장시간
        is_open, market_status = is_us_market_open()
        console.print(f"\n🕐 시장 상태: {market_status}")
        if not is_open:
            if BYPASS_MARKET_HOURS:
                console.print("[yellow]⚠️ 장외시간이지만 BYPASS_MARKET_HOURS=True로 진행[/yellow]")
            else:
                console.print("[yellow]⚠️ 장외시간 - 주문 불가[/yellow]")
                return {'success': False, 'reason': 'market_closed', 'status': market_status}

        # 안전장치 2: 일일 한도
        if daily_order_count >= MAX_DAILY_ORDERS:
            console.print(f"[red]❌ 1일 최대 주문 횟수 초과 ({MAX_DAILY_ORDERS}회)[/red]")
            return {'success': False, 'reason': 'daily_limit_exceeded'}

        # 안전장치 3: 중복 주문
        if symbol in open_orders:
            console.print(f"[red]❌ 이미 진행 중인 주문 있음: {symbol}[/red]")
            return {'success': False, 'reason': 'duplicate_order'}

        # 현재가 조회
        console.print(f"\n📊 현재가 조회 중...")
        current_price = self.get_current_price(symbol)
        if not current_price:
            # fallback: yfinance
            try:
                import yfinance as yf
                hist = yf.Ticker(symbol).history(period='2d')
                current_price = float(hist['Close'].iloc[-1]) if not hist.empty else None
            except Exception:
                pass
        if not current_price:
            console.print("[red]❌ 현재가 조회 실패[/red]")
            return {'success': False, 'reason': 'price_fetch_failed'}

        console.print(f"   현재가: ${current_price:.2f}")
        order_price = round(current_price * (1 + price_offset_pct / 100), 2)
        console.print(f"   주문가: ${order_price:.2f} ({price_offset_pct:+.1f}%)")

        order_info = {
            'timestamp': datetime.now().isoformat(),
            'action': 'SELL',
            'symbol': symbol,
            'qty': qty,
            'current_price': current_price,
            'order_price': order_price,
            'price_offset_pct': price_offset_pct,
            'market_status': market_status,
            'real_trading': REAL_TRADING
        }

        console.print(f"\n📋 주문 요청:")
        console.print(f"   종목: {symbol}")
        console.print(f"   수량: {qty}주")
        console.print(f"   가격: ${order_price:.2f}")
        console.print(f"   예상금액: ${order_price * qty:.2f}")

        # 안전장치 5: REAL_TRADING 스위치
        if not REAL_TRADING:
            console.print("\n[yellow]🔒 REAL_TRADING=False - 시뮬레이션 모드[/yellow]")
            console.print("[dim]실제 주문이 전송되지 않았습니다.[/dim]")
            order_info['status'] = 'simulated'
            order_info['order_no'] = 'SIM_' + datetime.now().strftime('%H%M%S')
            self.log_order(order_info)
            return {'success': True, 'simulated': True, 'order_info': order_info}

        # 실제 매도 주문 전송
        console.print("\n[bold red]🚀 실제 매도 주문 전송 중...[/bold red]")
        open_orders.add(symbol)
        try:
            result = self.api.order_overseas_stock(
                symbol=symbol,
                side="SELL",
                qty=qty,
                price=order_price,
                exchange="AMEX"
            )
            order_info['api_response'] = result
            order_info['status'] = 'success' if result['success'] else 'failed'
            if result['success']:
                order_info['order_no'] = result.get('order_no', '')
                order_info['order_time'] = result.get('order_time', '')
                daily_order_count += 1
                console.print(f"\n[green]✅ 매도 주문 성공![/green]")
                console.print(f"   주문번호: {order_info['order_no']}")
                console.print(f"   주문시간: {order_info['order_time']}")
            else:
                order_info['error'] = result.get('error', '')
                order_info['error_code'] = result.get('code', '')
                console.print(f"\n[red]❌ 주문 실패: {order_info['error']}[/red]")
                console.print("[yellow]⚠️ 자동 재시도 하지 않음[/yellow]")
        except Exception as e:
            order_info['status'] = 'exception'
            order_info['error'] = str(e)
            console.print(f"\n[red]❌ 예외 발생: {e}[/red]")
        finally:
            open_orders.discard(symbol)

        self.log_order(order_info)
        console.print(f"\n📁 로그 저장: {self.log_file}")
        return {'success': order_info.get('status') == 'success', 'order_info': order_info}

    def check_order_status(self, order_no: str) -> Dict:
        """
        주문 체결 상태 확인

        Args:
            order_no: 주문번호

        Returns:
            체결 상태
        """
        # TODO: 미체결 조회 API 구현
        console.print(f"\n📋 주문 상태 확인: {order_no}")
        console.print("[yellow]미구현 - 미체결내역 API 필요[/yellow]")

        return {'status': 'pending'}

    def show_current_holdings(self):
        """현재 보유 현황 표시"""
        console.print("\n📊 현재 해외주식 보유현황")
        console.print("-" * 40)

        result = self.api.get_overseas_balance()

        if result['success']:
            holdings = result['data']

            if holdings:
                table = Table()
                table.add_column("종목", style="cyan")
                table.add_column("수량", justify="right")
                table.add_column("평균가", justify="right")
                table.add_column("현재가", justify="right")
                table.add_column("수익률", justify="right")
                table.add_column("평가손익", justify="right")

                for h in holdings:
                    profit_pct = float(h.get('evlu_pfls_rt', 0))
                    profit_style = "green" if profit_pct >= 0 else "red"

                    table.add_row(
                        h.get('ovrs_pdno', ''),
                        h.get('ovrs_cblc_qty', '0'),
                        f"${float(h.get('pchs_avg_pric', 0)):.2f}",
                        f"${float(h.get('now_pric2', 0)):.2f}",
                        f"[{profit_style}]{profit_pct:+.2f}%[/{profit_style}]",
                        f"${float(h.get('ovrs_stck_evlu_pfls_amt', 0)):.2f}"
                    )

                console.print(table)
            else:
                console.print("보유 종목 없음")
        else:
            console.print(f"[red]조회 실패: {result.get('error')}[/red]")


# ============================================================================
# 메인 실행
# ============================================================================

def main():
    """메인 테스트 함수"""
    console.print()
    console.print(Panel(
        "[bold]해외주식 실거래 테스트[/bold]\n\n"
        f"REAL_TRADING = [{'red' if REAL_TRADING else 'yellow'}]{REAL_TRADING}[/]\n"
        f"BYPASS_MARKET_HOURS = [{'yellow' if BYPASS_MARKET_HOURS else 'green'}]{BYPASS_MARKET_HOURS}[/]\n"
        f"MAX_DAILY_ORDERS = {MAX_DAILY_ORDERS}",
        title="⚠️ 설정 확인",
        border_style="yellow" if not REAL_TRADING else "red"
    ))

    # 테스터 초기화
    tester = OverseasOrderTester()

    if not tester.initialize():
        console.print("[red]❌ API 초기화 실패[/red]")
        return

    # 현재 보유현황 표시
    tester.show_current_holdings()

    # 시장 상태 확인
    is_open, status = is_us_market_open()
    console.print(f"\n🕐 미국 시장: {status}")

    # 테스트 주문 실행 (SOFI 1주)
    console.print("\n" + "=" * 60)
    console.print("[bold]테스트 주문 실행[/bold]")
    console.print("=" * 60)

    result = tester.test_buy_order(
        symbol="SOFI",
        qty=1,
        price_offset_pct=-0.5  # 현재가보다 0.5% 낮게
    )

    # 결과 요약
    console.print("\n" + "=" * 60)
    console.print("[bold]테스트 결과 요약[/bold]")
    console.print("=" * 60)

    if result.get('simulated'):
        console.print("[yellow]시뮬레이션 모드로 실행됨[/yellow]")
        console.print("실제 주문을 하려면 REAL_TRADING = True 로 변경하세요")
    elif result.get('success'):
        console.print("[green]✅ 주문 성공[/green]")
    else:
        console.print(f"[red]❌ 주문 실패: {result.get('order_info', {}).get('error', 'Unknown')}[/red]")


if __name__ == "__main__":
    main()
