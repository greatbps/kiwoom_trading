"""
키움증권 API 일봉 차트 조회 테스트
"""
import sys
import os
from datetime import datetime, timedelta

# 상위 디렉토리의 모듈을 import하기 위해 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI


def test_daily_chart(stock_code: str = "005930", base_dt: str = None, days: int = 120):
    """
    일봉 차트 조회 테스트

    Args:
        stock_code: 종목코드
        base_dt: 기준일자 (YYYYMMDD, None이면 오늘)
        days: 조회할 일수 (기본 120일)
    """

    print("=" * 70)
    print(f"📊 {stock_code} 일봉 차트 조회 테스트")
    print("=" * 70)

    try:
        with KiwoomAPI() as api:
            print(f"\n[1단계] 접근 토큰 발급")
            print("-" * 70)
            token = api.get_access_token()
            print(f"✓ 토큰: {token[:30] if token else 'None'}...")

            print(f"\n[2단계] 일봉 차트 조회")
            print("-" * 70)

            # 기준일자 설정
            if not base_dt:
                base_dt = datetime.now().strftime("%Y%m%d")

            print(f"기준일자: {base_dt}")
            print(f"조회 목표: 최근 {days}일")

            all_chart_data = []
            next_key = ""
            cont_yn = "N"
            page = 1

            # 연속조회로 필요한 일수만큼 데이터 수집
            while len(all_chart_data) < days:
                print(f"\n조회 중... (페이지 {page}, 누적: {len(all_chart_data)}개)")

                result = api.get_daily_chart(
                    stock_code=stock_code,
                    base_dt=base_dt,
                    upd_stkpc_tp="1",
                    cont_yn=cont_yn,
                    next_key=next_key
                )

                # 응답 코드 확인
                return_code = result.get('return_code')
                return_msg = result.get('return_msg')

                if return_code != 0:
                    print(f"✗ 조회 실패: {return_msg}")
                    break

                # 차트 데이터 추출
                chart_data = result.get('stk_dt_pole_chart_qry', [])
                if not chart_data:
                    print("더 이상 조회할 데이터가 없습니다.")
                    break

                all_chart_data.extend(chart_data)

                # 연속조회 정보
                next_key = result.get('next_key', '')
                cont_yn = result.get('cont_yn', 'N')

                # 연속조회 불가능하면 종료
                if cont_yn != 'Y':
                    print("연속조회 종료")
                    break

                page += 1

            print(f"\n✓ 총 조회된 일봉 개수: {len(all_chart_data)}개")

            print(f"\n[3단계] 차트 데이터 분석")
            print("=" * 70)

            if not all_chart_data:
                print("✗ 차트 데이터가 없습니다.")
                return

            # 최근 10개 데이터 출력
            print(f"\n📈 최근 10일 일봉 데이터:")
            print("-" * 70)
            print(f"{'날짜':<12} {'시가':>10} {'고가':>10} {'저가':>10} {'종가':>10} {'거래량':>12} {'등락률':>8}")
            print("-" * 70)

            for i, candle in enumerate(all_chart_data[:10]):
                date_str = candle.get('dt', '')  # YYYYMMDD
                # 날짜 포맷 변환
                if len(date_str) == 8:
                    formatted_date = f"{date_str[0:4]}-{date_str[4:6]}-{date_str[6:8]}"
                else:
                    formatted_date = date_str

                open_price = int(candle.get('open_pric', 0))
                high_price = int(candle.get('high_pric', 0))
                low_price = int(candle.get('low_pric', 0))
                cur_price = int(candle.get('cur_prc', 0))
                volume = int(candle.get('trde_qty', 0))
                change_rate = candle.get('trde_tern_rt', '0')

                print(f"{formatted_date:<12} {open_price:>10,} {high_price:>10,} {low_price:>10,} "
                      f"{cur_price:>10,} {volume:>12,} {change_rate:>8}%")

            # 통계 정보
            print(f"\n📊 기간 통계 정보 ({len(all_chart_data)}일):")
            print("-" * 70)

            prices = [int(c.get('cur_prc', 0)) for c in all_chart_data if c.get('cur_prc')]
            volumes = [int(c.get('trde_qty', 0)) for c in all_chart_data if c.get('trde_qty')]
            high_prices = [int(c.get('high_pric', 0)) for c in all_chart_data if c.get('high_pric')]
            low_prices = [int(c.get('low_pric', 0)) for c in all_chart_data if c.get('low_pric')]

            if prices:
                print(f"  • 최고가: {max(high_prices):,}원 (기간 중 최고)")
                print(f"  • 최저가: {min(low_prices):,}원 (기간 중 최저)")
                print(f"  • 평균 종가: {sum(prices)//len(prices):,}원")
                print(f"  • 최신 종가: {prices[0]:,}원")

                # 기간 수익률 계산
                if len(prices) > 1:
                    period_return = ((prices[0] - prices[-1]) / prices[-1]) * 100
                    print(f"  • 기간 수익률: {period_return:+.2f}%")

            if volumes:
                print(f"  • 최대 거래량: {max(volumes):,}주")
                print(f"  • 평균 거래량: {sum(volumes)//len(volumes):,}주")
                print(f"  • 총 거래량: {sum(volumes):,}주")

            # 기술적 분석을 위한 데이터 준비 완료 메시지
            print(f"\n✅ 기술적 분석 준비 완료:")
            print(f"  • 일봉 데이터: {len(all_chart_data)}개")
            print(f"  • 이동평균선 계산 가능: MA5, MA20, MA60, MA120")
            print(f"  • 기술적 지표 계산 가능: RSI, MACD, Bollinger Bands 등")

            print("\n" + "=" * 70)
            print("✓ 테스트 완료")
            print("=" * 70)

            return all_chart_data

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
        return None


if __name__ == "__main__":
    # 기본 테스트: 삼성전자 최근 120일
    result = test_daily_chart("005930", days=120)

    # 특정 날짜부터 조회하려면:
    # test_daily_chart("005930", base_dt="20251001", days=60)
