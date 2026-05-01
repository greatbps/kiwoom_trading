#!/usr/bin/env python3
"""
통합 트레이딩 시스템 (Windows / Linux 공통)

  python run.py              대시보드 → 파이프라인 (백그라운드)
  python run.py -f           대시보드 → 파이프라인 (포그라운드)
  python run.py status       상태 확인
  python run.py stop         중지
  python run.py logs         로그 (tail -f)
"""
import os, sys, signal, subprocess, time, platform
from pathlib import Path
from datetime import datetime

SCRIPT_DIR = Path(__file__).parent.resolve()
PID_FILE   = SCRIPT_DIR / ".auto_trading.pid"
LOG_DIR    = SCRIPT_DIR / "logs"
IS_WIN     = platform.system() == "Windows"

# venv Python 우선, 없으면 현재 인터프리터
_venv = SCRIPT_DIR / ("venv/Scripts/python.exe" if IS_WIN else "venv/bin/python")
PYTHON = str(_venv) if _venv.exists() else sys.executable

# ANSI 색상 (Windows Terminal / VS Code 터미널은 기본 지원)
if IS_WIN:
    os.system("")          # cmd.exe에서 ANSI 활성화
R = "\033[0;31m"; G = "\033[0;32m"; Y = "\033[1;33m"
C = "\033[0;36m"; NC = "\033[0m"


# ─────────────────────────── 프로세스 관리 ───────────────────────────

def is_running():
    """(alive: bool, pid: int|None)"""
    if not PID_FILE.exists():
        return False, None
    try:
        pid = int(PID_FILE.read_text().strip())
        os.kill(pid, 0)          # 0 = 존재 확인만, 실제로 시그널 보내지 않음
        return True, pid
    except (OSError, ValueError, PermissionError):
        PID_FILE.unlink(missing_ok=True)
        return False, None


def _spawn_background(log_file: Path) -> subprocess.Popen:
    """플랫폼별 백그라운드 프로세스 생성 (부모 종료 후에도 유지)"""
    with open(log_file, "a") as lf:
        kwargs = dict(
            args=[PYTHON, "main_auto_trading.py"],
            cwd=SCRIPT_DIR,
            stdout=lf,
            stderr=subprocess.STDOUT,
        )
        if IS_WIN:
            kwargs["creationflags"] = (
                subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
            )
        else:
            kwargs["start_new_session"] = True

        return subprocess.Popen(**kwargs)


def _kill(pid: int):
    """프로세스 + 자식까지 강제 종료"""
    try:
        if IS_WIN:
            subprocess.run(
                ["taskkill", "/T", "/F", "/PID", str(pid)],
                capture_output=True,
            )
        else:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
            time.sleep(3)
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    except Exception:
        pass


# ─────────────────────────── 명령 구현 ───────────────────────────────

def run_dashboard():
    print(f"\n{C}═══════════════════════════════════════════════════════════{NC}")
    print(f"{G}  [1/2] 포트폴리오 현황{NC}")
    print(f"{C}═══════════════════════════════════════════════════════════{NC}")
    subprocess.run([PYTHON, "main_trading.py"], cwd=SCRIPT_DIR)


def run_foreground():
    print(f"\n{C}═══════════════════════════════════════════════════════════{NC}")
    print(f"{G}  [2/2] 파이프라인 + 모니터링 (포그라운드){NC}")
    print(f"{C}═══════════════════════════════════════════════════════════{NC}")
    print(f"\n  {Y}Ctrl+C 로 종료{NC}\n")
    subprocess.run([PYTHON, "main_auto_trading.py"], cwd=SCRIPT_DIR)


def run_background():
    alive, pid = is_running()
    if alive:
        print(f"{G}[파이프라인]{NC} 이미 실행 중 (PID: {pid})")
        return

    print(f"\n{C}═══════════════════════════════════════════════════════════{NC}")
    print(f"{G}  [2/2] 파이프라인 + 모니터링 (백그라운드){NC}")
    print(f"{C}═══════════════════════════════════════════════════════════{NC}\n")

    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"auto_trading_{datetime.now().strftime('%Y%m%d')}.log"

    proc = _spawn_background(log_file)
    PID_FILE.write_text(str(proc.pid))

    time.sleep(3)
    alive, pid = is_running()
    if alive:
        print(f"{G}[성공]{NC} PID: {pid}")
        print(f"\n  로그  : {log_file}")
        print(f"  상태  : python run.py status")
        print(f"  중지  : python run.py stop\n")
    else:
        print(f"{R}[실패]{NC} 로그 확인: {log_file}")
        PID_FILE.unlink(missing_ok=True)


def stop_pipeline():
    alive, pid = is_running()
    if not alive:
        print(f"{Y}[알림]{NC} 실행 중 아님")
        return

    print(f"{Y}[중지]{NC} PID: {pid}")
    _kill(pid)
    PID_FILE.unlink(missing_ok=True)
    print(f"{G}[완료]{NC}")


def show_status():
    print(f"\n{C}═══════════════════════════════════════════════════════════{NC}")
    print(f"현재: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} KST")
    print(f"{C}═══════════════════════════════════════════════════════════{NC}\n")
    alive, pid = is_running()
    if alive:
        print(f"{G}●{NC} 파이프라인 실행 중 (PID: {pid})")
    else:
        print(f"{R}●{NC} 파이프라인 중지")
    print()


def show_logs():
    log_file = LOG_DIR / f"auto_trading_{datetime.now().strftime('%Y%m%d')}.log"
    if not log_file.exists():
        candidates = sorted(
            LOG_DIR.glob("auto_trading_*.log"),
            key=lambda p: p.stat().st_mtime, reverse=True,
        )
        log_file = candidates[0] if candidates else None

    if not log_file or not log_file.exists():
        print("로그 없음")
        return

    print(f"{G}[로그]{NC} {log_file}")
    try:
        with open(log_file) as f:
            f.seek(0, 2)                    # EOF로 이동 (tail -f 동작)
            while True:
                line = f.readline()
                if line:
                    print(line, end="", flush=True)
                else:
                    time.sleep(0.3)
    except KeyboardInterrupt:
        pass


# ─────────────────────────── 진입점 ──────────────────────────────────

HELP = f"""
사용법: python run.py [옵션]

  (없음)       대시보드 → 파이프라인 (백그라운드)
  -f           대시보드 → 파이프라인 (포그라운드)
  status       상태 확인
  stop         중지
  logs         로그 보기 (tail -f)
"""

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ("-f", "--foreground", "foreground"):
        run_dashboard(); run_foreground()
    elif cmd == "status":
        show_status()
    elif cmd == "stop":
        stop_pipeline()
    elif cmd in ("logs", "log"):
        show_logs()
    elif cmd in ("help", "-h", "--help"):
        print(HELP)
    elif cmd == "":
        run_dashboard(); run_background()
    else:
        print(f"{R}알 수 없는 옵션: {cmd}{NC}")
        print("python run.py help")


if __name__ == "__main__":
    main()
