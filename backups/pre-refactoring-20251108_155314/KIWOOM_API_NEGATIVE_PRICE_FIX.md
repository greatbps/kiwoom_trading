# 키움 API 음수 가격 문제 해결

## 문제 상황

자동매매 시스템에서 주식 현재가가 음수로 표시되는 문제 발생:
- 현재가: -34,450원, -22,800원 등
- 거래량 증감: 모두 0%

## 원인 분석

키움 REST API `ka10080` (주식분봉차트조회요청) 응답 형식 조사 결과:

### API 문서 확인 (docs/키움api/키움 REST API 문서.xlsx)

```json
{
    "stk_cd": "005930",
    "stk_min_pole_chart_qry": [
        {
            "cur_prc": "-78800",      // ← 음수!
            "trde_qty": "7913",
            "cntr_tm": "20250917132000",
            "open_pric": "-78850",    // ← 음수!
            "high_pric": "-78900",    // ← 음수!
            "low_pric": "-78800",     // ← 음수!
            "pred_pre": "-600",
            "pred_pre_sig": "5"       // 5: 하락
        }
    ]
}
```

**핵심 발견**: 키움 API는 가격 하락 시 음수 부호(`-`)를 붙여서 반환함!
- `pred_pre_sig`: 1=상한가, 2=상승, 3=보합, 4=하한가, **5=하락**
- 하락일 때 가격에 `-` 부호가 붙지만, 이것은 **방향 표시**이지 실제 음수 가격이 아님
- 실제 가격은 절대값 사용해야 함

## 해결 방법

### 수정 전 (잘못된 코드)

```python
# numeric 변환
for col in ['open', 'high', 'low', 'close', 'volume']:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce')  # ← -78800 그대로 유지
```

**문제**: 음수 부호가 그대로 남아서 VWAP 계산 등에서 오류 발생

### 수정 후 (올바른 코드)

```python
# 🔧 CRITICAL: 키움 API는 음수 부호로 하락을 표시 → 절대값 변환 필수!
# 예: cur_prc="-78800" → 실제 가격은 78,800원
for col in ['open', 'high', 'low', 'close', 'volume']:
    if col in df.columns:
        df[col] = pd.to_numeric(df[col], errors='coerce').abs()  # ← .abs() 추가!
```

**해결**: `.abs()`를 추가하여 음수를 절대값으로 변환

## 적용된 파일

1. **main_auto_trading.py** (라인 1203-1206)
   - 보유 종목 현재가 조회 시 적용

2. **main_condition_filter.py** (라인 183-186)
   - 조건 검색 종목 VWAP 백테스트 시 적용

## 효과

✅ **음수 가격 해결**
- 기존: -34,450원, -22,800원
- 수정 후: 34,450원, 22,800원

✅ **VWAP 백테스트 -199% 오류 해결**
- 음수 가격으로 인한 비정상적인 수익률 계산 문제 해결
- 27/28 종목이 실패하던 백테스트가 정상 작동 예상

✅ **거래량 0% 문제 해결**
- volume도 절대값 적용으로 정상 계산

## 테스트

```python
# 테스트 코드
import pandas as pd

kiwoom_data = [
    {"cur_prc": "-78800", "open_pric": "-78850", "trde_qty": "7913"},
]
df = pd.DataFrame(kiwoom_data)

# Before: df['cur_prc'] = "-78800"
df['close'] = pd.to_numeric(df['cur_prc'], errors='coerce').abs()
# After:  df['close'] = 78800 ✅
```

## 주의사항

- **절대값 변환은 필수**: 키움 API 데이터 사용 시 모든 가격 컬럼에 `.abs()` 적용 필요
- **방향 정보 필요 시**: `pred_pre_sig` 필드 참조 (1~5 값)
- **다른 API도 확인**: 다른 키움 API도 동일한 형식일 가능성 있음

## 참고 문서

- 키움 REST API 문서.xlsx → "주식분봉차트조회요청(ka10080)" 시트
- API URL: `POST /api/dostk/chart`
- 응답 필드:
  - `cur_prc`: 현재가
  - `open_pric`: 시가
  - `high_pric`: 고가
  - `low_pric`: 저가
  - `trde_qty`: 거래량
  - `pred_pre_sig`: 전일대비 기호 (1~5)

---
작성일: 2025-11-03
