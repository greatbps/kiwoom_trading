"""
tests/unit/test_daily_routine_guard.py

utils/daily_reset_marker.py — daily_routine() 재시작 판별 가드 단위 테스트
(2026-07-27 운영 안정성 감사에서 발견: daily_routine()이 순수 시계기반이라
장중 재시작 시 일일 리셋 블록이 중복 실행되던 문제의 회귀 방지 테스트)

daily_routine() 자체는 WebSocket 연결 등을 포함해 단위테스트 불가능하므로,
분리된 마커 판별 함수만 직접 검증한다.
"""
import sys
import os
import tempfile
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from utils.daily_reset_marker import should_run_daily_reset


def _tmp_marker() -> Path:
    return Path(tempfile.mktemp(suffix='.txt'))


def test_no_marker_file_should_reset():
    """마커 파일 자체가 없으면(최초 실행) 리셋 필요."""
    marker = _tmp_marker()
    assert not marker.exists()
    assert should_run_daily_reset(marker, today=date(2026, 7, 27)) is True


def test_marker_written_after_first_call():
    """리셋 필요 판정 후 마커 파일에 오늘 날짜가 기록되어야 함."""
    marker = _tmp_marker()
    should_run_daily_reset(marker, today=date(2026, 7, 27))
    assert marker.exists()
    assert marker.read_text(encoding='utf-8').strip() == '2026-07-27'


def test_same_day_second_call_skips_reset():
    """같은 날 두 번째 호출(=장중 재시작 시뮬레이션) → False, 리셋 생략."""
    marker = _tmp_marker()
    today = date(2026, 7, 27)
    first = should_run_daily_reset(marker, today=today)
    second = should_run_daily_reset(marker, today=today)  # 재시작으로 다시 호출됐다고 가정
    assert first is True
    assert second is False, "장중 재시작 시 같은 날짜면 리셋을 다시 실행하면 안 됨"


def test_next_day_resets_again():
    """마커가 어제 날짜면 오늘은 다시 리셋 필요(정상적인 새 날)."""
    marker = _tmp_marker()
    yesterday = date(2026, 7, 26)
    today = date(2026, 7, 27)
    should_run_daily_reset(marker, today=yesterday)
    assert should_run_daily_reset(marker, today=today) is True
    assert marker.read_text(encoding='utf-8').strip() == '2026-07-27'


def test_corrupted_marker_file_defaults_to_reset():
    """마커 파일이 손상/읽기실패해도 예외 없이 '리셋 필요'로 안전하게 처리."""
    marker = _tmp_marker()
    marker.write_text('', encoding='utf-8')  # 빈 파일(오늘 날짜와 다름)
    assert should_run_daily_reset(marker, today=date(2026, 7, 27)) is True
