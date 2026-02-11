#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/utils/display.py

Rich 라이브러리를 활용한 터미널 결과 표시 유틸리티
"""

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from typing import List, Dict, Any

console = Console()

class DisplayUtils:
    """결과 표시 유틸리티 클래스"""

    def display_comprehensive_analysis_results(self, results: List[Dict[str, Any]]):
        """종합 분석 결과를 간결한 형태로 표시"""
        if not results:
            console.print("[yellow]분석 결과가 없습니다.[/yellow]")
            return

        # 점수 기준으로 내림차순 정렬
        results.sort(key=lambda x: x.get('comprehensive_score', 0), reverse=True)
        
        # 헤더
        total_results = len(results)
        avg_score = sum(r.get('comprehensive_score', 0) for r in results) / total_results if total_results > 0 else 0
        console.print(f"\n[bold white]📊 종합 분석 결과[/bold white] | 총 {total_results}개 종목 | 평균: {avg_score:.1f}점")
        console.print("[dim]─" * 100 + "[/dim]")

        # 각 종목을 간결한 라인으로 표시
        for i, result in enumerate(results):
            # 기본 정보
            symbol = result.get('symbol', 'N/A')
            name = result.get('name', 'N/A')
            comp_score = result.get('comprehensive_score', 0)
            recommendation = result.get('recommendation', 'HOLD')
            
            # 개별 점수들
            tech_score = result.get('technical_score', 0)
            supply_score = result.get('supply_demand_score', 0) 
            sentiment_score = result.get('sentiment_score', 0)
            pattern_score = result.get('chart_pattern_score', 0)
            
            # 색상 설정
            rec_color = "green" if "BUY" in recommendation else "red" if "SELL" in recommendation else "yellow"
            score_color = "green" if comp_score >= 70 else "yellow" if comp_score >= 60 else "white" if comp_score >= 50 else "red"
            
            # 종목명 길이 조정 (12자리)
            name_display = name[:10] + '..' if len(name) > 12 else name.ljust(12)
            symbol_display = symbol.ljust(8)
            
            # 점수들을 간결하게 표시
            scores = f"기술:{tech_score:4.1f} 수급:{supply_score:4.1f} 뉴스:{sentiment_score:4.1f} 패턴:{pattern_score:4.1f}"
            
            console.print(
                f"[cyan]{i+1:2}.[/cyan] "
                f"[bold white]{name_display}[/bold white] "
                f"[dim]({symbol_display})[/dim] "
                f"[{score_color}]{comp_score:5.1f}점[/{score_color}] "
                f"[{rec_color}]{recommendation:4s}[/{rec_color}] "
                f"[dim]{scores}[/dim]"
            )
        
        console.print("[dim]─" * 100 + "[/dim]")
        
        # 간단한 통계
        buy_count = sum(1 for r in results if "BUY" in r.get('recommendation', ''))
        sell_count = sum(1 for r in results if "SELL" in r.get('recommendation', ''))
        hold_count = total_results - buy_count - sell_count
        
        console.print(
            f"[green]매수:{buy_count}[/green] | "
            f"[yellow]보유:{hold_count}[/yellow] | "
            f"[red]매도:{sell_count}[/red] | "
            f"[dim]범례: 70+ 우수, 60+ 양호, 50+ 보통, 50- 주의[/dim]"
        )

    def _create_news_panel(self, sentiment_details: Dict[str, Any]) -> Panel:
        """장/중/단기 뉴스 분석 결과를 담은 패널 생성"""
        news_table = Table(
            title="[bold]📰 기간별 뉴스 분석[/bold]",
            show_header=True, header_style="bold magenta"
        )
        news_table.add_column("기간", style="cyan", width=15)
        news_table.add_column("점수", style="green", justify="center", width=8)
        news_table.add_column("핵심 요약", style="white")
        news_table.add_column("키워드", style="yellow")

        periods = ['short_term_analysis', 'mid_term_analysis', 'long_term_analysis']
        for period_key in periods:
            period_data = sentiment_details.get(period_key, {})
            if period_data:
                news_table.add_row(
                    period_data.get('period', 'N/A'),
                    f"{period_data.get('score', 50):.1f}",
                    period_data.get('summary', '요약 없음'),
                    ", ".join(period_data.get('keywords', []))
                )
        
        return Panel(news_table, border_style="magenta")

    def display_recommendations_summary(self, results: List[Dict[str, Any]]):
        """추천 등급 요약 표시"""
        if not results:
            return

        buy_count = sum(1 for r in results if "BUY" in r.get('recommendation', ''))
        sell_count = sum(1 for r in results if "SELL" in r.get('recommendation', ''))
        hold_count = len(results) - buy_count - sell_count

        summary_text = (
            f"총 [bold]{len(results)}[/bold]개 종목 분석 완료\n"
            f"  - [green]매수 추천[/green]: {buy_count}개\n"
            f"  - [yellow]보유 추천[/yellow]: {hold_count}개\n"
            f"  - [red]매도 추천[/red]: {sell_count}개"
        )
        console.print(Panel(summary_text, title="[bold blue]분석 요약[/bold blue]", border_style="blue"))

    def display_detailed_news_analysis(self, symbol: str, name: str, news_data: List[Dict], analysis_result: Dict):
        """개별 종목의 뉴스 분석 세부 결과를 표시 - 간결한 형태로 개선"""
        if not news_data:
            console.print(f"[yellow]{symbol}({name}) 뉴스 데이터가 없습니다.[/yellow]")
            return

        # 헤더 - 간결하게
        overall_score = analysis_result.get('overall_score', 50) if analysis_result else 50
        score_color = "green" if overall_score >= 70 else "yellow" if overall_score >= 50 else "red"
        
        console.print(f"\n[bold white]📰 {name}({symbol})[/bold white] | 뉴스 {len(news_data)}개 | 점수: [{score_color}]{overall_score:.1f}[/{score_color}]")
        
        # 핵심 뉴스만 간결하게 표시 (최신 5개)
        console.print("[dim]─" * 80 + "[/dim]")
        for i, news in enumerate(news_data[:5]):
            date = news.get('date', 'N/A')[:10]  # YYYY-MM-DD
            title = news.get('title', '제목 없음')
            source = news.get('source', '출처미상')
            
            # 제목 길이 조정
            if len(title) > 55:
                title = title[:55] + '...'
            
            # 날짜와 출처를 우측 정렬
            padding = max(0, 65 - len(title))
            
            console.print(f"[cyan]{i+1:2}.[/cyan] {title}{' ' * padding}[dim]{date} ({source})[/dim]")
        
        # 키워드 요약
        all_titles = [news.get('title', '') for news in news_data if news.get('title')]
        common_keywords = self._extract_common_keywords(all_titles)
        
        if common_keywords:
            keywords_str = ' | '.join(common_keywords[:5])  # 상위 5개만
            console.print(f"\n[yellow]🔑 주요 키워드:[/yellow] {keywords_str}")
        
        console.print("[dim]─" * 80 + "[/dim]")
    
    def _extract_common_keywords(self, titles: List[str]) -> List[str]:
        """뉴스 제목에서 공통 키워드 추출"""
        if not titles:
            return []
        
        # 간단한 키워드 추출 (한글 키워드 중심)
        import re
        from collections import Counter
        
        # 모든 제목을 합쳐서 처리
        all_text = ' '.join(titles)
        
        # 한글 단어 추출 (2글자 이상)
        korean_words = re.findall(r'[가-힣]{2,}', all_text)
        
        # 빈도수 계산
        word_counts = Counter(korean_words)
        
        # 의미없는 단어 필터링
        stop_words = {'것', '등', '및', '그리고', '하는', '있는', '되는', '위한', '통해', '대한', '관련', '발표', '계획', '예정', '진행'}
        
        # 빈도수 높은 단어 반환 (불용어 제외)
        common_words = [word for word, count in word_counts.most_common(10) 
                       if word not in stop_words and count >= 2]
        
        return common_words

    def _display_individual_news_analysis(self, news_data: List[Dict], individual_analysis: List[Dict]):
        """개별 뉴스별 분석 결과 표시"""
        news_table = Table(
            title="[bold]📋 개별 뉴스 분석[/bold]",
            show_header=True,
            header_style="bold magenta",
            border_style="blue"
        )
        news_table.add_column("번호", style="dim", width=4, justify="center")
        news_table.add_column("제목", style="cyan", width=30)
        news_table.add_column("영향기간", style="yellow", width=10, justify="center")
        news_table.add_column("점수", style="bold", width=6, justify="center")
        news_table.add_column("키워드", style="green", width=20)
        news_table.add_column("영향도", style="white", width=25)

        for i, (news, analysis) in enumerate(zip(news_data, individual_analysis)):
            title = news.get('title', '제목 없음')[:28] + '...' if len(news.get('title', '')) > 28 else news.get('title', '제목 없음')
            
            period = analysis.get('period', 'UNKNOWN')
            period_text = {'SHORT_TERM': '단기', 'MEDIUM_TERM': '중기', 'LONG_TERM': '장기'}.get(period, '미상')
            
            score = analysis.get('score', 50)
            score_color = "green" if score >= 70 else "yellow" if score >= 60 else "white" if score >= 50 else "red"
            
            keywords = ', '.join(analysis.get('keywords', [])[:2])  # 상위 2개만
            impact = analysis.get('impact', '영향도 미상')[:23] + '...' if len(analysis.get('impact', '')) > 23 else analysis.get('impact', '영향도 미상')
            
            news_table.add_row(
                str(i + 1),
                title,
                f"[{score_color}]{period_text}[/{score_color}]",
                f"[{score_color}]{score:.1f}[/{score_color}]",
                keywords or '-',
                impact
            )

        console.print(news_table)