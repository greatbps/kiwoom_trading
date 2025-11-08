"""
키움증권 API 토큰 발급 테스트
"""
import sys
import os

# 상위 디렉토리의 모듈을 import하기 위해 경로 추가
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI


def test_token():
    """토큰 발급 테스트"""
    print("=" * 60)
    print("키움증권 API 토큰 발급 테스트")
    print("=" * 60)

    try:
        # API 클라이언트 생성
        api = KiwoomAPI()

        print(f"\n[설정 정보]")
        print(f"API Key: {api.api_key[:10]}..." if api.api_key else "None")
        print(f"API Secret: {api.api_secret[:10]}..." if api.api_secret else "None")
        print(f"Account: {api.account_number}")
        print(f"User ID: {api.user_id}")

        print(f"\n[토큰 발급 시도]")
        print(f"Base URL: {api.BASE_URL}")
        print(f"요청 경로: /oauth2/token")

        # 토큰 발급
        token = api.get_access_token()

        print(f"\n[토큰 발급 결과]")
        print(f"반환된 토큰: {token}")
        print(f"토큰 타입: {type(token)}")
        print(f"api.access_token: {api.access_token}")

        if token:
            print(f"\n✓ 토큰 발급 성공!")
            print(f"Token: {token[:30]}...")
            print(f"Token 길이: {len(token)}")
            print(f"만료 시간: {api.token_expires_at}")
        else:
            print(f"\n✗ 토큰이 None 또는 빈 문자열입니다.")

    except Exception as e:
        print(f"\n✗ 오류 발생: {e}")
        import traceback
        traceback.print_exc()
    finally:
        if 'api' in locals():
            api.close()

    print("\n" + "=" * 60)


if __name__ == "__main__":
    test_token()
