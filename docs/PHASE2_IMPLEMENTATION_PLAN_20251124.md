# Phase 2: Multi-Alpha Engine 구현 계획

**작성일**: 2025-11-24
**목적**: Simons-style 멀티-알파 엔진 구현 및 SignalOrchestrator 통합
**예상 기간**: 1개월
**예상 비용**: $0 (기존 데이터 재활용)

---

## 📊 1. 개요

### 목표
- VWAP 단일 전략 → 5개 알파 포트폴리오로 확장
- 알파 간 다양화로 승률 및 수익률 개선
- 기존 데이터 100% 재활용 (추가 비용 없음)

### 기대 효과

| 지표 | Phase 1 (현재) | Phase 2 (목표) | 개선율 |
|------|----------------|----------------|--------|
| 승률 | 50%+ | 55-60% | +10-20% |
| 평균 수익률 | +1.0%+ | +1.5-2.0% | +50-100% |
| 최대 손실 | -0.6% | -0.6% | 유지 |
| Sharpe Ratio | 0.8 | 1.2-1.5 | +50-88% |

---

## 🏗️ 2. 시스템 아키텍처

### 2-1. 전체 구조

```
┌─────────────────────────────────────────┐
│       SignalOrchestrator (기존)         │
│  L0 → L1 → L2 → L3 → L4 → L5 → L6      │
│           ↓ (Confidence 0-1)            │
│    base_conf < 0.5 → 진입 차단          │
└──────────────┬──────────────────────────┘
               ↓
┌──────────────────────────────────────────┐
│    SimonsStyleAlphaEngine (신규)         │
│                                          │
│  ┌──────────┐  ┌──────────┐             │
│  │ VWAP     │  │ Volume   │             │
│  │ Alpha    │  │ Spike    │             │
│  │ w=2.0    │  │ w=1.5    │             │
│  └──────────┘  └──────────┘             │
│                                          │
│  ┌──────────┐  ┌──────────┐  ┌────────┐ │
│  │ OBV      │  │ Inst     │  │ News   │ │
│  │ Trend    │  │ Flow     │  │ Score  │ │
│  │ w=1.2    │  │ w=1.0    │  │ w=0.8  │ │
│  └──────────┘  └──────────┘  └────────┘ │
│                                          │
│         ↓ Weighted Aggregate             │
│   aggregate_score (-3 ~ +3)              │
└──────────────┬───────────────────────────┘
               ↓
       ┌──────┴──────┐
       │  > +1.0 → BUY
       │  < -1.0 → SELL
       └─────────────┘
```

### 2-2. AlphaOutput 데이터 구조

```python
@dataclass
class AlphaOutput:
    name: str                # 알파 이름 (예: "VWAP", "VOLUME_SPIKE")
    score: float             # -3.0 ~ +3.0 (방향 및 강도)
    confidence: float        # 0.0 ~ 1.0 (신뢰도)
    reason: str = ""         # 설명 (디버깅용)
    metadata: dict = None    # 추가 정보
```

### 2-3. 최종 신호 생성 로직

```python
def generate_signal(symbol, df, ai_analysis):
    # Step 1: L0-L2 기본 필터 (Pass/Fail)
    if not l0_system_filter(df): return None
    if not l1_regime_filter(df): return None
    if not l2_rs_filter(symbol): return None

    # Step 2: L3-L6 Confidence 필터
    base_conf = calculate_base_confidence()  # Phase 1
    if base_conf < 0.5: return None

    # Step 3: Multi-Alpha Engine 실행
    state = prepare_state(df, ai_analysis)
    alpha_result = alpha_engine.compute(symbol, state)
    aggregate_score = alpha_result["aggregate_score"]

    # Step 4: 매수/매도 결정
    if aggregate_score > 1.0:
        position_mult = 0.6 + (base_conf * 0.4)  # 0.6 ~ 1.0
        return BUY_SIGNAL(
            confidence=base_conf,
            aggregate_score=aggregate_score,
            position_multiplier=position_mult,
            alpha_breakdown=alpha_result["alphas"]
        )
    elif aggregate_score < -1.0:
        return SELL_SIGNAL(aggregate_score, alpha_result["alphas"])
    else:
        return None  # 중립
```

---

## 🧩 3. 알파 상세 설계

### 3-1. BaseAlpha (추상 클래스)

```python
# trading/alphas/base_alpha.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, Any

@dataclass
class AlphaOutput:
    """알파 계산 결과"""
    name: str
    score: float         # -3.0 ~ +3.0
    confidence: float    # 0.0 ~ 1.0
    reason: str = ""
    metadata: dict = None

class BaseAlpha(ABC):
    """모든 알파의 기본 클래스"""

    def __init__(self, name: str, weight: float = 1.0):
        self.name = name
        self.weight = weight

    @abstractmethod
    def compute(self, symbol: str, state: Dict[str, Any]) -> AlphaOutput:
        """
        알파 계산

        Args:
            symbol: 종목코드
            state: {
                "df": OHLCV DataFrame,
                "ai_analysis": AI 종합분석 결과,
                "institutional_flow": 수급 데이터,
                ...
            }

        Returns:
            AlphaOutput(name, score, confidence, reason)
        """
        pass
```

---

### 3-2. VWAP Alpha (가중치: 2.0)

```python
# trading/alphas/vwap_alpha.py

import numpy as np
from .base_alpha import BaseAlpha, AlphaOutput

class VWAPAlpha(BaseAlpha):
    """
    기존 VWAP 전략을 알파로 변환

    Logic:
    - VWAP 돌파 강도: (price - vwap) / vwap
    - EMA 정렬: 5m > 15m > 60m
    - 거래량 증가: Z-score

    Score:
    - +3.0: 강한 돌파 (>1% + EMA 정렬 + 거래량 급증)
    - 0.0: VWAP 근처
    - -3.0: 강한 이탈

    Confidence:
    - 1.0: 모든 조건 만족
    - 0.5: 일부 조건 만족
    - 0.0: 조건 미달
    """

    def __init__(self, weight: float = 2.0):
        super().__init__("VWAP", weight)

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        df = state["df"]

        # 현재가
        current_price = df["close"].iloc[-1]

        # VWAP 계산
        vwap = self._calculate_vwap(df)

        # 1. VWAP 돌파 강도
        vwap_diff = (current_price - vwap) / vwap  # -1.0 ~ +1.0
        vwap_score = np.clip(vwap_diff * 300, -1.5, 1.5)  # 0.5% 돌파 = 1.5점

        # 2. EMA 정렬
        ema_5m = df["close"].ewm(span=5).mean().iloc[-1]
        ema_15m = df["close"].ewm(span=15).mean().iloc[-1]
        ema_60m = df["close"].ewm(span=60).mean().iloc[-1]

        if ema_5m > ema_15m > ema_60m:
            ema_score = 1.0
            ema_conf = 0.4
        elif ema_5m > ema_15m:
            ema_score = 0.5
            ema_conf = 0.2
        else:
            ema_score = 0.0
            ema_conf = 0.0

        # 3. 거래량 증가
        volume_z = self._calculate_volume_z(df)
        if volume_z > 2.0:
            volume_score = min((volume_z - 2.0) / 2.0, 0.5)  # z=4 → 0.5점
            volume_conf = min(volume_z / 4.0, 0.3)
        else:
            volume_score = 0.0
            volume_conf = 0.0

        # 최종 점수
        total_score = vwap_score + ema_score + volume_score
        total_score = np.clip(total_score, -3.0, 3.0)

        # 신뢰도
        vwap_conf = min(abs(vwap_diff) * 200, 0.3)  # 0.5% = 0.3
        confidence = min(vwap_conf + ema_conf + volume_conf, 1.0)

        reason = f"VWAP {vwap_diff:+.2%}, EMA {'정렬' if ema_score > 0 else '역배열'}, Vol Z={volume_z:.1f}"

        return AlphaOutput(
            name="VWAP",
            score=total_score,
            confidence=confidence,
            reason=reason,
            metadata={"vwap": vwap, "price": current_price}
        )

    def _calculate_vwap(self, df):
        """VWAP 계산"""
        typical_price = (df["high"] + df["low"] + df["close"]) / 3
        return (typical_price * df["volume"]).sum() / df["volume"].sum()

    def _calculate_volume_z(self, df):
        """거래량 Z-score 계산"""
        vol = df["volume"]
        mean = vol.rolling(40).mean().iloc[-1]
        std = vol.rolling(40).std().iloc[-1]
        current = vol.iloc[-1]
        return (current - mean) / (std + 1e-9)
```

---

### 3-3. Volume Spike Alpha (가중치: 1.5)

```python
# trading/alphas/volume_spike_alpha.py

import numpy as np
from .base_alpha import BaseAlpha, AlphaOutput

class VolumeSpikeAlpha(BaseAlpha):
    """
    거래량 급등 감지

    Logic:
    - 거래량 Z-score > 2.0 → 급등
    - 방향: 최근 수익률 부호
    - 급등 시 가격 상승 → BUY, 하락 → SELL

    Score:
    - Z > 3.0 && ret > 0 → +3.0 (강한 매수)
    - Z > 2.0 && ret > 0 → +1.5
    - Z < 1.0 → 0.0 (중립)

    Confidence:
    - Z > 3.0 → 1.0
    - Z = 2.0 → 0.67
    - Z < 2.0 → 0.0
    """

    def __init__(self, weight: float = 1.5, lookback: int = 40):
        super().__init__("VOLUME_SPIKE", weight)
        self.lookback = lookback

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        df = state["df"]
        vol = df["volume"]

        # Z-score 계산
        mean = vol.rolling(self.lookback).mean().iloc[-1]
        std = vol.rolling(self.lookback).std().iloc[-1]
        current = vol.iloc[-1]
        z = (current - mean) / (std + 1e-9)

        # 방향: 최근 수익률
        ret = df["close"].pct_change().iloc[-1]
        direction = np.sign(ret)

        # Score: Z > 2면 신뢰도 높음
        if z > 2.0:
            score = direction * min(z / 2.0, 3.0)  # z=6 → ±3.0
            confidence = min((z - 2.0) / 2.0 + 0.5, 1.0)  # z=4 → 1.0
        else:
            score = 0.0
            confidence = 0.0

        reason = f"Vol Z={z:.1f}, Ret={ret:+.2%}"

        return AlphaOutput(
            name="VOLUME_SPIKE",
            score=score,
            confidence=confidence,
            reason=reason,
            metadata={"z_score": z, "return": ret}
        )
```

---

### 3-4. OBV Trend Alpha (가중치: 1.2)

```python
# trading/alphas/obv_trend_alpha.py

import numpy as np
from .base_alpha import BaseAlpha, AlphaOutput

class OBVTrendAlpha(BaseAlpha):
    """
    On-Balance Volume 추세 분석

    Logic:
    - OBV = cumsum(sign(close.diff()) * volume)
    - Fast MA (5) vs Slow MA (20)
    - Fast > Slow → 상승 추세 → BUY

    Score:
    - (Fast - Slow) / Slow > 0.05 → +3.0
    - 0.02 ~ 0.05 → +1.5
    - < 0.01 → 0.0

    Confidence:
    - 차이 클수록 신뢰도 높음
    """

    def __init__(self, weight: float = 1.2, fast: int = 5, slow: int = 20):
        super().__init__("OBV_TREND", weight)
        self.fast = fast
        self.slow = slow

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        df = state["df"]

        # OBV 계산
        direction = np.sign(df["close"].diff())
        obv = (direction * df["volume"]).cumsum()

        # Fast/Slow MA
        obv_fast = obv.rolling(self.fast).mean().iloc[-1]
        obv_slow = obv.rolling(self.slow).mean().iloc[-1]

        diff = obv_fast - obv_slow
        norm = abs(obv_slow) + 1e-9
        ratio = diff / norm

        # Score
        score = np.clip(ratio * 60, -3.0, 3.0)  # 5% 차이 → ±3.0

        # Confidence
        confidence = np.clip(abs(ratio) * 20, 0.0, 1.0)  # 5% 차이 → 1.0

        reason = f"OBV Fast/Slow={ratio:+.2%}"

        return AlphaOutput(
            name="OBV_TREND",
            score=score,
            confidence=confidence,
            reason=reason,
            metadata={"obv_fast": obv_fast, "obv_slow": obv_slow}
        )
```

---

### 3-5. Institutional Flow Alpha (가중치: 1.0)

```python
# trading/alphas/institutional_flow_alpha.py

import numpy as np
from .base_alpha import BaseAlpha, AlphaOutput

class InstitutionalFlowAlpha(BaseAlpha):
    """
    기관/외인 수급 분석

    Logic:
    - 기관 순매수 + 외인 순매수 / 거래대금
    - 비율 > 5% → 강한 수급 → BUY

    Score:
    - 비율 > 10% → +3.0
    - 5% ~ 10% → +1.5
    - < 1% → 0.0

    Confidence:
    - 비율이 클수록 높음
    """

    def __init__(self, weight: float = 1.0):
        super().__init__("INST_FLOW", weight)

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        flow = state.get("institutional_flow", None)

        if flow is None or flow.get("total_traded_value", 0) == 0:
            return AlphaOutput(
                name="INST_FLOW",
                score=0.0,
                confidence=0.0,
                reason="데이터 없음"
            )

        # 기관 + 외인 순매수 비율
        inst_buy = flow.get("inst_net_buy", 0)
        foreign_buy = flow.get("foreign_net_buy", 0)
        total_value = flow["total_traded_value"]

        ratio = (inst_buy + foreign_buy) / total_value

        # Score
        score = np.clip(ratio * 30, -3.0, 3.0)  # 10% 비율 → ±3.0

        # Confidence
        confidence = np.clip(abs(ratio) * 10, 0.0, 1.0)  # 10% → 1.0

        reason = f"기관+외인 비율={ratio:+.1%}"

        return AlphaOutput(
            name="INST_FLOW",
            score=score,
            confidence=confidence,
            reason=reason,
            metadata={"ratio": ratio}
        )
```

---

### 3-6. News Score Alpha (가중치: 0.8)

```python
# trading/alphas/news_score_alpha.py

import numpy as np
from .base_alpha import BaseAlpha, AlphaOutput

class NewsScoreAlpha(BaseAlpha):
    """
    기존 AI 종합분석 뉴스 점수 재활용

    Logic:
    - score_news (0-100) → -3 ~ +3 변환
    - 50 = 중립(0), 100 = +3, 0 = -3

    Score:
    - 100 → +3.0 (강한 긍정)
    - 75 → +1.5
    - 50 → 0.0 (중립)

    Confidence:
    - 극단적일수록 높음
    - 100 or 0 → 1.0
    - 50 → 0.0
    """

    def __init__(self, weight: float = 0.8):
        super().__init__("NEWS", weight)

    def compute(self, symbol: str, state: dict) -> AlphaOutput:
        analysis = state.get("ai_analysis", None)

        if analysis is None:
            return AlphaOutput(
                name="NEWS",
                score=0.0,
                confidence=0.0,
                reason="AI 분석 없음"
            )

        # score_news: 0~100
        news_score = analysis.get("scores", {}).get("news", 50)

        # 0~100 → -3~+3 변환
        score = ((news_score - 50) / 50) * 3.0
        score = np.clip(score, -3.0, 3.0)

        # 극단적일수록 신뢰도 높음
        confidence = abs(score) / 3.0

        reason = f"뉴스 점수={news_score}/100"

        return AlphaOutput(
            name="NEWS",
            score=score,
            confidence=confidence,
            reason=reason,
            metadata={"raw_score": news_score}
        )
```

---

## ⚙️ 4. SimonsStyleAlphaEngine 구현

```python
# trading/alpha_engine.py

from typing import List, Dict, Any
from .alphas.base_alpha import BaseAlpha, AlphaOutput

class SimonsStyleAlphaEngine:
    """
    멀티-알파 엔진

    여러 알파를 결합하여 최종 aggregate score 계산
    """

    def __init__(self, alphas: List[BaseAlpha]):
        self.alphas = alphas

    def compute(self, symbol: str, state: Dict[str, Any]) -> Dict:
        """
        모든 알파 계산 및 가중 평균

        Returns:
            {
                "aggregate_score": float (-3 ~ +3),
                "alphas": [AlphaOutput, ...],
                "weighted_scores": {...}
            }
        """
        alpha_outputs = []

        # 각 알파 계산
        for alpha in self.alphas:
            try:
                output = alpha.compute(symbol, state)
                alpha_outputs.append(output)
            except Exception as e:
                print(f"❌ {alpha.name} 계산 실패: {e}")
                alpha_outputs.append(AlphaOutput(
                    name=alpha.name,
                    score=0.0,
                    confidence=0.0,
                    reason=f"오류: {e}"
                ))

        # 가중 평균 계산
        total_weighted_score = 0.0
        total_weight = 0.0

        weighted_scores = {}

        for alpha, output in zip(self.alphas, alpha_outputs):
            # weight × confidence × score
            weighted = alpha.weight * output.confidence * output.score
            total_weighted_score += weighted
            total_weight += alpha.weight * output.confidence

            weighted_scores[output.name] = {
                "score": output.score,
                "confidence": output.confidence,
                "weight": alpha.weight,
                "weighted_contribution": weighted
            }

        # Aggregate score
        if total_weight > 0:
            aggregate_score = total_weighted_score / total_weight
        else:
            aggregate_score = 0.0

        return {
            "aggregate_score": aggregate_score,
            "alphas": alpha_outputs,
            "weighted_scores": weighted_scores,
            "total_weight": total_weight
        }
```

---

## 🔗 5. SignalOrchestrator 통합

```python
# analyzers/signal_orchestrator.py (수정)

from trading.alpha_engine import SimonsStyleAlphaEngine
from trading.alphas.vwap_alpha import VWAPAlpha
from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha
from trading.alphas.obv_trend_alpha import OBVTrendAlpha
from trading.alphas.institutional_flow_alpha import InstitutionalFlowAlpha
from trading.alphas.news_score_alpha import NewsScoreAlpha

class SignalOrchestrator:
    def __init__(self, kiwoom_api, config):
        # 기존 L0-L6 필터 초기화
        ...

        # Multi-Alpha Engine 초기화
        self.alpha_engine = SimonsStyleAlphaEngine(
            alphas=[
                VWAPAlpha(weight=2.0),
                VolumeSpikeAlpha(weight=1.5, lookback=40),
                OBVTrendAlpha(weight=1.2, fast=5, slow=20),
                InstitutionalFlowAlpha(weight=1.0),
                NewsScoreAlpha(weight=0.8),
            ]
        )

    def generate_signal(self, symbol, stock_name, ai_analysis=None):
        """
        최종 매매 신호 생성
        """
        # Step 1: OHLCV 데이터 수집
        df_1m = self.get_ohlcv(symbol, "1")
        df_5m = self.get_ohlcv(symbol, "5")

        # Step 2: L0-L2 기본 필터
        if not self.system_filter.check(df_1m):
            return None

        if not self.regime_filter.check(df_5m):
            return None

        if not self.rs_ranker.check(symbol):
            return None

        # Step 3: L3-L6 Confidence 필터
        l3_result = self.mtf_consensus.check_with_confidence(symbol, df_1m, df_5m)
        l4_result = self.liquidity_detector.check_with_confidence(symbol)
        l5_result = self.squeeze.check_with_confidence(df_5m)
        l6_result = self.validator.check_with_confidence(symbol, stock_name)

        # Confidence 집계
        from trading.confidence_aggregator import ConfidenceAggregator
        aggregator = ConfidenceAggregator()

        filter_results = [
            ("L3_MTF", l3_result),
            ("L4_LIQUIDITY", l4_result),
            ("L5_SQUEEZE", l5_result),
            ("L6_VALIDATOR", l6_result),
        ]

        base_conf, should_pass, reason = aggregator.aggregate(filter_results)

        if not should_pass:
            print(f"❌ {symbol} Confidence 미달: {base_conf:.2f} < 0.5")
            return None

        # Step 4: Multi-Alpha Engine 실행
        state = {
            "df": df_1m,
            "df_5m": df_5m,
            "ai_analysis": ai_analysis,
            "institutional_flow": self.get_investor_flow(symbol),
        }

        alpha_result = self.alpha_engine.compute(symbol, state)
        aggregate_score = alpha_result["aggregate_score"]

        print(f"\n{'='*60}")
        print(f"📊 {symbol} Multi-Alpha Analysis")
        print(f"{'='*60}")
        print(f"Base Confidence: {base_conf:.2f}")
        print(f"Aggregate Score: {aggregate_score:+.2f}")
        print(f"\nAlpha Breakdown:")
        for alpha_output in alpha_result["alphas"]:
            print(f"  {alpha_output.name:15s}: {alpha_output.score:+.2f} (conf: {alpha_output.confidence:.2f}) - {alpha_output.reason}")
        print(f"{'='*60}\n")

        # Step 5: 매수/매도 결정
        if aggregate_score > 1.0:
            # 포지션 크기 조정
            position_mult = aggregator.calculate_position_multiplier(base_conf)

            return {
                "action": "BUY",
                "symbol": symbol,
                "stock_name": stock_name,
                "confidence": base_conf,
                "aggregate_score": aggregate_score,
                "position_multiplier": position_mult,
                "alpha_breakdown": alpha_result["alphas"],
                "weighted_scores": alpha_result["weighted_scores"]
            }

        elif aggregate_score < -1.0:
            return {
                "action": "SELL",
                "symbol": symbol,
                "stock_name": stock_name,
                "aggregate_score": aggregate_score,
                "alpha_breakdown": alpha_result["alphas"]
            }

        else:
            print(f"⚠️ {symbol} 중립: aggregate_score={aggregate_score:+.2f}")
            return None
```

---

## 🧪 6. 테스트 및 검증 계획

### 6-1. 단위 테스트

```python
# tests/test_alphas.py

import pytest
from trading.alphas.vwap_alpha import VWAPAlpha
from trading.alphas.volume_spike_alpha import VolumeSpikeAlpha

def test_vwap_alpha():
    """VWAP Alpha 단위 테스트"""
    alpha = VWAPAlpha(weight=2.0)

    # Mock 데이터
    state = {
        "df": create_mock_ohlcv(
            close=[100, 101, 102, 103, 104],
            volume=[1000, 1200, 5000, 1100, 1000]
        )
    }

    result = alpha.compute("005930", state)

    assert -3.0 <= result.score <= 3.0
    assert 0.0 <= result.confidence <= 1.0
    assert result.name == "VWAP"

def test_volume_spike_alpha():
    """Volume Spike Alpha 단위 테스트"""
    alpha = VolumeSpikeAlpha(weight=1.5)

    # 거래량 급등 시나리오
    state = {
        "df": create_mock_ohlcv(
            close=[100, 101, 102, 103, 105],  # 상승
            volume=[1000]*40 + [5000]  # 5배 급등
        )
    }

    result = alpha.compute("005930", state)

    assert result.score > 1.0  # 상승 + 거래량 급등
    assert result.confidence > 0.5
```

### 6-2. 통합 테스트

```python
# tests/test_alpha_engine.py

def test_multi_alpha_engine():
    """Multi-Alpha Engine 통합 테스트"""
    engine = SimonsStyleAlphaEngine(
        alphas=[
            VWAPAlpha(weight=2.0),
            VolumeSpikeAlpha(weight=1.5),
            OBVTrendAlpha(weight=1.2),
        ]
    )

    state = create_test_state()
    result = engine.compute("005930", state)

    assert "aggregate_score" in result
    assert len(result["alphas"]) == 3
    assert -3.0 <= result["aggregate_score"] <= 3.0
```

### 6-3. 백테스트 시나리오

**시나리오 1: 메드팩토 6건 재분석**

| 시간 | VWAP | Volume | OBV | Inst | News | Aggregate | 기존 | Phase 2 | 결과 |
|------|------|--------|-----|------|------|-----------|------|---------|------|
| 10:11 | +2.0 | -0.5 | -1.0 | +0.2 | +0.5 | **+0.32** | ✅ 진입 | ❌ 차단 | -1.41% 손실 방지 |
| 10:13 | +1.5 | +0.8 | -1.5 | -0.3 | +0.5 | **+0.18** | ✅ 진입 | ❌ 차단 | -4.53% 손실 방지 |
| 10:16 | +2.5 | +2.0 | +1.0 | +0.8 | +0.5 | **+2.26** | ✅ 진입 | ✅ 진입 | -0.62% 손실 (감수) |

**기대 효과**:
- 6건 → 1건 (5건 차단)
- 손실 -3,910원 → -124원 (-97%)

---

## 📅 7. 구현 일정 (4주)

### Week 1: 기반 구축
- [ ] `trading/alphas/base_alpha.py` 작성
- [ ] `trading/alpha_engine.py` 작성
- [ ] 단위 테스트 작성

### Week 2: 알파 구현 (Part 1)
- [ ] VWAPAlpha 구현
- [ ] VolumeSpikeAlpha 구현
- [ ] 단위 테스트 통과

### Week 3: 알파 구현 (Part 2)
- [ ] OBVTrendAlpha 구현
- [ ] InstitutionalFlowAlpha 구현
- [ ] NewsScoreAlpha 구현
- [ ] 통합 테스트

### Week 4: 통합 및 검증
- [ ] SignalOrchestrator 통합
- [ ] 백테스트 실행 (메드팩토 6건)
- [ ] 소액 실전 테스트 (10만원)
- [ ] Phase 2 완료 보고서 작성

---

## 🎯 8. 성공 기준

### 필수 조건 ✅
1. **모든 알파 정상 작동**: 5개 알파 모두 score + confidence 반환
2. **Aggregate 계산 정확**: 가중 평균 로직 검증
3. **SignalOrchestrator 통합**: 기존 L0-L6 + 신규 Alpha Engine 연동

### 성능 목표 🎯
1. **승률**: 50%+ → 55-60%
2. **평균 수익률**: +1.0%+ → +1.5-2.0%
3. **백테스트 검증**: 메드팩토 6건 중 5건 차단 성공

### 리스크 관리 ⚠️
1. **최대 손실 유지**: -0.6% (Early Failure Cut)
2. **포지션 크기**: 0.6 ~ 1.0 (동적 조정)
3. **Confidence 임계값**: 0.5 이상만 진입

---

## 📝 9. 문서화

### 생성할 문서
1. **ALPHA_ENGINE_ARCHITECTURE.md**: 전체 시스템 아키텍처
2. **ALPHA_IMPLEMENTATION_GUIDE.md**: 새 알파 추가 가이드
3. **PHASE2_FINAL_REPORT.md**: Phase 2 완료 보고서

### 코드 주석
- 각 알파 클래스에 docstring 필수
- compute() 메서드 로직 설명
- 테스트 케이스 주석

---

## 🚀 10. Next Actions

### 즉시 시작 (오늘)
1. `trading/alphas/` 디렉토리 생성
2. `base_alpha.py` 작성
3. `vwap_alpha.py` 구현 시작

### 이번 주 완료
1. VWAPAlpha + VolumeSpikeAlpha 구현
2. 단위 테스트 작성 및 통과
3. `alpha_engine.py` 초안 작성

### 다음 주 목표
1. 나머지 3개 알파 구현
2. SignalOrchestrator 통합
3. 백테스트 실행

---

**작성자**: Claude Code
**검토 필요**: 알파 가중치 조정, 임계값 튜닝
**참고 문서**: `docs/SIMONS_ALPHA_ENGINE_ANALYSIS_20251121.md`
