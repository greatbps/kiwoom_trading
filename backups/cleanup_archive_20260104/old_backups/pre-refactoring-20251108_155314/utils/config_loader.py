"""
설정 파일 로더 (Config Loader)

YAML 설정 파일을 로드하고 각 컴포넌트에 전달
"""
import yaml
from pathlib import Path
from typing import Dict, Any, Optional


class ConfigLoader:
    """YAML 설정 파일 로더"""

    def __init__(self, config_path: str = "config/strategy_config.yaml"):
        """
        초기화

        Args:
            config_path: 설정 파일 경로 (프로젝트 루트 기준)
        """
        self.config_path = Path(config_path)
        self.config: Dict[str, Any] = {}
        self.load()

    def load(self):
        """설정 파일 로드"""
        if not self.config_path.exists():
            raise FileNotFoundError(f"설정 파일을 찾을 수 없습니다: {self.config_path}")

        with open(self.config_path, 'r', encoding='utf-8') as f:
            self.config = yaml.safe_load(f)

    def get(self, key: str, default: Any = None) -> Any:
        """
        설정 값 가져오기 (키 경로 지원)

        Args:
            key: 설정 키 (예: "trailing.activation_pct")
            default: 기본값

        Returns:
            설정 값 또는 기본값
        """
        keys = key.split('.')
        value = self.config

        for k in keys:
            if isinstance(value, dict) and k in value:
                value = value[k]
            else:
                return default

        return value

    def get_section(self, section: str) -> Dict[str, Any]:
        """
        섹션 전체 가져오기

        Args:
            section: 섹션 이름 (예: "trailing", "filters")

        Returns:
            섹션 딕셔너리
        """
        return self.config.get(section, {})

    def get_analyzer_config(self) -> Dict[str, Any]:
        """
        EntryTimingAnalyzer 초기화용 설정 추출

        Returns:
            analyzer __init__ 파라미터 딕셔너리
        """
        trailing = self.get_section('trailing')
        filters = self.get_section('filters')
        time_filter = self.get_section('time_filter')
        re_entry = self.get_section('re_entry')

        return {
            # 트레일링 스탑
            'trailing_activation_pct': trailing.get('activation_pct', 1.5),
            'trailing_ratio': trailing.get('ratio', 1.0),
            'stop_loss_pct': trailing.get('stop_loss_pct', 1.0),
            'profit_tier_trailing_ratio': trailing.get('profit_tier_ratio', 0.5),

            # 필터
            'breakout_confirm_candles': filters.get('breakout_confirm_candles', 2),
            'min_volume_value': filters.get('min_volume_value', 1000000000),

            # 시간 필터
            'avoid_early_minutes': time_filter.get('avoid_early_minutes', 10),
            'avoid_late_minutes': time_filter.get('avoid_late_minutes', 10),

            # 재진입 방지
            're_entry_cooldown_minutes': re_entry.get('cooldown_minutes', 30),
        }

    def get_risk_manager_config(self) -> Dict[str, Any]:
        """
        RiskManager 초기화용 설정 추출

        Returns:
            risk_manager __init__ 파라미터 딕셔너리
        """
        risk = self.get_section('risk_management')

        return {
            'initial_capital': risk.get('initial_capital', 10000000),
            'daily_max_loss_pct': risk.get('daily_max_loss_pct', 2.0),
            'max_drawdown_pct': risk.get('max_drawdown_pct', 10.0),
            'max_trades_per_day': risk.get('max_trades_per_day', 5),
            'max_consecutive_losses': risk.get('max_consecutive_losses', 3),
            'position_risk_pct': risk.get('position_risk_pct', 1.0),
        }

    def get_logger_config(self) -> Dict[str, Any]:
        """
        TradeLogger 초기화용 설정 추출

        Returns:
            logger __init__ 파라미터 딕셔너리
        """
        logging = self.get_section('logging')

        return {
            'log_dir': logging.get('log_dir', 'logs'),
            'enabled': logging.get('enabled', True),
        }

    def get_signal_generation_config(self) -> Dict[str, Any]:
        """
        generate_signals() 메서드용 설정 추출

        Returns:
            generate_signals 파라미터 딕셔너리
        """
        filters = self.get_section('filters')

        return {
            'use_trend_filter': filters.get('use_trend_filter', True),
            'use_volume_filter': filters.get('use_volume_filter', True),
            'use_breakout_confirm': filters.get('use_breakout_confirm', False),
            'use_volume_value_filter': filters.get('use_volume_value_filter', False),
            'use_daily_trend_filter': filters.get('use_daily_trend_filter', False),
            'trend_period': filters.get('trend_period', 20),
            # 🔽 신규 추가: 거래량 배수 및 시장 모멘텀 설정
            'volume_multiplier': float(filters.get('volume_multiplier', 1.2)),
            'use_market_momentum': filters.get('use_market_momentum', False),
            # 🔽 진입 조건 완화 옵션
            'vwap_tolerance_pct': float(filters.get('vwap_tolerance_pct', 0.0)),
            'ma_tolerance_pct': float(filters.get('ma_tolerance_pct', 0.0)),
            'vwap_cross_only': bool(filters.get('vwap_cross_only', True)),
            # 🔽 Williams %R 필터
            'use_williams_r_filter': bool(filters.get('use_williams_r_filter', False)),
            'williams_r_period': int(filters.get('williams_r_period', 14)),
            'williams_r_long_ceiling': float(filters.get('williams_r_long_ceiling', -20.0)),
            'williams_r_short_floor': float(filters.get('williams_r_short_floor', -80.0)),
        }

    def get_trailing_config(self) -> Dict[str, Any]:
        """
        check_trailing_stop() 메서드용 설정 추출

        Returns:
            trailing stop 파라미터 딕셔너리
        """
        trailing = self.get_section('trailing')

        return {
            'use_atr_based': trailing.get('use_atr_based', False),
            'atr_multiplier': trailing.get('atr_multiplier', 1.5),
            'use_profit_tier': trailing.get('use_profit_tier', False),
            'profit_tier_threshold': trailing.get('profit_tier_threshold', 3.0),
        }

    def get_partial_exit_config(self) -> Dict[str, Any]:
        """
        부분 청산 설정 추출

        Returns:
            부분 청산 설정 딕셔너리
        """
        partial = self.get_section('partial_exit')

        return {
            'enabled': partial.get('enabled', False),
            'tiers': partial.get('tiers', []),
        }

    def reload(self):
        """설정 파일 다시 로드"""
        self.load()

    def __repr__(self):
        return f"ConfigLoader(path={self.config_path})"


def load_config(config_path: str = "config/strategy_config.yaml") -> ConfigLoader:
    """
    설정 파일 로드 (헬퍼 함수)

    Args:
        config_path: 설정 파일 경로

    Returns:
        ConfigLoader 인스턴스
    """
    return ConfigLoader(config_path)
