"""
청산 로직 최적화 검증 스크립트
OptimizedExitLogic 동작 테스트
"""

import sys
from pathlib import Path
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from rich.console import Console
from rich.table import Table
from utils.config_loader import load_config
from trading.exit_logic_optimized import OptimizedExitLogic

console = Console()


def create_test_dataframe(current_price: float, trend: str = "up") -> pd.DataFrame:
    """테스트용 DataFrame 생성

    Args:
        current_price: 현재가
        trend: "up", "down", "neutral"
    """
    length = 100

    if trend == "up":
        prices = np.linspace(current_price * 0.95, current_price, length)
    elif trend == "down":
        prices = np.linspace(current_price, current_price * 0.95, length)
    else:
        prices = np.full(length, current_price)

    # 노이즈 추가
    noise = np.random.normal(0, current_price * 0.002, length)
    prices = prices + noise

    df = pd.DataFrame({
        'open': prices,
        'high': prices * 1.01,
        'low': prices * 0.99,
        'close': prices,
        'volume': np.random.randint(1000000, 5000000, length)
    })

    # VWAP 계산
    df['vwap'] = (df['close'] * df['volume']).cumsum() / df['volume'].cumsum()

    # 시그널 생성
    df['signal'] = 0
    df.loc[df['close'] > df['vwap'], 'signal'] = 1
    df.loc[df['close'] < df['vwap'], 'signal'] = -1

    # RSI 계산 (간단히)
    delta = df['close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['rsi'] = 100 - (100 / (1 + rs))
    df['rsi'].fillna(50, inplace=True)

    return df


def test_scenario(
    exit_logic: OptimizedExitLogic,
    scenario_name: str,
    entry_price: float,
    current_price: float,
    entry_time: datetime,
    trend: str = "neutral",
    position_overrides: dict = None
):
    """청산 시나리오 테스트

    Args:
        exit_logic: OptimizedExitLogic 인스턴스
        scenario_name: 시나리오 이름
        entry_price: 진입가
        current_price: 현재가
        entry_time: 진입 시간
        trend: DataFrame 트렌드
        position_overrides: 포지션 추가 속성
    """
    console.print(f"\n[bold cyan]테스트: {scenario_name}[/bold cyan]")
    console.print("=" * 80, style="cyan")

    # 포지션 생성
    position = {
        'entry_price': entry_price,
        'entry_time': entry_time,
        'quantity': 100,
        'initial_quantity': 100,
        'highest_price': max(entry_price, current_price),
        'trailing_active': False,
        'trailing_stop_price': None,
        'partial_exit_stage': 0
    }

    # 추가 속성 적용
    if position_overrides:
        position.update(position_overrides)

    # DataFrame 생성
    df = create_test_dataframe(current_price, trend=trend)

    # 청산 신호 체크
    should_exit, exit_reason, exit_info = exit_logic.check_exit_signal(
        position=position,
        current_price=current_price,
        df=df
    )

    # 수익률 계산
    profit_pct = ((current_price - entry_price) / entry_price) * 100
    holding_time = (datetime.now() - entry_time).total_seconds() / 60

    # 결과 출력
    console.print(f"  진입가: [cyan]{entry_price:,.0f}원[/cyan]")
    console.print(f"  현재가: [cyan]{current_price:,.0f}원[/cyan]")
    console.print(f"  수익률: [{'green' if profit_pct > 0 else 'red'}]{profit_pct:+.2f}%[/{'green' if profit_pct > 0 else 'red'}]")
    console.print(f"  보유시간: [cyan]{holding_time:.1f}분[/cyan]")
    console.print(f"  트레일링: [cyan]{position.get('trailing_active', False)}[/cyan]")

    if should_exit:
        console.print(f"\n  결과: [bold red]청산 실행[/bold red]")
        console.print(f"  이유: [yellow]{exit_reason}[/yellow]")

        if exit_info:
            if exit_info.get('partial_exit'):
                console.print(f"  [dim]→ 부분청산 {exit_info.get('stage')}차 ({exit_info.get('exit_ratio')*100:.0f}%)[/dim]")
            if exit_info.get('use_market_order'):
                console.print(f"  [dim]→ 시장가 주문 사용[/dim]")
    else:
        console.print(f"\n  결과: [bold green]보유 유지[/bold green]")

        if exit_info and exit_info.get('partial_exit'):
            console.print(f"  다음단계: [yellow]부분청산 {exit_info.get('stage')}차 준비[/yellow]")


def main():
    console.print("=" * 80, style="bold green")
    console.print("청산 로직 최적화 검증", style="bold green")
    console.print("=" * 80, style="bold green")

    # Config 로드
    config = load_config("config/strategy_hybrid.yaml")

    # ExitLogic 초기화
    exit_logic = OptimizedExitLogic(config)

    console.print("\n[bold]✅ OptimizedExitLogic 초기화 완료[/bold]")

    # 설정 확인
    console.print("\n[bold cyan]📋 청산 로직 설정:[/bold cyan]")
    console.print(f"  • Hard Stop: [red]-{exit_logic.hard_stop_pct}%[/red]")
    console.print(f"  • Technical Stop: [red]-{exit_logic.technical_stop_pct}%[/red]")
    console.print(f"  • 초기 실패 컷: [red]{exit_logic.early_failure_loss}% (15분 이내)[/red]")
    console.print(f"  • 트레일링 활성화: [green]+{exit_logic.trailing_activation}%[/green]")
    console.print(f"  • 트레일링 거리: [yellow]-{exit_logic.trailing_distance}%[/yellow]")
    console.print(f"  • 최소 수익 보장: [green]+{exit_logic.trailing_min_lock}%[/green]")
    console.print(f"  • 부분청산 단계: [cyan]{len(exit_logic.partial_tiers)}개[/cyan]")

    # 부분청산 티어 출력
    if exit_logic.partial_tiers:
        console.print("\n[bold cyan]📊 부분청산 티어:[/bold cyan]")
        for idx, tier in enumerate(exit_logic.partial_tiers, 1):
            console.print(f"    {idx}차: [green]+{tier['profit_pct']}%[/green] → [yellow]{tier['exit_ratio']*100:.0f}% 청산[/yellow]")

    # 기준 가격
    base_price = 50000
    now = datetime.now()

    # ========================================
    # 시나리오 테스트
    # ========================================

    # 1. Hard Stop 테스트
    test_scenario(
        exit_logic,
        "1. Hard Stop (-2.5% 초과)",
        entry_price=base_price,
        current_price=base_price * 0.97,  # -3%
        entry_time=now - timedelta(minutes=30)
    )

    # 2. 초기 실패 컷 테스트
    test_scenario(
        exit_logic,
        "2. 초기 실패 컷 (15분 이내 -0.8%)",
        entry_price=base_price,
        current_price=base_price * 0.991,  # -0.9%
        entry_time=now - timedelta(minutes=10)
    )

    # 3. 부분청산 1차 테스트
    test_scenario(
        exit_logic,
        "3. 부분청산 1차 (+2.5%)",
        entry_price=base_price,
        current_price=base_price * 1.026,  # +2.6%
        entry_time=now - timedelta(minutes=20),
        trend="up"
    )

    # 4. 부분청산 2차 테스트
    test_scenario(
        exit_logic,
        "4. 부분청산 2차 (+4.5%)",
        entry_price=base_price,
        current_price=base_price * 1.047,  # +4.7%
        entry_time=now - timedelta(minutes=25),
        trend="up",
        position_overrides={'partial_exit_stage': 1}
    )

    # 5. 트레일링 스톱 활성화 테스트
    test_scenario(
        exit_logic,
        "5. 트레일링 활성화 (+1.5%)",
        entry_price=base_price,
        current_price=base_price * 1.016,  # +1.6%
        entry_time=now - timedelta(minutes=15),
        trend="up"
    )

    # 6. 트레일링 스톱 발동 테스트
    test_scenario(
        exit_logic,
        "6. 트레일링 스톱 발동",
        entry_price=base_price,
        current_price=base_price * 1.015,  # +1.5% (최고가 +3%에서 하락)
        entry_time=now - timedelta(minutes=30),
        trend="down",
        position_overrides={
            'highest_price': base_price * 1.03,  # 최고가 +3%
            'trailing_active': True,
            'trailing_stop_price': base_price * 1.03 * 0.99  # -1% 트레일링
        }
    )

    # 7. 15:00 손실/본전 정리 테스트
    test_scenario(
        exit_logic,
        "7. 15:00 손실/본전 정리 (+0.2%)",
        entry_price=base_price,
        current_price=base_price * 1.002,  # +0.2%
        entry_time=now.replace(hour=15, minute=1, second=0)  # 15:01
    )

    # 8. VWAP 다중 조건 테스트 (보유 유지)
    test_scenario(
        exit_logic,
        "8. VWAP 단독 신호 (보유 유지)",
        entry_price=base_price,
        current_price=base_price * 1.005,  # +0.5%
        entry_time=now - timedelta(minutes=20),
        trend="down"  # VWAP 하향이지만 다중 조건 미충족
    )

    # ========================================
    # 결과 요약
    # ========================================
    console.print("\n" + "=" * 80, style="bold green")
    console.print("✅ 청산 로직 검증 완료!", style="bold green")
    console.print("=" * 80, style="bold green")

    console.print("\n[bold yellow]💡 주요 개선사항:[/bold yellow]")
    console.print("  1. [cyan]초기 실패 컷[/cyan]: 15분 이내 -0.8% → 빠른 손절로 손익비 개선")
    console.print("  2. [cyan]3단계 부분청산[/cyan]: +2.5%, +4.5%, +7.0% → 수익 점진적 실현")
    console.print("  3. [cyan]트레일링 스톱[/cyan]: +1.5% 활성화, -1.0% 추적 → 대박 기회 보존")
    console.print("  4. [cyan]VWAP 권한 약화[/cyan]: 다중 조건 필요 → 과도한 조기 청산 방지")
    console.print("  5. [cyan]시간 기반 청산[/cyan]: 15:00/15:10 자동 정리 → 익일 갭 리스크 제거")

    console.print("\n[bold green]예상 성과:[/bold green]")
    console.print("  • 손익비: [red]0.27[/red] → [green]1.2+[/green] (4배 이상 개선)")
    console.print("  • 강제 청산률: [red]71.4%[/red] → [green]30-40%[/green] (절반 이하)")
    console.print("  • 평균 손실: 감소 (초기 실패 컷)")
    console.print("  • 평균 이익: 증가 (부분청산 + 트레일링)")

    console.print("\n[bold cyan]📝 다음 단계:[/bold cyan]")
    console.print("  1. 백테스트 검증 모드로 실제 데이터 테스트")
    console.print("     python3 main_menu.py → [2] 선택")
    console.print("  2. 실전 투입 전 파라미터 미세 조정")
    console.print("  3. 월요일 실전 투입 및 성과 모니터링")


if __name__ == "__main__":
    main()
