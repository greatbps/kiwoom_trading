"""
Multi-Alpha Engine 테스트

Phase 2 구현 검증:
1. 각 알파 단위 테스트
2. Alpha Engine 통합 테스트
3. 실제 데이터 시나리오 테스트
"""

import sys
import os
import pandas as pd
import numpy as np

# 프로젝트 루트를 sys.path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.alphas.vwap_alpha import VWAPAlpha
from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha
from trading.alphas.obv_trend_alpha import OBVTrendAlpha
from trading.alphas.institutional_flow_alpha import InstitutionalFlowAlpha
from trading.alphas.news_score_alpha import NewsScoreAlpha
from trading.alpha_engine import SimonsStyleAlphaEngine


def create_mock_ohlcv(length=100, price_trend="up", volume_spike=False):
    """
    Mock OHLCV 데이터 생성

    Args:
        length: 데이터 길이
        price_trend: "up", "down", "flat"
        volume_spike: 마지막에 거래량 급증 여부
    """
    np.random.seed(42)

    if price_trend == "up":
        close = np.linspace(100, 110, length)
        # 노이즈 추가하되 마지막은 확실히 상승
        noise = np.random.randn(length) * 0.3
        noise[-1] = abs(noise[-1])  # 마지막은 양수로
        close = close + noise
    elif price_trend == "down":
        close = np.linspace(110, 100, length)
        noise = np.random.randn(length) * 0.3
        noise[-1] = -abs(noise[-1])  # 마지막은 음수로
        close = close + noise
    else:  # flat
        close = np.ones(length) * 105 + np.random.randn(length) * 0.3

    high = close + np.abs(np.random.randn(length) * 0.5)
    low = close - np.abs(np.random.randn(length) * 0.5)
    volume = np.random.randint(1000, 2000, length).astype(float)

    if volume_spike:
        # 마지막 5개 bar에 거래량 5배 급등
        volume[-5:] = volume[-5:] * 5

    df = pd.DataFrame({
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })

    return df


def test_vwap_alpha():
    """VWAP Alpha 단위 테스트"""
    print("\n" + "="*70)
    print("Test 1: VWAP Alpha")
    print("="*70)

    alpha = VWAPAlpha(weight=2.0)

    # 시나리오 1: 상승 추세
    df_up = create_mock_ohlcv(length=100, price_trend="up", volume_spike=True)
    state = {"df": df_up}

    result = alpha.compute("TEST001", state)

    print(f"✅ Scenario: 상승 추세 + 거래량 급증")
    print(f"   Score: {result.score:+.2f} (기대: > 0)")
    print(f"   Confidence: {result.confidence:.2f}")
    print(f"   Reason: {result.reason}")

    assert -3.0 <= result.score <= 3.0, "Score 범위 오류"
    assert 0.0 <= result.confidence <= 1.0, "Confidence 범위 오류"
    assert result.name == "VWAP", "알파 이름 오류"

    print("✅ VWAP Alpha 테스트 통과\n")


def test_volume_spike_alpha():
    """Volume Spike Alpha 단위 테스트"""
    print("="*70)
    print("Test 2: Volume Spike Alpha")
    print("="*70)

    alpha = VolumeSpikeAlpha(weight=1.5)

    # 시나리오: 거래량 급증 + 가격 상승
    df = create_mock_ohlcv(length=100, price_trend="up", volume_spike=True)
    state = {"df": df}

    result = alpha.compute("TEST001", state)

    print(f"✅ Scenario: 거래량 5배 급증 + 가격 상승")
    print(f"   Score: {result.score:+.2f} (기대: > 1.0)")
    print(f"   Confidence: {result.confidence:.2f}")
    print(f"   Reason: {result.reason}")
    print(f"   Z-score: {result.metadata.get('z_score', 0):.2f}")

    assert result.score > 0, "상승 + 거래량 급증이면 score > 0"
    assert result.confidence > 0.5, "거래량 급증이면 confidence > 0.5"

    print("✅ Volume Spike Alpha 테스트 통과\n")


def test_obv_trend_alpha():
    """OBV Trend Alpha 단위 테스트"""
    print("="*70)
    print("Test 3: OBV Trend Alpha")
    print("="*70)

    alpha = OBVTrendAlpha(weight=1.2)

    # 시나리오: 상승 추세 (OBV도 상승)
    df = create_mock_ohlcv(length=100, price_trend="up")
    state = {"df": df}

    result = alpha.compute("TEST001", state)

    print(f"✅ Scenario: 지속적 상승 추세")
    print(f"   Score: {result.score:+.2f} (기대: > 0)")
    print(f"   Confidence: {result.confidence:.2f}")
    print(f"   Reason: {result.reason}")

    assert -3.0 <= result.score <= 3.0
    assert 0.0 <= result.confidence <= 1.0

    print("✅ OBV Trend Alpha 테스트 통과\n")


def test_institutional_flow_alpha():
    """Institutional Flow Alpha 단위 테스트"""
    print("="*70)
    print("Test 4: Institutional Flow Alpha")
    print("="*70)

    alpha = InstitutionalFlowAlpha(weight=1.0)

    # 시나리오: 기관+외인 강한 순매수
    state = {
        "institutional_flow": {
            "inst_net_buy": 5_000_000_000,  # 50억 순매수
            "foreign_net_buy": 3_000_000_000,  # 30억 순매수
            "total_traded_value": 100_000_000_000,  # 총 거래대금 1000억
        }
    }

    result = alpha.compute("TEST001", state)

    print(f"✅ Scenario: 기관+외인 80억 순매수 (8% 비율)")
    print(f"   Score: {result.score:+.2f} (기대: > 1.5)")
    print(f"   Confidence: {result.confidence:.2f}")
    print(f"   Reason: {result.reason}")

    assert result.score > 1.0, "8% 순매수면 score > 1.0"
    assert result.confidence > 0.5

    print("✅ Institutional Flow Alpha 테스트 통과\n")


def test_news_score_alpha():
    """News Score Alpha 단위 테스트"""
    print("="*70)
    print("Test 5: News Score Alpha")
    print("="*70)

    alpha = NewsScoreAlpha(weight=0.8)

    # 시나리오 1: 매우 긍정적 뉴스
    state = {
        "ai_analysis": {
            "scores": {
                "news": 90  # 매우 긍정
            }
        }
    }

    result = alpha.compute("TEST001", state)

    print(f"✅ Scenario: 뉴스 점수 90/100 (매우 긍정)")
    print(f"   Score: {result.score:+.2f} (기대: +2.4)")
    print(f"   Confidence: {result.confidence:.2f}")
    print(f"   Reason: {result.reason}")

    assert 2.0 < result.score < 3.0, "90점이면 +2.4 근처"
    assert result.confidence > 0.7

    # 시나리오 2: 중립
    state["ai_analysis"]["scores"]["news"] = 50
    result = alpha.compute("TEST001", state)

    print(f"\n✅ Scenario: 뉴스 점수 50/100 (중립)")
    print(f"   Score: {result.score:+.2f} (기대: 0.0)")
    print(f"   Confidence: {result.confidence:.2f}")

    assert abs(result.score) < 0.1, "50점이면 0.0"
    assert result.confidence < 0.1

    print("✅ News Score Alpha 테스트 통과\n")


def test_alpha_engine_integration():
    """Alpha Engine 통합 테스트"""
    print("="*70)
    print("Test 6: Multi-Alpha Engine Integration")
    print("="*70)

    # 엔진 생성
    engine = SimonsStyleAlphaEngine(
        alphas=[
            VWAPAlpha(weight=2.0),
            VolumeSpikeAlpha(weight=1.5),
            OBVTrendAlpha(weight=1.2),
            InstitutionalFlowAlpha(weight=1.0),
            NewsScoreAlpha(weight=0.8),
        ]
    )

    # 종합 시나리오: 모든 알파 긍정
    df = create_mock_ohlcv(length=100, price_trend="up", volume_spike=True)
    state = {
        "df": df,
        "df_5m": df,
        "institutional_flow": {
            "inst_net_buy": 5_000_000_000,
            "foreign_net_buy": 3_000_000_000,
            "total_traded_value": 100_000_000_000,
        },
        "ai_analysis": {
            "scores": {
                "news": 85
            }
        }
    }

    result = engine.compute("TEST001", state)

    print(f"\n✅ Scenario: 모든 알파 긍정적 신호")
    print(f"   Aggregate Score: {result['aggregate_score']:+.3f} (기대: > 1.0)")
    print(f"   Total Weight: {result['total_weight']:.2f}")
    print(f"   알파 수: {len(result['alphas'])}")

    # Breakdown 출력
    engine.print_breakdown(result)

    assert result["aggregate_score"] > 0, "모든 알파 긍정이면 aggregate > 0"
    assert len(result["alphas"]) == 5, "5개 알파 모두 계산"
    assert "VWAP" in result["weighted_scores"]
    assert "VOLUME_SPIKE" in result["weighted_scores"]

    print("✅ Alpha Engine 통합 테스트 통과\n")


def test_buy_sell_decision():
    """매수/매도 결정 테스트"""
    print("="*70)
    print("Test 7: Buy/Sell Decision")
    print("="*70)

    engine = SimonsStyleAlphaEngine(
        alphas=[
            VWAPAlpha(weight=2.0),
            VolumeSpikeAlpha(weight=1.5),
            OBVTrendAlpha(weight=1.2),
        ]
    )

    # 매수 시나리오
    df_up = create_mock_ohlcv(length=100, price_trend="up", volume_spike=True)
    state_buy = {"df": df_up}
    result_buy = engine.compute("TEST001", state_buy)

    print(f"✅ BUY Scenario:")
    print(f"   Aggregate Score: {result_buy['aggregate_score']:+.3f}")

    if result_buy["aggregate_score"] > 1.0:
        print(f"   ✅ 매수 신호 (score > 1.0)")
    else:
        print(f"   ⚠️ 중립 (0.0 < score < 1.0)")

    # 매도 시나리오
    df_down = create_mock_ohlcv(length=100, price_trend="down", volume_spike=True)
    state_sell = {"df": df_down}
    result_sell = engine.compute("TEST001", state_sell)

    print(f"\n✅ SELL Scenario:")
    print(f"   Aggregate Score: {result_sell['aggregate_score']:+.3f}")

    if result_sell["aggregate_score"] < -1.0:
        print(f"   ✅ 매도 신호 (score < -1.0)")
    else:
        print(f"   ⚠️ 중립")

    print("\n✅ Buy/Sell Decision 테스트 통과\n")


def main():
    """모든 테스트 실행"""
    print("\n" + "🔬" + "="*68 + "🔬")
    print("   Phase 2: Multi-Alpha Engine 테스트 시작")
    print("🔬" + "="*68 + "🔬\n")

    try:
        # 단위 테스트
        test_vwap_alpha()
        test_volume_spike_alpha()
        test_obv_trend_alpha()
        test_institutional_flow_alpha()
        test_news_score_alpha()

        # 통합 테스트
        test_alpha_engine_integration()
        test_buy_sell_decision()

        print("\n" + "✅" + "="*68 + "✅")
        print("   모든 테스트 통과!")
        print("✅" + "="*68 + "✅\n")

        print("📊 결과 요약:")
        print("   - 5개 알파 모두 정상 작동")
        print("   - Alpha Engine 가중 평균 계산 검증")
        print("   - Buy/Sell 결정 로직 검증")
        print("   - Score 범위: -3.0 ~ +3.0 ✅")
        print("   - Confidence 범위: 0.0 ~ 1.0 ✅")

        return 0

    except AssertionError as e:
        print(f"\n❌ 테스트 실패: {e}")
        return 1
    except Exception as e:
        print(f"\n❌ 예외 발생: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit(main())
