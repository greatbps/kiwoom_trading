"""
실계좌 연동 통합 테스트 (업데이트된 API)
"""
import sys
from pathlib import Path

# 프로젝트 루트 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from kiwoom_api import KiwoomAPI
from core.risk_manager import RiskManager


def test_real_account():
    """실제 계좌 기반 리스크 관리 테스트"""
    print("=" * 80)
    print("실계좌 기반 리스크 관리 통합 테스트")
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

    # 계좌 잔고 조회 (kt00001)
    print("\n[2] 예수금 조회 (API-ID: kt00001)")
    try:
        balance_info = api.get_balance()

        # 예수금 파싱
        cash_str = balance_info.get('entr', '000000000000000')
        current_cash = float(cash_str)

        ord_alow_amt = float(balance_info.get('ord_alow_amt', '0'))

        print(f"  예수금: {current_cash:,.0f}원")
        print(f"  주문가능금액: {ord_alow_amt:,.0f}원")

    except Exception as e:
        print(f"❌ 잔고 조회 실패: {e}")
        return False

    # 보유 종목 조회 (ka01690)
    print("\n[3] 보유 종목 조회 (API-ID: ka01690)")
    try:
        account_info = api.get_account_info()

        positions = account_info.get('day_bal_rt', [])
        tot_buy_amt = int(account_info.get('tot_buy_amt', 0))
        tot_evlt_amt = int(account_info.get('tot_evlt_amt', 0))
        tot_evltv_prft = int(account_info.get('tot_evltv_prft', 0))

        print(f"  총 매수금액: {tot_buy_amt:,.0f}원")
        print(f"  총 평가금액: {tot_evlt_amt:,.0f}원")
        print(f"  총 평가손익: {tot_evltv_prft:,.0f}원")
        print(f"  보유 종목 수: {len([p for p in positions if p.get('stk_cd')])}")

        # 보유 종목 상세
        positions_value = 0.0
        for pos in positions:
            stock_code = pos.get('stk_cd', '')
            if not stock_code or stock_code == '':
                continue

            stock_name = pos.get('stk_nm', '')
            rmnd_qty = int(pos.get('rmnd_qty', 0)) if pos.get('rmnd_qty') else 0
            cur_prc = int(pos.get('cur_prc', 0)) if pos.get('cur_prc') else 0
            buy_uv = int(pos.get('buy_uv', 0)) if pos.get('buy_uv') else 0
            prft_rt = pos.get('prft_rt', '0')

            positions_value += cur_prc * rmnd_qty

            print(f"  • {stock_name}({stock_code}): {rmnd_qty}주 @ {cur_prc:,}원 "
                  f"(매입단가: {buy_uv:,}원, 수익률: {prft_rt})")

    except Exception as e:
        print(f"❌ 보유 종목 조회 실패: {e}")
        return False

    # 리스크 관리자 초기화 (실제 잔고 기반)
    print("\n[4] 리스크 관리자 초기화 (실제 잔고 기반)")
    risk_manager = RiskManager(
        initial_balance=current_cash,
        storage_path='data/test_risk_log.json'
    )
    print(f"  초기 잔고: {current_cash:,.0f}원")

    # 포지션 크기 계산 (삼성전자 가정: 50,000원)
    print("\n[5] 포지션 크기 계산 테스트")
    test_price = 50000
    stop_loss_price = test_price * 0.97  # -3%

    print(f"  현재가: {test_price:,}원")
    print(f"  손절가: {stop_loss_price:,.0f}원")

    position_calc = risk_manager.calculate_position_size(
        current_balance=current_cash,
        current_price=test_price,
        stop_loss_price=stop_loss_price,
        entry_confidence=1.0
    )

    print(f"\n  계산 결과:")
    print(f"  - 수량: {position_calc['quantity']}주")
    print(f"  - 투자금액: {position_calc['investment']:,.0f}원")
    print(f"  - 리스크 금액: {position_calc['risk_amount']:,.0f}원 ({current_cash * 0.02:,.0f}원 = 2%)")
    print(f"  - 포지션 비율: {position_calc['position_ratio']:.2f}%")
    print(f"  - 최대 손실: {position_calc['max_loss']:,.0f}원")

    # 진입 가능 여부 확인
    print(f"\n[6] 진입 가능 여부 확인")
    can_enter, reason = risk_manager.can_open_position(
        current_balance=current_cash,
        current_positions_value=positions_value,
        position_count=len([p for p in positions if p.get('stk_cd')]),
        position_size=position_calc['investment']
    )

    print(f"  총 자산: {current_cash + positions_value:,.0f}원")
    print(f"  현금 잔고: {current_cash:,.0f}원")
    print(f"  보유 종목 평가: {positions_value:,.0f}원")
    print(f"  결과: {'✓ 진입 가능' if can_enter else '❌ 진입 불가'}")
    print(f"  사유: {reason}")

    # 리스크 지표
    print(f"\n[7] 리스크 지표")
    risk_metrics = risk_manager.get_risk_metrics(
        current_balance=current_cash,
        positions_value=positions_value,
        unrealized_pnl=tot_evltv_prft
    )

    print(f"  현금 비율: {risk_metrics['cash_ratio']:.2f}%")
    print(f"  포지션 비율: {risk_metrics['position_ratio']:.2f}%")
    print(f"  일일 실현 손익: {risk_metrics['daily_realized_pnl']:,.0f}원")
    print(f"  일일 미실현 손익: {risk_metrics['daily_unrealized_pnl']:,.0f}원")
    print(f"  일일 총 손익: {risk_metrics['daily_total_pnl']:,.0f}원")
    print(f"  일일 수익률: {risk_metrics['daily_return']:.2f}%")

    print("\n" + "=" * 80)
    return True


if __name__ == "__main__":
    print("\n🚀 실계좌 기반 리스크 관리 통합 테스트\n")

    success = test_real_account()

    if success:
        print("\n✅ 모든 테스트 완료!")
        print("\n📊 결과:")
        print("  - API 연동: ✓")
        print("  - 실계좌 조회: ✓")
        print("  - 리스크 관리자: ✓")
        print("  - 포지션 계산: ✓")
        print("\n🚀 월요일 실전 투입 준비 완료!")
    else:
        print("\n❌ 테스트 실패")

    print()
