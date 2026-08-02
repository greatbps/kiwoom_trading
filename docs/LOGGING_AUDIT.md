# 로그 신뢰성 감사

> 생성일: 2026-07-27 | 운영 안정성 감사 v3.0 — Audit 6
> 오늘 세션 중 실제로 발생한 로그 유실 사고(§아래)를 계기로 구조 전체를 문서화한다.

## 구조

```
main_auto_trading.py 모듈 레벨 (라인 68-134 부근):

  _DailyFileHandler(logging.Handler)
    - 프로세스가 자정을 넘겨도 "기록 시점 날짜" 기준으로 파일을 갈아끼움
    - logging.FileHandler(path, mode='a')를 내부에서 별도로 오픈 (O_APPEND)
    - _at_fh = _DailyFileHandler('logs/auto_trading_{date}.log')  ← logger.info() 등의 실제 목적지

  _logging.basicConfig(handlers=[_at_fh] + (StreamHandler(stderr) if tty else []))
    - stderr가 TTY일 때만 콘솔 StreamHandler 추가 (nohup 등 비-TTY 환경에서는 중복기록 방지 목적)

  프로세스 실행 방식(watchdog.py:135-142, 정상):
    with open(log_file, 'a') as lf:              ← O_APPEND
        subprocess.Popen(stdout=lf, stderr=lf, ...)
```

정상 실행에서는 **두 개의 독립된 O_APPEND 파일 디스크립터**(watchdog의 stdout/stderr 리다이렉트 + `_DailyFileHandler`의 자체 핸들)가 같은 파일에 쓴다. O_APPEND는 각 write()가 커널 레벨에서 파일 끝을 원자적으로 재확인하므로, 두 fd가 동시에 append해도 서로 덮어쓰지 않는다 — **정상 재시작 경로는 안전**.

## 오늘 실제 발생한 사고

세션 중 Claude가 두 차례 수동 재시작을 하며 다음 명령을 사용:
```bash
nohup venv/bin/python main_auto_trading.py > logs/auto_trading_YYYYMMDD.log 2>&1 &
```
셸의 `>`는 **트렁케이트 오픈(비-append)** — O_APPEND 플래그가 없다. 결과적으로:
- 셸 리다이렉트 fd(비-append, 자신의 마지막 write 위치만 추적)
- `_DailyFileHandler`의 fd(O_APPEND, 항상 파일의 실제 끝에 씀)

두 fd가 서로 다른 오프셋 기준으로 동작하면서, 리치콘솔의 대량 카운트다운 출력(비-append fd)이 다음 write 시 `_DailyFileHandler`가 방금 삽입한 내용(REGIME 등 타임스탬프 로그)을 물리적으로 덮어씀 — **logger.info() 계열 기록만 선택적으로 유실**, 콘솔 출력 자체는 보존.

**실제 영향**: 거래 로직/판단에는 영향 없음(프로세스 자체는 정상 동작, 하트비트 정상). 순수 관측성(사후 로그 분석) 문제.

**재발 방지**: 이후 재시작은 `open(path, 'a')` 기반(watchdog과 동일 방식)으로 전환해 재발 없음을 확인(오늘 이후 재시작 3회, 로그 정상 확인).

## 그 외 확인 항목

- **append 모드**: 위와 같이 정상 경로는 안전, 수동 개입 시에만 위험 — 운영 가이드로 명문화(아래).
- **Daily Rotation**: `_ensure_today()`가 `datetime.date.today()` 비교로 자정 롤오버 시 새 파일로 전환 — 과거 "자정 넘겨도 전날 파일에 쌓이던 버그"(2026-07-07 수정 완료, 메모리 기록)의 재발 여부 확인함, 문제 없음.
- **watchdog 재시작**: `watchdog.py`의 좀비 판정(`하트비트 날짜 불일치`) 로직이 "방금 막 재시작해서 아직 첫 하트비트를 못 쓴 정상 프로세스"를 좀비로 오판해 강제 재시작하는 사례를 오늘 실제로 관찰함(08:45 크론 실행 시점과 프로세스 기동 타이밍이 겹칠 때). 치명적이진 않음(재시작이 또 재시작되는 정도) — 다만 `STARTUP_GRACE_SEC`(1시간) 판정 로직과 하트비트 날짜비교 로직이 상호작용하는 경계 케이스로, 후속 점검 대상으로 기록.
- **logger flush**: 별도 flush 호출 없이 Python logging 기본 동작(각 handler의 emit이 즉시 write) 사용 — 버퍼링으로 인한 유실 위험은 낮음(오늘 사고는 버퍼링이 아니라 fd 오프셋 경쟁이 원인이었음).
- **로그-DB 일치 여부**: `GATE_PIPELINE_AUDIT.md`에서 다룬 execute_buy() 사각지대가 여기에도 해당 — 로그에는 있지만 DB엔 없는 케이스가 다수 확인됨.

## 운영 가이드 (문서화만, 코드 변경 없음)

1. 프로세스를 수동으로 재시작해야 할 경우, `watchdog.py`의 `start_trading()` 방식(Python `open(path, 'a')` 기반)을 그대로 사용할 것.
2. 셸에서 `nohup ... > file &`(트렁케이트) 사용 금지. 부득이하면 최소 `>>`를 쓰되, 이 역시 `_DailyFileHandler`와의 이중 writer 구조 자체를 없애는 근본 해법은 아님을 인지할 것.
3. 근본 해법(코드 수정, 이번 감사 범위 밖 — 별도 승인 필요): `main_auto_trading.py`가 자체 stdout/stderr를 파일로 직접 열지 않고 `subprocess`/셸 레이어에 위임하거나, rich 콘솔 출력과 logger 출력을 처음부터 별도 파일로 분리하는 방안 검토.
