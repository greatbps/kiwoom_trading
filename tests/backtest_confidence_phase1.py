"""
Phase 1 Confidence 백테스트

실제 거래내역 27건에 대해 Confidence 기반 필터링을 재평가하여
약한 신호가 차단되는지 검증
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys
from datetime import datetime, time
from typing import Dict, List

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from analyzers.signal_orchestrator import SignalOrchestrator
from utils.config_loader import ConfigLoader
from rich.console import Console
from rich.table import Table
from rich import box

console = Console()


class ConfidenceBacktester:
    """Confidence 백테스트 엔진"""

    def __init__(self):
        """초기화"""
        # Config 로드
        self.config = ConfigLoader()

        # SignalOrchestrator 생성 (V2 필터 포함)
        self.orchestrator = SignalOrchestrator(self.config, api=None)

        # 종목명 → 종목코드 매핑 (실제 코드)
        self.name_to_code = {
            "한올바이오파마": "009420",
            "태성": "004410",
            "로킷헬스케어": "418550",
            "삼영": "005680",
            "코오롱티슈진": "014680",
            "신테카바이오": "226330",
            "메드팩토": "035430",
            "글로벌텍스프리": "204620",
            "미래컴퍼니": "049950",
            "롯데관광개발": "032350"
        }

    def load_trade_history(self) -> pd.DataFrame:
        """거래내역 27건 로드"""
        csv_path = project_root / "data" / "trade_analysis_detailed.csv"
        df = pd.read_csv(csv_path, encoding='utf-8-sig')

        # 종목코드 추가
        df['종목코드'] = df['종목'].map(self.name_to_code)

        console.print(f"\n✅ 거래내역 로드: {len(df)}건")
        return df

    def generate_mock_ohlcv(
        self,
        stock_code: str,
        stock_name: str,
        entry_price: float,
        profit_pct: float
    ) -> pd.DataFrame:
        """
        Mock OHLCV 데이터 생성 (실제 데이터 없을 때)

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            entry_price: 진입가
            profit_pct: 실제 수익률

        Returns:
            Mock 5분봉 데이터
        """
        # 100개 봉 생성
        n = 100
        df = pd.DataFrame()

        # 가격 시뮬레이션
        base_price = entry_price * 0.98  # 진입 전 가격
        volatility = abs(profit_pct) * 0.5 if profit_pct != 0 else 0.5

        # 랜덤 가격 생성
        np.random.seed(hash(stock_code) % 10000)
        price_changes = np.random.randn(n) * volatility
        prices = base_price * (1 + np.cumsum(price_changes) / 100)

        df['close'] = prices
        df['open'] = prices * (1 + np.random.randn(n) * 0.001)
        df['high'] = df[['open', 'close']].max(axis=1) * (1 + abs(np.random.randn(n)) * 0.002)
        df['low'] = df[['open', 'close']].min(axis=1) * (1 - abs(np.random.randn(n)) * 0.002)
        df['volume'] = np.random.randint(1000, 50000, n)

        # VWAP 계산 (간단 버전)
        df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()

        # ATR 계산
        df['atr'] = (df['high'] - df['low']).rolling(14).mean()

        return df

    def calculate_confidence_for_trade(
        self,
        trade: pd.Series
    ) -> Dict:
        """
        개별 거래에 대한 Confidence 계산

        Args:
            trade: 거래 정보 (종목, 가격, 수익률 등)

        Returns:
            Confidence 평가 결과
        """
        stock_code = trade['종목코드']
        stock_name = trade['종목']
        entry_price = trade['매수가']
        profit_pct = trade['수익률']

        # Mock OHLCV 데이터 생성
        df = self.generate_mock_ohlcv(stock_code, stock_name, entry_price, profit_pct)

        # L0 필터를 우회하기 위해 직접 L3-L6만 평가
        # (백테스트에서는 시간 필터가 의미 없음)
        try:
            from trading.filters.base_filter import FilterResult

            # L3: MTF Consensus
            l3_result = self.orchestrator.mtf_consensus.check_with_confidence(stock_code, 'KOSPI', df)

            # L4: Liquidity Shift
            l4_result = self.orchestrator.liquidity_detector.check_with_confidence(stock_code)

            # L5: Squeeze Momentum
            l5_result = self.orchestrator.squeeze.check_with_confidence(df)

            # L6: Pre-Trade Validator
            l6_result = self.orchestrator.validator.check_with_confidence(
                stock_code=stock_code,
                stock_name=stock_name,
                historical_data=df,
                current_price=entry_price,
                current_time=datetime.now()
            )

            # Confidence 결합
            filter_results = {
                "L3_MTF": l3_result,
                "L4_LIQUIDITY": l4_result if l4_result.passed else FilterResult(True, 0.3, "L4 Default"),
                "L5_SQUEEZE": l5_result if l5_result.passed else FilterResult(True, 0.3, "L5 Default"),
                "L6_VALIDATOR": l6_result
            }

            final_confidence, should_pass, aggregation_reason = \
                self.orchestrator.confidence_aggregator.aggregate(filter_results)

            # 포지션 크기
            position_multiplier = 0.0
            if should_pass:
                position_multiplier = self.orchestrator.confidence_aggregator.calculate_position_multiplier(
                    final_confidence
                )

            return {
                'allowed': should_pass,
                'confidence': final_confidence,
                'position_multiplier': position_multiplier,
                'rejection_level': None if should_pass else 'CONFIDENCE',
                'rejection_reason': None if should_pass else aggregation_reason,
                'details': {
                    'l3_confidence': l3_result.confidence,
                    'l4_confidence': l4_result.confidence,
                    'l5_confidence': l5_result.confidence,
                    'l6_confidence': l6_result.confidence
                }
            }

        except Exception as e:
            console.print(f"[red]❌ {stock_name} 평가 실패: {e}[/red]")
            return {
                'allowed': False,
                'confidence': 0.0,
                'position_multiplier': 0.0,
                'rejection_level': 'ERROR',
                'rejection_reason': str(e),
                'details': {}
            }

    def run_backtest(self) -> pd.DataFrame:
        """
        전체 백테스트 실행

        Returns:
            결과 DataFrame
        """
        console.print("\n" + "=" * 80)
        console.print("🧪 Phase 1 Confidence 백테스트 시작")
        console.print("=" * 80)

        # 거래내역 로드
        trades = self.load_trade_history()

        # 각 거래에 대해 Confidence 계산
        results = []

        for idx, trade in trades.iterrows():
            console.print(f"\n[cyan]📊 [{idx+1}/{len(trades)}] {trade['종목']}[/cyan]")

            # Confidence 계산
            conf_result = self.calculate_confidence_for_trade(trade)

            # 결과 저장
            result = {
                '종목': trade['종목'],
                '종목코드': trade['종목코드'],
                '매수가': trade['매수가'],
                '실제_수익률': trade['수익률'],
                '실제_손익': trade['손익'],
                'Confidence': conf_result['confidence'],
                '진입_허용': conf_result['allowed'],
                '포지션_배수': conf_result['position_multiplier'],
                '차단_레벨': conf_result['rejection_level'],
                '차단_사유': conf_result['rejection_reason']
            }

            results.append(result)

            # 실시간 출력
            if conf_result['allowed']:
                console.print(f"  ✅ [green]진입 허용[/green] - Conf: {conf_result['confidence']:.2f}, "
                            f"포지션: {conf_result['position_multiplier']:.2f}")
            else:
                console.print(f"  ❌ [red]진입 차단[/red] - {conf_result['rejection_level']}: "
                            f"{conf_result['rejection_reason'][:50]}...")

        results_df = pd.DataFrame(results)
        return results_df

    def analyze_results(self, results: pd.DataFrame):
        """
        백테스트 결과 분석

        Args:
            results: 백테스트 결과 DataFrame
        """
        console.print("\n" + "=" * 80)
        console.print("📊 백테스트 결과 분석")
        console.print("=" * 80)

        # 기본 통계
        total_trades = len(results)
        allowed_trades = results['진입_허용'].sum()
        blocked_trades = total_trades - allowed_trades

        console.print(f"\n📈 전체 거래: {total_trades}건")
        console.print(f"  ✅ 진입 허용: {allowed_trades}건 ({allowed_trades/total_trades*100:.1f}%)")
        console.print(f"  ❌ 진입 차단: {blocked_trades}건 ({blocked_trades/total_trades*100:.1f}%)")

        # 손익 분석
        total_profit_original = results['실제_손익'].sum()
        console.print(f"\n💰 기존 시스템 (27건 모두 진입):")
        console.print(f"  총 손익: {total_profit_original:+,.0f}원")

        # Confidence 기반 필터링 후 손익
        allowed_results = results[results['진입_허용']]
        if len(allowed_results) > 0:
            total_profit_filtered = allowed_results['실제_손익'].sum()
            console.print(f"\n💎 신규 시스템 ({len(allowed_results)}건 진입):")
            console.print(f"  총 손익: {total_profit_filtered:+,.0f}원")
            console.print(f"  개선율: {((total_profit_filtered - total_profit_original) / abs(total_profit_original) * 100):+.1f}%")
        else:
            console.print(f"\n⚠️  모든 거래 차단됨")

        # Confidence 분포
        console.print(f"\n📊 Confidence 분포:")
        allowed_conf = results[results['진입_허용']]['Confidence']
        if len(allowed_conf) > 0:
            console.print(f"  평균: {allowed_conf.mean():.2f}")
            console.print(f"  중앙값: {allowed_conf.median():.2f}")
            console.print(f"  최소: {allowed_conf.min():.2f}")
            console.print(f"  최대: {allowed_conf.max():.2f}")

        # 차단 사유 분석
        console.print(f"\n🚫 차단 사유 분포:")
        blocked_results = results[~results['진입_허용']]
        if len(blocked_results) > 0:
            rejection_counts = blocked_results['차단_레벨'].value_counts()
            for level, count in rejection_counts.items():
                console.print(f"  {level}: {count}건")

        # 상세 테이블
        console.print(f"\n📋 상세 결과:")
        table = Table(box=box.ROUNDED)
        table.add_column("종목", style="cyan")
        table.add_column("실제수익률", justify="right")
        table.add_column("Conf", justify="right")
        table.add_column("허용", justify="center")
        table.add_column("포지션", justify="right")

        for _, row in results.iterrows():
            profit_color = "green" if row['실제_수익률'] > 0 else "red"
            allowed_emoji = "✅" if row['진입_허용'] else "❌"

            table.add_row(
                row['종목'],
                f"[{profit_color}]{row['실제_수익률']:+.2f}%[/{profit_color}]",
                f"{row['Confidence']:.2f}",
                allowed_emoji,
                f"{row['포지션_배수']:.2f}" if row['진입_허용'] else "-"
            )

        console.print(table)

        # 메드팩토 특별 분석
        medpact_trades = results[results['종목'] == '메드팩토']
        if len(medpact_trades) > 0:
            console.print(f"\n🔍 메드팩토 상세 분석 ({len(medpact_trades)}건):")
            medpact_allowed = medpact_trades['진입_허용'].sum()
            medpact_profit_original = medpact_trades['실제_손익'].sum()
            medpact_profit_filtered = medpact_trades[medpact_trades['진입_허용']]['실제_손익'].sum()

            console.print(f"  기존: {len(medpact_trades)}건 진입, 손익 {medpact_profit_original:+,.0f}원")
            console.print(f"  신규: {medpact_allowed}건 진입, 손익 {medpact_profit_filtered:+,.0f}원")
            console.print(f"  개선: {len(medpact_trades) - medpact_allowed}건 차단, "
                        f"{medpact_profit_filtered - medpact_profit_original:+,.0f}원 손실 방지")

        return results


def main():
    """메인 실행"""
    backtester = ConfidenceBacktester()

    # 백테스트 실행
    results = backtester.run_backtest()

    # 결과 분석
    backtester.analyze_results(results)

    # 결과 CSV 저장
    output_path = project_root / "data" / "confidence_backtest_results.csv"
    results.to_csv(output_path, index=False, encoding='utf-8-sig')
    console.print(f"\n💾 결과 저장: {output_path}")

    console.print("\n" + "=" * 80)
    console.print("✅ 백테스트 완료!")
    console.print("=" * 80)


if __name__ == "__main__":
    main()
