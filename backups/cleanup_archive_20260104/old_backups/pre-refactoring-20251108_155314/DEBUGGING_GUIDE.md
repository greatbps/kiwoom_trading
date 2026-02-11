# Debugging Guide - Kiwoom API 데이터 조회 이슈

**날짜:** 2025-10-30
**이슈:** 키움 API에서 모든 종목 데이터가 0개로 반환됨

---

## 🔍 증상

```
⚠️  대원전선(006340) 키움 데이터 부족 (0개) → Yahoo Finance 보충 시도
✓ 006340.KS 데이터 로드 성공 (504개 봉)
```

모든 종목에서 키움 API 조회 결과가 0개입니다.

---

## 🛠️ 추가된 디버깅 로그

`main_auto_trading.py`의 `get_kiwoom_minute_data()` 함수에 다음 로그를 추가했습니다:

### 1. **return_code 확인**
```python
return_code = result.get('return_code')
if return_code != 0:
    return_msg = result.get('return_msg', 'Unknown error')
    console.print(f"[dim]키움 API 오류 ({stock_code}): return_code={return_code}, msg={return_msg}[/dim]")
```

**확인 사항:** return_code가 0이 아니면 오류 메시지가 출력됩니다.

### 2. **응답 키 확인**
```python
if not data:
    console.print(f"[yellow]⚠️  키움 API 응답 키 없음 ({stock_code}). 응답 키: {list(result.keys())}[/yellow]")
```

**확인 사항:** 실제 API 응답의 키 목록이 출력됩니다.

### 3. **컬럼명 확인**
```python
if missing_cols:
    console.print(f"[yellow]⚠️  키움 데이터 컬럼 부족 ({stock_code}): 없는 컬럼 {missing_cols}[/yellow]")
    console.print(f"[dim]실제 컬럼: {list(df.columns)}[/dim]")
```

**확인 사항:** 예상한 컬럼명과 실제 컬럼명이 다를 경우 출력됩니다.

---

## 📋 다음 실행 시 확인할 내용

프로그램을 다시 실행하고 다음 내용을 확인해주세요:

### 케이스 1: return_code 오류
```
키움 API 오류 (006340): return_code=XXX, msg=YYYYY
```

**해결책:** API 인증 또는 권한 문제. access_token 확인 필요.

### 케이스 2: 응답 키 없음
```
⚠️  키움 API 응답 키 없음 (006340). 응답 키: ['abc', 'def', 'ghi']
```

**해결책:** `possible_keys` 리스트에 실제 키 추가 필요.

### 케이스 3: 컬럼명 불일치
```
⚠️  키움 데이터 컬럼 부족 (006340): 없는 컬럼 ['datetime', 'time']
실제 컬럼: ['date', 'hour', 'price', ...]
```

**해결책:** `column_mapping` 딕셔너리에 실제 컬럼명 매핑 추가 필요.

---

## 🔧 수정 방법

### 1. 응답 키 추가
`main_auto_trading.py` Line 135:
```python
possible_keys = ['stk_mnut_pole_chart_qry', 'output', 'output1', 'output2', 'data']
# → 실제 키를 추가
possible_keys = ['stk_mnut_pole_chart_qry', 'output', 'output1', 'output2', 'data', '실제키']
```

### 2. 컬럼명 매핑 추가
`main_auto_trading.py` Line 155-170:
```python
column_mapping = {
    'dt': 'datetime',
    'tm': 'time',
    # ... 기존 매핑
    '실제컬럼명': 'datetime',  # 추가
    '실제시간컬럼': 'time'      # 추가
}
```

---

## 🧪 테스트용 임시 코드

키움 API 응답을 직접 확인하려면 다음 코드를 추가하세요:

```python
# get_kiwoom_minute_data() 함수 내부, Line 120 다음에 추가:
import json
console.print(f"[cyan]===== 키움 API 응답 ({stock_code}) =====[/cyan]")
console.print(json.dumps(result, indent=2, ensure_ascii=False)[:1000])
console.print("[cyan]================================[/cyan]")
```

---

## 📊 Yahoo Finance 6개 봉 문제

**증상:**
```
✓ 046390.KS 데이터 로드 성공 (6개 봉)
```

**원인:** 거래량이 매우 적은 종목 (장 시작 후 6개 거래만 발생)

**정상 동작:**
- 100개 미만이므로 검증 실패 → 관심 종목에서 제외
- 이것은 **정상적인 필터링**입니다

**검증 통과 종목:**
```
✅ 대원전선: 승률 66.7%, 수익 +1.2%  (504개 봉 → 통과)
✅ 한국화장품: 승률 55.6%, 수익 +1.4%  (503개 봉 → 통과)
```

---

## ✅ 체크리스트

실행 후 다음을 확인하세요:

- [ ] 디버깅 로그에서 `return_code` 확인
- [ ] 디버깅 로그에서 `응답 키` 확인
- [ ] 디버깅 로그에서 `실제 컬럼명` 확인
- [ ] 검증 통과한 종목이 watchlist에 추가되는지 확인
- [ ] 실시간 모니터링에서 해당 종목들이 표시되는지 확인

---

## 📞 이슈 보고 시 포함할 정보

문제가 계속되면 다음 정보를 함께 제공해주세요:

1. **디버깅 로그 전체 내용** (특히 첫 번째 종목의 로그)
2. **키움 API access_token 발급 상태** (만료 여부)
3. **키움 API 문서의 분봉 차트 응답 예시** (가능하면)
4. **프로그램 실행 시간** (정규장 시간인지 확인)

---

**다음 단계:** 프로그램을 다시 실행하고 위의 디버깅 로그를 확인한 후 알려주세요!
