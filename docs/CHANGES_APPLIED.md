# 프로그램 수정 완료 보고서

**적용 일시**: 2025-11-28
**수정 목적**: 중복 프로세스 방지 및 쿨다운 동기화

---

## ✅ 적용된 수정사항

### 1. PID Lock File 구현 (Priority 1)

**파일**: `main_auto_trading.py`

**변경 내용**:
- 새 함수 추가: `check_and_create_pid_lock()` (line 3469-3509)
- PID lock file 경로: `/tmp/kiwoom_trading.pid`
- 중복 실행 시 에러 메시지 출력 및 종료
- 프로세스 종료 시 자동으로 PID 파일 삭제 (atexit)

**효과**:
```python
# 실행 시:
✓ PID lock 생성 완료 (PID: 12345)

# 중복 실행 시:
❌ 이미 실행 중입니다! (PID: 12345)
기존 프로세스를 종료하려면: kill 12345
또는: pkill -f 'main_auto_trading.py'
```

**코드 위치**:
- 함수 정의: line 3469-3509
- 호출: line 3664-3666 (if __name__ == "__main__" 블록)

---

### 2. run.sh 중복 프로세스 체크 (Priority 1)

**파일**: `run.sh`

**변경 내용**:
- 실행 전 기존 프로세스 확인 로직 추가 (line 82-116)
- 사용자 선택 UI 제공:
  - 1) 기존 프로세스 종료 후 재시작
  - 2) 취소 (기존 프로세스 유지)
- 강제 종료 (kill -9) 폴백 로직 포함

**실행 흐름**:
```bash
[5/6] 기존 프로세스 확인 중...

# 중복 발견 시:
❌ 이미 실행 중인 프로세스 발견! (PID: 12345)
다음 중 선택하세요:
  1) 기존 프로세스 종료하고 재시작
  2) 취소 (기존 프로세스 유지)
선택 (1/2): 1

기존 프로세스 종료 중...
✓ 프로세스 종료 완료

[6/6] 실전 자동매매를 시작합니다...
```

---

### 3. 쿨다운 파일 기반 동기화 (Priority 2)

**파일**: `core/risk_manager.py`

**변경 내용**:
- `can_open_position()` 함수에 파일 기반 쿨다운 체크 추가 (line 103-138)
- 쿨다운 파일 경로: `data/cooldown.lock`
- 프로세스 간 공유 가능한 쿨다운 상태
- 메모리 쿨다운과 파일 쿨다운 둘 다 체크 (하위 호환성)

**쿨다운 파일 형식**:
```json
{
  "stock_code": "009420",
  "stock_name": "한올바이오파마",
  "triggered_at": "2025-11-28T11:01:47.635146",
  "cooldown_until": "2025-11-29T11:01:47.635146",
  "consecutive_losses": 3,
  "reason": "3회 연속 손실"
}
```

**효과**:
- 여러 프로세스가 동시 실행되어도 쿨다운 상태 공유
- 쿨다운 기간 만료 시 자동으로 파일 삭제 및 해제

---

**파일**: `main_auto_trading.py`

**변경 내용**:
- 3회 연속 손실 시 쿨다운 파일 생성 (line 3149-3169)
- 콘솔 메시지 추가: "🔒 쿨다운 활성화: YYYY-MM-DD까지 모든 거래 중지"

**트리거 조건**:
- 동일 종목 3회 연속 손실 시
- 다음날까지 모든 신규 거래 중지

---

### 4. 로깅 개선 - process_id 추가 (Priority 2)

**파일**: `main_auto_trading.py`

**변경 내용**:
- `execute_buy()` trade_data에 필드 추가 (line 2816-2817):
  - `process_id`: 거래 실행 프로세스 ID
  - `order_no`: 키움 주문번호

**효과**:
- 어느 프로세스가 어떤 거래를 실행했는지 추적 가능
- 중복 매수 발생 시 원인 분석 용이

---

**파일**: `analyzers/signal_orchestrator.py`

**변경 내용**:
- ACCEPT 로그에 PID 추가 (line 580-583):
  ```python
  ✅ ACCEPT 318060 @19750원 | PID:151120 | conf=0.48 alpha=+1.38 pos_mult=0.40
  ```

- REJECT 로그에 PID 추가 (line 453-456):
  ```python
  ❌ REJECT 318060 | PID:151120 | L0 | 시간 제한
  ```

**효과**:
- 필터 로그에서 프로세스 식별 가능
- 다중 프로세스 환경에서 디버깅 용이

---

## 📊 수정 전/후 비교

### 문제 상황 (수정 전)
```
10:57 - 백그라운드 프로세스 시작 (PID 151120)
11:30 - 사용자가 터미널에서 중복 실행 (PID 151781)
11:30 - 009420 이중 매수 (1주 + 1주)
11:01 - Early Failure Cut -600원 (2주 매도)

문제:
- 중복 프로세스 허용
- 이중 매수로 인한 손실
- 쿨다운 상태 불일치
- 프로세스 추적 불가
```

### 해결 (수정 후)
```
✅ PID lock으로 중복 실행 차단
✅ run.sh에서 사전 확인 및 선택 UI
✅ 쿨다운 파일로 프로세스 간 동기화
✅ 모든 로그에 PID 포함 (추적 가능)

예상 효과:
- 중복 프로세스 원천 차단
- 이중 매수 방지
- 쿨다운 상태 일관성 보장
- 디버깅 및 문제 추적 용이
```

---

## 🔍 테스트 방법

### 1. PID Lock 테스트
```bash
# 터미널 1
python3 main_auto_trading.py --live --conditions 17,18,19,20,21,22

# 터미널 2 (중복 실행 시도)
python3 main_auto_trading.py --live --conditions 17,18,19,20,21,22
# 예상 결과: ❌ 이미 실행 중입니다! (PID: xxxx)
```

### 2. run.sh 중복 체크 테스트
```bash
# 백그라운드 실행 후
nohup ./run.sh > nohup.out 2>&1 &

# 다시 실행
./run.sh
# 예상 결과: 선택 UI 표시
```

### 3. 쿨다운 파일 확인
```bash
# 3회 연속 손실 발생 시
cat data/cooldown.lock
# 예상 결과: JSON 형식 쿨다운 정보
```

### 4. 로그 확인
```bash
# process_id 포함 확인
grep "PID:" logs/signal_orchestrator.log | tail -5
```

---

## 📁 수정된 파일 목록

1. ✅ `main_auto_trading.py`
   - check_and_create_pid_lock() 함수 추가
   - 쿨다운 파일 생성 로직 추가
   - trade_data에 process_id, order_no 추가

2. ✅ `run.sh`
   - 중복 프로세스 체크 로직 추가
   - 사용자 선택 UI 추가

3. ✅ `core/risk_manager.py`
   - 파일 기반 쿨다운 체크 추가
   - 쿨다운 만료 시 자동 해제

4. ✅ `analyzers/signal_orchestrator.py`
   - ACCEPT/REJECT 로그에 PID 추가

---

## ⚠️ 주의사항

### 재시작 필요
현재 실행 중인 프로세스는 **구버전 코드**를 사용 중입니다.
수정사항을 적용하려면 **프로세스 재시작 필수**:

```bash
# 현재 프로세스 종료
pkill -f "main_auto_trading.py"

# 재시작
./run.sh
```

### PID 파일 위치
- 경로: `/tmp/kiwoom_trading.pid`
- 수동 삭제 가능: `rm /tmp/kiwoom_trading.pid`

### 쿨다운 파일 위치
- 경로: `data/cooldown.lock`
- 수동 해제 가능: `rm data/cooldown.lock`

---

## 📈 기대 효과

1. **중복 매수 방지**: 100% 차단
2. **손실 감소**: 이중 매수로 인한 손실 제거
3. **안정성 향상**: 프로세스 관리 체계화
4. **디버깅 개선**: PID 추적으로 문제 원인 빠른 파악

---

**최종 검증**: 재시작 후 정상 작동 확인 필요
**다음 단계**: Priority 3 (CONFIDENCE 통계 분석) 검토
