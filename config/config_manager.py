"""
설정 파일 관리 모듈

YAML 설정 파일을 로드하고 중앙에서 관리
Magic Numbers 제거 및 환경별 설정 지원
"""
import yaml
from pathlib import Path
from typing import Any, Dict, Optional
from rich.console import Console

console = Console()


class ConfigManager:
    """설정 관리자 (Singleton)"""

    _instance = None
    _config: Optional[Dict[str, Any]] = None
    _env: str = 'development'

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(
        self,
        config_path: str = 'config/trading_config.yaml',
        environment: str = 'development'
    ) -> Dict[str, Any]:
        """
        설정 파일 로드 (Singleton)

        Args:
            config_path: 설정 파일 경로
            environment: 환경 ('development' or 'production')

        Returns:
            설정 딕셔너리

        Example:
            >>> config = ConfigManager()
            >>> config.load()
            >>> value = config.get('trading.vwap_validation.lookback_days')
        """
        if self._config is None or self._env != environment:
            path = Path(config_path)

            if not path.exists():
                # 기본 설정 반환
                console.print(f"[yellow]⚠️  설정 파일 없음: {config_path}[/yellow]")
                console.print("[yellow]기본 설정 사용[/yellow]")
                self._config = self._get_default_config()
            else:
                try:
                    with open(path, 'r', encoding='utf-8') as f:
                        self._config = yaml.safe_load(f)

                    console.print(f"[green]✓ 설정 로드: {config_path}[/green]")

                except Exception as e:
                    console.print(f"[red]❌ 설정 로드 실패: {e}[/red]")
                    self._config = self._get_default_config()

            self._env = environment

            # 환경별 설정 병합
            self._merge_environment_config(environment)

        return self._config

    def _merge_environment_config(self, environment: str):
        """환경별 설정 병합"""
        if 'environments' in self._config and environment in self._config['environments']:
            env_config = self._config['environments'][environment]

            # 환경별 설정을 기본 설정에 오버라이드
            for key, value in env_config.items():
                if key in self._config:
                    if isinstance(value, dict) and isinstance(self._config[key], dict):
                        self._config[key].update(value)
                    else:
                        self._config[key] = value

            console.print(f"[green]✓ 환경 설정 적용: {environment}[/green]")

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        중첩된 키 값 가져오기

        Args:
            key_path: 점(.)으로 구분된 키 경로
                예: 'trading.vwap_validation.lookback_days'
            default: 기본값

        Returns:
            설정 값 또는 기본값

        Example:
            >>> config = ConfigManager()
            >>> config.load()
            >>> lookback = config.get('trading.vwap_validation.lookback_days', 10)
            >>> print(lookback)  # 10
        """
        if self._config is None:
            self.load()

        keys = key_path.split('.')
        value = self._config

        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default

        return value if value is not None else default

    def get_section(self, section: str) -> Dict[str, Any]:
        """
        섹션 전체 가져오기

        Args:
            section: 섹션 이름 (예: 'trading', 'data')

        Returns:
            섹션 딕셔너리

        Example:
            >>> config = ConfigManager()
            >>> trading_config = config.get_section('trading')
            >>> print(trading_config['vwap_validation'])
        """
        if self._config is None:
            self.load()

        return self._config.get(section, {})

    def reload(self, config_path: str = 'config/trading_config.yaml'):
        """
        설정 파일 다시 로드

        Args:
            config_path: 설정 파일 경로
        """
        self._config = None
        self.load(config_path, self._env)

    def _get_default_config(self) -> Dict[str, Any]:
        """기본 설정 반환 (YAML 파일 없을 때)"""
        return {
            'trading': {
                'vwap_validation': {
                    'lookback_days': 10,
                    'min_trades': 6,
                    'min_win_rate': 40.0,
                    'min_avg_profit': 0.3,
                    'min_profit_factor': 1.15
                },
                'risk_management': {
                    'max_position_size': 0.1,
                    'max_daily_loss': 0.05,
                    'stop_loss': 0.03,
                    'take_profit': 0.05,
                    'max_positions': 5
                }
            },
            'data': {
                'fetching': {
                    'default_days': 7,
                    'min_data_points': 100,
                    'cache_ttl': 300
                }
            },
            'validation': {
                'stock': {
                    'min_data_points': 100,
                    'min_volume': 1000,
                    'min_price': 100,
                    'max_price': 1000000
                }
            },
            'monitoring': {
                'realtime': {
                    'check_interval': 60
                }
            },
            'database': {
                'path': './database/kiwoom_trading.db'
            },
            'logging': {
                'level': 'INFO',
                'file': './logs/trading.log'
            }
        }

    def set(self, key_path: str, value: Any):
        """
        설정값 동적 변경 (런타임)

        Args:
            key_path: 점(.)으로 구분된 키 경로
            value: 새 값

        Example:
            >>> config = ConfigManager()
            >>> config.set('trading.risk_management.max_positions', 10)
        """
        if self._config is None:
            self.load()

        keys = key_path.split('.')
        target = self._config

        # 마지막 키 전까지 탐색
        for key in keys[:-1]:
            if key not in target:
                target[key] = {}
            target = target[key]

        # 마지막 키에 값 설정
        target[keys[-1]] = value

    def to_dict(self) -> Dict[str, Any]:
        """전체 설정을 딕셔너리로 반환"""
        if self._config is None:
            self.load()
        return self._config.copy()


# 전역 인스턴스
config = ConfigManager()


# 편의 함수
def get_config(key_path: str, default: Any = None) -> Any:
    """
    설정 값 가져오기 (전역 함수)

    Args:
        key_path: 키 경로
        default: 기본값

    Returns:
        설정 값

    Example:
        >>> from config.config_manager import get_config
        >>> lookback = get_config('trading.vwap_validation.lookback_days', 10)
    """
    return config.get(key_path, default)


def get_section(section: str) -> Dict[str, Any]:
    """
    섹션 가져오기 (전역 함수)

    Args:
        section: 섹션 이름

    Returns:
        섹션 딕셔너리

    Example:
        >>> from config.config_manager import get_section
        >>> trading = get_section('trading')
    """
    return config.get_section(section)


def reload_config():
    """설정 다시 로드 (전역 함수)"""
    config.reload()
