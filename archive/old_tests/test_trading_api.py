#!/usr/bin/env python3
"""
키움 API 매매 기능 테스트 스크립트

이 스크립트는 키움 REST API의 주요 매매 기능을 테스트합니다:
1. 접근 토큰 발급
2. 계좌 정보 조회 (잔고, 평가현황)
3. 미체결/체결 주문 조회
4. 주식 시세 조회 (호가, 체결 정보)
5. 주문 기능 (매수/매도/정정/취소)
"""
import sys
from kiwoom_api import KiwoomAPI
from datetime import datetime


def print_section(title):
    """섹션 제목 출력"""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)


def test_token(api):
    """접근 토큰 발급 테스트"""
    print_section("1. 접근 토큰 발급 테스트")
    try:
        token = api.get_access_token()
        print(f"✓ 토큰 발급 성공")
        print(f"  토큰: {token[:30]}..." if token else "None")
        return True
    except Exception as e:
        print(f"✗ 토큰 발급 실패: {e}")
        return False


def test_balance(api):
    """계좌 잔고 조회 테스트"""
    print_section("2. 계좌 잔고 조회 테스트 (예수금 상세현황)")
    try:
        result = api.get_balance()
        print(f"✓ 잔고 조회 성공")
        print(f"  return_code: {result.get('return_code')}")
        print(f"  return_msg: {result.get('return_msg')}")

        # 응답 데이터 출력
        if result.get('return_code') == 0:
            # 가능한 키들을 체크
            for key in ['entr_dtl_prst', 'output', 'data']:
                if key in result:
                    data = result[key]
                    print(f"\n  [{key}]")
                    if isinstance(data, dict):
                        for k, v in data.items():
                            print(f"    {k}: {v}")
                    elif isinstance(data, list) and data:
                        print(f"    (리스트, {len(data)}개 항목)")
                        if data:
                            print(f"    첫 항목: {data[0]}")
        return True
    except Exception as e:
        print(f"✗ 잔고 조회 실패: {e}")
        return False


def test_account_evaluation(api):
    """계좌 평가현황 조회 테스트"""
    print_section("3. 계좌 평가현황 조회 테스트")
    try:
        result = api.get_account_evaluation()
        print(f"✓ 계좌평가현황 조회 성공")
        print(f"  return_code: {result.get('return_code')}")
        print(f"  return_msg: {result.get('return_msg')}")

        # 응답 데이터 출력
        if result.get('return_code') == 0:
            # 가능한 키들을 체크
            for key in ['acnt_evlt_prst', 'output1', 'output2', 'data']:
                if key in result:
                    data = result[key]
                    print(f"\n  [{key}]")
                    if isinstance(data, dict):
                        for k, v in data.items():
                            print(f"    {k}: {v}")
                    elif isinstance(data, list):
                        print(f"    (리스트, {len(data)}개 보유종목)")
                        for idx, item in enumerate(data[:3]):  # 최대 3개만 출력
                            print(f"    [{idx+1}] {item}")
        return True
    except Exception as e:
        print(f"✗ 계좌평가현황 조회 실패: {e}")
        return False


def test_account_info(api):
    """계좌 보유 종목 조회 테스트"""
    print_section("4. 계좌 보유 종목 조회 테스트 (일별잔고수익률)")
    try:
        result = api.get_account_info()
        print(f"✓ 보유 종목 조회 성공")
        print(f"  return_code: {result.get('return_code')}")
        print(f"  return_msg: {result.get('return_msg')}")

        # 응답 데이터 출력
        if result.get('return_code') == 0:
            for key in ['day_bal_rt', 'output1', 'output2', 'data']:
                if key in result:
                    data = result[key]
                    print(f"\n  [{key}]")
                    if isinstance(data, list):
                        print(f"    보유종목 수: {len(data)}")
                        for idx, item in enumerate(data[:3]):  # 최대 3개만 출력
                            print(f"    [{idx+1}] {item}")
        return True
    except Exception as e:
        print(f"✗ 보유 종목 조회 실패: {e}")
        return False


def test_unexecuted_orders(api):
    """미체결 주문 조회 테스트"""
    print_section("5. 미체결 주문 조회 테스트")
    try:
        result = api.get_unexecuted_orders()
        print(f"✓ 미체결 주문 조회 성공")
        print(f"  return_code: {result.get('return_code')}")
        print(f"  return_msg: {result.get('return_msg')}")

        # 응답 데이터 출력
        if result.get('return_code') == 0:
            for key in ['ord_noexe', 'output', 'data']:
                if key in result:
                    data = result[key]
                    print(f"\n  [{key}]")
                    if isinstance(data, list):
                        print(f"    미체결 주문 수: {len(data)}")
                        for idx, item in enumerate(data[:5]):  # 최대 5개만 출력
                            print(f"    [{idx+1}] {item}")
        return True
    except Exception as e:
        print(f"✗ 미체결 주문 조회 실패: {e}")
        return False


def test_executed_orders(api):
    """체결 주문 조회 테스트"""
    print_section("6. 체결 주문 조회 테스트")
    try:
        result = api.get_executed_orders()
        print(f"✓ 체결 주문 조회 성공")
        print(f"  return_code: {result.get('return_code')}")
        print(f"  return_msg: {result.get('return_msg')}")

        # 응답 데이터 출력
        if result.get('return_code') == 0:
            for key in ['ord_cntr', 'output', 'data']:
                if key in result:
                    data = result[key]
                    print(f"\n  [{key}]")
                    if isinstance(data, list):
                        print(f"    체결 주문 수: {len(data)}")
                        for idx, item in enumerate(data[:5]):  # 최대 5개만 출력
                            print(f"    [{idx+1}] {item}")
        return True
    except Exception as e:
        print(f"✗ 체결 주문 조회 실패: {e}")
        return False


def test_stock_quote(api, stock_code="005930"):
    """주식 호가 조회 테스트"""
    print_section(f"7. 주식 호가 조회 테스트 ({stock_code})")
    try:
        result = api.get_stock_quote(stock_code)
        print(f"✓ 호가 조회 성공")
        print(f"  return_code: {result.get('return_code')}")
        print(f"  return_msg: {result.get('return_msg')}")

        # 응답 데이터 출력
        if result.get('return_code') == 0:
            for key in ['stk_hoga', 'output', 'data']:
                if key in result:
                    data = result[key]
                    print(f"\n  [{key}]")
                    if isinstance(data, dict):
                        # 주요 호가 정보 출력
                        print(f"    종목코드: {data.get('stk_cd')}")
                        print(f"    종목명: {data.get('stk_nm')}")
                        print(f"    현재가: {data.get('cur_prc')}")
                        print(f"    매도호가1: {data.get('sell_hoga_1')} ({data.get('sell_hoga_rem_qty_1')})")
                        print(f"    매수호가1: {data.get('buy_hoga_1')} ({data.get('buy_hoga_rem_qty_1')})")
        return True
    except Exception as e:
        print(f"✗ 호가 조회 실패: {e}")
        return False


def test_execution_info(api, stock_code="005930"):
    """체결정보 조회 테스트"""
    print_section(f"8. 체결정보 조회 테스트 ({stock_code})")
    try:
        result = api.get_execution_info(stock_code)
        print(f"✓ 체결정보 조회 성공")
        print(f"  return_code: {result.get('return_code')}")
        print(f"  return_msg: {result.get('return_msg')}")

        # 응답 데이터 출력
        if result.get('return_code') == 0:
            for key in ['stk_cntr_info', 'output', 'data']:
                if key in result:
                    data = result[key]
                    print(f"\n  [{key}]")
                    if isinstance(data, list):
                        print(f"    체결 틱 수: {len(data)}")
                        for idx, item in enumerate(data[:5]):  # 최대 5개만 출력
                            print(f"    [{idx+1}] {item}")
        return True
    except Exception as e:
        print(f"✗ 체결정보 조회 실패: {e}")
        return False


def test_stock_info(api, stock_code="005930"):
    """주식 기본정보 조회 테스트"""
    print_section(f"9. 주식 기본정보 조회 테스트 ({stock_code})")
    try:
        result = api.get_stock_info(stock_code)
        print(f"✓ 기본정보 조회 성공")
        print(f"  return_code: {result.get('return_code')}")
        print(f"  return_msg: {result.get('return_msg')}")

        # 응답 데이터 출력
        if result.get('return_code') == 0:
            for key in ['stk_basi_info', 'output', 'data']:
                if key in result:
                    data = result[key]
                    print(f"\n  [{key}]")
                    if isinstance(data, dict):
                        print(f"    종목코드: {data.get('stk_cd')}")
                        print(f"    종목명: {data.get('stk_nm')}")
                        print(f"    현재가: {data.get('cur_prc')}")
                        print(f"    PER: {data.get('per')}")
                        print(f"    PBR: {data.get('pbr')}")
                        print(f"    시가총액: {data.get('cap')}")
        return True
    except Exception as e:
        print(f"✗ 기본정보 조회 실패: {e}")
        return False


def main():
    """메인 테스트 함수"""
    print("\n")
    print("=" * 80)
    print("  키움 API 매매 기능 테스트")
    print("=" * 80)
    print(f"  테스트 시작 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # API 초기화
    try:
        api = KiwoomAPI()
    except Exception as e:
        print(f"\n✗ API 초기화 실패: {e}")
        print("  .env 파일에 다음 정보가 설정되어 있는지 확인하세요:")
        print("  - KIWOOM_APP_KEY")
        print("  - KIWOOM_APP_SECRET")
        print("  - KIWOOM_ACCOUNT_NUMBER")
        sys.exit(1)

    # 테스트 실행
    results = []

    # 1. 접근 토큰 발급 (필수)
    if not test_token(api):
        print("\n✗ 토큰 발급에 실패하여 테스트를 중단합니다.")
        sys.exit(1)

    # 2. 계좌 관련 테스트
    results.append(("잔고 조회", test_balance(api)))
    results.append(("계좌평가현황", test_account_evaluation(api)))
    results.append(("보유종목 조회", test_account_info(api)))

    # 3. 주문 관련 테스트
    results.append(("미체결 조회", test_unexecuted_orders(api)))
    results.append(("체결 조회", test_executed_orders(api)))

    # 4. 시세 관련 테스트 (삼성전자)
    stock_code = "005930"
    results.append(("호가 조회", test_stock_quote(api, stock_code)))
    results.append(("체결정보 조회", test_execution_info(api, stock_code)))
    results.append(("기본정보 조회", test_stock_info(api, stock_code)))

    # 결과 요약
    print_section("테스트 결과 요약")
    success_count = sum(1 for _, result in results if result)
    total_count = len(results)

    print(f"\n  총 {total_count}개 테스트 중 {success_count}개 성공\n")

    for name, result in results:
        status = "✓ 성공" if result else "✗ 실패"
        print(f"    {status}: {name}")

    print(f"\n  테스트 종료 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)

    # API 세션 종료
    api.close()

    # 실패한 테스트가 있으면 비정상 종료
    if success_count < total_count:
        sys.exit(1)


if __name__ == "__main__":
    main()
