#!/usr/bin/env python3
"""
실전 투입 전 최종 점검 스크립트

모든 시스템 컴포넌트를 검증하고 실전 준비 상태를 확인합니다.
"""

import sys
from pathlib import Path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

import asyncio
import pandas as pd
import numpy as np
from datetime import datetime
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
import traceback

console = Console()


class PreLaunchChecker:
    """실전 투입 전 최종 점검"""

    def __init__(self):
        self.checks = []
        self.errors = []
        self.warnings = []

    def add_check(self, category: str, item: str, status: str, details: str = ""):
        """점검 항목 추가"""
        self.checks.append({
            "category": category,
            "item": item,
            "status": status,
            "details": details
        })

    def add_error(self, message: str):
        """에러 추가"""
        self.errors.append(message)

    def add_warning(self, message: str):
        """경고 추가"""
        self.warnings.append(message)

    async def check_config_files(self):
        """설정 파일 확인"""
        console.print("\n[cyan]1️⃣  설정 파일 점검...[/cyan]")

        try:
            from config.config_manager import ConfigManager

            # Config Manager 초기화
            config_manager = ConfigManager()
            config = config_manager.load('config/trading_config.yaml', environment='development')

            # 주요 설정 확인
            if not config:
                self.add_check("설정", "trading_config.yaml", "❌", "설정 파일 로드 실패")
                self.add_error("설정 파일을 로드할 수 없습니다")
                return False

            self.add_check("설정", "trading_config.yaml", "✅", "설정 파일 로드 성공")

            # 필수 설정 항목 확인
            required_keys = ['trading', 'data', 'validation', 'monitoring']
            for key in required_keys:
                if key in config:
                    self.add_check("설정", f"{key} 섹션", "✅", f"존재")
                else:
                    self.add_check("설정", f"{key} 섹션", "⚠️", "누락")
                    self.add_warning(f"설정 섹션 '{key}'이 누락되었습니다")

            # 리스크 관리 설정 확인
            if 'trading' in config and 'risk_management' in config['trading']:
                risk_config = config['trading']['risk_management']
                console.print(f"   💰 일일 최대 손실: {risk_config.get('max_daily_loss', 'N/A')}")
                console.print(f"   💰 최대 포지션 크기: {risk_config.get('max_position_size', 'N/A')}")
                console.print(f"   💰 손절: {risk_config.get('stop_loss', 'N/A')}")

            return True

        except Exception as e:
            self.add_check("설정", "Config Manager", "❌", str(e))
            self.add_error(f"설정 파일 점검 실패: {e}")
            console.print(f"   [red]❌ 오류: {e}[/red]")
            return False

    async def check_signal_orchestrator(self):
        """SignalOrchestrator 점검"""
        console.print("\n[cyan]2️⃣  SignalOrchestrator 점검...[/cyan]")

        try:
            from analyzers.signal_orchestrator import SignalOrchestrator
            from config.config_manager import ConfigManager

            # Config 로드
            config_manager = ConfigManager()
            config = config_manager.load('config/trading_config.yaml', environment='development')

            # Orchestrator 초기화
            orchestrator = SignalOrchestrator(config)

            self.add_check("신호 생성", "SignalOrchestrator", "✅", "초기화 성공")

            # 알파 엔진 확인
            if hasattr(orchestrator, 'alpha_engine') and orchestrator.alpha_engine:
                num_alphas = len(orchestrator.alpha_engine.alphas)
                self.add_check("신호 생성", "Alpha Engine", "✅", f"{num_alphas}개 알파 로드")
                console.print(f"   ✅ Alpha Engine: {num_alphas}개 알파 로드됨")

                # 개별 알파 확인
                for alpha in orchestrator.alpha_engine.alphas:
                    alpha_name = alpha.__class__.__name__
                    alpha_weight = getattr(alpha, 'weight', 'N/A')
                    console.print(f"      - {alpha_name}: weight={alpha_weight}")
            else:
                self.add_check("신호 생성", "Alpha Engine", "❌", "Alpha Engine 없음")
                self.add_error("Alpha Engine이 초기화되지 않았습니다")

            # Dynamic Weight Adjuster 확인
            if hasattr(orchestrator, 'weight_adjuster') and orchestrator.weight_adjuster:
                self.add_check("신호 생성", "Dynamic Weight Adjuster", "✅", "활성화됨")
                console.print(f"   ✅ Dynamic Weight Adjuster 활성화")

                current_regime = orchestrator.current_regime
                console.print(f"   📊 현재 Regime: {current_regime}")

                # 현재 가중치 출력
                console.print(f"   📊 현재 가중치:")
                for alpha_name, weight in orchestrator.current_weights.items():
                    console.print(f"      - {alpha_name}: {weight}")
            else:
                self.add_check("신호 생성", "Dynamic Weight Adjuster", "⚠️", "비활성화")
                self.add_warning("Dynamic Weight Adjuster가 비활성화되어 있습니다")

            return True

        except Exception as e:
            self.add_check("신호 생성", "SignalOrchestrator", "❌", str(e))
            self.add_error(f"SignalOrchestrator 점검 실패: {e}")
            console.print(f"   [red]❌ 오류: {e}[/red]")
            traceback.print_exc()
            return False

    async def check_alphas(self):
        """8개 알파 개별 점검"""
        console.print("\n[cyan]3️⃣  개별 알파 점검...[/cyan]")

        try:
            from trading.alphas.vwap_alpha import VWAPAlpha
            from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha
            from trading.alphas.obv_trend_alpha import OBVTrendAlpha
            from trading.alphas.institutional_flow_alpha import InstitutionalFlowAlpha
            from trading.alphas.news_score_alpha import NewsScoreAlpha
            from trading.alphas.momentum_alpha import MomentumAlpha
            from trading.alphas.mean_reversion_alpha import MeanReversionAlpha
            from trading.alphas.volatility_alpha import VolatilityAlpha

            alphas = [
                ("VWAPAlpha", VWAPAlpha),
                ("VolumeSpikeAlpha", VolumeSpikeAlpha),
                ("OBVTrendAlpha", OBVTrendAlpha),
                ("InstitutionalFlowAlpha", InstitutionalFlowAlpha),
                ("NewsScoreAlpha", NewsScoreAlpha),
                ("MomentumAlpha", MomentumAlpha),
                ("MeanReversionAlpha", MeanReversionAlpha),
                ("VolatilityAlpha", VolatilityAlpha),
            ]

            for alpha_name, alpha_class in alphas:
                try:
                    # 알파 인스턴스 생성 테스트
                    alpha = alpha_class(weight=1.0)
                    self.add_check("알파", alpha_name, "✅", "로드 성공")
                    console.print(f"   ✅ {alpha_name}")
                except Exception as e:
                    self.add_check("알파", alpha_name, "❌", str(e))
                    self.add_error(f"{alpha_name} 로드 실패: {e}")
                    console.print(f"   [red]❌ {alpha_name}: {e}[/red]")

            return True

        except Exception as e:
            self.add_error(f"알파 점검 실패: {e}")
            console.print(f"   [red]❌ 오류: {e}[/red]")
            return False

    async def check_api_connection(self):
        """키움 API 연결 테스트"""
        console.print("\n[cyan]4️⃣  키움 API 연결 테스트...[/cyan]")

        try:
            # API 모듈은 선택적 의존성 - 없어도 됨
            try:
                from api.kiwoom_api import KiwoomAPI
                self.add_check("API", "KiwoomAPI 모듈", "✅", "모듈 존재")
                console.print(f"   ✅ KiwoomAPI 모듈 존재")
            except ImportError:
                self.add_check("API", "KiwoomAPI 모듈", "⚠️", "모듈 없음 (선택적)")
                self.add_warning("KiwoomAPI 모듈이 없습니다 (실전 투입 시 필요)")
                console.print(f"   ⚠️  KiwoomAPI 모듈 없음 (선택적)")

            # 실제 연결 테스트는 장 시작 전에 수동으로 해야 함
            self.add_check("API", "실제 연결 테스트", "⚠️", "장 시작 전 수동 테스트 필요")
            self.add_warning("실제 API 연결은 장 시작 전에 수동으로 테스트하세요")
            console.print(f"   ⚠️  실제 연결은 장 시작 전 수동 테스트 필요")

            return True

        except Exception as e:
            self.add_check("API", "API 점검", "❌", str(e))
            self.add_error(f"API 점검 실패: {e}")
            console.print(f"   [red]❌ 오류: {e}[/red]")
            return False

    async def check_database(self):
        """데이터베이스 점검"""
        console.print("\n[cyan]5️⃣  데이터베이스 점검...[/cyan]")

        try:
            # Database 모듈은 선택적 의존성
            try:
                from db.database import Database
                db = Database()
                self.add_check("데이터베이스", "Database 클래스", "✅", "초기화 성공")
                console.print(f"   ✅ Database 클래스 초기화 성공")
            except ImportError:
                self.add_check("데이터베이스", "Database 모듈", "⚠️", "모듈 없음 (선택적)")
                self.add_warning("Database 모듈이 없습니다 (선택적)")
                console.print(f"   ⚠️  Database 모듈 없음 (선택적)")

            # 테이블 존재 확인 (간단히)
            db_path = Path(project_root) / "data" / "trading.db"
            if db_path.exists():
                self.add_check("데이터베이스", "trading.db 파일", "✅", f"크기: {db_path.stat().st_size / 1024:.1f}KB")
                console.print(f"   ✅ trading.db 파일 존재 (크기: {db_path.stat().st_size / 1024:.1f}KB)")
            else:
                self.add_check("데이터베이스", "trading.db 파일", "⚠️", "파일 없음 (첫 실행 시 생성됨)")
                console.print(f"   ⚠️  trading.db 파일 없음 (첫 실행 시 생성됨)")

            return True

        except Exception as e:
            self.add_check("데이터베이스", "Database", "❌", str(e))
            self.add_error(f"데이터베이스 점검 실패: {e}")
            console.print(f"   [red]❌ 오류: {e}[/red]")
            return False

    async def check_risk_management(self):
        """리스크 관리 점검"""
        console.print("\n[cyan]6️⃣  리스크 관리 시스템 점검...[/cyan]")

        try:
            from config.config_manager import ConfigManager

            config_manager = ConfigManager()
            config = config_manager.load('config/trading_config.yaml', environment='development')

            if 'trading' not in config or 'risk_management' not in config['trading']:
                self.add_check("리스크", "risk_management 설정", "❌", "설정 없음")
                self.add_error("리스크 관리 설정이 없습니다")
                return False

            risk_config = config['trading']['risk_management']

            # 필수 리스크 설정 확인
            required_risk_keys = [
                'max_daily_loss',
                'max_position_size',
                'stop_loss',
                'take_profit',
            ]

            for key in required_risk_keys:
                if key in risk_config:
                    value = risk_config[key]
                    self.add_check("리스크", key, "✅", f"{value}")
                    console.print(f"   ✅ {key}: {value}")
                else:
                    self.add_check("리스크", key, "⚠️", "설정 없음")
                    self.add_warning(f"리스크 설정 '{key}'이 없습니다")
                    console.print(f"   ⚠️  {key}: 설정 없음")

            # 리스크 설정 값 검증
            max_daily_loss = risk_config.get('max_daily_loss', 0)
            if max_daily_loss <= 0:
                self.add_warning("max_daily_loss가 0 이하입니다. 실전에서는 반드시 설정하세요!")
                console.print(f"   [yellow]⚠️  max_daily_loss가 0 이하입니다![/yellow]")

            return True

        except Exception as e:
            self.add_check("리스크", "Risk Management", "❌", str(e))
            self.add_error(f"리스크 관리 점검 실패: {e}")
            console.print(f"   [red]❌ 오류: {e}[/red]")
            return False

    async def check_logging_system(self):
        """로깅 시스템 점검"""
        console.print("\n[cyan]7️⃣  로깅 시스템 점검...[/cyan]")

        try:
            # 로그 디렉토리 확인
            log_dir = Path(project_root) / "logs"
            if log_dir.exists():
                self.add_check("로깅", "logs 디렉토리", "✅", "존재")
                console.print(f"   ✅ logs 디렉토리 존재")

                # 최근 로그 파일 확인
                log_files = list(log_dir.glob("*.log"))
                if log_files:
                    latest_log = max(log_files, key=lambda f: f.stat().st_mtime)
                    console.print(f"   📝 최근 로그: {latest_log.name}")
            else:
                self.add_check("로깅", "logs 디렉토리", "⚠️", "없음 (자동 생성됨)")
                self.add_warning("logs 디렉토리가 없습니다 (첫 실행 시 생성됨)")

            # data 디렉토리 확인
            data_dir = Path(project_root) / "data"
            if data_dir.exists():
                self.add_check("로깅", "data 디렉토리", "✅", "존재")
                console.print(f"   ✅ data 디렉토리 존재")

                # watchlist, risk_log 확인
                watchlist_file = data_dir / "watchlist.json"
                risk_log_file = data_dir / "risk_log.json"

                if watchlist_file.exists():
                    console.print(f"   📋 watchlist.json 존재")
                if risk_log_file.exists():
                    console.print(f"   📋 risk_log.json 존재")
            else:
                self.add_check("로깅", "data 디렉토리", "⚠️", "없음")
                self.add_warning("data 디렉토리가 없습니다")

            return True

        except Exception as e:
            self.add_check("로깅", "Logging System", "❌", str(e))
            self.add_error(f"로깅 시스템 점검 실패: {e}")
            console.print(f"   [red]❌ 오류: {e}[/red]")
            return False

    async def check_signal_generation(self):
        """신호 생성 테스트"""
        console.print("\n[cyan]8️⃣  신호 생성 통합 테스트...[/cyan]")

        try:
            from analyzers.signal_orchestrator import SignalOrchestrator
            from config.config_manager import ConfigManager

            # Config 로드
            config_manager = ConfigManager()
            config = config_manager.load('config/trading_config.yaml', environment='development')

            # Orchestrator 초기화
            orchestrator = SignalOrchestrator(config)

            # 테스트 데이터 생성
            console.print(f"   🧪 테스트 데이터로 신호 생성 테스트 중...")

            dates = pd.date_range(end=datetime.now(), periods=60, freq='5min')
            test_df = pd.DataFrame({
                'date': dates,
                'open': 50000 + np.random.randint(-500, 500, 60),
                'high': 50500 + np.random.randint(-500, 500, 60),
                'low': 49500 + np.random.randint(-500, 500, 60),
                'close': 50000 + np.random.randint(-500, 500, 60),
                'volume': 10000 + np.random.randint(-2000, 2000, 60),
            })

            # 대문자 컬럼 추가
            test_df['Close'] = test_df['close']
            test_df['High'] = test_df['high']
            test_df['Low'] = test_df['low']
            test_df['Volume'] = test_df['volume']
            test_df['vwap'] = (test_df['close'] * test_df['volume']).cumsum() / test_df['volume'].cumsum()
            test_df['ma20'] = test_df['close'].rolling(20).mean()

            # OBV 계산
            test_df['obv'] = 0.0
            for i in range(1, len(test_df)):
                if test_df['close'].iloc[i] > test_df['close'].iloc[i-1]:
                    test_df.loc[test_df.index[i], 'obv'] = test_df['obv'].iloc[i-1] + test_df['volume'].iloc[i]
                elif test_df['close'].iloc[i] < test_df['close'].iloc[i-1]:
                    test_df.loc[test_df.index[i], 'obv'] = test_df['obv'].iloc[i-1] - test_df['volume'].iloc[i]
                else:
                    test_df.loc[test_df.index[i], 'obv'] = test_df['obv'].iloc[i-1]

            state = {
                "df": test_df,
                "df_5m": test_df,
                "current_price": test_df['close'].iloc[-1],
                "institutional_flow": {
                    "inst_net_buy": 50000000,
                    "foreign_net_buy": 30000000,
                    "total_traded_value": 1000000000
                },
                "ai_analysis": {
                    "news_score": 70
                }
            }

            # 신호 생성 - 올바른 파라미터로 호출
            current_price = test_df['close'].iloc[-1]
            result = orchestrator.evaluate_signal(
                stock_code="TEST",
                stock_name="테스트",
                current_price=current_price,
                df=test_df,
                market='KOSPI',
                current_cash=10000000,
                daily_pnl=0
            )

            if result:
                tier = result.get('tier', 'UNKNOWN')
                aggregate_score = result.get('aggregate_score', 0)

                self.add_check("신호 생성", "통합 테스트", "✅", f"Tier: {tier}, Score: {aggregate_score:+.2f}")
                console.print(f"   ✅ 신호 생성 성공: Tier={tier}, Score={aggregate_score:+.2f}")

                # 개별 알파 스코어 확인
                if 'weighted_scores' in result:
                    console.print(f"   📊 개별 알파 스코어:")
                    for alpha_name, alpha_data in result['weighted_scores'].items():
                        score = alpha_data.get('weighted_contribution', 0)
                        console.print(f"      - {alpha_name}: {score:+.2f}")
            else:
                self.add_check("신호 생성", "통합 테스트", "❌", "신호 생성 실패")
                self.add_error("신호 생성 테스트 실패")

            return True

        except Exception as e:
            self.add_check("신호 생성", "통합 테스트", "❌", str(e))
            self.add_error(f"신호 생성 테스트 실패: {e}")
            console.print(f"   [red]❌ 오류: {e}[/red]")
            traceback.print_exc()
            return False

    def generate_report(self):
        """최종 점검 리포트 생성"""
        console.print("\n")
        console.print("=" * 120)
        console.print("📋 실전 투입 전 최종 점검 리포트", style="bold green")
        console.print("=" * 120)
        console.print()

        # 점검 결과 테이블
        check_table = Table(title="점검 결과", show_header=True, header_style="bold magenta")
        check_table.add_column("카테고리", style="cyan", width=15)
        check_table.add_column("항목", style="yellow", width=25)
        check_table.add_column("상태", justify="center", width=8)
        check_table.add_column("세부사항", style="dim", width=50)

        for check in self.checks:
            check_table.add_row(
                check['category'],
                check['item'],
                check['status'],
                check['details']
            )

        console.print(check_table)
        console.print()

        # 에러 요약
        if self.errors:
            console.print("[red]❌ 에러 (실전 투입 전 반드시 해결 필요):[/red]")
            for i, error in enumerate(self.errors, 1):
                console.print(f"   {i}. {error}")
            console.print()

        # 경고 요약
        if self.warnings:
            console.print("[yellow]⚠️  경고 (확인 권장):[/yellow]")
            for i, warning in enumerate(self.warnings, 1):
                console.print(f"   {i}. {warning}")
            console.print()

        # 최종 판정
        if self.errors:
            console.print("=" * 120)
            console.print("❌ 실전 투입 불가 - 에러를 먼저 해결하세요", style="bold red")
            console.print("=" * 120)
            return False
        elif self.warnings:
            console.print("=" * 120)
            console.print("⚠️  실전 투입 주의 - 경고 사항을 확인하세요", style="bold yellow")
            console.print("=" * 120)
            return True
        else:
            console.print("=" * 120)
            console.print("✅ 실전 투입 준비 완료!", style="bold green")
            console.print("=" * 120)
            return True

    def print_final_checklist(self):
        """장 시작 전 최종 체크리스트"""
        console.print()
        console.print("=" * 120)
        console.print("📝 장 시작 전 최종 체크리스트", style="bold cyan")
        console.print("=" * 120)
        console.print()

        checklist = [
            ("키움증권 OpenAPI 로그인", "키움 Hero를 실행하고 로그인하세요"),
            ("계좌 비밀번호 확인", "자동 매매용 계좌 비밀번호를 확인하세요"),
            ("잔고 확인", "투자 가능한 잔고를 확인하세요"),
            ("리스크 설정 재확인", "max_daily_loss, stop_loss_pct 등을 재확인하세요"),
            ("로그 모니터링 준비", "로그 파일을 실시간으로 확인할 준비를 하세요"),
            ("긴급 종료 방법 숙지", "문제 발생 시 즉시 프로그램을 종료하는 방법을 알아두세요"),
            ("백업 계획", "중요 데이터는 정기적으로 백업하세요"),
            ("작은 금액으로 시작", "첫 실전은 최소 금액으로 시작하세요"),
        ]

        for i, (item, description) in enumerate(checklist, 1):
            console.print(f"  [{i}] {item}")
            console.print(f"      → {description}")
            console.print()

        console.print("=" * 120)
        console.print()

    async def run_all_checks(self):
        """모든 점검 실행"""
        console.print()
        console.print("=" * 120)
        console.print("🚀 실전 투입 전 최종 점검 시작", style="bold green")
        console.print("=" * 120)

        # 모든 점검 실행
        await self.check_config_files()
        await self.check_signal_orchestrator()
        await self.check_alphas()
        await self.check_api_connection()
        await self.check_database()
        await self.check_risk_management()
        await self.check_logging_system()
        await self.check_signal_generation()

        # 리포트 생성
        ready = self.generate_report()

        # 최종 체크리스트
        self.print_final_checklist()

        return ready


async def main():
    """메인 실행 함수"""
    checker = PreLaunchChecker()

    try:
        ready = await checker.run_all_checks()

        if ready:
            console.print("[bold green]✅ 시스템 점검 완료! 실전 투입 가능합니다.[/bold green]")
            console.print()
            console.print("[yellow]⚠️  하지만 작은 금액으로 시작하고, 실시간으로 모니터링하세요![/yellow]")
        else:
            console.print("[bold red]❌ 시스템 점검 실패! 에러를 해결한 후 다시 점검하세요.[/bold red]")

    except KeyboardInterrupt:
        console.print()
        console.print("[yellow]⚠️  점검이 중단되었습니다.[/yellow]")
    except Exception as e:
        console.print()
        console.print(f"[red]❌ 점검 중 오류 발생: {e}[/red]")
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
