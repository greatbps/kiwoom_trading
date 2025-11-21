"""
DB 마이그레이션: trades 테이블에 context 컬럼 추가

기존 trading.db에 entry_context, exit_context, filter_scores 컬럼 추가
"""
import sqlite3
import sys
from pathlib import Path

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))


def migrate_add_context_columns(db_path: str = "data/trading.db"):
    """
    trades 테이블에 context 컬럼 추가

    Args:
        db_path: DB 파일 경로
    """
    print("=" * 80)
    print(f"DB 마이그레이션: {db_path}")
    print("=" * 80)

    db_file = Path(db_path)
    if not db_file.exists():
        print(f"❌ DB 파일이 존재하지 않습니다: {db_path}")
        print("   새로운 DB는 자동으로 생성됩니다.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # 1. 현재 테이블 구조 확인
    print("\n1. 현재 trades 테이블 구조 확인...")
    cursor.execute("PRAGMA table_info(trades)")
    columns = cursor.fetchall()
    column_names = [col[1] for col in columns]

    print(f"   총 {len(columns)}개 컬럼:")
    for col in columns:
        print(f"     - {col[1]}: {col[2]}")

    # 2. 컬럼 추가 (이미 있으면 스킵)
    print("\n2. 새 컬럼 추가...")

    columns_to_add = [
        ('entry_context', 'TEXT', '진입 시점 전체 지표 (JSON)'),
        ('exit_context', 'TEXT', '청산 시점 전체 지표 (JSON)'),
        ('filter_scores', 'TEXT', '진입 필터 점수 (JSON)'),
    ]

    added_count = 0
    for col_name, col_type, description in columns_to_add:
        if col_name in column_names:
            print(f"   ⏭️  {col_name}: 이미 존재함 (스킵)")
        else:
            try:
                cursor.execute(f"ALTER TABLE trades ADD COLUMN {col_name} {col_type}")
                print(f"   ✅ {col_name}: 추가 완료 - {description}")
                added_count += 1
            except sqlite3.OperationalError as e:
                print(f"   ❌ {col_name}: 추가 실패 - {e}")

    conn.commit()

    # 3. 마이그레이션 후 구조 확인
    print("\n3. 마이그레이션 후 구조 확인...")
    cursor.execute("PRAGMA table_info(trades)")
    new_columns = cursor.fetchall()

    print(f"   총 {len(new_columns)}개 컬럼 ({added_count}개 추가됨)")

    # 새로 추가된 컬럼만 표시
    if added_count > 0:
        print("\n   [새로 추가된 컬럼]")
        for col in new_columns:
            if col[1] in [c[0] for c in columns_to_add]:
                print(f"     - {col[1]}: {col[2]}")

    # 4. 기존 거래 데이터 확인
    cursor.execute("SELECT COUNT(*) FROM trades")
    trade_count = cursor.fetchone()[0]
    print(f"\n4. 기존 거래 데이터: {trade_count}건")

    if trade_count > 0:
        print("   ℹ️  기존 거래의 context 컬럼은 NULL입니다.")
        print("   ℹ️  새로운 매매부터 context가 저장됩니다.")

    conn.close()

    print("\n" + "=" * 80)
    print("✅ 마이그레이션 완료!")
    print("=" * 80)
    print("\n📝 변경 사항:")
    print(f"  - entry_context: 진입 시점 지표 (가격, VWAP, MA, RSI, Williams %R, 거래량 등)")
    print(f"  - exit_context: 청산 시점 지표 (최고가, 트레일링 정보, 부분청산 정보 등)")
    print(f"  - filter_scores: 진입 필터 통과/차단 정보")
    print("\n🚀 다음 단계:")
    print("  1. 실제 매매 시스템에서 동작 확인")
    print("  2. 거래 후 DB 조회하여 context 저장 확인")
    print("  3. ML 학습용 데이터 추출")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description='DB 마이그레이션: context 컬럼 추가')
    parser.add_argument('--db', default='data/trading.db', help='DB 파일 경로')
    args = parser.parse_args()

    migrate_add_context_columns(args.db)
