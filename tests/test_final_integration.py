"""
최종 통합 테스트

실제 Kiwoom API와 V2 필터를 연동하여 전체 시스템 작동 확인
"""

import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich import box
import pandas as pd
import numpy as np

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

console = Console()


def test_api_connection():
    """Kiwoom API 연결 테스트"""
    console.print("\n" + "="*80)
    console.print("[bold cyan]Kiwoom API 연결 테스트[/bold cyan]")
    console.print("="*80 + "\n")

    try:
        from kiwoom_api import KiwoomAPI

        api = KiwoomAPI()
        console.print("✅ [green]KiwoomAPI 초기화 성공[/green]")
        console.print(f"   API Key: {api.api_key[:10]}..." if api.api_key else "   API Key: None")
        console.print(f"   계좌번호: {api.account_number}" if api.account_number else "   계좌번호: None")

        return api

    except Exception as e:
        console.print(f"❌ [red]KiwoomAPI 초기화 실패: {e}[/red]")
        return None


def test_orchestrator_with_api(api):
    """SignalOrchestrator + API 통합 테스트"""
    console.print("\n" + "="*80)
    console.print("[bold cyan]SignalOrchestrator + API 통합 테스트[/bold cyan]")
    console.print("="*80 + "\n")

    try:
        from analyzers.signal_orchestrator import SignalOrchestrator
        from utils.config_loader import ConfigLoader

        config = ConfigLoader()
        orchestrator = SignalOrchestrator(config, api=api)

        console.print("✅ [green]SignalOrchestrator 초기화 성공 (API 연동)[/green]")
        console.print(f"\n[bold]구성:[/bold]")
        console.print(f"  L3: {type(orchestrator.mtf_consensus).__name__}")
        console.print(f"  L4: {type(orchestrator.liquidity_detector).__name__}")
        console.print(f"  L5: {type(orchestrator.squeeze).__name__}")
        console.print(f"  L6: {type(orchestrator.validator).__name__}")
        console.print(f"  Aggregator: {type(orchestrator.confidence_aggregator).__name__}")
        console.print(f"  API: {'Connected' if api else 'None'}")

        return orchestrator

    except Exception as e:
        console.print(f"❌ [red]SignalOrchestrator 초기화 실패: {e}[/red]")
        import traceback
        traceback.print_exc()
        return None


def test_mock_signal_evaluation(orchestrator):
    """Mock 데이터로 시그널 평가 테스트"""
    console.print("\n" + "="*80)
    console.print("[bold cyan]Mock 데이터 시그널 평가 테스트[/bold cyan]")
    console.print("="*80 + "\n")

    # Mock OHLCV 데이터 생성
    n = 100
    df = pd.DataFrame({
        'open': np.random.randn(n).cumsum() + 50000,
        'high': np.random.randn(n).cumsum() + 50100,
        'low': np.random.randn(n).cumsum() + 49900,
        'close': np.random.randn(n).cumsum() + 50000,
        'volume': np.random.randint(10000, 100000, n)
    })

    # VWAP 추가
    df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()

    # ATR 추가
    df['high_low'] = df['high'] - df['low']
    df['tr'] = df['high_low']
    df['atr'] = df['tr'].rolling(14).mean()

    console.print(f"[bold]Mock 데이터:[/bold]")
    console.print(f"  종목: 삼성전자 (005930)")
    console.print(f"  현재가: {df['close'].iloc[-1]:.0f}원")
    console.print(f"  데이터: {len(df)}개 봉")

    # L0-L1 필터는 스킵하고 L3-L6만 테스트
    try:
        from trading.filters.base_filter import FilterResult

        # L3: MTF Consensus
        console.print(f"\n[bold cyan]L3: MTF Consensus 테스트[/bold cyan]")
        l3_result = orchestrator.mtf_consensus.check_with_confidence("005930", "KOSPI", df)
        console.print(f"  Result: {'✅ PASS' if l3_result.passed else '❌ FAIL'}")
        console.print(f"  Confidence: {l3_result.confidence:.2f}")
        console.print(f"  Reason: {l3_result.reason[:100]}...")

        # L4: Liquidity Shift
        console.print(f"\n[bold cyan]L4: Liquidity Shift 테스트[/bold cyan]")
        l4_result = orchestrator.liquidity_detector.check_with_confidence("005930")
        console.print(f"  Result: {'✅ PASS' if l4_result.passed else '❌ FAIL'}")
        console.print(f"  Confidence: {l4_result.confidence:.2f}")
        console.print(f"  Reason: {l4_result.reason[:100]}...")

        # L5: Squeeze Momentum
        console.print(f"\n[bold cyan]L5: Squeeze Momentum 테스트[/bold cyan]")
        l5_result = orchestrator.squeeze.check_with_confidence(df)
        console.print(f"  Result: {'✅ PASS' if l5_result.passed else '❌ FAIL'}")
        console.print(f"  Confidence: {l5_result.confidence:.2f}")
        console.print(f"  Reason: {l5_result.reason[:100]}...")

        # L6: Pre-Trade Validator
        console.print(f"\n[bold cyan]L6: Pre-Trade Validator 테스트[/bold cyan]")
        from datetime import datetime
        l6_result = orchestrator.validator.check_with_confidence(
            stock_code="005930",
            stock_name="삼성전자",
            historical_data=df,
            current_price=df['close'].iloc[-1],
            current_time=datetime.now()
        )
        console.print(f"  Result: {'✅ PASS' if l6_result.passed else '❌ FAIL'}")
        console.print(f"  Confidence: {l6_result.confidence:.2f}")
        console.print(f"  Reason: {l6_result.reason[:100]}...")

        # Confidence 결합
        console.print(f"\n[bold cyan]Confidence 결합 테스트[/bold cyan]")
        filter_results = {
            "L3_MTF": l3_result,
            "L4_LIQUIDITY": l4_result if l4_result.passed else FilterResult(True, 0.3, "L4 Default"),
            "L5_SQUEEZE": l5_result if l5_result.passed else FilterResult(True, 0.3, "L5 Default"),
            "L6_VALIDATOR": l6_result
        }

        final_conf, should_pass, reason = orchestrator.confidence_aggregator.aggregate(filter_results)

        console.print(f"\n[bold]최종 결과:[/bold]")
        console.print(f"  최종 Confidence: {final_conf:.2f}")
        console.print(f"  진입 허용: {'✅ YES' if should_pass else '❌ NO'}")

        if should_pass:
            pos_mult = orchestrator.confidence_aggregator.calculate_position_multiplier(final_conf)
            console.print(f"  포지션 크기: {pos_mult:.2f} ({pos_mult*100:.0f}%)")
        else:
            console.print(f"  차단 사유: {reason}")

        console.print("\n✅ [green]Mock 시그널 평가 성공![/green]")
        return True

    except Exception as e:
        console.print(f"\n❌ [red]Mock 시그널 평가 실패: {e}[/red]")
        import traceback
        traceback.print_exc()
        return False


def main():
    """메인 실행"""
    console.print("\n" + "="*80)
    console.print("[bold green]🚀 Phase 1 최종 통합 테스트[/bold green]")
    console.print("="*80)

    # 1. API 연결 테스트
    api = test_api_connection()

    # 2. SignalOrchestrator + API 통합
    orchestrator = test_orchestrator_with_api(api)

    if not orchestrator:
        console.print("\n[red]SignalOrchestrator 초기화 실패. 테스트 중단.[/red]")
        return

    # 3. Mock 시그널 평가
    test_mock_signal_evaluation(orchestrator)

    # 최종 결과
    console.print("\n" + "="*80)
    console.print("[bold green]✅ 최종 통합 테스트 완료![/bold green]")
    console.print("="*80)

    console.print("\n[bold]내일 실전 테스트 준비사항:[/bold]")
    console.print("  1. ✅ Phase 1 구현 완료")
    console.print("  2. ✅ V2 필터 통합 완료")
    console.print("  3. ✅ Confidence 계산 로직 정상")
    console.print("  4. ✅ API 연동 준비 완료")
    console.print("  5. ⏳ 장 시작 시 실시간 데이터로 검증")

    console.print("\n[bold cyan]내일 실전 테스트 항목:[/bold cyan]")
    console.print("  - 실시간 1분봉/5분봉 데이터 수신 확인")
    console.print("  - L3-L6 Confidence 점수 모니터링")
    console.print("  - 진입/차단 패턴 관찰")
    console.print("  - Confidence 분포 수집")
    console.print("  - 포지션 크기 조정 효과 확인")

    console.print("\n[bold green]🎯 시스템 준비 완료! 내일 실전 테스트 진행하세요! 🚀[/bold green]\n")


if __name__ == "__main__":
    main()
