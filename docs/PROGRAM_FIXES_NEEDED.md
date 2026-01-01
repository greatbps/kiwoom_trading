# 프로그램 수정 필요사항

## 🚨 Priority 1: 중복 프로세스 방지

### 문제
- 동일 시스템에서 main_auto_trading.py가 중복 실행됨
- 같은 종목에 이중 매수 발생 (009420: 1주 → 2주)
- -600원 손실 발생

### 해결 방법 1: PID Lock File

**파일**: `main_auto_trading.py` 시작 부분에 추가

```python
import os
import sys
from pathlib import Path

def check_and_create_pid_lock():
    """
    PID lock file로 중복 실행 방지
    """
    pid_file = Path('/tmp/kiwoom_trading.pid')

    # 기존 PID 파일 확인
    if pid_file.exists():
        try:
            old_pid = int(pid_file.read_text().strip())
            # 프로세스가 실제로 실행 중인지 확인
            os.kill(old_pid, 0)  # 프로세스 존재 확인 (신호 전송 없음)

            # 프로세스가 살아있음
            console.print(f"[red]❌ 이미 실행 중입니다! (PID: {old_pid})[/red]")
            console.print(f"[yellow]기존 프로세스를 종료하려면: kill {old_pid}[/yellow]")
            sys.exit(1)

        except (ProcessLookupError, ValueError):
            # 프로세스가 죽었거나 PID 파일이 손상됨
            console.print(f"[yellow]⚠️  이전 PID 파일 정리 중...[/yellow]")
            pid_file.unlink()

    # 현재 PID 저장
    pid_file.write_text(str(os.getpid()))
    console.print(f"[green]✓ PID lock 생성 완료 (PID: {os.getpid()})[/green]")

    # 종료 시 PID 파일 삭제
    import atexit
    atexit.register(lambda: pid_file.unlink() if pid_file.exists() else None)

# main() 함수 시작 부분에 추가
if __name__ == "__main__":
    check_and_create_pid_lock()  # ← 이 줄 추가
    # ... 기존 코드 계속
```

### 해결 방법 2: run.sh 수정

**파일**: `run.sh` Line 82-87 수정

```bash
# 5. 실전 자동매매 실행 전 중복 체크
echo -e "${YELLOW}[5/6] 기존 프로세스 확인 중...${NC}"
EXISTING_PID=$(pgrep -f "main_auto_trading.py --live" || echo "")

if [ -n "$EXISTING_PID" ]; then
    echo -e "${RED}❌ 이미 실행 중인 프로세스 발견! (PID: $EXISTING_PID)${NC}"
    echo -e "${YELLOW}다음 중 선택하세요:${NC}"
    echo -e "  1) 기존 프로세스 종료하고 재시작"
    echo -e "  2) 취소"
    read -p "선택 (1/2): " choice

    if [ "$choice" = "1" ]; then
        echo -e "${YELLOW}기존 프로세스 종료 중...${NC}"
        kill $EXISTING_PID
        sleep 2
        echo -e "${GREEN}✓ 프로세스 종료 완료${NC}"
    else
        echo -e "${YELLOW}취소되었습니다.${NC}"
        exit 0
    fi
else
    echo -e "${GREEN}✓ 중복 프로세스 없음${NC}"
fi

echo ""
echo -e "${BLUE}=====================================${NC}"
echo -e "${BLUE}  모든 준비 완료!${NC}"
echo -e "${BLUE}=====================================${NC}"
echo ""

# 6. 실전 자동매매 실행
echo -e "${GREEN}실전 자동매매를 시작합니다...${NC}"
echo -e "${RED}※ 실제 계좌로 거래합니다! 주의하세요!${NC}"
echo -e "${YELLOW}※ 종료하려면 Ctrl+C를 누르세요${NC}"
echo ""
python3 main_auto_trading.py --live --conditions 17,18,19,20,21,22
```

---

## ⚠️ Priority 2: 쿨다운 상태 동기화

### 문제
- risk_log.json의 cooldown_until과 실제 동작 불일치
- 로그에는 "쿨다운 중" 메시지가 558회 출력되지만 거래는 계속 진행됨

### 해결 방법: 쿨다운 상태 파일

**파일**: `core/risk_manager.py` 수정

```python
def can_open_position(
    self,
    current_balance: float,
    current_positions_value: float,
    position_count: int,
    position_size: float
) -> tuple[bool, str]:
    """신규 포지션 진입 가능 여부 확인"""

    # 🔧 FIX: 쿨다운 체크를 파일 기반으로 변경
    cooldown_file = Path('data/cooldown.lock')

    if cooldown_file.exists():
        try:
            cooldown_data = json.loads(cooldown_file.read_text())
            cooldown_until = cooldown_data.get('cooldown_until')

            if cooldown_until:
                from datetime import datetime
                until_dt = datetime.fromisoformat(cooldown_until)

                if datetime.now() <= until_dt:
                    return False, f"연속 손실 쿨다운 중 (해제: {cooldown_until[:10]})"
                else:
                    # 쿨다운 기간 만료 → 파일 삭제
                    cooldown_file.unlink()
        except Exception as e:
            console.print(f"[yellow]⚠️  쿨다운 파일 읽기 실패: {e}[/yellow]")
            # 손상된 파일 삭제
            cooldown_file.unlink()

    # ... 기존 코드 계속
```

**쿨다운 활성화 코드 수정** (main_auto_trading.py):

```python
def _handle_consecutive_loss(self, stock_code: str, stock_name: str):
    """연속 손실 처리"""

    # 쿨다운 파일 생성
    from datetime import datetime, timedelta
    from pathlib import Path
    import json

    cooldown_file = Path('data/cooldown.lock')
    cooldown_until = (datetime.now() + timedelta(days=1)).isoformat()

    cooldown_data = {
        'stock_code': stock_code,
        'stock_name': stock_name,
        'triggered_at': datetime.now().isoformat(),
        'cooldown_until': cooldown_until,
        'consecutive_losses': self.stock_loss_count.get(stock_code, 0)
    }

    cooldown_file.write_text(json.dumps(cooldown_data, indent=2, ensure_ascii=False))
    console.print(f"[red]🚫 {stock_name}: 쿨다운 활성화 → {cooldown_until[:10]}까지[/red]")
```

---

## 📊 Priority 3: 로깅 개선

### 추가 정보 로깅

**파일**: `main_auto_trading.py` execute_buy() 수정

```python
def execute_buy(self, stock_code: str, stock_name: str, price: float, ...):
    """매수 실행"""

    # 프로세스 ID 추가
    process_id = os.getpid()

    trade_data = {
        'stock_code': stock_code,
        'stock_name': stock_name,
        'trade_type': 'BUY',
        'trade_time': entry_time.isoformat(),
        'price': float(price),
        'quantity': int(quantity),
        'amount': float(amount),
        'process_id': process_id,  # ← 추가
        'order_no': order_no,      # ← 추가
        # ... 기존 필드들
    }
```

**Signal Orchestrator 로그 개선**:

```python
# 프로세스 ID 포함
msg = f"✅ ACCEPT {stock_code} @{current_price:.0f}원 | PID:{os.getpid()} | conf={final_confidence:.2f} alpha={aggregate_score:+.2f}"
```

---

## 🔍 Priority 4: CONFIDENCE 분석 및 모니터링

### 현황
- CONFIDENCE 부족으로 490건 차단 (55%)
- MIN_CONFIDENCE = 0.4

### 모니터링 추가

**파일**: 새 파일 `scripts/analyze_confidence.py` 생성

```python
"""
CONFIDENCE 차단 통계 분석 스크립트
"""
import re
from pathlib import Path
from collections import defaultdict

log_file = Path('logs/signal_orchestrator.log')

# REJECT CONFIDENCE 분석
reject_conf = defaultdict(list)

for line in log_file.read_text().split('\n'):
    if '❌ REJECT' in line and 'CONFIDENCE' in line:
        # Extract: stock_code, confidence value
        match = re.search(r'(\d{6}) @(\d+)원.*confidence \((\d+\.\d+)', line)
        if match:
            stock_code, price, conf = match.groups()
            reject_conf[stock_code].append(float(conf))

# 통계 출력
print("=== CONFIDENCE 차단 통계 ===")
print(f"총 차단 건수: {sum(len(v) for v in reject_conf.values())}건")
print(f"차단된 종목 수: {len(reject_conf)}개")
print()

# 종목별 평균 confidence
print("종목별 평균 차단 Confidence:")
for code, confs in sorted(reject_conf.items(), key=lambda x: -len(x[1]))[:10]:
    avg_conf = sum(confs) / len(confs)
    print(f"  {code}: {len(confs):3}회 차단, 평균 {avg_conf:.3f}")

# 히스토그램
print("\nConfidence 분포:")
bins = [0.0, 0.1, 0.2, 0.3, 0.35, 0.38, 0.39, 0.4]
for i in range(len(bins)-1):
    count = sum(1 for confs in reject_conf.values() for c in confs if bins[i] <= c < bins[i+1])
    bar = '█' * (count // 10)
    print(f"  {bins[i]:.2f}-{bins[i+1]:.2f}: {count:3}건 {bar}")
```

실행:
```bash
python3 scripts/analyze_confidence.py
```

---

## ✅ 적용 우선순위

1. **즉시 적용** (오늘):
   - PID lock file 추가
   - run.sh 중복 프로세스 체크

2. **단기 적용** (1-2일 내):
   - 쿨다운 파일 기반 동기화
   - 로깅 개선 (process_id 추가)

3. **중장기 검토** (1주일 내):
   - CONFIDENCE 통계 분석
   - MIN_CONFIDENCE 조정 필요성 검토

---

**작성일**: 2025-11-28
**기반 데이터**: 14건 거래 분석
**예상 효과**: 중복 프로세스 방지로 의도치 않은 손실 제거
