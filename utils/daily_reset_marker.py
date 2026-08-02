"""
utils/daily_reset_marker.py

main_auto_trading.py의 daily_routine()이 "오늘 이미 일일 리셋을 실행했는지"를
프로세스 재시작에도 살아남게 판별하기 위한 초소형 유틸.

배경: daily_routine()은 순수 시계 기반(08:50 지났으면 바로 실행)이라, 장중에
프로세스가 재시작되면(watchdog 등) 오늘 이미 거래가 진행 중이었어도 이를
"새로운 하루"로 오인해 market_context/일일 카운터/DrawdownEngine 등 전체
일일 리셋 블록을 다시 실행해버리는 문제가 있었다(2026-07-27 감사에서 발견).

이 모듈은 별도로 분리해 main_auto_trading.py를 임포트하지 않고도 단위 테스트
가능하게 한다.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional


def should_run_daily_reset(marker_path: Path, today: Optional[date] = None) -> bool:
    """
    오늘 일일 리셋을 실행해야 하면 True, 이미 실행된 적 있으면 False.
    True를 반환하는 경우 마커 파일에 오늘 날짜를 즉시 기록한다(다음 호출부터 False).
    """
    today = today or date.today()
    today_str = today.isoformat()

    try:
        if marker_path.exists() and marker_path.read_text(encoding='utf-8').strip() == today_str:
            return False
    except Exception:
        pass  # 마커 파일 손상 시 안전하게 "리셋 필요"로 취급

    try:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(today_str, encoding='utf-8')
    except Exception:
        pass  # 마커 기록 실패해도 리셋 자체는 진행 (fail-open이 아니라 "평소처럼 리셋"이 안전한 기본값)

    return True
