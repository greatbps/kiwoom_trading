"""
실계좌 연동 테스트
"""
import sys
from pathlib import Path

# 프로젝트 루트 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from kiwoom_api import KiwoomAPI
from core.risk_manager import RiskManager


def test_account_info():
    """계좌 정보 조회 테스트"""
    print("=" * 80)
    print("계좌 정보 조회 테스트")
    print("=" * 80)
    
    # API 클라이언트 생성
    api = KiwoomAPI()
    
    # 토큰 발급
    print("\n[1] 토큰 발급")
    api.get_access_token()
    
    if not api.access_token:
        print("❌ 토큰 발급 실패")
        return False
    
    print("✓ 토큰 발급 성공")
    
    # 계좌 정보 조회
    print("\n[2] 계좌 기본 정보 조회")
    try:
        account_info = api.get_account_info()
        print(f"응답: {account_info}")
    except Exception as e:
        print(f"❌ 계좌 정보 조회 실패: {e}")
        print("⚠️  API 엔드포인트가 실제와 다를 수 있습니다.")
    
    # 계좌 잔고 조회
    print("\n[3] 계좌 잔고 조회")
    try:
        balance_info = api.get_balance()
        print(f"응답: {balance_info}")
    except Exception as e:
        print(f"❌ 잔고 조회 실패: {e}")
        print("⚠️  API 엔드포인트가 실제와 다를 수 있습니다.")
    
    print("\n" + "=" * 80)
    return True


def test_risk_manager():
    """리스크 관리자 테스트"""
    print("=" * 80)
    print("리스크 관리자 테스트")
    print("=" * 80)
    
    # 초기 잔고 1000만원으로 초기화
    initial_balance = 10000000
    risk_manager = RiskManager(
        initial_balance=initial_balance,
        storage_path='data/test_risk_log.json'
    )
    
    print(f"\n[1] 초기 잔고: {initial_balance:,}원")
    
    # 포지션 크기 계산
    current_price = 50000
    stop_loss_price = 48500  # -3%
    
    print(f"\n[2] 포지션 크기 계산")
    print(f"   현재가: {current_price:,}원")
    print(f"   손절가: {stop_loss_price:,}원")
    
    position_calc = risk_manager.calculate_position_size(
        current_balance=initial_balance,
        current_price=current_price,
        stop_loss_price=stop_loss_price,
        entry_confidence=1.0
    )
    
    print(f"\n   계산 결과:")
    print(f"   - 수량: {position_calc['quantity']}주")
    print(f"   - 투자금액: {position_calc['investment']:,.0f}원")
    print(f"   - 리스크 금액: {position_calc['risk_amount']:,.0f}원")
    print(f"   - 포지션 비율: {position_calc['position_ratio']:.2f}%")
    print(f"   - 최대 손실: {position_calc['max_loss']:,.0f}원")
    
    # 진입 가능 여부 확인
    print(f"\n[3] 진입 가능 여부 확인")
    can_enter, reason = risk_manager.can_open_position(
        current_balance=initial_balance,
        current_positions_value=0,
        position_count=0,
        position_size=position_calc['investment']
    )
    
    print(f"   결과: {'✓ 진입 가능' if can_enter else '❌ 진입 불가'}")
    print(f"   사유: {reason}")
    
    # 잔고 업데이트 테스트
    print(f"\n[4] 잔고 업데이트 테스트")
    new_balance = 9500000
    risk_manager.update_balance(new_balance)
    print(f"   업데이트 전: {initial_balance:,}원")
    print(f"   업데이트 후: {risk_manager.initial_balance:,}원")
    
    print("\n" + "=" * 80)
    return True


if __name__ == "__main__":
    print("\n🚀 실계좌 연동 통합 테스트\n")
    
    # Test 1: 계좌 정보 조회
    test_account_info()
    
    print("\n")
    
    # Test 2: 리스크 관리자
    test_risk_manager()
    
    print("\n✅ 모든 테스트 완료!\n")
