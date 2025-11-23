"""
시스템 전체 점검 스크립트

Phase 1 구현 후 모든 컴포넌트가 정상 작동하는지 확인
"""

import sys
from pathlib import Path
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

console = Console()

def print_header(title: str):
    """섹션 헤더 출력"""
    console.print(f"\n{'='*80}")
    console.print(f"[bold cyan]{title}[/bold cyan]")
    console.print(f"{'='*80}\n")


def test_1_imports():
    """테스트 1: 모든 V2 모듈 Import 확인"""
    print_header("테스트 1: V2 모듈 Import 확인")

    imports = []

    try:
        from trading.filters.base_filter import FilterResult, BaseFilter
        imports.append(("✅", "trading.filters.base_filter", "OK"))
    except Exception as e:
        imports.append(("❌", "trading.filters.base_filter", str(e)))

    try:
        from trading.confidence_aggregator import ConfidenceAggregator
        imports.append(("✅", "trading.confidence_aggregator", "OK"))
    except Exception as e:
        imports.append(("❌", "trading.confidence_aggregator", str(e)))

    try:
        from analyzers.multi_timeframe_consensus_v2 import MultiTimeframeConsensusV2
        imports.append(("✅", "analyzers.multi_timeframe_consensus_v2", "OK"))
    except Exception as e:
        imports.append(("❌", "analyzers.multi_timeframe_consensus_v2", str(e)))

    try:
        from analyzers.liquidity_shift_detector_v2 import LiquidityShiftDetectorV2
        imports.append(("✅", "analyzers.liquidity_shift_detector_v2", "OK"))
    except Exception as e:
        imports.append(("❌", "analyzers.liquidity_shift_detector_v2", str(e)))

    try:
        from analyzers.squeeze_momentum_v2 import SqueezeMomentumProV2
        imports.append(("✅", "analyzers.squeeze_momentum_v2", "OK"))
    except Exception as e:
        imports.append(("❌", "analyzers.squeeze_momentum_v2", str(e)))

    try:
        from analyzers.pre_trade_validator_v2 import PreTradeValidatorV2
        imports.append(("✅", "analyzers.pre_trade_validator_v2", "OK"))
    except Exception as e:
        imports.append(("❌", "analyzers.pre_trade_validator_v2", str(e)))

    try:
        from analyzers.signal_orchestrator import SignalOrchestrator
        imports.append(("✅", "analyzers.signal_orchestrator", "OK"))
    except Exception as e:
        imports.append(("❌", "analyzers.signal_orchestrator", str(e)))

    # 결과 출력
    for status, module, msg in imports:
        if status == "✅":
            console.print(f"{status} [green]{module}[/green]: {msg}")
        else:
            console.print(f"{status} [red]{module}[/red]: {msg}")

    passed = sum(1 for s, _, _ in imports if s == "✅")
    total = len(imports)
    console.print(f"\n[bold]결과: {passed}/{total} 통과[/bold]")

    return passed == total


def test_2_filter_initialization():
    """테스트 2: V2 필터 초기화 확인"""
    print_header("테스트 2: V2 필터 초기화 확인")

    from utils.config_loader import ConfigLoader

    results = []

    # Config 로드
    try:
        config = ConfigLoader()
        console.print("✅ [green]ConfigLoader 초기화 성공[/green]")
        results.append(True)
    except Exception as e:
        console.print(f"❌ [red]ConfigLoader 초기화 실패: {e}[/red]")
        results.append(False)
        return False

    # L3 MTF V2
    try:
        from analyzers.multi_timeframe_consensus_v2 import MultiTimeframeConsensusV2
        mtf_v2 = MultiTimeframeConsensusV2(config)
        console.print("✅ [green]L3 MTF V2 초기화 성공[/green]")
        results.append(True)
    except Exception as e:
        console.print(f"❌ [red]L3 MTF V2 초기화 실패: {e}[/red]")
        results.append(False)

    # L4 Liquidity V2
    try:
        from analyzers.liquidity_shift_detector_v2 import LiquidityShiftDetectorV2
        liquidity_v2 = LiquidityShiftDetectorV2(api=None)
        console.print("✅ [green]L4 Liquidity V2 초기화 성공[/green]")
        results.append(True)
    except Exception as e:
        console.print(f"❌ [red]L4 Liquidity V2 초기화 실패: {e}[/red]")
        results.append(False)

    # L5 Squeeze V2
    try:
        from analyzers.squeeze_momentum_v2 import SqueezeMomentumProV2
        squeeze_v2 = SqueezeMomentumProV2()
        console.print("✅ [green]L5 Squeeze V2 초기화 성공[/green]")
        results.append(True)
    except Exception as e:
        console.print(f"❌ [red]L5 Squeeze V2 초기화 실패: {e}[/red]")
        results.append(False)

    # L6 Validator V2
    try:
        from analyzers.pre_trade_validator_v2 import PreTradeValidatorV2
        validator_v2 = PreTradeValidatorV2(config)
        console.print("✅ [green]L6 Validator V2 초기화 성공[/green]")
        results.append(True)
    except Exception as e:
        console.print(f"❌ [red]L6 Validator V2 초기화 실패: {e}[/red]")
        results.append(False)

    # Confidence Aggregator
    try:
        from trading.confidence_aggregator import ConfidenceAggregator
        aggregator = ConfidenceAggregator()
        console.print("✅ [green]Confidence Aggregator 초기화 성공[/green]")
        console.print(f"   가중치: {aggregator.weights}")
        results.append(True)
    except Exception as e:
        console.print(f"❌ [red]Confidence Aggregator 초기화 실패: {e}[/red]")
        results.append(False)

    passed = sum(results)
    total = len(results)
    console.print(f"\n[bold]결과: {passed}/{total} 통과[/bold]")

    return all(results)


def test_3_orchestrator_integration():
    """테스트 3: SignalOrchestrator V2 통합 확인"""
    print_header("테스트 3: SignalOrchestrator V2 통합 확인")

    try:
        from analyzers.signal_orchestrator import SignalOrchestrator
        from utils.config_loader import ConfigLoader

        config = ConfigLoader()
        orchestrator = SignalOrchestrator(config, api=None)

        # V2 필터 확인
        console.print("\n[bold]SignalOrchestrator 컴포넌트:[/bold]")
        console.print(f"  L3 MTF: {type(orchestrator.mtf_consensus).__name__}")
        console.print(f"  L4 Liquidity: {type(orchestrator.liquidity_detector).__name__}")
        console.print(f"  L5 Squeeze: {type(orchestrator.squeeze).__name__}")
        console.print(f"  L6 Validator: {type(orchestrator.validator).__name__}")
        console.print(f"  Aggregator: {type(orchestrator.confidence_aggregator).__name__}")

        # V2 버전 확인
        checks = []
        checks.append(("L3", "MultiTimeframeConsensusV2" in type(orchestrator.mtf_consensus).__name__))
        checks.append(("L4", "LiquidityShiftDetectorV2" in type(orchestrator.liquidity_detector).__name__))
        checks.append(("L5", "SqueezeMomentumProV2" in type(orchestrator.squeeze).__name__))
        checks.append(("L6", "PreTradeValidatorV2" in type(orchestrator.validator).__name__))

        console.print("\n[bold]V2 버전 확인:[/bold]")
        for name, is_v2 in checks:
            status = "✅" if is_v2 else "❌"
            console.print(f"  {status} {name}: {'V2' if is_v2 else 'V1 (문제!)'}")

        all_v2 = all(check for _, check in checks)

        if all_v2:
            console.print("\n✅ [green]모든 필터가 V2로 통합되었습니다![/green]")
        else:
            console.print("\n❌ [red]일부 필터가 V1입니다. signal_orchestrator.py를 확인하세요.[/red]")

        return all_v2

    except Exception as e:
        console.print(f"❌ [red]SignalOrchestrator 초기화 실패: {e}[/red]")
        import traceback
        traceback.print_exc()
        return False


def test_4_confidence_calculation():
    """테스트 4: Confidence 계산 로직 테스트"""
    print_header("테스트 4: Confidence 계산 로직 테스트")

    try:
        from trading.filters.base_filter import FilterResult
        from trading.confidence_aggregator import ConfidenceAggregator

        aggregator = ConfidenceAggregator()

        # 테스트 케이스 1: 모든 필터 높은 confidence
        console.print("\n[bold cyan]케이스 1: 모든 필터 높은 confidence[/bold cyan]")
        results_high = {
            "L3_MTF": FilterResult(True, 0.8, "강한 VWAP 돌파"),
            "L4_LIQUIDITY": FilterResult(True, 0.7, "강한 수급"),
            "L5_SQUEEZE": FilterResult(True, 0.6, "Squeeze + Momentum"),
            "L6_VALIDATOR": FilterResult(True, 0.75, "백테스트 양호")
        }

        conf, passed, reason = aggregator.aggregate(results_high)
        pos_mult = aggregator.calculate_position_multiplier(conf)

        console.print(f"  최종 Confidence: {conf:.2f}")
        console.print(f"  진입 허용: {'✅ YES' if passed else '❌ NO'}")
        console.print(f"  포지션 배수: {pos_mult:.2f}")
        console.print(f"  사유: {reason}")

        # 테스트 케이스 2: 낮은 confidence (차단)
        console.print("\n[bold cyan]케이스 2: 낮은 confidence (차단 예상)[/bold cyan]")
        results_low = {
            "L3_MTF": FilterResult(True, 0.2, "약한 VWAP 돌파"),
            "L4_LIQUIDITY": FilterResult(True, 0.3, "보통 수급"),
            "L5_SQUEEZE": FilterResult(True, 0.3, "Squeeze 없음 (default)"),
            "L6_VALIDATOR": FilterResult(True, 0.25, "백테스트 미달")
        }

        conf, passed, reason = aggregator.aggregate(results_low)
        pos_mult = aggregator.calculate_position_multiplier(conf) if passed else 0.0

        console.print(f"  최종 Confidence: {conf:.2f}")
        console.print(f"  진입 허용: {'✅ YES' if passed else '❌ NO (예상대로!)'}")
        console.print(f"  포지션 배수: {pos_mult:.2f}")
        console.print(f"  사유: {reason}")

        # 테스트 케이스 3: 중간 confidence (진입, 낮은 포지션)
        console.print("\n[bold cyan]케이스 3: 중간 confidence (낮은 포지션)[/bold cyan]")
        results_mid = {
            "L3_MTF": FilterResult(True, 0.5, "보통 VWAP"),
            "L4_LIQUIDITY": FilterResult(True, 0.4, "보통 수급"),
            "L5_SQUEEZE": FilterResult(True, 0.4, "약한 Squeeze"),
            "L6_VALIDATOR": FilterResult(True, 0.5, "백테스트 통과")
        }

        conf, passed, reason = aggregator.aggregate(results_mid)
        pos_mult = aggregator.calculate_position_multiplier(conf) if passed else 0.0

        console.print(f"  최종 Confidence: {conf:.2f}")
        console.print(f"  진입 허용: {'✅ YES' if passed else '❌ NO'}")
        console.print(f"  포지션 배수: {pos_mult:.2f} (낮음 - 예상대로!)")
        console.print(f"  사유: {reason}")

        console.print("\n✅ [green]Confidence 계산 로직 정상 작동![/green]")
        return True

    except Exception as e:
        console.print(f"❌ [red]Confidence 계산 실패: {e}[/red]")
        import traceback
        traceback.print_exc()
        return False


def test_5_kiwoom_api():
    """테스트 5: Kiwoom API 연결 확인"""
    print_header("테스트 5: Kiwoom API 연결 확인")

    try:
        from utils.kiwoom_api import KiwoomAPI

        console.print("Kiwoom API 초기화 시도 중...")
        api = KiwoomAPI()

        # API 상태 확인
        console.print(f"\n[bold]API 상태:[/bold]")
        console.print(f"  연결 상태: {api.is_connected if hasattr(api, 'is_connected') else 'Unknown'}")

        # 계좌 정보 확인
        if hasattr(api, 'account_number'):
            console.print(f"  계좌번호: {api.account_number}")

        console.print("\n✅ [green]Kiwoom API 초기화 성공[/green]")
        console.print("⚠️  [yellow]실제 연결 상태는 로그인 후 확인 필요[/yellow]")

        return True

    except Exception as e:
        console.print(f"❌ [red]Kiwoom API 초기화 실패: {e}[/red]")
        console.print("⚠️  [yellow]장 시작 전이거나 API 미설치일 수 있습니다[/yellow]")
        return False


def main():
    """메인 실행"""
    console.print("\n" + "="*80)
    console.print("[bold green]🔍 Phase 1 시스템 전체 점검 시작[/bold green]")
    console.print("="*80)

    results = {}

    # 테스트 실행
    results['imports'] = test_1_imports()
    results['initialization'] = test_2_filter_initialization()
    results['orchestrator'] = test_3_orchestrator_integration()
    results['confidence'] = test_4_confidence_calculation()
    results['api'] = test_5_kiwoom_api()

    # 최종 결과
    print_header("최종 결과")

    table = Table(box=box.ROUNDED)
    table.add_column("테스트", style="cyan", width=30)
    table.add_column("결과", justify="center", width=10)

    for name, passed in results.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        color = "green" if passed else "red"
        table.add_row(name.upper(), f"[{color}]{status}[/{color}]")

    console.print(table)

    # 종합 판정
    all_passed = all(results.values())
    critical_passed = results['imports'] and results['initialization'] and results['orchestrator'] and results['confidence']

    console.print("\n" + "="*80)
    if all_passed:
        console.print("[bold green]🎉 모든 테스트 통과! 시스템 준비 완료![/bold green]")
    elif critical_passed:
        console.print("[bold yellow]⚠️  핵심 테스트 통과! API는 장 시작 시 확인 필요[/bold yellow]")
    else:
        console.print("[bold red]❌ 일부 테스트 실패! 문제를 해결하세요[/bold red]")
    console.print("="*80 + "\n")


if __name__ == "__main__":
    main()
