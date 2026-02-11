"""
병렬 처리 성능 벤치마크
asyncio로 최대 5개 종목 동시 분석
"""
import sys
import os
import time
import asyncio
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.analysis_engine import AnalysisEngine

def analyze_single_stock(stock_code, api, engine):
    """단일 종목 분석 (동기 함수)"""
    try:
        # 종목 정보 조회
        stock_info = api.get_stock_info(stock_code)
        stock_name = stock_info.get('stk_nm', stock_code)

        # 종합 분석
        analysis_result = engine.analyze(stock_code, stock_name, stock_info=stock_info)

        return {
            'code': stock_code,
            'name': stock_name,
            'score': analysis_result['final_score'],
            'success': True
        }
    except Exception as e:
        return {
            'code': stock_code,
            'name': 'ERROR',
            'score': 0,
            'success': False,
            'error': str(e)
        }

async def analyze_stocks_parallel(stock_codes, max_workers=5):
    """병렬 처리로 종목 분석"""

    # API 및 엔진 초기화 (각 워커가 공유)
    api = KiwoomAPI()
    api.get_access_token()
    engine = AnalysisEngine()

    # ThreadPoolExecutor로 병렬 실행
    loop = asyncio.get_event_loop()

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # 청크로 나누기 (max_workers개씩)
        results = []

        for i in range(0, len(stock_codes), max_workers):
            chunk = stock_codes[i:i+max_workers]

            # 병렬 실행
            tasks = [
                loop.run_in_executor(
                    executor,
                    analyze_single_stock,
                    stock_code,
                    api,
                    engine
                )
                for stock_code in chunk
            ]

            # 완료 대기
            chunk_results = await asyncio.gather(*tasks)
            results.extend(chunk_results)

            print(f"  진행: {min(i+max_workers, len(stock_codes))}/{len(stock_codes)} 완료")

    return results

def main():
    """메인 함수"""

    # 테스트할 종목
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
    print("🚀 병렬 처리 성능 벤치마크 (10개 종목, 최대 5개 동시)")
    print("=" * 80)
    print()

    # 시작
    start_time = time.time()

    # 병렬 실행
    results = asyncio.run(analyze_stocks_parallel(test_stocks, max_workers=5))

    # 종료
    total_time = time.time() - start_time

    # 결과 출력
    print()
    print("=" * 80)
    print("📊 벤치마크 결과")
    print("=" * 80)
    print()

    # 성공한 결과만
    success_results = [r for r in results if r['success']]

    print(f"성공: {len(success_results)}/{len(test_stocks)}개")
    print()

    print("종목별 결과:")
    print("-" * 80)
    for i, r in enumerate(results, 1):
        if r['success']:
            print(f"{i:2d}. {r['code']} ({r['name'][:10]:10s}) | 점수: {r['score']:.2f}점")
        else:
            print(f"{i:2d}. {r['code']} | ✗ 실패: {r.get('error', 'Unknown')}")
    print("-" * 80)
    print()

    # 통계
    print("성능:")
    print(f"  • 전체 소요 시간: {total_time:.2f}초 ({total_time/60:.2f}분)")
    print(f"  • 종목당 평균: {total_time/len(test_stocks):.2f}초")
    print()

    # 순차 처리와 비교
    sequential_time = 10.27 * len(test_stocks)  # 실측 평균
    speedup = sequential_time / total_time

    print("비교:")
    print(f"  • 순차 처리 (예상): {sequential_time:.2f}초 ({sequential_time/60:.2f}분)")
    print(f"  • 병렬 처리 (실측): {total_time:.2f}초 ({total_time/60:.2f}분)")
    print(f"  • 속도 향상: {speedup:.2f}배 ({(1-total_time/sequential_time)*100:.1f}% 단축)")
    print()

    # 100개 예상
    time_per_stock_parallel = total_time / len(test_stocks)
    time_100_parallel = time_per_stock_parallel * 100
    time_100_sequential = 10.27 * 100

    print("100개 종목 예상:")
    print(f"  • 순차 처리: {time_100_sequential:.0f}초 ({time_100_sequential/60:.1f}분)")
    print(f"  • 병렬 처리: {time_100_parallel:.0f}초 ({time_100_parallel/60:.1f}분)")
    print(f"  • 속도 향상: {time_100_sequential/time_100_parallel:.2f}배")
    print()
    print("=" * 80)

if __name__ == "__main__":
    main()
