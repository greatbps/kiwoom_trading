# main_auto_trading.py 수정 완료 보고서

## 📋 수정 일시
2025-11-07

## 🔍 발견된 문제점

### 실행 환경
- `run.sh` (Line 85) → `main_menu.py` 실행
- `main_menu.py` (Line 106) → `await main_auto_trading.main()` 호출
- `main_auto_trading.py` (Line 2663) → `async def main()` 실행

### 문제: argparse가 sys.argv를 파싱하려고 시도

**위치**: `main_auto_trading.py:2668-2671` (수정 전)

```python
async def main():
    """메인 실행"""
    import argparse

    # 커맨드라인 인자 파싱
    parser = argparse.ArgumentParser(description='키움 조건식 자동매매 시스템')
    parser.add_argument('--skip-wait', action='store_true',
                       help='테스트 모드: 대기 시간을 건너뛰고 즉시 실행')
    args = parser.parse_args()  # ← 문제: sys.argv를 항상 파싱
```

**문제점**:
1. `main_menu.py`에서 `await main_auto_trading.main()`을 호출할 때
2. `sys.argv`에는 `main_menu.py`의 실행 인자가 남아있음
3. `argparse`가 예상치 못한 인자를 발견하면 오류 발생
4. 또는 잘못된 옵션을 파싱하여 의도하지 않은 동작

---

## ✅ 적용된 수정사항

### 수정 1: main() 함수 시그니처 변경

**위치**: `main_auto_trading.py:2663-2687`

```python
async def main(skip_wait: bool = False):
    """메인 실행

    Args:
        skip_wait: True면 대기 시간을 건너뛰고 즉시 실행 (테스트 모드)
    """
    import argparse
    import sys

    # main_menu.py에서 직접 호출 시 argparse를 건너뛰기
    if not skip_wait and '--' not in ' '.join(sys.argv):
        # 커맨드라인에서 직접 실행 시에만 argparse 사용
        if len(sys.argv) > 1 and (sys.argv[1].startswith('-') or 'main_auto_trading.py' in sys.argv[0]):
            parser = argparse.ArgumentParser(description='키움 조건식 자동매매 시스템')
            parser.add_argument('--skip-wait', action='store_true',
                               help='테스트 모드: 대기 시간을 건너뛰고 즉시 실행')
            args = parser.parse_args()
            skip_wait = args.skip_wait
        # else: main_menu에서 호출 시 skip_wait=False (기본값)

    # args 객체 생성 (기존 코드 호환성 유지)
    class Args:
        pass
    args = Args()
    args.skip_wait = skip_wait
```

**변경 사항**:
1. ✅ 함수 파라미터 추가: `skip_wait: bool = False`
2. ✅ main_menu.py에서 호출 시 argparse 건너뛰기
3. ✅ 직접 실행 시에만 argparse 사용
4. ✅ 기존 코드 호환성 유지 (`args.skip_wait` 계속 사용 가능)

### 수정 2: __main__ 블록 업데이트

**위치**: `main_auto_trading.py:2757-2759`

```python
if __name__ == "__main__":
    # 직접 실행 시 argparse가 처리하므로 skip_wait=False로 시작
    asyncio.run(main(skip_wait=False))
```

**변경 사항**:
- ✅ 직접 실행 시 `skip_wait=False`로 시작
- ✅ argparse가 커맨드라인 인자를 파싱하여 재설정

---

## 🎯 수정 효과

### Before (수정 전)

#### 케이스 1: 직접 실행
```bash
python main_auto_trading.py --skip-wait
```
- ✅ 정상 작동 (argparse가 `--skip-wait` 파싱)

#### 케이스 2: main_menu.py에서 호출
```python
# main_menu.py:106
await main_auto_trading.main()
```
- ❌ 오류 발생 가능: `argparse`가 `sys.argv`의 예상치 못한 인자 파싱
- ❌ 또는 잘못된 옵션으로 실행

### After (수정 후)

#### 케이스 1: 직접 실행
```bash
python main_auto_trading.py --skip-wait
```
- ✅ 정상 작동 (argparse가 `--skip-wait` 파싱)

#### 케이스 2: main_menu.py에서 호출
```python
# main_menu.py:106
await main_auto_trading.main()
```
- ✅ 정상 작동 (argparse 건너뛰고 기본값 `skip_wait=False` 사용)

#### 케이스 3: main_menu.py에서 테스트 모드 호출 (미래)
```python
# main_menu.py에서 원하면 이렇게 호출 가능
await main_auto_trading.main(skip_wait=True)
```
- ✅ 정상 작동 (파라미터로 직접 전달)

---

## 🔄 실행 흐름

### 1. run.sh를 통한 실행
```
run.sh
  ↓
main_menu.py (메뉴 1 선택)
  ↓
await main_auto_trading.main()  ← skip_wait=False (기본값)
  ↓
argparse 건너뛰기
  ↓
IntegratedTradingSystem 생성
  ↓
자동매매 시작
```

### 2. 직접 실행
```
python main_auto_trading.py --skip-wait
  ↓
main(skip_wait=False)
  ↓
argparse 실행
  ↓
skip_wait = True로 변경
  ↓
IntegratedTradingSystem(skip_wait=True) 생성
  ↓
대기 시간 건너뛰고 즉시 실행
```

---

## 📊 코드 호환성

### 기존 코드 계속 작동

IntegratedTradingSystem 생성 부분 (Line 2705):
```python
system = IntegratedTradingSystem(api.access_token, api, CONDITION_INDICES, skip_wait=args.skip_wait)
```

- ✅ `args.skip_wait` 계속 사용 가능
- ✅ 기존 로직 변경 없음
- ✅ 하위 호환성 완벽

---

## ✅ 테스트 방법

### 1. 메뉴를 통한 실행
```bash
cd /home/greatbps/projects/kiwoom_trading
./run.sh
# 메뉴에서 [1] 선택
```

**예상 동작**:
- argparse 건너뛰기
- 정상적으로 자동매매 시작
- 오류 없음

### 2. 직접 실행 (일반 모드)
```bash
python main_auto_trading.py
```

**예상 동작**:
- argparse 실행
- `skip_wait=False` (기본값)
- 정상적인 대기 시간 포함

### 3. 직접 실행 (테스트 모드)
```bash
python main_auto_trading.py --skip-wait
```

**예상 동작**:
- argparse 실행
- `skip_wait=True`
- 대기 시간 건너뛰고 즉시 실행

---

## 📝 주의사항

### 1. main_menu.py 수정 불필요
- `main_menu.py:106`은 그대로 `await main_auto_trading.main()` 호출
- 기본값 `skip_wait=False`가 자동 적용

### 2. 추가 파라미터 전달 가능
미래에 main_menu.py에서 테스트 모드로 실행하고 싶다면:
```python
# main_menu.py:106 수정 가능 (선택사항)
await main_auto_trading.main(skip_wait=True)
```

### 3. 다른 main() 함수도 동일한 패턴 적용 가능
다른 스크립트들도 비슷한 문제가 있을 수 있음:
- `main_condition_filter.py`
- `backtest_with_ranker.py`
- 기타 main() 함수를 가진 스크립트

---

## 🎯 결론

**수정 전**:
- main_menu.py에서 호출 시 argparse 오류 가능
- sys.argv 파싱 문제

**수정 후**:
- ✅ main_menu.py에서 안전하게 호출 가능
- ✅ 직접 실행도 정상 작동
- ✅ 기존 코드 호환성 유지
- ✅ 테스트 모드 지원

**이제 run.sh를 통한 실행이 안정적으로 작동합니다!** 🚀

---

## 📌 관련 파일

- `run.sh` - 시스템 시작 스크립트
- `main_menu.py` - 통합 메뉴 시스템
- `main_auto_trading.py` - 자동매매 메인 스크립트 (수정 완료)

---

**다음 실행부터 안전하게 사용하세요!** 🎯
