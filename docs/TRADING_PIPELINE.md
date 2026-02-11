# 자동매매 시스템 파이프라인 (2026-01-23 기준)

## 현재 설정

| 항목 | 값 |
|------|-----|
| **entry_mode** | `smc` (Smart Money Concepts) |
| **require_liquidity_sweep** | `false` (CHoCH만으로 진입) |
| **손절** | -1.5% (당일매수) / -2.0% (일반) |
| **트레일링** | +1.5% 활성화 → -1.0% 추적 |

---

## 1. 일일 스케줄

```
08:50  ┌─ 시스템 시작
       │   ├─ WebSocket 연결
       │   ├─ Token 검증
       │   ├─ 계좌 정보 로드
       │   └─ 조건식 목록 조회
       │
08:50  ├─ [1단계] 조건검색 필터링
~09:00 │   ├─ 조건식 17~23번 스캔
       │   ├─ 1차 필터 (가격, 거래량)
       │   └─ watchlist 생성
       │
09:00  ├─ [2단계] 갭업 재진입 체크
       │   └─ 전일 우선감시 종목 갭업 확인
       │
09:00  ├─ [3단계] 실시간 모니터링 시작
~15:30 │   ├─ 1분마다: 종목 체크 (진입/청산)
       │   ├─ 5분마다: 조건검색 재실행 (신규 종목 추가)
       │   ├─ 5분마다: 거래내역 동기화
       │   └─ 14:55~14:59: EOD 프로세스
       │
15:30  └─ 거래 종료 → 내일 08:50까지 대기
```

---

## 2. 진입 파이프라인 (SMC 모드)

### 2.1 전체 흐름

```
조건검색 신호 발생
        │
        ▼
┌───────────────────────────┐
│ [L0] 시스템/시간 필터     │
│   • 진입 시간: 10:00~14:59│
│   • 점심시간: 제한 없음   │
│   • 당일 손실 한도 체크   │
└───────────┬───────────────┘
            ▼
┌───────────────────────────┐
│ [데이터 수집]             │
│   • 키움 1분봉 100개      │
│   • VWAP, ATR 계산        │
│   • ATR > 5% → 차단       │
└───────────┬───────────────┘
            ▼
┌───────────────────────────┐
│ [5분봉 리샘플링]          │
│   • 1분봉 → 5분봉 변환    │
│   • 최소 50개 봉 필요     │
└───────────┬───────────────┘
            ▼
┌───────────────────────────┐
│ [BB30 관측] (로깅만)      │  ← NEW: 진입 X, 관측만
│   • BB(30,1) 돌파 체크    │
│   • logs/bb30_observation │
└───────────┬───────────────┘
            ▼
┌───────────────────────────┐
│ [SMC 전략 분석]           │  ← 핵심 진입 로직
│   • CHoCH 탐지            │
│   • Liquidity Sweep 확인  │
│   • Order Block 식별      │
│   • 방향: long만 허용     │
└───────────┬───────────────┘
            │
       ┌────┴────┐
       │ 신호?   │
       └────┬────┘
      No    │    Yes
       │    ▼
       │  ┌─────────────────────┐
       │  │ [포지션 계산]       │
       │  │   • 신뢰도 기반     │
       │  │   • 최대 40% 한도   │
       │  └──────────┬──────────┘
       │             ▼
       │  ┌─────────────────────┐
       │  │ [매수 주문 실행]    │
       │  │   • 지정가 주문     │
       │  │   • DB 기록         │
       │  └─────────────────────┘
       ▼
     종료
```

### 2.2 SMC 전략 상세

```python
# SMC (Smart Money Concepts) 핵심 로직

1. Market Structure 분석
   - HH (Higher High): 고점 갱신
   - HL (Higher Low): 저점 상승
   - LH (Lower High): 고점 하락
   - LL (Lower Low): 저점 갱신

2. CHoCH (Change of Character) 탐지
   - 상승 전환: 하락 추세 중 첫 HL 출현
   - 진입 조건: CHoCH bullish 확인

3. Liquidity Sweep (옵션)
   - require_liquidity_sweep: false
   - CHoCH만으로 진입 가능

4. Order Block
   - CHoCH 직전 캔들 영역
   - 지지/저항으로 활용
```

---

## 3. 청산 파이프라인

### 3.1 청산 우선순위

```
[우선순위 -1] 동적 락 (30분 기본)
     │
     │  ✗ 청산 불가 (복합 붕괴 예외만 허용)
     ▼
[우선순위 0] Early Failure Cut
     │
     │  30분 이내 + 손실 -1.6% 이하 → 시장가 청산
     ▼
[우선순위 1] Hard Stop (절대 손절)
     │
     │  당일 매수: -1.5%
     │  일반:     -2.0%
     │  → 시장가 즉시 청산
     ▼
[우선순위 2] 트레일링 스탑
     │
     │  활성화: +1.5% 도달
     │  추적:   고점 대비 -1.0%
     │  고수익: +6% 이상 → 추적 -0.5%
     ▼
[우선순위 3] 전략별 청산 (SMC/MA/Squeeze)
     │
     │  SMC: CHoCH bearish 감지
     │  MA Cross: 데드크로스
     │  Squeeze: DR/BR 전환
     ▼
[우선순위 4] VWAP 이탈 (복합 조건)
     │
     │  VWAP -0.5% 이탈 + 체결강도 < 80%
     ▼
[우선순위 5] 시간 청산 (비활성화)
     │
     │  eod_policy.enabled: false
     ▼
청산 안 함 → 보유 유지
```

### 3.2 청산 로직 상세

```python
# 1. 동적 min_hold_time 계산
def calculate_dynamic_min_hold_time():
    # 변동성 기반 (ATR)
    # ATR 0.5% → 30분
    # ATR 0.25% → 60분
    # ATR 1.0% → 15분

    # Squeeze 상태 보정
    # BG/DG → 1.5배 연장

# 2. 당일 매수 강화 손절
if is_same_day_entry:
    hard_stop = 1.5%      # (기본 2.0%)
    trailing = 0.8%       # (기본 1.0%)

# 3. 부분 청산 후 손절 상향
if partial_stage >= 1:
    hard_stop = 0.3%      # 사실상 BE
if partial_stage >= 2:
    hard_stop = -0.2%     # 익절 보장
```

---

## 4. 주요 컴포넌트

### 4.1 파일 구조

```
main_auto_trading.py          # 메인 루프
├── daily_routine()           # 일일 스케줄 관리
├── run_condition_filtering() # 조건검색 실행
├── monitor_and_trade()       # 실시간 모니터링
├── check_entry_signal()      # 진입 신호 체크 (모드별 분기)
├── check_exit_signal_internal() # 청산 신호 체크
└── execute_sell()            # 매도 실행

analyzers/
├── smc/                      # SMC 전략
│   ├── smc_utils.py          # 스윙 탐지
│   ├── smc_structure.py      # BOS/CHoCH
│   └── smc_signals.py        # 최종 신호
├── bb30_observer.py          # BB(30,1) 관측 (NEW)
├── squeeze_momentum_lazybear.py  # Squeeze 전략
└── signal_orchestrator.py    # L0-L6 필터

trading/
├── exit_logic_optimized.py   # 청산 로직
├── risk_manager.py           # 리스크 관리
└── dynamic_weight_adjuster.py # 동적 가중치

config/
└── strategy_hybrid.yaml      # 전략 설정
```

### 4.2 entry_mode 옵션

| 모드 | 설명 | 진입 조건 |
|------|------|-----------|
| `smc` | **현재 사용 중** | CHoCH + (옵션: Liquidity Sweep) |
| `squeeze_2tf` | 2-타임프레임 | 30분봉 골든크로스 + Squeeze + 5분봉 |
| `ma_cross` | MA 교차 | 5분봉 MA5/MA10 골든크로스 |
| `squeeze_only` | Squeeze 전용 | Bright Green + 모멘텀 상승 |
| `hybrid` | 하이브리드 | L0-L6 필터 + Squeeze |
| `legacy_only` | 레거시 | L0-L6 필터만 |

---

## 5. 리스크 관리

### 5.1 포지션 제한

| 항목 | 값 |
|------|-----|
| 최대 동시 보유 | 3개 |
| 포지션당 최대 | 40% (50만원 hard limit) |
| 최소 현금 보유 | 10% |
| 일일 최대 거래 | 15회 |
| 연속 손실 시 | 당일 중지 (3회) |

### 5.2 시간 필터

| 시간대 | 상태 |
|--------|------|
| 09:00~10:00 | 진입 차단 (장 초반 변동성) |
| 10:00~10:30 | **골든타임** (포지션 1.2배) |
| 10:00~12:00 | 정상 진입 |
| 12:00~13:00 | squeeze_2tf만 차단 |
| 13:00~14:59 | 정상 진입 |
| 14:59 이후 | 진입 차단 |

---

## 6. 로그 파일

| 파일 | 내용 |
|------|------|
| `logs/signal_orchestrator.log` | 진입/차단 신호 |
| `logs/bb30_observation.log` | BB(30,1) 관측 (NEW) |
| `logs/auto_trading_errors.log` | 에러 로그 |
| `data/debug_log.txt` | 디버그 로그 |
| `data/risk_log.json` | 리스크 관리 이력 |

---

## 7. 실행 명령

```bash
# 일반 실행
./run.sh

# 또는 직접 실행
python3 main_auto_trading.py --live --conditions 17,18,19,20,21,22,23
```

---

## 변경 이력

| 날짜 | 변경 내용 |
|------|-----------|
| 2026-01-23 | BB(30,1) 관측 추가, SMC 모드 활성화 |
| 2026-01-20 | 당일 매수 강화 손절 추가 |
| 2026-01-17 | 점심시간 12:00~13:00으로 완화 |
