# P1 — Daily Loss Source of Truth 통합 설계 (구현 없음)

> 작성일: 2026-07-27 | 운영 안정성 감사 v3.1 — 작업 4
> 상태: 설계 문서만. 실제 구현은 별도 세션(P1 착수 시)에서 진행.
> 절대 원칙: Threshold 값은 이번 설계에서 변경하지 않는다. 통합 후에도 각 소비처가
> 지금과 동일한 임계값으로 동일하게 동작해야 한다(구조만 통합, 정책은 불변).

## 1. 현재 3곳 독립 구현 (Source of Truth Audit 확정 사실)

| # | 구현체 | 변수 | Config 키 | 현재값 | 영속화 | 리셋 시점 |
|---|---|---|---|---|---|---|
| 1 | `core/risk_manager.py` | `daily_realized_pnl` vs `HARD_MAX_DAILY_LOSS_PCT` | `risk_management.daily_max_loss_pct` | **2.0%** | `data/risk_log.json` (`today` 필드로 날짜 판별 후 리셋) | 날짜 변경 시 |
| 2 | `analyzers/signal_orchestrator.py:176-211` (`check_l0_system_filter`) | 파라미터로 받은 `daily_pnl`/`current_cash` | `risk_control.max_daily_loss_pct` | **3.0%** | 없음(호출 시점 값을 그대로 계산) | 없음(매 호출 재계산) |
| 3 | `main_auto_trading.py` 자체 | `self._daily_pnl_pct`, `self._daily_loss_halted` | `risk_control.daily_risk.daily_loss_limit_pct` | **-3.0%** | 없음(순수 메모리, 2026-07-27부터 `utils/daily_reset_marker.py`로 재시작 시 리셋 방지) | `daily_routine()`의 일일 리셋 블록 |

**핵심 문제**: 3곳의 threshold가 이미 서로 다르다(2.0% vs 3.0% vs 3.0%지만 부호/계산기준
다름). 계좌가 동시에 세 계산을 통과/실패할 수 있어, 실제로 "오늘 손실이 한도를 넘었는가"에
대해 서로 다른 답을 낼 수 있는 구조다. 지금까지 사고로 이어지지 않은 건 우연에 가깝다.

## 2. 각 구현체의 입력 데이터 차이 (통합 시 반드시 고려)

- **#1 (risk_manager)**: `daily_realized_pnl`은 **실현손익(원화)** 누적값 — 매도 체결 시마다
  갱신, 파일에 영속화됨. 계좌 기준 절대 금액.
- **#2 (signal_orchestrator)**: 호출부(`main_auto_trading.py`)가 넘겨주는 `daily_pnl`/
  `current_cash` — 어디서 계산해서 넘기는지 호출부마다 다를 수 있어 정합성 보장이 약함.
- **#3 (main_auto_trading 자체)**: `self._daily_pnl_pct`는 **profit_rate(%)의 누적 합** —
  거래별 수익률을 그대로 더한 값으로, #1의 "실현손익 금액 ÷ 초기자본"과는 계산 방식 자체가
  다르다(포지션 크기가 거래마다 다르면 두 값은 서로 다른 결과를 낼 수 있음).

즉 단순히 "숫자 하나로 합치면 끝"이 아니라, **애초에 서로 다른 걸 측정하고 있을 가능성**을
먼저 확인해야 한다 — 이는 구현 세션에서 반드시 먼저 검증할 사항이다(§5 회귀테스트 참고).

## 3. 제안 인터페이스 — `DailyLossService`

```python
# services/daily_loss_service.py (예시, 구현 아님)

class DailyLossState(NamedTuple):
    realized_pnl_amount: float      # 원화 실현손익 누적
    realized_pnl_pct: float         # 초기자본 대비 %
    profit_rate_sum_pct: float      # 거래별 profit_rate 누적 합 (#3 방식, 하위호환용)
    is_halted: bool                 # 임계값 하나라도 넘었으면 True
    halted_reason: Optional[str]    # 어떤 threshold가 걸렸는지

class DailyLossService:
    def __init__(self, storage_path: str, config: dict): ...

    def record_trade_close(self, pnl_amount: float, profit_rate_pct: float) -> None:
        """매도 체결마다 호출 — 내부 상태 갱신 + 영속화."""

    def get_daily_loss_state(self) -> DailyLossState:
        """모든 소비처가 이 메서드 하나만 호출한다."""

    def reset_daily(self, today: date) -> None:
        """일일 리셋 — daily_reset_marker와 연동해 재시작 시 중복 실행 방지 필요."""
```

**소비처 변경 방향**:
- `core/risk_manager.can_open_position()`: 자체 `daily_realized_pnl` 계산 제거,
  `DailyLossService.get_daily_loss_state().is_halted` 참조로 대체.
- `analyzers/signal_orchestrator.check_l0_system_filter()`: `daily_pnl`/`current_cash`
  파라미터 제거, 생성자 또는 메서드 인자로 `DailyLossService` 주입받아 `get_daily_loss_state()`
  호출로 대체.
- `main_auto_trading.py`의 `self._daily_pnl_pct`/`self._daily_loss_halted`: 제거하고
  `self.daily_loss_service.get_daily_loss_state()`로 대체. 매도 체결 시점(현재
  `self._daily_pnl_pct += profit_pct` 하던 지점, `main_auto_trading.py:12692` 부근)에서
  `record_trade_close()` 호출로 대체.

**Threshold 정책**: 이번 통합은 구조만 바꾸고 값은 그대로 둔다. 3개 값(2.0/3.0/3.0-signed)을
하나로 합칠지, 아니면 `DailyLossState`가 3개 threshold를 각각 체크해 `halted_reason`으로
어떤 게 걸렸는지 구분해 반환할지는 **정책 결정 사항이며 이번 설계에서 결정하지 않는다** —
"임계값을 하나로 통일하는 것" 자체가 정책 변경이므로 별도 승인이 필요하다. 구현 세션에서는
우선 "3개 threshold를 유지한 채 계산 로직만 한 곳으로 모으는" 안전한 접근을 권장한다.

## 4. 영향도 분석

| 영향 대상 | 영향 내용 | 리스크 |
|---|---|---|
| `core/risk_manager.py` | `can_open_position()` 내부 로직 일부 제거+대체 | 중 — 실거래 매수 게이트 직결 |
| `analyzers/signal_orchestrator.py` | `check_l0_system_filter()` 시그니처 변경 가능성(파라미터 제거) | 중 — 호출부(main_auto_trading.py) 동시 수정 필요 |
| `main_auto_trading.py` | `_daily_pnl_pct`/`_daily_loss_halted` 참조하는 모든 곳 (로그 출력, 콘솔 출력 등 포함) 확인 필요 | 높음 — 5000줄 파일 전역 검색 필요, [DAILY_LOSS_LIMIT] 로그 등 |
| `data/risk_log.json` | 스키마 변경 가능성 (새 필드 추가는 안전, 필드 제거는 하위호환 깨짐) | 낮음(추가만 한다면) |
| 회귀 테스트 | `tests/unit/test_risk_manager.py` 등 기존 테스트 다수 영향 가능 | 높음 |

**가장 큰 리스크**: `main_auto_trading.py`가 5000줄이 넘는 단일 파일이라, `_daily_pnl_pct`/
`_daily_loss_halted`를 참조하는 모든 지점(콘솔 출력, 로그, 조건 분기)을 전수 확인하지 않으면
일부 표시/로직이 새 값과 어긋나는 상태로 남을 수 있다. 구현 전 반드시
`grep -n "_daily_pnl_pct\|_daily_loss_halted" main_auto_trading.py`로 전체 참조처를 먼저
목록화할 것.

## 5. 회귀 테스트 계획 (구현 세션에서 작성)

1. **사전 검증(구현 전 필수)**: 과거 실제 거래 데이터로 #1(금액 기준)과 #3(profit_rate 합
   기준) 계산 결과가 실제로 같은 날 같은 결론(halt 여부)을 내는지 최소 다과거 거래일 대상
   재현 — 다르면 통합 시 어느 쪽을 "진실"로 할지 사용자 승인 필요.
2. `tests/unit/test_daily_loss_service.py` (신규): `DailyLossService` 단위테스트 —
   record_trade_close 누적, threshold 초과 판정, reset_daily 동작, 재시작 후 상태 복원
   (risk_log.json 방식 참고).
3. `tests/unit/test_risk_manager.py` 갱신: `can_open_position()`이 `DailyLossService` 주입
   버전에서도 기존 시나리오(정상/한도초과) 동일하게 동작하는지.
4. `tests/unit/test_regime_analyzer.py`(또는 signal_orchestrator 관련 기존 테스트) 갱신:
   `check_l0_system_filter()` 시그니처 변경 반영.
5. 통합 시나리오: 동일한 하루치 합성 거래 시퀀스를 통합 전/후 버전에 각각 흘려
   3곳의 최종 halt 여부·시점이 기존과 동일한지 diff 비교(Threshold 불변 검증).

## 6. 이번 세션에서 하지 않는 것

- `services/daily_loss_service.py` 실제 작성
- `risk_manager.py`/`signal_orchestrator.py`/`main_auto_trading.py` 수정
- Threshold 값 결정(2.0% vs 3.0% 중 하나로 통일할지 여부)
- 회귀 테스트 작성
