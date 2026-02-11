"""
Trade Intent Classification System
===================================
시그널을 단타/중기로 분류하고 적절한 계좌로 라우팅하는 시스템

핵심 개념:
- 하나의 파이프라인에서 발생한 시그널을 Intent로 분류
- Intent에 따라 다른 계좌 + 다른 Exit Engine 사용
- 단타: 기존 분봉 기반 Exit Logic
- 중기: Daily Squeeze 기반 Exit Logic
"""

import os
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict, Any, List
from dotenv import load_dotenv
from pathlib import Path

# 환경변수 로드
env_path = Path(__file__).parent.parent / '.env'
load_dotenv(dotenv_path=env_path)


class TradeIntent(Enum):
    """매매 의도 분류"""
    SCALP = "scalp"                    # 초단타 (분~수십분)
    INTRADAY = "intraday"              # 당일 (수시간)
    SWING = "swing"                    # 스윙 (수일)
    SQUEEZE_TREND = "squeeze_trend"    # 스퀴즈 추세 (수일~수주)


class NewsPersistence(Enum):
    """뉴스 지속성 분류"""
    FLASH = "flash"          # 단발성 (수주 공시, 단기 이슈)
    NARRATIVE = "narrative"  # 연속성 (테마, 스토리)


@dataclass
class TimeframeContext:
    """다중 타임프레임 컨텍스트"""
    # 저타임프레임 (분봉)
    ltf_trend: str = "neutral"       # "bullish", "bearish", "neutral"
    ltf_momentum: float = 0.0
    ltf_vwap_distance: float = 0.0   # VWAP 대비 거리 (%)

    # 고타임프레임 (일봉)
    htf_trend: str = "neutral"
    htf_structure: str = "intact"    # "intact", "weakening", "broken"
    htf_ma_alignment: bool = False   # MA 정배열 여부


@dataclass
class SqueezeIndicators:
    """Daily Squeeze Momentum Pro 지표"""
    squeeze_on: bool = False         # 스퀴즈 상태 (ON/OFF)
    momentum: float = 0.0            # 모멘텀 값
    momentum_slope: float = 0.0      # 모멘텀 기울기 (전일 대비)
    momentum_slope_2d: float = 0.0   # 2일 평균 기울기
    bars_since_squeeze: int = 0      # 스퀴즈 후 경과 봉 수


@dataclass
class TechnicalIndicators:
    """기술적 지표"""
    squeeze: SqueezeIndicators = field(default_factory=SqueezeIndicators)
    atr_daily: float = 0.0
    atr_intraday: float = 0.0
    rsi_daily: float = 50.0
    volume_ratio: float = 1.0        # 평균 대비 거래량 비율


@dataclass
class NewsScore:
    """뉴스 분석 점수"""
    sentiment: float = 0.0           # -1.0 ~ 1.0
    persistence: NewsPersistence = NewsPersistence.FLASH
    keywords: List[str] = field(default_factory=list)
    impact_score: float = 0.0        # 0.0 ~ 1.0


@dataclass
class FlowScore:
    """수급 분석 점수"""
    institution_net: float = 0.0     # 기관 순매수
    foreign_net: float = 0.0         # 외국인 순매수
    program_net: float = 0.0         # 프로그램 순매수
    score: float = 0.0               # 종합 수급 점수


@dataclass
class TradeSignal:
    """
    표준화된 매매 시그널 구조

    파이프라인의 모든 분석 결과를 담는 컨테이너
    """
    # 기본 정보
    symbol: str
    stock_name: str
    timestamp: datetime
    price: float

    # 컨텍스트
    timeframe_context: TimeframeContext = field(default_factory=TimeframeContext)

    # 지표
    indicators: TechnicalIndicators = field(default_factory=TechnicalIndicators)

    # 분석 점수
    news_score: NewsScore = field(default_factory=NewsScore)
    flow_score: FlowScore = field(default_factory=FlowScore)

    # 시그널 메타데이터
    signal_source: str = ""          # 시그널 발생 소스
    signal_strength: float = 0.0     # 시그널 강도 (0.0 ~ 1.0)
    condition_name: str = ""         # 조건검색식 이름

    # Intent (분류 후 설정)
    intent: Optional[TradeIntent] = None
    intent_confidence: float = 0.0
    intent_reason: str = ""


class TradeIntentClassifier:
    """
    매매 의도 분류기

    시그널을 분석하여 적절한 매매 의도(Intent)를 결정

    분류 규칙 (v1 - 룰 기반):
    - squeeze_trend: Daily Squeeze ON + Momentum > 0 + 기울기 >= 0 + Narrative 뉴스
    - swing: HTF 구조 intact + 수급 양호
    - intraday: LTF 모멘텀 양호 + VWAP 위
    - scalp: 기본값 (위 조건 미충족)
    """

    def __init__(self):
        self.classification_log: List[Dict[str, Any]] = []

    def classify(self, signal: TradeSignal) -> TradeSignal:
        """
        시그널을 분류하고 Intent를 설정

        Returns:
            Intent가 설정된 TradeSignal
        """
        intent, confidence, reason = self._determine_intent(signal)

        signal.intent = intent
        signal.intent_confidence = confidence
        signal.intent_reason = reason

        # 분류 로그 기록
        self._log_classification(signal)

        return signal

    def _determine_intent(self, signal: TradeSignal) -> tuple:
        """Intent 결정 로직"""
        squeeze = signal.indicators.squeeze
        tf_ctx = signal.timeframe_context
        news = signal.news_score
        flow = signal.flow_score

        # 1. Squeeze Trend 조건 체크 (가장 높은 우선순위)
        if self._is_squeeze_trend(squeeze, news, tf_ctx):
            confidence = self._calc_squeeze_confidence(squeeze, news)
            reason = self._build_squeeze_reason(squeeze, news)
            return TradeIntent.SQUEEZE_TREND, confidence, reason

        # 2. Swing 조건 체크
        if self._is_swing(tf_ctx, flow, squeeze):
            confidence = 0.7
            reason = f"HTF구조:{tf_ctx.htf_structure}, 수급:{flow.score:.2f}"
            return TradeIntent.SWING, confidence, reason

        # 3. Intraday 조건 체크
        if self._is_intraday(tf_ctx, signal.indicators):
            confidence = 0.6
            reason = f"LTF모멘텀:{tf_ctx.ltf_momentum:.2f}, VWAP거리:{tf_ctx.ltf_vwap_distance:.2f}%"
            return TradeIntent.INTRADAY, confidence, reason

        # 4. 기본값: Scalp
        return TradeIntent.SCALP, 0.5, "기본분류(조건미충족)"

    def _is_squeeze_trend(self, squeeze: SqueezeIndicators,
                          news: NewsScore, tf_ctx: TimeframeContext) -> bool:
        """Squeeze Trend 조건 충족 여부"""
        # 핵심 조건: Squeeze ON + Momentum 양수 + 기울기 양수
        core_condition = (
            squeeze.squeeze_on and
            squeeze.momentum > 0 and
            squeeze.momentum_slope >= 0
        )

        # 보조 조건: Narrative 뉴스 또는 HTF 구조 intact
        support_condition = (
            news.persistence == NewsPersistence.NARRATIVE or
            tf_ctx.htf_structure == "intact"
        )

        return core_condition and support_condition

    def _is_swing(self, tf_ctx: TimeframeContext,
                  flow: FlowScore, squeeze: SqueezeIndicators) -> bool:
        """Swing 조건 충족 여부"""
        return (
            tf_ctx.htf_structure == "intact" and
            flow.score > 0.3 and
            squeeze.momentum > 0  # 모멘텀은 양수여야 함
        )

    def _is_intraday(self, tf_ctx: TimeframeContext,
                     indicators: TechnicalIndicators) -> bool:
        """Intraday 조건 충족 여부"""
        return (
            tf_ctx.ltf_momentum > 0 and
            tf_ctx.ltf_vwap_distance > 0  # VWAP 위
        )

    def _calc_squeeze_confidence(self, squeeze: SqueezeIndicators,
                                  news: NewsScore) -> float:
        """Squeeze Trend 신뢰도 계산"""
        confidence = 0.7  # 기본값

        # 모멘텀 강도에 따른 가산
        if squeeze.momentum > 0.5:
            confidence += 0.1

        # 기울기에 따른 가산
        if squeeze.momentum_slope > 0:
            confidence += 0.1

        # Narrative 뉴스에 따른 가산
        if news.persistence == NewsPersistence.NARRATIVE:
            confidence += 0.1

        return min(confidence, 1.0)

    def _build_squeeze_reason(self, squeeze: SqueezeIndicators,
                               news: NewsScore) -> str:
        """Squeeze Trend 분류 사유 생성"""
        parts = [
            f"Squeeze:{'ON' if squeeze.squeeze_on else 'OFF'}",
            f"Mom:{squeeze.momentum:.2f}",
            f"Slope:{squeeze.momentum_slope:.3f}",
            f"News:{news.persistence.value}"
        ]
        return ", ".join(parts)

    def _log_classification(self, signal: TradeSignal):
        """분류 결과 로깅"""
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "symbol": signal.symbol,
            "stock_name": signal.stock_name,
            "intent": signal.intent.value if signal.intent else "unknown",
            "confidence": signal.intent_confidence,
            "reason": signal.intent_reason
        }
        self.classification_log.append(log_entry)

        # 최근 100개만 유지
        if len(self.classification_log) > 100:
            self.classification_log = self.classification_log[-100:]

    def get_recent_logs(self, n: int = 10) -> List[Dict[str, Any]]:
        """최근 분류 로그 조회"""
        return self.classification_log[-n:]


class OrderRouter:
    """
    주문 라우터

    Intent에 따라 적절한 계좌로 주문을 라우팅
    """

    def __init__(self):
        # 계좌 설정 로드
        self.scalp_account = os.getenv('KIWOOM_SCALP_ACCOUNT', '6259-3479')
        self.trend_account = os.getenv('KIWOOM_TREND_ACCOUNT', '5202-2235')

        # Intent -> 계좌 매핑
        self.account_map = {
            TradeIntent.SCALP: self.scalp_account,
            TradeIntent.INTRADAY: self.scalp_account,
            TradeIntent.SWING: self.trend_account,
            TradeIntent.SQUEEZE_TREND: self.trend_account,
        }

        # Intent -> Exit Engine 매핑
        self.exit_engine_map = {
            TradeIntent.SCALP: "scalp_exit",
            TradeIntent.INTRADAY: "scalp_exit",
            TradeIntent.SWING: "trend_exit",
            TradeIntent.SQUEEZE_TREND: "trend_exit",
        }

        # 라우팅 로그
        self.routing_log: List[Dict[str, Any]] = []

    def route(self, signal: TradeSignal) -> Dict[str, Any]:
        """
        시그널을 적절한 계좌로 라우팅

        Returns:
            라우팅 정보 딕셔너리
        """
        if signal.intent is None:
            raise ValueError("Signal must be classified before routing")

        account = self.account_map.get(signal.intent, self.scalp_account)
        exit_engine = self.exit_engine_map.get(signal.intent, "scalp_exit")

        routing_info = {
            "symbol": signal.symbol,
            "stock_name": signal.stock_name,
            "intent": signal.intent.value,
            "account": account,
            "exit_engine": exit_engine,
            "timestamp": datetime.now().isoformat(),
            "confidence": signal.intent_confidence,
            "reason": signal.intent_reason
        }

        # 로그 기록
        self._log_routing(routing_info)

        return routing_info

    def _log_routing(self, routing_info: Dict[str, Any]):
        """라우팅 로그 기록"""
        self.routing_log.append(routing_info)

        # 최근 100개만 유지
        if len(self.routing_log) > 100:
            self.routing_log = self.routing_log[-100:]

    def get_account_for_intent(self, intent: TradeIntent) -> str:
        """특정 Intent에 대한 계좌 조회"""
        return self.account_map.get(intent, self.scalp_account)

    def get_exit_engine_for_intent(self, intent: TradeIntent) -> str:
        """특정 Intent에 대한 Exit Engine 조회"""
        return self.exit_engine_map.get(intent, "scalp_exit")

    def get_routing_summary(self) -> Dict[str, Any]:
        """라우팅 현황 요약"""
        if not self.routing_log:
            return {"total": 0}

        summary = {
            "total": len(self.routing_log),
            "by_intent": {},
            "by_account": {}
        }

        for log in self.routing_log:
            intent = log["intent"]
            account = log["account"]

            summary["by_intent"][intent] = summary["by_intent"].get(intent, 0) + 1
            summary["by_account"][account] = summary["by_account"].get(account, 0) + 1

        return summary


# =============================================================================
# 헬퍼 함수
# =============================================================================

def create_signal_from_dict(data: Dict[str, Any]) -> TradeSignal:
    """딕셔너리에서 TradeSignal 생성"""
    # 기본 필드
    signal = TradeSignal(
        symbol=data.get("symbol", ""),
        stock_name=data.get("stock_name", ""),
        timestamp=data.get("timestamp", datetime.now()),
        price=data.get("price", 0.0),
        signal_source=data.get("signal_source", ""),
        signal_strength=data.get("signal_strength", 0.0),
        condition_name=data.get("condition_name", "")
    )

    # Squeeze 지표 설정
    if "squeeze" in data:
        sq = data["squeeze"]
        signal.indicators.squeeze = SqueezeIndicators(
            squeeze_on=sq.get("squeeze_on", False),
            momentum=sq.get("momentum", 0.0),
            momentum_slope=sq.get("momentum_slope", 0.0),
            momentum_slope_2d=sq.get("momentum_slope_2d", 0.0),
            bars_since_squeeze=sq.get("bars_since_squeeze", 0)
        )

    # Timeframe Context 설정
    if "timeframe" in data:
        tf = data["timeframe"]
        signal.timeframe_context = TimeframeContext(
            ltf_trend=tf.get("ltf_trend", "neutral"),
            ltf_momentum=tf.get("ltf_momentum", 0.0),
            ltf_vwap_distance=tf.get("ltf_vwap_distance", 0.0),
            htf_trend=tf.get("htf_trend", "neutral"),
            htf_structure=tf.get("htf_structure", "intact"),
            htf_ma_alignment=tf.get("htf_ma_alignment", False)
        )

    # News Score 설정
    if "news" in data:
        ns = data["news"]
        persistence = ns.get("persistence", "flash")
        signal.news_score = NewsScore(
            sentiment=ns.get("sentiment", 0.0),
            persistence=NewsPersistence(persistence) if isinstance(persistence, str) else persistence,
            keywords=ns.get("keywords", []),
            impact_score=ns.get("impact_score", 0.0)
        )

    # Flow Score 설정
    if "flow" in data:
        fl = data["flow"]
        signal.flow_score = FlowScore(
            institution_net=fl.get("institution_net", 0.0),
            foreign_net=fl.get("foreign_net", 0.0),
            program_net=fl.get("program_net", 0.0),
            score=fl.get("score", 0.0)
        )

    return signal


# =============================================================================
# 테스트 코드
# =============================================================================

if __name__ == "__main__":
    print("=" * 60)
    print("Trade Intent Classification System - Test")
    print("=" * 60)

    # 분류기 및 라우터 초기화
    classifier = TradeIntentClassifier()
    router = OrderRouter()

    print(f"\n계좌 설정:")
    print(f"  단타 계좌: {router.scalp_account}")
    print(f"  중기 계좌: {router.trend_account}")

    # 테스트 시그널 1: Squeeze Trend (중기)
    signal1 = TradeSignal(
        symbol="240810",
        stock_name="원익IPS",
        timestamp=datetime.now(),
        price=103500
    )
    signal1.indicators.squeeze = SqueezeIndicators(
        squeeze_on=True,
        momentum=0.8,
        momentum_slope=0.05
    )
    signal1.news_score = NewsScore(
        sentiment=0.7,
        persistence=NewsPersistence.NARRATIVE
    )
    signal1.timeframe_context.htf_structure = "intact"

    # 분류 및 라우팅
    classified1 = classifier.classify(signal1)
    routing1 = router.route(classified1)

    print(f"\n테스트 1: {signal1.stock_name}")
    print(f"  Intent: {classified1.intent.value}")
    print(f"  Confidence: {classified1.intent_confidence:.2f}")
    print(f"  Reason: {classified1.intent_reason}")
    print(f"  Account: {routing1['account']}")
    print(f"  Exit Engine: {routing1['exit_engine']}")

    # 테스트 시그널 2: Scalp (단타)
    signal2 = TradeSignal(
        symbol="060280",
        stock_name="큐렉소",
        timestamp=datetime.now(),
        price=18660
    )
    signal2.indicators.squeeze = SqueezeIndicators(
        squeeze_on=False,
        momentum=-0.2,
        momentum_slope=-0.03
    )

    classified2 = classifier.classify(signal2)
    routing2 = router.route(classified2)

    print(f"\n테스트 2: {signal2.stock_name}")
    print(f"  Intent: {classified2.intent.value}")
    print(f"  Account: {routing2['account']}")
    print(f"  Exit Engine: {routing2['exit_engine']}")

    # 라우팅 요약
    print(f"\n라우팅 요약:")
    summary = router.get_routing_summary()
    print(f"  총 라우팅: {summary['total']}건")
    print(f"  Intent별: {summary['by_intent']}")
    print(f"  계좌별: {summary['by_account']}")

    print("\n" + "=" * 60)
    print("Test Completed Successfully!")
    print("=" * 60)
