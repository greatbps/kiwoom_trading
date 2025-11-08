#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backtest_with_ranker.py

백테스트 + Ranker 학습 데이터 수집

Flow:
1. 조건검색 실행
2. VWAP 필터 적용
3. Feature 수집 (백테스트 진입 시점)
4. 시뮬레이션 (가상 매매)
5. 결과 저장 → Ranker 학습 데이터
"""

import asyncio
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any

import pandas as pd
import numpy as np

from rich.console import Console
from rich.table import Table
from rich.progress import Progress

console = Console()
logger = logging.getLogger(__name__)


class BacktestRunner:
    """
    백테스트 실행 + Ranker 학습 데이터 생성
    """

    def __init__(
        self,
        output_dir: str = "./backtest_results",
        lookback_days: int = 30,
        use_real_data: bool = False,
        api_client=None
    ):
        """
        Args:
            output_dir: 백테스트 결과 저장 디렉토리
            lookback_days: 과거 N일 데이터 사용
            use_real_data: 실제 키움 API 데이터 사용 여부
            api_client: KiwoomRESTClient 인스턴스 (use_real_data=True일 때 필요)
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.lookback_days = lookback_days
        self.use_real_data = use_real_data
        self.api_client = api_client

        if use_real_data and api_client is None:
            raise ValueError("use_real_data=True일 때 api_client는 필수입니다")

    async def run_backtest(
        self,
        candidates: pd.DataFrame,
        holding_period: int = 5,  # 보유 기간 (일)
        take_profit_pct: float = 3.0,  # 익절 (%)
        stop_loss_pct: float = -2.0   # 손절 (%)
    ) -> Dict[str, Any]:
        """
        백테스트 실행

        Args:
            candidates: 1차(조건검색) + 2차(VWAP) 통과 종목
                       컬럼: code, name, entry_price, vwap, volume, ...
            holding_period: 보유 기간
            take_profit_pct: 익절 기준
            stop_loss_pct: 손절 기준

        Returns:
            백테스트 결과 (trades 포함)
        """
        console.print("\n[bold cyan]🔬 백테스트 실행 중...[/bold cyan]")

        trades = []

        with Progress() as progress:
            task = progress.add_task(
                "[cyan]시뮬레이션...",
                total=len(candidates)
            )

            for idx, stock in candidates.iterrows():
                # Feature 수집 (진입 시점)
                entry_features = self._extract_entry_features(stock)

                # 시뮬레이션 (과거 데이터 기반)
                trade_result = await self._simulate_trade(
                    stock_code=stock['code'],
                    stock_name=stock.get('name', stock['code']),
                    entry_price=stock['entry_price'],
                    entry_features=entry_features,
                    holding_period=holding_period,
                    take_profit_pct=take_profit_pct,
                    stop_loss_pct=stop_loss_pct
                )

                trades.append(trade_result)
                progress.advance(task)

        # 결과 분석
        results = self._analyze_results(trades)

        # 저장
        self._save_results(results)

        return results

    def _extract_entry_features(self, stock: pd.Series) -> Dict[str, float]:
        """
        진입 시점의 Feature 추출

        Args:
            stock: 종목 정보 (Series)

        Returns:
            Feature Dictionary
        """
        # 백테스트 통계 (이미 계산되어 있다고 가정)
        vwap_backtest_winrate = stock.get('vwap_backtest_winrate', 0.5) or 0.5
        vwap_avg_profit = stock.get('vwap_avg_profit', 0.0) or 0.0

        # VWAP 괴리율
        entry_price = stock.get('entry_price', 0) or 0
        vwap = stock.get('vwap', entry_price)
        # vwap이 None이거나 0일 때 안전 처리
        if vwap is None or vwap == 0:
            vwap = entry_price if entry_price > 0 else 1
        current_vwap_distance = (entry_price - vwap) / vwap * 100 if vwap > 0 else 0

        # 거래량 Z-score
        volume = stock.get('volume', 0) or 0
        volume_avg = stock.get('volume_avg_20d', volume) or volume
        volume_std = stock.get('volume_std_20d', 1) or 1
        volume_z_score = (volume - volume_avg) / (volume_std + 1e-9)

        # 최근 수익률
        recent_return_5d = stock.get('recent_return_5d', 0.0) or 0.0

        # 시장 변동성 (KOSPI)
        market_volatility = stock.get('market_volatility', 15.0) or 15.0

        # 업종 강도
        sector_strength = stock.get('sector_strength', 0.0) or 0.0

        # 가격 모멘텀
        price_momentum = stock.get('price_momentum', 0.0) or 0.0

        return {
            'vwap_backtest_winrate': vwap_backtest_winrate,
            'vwap_avg_profit': vwap_avg_profit,
            'current_vwap_distance': current_vwap_distance,
            'volume_z_score': volume_z_score,
            'recent_return_5d': recent_return_5d,
            'market_volatility': market_volatility,
            'sector_strength': sector_strength,
            'price_momentum': price_momentum,
        }

    async def _simulate_trade(
        self,
        stock_code: str,
        stock_name: str,
        entry_price: float,
        entry_features: Dict[str, float],
        holding_period: int,
        take_profit_pct: float,
        stop_loss_pct: float
    ) -> Dict[str, Any]:
        """
        단일 종목 거래 시뮬레이션

        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            entry_price: 진입가
            entry_features: 진입 시점 Feature
            holding_period: 보유 기간
            take_profit_pct: 익절 기준
            stop_loss_pct: 손절 기준

        Returns:
            거래 결과
        """
        # 실제 키움 API 데이터 사용 여부에 따라 분기
        if self.use_real_data and self.api_client:
            return await self._simulate_trade_with_real_data(
                stock_code, stock_name, entry_price, entry_features,
                holding_period, take_profit_pct, stop_loss_pct
            )
        else:
            return await self._simulate_trade_with_mock_data(
                stock_code, stock_name, entry_price, entry_features,
                holding_period, take_profit_pct, stop_loss_pct
            )

    async def _simulate_trade_with_real_data(
        self,
        stock_code: str,
        stock_name: str,
        entry_price: float,
        entry_features: Dict[str, float],
        holding_period: int,
        take_profit_pct: float,
        stop_loss_pct: float
    ) -> Dict[str, Any]:
        """실제 키움 API 데이터로 시뮬레이션"""
        try:
            # 진입일 (과거 데이터)
            entry_date = datetime.now() - timedelta(days=holding_period + 10)
            end_date = entry_date + timedelta(days=holding_period + 5)

            # 과거 차트 데이터 조회
            chart_data = await self.api_client.get_historical_data_for_backtest(
                stock_code=stock_code,
                start_date=entry_date,
                end_date=end_date,
                interval='D'  # 일봉
            )

            if not chart_data or len(chart_data) < holding_period:
                # 데이터 부족 시 mock 데이터로 폴백
                logger.warning(f"{stock_code}: 실제 데이터 부족, mock 데이터 사용")
                return await self._simulate_trade_with_mock_data(
                    stock_code, stock_name, entry_price, entry_features,
                    holding_period, take_profit_pct, stop_loss_pct
                )

            # 실제 가격 변동으로 시뮬레이션
            exit_reason = "holding_period"
            exit_day = holding_period
            exit_price = entry_price

            for day_idx, day_data in enumerate(chart_data[:holding_period], 1):
                # 키움 API 응답 필드명에 맞게 수정 필요 (stk_close_prc 등)
                current_price = float(day_data.get('stk_close_prc', day_data.get('close', entry_price)))
                profit_pct = (current_price - entry_price) / entry_price * 100

                # 익절 체크
                if profit_pct >= take_profit_pct:
                    exit_reason = "take_profit"
                    exit_day = day_idx
                    exit_price = current_price
                    break

                # 손절 체크
                if profit_pct <= stop_loss_pct:
                    exit_reason = "stop_loss"
                    exit_day = day_idx
                    exit_price = current_price
                    break

                exit_price = current_price

            final_profit_pct = (exit_price - entry_price) / entry_price * 100

            return {
                'symbol': stock_code,
                'name': stock_name,
                'entry_date': entry_date.strftime('%Y-%m-%d'),
                'exit_date': (entry_date + timedelta(days=exit_day)).strftime('%Y-%m-%d'),
                'entry_price': entry_price,
                'exit_price': exit_price,
                'profit_pct': final_profit_pct,
                'exit_reason': exit_reason,
                'holding_days': exit_day,
                'entry_features': entry_features,
            }

        except Exception as e:
            logger.error(f"{stock_code} 실제 데이터 시뮬레이션 실패: {e}")
            # 에러 시 mock 데이터로 폴백
            return await self._simulate_trade_with_mock_data(
                stock_code, stock_name, entry_price, entry_features,
                holding_period, take_profit_pct, stop_loss_pct
            )

    async def _simulate_trade_with_mock_data(
        self,
        stock_code: str,
        stock_name: str,
        entry_price: float,
        entry_features: Dict[str, float],
        holding_period: int,
        take_profit_pct: float,
        stop_loss_pct: float
    ) -> Dict[str, Any]:
        """Mock 데이터로 시뮬레이션 (랜덤 워크)"""
        # 가격 변동 시뮬레이션 (랜덤 워크)
        returns = np.random.normal(
            entry_features['vwap_avg_profit'] / 100,  # 평균
            0.02,  # 표준편차 2%
            holding_period
        )

        cumulative_return = 0
        exit_reason = "holding_period"
        exit_day = holding_period

        for day, ret in enumerate(returns, 1):
            cumulative_return += ret

            # 익절 체크
            if cumulative_return * 100 >= take_profit_pct:
                exit_reason = "take_profit"
                exit_day = day
                break

            # 손절 체크
            if cumulative_return * 100 <= stop_loss_pct:
                exit_reason = "stop_loss"
                exit_day = day
                break

        profit_pct = cumulative_return * 100

        entry_date = datetime.now() - timedelta(days=holding_period)
        exit_date = entry_date + timedelta(days=exit_day)

        return {
            'symbol': stock_code,
            'name': stock_name,
            'entry_date': entry_date.strftime('%Y-%m-%d'),
            'exit_date': exit_date.strftime('%Y-%m-%d'),
            'entry_price': entry_price,
            'exit_price': entry_price * (1 + cumulative_return),
            'profit_pct': profit_pct,
            'exit_reason': exit_reason,
            'holding_days': exit_day,
            'entry_features': entry_features,
        }

    def _analyze_results(self, trades: List[Dict]) -> Dict[str, Any]:
        """
        백테스트 결과 분석

        Args:
            trades: 거래 리스트

        Returns:
            분석 결과
        """
        if not trades:
            return {
                'date': datetime.now().strftime('%Y-%m-%d'),
                'trades': [],
                'summary': {
                    'total_trades': 0,
                    'win_rate': 0.0,
                    'avg_profit': 0.0,
                }
            }

        df = pd.DataFrame(trades)

        total_trades = len(df)
        winning_trades = (df['profit_pct'] > 0).sum()
        win_rate = winning_trades / total_trades if total_trades > 0 else 0
        avg_profit = df['profit_pct'].mean()
        avg_win = df[df['profit_pct'] > 0]['profit_pct'].mean() if winning_trades > 0 else 0
        avg_loss = df[df['profit_pct'] < 0]['profit_pct'].mean() if (total_trades - winning_trades) > 0 else 0

        summary = {
            'total_trades': total_trades,
            'winning_trades': winning_trades,
            'losing_trades': total_trades - winning_trades,
            'win_rate': f"{win_rate * 100:.1f}%",
            'avg_profit': f"{avg_profit:.2f}%",
            'avg_win': f"{avg_win:.2f}%",
            'avg_loss': f"{avg_loss:.2f}%",
            'max_profit': f"{df['profit_pct'].max():.2f}%",
            'max_loss': f"{df['profit_pct'].min():.2f}%",
        }

        return {
            'date': datetime.now().strftime('%Y-%m-%d'),
            'trades': trades,
            'summary': summary,
        }

    def _save_results(self, results: Dict[str, Any]):
        """
        백테스트 결과 저장

        Args:
            results: 백테스트 결과
        """
        filename = f"backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        filepath = self.output_dir / filename

        # numpy int64 → int 변환
        def convert_types(obj):
            if isinstance(obj, np.integer):
                return int(obj)
            elif isinstance(obj, np.floating):
                return float(obj)
            elif isinstance(obj, np.ndarray):
                return obj.tolist()
            elif isinstance(obj, dict):
                return {k: convert_types(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_types(i) for i in obj]
            return obj

        results_clean = convert_types(results)

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(results_clean, f, indent=2, ensure_ascii=False)

        console.print(f"\n[green]✅ 백테스트 결과 저장: {filepath}[/green]")

    def display_results(self, results: Dict[str, Any]):
        """
        백테스트 결과 출력

        Args:
            results: 백테스트 결과
        """
        summary = results['summary']

        console.print("\n" + "=" * 70)
        console.print("[bold cyan]📊 백테스트 결과 요약[/bold cyan]")
        console.print("=" * 70)

        table = Table(show_header=False, box=None)
        table.add_column("항목", style="cyan")
        table.add_column("값", style="white")

        table.add_row("총 거래", str(summary['total_trades']))
        table.add_row("승리", f"{summary['winning_trades']} 건")
        table.add_row("패배", f"{summary['losing_trades']} 건")
        table.add_row("승률", summary['win_rate'])
        table.add_row("평균 수익률", summary['avg_profit'])
        table.add_row("평균 승리", summary['avg_win'])
        table.add_row("평균 손실", summary['avg_loss'])
        table.add_row("최대 수익", summary['max_profit'])
        table.add_row("최대 손실", summary['max_loss'])

        console.print(table)
        console.print("=" * 70)


async def main():
    """테스트 실행"""
    logging.basicConfig(level=logging.INFO)

    # 샘플 후보 종목 (1차+2차 필터 통과)
    candidates = pd.DataFrame({
        'code': ['005930', '000660', '035420', '035720', '005380'],
        'name': ['삼성전자', 'SK하이닉스', 'NAVER', '카카오', '현대차'],
        'entry_price': [72000, 145000, 210000, 45000, 245000],
        'vwap': [71500, 144000, 212000, 46000, 243000],
        'volume': [1000000, 500000, 300000, 800000, 400000],
        'volume_avg_20d': [800000, 450000, 280000, 700000, 380000],
        'volume_std_20d': [100000, 50000, 30000, 80000, 40000],
        'vwap_backtest_winrate': [0.65, 0.72, 0.58, 0.62, 0.68],
        'vwap_avg_profit': [2.3, 3.1, 1.5, 1.8, 2.5],
        'recent_return_5d': [-1.2, 2.3, -3.5, 0.5, 1.2],
        'market_volatility': [15.3] * 5,
        'sector_strength': [0.8, 1.2, 0.3, 0.5, 0.9],
        'price_momentum': [1.2, 1.8, -0.5, 0.3, 1.0],
    })

    # 백테스트 실행
    runner = BacktestRunner()
    results = await runner.run_backtest(
        candidates,
        holding_period=5,
        take_profit_pct=3.0,
        stop_loss_pct=-2.0
    )

    # 결과 출력
    runner.display_results(results)

    # Ranker 학습 데이터로 사용 가능
    console.print("\n[yellow]💡 이 결과를 Ranker 학습에 사용할 수 있습니다:[/yellow]")
    console.print(f"[dim]   python ml_train_menu.py[/dim]")


if __name__ == "__main__":
    asyncio.run(main())
