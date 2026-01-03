"""
3개 전략 빠른 테스트 (분석 생략, 종목 리스트만)
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from kiwoom_ws_client import KiwoomWSClient
import time

def main():
    print("=" * 100)
    print("3개 전략 조건검색 테스트 (종목 리스트업)")
    print("=" * 100)

    # API 초기화
    api = KiwoomAPI()
    api.get_access_token()

    # WebSocket 클라이언트 생성 및 로그인
    ws_client = KiwoomWSClient()
    ws_client.login()
    time.sleep(2)

    # 조건검색식 목록 조회
    conditions = ws_client.get_condition_list()

    # 테스트할 3개 전략
    test_strategies = [
        {"seq": "31", "name": "Momentum 전략"},
        {"seq": "32", "name": "Breakout 전략"},
        {"seq": "33", "name": "EOD 전략"}
    ]

    all_results = {}

    for strategy in test_strategies:
        seq = strategy["seq"]
        name = strategy["name"]

        print(f"\n{'='*100}")
        print(f"[{name}] 조건검색 실행")
        print(f"{'='*100}")

        # 조건검색 실행
        result = ws_client.request_condition_search(seq)

        if result and result.get('return_code') == 0:
            stocks = result.get('data', [])
            stock_codes = [s['jmcode'].replace('A', '') for s in stocks]

            print(f"✓ 발견 종목: {len(stock_codes)}개")

            # 종목 정보 간단히 조회 (처음 10개만)
            stock_info_list = []
            for i, code in enumerate(stock_codes[:10]):
                try:
                    info = api.get_stock_info(code)
                    stock_info_list.append({
                        'code': code,
                        'name': info.get('stk_nm', code),
                        'price': info.get('cur_prc', 'N/A'),
                        'change_rate': info.get('flu_rt', 'N/A'),
                        'per': info.get('per', 'N/A'),
                        'pbr': info.get('pbr', 'N/A')
                    })
                    time.sleep(0.1)  # API 호출 제한
                except Exception as e:
                    print(f"  ⚠ {code} 정보 조회 실패: {e}")

            all_results[name] = {
                'total_count': len(stock_codes),
                'stock_codes': stock_codes,
                'stock_info': stock_info_list
            }

            # 결과 출력
            print(f"\n[상위 10개 종목]")
            print("─" * 100)
            print(f"{'순위':<4} {'종목코드':<8} {'종목명':<20} {'현재가':<12} {'등락률':<10} {'PER':<8} {'PBR':<8}")
            print("─" * 100)

            for idx, stock in enumerate(stock_info_list, 1):
                print(f"{idx:<4} {stock['code']:<8} {stock['name']:<20} "
                      f"{stock['price']:<12} {stock['change_rate']:<10} "
                      f"{stock['per']:<8} {stock['pbr']:<8}")

            if len(stock_codes) > 10:
                print(f"... 외 {len(stock_codes) - 10}개")

        else:
            print(f"✗ 조건검색 실패: {result}")
            all_results[name] = {'total_count': 0, 'stock_codes': [], 'stock_info': []}

        time.sleep(1)

    # 전체 요약
    print(f"\n\n{'='*100}")
    print("전체 요약")
    print(f"{'='*100}")

    for strategy_name, data in all_results.items():
        print(f"\n[{strategy_name}]")
        print(f"  총 종목 수: {data['total_count']}개")
        print(f"  종목 코드: {', '.join(data['stock_codes'][:20])}")
        if data['total_count'] > 20:
            print(f"             ... 외 {data['total_count'] - 20}개")

    print(f"\n{'='*100}")

if __name__ == "__main__":
    main()
