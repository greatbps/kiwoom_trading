# Dual Account Trading System

## 시스템 개요

단타/중기 계좌 분리 운용 시스템으로, 하나의 파이프라인에서 발생한 매매 시그널을
Intent(의도)에 따라 분류하고 적절한 계좌와 Exit Engine으로 라우팅합니다.

## 아키텍처

```
[기존 파이프라인]
      │
      ▼
[Trade Intent Classifier] ← 핵심 신규 레이어
      │
      ├── scalp/intraday → 단타 계좌 (6259-3479)
      │                    └── 기존 Exit Logic (분봉 기반)
      │
      └── swing/squeeze_trend → 중기 계좌 (5202-2235)
                               └── Trend Exit Engine (일봉 기반)
```

## 계좌 설정

| 계좌 | 번호 | 용도 | 배분 |
|------|------|------|------|
| 단타 | 6259-3479 | 스캘핑/인트라데이 | 60% |
| 중기 | 5202-2235 | 스윙/추세 추종 | 40% |

## 핵심 모듈

### 1. TradeIntentClassifier (`trade_intent.py`)

시그널을 4가지 Intent로 분류:
- `SCALP`: 초단타 (분~수십분)
- `INTRADAY`: 당일 (수시간)
- `SWING`: 스윙 (수일)
- `SQUEEZE_TREND`: 스퀴즈 추세 (수일~수주)

**분류 규칙 (v1):**
```python
IF squeeze_daily == True
AND momentum_daily > 0
AND momentum_slope >= 0
AND (news.persistence == "narrative" OR htf_structure == "intact")
→ squeeze_trend (중기 계좌)

ELSE → scalp (단타 계좌)
```

### 2. TrendExitEngine (`trend_exit_engine.py`)

중기 전략용 청산 로직 (B(Pro) 규칙 기반):

| 조건 | 액션 |
|------|------|
| Squeeze ON | 무조건 HOLD |
| Momentum 둔화 (양수지만 감소) | 무시 (HOLD) |
| Momentum 음전 + 기울기 하락 | 전량 청산 |
| 수익 +4% | 트레일링 활성화 |
| 수익 +8% | 트레일링 타이트닝 |
| 20거래일 초과 | 타임아웃 청산 |

### 3. DualAccountOrchestrator (`dual_account_orchestrator.py`)

전체 시스템 조율:
- 시그널 처리 및 Intent 분류
- 계좌별 주문 라우팅
- 중기 포지션 EOD 평가
- 일일 리포트 생성

## 사용 예시

```python
from trading import DualAccountOrchestrator

# 초기화
orchestrator = DualAccountOrchestrator()

# 시그널 처리
signal = {
    "symbol": "240810",
    "stock_name": "원익IPS",
    "current_price": 103500,
    "squeeze_on": True,
    "momentum": 0.8,
    "momentum_slope": 0.05,
    "news_persistence": "narrative"
}

result = orchestrator.process_signal(signal)
# 결과: squeeze_trend → 5202-2235 (중기 계좌)

# 중기 포지션 진입
if result["intent"] in ["squeeze_trend", "swing"]:
    pos = orchestrator.execute_trend_entry(signal, quantity=10)

# EOD 평가
exit_signals = orchestrator.evaluate_trend_positions(market_data)
```

## 설정 파일 (.env)

```env
# 계좌 설정 (단타/중기 분리)
KIWOOM_SCALP_ACCOUNT = "6259-3479"      # 단타 계좌
KIWOOM_TREND_ACCOUNT = "5202-2235"      # 중기 계좌
```

## 로그 구조

```
logs/
├── trading_YYYYMMDD_HHMMSS.log    # 메인 로그
└── trend_account/                  # 중기 계좌 전용 로그
    ├── positions.log
    ├── exits.log
    └── daily_reports/
```

## 주의사항

1. **Intent는 진입 시 결정됨** - 한번 분류된 Intent는 변경 불가
2. **계좌 혼용 금지** - 단타 시그널은 단타 계좌로만
3. **EOD 평가 필수** - 중기 포지션은 매일 장 마감 후 평가
4. **로그 분리** - 계좌별 성과 분석을 위해 로그 분리 유지

## 다음 단계

1. [ ] Daily Squeeze 데이터 자동 수집 연동
2. [ ] 실제 주문 API 연동
3. [ ] 텔레그램 알림 연동
4. [ ] 백테스트 검증

---

**버전**: 1.0.0
**생성일**: 2026-02-01
**작성자**: Claude
