"""
키움증권 REST API 접속 모듈
"""
import os
import requests
import time
from typing import Optional, Dict, Any
from dotenv import load_dotenv
import base64
import hashlib
import hmac

from exceptions import (
    handle_api_errors,
    handle_trading_errors,
    retry_on_error,
    AuthenticationError,
    ConnectionError as TradingConnectionError,
    TimeoutError as TradingTimeoutError,
    APIException,
    ConfigurationError,
    OrderFailedError,
    InsufficientFundsError
)


class KiwoomAPI:
    """키움증권 REST API 클라이언트"""

    # API 엔드포인트
    BASE_URL = "https://api.kiwoom.com"

    def __init__(self, api_key: Optional[str] = None, api_secret: Optional[str] = None,
                 account_number: Optional[str] = None, user_id: Optional[str] = None):
        """
        초기화

        Args:
            api_key: API 키 (없으면 .env에서 로드)
            api_secret: API 시크릿 (없으면 .env에서 로드)
            account_number: 계좌번호 (없으면 .env에서 로드)
            user_id: 사용자 ID (없으면 .env에서 로드)
        """
        # .env 파일 로드
        load_dotenv()

        # API 인증 정보
        self.api_key = api_key or os.getenv("KIWOOM_APP_KEY")
        self.api_secret = api_secret or os.getenv("KIWOOM_APP_SECRET")
        self.account_number = account_number or os.getenv("KIWOOM_ACCOUNT_NUMBER")
        self.user_id = user_id or os.getenv("KIWOOM_USER_ID")

        # 필수 정보 검증
        if not all([self.api_key, self.api_secret]):
            raise ConfigurationError(
                "API 키 정보가 필요합니다. .env 파일을 확인하세요.",
                config_key="KIWOOM_APP_KEY or KIWOOM_APP_SECRET"
            )

        # 토큰 관리
        self.access_token: Optional[str] = None
        self.token_expires_at: Optional[float] = None

        # 세션 생성
        self.session = requests.Session()

    def _generate_signature(self, method: str, path: str, params: Dict[str, Any] = None,
                          body: Dict[str, Any] = None) -> str:
        """
        API 요청 서명 생성

        Args:
            method: HTTP 메서드
            path: API 경로
            params: 쿼리 파라미터
            body: 요청 바디

        Returns:
            서명 문자열
        """
        timestamp = str(int(time.time() * 1000))

        # 서명할 메시지 생성
        message = f"{method.upper()}\n{path}\n{timestamp}"

        if params:
            query_string = "&".join([f"{k}={v}" for k, v in sorted(params.items())])
            message += f"\n{query_string}"

        if body:
            import json
            message += f"\n{json.dumps(body, separators=(',', ':'))}"

        # HMAC-SHA256 서명 생성
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()

        # Base64 인코딩
        return base64.b64encode(signature).decode('utf-8')

    def _get_headers(self, method: str = "GET", path: str = "",
                    params: Dict[str, Any] = None, body: Dict[str, Any] = None) -> Dict[str, str]:
        """
        API 요청 헤더 생성

        Args:
            method: HTTP 메서드
            path: API 경로
            params: 쿼리 파라미터
            body: 요청 바디

        Returns:
            헤더 딕셔너리
        """
        timestamp = str(int(time.time() * 1000))

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "apikey": self.api_key,
            "timestamp": timestamp,
        }

        # 서명 생성 및 추가
        if path:
            signature = self._generate_signature(method, path, params, body)
            headers["signature"] = signature

        # 인증 토큰이 있으면 추가
        if self.access_token:
            headers["authorization"] = f"Bearer {self.access_token}"

        return headers

    def _handle_request_error(self, e: requests.exceptions.RequestException, operation: str, timeout: int = None):
        """
        HTTP 요청 에러를 적절한 Trading 예외로 변환

        Args:
            e: requests 예외
            operation: 작업 설명
            timeout: 타임아웃 시간 (초)

        Raises:
            TradingTimeoutError: 타임아웃 시
            TradingConnectionError: 연결 실패 시
            AuthenticationError: 인증 실패 시
            APIException: 기타 API 오류 시
        """
        if isinstance(e, requests.exceptions.Timeout):
            raise TradingTimeoutError(
                f"{operation} 타임아웃",
                timeout_seconds=timeout
            ) from e
        elif isinstance(e, requests.exceptions.ConnectionError):
            raise TradingConnectionError(
                f"{operation} 연결 실패: {str(e)}"
            ) from e
        elif isinstance(e, requests.exceptions.HTTPError):
            status_code = e.response.status_code if e.response else None
            try:
                response_data = e.response.json() if e.response and e.response.content else None
            except:
                response_data = e.response.text if e.response else None

            if status_code == 401:
                # 토큰 만료 시 재발급 시도
                self.access_token = None
                raise AuthenticationError(
                    f"{operation} 인증 만료",
                    status_code=status_code,
                    response_data=response_data
                ) from e
            else:
                raise APIException(
                    f"{operation} API 오류",
                    status_code=status_code,
                    response_data=response_data
                ) from e
        else:
            raise APIException(f"{operation} 요청 실패: {str(e)}") from e

    @retry_on_error(max_retries=2, delay=1.0, backoff=2.0, exceptions=(TradingConnectionError, TradingTimeoutError))
    @handle_api_errors(raise_on_auth_error=True, log_errors=True)
    def get_access_token(self) -> str:
        """
        접근 토큰(Access Token) 발급

        Returns:
            접근 토큰

        Raises:
            AuthenticationError: 인증 실패 시
            ConnectionError: 연결 실패 시
            TimeoutError: 타임아웃 시
            APIException: API 오류 시
        """
        # 토큰이 유효하면 재사용 (24시간 유효, 5분 여유)
        if self.access_token and self.token_expires_at:
            if time.time() < self.token_expires_at - 300:
                return self.access_token

        url = f"{self.BASE_URL}/oauth2/token"

        data = {
            "grant_type": "client_credentials",
            "appkey": self.api_key,
            "secretkey": self.api_secret
        }

        headers = {
            "Content-Type": "application/json;charset=UTF-8"
        }

        try:
            response = self.session.post(url, json=data, headers=headers, timeout=30)
            response.raise_for_status()

            result = response.json()
            print(f"[DEBUG] API 응답: {result}")

            # 응답 코드 확인
            return_code = result.get("return_code")
            return_msg = result.get("return_msg")

            if return_code != 0:
                raise AuthenticationError(
                    f"토큰 발급 실패: [{return_code}] {return_msg}",
                    response_data=result
                )

            # 토큰 저장
            self.access_token = result.get("token")

            # 만료 시간 파싱 (형식: "20241107083713" -> YYYYMMDDHHMMSS)
            expires_dt = result.get("expires_dt")
            if expires_dt:
                from datetime import datetime
                expire_datetime = datetime.strptime(expires_dt, "%Y%m%d%H%M%S")
                self.token_expires_at = expire_datetime.timestamp()
                expires_in = int(self.token_expires_at - time.time())
            else:
                expires_in = 86400  # 기본 24시간
                self.token_expires_at = time.time() + expires_in

            print(f"✓ 접근 토큰 발급 성공")
            print(f"  - 만료일시: {expires_dt}")
            print(f"  - 남은시간: {expires_in}초 ({expires_in/3600:.1f}시간)")
            print(f"[DEBUG] 저장된 토큰: {self.access_token[:30]}..." if self.access_token else "None")
            return self.access_token

        except requests.exceptions.Timeout as e:
            raise TradingTimeoutError(
                "토큰 발급 요청 타임아웃",
                timeout_seconds=30
            ) from e
        except requests.exceptions.ConnectionError as e:
            raise TradingConnectionError(
                f"토큰 발급 서버 연결 실패: {str(e)}"
            ) from e
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response else None
            response_data = e.response.json() if e.response and e.response.content else None

            if status_code == 401:
                raise AuthenticationError(
                    "API 키 인증 실패",
                    status_code=status_code,
                    response_data=response_data
                ) from e
            else:
                raise APIException(
                    f"토큰 발급 API 오류: {str(e)}",
                    status_code=status_code,
                    response_data=response_data
                ) from e
        except requests.exceptions.RequestException as e:
            raise APIException(f"토큰 발급 요청 실패: {str(e)}") from e

    @retry_on_error(max_retries=2, delay=0.5, backoff=2.0, exceptions=(TradingConnectionError, TradingTimeoutError))
    @handle_api_errors(default_return=None, log_errors=True)
    def get_stock_price(self, stock_code: str) -> Dict[str, Any]:
        """
        주식 현재가 조회 (기본주식정보조회 - ka10001)

        Args:
            stock_code: 종목코드 (6자리)

        Returns:
            주식 정보 (실패 시 None)

        Raises:
            AuthenticationError: 인증 만료 시
            APIException: API 오류 시
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        path = f"/v1/kr/stock/price/{stock_code}"
        url = f"{self.BASE_URL}{path}"

        headers = self._get_headers(method="GET", path=path)

        try:
            response = self.session.get(url, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            # 가격 조회 실패는 정상 동작 (5분봉 데이터 사용)
            # 에러 로그 출력하지 않고 None 반환
            return None

    @retry_on_error(max_retries=2, delay=0.5, backoff=2.0, exceptions=(TradingConnectionError, TradingTimeoutError))
    @handle_api_errors(default_return=None, log_errors=True)
    def get_balance(self) -> Dict[str, Any]:
        """
        계좌 잔고 조회 (예수금 상세 현황)
        API-ID: kt00001

        Returns:
            {
                'entr': 예수금,
                'ord_alow_amt': 주문가능금액,
                'pymn_alow_amt': 출금가능금액,
                ...
            } (실패 시 None)

        Raises:
            ConfigurationError: 계좌번호 미설정 시
            AuthenticationError: 인증 만료 시
            APIException: API 오류 시
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        # 계좌번호 검증
        if not self.account_number:
            raise ConfigurationError(
                "계좌번호가 설정되지 않았습니다.",
                config_key="KIWOOM_ACCOUNT_NUMBER"
            )

        # 올바른 엔드포인트
        endpoint = '/api/dostk/acnt'
        url = f"{self.BASE_URL}{endpoint}"

        # 헤더 구성
        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.access_token}',
            'cont-yn': 'N',  # 연속조회 안함
            'next-key': '',  # 연속조회키 없음
            'api-id': 'kt00001',  # 예수금상세현황
        }

        # 요청 데이터
        data = {
            'qry_tp': '3',  # 조회구분: 3=추정조회
        }

        try:
            response = self.session.post(url, headers=headers, json=data, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            self._handle_request_error(e, "계좌 잔고 조회", timeout=10)

    @handle_api_errors(default_return={'return_code': -1, 'output': []}, log_errors=True)
    def get_account_info(self) -> Dict[str, Any]:
        """
        계좌 보유 종목 조회 (일별잔고수익률)
        API-ID: ka01690

        Returns:
            {
                'tot_buy_amt': 총 매수금액,
                'tot_evlt_amt': 총 평가금액,
                'tot_evltv_prft': 총 평가손익,
                'day_bal_rt': [
                    {
                        'stk_cd': 종목코드,
                        'stk_nm': 종목명,
                        'rmnd_qty': 보유수량,
                        'cur_prc': 현재가,
                        'buy_uv': 매입단가,
                        'evltv_prft': 평가손익,
                        'prft_rt': 수익률,
                        ...
                    }
                ]
            }
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        # 계좌번호 검증
        if not self.account_number:
            raise ValueError("계좌번호가 설정되지 않았습니다.")

        # 올바른 엔드포인트
        endpoint = '/api/dostk/acnt'
        url = f"{self.BASE_URL}{endpoint}"

        # 헤더 구성
        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.access_token}',
            'cont-yn': 'N',
            'next-key': '',
            'api-id': 'ka01690',  # 일별잔고수익률
        }

        # 요청 데이터 (오늘 날짜)
        from datetime import datetime
        today = datetime.now().strftime('%Y%m%d')
        data = {
            'qry_dt': today,
        }

        try:
            response = self.session.post(url, headers=headers, json=data)
            response.raise_for_status()

            return response.json()

        except requests.exceptions.RequestException as e:
            print(f"✗ 계좌 정보 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'data': []}, log_errors=True)
    def get_daily_chart(self, stock_code: str, base_dt: str = None,
                       upd_stkpc_tp: str = "1", cont_yn: str = "N",
                       next_key: str = "") -> Dict[str, Any]:
        """
        주식 일봉 차트 조회

        Args:
            stock_code: 종목코드 (예: 005930)
            base_dt: 기준일자 YYYYMMDD (없으면 오늘)
            upd_stkpc_tp: 수정주가구분 (0 or 1)
            cont_yn: 연속조회여부 (N/Y)
            next_key: 연속조회키

        Returns:
            일봉 데이터
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        # 기준일자가 없으면 오늘 날짜 사용
        if not base_dt:
            from datetime import datetime
            base_dt = datetime.now().strftime("%Y%m%d")

        url = f"{self.BASE_URL}/api/dostk/chart"

        data = {
            "stk_cd": stock_code,
            "base_dt": base_dt,
            "upd_stkpc_tp": upd_stkpc_tp
        }

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "cont-yn": cont_yn,
            "next-key": next_key,
            "api-id": "ka10081"
        }

        try:
            response = self.session.post(url, json=data, headers=headers)
            response.raise_for_status()

            result = response.json()

            # 응답 헤더에서 연속조회 정보 추출
            next_key = response.headers.get('next-key', '')
            cont_yn = response.headers.get('cont-yn', 'N')

            return {
                **result,
                'next_key': next_key,
                'cont_yn': cont_yn
            }

        except requests.exceptions.RequestException as e:
            print(f"✗ 일봉 차트 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'data': []}, log_errors=True)
    def get_minute_chart(self, stock_code: str, tic_scope: str = "1",
                        upd_stkpc_tp: str = "1", cont_yn: str = "N",
                        next_key: str = "") -> Dict[str, Any]:
        """
        주식 분봉 차트 조회

        Args:
            stock_code: 종목코드 (예: 005930)
            tic_scope: 틱범위 (1:1분, 3:3분, 5:5분, 10:10분, 15:15분, 30:30분, 45:45분, 60:60분)
            upd_stkpc_tp: 수정주가구분 (0 or 1)
            cont_yn: 연속조회여부 (N/Y)
            next_key: 연속조회키

        Returns:
            분봉 데이터
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        url = f"{self.BASE_URL}/api/dostk/chart"

        data = {
            "stk_cd": stock_code,
            "tic_scope": tic_scope,
            "upd_stkpc_tp": upd_stkpc_tp
        }

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "cont-yn": cont_yn,
            "next-key": next_key,
            "api-id": "ka10080"
        }

        try:
            response = self.session.post(url, json=data, headers=headers)
            response.raise_for_status()

            result = response.json()

            # 응답 헤더에서 연속조회 정보 추출
            next_key = response.headers.get('next-key', '')
            cont_yn = response.headers.get('cont-yn', 'N')

            return {
                **result,
                'next_key': next_key,
                'cont_yn': cont_yn
            }

        except requests.exceptions.RequestException as e:
            print(f"✗ 분봉 차트 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'output': []}, log_errors=True)
    def get_foreign_investor_trend(self, stock_code: str, cont_yn: str = "N",
                                   next_key: str = "") -> Dict[str, Any]:
        """
        외국인 종목별 매매 동향 조회

        Args:
            stock_code: 종목코드 (예: 005930)
            cont_yn: 연속조회여부 (N/Y)
            next_key: 연속조회키

        Returns:
            외국인 매매 동향 데이터
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        url = f"{self.BASE_URL}/api/dostk/frgnistt"

        data = {
            "stk_cd": stock_code
        }

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "cont-yn": cont_yn,
            "next-key": next_key,
            "api-id": "ka10008"
        }

        try:
            response = self.session.post(url, json=data, headers=headers)
            response.raise_for_status()

            result = response.json()

            # 응답 헤더에서 연속조회 정보 추출
            next_key = response.headers.get('next-key', '')
            cont_yn = response.headers.get('cont-yn', 'N')

            return {
                **result,
                'next_key': next_key,
                'cont_yn': cont_yn
            }

        except requests.exceptions.RequestException as e:
            print(f"✗ 외국인 매매 동향 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'output': []}, log_errors=True)
    def get_investor_trend(self, stock_code: str, dt: str = None,
                          amt_qty_tp: str = "1", trde_tp: str = "0",
                          unit_tp: str = "1000", cont_yn: str = "N",
                          next_key: str = "") -> Dict[str, Any]:
        """
        종목별 투자자 기관별 매매 동향 조회 (ka10059)

        Args:
            stock_code: 종목코드 (예: 005930)
            dt: 일자 YYYYMMDD (없으면 오늘)
            amt_qty_tp: 금액수량구분 (1:금액, 2:수량)
            trde_tp: 매매구분 (0:순매수, 1:매수, 2:매도)
            unit_tp: 단위구분 (1000:천주, 1:단주)
            cont_yn: 연속조회여부 (N/Y)
            next_key: 연속조회키

        Returns:
            투자자별 매매 동향 데이터
            - stk_invsr_orgn: LIST
              - ind_invsr: 개인
              - frgnr_invsr: 외국인
              - orgn: 기관계
              - fnnc_invt: 금융투자
              - insrnc: 보험
              - invtrt: 투신
              - etc_fnnc: 기타금융
              - bank: 은행
              - penfnd_etc: 연기금등
              - samo_fund: 사모펀드
              - natn: 국가,지자체
              - etc_corp: 기타법인
              - natfor: 내외국인
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        # 일자가 없으면 오늘 날짜 사용
        if not dt:
            from datetime import datetime
            dt = datetime.now().strftime("%Y%m%d")

        url = f"{self.BASE_URL}/api/dostk/stkinfo"  # ← 수정!

        data = {
            "dt": dt,           # ← 순서 변경 (dt가 먼저)
            "stk_cd": stock_code,
            "amt_qty_tp": amt_qty_tp,
            "trde_tp": trde_tp,
            "unit_tp": unit_tp
        }

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "cont-yn": cont_yn,
            "next-key": next_key,
            "api-id": "ka10059"
        }

        try:
            response = self.session.post(url, json=data, headers=headers)
            response.raise_for_status()

            result = response.json()

            # 응답 헤더에서 연속조회 정보 추출
            next_key = response.headers.get('next-key', '')
            cont_yn = response.headers.get('cont-yn', 'N')

            return {
                **result,
                'next_key': next_key,
                'cont_yn': cont_yn
            }

        except requests.exceptions.RequestException as e:
            print(f"✗ 투자자별 매매 동향 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'output': []}, log_errors=True)
    def get_program_trading(self, dt: str = None, mrkt_tp: str = "P00101",
                           stex_tp: str = "1", cont_yn: str = "N",
                           next_key: str = "") -> Dict[str, Any]:
        """
        종목별 프로그램 매매 현황 조회

        Args:
            dt: 일자 YYYYMMDD (없으면 오늘)
            mrkt_tp: 시장구분 (P00101:코스피, P10102:코스닥)
            stex_tp: 거래소구분 (1:KRX, 2:NXT, 3:통합)
            cont_yn: 연속조회여부 (N/Y)
            next_key: 연속조회키

        Returns:
            프로그램 매매 현황 데이터
            - stk_prm_trde_prst: 종목별 프로그램 매매 현황 리스트
              - buy_cntr_qty: 매수체결수량
              - buy_cntr_amt: 매수체결금액
              - sel_cntr_qty: 매도체결수량
              - sel_cntr_amt: 매도체결금액
              - netprps_prica: 순매수금액
              - all_trde_rt: 전체거래비율
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        # 일자가 없으면 오늘 날짜 사용
        if not dt:
            from datetime import datetime
            dt = datetime.now().strftime("%Y%m%d")

        url = f"{self.BASE_URL}/api/dostk/stkinfo"

        data = {
            "dt": dt,
            "mrkt_tp": mrkt_tp,
            "stex_tp": stex_tp
        }

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "cont-yn": cont_yn,
            "next-key": next_key,
            "api-id": "ka90004"
        }

        try:
            response = self.session.post(url, json=data, headers=headers)
            response.raise_for_status()

            result = response.json()

            # 응답 헤더에서 연속조회 정보 추출
            next_key = response.headers.get('next-key', '')
            cont_yn = response.headers.get('cont-yn', 'N')

            return {
                **result,
                'next_key': next_key,
                'cont_yn': cont_yn
            }

        except requests.exceptions.RequestException as e:
            print(f"✗ 프로그램 매매 현황 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'output': {}}, log_errors=True)
    def get_stock_info(self, stock_code: str, cont_yn: str = "N",
                      next_key: str = "") -> Dict[str, Any]:
        """
        주식 기본정보 조회

        Args:
            stock_code: 종목코드 (예: 005930)
            cont_yn: 연속조회여부 (N/Y)
            next_key: 연속조회키

        Returns:
            주식 기본정보
            - stk_cd: 종목코드
            - stk_nm: 종목명
            - cur_prc: 현재가
            - per: PER
            - eps: EPS
            - roe: ROE
            - pbr: PBR
            - bps: BPS
            - cap: 시가총액
            - flo_stk: 유통주식수
            - crd_rt: 신용비율
            - oyr_hgst: 연중최고가
            - oyr_lwst: 연중최저가
            - for_exh_rt: 외국인보유비율
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        url = f"{self.BASE_URL}/api/dostk/stkinfo"

        data = {
            "stk_cd": stock_code
        }

        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "authorization": f"Bearer {self.access_token}",
            "cont-yn": cont_yn,
            "next-key": next_key,
            "api-id": "ka10001"
        }

        try:
            response = self.session.post(url, json=data, headers=headers)
            response.raise_for_status()

            result = response.json()

            # 응답 헤더에서 연속조회 정보 추출
            next_key = response.headers.get('next-key', '')
            cont_yn = response.headers.get('cont-yn', 'N')

            return {
                **result,
                'next_key': next_key,
                'cont_yn': cont_yn
            }

        except requests.exceptions.RequestException as e:
            print(f"✗ 주식 기본정보 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @retry_on_error(max_retries=1, delay=1.0, exceptions=(TradingConnectionError, TradingTimeoutError))
    @handle_trading_errors(notify_user=True, log_errors=True)
    @handle_api_errors(raise_on_auth_error=True, log_errors=True)
    def order_buy(self, stock_code: str, quantity: int, price: int = 0,
                  trade_type: str = "0", dmst_stex_tp: str = "KRX") -> Dict[str, Any]:
        """
        주식 매수 주문 (kt10000)

        Args:
            stock_code: 종목코드 (예: 005930)
            quantity: 주문수량
            price: 주문단가 (0이면 시장가)
            trade_type: 매매구분
                - 0: 보통, 3: 시장가, 5: 조건부지정가
                - 6: 최유리지정가, 7: 최우선지정가
                - 10: 보통(IOC), 13: 시장가(IOC), 16: 최유리(IOC)
                - 20: 보통(FOK), 23: 시장가(FOK), 26: 최유리(FOK)
            dmst_stex_tp: 국내거래소구분 (KRX, NXT, SOR)

        Returns:
            주문 결과
            - ord_no: 주문번호
            - return_code: 응답코드 (0: 정상)
            - return_msg: 응답메시지

        Raises:
            InsufficientFundsError: 잔고 부족 시
            OrderFailedError: 주문 실패 시
            AuthenticationError: 인증 만료 시
            APIException: API 오류 시
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        url = f"{self.BASE_URL}/api/dostk/ordr"

        # 시장가 주문이면 가격 빈 문자열
        ord_uv = str(price) if price > 0 else ""

        data = {
            "dmst_stex_tp": dmst_stex_tp,
            "stk_cd": stock_code,
            "ord_qty": str(quantity),
            "ord_uv": ord_uv,
            "trde_tp": trade_type,
            "cond_uv": ""
        }

        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.access_token}',
            'cont-yn': 'N',
            'next-key': '',
            'api-id': 'kt10000',  # 매수 주문
        }

        try:
            print(f"\n[매수 주문 API 요청]")
            print(f"  종목: {stock_code}, 수량: {quantity}, 가격: {price if price > 0 else '시장가'}")

            response = self.session.post(url, headers=headers, json=data, timeout=15)
            response.raise_for_status()

            result = response.json()

            print(f"[DEBUG] 매수 주문 응답: {result}")

            return_code = result.get('return_code')
            return_msg = result.get('return_msg', '')
            ord_no = result.get('ord_no')

            if return_code == 0:
                print(f"✓ 매수 주문 성공 - 주문번호: {ord_no}")
                return result
            else:
                # 잔고 부족 에러 체크
                if '잔고' in return_msg or '예수금' in return_msg or 'insufficient' in return_msg.lower():
                    raise InsufficientFundsError(
                        required_amount=price * quantity if price > 0 else 0,
                        available_amount=0,  # API 응답에서 파싱 필요
                        stock_code=stock_code,
                        details={'return_code': return_code, 'return_msg': return_msg}
                    )
                else:
                    raise OrderFailedError(
                        f"매수 주문 실패: {return_msg}",
                        order_id=ord_no,
                        stock_code=stock_code,
                        order_type='buy',
                        details={'return_code': return_code, 'quantity': quantity, 'price': price}
                    )

        except requests.exceptions.RequestException as e:
            self._handle_request_error(e, f"매수 주문({stock_code})", timeout=15)

    @retry_on_error(max_retries=1, delay=1.0, exceptions=(TradingConnectionError, TradingTimeoutError))
    @handle_trading_errors(notify_user=True, log_errors=True)
    @handle_api_errors(raise_on_auth_error=True, log_errors=True)
    def order_sell(self, stock_code: str, quantity: int, price: int = 0,
                   trade_type: str = "0", dmst_stex_tp: str = "KRX") -> Dict[str, Any]:
        """
        주식 매도 주문 (kt10001)

        Args:
            stock_code: 종목코드 (예: 005930)
            quantity: 주문수량
            price: 주문단가 (0이면 시장가)
            trade_type: 매매구분
                - 0: 보통, 3: 시장가, 5: 조건부지정가
                - 6: 최유리지정가, 7: 최우선지정가
                - 10: 보통(IOC), 13: 시장가(IOC), 16: 최유리(IOC)
                - 20: 보통(FOK), 23: 시장가(FOK), 26: 최유리(FOK)
            dmst_stex_tp: 국내거래소구분 (KRX, NXT, SOR)

        Returns:
            주문 결과
            - ord_no: 주문번호
            - dmst_stex_tp: 거래소구분
            - return_code: 응답코드 (0: 정상)
            - return_msg: 응답메시지

        Raises:
            OrderFailedError: 주문 실패 시
            AuthenticationError: 인증 만료 시
            APIException: API 오류 시
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        url = f"{self.BASE_URL}/api/dostk/ordr"

        # 시장가 주문이면 가격 빈 문자열
        ord_uv = str(price) if price > 0 else ""

        data = {
            "dmst_stex_tp": dmst_stex_tp,
            "stk_cd": stock_code,
            "ord_qty": str(quantity),
            "ord_uv": ord_uv,
            "trde_tp": trade_type,
            "cond_uv": ""
        }

        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.access_token}',
            'cont-yn': 'N',
            'next-key': '',
            'api-id': 'kt10001',  # 매도 주문
        }

        try:
            print(f"\n[매도 주문 API 요청]")
            print(f"  종목: {stock_code}, 수량: {quantity}, 가격: {price if price > 0 else '시장가'}")

            response = self.session.post(url, headers=headers, json=data, timeout=15)
            response.raise_for_status()

            result = response.json()

            print(f"[DEBUG] 매도 주문 응답: {result}")

            return_code = result.get('return_code')
            return_msg = result.get('return_msg', '')
            ord_no = result.get('ord_no')

            if return_code == 0:
                print(f"✓ 매도 주문 성공 - 주문번호: {ord_no}")
                return result
            else:
                raise OrderFailedError(
                    f"매도 주문 실패: {return_msg}",
                    order_id=ord_no,
                    stock_code=stock_code,
                    order_type='sell',
                    details={'return_code': return_code, 'quantity': quantity, 'price': price}
                )

        except requests.exceptions.RequestException as e:
            self._handle_request_error(e, f"매도 주문({stock_code})", timeout=15)

    @handle_api_errors(default_return={'return_code': -1, 'order_number': None}, log_errors=True)
    def order_modify(self, orig_ord_no: str, stock_code: str, quantity: int,
                     price: int, dmst_stex_tp: str = "KRX") -> Dict[str, Any]:
        """
        주식 정정 주문 (kt10002)

        Args:
            orig_ord_no: 원주문번호
            stock_code: 종목코드
            quantity: 정정수량
            price: 정정단가
            dmst_stex_tp: 국내거래소구분 (KRX, NXT, SOR)

        Returns:
            주문 결과
            - ord_no: 주문번호
            - base_orig_ord_no: 기준원주문번호
            - mdfy_qty: 정정수량
            - dmst_stex_tp: 거래소구분
            - return_code: 응답코드 (0: 정상)
            - return_msg: 응답메시지
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        url = f"{self.BASE_URL}/api/dostk/ordr"

        data = {
            "dmst_stex_tp": dmst_stex_tp,
            "orig_ord_no": orig_ord_no,
            "stk_cd": stock_code,
            "mdfy_qty": str(quantity),
            "mdfy_uv": str(price),
            "mdfy_cond_uv": ""
        }

        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.access_token}',
            'cont-yn': 'N',
            'next-key': '',
            'api-id': 'kt10002',  # 정정 주문
        }

        try:
            print(f"\n[정정 주문 API 요청]")
            print(f"  원주문번호: {orig_ord_no}, 수량: {quantity}, 가격: {price}")

            response = self.session.post(url, headers=headers, json=data)
            response.raise_for_status()

            result = response.json()

            print(f"[DEBUG] 정정 주문 응답: {result}")

            if result.get('return_code') == 0:
                print(f"✓ 정정 주문 성공 - 주문번호: {result.get('ord_no')}")
            else:
                print(f"✗ 정정 주문 실패: {result.get('return_msg')}")

            return result

        except requests.exceptions.RequestException as e:
            print(f"✗ 정정 주문 API 호출 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @retry_on_error(max_retries=1, delay=1.0, exceptions=(TradingConnectionError, TradingTimeoutError))
    @handle_trading_errors(notify_user=True, log_errors=True)
    @handle_api_errors(raise_on_auth_error=True, log_errors=True)
    def order_cancel(self, orig_ord_no: str, stock_code: str, quantity: int = 0,
                     dmst_stex_tp: str = "KRX") -> Dict[str, Any]:
        """
        주식 취소 주문 (kt10003)

        Args:
            orig_ord_no: 원주문번호
            stock_code: 종목코드
            quantity: 취소수량 (0이면 잔량 전부 취소)
            dmst_stex_tp: 국내거래소구분 (KRX, NXT, SOR)

        Returns:
            주문 결과
            - ord_no: 주문번호
            - base_orig_ord_no: 기준원주문번호
            - cncl_qty: 취소수량
            - return_code: 응답코드 (0: 정상)
            - return_msg: 응답메시지

        Raises:
            OrderFailedError: 취소 실패 시
            AuthenticationError: 인증 만료 시
            APIException: API 오류 시
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        url = f"{self.BASE_URL}/api/dostk/ordr"

        data = {
            "dmst_stex_tp": dmst_stex_tp,
            "orig_ord_no": orig_ord_no,
            "stk_cd": stock_code,
            "cncl_qty": str(quantity)
        }

        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.access_token}',
            'cont-yn': 'N',
            'next-key': '',
            'api-id': 'kt10003',  # 취소 주문
        }

        try:
            print(f"\n[취소 주문 API 요청]")
            print(f"  원주문번호: {orig_ord_no}, 취소수량: {quantity if quantity > 0 else '전체'}")

            response = self.session.post(url, headers=headers, json=data)
            response.raise_for_status()

            result = response.json()

            print(f"[DEBUG] 취소 주문 응답: {result}")

            if result.get('return_code') == 0:
                print(f"✓ 취소 주문 성공 - 주문번호: {result.get('ord_no')}")
            else:
                print(f"✗ 취소 주문 실패: {result.get('return_msg')}")

            return result

        except requests.exceptions.RequestException as e:
            print(f"✗ 취소 주문 API 호출 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'output': []}, log_errors=True)
    def get_unexecuted_orders(self, dmst_stex_tp: str = "KRX", cont_yn: str = "N",
                             next_key: str = "") -> Dict[str, Any]:
        """
        미체결 주문 조회 (ka10075)

        Args:
            dmst_stex_tp: 국내거래소구분 (KRX, NXT, SOR)
            cont_yn: 연속조회여부 (N/Y)
            next_key: 연속조회키

        Returns:
            미체결 주문 내역
            - ord_noexe: 미체결 주문 리스트
              - ord_no: 주문번호
              - orig_ord_no: 원주문번호
              - stk_cd: 종목코드
              - stk_nm: 종목명
              - ord_qty: 주문수량
              - ord_uv: 주문단가
              - cntr_qty: 체결수량
              - noexe_qty: 미체결수량
              - buy_sel_tp_nm: 매수매도구분명
              - ord_dt: 주문일자
              - ord_time: 주문시각
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        url = f"{self.BASE_URL}/api/dostk/acnt"

        data = {
            "dmst_stex_tp": dmst_stex_tp
        }

        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.access_token}',
            'cont-yn': cont_yn,
            'next-key': next_key,
            'api-id': 'ka10075',
        }

        try:
            response = self.session.post(url, headers=headers, json=data)
            response.raise_for_status()

            result = response.json()

            # 응답 헤더에서 연속조회 정보 추출
            next_key = response.headers.get('next-key', '')
            cont_yn = response.headers.get('cont-yn', 'N')

            return {
                **result,
                'next_key': next_key,
                'cont_yn': cont_yn
            }

        except requests.exceptions.RequestException as e:
            print(f"✗ 미체결 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'output': []}, log_errors=True)
    def get_executed_orders(self, qry_dt: str = None, dmst_stex_tp: str = "KRX",
                           cont_yn: str = "N", next_key: str = "") -> Dict[str, Any]:
        """
        체결 주문 조회 (ka10076)

        Args:
            qry_dt: 조회일자 YYYYMMDD (없으면 오늘)
            dmst_stex_tp: 국내거래소구분 (KRX, NXT, SOR)
            cont_yn: 연속조회여부 (N/Y)
            next_key: 연속조회키

        Returns:
            체결 주문 내역
            - ord_cntr: 체결 주문 리스트
              - ord_no: 주문번호
              - orig_ord_no: 원주문번호
              - stk_cd: 종목코드
              - stk_nm: 종목명
              - ord_qty: 주문수량
              - ord_uv: 주문단가
              - cntr_qty: 체결수량
              - cntr_uv: 체결단가
              - buy_sel_tp_nm: 매수매도구분명
              - ord_dt: 주문일자
              - cntr_dt: 체결일자
              - cntr_time: 체결시각
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        # 조회일자가 없으면 오늘 날짜 사용
        if not qry_dt:
            from datetime import datetime
            qry_dt = datetime.now().strftime("%Y%m%d")

        url = f"{self.BASE_URL}/api/dostk/acnt"

        data = {
            "qry_dt": qry_dt,
            "dmst_stex_tp": dmst_stex_tp
        }

        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.access_token}',
            'cont-yn': cont_yn,
            'next-key': next_key,
            'api-id': 'ka10076',
        }

        try:
            response = self.session.post(url, headers=headers, json=data)
            response.raise_for_status()

            result = response.json()

            # 응답 헤더에서 연속조회 정보 추출
            next_key = response.headers.get('next-key', '')
            cont_yn = response.headers.get('cont-yn', 'N')

            return {
                **result,
                'next_key': next_key,
                'cont_yn': cont_yn
            }

        except requests.exceptions.RequestException as e:
            print(f"✗ 체결 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'output': []}, log_errors=True)
    def get_account_evaluation(self, cont_yn: str = "N", next_key: str = "") -> Dict[str, Any]:
        """
        계좌평가현황 조회 (kt00004)

        Args:
            cont_yn: 연속조회여부 (N/Y)
            next_key: 연속조회키

        Returns:
            계좌 평가 현황
            - tot_evlt_amt: 총평가금액
            - tot_buy_amt: 총매수금액
            - tot_evltv_prft: 총평가손익
            - evltv_prft_rt: 평가손익률
            - acnt_evlt_prst: 계좌평가현황 리스트
              - stk_cd: 종목코드
              - stk_nm: 종목명
              - rmnd_qty: 보유수량
              - buy_uv: 매입단가
              - cur_prc: 현재가
              - evlt_amt: 평가금액
              - evltv_prft: 평가손익
              - prft_rt: 수익률
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        url = f"{self.BASE_URL}/api/dostk/acnt"

        data = {}

        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.access_token}',
            'cont-yn': cont_yn,
            'next-key': next_key,
            'api-id': 'kt00004',
        }

        try:
            response = self.session.post(url, headers=headers, json=data)
            response.raise_for_status()

            result = response.json()

            # 응답 헤더에서 연속조회 정보 추출
            next_key = response.headers.get('next-key', '')
            cont_yn = response.headers.get('cont-yn', 'N')

            return {
                **result,
                'next_key': next_key,
                'cont_yn': cont_yn
            }

        except requests.exceptions.RequestException as e:
            print(f"✗ 계좌평가현황 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'output': {}}, log_errors=True)
    def get_stock_quote(self, stock_code: str, cont_yn: str = "N",
                       next_key: str = "") -> Dict[str, Any]:
        """
        주식 호가 조회 (ka10004)

        Args:
            stock_code: 종목코드 (예: 005930)
            cont_yn: 연속조회여부 (N/Y)
            next_key: 연속조회키

        Returns:
            호가 정보
            - stk_cd: 종목코드
            - stk_nm: 종목명
            - cur_prc: 현재가
            - sell_hoga_10: 매도호가10 (상위)
            - sell_hoga_rem_qty_10: 매도호가잔량10
            - sell_hoga_9 ~ sell_hoga_1: 매도호가 9~1
            - buy_hoga_1 ~ buy_hoga_10: 매수호가 1~10
            - buy_hoga_rem_qty_1 ~ buy_hoga_rem_qty_10: 매수호가잔량
            - tot_sell_hoga_rem_qty: 총매도호가잔량
            - tot_buy_hoga_rem_qty: 총매수호가잔량
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        url = f"{self.BASE_URL}/api/dostk/mrkcond"

        data = {
            "stk_cd": stock_code
        }

        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.access_token}',
            'cont-yn': cont_yn,
            'next-key': next_key,
            'api-id': 'ka10004',
        }

        try:
            response = self.session.post(url, headers=headers, json=data)
            response.raise_for_status()

            result = response.json()

            # 응답 헤더에서 연속조회 정보 추출
            next_key = response.headers.get('next-key', '')
            cont_yn = response.headers.get('cont-yn', 'N')

            return {
                **result,
                'next_key': next_key,
                'cont_yn': cont_yn
            }

        except requests.exceptions.RequestException as e:
            print(f"✗ 주식 호가 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'output': []}, log_errors=True)
    def get_execution_info(self, stock_code: str, cont_yn: str = "N",
                          next_key: str = "") -> Dict[str, Any]:
        """
        체결정보 조회 (ka10003)

        Args:
            stock_code: 종목코드 (예: 005930)
            cont_yn: 연속조회여부 (N/Y)
            next_key: 연속조회키

        Returns:
            체결 정보 (체결 틱 데이터)
            - stk_cntr_info: 체결정보 리스트
              - cntr_time: 체결시각
              - cntr_prc: 체결가격
              - chng_prc: 전일대비
              - cntr_qty: 체결수량
              - sell_buy_tp: 매도매수구분 (1:매도, 2:매수, 3:체결)
        """
        # 토큰 확인
        if not self.access_token:
            self.get_access_token()

        url = f"{self.BASE_URL}/api/dostk/stkinfo"

        data = {
            "stk_cd": stock_code
        }

        headers = {
            'Content-Type': 'application/json;charset=UTF-8',
            'authorization': f'Bearer {self.access_token}',
            'cont-yn': cont_yn,
            'next-key': next_key,
            'api-id': 'ka10003',
        }

        try:
            response = self.session.post(url, headers=headers, json=data)
            response.raise_for_status()

            result = response.json()

            # 응답 헤더에서 연속조회 정보 추출
            next_key = response.headers.get('next-key', '')
            cont_yn = response.headers.get('cont-yn', 'N')

            return {
                **result,
                'next_key': next_key,
                'cont_yn': cont_yn
            }

        except requests.exceptions.RequestException as e:
            print(f"✗ 체결정보 조회 실패: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  응답 코드: {e.response.status_code}")
                print(f"  응답 내용: {e.response.text}")
            raise

    @handle_api_errors(default_return={'return_code': -1, 'data': []}, log_errors=True)
    def get_ohlcv_data(self, stock_code: str, period: str = 'D', count: int = 30) -> Dict[str, Any]:
        """
        OHLCV 데이터 조회 (일봉/분봉)

        Args:
            stock_code: 종목코드 (6자리)
            period: 'D' (일봉) or 'M' (분봉)
            count: 조회할 데이터 개수

        Returns:
            OHLCV 데이터 (return_code, data 등)
        """
        if period.upper() == 'D':
            # 일봉 데이터 조회
            result = self.get_daily_chart(stock_code=stock_code)

            # 일봉 데이터 키 정규화 (stk_dt_pole_chart_qry → data)
            if result.get('return_code') == 0:
                # 여러 가능한 키 체크
                for key in ['stk_dt_pole_chart_qry', 'stk_day_pole_chart_qry', 'output', 'output1', 'data']:
                    if key in result and result[key]:
                        chart_data = result[key]
                        # 데이터 개수 제한
                        if len(chart_data) > count:
                            chart_data = chart_data[:count]
                        result['data'] = chart_data
                        break

                # data 키가 없으면 빈 리스트
                if 'data' not in result:
                    result['data'] = []

            return result

        elif period.upper() == 'M':
            # 분봉 데이터 조회 (5분봉)
            result = self.get_minute_chart(stock_code=stock_code, tic_scope="5")

            # 분봉 데이터 키 정규화
            if result.get('return_code') == 0:
                # 여러 가능한 키 체크
                for key in ['stk_min_pole_chart_qry', 'stk_mnut_pole_chart_qry', 'output', 'output1', 'data']:
                    if key in result and result[key]:
                        chart_data = result[key]
                        # 데이터 개수 제한
                        if len(chart_data) > count:
                            chart_data = chart_data[:count]
                        result['data'] = chart_data
                        break

                # data 키가 없으면 빈 리스트
                if 'data' not in result:
                    result['data'] = []

            return result

        else:
            return {
                'return_code': -1,
                'return_msg': f'지원하지 않는 기간: {period}',
                'data': []
            }

    def close(self):
        """세션 종료"""
        self.session.close()

    def __enter__(self):
        """컨텍스트 매니저 진입"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """컨텍스트 매니저 종료"""
        self.close()
