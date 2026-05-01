#!/usr/bin/env python3
"""
중기 투자 룰 엔진
================

한투 계좌 보유 종목에 대한 중기 투자 룰 적용
- 국내: BrokerType.KIS_DOMESTIC
- 해외: BrokerType.KIS_OVERSEAS

Actions:
- HOLD: 유지
- TRAILING_STOP: 트레일링 스탑 전환
- TAKE_PROFIT: 수익 실현
- ADD_ON_PULLBACK: 눌림 추가매수
- REDUCE: 비중 축소
- STOP_LOSS: 기계적 정리
"""

import os
import logging
from enum import Enum
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# 프로젝트 루트
project_root = Path(__file__).parent.parent
import sys
sys.path.insert(0, str(project_root))

# 환경변수 로드
load_dotenv(project_root / '.env')

# 브로커 추상화 임포트
from brokers import get_broker, BrokerType, Market

console = Console()
logger = logging.getLogger(__name__)


# ============================================================================
# Action 정의
# ============================================================================

class Action(Enum):
    """중기 투자 행동"""
    HOLD = "HOLD"                      # 유지
    TRAILING_STOP = "TRAILING_STOP"    # 트레일링 스탑 전환
    TAKE_PROFIT = "TAKE_PROFIT"        # 수익 실현
    ADD_ON_PULLBACK = "ADD_ON_PULLBACK"  # 눌림 추가매수
    REDUCE = "REDUCE"                  # 비중 축소
    STOP_LOSS = "STOP_LOSS"            # 기계적 정리


# ============================================================================
# 포지션 그룹 정의
# ============================================================================

class PositionGroup(Enum):
    """포지션 그룹"""
    A_CORE = "A"           # 코어 (장기 우량)
    B_TREND = "B"          # 중기 트렌드
    C_REBALANCE = "C"      # 리밸런싱 대상 (커버드콜 등)
    D_EXIT = "D"           # 정리 후보


# 종목별 그룹 매핑 (국내)
STOCK_GROUP_MAP = {
    # 국내 ETF
    "469070": PositionGroup.A_CORE,      # RISE AI&로봇
    "464310": PositionGroup.B_TREND,     # TIGER 글로벌AI&로보틱스
    "371450": PositionGroup.B_TREND,     # TIGER 글로벌클라우드컴퓨팅
    "491620": PositionGroup.C_REBALANCE, # RISE 미국테크100커버드콜
    "494300": PositionGroup.C_REBALANCE, # KODEX 미국나스닥100커버드콜

    # 해외 종목 (티커)
    "ROBO": PositionGroup.A_CORE,        # ROBO Global Robotics ETF
    "SOXL": PositionGroup.A_CORE,        # Direxion Semi 3x (장기 보유)
    "SOFI": PositionGroup.B_TREND,       # SoFi Technologies
    "WCLD": PositionGroup.B_TREND,       # WisdomTree Cloud Computing
}


# ============================================================================
# 데이터 클래스
# ============================================================================

@dataclass
class Position:
    """포지션 정보"""
    stock_code: str
    stock_name: str
    quantity: int
    avg_price: float
    current_price: float
    profit_pct: float
    eval_amount: float
    group: PositionGroup = PositionGroup.B_TREND
    weight_pct: float = 0.0  # 포트폴리오 내 비중


@dataclass
class MarketData:
    """시장 데이터"""
    weekly_trend_ok: bool = False      # 주봉 추세 양호
    above_ma20_weekly: bool = False    # 주봉 20MA 위
    macd_positive: bool = False        # MACD > 0
    volume_increasing: bool = False    # 거래량 증가
    in_pullback: bool = False          # 눌림 구간
    pullback_pct: float = 0.0          # 고점 대비 하락률


@dataclass
class EvaluationResult:
    """평가 결과"""
    position: Position
    action: Action
    reason: str
    params: Dict = field(default_factory=dict)
    market_data: Optional[MarketData] = None


# ============================================================================
# 룰 파라미터
# ============================================================================

# 수익 구간
TRAILING_TRIGGER_PCT = 25.0    # 트레일링 스탑 전환 기준
TRAILING_STOP_PCT = 10.0       # 트레일링 스탑 폭

# 손실 구간
STOP_LOSS_PCT = -12.0          # 기계적 정리 기준

# 눌림 구간
PULLBACK_MIN = -15.0           # 눌림 하한
PULLBACK_MAX = -7.0            # 눌림 상한

# 비중 관리
MAX_THEME_WEIGHT = 40.0        # 테마별 최대 비중
MAX_COVERED_CALL_WEIGHT = 30.0 # 커버드콜 최대 비중


# ============================================================================
# 시장 데이터 조회
# ============================================================================

def get_market_data(stock_code: str, market: Market = Market.KR) -> MarketData:
    """
    주봉/일봉 기술적 데이터 조회

    Args:
        stock_code: 종목코드 (국내: 6자리, 해외: 티커)
        market: 시장 구분

    Returns:
        MarketData
    """
    try:
        # yfinance 티커 결정
        if market == Market.US:
            # 미국 주식은 티커 그대로
            ticker = stock_code
        else:
            # 한국 주식
            ticker = f"{stock_code}.KS"

        # 주봉 데이터 (6개월)
        weekly = yf.download(ticker, period="6mo", interval="1wk", progress=False)

        if weekly.empty and market == Market.KR:
            # 코스닥 시도
            ticker = f"{stock_code}.KQ"
            weekly = yf.download(ticker, period="6mo", interval="1wk", progress=False)

        if weekly.empty:
            logger.warning(f"주봉 데이터 없음: {stock_code}")
            return MarketData()

        # 일봉 데이터 (3개월)
        daily = yf.download(ticker, period="3mo", interval="1d", progress=False)

        # DataFrame 컬럼 정리 (MultiIndex 처리)
        if isinstance(weekly.columns, pd.MultiIndex):
            weekly.columns = weekly.columns.get_level_values(0)
        if isinstance(daily.columns, pd.MultiIndex):
            daily.columns = daily.columns.get_level_values(0)

        # 지표 계산
        market_data = MarketData()

        # 1. 주봉 20MA 위?
        if len(weekly) >= 20:
            weekly['MA20'] = weekly['Close'].rolling(20).mean()
            current_price = float(weekly['Close'].iloc[-1])
            ma20 = float(weekly['MA20'].iloc[-1])
            market_data.above_ma20_weekly = current_price > ma20

        # 2. MACD > 0?
        if len(weekly) >= 26:
            exp1 = weekly['Close'].ewm(span=12, adjust=False).mean()
            exp2 = weekly['Close'].ewm(span=26, adjust=False).mean()
            macd = exp1 - exp2
            market_data.macd_positive = float(macd.iloc[-1]) > 0

        # 3. 주봉 추세 양호 (MA20 위 + MACD 양수)
        market_data.weekly_trend_ok = (
            market_data.above_ma20_weekly and market_data.macd_positive
        )

        # 4. 거래량 증가? (최근 5일 평균 > 20일 평균)
        if len(daily) >= 20:
            vol_5d = float(daily['Volume'].tail(5).mean())
            vol_20d = float(daily['Volume'].tail(20).mean())
            market_data.volume_increasing = vol_5d > vol_20d * 1.1  # 10% 이상 증가

        # 5. 고점 대비 하락률 (눌림 판단용)
        if len(daily) >= 60:
            high_60d = float(daily['High'].tail(60).max())
            current = float(daily['Close'].iloc[-1])
            market_data.pullback_pct = ((current - high_60d) / high_60d) * 100
            market_data.in_pullback = PULLBACK_MIN <= market_data.pullback_pct <= PULLBACK_MAX

        return market_data

    except Exception as e:
        logger.error(f"시장 데이터 조회 실패 ({stock_code}): {e}")
        return MarketData()


# ============================================================================
# 룰 엔진
# ============================================================================

def evaluate_position(position: Position, market_data: MarketData) -> EvaluationResult:
    """
    단일 포지션 평가

    Args:
        position: 포지션 정보
        market_data: 시장 데이터

    Returns:
        EvaluationResult
    """
    p = position.profit_pct
    group = position.group

    # ─────────────────────────────────────────────────────────
    # 1. 수익 구간: +25% 이상 → 트레일링 스탑
    # ─────────────────────────────────────────────────────────
    if p >= TRAILING_TRIGGER_PCT:
        return EvaluationResult(
            position=position,
            action=Action.TRAILING_STOP,
            reason=f"수익률 {p:.1f}% → 트레일링 스탑 {TRAILING_STOP_PCT}% 적용",
            params={"trailing_pct": TRAILING_STOP_PCT},
            market_data=market_data
        )

    # ─────────────────────────────────────────────────────────
    # 2. 손실 구간: -12% 이하 → 기계적 정리
    # ─────────────────────────────────────────────────────────
    if p <= STOP_LOSS_PCT:
        return EvaluationResult(
            position=position,
            action=Action.STOP_LOSS,
            reason=f"손실률 {p:.1f}% → 손절 기준 {STOP_LOSS_PCT}% 도달",
            params={},
            market_data=market_data
        )

    # ─────────────────────────────────────────────────────────
    # 3. B그룹 (중기 트렌드): 눌림 추가매수
    # ─────────────────────────────────────────────────────────
    if group == PositionGroup.B_TREND:
        if PULLBACK_MIN <= p <= PULLBACK_MAX:
            if market_data.weekly_trend_ok and market_data.volume_increasing:
                return EvaluationResult(
                    position=position,
                    action=Action.ADD_ON_PULLBACK,
                    reason=f"눌림구간 {p:.1f}% + 주봉추세OK + 거래량↑",
                    params={"pullback_pct": p},
                    market_data=market_data
                )
            elif market_data.weekly_trend_ok:
                return EvaluationResult(
                    position=position,
                    action=Action.HOLD,
                    reason=f"눌림구간 {p:.1f}% + 주봉추세OK (거래량 대기)",
                    params={},
                    market_data=market_data
                )

    # ─────────────────────────────────────────────────────────
    # 4. C그룹 (커버드콜): 비중 관리
    # ─────────────────────────────────────────────────────────
    if group == PositionGroup.C_REBALANCE:
        if position.weight_pct > MAX_COVERED_CALL_WEIGHT / 2:  # 개별 15% 초과
            return EvaluationResult(
                position=position,
                action=Action.REDUCE,
                reason=f"비중 {position.weight_pct:.1f}% → 축소 검토",
                params={"target_weight": 10.0},
                market_data=market_data
            )

    # ─────────────────────────────────────────────────────────
    # 5. 기본: HOLD
    # ─────────────────────────────────────────────────────────
    return EvaluationResult(
        position=position,
        action=Action.HOLD,
        reason="유지 (특이사항 없음)",
        params={},
        market_data=market_data
    )


# ============================================================================
# 메인 엔진
# ============================================================================

class MidTermEngine:
    """중기 투자 룰 엔진"""

    def __init__(self, market: Market = Market.KR):
        """
        Args:
            market: 시장 구분 (Market.KR: 국내, Market.US: 해외)
        """
        self.market = market

        # 브로커 추상화 사용
        if market == Market.KR:
            self.broker = get_broker(BrokerType.KIS_DOMESTIC)
        else:
            self.broker = get_broker(BrokerType.KIS_OVERSEAS)

        self.positions: List[Position] = []
        self.results: List[EvaluationResult] = []
        self.prev_actions: Dict[str, Action] = {}

    def initialize(self) -> bool:
        """브로커 초기화"""
        return self.broker.initialize()

    def fetch_positions(self) -> List[Position]:
        """포지션 조회 (브로커 추상화 사용)"""
        broker_positions = self.broker.get_positions()

        if not broker_positions:
            console.print(f"[yellow]보유 종목 없음[/yellow]")
            return []

        total_eval = sum(pos.eval_amount for pos in broker_positions)

        self.positions = []
        for bp in broker_positions:
            weight = (bp.eval_amount / total_eval * 100) if total_eval > 0 else 0

            pos = Position(
                stock_code=bp.symbol,
                stock_name=bp.name,
                quantity=bp.quantity,
                avg_price=bp.avg_price,
                current_price=bp.current_price,
                profit_pct=bp.profit_pct,
                eval_amount=bp.eval_amount,
                group=STOCK_GROUP_MAP.get(bp.symbol, PositionGroup.B_TREND),
                weight_pct=weight
            )
            self.positions.append(pos)

        return self.positions

    def evaluate_all(self) -> List[EvaluationResult]:
        """전체 포지션 평가"""
        self.results = []

        for pos in self.positions:
            console.print(f"[dim]분석 중: {pos.stock_name}...[/dim]")

            # 시장 데이터 조회 (시장 구분 전달)
            market_data = get_market_data(pos.stock_code, self.market)

            # 룰 평가
            result = evaluate_position(pos, market_data)
            self.results.append(result)

        return self.results

    def get_alerts(self) -> List[EvaluationResult]:
        """액션 변경된 종목만 반환 (알림용)"""
        alerts = []
        for r in self.results:
            prev = self.prev_actions.get(r.position.stock_code)
            if prev != r.action:
                alerts.append(r)
                self.prev_actions[r.position.stock_code] = r.action
        return alerts

    def display_results(self):
        """결과 테이블 표시"""
        market_label = "해외 (USD)" if self.market == Market.US else "국내"
        table = Table(title=f"📊 중기 투자 포지션 평가 [{market_label}]")

        is_us = self.market == Market.US
        table.add_column("그룹", style="dim", width=3)
        table.add_column("종목명", style="cyan", width=20)
        table.add_column("현재가", justify="right", width=10)
        table.add_column("수익률", justify="right", width=8)
        table.add_column("비중", justify="right", width=6)
        table.add_column("Action", width=15)
        table.add_column("사유", width=35)

        for r in self.results:
            pos = r.position

            profit_style = "green" if pos.profit_pct >= 0 else "red"
            action_styles = {
                Action.HOLD: "white",
                Action.TRAILING_STOP: "green",
                Action.TAKE_PROFIT: "green",
                Action.ADD_ON_PULLBACK: "cyan",
                Action.REDUCE: "yellow",
                Action.STOP_LOSS: "red bold",
            }
            action_style = action_styles.get(r.action, "white")
            price_str = f"${pos.current_price:.2f}" if is_us else f"{pos.current_price:,.0f}원"

            table.add_row(
                pos.group.value,
                pos.stock_name[:18],
                price_str,
                f"[{profit_style}]{pos.profit_pct:+.1f}%[/{profit_style}]",
                f"{pos.weight_pct:.1f}%",
                f"[{action_style}]{r.action.value}[/{action_style}]",
                r.reason[:33]
            )

        console.print(table)

    def display_market_analysis(self):
        """시장 분석 상세 표시"""
        console.print("\n[bold]📈 시장 데이터 분석[/bold]")

        for r in self.results:
            if r.market_data:
                md = r.market_data
                pos = r.position

                indicators = []
                indicators.append(f"주봉MA20: {'✅' if md.above_ma20_weekly else '❌'}")
                indicators.append(f"MACD: {'✅' if md.macd_positive else '❌'}")
                indicators.append(f"거래량↑: {'✅' if md.volume_increasing else '❌'}")
                indicators.append(f"고점대비: {md.pullback_pct:.1f}%")

                console.print(f"  {pos.stock_name}: {' | '.join(indicators)}")

    def generate_scenario(self) -> str:
        """종목별 시나리오 자동 생성"""
        market_label = "해외" if self.market == Market.US else "국내"
        lines = [
            "=" * 60,
            f"📋 중기 투자 시나리오 ({market_label})",
            f"생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            "=" * 60,
            ""
        ]

        currency = "USD" if self.market == Market.US else "원"

        for r in self.results:
            pos = r.position
            md = r.market_data

            lines.append(f"▶ {pos.stock_name} ({pos.stock_code})")
            lines.append(f"  그룹: {pos.group.value} | 수익률: {pos.profit_pct:+.1f}% | 비중: {pos.weight_pct:.1f}%")
            lines.append(f"  현재 Action: {r.action.value}")
            lines.append(f"  사유: {r.reason}")

            # 시나리오 분기
            if r.action == Action.TRAILING_STOP:
                trail_price = pos.current_price * (1 - TRAILING_STOP_PCT / 100)
                price_fmt = f"{trail_price:.2f} {currency}" if self.market == Market.US else f"{trail_price:,.0f}{currency}"
                lines.append(f"  → 트레일링 스탑가: {price_fmt}")
                lines.append(f"  → 이탈 시 전량 매도")

            elif r.action == Action.STOP_LOSS:
                lines.append(f"  → ⚠️ 즉시 정리 검토")
                lines.append(f"  → 손절가 도달, 감정 개입 금지")

            elif r.action == Action.ADD_ON_PULLBACK:
                lines.append(f"  → 추가 매수 검토 가능")
                lines.append(f"  → 단, 총 비중 {MAX_THEME_WEIGHT}% 이내 유지")

            elif r.action == Action.REDUCE:
                lines.append(f"  → 비중 축소 검토")
                lines.append(f"  → 목표 비중: 10% 내외")

            else:  # HOLD
                lines.append(f"  → 현 상태 유지")
                if md and not md.weekly_trend_ok:
                    lines.append(f"  → 주봉 추세 악화 시 재평가")

            lines.append("")

        return "\n".join(lines)


# ============================================================================
# 실행
# ============================================================================

def main(market: Market = Market.KR):
    """
    중기 투자 룰 엔진 실행

    Args:
        market: 시장 구분 (Market.KR: 국내, Market.US: 해외)
    """
    market_name = "국내" if market == Market.KR else "해외"

    console.print()
    console.print(Panel(
        f"[bold]중기 투자 룰 엔진[/bold]\n\n"
        f"한투 {market_name} 계좌 보유 종목 평가",
        title=f"📊 Mid-Term Engine ({market_name})",
        border_style="blue"
    ))

    engine = MidTermEngine(market=market)

    if not engine.initialize():
        console.print("[red]❌ 브로커 초기화 실패[/red]")
        return

    # 포지션 조회
    console.print(f"\n[cyan]포지션 조회 중 ({market_name})...[/cyan]")
    positions = engine.fetch_positions()

    if not positions:
        console.print("[yellow]보유 종목 없음[/yellow]")
        return

    console.print(f"[green]✅ {len(positions)}개 종목 조회 완료[/green]\n")

    # 전체 평가
    console.print("[cyan]룰 평가 중...[/cyan]\n")
    engine.evaluate_all()

    # 결과 표시
    engine.display_results()
    engine.display_market_analysis()

    # 시나리오 생성
    scenario = engine.generate_scenario()
    console.print("\n" + scenario)

    # 파일 저장
    market_suffix = "overseas" if market == Market.US else "domestic"
    scenario_file = project_root / 'logs' / f"mid_term_scenario_{market_suffix}_{datetime.now().strftime('%Y%m%d')}.txt"
    with open(scenario_file, 'w', encoding='utf-8') as f:
        f.write(scenario)
    console.print(f"\n[green]📁 시나리오 저장: {scenario_file}[/green]")

    return engine


def main_all():
    """국내 + 해외 모두 실행"""
    console.print(Panel(
        "[bold]중기 투자 전체 점검[/bold]\n\n"
        "국내 + 해외 모든 포지션 평가",
        title="📊 Mid-Term Full Check",
        border_style="blue"
    ))

    # 국내
    main(Market.KR)

    # 해외
    console.print("\n")
    main(Market.US)


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "--overseas":
        main(Market.US)
    elif len(sys.argv) > 1 and sys.argv[1] == "--all":
        main_all()
    else:
        main(Market.KR)
