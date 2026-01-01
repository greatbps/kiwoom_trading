#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Squeeze Momentum 통합 테스트
키움 트레이딩 시스템에 통합된 스퀴즈 모멘텀 기능 테스트
"""

import pandas as pd
import yfinance as yf
from utils.squeeze_momentum_realtime import (
    calculate_squeeze_momentum,
    get_current_squeeze_signal,
    should_enter_trade,
    should_exit_trade,
    check_squeeze_momentum_filter
)
from rich.console import Console
from rich.table import Table

console = Console()


def test_squeeze_momentum_calculation():
    """스퀴즈 모멘텀 계산 테스트"""
    console.print("\n[bold cyan]=" * 40)
    console.print("[bold cyan]1. 스퀴즈 모멘텀 계산 테스트")
    console.print("[bold cyan]=" * 40)

    # 삼성전자 5분봉 데이터
    ticker = "005930.KS"
    console.print(f"\n종목: {ticker} (5분봉)")

    df = yf.download(ticker, period="5d", interval="5m", progress=False)

    if df is None or len(df) < 50:
        console.print("[red]  ❌ 데이터 부족[/red]")
        return False

    # 컬럼명 소문자 (MultiIndex 처리)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
    else:
        df.columns = df.columns.str.lower()

    # 스퀴즈 모멘텀 계산
    df = calculate_squeeze_momentum(df)

    # 현재 시그널
    signal = get_current_squeeze_signal(df)

    table = Table(title="현재 스퀴즈 모멘텀 상태")
    table.add_column("항목", style="cyan")
    table.add_column("값", style="yellow")

    table.add_row("색상", signal['color'])
    table.add_row("신호", signal['signal'])
    table.add_row("모멘텀", f"{signal['momentum']:.4f}")
    table.add_row("Squeeze ON", str(signal['squeeze_on']))
    table.add_row("가속 중", str(signal['is_accelerating']))
    table.add_row("감속 중", str(signal['is_decelerating']))

    console.print(table)

    # 최근 5봉 추세
    console.print("\n최근 5봉 추세:")
    recent = df.tail(5)[['close', 'sqz_color', 'sqz_momentum', 'sqz_signal']]
    for idx, row in recent.iterrows():
        color_emoji = {
            'bright_green': '🟢',
            'dark_green': '🟡',
            'dark_red': '🔴',
            'bright_red': '🟠',
            'gray': '⚪'
        }.get(row['sqz_color'], '⚪')

        console.print(f"  {idx} | {color_emoji} {row['sqz_color']:15} | {row['sqz_momentum']:>8.2f} | {row['sqz_signal']}")

    return True


def test_entry_filter():
    """진입 필터 테스트"""
    console.print("\n[bold cyan]=" * 40)
    console.print("[bold cyan]2. 진입 필터 테스트")
    console.print("[bold cyan]=" * 40)

    ticker = "005930.KS"
    df = yf.download(ticker, period="5d", interval="5m", progress=False)

    if df is None or len(df) < 50:
        console.print("[red]  ❌ 데이터 부족[/red]")
        return False

    # 컬럼명 소문자 (MultiIndex 처리)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
    else:
        df.columns = df.columns.str.lower()

    # 진입 필터 테스트
    passed, reason, details = check_squeeze_momentum_filter(df, for_entry=True)

    table = Table(title="진입 필터 결과")
    table.add_column("항목", style="cyan")
    table.add_column("값", style="yellow" if passed else "red")

    table.add_row("통과 여부", "✅ 통과" if passed else "❌ 차단")
    table.add_row("사유", reason)
    table.add_row("색상", details.get('color', 'N/A'))
    table.add_row("모멘텀", f"{details.get('momentum', 0):.4f}")

    console.print(table)

    # 추가 테스트: should_enter_trade()
    can_enter, enter_reason = should_enter_trade(calculate_squeeze_momentum(df))
    console.print(f"\n진입 가능: {can_enter}")
    console.print(f"사유: {enter_reason}")

    return True


def test_exit_filter():
    """청산 필터 테스트"""
    console.print("\n[bold cyan]=" * 40)
    console.print("[bold cyan]3. 청산 필터 테스트")
    console.print("[bold cyan]=" * 40)

    ticker = "005930.KS"
    df = yf.download(ticker, period="5d", interval="5m", progress=False)

    if df is None or len(df) < 50:
        console.print("[red]  ❌ 데이터 부족[/red]")
        return False

    # 컬럼명 소문자 (MultiIndex 처리)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
    else:
        df.columns = df.columns.str.lower()

    df = calculate_squeeze_momentum(df)

    # 청산 필터 테스트 (여러 수익률 시나리오)
    test_profits = [
        (0.5, "0.5% 수익"),
        (1.5, "1.5% 수익"),
        (2.5, "2.5% 수익"),
        (-0.5, "-0.5% 손실"),
    ]

    for profit_pct, description in test_profits:
        should_exit, exit_reason, exit_type = should_exit_trade(df, current_profit_rate=profit_pct)

        console.print(f"\n[{description}]")
        console.print(f"  청산 여부: {should_exit}")
        if should_exit:
            console.print(f"  사유: {exit_reason}")
            console.print(f"  타입: {exit_type}")

    return True


def test_real_trade_scenarios():
    """실제 거래 시나리오 테스트 (docs/1231 분석 기반)"""
    console.print("\n[bold cyan]=" * 40)
    console.print("[bold cyan]4. 실제 거래 시나리오 재현 테스트")
    console.print("[bold cyan]=" * 40)

    # 휴림로봇, 모비스, 아이티센글로벌 등 실제 거래 종목 테스트
    test_stocks = [
        ("090710.KQ", "휴림로봇"),
        ("005930.KS", "삼성전자"),  # 대형주 대표
    ]

    for ticker, name in test_stocks:
        console.print(f"\n[bold yellow]종목: {name} ({ticker})[/bold yellow]")

        df = yf.download(ticker, period="5d", interval="5m", progress=False)

        if df is None or len(df) < 50:
            console.print(f"  [red]❌ 데이터 부족[/red]")
            continue

        # 컬럼명 소문자 (MultiIndex 처리)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = [col[0].lower() if isinstance(col, tuple) else col.lower() for col in df.columns]
        else:
            df.columns = df.columns.str.lower()

        df = calculate_squeeze_momentum(df)

        signal = get_current_squeeze_signal(df)

        # 시나리오 1: 진입 가능?
        can_enter, enter_reason = should_enter_trade(df)
        console.print(f"  진입 가능: {can_enter} - {enter_reason}")

        # 시나리오 2: 2% 수익 시 청산?
        should_exit, exit_reason, exit_type = should_exit_trade(df, current_profit_rate=2.0)
        console.print(f"  2% 수익 시 청산: {should_exit}")
        if should_exit:
            console.print(f"    사유: {exit_reason} ({exit_type})")

        # 현재 색상
        color_emoji = {
            'bright_green': '🟢',
            'dark_green': '🟡',
            'dark_red': '🔴',
            'bright_red': '🟠',
            'gray': '⚪'
        }.get(signal['color'], '⚪')
        console.print(f"  현재 상태: {color_emoji} {signal['color']} (모멘텀: {signal['momentum']:.2f})")

    return True


def main():
    """메인 테스트 함수"""
    console.print("[bold green]=" * 50)
    console.print("[bold green]Squeeze Momentum 통합 테스트 시작")
    console.print("[bold green]=" * 50)

    try:
        # 1. 계산 테스트
        if not test_squeeze_momentum_calculation():
            console.print("[red]❌ 스퀴즈 모멘텀 계산 테스트 실패[/red]")
            return

        # 2. 진입 필터 테스트
        if not test_entry_filter():
            console.print("[red]❌ 진입 필터 테스트 실패[/red]")
            return

        # 3. 청산 필터 테스트
        if not test_exit_filter():
            console.print("[red]❌ 청산 필터 테스트 실패[/red]")
            return

        # 4. 실제 거래 시나리오 테스트
        if not test_real_trade_scenarios():
            console.print("[red]❌ 실제 거래 시나리오 테스트 실패[/red]")
            return

        console.print("\n[bold green]=" * 50)
        console.print("[bold green]✅ 모든 테스트 통과!")
        console.print("[bold green]=" * 50)

        console.print("\n[cyan]다음 단계:[/cyan]")
        console.print("  1. config/strategy_hybrid.yaml에서 squeeze_momentum.enabled를 true로 설정")
        console.print("  2. main_auto_trading.py 실행하여 실전 테스트")
        console.print("  3. 로그에서 'Squeeze Momentum' 관련 메시지 확인")

    except Exception as e:
        console.print(f"\n[red]❌ 테스트 실패: {e}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
