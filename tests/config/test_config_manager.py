"""
ConfigManager 테스트
"""
import pytest
import tempfile
import yaml
from pathlib import Path
from config.config_manager import ConfigManager, get_config, get_section


class TestConfigManager:
    """ConfigManager 단위 테스트"""

    @pytest.fixture
    def temp_config_file(self):
        """임시 설정 파일 생성"""
        config_data = {
            'trading': {
                'vwap_validation': {
                    'lookback_days': 10,
                    'min_trades': 6
                },
                'risk_management': {
                    'max_positions': 5
                }
            },
            'data': {
                'fetching': {
                    'default_days': 7
                }
            },
            'environments': {
                'development': {
                    'debug': True,
                    'max_positions': 2
                },
                'production': {
                    'debug': False,
                    'max_positions': 10
                }
            }
        }

        # 임시 파일 생성
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            yaml.dump(config_data, f)
            temp_file = f.name

        yield temp_file

        # 정리
        Path(temp_file).unlink()

    def test_singleton_pattern(self):
        """Singleton 패턴 테스트"""
        # Given
        config1 = ConfigManager()
        config2 = ConfigManager()

        # Then
        assert config1 is config2

    def test_load_config_file(self, temp_config_file):
        """설정 파일 로드 테스트"""
        # Given
        config = ConfigManager()
        config._config = None  # Reset

        # When
        result = config.load(temp_config_file)

        # Then
        assert result is not None
        assert 'trading' in result
        assert 'data' in result

    def test_get_nested_value(self, temp_config_file):
        """중첩된 값 가져오기 테스트"""
        # Given
        config = ConfigManager()
        config._config = None  # Reset
        config.load(temp_config_file)

        # When
        lookback_days = config.get('trading.vwap_validation.lookback_days')
        default_days = config.get('data.fetching.default_days')

        # Then
        assert lookback_days == 10
        assert default_days == 7

    def test_get_with_default(self, temp_config_file):
        """존재하지 않는 키에 대해 기본값 반환 테스트"""
        # Given
        config = ConfigManager()
        config._config = None  # Reset
        config.load(temp_config_file)

        # When
        value = config.get('nonexistent.key', default=999)

        # Then
        assert value == 999

    def test_get_section(self, temp_config_file):
        """섹션 전체 가져오기 테스트"""
        # Given
        config = ConfigManager()
        config._config = None  # Reset
        config.load(temp_config_file)

        # When
        trading_section = config.get_section('trading')

        # Then
        assert 'vwap_validation' in trading_section
        assert 'risk_management' in trading_section

    def test_environment_merge_development(self, temp_config_file):
        """개발 환경 설정 병합 테스트"""
        # Given
        config = ConfigManager()
        config._config = None  # Reset
        config.load(temp_config_file, environment='development')

        # When
        debug = config.get('debug')
        max_positions = config.get('max_positions')

        # Then
        assert debug is True
        assert max_positions == 2  # development 환경 값

    def test_environment_merge_production(self, temp_config_file):
        """운영 환경 설정 병합 테스트"""
        # Given
        config = ConfigManager()
        config._config = None  # Reset
        config.load(temp_config_file, environment='production')

        # When
        debug = config.get('debug')
        max_positions = config.get('max_positions')

        # Then
        assert debug is False
        assert max_positions == 10  # production 환경 값

    def test_set_value_runtime(self, temp_config_file):
        """런타임 설정값 변경 테스트"""
        # Given
        config = ConfigManager()
        config._config = None  # Reset
        config.load(temp_config_file)

        # When
        config.set('trading.risk_management.max_positions', 20)
        value = config.get('trading.risk_management.max_positions')

        # Then
        assert value == 20

    def test_default_config_when_file_not_exists(self):
        """설정 파일 없을 때 기본 설정 사용 테스트"""
        # Given
        config = ConfigManager()
        config._config = None  # Reset

        # When
        config.load('nonexistent_file.yaml')
        value = config.get('trading.vwap_validation.lookback_days')

        # Then
        assert value == 10  # 기본값

    def test_reload_config(self, temp_config_file):
        """설정 다시 로드 테스트"""
        # Given
        config = ConfigManager()
        config._config = None  # Reset
        config.load(temp_config_file)
        old_value = config.get('trading.vwap_validation.lookback_days')

        # When
        config.reload(temp_config_file)
        new_value = config.get('trading.vwap_validation.lookback_days')

        # Then
        assert old_value == new_value  # 값은 동일 (파일 내용이 같으므로)

    def test_to_dict(self, temp_config_file):
        """전체 설정 딕셔너리 변환 테스트"""
        # Given
        config = ConfigManager()
        config._config = None  # Reset
        config.load(temp_config_file)

        # When
        config_dict = config.to_dict()

        # Then
        assert isinstance(config_dict, dict)
        assert 'trading' in config_dict
        assert config_dict is not config._config  # 복사본 확인


class TestGlobalFunctions:
    """전역 함수 테스트"""

    def test_get_config_function(self):
        """get_config 전역 함수 테스트"""
        # When
        value = get_config('trading.vwap_validation.lookback_days', default=10)

        # Then
        assert value is not None

    def test_get_section_function(self):
        """get_section 전역 함수 테스트"""
        # When
        section = get_section('trading')

        # Then
        assert isinstance(section, dict)
