"""
EnvironmentConfig 테스트
"""
import pytest
import os
from unittest.mock import patch
from config.env_config import EnvironmentConfig, get_env, is_production, is_development


class TestEnvironmentConfig:
    """EnvironmentConfig 단위 테스트"""

    def test_create_with_required_fields(self):
        """필수 필드로 인스턴스 생성"""
        # Given
        app_key = "test_app_key"
        app_secret = "test_app_secret"

        # When
        env_config = EnvironmentConfig(
            KIWOOM_APP_KEY=app_key,
            KIWOOM_APP_SECRET=app_secret
        )

        # Then
        assert env_config.KIWOOM_APP_KEY == app_key
        assert env_config.KIWOOM_APP_SECRET == app_secret

    def test_default_values(self):
        """기본값 테스트"""
        # Given
        env_config = EnvironmentConfig(
            KIWOOM_APP_KEY="test_key",
            KIWOOM_APP_SECRET="test_secret"
        )

        # Then
        assert env_config.ENVIRONMENT == 'development'
        assert env_config.DEBUG is False
        assert env_config.PAPER_TRADING is True
        assert env_config.LOG_LEVEL == 'INFO'

    def test_is_production(self):
        """운영 환경 여부 테스트"""
        # Given
        env_config = EnvironmentConfig(
            KIWOOM_APP_KEY="test_key",
            KIWOOM_APP_SECRET="test_secret",
            ENVIRONMENT='production'
        )

        # Then
        assert env_config.is_production() is True
        assert env_config.is_development() is False

    def test_is_development(self):
        """개발 환경 여부 테스트"""
        # Given
        env_config = EnvironmentConfig(
            KIWOOM_APP_KEY="test_key",
            KIWOOM_APP_SECRET="test_secret",
            ENVIRONMENT='development'
        )

        # Then
        assert env_config.is_development() is True
        assert env_config.is_production() is False

    def test_is_paper_trading(self):
        """모의 거래 여부 테스트"""
        # Given
        env_config_paper = EnvironmentConfig(
            KIWOOM_APP_KEY="test_key",
            KIWOOM_APP_SECRET="test_secret",
            PAPER_TRADING=True
        )

        env_config_real = EnvironmentConfig(
            KIWOOM_APP_KEY="test_key",
            KIWOOM_APP_SECRET="test_secret",
            PAPER_TRADING=False
        )

        # Then
        assert env_config_paper.is_paper_trading() is True
        assert env_config_real.is_paper_trading() is False

    def test_telegram_enabled(self):
        """Telegram 활성화 여부 테스트"""
        # Given
        env_config_enabled = EnvironmentConfig(
            KIWOOM_APP_KEY="test_key",
            KIWOOM_APP_SECRET="test_secret",
            TELEGRAM_BOT_TOKEN="test_token",
            TELEGRAM_CHAT_ID="test_chat_id"
        )

        env_config_disabled = EnvironmentConfig(
            KIWOOM_APP_KEY="test_key",
            KIWOOM_APP_SECRET="test_secret"
        )

        # Then
        assert env_config_enabled.telegram_enabled() is True
        assert env_config_disabled.telegram_enabled() is False

    @patch.dict(os.environ, {
        'KIWOOM_APP_KEY': 'env_app_key',
        'KIWOOM_APP_SECRET': 'env_app_secret',
        'ENVIRONMENT': 'production',
        'DEBUG': 'true'
    })
    def test_load_from_environment(self):
        """환경 변수에서 로드 테스트"""
        # When
        env_config = EnvironmentConfig()

        # Then
        assert env_config.KIWOOM_APP_KEY == 'env_app_key'
        assert env_config.KIWOOM_APP_SECRET == 'env_app_secret'
        assert env_config.ENVIRONMENT == 'production'
        assert env_config.DEBUG is True

    def test_custom_values(self):
        """커스텀 값 설정 테스트"""
        # Given
        custom_db_path = '/custom/path/db.sqlite'
        custom_log_level = 'DEBUG'

        # When
        env_config = EnvironmentConfig(
            KIWOOM_APP_KEY="test_key",
            KIWOOM_APP_SECRET="test_secret",
            DATABASE_PATH=custom_db_path,
            LOG_LEVEL=custom_log_level
        )

        # Then
        assert env_config.DATABASE_PATH == custom_db_path
        assert env_config.LOG_LEVEL == custom_log_level


class TestGlobalFunctions:
    """전역 함수 테스트"""

    def test_get_env_function(self):
        """get_env 전역 함수 테스트"""
        # When
        value = get_env('LOG_LEVEL', default='INFO')

        # Then
        assert value is not None

    def test_is_production_function(self):
        """is_production 전역 함수 테스트"""
        # When
        result = is_production()

        # Then
        assert isinstance(result, bool)

    def test_is_development_function(self):
        """is_development 전역 함수 테스트"""
        # When
        result = is_development()

        # Then
        assert isinstance(result, bool)
