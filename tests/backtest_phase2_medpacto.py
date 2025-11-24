"""
Phase 2 Multi-Alpha Engine 백테스트: 메드팩토 6건 시나리오

목적: Phase 2가 손실 거래를 효과적으로 차단하는지 검증
기대 효과: 6건 → 1건 (5건 차단), 손실 -3,910원 → -124원 (-97%)
"""

import sys
from pathlib import Path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table

from trading.alpha_engine import SimonsStyleAlphaEngine
from trading.alphas.vwap_alpha import VWAPAlpha
from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha
from trading.alphas.obv_trend_alpha import OBVTrendAlpha
from trading.alphas.institutional_flow_alpha import InstitutionalFlowAlpha
from trading.alphas.news_score_alpha import NewsScoreAlpha

console = Console()


def create_mock_state(vwap_score, volume_score, obv_score, inst_score, news_score):
    """
    Mock 상태 생성 (각 알파의 예상 점수 기반)

    Args:
        vwap_score: VWAP alpha 예상 점수 (-3 ~ +3)
        volume_score: Volume Spike alpha 예상 점수
        obv_score: OBV Trend alpha 예상 점수
        inst_score: Institutional Flow alpha 예상 점수
        news_score: News Score alpha 예상 점수 (0-100)
    """
    # VWAP 설정
    if vwap_score > 0:
        # 상승 시나리오: 현재가 > VWAP, EMA 정렬
        price = 7000
        vwap = 6800
        ema_5 = 6950
        ema_15 = 6900
        ema_60 = 6850
    else:
        # 하락 시나리오
        price = 6800
        vwap = 7000
        ema_5 = 6850
        ema_15 = 6900
        ema_60 = 6950

    # 거래량 설정 (Z-score 기반)
    if volume_score > 0:
        # 거래량 급등
        volumes = [1000] * 40 + [5000]  # Z-score ~4
    elif volume_score < 0:
        # 거래량 감소
        volumes = [5000] * 40 + [1000]  # Z-score ~-4
    else:
        volumes = [1000] * 41  # 평범

    # DataFrame 생성
    df = pd.DataFrame({
        'close': [price] * 50,
        'high': [price * 1.01] * 50,
        'low': [price * 0.99] * 50,
        'volume': volumes + [1000] * 9,  # 총 50개
        'vwap': [vwap] * 50
    })

    # 수급 데이터
    inst_flow = {
        "inst_net_buy": int(inst_score * 10000000),  # 1억 단위
        "foreign_net_buy": 0,
        "total_traded_value": 100000000  # 1억
    }

    # AI 분석 (뉴스 점수)
    ai_analysis = {
        "scores": {
            "news": news_score
        }
    }

    state = {
        "df": df,
        "df_5m": df,
        "institutional_flow": inst_flow,
        "ai_analysis": ai_analysis
    }

    return state


def main():
    console.print("\n" + "=" * 80)
    console.print("🧪 Phase 2 백테스트: 메드팩토 6건 시나리오", style="bold cyan")
    console.print("=" * 80 + "\n")

    # Alpha Engine 초기화 (실제 시스템과 동일)
    engine = SimonsStyleAlphaEngine(
        alphas=[
            VWAPAlpha(weight=2.0),
            VolumeSpikeAlpha(weight=1.5, lookback=40),
            OBVTrendAlpha(weight=1.2, fast=5, slow=20),
            InstitutionalFlowAlpha(weight=1.0),
            NewsScoreAlpha(weight=0.8),
        ]
    )

    # 메드팩토 6건 시나리오 (문서 기준)
    scenarios = [
        {
            "time": "10:11",
            "vwap": 2.0,
            "volume": -0.5,
            "obv": -1.0,
            "inst": 0.2,
            "news": 62.5,  # +0.5 * 50 + 50 = 75 (재계산: 0.5 점수 → 62.5 raw)
            "expected_agg": 0.32,
            "actual_result": -1.41,
            "description": "VWAP 돌파했지만 거래량/OBV 약세"
        },
        {
            "time": "10:13",
            "vwap": 1.5,
            "volume": 0.8,
            "obv": -1.5,
            "inst": -0.3,
            "news": 62.5,
            "expected_agg": 0.18,
            "actual_result": -4.53,
            "description": "약한 VWAP, OBV/수급 악화"
        },
        {
            "time": "10:16",
            "vwap": 2.5,
            "volume": 2.0,
            "obv": 1.0,
            "inst": 0.8,
            "news": 62.5,
            "expected_agg": 2.26,
            "actual_result": -0.62,
            "description": "모든 지표 강세 (진입 허용)"
        },
        {
            "time": "10:18",
            "vwap": 1.8,
            "volume": -1.0,
            "obv": -2.0,
            "inst": 0.0,
            "news": 62.5,
            "expected_agg": -0.10,
            "actual_result": -1.39,
            "description": "VWAP 약하고 거래량/OBV 급락"
        },
        # 나머지 2건 (상세 데이터 추정)
        {
            "time": "10:20",
            "vwap": 1.2,
            "volume": -0.8,
            "obv": -1.2,
            "inst": -0.5,
            "news": 50,
            "expected_agg": -0.50,
            "actual_result": -1.57,
            "description": "전반적 약세"
        },
        {
            "time": "10:25",
            "vwap": 1.0,
            "volume": 0.5,
            "obv": -0.8,
            "inst": 0.0,
            "news": 55,
            "expected_agg": 0.40,
            "actual_result": -0.60,
            "description": "약한 신호"
        }
    ]

    # 백테스트 실행
    results = []
    total_old_loss = 0.0
    total_new_loss = 0.0
    blocked_count = 0

    table = Table(title="메드팩토 6건 Phase 2 백테스트 결과")
    table.add_column("시간", style="cyan")
    table.add_column("예상\nAgg", justify="right")
    table.add_column("실제\nAgg", justify="right", style="yellow")
    table.add_column("Phase 1", justify="center")
    table.add_column("Phase 2", justify="center")
    table.add_column("실제손익%", justify="right", style="red")
    table.add_column("설명")

    for scenario in scenarios:
        # Mock state 생성
        state = create_mock_state(
            vwap_score=scenario["vwap"],
            volume_score=scenario["volume"],
            obv_score=scenario["obv"],
            inst_score=scenario["inst"],
            news_score=scenario["news"]
        )

        # Alpha Engine 실행
        result = engine.compute("235980", state)
        agg_score = result["aggregate_score"]

        # 판단
        phase1_decision = "✅ 진입"  # Phase 1은 모두 진입
        phase2_decision = "✅ 진입" if agg_score > 1.0 else "❌ 차단"

        # 손실 집계
        total_old_loss += scenario["actual_result"]
        if agg_score > 1.0:
            total_new_loss += scenario["actual_result"]
        else:
            blocked_count += 1

        # 테이블 행 추가
        table.add_row(
            scenario["time"],
            f"{scenario['expected_agg']:+.2f}",
            f"{agg_score:+.2f}",
            phase1_decision,
            phase2_decision,
            f"{scenario['actual_result']:.2f}%",
            scenario["description"]
        )

        results.append({
            "time": scenario["time"],
            "expected_agg": scenario["expected_agg"],
            "actual_agg": agg_score,
            "phase2_pass": agg_score > 1.0,
            "loss_pct": scenario["actual_result"]
        })

    console.print(table)
    console.print()

    # 요약
    console.print("=" * 80)
    console.print("📊 백테스트 요약", style="bold green")
    console.print("=" * 80)
    console.print(f"총 거래 건수: {len(scenarios)}건")
    console.print(f"Phase 1 진입: {len(scenarios)}건 (100%)")
    console.print(f"Phase 2 차단: {blocked_count}건 ({blocked_count/len(scenarios)*100:.1f}%)")
    console.print(f"Phase 2 진입: {len(scenarios)-blocked_count}건")
    console.print()
    console.print(f"Phase 1 총 손실: {total_old_loss:.2f}%")
    console.print(f"Phase 2 총 손실: {total_new_loss:.2f}%")
    console.print(f"손실 개선: {(1 - total_new_loss/total_old_loss)*100:.1f}%", style="bold green")
    console.print()

    # 가격 기준 계산 (1주당 7,000원 가정)
    entry_price = 7000
    old_loss_krw = total_old_loss / 100 * entry_price * 20  # 20주 가정
    new_loss_krw = total_new_loss / 100 * entry_price * 20

    console.print(f"Phase 1 손실액: {old_loss_krw:,.0f}원 (20주 기준)")
    console.print(f"Phase 2 손실액: {new_loss_krw:,.0f}원 (20주 기준)")
    console.print(f"절감액: {old_loss_krw - new_loss_krw:,.0f}원", style="bold green")
    console.print()

    # 성공 기준 확인
    console.print("=" * 80)
    console.print("✅ 성공 기준 확인", style="bold cyan")
    console.print("=" * 80)

    target_blocked = 5
    target_improvement = 90  # 90% 손실 감소

    actual_improvement = (1 - total_new_loss/total_old_loss) * 100

    if blocked_count >= target_blocked:
        console.print(f"✅ 차단 건수: {blocked_count}건 ≥ {target_blocked}건 목표", style="green")
    else:
        console.print(f"❌ 차단 건수: {blocked_count}건 < {target_blocked}건 목표", style="red")

    if actual_improvement >= target_improvement:
        console.print(f"✅ 손실 개선: {actual_improvement:.1f}% ≥ {target_improvement}% 목표", style="green")
    else:
        console.print(f"❌ 손실 개선: {actual_improvement:.1f}% < {target_improvement}% 목표", style="red")

    console.print()
    console.print("=" * 80)
    console.print("🎯 결론", style="bold yellow")
    console.print("=" * 80)

    if blocked_count >= target_blocked and actual_improvement >= target_improvement:
        console.print("✅ Phase 2 Multi-Alpha Engine이 성공적으로 손실 거래를 차단했습니다!", style="bold green")
    else:
        console.print("⚠️  Phase 2 성능이 목표에 미달합니다. 알파 가중치 조정이 필요할 수 있습니다.", style="yellow")

    console.print()


if __name__ == "__main__":
    main()
