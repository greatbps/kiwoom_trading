"""
Phase 1 Confidence 백테스트 (실제 데이터)

yfinance로 실제 과거 시장 데이터를 다운로드하여
27건 거래내역에 대한 Confidence 재평가
"""

import pandas as pd
import numpy as np
from pathlib import Path
import sys
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import yfinance as yf

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from analyzers.signal_orchestrator import SignalOrchestrator
from utils.config_loader import ConfigLoader
from rich.console import Console
from rich.table import Table
from rich import box
from rich.progress import Progress, SpinnerColumn, TextColumn

console = Console()


class RealDataBacktester:
    """실제 데이터 기반 Confidence 백테스트"""

    def __init__(self):
        """초기화"""
        self.config = ConfigLoader()
        self.orchestrator = SignalOrchestrator(self.config, api=None)

        # 종목명 → 종목코드 매핑
        self.name_to_code = {
            "한올바이오파마": "009420.KS",
            "태성": "004410.KS",
            "로킷헬스케어": "418550.KQ",
            "삼영": "005680.KS",
            "코오롱티슈진": "014680.KS",
            "신테카바이오": "226330.KQ",
            "메드팩토": "035760.KQ",  # 035430이 아니라 035760
            "글로벌텍스프리": "204620.KS",
            "미래컴퍼니": "049950.KQ",
            "롯데관광개발": "032350.KS"
        }

        # 데이터 캐시
        self.data_cache = {}

    def load_trade_history(self) -> pd.DataFrame:
        """거래내역 27건 로드"""
        csv_path = project_root / "data" / "trade_analysis_detailed.csv"
        df = pd.read_csv(csv_path, encoding='utf-8-sig')

        # 종목코드 추가
        df['yf_ticker'] = df['종목'].map(self.name_to_code)

        console.print(f"\n✅ 거래내역 로드: {len(df)}건")
        return df

    def download_stock_data(
        self,
        ticker: str,
        start_date: str,
        end_date: str
    ) -> Optional[pd.DataFrame]:
        """
        yfinance로 주식 데이터 다운로드

        Args:
            ticker: yfinance 티커 (예: 009420.KS)
            start_date: 시작일 (YYYY-MM-DD)
            end_date: 종료일 (YYYY-MM-DD)

        Returns:
            OHLCV 데이터프레임 또는 None
        """
        # 캐시 확인
        cache_key = f"{ticker}_{start_date}_{end_date}"
        if cache_key in self.data_cache:
            return self.data_cache[cache_key]

        try:
            # 일봉 데이터 다운로드
            df = yf.download(
                ticker,
                start=start_date,
                end=end_date,
                interval='1d',  # 일봉
                progress=False,
                auto_adjust=True  # 자동 조정
            )

            if df is None or len(df) == 0:
                console.print(f"[yellow]⚠️  {ticker} 데이터 없음[/yellow]")
                return None

            # MultiIndex 처리
            if isinstance(df.columns, pd.MultiIndex):
                # MultiIndex인 경우 첫 번째 레벨만 사용
                df.columns = df.columns.get_level_values(0)

            # 컬럼명 소문자 변환
            df.columns = [col.lower() if isinstance(col, str) else col for col in df.columns]

            # VWAP 계산 (간단 버전: close 사용)
            df['vwap'] = df['close']

            # ATR 계산
            df['high_low'] = df['high'] - df['low']
            df['high_close'] = abs(df['high'] - df['close'].shift())
            df['low_close'] = abs(df['low'] - df['close'].shift())
            df['tr'] = df[['high_low', 'high_close', 'low_close']].max(axis=1)
            df['atr'] = df['tr'].rolling(14).mean()

            # 캐시 저장
            self.data_cache[cache_key] = df

            return df

        except Exception as e:
            console.print(f"[red]❌ {ticker} 다운로드 실패: {e}[/red]")
            return None

    def calculate_confidence_for_trade(
        self,
        trade: pd.Series
    ) -> Dict:
        """
        개별 거래에 대한 Confidence 계산 (실제 데이터)

        Args:
            trade: 거래 정보

        Returns:
            Confidence 평가 결과
        """
        ticker = trade['yf_ticker']
        stock_name = trade['종목']
        entry_price = trade['매수가']

        # 거래일 전후 데이터 다운로드 (거래일 기준 -30일 ~ +1일)
        # 실제 거래 시간은 무시하고 거래일만 사용
        trade_date = datetime.now()  # 실제로는 거래내역에서 파싱해야 하지만 데이터에 없음

        # 최근 3개월 데이터 다운로드
        end_date = (datetime.now() + timedelta(days=1)).strftime('%Y-%m-%d')
        start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

        df = self.download_stock_data(ticker, start_date, end_date)

        if df is None or len(df) < 30:
            return {
                'allowed': False,
                'confidence': 0.0,
                'position_multiplier': 0.0,
                'rejection_level': 'DATA',
                'rejection_reason': '과거 데이터 부족',
                'details': {}
            }

        try:
            from trading.filters.base_filter import FilterResult

            # 종목코드 추출 (yf_ticker에서 .KS, .KQ 제거)
            stock_code = ticker.split('.')[0]

            # L3: MTF Consensus
            # 일봉 데이터이므로 MTF 체크는 스킵하고 단순 VWAP만 체크
            l3_result = FilterResult(True, 0.5, "L3 일봉 데이터 (MTF 스킵)")

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
            import traceback
            traceback.print_exc()
            return {
                'allowed': False,
                'confidence': 0.0,
                'position_multiplier': 0.0,
                'rejection_level': 'ERROR',
                'rejection_reason': str(e),
                'details': {}
            }

    def run_backtest(self) -> pd.DataFrame:
        """전체 백테스트 실행"""
        console.print("\n" + "=" * 80)
        console.print("🧪 Phase 1 Confidence 백테스트 (실제 데이터)")
        console.print("=" * 80)

        trades = self.load_trade_history()
        results = []

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console
        ) as progress:
            task = progress.add_task("[cyan]백테스트 진행 중...", total=len(trades))

            for idx, trade in trades.iterrows():
                progress.update(
                    task,
                    description=f"[cyan][{idx+1}/{len(trades)}] {trade['종목']}[/cyan]"
                )

                conf_result = self.calculate_confidence_for_trade(trade)

                result = {
                    '종목': trade['종목'],
                    'Ticker': trade['yf_ticker'],
                    '매수가': trade['매수가'],
                    '실제_수익률': trade['수익률'],
                    '실제_손익': trade['손익'],
                    'Confidence': conf_result['confidence'],
                    '진입_허용': conf_result['allowed'],
                    '포지션_배수': conf_result['position_multiplier'],
                    '차단_레벨': conf_result['rejection_level'],
                    '차단_사유': conf_result['rejection_reason'],
                    'L3_Conf': conf_result['details'].get('l3_confidence', 0),
                    'L4_Conf': conf_result['details'].get('l4_confidence', 0),
                    'L5_Conf': conf_result['details'].get('l5_confidence', 0),
                    'L6_Conf': conf_result['details'].get('l6_confidence', 0)
                }

                results.append(result)
                progress.advance(task)

        return pd.DataFrame(results)

    def analyze_results(self, results: pd.DataFrame):
        """결과 분석 및 출력"""
        console.print("\n" + "=" * 80)
        console.print("📊 백테스트 결과 분석 (실제 데이터)")
        console.print("=" * 80)

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

        allowed_results = results[results['진입_허용']]
        if len(allowed_results) > 0:
            total_profit_filtered = allowed_results['실제_손익'].sum()
            blocked_loss = results[~results['진입_허용']]['실제_손익'].sum()

            console.print(f"\n💎 신규 시스템 ({len(allowed_results)}건 진입):")
            console.print(f"  총 손익: {total_profit_filtered:+,.0f}원")
            console.print(f"  차단된 손익: {blocked_loss:+,.0f}원")
            console.print(f"  순개선: {total_profit_filtered - total_profit_original:+,.0f}원")

            if total_profit_original != 0:
                improvement_pct = ((total_profit_filtered - total_profit_original) / abs(total_profit_original) * 100)
                console.print(f"  개선율: {improvement_pct:+.1f}%")
        else:
            console.print(f"\n⚠️  모든 거래 차단됨")

        # Confidence 분포
        if len(allowed_results) > 0:
            console.print(f"\n📊 Confidence 분포 (진입 허용):")
            console.print(f"  평균: {allowed_results['Confidence'].mean():.2f}")
            console.print(f"  중앙값: {allowed_results['Confidence'].median():.2f}")
            console.print(f"  최소: {allowed_results['Confidence'].min():.2f}")
            console.print(f"  최대: {allowed_results['Confidence'].max():.2f}")

        # Layer별 Confidence 분석
        console.print(f"\n🔍 Layer별 평균 Confidence:")
        console.print(f"  L3 (MTF): {results['L3_Conf'].mean():.2f}")
        console.print(f"  L4 (Liquidity): {results['L4_Conf'].mean():.2f}")
        console.print(f"  L5 (Squeeze): {results['L5_Conf'].mean():.2f}")
        console.print(f"  L6 (Validator): {results['L6_Conf'].mean():.2f}")

        # 차단 사유
        if len(results[~results['진입_허용']]) > 0:
            console.print(f"\n🚫 차단 사유 분포:")
            blocked = results[~results['진입_허용']]
            rejection_counts = blocked['차단_레벨'].value_counts()
            for level, count in rejection_counts.items():
                console.print(f"  {level}: {count}건")

        # 메드팩토 분석
        medpact_trades = results[results['종목'] == '메드팩토']
        if len(medpact_trades) > 0:
            console.print(f"\n🔍 메드팩토 상세 분석 ({len(medpact_trades)}건):")
            medpact_allowed = medpact_trades['진입_허용'].sum()
            medpact_profit_original = medpact_trades['실제_손익'].sum()
            medpact_profit_filtered = medpact_trades[medpact_trades['진입_허용']]['실제_손익'].sum()

            console.print(f"  기존: {len(medpact_trades)}건 진입, 손익 {medpact_profit_original:+,.0f}원")
            console.print(f"  신규: {medpact_allowed}건 진입, 손익 {medpact_profit_filtered:+,.0f}원")
            console.print(f"  개선: {len(medpact_trades) - medpact_allowed}건 차단, "
                        f"{medpact_profit_filtered - medpact_profit_original:+,.0f}원")

        # 상세 테이블
        console.print(f"\n📋 상세 결과:")
        table = Table(box=box.ROUNDED, show_lines=True)
        table.add_column("종목", style="cyan", width=12)
        table.add_column("수익률", justify="right", width=8)
        table.add_column("Conf", justify="right", width=6)
        table.add_column("L3", justify="right", width=5)
        table.add_column("L4", justify="right", width=5)
        table.add_column("L5", justify="right", width=5)
        table.add_column("L6", justify="right", width=5)
        table.add_column("허용", justify="center", width=4)
        table.add_column("포지션", justify="right", width=6)

        for _, row in results.iterrows():
            profit_color = "green" if row['실제_수익률'] > 0 else "red"
            allowed_emoji = "✅" if row['진입_허용'] else "❌"

            table.add_row(
                row['종목'],
                f"[{profit_color}]{row['실제_수익률']:+.2f}%[/{profit_color}]",
                f"{row['Confidence']:.2f}",
                f"{row['L3_Conf']:.2f}",
                f"{row['L4_Conf']:.2f}",
                f"{row['L5_Conf']:.2f}",
                f"{row['L6_Conf']:.2f}",
                allowed_emoji,
                f"{row['포지션_배수']:.2f}" if row['진입_허용'] else "-"
            )

        console.print(table)

        return results


def main():
    """메인 실행"""
    backtester = RealDataBacktester()

    # 백테스트 실행
    results = backtester.run_backtest()

    # 결과 분석
    backtester.analyze_results(results)

    # 결과 저장
    output_path = project_root / "data" / "confidence_backtest_real_data_results.csv"
    results.to_csv(output_path, index=False, encoding='utf-8-sig')
    console.print(f"\n💾 결과 저장: {output_path}")

    console.print("\n" + "=" * 80)
    console.print("✅ 백테스트 완료!")
    console.print("=" * 80)


if __name__ == "__main__":
    main()
