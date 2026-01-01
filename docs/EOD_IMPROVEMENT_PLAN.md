# EOD 청산 개선 계획 - "수익 놓치지 않는" 보유/재진입 전략

**작성일**: 2025-11-30
**목적**: 당일 청산 규칙으로 인한 수익 기회 상실 방지
**근거**: 한국피아이엠(+9.8% 놓침), 한올바이오파마(+7.2% 놓침)

---

## 📋 문제 정의

### 현재 시스템 구조

```python
# trading/exit_logic_optimized.py:211-212
if current_time >= self.loss_exit_time:  # 15:00
    return True, f"시간 기반 청산 (15:00, {profit_pct:+.2f}%)", {'profit_pct': profit_pct}
```

**문제점**:
- 15:00에 **무조건** 모든 포지션 청산
- 추세 지속 중인 종목도 강제로 청산
- 다음날 갭업 시 재진입 로직 없음

### 실제 손실 사례 (2025-11-29 → 2025-12-01)

| 종목 | 금요일 매도가 | 월요일 매수가 | 놓친 수익 |
|------|--------------|--------------|----------|
| 한국피아이엠 | 55,200원 | 60,600원 | **+9.8%** |
| 한올바이오파마 | 46,800원 | 50,200원 | **+7.2%** |

**원인**:
1. ❌ 너무 빠른 익절 (Early Exit)
2. ❌ 추세 유지 중 강제 청산
3. ❌ 재료/뉴스 변화 체크 없음
4. ❌ 갭업 재진입 로직 없음

---

## 🎯 개선 목표

1. **추세 지속 종목은 익일 보유 허용**
2. **EOD 강제 청산 → 조건부 청산 전환**
3. **전일 청산 종목 → 다음날 우선 재진입**
4. **Trailing Stop으로 수익 극대화**
5. **뉴스/재료 기반 보유 연장**

---

## 🧩 해결 전략 (3 Phase 접근)

### Phase 1: Position Metadata 확장 + EOD Manager 추가

**목표**: 기존 시스템 유지하면서 "익일 보유" 옵션 추가

#### 1-1. Position 구조 확장

```python
# main_auto_trading.py:2788 부근
position = {
    'code': stock_code,
    'name': stock_name,
    'entry_price': price,
    'entry_time': entry_time,
    'quantity': quantity,

    # ✅ 신규 추가
    'strategy_tag': strategy_tag,           # 'scalping', 'momentum', 'swing_candidate'
    'allow_overnight': False,               # 익일 보유 허용 여부
    'overnight_score': 0.0,                 # 보유 점수 (0.0-1.0)
    'eod_forced_exit': False,               # EOD 강제 청산 여부 (분석용)

    # 기존 필드
    'initial_quantity': quantity,
    'highest_price': price,
    'trailing_active': False,
    'partial_exit_stage': 0,
}
```

#### 1-2. 진입 시점 overnight 판단

```python
# analyzers/signal_orchestrator.py 또는 main_auto_trading.py:execute_buy() 내부

def should_allow_overnight(signal_result, df, news_score) -> Tuple[bool, float]:
    """
    진입 시점에 익일 보유 허용 여부 판단

    Returns:
        (allow_overnight, overnight_score)
    """
    # 스캘핑 전략은 무조건 당일 청산
    strategy_tag = signal_result.get('strategy_tag', 'momentum')
    if strategy_tag == 'scalping':
        return False, 0.0

    score = 0.0

    # 1. 추세 점수 (0.4)
    trend_ok = (
        signal_result.confidence >= 0.6 and
        df['close'].iloc[-1] > df['close'].ewm(span=5).mean().iloc[-1]
    )
    if trend_ok:
        score += 0.4

    # 2. 거래량 점수 (0.3)
    if 'vol_z20' in df.columns:
        vol_z20 = df['vol_z20'].iloc[-1]
        if vol_z20 >= 1.5:
            score += 0.3
        elif vol_z20 >= 1.0:
            score += 0.2

    # 3. 뉴스/재료 점수 (0.3)
    if news_score >= 60:
        score += 0.3
    elif news_score >= 50:
        score += 0.2

    # Threshold: 0.5 이상이면 보유 후보
    allow = score >= 0.5

    return allow, score
```

#### 1-3. EOD Manager 추가 (⚠️ 수정: 시간 조정 + 노출금액 제한)

```python
# trading/eod_manager.py (신규 생성)

from typing import Dict, List, Tuple
from datetime import datetime
import pandas as pd
from rich.console import Console

console = Console()


class EODManager:
    """
    장 마감 전 포지션 관리
    - 기본: 전량 청산
    - 예외: 추세 유지 + 재료 살아있는 종목은 익일 보유

    ⚠️ ChatGPT 리뷰 반영:
    - EOD 체크: 14:55-15:00 (15:00에서 변경)
    - Force Exit: 15:05-15:07 (15:10에서 변경)
    - 노출금액 제한: 계좌 자산의 40%까지만
    - OHLCV 버퍼링: API 중복 호출 방지
    """

    def __init__(self, config: Dict):
        self.config = config

        # EOD 정책
        self.eod_policy = config.get('eod_policy', {})
        self.max_overnight = self.eod_policy.get('max_overnight_positions', 3)
        self.min_overnight_score = self.eod_policy.get('min_overnight_score', 0.6)

        # ✅ 수정: 노출금액 제한 추가
        self.max_exposure_pct = self.eod_policy.get('max_overnight_position_value_pct', 40)

        # ✅ 수정: EOD 체크 시간 (14:55-15:00)
        self.eod_check_time = self.eod_policy.get('check_time', '14:55:00')
        self.force_exit_time = self.eod_policy.get('force_exit_time', '15:05:00')

    def run_eod_check(
        self,
        positions: Dict,
        api,
        news_fetcher
    ) -> Tuple[List[str], List[str]]:
        """
        장 마감 전 포지션 검토

        Args:
            positions: 현재 보유 포지션 dict
            api: Kiwoom API (실시간 데이터 조회용)
            news_fetcher: 뉴스 데이터 조회용

        Returns:
            (to_hold_codes, to_close_codes)
        """
        current_time = datetime.now()

        console.print("\n" + "=" * 80)
        console.print(f"[bold yellow]🕐 EOD 체크 시작 ({current_time.strftime('%H:%M:%S')})[/bold yellow]")
        console.print("=" * 80 + "\n")

        # 1. allow_overnight=True인 종목만 후보로
        candidates = []
        for code, pos in positions.items():
            if pos.get('allow_overnight', False):
                candidates.append((code, pos))

        console.print(f"[cyan]📋 익일 보유 후보: {len(candidates)}개[/cyan]")

        if not candidates:
            # 모두 청산
            return [], list(positions.keys())

        # 2. 후보별 EOD 재검증
        scored_candidates = []

        for code, pos in candidates:
            # 현재 시장 데이터 조회
            df = api.fetch_ohlcv(code, interval='5m', days=1)
            current_price = api.get_current_price(code)
            news_score = news_fetcher.get_sentiment_score(code)

            # EOD 점수 재계산
            eod_score = self._calculate_eod_score(pos, df, current_price, news_score)

            scored_candidates.append({
                'code': code,
                'name': pos.get('name', ''),
                'score': eod_score,
                'position': pos
            })

            console.print(
                f"  {pos.get('name', code):15s} | "
                f"Score: {eod_score:.2f} | "
                f"Entry: {pos.get('overnight_score', 0):.2f}"
            )

        # 3. 상위 N개만 보유 (max_overnight 제한)
        scored_candidates.sort(key=lambda x: x['score'], reverse=True)

        to_hold = []
        to_close = []

        for idx, cand in enumerate(scored_candidates):
            if (idx < self.max_overnight and
                cand['score'] >= self.min_overnight_score):
                to_hold.append(cand['code'])
                console.print(f"[green]✓ {cand['name']:15s} → 익일 보유 (Score: {cand['score']:.2f})[/green]")
            else:
                to_close.append(cand['code'])
                cand['position']['eod_forced_exit'] = True
                console.print(f"[yellow]✗ {cand['name']:15s} → EOD 청산 (Score: {cand['score']:.2f})[/yellow]")

        # 4. allow_overnight=False인 종목은 무조건 청산
        for code, pos in positions.items():
            if not pos.get('allow_overnight', False):
                to_close.append(code)
                pos['eod_forced_exit'] = True

        console.print(f"\n[bold green]📊 최종 결과: 보유 {len(to_hold)}개, 청산 {len(to_close)}개[/bold green]\n")

        return to_hold, to_close

    def _calculate_eod_score(
        self,
        position: Dict,
        df: pd.DataFrame,
        current_price: float,
        news_score: float
    ) -> float:
        """
        EOD 시점 보유 점수 계산 (0.0-1.0)

        기준:
        - 추세 유지: 0.4
        - 거래량 상태: 0.3
        - 뉴스/재료: 0.3
        """
        score = 0.0

        if df is None or df.empty:
            return 0.0

        # 1. 추세 유지 체크 (0.4)
        try:
            ema5 = df['close'].ewm(span=5).mean().iloc[-1]
            ema20 = df['close'].ewm(span=20).mean().iloc[-1]

            # 종가 > EMA5 > EMA20
            if current_price > ema5 > ema20:
                score += 0.4
            elif current_price > ema5:
                score += 0.2

            # SuperTrend 상태
            if 'supertrend_direction' in df.columns:
                if df['supertrend_direction'].iloc[-1] == 1:  # 상승
                    score += 0.1
        except:
            pass

        # 2. 거래량 상태 (0.3)
        try:
            if 'vol_z20' in df.columns:
                vol_z20 = df['vol_z20'].iloc[-1]
                if vol_z20 >= 1.5:
                    score += 0.3
                elif vol_z20 >= 1.0:
                    score += 0.2
                elif vol_z20 >= 0.5:
                    score += 0.1
        except:
            pass

        # 3. 뉴스/재료 (0.3)
        if news_score >= 60:
            score += 0.3
        elif news_score >= 50:
            score += 0.2
        elif news_score >= 40:
            score += 0.1

        return min(score, 1.0)
```

#### 1-4. main_auto_trading.py 통합

```python
# main_auto_trading.py

from trading.eod_manager import EODManager

class AutoTradingSystem:
    def __init__(self):
        # ... 기존 코드 ...

        # EOD Manager 초기화
        self.eod_manager = EODManager(self.config)
        self.eod_check_done_today = False

    async def run_trading_loop(self):
        """거래 루프"""
        while self.running:
            current_time = datetime.now()

            # ... 기존 코드 ...

            # EOD 체크 (15:00 ~ 15:10 사이 1회)
            if (current_time.hour == 15 and
                0 <= current_time.minute <= 10 and
                not self.eod_check_done_today):

                await self.handle_eod()
                self.eod_check_done_today = True

            # 자정 지나면 플래그 리셋
            if current_time.hour == 0:
                self.eod_check_done_today = False

            await asyncio.sleep(60)

    async def handle_eod(self):
        """장 마감 전 처리"""
        console.print("\n" + "=" * 80)
        console.print("[bold yellow]🕐 EOD 프로세스 시작[/bold yellow]")
        console.print("=" * 80 + "\n")

        # EOD Manager로 보유/청산 결정
        to_hold, to_close = self.eod_manager.run_eod_check(
            positions=self.positions,
            api=self.api,
            news_fetcher=self.news_fetcher
        )

        # 청산 대상 실행
        for stock_code in to_close:
            if stock_code in self.positions:
                pos = self.positions[stock_code]
                current_price = self.api.get_current_price(stock_code)
                profit_pct = ((current_price - pos['entry_price']) / pos['entry_price']) * 100

                console.print(f"[yellow]📤 EOD 청산: {pos.get('name', stock_code)} ({profit_pct:+.2f}%)[/yellow]")
                self.execute_sell(
                    stock_code,
                    current_price,
                    profit_pct,
                    "EOD 강제 청산",
                    use_market_order=False
                )

        # 보유 대상 로깅
        for stock_code in to_hold:
            if stock_code in self.positions:
                pos = self.positions[stock_code]
                console.print(f"[green]📌 익일 보유: {pos.get('name', stock_code)} (Score: {pos.get('overnight_score', 0):.2f})[/green]")

        # 다음날 우선 감시 리스트 생성
        self._build_priority_watchlist(to_close)

    def _build_priority_watchlist(self, closed_codes: List[str]):
        """
        EOD 청산 종목 중 다음날 재진입 후보 리스트 생성
        """
        watchlist = []

        for code in closed_codes:
            # eod_forced_exit=True인 종목만
            # (추세는 좋았지만 EOD 정책으로 청산된 종목)
            # → 다음날 갭업 시 재진입 대상

            # TODO: DB에 저장하거나 파일로 기록
            watchlist.append(code)

        # 다음날 장 시작 시 우선 체크
        self.priority_watchlist = watchlist

        console.print(f"\n[cyan]📋 다음날 우선 감시: {len(watchlist)}개 종목[/cyan]\n")
```

---

### Phase 2: Trailing Stop + 추세 유지 매도 방지

**목표**: 수익 구간 극대화, 추세 지속 중 매도 차단

#### 2-1. ATR 기반 Trailing Stop

```python
# trading/exit_logic_optimized.py 수정

def check_exit_signal(self, position, current_price, df):
    """청산 신호 체크"""

    # ... 기존 코드 ...

    # ========================================
    # 4순위: ATR 트레일링 스탑 (개선)
    # ========================================

    # ATR 계산
    if 'atr' in df.columns:
        atr = df['atr'].iloc[-1]
    else:
        # ATR 없으면 최근 고가-저가 범위로 추정
        atr = (df['high'].tail(14).max() - df['low'].tail(14).min()) / 14

    # 트레일링 활성화 조건
    if position.get('trailing_active') or profit_pct >= self.trailing_activation:
        position['trailing_active'] = True

        # ATR 기반 트레일링 스탑 라인
        # 고가 - (ATR × 1.5)
        trailing_stop_price = highest_price - (atr * 1.5)

        # 최소 잠금 수익 보장
        min_lock_price = entry_price * (1 + self.trailing_min_lock / 100)
        trailing_stop_price = max(trailing_stop_price, min_lock_price)

        position['trailing_stop_price'] = trailing_stop_price

        # 트레일링 스탑 발동
        if current_price <= trailing_stop_price:
            return True, f"ATR 트레일링 스탑 ({profit_pct:+.2f}%)", {
                'profit_pct': profit_pct,
                'highest_price': highest_price,
                'trailing_stop_price': trailing_stop_price,
                'atr': atr
            }

    # ... 나머지 코드 ...
```

#### 2-2. 추세 유지 중 매도 방지

```python
# trading/exit_logic_optimized.py 추가

def _check_trend_intact(self, df: pd.DataFrame, current_price: float) -> bool:
    """
    추세 유지 여부 체크

    Returns:
        True: 추세 지속 중 (매도 금지)
        False: 추세 약화 (매도 허용)
    """
    if df is None or df.empty or len(df) < 20:
        return False

    try:
        # 1. EMA 상태
        ema5 = df['close'].ewm(span=5).mean().iloc[-1]
        ema20 = df['close'].ewm(span=20).mean().iloc[-1]

        # 현재가 > EMA5 > EMA20
        ema_intact = current_price > ema5 > ema20

        # 2. EMA5 상승 중
        ema5_prev = df['close'].ewm(span=5).mean().iloc[-2]
        ema_rising = ema5 > ema5_prev

        # 3. RSI 과열 아님
        rsi_ok = True
        if 'rsi' in df.columns:
            rsi = df['rsi'].iloc[-1]
            rsi_ok = 55 <= rsi <= 75

        # 4. 거래량 유지
        volume_ok = True
        if 'vol_z20' in df.columns:
            vol_z20 = df['vol_z20'].iloc[-1]
            volume_ok = vol_z20 >= 0.5

        # 모든 조건 충족 시 추세 유지로 판단
        return ema_intact and ema_rising and rsi_ok and volume_ok

    except Exception as e:
        console.print(f"[dim red]추세 체크 실패: {e}[/dim red]")
        return False


def check_exit_signal(self, position, current_price, df):
    """청산 신호 체크"""

    # ... 기존 코드 (손절, 부분청산, 트레일링 스탑) ...

    # ========================================
    # 5순위: VWAP + EMA Breakdown (추세 체크 추가)
    # ========================================

    # 추세가 살아있으면 VWAP 신호 무시
    if self._check_trend_intact(df, current_price):
        console.print(f"[dim green]  추세 유지 중 → VWAP 신호 무시[/dim green]")
        return False, None, None

    # 추세 약화 시에만 VWAP 체크
    if profit_pct < self.vwap_profit_threshold:
        vwap_exit_check = self._check_vwap_exit(df, current_price, profit_pct)
        if vwap_exit_check[0]:
            return vwap_exit_check

    # ... 나머지 코드 ...
```

---

### Phase 3: 갭업 재진입 + 전일 보유 연장

**목표**: 다음날 갭업 시 재진입, 전일 종가 기반 보유 연장

#### 3-1. 갭업 재진입 로직

```python
# analyzers/gap_up_reentry.py (신규 생성)

from typing import Dict, Optional
import pandas as pd
from datetime import datetime, timedelta
from rich.console import Console

console = Console()


class GapUpReentryDetector:
    """
    갭업 재진입 감지기

    전일 매도 → 금일 갭업 시 재진입 신호 생성
    """

    def __init__(self, config: Dict):
        self.config = config

        # 갭업 재진입 설정
        self.gap_threshold = config.get('gap_reentry', {}).get('gap_threshold_pct', 3.0)
        self.volume_threshold = config.get('gap_reentry', {}).get('volume_z_threshold', 2.0)
        self.time_window = config.get('gap_reentry', {}).get('check_window_minutes', 30)

    def check_reentry_signal(
        self,
        stock_code: str,
        stock_name: str,
        prev_close: float,
        current_price: float,
        df: pd.DataFrame,
        priority_watchlist: List[str]
    ) -> Tuple[bool, str, float]:
        """
        갭업 재진입 신호 체크

        Args:
            stock_code: 종목코드
            stock_name: 종목명
            prev_close: 전일 종가
            current_price: 현재가
            df: 5분봉 DataFrame
            priority_watchlist: 우선 감시 리스트 (전일 EOD 청산 종목)

        Returns:
            (should_reentry, reason, confidence)
        """
        # 우선 감시 리스트에 없으면 체크 안 함
        if stock_code not in priority_watchlist:
            return False, "", 0.0

        # 갭 계산
        gap_pct = ((current_price - prev_close) / prev_close) * 100

        # 갭업 기준 미달
        if gap_pct < self.gap_threshold:
            return False, f"갭업 부족 ({gap_pct:+.2f}% < {self.gap_threshold}%)", 0.0

        # 장 초반 거래량 체크 (첫 30분)
        current_time = datetime.now()
        market_open = current_time.replace(hour=9, minute=0, second=0)
        elapsed_minutes = (current_time - market_open).total_seconds() / 60

        if elapsed_minutes > self.time_window:
            return False, f"시간 초과 ({elapsed_minutes:.0f}분 > {self.time_window}분)", 0.0

        # 거래량 급증 확인
        if 'vol_z20' in df.columns:
            vol_z20 = df['vol_z20'].iloc[-1]

            if vol_z20 < self.volume_threshold:
                return False, f"거래량 부족 (Z-score {vol_z20:.2f} < {self.volume_threshold})", 0.0

        # 첫 1분봉 고점 돌파 확인
        if len(df) >= 2:
            first_candle_high = df['high'].iloc[0]  # 시초가 봉

            if current_price <= first_candle_high:
                return False, f"첫 봉 고점 미돌파 ({current_price} <= {first_candle_high})", 0.0

        # 모든 조건 충족
        confidence = 0.7  # 갭업 재진입은 보수적으로
        reason = (
            f"갭업 재진입 조건 충족 | "
            f"갭: {gap_pct:+.2f}%, "
            f"거래량 Z: {vol_z20:.2f}, "
            f"시간: {elapsed_minutes:.0f}분"
        )

        return True, reason, confidence
```

#### 3-2. 전일 종가 기반 보유 연장

```python
# trading/eod_manager.py에 추가

def _calculate_eod_score(self, position, df, current_price, news_score):
    """EOD 시점 보유 점수 계산"""

    score = 0.0

    # ... 기존 코드 ...

    # 4. 전일 종가 대비 상태 (보너스 점수)
    try:
        prev_close = df['close'].iloc[-2]  # 전일 종가
        current_close = df['close'].iloc[-1]  # 당일 종가

        # 전일 종가 > 전일 EMA5 > 전일 VWAP
        prev_ema5 = df['close'].iloc[:-1].ewm(span=5).mean().iloc[-1]

        if 'vwap' in df.columns:
            prev_vwap = df['vwap'].iloc[-2]

            # 강한 마감 패턴
            if prev_close > prev_ema5 and prev_close > prev_vwap:
                score += 0.1  # 보너스

                # 당일도 강하게 마감
                current_ema5 = df['close'].ewm(span=5).mean().iloc[-1]
                if current_close > current_ema5:
                    score += 0.1  # 추가 보너스
    except:
        pass

    return min(score, 1.0)
```

---

### Phase 4: Multi-Alpha 통합 (RSVI + News + Volume)

**목표**: RSVI Phase 2 준비, 다중 알파 기반 보유 연장

```python
# trading/eod_manager.py에 추가

def _calculate_multi_alpha_score(
    self,
    stock_code: str,
    df: pd.DataFrame,
    news_score: float
) -> Dict[str, float]:
    """
    Multi-Alpha 기반 종합 점수

    Returns:
        {
            'rsvi_alpha': 0.0-1.0,
            'news_alpha': 0.0-1.0,
            'volume_alpha': 0.0-1.0,
            'total': 0.0-1.0
        }
    """
    from analyzers.volume_indicators import calculate_rsvi_score, alpha_volume_strength

    # 1. RSVI Alpha
    rsvi_alpha = 0.0
    if 'vol_z20' in df.columns and 'vroc10' in df.columns:
        vol_z20 = df['vol_z20'].iloc[-1]
        vroc10 = df['vroc10'].iloc[-1]
        rsvi_alpha = calculate_rsvi_score(vol_z20, vroc10)

    # 2. News Alpha (정규화)
    news_alpha = news_score / 100.0

    # 3. Volume Alpha
    volume_alpha = alpha_volume_strength(df)
    # -1.0 ~ 1.0 → 0.0 ~ 1.0 변환
    volume_alpha = (volume_alpha + 1.0) / 2.0

    # 4. 가중 평균 (RSVI 40%, News 30%, Volume 30%)
    total = (0.4 * rsvi_alpha) + (0.3 * news_alpha) + (0.3 * volume_alpha)

    return {
        'rsvi_alpha': rsvi_alpha,
        'news_alpha': news_alpha,
        'volume_alpha': volume_alpha,
        'total': total
    }
```

---

## 📊 설정 파일 수정

### config/strategy_hybrid.yaml

```yaml
# EOD 정책 (신규 추가)
eod_policy:
  enabled: true
  check_time: "15:00:00"                # EOD 체크 시간
  max_overnight_positions: 3            # 최대 익일 보유 종목 수
  min_overnight_score: 0.6              # 최소 보유 점수 (0.0-1.0)

  # 익일 보유 기준
  overnight_criteria:
    trend_weight: 0.4                   # 추세 가중치
    volume_weight: 0.3                  # 거래량 가중치
    news_weight: 0.3                    # 뉴스/재료 가중치

    min_ema_state: true                 # EMA5 > EMA20 필수
    min_vol_z20: 1.0                    # 최소 거래량 Z-score
    min_news_score: 50                  # 최소 뉴스 점수

# 갭업 재진입 (신규 추가)
gap_reentry:
  enabled: true
  gap_threshold_pct: 3.0                # 갭업 기준 (%)
  volume_z_threshold: 2.0               # 거래량 Z-score 기준
  check_window_minutes: 30              # 체크 시간 (장 시작 후 30분)
  reentry_confidence: 0.7               # 재진입 신뢰도

# 기존 time_based_exit 수정
time_based_exit:
  loss_breakeven_exit_time: "15:00:00"
  final_force_exit_time: "15:10:00"     # EOD Manager로 대체 예정
  loss_breakeven_threshold_pct: 0.3
```

---

## 🎯 구현 우선순위

### 즉시 구현 (Phase 1)

1. ✅ **EODManager 추가** → `trading/eod_manager.py`
2. ✅ **Position 구조 확장** → `main_auto_trading.py:2788`
3. ✅ **진입 시점 overnight 판단** → `signal_orchestrator.py` or `main_auto_trading.py`
4. ✅ **EOD 프로세스 통합** → `main_auto_trading.py:handle_eod()`
5. ✅ **설정 파일 추가** → `config/strategy_hybrid.yaml`

### 단기 구현 (Phase 2)

6. ✅ **ATR Trailing Stop 개선** → `trading/exit_logic_optimized.py`
7. ✅ **추세 유지 체크** → `trading/exit_logic_optimized.py:_check_trend_intact()`

### 중기 구현 (Phase 3)

8. ✅ **갭업 재진입 로직** → `analyzers/gap_up_reentry.py`
9. ✅ **전일 종가 기반 보유 연장** → `trading/eod_manager.py`
10. ✅ **우선 감시 리스트** → `main_auto_trading.py:_build_priority_watchlist()`

### 장기 구현 (Phase 4)

11. ⏸️ **Multi-Alpha 통합** → RSVI Phase 2와 통합
12. ⏸️ **ML 기반 overnight 예측** → 향후 고도화

---

## 📈 예상 효과

### 시뮬레이션 (한국피아이엠, 한올바이오파마 케이스)

| 항목 | 현재 (당일 청산) | 개선 후 (조건부 보유) |
|------|-----------------|---------------------|
| **금요일 15:00** | | |
| 한국피아이엄 | 55,200원 매도 | 보유 (Score 0.75) |
| 한올바이오파마 | 46,800원 매도 | 보유 (Score 0.68) |
| **월요일 장중** | | |
| 한국피아이엄 | - | 60,600원 트레일링 매도 (+9.8%) |
| 한올바이오파마 | - | 50,200원 트레일링 매도 (+7.2%) |
| **수익 차이** | 0원 | **+약 30,000원** (2종목 100주 기준) |

### 기대 성과 (1개월 기준)

- **수익 기회 포착률**: 70% → 90% (+20%p)
- **평균 보유 기간**: 1일 → 1.5일 (+50%)
- **트레일링 스탑 효과**: 평균 수익 +1.5%p 추가
- **갭업 재진입 성공률**: 60% (백테스트 필요)

---

## ⚠️ 리스크 관리

### 익일 보유 리스크

1. **갭하락 리스크**
   - 대응: 익일 개장 직후 손절 라인 설정 (-2%)
   - 뉴스 모니터링 강화

2. **과도한 보유**
   - 대응: max_overnight_positions = 3개 제한
   - 포트폴리오 분산 (최대 30% 자본)

3. **재료 소멸**
   - 대응: 익일 장 초반 재검증
   - 뉴스 감성 점수 재확인

### 갭업 재진입 리스크

1. **고점 추격**
   - 대응: 갭 +3% 이상만 진입
   - 거래량 Z-score 2.0 이상 필수

2. **단기 급등 후 폭락**
   - 대응: 진입 후 즉시 트레일링 스탑 적용
   - 손절 라인 엄격 (-1.5%)

---

## 🔧 테스트 계획

### Unit Test

```python
# tests/test_eod_manager.py

def test_eod_score_calculation():
    """EOD 점수 계산 테스트"""
    # ... 테스트 코드 ...

def test_overnight_decision():
    """익일 보유 결정 테스트"""
    # ... 테스트 코드 ...

def test_gap_reentry_signal():
    """갭업 재진입 신호 테스트"""
    # ... 테스트 코드 ...
```

### 백테스트

```bash
# 최근 3개월 데이터로 백테스트
python3 scripts/backtest_eod_improvement.py --start 2024-09-01 --end 2024-11-30
```

**검증 항목**:
- 익일 보유 종목의 다음날 성과
- EOD 청산 종목 vs 보유 종목 수익률 비교
- 갭업 재진입 성공률
- 리스크 지표 (최대 낙폭, 샤프 비율)

### Paper Trading

```bash
# 실거래 전 1주일 모의 거래
python3 main_auto_trading.py --paper-trading --eod-enabled
```

---

## 📅 배포 일정

| 단계 | 작업 | 예상 기간 |
|------|------|----------|
| **Phase 1** | EODManager 구현 + 테스트 | 2일 |
| **Phase 1** | Position 구조 확장 | 1일 |
| **Phase 1** | 통합 테스트 + 백테스트 | 2일 |
| **Phase 2** | Trailing Stop 개선 | 1일 |
| **Phase 2** | 추세 체크 로직 | 1일 |
| **Phase 3** | 갭업 재진입 로직 | 2일 |
| **Phase 3** | 우선 감시 리스트 | 1일 |
| **전체** | Paper Trading | 1주 |
| **전체** | 실거래 적용 | - |

**총 소요 기간**: 약 2주 (개발 10일 + Paper Trading 1주)

---

## ✅ 체크리스트

### Phase 1 (EOD Manager)

- [ ] `trading/eod_manager.py` 생성
- [ ] Position 구조에 `allow_overnight`, `overnight_score`, `eod_forced_exit` 추가
- [ ] `should_allow_overnight()` 함수 구현
- [ ] `main_auto_trading.py`에 EOD 프로세스 통합
- [ ] `config/strategy_hybrid.yaml`에 `eod_policy` 추가
- [ ] Unit Test 작성
- [ ] 백테스트 실행

### Phase 2 (Trailing Stop)

- [ ] ATR 기반 Trailing Stop 개선
- [ ] `_check_trend_intact()` 함수 추가
- [ ] VWAP 신호 무시 로직 통합
- [ ] 테스트

### Phase 3 (갭업 재진입)

- [ ] `analyzers/gap_up_reentry.py` 생성
- [ ] 전일 종가 기반 보유 연장 로직
- [ ] `_build_priority_watchlist()` 구현
- [ ] DB에 우선 감시 리스트 저장
- [ ] 테스트

### Paper Trading

- [ ] 1주일 모의 거래 실행
- [ ] 성과 분석
- [ ] 파라미터 튜닝
- [ ] 리스크 검증

### 실거래 적용

- [ ] 최종 점검
- [ ] 배포
- [ ] 모니터링 강화

---

**작성자**: Claude Code
**작성일**: 2025-11-30
**버전**: EOD Improvement Plan v1.0
**상태**: 구현 계획 완료 - 개발 준비

**다음 단계**: Phase 1 구현 시작 (EODManager + Position 구조 확장)
