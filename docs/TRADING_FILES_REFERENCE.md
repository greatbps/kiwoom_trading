# 매매 관련 핵심 파일 가이드

**작성일**: 2025-12-23
**목적**: GPT와의 논의 및 시스템 이해를 위한 파일 구조 설명

---

## 1. 메인 실행 파일

### `main_auto_trading.py` (4,590 lines)

**역할**: 통합 자동매매 시스템의 핵심 엔트리포인트

**주요 클래스**: `IntegratedTradingSystem`

**핵심 기능**:
```python
class IntegratedTradingSystem:
    def __init__(self, condition_indices, live_mode):
        # 초기화
        self.condition_indices = [17, 18, 19, 20, 21, 22, 23]
        self.bottom_manager = BottomPullbackManager()
        self.orchestrator = SignalOrchestrator()
        self.eod_manager = EODManager()

    async def daily_routine(self):
        # 일일 루틴 (08:50 ~ 15:30)
        await self.wait_until_time(8, 50)
        await self.search_and_filter_conditions()
        await self.real_time_monitoring()

    async def run(self):
        # 무한 반복 스케줄링
        while self.running:
            await self.daily_routine()
            await self.wait_until(next_day, 08:50)
```

**주요 메서드**:
- `search_and_filter_conditions()` - 조건검색 + L0-L6 필터링
- `real_time_monitoring()` - 실시간 모니터링 (60초 주기)
- `check_entry_signal()` - 매수 진입 체크
- `check_exit_conditions()` - 청산 조건 체크
- `wait_until_time()` - 적응형 대기 (1시간 → 10분 → 1분 → 10초)

**파일 위치**: `/home/greatbps/projects/kiwoom_trading/main_auto_trading.py`

---

## 2. 전략 관리 파일

### `trading/bottom_pullback_manager.py` (286 lines)

**역할**: Bottom Pullback 전략 (조건 23번) 상태 관리

**핵심 클래스**: `BottomPullbackManager`

**상태 관리**:
```python
self.signals = {
    stock_code: {
        'state': 'WAIT_PULLBACK' | 'PULLBACK_DETECTED' | 'READY_TO_ENTER' | 'IN_POSITION' | 'INVALIDATED',
        'signal_date': date,
        'signal_low': float,
        'signal_vwap': float,
        'below_vwap_detected': bool,
        'pullback_used': bool,
    }
}
```

**핵심 메서드**:
- `register_signal()` - 조건검색 신호 등록
- `check_pullback()` - Pullback 조건 체크
- `mark_entered()` - 매수 진입 완료 표시
- `_invalidate_signal()` - 신호 무효화
- `reset_daily()` - 일일 리셋

**파일 위치**: `/home/greatbps/projects/kiwoom_trading/trading/bottom_pullback_manager.py`

---

### `trading/signal_orchestrator.py`

**역할**: L0-L6 필터 파이프라인 관리

**필터 순서**:
```
L0: 일일 손실 제한 체크
L1: 거래량 필터 (1.5x 이상)
L2: 변동성 필터 (ATR 0.8% ~ 4.0%)
L3: 시간대 필터 (점심시간 차단)
L4: RS 필터 (상대강도)
L5: Squeeze 필터 (변동성 축소)
L6: 리스크 검증 (승률, PF, Multi-Alpha)
```

**파일 위치**: `/home/greatbps/projects/kiwoom_trading/trading/signal_orchestrator.py`

---

### `trading/eod_manager.py`

**역할**: End-of-Day 정책 관리

**핵심 기능**:
- 14:55 익일 보유 판단
- 15:05 강제 청산
- EOD 점수 계산 (추세, 거래량, 뉴스)
- 우선 감시 리스트 생성

**파일 위치**: `/home/greatbps/projects/kiwoom_trading/trading/eod_manager.py`

---

## 3. 설정 파일

### `config/strategy_hybrid.yaml`

**역할**: 전략 파라미터 중앙 관리

**주요 섹션**:
```yaml
# 트레일링 스톱
trailing:
  activation_pct: 1.5
  ratio: 1.0
  stop_loss_pct: 3.0
  use_atr_based: true

# 필터 설정
filters:
  use_volume_filter: true
  volume_multiplier: 1.5
  use_volatility_filter: true
  min_atr_pct: 0.8
  max_atr_pct: 4.0

# 시간 필터
time_filter:
  avoid_early_minutes: 30      # 09:30까지 회피
  avoid_late_minutes: 21       # 14:59까지만 진입
  time_weight:
    morning_penalty: 0.5       # 오전 가중치 감소
    midday_penalty: 0.0        # 점심시간 완전 차단
    afternoon_bonus: 1.5       # 오후 가중치 증가

# 리스크 관리
risk_management:
  max_positions: 3
  position_risk_pct: 1.0
  hard_max_position: 300000

# 부분 청산
partial_exit:
  enabled: true
  tiers:
    - profit_pct: 1.5
      exit_ratio: 0.3          # +1.5%에 30% 청산
    - profit_pct: 2.5
      exit_ratio: 0.3          # +2.5%에 30% 청산
    # 나머지 40%는 트레일링 스톱

# 조건 전략
condition_strategies:
  momentum:
    condition_indices: [17, 18, 19, 20, 21, 22]
    immediate_entry: true

  bottom_pullback:
    condition_indices: [23]
    immediate_entry: false
    wait_for_pullback: true
    pullback:
      type: "vwap_reclaim"
      reclaim_conditions:
        min_volume_ratio: 1.0
      invalidation:
        break_signal_low_pct: -0.5
        max_wait_minutes: 180
      time_window:
        start: "09:30"
        end: "14:30"

# EOD 정책
eod_policy:
  enabled: true
  check_time: "14:55:00"
  force_exit_time: "15:05:00"
  max_overnight_positions: 3
  min_overnight_score: 0.6
```

**파일 위치**: `/home/greatbps/projects/kiwoom_trading/config/strategy_hybrid.yaml`

---

## 4. 실행 스크립트

### `run.sh` (410 lines)

**역할**: 통합 실행 및 관리 스크립트

**주요 함수**:
```bash
setup_environment()           # 가상환경, 패키지, .env 체크
start_trading()               # 포그라운드 실행
start_trading_background()    # 백그라운드 실행 (nohup)
analyze_today()               # 오늘 거래 분석
check_process()               # 프로세스 확인
stop_process()                # 프로세스 중지
```

**명령어**:
```bash
./run.sh                      # 인터랙티브 메뉴
./run.sh start                # 포그라운드 시작
./run.sh bg                   # 백그라운드 시작
./run.sh status               # 상태 확인
./run.sh stop                 # 중지
./run.sh analyze              # 오늘 분석
./run.sh analyze-date 2025-12-22  # 특정 날짜 분석
```

**파일 위치**: `/home/greatbps/projects/kiwoom_trading/run.sh`

---

## 5. 분석 스크립트

### `analyze_daily_trades.py`

**역할**: 일별 거래 분석

**출력**:
- 거래 요약 (건수, P&L)
- 거래 내역 테이블
- GPT 개선 사항 체크
- 종목별 손익

**사용법**:
```bash
python3 analyze_daily_trades.py              # 오늘
python3 analyze_daily_trades.py 2025-12-22  # 특정 날짜
```

### `analyze_daily_trades_detailed.py`

**역할**: 상세 거래 분석 (시간대별, 전략별)

**출력**:
- 일일 P&L 상세
- 시간대별 분석
- 전략별 성과
- 승/패 거래 상세

**파일 위치**: `/home/greatbps/projects/kiwoom_trading/analyze_daily_trades*.py`

---

## 6. 데이터 파일

### `data/watchlist.json`

**역할**: 감시 종목 목록 (RS 기반 필터링)

**구조**:
```json
[
  {
    "stock_code": "295310",
    "stock_name": "에이치브이엠",
    "market": "KOSPI",
    "rs_rating": 90.0,
    "ai_score": 0,
    "win_rate": 100.0,
    "avg_profit_pct": 1.7458,
    "total_trades": 3,
    "profit_factor": 59800.0,
    "last_check_time": "2025-12-22T11:29:18"
  }
]
```

### `data/weekly_trade_report.json`

**역할**: 주간 거래 요약

**구조**:
```json
{
  "period": {"start": "2025-12-15", "end": "2025-12-17"},
  "summary": {
    "total_trades": 11,
    "realized_pnl": -2060.0,
    "buy_count": 5,
    "sell_count": 6
  },
  "daily_summary": {...},
  "stock_summary": {...},
  "trades": [...]
}
```

**파일 위치**: `/home/greatbps/projects/kiwoom_trading/data/`

---

## 7. 로그 파일

### `/tmp/trading_7strategies.log`

**역할**: 실시간 거래 로그

**포함 정보**:
- 조건검색 결과
- 필터링 과정
- Bottom Pullback 상태 변경
- 매수/매도 실행
- 포지션 현황
- 에러 메시지

**확인 방법**:
```bash
tail -f /tmp/trading_7strategies.log          # 실시간 모니터링
tail -100 /tmp/trading_7strategies.log        # 최근 100줄
grep "Bottom" /tmp/trading_7strategies.log    # Bottom 전략 로그만
```

---

## 8. API 클라이언트

### `api/kiwoom_open_api.py`

**역할**: 키움 OpenAPI 연동

**핵심 메서드**:
- `get_access_token()` - 인증 토큰 발급
- `get_current_price()` - 현재가 조회
- `get_orderbook()` - 호가 조회
- `get_balance()` - 계좌 잔고 조회
- `buy_order()` - 매수 주문
- `sell_order()` - 매도 주문

**파일 위치**: `/home/greatbps/projects/kiwoom_trading/api/kiwoom_open_api.py`

---

## 9. WebSocket 연결

### `realtime/websocket_client.py`

**역할**: 실시간 데이터 스트리밍

**기능**:
- 현재가 실시간 수신
- 체결 알림
- 계좌 변동 알림
- 자동 재연결

**파일 위치**: `/home/greatbps/projects/kiwoom_trading/realtime/websocket_client.py`

---

## 10. 유틸리티

### `strategy/vwap_filter.py`

**역할**: VWAP 계산 및 필터링

**기능**:
- VWAP 계산
- VWAP 상단/하단 판단
- 거래량 가중 평균가

### `strategy/condition_engine.py`

**역할**: 조건검색 실행 엔진

**기능**:
- 조건식 등록
- 실시간 조건검색
- 신호 필터링

### `utils/config_loader.py`

**역할**: YAML 설정 파일 로더

**기능**:
- 설정 파일 파싱
- 섹션별 조회
- 기본값 처리

---

## 11. 파일 구조 요약

```
kiwoom_trading/
├── main_auto_trading.py              # ⭐ 메인 실행 파일
├── run.sh                             # ⭐ 통합 스크립트
├── config/
│   └── strategy_hybrid.yaml           # ⭐ 전략 설정
├── trading/
│   ├── bottom_pullback_manager.py     # ⭐ Bottom 전략
│   ├── signal_orchestrator.py         # L0-L6 필터
│   └── eod_manager.py                 # EOD 정책
├── api/
│   └── kiwoom_open_api.py             # API 클라이언트
├── realtime/
│   └── websocket_client.py            # WebSocket
├── strategy/
│   ├── vwap_filter.py                 # VWAP
│   └── condition_engine.py            # 조건검색
├── data/
│   ├── watchlist.json                 # 감시 종목
│   └── weekly_trade_report.json       # 주간 요약
├── analyze_daily_trades.py            # ⭐ 거래 분석
└── docs/
    ├── TRADING_SYSTEM_OVERVIEW.md     # ⭐ 시스템 개요
    ├── BOTTOM_PULLBACK_STRATEGY.md    # ⭐ Bottom 전략
    └── TRADING_FILES_REFERENCE.md     # ⭐ 파일 가이드 (본 문서)
```

---

## 12. 주요 데이터 흐름

### 매수 흐름

```
[조건검색]
    ↓
main_auto_trading.py::search_and_filter_conditions()
    ↓
SignalOrchestrator (L0-L6)
    ↓
분기: Momentum (17-22) vs Bottom (23)
    ↓                      ↓
watchlist 추가        signal_watchlist 추가
    ↓                      ↓
실시간 모니터링            Pullback 모니터링
    ↓                      ↓
즉시 매수                 조건 충족 시 매수
    ↓                      ↓
         [포지션 보유]
```

### 청산 흐름

```
[포지션 보유]
    ↓
real_time_monitoring() (60초 주기)
    ↓
check_exit_conditions()
    ↓
분기: 부분 청산 | 트레일링 | 손절 | EOD
    ↓
매도 주문 실행
    ↓
포지션 제거
```

---

## 13. GPT 논의 시 참고 사항

### 질문 예시

1. **전략 개선**:
   - "Bottom Pullback 전략의 승률을 높이려면?"
   - "Momentum과 Bottom의 최적 비율은?"
   - "시간대 필터를 더 정교하게 만들려면?"

2. **리스크 관리**:
   - "연속 손실 3회 발생 시 대응 방안은?"
   - "부분 청산 비율을 조정하려면?"
   - "EOD 정책 개선 방향은?"

3. **코드 리팩토링**:
   - "main_auto_trading.py가 너무 길어서 분리하려면?"
   - "상태 관리를 더 효율적으로 하려면?"
   - "로깅을 구조화하려면?"

### 제공 정보

GPT에게 질문 시 함께 제공하면 좋은 정보:
- 최근 1주일 거래 데이터
- 현재 설정 값 (strategy_hybrid.yaml)
- 로그 파일 일부 (에러 또는 관심 구간)
- 특정 종목의 거래 히스토리

---

## 참고 문서

- `TRADING_SYSTEM_OVERVIEW.md` - 시스템 전체 구조
- `BOTTOM_PULLBACK_STRATEGY.md` - Bottom 전략 상세
- `WEEKLY_TRADE_SUMMARY_2025-12-16_to_12-22.md` - 주간 거래 분석
