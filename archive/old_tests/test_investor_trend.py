"""
투자자별 매매 동향 조회 테스트
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
import json


def test_investor_trend():
    """투자자별 매매 동향 조회 테스트"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    # 삼성전자 종목코드
    stock_code = "005930"

    print(f"=== 종목별 투자자 기관별 매매 동향 조회 ===")
    print(f"종목코드: {stock_code}")

    try:
        # 토큰 발급
        print("\n[1] 토큰 발급 중...")
        api.get_access_token()

        # 투자자별 매매 동향 조회
        print("\n[2] 투자자별 매매 동향 조회 중...")
        result = api.get_investor_trend(
            stock_code=stock_code,
            amt_qty_tp="1",  # 금액
            trde_tp="0",     # 순매수
            unit_tp="1000"   # 천원 단위
        )

        print("\n[3] 응답 데이터:")
        print(json.dumps(result, indent=2, ensure_ascii=False))

        # 주요 데이터 추출
        if 'output' in result and 'stk_invsr_orgn' in result['output']:
            data = result['output']['stk_invsr_orgn']

            print("\n[4] 투자자별 순매수 현황 (천원):")
            print("-" * 60)

            if isinstance(data, list) and len(data) > 0:
                item = data[0]

                print(f"일자: {item.get('dt', 'N/A')}")
                print(f"\n주요 투자자:")
                print(f"  개인          : {item.get('ind_invsr', 'N/A'):>15}")
                print(f"  외국인        : {item.get('frgnr_invsr', 'N/A'):>15}")
                print(f"  기관계        : {item.get('orgn', 'N/A'):>15}")

                print(f"\n기관 세부:")
                print(f"  금융투자      : {item.get('fnnc_invt', 'N/A'):>15}")
                print(f"  보험          : {item.get('insrnc', 'N/A'):>15}")
                print(f"  투신          : {item.get('invtrt', 'N/A'):>15}")
                print(f"  사모펀드      : {item.get('samo_fund', 'N/A'):>15}")
                print(f"  은행          : {item.get('bank', 'N/A'):>15}")
                print(f"  연기금등      : {item.get('penfnd_etc', 'N/A'):>15}")
                print(f"  기타금융      : {item.get('etc_fnnc', 'N/A'):>15}")
                print(f"  기타법인      : {item.get('etc_corp', 'N/A'):>15}")
                print(f"  국가,지자체   : {item.get('natn', 'N/A'):>15}")

                print("\n" + "=" * 60)

                # 수급 분석
                try:
                    ind = int(item.get('ind_invsr', '0').replace(',', ''))
                    frgn = int(item.get('frgnr_invsr', '0').replace(',', ''))
                    inst = int(item.get('orgn', '0').replace(',', ''))

                    print(f"\n[5] 수급 분석:")
                    if frgn > 0 and inst > 0:
                        print(f"  ✅ 외국인+기관 동반 매수 (강한 상승 신호)")
                    elif frgn > 0:
                        print(f"  ⬆️  외국인 매수 (상승 기대)")
                    elif inst > 0:
                        print(f"  ⬆️  기관 매수 (상승 기대)")
                    elif frgn < 0 and inst < 0:
                        print(f"  ❌ 외국인+기관 동반 매도 (약세 신호)")
                    else:
                        print(f"  ➡️  혼조세")

                    if ind < 0 and (frgn > 0 or inst > 0):
                        print(f"  ℹ️  개인 매도, 외국인/기관 매수 (전형적 상승 패턴)")
                    elif ind > 0 and (frgn < 0 or inst < 0):
                        print(f"  ⚠️  개인 매수, 외국인/기관 매도 (주의 필요)")

                except Exception as e:
                    print(f"  수급 분석 오류: {e}")

            else:
                print("데이터가 없습니다.")
        else:
            print("응답 형식이 예상과 다릅니다.")

        print("\n✓ 투자자별 매매 동향 조회 완료")

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 세션 종료
        api.close()


if __name__ == "__main__":
    test_investor_trend()
