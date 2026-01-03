"""
과거 분봉 데이터 조회 테스트
키움 API에서 과거 분봉 데이터를 얼마나 제공하는지 확인
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
import time

def test_historical_minute_data():
    """과거 분봉 데이터 조회 테스트"""

    api = KiwoomAPI()
    api.get_access_token()

    stock_code = "005930"  # 삼성전자

    print("="*80)
    print("과거 5분봉 데이터 조회 테스트")
    print("="*80)
    print(f"종목: 삼성전자 ({stock_code})")
    print()

    # 1차 조회 (연속조회 없이)
    print("[1차 조회] cont_yn=N")
    result = api.get_minute_chart(
        stock_code=stock_code,
        tic_scope='5',  # 5분봉
        cont_yn='N'
    )

    print(f"return_code: {result.get('return_code')}")
    print(f"return_msg: {result.get('return_msg')}")

    data = result.get('stk_dt_pole_minute_chart_qry', [])
    print(f"데이터 개수: {len(data)}개")

    if data:
        print(f"\n[최신 데이터]")
        print(f"  일자: {data[0].get('dt')}")
        print(f"  시간: {data[0].get('tic_tm')}")
        print(f"  종가: {data[0].get('cur_prc')}")

        print(f"\n[최초 데이터]")
        print(f"  일자: {data[-1].get('dt')}")
        print(f"  시간: {data[-1].get('tic_tm')}")
        print(f"  종가: {data[-1].get('cur_prc')}")

        # next_key 확인
        next_key = result.get('next_key', '')
        print(f"\nnext_key: {next_key[:50] if next_key else 'None'}...")

        if next_key:
            print("\n" + "="*80)
            print("[2차 조회] cont_yn=Y (과거 데이터 더 조회)")
            time.sleep(0.2)

            result2 = api.get_minute_chart(
                stock_code=stock_code,
                tic_scope='5',
                cont_yn='Y',
                next_key=next_key
            )

            data2 = result2.get('stk_dt_pole_minute_chart_qry', [])
            print(f"데이터 개수: {len(data2)}개")

            if data2:
                print(f"\n[최신 데이터]")
                print(f"  일자: {data2[0].get('dt')}")
                print(f"  시간: {data2[0].get('tic_tm')}")

                print(f"\n[최초 데이터]")
                print(f"  일자: {data2[-1].get('dt')}")
                print(f"  시간: {data2[-1].get('tic_tm')}")

                print(f"\n총 누적: {len(data) + len(data2)}개 (1차 {len(data)}개 + 2차 {len(data2)}개)")
    else:
        print("\n⚠️ 분봉 데이터 없음")
        print("  → 장 마감 후에는 당일 분봉 데이터만 제공됨")
        print("  → 과거 분봉은 장 시간 중에만 조회 가능할 수 있음")

    print("\n" + "="*80)

if __name__ == "__main__":
    test_historical_minute_data()
