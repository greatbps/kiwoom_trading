"""
키움 API로 5분봉 데이터 수집
삼성전자 5분봉 데이터를 최대한 수집
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
import time
import json

def collect_minute_data(stock_code: str, stock_name: str):
    """5분봉 데이터 수집 (연속 조회)"""

    api = KiwoomAPI()
    api.get_access_token()

    print(f"{'='*80}")
    print(f"{stock_name} ({stock_code}) 5분봉 데이터 수집")
    print(f"{'='*80}\n")

    all_data = []
    next_key = ""
    page = 1

    while True:
        print(f"[{page}페이지] 조회 중... (next_key: {next_key[:20] if next_key else 'None'})")

        # 5분봉 데이터 조회
        result = api.get_minute_chart(
            stock_code=stock_code,
            tic_scope='5',  # 5분봉
            cont_yn='Y' if next_key else 'N',
            next_key=next_key
        )

        if result.get('return_code') != 0:
            print(f"✗ 조회 실패: {result.get('return_msg')}")
            break

        data = result.get('stk_dt_pole_minute_chart_qry', [])

        if not data:
            print(f"✓ 더 이상 데이터 없음")
            break

        all_data.extend(data)
        print(f"  ✓ {len(data)}개 수집 (누적: {len(all_data)}개)")

        # 다음 페이지 키 확인
        next_key = result.get('next_key', '')

        if not next_key or next_key.strip() == '':
            print(f"✓ 마지막 페이지 도달")
            break

        # API 제한 (0.2초 대기)
        time.sleep(0.2)
        page += 1

        # 안전장치: 최대 100페이지
        if page > 100:
            print(f"⚠ 최대 페이지 도달 (100페이지)")
            break

    print(f"\n{'='*80}")
    print(f"수집 완료: 총 {len(all_data)}개 데이터")
    print(f"{'='*80}\n")

    # 파일로 저장
    filename = f'data/samsung_5min_{len(all_data)}.json'
    os.makedirs('data', exist_ok=True)

    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)

    print(f"✓ 저장 완료: {filename}")

    # 데이터 확인
    if all_data:
        print(f"\n[데이터 샘플]")
        print(f"  최신: {all_data[0].get('dt', 'N/A')} {all_data[0].get('tic_tm', 'N/A')} - {all_data[0].get('cur_prc', 'N/A')}원")
        print(f"  최초: {all_data[-1].get('dt', 'N/A')} {all_data[-1].get('tic_tm', 'N/A')} - {all_data[-1].get('cur_prc', 'N/A')}원")

    return all_data

if __name__ == "__main__":
    # 삼성전자 5분봉 수집
    data = collect_minute_data("005930", "삼성전자")

    print(f"\n수집 완료! 총 {len(data)}개 5분봉 데이터")
