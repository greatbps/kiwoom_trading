"""
분석 성능 벤치마크 테스트
실제 10개 종목으로 정확한 시간 측정
"""
import sys
import os
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.analysis_engine import AnalysisEngine

def benchmark_analysis():
    """10개 종목 분석 시간 측정"""

    # 테스트할 종목 (Momentum 전략 결과에서)
    test_stocks = [
        "001440",  # 대한전선
        "005070",  # 코스모신소재
        "005690",  # 파미셀
        "007660",  # 이수페타시스
        "009830",  # 한화솔루션
        "010140",  # 삼성중공업
        "011080",  # 오리온홀딩스
        "022100",  # 포스코DX
        "062040",  # 케이피엠테크
        "090360",  # 로보스타
    ]

    print("=" * 80)
    print("📊 분석 성능 벤치마크 테스트 (10개 종목)")
    print("=" * 80)
    print()

    # API 초기화
    print("[1] API 초기화...")
    api = KiwoomAPI()
    api.get_access_token()

    engine = AnalysisEngine()

    print("✓ 초기화 완료")
    print()

    # 전체 시작 시간
    total_start = time.time()

    results = []

    for i, stock_code in enumerate(test_stocks, 1):
        print(f"[{i}/10] {stock_code} 분석 중...")

        # 종목별 시작 시간
        stock_start = time.time()

        try:
            # 1. 종목 정보 조회
            step1_start = time.time()
            stock_info = api.get_stock_info(stock_code)
            step1_time = time.time() - step1_start

            stock_name = stock_info.get('stk_nm', stock_code)

            # 2. 종합 분석
            step2_start = time.time()
            analysis_result = engine.analyze(stock_code, stock_name, stock_info=stock_info)
            step2_time = time.time() - step2_start

            # 종목별 총 시간
            stock_total = time.time() - stock_start

            results.append({
                'code': stock_code,
                'name': stock_name,
                'info_time': step1_time,
                'analysis_time': step2_time,
                'total_time': stock_total,
                'score': analysis_result['final_score']
            })

            print(f"  ✓ 완료: {stock_total:.2f}초 (정보: {step1_time:.2f}초, 분석: {step2_time:.2f}초)")

        except Exception as e:
            print(f"  ✗ 실패: {e}")
            results.append({
                'code': stock_code,
                'name': 'ERROR',
                'info_time': 0,
                'analysis_time': 0,
                'total_time': 0,
                'score': 0
            })

        print()

    # 전체 종료 시간
    total_time = time.time() - total_start

    # 결과 출력
    print("=" * 80)
    print("📊 벤치마크 결과")
    print("=" * 80)
    print()

    # 개별 결과
    print("종목별 소요 시간:")
    print("-" * 80)
    for i, r in enumerate(results, 1):
        print(f"{i:2d}. {r['code']} ({r['name'][:10]:10s}) | "
              f"총 {r['total_time']:5.2f}초 = 정보 {r['info_time']:5.2f}초 + 분석 {r['analysis_time']:5.2f}초")
    print("-" * 80)
    print()

    # 통계
    valid_results = [r for r in results if r['total_time'] > 0]

    if valid_results:
        avg_info = sum(r['info_time'] for r in valid_results) / len(valid_results)
        avg_analysis = sum(r['analysis_time'] for r in valid_results) / len(valid_results)
        avg_total = sum(r['total_time'] for r in valid_results) / len(valid_results)
        max_time = max(r['total_time'] for r in valid_results)
        min_time = min(r['total_time'] for r in valid_results)

        print("통계:")
        print(f"  • 전체 소요 시간: {total_time:.2f}초 ({total_time/60:.2f}분)")
        print(f"  • 종목당 평균: {avg_total:.2f}초")
        print(f"    - 정보 조회: {avg_info:.2f}초")
        print(f"    - 분석 실행: {avg_analysis:.2f}초")
        print(f"  • 최대 시간: {max_time:.2f}초")
        print(f"  • 최소 시간: {min_time:.2f}초")
        print()

        # 100개 예상
        print("예상 소요 시간:")
        print(f"  • 100개 종목: {avg_total * 100:.0f}초 ({avg_total * 100 / 60:.1f}분)")
        print(f"  • 200개 종목: {avg_total * 200:.0f}초 ({avg_total * 200 / 60:.1f}분)")
        print()

        # 병목 분석
        info_pct = (avg_info / avg_total) * 100
        analysis_pct = (avg_analysis / avg_total) * 100

        print("병목 분석:")
        print(f"  • 정보 조회: {info_pct:.1f}%")
        print(f"  • 분석 실행: {analysis_pct:.1f}%")

        if info_pct > 50:
            print(f"  → [병목] API 호출이 주요 병목 ({info_pct:.1f}%)")
        else:
            print(f"  → [병목] 분석 로직이 주요 병목 ({analysis_pct:.1f}%)")

    print()
    print("=" * 80)

if __name__ == "__main__":
    benchmark_analysis()
