#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Squeeze Momentum Pro 통합 시스템 테스트
"""

import asyncio
import sys
from pathlib import Path
from datetime import datetime
import json

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.progress import track

console = Console()

class SqueezeMomentumIntegrationTester:
    """Squeeze Momentum Pro 통합 테스트"""
    
    def __init__(self):
        self.test_results = {}
        
    async def run_full_integration_test(self):
        """전체 통합 테스트 실행"""
        console.print(Panel("[bold cyan]Squeeze Momentum Pro 통합 테스트 시작[/bold cyan]", border_style="cyan"))
        
        try:
            # 1. 2차 필터링 엔진 테스트
            await self._test_recommendation_engine()
            
            # 2. 종목 추천 시스템 테스트
            await self._test_recommendation_system()
            
            # 3. Phase 1 통합 테스트
            await self._test_phase1_integration()
            
            # 4. 성능 벤치마크
            await self._test_performance_benchmark()
            
            # 5. 결과 리포트
            self._generate_test_report()
            
        except Exception as e:
            console.print(f"[red]통합 테스트 실패: {e}[/red]")
    
    async def _test_recommendation_engine(self):
        """추천 엔진 테스트"""
        console.print("\n[yellow]1. Squeeze Momentum 추천 엔진 테스트...[/yellow]")
        
        try:
            from recommendations.squeeze_momentum_engine import SqueezeMomentumRecommendationEngine, SqueezeMomentumCandidate
            
            # 엔진 초기화
            engine = SqueezeMomentumRecommendationEngine()
            
            # 테스트 후보 데이터 생성
            test_candidates = [
                SqueezeMomentumCandidate(
                    symbol='TEST001',
                    name='테스트종목1',
                    current_price=50000,
                    squeeze_duration=8,
                    squeeze_release_signal=True,
                    momentum_strength=0.8,
                    momentum_direction='bullish',
                    bb_squeeze_ratio=0.9,
                    kc_position=0.7,
                    volume_ratio=2.0,
                    atr_percentile=0.75,
                    sector='테스트',
                    market_cap=1000000000000,
                    trading_value=2000000000,
                    volatility=0.04
                ),
                SqueezeMomentumCandidate(
                    symbol='TEST002',
                    name='테스트종목2',
                    current_price=30000,
                    squeeze_duration=5,
                    squeeze_release_signal=True,
                    momentum_strength=0.6,
                    momentum_direction='bullish',
                    bb_squeeze_ratio=0.7,
                    kc_position=0.5,
                    volume_ratio=1.5,
                    atr_percentile=0.6,
                    sector='테스트',
                    market_cap=500000000000,
                    trading_value=1000000000,
                    volatility=0.03
                )
            ]
            
            # 2차 필터링 실행
            results = await engine.apply_secondary_filter(test_candidates)
            
            # 결과 검증
            self.test_results['recommendation_engine'] = {
                'candidates_input': len(test_candidates),
                'recommendations_output': len(results),
                'success': len(results) > 0,
                'processing_stats': engine.get_processing_statistics()
            }
            
            status = "[green]PASS[/green]" if len(results) > 0 else "[red]FAIL[/red]"
            console.print(f"추천 엔진 테스트: 입력 {len(test_candidates)}개 → 출력 {len(results)}개 - {status}")
            
        except Exception as e:
            console.print(f"[red]추천 엔진 테스트 실패: {e}[/red]")
            self.test_results['recommendation_engine'] = {'success': False, 'error': str(e)}
    
    async def _test_recommendation_system(self):
        """종목 추천 시스템 테스트"""
        console.print("\n[yellow]2. 종목 추천 시스템 테스트...[/yellow]")
        
        try:
            from recommendations.stock_recommendation_system import StockRecommendationSystem
            
            # 시스템 초기화
            system = StockRecommendationSystem()
            
            # 일일 추천 생성 테스트
            daily_report = await system.generate_daily_recommendations()
            
            # 시스템 상태 확인
            system_status = system.get_system_status()
            
            # 결과 검증
            total_recommendations = sum(
                len(candidates) for candidates in daily_report['recommendations_by_grade'].values()
            )
            
            self.test_results['recommendation_system'] = {
                'daily_report_generated': 'date' in daily_report,
                'total_recommendations': total_recommendations,
                'grade_distribution': {
                    grade: len(candidates) 
                    for grade, candidates in daily_report['recommendations_by_grade'].items()
                },
                'system_status': system_status['system_status'],
                'success': total_recommendations > 0
            }
            
            status = "[green]PASS[/green]" if total_recommendations > 0 else "[red]FAIL[/red]"
            console.print(f"추천 시스템 테스트: {total_recommendations}개 종목 추천 생성 - {status}")
            
            # 등급별 분포 출력
            for grade, candidates in daily_report['recommendations_by_grade'].items():
                if candidates:
                    console.print(f"  {grade}: {len(candidates)}개")
            
        except Exception as e:
            console.print(f"[red]추천 시스템 테스트 실패: {e}[/red]")
            self.test_results['recommendation_system'] = {'success': False, 'error': str(e)}
    
    async def _test_phase1_integration(self):
        """Phase 1 통합 테스트"""
        console.print("\n[yellow]3. Phase 1 품질 게이트 통합 테스트...[/yellow]")
        
        try:
            # Phase 1 모듈 개별 테스트
            from signal_processing.consensus_engine import ConsensusEngine
            from signal_processing.mtf_analyzer import MTFAnalyzer
            from signal_processing.liquidity_gate import LiquidityGate
            from signal_processing.news_gate import NewsGate
            from signal_processing.regime_gate import RegimeGate
            
            phase1_tests = {}
            
            # 1. ConsensusEngine 테스트
            strategies = ['squeeze_momentum', 'momentum', 'breakout', 'volume']
            consensus_engine = ConsensusEngine(strategies)
            test_signals = {'squeeze_momentum': 0.8, 'momentum': 0.6, 'breakout': 0.7, 'volume': 0.5}
            
            consensus_score = consensus_engine.calculate_consensus_score(test_signals)
            consensus_reached = consensus_engine.is_consensus_reached(consensus_score)
            phase1_tests['consensus'] = consensus_score > 0 and isinstance(consensus_reached, bool)
            
            # 2. MTFAnalyzer 테스트
            mtf_analyzer = MTFAnalyzer(['5m', '15m', '1h', '1d'])
            mtf_signals = {'5m': 0.7, '15m': 0.8, '1h': 0.6, '1d': 0.9}
            
            mtf_score = mtf_analyzer.calculate_confluence_score(mtf_signals)
            mtf_confluence = mtf_analyzer.is_confluence_met(mtf_score)
            phase1_tests['mtf'] = mtf_score > 0 and isinstance(mtf_confluence, bool)
            
            # 3. LiquidityGate 테스트
            liquidity_gate = LiquidityGate()
            market_data = {'price': 50000, 'atr': 1000, 'avg_trade_value': 2000000000, 'spread_pct': 0.3}
            liquidity_pass = liquidity_gate.evaluate('TEST', market_data)
            phase1_tests['liquidity'] = isinstance(liquidity_pass, bool)
            
            # 4. NewsGate 테스트
            news_gate = NewsGate()
            news_data = [{'type': 'news', 'timestamp': datetime.now(), 'sentiment': 0.2, 'content': '테스트'}]
            news_pass = news_gate.evaluate('TEST', news_data)
            phase1_tests['news'] = isinstance(news_pass, bool)
            
            # 5. RegimeGate 테스트
            regime_gate = RegimeGate()
            regime_data = {'adx': 30, 'sma_short': 51000, 'sma_long': 49000}
            regime = regime_gate.detect_regime(regime_data)
            regime_score = regime_gate.evaluate_strategy_for_regime('momentum', regime)
            phase1_tests['regime'] = isinstance(regime, str) and isinstance(regime_score, float)
            
            # 전체 결과
            passed_tests = sum(phase1_tests.values())
            total_tests = len(phase1_tests)
            
            self.test_results['phase1_integration'] = {
                'individual_tests': phase1_tests,
                'passed_tests': passed_tests,
                'total_tests': total_tests,
                'success_rate': passed_tests / total_tests,
                'success': passed_tests == total_tests
            }
            
            status = "[green]PASS[/green]" if passed_tests == total_tests else "[red]FAIL[/red]"
            console.print(f"Phase 1 통합 테스트: {passed_tests}/{total_tests} 모듈 통과 - {status}")
            
        except Exception as e:
            console.print(f"[red]Phase 1 통합 테스트 실패: {e}[/red]")
            self.test_results['phase1_integration'] = {'success': False, 'error': str(e)}
    
    async def _test_performance_benchmark(self):
        """성능 벤치마크 테스트"""
        console.print("\n[yellow]4. 성능 벤치마크 테스트...[/yellow]")
        
        try:
            from recommendations.stock_recommendation_system import StockRecommendationSystem
            import time
            
            system = StockRecommendationSystem()
            
            # 성능 측정
            processing_times = []
            recommendation_counts = []
            
            for i in track(range(5), description="성능 측정"):
                start_time = time.perf_counter()
                
                # 추천 생성
                report = await system.generate_daily_recommendations()
                
                end_time = time.perf_counter()
                processing_time = (end_time - start_time) * 1000
                
                processing_times.append(processing_time)
                recommendation_counts.append(
                    sum(len(candidates) for candidates in report['recommendations_by_grade'].values())
                )
                
                # 과부하 방지
                await asyncio.sleep(0.1)
            
            # 성능 통계
            avg_time = sum(processing_times) / len(processing_times)
            max_time = max(processing_times)
            min_time = min(processing_times)
            avg_recommendations = sum(recommendation_counts) / len(recommendation_counts)
            
            # 목표 대비 평가
            target_time_ms = 5000  # 5초 목표
            performance_pass = avg_time < target_time_ms
            
            self.test_results['performance_benchmark'] = {
                'avg_processing_time_ms': round(avg_time, 1),
                'max_processing_time_ms': round(max_time, 1),
                'min_processing_time_ms': round(min_time, 1),
                'avg_recommendations': round(avg_recommendations, 1),
                'target_time_ms': target_time_ms,
                'performance_pass': performance_pass,
                'success': performance_pass
            }
            
            status = "[green]PASS[/green]" if performance_pass else "[red]FAIL[/red]"
            console.print(f"성능 벤치마크: 평균 {avg_time:.1f}ms (목표: <{target_time_ms}ms) - {status}")
            console.print(f"  평균 추천 종목: {avg_recommendations:.1f}개")
            
        except Exception as e:
            console.print(f"[red]성능 벤치마크 테스트 실패: {e}[/red]")
            self.test_results['performance_benchmark'] = {'success': False, 'error': str(e)}
    
    def _generate_test_report(self):
        """테스트 결과 리포트 생성"""
        console.print("\n[bold blue]Squeeze Momentum Pro 통합 테스트 결과[/bold blue]")
        
        table = Table(title="통합 테스트 결과")
        table.add_column("테스트 항목", style="cyan")
        table.add_column("결과", style="yellow")
        table.add_column("세부 사항", style="white")
        
        test_items = [
            ('추천 엔진', 'recommendation_engine'),
            ('추천 시스템', 'recommendation_system'), 
            ('Phase 1 통합', 'phase1_integration'),
            ('성능 벤치마크', 'performance_benchmark')
        ]
        
        successful_tests = 0
        
        for name, key in test_items:
            result = self.test_results.get(key, {})
            
            if result.get('success', False):
                status = "PASS"
                successful_tests += 1
                
                # 세부 사항
                if key == 'recommendation_engine':
                    details = f"{result.get('recommendations_output', 0)}개 추천 생성"
                elif key == 'recommendation_system':
                    details = f"{result.get('total_recommendations', 0)}개 종목 추천"
                elif key == 'phase1_integration':
                    details = f"{result.get('passed_tests', 0)}/{result.get('total_tests', 0)} 모듈 통과"
                elif key == 'performance_benchmark':
                    details = f"{result.get('avg_processing_time_ms', 0):.1f}ms 평균 처리시간"
                else:
                    details = "성공"
                    
            else:
                status = "FAIL"
                details = result.get('error', '테스트 실패')
            
            table.add_row(name, status, details)
        
        console.print(table)
        
        # 전체 결과 판정
        success_rate = successful_tests / len(test_items)
        
        if success_rate >= 0.8:
            console.print(f"\n[green]OK Squeeze Momentum Pro 시스템 준비 완료 ({successful_tests}/{len(test_items)})[/green]")
            console.print("[green]제미나이의 1차 필터링 구현을 기다리는 중입니다.[/green]")
        else:
            console.print(f"\n[yellow]WARN 일부 테스트 실패 ({successful_tests}/{len(test_items)})[/yellow]")
            console.print("[yellow]시스템 점검이 필요합니다.[/yellow]")
        
        # 상세 리포트 저장
        report_path = f"squeeze_momentum_test_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                json.dump({
                    'timestamp': datetime.now().isoformat(),
                    'overall_success_rate': success_rate,
                    'successful_tests': successful_tests,
                    'total_tests': len(test_items),
                    'detailed_results': self.test_results
                }, f, ensure_ascii=False, indent=2)
            
            console.print(f"\n[green]REPORT 상세 리포트 저장: {report_path}[/green]")
            
        except Exception as e:
            console.print(f"[red]리포트 저장 실패: {e}[/red]")

async def main():
    """메인 실행 함수"""
    tester = SqueezeMomentumIntegrationTester()
    await tester.run_full_integration_test()

if __name__ == "__main__":
    asyncio.run(main())