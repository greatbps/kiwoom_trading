# CONDITION_ATTRIBUTION_REPORT

Iteration 8-1 · 2026-08-02
Regression: **484 PASS / 16 Known FAIL** — RELEASE ALLOWED
**전략 로직 무변경 · 데이터 기록만 추가**

---

## 1. 현재 조건검색 Pipeline 구조

```
[08:45~] main_auto_trading.py
    │
    ├─ get_condition_list()          키움에서 조건식 목록 수신
    │                                 이름은 런타임에만 존재 (코드/설정에 없음)
    │
    ├─ for idx in condition_indices  [17,18,19,20,21,22]  ← 6개
    │     condition = self.condition_list[idx]
    │     seq, name = condition[0], condition[1]
    │     stocks = await search_condition(seq, name)
    │        ↓
    │     stock_to_condition_map[code] = idx    ← ⚠️ 덮어쓰기
    │        ↓
    │  all_stocks (1차 필터 통과)
    │
    ├─ L2 RS 필터 → L3 → VWAP 필터
    │        ↓
    │  validated_stocks[code] = { name, market, rs_rating, strategy, ... }
    │        ↓
    ├─ execute_buy()
    │     trade_data['condition_name'] = 'VWAP+AI'   ← ⚠️ 출처 유실
    │        ↓
    └─ trades 테이블
```

### 확인된 조건식 (로그 등장 횟수)

| 조건식 | 등장 |
|---|---|
| GreatMid | 8,916 |
| Momentum 전략 | 6,100 |
| 알고리즘추출_1110 | 1,256 |
| 종가매수_대박주식 | 854 |

⚠️ **인덱스 17~22 와 위 이름의 대응은 확인하지 못했다.** 로그에
`인덱스 = 이름` 형태로 찍힌 줄이 0건이고, 이름은 키움 API 가 런타임에
내려주는 값이라 코드·설정에 없다. **추정하지 않는다.**

---

## 2. 수정 전/후 데이터 흐름

### 수정 전

```
GreatMid ─┐
Momentum ─┼─▶ 종목 ─▶ VWAP ─▶ BUY ─▶ trades.condition_name = 'VWAP+AI'
알고리즘  ─┤                                    ▲
종가매수 ─┘                                     └─ 출처 전부 소실
```

두 가지가 겹쳐 있었다.

1. `stock_to_condition_map[code] = idx` — **덮어쓰기**. 한 종목이 여러
   조건식에 걸리면 마지막 것만 남는다.
2. 원장에는 `'VWAP+AI'` 상수만 기록. 인덱스조차 안 남는다.

### 수정 후

```
GreatMid ─┐
Momentum ─┼─▶ _cond_sources[code] = ['GreatMid','Momentum 전략']  (누적)
알고리즘  ─┤        ↓
종가매수 ─┘   validated_stocks[code].condition_sources
                     ↓
              trades.entry_context = {
                  condition_sources:   ['GreatMid','Momentum 전략'],
                  primary_condition:   'GreatMid',
                  condition_match_time: '2026-08-02T09:01:00'
              }
                     ↓
              [COND_ATTR] 로그
```

---

## 3. Schema 변경 — **DDL 없음**

기존 `trades.entry_context` (JSONB) 를 쓴다.

```json
{
  "condition_sources": ["GreatMid", "Momentum 전략"],
  "primary_condition": "GreatMid",
  "condition_match_time": "2026-08-02T09:01:00",
  "source": "condition_search"
}
```

**신규 컬럼·테이블을 만들지 않았다.** 라이브 시스템에 DDL 을 거는
위험을 피했고, CLAUDE.md Phase A 의 "운영 스키마 신규 테이블 금지"
제약과도 충돌하지 않는다. JSONB 라 Postgres 에서 그대로 질의된다.

```sql
SELECT entry_context->>'primary_condition', count(*)
FROM trades WHERE trade_type='BUY' GROUP BY 1;
```

---

## 4. Test 결과 (dry-run, 실주문 0건)

```
==============================
CONDITION ATTRIBUTION TEST
==============================

✅ PASS  Test 1  조건검색 → 종목 저장 (다중 출처)
          005930=['GreatMid', 'Momentum 전략']
✅ PASS  Test 2  VWAP 통과 → 출처 유지
          primary=GreatMid
✅ PASS  Test 3  BUY → trade record 저장
          ["GreatMid", "Momentum 전략"]
✅ PASS  Test 4  EXIT → 원 거래 연결 (stock_code 기준)
          청산 행은 entry_context 미기록 — 조인으로 연결
✅ PASS  Test 5  기록 없음 → UNKNOWN (추정 금지)
          ['UNKNOWN']

Condition Events       5 시나리오
Mapped Trades          0
Unknown Trades         165
Attribution Coverage % 0.0%
DB Migration Result    158건 UNKNOWN 표시 완료
Regression Result      484 PASS / 16 Known FAIL — RELEASE ALLOWED
==============================
```

### Coverage 0% 는 정상이다

과거 거래는 **기록 자체가 없었다.** 추정하지 않고 UNKNOWN 으로 뒀다
(§9 준수). **신규 매수부터 쌓인다.**

### DB Migration

```
UPDATE 대상   trade_type='BUY' AND entry_context IS NULL   → 158행
보존          기존 entry_context 있는 7행 (스윙 진입 정보)  → 미변경
결과          NULL 잔여 0 · UNKNOWN 표시 158 · 스윙정보 5행 보존
```

**삭제 없음. NULL 채우기만.**

### 회귀 테스트 10건 추가

`tests/unit/test_condition_attribution.py`

| 테스트 | 지키는 것 |
|---|---|
| `test_sources_are_accumulated_not_overwritten` | `= name` 덮어쓰기 금지 |
| `test_existing_condition_map_untouched` | 기존 전략 태그 배정 불변 |
| `test_validated_stocks_carries_attribution` | 두 분기 모두 전달 |
| `test_helper_exists_and_fails_closed` | 기록 없으면 UNKNOWN |
| `test_trade_record_carries_attribution` | 매수 2곳 모두 |
| `test_no_strategy_logic_changed` | 헬퍼 호출 3회만 |
| `test_runtime_multi_source` / `unknown_when_no_record` | 런타임 동작 |

---

## 5. 성능 영향 (§10)

| 항목 | 값 |
|---|---|
| 출처 조회 | **0.58 μs/회** (dict 조회) |
| scan latency | 영향 없음 — 조건검색 루프에 `setdefault().append()` 2회 추가 |
| order latency | 영향 없음 — 매수 시 dict 조회 1회 |
| DB write time | 영향 없음 — 기존 `entry_context` 컬럼 재사용, 컬럼 추가 없음 |
| memory | 종목당 리스트 1개 (조건식 최대 6개 문자열) |

⚠️ **기존 대비 실측 비교는 하지 않았다.** 변경 전 바이너리를 같은
조건에서 재실행해야 하는데, 라이브 시스템이라 그렇게 하지 않았다.
위 값은 추가된 연산 자체의 비용이다.

---

## 6. 샘플 로그

```
[BUY_COMPLETE] 005930 삼성전자 | price=71,500 qty=14 amount=1,001,000 |
               reason=09:12 VWAP 상향 돌파 | trade_id=417
[COND_ATTR] symbol=005930 trade_id=417
            condition_sources=['GreatMid', 'Momentum 전략']
            primary_condition=GreatMid
            condition_match_time=2026-08-02T09:01:00
            entry_reason=09:12 VWAP 상향 돌파
```

기록 없는 경우:

```
[COND_ATTR] symbol=396470 trade_id=418
            condition_sources=['UNKNOWN'] primary_condition=UNKNOWN
            condition_match_time=None entry_reason=VWAP+AI
```

---

## 7. 완료 기준 대비

| 항목 | 기준 | 결과 |
|---|---|---|
| 조건검색 출처 저장 | PASS | ✅ |
| 거래 Attribution | PASS | ✅ (신규 매수부터) |
| 기존 전략 영향 | 없음 | ✅ 계산식 변경 0건 |
| Regression | PASS | ✅ 484 PASS / 16 FAIL |
| Runtime Error | 0 | ✅ |
| Dry-run 검증 | PASS | ✅ 5/5 |

### 변경 파일

```
main_auto_trading.py                        +62 -2  (기록만)
tests/unit/test_condition_attribution.py    신규 10건
phase1/condition_attribution_test.py        신규 (dry-run 도구)

무수정: risk_manager · exit_logic_optimized · choch_engine ·
        swing_runner · backtest/adapter · VWAP 계산 · Entry/Exit 조건
```

diff 중 `VWAP` 문자열 3건은 **전부 주석·로그 문구**다. 계산식 변경 0건.

---

## 8. 남은 한계 — 다음 작업으로

1. **조건식 인덱스 ↔ 이름 매핑이 확인되지 않았다.**
   실행 중인 프로세스에서 `condition_list` 를 조회해야 알 수 있다.
   지금은 이름이 기록되므로 **신규 매수가 쌓이면 자연히 드러난다.**

2. **과거 165건은 영원히 UNKNOWN 이다.** 조건식별 성과 분석은
   신규 데이터로만 가능하다.

3. **청산 행에는 출처를 기록하지 않았다.** `stock_code` 조인으로
   원 거래를 찾는 구조인데, Iteration 7-2 에서 확인했듯 청산 242건 중
   **120건(49.6%)에 선행 BUY 기록이 없다.** 이 상태로는 조건식별
   손익 귀속이 절반만 가능하다. **별도 Iteration 이 필요하다.**

---

## 9. 다음 단계

조건식별 분석(승률·PF·VWAP 통과율·매매 전환율)은 **신규 매수가
일정량 쌓인 뒤** 가능하다. 실거래 빈도로 보아 수십 건 확보에
1~2개월이 걸린다.

그 사이 할 수 있는 것: **VWAP+AI 전략 백테스트화.**
실거래의 95.8%인데 아직 한 번도 검증된 적이 없다.
다만 분봉 데이터 확보가 선행 과제다.
