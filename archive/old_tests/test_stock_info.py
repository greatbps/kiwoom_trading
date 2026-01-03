"""
주식 기본정보 조회 테스트
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
import json


def test_stock_info():
    """주식 기본정보 조회 테스트"""

    # API 인스턴스 생성
    api = KiwoomAPI()

    # 삼성전자 종목코드
    stock_code = "005930"

    print(f"=== 주식 기본정보 조회 ===")
    print(f"종목코드: {stock_code}")

    try:
        # 토큰 발급
        print("\n[1] 토큰 발급 중...")
        api.get_access_token()

        # 주식 기본정보 조회
        print("\n[2] 주식 기본정보 조회 중...")
        result = api.get_stock_info(stock_code=stock_code)

        print("\n[3] 응답 데이터:")
        print(json.dumps(result, indent=2, ensure_ascii=False))

        # 주요 데이터 추출 및 정리
        print("\n[4] 기본 정보:")
        print("-" * 60)
        print(f"종목코드        : {result.get('stk_cd', 'N/A')}")
        print(f"종목명          : {result.get('stk_nm', 'N/A')}")
        print(f"현재가          : {result.get('cur_prc', 'N/A')}원")

        # 등락 정보
        pre_sig = result.get('pre_sig', '')
        pred_pre = result.get('pred_pre', 'N/A')
        flu_rt = result.get('flu_rt', 'N/A')

        if pre_sig == '2':
            sign = '▲'
        elif pre_sig == '5':
            sign = '▼'
        else:
            sign = '-'

        print(f"전일대비        : {sign} {pred_pre}원 ({flu_rt}%)")

        print("\n[5] 거래 정보:")
        print("-" * 60)
        print(f"시가            : {result.get('open_pric', 'N/A')}원")
        print(f"고가            : {result.get('high_pric', 'N/A')}원")
        print(f"저가            : {result.get('low_pric', 'N/A')}원")
        print(f"거래량          : {result.get('trde_qty', 'N/A')}주")
        print(f"거래대금        : {result.get('trde_pre', 'N/A')}원")

        print("\n[6] 가격 범위:")
        print("-" * 60)
        print(f"52주 최고가     : {result.get('oyr_hgst', 'N/A')}원")
        print(f"52주 최저가     : {result.get('oyr_lwst', 'N/A')}원")
        print(f"250일 최고가    : {result.get('250hgst', 'N/A')}원")
        print(f"250일 최저가    : {result.get('250lwst', 'N/A')}원")

        print("\n[7] 재무/투자 지표:")
        print("-" * 60)
        print(f"PER             : {result.get('per', 'N/A')}")
        print(f"PBR             : {result.get('pbr', 'N/A')}")
        print(f"ROE             : {result.get('roe', 'N/A')}%")
        print(f"EPS             : {result.get('eps', 'N/A')}원")
        print(f"BPS             : {result.get('bps', 'N/A')}원")

        print("\n[8] 시장 정보:")
        print("-" * 60)
        print(f"시가총액        : {result.get('cap', 'N/A')}억원")
        print(f"유통주식수      : {result.get('flo_stk', 'N/A')}천주")
        print(f"액면가          : {result.get('fav', 'N/A')}원")
        print(f"외국인보유비율  : {result.get('for_exh_rt', 'N/A')}%")
        print(f"신용비율        : {result.get('crd_rt', 'N/A')}%")

        # 투자 판단 분석
        print("\n[9] 기본 분석:")
        print("-" * 60)

        try:
            # PER 분석
            per = result.get('per', '')
            if per and per != '' and per != 'N/A':
                per_val = float(per.replace(',', ''))
                if per_val < 10:
                    print(f"  PER: {per_val:.2f} - 저평가 가능성 ✅")
                elif per_val < 20:
                    print(f"  PER: {per_val:.2f} - 적정 수준 ➡️")
                else:
                    print(f"  PER: {per_val:.2f} - 고평가 가능성 ⚠️")

            # PBR 분석
            pbr = result.get('pbr', '')
            if pbr and pbr != '' and pbr != 'N/A':
                pbr_val = float(pbr.replace(',', ''))
                if pbr_val < 1.0:
                    print(f"  PBR: {pbr_val:.2f} - 저평가 가능성 ✅")
                elif pbr_val < 2.0:
                    print(f"  PBR: {pbr_val:.2f} - 적정 수준 ➡️")
                else:
                    print(f"  PBR: {pbr_val:.2f} - 고평가 가능성 ⚠️")

            # ROE 분석
            roe = result.get('roe', '')
            if roe and roe != '' and roe != 'N/A':
                roe_val = float(roe.replace(',', '').replace('%', ''))
                if roe_val > 15:
                    print(f"  ROE: {roe_val:.2f}% - 우수한 수익성 ✅")
                elif roe_val > 10:
                    print(f"  ROE: {roe_val:.2f}% - 양호한 수익성 ➡️")
                else:
                    print(f"  ROE: {roe_val:.2f}% - 낮은 수익성 ⚠️")

            # 외국인 보유 비율 분석
            for_rt = result.get('for_exh_rt', '')
            if for_rt and for_rt != '' and for_rt != 'N/A':
                for_val = float(for_rt.replace(',', '').replace('%', ''))
                if for_val > 30:
                    print(f"  외국인 보유: {for_val:.2f}% - 높은 신뢰도 ✅")
                elif for_val > 10:
                    print(f"  외국인 보유: {for_val:.2f}% - 보통 수준 ➡️")
                else:
                    print(f"  외국인 보유: {for_val:.2f}% - 낮은 관심도 ⚠️")

        except Exception as e:
            print(f"  분석 오류: {e}")

        print("\n✓ 주식 기본정보 조회 완료")

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()

    finally:
        # API 세션 종료
        api.close()


if __name__ == "__main__":
    test_stock_info()
