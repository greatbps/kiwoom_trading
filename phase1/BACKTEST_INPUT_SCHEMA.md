# BACKTEST_INPUT_SCHEMA — VWAP+AI 백테스트 입력 정의

Iteration 6-0 · 2026-08-03

---

## 1. 스키마

VWAP Engine 이 받을 분봉 DataFrame.

| 필드 | 자료형 | 필수 | 결측 처리 | 비고 |
|---|---|---|---|---|
| `datetime` | `pd.Timestamp` (tz-aware, **Asia/Seoul**) | ✅ | 결측행 **제거** | 인덱스로 사용 |
| `open` | `float64` | ✅ | 제거 | 원 응답 `open_pric` |
| `high` | `float64` | ✅ | 제거 | `high_pric` |
| `low` | `float64` | ✅ | 제거 | `low_pric` |
| `close` | `float64` | ✅ | 제거 | `cur_prc` |
| `volume` | `int64` | ✅ | 0 허용 | `trde_qty` |
| `acc_volume` | `int64` | ❌ | 결측 허용 | `acc_trde_qty` — VWAP 검증용 |

**인덱스**: `datetime` 오름차순, 중복 없음.

---

## 2. 키움 응답 → 스키마 변환

`ka10080` 응답 컬럼 9개 (실측):

```
cntr_tm  open_pric  high_pric  low_pric  cur_prc
trde_qty  acc_trde_qty  pred_pre  pred_pre_sig
```

| 원 필드 | 변환 | 주의 |
|---|---|---|
| `cntr_tm` | `YYYYMMDDHHMMSS` → Timestamp(Asia/Seoul) | 문자열이다 |
| `open_pric` | `+262500` → `262500.0` | ⚠️ **부호 접두**가 붙는다. `+`/`-` 제거 후 `abs()` |
| `high_pric` | 〃 | 〃 |
| `low_pric` | 〃 | 〃 |
| `cur_prc` | 〃 → `close` | 〃 |
| `trde_qty` | `'4697892'` → `int` | |
| `acc_trde_qty` | 〃 | 누적값 — 봉별 거래량 아님 |
| `pred_pre`, `pred_pre_sig` | **미사용** | 전일 대비 |

⚠️ **부호 접두가 가장 위험하다.** `float('+262500')` 은 통과하지만
`-` 가 붙으면 음수 가격이 된다. 실측 샘플에서 `open_pric='+262500'` 을
확인했다. 반드시 정규화한다.

```python
def _px(v: str) -> float:
    return abs(float(str(v).replace('+', '').replace('-', '')))
```

---

## 3. 시간대

- 원 응답은 **KST 로컬 시각**이고 tz 정보가 없다.
- `Asia/Seoul` 로 localize 한다. UTC 변환 금지 — 장중 시각 판정
  (09:00 시작 · 15:30 종료 · 14:50 오버나이트 차단)이 로컬 기준이다.
- 기존 캐시 `data/raw/396500_KS_5m.csv` 는 **UTC** 로 저장돼 있다
  (`2025-12-05 00:00:00+00:00`). 섞으면 9시간 어긋난다.

---

## 4. 검증 규칙 (로드 시 강제)

| 규칙 | 위반 시 |
|---|---|
| `low <= open,close <= high` | 해당 행 제거 + 경고 |
| `high >= low` | 제거 |
| 가격 > 0 | 제거 |
| `datetime` 중복 | 뒤엣것 유지 |
| 정렬 오름차순 | 재정렬 |
| 장중 시각(09:00~15:30) | 밖이면 **경고만** — 시간외 봉 존재 가능 |

⚠️ **결측을 앞뒤 값으로 채우지 않는다.** 거래가 없어 봉이 없는 것과
데이터가 빠진 것을 구분할 수 없다. 채우면 없는 거래를 만들어 낸다.

---

## 5. 저장 포맷

```
phase1/minute_cache/{tic}m/{stock_code}.pkl     # DataFrame
phase1/minute_cache/meta.json                   # 수집 시각·범위·건수
```

⚠️ DB 테이블을 만들지 않는다. CLAUDE.md Phase A 가 운영 스키마 신규
테이블을 금지하고, 백테스트 캐시는 재생성 가능한 산출물이다.
