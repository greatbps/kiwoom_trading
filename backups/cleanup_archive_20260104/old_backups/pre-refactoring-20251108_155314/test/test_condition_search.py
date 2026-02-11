"""
키움증권 조건검색식 + 통합 분석 자동 매매

1차 필터링: 조건검색식 실행 → 종목 리스트
2차 필터링: 통합 분석 점수 계산 → 70점 이상 종목 선별
3. 매수 조건 확인 → 자동 매수
"""
import asyncio
import websockets
import json
import sys
import os
from datetime import datetime
from typing import List, Dict
from concurrent.futures import ThreadPoolExecutor

# 상위 디렉토리를 경로에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kiwoom_api import KiwoomAPI
from analyzers.analysis_engine import AnalysisEngine
from strategies.trading_strategy import TradingStrategy
from core.auto_trading_handler import AutoTradingHandler
from dotenv import load_dotenv
import time

# 환경변수 로드
load_dotenv()

# WebSocket URL
SOCKET_URL = 'wss://api.kiwoom.com:10000/api/dostk/websocket'

# 통합 분석 점수 임계값
SCORE_THRESHOLD = 70.0


def analyze_single_stock_sync(stock_code: str, api: KiwoomAPI, engine: AnalysisEngine, strategy: TradingStrategy, threshold: float):
    """단일 종목 분석 (동기 함수 - 병렬 처리용)"""
    try:
        # 데이터 수집
        chart_result = api.get_daily_chart(stock_code=stock_code)
        chart_data = chart_result.get('stk_dt_pole_chart_qry') if chart_result.get('return_code') == 0 else None

        if not chart_data:
            return {'success': False, 'error': '차트 데이터 없음'}

        investor_result = api.get_investor_trend(stock_code=stock_code, amt_qty_tp="1", trde_tp="0", unit_tp="1")
        investor_data = investor_result.get('stk_invsr_orgn') if investor_result.get('return_code') == 0 else None

        program_result = api.get_program_trading(mrkt_tp="P00101", stex_tp="1")
        program_data = program_result.get('stk_prm_trde_prst') if program_result.get('return_code') == 0 else None

        stock_info_result = api.get_stock_info(stock_code=stock_code)
        stock_info = stock_info_result if stock_info_result.get('return_code') == 0 else None

        # 종목명 추출
        stock_name = stock_info.get('stk_nm_kr', stock_code) if stock_info else stock_code

        # 현재가 추출
        current_price = None
        if stock_info and stock_info.get('cur_prc'):
            current_price = float(str(stock_info['cur_prc']).replace(',', '').replace('+', '').replace('-', ''))

        if not current_price and chart_data:
            latest_candle = chart_data[0]
            current_price = float(str(latest_candle.get('cur_prc', 0)).replace(',', '').replace('+', '').replace('-', ''))

        if not current_price:
            return {'success': False, 'error': '현재가 정보 없음'}

        # 통합 분석
        analysis_result = engine.analyze(
            stock_code=stock_code,
            stock_name=stock_name,
            chart_data=chart_data,
            investor_data=investor_data,
            program_data=program_data,
            stock_info=stock_info
        )

        final_score = analysis_result['final_score']
        recommendation = analysis_result['recommendation']

        # 임계값 이상만 매매 계획 생성
        trading_plan = None
        if final_score >= threshold:
            trading_plan = strategy.generate_trading_plan(
                stock_code=stock_code,
                stock_name=stock_name,
                current_price=current_price,
                account_balance=10000000,  # 임시
                chart_data=chart_data,
                analysis_result=analysis_result
            )

        return {
            'success': True,
            'stock_code': stock_code,
            'stock_name': stock_name,
            'current_price': current_price,
            'final_score': final_score,
            'recommendation': recommendation,
            'analysis_result': analysis_result,
            'trading_plan': trading_plan
        }

    except Exception as e:
        return {
            'success': False,
            'stock_code': stock_code,
            'error': str(e)
        }


class KiwoomConditionTradingClient:
    """키움증권 조건검색 + 자동매매 클라이언트"""

    def __init__(self, access_token: str, api: KiwoomAPI,
                 analysis_engine: AnalysisEngine,
                 trading_strategy: TradingStrategy,
                 auto_trading_handler: AutoTradingHandler = None):
        self.uri = SOCKET_URL
        self.access_token = access_token
        self.api = api
        self.analysis_engine = analysis_engine
        self.trading_strategy = trading_strategy
        self.auto_trading_handler = auto_trading_handler

        self.websocket = None
        self.connected = False
        self.keep_running = True

        # 조건검색 결과
        self.condition_list = []
        self.condition_stocks = {}  # {seq: [stock_codes]}

        # 분석 결과 (점수 70 이상만)
        self.high_score_stocks = {}  # {stock_code: analysis_result}

        # 매수 대기/완료 종목
        self.buy_pending = {}  # {stock_code: plan}
        self.positions = {}  # {stock_code: position_info}

    async def connect(self):
        """WebSocket 서버에 연결"""
        try:
            self.websocket = await websockets.connect(self.uri)
            self.connected = True
            print("=" * 120)
            print(f"{'키움증권 조건검색 자동매매 시스템':^120}")
            print("=" * 120)

            # 로그인 패킷
            login_packet = {
                'trnm': 'LOGIN',
                'token': self.access_token
            }

            print(f"\n[{datetime.now().strftime('%H:%M:%S')}] WebSocket 로그인")
            await self.send_message(login_packet)

        except Exception as e:
            print(f'❌ 연결 오류: {e}')
            self.connected = False

    async def send_message(self, message):
        """서버에 메시지 전송"""
        if not self.connected:
            await self.connect()

        if self.connected:
            if not isinstance(message, str):
                message = json.dumps(message)
            await self.websocket.send(message)

    async def receive_messages(self):
        """서버 메시지 수신"""
        while self.keep_running:
            try:
                response = json.loads(await self.websocket.recv())
                trnm = response.get('trnm')

                # 로그인 응답
                if trnm == 'LOGIN':
                    if response.get('return_code') != 0:
                        print(f"❌ 로그인 실패: {response.get('return_msg')}")
                        await self.disconnect()
                    else:
                        print(f"✅ 로그인 성공")

                # PING 응답
                elif trnm == 'PING':
                    await self.send_message(response)

                # 조건검색식 목록 응답
                elif trnm == 'CNSRLST':
                    await self.handle_condition_list(response)

                # 조건검색 결과 (1차 필터링)
                elif trnm == 'CNSRREQ':
                    await self.handle_condition_result(response)

                # 실시간 조건검색 신호
                elif trnm == 'REAL':
                    await self.handle_realtime_signal(response)

            except websockets.ConnectionClosed:
                print('\n❌ WebSocket 연결 종료')
                self.connected = False
                break
            except Exception as e:
                print(f'\n❌ 수신 오류: {e}')
                import traceback
                traceback.print_exc()

    async def handle_condition_list(self, response):
        """조건검색식 목록 처리"""
        print(f"\n{'=' * 120}")
        print(f"{'조건검색식 목록':^120}")
        print(f"{'=' * 120}")

        self.condition_list = response.get('data', [])

        if self.condition_list:
            print(f"\n✅ 총 {len(self.condition_list)}개 조건검색식:")
            print("─" * 120)
            print(f"{'번호':<6} {'인덱스':<8} {'조건검색식명':<50}")
            print("─" * 120)

            greatbps_conditions = []
            for idx, condition in enumerate(self.condition_list, 1):
                cond_idx = condition[0]
                cond_name = condition[1]

                # GreatBPS 폴더 조건식만 출력
                if 'GreatBPS' in cond_name or 'GREAT' in cond_name:
                    print(f"{idx:<6} {cond_idx:<8} {cond_name:<50} ⭐")
                    greatbps_conditions.append((cond_idx, cond_name))
                else:
                    print(f"{idx:<6} {cond_idx:<8} {cond_name:<50}")

            print("=" * 120)

            # GreatBPS 조건식 저장
            self.greatbps_conditions = greatbps_conditions
        else:
            print("\n⚠️  조건검색식이 없습니다.")

    async def handle_condition_result(self, response):
        """조건검색 결과 처리 (1차 필터링)"""
        seq = response.get('seq')
        return_code = response.get('return_code')
        data = response.get('data')

        print(f"\n{'=' * 120}")
        print(f"{'조건검색 결과 (1차 필터링)':^120}")
        print(f"{'=' * 120}")
        print(f"[DEBUG] 원본 응답: {response}")
        print(f"조건식 번호: {seq}")
        print(f"응답 코드: {return_code}")

        # data가 None이면 빈 리스트로 처리
        if data is None:
            data = []

        print(f"발견 종목: {len(data)}개")

        if return_code != 0:
            print("❌ 조건검색 실패")
            return

        # 종목이 없으면 종료
        if len(data) == 0:
            print("⚠️  조건 만족 종목 없음")
            print("=" * 120)
            return

        # 종목 코드 추출 (A 제거)
        stock_codes = [item['jmcode'].replace('A', '') for item in data]
        self.condition_stocks[seq] = stock_codes

        print(f"\n1차 필터링 종목 리스트:")
        print("─" * 120)
        for i, code in enumerate(stock_codes[:10], 1):  # 최대 10개만 출력
            print(f"  {i}. {code}")
        if len(stock_codes) > 10:
            print(f"  ... 외 {len(stock_codes) - 10}개")
        print("─" * 120)

        # 2차 필터링 시작 (통합 분석)
        await self.analyze_stocks(stock_codes, seq)

    async def analyze_stocks(self, stock_codes: List[str], seq: str):
        """2차 필터링: 통합 분석 점수 계산 (병렬 처리)"""
        print(f"\n{'=' * 120}")
        print(f"{'2차 필터링: 통합 분석 (병렬 처리, 점수 {SCORE_THRESHOLD} 이상)':^120}")
        print(f"{'=' * 120}")
        print(f"총 {len(stock_codes)}개 종목 분석 시작 (최대 5개 동시 처리)")

        results = []
        loop = asyncio.get_event_loop()
        max_workers = 5

        # ThreadPoolExecutor로 병렬 실행
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 청크로 나누기 (max_workers개씩)
            for i in range(0, len(stock_codes), max_workers):
                chunk = stock_codes[i:i+max_workers]

                # 병렬 실행
                tasks = [
                    loop.run_in_executor(
                        executor,
                        analyze_single_stock_sync,
                        stock_code,
                        self.api,
                        self.analysis_engine,
                        self.trading_strategy,
                        SCORE_THRESHOLD
                    )
                    for stock_code in chunk
                ]

                # 완료 대기
                chunk_results = await asyncio.gather(*tasks)

                # 결과 처리
                for result in chunk_results:
                    if result['success']:
                        stock_code = result['stock_code']
                        stock_name = result['stock_name']
                        final_score = result['final_score']
                        recommendation = result['recommendation']

                        print(f"  ✓ {stock_code} ({stock_name}): {final_score:.2f}점 | {recommendation}")

                        # 70점 이상만 저장
                        if final_score >= SCORE_THRESHOLD and result.get('trading_plan'):
                            trading_plan = result['trading_plan']

                            results.append({
                                'stock_code': stock_code,
                                'stock_name': stock_name,
                                'current_price': result['current_price'],
                                'final_score': final_score,
                                'recommendation': recommendation,
                                'entry_signal': trading_plan['entry_signal']['signal'],
                                'entry_confidence': trading_plan['entry_signal']['confidence'],
                                'analysis_result': result['analysis_result'],
                                'trading_plan': trading_plan
                            })

                            self.high_score_stocks[stock_code] = results[-1]
                    else:
                        stock_code = result.get('stock_code', 'Unknown')
                        error = result.get('error', 'Unknown error')
                        print(f"  ✗ {stock_code}: {error}")

                print(f"  진행: {min(i+max_workers, len(stock_codes))}/{len(stock_codes)} 완료")

        # 결과 출력
        if results:
            print(f"\n{'=' * 120}")
            print(f"{'2차 필터링 결과 (점수 {SCORE_THRESHOLD} 이상)':^120}")
            print(f"{'=' * 120}")
            print(f"{'순위':<6} {'종목명':<14} {'코드':<8} {'현재가':>12} {'점수':>8} {'추천':>10} {'진입신호':>12} {'신뢰도':>10}")
            print("─" * 120)

            # 점수 순 정렬
            results.sort(key=lambda x: x['final_score'], reverse=True)

            for idx, r in enumerate(results, 1):
                print(f"{idx:<6} {r['stock_name']:<14} {r['stock_code']:<8} {r['current_price']:>12,.0f}원 "
                      f"{r['final_score']:>8.2f} {r['recommendation']:>10} {r['entry_signal']:>12} {r['entry_confidence']:>10}")

            print("=" * 120)

            # 매수 조건 확인
            buy_candidates = [r for r in results if r['entry_signal'] in ['BUY', 'STRONG_BUY']]

            if buy_candidates:
                print(f"\n{'=' * 120}")
                print(f"{'매수 조건 충족 종목 ({len(buy_candidates)}개)':^120}")
                print(f"{'=' * 120}")

                for idx, r in enumerate(buy_candidates, 1):
                    print(f"\n{idx}. {r['stock_name']} ({r['stock_code']})")
                    print(f"   현재가: {r['current_price']:,}원")
                    print(f"   점수: {r['final_score']:.2f}")
                    print(f"   진입신호: {r['entry_signal']} ({r['entry_confidence']})")
                    print(f"   매수 수량: {r['trading_plan']['position']['quantity']:,}주")
                    print(f"   투자 금액: {r['trading_plan']['position']['investment']:,}원")

                    # 매수 대기 리스트에 추가
                    self.buy_pending[r['stock_code']] = r

                print("=" * 120)
            else:
                print(f"\n⚠️  현재 매수 조건 충족 종목 없음")

        else:
            print(f"\n⚠️  점수 {SCORE_THRESHOLD} 이상 종목 없음")

    async def handle_realtime_signal(self, response):
        """실시간 조건검색 신호 처리"""
        data_list = response.get('data', [])

        for data_item in data_list:
            if data_item.get('name') == '조건검색':
                values = data_item.get('values', {})
                stock_code = values.get('9001', '').replace('A', '')
                signal_tp = values.get('843', '')  # I: 편입, D: 이탈
                cond_seq = values.get('841', '')

                print(f"\n{'🎯 실시간 조건검색 신호':^120}")
                print("─" * 120)
                print(f"시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
                print(f"종목코드: {stock_code}")
                print(f"조건식 번호: {cond_seq}")
                print(f"신호: {'✅ 편입' if signal_tp == 'I' else '❌ 이탈'}")
                print("─" * 120)

                # 편입 신호면 분석 시작
                if signal_tp == 'I':
                    print(f"→ 신규 편입 종목 분석 시작...")
                    await self.analyze_stocks([stock_code], cond_seq)

    async def execute_condition_search(self, seq: str, cond_name: str):
        """조건검색 실행"""
        print(f"\n[{datetime.now().strftime('%H:%M:%S')}] 조건검색 실행")
        print(f"  조건식 번호: {seq}")
        print(f"  조건식명: {cond_name}")

        await self.send_message({
            'trnm': 'CNSRREQ',
            'seq': seq,
            'search_type': '1',  # 조회타입
            'stex_tp': 'K'  # 거래소구분 (K: 코스피/코스닥)
        })

        # 응답 대기
        await asyncio.sleep(3)

    async def run(self):
        """WebSocket 실행"""
        await self.connect()
        await self.receive_messages()

    async def disconnect(self):
        """연결 종료"""
        self.keep_running = False
        if self.connected and self.websocket:
            await self.websocket.close()
            self.connected = False
            print('\n✅ WebSocket 연결 종료')


async def main():
    """메인 함수"""
    print("=" * 120)
    print(f"{'키움증권 조건검색 자동매매 시스템':^120}")
    print("=" * 120)

    # 1. REST API 및 분석 엔진 초기화
    print("\n[1] 시스템 초기화")
    api = KiwoomAPI()
    analysis_engine = AnalysisEngine()
    trading_strategy = TradingStrategy()

    print("  ✓ API 클라이언트 생성")
    print("  ✓ 통합 분석 엔진 생성")
    print("  ✓ 매매 전략 엔진 생성")

    # 2. AccessToken 발급
    print("\n[2] AccessToken 발급")
    api.get_access_token()

    if not api.access_token:
        print("❌ AccessToken 발급 실패")
        return

    # 3. WebSocket 클라이언트 생성
    print("\n[3] WebSocket 클라이언트 생성")
    client = KiwoomConditionTradingClient(
        access_token=api.access_token,
        api=api,
        analysis_engine=analysis_engine,
        trading_strategy=trading_strategy
    )

    # 4. WebSocket 백그라운드 실행
    receive_task = asyncio.create_task(client.run())

    # 5. 로그인 대기
    await asyncio.sleep(2)

    # 6. 조건검색식 목록 조회
    print("\n[4] 조건검색식 목록 조회")
    await client.send_message({'trnm': 'CNSRLST'})
    await asyncio.sleep(3)

    # 7. 조건검색식 실행 (31, 32, 33)
    test_conditions = [
        ('31', 'Momentum 전략'),
        ('32', 'Breakout 전략'),
        ('33', 'EOD 전략')
    ]

    print(f"\n[5] 조건검색식 실행 ({len(test_conditions)}개)")
    print("=" * 120)

    for cond_idx, cond_name in test_conditions:
        await client.execute_condition_search(cond_idx, cond_name)
        await asyncio.sleep(2)

    # 8. 실시간 신호 수신 대기
    print(f"\n[6] 실시간 신호 수신 모드")
    print("=" * 120)
    print("⏰ 10초 동안 실시간 신호를 수신합니다.")
    print("📌 새로운 종목이 조건에 편입되면 자동으로 분석합니다.")
    print("📌 Ctrl+C를 누르면 종료됩니다.")
    print("=" * 120)

    try:
        await asyncio.sleep(10)  # 10초 대기
    except KeyboardInterrupt:
        print("\n\n⚠️  사용자가 중단했습니다.")

    # 9. WebSocket 연결 종료
    await client.disconnect()

    # 10. REST API 종료
    api.close()

    print("\n" + "=" * 120)
    print(f"{'✅ 조건검색 자동매매 시스템 종료':^120}")
    print("=" * 120)

    # 수신 태스크 취소
    receive_task.cancel()
    try:
        await receive_task
    except asyncio.CancelledError:
        pass


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n⚠️  프로그램 종료")
