#!/usr/bin/env python3
"""
듀얼 계좌 통합 자동매매 시스템
==============================

단기 계좌 (5765-7162): 스캘핑/단타 자동매매
중기 계좌 (5202-2235): 중기 보유 종목 모니터링 + 청산 시그널

Usage:
    python main_dual_account.py              # 통합 실행
    python main_dual_account.py --test       # 테스트 모드
"""

import os
import sys
import time
import logging
import argparse
import schedule
from datetime import datetime, time as dtime
from typing import Dict, List, Optional
from threading import Thread, Event
from pathlib import Path
from dotenv import load_dotenv

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.layout import Layout
from rich.live import Live

# 환경변수 로드
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

# 로깅 설정
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(f'logs/dual_account_{datetime.now().strftime("%Y%m%d_%H%M%S")}.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

console = Console()

# 계좌 정보
SHORT_TERM_ACCOUNT = os.getenv('KIWOOM_ACCOUNT', '5765-7162')
TREND_ACCOUNT = os.getenv('KIWOOM_TREND_ACCOUNT', '5202-2235')


class DualAccountTradingSystem:
    """
    듀얼 계좌 통합 트레이딩 시스템

    - 단기 계좌: 자동매매 (기존 로직)
    - 중기 계좌: 모니터링 + 청산 시그널
    """

    def __init__(self):
        self.short_term_account = SHORT_TERM_ACCOUNT
        self.trend_account = TREND_ACCOUNT

        # API 인스턴스
        self.short_api = None
        self.trend_api = None

        # 모니터링 데이터
        self.short_holdings = []
        self.trend_holdings = []
        self.short_signals = []
        self.trend_exit_signals = []

        # 상태
        self.is_running = False
        self.stop_event = Event()
        self.last_update = None

        # 통계
        self.stats = {
            'short_total_invested': 0,
            'short_total_eval': 0,
            'short_profit_pct': 0,
            'trend_total_invested': 0,
            'trend_total_eval': 0,
            'trend_profit_pct': 0,
        }

        logger.info("듀얼 계좌 시스템 초기화")
        logger.info(f"  단기 계좌: {self.short_term_account}")
        logger.info(f"  중기 계좌: {self.trend_account}")

    def initialize(self) -> bool:
        """API 초기화"""
        try:
            from kiwoom_api import KiwoomAPI

            # 단기 계좌 API
            console.print(f"[cyan]단기 계좌 연결 중... ({self.short_term_account})[/cyan]")
            self.short_api = KiwoomAPI(account_number=self.short_term_account)
            token1 = self.short_api.get_access_token()
            if not token1:
                console.print("[red]❌ 단기 계좌 토큰 발급 실패[/red]")
                return False
            console.print(f"[green]✅ 단기 계좌 연결 성공[/green]")

            # 중기 계좌 API
            console.print(f"[cyan]중기 계좌 연결 중... ({self.trend_account})[/cyan]")
            self.trend_api = KiwoomAPI(account_number=self.trend_account)
            token2 = self.trend_api.get_access_token()
            if not token2:
                console.print("[red]❌ 중기 계좌 토큰 발급 실패[/red]")
                return False
            console.print(f"[green]✅ 중기 계좌 연결 성공[/green]")

            return True

        except Exception as e:
            logger.error(f"초기화 실패: {e}")
            console.print(f"[red]❌ 초기화 실패: {e}[/red]")
            return False

    def fetch_short_term_holdings(self):
        """단기 계좌 보유종목 조회"""
        if not self.short_api:
            return

        try:
            result = self.short_api.get_account_info()
            if result.get('return_code') == 0:
                self.short_holdings = result.get('data', [])
                self._calculate_short_stats()
        except Exception as e:
            logger.error(f"단기 계좌 조회 실패: {e}")

    def fetch_trend_holdings(self):
        """중기 계좌 보유종목 조회"""
        if not self.trend_api:
            return

        try:
            result = self.trend_api.get_account_info()
            if result.get('return_code') == 0:
                self.trend_holdings = result.get('data', [])
                self._calculate_trend_stats()
        except Exception as e:
            logger.error(f"중기 계좌 조회 실패: {e}")

    def _calculate_short_stats(self):
        """단기 계좌 통계"""
        total_invested = 0
        total_eval = 0

        for h in self.short_holdings:
            qty = int(h.get('hold_qty', h.get('quantity', 0)))
            avg = float(h.get('avg_buy_price', h.get('avg_price', 0)))
            cur = float(h.get('cur_price', h.get('current_price', 0)))
            total_invested += qty * avg
            total_eval += qty * cur

        self.stats['short_total_invested'] = total_invested
        self.stats['short_total_eval'] = total_eval
        self.stats['short_profit_pct'] = ((total_eval - total_invested) / total_invested * 100) if total_invested > 0 else 0

    def _calculate_trend_stats(self):
        """중기 계좌 통계"""
        total_invested = 0
        total_eval = 0

        for h in self.trend_holdings:
            qty = int(h.get('hold_qty', h.get('quantity', 0)))
            avg = float(h.get('avg_buy_price', h.get('avg_price', 0)))
            cur = float(h.get('cur_price', h.get('current_price', 0)))
            total_invested += qty * avg
            total_eval += qty * cur

        self.stats['trend_total_invested'] = total_invested
        self.stats['trend_total_eval'] = total_eval
        self.stats['trend_profit_pct'] = ((total_eval - total_invested) / total_invested * 100) if total_invested > 0 else 0

    def check_trend_exit_signals(self):
        """중기 계좌 청산 시그널 체크"""
        # TODO: Daily Squeeze 데이터 연동 후 구현
        self.trend_exit_signals = []

    def run_short_term_filtering(self):
        """단기 매매 필터링 (08:50) - main_auto_trading 호출"""
        logger.info("=" * 60)
        logger.info("📊 단기 매매 필터링 시작")
        logger.info("=" * 60)

        # 필터링은 main_auto_trading.py의 스케줄러가 처리
        # 여기서는 상태만 업데이트
        self.update_all()
        logger.info("필터링 완료 (main_auto_trading 연동)")

    def run_short_term_trading(self):
        """단기 자동매매 실행 (09:00~15:20) - main_auto_trading 호출"""
        logger.info("=" * 60)
        logger.info("🚀 단기 자동매매 시작")
        logger.info("=" * 60)

        # 매매 실행은 main_auto_trading.py가 별도 프로세스로 처리
        # 여기서는 상태만 업데이트
        self.update_all()
        logger.info("매매 실행 중 (main_auto_trading 연동)")

    def update_all(self):
        """전체 상태 업데이트"""
        self.fetch_short_term_holdings()
        self.fetch_trend_holdings()
        self.check_trend_exit_signals()
        self.last_update = datetime.now()

    def display_status(self):
        """통합 상태 표시"""
        console.clear()

        # 헤더
        console.print(Panel(
            f"[bold white]듀얼 계좌 트레이딩 시스템[/bold white]\n"
            f"[dim]마지막 업데이트: {self.last_update.strftime('%H:%M:%S') if self.last_update else '-'}[/dim]",
            style="blue"
        ))

        # 단기 계좌
        short_style = "green" if self.stats['short_profit_pct'] >= 0 else "red"
        console.print(f"\n[bold cyan]📈 단기 계좌 ({self.short_term_account})[/bold cyan]")
        console.print(f"   보유: {len(self.short_holdings)}종목 | "
                     f"투자: {self.stats['short_total_invested']:,.0f}원 | "
                     f"평가: {self.stats['short_total_eval']:,.0f}원 | "
                     f"수익률: [{short_style}]{self.stats['short_profit_pct']:+.2f}%[/{short_style}]")

        # 중기 계좌
        trend_style = "green" if self.stats['trend_profit_pct'] >= 0 else "red"
        console.print(f"\n[bold yellow]📊 중기 계좌 ({self.trend_account})[/bold yellow]")
        console.print(f"   보유: {len(self.trend_holdings)}종목 | "
                     f"투자: {self.stats['trend_total_invested']:,.0f}원 | "
                     f"평가: {self.stats['trend_total_eval']:,.0f}원 | "
                     f"수익률: [{trend_style}]{self.stats['trend_profit_pct']:+.2f}%[/{trend_style}]")

        # 청산 시그널
        if self.trend_exit_signals:
            console.print(f"\n[bold red]🚨 청산 시그널: {len(self.trend_exit_signals)}건[/bold red]")
            for sig in self.trend_exit_signals:
                console.print(f"   ⚠️ {sig}")

        # 총 합계
        total_invested = self.stats['short_total_invested'] + self.stats['trend_total_invested']
        total_eval = self.stats['short_total_eval'] + self.stats['trend_total_eval']
        total_profit_pct = ((total_eval - total_invested) / total_invested * 100) if total_invested > 0 else 0
        total_style = "green" if total_profit_pct >= 0 else "red"

        console.print(f"\n{'─' * 50}")
        console.print(f"[bold]💰 총 자산: {total_eval:,.0f}원 "
                     f"([{total_style}]{total_profit_pct:+.2f}%[/{total_style}])[/bold]")

    def run_monitoring_loop(self, interval: int = 60):
        """모니터링 루프"""
        while not self.stop_event.is_set():
            self.update_all()
            self.display_status()

            # 청산 시그널 알림
            if self.trend_exit_signals:
                logger.warning(f"🚨 청산 시그널 발생: {len(self.trend_exit_signals)}건")

            console.print(f"\n[dim]다음 갱신: {interval}초 후... (Ctrl+C 종료)[/dim]")
            self.stop_event.wait(interval)

    def run_scheduled(self):
        """스케줄 기반 실행"""
        # 08:50 필터링
        schedule.every().day.at("08:50").do(self.run_short_term_filtering)

        # 09:00 매매 시작
        schedule.every().day.at("09:00").do(self.run_short_term_trading)

        # 1분마다 상태 업데이트
        schedule.every(1).minutes.do(self.update_all)

        logger.info("스케줄 등록 완료")
        logger.info("  - 08:50: 단기 필터링")
        logger.info("  - 09:00: 단기 매매 시작")
        logger.info("  - 1분마다: 상태 업데이트")

        # 초기 상태 표시
        self.update_all()
        self.display_status()

        # 스케줄 실행
        while not self.stop_event.is_set():
            schedule.run_pending()
            time.sleep(1)

    def run(self, mode: str = 'monitor'):
        """
        시스템 실행

        Args:
            mode: 'monitor' (모니터링만) | 'scheduled' (스케줄 매매)
        """
        if not self.initialize():
            return

        self.is_running = True

        console.print()
        console.print("=" * 60)
        console.print(f"[bold]🚀 듀얼 계좌 시스템 시작 (모드: {mode})[/bold]")
        console.print("=" * 60)

        try:
            if mode == 'monitor':
                self.run_monitoring_loop(interval=60)
            elif mode == 'scheduled':
                self.run_scheduled()
            else:
                self.run_monitoring_loop(interval=60)

        except KeyboardInterrupt:
            console.print("\n[yellow]시스템 종료 중...[/yellow]")
        finally:
            self.stop_event.set()
            self.is_running = False
            logger.info("시스템 종료")

    def stop(self):
        """시스템 중지"""
        self.stop_event.set()


def main():
    parser = argparse.ArgumentParser(description='듀얼 계좌 통합 트레이딩 시스템')
    parser.add_argument('--mode', choices=['monitor', 'scheduled'], default='monitor',
                       help='실행 모드 (monitor: 모니터링만, scheduled: 스케줄 매매)')
    parser.add_argument('--test', action='store_true', help='테스트 모드')

    args = parser.parse_args()

    system = DualAccountTradingSystem()

    if args.test:
        # 테스트: 초기화 및 1회 조회
        if system.initialize():
            system.update_all()
            system.display_status()
    else:
        system.run(mode=args.mode)


if __name__ == "__main__":
    main()
