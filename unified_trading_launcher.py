#!/usr/bin/env python3
"""
통합 트레이딩 모니터링 런처

기존 파이프라인들을 시간대별로 자동 관리:
- swing_trader_pipeline (국내 중기) - 키움
- trading_system (국내 단기) - 한투
- oversea2 (해외) - 한투

시간대:
- 국내: 09:00-15:30 KST
- 미국: 전장/장중/장후
"""

import asyncio
import subprocess
import signal
import sys
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from enum import Enum
from typing import Optional, Dict, List
import logging

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("UnifiedLauncher")


class MarketPhase(Enum):
    CLOSED = "closed"
    KR_REGULAR = "kr_regular"
    US_PREMARKET = "us_premarket"
    US_REGULAR = "us_regular"
    US_AFTERHOURS = "us_afterhours"


class MarketScheduleManager:
    def __init__(self):
        self.kst = ZoneInfo('Asia/Seoul')
        self.et = ZoneInfo('America/New_York')

    def get_current_phases(self) -> List[MarketPhase]:
        """현재 활성 시장들 반환 (동시에 여러 시장 가능)"""
        now_kst = datetime.now(self.kst)
        now_et = datetime.now(self.et)
        phases = []

        if now_kst.weekday() >= 5:
            return [MarketPhase.CLOSED]

        kst_time = now_kst.time()
        et_time = now_et.time()

        # 국내장 09:00-15:30
        if time(9, 0) <= kst_time <= time(15, 30):
            phases.append(MarketPhase.KR_REGULAR)

        # 미국 시간 (국내장과 동시 가능)
        if time(4, 0) <= et_time < time(9, 30):
            phases.append(MarketPhase.US_PREMARKET)
        elif time(9, 30) <= et_time < time(16, 0):
            phases.append(MarketPhase.US_REGULAR)
        elif time(16, 0) <= et_time < time(20, 0):
            phases.append(MarketPhase.US_AFTERHOURS)

        return phases if phases else [MarketPhase.CLOSED]

    def get_current_phase(self) -> MarketPhase:
        """하위 호환용"""
        phases = self.get_current_phases()
        return phases[0] if phases else MarketPhase.CLOSED

    def get_phase_korean(self, phase: MarketPhase) -> str:
        names = {
            MarketPhase.CLOSED: "장 마감",
            MarketPhase.KR_REGULAR: "국내 정규장",
            MarketPhase.US_PREMARKET: "미국 전장",
            MarketPhase.US_REGULAR: "미국 정규장",
            MarketPhase.US_AFTERHOURS: "미국 장후",
        }
        return names.get(phase, "알 수 없음")


class ProcessManager:
    def __init__(self):
        self.processes: Dict[str, subprocess.Popen] = {}

    def start_process(self, name: str, cmd: List[str], cwd: str) -> bool:
        if name in self.processes and self.processes[name].poll() is None:
            return True

        try:
            process = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True
            )
            self.processes[name] = process
            logger.info(f"[START] {name} (PID: {process.pid})")
            return True
        except Exception as e:
            logger.error(f"[ERROR] {name} 시작 실패: {e}")
            return False

    def stop_process(self, name: str) -> bool:
        if name not in self.processes:
            return True

        process = self.processes[name]
        if process.poll() is not None:
            del self.processes[name]
            return True

        try:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            del self.processes[name]
            logger.info(f"[STOP] {name}")
            return True
        except Exception as e:
            logger.error(f"[ERROR] {name} 중지 실패: {e}")
            return False

    def stop_all(self):
        for name in list(self.processes.keys()):
            self.stop_process(name)

    def is_running(self, name: str) -> bool:
        if name not in self.processes:
            return False
        return self.processes[name].poll() is None


class UnifiedTradingLauncher:
    def __init__(self):
        self.schedule_manager = MarketScheduleManager()
        self.process_manager = ProcessManager()
        self.running = True

        self.projects = {
            # swing_trader_pipeline - venv 없어서 비활성화
            # "swing_trader_pipeline": {
            #     "path": "/home/greatbps/projects/swing_trader_pipeline",
            #     "venv": "/home/greatbps/projects/swing_trader_pipeline/venv",
            #     "cmd": ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8001"],
            #     "markets": [MarketPhase.KR_REGULAR],
            #     "description": "국내 중기 스윙 (키움)"
            # },
            "trading_system": {
                "path": "/home/greatbps/projects/trading_system",
                "venv": "/home/greatbps/projects/trading_system/venv",
                "cmd": ["python", "background_monitoring_service.py"],
                "markets": [MarketPhase.KR_REGULAR],
                "description": "국내 단기 (한투)"
            },
            "oversea_trading": {
                "path": "/home/greatbps/projects/oversea2",
                "venv": "/home/greatbps/projects/oversea2/.venv",
                "cmd": ["python", "main_trading_daemon.py"],
                "markets": [MarketPhase.US_PREMARKET, MarketPhase.US_REGULAR, MarketPhase.US_AFTERHOURS],
                "description": "해외 (한투)"
            }
        }

    def get_venv_cmd(self, project_name: str) -> List[str]:
        project = self.projects[project_name]
        venv_path = project["venv"]
        cmd = project["cmd"].copy()

        if cmd[0] == "python":
            cmd[0] = f"{venv_path}/bin/python"
        elif cmd[0] == "uvicorn":
            cmd[0] = f"{venv_path}/bin/uvicorn"

        return cmd

    def should_run(self, project_name: str, phases: List[MarketPhase]) -> bool:
        project_markets = self.projects[project_name]["markets"]
        return any(phase in project_markets for phase in phases)

    def manage_processes(self, phases: List[MarketPhase]):
        for name, project in self.projects.items():
            should_be_running = self.should_run(name, phases)
            is_running = self.process_manager.is_running(name)

            if should_be_running and not is_running:
                logger.info(f"[MARKET] {project['description']} 시작")
                cmd = self.get_venv_cmd(name)
                self.process_manager.start_process(name, cmd, project["path"])

            elif not should_be_running and is_running:
                logger.info(f"[MARKET] {project['description']} 중지 (장 마감)")
                self.process_manager.stop_process(name)

    def print_status(self, phase: MarketPhase):
        phase_korean = self.schedule_manager.get_phase_korean(phase)
        now_kst = datetime.now(self.schedule_manager.kst)
        now_et = datetime.now(self.schedule_manager.et)

        print()
        print("=" * 60)
        print(f"[{now_kst.strftime('%H:%M:%S')}] {phase_korean}")
        print(f"한국: {now_kst.strftime('%H:%M')} | 미국: {now_et.strftime('%H:%M')} ET")
        print("-" * 60)

        for name, project in self.projects.items():
            is_running = self.process_manager.is_running(name)
            status = "🟢 실행중" if is_running else "⚫ 대기"
            print(f"  {status} {project['description']}")

        print("=" * 60)

    async def run(self):
        last_phases = []
        check_interval = 60

        def signal_handler(signum, frame):
            logger.info("종료 시그널 수신...")
            self.running = False

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        logger.info("통합 모니터링 시작...")

        try:
            while self.running:
                phases = self.schedule_manager.get_current_phases()

                if set(phases) != set(last_phases):
                    phase_names = [self.schedule_manager.get_phase_korean(p) for p in phases]
                    logger.info(f"[PHASE] 시장 단계: {', '.join(phase_names)}")
                    last_phases = phases

                self.manage_processes(phases)

                # 10분마다 상태 출력
                now = datetime.now()
                if now.minute % 10 == 0 and now.second < check_interval:
                    self.print_status(phases[0] if phases else MarketPhase.CLOSED)

                await asyncio.sleep(check_interval)

        finally:
            logger.info("모든 프로세스 종료 중...")
            self.process_manager.stop_all()
            logger.info("통합 모니터링 종료")


async def main():
    launcher = UnifiedTradingLauncher()
    await launcher.run()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n종료")
