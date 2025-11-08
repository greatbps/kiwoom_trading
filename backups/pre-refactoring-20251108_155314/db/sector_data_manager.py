"""
업종별 평균 데이터 관리 모듈
PostgreSQL에서 업종 평균 지표 조회
"""
import os
import psycopg2
from psycopg2.extras import RealDictCursor
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from datetime import datetime, timedelta


class SectorDataManager:
    """업종별 평균 데이터 관리 클래스"""

    def __init__(self):
        """초기화"""
        load_dotenv()

        # DB 연결 정보
        self.db_config = {
            'host': os.getenv('POSTGRES_HOST', 'localhost'),
            'port': int(os.getenv('POSTGRES_PORT', 5432)),
            'database': os.getenv('POSTGRES_DB', 'trading_system'),
            'user': os.getenv('POSTGRES_USER', 'postgres'),
            'password': os.getenv('POSTGRES_PASSWORD', '')
        }

        self.conn = None
        self.cursor = None

    def connect(self):
        """DB 연결"""
        try:
            self.conn = psycopg2.connect(**self.db_config)
            self.cursor = self.conn.cursor(cursor_factory=RealDictCursor)
        except Exception as e:
            print(f"✗ DB 연결 실패: {e}")
            raise

    def close(self):
        """DB 연결 종료"""
        if self.cursor:
            self.cursor.close()
        if self.conn:
            self.conn.close()

    def get_sector_averages(self, sector_code: str, market_type: str = "0") -> Optional[Dict[str, Any]]:
        """
        업종별 평균 지표 조회

        Args:
            sector_code: 업종 코드
            market_type: 시장구분 (0:코스피, 10:코스닥 등)

        Returns:
            업종 평균 데이터 또는 None
            {
                'sector_code': str,
                'sector_name': str,
                'avg_per': float,
                'avg_pbr': float,
                'avg_roe': float,
                'stock_count': int,
                'updated_at': datetime
            }
        """
        if not self.conn:
            self.connect()

        try:
            query = """
                SELECT
                    sector_code,
                    sector_name,
                    market_type,
                    avg_per,
                    avg_pbr,
                    avg_roe,
                    avg_eps,
                    avg_bps,
                    stock_count,
                    valid_per_count,
                    valid_pbr_count,
                    valid_roe_count,
                    updated_at
                FROM sector_averages
                WHERE sector_code = %s AND market_type = %s
                LIMIT 1
            """

            self.cursor.execute(query, (sector_code, market_type))
            result = self.cursor.fetchone()

            if result:
                return dict(result)
            return None

        except Exception as e:
            print(f"✗ 업종 평균 조회 실패: {e}")
            return None

    def get_sector_averages_by_name(self, sector_name: str, market_type: str = "0") -> Optional[Dict[str, Any]]:
        """
        업종명으로 평균 지표 조회

        Args:
            sector_name: 업종명 (예: '전기/전자')
            market_type: 시장구분 (0:코스피, 10:코스닥 등)

        Returns:
            업종 평균 데이터 또는 None
        """
        if not self.conn:
            self.connect()

        try:
            query = """
                SELECT
                    sector_code,
                    sector_name,
                    market_type,
                    avg_per,
                    avg_pbr,
                    avg_roe,
                    avg_eps,
                    avg_bps,
                    stock_count,
                    valid_per_count,
                    valid_pbr_count,
                    valid_roe_count,
                    updated_at
                FROM sector_averages
                WHERE sector_name = %s AND market_type = %s
                LIMIT 1
            """

            self.cursor.execute(query, (sector_name, market_type))
            result = self.cursor.fetchone()

            if result:
                return dict(result)
            return None

        except Exception as e:
            print(f"✗ 업종 평균 조회 실패: {e}")
            return None

    def is_data_stale(self, sector_code: str, market_type: str = "0", max_age_days: int = 30) -> bool:
        """
        데이터가 오래되었는지 확인

        Args:
            sector_code: 업종 코드
            market_type: 시장구분
            max_age_days: 최대 허용 일수 (기본 30일)

        Returns:
            True: 갱신 필요, False: 최신 데이터
        """
        data = self.get_sector_averages(sector_code, market_type)

        if not data:
            return True  # 데이터 없음 → 갱신 필요

        updated_at = data.get('updated_at')
        if not updated_at:
            return True

        # 현재 시간과 비교
        age = datetime.now() - updated_at
        return age.days >= max_age_days

    def upsert_sector_average(self, sector_data: Dict[str, Any]):
        """
        업종 평균 데이터 삽입/갱신

        Args:
            sector_data: {
                'sector_code': str,
                'sector_name': str,
                'market_type': str,
                'avg_per': float,
                'avg_pbr': float,
                'avg_roe': float,
                'avg_eps': float,
                'avg_bps': float,
                'stock_count': int,
                'valid_per_count': int,
                'valid_pbr_count': int,
                'valid_roe_count': int
            }
        """
        if not self.conn:
            self.connect()

        try:
            query = """
                INSERT INTO sector_averages (
                    sector_code, sector_name, market_type,
                    avg_per, avg_pbr, avg_roe, avg_eps, avg_bps,
                    stock_count, valid_per_count, valid_pbr_count, valid_roe_count,
                    updated_at
                ) VALUES (
                    %(sector_code)s, %(sector_name)s, %(market_type)s,
                    %(avg_per)s, %(avg_pbr)s, %(avg_roe)s, %(avg_eps)s, %(avg_bps)s,
                    %(stock_count)s, %(valid_per_count)s, %(valid_pbr_count)s, %(valid_roe_count)s,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT (sector_code, market_type)
                DO UPDATE SET
                    sector_name = EXCLUDED.sector_name,
                    avg_per = EXCLUDED.avg_per,
                    avg_pbr = EXCLUDED.avg_pbr,
                    avg_roe = EXCLUDED.avg_roe,
                    avg_eps = EXCLUDED.avg_eps,
                    avg_bps = EXCLUDED.avg_bps,
                    stock_count = EXCLUDED.stock_count,
                    valid_per_count = EXCLUDED.valid_per_count,
                    valid_pbr_count = EXCLUDED.valid_pbr_count,
                    valid_roe_count = EXCLUDED.valid_roe_count,
                    updated_at = CURRENT_TIMESTAMP
            """

            self.cursor.execute(query, sector_data)
            self.conn.commit()

        except Exception as e:
            print(f"✗ 업종 평균 저장 실패: {e}")
            if self.conn:
                self.conn.rollback()
            raise

    def get_all_sectors(self, market_type: Optional[str] = None) -> list:
        """
        전체 업종 목록 조회

        Args:
            market_type: 시장구분 (None이면 전체)

        Returns:
            업종 목록 리스트
        """
        if not self.conn:
            self.connect()

        try:
            if market_type:
                query = """
                    SELECT sector_code, sector_name, market_type, updated_at
                    FROM sector_averages
                    WHERE market_type = %s
                    ORDER BY sector_code
                """
                self.cursor.execute(query, (market_type,))
            else:
                query = """
                    SELECT sector_code, sector_name, market_type, updated_at
                    FROM sector_averages
                    ORDER BY market_type, sector_code
                """
                self.cursor.execute(query)

            results = self.cursor.fetchall()
            return [dict(row) for row in results]

        except Exception as e:
            print(f"✗ 업종 목록 조회 실패: {e}")
            return []

    def create_table_if_not_exists(self):
        """테이블이 없으면 생성"""
        if not self.conn:
            self.connect()

        try:
            # 스키마 파일 읽기
            schema_path = os.path.join(
                os.path.dirname(__file__),
                'schema',
                'sector_averages.sql'
            )

            if os.path.exists(schema_path):
                with open(schema_path, 'r', encoding='utf-8') as f:
                    schema_sql = f.read()
                    self.cursor.execute(schema_sql)
                    self.conn.commit()
                    print("✓ sector_averages 테이블 생성 완료")
            else:
                print(f"⚠ 스키마 파일을 찾을 수 없음: {schema_path}")

        except Exception as e:
            print(f"✗ 테이블 생성 실패: {e}")
            if self.conn:
                self.conn.rollback()
            raise

    def __enter__(self):
        """컨텍스트 매니저 진입"""
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """컨텍스트 매니저 종료"""
        self.close()


# 사용 예시
if __name__ == "__main__":
    # 컨텍스트 매니저 사용
    with SectorDataManager() as manager:
        # 테이블 생성
        manager.create_table_if_not_exists()

        # 업종 평균 조회
        sector = manager.get_sector_averages("001", "0")  # 코스피 음식료업
        if sector:
            print(f"\n업종: {sector['sector_name']}")
            print(f"평균 PER: {sector['avg_per']}")
            print(f"평균 PBR: {sector['avg_pbr']}")
            print(f"평균 ROE: {sector['avg_roe']}")
            print(f"갱신일: {sector['updated_at']}")
        else:
            print("업종 데이터 없음")
