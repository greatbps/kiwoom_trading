"""
RSVI Phase 1 최종 검증 스크립트
실거래 투입 전 최종 점검

검증 항목:
1. 모든 모듈 임포트 검증
2. 코드 문법 검증
3. 통합 경로 검증 (main → orchestrator → validator)
4. RSVI 계산 로직 검증
5. ChatGPT 제안 수정사항 적용 확인

작성일: 2025-11-30
"""

import sys
from pathlib import Path
import py_compile
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

console = Console()


class FinalValidator:
    """최종 검증 클래스"""

    def __init__(self):
        self.results = []

    def log(self, category: str, item: str, passed: bool, detail: str = ""):
        """검증 결과 로그"""
        self.results.append({
            'category': category,
            'item': item,
            'passed': passed,
            'detail': detail
        })

        icon = "✓" if passed else "✗"
        style = "green" if passed else "red"
        console.print(f"  [{style}]{icon}[/{style}] {item}")
        if detail:
            console.print(f"     [dim]{detail}[/dim]")

    def validate_syntax(self):
        """문법 검증"""
        console.print("\n[bold cyan]1. 코드 문법 검증[/bold cyan]")

        files = [
            "analyzers/volume_indicators.py",
            "analyzers/pre_trade_validator_v2.py",
            "analyzers/signal_orchestrator.py",
            "main_auto_trading.py"
        ]

        for file_path in files:
            full_path = project_root / file_path
            try:
                py_compile.compile(str(full_path), doraise=True)
                self.log("문법", file_path, True, "컴파일 성공")
            except Exception as e:
                self.log("문법", file_path, False, f"컴파일 실패: {e}")

    def validate_imports(self):
        """모듈 임포트 검증"""
        console.print("\n[bold cyan]2. 모듈 임포트 검증[/bold cyan]")

        try:
            from analyzers.volume_indicators import attach_rsvi_indicators, calculate_rsvi_score
            self.log("임포트", "volume_indicators", True, "attach_rsvi_indicators, calculate_rsvi_score")
        except Exception as e:
            self.log("임포트", "volume_indicators", False, str(e))

        try:
            from analyzers.pre_trade_validator_v2 import PreTradeValidatorV2
            self.log("임포트", "pre_trade_validator_v2", True, "PreTradeValidatorV2")
        except Exception as e:
            self.log("임포트", "pre_trade_validator_v2", False, str(e))

        try:
            from analyzers.signal_orchestrator import SignalOrchestrator
            self.log("임포트", "signal_orchestrator", True, "SignalOrchestrator")
        except Exception as e:
            self.log("임포트", "signal_orchestrator", False, str(e))

        try:
            from trading.filters.base_filter import FilterResult
            self.log("임포트", "base_filter", True, "FilterResult")
        except Exception as e:
            self.log("임포트", "base_filter", False, str(e))

    def validate_integration_path(self):
        """통합 경로 검증"""
        console.print("\n[bold cyan]3. 통합 경로 검증[/bold cyan]")

        try:
            from analyzers.signal_orchestrator import SignalOrchestrator
            from analyzers.pre_trade_validator_v2 import PreTradeValidatorV2
            from utils.config_loader import ConfigLoader

            config = ConfigLoader()
            orchestrator = SignalOrchestrator(config=config, api=None)

            # validator 속성 확인
            if hasattr(orchestrator, 'validator'):
                if isinstance(orchestrator.validator, PreTradeValidatorV2):
                    self.log("통합", "SignalOrchestrator.validator", True, "PreTradeValidatorV2 연결 확인")
                else:
                    self.log("통합", "SignalOrchestrator.validator", False, f"타입 불일치: {type(orchestrator.validator)}")
            else:
                self.log("통합", "SignalOrchestrator.validator", False, "validator 속성 없음")

            # check_with_confidence 메서드 확인
            if hasattr(orchestrator.validator, 'check_with_confidence'):
                self.log("통합", "check_with_confidence", True, "메서드 존재")
            else:
                self.log("통합", "check_with_confidence", False, "메서드 없음")

        except Exception as e:
            self.log("통합", "통합 경로", False, str(e))

    def validate_rsvi_logic(self):
        """RSVI 계산 로직 검증"""
        console.print("\n[bold cyan]4. RSVI 계산 로직 검증[/bold cyan]")

        try:
            import pandas as pd
            import numpy as np
            from analyzers.volume_indicators import attach_rsvi_indicators, calculate_rsvi_score

            # 정상 데이터
            df = pd.DataFrame({'volume': [100, 120, 150, 180, 200] * 5})
            df = attach_rsvi_indicators(df)

            required_cols = ['vol_ma20', 'vol_std20', 'vol_z20', 'vroc10']
            if all(col in df.columns for col in required_cols):
                self.log("RSVI", "필수 컬럼 생성", True, ", ".join(required_cols))
            else:
                missing = [col for col in required_cols if col not in df.columns]
                self.log("RSVI", "필수 컬럼 생성", False, f"누락: {missing}")

            # 클리핑 확인
            latest = df.iloc[-1]
            vol_z20 = latest['vol_z20']
            vroc10 = latest['vroc10']

            if -5.0 <= vol_z20 <= 5.0:
                self.log("RSVI", "vol_z20 클리핑", True, f"값: {vol_z20:.2f}")
            else:
                self.log("RSVI", "vol_z20 클리핑", False, f"범위 초과: {vol_z20:.2f}")

            if -5.0 <= vroc10 <= 5.0:
                self.log("RSVI", "vroc10 클리핑", True, f"값: {vroc10:.2f}")
            else:
                self.log("RSVI", "vroc10 클리핑", False, f"범위 초과: {vroc10:.2f}")

            # RSVI 점수 범위
            rsvi_score = calculate_rsvi_score(vol_z20, vroc10)
            if 0.0 <= rsvi_score <= 1.0:
                self.log("RSVI", "점수 범위", True, f"값: {rsvi_score:.2f}")
            else:
                self.log("RSVI", "점수 범위", False, f"범위 초과: {rsvi_score:.2f}")

        except Exception as e:
            self.log("RSVI", "RSVI 로직", False, str(e))

    def validate_chatgpt_fixes(self):
        """ChatGPT 제안 수정사항 확인"""
        console.print("\n[bold cyan]5. ChatGPT 제안 수정사항 확인[/bold cyan]")

        # Fix 1: VROC fillna(-1.0)
        try:
            with open(project_root / "analyzers/volume_indicators.py", "r") as f:
                content = f.read()
                if "fillna(-1.0)" in content and "유동성 없음" in content:
                    self.log("수정", "VROC fillna(-1.0)", True, "저유동성 처리 확인")
                else:
                    self.log("수정", "VROC fillna(-1.0)", False, "코드 미발견")
        except Exception as e:
            self.log("수정", "VROC fillna(-1.0)", False, str(e))

        # Fix 2: Clipping
        try:
            with open(project_root / "analyzers/volume_indicators.py", "r") as f:
                content = f.read()
                if "clip(lower=-5.0, upper=5.0)" in content:
                    self.log("수정", "극단값 클리핑", True, "vol_z20, vroc10 클리핑 확인")
                else:
                    self.log("수정", "극단값 클리핑", False, "클리핑 코드 미발견")
        except Exception as e:
            self.log("수정", "극단값 클리핑", False, str(e))

        # Fix 3: backtest_conf or 0.0
        try:
            with open(project_root / "analyzers/pre_trade_validator_v2.py", "r") as f:
                content = f.read()
                if "backtest_conf = backtest_conf or 0.0" in content:
                    self.log("수정", "backtest_conf None 처리", True, "방어 코드 확인")
                else:
                    self.log("수정", "backtest_conf None 처리", False, "코드 미발견")
        except Exception as e:
            self.log("수정", "backtest_conf None 처리", False, str(e))

        # Fix 4: DataFrame 정렬
        try:
            with open(project_root / "analyzers/pre_trade_validator_v2.py", "r") as f:
                content = f.read()
                if "sort_values(by='datetime')" in content and "sort_index()" in content:
                    self.log("수정", "DataFrame 정렬", True, "역순 데이터 처리 확인")
                else:
                    self.log("수정", "DataFrame 정렬", False, "정렬 코드 미발견")
        except Exception as e:
            self.log("수정", "DataFrame 정렬", False, str(e))

        # Fix 5: Safety Gate
        try:
            with open(project_root / "analyzers/pre_trade_validator_v2.py", "r") as f:
                content = f.read()
                if "BACKTEST_MIN_THRESHOLD = 0.1" in content and "Safety Gate" in content:
                    self.log("수정", "Safety Gate", True, "백테스트 과락 차단 확인")
                else:
                    self.log("수정", "Safety Gate", False, "Safety Gate 코드 미발견")
        except Exception as e:
            self.log("수정", "Safety Gate", False, str(e))

    def validate_rsvi_hard_cut(self):
        """RSVI 하드컷 확인"""
        console.print("\n[bold cyan]6. RSVI 하드컷 확인[/bold cyan]")

        try:
            with open(project_root / "analyzers/pre_trade_validator_v2.py", "r") as f:
                content = f.read()
                if "vol_z20 < -1.0 and vroc10 < -0.5" in content:
                    self.log("하드컷", "RSVI 하드컷 조건", True, "vol_z20 < -1.0 AND vroc10 < -0.5")
                else:
                    self.log("하드컷", "RSVI 하드컷 조건", False, "하드컷 코드 미발견")

                if "RSVI 하드컷" in content:
                    self.log("하드컷", "하드컷 메시지", True, "차단 사유 로깅 확인")
                else:
                    self.log("하드컷", "하드컷 메시지", False, "메시지 미발견")
        except Exception as e:
            self.log("하드컷", "RSVI 하드컷", False, str(e))

    def validate_confidence_calculation(self):
        """Confidence 계산 확인"""
        console.print("\n[bold cyan]7. Confidence 계산 확인[/bold cyan]")

        try:
            with open(project_root / "analyzers/pre_trade_validator_v2.py", "r") as f:
                content = f.read()

                if "0.3 * backtest_conf" in content and "0.7 * rsvi_score" in content:
                    self.log("계산", "가중치 (0.3*BT + 0.7*RSVI)", True, "Phase 1 가중치 확인")
                else:
                    self.log("계산", "가중치", False, "가중치 코드 미발견")

                if "threshold = 0.4" in content:
                    self.log("계산", "Threshold", True, "0.4 기준 확인")
                else:
                    self.log("계산", "Threshold", False, "Threshold 미발견")

        except Exception as e:
            self.log("계산", "Confidence 계산", False, str(e))

    def print_summary(self):
        """결과 요약 출력"""
        console.print("\n" + "=" * 80)
        console.print("[bold cyan]📊 최종 검증 결과[/bold cyan]")
        console.print("=" * 80 + "\n")

        # 카테고리별 집계
        categories = {}
        for result in self.results:
            cat = result['category']
            if cat not in categories:
                categories[cat] = {'passed': 0, 'failed': 0}

            if result['passed']:
                categories[cat]['passed'] += 1
            else:
                categories[cat]['failed'] += 1

        # 테이블 생성
        table = Table(show_header=True, header_style="bold magenta")
        table.add_column("카테고리", style="cyan", width=15)
        table.add_column("통과", justify="right", style="green", width=10)
        table.add_column("실패", justify="right", style="red", width=10)
        table.add_column("통과율", justify="right", width=10)

        total_passed = 0
        total_failed = 0

        for cat, counts in categories.items():
            passed = counts['passed']
            failed = counts['failed']
            total = passed + failed
            rate = (passed / total * 100) if total > 0 else 0

            table.add_row(
                cat,
                str(passed),
                str(failed),
                f"{rate:.0f}%"
            )

            total_passed += passed
            total_failed += failed

        # 합계
        total_all = total_passed + total_failed
        total_rate = (total_passed / total_all * 100) if total_all > 0 else 0

        table.add_row(
            "[bold]전체[/bold]",
            f"[bold]{total_passed}[/bold]",
            f"[bold]{total_failed}[/bold]",
            f"[bold]{total_rate:.0f}%[/bold]"
        )

        console.print(table)
        console.print()

        # 최종 판정
        if total_failed == 0:
            summary_text = """
✅ 모든 검증 항목 통과!
🚀 RSVI Phase 1 실거래 투입 준비 완료

적용 방법:
1. pkill -f "main_auto_trading.py"
2. ./run.sh
3. tail -f logs/trading_*.log | grep "RSVI\\|L6"

모니터링 포인트:
- RSVI 하드컷 발동 빈도
- L6+RSVI 통과율
- Confidence 분포 (0.4 기준)
- Safety Gate 발동 빈도
- 승률 개선 추이 (목표: 8.9% → 25%+)
            """.strip()
            console.print(Panel(summary_text, title="🎉 최종 검증 완료", style="bold green", expand=False))
        else:
            summary_text = f"""
❌ {total_failed}개 항목 실패
⚠️  실거래 투입 전 수정 필요

실패 항목을 확인하고 수정하세요.
            """.strip()
            console.print(Panel(summary_text, title="⚠️ 검증 실패", style="bold red", expand=False))

            # 실패 항목 상세
            console.print("\n[bold red]실패 항목 상세:[/bold red]\n")
            for result in self.results:
                if not result['passed']:
                    console.print(f"  [red]✗[/red] [{result['category']}] {result['item']}")
                    if result['detail']:
                        console.print(f"     [dim]{result['detail']}[/dim]")

    def run_all_validations(self):
        """모든 검증 실행"""
        console.print("\n[bold green]" + "=" * 80 + "[/bold green]")
        console.print("[bold green]🔍 RSVI Phase 1 최종 검증[/bold green]")
        console.print("[bold green]" + "=" * 80 + "[/bold green]")

        self.validate_syntax()
        self.validate_imports()
        self.validate_integration_path()
        self.validate_rsvi_logic()
        self.validate_chatgpt_fixes()
        self.validate_rsvi_hard_cut()
        self.validate_confidence_calculation()

        self.print_summary()


if __name__ == "__main__":
    validator = FinalValidator()
    validator.run_all_validations()
