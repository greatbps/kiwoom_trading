"""
한국투자증권 REST API 클라이언트
================================

해외주식 + 국내주식(중기매매) 지원

사용법:
    from korea_invest_api import KoreaInvestAPI

    api = KoreaInvestAPI()
    api.get_access_token()

    # 해외주식 잔고 조회
    balance = api.get_overseas_balance()

    # 국내주식 잔고 조회
    balance = api.get_domestic_balance()
"""

import os
import time
import logging
import hashlib
import requests
from typing import Dict, List, Optional, Any
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv

# 환경변수 로드
env_path = Path(__file__).parent / '.env'
load_dotenv(dotenv_path=env_path)

logger = logging.getLogger(__name__)


class KoreaInvestAPI:
    """
    한국투자증권 REST API 클라이언트

    해외주식/국내주식 모두 지원
    """

    # API 엔드포인트
    BASE_URL_PROD = "https://openapi.koreainvestment.com:9443"
    BASE_URL_MOCK = "https://openapivts.koreainvestment.com:29443"

    def __init__(
        self,
        app_key: Optional[str] = None,
        app_secret: Optional[str] = None,
        account_number: Optional[str] = None,
        is_mock: bool = False
    ):
        """
        초기화

        Args:
            app_key: API 앱키 (없으면 환경변수에서 로드)
            app_secret: API 시크릿키 (없으면 환경변수에서 로드)
            account_number: 계좌번호 (없으면 환경변수에서 로드)
            is_mock: 모의투자 여부
        """
        self.app_key = app_key or os.getenv('KIS_APP_KEY')
        self.app_secret = app_secret or os.getenv('KIS_APP_SECRET')
        self.account_number = account_number or os.getenv('KIS_ACCOUNT_NUMBER')
        self.is_mock = is_mock

        # 계좌번호 파싱 (64556264-01 → CANO=64556264, ACNT_PRDT_CD=01)
        if self.account_number and '-' in self.account_number:
            parts = self.account_number.split('-')
            self.cano = parts[0]  # 계좌번호 앞 8자리
            self.acnt_prdt_cd = parts[1]  # 계좌상품코드
        else:
            self.cano = self.account_number[:8] if self.account_number else ''
            self.acnt_prdt_cd = self.account_number[8:] if self.account_number and len(self.account_number) > 8 else '01'

        # 토큰
        self.access_token = None
        self.token_expires_at = None

        # 토큰 캐시 파일
        self.token_cache_file = Path(__file__).parent / '.kis_token_cache.json'

        # 기본 URL
        self.base_url = self.BASE_URL_MOCK if is_mock else self.BASE_URL_PROD

        # 캐시된 토큰 로드
        self._load_cached_token()

        logger.info(f"KoreaInvestAPI 초기화")
        logger.info(f"  계좌: {self.cano}-{self.acnt_prdt_cd}")
        logger.info(f"  모드: {'모의투자' if is_mock else '실전투자'}")

    def _load_cached_token(self):
        """캐시된 토큰 로드"""
        try:
            if self.token_cache_file.exists():
                import json
                with open(self.token_cache_file, 'r') as f:
                    cache = json.load(f)

                # 만료 시간 확인
                expires_at = cache.get('expires_at', 0)
                if time.time() < expires_at - 300:  # 5분 여유
                    self.access_token = cache.get('access_token')
                    self.token_expires_at = expires_at
                    logger.info(f"캐시된 토큰 로드 (만료: {cache.get('expires_str', 'N/A')})")
        except Exception as e:
            logger.debug(f"토큰 캐시 로드 실패: {e}")

    def _save_token_cache(self, token: str, expires_at: float, expires_str: str):
        """토큰 캐시 저장"""
        try:
            import json
            cache = {
                'access_token': token,
                'expires_at': expires_at,
                'expires_str': expires_str,
                'saved_at': datetime.now().isoformat()
            }
            with open(self.token_cache_file, 'w') as f:
                json.dump(cache, f, indent=2)
            logger.debug("토큰 캐시 저장 완료")
        except Exception as e:
            logger.error(f"토큰 캐시 저장 실패: {e}")

    def get_access_token(self) -> str:
        """
        접근 토큰 발급

        Returns:
            접근 토큰
        """
        # 토큰이 유효하면 재사용
        if self.access_token and self.token_expires_at:
            if time.time() < self.token_expires_at - 300:  # 5분 여유
                return self.access_token

        url = f"{self.base_url}/oauth2/tokenP"

        data = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }

        headers = {
            "Content-Type": "application/json;charset=UTF-8"
        }

        try:
            response = requests.post(url, json=data, headers=headers, timeout=10)
            response.raise_for_status()

            result = response.json()

            if 'access_token' in result:
                self.access_token = result['access_token']
                expires_in = result.get('expires_in', 86400)
                self.token_expires_at = time.time() + expires_in
                expires_str = result.get('access_token_token_expired', 'N/A')

                # 토큰 캐시 저장
                self._save_token_cache(self.access_token, self.token_expires_at, expires_str)

                print(f"✅ 한투 토큰 발급 성공")
                print(f"   만료: {expires_str}")

                return self.access_token
            else:
                logger.error(f"토큰 발급 실패: {result}")
                return None

        except Exception as e:
            logger.error(f"토큰 발급 오류: {e}")
            return None

    def _get_headers(self, tr_id: str, tr_cont: str = "") -> Dict[str, str]:
        """공통 헤더 생성"""
        return {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "tr_cont": tr_cont,
            "custtype": "P",  # 개인
        }

    def _get_hashkey(self, data: Dict) -> str:
        """Hashkey 생성 (POST 요청시 필요)"""
        url = f"{self.base_url}/uapi/hashkey"

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "appkey": self.app_key,
            "appsecret": self.app_secret
        }

        try:
            response = requests.post(url, json=data, headers=headers, timeout=10)
            result = response.json()
            return result.get('HASH', '')
        except Exception as e:
            logger.error(f"Hashkey 생성 오류: {e}")
            return ''

    # =========================================================================
    # 해외주식 API
    # =========================================================================

    def get_overseas_balance(self, currency: str = "USD") -> Dict[str, Any]:
        """
        해외주식 잔고 조회

        Args:
            currency: 통화 (USD, CNY, JPY, VND, HKD)

        Returns:
            잔고 정보
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/inquire-balance"

        # TR_ID: 실전 TTTS3012R, 모의 VTTS3012R
        tr_id = "VTTS3012R" if self.is_mock else "TTTS3012R"

        headers = self._get_headers(tr_id)

        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": "NASD",  # 나스닥 (미국: NASD, NYSE, AMEX)
            "TR_CRCY_CD": currency,
            "CTX_AREA_FK200": "",
            "CTX_AREA_NK200": ""
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            result = response.json()

            if result.get('rt_cd') == '0':
                return {
                    'success': True,
                    'data': result.get('output1', []),  # 보유종목
                    'summary': result.get('output2', {}),  # 요약
                    'msg': result.get('msg1', '')
                }
            else:
                return {
                    'success': False,
                    'error': result.get('msg1', 'Unknown error'),
                    'code': result.get('msg_cd', '')
                }

        except Exception as e:
            logger.error(f"해외주식 잔고 조회 오류: {e}")
            return {'success': False, 'error': str(e)}

    def get_overseas_price(self, symbol: str, exchange: str = "NAS") -> Dict[str, Any]:
        """
        해외주식 현재가 조회

        Args:
            symbol: 종목코드 (예: AAPL, TSLA)
            exchange: 거래소 (NAS=나스닥, NYS=뉴욕, AMS=아멕스)

        Returns:
            현재가 정보
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/uapi/overseas-price/v1/quotations/price"

        tr_id = "HHDFS00000300"
        headers = self._get_headers(tr_id)

        params = {
            "AUTH": "",
            "EXCD": exchange,
            "SYMB": symbol
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            result = response.json()

            if result.get('rt_cd') == '0':
                output = result.get('output', {})
                return {
                    'success': True,
                    'symbol': symbol,
                    'price': float(output.get('last', 0)),
                    'change': float(output.get('diff', 0)),
                    'change_pct': float(output.get('rate', 0)),
                    'volume': int(output.get('tvol', 0)),
                    'open': float(output.get('open', 0)),
                    'high': float(output.get('high', 0)),
                    'low': float(output.get('low', 0)),
                    'raw': output
                }
            else:
                return {
                    'success': False,
                    'error': result.get('msg1', 'Unknown error')
                }

        except Exception as e:
            logger.error(f"해외주식 현재가 조회 오류: {e}")
            return {'success': False, 'error': str(e)}

    def order_overseas_stock(
        self,
        symbol: str,
        side: str,  # "BUY" or "SELL"
        qty: int,
        price: float = 0,
        exchange: str = "NASD",
        order_type: str = "LOC"  # LOC=지정가, MOC=시장가
    ) -> Dict[str, Any]:
        """
        해외주식 주문

        Args:
            symbol: 종목코드 (예: AAPL)
            side: 매수/매도 ("BUY" or "SELL")
            qty: 수량
            price: 가격 (시장가면 0)
            exchange: 거래소 (NASD, NYSE, AMEX)
            order_type: 주문유형

        Returns:
            주문 결과
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order"

        # TR_ID 결정
        if self.is_mock:
            tr_id = "VTTT1002U" if side.upper() == "BUY" else "VTTT1001U"
        else:
            tr_id = "TTTT1002U" if side.upper() == "BUY" else "TTTT1006U"

        data = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange,
            "PDNO": symbol,
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": str(price) if price > 0 else "0",
            "CTAC_TLNO": "",
            "ORD_SVR_DVSN_CD": "0",
            "ORD_DVSN": "00"  # 지정가
        }

        headers = self._get_headers(tr_id)
        hashkey = self._get_hashkey(data)
        if hashkey:
            headers["hashkey"] = hashkey

        try:
            response = requests.post(url, json=data, headers=headers, timeout=10)
            result = response.json()

            if result.get('rt_cd') == '0':
                output = result.get('output', {})
                return {
                    'success': True,
                    'order_no': output.get('ODNO', ''),
                    'order_time': output.get('ORD_TMD', ''),
                    'msg': result.get('msg1', '')
                }
            else:
                return {
                    'success': False,
                    'error': result.get('msg1', 'Unknown error'),
                    'code': result.get('msg_cd', '')
                }

        except Exception as e:
            logger.error(f"해외주식 주문 오류: {e}")
            return {'success': False, 'error': str(e)}

    def cancel_overseas_order(
        self,
        order_no: str,
        symbol: str,
        qty: int,
        exchange: str = "NASD"
    ) -> Dict[str, Any]:
        """
        해외주식 주문 취소

        Args:
            order_no: 원주문번호
            symbol: 종목코드
            qty: 취소수량
            exchange: 거래소

        Returns:
            취소 결과
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/uapi/overseas-stock/v1/trading/order-rvsecncl"

        tr_id = "VTTT1004U" if self.is_mock else "TTTT1004U"

        data = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "OVRS_EXCG_CD": exchange,
            "PDNO": symbol,
            "ORGN_ODNO": order_no,
            "RVSE_CNCL_DVSN_CD": "02",  # 02=취소
            "ORD_QTY": str(qty),
            "OVRS_ORD_UNPR": "0",
            "CTAC_TLNO": "",
            "MGCO_APTM_ODNO": "",
            "ORD_SVR_DVSN_CD": "0"
        }

        headers = self._get_headers(tr_id)
        hashkey = self._get_hashkey(data)
        if hashkey:
            headers["hashkey"] = hashkey

        try:
            response = requests.post(url, json=data, headers=headers, timeout=10)
            result = response.json()

            if result.get('rt_cd') == '0':
                output = result.get('output', {})
                return {
                    'success': True,
                    'order_no': output.get('ODNO', ''),
                    'msg': result.get('msg1', '')
                }
            else:
                return {
                    'success': False,
                    'error': result.get('msg1', 'Unknown error'),
                    'code': result.get('msg_cd', '')
                }

        except Exception as e:
            logger.error(f"해외주식 주문 취소 오류: {e}")
            return {'success': False, 'error': str(e)}

    # =========================================================================
    # 국내주식 API (중기매매용)
    # =========================================================================

    def get_domestic_balance(self) -> Dict[str, Any]:
        """
        국내주식 잔고 조회

        Returns:
            잔고 정보
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/inquire-balance"

        tr_id = "VTTC8434R" if self.is_mock else "TTTC8434R"
        headers = self._get_headers(tr_id)

        params = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "AFHR_FLPR_YN": "N",
            "OFL_YN": "",
            "INQR_DVSN": "02",
            "UNPR_DVSN": "01",
            "FUND_STTL_ICLD_YN": "N",
            "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00",
            "CTX_AREA_FK100": "",
            "CTX_AREA_NK100": ""
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            result = response.json()

            if result.get('rt_cd') == '0':
                return {
                    'success': True,
                    'data': result.get('output1', []),  # 보유종목
                    'summary': result.get('output2', []),  # 요약
                    'msg': result.get('msg1', '')
                }
            else:
                return {
                    'success': False,
                    'error': result.get('msg1', 'Unknown error'),
                    'code': result.get('msg_cd', '')
                }

        except Exception as e:
            logger.error(f"국내주식 잔고 조회 오류: {e}")
            return {'success': False, 'error': str(e)}

    def order_domestic_stock(
        self,
        stock_code: str,
        side: str,  # "BUY" or "SELL"
        qty: int,
        price: int = 0,
        order_type: str = "00"  # 00=지정가, 01=시장가
    ) -> Dict[str, Any]:
        """
        국내주식 주문

        Args:
            stock_code: 종목코드 (6자리)
            side: 매수/매도 ("BUY" or "SELL")
            qty: 수량
            price: 가격 (시장가면 0)
            order_type: 주문유형 (00=지정가, 01=시장가)

        Returns:
            주문 결과
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/uapi/domestic-stock/v1/trading/order-cash"

        # TR_ID 결정
        if self.is_mock:
            tr_id = "VTTC0802U" if side.upper() == "BUY" else "VTTC0801U"
        else:
            tr_id = "TTTC0802U" if side.upper() == "BUY" else "TTTC0801U"

        data = {
            "CANO": self.cano,
            "ACNT_PRDT_CD": self.acnt_prdt_cd,
            "PDNO": stock_code,
            "ORD_DVSN": order_type,
            "ORD_QTY": str(qty),
            "ORD_UNPR": str(price) if price > 0 else "0"
        }

        headers = self._get_headers(tr_id)
        hashkey = self._get_hashkey(data)
        if hashkey:
            headers["hashkey"] = hashkey

        try:
            response = requests.post(url, json=data, headers=headers, timeout=10)
            result = response.json()

            if result.get('rt_cd') == '0':
                output = result.get('output', {})
                return {
                    'success': True,
                    'order_no': output.get('ODNO', ''),
                    'order_time': output.get('ORD_TMD', ''),
                    'msg': result.get('msg1', '')
                }
            else:
                return {
                    'success': False,
                    'error': result.get('msg1', 'Unknown error'),
                    'code': result.get('msg_cd', '')
                }

        except Exception as e:
            logger.error(f"국내주식 주문 오류: {e}")
            return {'success': False, 'error': str(e)}

    def get_news_titles(
        self,
        stock_code: str = "",
        count: int = 20,
        date: str = "",
        hour: str = "",
        serial_no: str = ""
    ) -> List[Dict[str, str]]:
        """
        HTS 종합 시황/공시 제목 조회 (국내주식-141)

        TR_ID: FHKST01011800
        URL: GET /uapi/domestic-stock/v1/quotations/news-title

        Args:
            stock_code: 종목코드 6자리 (빈 문자열 = 전체)
            count: 반환할 최대 건수 (기본 20)
            date: 조회 시작일 YYYYMMDD (빈 문자열 = 오늘)
            hour: 조회 시작시간 HHMMSS (빈 문자열 = 현재)
            serial_no: 연속 조회용 일련번호 (첫 조회 시 빈 문자열)

        Returns:
            뉴스 항목 리스트:
              - title: 뉴스 제목
              - date: 날짜 YYYYMMDD
              - time: 시간 HHMMSS
              - source_code: 뉴스 제공사 코드
              - news_code: 뉴스 대분류 코드
              - serial_no: 연속 조회용 일련번호
        """
        if not self.access_token:
            self.get_access_token()

        url = f"{self.base_url}/uapi/domestic-stock/v1/quotations/news-title"
        headers = self._get_headers("FHKST01011800")
        headers["custtype"] = "P"

        params = {
            "FID_NEWS_OFER_ENTP_CODE": "",
            "FID_COND_MRKT_CLS_CODE": "",
            "FID_INPUT_ISCD": stock_code,
            "FID_TITL_CNTT": "",
            "FID_INPUT_DATE_1": date,
            "FID_INPUT_HOUR_1": hour,
            "FID_RANK_SORT_CLS_CODE": "",
            "FID_INPUT_SRNO": serial_no,
        }

        try:
            response = requests.get(url, headers=headers, params=params, timeout=10)
            result = response.json()

            if result.get('rt_cd') == '0':
                raw = result.get('output', []) or []
                items = []
                for item in raw[:count]:
                    items.append({
                        'title': item.get('hts_pbnt_titl_cntt', ''),
                        'date': item.get('data_dt', ''),
                        'time': item.get('data_tm', ''),
                        'source_code': item.get('news_ofer_entp_code', ''),
                        'news_code': item.get('news_lrdv_code', ''),
                        'serial_no': item.get('cntt_usiq_srno', ''),
                    })
                return items
            else:
                logger.warning(f"뉴스 조회 실패: {result.get('msg1', '')} ({result.get('msg_cd', '')})")
                return []

        except Exception as e:
            logger.error(f"뉴스 조회 오류: {e}")
            return []


# =============================================================================
# 테스트
# =============================================================================

if __name__ == "__main__":
    from rich.console import Console
    from rich.table import Table

    console = Console()

    print("=" * 60)
    print("한국투자증권 API 테스트")
    print("=" * 60)

    # API 초기화
    api = KoreaInvestAPI()

    # 토큰 발급
    token = api.get_access_token()
    if not token:
        print("토큰 발급 실패")
        exit(1)

    # 해외주식 잔고 조회
    print("\n📊 해외주식 잔고 조회...")
    overseas = api.get_overseas_balance()

    if overseas['success']:
        holdings = overseas['data']
        if holdings:
            table = Table(title="해외주식 보유현황")
            table.add_column("종목")
            table.add_column("수량", justify="right")
            table.add_column("평균가", justify="right")
            table.add_column("현재가", justify="right")
            table.add_column("수익률", justify="right")

            for h in holdings:
                table.add_row(
                    h.get('ovrs_pdno', ''),
                    h.get('ovrs_cblc_qty', '0'),
                    h.get('pchs_avg_pric', '0'),
                    h.get('now_pric2', '0'),
                    f"{h.get('evlu_pfls_rt', '0')}%"
                )

            console.print(table)
        else:
            print("보유 해외주식 없음")
    else:
        print(f"조회 실패: {overseas.get('error')}")

    # 국내주식 잔고 조회
    print("\n📈 국내주식 잔고 조회...")
    domestic = api.get_domestic_balance()

    if domestic['success']:
        holdings = domestic['data']
        if holdings:
            table = Table(title="국내주식 보유현황")
            table.add_column("종목명")
            table.add_column("종목코드")
            table.add_column("수량", justify="right")
            table.add_column("평균가", justify="right")
            table.add_column("현재가", justify="right")
            table.add_column("수익률", justify="right")

            for h in holdings:
                table.add_row(
                    h.get('prdt_name', ''),
                    h.get('pdno', ''),
                    h.get('hldg_qty', '0'),
                    h.get('pchs_avg_pric', '0'),
                    h.get('prpr', '0'),
                    f"{h.get('evlu_pfls_rt', '0')}%"
                )

            console.print(table)
        else:
            print("보유 국내주식 없음")
    else:
        print(f"조회 실패: {domestic.get('error')}")
