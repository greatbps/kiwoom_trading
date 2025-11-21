# 거래 내역 분석 및 개선 방안
**분석일**: 2025-11-21
**분석 기간**: 2025-11-17 ~ 2025-11-21
**총 거래**: 27건 (매수 23건, 매도 24건)

---

## 목차
1. [현재 성과 분석](#1-현재-성과-분석)
2. [Critical 문제점](#2-critical-문제점)
3. [종목별 상세 분석](#3-종목별-상세-분석)
4. [개선 방안](#4-개선-방안)
5. [긴급 수정 사항](#5-긴급-수정-사항)

---

## 1. 현재 성과 분석

### 1.1 전체 통계

| 지표 | 현재 값 | 목표 값 | 상태 |
|------|---------|---------|------|
| **승률** | 40.7% | 45~55% | ❌ 낮음 |
| **평균 수익률** | +0.26% | +2~4% | ❌ 매우 낮음 |
| **중간 수익률** | -0.26% | +1% | ❌ 음수 |
| **손익비** | 1.72 | 1.5~2.0 | ✅ 양호 |
| **평균 승리** | +2.46% | +2~4% | ✅ 양호 |
| **평균 손실** | -1.43% | -1~2% | ⚠️ 경계 |
| **최대 손실** | -4.53% | -3% 이하 | ❌ Hard Stop 미작동 |

### 1.2 거래 시간 분석

```
진입 시간대별 분포:
- 09시: 1건 ⚠️
- 10시: 8건
- 11시: 6건
- 12시: 4건
- 13시: 3건
- 15시: 1건 ❌ (15:30 진입)
```

**문제점**:
- ❌ **09:00~09:30 진입**: 1건 (시간 필터 미작동)
- ❌ **15:00 이후 진입**: 1건 (15:30, 완전한 필터 위반)

### 1.3 보유 시간 분석

| 거래 유형 | 평균 보유 시간 | 목표 |
|-----------|----------------|------|
| **전체 평균** | 115.1분 (1.9시간) | 2~4시간 |
| **승리 거래** | 82.6분 (1.4시간) | 2~3시간 |
| **손실 거래** | 135.1분 (2.3시간) | 1~2시간 |

**심각한 문제**:
- ❌ **손실 거래가 승리 거래보다 52.5분 더 길게 보유**
- 원인: Early Failure Cut 미작동

---

## 2. Critical 문제점

### 🚨 문제 1: Early Failure Cut 완전 미작동

**설정** (`config/strategy_hybrid.yaml:63-66`):
```yaml
early_failure:
  enabled: true
  window_minutes: 15
  loss_cut_pct: -0.6
```

**실제 결과**:
```
15분 이내 -0.6% 이하 손실: 5건 발견

1. 메드팩토 10:11 → 10:12 (1분): -1.41% ❌ -0.6% 초과
2. 메드팩토 10:13 → 10:15 (2분): -4.53% ❌ -0.6% 초과
3. 메드팩토 10:16 → 10:17 (1분): -0.62% ❌ -0.6% 근접
4. 메드팩토 10:16 → 10:19 (3분): -3.11% ❌ -0.6% 초과
5. 메드팩토 10:18 → 10:19 (1분): -1.39% ❌ -0.6% 초과
```

**손실 영향**:
- 총 손실: -3,910원 (메드팩토 6건 중 5건)
- 이론적 손실 (Early Cut 시): -0.6% × 5건 = 약 -300원
- **실제 초과 손실: -3,610원 (1,200% 증가!)**

**원인 분석**:
1. `exit_logic_optimized.py`에서 Early Failure Cut이 실제로 작동하지 않음
2. 또는 실시간 가격 체크 주기가 너무 김 (1분 이상)
3. Hard Stop (-3%)만 작동, Early Failure Cut (-0.6%)은 무시됨

---

### 🚨 문제 2: Hard Stop 미작동 (최악의 손실)

**설정**: hard_stop_pct = 3.0%

**실제 결과**:
```
메드팩토 10:13 → 10:15 (2분): -4.53% ❌

진입가: 8,390원
청산가: 8,010원
손실: -4.53%
```

**-3% 도달 시점**: 8,390 × 0.97 = **8,138원**
**실제 청산가**: 8,010원 (추가 -128원, -1.57% 더 하락)

**원인**:
- Hard Stop이 시장가로 즉시 청산하지 않음
- 지정가로 주문하여 체결되지 않았을 가능성
- 실시간 모니터링 주기가 너무 김

---

### 🚨 문제 3: 시간 필터 완전 위반

**설정** (`config/strategy_hybrid.yaml:38-42`):
```yaml
time_filter:
  use_time_filter: true
  avoid_early_minutes: 30    # 09:30까지 회피
  avoid_late_minutes: 21     # 14:59까지만 진입
```

**실제 결과**:
```
신테카바이오 15:30:00 진입 ❌

설정: 14:59까지만 진입 허용
실제: 15:30 진입 (31분 초과)
결과: -1.82% 손실
```

**원인**:
- `signal_orchestrator.py` L0 필터가 작동하지 않음
- 또는 실시간 거래 경로가 L0 필터를 우회함

---

### 🚨 문제 4: 연속 손실 방지 미작동

**설정** (`config/strategy_hybrid.yaml:44-46`):
```yaml
re_entry:
  use_cooldown: true
  cooldown_minutes: 20
```

**실제 결과**:
```
태성: 5건 연속 100% 손실
- 10:54 진입 → 14:05 청산 (-1.59%)
- 11:34 진입 (40분 후) → 12:44 청산 (-1.54%)
- 11:34 진입 (동일 시각!) → 12:44 청산 (-1.54%)  ❌ 중복 진입
- 13:33 진입 → 13:35 청산 (-0.58%)
- 13:34 진입 (1분 후!) → 13:41 청산 (-0.52%)    ❌ 쿨다운 무시

메드팩토: 6건 중 5건 손실 (83.3% 손실률)
- 10:11~10:19: 8분 동안 5건 진입 ❌ 완전한 쿨다운 무시
```

**원인**:
- 쿨다운이 전혀 작동하지 않음
- 손실 직후 즉시 재진입 허용
- 동일 시각 중복 진입 발생

---

## 3. 종목별 상세 분석

### 3.1 최악의 종목: 메드팩토

| 거래 | 진입 시간 | 청산 시간 | 보유 시간 | 수익률 | 문제점 |
|------|----------|----------|----------|--------|--------|
| 1 | 10:11 | 10:12 | 1분 | -1.41% | Early Cut 미작동 |
| 2 | 10:13 | 10:14 | 1분 | +1.19% | 유일한 승리 |
| 3 | 10:13 | 10:15 | 2분 | **-4.53%** | Hard Stop 미작동 |
| 4 | 10:16 | 10:17 | 1분 | -0.62% | Early Cut 경계선 |
| 5 | 10:16 | 10:19 | 3분 | -3.11% | Hard Stop 미작동 |
| 6 | 10:18 | 10:19 | 1분 | -1.39% | Early Cut 미작동 |

**총 성과**: 6건 거래, 5건 손실 (83.3%), 총 -3,910원 (-9.87%)

**문제 분석**:
1. **10:11~10:19 (8분)**: 6건 진입 → 쿨다운 완전 무시
2. **급격한 하락장**: VWAP 아래로 이탈 중 계속 진입
3. **시스템 실패**: 손실 신호를 무시하고 계속 진입

---

### 3.2 100% 손실 종목: 태성

| 거래 | 진입 시간 | 청산 시간 | 보유 시간 | 수익률 |
|------|----------|----------|----------|--------|
| 1 | 10:54 | 14:05 | 191분 | -1.59% |
| 2 | 11:34 | 12:44 | 70분 | -1.54% |
| 3 | 11:34 | 12:44 | 70분 | -1.54% |
| 4 | 13:33 | 13:35 | 2분 | -0.58% |
| 5 | 13:34 | 13:41 | 7분 | -0.52% |

**총 성과**: 5건 거래, 5건 손실 (100%), 총 -2,225원 (-5.78%)

**문제 분석**:
1. **VWAP 백테스트 실패**: 태성은 검증 단계에서 걸러졌어야 함
2. **연속 손실 무시**: 5번 연속 손실인데도 계속 진입
3. **중복 진입**: 11:34 동시에 2건, 13:33~13:34 연속 2건

---

### 3.3 성공 종목: 로킷헬스케어, 글로벌텍스프리

**로킷헬스케어**:
- 1건 거래: +5.41% (2,800원)
- 보유 시간: 232분 (3.9시간)
- 진입: 10:59, 청산: 14:51

**글로벌텍스프리**:
- 4건 거래: 3승 1무 (75% 승률)
- 평균 수익률: +3.98%
- 총 수익: +1,930원
- 보유 시간: 39~117분

**성공 요인**:
1. ✅ 충분한 보유 시간 (평균 2~4시간)
2. ✅ 급격한 손실 없음
3. ✅ VWAP 위에서 안정적 상승

---

## 4. 개선 방안

### 4.1 긴급 수정 (Priority 1)

#### ✅ Fix 1: Early Failure Cut 실제 작동 확인

**현재 문제**: 설정은 되어 있으나 실제로 작동하지 않음

**수정 방안**:

**A. Exit Logic 수정** (`trading/exit_logic_optimized.py`):
```python
def check_exit_signal(self, position, df, current_price, current_time):
    """
    청산 신호 체크 (우선순위 순서)
    """
    # ====== 1순위: Early Failure Cut ======
    if self.early_failure_enabled:
        entry_time = position['entry_time']
        elapsed_minutes = (current_time - entry_time).total_seconds() / 60

        if elapsed_minutes <= self.early_failure_window_minutes:
            profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

            if profit_pct <= self.early_failure_loss_cut_pct:  # -0.6% 이하
                return True, f"Early Failure Cut ({elapsed_minutes:.0f}분, {profit_pct:.2f}%)", {
                    'profit_pct': profit_pct,
                    'use_market_order': True,  # 시장가 즉시 청산
                    'reason': 'EARLY_FAILURE'
                }

    # ====== 2순위: Hard Stop ======
    profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100

    if profit_pct <= -self.hard_stop_pct:  # -3%
        return True, f"Hard Stop ({profit_pct:.2f}%)", {
            'profit_pct': profit_pct,
            'use_market_order': True,  # 시장가 즉시 청산
            'reason': 'HARD_STOP'
        }

    # ... 이후 순위 로직
```

**B. 실시간 모니터링 주기 단축** (`main_auto_trading.py`):
```python
# 기존: 1분마다 체크
await asyncio.sleep(60)

# 수정: 10초마다 체크 (Early Failure Cut을 위해)
await asyncio.sleep(10)
```

**C. 시장가 주문 강제** (`main_auto_trading.py:execute_sell`):
```python
if exit_info.get('use_market_order'):
    # 시장가 주문 (즉시 체결)
    result = self.api.send_order(
        'SELL',
        stock_code,
        quantity,
        0,  # 가격 0 = 시장가
        '03'  # 시장가 주문 타입
    )
else:
    # 지정가 주문
    result = self.api.send_order(...)
```

---

#### ✅ Fix 2: 시간 필터 강제 적용

**문제**: 15:30 진입 발생

**수정** (`main_auto_trading.py:_should_evaluate_entry`):
```python
def _should_evaluate_entry(self, current_time: datetime) -> bool:
    """
    진입 평가 가능 여부 체크 (모든 경로에서 강제 체크)
    """
    current_time_only = current_time.time()

    # Hard-coded 시간 체크 (설정 파일 무시)
    ENTRY_START = time(9, 30, 0)
    ENTRY_END = time(14, 59, 0)

    if not (ENTRY_START <= current_time_only <= ENTRY_END):
        return False

    # L0 시스템 필터도 추가 체크
    l0_passed, _ = self.signal_orchestrator.check_l0_system_filter(
        current_time=current_time,
        daily_pnl=self.calculate_daily_pnl()
    )

    return l0_passed
```

**모든 진입 경로에 적용**:
```python
# 조건검색 진입
if not self._should_evaluate_entry(datetime.now()):
    return  # 진입 차단

# 실시간 신호 진입
if not self._should_evaluate_entry(datetime.now()):
    return  # 진입 차단
```

---

#### ✅ Fix 3: 쿨다운 강제 적용

**문제**: 동일 종목 연속 진입 (8분 동안 6건)

**수정** (`main_auto_trading.py`):
```python
class AutoTradingSystem:
    def __init__(self, ...):
        # ...
        self.stock_cooldown = {}  # {stock_code: last_exit_time}
        self.cooldown_minutes = 20  # 고정값

    def _check_cooldown(self, stock_code: str) -> Tuple[bool, str]:
        """
        재진입 쿨다운 체크
        """
        if stock_code not in self.stock_cooldown:
            return True, ""

        last_exit_time = self.stock_cooldown[stock_code]
        elapsed_minutes = (datetime.now() - last_exit_time).total_seconds() / 60

        if elapsed_minutes < self.cooldown_minutes:
            remaining = self.cooldown_minutes - elapsed_minutes
            return False, f"쿨다운 {remaining:.0f}분 남음"

        return True, ""

    async def _process_buy_signal(self, stock_code, stock_name, ...):
        """
        매수 신호 처리
        """
        # 쿨다운 체크 (최우선)
        cooldown_ok, reason = self._check_cooldown(stock_code)
        if not cooldown_ok:
            console.print(f"[yellow]⏸️  {stock_name}: {reason}[/yellow]")
            return

        # ... 기존 로직
        # 매수 실행 후 쿨다운 기록 제거
        if stock_code in self.stock_cooldown:
            del self.stock_cooldown[stock_code]

    async def _execute_exit(self, stock_code, ...):
        """
        청산 실행
        """
        # ... 청산 로직
        # 청산 완료 후 쿨다운 기록
        if result['status'] == 'LOSS':  # 손실 거래만 쿨다운 적용
            self.stock_cooldown[stock_code] = datetime.now()
```

---

#### ✅ Fix 4: 연속 손실 차단

**신규 기능**: 동일 종목 3회 연속 손실 시 당일 진입 금지

**구현** (`main_auto_trading.py`):
```python
class AutoTradingSystem:
    def __init__(self, ...):
        # ...
        self.stock_loss_streak = {}  # {stock_code: consecutive_losses}
        self.stock_ban_list = set()  # 당일 진입 금지 종목

    def _update_loss_streak(self, stock_code: str, is_win: bool):
        """
        연속 손실 카운트 업데이트
        """
        if is_win:
            self.stock_loss_streak[stock_code] = 0
        else:
            self.stock_loss_streak[stock_code] = self.stock_loss_streak.get(stock_code, 0) + 1

        # 3회 연속 손실 → 당일 진입 금지
        if self.stock_loss_streak[stock_code] >= 3:
            self.stock_ban_list.add(stock_code)
            console.print(f"[red]🚫 {stock_code}: 3회 연속 손실로 당일 진입 금지[/red]")

            # risk_log에 기록
            self._log_risk_event(
                stock_code=stock_code,
                event_type='BAN_3_CONSECUTIVE_LOSSES',
                details=f"연속 손실 {self.stock_loss_streak[stock_code]}회"
            )

    async def _process_buy_signal(self, stock_code, stock_name, ...):
        """
        매수 신호 처리
        """
        # 1. 금지 종목 체크
        if stock_code in self.stock_ban_list:
            console.print(f"[red]🚫 {stock_name}: 당일 진입 금지 종목[/red]")
            return

        # 2. 쿨다운 체크
        # ... (Fix 3)

        # ... 매수 로직

    async def _execute_exit(self, stock_code, ...):
        """
        청산 실행
        """
        # ... 청산 로직
        profit_pct = result['profit_pct']
        is_win = profit_pct > 0

        # 연속 손실 업데이트
        self._update_loss_streak(stock_code, is_win)
```

---

### 4.2 전략 파라미터 조정 (Priority 2)

#### 📊 조정 1: Early Failure Cut 강화

**현재 설정**:
```yaml
early_failure:
  enabled: true
  window_minutes: 15
  loss_cut_pct: -0.6
```

**제안**:
```yaml
early_failure:
  enabled: true
  window_minutes: 10        # 15분 → 10분 (더 빠르게)
  loss_cut_pct: -0.5        # -0.6% → -0.5% (더 타이트하게)
```

**근거**:
- 메드팩토 손실이 1~3분 내에 발생
- 15분 창은 너무 길어서 -4.53%까지 손실 확대
- 10분 내 -0.5% 도달 시 빠른 정리 필요

---

#### 📊 조정 2: Hard Stop 강화

**현재 설정**:
```yaml
hard_stop_pct: 3.0
```

**제안**:
```yaml
hard_stop_pct: 2.5        # 3.0% → 2.5% (더 보수적)
```

**근거**:
- 메드팩토 -4.53% 손실은 심리적 충격 큼
- Hard Stop은 최후의 방어선, 더 타이트하게 설정
- Early Cut이 -0.5%, Hard Stop이 -2.5%로 2단계 방어

---

#### 📊 조정 3: VWAP 백테스트 기준 강화

**현재 설정** (`analyzers/pre_trade_validator.py`):
```python
self.min_win_rate = 40.0      # 최소 승률
self.min_avg_profit = 1.0     # 최소 평균 수익률
self.min_trades = 2           # 최소 거래 수
```

**제안**:
```python
self.min_win_rate = 45.0      # 40% → 45% (더 엄격)
self.min_avg_profit = 1.5     # 1.0% → 1.5% (더 높게)
self.min_trades = 3           # 2회 → 3회 (더 많은 샘플)
```

**근거**:
- 태성 (100% 손실)과 메드팩토 (83% 손실)는 백테스트에서 걸러졌어야 함
- 현재 기준이 너무 느슨함
- 승률 45% + 평균 1.5% 이상만 진입 허용

---

#### 📊 조정 4: Stage 2/3 진입 비중 축소

**현재 설정**:
- Stage 1: 100%
- Stage 2: 60%
- Stage 3: 30%

**제안**:
- Stage 1: 100% (유지)
- Stage 2: **40%** (60% → 40%)
- Stage 3: **20%** (30% → 20%)

**근거**:
- Stage 2/3는 데이터 부족 또는 낮은 신뢰도
- 현재 손실률 51.9%로 보아 리스크 과다
- 진입 비중을 더 보수적으로 조정

---

### 4.3 필터 강화 (Priority 3)

#### 🔍 추가 필터 1: 급격한 하락 감지

**목적**: 메드팩토처럼 급락 중인 종목 진입 차단

**구현** (`analyzers/signal_orchestrator.py`):
```python
def check_rapid_decline(self, df: pd.DataFrame, stock_code: str) -> Tuple[bool, str]:
    """
    급격한 하락 감지 (최근 10분 내 -2% 이상 하락)
    """
    if len(df) < 10:
        return True, ""

    recent_10 = df.tail(10)
    high_10 = recent_10['high'].max()
    current = recent_10['close'].iloc[-1]

    decline_pct = ((current - high_10) / high_10) * 100

    if decline_pct < -2.0:
        return False, f"급격한 하락 감지 ({decline_pct:.2f}%)"

    return True, ""
```

---

#### 🔍 추가 필터 2: VWAP 이탈 확인

**목적**: VWAP 아래에서 진입 차단

**구현** (`analyzers/signal_orchestrator.py`):
```python
def check_vwap_position(self, df: pd.DataFrame) -> Tuple[bool, str]:
    """
    VWAP 위에 있는지 확인
    """
    if 'vwap' not in df.columns:
        return True, ""

    current_price = df['close'].iloc[-1]
    current_vwap = df['vwap'].iloc[-1]

    # VWAP 위 +0.3% 이상에서만 진입
    vwap_distance = ((current_price - current_vwap) / current_vwap) * 100

    if vwap_distance < 0.3:
        return False, f"VWAP 너무 근접 ({vwap_distance:.2f}%)"

    return True, ""
```

---

## 5. 긴급 수정 사항

### 5.1 즉시 적용 (오늘 내)

#### ✅ 수정 1: Exit Logic Early Failure Cut 활성화

**파일**: `trading/exit_logic_optimized.py`

**위치**: `check_exit_signal()` 메서드 최상단

**수정 내용**:
```python
# 1순위로 Early Failure Cut 체크 (Hard Stop보다 우선)
if self.early_failure_enabled:
    entry_time = position.get('entry_time')
    if entry_time:
        elapsed_minutes = (current_time - entry_time).total_seconds() / 60
        if elapsed_minutes <= self.early_failure_window_minutes:
            profit_pct = ((current_price - position['entry_price']) / position['entry_price']) * 100
            if profit_pct <= self.early_failure_loss_cut_pct:
                return True, f"Early Failure Cut", {
                    'profit_pct': profit_pct,
                    'use_market_order': True
                }
```

---

#### ✅ 수정 2: 시간 필터 모든 경로 강제 적용

**파일**: `main_auto_trading.py`

**신규 메서드 추가**:
```python
def _is_valid_entry_time(self, current_time: datetime) -> bool:
    """시간 필터 강제 체크 (설정 파일 무관)"""
    t = current_time.time()
    return time(9, 30) <= t <= time(14, 59)
```

**모든 진입 함수에 추가**:
```python
async def _process_condition_stocks(self, ...):
    if not self._is_valid_entry_time(datetime.now()):
        return
    # ...

async def _process_buy_signal(self, ...):
    if not self._is_valid_entry_time(datetime.now()):
        return
    # ...
```

---

#### ✅ 수정 3: 쿨다운 + 연속 손실 차단 추가

**파일**: `main_auto_trading.py`

**초기화 추가**:
```python
def __init__(self, ...):
    # ...
    self.stock_cooldown = {}
    self.stock_loss_streak = {}
    self.stock_ban_list = set()
    self.cooldown_minutes = 20
```

**진입 체크 추가**:
```python
async def _process_buy_signal(self, stock_code, stock_name, ...):
    # 1. 금지 종목 체크
    if stock_code in self.stock_ban_list:
        console.print(f"[red]🚫 {stock_name}: 연속 손실로 진입 금지[/red]")
        return

    # 2. 쿨다운 체크
    if stock_code in self.stock_cooldown:
        last_exit = self.stock_cooldown[stock_code]
        elapsed = (datetime.now() - last_exit).total_seconds() / 60
        if elapsed < self.cooldown_minutes:
            console.print(f"[yellow]⏸️  {stock_name}: 쿨다운 {self.cooldown_minutes - elapsed:.0f}분 남음[/yellow]")
            return

    # ... 매수 로직
```

---

### 5.2 내일 적용 (테스트 후)

#### 📊 파라미터 조정

**파일**: `config/strategy_hybrid.yaml`

```yaml
# Early Failure Cut 강화
early_failure:
  enabled: true
  window_minutes: 10        # 15 → 10
  loss_cut_pct: -0.5        # -0.6 → -0.5

# Hard Stop 강화
risk_control:
  hard_stop_pct: 2.5        # 3.0 → 2.5

# Stage 비중 축소
# (코드 수정: signal_orchestrator.py calculate_stage)
Stage 2: 0.4  # 0.6 → 0.4
Stage 3: 0.2  # 0.3 → 0.2
```

**파일**: `analyzers/pre_trade_validator.py`

```python
# VWAP 백테스트 기준 강화
self.min_win_rate = 45.0      # 40 → 45
self.min_avg_profit = 1.5     # 1.0 → 1.5
self.min_trades = 3           # 2 → 3
```

---

## 6. 예상 효과

### 6.1 정량적 개선

| 지표 | 현재 | 목표 | 개선 방법 |
|------|------|------|-----------|
| **승률** | 40.7% | 50%+ | VWAP 기준 강화 (45%, 1.5%) |
| **평균 수익률** | 0.26% | 1.5%+ | 손실 종목 사전 차단 |
| **최대 손실** | -4.53% | -2.5% | Hard Stop 2.5% 강제 |
| **Early Cut 작동률** | 0% | 100% | 실시간 모니터링 10초 |
| **시간 필터 위반** | 1건 | 0건 | 모든 경로 강제 체크 |
| **연속 손실** | 5건 | 3건 상한 | 3회 연속 시 진입 금지 |

### 6.2 정성적 개선

1. **손실 감소**:
   - 메드팩토 -3,910원 → 약 -300원 (92% 감소)
   - 태성 진입 자체 차단 (VWAP 기준 미달)

2. **심리적 안정**:
   - 최대 손실 -2.5% 상한 → 안정적 트레이딩
   - 연속 손실 3회 상한 → 과도한 손실 방지

3. **시스템 신뢰도**:
   - 시간 필터 100% 준수 → 규칙 기반 신뢰
   - 쿨다운 작동 → 충동적 진입 방지

---

## 7. 실행 계획

### 오늘 (2025-11-21)
- [x] 거래 내역 분석 완료
- [ ] Exit Logic Early Failure Cut 수정
- [ ] 시간 필터 강제 적용
- [ ] 쿨다운 + 연속 손실 차단 코드 추가

### 내일 (2025-11-22)
- [ ] 파라미터 조정 (strategy_hybrid.yaml)
- [ ] VWAP 백테스트 기준 강화
- [ ] Stage 비중 축소
- [ ] 소액 테스트 실행 (1종목, 1주)

### 모레 (2025-11-23)
- [ ] 정상 운영 재개
- [ ] 실시간 모니터링 강화
- [ ] 일일 성과 리포트 작성

---

## 8. 모니터링 체크리스트

### 매일 확인
- [ ] Early Failure Cut 발동 건수
- [ ] Hard Stop 발동 건수
- [ ] 시간 필터 위반 건수 (0건 목표)
- [ ] 쿨다운 차단 건수
- [ ] 연속 손실 3회 종목
- [ ] 일일 승률 / 평균 수익률

### 주간 확인
- [ ] 종목별 성과 분석
- [ ] 최악의 종목 TOP 3
- [ ] 최선의 종목 TOP 3
- [ ] 파라미터 조정 필요성 검토

---

**문서 작성**: 2025-11-21
**다음 검토**: 2025-11-28 (1주일 후)
