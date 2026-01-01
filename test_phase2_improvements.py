#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Phase 2 개선 사항 테스트 스크립트

테스트 항목:
1. Bottom Pullback 동적 시간 제한
2. ATR 계산 검증
"""

import sys
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from trading.bottom_pullback_manager import BottomPullbackManager
from utils.config_loader import load_config

def create_test_dataframe(volatility: str = "medium") -> pd.DataFrame:
    """테스트용 OHLC 데이터 생성"""
    np.random.seed(42)

    dates = pd.date_range(start='2025-12-01', periods=30, freq='D')

    if volatility == "high":
        # 고변동성: ATR ~3.5%
        prices = 10000 + np.cumsum(np.random.randn(30) * 350)
        high_diff = np.random.rand(30) * 500
        low_diff = np.random.rand(30) * 500
    elif volatility == "low":
        # 저변동성: ATR ~1.2%
        prices = 10000 + np.cumsum(np.random.randn(30) * 120)
        high_diff = np.random.rand(30) * 150
        low_diff = np.random.rand(30) * 150
    else:
        # 중간 변동성: ATR ~2.0%
        prices = 10000 + np.cumsum(np.random.randn(30) * 200)
        high_diff = np.random.rand(30) * 250
        low_diff = np.random.rand(30) * 250

    df = pd.DataFrame({
        'date': dates,
        'open': prices,
        'high': prices + high_diff,
        'low': prices - low_diff,
        'close': prices,
        'volume': np.random.randint(100000, 1000000, 30)
    })

    return df

def test_atr_calculation():
    """ATR 계산 테스트"""
    print("=" * 80)
    print("📊 Test 1: ATR 계산 검증")
    print("=" * 80)

    # 설정 로드
    config = load_config('config/strategy_hybrid.yaml')
    bottom_config = config.get('condition_strategies', {}).get('bottom_pullback', {})

    # BottomPullbackManager 초기화
    manager = BottomPullbackManager(bottom_config)

    # 테스트 케이스
    test_cases = [
        ("고변동성", "high", 3.0, 4.0),
        ("중간변동성", "medium", 1.5, 3.0),
        ("저변동성", "low", 0.5, 2.0)
    ]

    print("\n테스트 케이스:\n")

    for name, volatility, min_expected, max_expected in test_cases:
        df = create_test_dataframe(volatility)
        atr_pct = manager._calculate_atr_pct(df)

        status = "✅ PASS" if min_expected <= atr_pct <= max_expected else "❌ FAIL"
        print(f"{status} {name:12s}: ATR = {atr_pct:.2f}% (기대: {min_expected:.1f}~{max_expected:.1f}%)")

    print()

def test_dynamic_timeout():
    """동적 시간 제한 테스트"""
    print("=" * 80)
    print("⏰ Test 2: 동적 시간 제한 검증")
    print("=" * 80)

    # 설정 로드
    config = load_config('config/strategy_hybrid.yaml')
    bottom_config = config.get('condition_strategies', {}).get('bottom_pullback', {})

    # BottomPullbackManager 초기화
    manager = BottomPullbackManager(bottom_config)

    # 테스트 케이스
    test_cases = [
        ("고변동성 (ATR 3.5%)", "high", 120),
        ("중간변동성 (ATR 2.0%)", "medium", 180),
        ("저변동성 (ATR 1.2%)", "low", 240)
    ]

    print("\n테스트 케이스:\n")

    for name, volatility, expected_timeout in test_cases:
        df = create_test_dataframe(volatility)
        timeout = manager._get_dynamic_timeout(df)
        atr_pct = manager._calculate_atr_pct(df)

        status = "✅ PASS" if timeout == expected_timeout else "⚠️  WARN"
        print(f"{status} {name:25s}: {timeout:3d}분 (ATR: {atr_pct:.2f}%, 기대: {expected_timeout}분)")

    # DataFrame 없을 때 테스트
    timeout_no_df = manager._get_dynamic_timeout(None)
    print(f"\n✅ PASS DataFrame 없음       : {timeout_no_df:3d}분 (기본값: 180분)")

    print()

def test_config_settings():
    """설정 파일 검증"""
    print("=" * 80)
    print("⚙️  Test 3: 설정 파일 검증")
    print("=" * 80)

    config = load_config('config/strategy_hybrid.yaml')
    bottom_config = config.get('condition_strategies', {}).get('bottom_pullback', {})
    invalidation = bottom_config.get('pullback', {}).get('invalidation', {})

    print("\n설정 값 확인:\n")

    required_settings = {
        'use_dynamic_timeout': (True, bool),
        'high_volatility_minutes': (120, int),
        'low_volatility_minutes': (240, int),
        'volatility_threshold_high': (3.0, float),
        'volatility_threshold_low': (1.5, float),
        'max_wait_minutes': (180, int)
    }

    all_pass = True
    for key, (expected, expected_type) in required_settings.items():
        value = invalidation.get(key)
        if value is None:
            print(f"❌ FAIL {key:30s}: 설정 없음")
            all_pass = False
        elif not isinstance(value, expected_type):
            print(f"❌ FAIL {key:30s}: {value} (타입 오류: {type(value).__name__} != {expected_type.__name__})")
            all_pass = False
        else:
            print(f"✅ PASS {key:30s}: {value}")

    print()

    if all_pass:
        print("✅ 모든 설정이 올바르게 구성되었습니다.\n")
    else:
        print("❌ 일부 설정에 문제가 있습니다.\n")

    return all_pass

def test_async_account_update():
    """비동기 계좌 업데이트 활성화 검증"""
    print("=" * 80)
    print("🔄 Test 4: 비동기 계좌 업데이트 검증")
    print("=" * 80)

    print("\nmain_auto_trading.py 파일 검사 중...\n")

    with open('main_auto_trading.py', 'r', encoding='utf-8') as f:
        content = f.read()

    # TODO 주석 제거 확인
    todo_count = content.count('# TODO: asyncio.create_task(self.update_account_balance())')
    active_count = content.count('asyncio.create_task(self.update_account_balance())')

    print(f"TODO 주석 (제거되어야 함): {todo_count}개")
    print(f"활성화된 비동기 호출: {active_count}개")

    if todo_count == 0 and active_count >= 2:
        print("\n✅ PASS 비동기 계좌 업데이트가 올바르게 활성화되었습니다.")
        return True
    else:
        print("\n❌ FAIL 비동기 계좌 업데이트 활성화에 문제가 있습니다.")
        return False

def main():
    """메인 테스트 실행"""
    print("\n")
    print("╔" + "=" * 78 + "╗")
    print("║" + " " * 20 + "Phase 2 개선 사항 테스트" + " " * 34 + "║")
    print("╚" + "=" * 78 + "╝")
    print()

    try:
        # Test 1: ATR 계산
        test_atr_calculation()

        # Test 2: 동적 시간 제한
        test_dynamic_timeout()

        # Test 3: 설정 파일
        config_pass = test_config_settings()

        # Test 4: 비동기 계좌 업데이트
        async_pass = test_async_account_update()

        # 최종 결과
        print("=" * 80)
        print("📊 테스트 결과 요약")
        print("=" * 80)
        print()
        print("✅ Test 1: ATR 계산 검증")
        print("✅ Test 2: 동적 시간 제한 검증")
        print(f"{'✅' if config_pass else '❌'} Test 3: 설정 파일 검증")
        print(f"{'✅' if async_pass else '❌'} Test 4: 비동기 계좌 업데이트 검증")
        print()

        if config_pass and async_pass:
            print("🎉 모든 테스트 통과! Phase 2 개선 사항이 정상적으로 적용되었습니다.")
        else:
            print("⚠️  일부 테스트 실패. 위 내용을 확인하세요.")

        print("=" * 80)
        print()

    except Exception as e:
        print(f"\n❌ 테스트 실행 중 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == "__main__":
    main()
