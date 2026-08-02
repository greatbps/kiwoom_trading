#!/usr/bin/env python3
"""
주식 현재가 및 고가 확인 (5분봉 데이터 사용)
"""
import sys
from kiwoom_api import KiwoomAPI

def check_stock_price(stock_code: str):
    """주식 현재가 및 고가 확인"""

    api = KiwoomAPI()

    # 토큰 발급
    try:
        access_token = api.get_access_token()
        print(f"✓ 토큰 발급 성공")
    except Exception as e:
        print(f"✗ 토큰 발급 실패: {e}")
        return

    # 5분봉 데이터 조회
    print(f"\n5분봉 데이터 조회 중...")
    try:
        result = api.get_minute_chart(stock_code, tic_scope='5')

        if result is None or result.get('return_code') != 0:
            print(f"✗ 5분봉 데이터 없음: {result}")
            return

        # output 추출 (키움 API는 stk_min_pole_chart_qry 사용)
        output = result.get('stk_min_pole_chart_qry') or result.get('output') or result.get('output1') or []

        if not output:
            print(f"✗ 데이터 없음")
            return

        import pandas as pd
        df = pd.DataFrame(output)

        print(f"✓ {len(df)}개 5분봉 데이터 조회 성공")

        # 가격 데이터 정제 (+/- 기호 제거)
        for col in ['cur_prc', 'open_pric', 'high_pric', 'low_pric']:
            df[col] = df[col].astype(str).str.replace('+', '').str.replace('-', '').astype(int)

        # 오늘 데이터만 필터링
        from datetime import datetime
        today = datetime.now().strftime('%Y%m%d')

        df['date'] = df['cntr_tm'].astype(str).str[:8]
        today_df = df[df['date'] == today].copy()

        if today_df.empty:
            print(f"✗ 오늘({today}) 데이터 없음")
            # 최근 데이터라도 보여주기
            print(f"\n최근 데이터 (가장 최근 날짜):")
            latest_date = df['date'].max()
            latest_df = df[df['date'] == latest_date].copy()
            current_price = latest_df['cur_prc'].iloc[-1]  # 종가
            high_price = latest_df['high_pric'].max()  # 고가
            low_price = latest_df['low_pric'].min()    # 저가
            open_price = latest_df['open_pric'].iloc[0]  # 시가
            data_date = latest_date
        else:
            # 오늘 데이터 분석
            current_price = today_df['cur_prc'].iloc[-1]      # 마지막 종가 = 현재가
            high_price = today_df['high_pric'].max()          # 오늘 고가
            low_price = today_df['low_pric'].min()            # 오늘 저가
            open_price = today_df['open_pric'].iloc[0]        # 오늘 시가
            data_date = today

        # 매수가 (risk_log.json에서)
        import json
        from pathlib import Path

        risk_log_path = Path("data/risk_log.json")
        buy_price = None
        if risk_log_path.exists():
            with open(risk_log_path, 'r', encoding='utf-8') as f:
                risk_data = json.load(f)

            # 오늘 매수 찾기
            for trade in risk_data.get('daily_trades', []):
                if trade['stock_code'] == stock_code and trade['type'] == 'BUY':
                    buy_price = trade['price']
                    break

        # 종목명 조회
        stock_name = stock_code
        try:
            info = api.get_stock_info(stock_code)
            if info:
                stock_name = info.get('stk_nm', stock_code).strip()
        except Exception:
            pass

        print()
        print("=" * 60)
        print(f"📊 {stock_code} - {stock_name}")
        print("=" * 60)
        print(f"데이터 날짜: {data_date}")
        print()
        print(f"현재가: {current_price:,}원  (마지막 5분봉 종가)")
        print(f"시가:   {open_price:,}원")
        print(f"고가:   {high_price:,}원")
        print(f"저가:   {low_price:,}원")
        print()

        if buy_price:
            print(f"매수가: {buy_price:,}원")
            print()

            # 수익률 계산
            current_profit = ((current_price - buy_price) / buy_price) * 100
            high_profit = ((high_price - buy_price) / buy_price) * 100

            print(f"현재 수익률: {current_profit:+.2f}%")
            print(f"최고 수익률: {high_profit:+.2f}%")
            print()

        # 고가 대비 하락률
        decline_from_high = ((current_price - high_price) / high_price) * 100

        print(f"고가 대비 현재가: {decline_from_high:+.2f}%")
        print()
        print("=" * 60)

    except Exception as e:
        print(f"✗ 조회 실패: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    stock_code = "476060"  # 온코닉테라퓨틱스
    if len(sys.argv) > 1:
        stock_code = sys.argv[1]

    check_stock_price(stock_code)
