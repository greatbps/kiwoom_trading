#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/kiwoom_rest_client.py

키움 REST API 클라이언트
- OAuth 인증 관리
- 분봉/일봉 차트 데이터 수집
- Rate Limit 처리
- 에러 핸들링 및 재시도 로직
"""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
import aiohttp
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
    before_sleep_log
)

# 로거 설정
logger = logging.getLogger(__name__)


# 키움 API 에러 코드
KIWOOM_ERROR_CODES = {
    1501: "API ID가 Null이거나 값이 없습니다",
    1504: "해당 URI에서는 지원하는 API ID가 아닙니다",
    1505: "해당 API ID는 존재하지 않습니다",
    1511: "필수 입력 값에 값이 존재하지 않습니다",
    1687: "재귀 호출이 발생하여 API 호출을 제한합니다",
    1700: "허용된 요청 개수를 초과하였습니다",
    1901: "시장 코드값이 존재하지 않습니다",
    1902: "종목 정보가 없습니다",
    8001: "App Key와 Secret Key 검증에 실패했습니다",
    8005: "Token이 유효하지 않습니다",
    8010: "Token을 발급받은 IP와 서비스를 요청한 IP가 동일하지 않습니다",
    8030: "투자구분(실전/모의)이 달라서 Appkey를 사용할수가 없습니다",
}


class KiwoomAPIError(Exception):
    """키움 API 에러"""
    def __init__(self, error_code: int, message: str):
        self.error_code = error_code
        self.message = message
        super().__init__(f"[{error_code}] {message}")


class RateLimitError(KiwoomAPIError):
    """Rate Limit 초과 에러"""
    pass


@dataclass
class TokenInfo:
    """OAuth 토큰 정보"""
    access_token: str
    token_type: str = "Bearer"
    expires_in: int = 86400  # 24시간
    fetched_at: float = 0

    def __post_init__(self):
        if self.fetched_at == 0:
            self.fetched_at = time.time()

    @property
    def is_expired(self) -> bool:
        """토큰 만료 여부 (5분 여유)"""
        return time.time() > (self.fetched_at + self.expires_in - 300)

    @property
    def authorization_header(self) -> str:
        """Authorization 헤더 값"""
        return f"{self.token_type} {self.access_token}"


class KiwoomRESTClient:
    """키움 REST API 클라이언트"""

    # API 엔드포인트
    PROD_BASE_URL = "https://api.kiwoom.com"
    MOCK_BASE_URL = "https://mockapi.kiwoom.com"

    # API 경로
    TOKEN_PATH = "/oauth2/token"
    REVOKE_PATH = "/oauth2/revoke"
    CHART_PATH = "/api/dostk/chart"

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        max_requests_per_second: float = 5.0,  # 초당 최대 요청 수
        retry_attempts: int = 3
    ):
        """
        Args:
            app_key: 앱 키
            app_secret: 앱 시크릿
            max_requests_per_second: 초당 최대 요청 수
            retry_attempts: 재시도 횟수
        """
        self.app_key = app_key
        self.app_secret = app_secret
        self.base_url = self.PROD_BASE_URL  # 항상 실전 서버 사용

        # Rate Limiting
        self.max_requests_per_second = max_requests_per_second
        self.min_request_interval = 1.0 / max_requests_per_second
        self.last_request_time = 0

        # 토큰 관리
        self.token_info: Optional[TokenInfo] = None

        # HTTP 세션
        self.session: Optional[aiohttp.ClientSession] = None

        # 통계
        self.stats = {
            'total_requests': 0,
            'failed_requests': 0,
            'rate_limit_errors': 0,
            'token_refreshes': 0
        }

        logger.info("키움 REST API 클라이언트 초기화")

    async def __aenter__(self):
        """비동기 컨텍스트 매니저 진입"""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """비동기 컨텍스트 매니저 종료"""
        await self.close()

    async def initialize(self):
        """클라이언트 초기화"""
        if self.session is None:
            self.session = aiohttp.ClientSession()

        # 토큰 발급
        if self.token_info is None or self.token_info.is_expired:
            await self.get_access_token()

    async def close(self):
        """클라이언트 종료"""
        if self.token_info:
            try:
                await self.revoke_token()
            except Exception as e:
                logger.warning(f"토큰 폐기 실패: {e}")

        if self.session:
            await self.session.close()
            self.session = None

    async def _wait_for_rate_limit(self):
        """Rate Limit 대기"""
        now = time.time()
        elapsed = now - self.last_request_time

        if elapsed < self.min_request_interval:
            wait_time = self.min_request_interval - elapsed
            logger.debug(f"Rate limit 대기: {wait_time:.3f}초")
            await asyncio.sleep(wait_time)

        self.last_request_time = time.time()

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError)),
        before_sleep=before_sleep_log(logger, logging.WARNING)
    )
    async def get_access_token(self) -> TokenInfo:
        """
        OAuth 접근 토큰 발급

        Returns:
            TokenInfo: 토큰 정보
        """
        logger.info("접근 토큰 발급 요청")

        url = f"{self.base_url}{self.TOKEN_PATH}"
        headers = {
            "Content-Type": "application/json"
        }
        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "secretkey": self.app_secret
        }

        async with self.session.post(url, headers=headers, json=body) as response:
            result = await response.json()

            # 디버깅: 응답 로깅
            logger.debug(f"Token API Response Status: {response.status}")
            logger.debug(f"Token API Response Body: {result}")

            if response.status != 200:
                error_msg = result.get('message', result.get('error', '알 수 없는 오류'))
                error_code = result.get('status', response.status)
                logger.error(f"Token API Error: status={response.status}, msg={error_msg}")
                raise KiwoomAPIError(error_code, error_msg)

            # 키움 API 응답 체크
            return_code = result.get('return_code', -1)
            if return_code != 0:
                error_msg = result.get('return_msg', '토큰 발급 실패')
                logger.error(f"Token API Error: return_code={return_code}, msg={error_msg}")
                raise KiwoomAPIError(return_code, error_msg)

            # 토큰 정보 저장
            self.token_info = TokenInfo(
                access_token=result['token'],  # 'access_token' → 'token'
                token_type=result.get('token_type', 'Bearer'),
                expires_in=86400  # 24시간 (expires_dt로 계산 가능)
            )

            self.stats['token_refreshes'] += 1
            logger.info(f"접근 토큰 발급 성공 (만료: {result.get('expires_dt')})")

            return self.token_info

    async def revoke_token(self):
        """접근 토큰 폐기"""
        if not self.token_info:
            return

        logger.info("접근 토큰 폐기 요청")

        url = f"{self.base_url}{self.REVOKE_PATH}"
        headers = {
            "Content-Type": "application/json"
        }
        body = {
            "token": self.token_info.access_token
        }

        try:
            async with self.session.post(url, headers=headers, json=body) as response:
                if response.status == 200:
                    logger.info("접근 토큰 폐기 성공")
                else:
                    result = await response.json()
                    logger.warning(f"접근 토큰 폐기 실패: {result}")
        finally:
            self.token_info = None

    async def _ensure_token(self):
        """토큰 유효성 확인 및 갱신"""
        if self.token_info is None or self.token_info.is_expired:
            await self.get_access_token()

    async def _request(
        self,
        api_id: str,
        path: str,
        body: Dict[str, Any],
        cont_yn: str = "",
        next_key: str = ""
    ) -> Dict[str, Any]:
        """
        API 요청 공통 메서드

        Args:
            api_id: API ID
            path: API 경로
            body: 요청 본문
            cont_yn: 연속조회여부
            next_key: 연속조회키

        Returns:
            응답 데이터
        """
        await self._ensure_token()
        await self._wait_for_rate_limit()

        url = f"{self.base_url}{path}"
        headers = {
            "Content-Type": "application/json;charset=UTF-8",
            "api-id": api_id,
            "authorization": self.token_info.authorization_header
        }

        # 연속조회 헤더
        if cont_yn:
            headers["cont-yn"] = cont_yn
        if next_key:
            headers["next-key"] = next_key

        self.stats['total_requests'] += 1

        try:
            async with self.session.post(url, headers=headers, json=body) as response:
                result = await response.json()

                # 에러 처리
                if response.status != 200:
                    error_code = result.get('error_code', 0)
                    error_msg = result.get('error_message', '알 수 없는 오류')

                    self.stats['failed_requests'] += 1

                    # Rate Limit 에러
                    if error_code in [1687, 1700]:
                        self.stats['rate_limit_errors'] += 1
                        raise RateLimitError(error_code, error_msg)

                    raise KiwoomAPIError(error_code, error_msg)

                return result

        except aiohttp.ClientError as e:
            self.stats['failed_requests'] += 1
            logger.error(f"HTTP 요청 실패: {e}")
            raise

    async def get_minute_chart(
        self,
        stock_code: str,
        minute_interval: int = 1,
        adjusted_price: bool = True,
        cont_yn: str = "",
        next_key: str = ""
    ) -> Dict[str, Any]:
        """
        주식 분봉 차트 조회

        Args:
            stock_code: 종목코드 (예: "039490" 또는 "039490_KRX")
            minute_interval: 분봉 간격 (1, 3, 5, 10, 15, 30, 45, 60)
            adjusted_price: 수정주가 여부 (True: 1, False: 0)
            cont_yn: 연속조회여부
            next_key: 연속조회키

        Returns:
            차트 데이터
        """
        # 종목코드 형식 확인
        if "_" not in stock_code:
            stock_code = f"{stock_code}_KRX"  # 기본적으로 KRX

        # 분봉 간격 검증
        valid_intervals = [1, 3, 5, 10, 15, 30, 45, 60]
        if minute_interval not in valid_intervals:
            raise ValueError(f"잘못된 분봉 간격: {minute_interval}. 유효한 값: {valid_intervals}")

        body = {
            "stk_cd": stock_code,
            "tic_scope": str(minute_interval),
            "upd_stkpc_tp": "1" if adjusted_price else "0"
        }

        logger.debug(f"분봉차트 조회: {stock_code}, {minute_interval}분봉")

        result = await self._request(
            api_id="ka10080",
            path=self.CHART_PATH,
            body=body,
            cont_yn=cont_yn,
            next_key=next_key
        )

        return result

    async def get_all_minute_chart_data(
        self,
        stock_code: str,
        minute_interval: int = 1,
        adjusted_price: bool = True,
        max_pages: int = 100
    ) -> List[Dict[str, Any]]:
        """
        분봉차트 전체 데이터 수집 (연속조회 포함)

        Args:
            stock_code: 종목코드
            minute_interval: 분봉 간격
            adjusted_price: 수정주가 여부
            max_pages: 최대 페이지 수

        Returns:
            전체 차트 데이터 리스트
        """
        all_data = []
        cont_yn = ""
        next_key = ""
        page = 0

        logger.info(f"분봉차트 전체 데이터 수집 시작: {stock_code}, {minute_interval}분봉")

        while page < max_pages:
            try:
                result = await self.get_minute_chart(
                    stock_code=stock_code,
                    minute_interval=minute_interval,
                    adjusted_price=adjusted_price,
                    cont_yn=cont_yn,
                    next_key=next_key
                )

                # 응답 헤더에서 연속조회 정보 추출
                header = result.get('header', {})
                body = result.get('body', {})

                # 데이터 추출
                chart_data = body.get('stk_min_pole_chart_qry', [])
                if chart_data:
                    all_data.extend(chart_data)
                    logger.info(f"페이지 {page + 1}: {len(chart_data)}건 수집 (누적: {len(all_data)}건)")

                # 연속조회 확인
                cont_yn = header.get('cont-yn', 'N')
                if cont_yn != 'Y':
                    logger.info("마지막 페이지 도달")
                    break

                next_key = header.get('next-key', '')
                page += 1

                # Rate Limit 방지를 위한 추가 대기
                await asyncio.sleep(0.5)

            except RateLimitError as e:
                logger.warning(f"Rate Limit 에러 발생: {e}. 30초 대기 후 재시도")
                await asyncio.sleep(30)
                continue

            except KiwoomAPIError as e:
                logger.error(f"API 에러 발생: {e}")
                break

            except Exception as e:
                logger.error(f"예상치 못한 에러: {e}", exc_info=True)
                break

        logger.info(f"분봉차트 수집 완료: 총 {len(all_data)}건")
        return all_data

    async def get_daily_chart(
        self,
        stock_code: str,
        start_date: str = "",
        end_date: str = "",
        adjusted_price: bool = True,
        cont_yn: str = "",
        next_key: str = ""
    ) -> Dict[str, Any]:
        """
        주식 일봉 차트 조회

        Args:
            stock_code: 종목코드 (예: "039490" 또는 "039490_KRX")
            start_date: 시작일자 (YYYYMMDD)
            end_date: 종료일자 (YYYYMMDD)
            adjusted_price: 수정주가 여부 (True: 1, False: 0)
            cont_yn: 연속조회여부
            next_key: 연속조회키

        Returns:
            차트 데이터
        """
        # 종목코드 형식 확인
        if "_" not in stock_code:
            stock_code = f"{stock_code}_KRX"

        body = {
            "stk_cd": stock_code,
            "upd_stkpc_tp": "1" if adjusted_price else "0"
        }

        # 기간 설정
        if start_date:
            body["qry_start_dt"] = start_date
        if end_date:
            body["qry_end_dt"] = end_date

        logger.debug(f"일봉차트 조회: {stock_code}, {start_date} ~ {end_date}")

        result = await self._request(
            api_id="ka10081",  # 일봉차트 API ID
            path=self.CHART_PATH,
            body=body,
            cont_yn=cont_yn,
            next_key=next_key
        )

        return result

    async def get_historical_data_for_backtest(
        self,
        stock_code: str,
        start_date: datetime,
        end_date: datetime,
        interval: str = 'D'
    ) -> Optional[List[Dict[str, Any]]]:
        """
        백테스트용 과거 데이터 조회

        Args:
            stock_code: 종목코드
            start_date: 시작일
            end_date: 종료일
            interval: 간격 ('D': 일봉, '5': 5분봉, '60': 60분봉)

        Returns:
            차트 데이터 리스트 (시간순 정렬)
        """
        try:
            start_str = start_date.strftime('%Y%m%d')
            end_str = end_date.strftime('%Y%m%d')

            if interval == 'D':
                # 일봉 데이터
                logger.info(f"백테스트 데이터 수집: {stock_code} 일봉 {start_str}~{end_str}")
                result = await self.get_daily_chart(
                    stock_code=stock_code,
                    start_date=start_str,
                    end_date=end_str,
                    adjusted_price=True
                )

                # 데이터 추출
                body = result.get('body', {})
                chart_data = body.get('stk_day_pole_chart_qry', [])

            else:
                # 분봉 데이터
                minute_interval = int(interval)
                logger.info(f"백테스트 데이터 수집: {stock_code} {minute_interval}분봉 {start_str}~{end_str}")

                # 분봉은 연속조회로 수집
                chart_data = await self.get_all_minute_chart_data(
                    stock_code=stock_code,
                    minute_interval=minute_interval,
                    adjusted_price=True,
                    max_pages=50
                )

                # 기간 필터링
                chart_data = [
                    d for d in chart_data
                    if start_str <= d.get('chart_dt', '')[:8] <= end_str
                ]

            if not chart_data:
                logger.warning(f"데이터 없음: {stock_code} {start_str}~{end_str}")
                return None

            # 시간순 정렬 (오래된 것부터)
            chart_data.sort(key=lambda x: x.get('chart_dt', ''))

            logger.info(f"수집 완료: {len(chart_data)}건")
            return chart_data

        except Exception as e:
            logger.error(f"백테스트 데이터 수집 실패: {stock_code}, {e}", exc_info=True)
            return None

    def get_stats(self) -> Dict[str, Any]:
        """통계 정보 조회"""
        return {
            **self.stats,
            'success_rate': (
                (self.stats['total_requests'] - self.stats['failed_requests']) /
                self.stats['total_requests'] * 100
                if self.stats['total_requests'] > 0 else 0
            )
        }


async def main():
    """테스트 코드"""
    import os
    from dotenv import load_dotenv

    # .env 파일 로드
    load_dotenv()

    app_key = os.getenv('KIWOOM_APP_KEY')
    app_secret = os.getenv('KIWOOM_APP_SECRET')

    if not app_key or not app_secret:
        print("환경변수에 KIWOOM_APP_KEY, KIWOOM_APP_SECRET을 설정해주세요")
        return

    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    async with KiwoomRESTClient(app_key, app_secret, is_mock=True) as client:
        # 삼성전자 5분봉 데이터 수집
        data = await client.get_all_minute_chart_data(
            stock_code="005930",
            minute_interval=5,
            max_pages=3
        )

        print(f"\n수집된 데이터: {len(data)}건")
        if data:
            print(f"첫 번째 데이터: {data[0]}")

        # 통계 출력
        stats = client.get_stats()
        print(f"\n통계:")
        for key, value in stats.items():
            print(f"  {key}: {value}")


if __name__ == "__main__":
    asyncio.run(main())
