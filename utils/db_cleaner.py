#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils/db_cleaner.py

데이터베이스 자동 정리 스크립트
- 오래된 비활성 후보 → archive로 이동
- 필터링 실패 종목 삭제
- 오래된 학습 완료 아카이브 삭제
"""
import sys
from pathlib import Path
from datetime import datetime

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database.trading_db import TradingDatabase
from rich.console import Console

console = Console()


def clean_database(
    archive_days: int = 7,
    failed_days: int = 3,
    old_archive_days: int = 180,
    db_path: str = "data/trading.db"
):
    """
    데이터베이스 정리 실행

    Args:
        archive_days: 비활성 후보를 archive로 이동할 경과 일수 (기본 7일)
        failed_days: 실패 종목 삭제 경과 일수 (기본 3일)
        old_archive_days: 오래된 아카이브 삭제 경과 일수 (기본 180일)
        db_path: 데이터베이스 경로
    """
    console.print()
    console.print("=" * 70, style="cyan")
    console.print(f"{'데이터베이스 자동 정리':^70}", style="bold cyan")
    console.print("=" * 70, style="cyan")
    console.print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    console.print()

    db = TradingDatabase(db_path)

    # 1. 오래된 비활성 후보 → archive로 이동
    console.print("[1] 비활성 후보 아카이브 이동...", style="yellow")
    archived_count = db.archive_old_candidates(days=archive_days)
    console.print(f"  ✅ {archived_count}개 레코드 이동 (>= {archive_days}일 경과)", style="green")

    # 2. 필터링 실패 종목 삭제
    console.print("\n[2] 필터링 실패 종목 정리...", style="yellow")
    failed_count = db.clean_failed_candidates(days=failed_days)
    console.print(f"  ✅ {failed_count}개 레코드 삭제 (>= {failed_days}일 경과)", style="green")

    # 3. 오래된 학습 완료 아카이브 삭제
    console.print("\n[3] 오래된 아카이브 정리...", style="yellow")
    old_archive_count = db.clean_old_archives(days=old_archive_days)
    console.print(f"  ✅ {old_archive_count}개 학습 완료 아카이브 삭제 (>= {old_archive_days}일 경과)", style="green")

    console.print()
    console.print("=" * 70, style="green")
    console.print(f"{'정리 완료':^70}", style="bold green")
    console.print("=" * 70, style="green")
    console.print(f"총 처리: archive {archived_count} | 실패 삭제 {failed_count} | 아카이브 삭제 {old_archive_count}")
    console.print()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="데이터베이스 자동 정리")
    parser.add_argument("--archive-days", type=int, default=7,
                        help="비활성 후보를 archive로 이동할 경과 일수 (기본: 7)")
    parser.add_argument("--failed-days", type=int, default=3,
                        help="실패 종목 삭제 경과 일수 (기본: 3)")
    parser.add_argument("--old-archive-days", type=int, default=180,
                        help="오래된 아카이브 삭제 경과 일수 (기본: 180)")
    parser.add_argument("--db-path", type=str, default="data/trading.db",
                        help="데이터베이스 파일 경로")

    args = parser.parse_args()

    clean_database(
        archive_days=args.archive_days,
        failed_days=args.failed_days,
        old_archive_days=args.old_archive_days,
        db_path=args.db_path
    )
