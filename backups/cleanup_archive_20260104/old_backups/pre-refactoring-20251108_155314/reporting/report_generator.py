#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reporting/report_generator.py

리포팅 시스템
- HTML/Markdown 일일/주간 리포트
- 전략별 성과 대시보드
- Plotly 수익 그래프
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


class ReportGenerator:
    """리포트 생성기"""

    def __init__(
        self,
        output_dir: str = "./reports",
    ):
        """
        초기화

        Args:
            output_dir: 리포트 출력 디렉토리
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def generate_daily_report(
        self,
        trades: List[Dict[str, Any]],
        date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        일일 리포트 생성

        Args:
            trades: 거래 내역 리스트
            date: 리포트 날짜

        Returns:
            리포트 데이터
        """
        if not date:
            date = datetime.now()

        # 거래 통계 계산
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t.get('profit', 0) > 0)
        losing_trades = sum(1 for t in trades if t.get('profit', 0) < 0)

        total_profit = sum(t.get('profit', 0) for t in trades)
        total_loss = sum(t.get('profit', 0) for t in trades if t.get('profit', 0) < 0)
        total_gain = sum(t.get('profit', 0) for t in trades if t.get('profit', 0) > 0)

        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
        avg_profit = (total_profit / total_trades) if total_trades > 0 else 0.0

        profit_factor = abs(total_gain / total_loss) if total_loss != 0 else 0.0

        # 리포트 데이터
        report = {
            'date': date.strftime('%Y-%m-%d'),
            'summary': {
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'losing_trades': losing_trades,
                'win_rate': f"{win_rate:.1f}%",
                'total_profit': f"{total_profit:,.0f}원",
                'avg_profit': f"{avg_profit:,.0f}원",
                'profit_factor': f"{profit_factor:.2f}",
            },
            'trades': trades,
            'generated_at': datetime.now().isoformat(),
        }

        logger.info(
            f"일일 리포트 생성 완료: {date.strftime('%Y-%m-%d')}, "
            f"거래 {total_trades}건, 승률 {win_rate:.1f}%"
        )

        return report

    def generate_weekly_report(
        self,
        trades: List[Dict[str, Any]],
        week_start: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """
        주간 리포트 생성

        Args:
            trades: 거래 내역 리스트
            week_start: 주 시작일

        Returns:
            리포트 데이터
        """
        if not week_start:
            today = datetime.now()
            week_start = today - timedelta(days=today.weekday())

        # 일별 그룹화
        daily_stats = {}
        for trade in trades:
            trade_date = trade.get('date', datetime.now().strftime('%Y-%m-%d'))
            if trade_date not in daily_stats:
                daily_stats[trade_date] = {
                    'trades': 0,
                    'profit': 0.0,
                    'wins': 0,
                }

            daily_stats[trade_date]['trades'] += 1
            daily_stats[trade_date]['profit'] += trade.get('profit', 0)
            if trade.get('profit', 0) > 0:
                daily_stats[trade_date]['wins'] += 1

        # 주간 통계
        total_trades = len(trades)
        winning_trades = sum(1 for t in trades if t.get('profit', 0) > 0)
        total_profit = sum(t.get('profit', 0) for t in trades)
        win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

        # 전략별 통계
        strategy_stats = {}
        for trade in trades:
            strategy = trade.get('strategy', 'unknown')
            if strategy not in strategy_stats:
                strategy_stats[strategy] = {
                    'trades': 0,
                    'wins': 0,
                    'profit': 0.0,
                }

            strategy_stats[strategy]['trades'] += 1
            strategy_stats[strategy]['profit'] += trade.get('profit', 0)
            if trade.get('profit', 0) > 0:
                strategy_stats[strategy]['wins'] += 1

        # 리포트 데이터
        report = {
            'week_start': week_start.strftime('%Y-%m-%d'),
            'week_end': (week_start + timedelta(days=6)).strftime('%Y-%m-%d'),
            'summary': {
                'total_trades': total_trades,
                'winning_trades': winning_trades,
                'win_rate': f"{win_rate:.1f}%",
                'total_profit': f"{total_profit:,.0f}원",
            },
            'daily_stats': daily_stats,
            'strategy_stats': strategy_stats,
            'generated_at': datetime.now().isoformat(),
        }

        logger.info(
            f"주간 리포트 생성 완료: {week_start.strftime('%Y-%m-%d')}, "
            f"거래 {total_trades}건, 승률 {win_rate:.1f}%"
        )

        return report

    def save_report_json(
        self,
        report: Dict[str, Any],
        filename: Optional[str] = None,
    ) -> str:
        """
        리포트를 JSON 파일로 저장

        Args:
            report: 리포트 데이터
            filename: 파일명 (없으면 자동 생성)

        Returns:
            저장된 파일 경로
        """
        if not filename:
            date = report.get('date', report.get('week_start', datetime.now().strftime('%Y-%m-%d')))
            filename = f"report_{date}.json"

        filepath = self.output_dir / filename

        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)

        logger.info(f"리포트 JSON 저장: {filepath}")
        return str(filepath)

    def save_report_html(
        self,
        report: Dict[str, Any],
        filename: Optional[str] = None,
    ) -> str:
        """
        리포트를 HTML 파일로 저장

        Args:
            report: 리포트 데이터
            filename: 파일명 (없으면 자동 생성)

        Returns:
            저장된 파일 경로
        """
        if not filename:
            date = report.get('date', report.get('week_start', datetime.now().strftime('%Y-%m-%d')))
            filename = f"report_{date}.html"

        filepath = self.output_dir / filename

        # HTML 생성
        html = self._generate_html(report)

        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html)

        logger.info(f"리포트 HTML 저장: {filepath}")
        return str(filepath)

    def _generate_html(self, report: Dict[str, Any]) -> str:
        """HTML 리포트 생성"""
        summary = report.get('summary', {})

        html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>트레이딩 리포트 - {report.get('date', report.get('week_start', ''))}</title>
    <style>
        body {{
            font-family: 'Noto Sans KR', Arial, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
        }}
        h1 {{
            color: #333;
            border-bottom: 3px solid #4CAF50;
            padding-bottom: 10px;
        }}
        .summary {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
            margin-bottom: 20px;
        }}
        .stat-box {{
            display: inline-block;
            margin: 10px;
            padding: 15px;
            background: #f9f9f9;
            border-left: 4px solid #4CAF50;
            min-width: 150px;
        }}
        .stat-label {{
            font-size: 12px;
            color: #666;
            margin-bottom: 5px;
        }}
        .stat-value {{
            font-size: 24px;
            font-weight: bold;
            color: #333;
        }}
        .positive {{ color: #4CAF50; }}
        .negative {{ color: #f44336; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: white;
            border-radius: 8px;
            overflow: hidden;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        th, td {{
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #ddd;
        }}
        th {{
            background-color: #4CAF50;
            color: white;
        }}
    </style>
</head>
<body>
    <h1>📊 트레이딩 리포트</h1>
    <p><strong>날짜:</strong> {report.get('date', report.get('week_start', ''))}</p>

    <div class="summary">
        <h2>📈 요약</h2>
        <div class="stat-box">
            <div class="stat-label">총 거래</div>
            <div class="stat-value">{summary.get('total_trades', 0)}건</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">승률</div>
            <div class="stat-value positive">{summary.get('win_rate', '0%')}</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">총 손익</div>
            <div class="stat-value positive">{summary.get('total_profit', '0원')}</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">평균 손익</div>
            <div class="stat-value">{summary.get('avg_profit', '0원')}</div>
        </div>
    </div>

    <h2>📋 거래 내역</h2>
    <table>
        <thead>
            <tr>
                <th>시간</th>
                <th>종목</th>
                <th>전략</th>
                <th>방향</th>
                <th>손익</th>
            </tr>
        </thead>
        <tbody>
"""

        # 거래 내역 추가
        for trade in report.get('trades', []):
            profit = trade.get('profit', 0)
            profit_class = 'positive' if profit > 0 else 'negative'

            html += f"""
            <tr>
                <td>{trade.get('time', '-')}</td>
                <td>{trade.get('symbol', '-')}</td>
                <td>{trade.get('strategy', '-')}</td>
                <td>{trade.get('side', '-')}</td>
                <td class="{profit_class}">{profit:,.0f}원</td>
            </tr>
"""

        html += """
        </tbody>
    </table>

    <p style="text-align: center; color: #999; margin-top: 40px;">
        Generated by 키움 AI Trading System
    </p>
</body>
</html>
"""

        return html
