"""
키움증권 API 분봉 차트 조회 테스트
"""
import sys
import os

# 상위 디렉토리의 모듈을 import하기 위해 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI


def test_minute_chart(stock_code: str = "005930", tic_scope: str = "3"):
    """분봉 차트 조회 테스트"""

    print("=" * 70)
    print(f"📊 {stock_code} 분봉 차트 조회 테스트")
    print("=" * 70)

    tic_scope_name = {
        "1": "1분봉", "3": "3분봉", "5": "5분봉",
        "10": "10분봉", "15": "15분봉", "30": "30분봉",
        "45": "45분봉", "60": "60분봉"
    }

    try:
        with KiwoomAPI() as api:
            print(f"\n[1단계] 접근 토큰 발급")
            print("-" * 70)
            token = api.get_access_token()
            print(f"✓ 토큰: {token[:30] if token else 'None'}...")

            print(f"\n[2단계] {tic_scope_name.get(tic_scope, tic_scope)} 차트 조회")
            print("-" * 70)

            result = api.get_minute_chart(
                stock_code=stock_code,
                tic_scope=tic_scope,
                upd_stkpc_tp="1"
            )

            # 응답 코드 확인
            return_code = result.get('return_code')
            return_msg = result.get('return_msg')

            print(f"✓ 응답 코드: {return_code}")
            print(f"✓ 응답 메시지: {return_msg}")

            if return_code != 0:
                print(f"✗ 조회 실패: {return_msg}")
                return

            # 차트 데이터 추출
            chart_data = result.get('stk_min_pole_chart_qry', [])
            print(f"✓ 조회된 봉 개수: {len(chart_data)}개")

            # 연속조회 정보
            next_key = result.get('next_key', '')
            cont_yn = result.get('cont_yn', 'N')
            print(f"✓ 연속조회 가능: {cont_yn} (next_key: {next_key[:20] if next_key else 'None'})")

            print(f"\n[3단계] 차트 데이터 분석")
            print("=" * 70)

            if not chart_data:
                print("✗ 차트 데이터가 없습니다.")
                return

            # 최근 5개 데이터 출력
            print(f"\n📈 최근 {min(5, len(chart_data))}개 봉 데이터:")
            print("-" * 70)
            print(f"{'시간':<20} {'시가':>10} {'고가':>10} {'저가':>10} {'종가':>10} {'거래량':>12}")
            print("-" * 70)

            for i, candle in enumerate(chart_data[:5]):
                time_str = candle.get('cntr_tm', '')  # YYYYMMDDHHMMSS
                # 시간 포맷 변환
                if len(time_str) == 14:
                    formatted_time = f"{time_str[0:4]}-{time_str[4:6]}-{time_str[6:8]} {time_str[8:10]}:{time_str[10:12]}"
                else:
                    formatted_time = time_str

                # 가격은 음수로 오므로 절대값 사용
                open_price = abs(int(candle.get('open_pric', 0)))
                high_price = abs(int(candle.get('high_pric', 0)))
                low_price = abs(int(candle.get('low_pric', 0)))
                cur_price = abs(int(candle.get('cur_prc', 0)))
                volume = int(candle.get('trde_qty', 0))

                print(f"{formatted_time:<20} {open_price:>10,} {high_price:>10,} {low_price:>10,} {cur_price:>10,} {volume:>12,}")

            # 통계 정보
            print(f"\n📊 통계 정보:")
            print("-" * 70)

            prices = [abs(int(c.get('cur_prc', 0))) for c in chart_data if c.get('cur_prc')]
            volumes = [int(c.get('trde_qty', 0)) for c in chart_data if c.get('trde_qty')]

            if prices:
                print(f"  • 최고가: {max(prices):,}원")
                print(f"  • 최저가: {min(prices):,}원")
                print(f"  • 평균가: {sum(prices)//len(prices):,}원")

            if volumes:
                print(f"  • 최대 거래량: {max(volumes):,}주")
                print(f"  • 평균 거래량: {sum(volumes)//len(volumes):,}주")

            print("\n" + "=" * 70)
            print("✓ 테스트 완료")
            print("=" * 70)

            return result

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    # 기본 테스트: 삼성전자 3분봉
    result = test_minute_chart("005930", "3")

    # 다른 봉 테스트하려면:
    # test_minute_chart("005930", "1")   # 1분봉
    # test_minute_chart("005930", "5")   # 5분봉
    # test_minute_chart("005930", "60")  # 60분봉
