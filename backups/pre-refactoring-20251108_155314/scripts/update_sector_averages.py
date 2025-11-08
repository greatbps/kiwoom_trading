"""
업종별 평균 지표 계산 및 DB 저장 스크립트
월 1회 실행 권장 (cron: 0 2 1 * * 매월 1일 새벽 2시)
"""
import sys
import os
from datetime import datetime
import time
import statistics
from typing import Dict, List, Any

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from db.sector_data_manager import SectorDataManager


class SectorAverageUpdater:
    """업종별 평균 지표 계산 및 갱신"""

    def __init__(self):
        """초기화"""
        self.api = KiwoomAPI()
        self.db_manager = SectorDataManager()

        # API 호출 지연 (초당 최대 5건)
        self.api_delay = 0.2

    def get_sector_list_from_stocks(self, market_type: str = "0") -> Dict[str, List[str]]:
        """
        종목 리스트에서 업종별로 분류
        (업종코드 API가 없으므로 종목리스트의 upName을 사용)

        Args:
            market_type: 시장구분 (0:코스피, 10:코스닥)

        Returns:
            업종별 종목 딕셔너리 {'업종명': ['종목코드1', ...]}
        """
        print(f"\n[1] 종목 리스트 조회 및 업종별 분류 (시장: {market_type})")

        try:
            # 토큰 발급
            if not self.api.access_token:
                self.api.get_access_token()

            # 종목 리스트 조회 API 호출 (ka10099)
            url = f"{self.api.BASE_URL}/api/dostk/stkinfo"
            headers = {
                "Content-Type": "application/json;charset=UTF-8",
                "authorization": f"Bearer {self.api.access_token}",
                "cont-yn": "N",
                "next-key": "",
                "api-id": "ka10099"
            }
            data = {"mrkt_tp": market_type}

            response = self.api.session.post(url, headers=headers, json=data)
            response.raise_for_status()

            result = response.json()

            # 디버깅: 전체 응답 확인
            print(f"[DEBUG] API 응답 키: {result.keys()}")
            if 'return_code' in result:
                print(f"[DEBUG] return_code: {result.get('return_code')}")
                print(f"[DEBUG] return_msg: {result.get('return_msg')}")

            stock_list = result.get('list', [])

            print(f"✓ 종목 {len(stock_list)}개 조회")

            # 업종별로 종목 분류
            sector_stocks = {}
            no_sector_count = 0
            stock_mapping_data = []  # 종목-업종 매핑 데이터

            # 샘플 출력 (처음 5개)
            print(f"\n[샘플] 처음 5개 종목:")
            for i, stock in enumerate(stock_list[:5]):
                print(f"  {i+1}. code={stock.get('code')}, name={stock.get('name')}, upName='{stock.get('upName')}'")

            for stock in stock_list:
                code = stock.get('code', '')
                name = stock.get('name', '')
                sector_name = stock.get('upName', '').strip()

                if code:
                    if sector_name:
                        if sector_name not in sector_stocks:
                            sector_stocks[sector_name] = []
                        sector_stocks[sector_name].append(code)

                        # 매핑 데이터 저장
                        stock_mapping_data.append({
                            'stock_code': code,
                            'stock_name': name,
                            'sector_name': sector_name,
                            'market_type': market_type
                        })
                    else:
                        no_sector_count += 1

            print(f"\n[통계] upName 없는 종목: {no_sector_count}개")

            # 종목-업종 매핑 DB 저장
            self._save_stock_sector_mapping(stock_mapping_data)

            print(f"✓ {len(sector_stocks)}개 업종으로 분류")

            # 업종별 종목 수 출력
            for sector_name, codes in sorted(sector_stocks.items()):
                print(f"  - {sector_name}: {len(codes)}개 종목")

            return sector_stocks

        except Exception as e:
            print(f"✗ 종목 리스트 조회 실패: {e}")
            import traceback
            traceback.print_exc()
            return {}


    def _save_stock_sector_mapping(self, stock_mapping_data: List[Dict]):
        """
        종목-업종 매핑 데이터를 DB에 저장

        Args:
            stock_mapping_data: 종목 매핑 데이터 리스트
        """
        if not stock_mapping_data:
            return

        print(f"\n[매핑 저장] {len(stock_mapping_data)}개 종목 매핑 저장 중...")

        try:
            import psycopg2
            from psycopg2.extras import execute_batch

            # DB 연결 (db_manager와 별도)
            conn = psycopg2.connect(
                host=self.db_manager.db_config['host'],
                port=self.db_manager.db_config['port'],
                database=self.db_manager.db_config['database'],
                user=self.db_manager.db_config['user'],
                password=self.db_manager.db_config['password']
            )
            cursor = conn.cursor()

            # Upsert 쿼리
            query = """
                INSERT INTO stock_sector_mapping (stock_code, stock_name, sector_name, market_type, updated_at)
                VALUES (%(stock_code)s, %(stock_name)s, %(sector_name)s, %(market_type)s, CURRENT_TIMESTAMP)
                ON CONFLICT (stock_code)
                DO UPDATE SET
                    stock_name = EXCLUDED.stock_name,
                    sector_name = EXCLUDED.sector_name,
                    market_type = EXCLUDED.market_type,
                    updated_at = CURRENT_TIMESTAMP
            """

            execute_batch(cursor, query, stock_mapping_data, page_size=1000)
            conn.commit()

            cursor.close()
            conn.close()

            print(f"✓ {len(stock_mapping_data)}개 종목 매핑 저장 완료")

        except Exception as e:
            print(f"✗ 종목 매핑 저장 실패: {e}")

    def calculate_sector_average(self, stock_codes: List[str], sector_name: str) -> Dict[str, Any]:
        """
        업종별 평균 지표 계산

        Args:
            stock_codes: 종목 코드 리스트
            sector_name: 업종명

        Returns:
            평균 지표 데이터
        """
        print(f"\n  [{sector_name}] 종목 {len(stock_codes)}개 분석 중...")

        per_values = []
        pbr_values = []
        roe_values = []
        eps_values = []
        bps_values = []

        valid_count = 0
        error_count = 0

        for i, stock_code in enumerate(stock_codes):
            try:
                # API 호출 제한 대응
                time.sleep(self.api_delay)

                # 종목 정보 조회
                stock_info = self.api.get_stock_info(stock_code)

                # 지표 추출 (빈 문자열이 아닌 경우만)
                per = stock_info.get('per', '')
                pbr = stock_info.get('pbr', '')
                roe = stock_info.get('roe', '')
                eps = stock_info.get('eps', '')
                bps = stock_info.get('bps', '')

                # 숫자로 변환 가능한 경우만 추가
                if per and per.strip():
                    try:
                        per_val = float(per.replace(',', '').replace('+', '').replace('-', ''))
                        if 0 < per_val < 1000:  # 이상치 제거
                            per_values.append(per_val)
                    except:
                        pass

                if pbr and pbr.strip():
                    try:
                        pbr_val = float(pbr.replace(',', '').replace('+', '').replace('-', ''))
                        if 0 < pbr_val < 100:  # 이상치 제거
                            pbr_values.append(pbr_val)
                    except:
                        pass

                if roe and roe.strip():
                    try:
                        roe_val = float(roe.replace(',', '').replace('%', '').replace('+', '').replace('-', ''))
                        if -100 < roe_val < 100:  # 이상치 제거
                            roe_values.append(roe_val)
                    except:
                        pass

                if eps and eps.strip():
                    try:
                        eps_val = float(eps.replace(',', '').replace('+', '').replace('-', ''))
                        eps_values.append(eps_val)
                    except:
                        pass

                if bps and bps.strip():
                    try:
                        bps_val = float(bps.replace(',', '').replace('+', '').replace('-', ''))
                        if bps_val > 0:
                            bps_values.append(bps_val)
                    except:
                        pass

                valid_count += 1

                # 진행 상황 출력 (10개마다)
                if (i + 1) % 10 == 0:
                    print(f"    진행: {i+1}/{len(stock_codes)} ({valid_count}개 유효)")

            except Exception as e:
                error_count += 1
                if error_count < 5:  # 에러 5개까지만 출력
                    print(f"    ⚠ {stock_code} 조회 실패: {e}")

        # 평균 계산
        avg_per = statistics.mean(per_values) if per_values else None
        avg_pbr = statistics.mean(pbr_values) if pbr_values else None
        avg_roe = statistics.mean(roe_values) if roe_values else None
        avg_eps = statistics.mean(eps_values) if eps_values else None
        avg_bps = statistics.mean(bps_values) if bps_values else None

        per_str = f"{avg_per:.2f}" if avg_per else "N/A"
        pbr_str = f"{avg_pbr:.2f}" if avg_pbr else "N/A"
        roe_str = f"{avg_roe:.2f}" if avg_roe else "N/A"
        print(f"  ✓ 평균 PER: {per_str}, PBR: {pbr_str}, ROE: {roe_str}%")

        return {
            'avg_per': avg_per,
            'avg_pbr': avg_pbr,
            'avg_roe': avg_roe,
            'avg_eps': avg_eps,
            'avg_bps': avg_bps,
            'stock_count': len(stock_codes),
            'valid_per_count': len(per_values),
            'valid_pbr_count': len(pbr_values),
            'valid_roe_count': len(roe_values)
        }

    def update_all_sectors(self, market_types: List[str] = ["0", "10"]):
        """
        전체 업종 평균 갱신

        Args:
            market_types: 시장구분 리스트 (0:코스피, 10:코스닥)
        """
        print(f"\n{'='*60}")
        print(f"업종별 평균 지표 갱신 시작")
        print(f"시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")

        try:
            # DB 연결
            self.db_manager.connect()

            # 테이블 생성 (없으면)
            self.db_manager.create_table_if_not_exists()

            total_sectors = 0
            total_success = 0

            for market_type in market_types:
                print(f"\n\n{'='*60}")
                print(f"시장: {'코스피' if market_type == '0' else '코스닥'}")
                print(f"{'='*60}")

                # 종목 리스트 조회 및 업종별 분류
                sector_stocks = self.get_sector_list_from_stocks(market_type)

                if not sector_stocks:
                    print(f"⚠ 종목 리스트가 비어있습니다")
                    continue

                # 각 업종별 평균 계산 및 저장
                for sector_name, stock_codes in sector_stocks.items():
                    total_sectors += 1

                    # 업종 코드는 업종명을 해시한 값 사용 (간단하게 처리)
                    sector_code = str(hash(sector_name) % 10000).zfill(4)

                    # 평균 계산
                    avg_data = self.calculate_sector_average(stock_codes, sector_name)

                    # DB 저장
                    try:
                        sector_data = {
                            'sector_code': sector_code,
                            'sector_name': sector_name,
                            'market_type': market_type,
                            **avg_data
                        }

                        self.db_manager.upsert_sector_average(sector_data)
                        total_success += 1
                        print(f"  ✓ DB 저장 완료")

                    except Exception as e:
                        print(f"  ✗ DB 저장 실패: {e}")

            # 결과 요약
            print(f"\n\n{'='*60}")
            print(f"갱신 완료")
            print(f"종료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"{'='*60}")
            print(f"처리 업종: {total_sectors}개")
            print(f"성공: {total_success}개")
            print(f"실패: {total_sectors - total_success}개")
            print(f"{'='*60}\n")

        except Exception as e:
            print(f"\n✗ 전체 갱신 실패: {e}")
            import traceback
            traceback.print_exc()

        finally:
            # DB 연결 종료
            self.db_manager.close()
            self.api.close()


def main():
    """메인 함수"""
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║        업종별 평균 지표 계산 및 DB 저장 스크립트         ║
    ║                  (월 1회 실행 권장)                      ║
    ╚══════════════════════════════════════════════════════════╝
    """)

    updater = SectorAverageUpdater()

    # 코스피, 코스닥 전체 갱신
    updater.update_all_sectors(market_types=["0", "10"])


if __name__ == "__main__":
    main()
