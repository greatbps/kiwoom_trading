"""
Multi-Alpha Engine 데모

Phase 2 구현을 시연하는 간단한 스크립트
"""

import sys
import pandas as pd
import numpy as np

from trading.alpha_engine import SimonsStyleAlphaEngine
from trading.alphas.vwap_alpha import VWAPAlpha
from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha
from trading.alphas.obv_trend_alpha import OBVTrendAlpha
from trading.alphas.institutional_flow_alpha import InstitutionalFlowAlpha
from trading.alphas.news_score_alpha import NewsScoreAlpha


def create_sample_data():
    """샘플 OHLCV 데이터 생성"""
    np.random.seed(123)

    # 100개 bar의 상승 추세
    length = 100
    close = np.linspace(50000, 55000, length)
    close = close + np.random.randn(length) * 200

    high = close + np.abs(np.random.randn(length) * 100)
    low = close - np.abs(np.random.randn(length) * 100)
    volume = np.random.randint(100000, 200000, length).astype(float)

    # 마지막 5개 bar에 거래량 급증
    volume[-5:] = volume[-5:] * 4

    df = pd.DataFrame({
        "open": close,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    })

    return df


def demo():
    """Alpha Engine 데모 실행"""
    print("\n" + "="*70)
    print("🚀 Multi-Alpha Engine 데모")
    print("="*70 + "\n")

    # 1. Alpha Engine 초기화
    print("📦 Alpha Engine 초기화 중...")
    engine = SimonsStyleAlphaEngine(
        alphas=[
            VWAPAlpha(weight=2.0),
            VolumeSpikeAlpha(weight=1.5),
            OBVTrendAlpha(weight=1.2),
            InstitutionalFlowAlpha(weight=1.0),
            NewsScoreAlpha(weight=0.8),
        ]
    )
    print(f"✅ {len(engine.alphas)}개 알파 로드 완료\n")

    # 2. 샘플 데이터 생성
    print("📊 샘플 데이터 생성 중...")
    df = create_sample_data()
    print(f"✅ {len(df)}개 봉 데이터 생성 완료")
    print(f"   가격 범위: {df['close'].min():.0f} ~ {df['close'].max():.0f} 원")
    print(f"   평균 거래량: {df['volume'].mean():.0f} 주\n")

    # 3. State 준비
    state = {
        "df": df,
        "df_5m": df,
        "institutional_flow": {
            "inst_net_buy": 8_000_000_000,      # 80억 순매수
            "foreign_net_buy": 2_000_000_000,   # 20억 순매수
            "total_traded_value": 100_000_000_000,  # 총 1000억
        },
        "ai_analysis": {
            "scores": {
                "news": 75  # 긍정적 뉴스
            }
        }
    }

    # 4. 알파 계산 실행
    print("🔬 알파 계산 실행 중...\n")
    symbol = "005930"  # 삼성전자
    result = engine.compute(symbol, state)

    # 5. 결과 출력
    engine.print_breakdown(result)

    # 6. 매수/매도 결정
    aggregate_score = result["aggregate_score"]

    print("\n" + "="*70)
    print("💡 매매 결정")
    print("="*70 + "\n")

    if aggregate_score > 1.0:
        print(f"✅ 매수 신호! (Aggregate Score: {aggregate_score:+.3f})")
        print(f"   → 5개 알파 중 다수가 긍정적 신호")
        print(f"   → 포지션 크기: 80-100% 권장")
    elif aggregate_score < -1.0:
        print(f"❌ 매도 신호! (Aggregate Score: {aggregate_score:+.3f})")
        print(f"   → 5개 알파 중 다수가 부정적 신호")
    else:
        print(f"⚠️ 중립 (Aggregate Score: {aggregate_score:+.3f})")
        print(f"   → 관망 권장")

    print("\n" + "="*70)
    print("✨ 데모 완료!")
    print("="*70 + "\n")

    # 7. 개별 알파 상세 정보
    print("📝 개별 알파 상세 정보:\n")
    for alpha_output in result["alphas"]:
        print(f"[{alpha_output.name}]")
        print(f"  Score: {alpha_output.score:+.2f}")
        print(f"  Confidence: {alpha_output.confidence:.2f}")
        print(f"  Reason: {alpha_output.reason}")
        print()


if __name__ == "__main__":
    demo()
