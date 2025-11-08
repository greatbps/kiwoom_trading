#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
utils/export_to_parquet.py

아카이브 데이터를 Parquet 파일로 백업
- ML 학습용 데이터 장기 보관
- 압축 효율적인 Parquet 포맷 사용
"""
import sys
from pathlib import Path
from datetime import datetime
import pandas as pd

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from database.trading_db import TradingDatabase
from rich.console import Console

console = Console()


def export_to_parquet(
    output_dir: str = "datasets",
    db_path: str = "data/trading.db",
    date_filter: str = None
):
    """
    아카이브 데이터를 Parquet 파일로 export

    Args:
        output_dir: 출력 디렉토리
        db_path: 데이터베이스 경로
        date_filter: 날짜 필터 (YYYY-MM-DD 형식, None이면 전체)
    """
    console.print()
    console.print("=" * 70, style="cyan")
    console.print(f"{'Parquet 백업 실행':^70}", style="bold cyan")
    console.print("=" * 70, style="cyan")
    console.print(f"실행 시각: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    console.print()

    # 출력 디렉토리 생성
    output_path = Path(output_dir)
    year_dir = output_path / str(datetime.now().year)
    year_dir.mkdir(parents=True, exist_ok=True)

    # 데이터 조회
    db = TradingDatabase(db_path)

    console.print("[1] 아카이브 데이터 조회 중...", style="yellow")

    import sqlite3
    with sqlite3.connect(db_path) as conn:
        query = "SELECT * FROM archive_candidates"
        params = []

        if date_filter:
            query += " WHERE DATE(date_detected) = ?"
            params.append(date_filter)

        df = pd.read_sql_query(query, conn, params=params if params else None)

    if df.empty:
        console.print("  ⚠️  백업할 데이터가 없습니다.", style="yellow")
        return

    console.print(f"  ✅ {len(df):,}개 레코드 조회 완료", style="green")

    # Parquet 저장
    console.print("\n[2] Parquet 파일 저장 중...", style="yellow")

    today = datetime.now().strftime("%Y-%m-%d")
    filename = f"{today}_candidates.parquet"
    output_file = year_dir / filename

    df.to_parquet(output_file, engine='pyarrow', compression='snappy', index=False)

    file_size_mb = output_file.stat().st_size / (1024 * 1024)
    console.print(f"  ✅ 저장 완료: {output_file}", style="green")
    console.print(f"     파일 크기: {file_size_mb:.2f} MB", style="dim")

    # ML 학습용 뷰도 export
    console.print("\n[3] ML 학습용 데이터 export...", style="yellow")

    with sqlite3.connect(db_path) as conn:
        ml_df = pd.read_sql_query("SELECT * FROM ml_training_features", conn)

    if not ml_df.empty:
        ml_filename = f"{today}_ml_features.parquet"
        ml_output_file = year_dir / ml_filename

        ml_df.to_parquet(ml_output_file, engine='pyarrow', compression='snappy', index=False)

        ml_file_size_mb = ml_output_file.stat().st_size / (1024 * 1024)
        console.print(f"  ✅ ML 데이터 저장 완료: {ml_output_file}", style="green")
        console.print(f"     레코드: {len(ml_df):,}개 | 크기: {ml_file_size_mb:.2f} MB", style="dim")
    else:
        console.print("  ⚠️  ML 학습용 데이터가 없습니다.", style="yellow")

    console.print()
    console.print("=" * 70, style="green")
    console.print(f"{'백업 완료':^70}", style="bold green")
    console.print("=" * 70, style="green")
    console.print(f"출력 디렉토리: {year_dir}")
    console.print()


def load_from_parquet(parquet_file: str) -> pd.DataFrame:
    """
    Parquet 파일 로드

    Args:
        parquet_file: Parquet 파일 경로

    Returns:
        DataFrame
    """
    return pd.read_parquet(parquet_file, engine='pyarrow')


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parquet 백업")
    parser.add_argument("--output-dir", type=str, default="datasets",
                        help="출력 디렉토리 (기본: datasets)")
    parser.add_argument("--db-path", type=str, default="data/trading.db",
                        help="데이터베이스 파일 경로")
    parser.add_argument("--date", type=str, default=None,
                        help="날짜 필터 (YYYY-MM-DD 형식)")

    args = parser.parse_args()

    export_to_parquet(
        output_dir=args.output_dir,
        db_path=args.db_path,
        date_filter=args.date
    )
