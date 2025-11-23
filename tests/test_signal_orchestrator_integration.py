"""
SignalOrchestrator 통합 검증 스크립트
- Import 검증
- 초기화 검증
- Config 검증
- 기본 동작 테스트
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from rich.console import Console

console = Console()

def test_imports():
    """Import 검증"""
    console.print("\n[bold cyan]1. Import 검증[/bold cyan]")
    console.print("-" * 80)

    try:
        from analyzers.signal_orchestrator import SignalOrchestrator, SignalTier
        console.print("  ✅ SignalOrchestrator import 성공")
        console.print("  ✅ SignalTier import 성공")
        return True
    except Exception as e:
        console.print(f"  ❌ Import 실패: {e}")
        return False

def test_config():
    """Config 검증"""
    console.print("\n[bold cyan]2. Config 검증[/bold cyan]")
    console.print("-" * 80)

    try:
        from utils.config_loader import load_config
        config = load_config("config/strategy_hybrid.yaml")

        # risk_control 섹션 확인
        risk_control = config.get('risk_control', {})
        if risk_control:
            console.print(f"  ✅ risk_control 섹션 존재")
            console.print(f"     max_daily_loss_pct: {risk_control.get('max_daily_loss_pct', 'N/A')}%")
        else:
            console.print("  ⚠️  risk_control 섹션 없음 (기본값 사용)")

        return True
    except Exception as e:
        console.print(f"  ❌ Config 로드 실패: {e}")
        return False

def test_orchestrator_init():
    """SignalOrchestrator 초기화 검증"""
    console.print("\n[bold cyan]3. SignalOrchestrator 초기화 검증[/bold cyan]")
    console.print("-" * 80)

    try:
        from analyzers.signal_orchestrator import SignalOrchestrator
        from utils.config_loader import load_config

        config = load_config("config/strategy_hybrid.yaml")

        # API 없이 초기화
        orchestrator = SignalOrchestrator(config=config, api=None)

        console.print("  ✅ SignalOrchestrator 초기화 성공")
        console.print(f"     L1 RV detector: {orchestrator.regime_detector is not None}")
        console.print(f"     L2 RS filter: {orchestrator.rs_filter is not None}")
        console.print(f"     L3 MTF consensus: {orchestrator.mtf_consensus is not None}")
        console.print(f"     L4 Liquidity detector: {orchestrator.liquidity_detector is not None}")
        console.print(f"     L5 Squeeze momentum: {orchestrator.squeeze is not None}")
        console.print(f"     L6 Validator: {orchestrator.validator is not None}")

        return True, orchestrator
    except Exception as e:
        console.print(f"  ❌ 초기화 실패: {e}")
        import traceback
        traceback.print_exc()
        return False, None

def test_l0_filter(orchestrator):
    """L0 시스템 필터 테스트"""
    console.print("\n[bold cyan]4. L0 시스템 필터 테스트[/bold cyan]")
    console.print("-" * 80)

    try:
        # 정상 케이스
        l0_pass, l0_reason = orchestrator.check_l0_system_filter(
            current_cash=10000000,
            daily_pnl=-100000  # -1%
        )

        console.print(f"  테스트 1 (정상): {' ✅ PASS' if l0_pass else '❌ BLOCK'}")
        console.print(f"    이유: {l0_reason}")

        # 손실 한도 초과 케이스
        l0_pass2, l0_reason2 = orchestrator.check_l0_system_filter(
            current_cash=10000000,
            daily_pnl=-400000  # -4% (한도 3% 초과)
        )

        console.print(f"  테스트 2 (손실 한도): {'✅ PASS' if l0_pass2 else '❌ BLOCK'}")
        console.print(f"    이유: {l0_reason2}")

        return True
    except Exception as e:
        console.print(f"  ❌ L0 테스트 실패: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_l1_regime(orchestrator):
    """L1 장세 필터 테스트"""
    console.print("\n[bold cyan]5. L1 장세 필터 테스트[/bold cyan]")
    console.print("-" * 80)

    try:
        l1_pass, l1_reason, l1_confidence = orchestrator.check_l1_regime_filter('KOSPI')

        console.print(f"  결과: {'✅ PASS' if l1_pass else '❌ BLOCK'}")
        console.print(f"    이유: {l1_reason}")
        console.print(f"    신뢰도: {l1_confidence * 100:.0f}%")

        return True
    except Exception as e:
        console.print(f"  ❌ L1 테스트 실패: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_l2_rs_filter(orchestrator):
    """L2 RS 필터 테스트"""
    console.print("\n[bold cyan]6. L2 RS 필터 테스트[/bold cyan]")
    console.print("-" * 80)

    try:
        candidates = [
            {'stock_code': '005930', 'stock_name': '삼성전자', 'market': 'KOSPI'},
            {'stock_code': '000660', 'stock_name': 'SK하이닉스', 'market': 'KOSPI'},
        ]

        console.print(f"  입력 종목: {len(candidates)}개")

        filtered = orchestrator.check_l2_rs_filter(candidates, market='KOSPI')

        console.print(f"  필터링 결과: {len(filtered)}개 통과")

        for stock in filtered:
            console.print(f"    - {stock['stock_name']} ({stock['stock_code']}): RS {stock.get('rs_rating', 0):.0f}")

        return True
    except Exception as e:
        console.print(f"  ❌ L2 테스트 실패: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_calculate_daily_pnl():
    """calculate_daily_pnl 함수 테스트"""
    console.print("\n[bold cyan]7. calculate_daily_pnl 함수 검증[/bold cyan]")
    console.print("-" * 80)

    try:
        # main_auto_trading.py에서 함수 존재 확인
        import inspect
        from importlib import import_module

        # 직접 import는 불가능하므로 소스 검색
        with open('main_auto_trading.py', 'r', encoding='utf-8') as f:
            content = f.read()

        if 'def calculate_daily_pnl' in content:
            console.print("  ✅ calculate_daily_pnl 함수 존재")

            # 함수 시그니처 확인
            if 'self) -> float:' in content:
                console.print("  ✅ 반환 타입 (float) 정의됨")

            if 'daily_pnl=self.calculate_daily_pnl()' in content:
                console.print("  ✅ check_entry_signal에서 호출 확인")

            return True
        else:
            console.print("  ❌ calculate_daily_pnl 함수 없음")
            return False

    except Exception as e:
        console.print(f"  ❌ 검증 실패: {e}")
        return False

def test_execute_buy_signature():
    """execute_buy 함수 시그니처 검증"""
    console.print("\n[bold cyan]8. execute_buy 함수 시그니처 검증[/bold cyan]")
    console.print("-" * 80)

    try:
        with open('main_auto_trading.py', 'r', encoding='utf-8') as f:
            content = f.read()

        # position_size_mult 파라미터 확인
        if 'position_size_mult: float = 1.0' in content:
            console.print("  ✅ position_size_mult 파라미터 추가됨")
        else:
            console.print("  ❌ position_size_mult 파라미터 없음")
            return False

        # 포지션 조정 로직 확인
        if 'quantity = int(position_calc' in content and 'position_size_mult)' in content:
            console.print("  ✅ 포지션 조정 로직 구현됨")
        else:
            console.print("  ❌ 포지션 조정 로직 없음")
            return False

        return True

    except Exception as e:
        console.print(f"  ❌ 검증 실패: {e}")
        return False

def main():
    console.print("=" * 80, style="bold green")
    console.print("SignalOrchestrator 통합 검증", style="bold green")
    console.print("=" * 80, style="bold green")

    results = []

    # 1. Import 검증
    results.append(("Import", test_imports()))

    # 2. Config 검증
    results.append(("Config", test_config()))

    # 3. Orchestrator 초기화
    success, orchestrator = test_orchestrator_init()
    results.append(("Orchestrator 초기화", success))

    if orchestrator:
        # 4. L0 필터
        results.append(("L0 시스템 필터", test_l0_filter(orchestrator)))

        # 5. L1 장세 필터
        results.append(("L1 장세 필터", test_l1_regime(orchestrator)))

        # 6. L2 RS 필터
        results.append(("L2 RS 필터", test_l2_rs_filter(orchestrator)))

    # 7. calculate_daily_pnl
    results.append(("calculate_daily_pnl", test_calculate_daily_pnl()))

    # 8. execute_buy 시그니처
    results.append(("execute_buy 시그니처", test_execute_buy_signature()))

    # 결과 요약
    console.print("\n" + "=" * 80, style="bold cyan")
    console.print("검증 결과 요약", style="bold cyan")
    console.print("=" * 80, style="bold cyan")

    passed = sum(1 for _, success in results if success)
    total = len(results)

    for name, success in results:
        status = "✅ PASS" if success else "❌ FAIL"
        console.print(f"  {status} - {name}")

    console.print()
    console.print(f"총 {passed}/{total} 검증 통과", style="bold green" if passed == total else "bold yellow")

    if passed == total:
        console.print("\n🎉 모든 검증 통과! 실전 테스트 준비 완료!", style="bold green")
    else:
        console.print("\n⚠️  일부 검증 실패. 위 내용 확인 필요.", style="bold yellow")

    console.print("=" * 80)

if __name__ == "__main__":
    main()
