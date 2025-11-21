"""
환경 변수 관리 모듈

.env 파일의 환경 변수를 pydantic으로 검증 및 관리
API 키, 비밀번호 등 민감한 정보 관리
"""
from typing import Optional
from pydantic_settings import BaseSettings
from pydantic import Field
from pathlib import Path


class EnvironmentConfig(BaseSettings):
    """환경 변수 설정 (pydantic-settings)"""

    # ==========================================
    # Kiwoom API 인증
    # ==========================================
    KIWOOM_APP_KEY: str = Field(
        ...,
        description="Kiwoom API 앱 키"
    )

    KIWOOM_APP_SECRET: str = Field(
        ...,
        description="Kiwoom API 앱 시크릿"
    )

    KIWOOM_ACCOUNT_NO: Optional[str] = Field(
        default=None,
        description="Kiwoom 계좌번호"
    )

    # ==========================================
    # 데이터베이스
    # ==========================================
    DATABASE_PATH: str = Field(
        default='./database/kiwoom_trading.db',
        description="SQLite 데이터베이스 경로"
    )

    # ==========================================
    # 로깅
    # ==========================================
    LOG_LEVEL: str = Field(
        default='INFO',
        description="로그 레벨 (DEBUG, INFO, WARNING, ERROR)"
    )

    LOG_FILE: str = Field(
        default='./logs/trading.log',
        description="로그 파일 경로"
    )

    # ==========================================
    # WebSocket
    # ==========================================
    WEBSOCKET_URL: str = Field(
        default='wss://api.kiwoom.com:10000/api/dostk/websocket',
        description="Kiwoom WebSocket URL"
    )

    WEBSOCKET_TIMEOUT: int = Field(
        default=30,
        description="WebSocket 연결 타임아웃 (초)"
    )

    # ==========================================
    # Telegram 알림
    # ==========================================
    TELEGRAM_BOT_TOKEN: Optional[str] = Field(
        default=None,
        description="Telegram 봇 토큰"
    )

    TELEGRAM_CHAT_ID: Optional[str] = Field(
        default=None,
        description="Telegram 채팅방 ID"
    )

    # ==========================================
    # 환경 설정
    # ==========================================
    ENVIRONMENT: str = Field(
        default='development',
        description="환경 (development, production)"
    )

    DEBUG: bool = Field(
        default=False,
        description="디버그 모드"
    )

    PAPER_TRADING: bool = Field(
        default=True,
        description="모의 거래 모드 (False=실전 거래)"
    )

    # ==========================================
    # 기타
    # ==========================================
    MAX_WORKERS: int = Field(
        default=4,
        description="최대 워커 스레드 수"
    )

    CACHE_DIR: str = Field(
        default='./cache',
        description="캐시 디렉토리"
    )

    class Config:
        env_file = '.env'
        env_file_encoding = 'utf-8'
        case_sensitive = True
        extra = 'ignore'  # 추가 필드 무시

    def is_production(self) -> bool:
        """운영 환경 여부"""
        return self.ENVIRONMENT.lower() == 'production'

    def is_development(self) -> bool:
        """개발 환경 여부"""
        return self.ENVIRONMENT.lower() == 'development'

    def is_paper_trading(self) -> bool:
        """모의 거래 여부"""
        return self.PAPER_TRADING

    def telegram_enabled(self) -> bool:
        """Telegram 알림 활성화 여부"""
        return self.TELEGRAM_BOT_TOKEN is not None and self.TELEGRAM_CHAT_ID is not None


# 전역 인스턴스
try:
    env = EnvironmentConfig()
except Exception as e:
    print(f"⚠️  환경 변수 로드 실패: {e}")
    print("⚠️  기본값 사용")
    # 최소한의 기본값으로 인스턴스 생성
    env = EnvironmentConfig(
        KIWOOM_APP_KEY="",
        KIWOOM_APP_SECRET=""
    )


# 편의 함수
def get_env(key: str, default: any = None) -> any:
    """
    환경 변수 가져오기

    Args:
        key: 환경 변수 이름
        default: 기본값

    Returns:
        환경 변수 값

    Example:
        >>> from config.env_config import get_env
        >>> app_key = get_env('KIWOOM_APP_KEY')
    """
    return getattr(env, key, default)


def is_production() -> bool:
    """운영 환경 여부"""
    return env.is_production()


def is_development() -> bool:
    """개발 환경 여부"""
    return env.is_development()
