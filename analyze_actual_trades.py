#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
실제 거래 내역에 스퀴즈 모멘텀 적용 분석
"""

import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from utils.squeeze_momentum import calculate_squeeze_momentum, get_current_squeeze_signal


def get_minute_data(stock_code: str, date: str) -> pd.DataFrame:
    """
    분봉 데이터 가져오기 (pykrx는 분봉 지원 안함 - 일봉으로 대체)

    실제로는 키움 API로 분봉을 가져와야 하지만,
    여기서는 일봉 데이터로 해당 날짜 전후의 추세를 확인
    """
    try:
        from pykrx import stock

        # 해당 날짜 전후 30일 데이터
        target_date = datetime.strptime(date, '%Y-%m-%d')
        start_date = target_date - timedelta(days=60)
        end_date = target_date + timedelta(days=5)

        start_str = start_date.strftime('%Y%m%d')
        end_str = end_date.strftime('%Y%m%d')

        df = stock.get_market_ohlcv_by_date(start_str, end_str, stock_code)

        if df is None or df.empty:
            return pd.DataFrame()

        # 컬럼명 처리
        if '시가' in df.columns:
            df = df[['시가', '고가', '저가', '종가', '거래량']]
            df.columns = ['open', 'high', 'low', 'close', 'volume']
        else:
            if len(df.columns) >= 5:
                df = df.iloc[:, :5]
                df.columns = ['open', 'high', 'low', 'close', 'volume']

        return df

    except Exception as e:
        print(f"  ❌ 데이터 로드 실패: {e}")
        return pd.DataFrame()


def analyze_trade(trade_info: dict):
    """개별 거래 분석"""
    stock_code = str(trade_info['종목코드']).zfill(6)
    stock_name = trade_info['종목명']
    trade_date = pd.to_datetime(trade_info['날짜'])
    trade_type = trade_info['매매']
    price = trade_info['평단가']
    profit = trade_info['손익']

    date_str = trade_date.strftime('%Y-%m-%d')

    print(f"\n{'='*80}")
    print(f"종목: {stock_name} ({stock_code})")
    print(f"거래일시: {trade_date}")
    print(f"매매: {trade_type} | 가격: {price:,.0f}원", end='')
    if pd.notna(profit):
        print(f" | 손익: {profit:+,.0f}원")
    else:
        print()
    print(f"{'='*80}")

    # 일봉 데이터 가져오기
    df = get_minute_data(stock_code, date_str)

    if df.empty:
        print("  ⚠️  데이터 없음")
        return

    # 스퀴즈 모멘텀 계산
    df = calculate_squeeze_momentum(df)

    # 거래일 찾기
    trade_date_only = trade_date.date()
    matching_dates = [idx for idx in df.index if idx.date() == trade_date_only]

    if not matching_dates:
        print(f"  ⚠️  {date_str} 데이터 없음")
        # 가장 가까운 날짜 찾기
        closest_idx = df.index[df.index.get_indexer([trade_date], method='nearest')[0]]
        print(f"  → 가장 가까운 날짜: {closest_idx.date()}")
        signal = get_current_squeeze_signal(df.loc[:closest_idx])
    else:
        # 해당 날짜의 데이터 (첫 번째 매칭 인덱스 사용)
        signal = get_current_squeeze_signal(df.loc[:matching_dates[0]])

    print(f"\n  📊 스퀴즈 모멘텀 상태:")
    print(f"    - 신호: {signal['signal']}")
    print(f"    - 색상: {signal['color']}")
    print(f"    - 모멘텀: {signal['momentum']:.4f}")
    print(f"    - 가속: {signal['is_accelerating']}")
    print(f"    - 감속: {signal['is_decelerating']}")

    # 매수/매도 판단
    if trade_type == '매수':
        if signal['color'] == 'bright_green' and signal['signal'] == 'BUY':
            print(f"  ✅ 스퀴즈 모멘텀 매수 신호 일치!")
        elif signal['color'] in ['bright_green', 'dark_green']:
            print(f"  ⚠️  스퀴즈 녹색 구간이지만 신호는 {signal['signal']}")
        else:
            print(f"  ❌ 스퀴즈 모멘텀 매수 신호 없음 (빨간색 구간)")

    elif trade_type == '매도':
        if pd.notna(profit):
            if profit > 0:
                # 익절
                if signal['color'] == 'dark_green':
                    print(f"  ✅ 스퀴즈 모멘텀 부분 익절 신호 일치 (어두운 녹색)")
                elif signal['color'] in ['dark_red', 'bright_red']:
                    print(f"  ✅ 스퀴즈 모멘텀 전량 익절 신호 일치 (빨간색 전환)")
                elif signal['color'] == 'bright_green':
                    print(f"  ⚠️  여전히 밝은 녹색 - 너무 이른 익절일 수 있음")
                    print(f"  💡 스퀴즈는 더 보유 제안")
                else:
                    print(f"  ℹ️  스퀴즈 상태: {signal['color']}")
            else:
                # 손절
                print(f"  ℹ️  손절 (스퀴즈와 무관)")

    # 최근 5일 추세 표시
    if len(df) >= 5:
        recent_5 = df.tail(5)[['close', 'sqz_color', 'sqz_momentum']]
        print(f"\n  📈 최근 5일 추세:")
        for idx, row in recent_5.iterrows():
            color_emoji = {
                'bright_green': '🟢',
                'dark_green': '🟢',
                'dark_red': '🔴',
                'bright_red': '🔴',
                'gray': '⚪'
            }.get(row['sqz_color'], '⚪')

            print(f"    {idx.date()} | 종가: {row['close']:>6,.0f} | {color_emoji} {row['sqz_color']:15} | 모멘텀: {row['sqz_momentum']:>8.2f}")


def main():
    """메인 함수"""
    print("="*80)
    print("실제 거래 내역 스퀴즈 모멘텀 분석")
    print("="*80)

    # 엑셀 읽기
    excel_path = '/home/greatbps/projects/kiwoom_trading/docs/1231/투자결과.xlsx'
    df = pd.read_excel(excel_path)

    # 컬럼명 정리
    df.columns = ['번호', '날짜', '종목명', '종목코드', '매매', '수량', '평단가', '손익']

    # NaN 행 제거
    df = df.dropna(subset=['종목코드'])

    # 종목명 공백 제거
    df['종목명'] = df['종목명'].str.strip()

    # 주요 종목만 분석 (휴림로봇, 모비스, 삼보모터스)
    target_stocks = ['휴림로봇', '모비스', '삼보모터스', '아이티센글로벌', '오름테라퓨틱']

    for stock_name in target_stocks:
        stock_trades = df[df['종목명'] == stock_name]

        if len(stock_trades) == 0:
            continue

        print(f"\n\n{'#'*80}")
        print(f"# {stock_name} 거래 분석")
        print(f"{'#'*80}")

        for _, trade in stock_trades.iterrows():
            analyze_trade(trade)

    print(f"\n\n{'='*80}")
    print("분석 완료")
    print("="*80)


if __name__ == "__main__":
    main()
