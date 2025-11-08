#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
core/auth_manager.py

KIS API Access Token Manager
- 토큰 자동 갱신 및 캐싱
- 만료 전 자동 리프레시
- 멀티 계좌 지원
- 실패 시 자동 재시도
- 토큰 상태 모니터링
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional, Tuple, Any
from dataclasses import dataclass, asdict
from threading import Lock
import aiohttp

# Local imports
import sys
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from utils.retry import retry, RetryStrategy


@dataclass
class TokenInfo:
    """토큰 정보 데이터 클래스"""
    access_token: str
    token_type: str
    expires_in: int  # 초 단위
    fetched_at: float  # Unix timestamp
    access_token_token_expired: str  # "YYYY-MM-DD HH:MM:SS" 형식

    @property
    def is_expired(self) -> bool:
        """토큰 만료 여부 확인"""
        # 현재 시간
        now = time.time()

        # 만료 시간 (5분 여유 두기)
        expiry_time = self.fetched_at + self.expires_in - 300

        return now >= expiry_time

    @property
    def time_until_expiry(self) -> float:
        """만료까지 남은 시간 (초)"""
        now = time.time()
        expiry_time = self.fetched_at + self.expires_in
        return max(0, expiry_time - now)

    @property
    def expiry_percentage(self) -> float:
        """토큰 유효 시간 중 경과 비율 (0.0 ~ 1.0)"""
        now = time.time()
        elapsed = now - self.fetched_at
        return min(1.0, elapsed / self.expires_in)

    def to_dict(self) -> dict:
        """딕셔너리로 변환"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> 'TokenInfo':
        """딕셔너리에서 생성"""
        return cls(**data)


class AuthenticationError(Exception):
    """인증 관련 에러"""
    pass


class TokenRefreshError(Exception):
    """토큰 갱신 에러"""
    pass


class AuthManager:
    """
    KIS API Access Token Manager

    주요 기능:
    - 토큰 자동 발급 및 갱신
    - 토큰 캐싱 (파일 기반)
    - 만료 전 자동 리프레시
    - 멀티 계좌 지원
    - 스레드 세이프

    Example:
        auth_manager = AuthManager(
            app_key="YOUR_APP_KEY",
            app_secret="YOUR_APP_SECRET",
            account_no="YOUR_ACCOUNT"
        )

        # 토큰 발급
        await auth_manager.ensure_valid_token()

        # 토큰 사용
        token = auth_manager.get_access_token()
    """

    # KIS API 엔드포인트
    BASE_URL_PROD = "https://openapi.koreainvestment.com:9443"
    BASE_URL_MOCK = "https://openapivts.koreainvestment.com:29443"
    TOKEN_ENDPOINT = "/oauth2/tokenP"

    # 토큰 갱신 임계값 (기본: 만료 5분 전)
    REFRESH_THRESHOLD_SECONDS = 300

    def __init__(
        self,
        app_key: str,
        app_secret: str,
        account_no: str,
        is_mock: bool = False,
        cache_dir: Optional[Path] = None,
        auto_refresh: bool = True,
        logger: Optional[logging.Logger] = None,
    ):
        """
        Args:
            app_key: KIS API App Key
            app_secret: KIS API App Secret
            account_no: 계좌번호
            is_mock: 모의투자 여부
            cache_dir: 토큰 캐시 디렉토리 (기본: 프로젝트 루트)
            auto_refresh: 자동 갱신 활성화 여부
            logger: 로거 (없으면 자동 생성)
        """
        self.app_key = app_key
        self.app_secret = app_secret
        self.account_no = account_no
        self.is_mock = is_mock
        self.auto_refresh = auto_refresh

        # 로거 설정
        self.logger = logger or logging.getLogger(self.__class__.__name__)

        # 토큰 캐시 경로
        if cache_dir is None:
            cache_dir = PROJECT_ROOT.parent
        self.cache_file = cache_dir / ".kis_token_cache.json"

        # 토큰 정보
        self._token_info: Optional[TokenInfo] = None
        self._lock = Lock()

        # 자동 갱신 태스크
        self._refresh_task: Optional[asyncio.Task] = None
        self._session: Optional[aiohttp.ClientSession] = None

        # 메트릭
        self.metrics = {
            'token_fetches': 0,
            'token_refreshes': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'failures': 0,
            'last_fetch_time': None,
            'last_refresh_time': None,
            'last_error': None,
        }

    @property
    def base_url(self) -> str:
        """API Base URL"""
        return self.BASE_URL_MOCK if self.is_mock else self.BASE_URL_PROD

    def get_access_token(self) -> Optional[str]:
        """
        Access Token 반환 (동기)

        Returns:
            Access Token (없으면 None)
        """
        with self._lock:
            if self._token_info and not self._token_info.is_expired:
                return self._token_info.access_token
            return None

    def get_token_info(self) -> Optional[TokenInfo]:
        """토큰 정보 반환"""
        with self._lock:
            return self._token_info

    async def _get_or_create_session(self) -> aiohttp.ClientSession:
        """HTTP 세션 생성 또는 반환"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def close(self):
        """리소스 정리"""
        # 자동 갱신 태스크 종료
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

        # HTTP 세션 종료
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def _load_from_cache(self) -> bool:
        """
        캐시에서 토큰 로드

        Returns:
            로드 성공 여부
        """
        try:
            if not self.cache_file.exists():
                self.logger.debug(f"Token cache file not found: {self.cache_file}")
                self.metrics['cache_misses'] += 1
                return False

            with open(self.cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)

            # TokenInfo 생성
            token_info = TokenInfo.from_dict(data)

            # 만료 확인
            if token_info.is_expired:
                self.logger.info("Cached token is expired")
                self.metrics['cache_misses'] += 1
                return False

            with self._lock:
                self._token_info = token_info

            self.logger.info(
                f"✅ Token loaded from cache. "
                f"Expires in {token_info.time_until_expiry / 60:.1f} minutes"
            )
            self.metrics['cache_hits'] += 1
            return True

        except Exception as e:
            self.logger.warning(f"Failed to load token from cache: {e}")
            self.metrics['cache_misses'] += 1
            return False

    def _save_to_cache(self, token_info: TokenInfo):
        """
        토큰을 캐시에 저장

        Args:
            token_info: 토큰 정보
        """
        try:
            # 디렉토리 생성
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)

            # 파일 저장
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(token_info.to_dict(), f, indent=2)

            self.logger.debug(f"Token cached to {self.cache_file}")

        except Exception as e:
            self.logger.warning(f"Failed to cache token: {e}")

    @retry(
        max_attempts=3,
        initial_delay=1.0,
        max_delay=10.0,
        strategy=RetryStrategy.EXPONENTIAL,
        retryable_exceptions=(
            aiohttp.ClientConnectionError,
            aiohttp.ServerTimeoutError,
            asyncio.TimeoutError,
        ),
    )
    async def _fetch_new_token(self) -> TokenInfo:
        """
        새 토큰 발급 (API 호출)

        Returns:
            TokenInfo

        Raises:
            AuthenticationError: 인증 실패
        """
        url = f"{self.base_url}{self.TOKEN_ENDPOINT}"

        headers = {
            "content-type": "application/json; charset=utf-8",
        }

        body = {
            "grant_type": "client_credentials",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
        }

        session = await self._get_or_create_session()

        try:
            async with session.post(url, headers=headers, json=body) as response:
                data = await response.json()

                # 에러 확인
                if response.status != 200:
                    error_msg = data.get('msg1', 'Unknown error')
                    raise AuthenticationError(
                        f"Token fetch failed (HTTP {response.status}): {error_msg}"
                    )

                # 응답 파싱
                access_token = data.get('access_token')
                token_type = data.get('token_type', 'Bearer')
                expires_in = data.get('expires_in', 86400)  # 기본 24시간
                expired_str = data.get('access_token_token_expired', '')

                if not access_token:
                    raise AuthenticationError("No access_token in response")

                # TokenInfo 생성
                token_info = TokenInfo(
                    access_token=access_token,
                    token_type=token_type,
                    expires_in=expires_in,
                    fetched_at=time.time(),
                    access_token_token_expired=expired_str,
                )

                self.logger.info(
                    f"✅ New token fetched. Expires at: {expired_str} "
                    f"(in {expires_in / 3600:.1f} hours)"
                )

                # 메트릭 업데이트
                self.metrics['token_fetches'] += 1
                self.metrics['last_fetch_time'] = datetime.now().isoformat()

                return token_info

        except aiohttp.ClientError as e:
            self.logger.error(f"Network error fetching token: {e}")
            self.metrics['failures'] += 1
            self.metrics['last_error'] = str(e)
            raise

        except Exception as e:
            self.logger.error(f"Unexpected error fetching token: {e}")
            self.metrics['failures'] += 1
            self.metrics['last_error'] = str(e)
            raise AuthenticationError(f"Token fetch failed: {e}")

    async def ensure_valid_token(self, force_refresh: bool = False) -> str:
        """
        유효한 토큰 보장 (자동 갱신)

        Args:
            force_refresh: 강제 갱신 여부

        Returns:
            Access Token

        Raises:
            AuthenticationError: 토큰 발급 실패
        """
        with self._lock:
            # 1. 강제 갱신
            if force_refresh:
                self.logger.info("Force refresh requested")
                self._token_info = None

            # 2. 기존 토큰이 유효한 경우
            if self._token_info and not self._token_info.is_expired:
                return self._token_info.access_token

            # 3. 캐시에서 로드 시도
            if not force_refresh and self._load_from_cache():
                if self._token_info:
                    return self._token_info.access_token

        # 4. 새 토큰 발급
        self.logger.info("Fetching new token from API...")
        token_info = await self._fetch_new_token()

        with self._lock:
            self._token_info = token_info

        # 캐시 저장
        self._save_to_cache(token_info)

        # 자동 갱신 시작
        if self.auto_refresh and self._refresh_task is None:
            self._refresh_task = asyncio.create_task(self._auto_refresh_loop())

        return token_info.access_token

    async def refresh_token(self) -> str:
        """
        토큰 강제 갱신

        Returns:
            새 Access Token
        """
        self.logger.info("Manually refreshing token...")
        token = await self.ensure_valid_token(force_refresh=True)
        self.metrics['token_refreshes'] += 1
        self.metrics['last_refresh_time'] = datetime.now().isoformat()
        return token

    async def _auto_refresh_loop(self):
        """
        백그라운드 자동 갱신 루프

        만료 5분 전에 자동으로 토큰을 갱신합니다.
        """
        self.logger.info("Auto-refresh loop started")

        try:
            while True:
                # 토큰 정보 확인
                token_info = self.get_token_info()

                if token_info:
                    time_until_expiry = token_info.time_until_expiry

                    # 갱신 임계값 도달 시 갱신
                    if time_until_expiry <= self.REFRESH_THRESHOLD_SECONDS:
                        self.logger.info(
                            f"Token expiring in {time_until_expiry / 60:.1f} minutes. "
                            "Refreshing..."
                        )
                        await self.refresh_token()

                    else:
                        # 다음 체크까지 대기 (만료 10분 전에 체크)
                        check_interval = max(
                            60,  # 최소 1분
                            time_until_expiry - self.REFRESH_THRESHOLD_SECONDS - 600
                        )
                        self.logger.debug(
                            f"Token valid for {time_until_expiry / 60:.1f} minutes. "
                            f"Next check in {check_interval / 60:.1f} minutes"
                        )
                        await asyncio.sleep(check_interval)

                else:
                    # 토큰 없음 - 1분 후 재시도
                    self.logger.warning("No token available. Retrying in 1 minute...")
                    await asyncio.sleep(60)

        except asyncio.CancelledError:
            self.logger.info("Auto-refresh loop cancelled")
        except Exception as e:
            self.logger.error(f"Error in auto-refresh loop: {e}")
            self.metrics['failures'] += 1
            self.metrics['last_error'] = str(e)

    def get_metrics(self) -> dict:
        """메트릭 반환"""
        with self._lock:
            token_status = {
                'has_token': self._token_info is not None,
                'is_expired': self._token_info.is_expired if self._token_info else None,
                'time_until_expiry': (
                    self._token_info.time_until_expiry if self._token_info else None
                ),
                'expiry_percentage': (
                    self._token_info.expiry_percentage if self._token_info else None
                ),
            }

        return {
            **self.metrics,
            'token_status': token_status,
        }

    async def __aenter__(self):
        """Context manager 진입"""
        await self.ensure_valid_token()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager 종료"""
        await self.close()


# 편의 함수
async def create_auth_manager(
    app_key: str,
    app_secret: str,
    account_no: str,
    is_mock: bool = False,
    **kwargs
) -> AuthManager:
    """
    AuthManager 생성 및 초기화

    Example:
        auth = await create_auth_manager(
            app_key="YOUR_KEY",
            app_secret="YOUR_SECRET",
            account_no="12345678-01"
        )
    """
    manager = AuthManager(
        app_key=app_key,
        app_secret=app_secret,
        account_no=account_no,
        is_mock=is_mock,
        **kwargs
    )

    # 토큰 발급
    await manager.ensure_valid_token()

    return manager
