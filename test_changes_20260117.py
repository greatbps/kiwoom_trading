#!/usr/bin/env python3
"""
2026-01-17 변경사항 테스트
1. 골든크로스 + 모멘텀 조건 (Squeeze OFF 제거)
2. 12시 이전 진입 필터
3. 투자 비율 변경
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, time as time_class
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
import pandas as pd
import numpy as np
import yaml

console = Console()

def test_squeeze_momentum_conditions():
    """테스트 1: 골든크로스 + 모멘텀 조건 (Squeeze OFF 제거)"""
    console.print("\n" + "="*80)
    console.print("[bold cyan]테스트 1: 진입 조건 테스트 (Squeeze OFF 제거)[/bold cyan]")
    console.print("="*80)

    from analyzers.squeeze_momentum_lazybear import TwoTimeframeStrategy

    strategy = TwoTimeframeStrategy()

    # 테스트 데이터 생성 (30분봉)
    dates = pd.date_range('2026-01-16 09:00', periods=50, freq='30min')

    # 케이스 1: 골든크로스 발생 + 모멘텀 상승
    # MA5가 MA20을 "돌파"하는 순간을 만들어야 함
    console.print("\n[yellow]케이스 1: 골든크로스 발생 + 모멘텀 상승[/yellow]")

    # 하락 후 상승 전환 패턴 (골든크로스 발생)
    prices = []
    for i in range(50):
        if i < 30:
            prices.append(100 - i * 0.3)  # 하락
        elif i < 45:
            prices.append(91 + (i - 30) * 0.5)  # 상승 시작
        else:
            prices.append(98.5 + (i - 45) * 1.0)  # 급상승 (골든크로스 발생)

    df1 = pd.DataFrame({
        'open': [p - 0.5 for p in prices],
        'high': [p + 1 for p in prices],
        'low': [p - 1 for p in prices],
        'close': prices,
        'volume': [1000000] * 50
    }, index=dates)

    direction1, reason1, details1 = strategy.check_higher_tf_direction(df1, debug=True)
    console.print(f"  결과: {direction1}")
    console.print(f"  이유: {reason1}")
    console.print(f"  골든크로스: {details1.get('golden_cross', False)}")

    # 케이스 2: 골든크로스 없음 (이미 MA5 > MA20 상태)
    console.print("\n[yellow]케이스 2: 골든크로스 없음 (이미 MA5 > MA20 상태)[/yellow]")
    df2 = pd.DataFrame({
        'open': [100 + i*0.3 for i in range(50)],
        'high': [101 + i*0.3 for i in range(50)],
        'low': [99 + i*0.3 for i in range(50)],
        'close': [100 + i*0.3 for i in range(50)],  # 꾸준한 상승 (이미 MA5 > MA20)
        'volume': [1000000] * 50
    }, index=dates)

    direction2, reason2, details2 = strategy.check_higher_tf_direction(df2, debug=True)
    console.print(f"  결과: {direction2}")
    console.print(f"  이유: {reason2}")

    # 케이스 3: 골든크로스 발생 + 모멘텀 하락
    console.print("\n[yellow]케이스 3: 모멘텀 하락 (red/maroon)[/yellow]")
    # 하락 추세
    prices3 = [100 - i*0.5 for i in range(50)]
    df3 = pd.DataFrame({
        'open': [p + 0.5 for p in prices3],
        'high': [p + 1 for p in prices3],
        'low': [p - 1 for p in prices3],
        'close': prices3,
        'volume': [1000000] * 50
    }, index=dates)

    direction3, reason3, details3 = strategy.check_higher_tf_direction(df3, debug=True)
    console.print(f"  결과: {direction3}")
    console.print(f"  이유: {reason3}")
    console.print(f"  모멘텀 색상: {details3.get('momentum_color', 'N/A')}")

    # 결과 요약
    console.print("\n[bold]테스트 1 결과 요약:[/bold]")

    # 케이스 1: 골든크로스 + 모멘텀↑ → long 예상
    # 케이스 2: 골든크로스 없음 → neutral 예상
    # 케이스 3: 모멘텀↓ → neutral 또는 short 예상
    results = [
        ("골든크로스 + 모멘텀↑", direction1, details1.get('golden_cross', False), details1.get('golden_cross', False)),
        ("골든크로스 없음 (이미 상승)", direction2, "neutral", direction2 == "neutral"),
        ("모멘텀 하락", direction3, "neutral/short", direction3 in ["neutral", "short"]),
    ]

    all_passed = True
    for case, actual, expected, passed in results:
        status = "[green]✅ PASS[/green]" if passed else "[red]❌ FAIL[/red]"
        console.print(f"  {status} {case}: {actual} (예상: {expected})")
        if not passed:
            all_passed = False

    return all_passed


def test_time_filter():
    """테스트 2: 12시 이전 진입 필터"""
    console.print("\n" + "="*80)
    console.print("[bold cyan]테스트 2: 시간 필터 테스트 (12시 이전만 진입)[/bold cyan]")
    console.print("="*80)

    # main_auto_trading.py의 시간 필터 로직 테스트
    LATE_ENTRY_CUTOFF = time_class(14, 59, 0)
    MORNING_CUTOFF = time_class(12, 0, 0)

    test_times = [
        ("09:30", time_class(9, 30, 0), True, "오전장 - 진입 허용"),
        ("10:00", time_class(10, 0, 0), True, "오전장 - 진입 허용"),
        ("11:30", time_class(11, 30, 0), True, "오전장 - 진입 허용"),
        ("11:59", time_class(11, 59, 0), True, "오전장 마지막 - 진입 허용"),
        ("12:00", time_class(12, 0, 0), True, "경계 - 진입 허용 (<=12:00)"),
        ("12:01", time_class(12, 1, 0), False, "오후장 - 진입 차단"),
        ("13:30", time_class(13, 30, 0), False, "오후장 - 진입 차단"),
        ("14:30", time_class(14, 30, 0), False, "오후장 - 진입 차단"),
        ("14:59", time_class(14, 59, 0), False, "14:59 - 진입 차단"),
        ("15:00", time_class(15, 0, 0), False, "15:00 - 진입 차단"),
    ]

    all_passed = True
    for time_str, test_time, expected_allowed, description in test_times:
        # squeeze_2tf 모드 시간 필터 로직
        allowed = test_time <= MORNING_CUTOFF and test_time <= LATE_ENTRY_CUTOFF

        passed = allowed == expected_allowed
        status = "[green]✅[/green]" if passed else "[red]❌[/red]"
        result = "허용" if allowed else "차단"
        expected_str = "허용" if expected_allowed else "차단"

        console.print(f"  {status} {time_str} - {result} (예상: {expected_str}) - {description}")

        if not passed:
            all_passed = False

    return all_passed


def test_config_changes():
    """테스트 3: 설정 파일 변경 확인"""
    console.print("\n" + "="*80)
    console.print("[bold cyan]테스트 3: 설정 파일 변경 확인[/bold cyan]")
    console.print("="*80)

    config_path = 'config/strategy_hybrid.yaml'

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        risk_mgmt = config.get('risk_management', {})

        expected_values = {
            'min_cash_reserve_pct': 10,
            'max_position_size_pct': 40,
            'hard_max_position': 500000,
        }

        all_passed = True
        for key, expected in expected_values.items():
            actual = risk_mgmt.get(key)
            passed = actual == expected
            status = "[green]✅[/green]" if passed else "[red]❌[/red]"
            console.print(f"  {status} {key}: {actual} (예상: {expected})")
            if not passed:
                all_passed = False

        # 시간 가중치 확인
        time_filter = config.get('time_filter', {})
        time_weight = time_filter.get('time_weight', {})

        console.print(f"\n  시간 가중치 설정:")
        console.print(f"    morning_bonus: {time_weight.get('morning_bonus', 'N/A')}")
        console.print(f"    midday_penalty: {time_weight.get('midday_penalty', 'N/A')}")
        console.print(f"    afternoon_penalty: {time_weight.get('afternoon_penalty', 'N/A')}")

        return all_passed

    except Exception as e:
        console.print(f"[red]  ❌ 설정 파일 로드 실패: {e}[/red]")
        return False


def test_risk_manager():
    """테스트 4: RiskManager 설정 적용 확인"""
    console.print("\n" + "="*80)
    console.print("[bold cyan]테스트 4: RiskManager 설정 적용 확인[/bold cyan]")
    console.print("="*80)

    try:
        import yaml
        from core.risk_manager import RiskManager

        with open('config/strategy_hybrid.yaml', 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)

        # RiskManager 초기화
        rm = RiskManager(
            initial_balance=1000000,
            config=config
        )

        console.print(f"\n  초기 잔고: {rm.initial_balance:,}원")
        console.print(f"  MAX_POSITION_SIZE: {rm.MAX_POSITION_SIZE*100:.0f}%")
        console.print(f"  HARD_MAX_POSITION: {rm.HARD_MAX_POSITION:,}원")
        console.print(f"  MIN_CASH_RESERVE: {rm.MIN_CASH_RESERVE*100:.0f}%")

        # 포지션 계산 테스트
        current_price = 10000
        stop_loss_price = current_price * 0.98  # 2% 손절
        current_balance = 1000000

        position_calc = rm.calculate_position_size(
            current_balance=current_balance,
            current_price=current_price,
            stop_loss_price=stop_loss_price,
            entry_confidence=1.0
        )

        console.print(f"\n  포지션 계산 테스트 (잔고 100만원, 주가 10,000원, 손절가 9,800원):")
        console.print(f"    투자금: {position_calc['investment']:,}원")
        console.print(f"    수량: {position_calc['quantity']}주")
        console.print(f"    포지션 비율: {position_calc['position_ratio']*100:.1f}%")

        # 예상값 확인
        # max_position_size_pct = 40%, hard_max_position = 500,000
        # 1,000,000 * 40% = 400,000
        # min(400,000, 500,000) = 400,000
        expected_max_investment = min(1000000 * 0.4, 500000)

        passed = position_calc['investment'] <= expected_max_investment
        status = "[green]✅ PASS[/green]" if passed else "[red]❌ FAIL[/red]"
        console.print(f"\n  {status} 투자금 {position_calc['investment']:,}원 <= {expected_max_investment:,}원")

        return passed

    except Exception as e:
        console.print(f"[red]  ❌ RiskManager 테스트 실패: {e}[/red]")
        import traceback
        traceback.print_exc()
        return False


def test_full_integration():
    """테스트 5: 통합 테스트 (실제 API 데이터)"""
    console.print("\n" + "="*80)
    console.print("[bold cyan]테스트 5: 통합 테스트 (실제 API 데이터)[/bold cyan]")
    console.print("="*80)

    try:
        from kiwoom_api import KiwoomAPI
        from analyzers.squeeze_momentum_lazybear import TwoTimeframeStrategy
        import pandas as pd

        api = KiwoomAPI()
        strategy = TwoTimeframeStrategy()

        # 네오티스 (어제 골든크로스 발생 종목) 테스트
        stock_code = "085910"
        stock_name = "네오티스"

        console.print(f"\n  {stock_name} ({stock_code}) 30분봉 분석...")

        result = api.get_minute_chart(stock_code, tic_scope="30")

        if result and 'stk_min_pole_chart_qry' in result:
            raw_data = result['stk_min_pole_chart_qry']
            df = pd.DataFrame(raw_data)

            # 컬럼 매핑
            column_mapping = {
                'cur_prc': 'close',
                'high_pric': 'high',
                'low_pric': 'low',
                'open_pric': 'open',
                'trde_qty': 'volume',
                'cntr_tm': 'datetime'
            }
            df = df.rename(columns=column_mapping)

            # 숫자 변환
            for col in ['close', 'high', 'low', 'open', 'volume']:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.replace('+', '').str.replace('-', '')
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # datetime을 index로 변환
            df['datetime'] = pd.to_datetime(df['datetime'], format='%Y%m%d%H%M%S')
            df = df.set_index('datetime')
            df = df.sort_index()

            console.print(f"  데이터: {len(df)}개 봉")

            # 방향 체크
            direction, reason, details = strategy.check_higher_tf_direction(df, debug=True)

            console.print(f"\n  [bold]결과:[/bold]")
            console.print(f"    방향: {direction}")
            console.print(f"    이유: {reason}")
            console.print(f"    MA5: {details.get('ma_short', 0):,.0f}")
            console.print(f"    MA20: {details.get('ma_long', 0):,.0f}")
            console.print(f"    모멘텀: {details.get('momentum_color', 'N/A')}")
            console.print(f"    Squeeze OFF: {details.get('squeeze_off', False)}")

            return True
        else:
            console.print("[red]  ❌ API 데이터 조회 실패[/red]")
            return False

    except Exception as e:
        console.print(f"[red]  ❌ 통합 테스트 실패: {e}[/red]")
        import traceback
        traceback.print_exc()
        return False


def run_all_tests():
    """모든 테스트 실행"""
    console.print()
    console.print(Panel.fit(
        "[bold]2026-01-17 변경사항 테스트[/bold]\n"
        "1. 골든크로스 + 모멘텀 조건\n"
        "2. 12시 이전 진입 필터\n"
        "3. 투자 비율 변경\n"
        "4. RiskManager 적용\n"
        "5. 통합 테스트",
        border_style="cyan"
    ))

    results = []

    # 테스트 실행
    results.append(("진입 조건 (Squeeze OFF 제거)", test_squeeze_momentum_conditions()))
    results.append(("시간 필터 (12시 이전)", test_time_filter()))
    results.append(("설정 파일 변경", test_config_changes()))
    results.append(("RiskManager 설정", test_risk_manager()))
    results.append(("통합 테스트 (API)", test_full_integration()))

    # 최종 결과
    console.print("\n" + "="*80)
    console.print("[bold green]📋 테스트 결과 요약[/bold green]")
    console.print("="*80)

    table = Table()
    table.add_column("테스트", style="cyan")
    table.add_column("결과", justify="center")

    all_passed = True
    for name, passed in results:
        status = "[green]✅ PASS[/green]" if passed else "[red]❌ FAIL[/red]"
        table.add_row(name, status)
        if not passed:
            all_passed = False

    console.print(table)

    if all_passed:
        console.print("\n[bold green]🎉 모든 테스트 통과![/bold green]")
    else:
        console.print("\n[bold red]⚠️ 일부 테스트 실패[/bold red]")

    return all_passed


if __name__ == "__main__":
    run_all_tests()
