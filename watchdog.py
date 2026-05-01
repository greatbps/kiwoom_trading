#!/usr/bin/env python3
"""
watchdog.py — Kiwoom Trading 좀비 프로세스 감지 & 자동 재시작

cron 등록 (weekdays):
  45 8 * * 1-5  cd /home/greatbps/projects/kiwoom_trading && python3 watchdog.py >> logs/watchdog.log 2>&1
   0 9 * * 1-5  cd /home/greatbps/projects/kiwoom_trading && python3 watchdog.py >> logs/watchdog.log 2>&1
  15 9 * * 1-5  cd /home/greatbps/projects/kiwoom_trading && python3 watchdog.py >> logs/watchdog.log 2>&1

동작 흐름:
  1. PID 파일 확인 (/tmp/kiwoom_trading.pid)
  2. 프로세스 생존 확인 (os.kill(pid, 0))
  3. 하트비트 파일 확인 (/tmp/kiwoom_heartbeat.json)
     - 10분 이상 갱신 없으면 좀비로 판단
     - 하트비트의 날짜가 오늘이 아니면 (월요일 재시작 필요) 강제 재시작
  4. 좀비 감지 시: kill -9 → PID 파일 삭제 → 재시작
  5. PID 없을 때: 직접 시작
"""

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

# ─── 설정 ────────────────────────────────────────────────────────────────────
PROJECT_DIR   = Path(__file__).parent.resolve()
PID_FILE      = Path('/tmp/kiwoom_trading.pid')
HEARTBEAT_FILE = Path('/tmp/kiwoom_heartbeat.json')
MAIN_SCRIPT   = PROJECT_DIR / 'main_auto_trading.py'
LOG_DIR       = PROJECT_DIR / 'logs'

HEARTBEAT_MAX_AGE_SEC = 600   # 10분: 이 이상 갱신 없으면 좀비
STALE_DATE_FORCE_RESTART = True  # 하트비트 날짜가 오늘과 다르면 강제 재시작 (월요일 대응)

# ─── 로거 ─────────────────────────────────────────────────────────────────────
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [WATCHDOG] %(levelname)s %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger('watchdog')


# ─── 헬퍼 ─────────────────────────────────────────────────────────────────────

def read_pid() -> int | None:
    """PID 파일을 읽어 정수 반환. 없거나 파싱 실패 시 None."""
    try:
        return int(PID_FILE.read_text().strip())
    except Exception:
        return None


def is_process_alive(pid: int) -> bool:
    """프로세스 생존 여부 (kill 0으로 확인)."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def read_heartbeat() -> dict | None:
    """하트비트 JSON 파일 읽기. 없거나 파싱 실패 시 None."""
    try:
        data = json.loads(HEARTBEAT_FILE.read_text())
        return data
    except Exception:
        return None


def is_heartbeat_fresh(hb: dict) -> bool:
    """하트비트가 HEARTBEAT_MAX_AGE_SEC 이내인지 확인."""
    try:
        hb_time = datetime.fromisoformat(hb['time'])
        age = (datetime.now() - hb_time).total_seconds()
        return age < HEARTBEAT_MAX_AGE_SEC
    except Exception:
        return False


def is_heartbeat_today(hb: dict) -> bool:
    """하트비트 날짜가 오늘인지 확인 (월요일 전주 말 감지용)."""
    try:
        hb_date = datetime.fromisoformat(hb['time']).date()
        return hb_date == datetime.today().date()
    except Exception:
        return False


def kill_process(pid: int):
    """프로세스 강제 종료 + PID 파일 삭제."""
    logger.warning(f"좀비 프로세스 강제 종료: PID {pid}")
    try:
        os.kill(pid, signal.SIGKILL)
        time.sleep(2)
    except OSError as e:
        logger.info(f"kill 중 무시 가능한 오류: {e}")

    # PID 파일 삭제
    try:
        PID_FILE.unlink(missing_ok=True)
        logger.info("PID 파일 삭제 완료")
    except Exception as e:
        logger.error(f"PID 파일 삭제 실패: {e}")

    # 하트비트 파일 삭제
    try:
        HEARTBEAT_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def start_trading():
    """메인 트레이딩 스크립트 백그라운드 실행."""
    log_file = LOG_DIR / f"auto_trading_{datetime.today().strftime('%Y%m%d')}.log"
    cmd = [sys.executable, str(MAIN_SCRIPT)]
    logger.info(f"트레이딩 재시작: {' '.join(cmd)}")

    try:
        with open(log_file, 'a') as lf:
            proc = subprocess.Popen(
                cmd,
                cwd=str(PROJECT_DIR),
                stdout=lf,
                stderr=lf,
                start_new_session=True,  # 이 프로세스가 종료돼도 자식 유지
            )
        logger.info(f"✅ 재시작 완료: PID {proc.pid}")
        return proc.pid
    except Exception as e:
        logger.error(f"❌ 재시작 실패: {e}")
        return None


# ─── 메인 로직 ────────────────────────────────────────────────────────────────

def run():
    logger.info("=== Watchdog 실행 ===")
    today = datetime.today().date()
    now_str = datetime.now().strftime('%H:%M')

    pid = read_pid()
    hb  = read_heartbeat()

    # ── Case 1: PID 파일 없음 ──────────────────────────────────────────────
    if pid is None:
        logger.info("PID 파일 없음 → 트레이딩 시작")
        start_trading()
        return

    # ── Case 2: PID 파일 있음 → 프로세스 생존 확인 ─────────────────────────
    if not is_process_alive(pid):
        logger.warning(f"PID {pid} 프로세스 없음 (이미 종료됨) → PID 파일 정리 후 재시작")
        PID_FILE.unlink(missing_ok=True)
        HEARTBEAT_FILE.unlink(missing_ok=True)
        start_trading()
        return

    # ── Case 3: 프로세스 살아있음 → 하트비트 확인 ─────────────────────────
    if hb is None:
        logger.warning(f"PID {pid} 살아있으나 하트비트 파일 없음 → 좀비 의심")
        # 하트비트 파일 생성 전 초기 단계일 수 있으므로, 재시작은 신중히
        # PID가 최근에 시작됐을 수 있음 → PID mtime 확인
        try:
            pid_mtime = PID_FILE.stat().st_mtime
            pid_age = time.time() - pid_mtime
            if pid_age < HEARTBEAT_MAX_AGE_SEC:
                logger.info(f"PID 파일 {pid_age:.0f}초 전 생성됨 → 초기화 중으로 판단, 재시작 보류")
                return
        except Exception:
            pass
        logger.warning("하트비트 없이 장시간 경과 → 강제 재시작")
        kill_process(pid)
        start_trading()
        return

    # ── Case 4: 하트비트 날짜가 오늘이 아님 (월요일 전주 금요일 프로세스) ──
    if STALE_DATE_FORCE_RESTART and not is_heartbeat_today(hb):
        hb_date = datetime.fromisoformat(hb['time']).date()
        logger.warning(
            f"하트비트 날짜 불일치: {hb_date} → {today} "
            f"(전날/주말 프로세스 잔존) → 강제 재시작"
        )
        kill_process(pid)
        start_trading()
        return

    # ── Case 5: 하트비트 신선도 확인 ──────────────────────────────────────
    if not is_heartbeat_fresh(hb):
        hb_time_str = hb.get('time', 'unknown')
        hb_stage    = hb.get('stage', 'unknown')
        logger.warning(
            f"하트비트 만료: PID={pid}, stage={hb_stage}, "
            f"last_update={hb_time_str} → 좀비 프로세스 강제 재시작"
        )
        kill_process(pid)
        start_trading()
        return

    # ── 정상: 모든 체크 통과 ───────────────────────────────────────────────
    hb_stage = hb.get('stage', 'unknown')
    hb_time_str = hb.get('time', 'unknown')
    logger.info(
        f"✅ 정상 동작 중: PID={pid}, stage={hb_stage}, last_heartbeat={hb_time_str}"
    )


if __name__ == '__main__':
    run()
