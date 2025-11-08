"""
설정 파일 관리 모듈
- 분석 엔진 가중치 조정
- 백테스트 파라미터 설정
- JSON 형식 설정 파일 관리
"""
import json
import os
from typing import Dict, Any
from datetime import datetime


class ConfigManager:
    """설정 파일 관리 클래스"""

    def __init__(self, config_path: str = "./config/analysis_weights.json"):
        """
        초기화

        Args:
            config_path: 설정 파일 경로
        """
        self.config_path = config_path
        self.config = self.load_config()

    def load_config(self) -> Dict[str, Any]:
        """
        설정 파일 로드

        Returns:
            설정 딕셔너리
        """
        if os.path.exists(self.config_path):
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️  설정 파일 로드 실패: {e}")
                return self._default_config()
        else:
            return self._default_config()

    def _default_config(self) -> Dict[str, Any]:
        """
        기본 설정값

        Returns:
            기본 설정 딕셔너리
        """
        return {
            "analysis_weights": {
                "news": 0.30,
                "technical": 0.40,
                "supply_demand": 0.15,
                "fundamental": 0.15
            },
            "filter_params": {
                "min_total_score": 65,
                "min_vwap_win_rate": 0.50,
                "min_vwap_trades": 2,
                "min_chart_bars": 100
            },
            "trading_params": {
                "holding_period_days": 3,
                "take_profit_pct": 0.10,
                "stop_loss_pct": -0.05,
                "max_stocks": 10,
                "investment_per_stock": 1000000
            },
            "last_updated": datetime.now().isoformat(),
            "version": "2.0"
        }

    def save_config(self) -> bool:
        """
        설정 파일 저장

        Returns:
            저장 성공 여부
        """
        try:
            # 디렉토리 생성
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)

            # 마지막 업데이트 시간 갱신
            self.config["last_updated"] = datetime.now().isoformat()

            # 파일 저장
            with open(self.config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)

            return True
        except Exception as e:
            print(f"❌ 설정 파일 저장 실패: {e}")
            return False

    def update_weights(self, new_weights: Dict[str, float]) -> bool:
        """
        분석 가중치 업데이트

        Args:
            new_weights: 새로운 가중치 딕셔너리

        Returns:
            업데이트 성공 여부
        """
        # 가중치 합이 1.0인지 확인
        total = sum(new_weights.values())
        if abs(total - 1.0) > 0.01:
            print(f"⚠️  가중치 합이 1.0이 아닙니다: {total:.3f}")
            # 정규화
            new_weights = {k: v / total for k, v in new_weights.items()}
            print(f"✓ 정규화된 가중치로 조정: {new_weights}")

        self.config["analysis_weights"] = new_weights
        return self.save_config()

    def update_filter_params(self, new_params: Dict[str, Any]) -> bool:
        """
        필터 파라미터 업데이트

        Args:
            new_params: 새로운 필터 파라미터

        Returns:
            업데이트 성공 여부
        """
        self.config["filter_params"].update(new_params)
        return self.save_config()

    def update_trading_params(self, new_params: Dict[str, Any]) -> bool:
        """
        거래 파라미터 업데이트

        Args:
            new_params: 새로운 거래 파라미터

        Returns:
            업데이트 성공 여부
        """
        self.config["trading_params"].update(new_params)
        return self.save_config()

    def get_weights(self) -> Dict[str, float]:
        """
        현재 분석 가중치 가져오기

        Returns:
            가중치 딕셔너리
        """
        return self.config.get("analysis_weights", self._default_config()["analysis_weights"])

    def get_filter_params(self) -> Dict[str, Any]:
        """
        현재 필터 파라미터 가져오기

        Returns:
            필터 파라미터 딕셔너리
        """
        return self.config.get("filter_params", self._default_config()["filter_params"])

    def get_trading_params(self) -> Dict[str, Any]:
        """
        현재 거래 파라미터 가져오기

        Returns:
            거래 파라미터 딕셔너리
        """
        return self.config.get("trading_params", self._default_config()["trading_params"])

    def backup_config(self) -> str:
        """
        현재 설정을 백업

        Returns:
            백업 파일 경로
        """
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        backup_dir = os.path.join(os.path.dirname(self.config_path), "backups")
        os.makedirs(backup_dir, exist_ok=True)

        backup_path = os.path.join(backup_dir, f"weights_backup_{timestamp}.json")

        try:
            with open(backup_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)

            return backup_path
        except Exception as e:
            print(f"❌ 백업 실패: {e}")
            return ""

    def restore_from_backup(self, backup_path: str) -> bool:
        """
        백업에서 설정 복원

        Args:
            backup_path: 백업 파일 경로

        Returns:
            복원 성공 여부
        """
        try:
            with open(backup_path, 'r', encoding='utf-8') as f:
                self.config = json.load(f)

            return self.save_config()
        except Exception as e:
            print(f"❌ 복원 실패: {e}")
            return False

    def display_config(self) -> str:
        """
        현재 설정을 보기 좋게 출력

        Returns:
            포맷된 설정 문자열
        """
        output = []
        output.append("=" * 60)
        output.append("📋 현재 설정")
        output.append("=" * 60)

        # 가중치
        output.append("\n🔍 분석 가중치:")
        for key, value in self.config.get("analysis_weights", {}).items():
            output.append(f"  • {key}: {value:.2%}")

        # 필터 파라미터
        output.append("\n🎯 필터 파라미터:")
        for key, value in self.config.get("filter_params", {}).items():
            output.append(f"  • {key}: {value}")

        # 거래 파라미터
        output.append("\n💰 거래 파라미터:")
        for key, value in self.config.get("trading_params", {}).items():
            output.append(f"  • {key}: {value}")

        # 메타 정보
        output.append(f"\n📅 마지막 업데이트: {self.config.get('last_updated', 'N/A')}")
        output.append(f"🔖 버전: {self.config.get('version', 'N/A')}")
        output.append("=" * 60)

        return "\n".join(output)


if __name__ == "__main__":
    # 테스트
    manager = ConfigManager()

    print("초기 설정:")
    print(manager.display_config())

    # 가중치 업데이트 테스트
    new_weights = {
        "news": 0.25,
        "technical": 0.45,
        "supply_demand": 0.15,
        "fundamental": 0.15
    }

    if manager.update_weights(new_weights):
        print("\n✅ 가중치 업데이트 완료!")
        print(manager.display_config())
