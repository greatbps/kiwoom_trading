"""
확장 백테스팅: 10-20개 종목으로 VWAP + 트레일링 스탑 전략 검증

목적:
- 최적 파라미터 (1.5%, 1.0%) 검증
- 섹터별, 시가총액별 성과 비교
- 전략 강건성 확인
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yfinance as yf
import pandas as pd
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from typing import Dict, List
import time

console = Console()

# 테스트 종목: 다양한 섹터 및 시가총액
STOCKS = {
    # 대형주
    '삼성전자': {'ticker': '005930.KS', 'sector': '반도체', 'cap': '대형'},
    'SK하이닉스': {'ticker': '000660.KS', 'sector': '반도체', 'cap': '대형'},
    'NAVER': {'ticker': '035420.KS', 'sector': 'IT/플랫폼', 'cap': '대형'},
    '카카오': {'ticker': '035720.KS', 'sector': 'IT/플랫폼', 'cap': '대형'},

    # 중형주 - 배터리/전기차
    'LG에너지솔루션': {'ticker': '373220.KS', 'sector': '배터리', 'cap': '대형'},
    '삼성SDI': {'ticker': '006400.KS', 'sector': '배터리', 'cap': '대형'},
    '포스코퓨처엠': {'ticker': '003670.KS', 'sector': '소재', 'cap': '중형'},

    # 중형주 - 원자력/에너지
    '한국전력': {'ticker': '015760.KS', 'sector': '전력', 'cap': '대형'},
    '한전기술': {'ticker': '052690.KS', 'sector': '원자력', 'cap': '중형'},
    '두산에너빌리티': {'ticker': '034020.KS', 'sector': '원자력', 'cap': '중형'},

    # 중형주 - 바이오/제약
    '셀트리온': {'ticker': '068270.KS', 'sector': '바이오', 'cap': '대형'},
    '삼성바이오로직스': {'ticker': '207940.KS', 'sector': '바이오', 'cap': '대형'},
    '유한양행': {'ticker': '000100.KS', 'sector': '제약', 'cap': '중형'},

    # 중형주 - 기타
    '현대차': {'ticker': '005380.KS', 'sector': '자동차', 'cap': '대형'},
    'KB금융': {'ticker': '105560.KS', 'sector': '금융', 'cap': '대형'},
    '삼성물산': {'ticker': '028260.KS', 'sector': '건설/상사', 'cap': '대형'},
    'LG화학': {'ticker': '051910.KS', 'sector': '화학', 'cap': '대형'},
    '현대모비스': {'ticker': '012330.KS', 'sector': '자동차부품', 'cap': '대형'},
}

def download_stock_data(ticker: str, name: str) -> pd.DataFrame:
    """종목 데이터 다운로드"""
    try:
        data = yf.download(
            tickers=ticker,
            period='7d',
            interval='5m',
            progress=False
        )
        if data.empty:
            return None
        return data
    except Exception as e:
        console.print(f"  [red]✗ {name} 다운로드 실패: {e}[/red]")
        return None

def prepare_chart_data(df: pd.DataFrame) -> List[Dict]:
    """DataFrame을 차트 데이터로 변환"""
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0] for col in df.columns]

    chart_data = []
    for idx, row in df.iterrows():
        if pd.isna(row['Close']) or pd.isna(row['Volume']):
            continue

        chart_data.append({
            'dt': idx.strftime('%Y%m%d'),
            'tic_tm': idx.strftime('%H%M%S'),
            'open_pric': float(row['Open']),
            'high_pric': float(row['High']),
            'low_pric': float(row['Low']),
            'cur_prc': float(row['Close']),
            'trde_qty': int(row['Volume']) if row['Volume'] > 0 else 1
        })

    return chart_data

def simulate_trailing_stop(
    chart_data: List[Dict],
    activation_pct: float = 1.5,
    trail_ratio: float = 1.0,
    stop_loss_pct: float = 1.0
) -> Dict:
    """트레일링 스탑 전략 시뮬레이션"""

    analyzer = EntryTimingAnalyzer(
        trailing_activation_pct=activation_pct,
        trailing_ratio=trail_ratio,
        stop_loss_pct=stop_loss_pct
    )

    df = analyzer._prepare_dataframe(chart_data)
    df = analyzer.calculate_vwap(df)
    df = analyzer.generate_signals(df, use_trend_filter=True, use_volume_filter=True)

    cash = 10000000
    position = 0
    avg_price = 0
    highest_price = 0
    trailing_active = False
    trades = []

    for idx, row in df.iterrows():
        price = row['close']
        signal = row['signal']

        # 포지션 보유 중
        if position > 0:
            # 고가 갱신
            if price > highest_price:
                highest_price = price

            # 트레일링 스탑 체크
            should_exit, trailing_active, stop_price, reason = analyzer.check_trailing_stop(
                current_price=price,
                avg_price=avg_price,
                highest_price=highest_price,
                trailing_active=trailing_active
            )

            # VWAP 하향 돌파도 청산 사유
            if not should_exit and signal == -1:
                should_exit = True
                reason = 'VWAP 하향 돌파'

            # 청산 실행
            if should_exit:
                revenue = position * price
                cash += revenue
                profit = revenue - (position * avg_price)
                profit_rate = ((price - avg_price) / avg_price) * 100
                highest_profit_rate = ((highest_price - avg_price) / avg_price) * 100

                trades.append({
                    'type': 'SELL',
                    'reason': reason,
                    'price': price,
                    'profit': profit,
                    'profit_rate': profit_rate,
                    'highest_price': highest_price,
                    'highest_profit_rate': highest_profit_rate,
                    'trailing_active': trailing_active
                })

                position = 0
                avg_price = 0
                highest_price = 0
                trailing_active = False

        # 매수 시그널
        if signal == 1 and position == 0 and cash > 0:
            quantity = int(cash / price)
            if quantity > 0:
                cash -= quantity * price
                position = quantity
                avg_price = price
                highest_price = price
                trailing_active = False

                trades.append({
                    'type': 'BUY',
                    'price': price,
                    'quantity': quantity
                })

    # 최종 평가
    final_value = cash
    if position > 0:
        current_price = df.iloc[-1]['close']
        final_value += position * current_price

    buy_trades = [t for t in trades if t['type'] == 'BUY']
    sell_trades = [t for t in trades if t['type'] == 'SELL']
    win_trades = [t for t in sell_trades if t.get('profit', 0) > 0]
    trailing_exits = [t for t in sell_trades if '트레일링' in t.get('reason', '')]

    return {
        'final_value': final_value,
        'return': final_value - 10000000,
        'return_pct': ((final_value - 10000000) / 10000000) * 100,
        'trade_count': len(buy_trades),
        'exit_count': len(sell_trades),
        'win_count': len(win_trades),
        'win_rate': (len(win_trades) / len(sell_trades) * 100) if sell_trades else 0,
        'trailing_count': len(trailing_exits),
        'avg_profit_rate': sum([t.get('profit_rate', 0) for t in sell_trades]) / len(sell_trades) if sell_trades else 0,
        'trades': trades
    }

def main():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]📊 확장 백테스팅: VWAP + 트레일링 스탑 전략 (18종목)[/bold cyan]",
        border_style="cyan"
    ))
    console.print()

    console.print("[bold]최적 파라미터:[/bold]")
    console.print("  - 트레일링 활성화: +1.5%")
    console.print("  - 트레일링 비율: -1.0%")
    console.print("  - 기본 손절: -1.0%")
    console.print("  - 추세 필터: MA20")
    console.print("  - 거래량 필터: 평균 × 1.2")
    console.print()

    # 데이터 다운로드
    console.print("[bold cyan]데이터 다운로드 중...[/bold cyan]")
    stock_data = {}
    download_stats = {'success': 0, 'failed': 0}

    start_time = time.time()

    for name, info in STOCKS.items():
        ticker = info['ticker']
        data = download_stock_data(ticker, name)

        if data is not None:
            chart_data = prepare_chart_data(data)
            if chart_data and len(chart_data) >= 20:
                stock_data[name] = {
                    'chart_data': chart_data,
                    'sector': info['sector'],
                    'cap': info['cap']
                }
                download_stats['success'] += 1
                console.print(f"  ✓ {name:20s} ({info['sector']:12s}): {len(chart_data):4d}개")
            else:
                download_stats['failed'] += 1
                console.print(f"  [yellow]⚠ {name:20s}: 데이터 부족[/yellow]")
        else:
            download_stats['failed'] += 1

    download_time = time.time() - start_time
    console.print()
    console.print(f"[bold]다운로드 완료:[/bold] {download_stats['success']}개 성공, "
                 f"{download_stats['failed']}개 실패 ({download_time:.1f}초)")
    console.print()

    # 시뮬레이션 실행
    console.print("[bold cyan]시뮬레이션 실행 중...[/bold cyan]")
    console.print()

    results = []
    sim_start_time = time.time()

    for name, data in stock_data.items():
        result = simulate_trailing_stop(
            chart_data=data['chart_data'],
            activation_pct=1.5,
            trail_ratio=1.0,
            stop_loss_pct=1.0
        )

        result['stock_name'] = name
        result['sector'] = data['sector']
        result['cap'] = data['cap']
        results.append(result)

        # 간단한 진행 표시
        color = "green" if result['return_pct'] > 0 else "red" if result['return_pct'] < 0 else "yellow"
        console.print(f"  {name:20s}: [{color}]{result['return_pct']:+6.2f}%[/{color}] "
                     f"(거래 {result['trade_count']}회)")

    sim_time = time.time() - sim_start_time
    console.print()
    console.print(f"[bold]시뮬레이션 완료:[/bold] {len(results)}개 종목 ({sim_time:.1f}초)")
    console.print()

    # ===== 종합 결과 테이블 =====
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print("[bold cyan]📈 종목별 성과 비교[/bold cyan]")
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print()

    table = Table(
        title="🎯 전체 종목 백테스팅 결과",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold magenta"
    )

    table.add_column("순위", justify="center", width=6)
    table.add_column("종목", style="cyan", width=20)
    table.add_column("섹터", width=12)
    table.add_column("수익률", justify="right", width=10)
    table.add_column("거래", justify="center", width=6)
    table.add_column("승률", justify="right", width=8)
    table.add_column("트레일링", justify="center", width=10)

    # 수익률 순 정렬
    results_sorted = sorted(results, key=lambda x: x['return_pct'], reverse=True)

    for i, r in enumerate(results_sorted, 1):
        rank = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}"

        if r['return_pct'] > 0:
            return_text = f"[green]+{r['return_pct']:.2f}%[/green]"
        elif r['return_pct'] < 0:
            return_text = f"[red]{r['return_pct']:.2f}%[/red]"
        else:
            return_text = f"{r['return_pct']:.2f}%"

        win_rate_text = f"{r['win_rate']:.0f}%" if r['exit_count'] > 0 else "-"

        table.add_row(
            rank,
            r['stock_name'],
            r['sector'],
            return_text,
            f"{r['trade_count']}회",
            win_rate_text,
            f"{r['trailing_count']}회"
        )

    console.print(table)
    console.print()

    # ===== 종합 통계 =====
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print("[bold cyan]📊 종합 통계[/bold cyan]")
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print()

    total_stocks = len(results)
    avg_return = sum([r['return_pct'] for r in results]) / total_stocks
    total_return = sum([r['return'] for r in results])

    profitable_stocks = [r for r in results if r['return_pct'] > 0]
    losing_stocks = [r for r in results if r['return_pct'] < 0]
    neutral_stocks = [r for r in results if r['return_pct'] == 0]

    total_trades = sum([r['trade_count'] for r in results])
    total_exits = sum([r['exit_count'] for r in results])
    total_wins = sum([r['win_count'] for r in results])
    total_trailing = sum([r['trailing_count'] for r in results])

    overall_win_rate = (total_wins / total_exits * 100) if total_exits > 0 else 0

    console.print(f"[bold]전체 성과:[/bold]")
    console.print(f"  평균 수익률: [bold]{avg_return:+.2f}%[/bold]")
    console.print(f"  총 수익: {total_return:+,.0f}원 ({total_stocks}종목 합계)")
    console.print()

    console.print(f"[bold]종목 분포:[/bold]")
    console.print(f"  [green]수익 종목: {len(profitable_stocks)}개 ({len(profitable_stocks)/total_stocks*100:.1f}%)[/green]")
    console.print(f"  [red]손실 종목: {len(losing_stocks)}개 ({len(losing_stocks)/total_stocks*100:.1f}%)[/red]")
    console.print(f"  [yellow]무거래 종목: {len(neutral_stocks)}개 ({len(neutral_stocks)/total_stocks*100:.1f}%)[/yellow]")
    console.print()

    console.print(f"[bold]거래 통계:[/bold]")
    console.print(f"  총 거래: {total_trades}회 (평균 {total_trades/total_stocks:.1f}회/종목)")
    console.print(f"  총 청산: {total_exits}회")
    console.print(f"  전체 승률: {overall_win_rate:.1f}% ({total_wins}/{total_exits})")
    console.print(f"  트레일링 스탑: {total_trailing}회 ({total_trailing/total_exits*100:.1f}% 발동)" if total_exits > 0 else "")
    console.print()

    # ===== 섹터별 분석 =====
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print("[bold cyan]🏢 섹터별 분석[/bold cyan]")
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print()

    sector_stats = {}
    for r in results:
        sector = r['sector']
        if sector not in sector_stats:
            sector_stats[sector] = []
        sector_stats[sector].append(r)

    sector_table = Table(
        title="섹터별 성과",
        box=box.ROUNDED,
        border_style="cyan",
        show_header=True,
        header_style="bold magenta"
    )

    sector_table.add_column("섹터", style="cyan", width=15)
    sector_table.add_column("종목수", justify="center", width=8)
    sector_table.add_column("평균수익률", justify="right", width=12)
    sector_table.add_column("최고수익", justify="right", width=12)
    sector_table.add_column("평균거래", justify="center", width=10)

    for sector, sector_results in sorted(sector_stats.items(), key=lambda x: sum([r['return_pct'] for r in x[1]])/len(x[1]), reverse=True):
        avg_sector_return = sum([r['return_pct'] for r in sector_results]) / len(sector_results)
        max_sector_return = max([r['return_pct'] for r in sector_results])
        avg_trades = sum([r['trade_count'] for r in sector_results]) / len(sector_results)

        if avg_sector_return > 0:
            avg_text = f"[green]+{avg_sector_return:.2f}%[/green]"
        elif avg_sector_return < 0:
            avg_text = f"[red]{avg_sector_return:.2f}%[/red]"
        else:
            avg_text = f"{avg_sector_return:.2f}%"

        max_text = f"+{max_sector_return:.2f}%" if max_sector_return > 0 else f"{max_sector_return:.2f}%"

        sector_table.add_row(
            sector,
            f"{len(sector_results)}개",
            avg_text,
            max_text,
            f"{avg_trades:.1f}회"
        )

    console.print(sector_table)
    console.print()

    # ===== 최고 성과 종목 상세 =====
    if results_sorted:
        console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
        console.print("[bold cyan]🏆 TOP 3 상세 거래 내역[/bold cyan]")
        console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
        console.print()

        for i, r in enumerate(results_sorted[:3], 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉"
            console.print(f"{medal} [bold]{r['stock_name']}[/bold] ({r['sector']}) - {r['return_pct']:+.2f}%")

            if r['exit_count'] > 0:
                for t in r['trades']:
                    if t['type'] == 'SELL':
                        profit_color = "green" if t['profit'] > 0 else "red"
                        console.print(f"     청산: {t['price']:,.0f}원 → "
                                     f"[{profit_color}]{t['profit']:+,.0f}원 ({t['profit_rate']:+.2f}%)[/{profit_color}]")
                        console.print(f"     사유: {t['reason']} | 최고가: {t['highest_price']:,.0f}원 (+{t['highest_profit_rate']:.2f}%)")
            else:
                console.print("     거래 없음")

            console.print()

    # ===== 총평 =====
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print("[bold cyan]💡 전략 평가[/bold cyan]")
    console.print("[bold cyan]" + "="*80 + "[/bold cyan]")
    console.print()

    if avg_return > 0:
        console.print(f"[bold green]✅ 전략 유효성 확인[/bold green]")
        console.print(f"   평균 수익률 {avg_return:+.2f}%로 다양한 섹터에서 양수 수익 달성")
    else:
        console.print(f"[bold red]⚠️ 전략 개선 필요[/bold red]")
        console.print(f"   평균 수익률 {avg_return:+.2f}%로 손실 발생")

    console.print()

    if len(profitable_stocks) > len(losing_stocks):
        console.print(f"[green]✓ 승률 우수: {len(profitable_stocks)}/{total_stocks} 종목 수익[/green]")
    else:
        console.print(f"[yellow]⚠ 승률 개선 필요: {len(losing_stocks)}/{total_stocks} 종목 손실[/yellow]")

    console.print()

    if total_trailing > 0:
        trailing_rate = total_trailing / total_exits * 100
        console.print(f"[green]✓ 트레일링 스탑 활용률: {trailing_rate:.1f}% ({total_trailing}/{total_exits}회)[/green]")

    console.print()

if __name__ == "__main__":
    main()
