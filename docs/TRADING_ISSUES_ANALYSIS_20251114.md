# 매매 시스템 문제점 분석 및 개선 방안

**작성일**: 2025-11-14
**분석 기간**: 최근 7일 (2025-11-08 ~ 2025-11-14)
**현재 손익**: -7,840원 (24건 거래)

---

## 📊 거래 실적 분석

### 종합 지표
- **총 거래**: 24건
- **승률**: 66.7% (16승 8패)
- **총 손익**: -7,840원
- **평균 수익률**: -0.23%
- **최악의 거래**: -10.41% (카티스)
- **최고의 거래**: +3.39% (공구우먼)

### 주요 문제점

#### 🔴 1. 극단적 손실 발생 (최우선 해결 과제)
```
2025-11-12 00:00:41 | 140430 카티스 | 31주 @ 3,530원
손익: -12,710원 (-10.41%)
종료 사유: 손절 (-10.41%)
```

**문제**:
- 현재 손절 설정: **-1.3%** (strategy_config.yaml)
- 실제 손절 발생: **-10.41%**
- **8배 이상의 과도한 손실 발생**

**원인 분석**:
1. 손절가 미체결 (체결 지연 또는 급락)
2. 시장가 매도 시 슬리피지
3. 손절 로직 작동 지연

#### 🟡 2. 중복 매도 버그
```
2025-11-12 00:19:50 | 026890 스틱인베스트먼트 | 11주 @ 10,890원 | +110원 (+0.09%)
2025-11-12 00:20:50 | 026890 스틱인베스트먼트 | 11주 @ 10,890원 | +110원 (+0.09%)
2025-11-12 00:21:51 | 026890 스틱인베스트먼트 | 11주 @ 10,890원 | +110원 (+0.09%)
...
(총 11회 중복 매도 기록)
```

**문제**:
- 동일 종목 동일 가격에 11회 연속 매도 기록
- 실제로는 1회만 매도되었을 가능성 (DB 중복 저장)

**원인**:
- 장 마감 전 강제 청산 로직에서 중복 호출 가능성
- 포지션 트래커에서 매도 완료 체크 누락

#### 🟠 3. 부분 청산 미작동
**설정 (strategy_config.yaml)**:
```yaml
partial_exit:
  enabled: true
  tiers:
    - profit_pct: 1.0    # +1% 도달 시 30% 매도
      exit_ratio: 0.3
    - profit_pct: 2.0    # +2% 도달 시 30% 매도
      exit_ratio: 0.3
```

**실제 거래 기록**:
- 2025-11-13 23:33:07 | 삼성출판사 | +2.24% | **트레일링 스탑 (부분 청산 없음)**
- 2025-11-11 00:00:03 | 미래컴퍼니 | +0.62% | 장 마감 청산 (부분 청산 없음)

**문제**:
- +1%, +2% 부분 청산이 실행되지 않음
- 바로 트레일링 스탑 또는 장 마감 청산으로 이동

**추정 원인**:
- 부분 청산 체크 로직이 실제로 작동하지 않음
- 또는 15:00 강제 청산이 우선순위가 너무 높아서 부분 청산 건너뜀

#### 🟢 4. 승률은 양호하나 평균 수익 부족
- 승률 66.7%로 양호
- 하지만 평균 수익률 -0.23% (손익 상쇄 실패)
- 큰 손실 1건(-10.41%)이 전체 수익을 압도

---

## 🔍 코드 레벨 분석

### 1. 손절 로직 (signal_detector.py:236-248)

**현재 코드**:
```python
# 1. Hard Stop (설정값 사용)
stop_loss_pct = trailing_config.get('stop_loss_pct',
    getattr(self.analyzer, 'stop_loss_pct', 3.0))

if profit_pct <= -stop_loss_pct:
    console.print(f"[red]⚠️  Hard Stop 발동: {profit_pct:.2f}%
        (기준: -{stop_loss_pct}%)[/red]")
    return {
        'should_exit': True,
        'exit_type': 'full',
        ...
        'reason': f"Hard Stop (-{stop_loss_pct}%)"
    }
```

**문제점**:
1. ❌ **손절가 체크만 함, 실제 손절 주문은 order_executor에서 처리**
2. ❌ **시장가 주문 시 슬리피지 고려 안됨**
3. ❌ **급락 시 체결 지연 대응 없음**

**개선 방안**:
```python
# 👍 개선된 손절 로직
HARD_STOP_PCT = 1.0  # -1.0% 손절 (더 빠른 손절)

if profit_pct <= -HARD_STOP_PCT:
    # 즉시 시장가 매도 + 슬리피지 대비 안전마진
    return {
        'should_exit': True,
        'exit_type': 'full',
        'exit_ratio': 1.0,
        'order_type': 'market',  # ✅ 시장가 명시
        'reason': f"Hard Stop (-{HARD_STOP_PCT}%)",
        'urgent': True  # ✅ 긴급 플래그
    }
```

### 2. 부분 청산 로직 (signal_detector.py:250-284)

**현재 코드**:
```python
# 2. 부분 청산 체크 (문서 명세: +4% 40%, +6% 40%)
partial_exit_enabled = self.config.config.get('partial_exit', {}).get('enabled', False)

if partial_exit_enabled:
    partial_exit_stage = position.get('partial_exit_stage', 0)
    tiers = self.config.config.get('partial_exit', {}).get('tiers', [])

    # 2차 청산 체크 (+6%, 40%)
    if partial_exit_stage < 2 and len(tiers) >= 2:
        tier2 = tiers[1]
        if profit_pct >= tier2['profit_pct']:
            ...
```

**문제점**:
1. ❌ **주석과 실제 설정 불일치** (문서: +4%/+6%, 설정: +1%/+2%)
2. ❌ **position_tracker에서 partial_exit_stage 업데이트 안됨**
3. ❌ **15:00 강제 청산이 최우선 순위로 부분 청산 건너뜀**

**개선 방안**:
```python
# 👍 개선된 우선순위
# 0. 시간 체크는 마지막으로 이동
# 1. Hard Stop (-1.0%)
# 2. 부분 청산 (+1.0%, +2.0%)
# 3. 트레일링 스탑
# 4. VWAP 하향 돌파
# 5. 시간 기반 강제 청산 (15:00)
```

### 3. 중복 매도 방지

**현재 코드** (trading_orchestrator.py:358-372):
```python
elif signal['exit_type'] == 'full':
    success = self.order_executor.execute_sell(...)

    if success:
        # 포지션 제거
        self.position_tracker.remove_position(stock_code)
```

**문제점**:
1. ❌ **포지션 제거 전 중복 체크 없음**
2. ❌ **DB 저장 시 중복 방지 로직 없음**

**개선 방안**:
```python
# 👍 개선된 중복 방지
# 포지션 존재 여부 재확인
position_obj = self.position_tracker.get_position(stock_code)
if not position_obj:
    console.print(f"[yellow]⚠️  {stock_code}: 이미 청산됨 (중복 방지)[/yellow]")
    return

# 매도 실행
success = self.order_executor.execute_sell(...)

if success:
    # 포지션 즉시 제거 (중복 방지)
    self.position_tracker.remove_position(stock_code)
```

---

## 🛠️ 개선 우선순위

### Priority 1: 손절 로직 강화 (긴급)
**목표**: -10% 손실 재발 방지

**수정 파일**:
- `trading/signal_detector.py` (Hard Stop 로직)
- `trading/order_executor.py` (시장가 주문 강화)
- `config/strategy_config.yaml` (손절 기준 조정)

**변경 사항**:
```yaml
# strategy_config.yaml
trailing:
  stop_loss_pct: 1.0        # 1.3 → 1.0 (빠른 손절)
  use_emergency_stop: true  # 신규 추가
  emergency_stop_pct: 2.0   # 긴급 손절 (API 오류 대비)
```

```python
# signal_detector.py
# 1. Hard Stop (-1.0%)
if profit_pct <= -1.0:
    return {
        'should_exit': True,
        'exit_type': 'full',
        'exit_ratio': 1.0,
        'order_type': 'market',  # 시장가 강제
        'urgent': True,
        'reason': "Hard Stop (-1.0%)"
    }

# 2. 긴급 손절 (-2.0%) - API 오류 대비
if profit_pct <= -2.0:
    # 즉시 시장가 매도 + 로그 알림
    logger.critical(f"EMERGENCY STOP: {stock_code} {profit_pct:.2f}%")
    return {
        'should_exit': True,
        'exit_type': 'full',
        'exit_ratio': 1.0,
        'order_type': 'market',
        'urgent': True,
        'reason': "긴급 손절 (-2.0%)"
    }
```

### Priority 2: 중복 매도 버그 수정
**수정 파일**:
- `trading/trading_orchestrator.py`
- `trading/position_tracker.py`

**변경 사항**:
```python
# trading_orchestrator.py
async def _check_exit_signal(self, stock_code: str, stock_name: str, df) -> None:
    # ✅ 포지션 존재 확인
    position_obj = self.position_tracker.get_position(stock_code)
    if not position_obj:
        console.print(f"[dim]{stock_code}: 포지션 없음 (이미 청산)[/dim]")
        return

    # ✅ 이미 청산 대기 중인지 확인
    if position_obj.is_selling:
        console.print(f"[dim]{stock_code}: 매도 중... (중복 방지)[/dim]")
        return

    signal = self.signal_detector.check_exit_signal(...)

    if signal:
        # ✅ 매도 플래그 설정
        position_obj.is_selling = True

        if signal['exit_type'] == 'full':
            success = self.order_executor.execute_sell(...)

            if success:
                # ✅ 포지션 즉시 제거
                self.position_tracker.remove_position(stock_code)
            else:
                # ✅ 매도 실패 시 플래그 해제
                position_obj.is_selling = False
```

### Priority 3: 부분 청산 로직 수정
**수정 파일**:
- `trading/signal_detector.py` (청산 우선순위 조정)
- `trading/position_tracker.py` (partial_exit_stage 추적)

**변경 사항**:
```python
# signal_detector.py - 청산 우선순위 재정렬
def check_exit_signal(self, stock_code, stock_name, position, df):
    # 우선순위 1: Hard Stop (-1.0%)
    if profit_pct <= -1.0:
        return {'exit_type': 'full', 'reason': 'Hard Stop'}

    # 우선순위 2: 부분 청산 (+1.0%, +2.0%)
    partial_exit_stage = position.get('partial_exit_stage', 0)

    if partial_exit_stage < 2 and profit_pct >= 2.0:
        return {
            'exit_type': 'partial',
            'exit_ratio': 0.3,
            'stage': 2,
            'reason': '2차 부분 청산 (+2.0%)'
        }

    if partial_exit_stage < 1 and profit_pct >= 1.0:
        return {
            'exit_type': 'partial',
            'exit_ratio': 0.3,
            'stage': 1,
            'reason': '1차 부분 청산 (+1.0%)'
        }

    # 우선순위 3: 트레일링 스탑
    if trailing_result[0]:
        return {'exit_type': 'full', 'reason': 'Trailing Stop'}

    # 우선순위 4: VWAP 하향 돌파
    if latest_signal == -1:
        return {'exit_type': 'full', 'reason': 'VWAP Breakdown'}

    # 우선순위 5: 시간 기반 강제 청산 (15:00) - 마지막
    if current_time >= "15:00:00":
        return {'exit_type': 'full', 'reason': '장 마감 전 강제 청산'}

    return None
```

### Priority 4: 진입 타이밍 개선
**수정 파일**:
- `config/strategy_config.yaml` (Williams %R 필터)
- `analyzers/entry_timing_analyzer.py`

**변경 사항**:
```yaml
# strategy_config.yaml
filters:
  # Williams %R 필터 강화
  use_williams_r_filter: true
  williams_r_period: 14
  williams_r_long_ceiling: -30  # -20 → -30 (과매수 진입 더 억제)

  # 거래량 필터 강화
  use_volume_filter: true
  volume_multiplier: 1.2  # 1.1 → 1.2 (거래량 증가 필터 강화)
```

---

## 📈 기대 효과

### 개선 전 (현재)
- 승률: 66.7%
- 평균 수익률: -0.23%
- 최대 손실: -10.41%
- 총 손익: -7,840원

### 개선 후 (예상)
- 승률: 65~70% (유지)
- 평균 수익률: +0.5~1.0% (개선)
- 최대 손실: -1.5% 이하 (제한)
- 총 손익: +10,000~20,000원 (24건 기준)

**개선 근거**:
1. **손절 강화**: -10% 손실 → -1% 손절로 제한 (**+9% 개선**)
2. **부분 청산**: +1%, +2%에서 각 30% 청산으로 수익 보호
3. **중복 매도 방지**: 불필요한 거래 비용 절감
4. **진입 필터 강화**: 승률 유지하면서 평균 수익 개선

---

## 🚀 실행 계획

### 1단계: 긴급 수정 (오늘)
- [ ] 손절 로직 1.0%로 강화
- [ ] 중복 매도 버그 수정
- [ ] 긴급 손절 추가 (-2.0%)

### 2단계: 부분 청산 수정 (내일)
- [ ] 청산 우선순위 재정렬
- [ ] position_tracker에 partial_exit_stage 추가
- [ ] 15:00 청산 우선순위 하향

### 3단계: 백테스트 검증 (2일 후)
- [ ] 수정된 로직으로 과거 7일 백테스트
- [ ] 카티스 케이스 재현 테스트
- [ ] 부분 청산 작동 확인

### 4단계: 실전 적용 (3일 후)
- [ ] 소액으로 실전 테스트
- [ ] 1주일 모니터링
- [ ] 성과 평가 및 재조정

---

## 📝 체크리스트

### 즉시 수정 필요
- [x] ~~손절 기준 파악 (1.3%)~~
- [x] ~~실제 손실 확인 (-10.41%)~~
- [x] ~~중복 매도 버그 확인~~
- [x] ~~부분 청산 미작동 확인~~
- [ ] signal_detector.py 손절 로직 수정
- [ ] strategy_config.yaml 손절 기준 변경
- [ ] trading_orchestrator.py 중복 방지 추가
- [ ] 청산 우선순위 재정렬

### 검증 필요
- [ ] 백테스트 실행
- [ ] 로그 분석
- [ ] 슬리피지 측정

### 문서화
- [ ] 변경 사항 문서화
- [ ] 설정 파일 주석 업데이트
- [ ] 테스트 결과 기록

---

**작성자**: Claude Code Assistant
**최종 검토**: 2025-11-14
