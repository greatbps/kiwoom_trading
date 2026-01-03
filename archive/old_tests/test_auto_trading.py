"""
자동 매매 시스템 테스트

전체 자동 매매 시스템을 테스트합니다.
"""
import sys
import os

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.auto_trading_handler import AutoTradingHandler
import json


def test_auto_trading_simple():
    """
    자동 매매 시스템 간단 테스트

    실제 운영 모드가 아니라 주요 컴포넌트의 통합을 테스트합니다.
    """
    print("=" * 80)
    print(f"{'자동 매매 시스템 통합 테스트':^80}")
    print("=" * 80)

    # 자동 매매 핸들러 생성
    handler = AutoTradingHandler(
        account_no="12345678-01",  # 테스트 계좌
        initial_balance=10000000,  # 1천만원
        risk_per_trade=0.02,  # 2%
        max_position_size=0.30  # 30%
    )

    print("\n[1] 시스템 초기화 완료")
    print(f"  계좌번호: {handler.account_no}")
    print(f"  초기 잔고: {handler.initial_balance:,}원")

    # 토큰 발급 테스트
    print("\n[2] 토큰 발급 테스트")
    try:
        handler.api.get_access_token()
        print("  ✓ 토큰 발급 성공")
    except Exception as e:
        print(f"  ✗ 토큰 발급 실패: {e}")
        return

    # 관심 종목 리스트 확인
    print("\n[3] 관심 종목 리스트")
    watchlist = handler.market_monitor.get_watchlist_summary()
    for i, stock in enumerate(watchlist, 1):
        print(f"  {i}. {stock['stock_name']} ({stock['stock_code']})")

    # 포지션 매니저 테스트
    print("\n[4] 포지션 관리 테스트")
    portfolio_summary = handler.position_manager.get_summary()
    print(f"  보유 종목 수: {portfolio_summary['position_count']}")
    print(f"  총 투자금: {portfolio_summary['total_invested']:,}원")
    print(f"  총 평가액: {portfolio_summary['total_value']:,}원")
    print(f"  총 손익: {portfolio_summary['total_profit_loss']:+,.0f}원 ({portfolio_summary['total_profit_loss_rate']:+.2f}%)")

    # 리스크 관리자 테스트
    print("\n[5] 리스크 관리 테스트")
    current_balance = handler.order_executor.get_account_balance()
    positions_value = handler.position_manager.get_total_value()
    unrealized_pnl = handler.position_manager.get_total_profit_loss()

    risk_metrics = handler.risk_manager.get_risk_metrics(
        current_balance=current_balance,
        positions_value=positions_value,
        unrealized_pnl=unrealized_pnl
    )

    print(f"  총 자산: {risk_metrics['total_assets']:,}원")
    print(f"  현금 비율: {risk_metrics['cash_ratio']:.1f}%")
    print(f"  포지션 비율: {risk_metrics['position_ratio']:.1f}%")
    print(f"  일일 손익: {risk_metrics['daily_total_pnl']:+,.0f}원")
    print(f"  거래 횟수: {risk_metrics['daily_trade_count']}/{risk_metrics['max_daily_trades']}")

    # 긴급 중지 조건 확인
    should_stop, reason = handler.risk_manager.check_emergency_stop(unrealized_pnl)
    if should_stop:
        print(f"  🚨 긴급 중지: {reason}")
    else:
        print(f"  ✅ 정상 운영 가능")

    # 매수 신호 스캔 테스트 (1회만)
    print("\n[6] 매수 신호 스캔 테스트")
    print("  관심 종목을 스캔합니다...")

    try:
        buy_candidates = handler.market_monitor.scan_for_buy_signals(current_balance)

        if buy_candidates:
            print(f"\n  ✅ 매수 후보 {len(buy_candidates)}개 발견")
            print(f"\n  {'순위':<6} {'종목명':<12} {'신호':<12} {'점수':>8} {'현재가':>12} {'수량':>8} {'투자금':>14}")
            print(f"  {'-'*80}")

            for i, candidate in enumerate(buy_candidates, 1):
                tp = candidate['trading_plan']
                print(f"  {i:<6} {candidate['stock_name']:<12} {candidate['signal']:<12} "
                      f"{candidate['score']:>8.2f} {candidate['current_price']:>12,}원 "
                      f"{tp['position']['quantity']:>8,}주 {tp['position']['investment']:>14,}원")

            # 최고 점수 종목 상세 정보
            best = buy_candidates[0]
            print(f"\n  🏆 최고 점수 종목: {best['stock_name']} ({best['score']:.2f}점)")
            print(f"\n  📋 매매 계획:")
            tp = best['trading_plan']
            print(f"    진입 신호: {tp['entry_signal']['signal']} ({tp['entry_signal']['confidence']})")
            print(f"    매수 수량: {tp['position']['quantity']:,}주")
            print(f"    투자 금액: {tp['position']['investment']:,}원 ({tp['position']['position_ratio']:.1f}%)")
            print(f"    목표가:")
            print(f"      1차: {tp['targets']['target1']:,}원 (+{tp['targets']['target1_gain']:.2f}%)")
            print(f"      2차: {tp['targets']['target2']:,}원 (+{tp['targets']['target2_gain']:.2f}%)")
            print(f"      3차: {tp['targets']['target3']:,}원 (+{tp['targets']['target3_gain']:.2f}%)")
            print(f"    손절가: {tp['stop_loss']['stop_loss']:,}원 ({tp['stop_loss']['loss_rate']:.2f}%)")
            print(f"    리스크/리워드: 1:{tp['risk_reward']['risk_reward_ratio']:.2f}")

        else:
            print(f"  [INFO] 매수 신호 없음")

    except Exception as e:
        print(f"  ✗ 스캔 실패: {e}")
        import traceback
        traceback.print_exc()

    # 상태 리포트
    print("\n[7] 전체 상태 리포트")
    status_report = handler.get_status_report()
    print(json.dumps({
        'timestamp': status_report['timestamp'],
        'risk_metrics': {
            'total_assets': status_report['risk_metrics']['total_assets'],
            'cash_ratio': f"{status_report['risk_metrics']['cash_ratio']:.1f}%",
            'daily_pnl': status_report['risk_metrics']['daily_total_pnl'],
            'trade_count': status_report['risk_metrics']['daily_trade_count']
        },
        'portfolio': {
            'position_count': status_report['portfolio']['position_count'],
            'total_invested': status_report['portfolio']['total_invested'],
            'total_value': status_report['portfolio']['total_value'],
            'total_pnl': status_report['portfolio']['total_profit_loss']
        }
    }, indent=2, ensure_ascii=False))

    # 종료
    handler.cleanup()

    print("\n" + "=" * 80)
    print(f"{'✓ 통합 테스트 완료':^80}")
    print("=" * 80)


def test_position_manager():
    """포지션 관리자 단위 테스트"""
    from core.position_manager import Position, PositionManager
    from datetime import datetime

    print("\n" + "=" * 80)
    print(f"{'포지션 관리자 단위 테스트':^80}")
    print("=" * 80)

    # 포지션 관리자 생성
    pm = PositionManager(storage_path='data/test_positions.json')

    # 테스트 포지션 생성
    position = Position(
        stock_code="005930",
        stock_name="삼성전자",
        quantity=100,
        avg_price=70000,
        current_price=70000,
        buy_time=datetime.now(),
        target1=72000,
        target2=74000,
        target3=76000,
        stop_loss=67000,
        atr=1000
    )

    # 포지션 추가
    pm.add_position(position)
    print(f"\n[1] 포지션 추가: {position.stock_name}")
    print(f"  수량: {position.quantity}주")
    print(f"  평단가: {position.avg_price:,}원")

    # 가격 업데이트 (+5%)
    new_price = 73500
    pm.update_price("005930", new_price)
    updated_position = pm.get_position("005930")

    print(f"\n[2] 가격 업데이트: {new_price:,}원")
    print(f"  손익: {updated_position.profit_loss:+,.0f}원 ({updated_position.profit_loss_rate:+.2f}%)")

    # 1차 익절 (40%)
    sell_qty_1 = 40
    pm.update_stage("005930", new_stage=1, sold_quantity=sell_qty_1)
    print(f"\n[3] 1차 익절 매도: {sell_qty_1}주")
    updated_position = pm.get_position("005930")
    print(f"  잔여 수량: {updated_position.remaining_quantity}주")
    print(f"  단계: {updated_position.stage}")

    # 2차 익절 (40%) + trailing 활성화
    sell_qty_2 = 40
    pm.update_stage("005930", new_stage=2, sold_quantity=sell_qty_2)
    print(f"\n[4] 2차 익절 매도: {sell_qty_2}주 (Trailing 활성화)")
    updated_position = pm.get_position("005930")
    print(f"  잔여 수량: {updated_position.remaining_quantity}주")
    print(f"  단계: {updated_position.stage}")
    print(f"  Trailing 활성: {updated_position.is_trailing_active}")
    print(f"  Trailing Stop: {updated_position.trailing_stop:,}원" if updated_position.trailing_stop else "  Trailing Stop: 미설정")

    # 포트폴리오 요약
    print(f"\n[5] 포트폴리오 요약")
    summary = pm.get_summary()
    print(f"  보유 종목: {summary['position_count']}개")
    print(f"  총 투자: {summary['total_invested']:,}원")
    print(f"  총 평가: {summary['total_value']:,}원")
    print(f"  총 손익: {summary['total_profit_loss']:+,.0f}원 ({summary['total_profit_loss_rate']:+.2f}%)")

    print("\n" + "=" * 80)


def test_risk_manager():
    """리스크 관리자 단위 테스트"""
    from core.risk_manager import RiskManager

    print("\n" + "=" * 80)
    print(f"{'리스크 관리자 단위 테스트':^80}")
    print("=" * 80)

    # 리스크 관리자 생성
    rm = RiskManager(initial_balance=10000000, storage_path='data/test_risk_log.json')

    print(f"\n[1] 초기 설정")
    print(f"  초기 잔고: {rm.initial_balance:,}원")
    print(f"  거래당 리스크: {rm.RISK_PER_TRADE:.1%}")
    print(f"  최대 포지션: {rm.MAX_POSITION_SIZE:.1%}")
    print(f"  하드 포지션 한도: {rm.HARD_MAX_POSITION:,}원")
    print(f"  하드 일일 손실 한도: {rm.HARD_MAX_DAILY_LOSS:,}원")

    # 포지션 크기 계산
    print(f"\n[2] 포지션 크기 계산")
    position_size = rm.calculate_position_size(
        current_balance=10000000,
        current_price=70000,
        stop_loss_price=67000,
        entry_confidence=1.0
    )

    print(f"  현재가: 70,000원")
    print(f"  손절가: 67,000원")
    print(f"  → 매수 수량: {position_size['quantity']:,}주")
    print(f"  → 투자 금액: {position_size['investment']:,}원")
    print(f"  → 포지션 비율: {position_size['position_ratio']:.1f}%")
    print(f"  → 리스크 금액: {position_size['risk_amount']:,}원")
    print(f"  → 최대 손실: {position_size['max_loss']:,}원")

    # 거래 기록
    print(f"\n[3] 거래 기록 테스트")
    rm.record_trade(
        stock_code="005930",
        stock_name="삼성전자",
        trade_type="BUY",
        quantity=100,
        price=70000
    )
    print(f"  ✓ 매수 거래 기록: 삼성전자 100주 @ 70,000원")

    rm.record_trade(
        stock_code="005930",
        stock_name="삼성전자",
        trade_type="SELL",
        quantity=40,
        price=72000,
        realized_pnl=80000
    )
    print(f"  ✓ 매도 거래 기록: 삼성전자 40주 @ 72,000원 (손익: +80,000원)")

    # 일일 요약
    print(f"\n[4] 일일 거래 요약")
    daily_summary = rm.get_daily_summary(unrealized_pnl=50000)
    print(f"  날짜: {daily_summary.date}")
    print(f"  거래 횟수: {daily_summary.trade_count}")
    print(f"  승/패: {daily_summary.win_count}/{daily_summary.loss_count}")
    print(f"  실현 손익: {daily_summary.realized_pnl:+,.0f}원")
    print(f"  미실현 손익: {daily_summary.unrealized_pnl:+,.0f}원")
    print(f"  총 손익: {daily_summary.total_pnl:+,.0f}원")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description='자동 매매 시스템 테스트')
    parser.add_argument('--mode', choices=['full', 'position', 'risk', 'all'], default='full',
                        help='테스트 모드 (full: 통합 테스트, position: 포지션 관리자, risk: 리스크 관리자, all: 전체)')

    args = parser.parse_args()

    if args.mode == 'full':
        test_auto_trading_simple()
    elif args.mode == 'position':
        test_position_manager()
    elif args.mode == 'risk':
        test_risk_manager()
    elif args.mode == 'all':
        test_position_manager()
        test_risk_manager()
        test_auto_trading_simple()
