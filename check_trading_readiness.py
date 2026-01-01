#!/usr/bin/env python3
"""
매매 준비 상태 체크 스크립트
- 현재 시간이 매매 가능한지
- 주요 설정이 매매를 차단하지 않는지
- 필터가 너무 엄격하지 않은지
"""

import yaml
from datetime import datetime, time
from rich.console import Console
from rich.table import Table

console = Console()


def check_trading_time():
    """현재 시간이 매매 가능 시간인지 체크"""
    now = datetime.now()
    current_time = now.time()

    # 매수 가능 시간: 10:00 ~ 14:59
    buy_start = time(10, 0, 0)
    buy_end = time(14, 59, 0)

    # 매도 가능 시간: 09:00 ~ 15:30
    sell_start = time(9, 0, 0)
    sell_end = time(15, 30, 0)

    buy_allowed = buy_start <= current_time <= buy_end
    sell_allowed = sell_start <= current_time <= sell_end

    console.print(f"\n[bold cyan]📅 현재 시간: {now.strftime('%Y-%m-%d %H:%M:%S')}[/bold cyan]")
    console.print(f"  매수 가능: {'✅ YES' if buy_allowed else '❌ NO'} (10:00~14:59)")
    console.print(f"  매도 가능: {'✅ YES' if sell_allowed else '❌ NO'} (09:00~15:30)")

    return buy_allowed, sell_allowed


def check_config_settings():
    """주요 설정 파일 체크"""
    console.print(f"\n[bold cyan]⚙️  설정 파일 체크[/bold cyan]")

    try:
        with open('config/strategy_hybrid.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # Risk Control 설정
        risk = config.get('risk_control', {})
        console.print(f"\n[yellow]🛡️  Risk Control:[/yellow]")
        console.print(f"  Hard Stop Loss: {risk.get('hard_stop_pct', 'N/A')}%")
        console.print(f"  Technical Stop: {risk.get('technical_stop_pct', 'N/A')}%")
        console.print(f"  Max Positions: {risk.get('max_positions', 'N/A')}")
        console.print(f"  Max Daily Loss: {risk.get('max_daily_loss_pct', 'N/A')}%")

        # Early Failure Cut
        early = risk.get('early_failure', {})
        console.print(f"\n[yellow]⚡ Early Failure Cut:[/yellow]")
        console.print(f"  Enabled: {early.get('enabled', 'N/A')}")
        console.print(f"  Window: {early.get('window_minutes', 'N/A')}분")
        console.print(f"  Loss Cut: {early.get('loss_cut_pct', 'N/A')}%")

        # Position Sizing
        sizing = config.get('position_sizing', {})
        console.print(f"\n[yellow]💰 Position Sizing:[/yellow]")
        base_amount = sizing.get('base_amount_krw', 'N/A')
        if base_amount != 'N/A':
            console.print(f"  Base Amount: {base_amount:,}원")
        else:
            console.print(f"  Base Amount: N/A")
        console.print(f"  Min Cash Reserve: {sizing.get('min_cash_reserve_pct', 'N/A')}%")

        # L0 System Filter
        l0 = config.get('signal_orchestrator', {}).get('l0_system_filter', {})
        console.print(f"\n[yellow]🔒 L0 System Filter:[/yellow]")
        console.print(f"  Max Daily Loss: {l0.get('max_daily_loss_pct', 'N/A')}%")
        console.print(f"  Max Positions: {l0.get('max_positions', 'N/A')}")
        console.print(f"  Min Cash Reserve: {l0.get('min_cash_reserve_pct', 'N/A')}%")

        # L1 RSVI Filter
        l1 = config.get('signal_orchestrator', {}).get('l1_rsvi_filter', {})
        console.print(f"\n[yellow]📊 L1 RSVI Filter:[/yellow]")
        console.print(f"  Enabled: {l1.get('enabled', 'N/A')}")
        console.print(f"  Min Win Rate: {l1.get('min_win_rate', 'N/A')}%")
        console.print(f"  Max Avg Loss: {l1.get('max_avg_loss_pct', 'N/A')}%")
        console.print(f"  Min Profit Factor: {l1.get('min_profit_factor', 'N/A')}")

        # L2 Market Regime
        l2 = config.get('signal_orchestrator', {}).get('l2_market_regime', {})
        console.print(f"\n[yellow]🌍 L2 Market Regime:[/yellow]")
        console.print(f"  Enabled: {l2.get('enabled', 'N/A')}")
        console.print(f"  Block High Volatility: {l2.get('block_high_volatility', 'N/A')}")
        console.print(f"  Block Bear Market: {l2.get('block_bear_market', 'N/A')}")

        # L3 Concentration Risk
        l3 = config.get('signal_orchestrator', {}).get('l3_concentration_risk', {})
        console.print(f"\n[yellow]🎯 L3 Concentration Risk:[/yellow]")
        console.print(f"  Enabled: {l3.get('enabled', 'N/A')}")
        console.print(f"  Max Same Sector: {l3.get('max_same_sector_positions', 'N/A')}")
        console.print(f"  Max Sector Weight: {l3.get('max_sector_weight_pct', 'N/A')}%")

        # L4 Technical Quality
        l4 = config.get('signal_orchestrator', {}).get('l4_technical_quality', {})
        console.print(f"\n[yellow]📈 L4 Technical Quality:[/yellow]")
        console.print(f"  Enabled: {l4.get('enabled', 'N/A')}")
        console.print(f"  Min Signal Strength: {l4.get('min_signal_strength', 'N/A')}")
        console.print(f"  Required Confirmations: {l4.get('required_confirmations', 'N/A')}")

        # L5 Fundamental Screen
        l5 = config.get('signal_orchestrator', {}).get('l5_fundamental_screen', {})
        console.print(f"\n[yellow]📋 L5 Fundamental Screen:[/yellow]")
        console.print(f"  Enabled: {l5.get('enabled', 'N/A')}")
        console.print(f"  Min Market Cap: {l5.get('min_market_cap_billion', 'N/A')}억")
        console.print(f"  Min Avg Volume: {l5.get('min_avg_volume_million', 'N/A')}백만")

        # L6 ML Confidence (Phase 1)
        l6 = config.get('signal_orchestrator', {}).get('l6_ml_confidence', {})
        console.print(f"\n[yellow]🤖 L6 ML Confidence:[/yellow]")
        console.print(f"  Enabled: {l6.get('enabled', 'N/A')}")
        console.print(f"  Min Confidence: {l6.get('min_confidence_score', 'N/A')}")

        # Confidence Thresholds
        conf = config.get('signal_orchestrator', {}).get('confidence_thresholds', {})
        console.print(f"\n[yellow]🎚️  Confidence Thresholds:[/yellow]")
        console.print(f"  TIER_1 (100%): ≥{conf.get('tier_1_threshold', 'N/A')}")
        console.print(f"  TIER_2 (70%): ≥{conf.get('tier_2_threshold', 'N/A')}")
        console.print(f"  TIER_3 (50%): ≥{conf.get('tier_3_threshold', 'N/A')}")
        console.print(f"  REJECT: <{conf.get('tier_3_threshold', 'N/A')}")

        return config

    except FileNotFoundError:
        console.print("[red]❌ config/strategy_hybrid.yaml 파일을 찾을 수 없습니다[/red]")
        return None
    except Exception as e:
        console.print(f"[red]❌ 설정 파일 읽기 오류: {e}[/red]")
        return None


def check_potential_blockers(config):
    """매매를 차단할 수 있는 잠재적 요인 체크"""
    console.print(f"\n[bold cyan]⚠️  잠재적 차단 요인 체크[/bold cyan]")

    blockers = []
    warnings = []

    if config:
        # L1 RSVI 필터가 너무 엄격한지 체크
        l1 = config.get('signal_orchestrator', {}).get('l1_rsvi_filter', {})
        if l1.get('enabled', True):
            min_wr = l1.get('min_win_rate', 50)
            if min_wr > 60:
                warnings.append(f"L1: 승률 기준이 높음 (≥{min_wr}%)")

            min_pf = l1.get('min_profit_factor', 1.0)
            if min_pf > 1.5:
                warnings.append(f"L1: Profit Factor 기준이 높음 (≥{min_pf})")

        # L4 Technical Quality가 너무 엄격한지
        l4 = config.get('signal_orchestrator', {}).get('l4_technical_quality', {})
        if l4.get('enabled', True):
            min_strength = l4.get('min_signal_strength', 0)
            if min_strength > 0.7:
                warnings.append(f"L4: 신호 강도 기준이 높음 (≥{min_strength})")

            req_conf = l4.get('required_confirmations', 0)
            if req_conf > 3:
                warnings.append(f"L4: 필요 확인 신호가 많음 (≥{req_conf}개)")

        # L5 Fundamental이 활성화되어 있는지
        l5 = config.get('signal_orchestrator', {}).get('l5_fundamental_screen', {})
        if l5.get('enabled', False):
            blockers.append("L5 Fundamental Screen이 활성화됨 (시가총액/거래량 필터)")

        # Confidence Threshold가 너무 높은지
        conf = config.get('signal_orchestrator', {}).get('confidence_thresholds', {})
        tier3 = conf.get('tier_3_threshold', 0.3)
        if tier3 > 0.4:
            warnings.append(f"TIER_3 신뢰도 기준이 높음 (≥{tier3})")

    if blockers:
        console.print("\n[red]🚫 차단 요인:[/red]")
        for b in blockers:
            console.print(f"  - {b}")
    else:
        console.print("\n[green]✅ 심각한 차단 요인 없음[/green]")

    if warnings:
        console.print("\n[yellow]⚡ 주의 사항:[/yellow]")
        for w in warnings:
            console.print(f"  - {w}")
    else:
        console.print("[green]✅ 설정 정상[/green]")

    return blockers, warnings


def check_data_sources():
    """데이터 소스 체크"""
    console.print(f"\n[bold cyan]📡 데이터 소스 체크[/bold cyan]")

    # Kiwoom API 체크 (실제 연결은 런타임에 확인)
    console.print(f"  Kiwoom API: 런타임 시 연결 확인 필요")
    console.print(f"  Yahoo Finance: yfinance 라이브러리 사용")
    console.print(f"  Fallback 순서: Kiwoom → Yahoo .KS → Yahoo .KQ")


def check_watchlist():
    """감시 종목 리스트 체크"""
    console.print(f"\n[bold cyan]👀 감시 종목 리스트 체크[/bold cyan]")

    import json
    try:
        with open('data/watchlist.json', 'r', encoding='utf-8') as f:
            watchlist = json.load(f)

        console.print(f"  종목 수: {len(watchlist)}개")

        if len(watchlist) == 0:
            console.print("[yellow]⚠️  감시 종목이 없습니다. 조건 검색 실행 필요[/yellow]")
        elif len(watchlist) > 50:
            console.print("[yellow]⚠️  감시 종목이 많습니다. 리소스 사용량 증가 가능[/yellow]")
        else:
            console.print("[green]✅ 감시 종목 수 정상[/green]")

    except FileNotFoundError:
        console.print("[yellow]⚠️  data/watchlist.json 파일이 없습니다[/yellow]")
    except Exception as e:
        console.print(f"[red]❌ watchlist 읽기 오류: {e}[/red]")


def print_summary(buy_allowed, sell_allowed, blockers, warnings):
    """최종 요약"""
    console.print(f"\n[bold cyan]{'='*60}[/bold cyan]")
    console.print(f"[bold cyan]📋 매매 준비 상태 요약[/bold cyan]")
    console.print(f"[bold cyan]{'='*60}[/bold cyan]")

    table = Table(show_header=True, header_style="bold magenta")
    table.add_column("항목", style="cyan", width=30)
    table.add_column("상태", width=20)
    table.add_column("비고", width=30)

    # 시간 체크
    table.add_row(
        "현재 시간 (매수)",
        "✅ 가능" if buy_allowed else "❌ 불가",
        "10:00~14:59" if buy_allowed else "10시 이후 매수 가능"
    )

    table.add_row(
        "현재 시간 (매도)",
        "✅ 가능" if sell_allowed else "❌ 불가",
        "09:00~15:30" if sell_allowed else "장 시작 후 매도 가능"
    )

    # 차단 요인
    table.add_row(
        "차단 요인",
        "❌ 있음" if blockers else "✅ 없음",
        f"{len(blockers)}개" if blockers else "정상"
    )

    # 주의 사항
    table.add_row(
        "주의 사항",
        "⚠️  있음" if warnings else "✅ 없음",
        f"{len(warnings)}개" if warnings else "정상"
    )

    console.print(table)

    # 최종 판정
    console.print(f"\n[bold]최종 판정:[/bold]")
    if buy_allowed and not blockers:
        console.print("[bold green]✅ 매매 가능 상태입니다![/bold green]")
    elif not buy_allowed:
        console.print("[bold yellow]⏰ 매수 시간이 아닙니다 (10:00~14:59)[/bold yellow]")
    elif blockers:
        console.print("[bold red]🚫 차단 요인을 해결해야 합니다[/bold red]")

    if warnings:
        console.print("[yellow]⚠️  주의 사항을 확인하세요[/yellow]")


def main():
    console.print("[bold cyan]🔍 매매 준비 상태 체크 시작...[/bold cyan]")

    # 1. 시간 체크
    buy_allowed, sell_allowed = check_trading_time()

    # 2. 설정 파일 체크
    config = check_config_settings()

    # 3. 잠재적 차단 요인 체크
    blockers, warnings = check_potential_blockers(config)

    # 4. 데이터 소스 체크
    check_data_sources()

    # 5. 감시 종목 체크
    check_watchlist()

    # 6. 최종 요약
    print_summary(buy_allowed, sell_allowed, blockers, warnings)

    console.print(f"\n[dim]💡 Tip: 실제 매매는 run.sh로 프로그램을 실행해서 확인하세요[/dim]")


if __name__ == "__main__":
    main()
