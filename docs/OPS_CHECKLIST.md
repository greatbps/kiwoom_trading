# 실전 운영 체크리스트 v1.1
> 매일 장 진행 중 사용. 체크 후 OPERATIONS_LOG.md 일별 행에 요약 기입.

---

## 7/3 ~ (Daily)

| 시각 | 확인 항목 | 기준 | 판정 | 비고 |
|---|---|---|---|---|
| **08:50** | `[SYSTEM_START]` 로그 출력 | 있으면 OK | □ | |
| **08:50** | `[MKT_CTX]` 캐시 리셋 출력 | 있으면 OK | □ | |
| **08:50** | 연패 쿨다운 재적용 | `[TRADE_CD]` 로그 | □ | 쿨다운 잔여 횟수 |
| **09:30** | MKT_CTX 판정 | TRADE_OK / NO_TRADE_DAY | □ | 판정 근거 기록 (예: KOSPI LH+LL) |
| **09:30~** | E1 신규 진입 제한 검증 | 3번째 후보 시 TRADE_CD 차단 → OK, 미발생 → N/A | □ | 당일 후보 수: _건 |
| **15:35** | swing_runner 자동 실행 | `swing_runner.log`에 오늘 날짜 실행 로그 | □ | |
| **15:35** | `swing_orders_YYYY-MM-DD.json` 생성 | 오늘 날짜 파일 존재 | □ | |
| **15:35** | JSON 구조 유효 | 파싱 가능 + 필수 키 존재 | □ | 건수: _건 |
| **15:35** | traceback 없음 | Exception/Traceback 없음 | □ | |
| **익일 09:00** | swing_executor 파일 소비 | 오늘 생성 파일 로드 로그 | □ | 처리 결과(성공/실패) |

> **판정**: OK / FAIL / N/A
> **N/A 허용 항목**: E1 신규 진입 제한 (후보 3건 미발생 시)

---

## 빠른 확인 명령

```bash
# 08:50 초기화 확인
grep "SYSTEM_START\|캐시 리셋\|TRADE_CD" logs/auto_trading_20260701.log | grep "$(date +%Y-%m-%d) 08:5"

# 09:30 MKT_CTX 판정 (근거 포함)
grep "MKT_CTX.*상태=" logs/auto_trading_20260701.log | grep "$(date +%Y-%m-%d) 09:3"

# 장중 CANDIDATE_ACCEPT 후보 수 확인
grep "CANDIDATE_ACCEPT" logs/signal_orchestrator.log | grep "$(date +%Y-%m-%d)" | wc -l

# 15:35 swing_runner 실행 로그
grep "$(date +%Y-%m-%d)" logs/swing_runner.log 2>/dev/null | tail -5

# 15:35 JSON 유효성 확인
python3 - <<'PY'
import json, sys
p = f"logs/swing_orders_{__import__('datetime').date.today()}.json"
try:
    with open(p, encoding="utf-8") as f:
        data = json.load(f)
    n = len(data) if hasattr(data, "__len__") else "NA"
    print(f"JSON_OK  type={type(data).__name__}  items={n}")
    if isinstance(data, list) and data:
        print(f"  키: {list(data[0].keys())}")
except FileNotFoundError:
    print("파일 없음 (미생성 또는 미실행)")
except Exception as e:
    print(f"JSON_FAIL  {e}")
PY

# 익일 09:00 executor 소비 확인
grep "swing_orders_$(date +%Y-%m-%d)" logs/swing_executor.log 2>/dev/null | tail -3
```

---

## 판정 기준

| 항목 | OK | FAIL | N/A |
|---|---|---|---|
| 08:50 초기화 3종 | 로그 있음 | 로그 없음 | — |
| 09:30 MKT_CTX | 로그 있음 (값 무관) | 로그 없음 | — |
| E1 진입 제한 | 3번째 차단 확인됨 | 3번째 통과됨 | 후보 3건 미발생 |
| swing_runner | 실행 + JSON 정상 | 미실행 or JSON_FAIL | — |
| traceback | 없음 | 있음 | — |
| executor | 로드 로그 있음 | 미실행 or 오류 | — |
