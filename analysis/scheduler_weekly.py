"""
주간 자동 튜닝 스케줄러

실행:
    # 백그라운드 데몬으로 실행
    nohup python -m analysis.scheduler_weekly &

    # 또는 systemd 서비스 등록 (권장)

스케줄:
    매주 일요일 18:00 → 튜닝 + 카나리 배포
    매주 월요일 08:50 → 카나리 상태 확인 (1주 후)
    매주 월요일 08:55 → 전환 or 롤백

의존성:
    pip install schedule
"""

import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import schedule
except ImportError:
    print("[ERROR] schedule 미설치: pip install schedule")
    sys.exit(1)

from analysis.weekly_tuner import run_weekly_tuning
from analysis.safety_gate  import SafetyGate, deploy_if_pass, CANARY_YAML

# ── 로거 ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [SCHEDULER] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("logs/scheduler_weekly.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("scheduler_weekly")


# ── 작업 함수 ────────────────────────────────────────────────────────

def job_weekly_tuning():
    """일요일 18:00 — 튜닝 + 카나리 배포"""
    logger.info("=" * 50)
    logger.info("주간 튜닝 시작")
    try:
        result = run_weekly_tuning(weeks=12, n_trials=50)
        deploy_if_pass(result, auto_canary=True)
        logger.info("주간 튜닝 완료")
    except Exception as e:
        logger.error(f"주간 튜닝 실패: {e}", exc_info=True)


def job_canary_check():
    """월요일 08:50 — 1주일 카나리 성과 확인 → 전환 or 롤백"""
    logger.info("=" * 50)
    logger.info("카나리 상태 확인")

    gate = SafetyGate()

    if not CANARY_YAML.exists():
        logger.info("  카나리 배포 없음 — 스킵")
        return

    import yaml
    try:
        with open(CANARY_YAML, encoding="utf-8") as f:
            canary_cfg = yaml.safe_load(f)
    except Exception as e:
        logger.error(f"canary.yaml 로드 실패: {e}")
        gate.rollback()
        return

    ok, reason = gate.canary_status(min_trades=5)
    logger.info(f"  카나리 결과: {reason}")

    if ok:
        params = {
            "defensive": canary_cfg.get("defensive_mode", {}),
            "short":     canary_cfg.get("short_mode", {}),
        }
        gate.deploy_full(params)
        logger.info("  ✅ 프로덕션 전환 완료")
    else:
        gate.rollback()
        logger.warning(f"  ↩️ 롤백 완료. 이유: {reason}")


# ── 강제 실행 옵션 ───────────────────────────────────────────────────

def run_now():
    """즉시 튜닝 + 배포 (테스트용)"""
    logger.info("즉시 실행 모드")
    job_weekly_tuning()


# ── 스케줄 등록 + 루프 ───────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="주간 자동 튜닝 스케줄러")
    parser.add_argument("--now",    action="store_true", help="즉시 실행 (테스트)")
    parser.add_argument("--canary", action="store_true", help="카나리 체크만 즉시 실행")
    args = parser.parse_args()

    os.makedirs("logs", exist_ok=True)

    if args.now:
        run_now()
        return
    if args.canary:
        job_canary_check()
        return

    # 정기 스케줄 등록
    schedule.every().sunday.at("18:00").do(job_weekly_tuning)
    schedule.every().monday.at("08:50").do(job_canary_check)

    logger.info("스케줄러 시작:")
    logger.info("  일요일 18:00 → 주간 튜닝 + 카나리 배포")
    logger.info("  월요일 08:50 → 카나리 확인 → 전환/롤백")
    logger.info("  종료: Ctrl+C")

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        logger.info("스케줄러 종료")


if __name__ == "__main__":
    main()
