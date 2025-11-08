"""
프로그램 매매 현황 조회 테스트
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
import json


def test_program_trading():
    """프로그램 매매 현황 조회 테스트"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    print(f"=== 종목별 프로그램 매매 현황 조회 ===")

    try:
        # 토큰 발급
        print("\n[1] 토큰 발급 중...")
        api.get_access_token()

        # 프로그램 매매 현황 조회 (코스피)
        print("\n[2] 프로그램 매매 현황 조회 중 (코스피)...")
        result = api.get_program_trading(
            mrkt_tp="P00101",  # 코스피
            stex_tp="1"        # KRX
        )

        print("\n[3] 응답 데이터:")
        print(json.dumps(result, indent=2, ensure_ascii=False))

        # 주요 데이터 추출
        if 'stk_prm_trde_prst' in result:
            data_list = result['stk_prm_trde_prst']

            print(f"\n[4] 프로그램 매매 TOP 10:")
            print("-" * 100)
            print(f"{'종목명':<15} {'현재가':>12} {'등락':>8} {'매수금액':>15} {'매도금액':>15} {'순매수':>15} {'거래비중':>10}")
            print("-" * 100)

            if isinstance(data_list, list) and len(data_list) > 0:
                # 순매수 금액 기준으로 정렬 (절대값 큰 순)
                sorted_data = sorted(
                    data_list[:20],  # 상위 20개만
                    key=lambda x: abs(int(x.get('netprps_prica', '0').replace(',', '').replace('+', '').replace('-', '') or '0')),
                    reverse=True
                )[:10]

                for item in sorted_data:
                    stk_nm = item.get('stk_nm', 'N/A')
                    cur_prc = item.get('cur_prc', 'N/A')
                    pred_pre = item.get('pred_pre', 'N/A')
                    buy_amt = item.get('buy_cntr_amt', '0')
                    sel_amt = item.get('sel_cntr_amt', '0')
                    net_amt = item.get('netprps_prica', '0')
                    trde_rt = item.get('all_trde_rt', '0.00')

                    # 등락 부호
                    flu_sig = item.get('flu_sig', '3')
                    if flu_sig == '2':
                        sign = '▲'
                    elif flu_sig == '5':
                        sign = '▼'
                    else:
                        sign = '-'

                    print(f"{stk_nm:<15} {cur_prc:>12} {sign}{pred_pre:>7} {buy_amt:>15} {sel_amt:>15} {net_amt:>15} {trde_rt:>10}")

                print("\n" + "=" * 100)

                # 프로그램 매매 분석
                print(f"\n[5] 프로그램 매매 분석:")

                total_buy = sum(int(item.get('buy_cntr_amt', '0').replace(',', '') or '0') for item in data_list)
                total_sel = sum(int(item.get('sel_cntr_amt', '0').replace(',', '') or '0') for item in data_list)
                net_buy = total_buy - total_sel

                print(f"  총 매수금액: {total_buy:,}원")
                print(f"  총 매도금액: {total_sel:,}원")
                print(f"  순매수금액: {net_buy:+,}원")

                if net_buy > 0:
                    print(f"  ✅ 프로그램 순매수 (상승 지지)")
                elif net_buy < 0:
                    print(f"  ❌ 프로그램 순매도 (하락 압력)")
                else:
                    print(f"  ➡️  프로그램 중립")

                # 상위 종목 매매 패턴
                print(f"\n[6] 상위 종목 매매 패턴:")
                for i, item in enumerate(sorted_data[:5], 1):
                    stk_nm = item.get('stk_nm', 'N/A')
                    net_amt = item.get('netprps_prica', '0')

                    try:
                        net_val = int(net_amt.replace(',', '').replace('+', '').replace('-', '') or '0')
                        if net_amt.startswith('-'):
                            net_val = -net_val

                        if net_val > 0:
                            print(f"  {i}. {stk_nm}: 순매수 {net_val:,}원 ⬆️")
                        elif net_val < 0:
                            print(f"  {i}. {stk_nm}: 순매도 {abs(net_val):,}원 ⬇️")
                        else:
                            print(f"  {i}. {stk_nm}: 보합 ➡️")
                    except Exception:
                        print(f"  {i}. {stk_nm}: 데이터 파싱 오류")

            else:
                print("데이터가 없습니다.")
        else:
            print("응답 형식이 예상과 다릅니다.")

        print("\n✓ 프로그램 매매 현황 조회 완료")

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 세션 종료
        api.close()


if __name__ == "__main__":
    test_program_trading()
