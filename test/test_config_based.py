"""
YAML 설정 기반 종합 테스트

새로 추가된 기능 테스트:
1. YAML 설정 파일 로드
2. 중복 진입 방지
3. 시간 필터
4. 변동성 필터
5. 목표가 도달 후 트레일링 강화
6. 부분 청산 로직
"""
import sys
from pathlib import Path

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from datetime import datetime, timedelta
from utils.config_loader import load_config
from analyzers.entry_timing_analyzer import EntryTimingAnalyzer
from analyzers.risk_manager import RiskManager
from utils.trade_logger import TradeLogger
import yfinance as yf
import pandas as pd
from rich.console import Console
from rich.table import Table


console = Console()


def download_test_data(stock_symbol: str, days: int = 7, interval: str = "5m"):
    """야후 파이낸스에서 테스트 데이터 다운로드"""
    ticker = yf.Ticker(stock_symbol)
    df = ticker.history(period=f"{days}d", interval=interval)
    df.reset_index(inplace=True)
    df.columns = [col.lower() for col in df.columns]
    return df


def test_config_loader():
    """설정 로더 테스트"""
    console.print("\n[bold cyan]═══ 1. YAML 설정 로더 테스트 ═══[/bold cyan]")

    config = load_config()

    # 주요 설정 출력
    table = Table(title="주요 설정 값")
    table.add_column("카테고리", style="cyan")
    table.add_column("설정", style="yellow")
    table.add_column("값", style="green")

    # 트레일링
    table.add_row(
        "Trailing",
        "activation_pct",
        str(config.get('trailing.activation_pct'))
    )
    table.add_row(
        "Trailing",
        "use_profit_tier",
        str(config.get('trailing.use_profit_tier'))
    )

    # 필터
    table.add_row(
        "Filters",
        "use_breakout_confirm",
        str(config.get('filters.use_breakout_confirm'))
    )
    table.add_row(
        "Filters",
        "use_volatility_filter",
        str(config.get('filters.use_volatility_filter'))
    )

    # 시간 필터
    table.add_row(
        "Time Filter",
        "use_time_filter",
        str(config.get('time_filter.use_time_filter'))
    )
    table.add_row(
        "Time Filter",
        "avoid_early_minutes",
        str(config.get('time_filter.avoid_early_minutes'))
    )

    # 재진입 방지
    table.add_row(
        "Re-entry",
        "use_cooldown",
        str(config.get('re_entry.use_cooldown'))
    )
    table.add_row(
        "Re-entry",
        "cooldown_minutes",
        str(config.get('re_entry.cooldown_minutes'))
    )

    # 부분 청산
    partial = config.get_partial_exit_config()
    table.add_row(
        "Partial Exit",
        "enabled",
        str(partial['enabled'])
    )
    table.add_row(
        "Partial Exit",
        "tiers",
        str(len(partial['tiers'])) + " tiers"
    )

    console.print(table)

    return config


def test_re_entry_prevention():
    """재진입 방지 테스트"""
    console.print("\n[bold cyan]═══ 2. 재진입 방지 테스트 ═══[/bold cyan]")

    config = load_config()
    analyzer_config = config.get_analyzer_config()
    analyzer = EntryTimingAnalyzer(**analyzer_config)

    stock_code = "005930"
    base_time = datetime(2025, 10, 25, 10, 0, 0)

    # 첫 진입 - 허용되어야 함
    allowed, reason = analyzer.check_re_entry_allowed(stock_code, base_time)
    console.print(f"첫 진입: {'✅ 허용' if allowed else '❌ 차단'} - {reason}")

    # 청산 기록
    analyzer.record_exit(stock_code, base_time)

    # 10분 후 재진입 시도 - 차단되어야 함 (cooldown 30분)
    test_time = base_time + timedelta(minutes=10)
    allowed, reason = analyzer.check_re_entry_allowed(stock_code, test_time)
    console.print(f"10분 후: {'✅ 허용' if allowed else '❌ 차단'} - {reason}")

    # 35분 후 재진입 시도 - 허용되어야 함
    test_time = base_time + timedelta(minutes=35)
    allowed, reason = analyzer.check_re_entry_allowed(stock_code, test_time)
    console.print(f"35분 후: {'✅ 허용' if allowed else '❌ 차단'} - {reason}")


def test_time_filter():
    """시간 필터 테스트"""
    console.print("\n[bold cyan]═══ 3. 시간 필터 테스트 ═══[/bold cyan]")

    config = load_config()
    analyzer_config = config.get_analyzer_config()
    analyzer = EntryTimingAnalyzer(**analyzer_config)

    # 테스트 시간들
    test_times = [
        datetime(2025, 10, 25, 9, 5, 0),   # 장 시작 직후 (09:05)
        datetime(2025, 10, 25, 9, 15, 0),  # 장 시작 15분 후 (09:15)
        datetime(2025, 10, 25, 14, 0, 0),  # 정상 시간 (14:00)
        datetime(2025, 10, 25, 15, 15, 0), # 장 마감 직전 (15:15)
    ]

    for test_time in test_times:
        allowed, reason = analyzer.check_time_filter(test_time)
        status = "✅ 허용" if allowed else "❌ 차단"
        console.print(f"{test_time.strftime('%H:%M')}: {status} - {reason}")


def test_volatility_filter():
    """변동성 필터 테스트"""
    console.print("\n[bold cyan]═══ 4. 변동성 필터 테스트 ═══[/bold cyan]")

    config = load_config()
    analyzer_config = config.get_analyzer_config()
    analyzer = EntryTimingAnalyzer(**analyzer_config)

    # 테스트 데이터 생성
    df = pd.DataFrame({
        'close': [100, 100, 100, 100],
        'high': [102, 102, 102, 102],
        'low': [98, 98, 98, 98],
        'volume': [1000, 1000, 1000, 1000]
    })

    # ATR 계산
    df = analyzer.calculate_atr(df)

    # 정상 변동성 (ATR 2% = 정상 범위)
    allowed, reason = analyzer.check_volatility_filter(df, 3, min_atr_pct=0.5, max_atr_pct=5.0)
    console.print(f"정상 변동성: {'✅ 허용' if allowed else '❌ 차단'} - {reason}")

    # 너무 낮은 변동성 테스트
    df2 = pd.DataFrame({
        'close': [100, 100, 100, 100],
        'high': [100.1, 100.1, 100.1, 100.1],
        'low': [99.9, 99.9, 99.9, 99.9],
        'volume': [1000, 1000, 1000, 1000]
    })
    df2 = analyzer.calculate_atr(df2)
    allowed, reason = analyzer.check_volatility_filter(df2, 3, min_atr_pct=0.5, max_atr_pct=5.0)
    console.print(f"낮은 변동성: {'✅ 허용' if allowed else '❌ 차단'} - {reason}")


def test_profit_tier_trailing():
    """목표가 도달 후 트레일링 강화 테스트"""
    console.print("\n[bold cyan]═══ 5. 목표가 도달 후 트레일링 강화 테스트 ═══[/bold cyan]")

    config = load_config()
    analyzer_config = config.get_analyzer_config()
    analyzer = EntryTimingAnalyzer(**analyzer_config)

    avg_price = 100.0

    # 시나리오 1: 낮은 수익 (+2%) - 일반 트레일링 (1%)
    current_price = 102.0
    highest_price = 102.0
    should_exit, trailing_active, stop_price, reason = analyzer.check_trailing_stop(
        current_price=current_price,
        avg_price=avg_price,
        highest_price=highest_price,
        trailing_active=True,
        use_profit_tier=True,
        profit_tier_threshold=3.0
    )
    console.print(f"수익 +2%: 트레일링 스탑 = {stop_price:.2f} (일반 트레일링 1% 적용)")

    # 시나리오 2: 높은 수익 (+4%) - 강화 트레일링 (0.5%)
    current_price = 104.0
    highest_price = 104.0
    should_exit, trailing_active, stop_price, reason = analyzer.check_trailing_stop(
        current_price=current_price,
        avg_price=avg_price,
        highest_price=highest_price,
        trailing_active=True,
        use_profit_tier=True,
        profit_tier_threshold=3.0
    )
    console.print(f"수익 +4%: 트레일링 스탑 = {stop_price:.2f} (강화 트레일링 0.5% 적용)")


def test_partial_exit():
    """부분 청산 로직 테스트"""
    console.print("\n[bold cyan]═══ 6. 부분 청산 로직 테스트 ═══[/bold cyan]")

    config = load_config()
    analyzer_config = config.get_analyzer_config()
    analyzer = EntryTimingAnalyzer(**analyzer_config)

    # 부분 청산 티어 설정 (YAML에서 로드)
    partial_config = config.get_partial_exit_config()
    exit_tiers = partial_config['tiers']

    console.print(f"부분 청산 티어: {exit_tiers}")

    # 시뮬레이션
    avg_price = 100.0
    initial_quantity = 100
    current_quantity = initial_quantity
    executed_tiers = []

    # 수익률 시뮬레이션
    profit_scenarios = [0.5, 1.0, 1.5, 2.0, 3.0, 4.0]

    for profit_pct in profit_scenarios:
        current_price = avg_price * (1 + profit_pct / 100)

        should_exit, exit_qty, reason, new_executed_tiers = analyzer.check_partial_exit(
            current_price=current_price,
            avg_price=avg_price,
            current_quantity=current_quantity,
            exit_tiers=exit_tiers,
            executed_tiers=executed_tiers
        )

        if should_exit:
            current_quantity -= exit_qty
            executed_tiers = new_executed_tiers
            console.print(f"수익 +{profit_pct}%: {reason} → 잔여 수량 {current_quantity}")
        else:
            console.print(f"수익 +{profit_pct}%: 청산 없음 → 보유 수량 {current_quantity}")


def main():
    """메인 테스트 실행"""
    console.print("\n[bold green]╔══════════════════════════════════════════════════════╗[/bold green]")
    console.print("[bold green]║   YAML 설정 기반 종합 테스트 (3순위 기능)        ║[/bold green]")
    console.print("[bold green]╚══════════════════════════════════════════════════════╝[/bold green]")

    try:
        # 1. 설정 로더 테스트
        config = test_config_loader()

        # 2. 재진입 방지 테스트
        test_re_entry_prevention()

        # 3. 시간 필터 테스트
        test_time_filter()

        # 4. 변동성 필터 테스트
        test_volatility_filter()

        # 5. 목표가 도달 후 트레일링 강화 테스트
        test_profit_tier_trailing()

        # 6. 부분 청산 로직 테스트
        test_partial_exit()

        console.print("\n[bold green]✅ 모든 테스트 완료![/bold green]\n")

    except Exception as e:
        console.print(f"\n[bold red]❌ 테스트 실패: {e}[/bold red]\n")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
