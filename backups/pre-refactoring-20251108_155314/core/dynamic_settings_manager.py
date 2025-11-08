#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dynamic_settings_manager.py

매매 설정 동적 조정 시스템 - 잔고 변화에 따른 자동 최적화
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field
import numpy as np
import pandas as pd
from pathlib import Path
import json

from utils.logger import get_logger

# 알림 시스템 임포트
try:
    from monitoring.notification_system import send_notification, NotificationLevel
except ImportError:
    send_notification = None
    NotificationLevel = None

@dataclass
class BalanceThreshold:
    """잔고 임계값 설정"""
    min_balance: float  # 최소 잔고
    max_balance: float  # 최대 잔고
    position_size_ratio: float  # 포지션 크기 비율
    max_positions: int  # 최대 포지션 수
    risk_level: str  # 리스크 레벨 (low, medium, high)
    stop_loss_pct: float  # 손절 비율
    take_profit_pct: float  # 익절 비율

@dataclass
class TradingSettings:
    """거래 설정"""
    position_size_ratio: float = 0.1  # 포지션 크기 비율 (10%)
    max_positions: int = 5  # 최대 보유 포지션 수
    stop_loss_pct: float = 3.0  # 손절 비율 (3%)
    take_profit_pct: float = 8.0  # 익절 비율 (8%)
    risk_level: str = "medium"  # 리스크 레벨
    min_cash_reserve: float = 0.2  # 최소 현금 보유율 (20%)
    max_daily_trades: int = 10  # 일일 최대 거래 수
    volatility_adjustment: float = 1.0  # 변동성 조정 계수
    max_investment_per_stock: float = 0.05  # 종목당 최대 투자 비율 (5%)

@dataclass
class BalanceHistory:
    """잔고 히스토리"""
    timestamp: datetime
    total_balance: float
    cash_balance: float
    stock_value: float
    pnl: float
    pnl_pct: float
    settings_used: TradingSettings

class DynamicSettingsManager:
    """동적 설정 관리자"""

    def __init__(self, config=None, data_dir: str = "data"):
        """동적 설정 관리자 초기화"""
        self.logger = get_logger("DynamicSettingsManager")
        self.config = config
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(exist_ok=True)

        # 설정 파일 경로
        self.settings_file = self.data_dir / "dynamic_settings.json"
        self.history_file = self.data_dir / "balance_history.json"

        # 기본 임계값 설정
        self.balance_thresholds = self._initialize_thresholds()

        # 현재 설정
        self.current_settings = TradingSettings()

        # 잔고 히스토리
        self.balance_history: List[BalanceHistory] = []

        # 로드 기존 데이터
        self._load_settings()
        self._load_history()

    def _initialize_thresholds(self) -> List[BalanceThreshold]:
        """잔고 임계값 초기화"""
        return [
            # 보수적 (잔고 감소 시)
            BalanceThreshold(
                min_balance=0,
                max_balance=5_000_000,  # 500만원 이하
                position_size_ratio=0.05,  # 5%
                max_positions=3,
                risk_level="low",
                stop_loss_pct=2.0,  # 2%
                take_profit_pct=5.0   # 5%
            ),
            # 일반적 (중간 잔고)
            BalanceThreshold(
                min_balance=5_000_000,
                max_balance=20_000_000,  # 500만원~2000만원
                position_size_ratio=0.1,  # 10%
                max_positions=5,
                risk_level="medium",
                stop_loss_pct=3.0,  # 3%
                take_profit_pct=8.0   # 8%
            ),
            # 적극적 (높은 잔고)
            BalanceThreshold(
                min_balance=20_000_000,
                max_balance=float('inf'),  # 2000만원 이상
                position_size_ratio=0.15,  # 15%
                max_positions=8,
                risk_level="high",
                stop_loss_pct=4.0,  # 4%
                take_profit_pct=12.0  # 12%
            )
        ]

    async def update_balance_and_adjust_settings(
        self,
        current_balance: float,
        cash_balance: float,
        stock_value: float,
        trading_handler=None
    ) -> Tuple[TradingSettings, Dict[str, Any]]:
        """
        잔고 업데이트 및 설정 자동 조정

        Args:
            current_balance: 현재 총 잔고
            cash_balance: 현금 잔고
            stock_value: 주식 평가액
            trading_handler: 거래 핸들러

        Returns:
            Tuple[조정된 설정, 조정 정보]
        """
        try:
            self.logger.info(f"💰 잔고 업데이트 및 설정 조정 시작 - 총액: {current_balance:,.0f}원")

            # 잔고 히스토리 추가
            balance_record = await self._add_balance_record(
                current_balance, cash_balance, stock_value
            )

            # 성과 분석
            performance_analysis = await self._analyze_performance()

            # 시장 변동성 분석
            volatility_analysis = await self._analyze_market_volatility(trading_handler)

            # 설정 조정
            new_settings = await self._adjust_settings(
                current_balance,
                performance_analysis,
                volatility_analysis
            )

            # 조정 정보 생성
            adjustment_info = {
                "timestamp": datetime.now(),
                "previous_settings": self.current_settings.__dict__,
                "new_settings": new_settings.__dict__,
                "balance_info": {
                    "total": current_balance,
                    "cash": cash_balance,
                    "stocks": stock_value,
                    "cash_ratio": cash_balance / current_balance if current_balance > 0 else 0
                },
                "performance_analysis": performance_analysis,
                "volatility_analysis": volatility_analysis,
                "adjustments_made": self._get_setting_changes(self.current_settings, new_settings)
            }

            # 설정 업데이트
            self.current_settings = new_settings

            # 저장
            await self._save_settings()
            await self._save_history()

            # 알림 발송 (중요한 변경사항이 있는 경우)
            await self._send_notifications(adjustment_info)

            self.logger.info(f"✅ 설정 조정 완료 - 리스크 레벨: {new_settings.risk_level}")

            return new_settings, adjustment_info

        except Exception as e:
            self.logger.error(f"❌ 설정 조정 실패: {e}")
            return self.current_settings, {"error": str(e)}

    async def _add_balance_record(
        self,
        total_balance: float,
        cash_balance: float,
        stock_value: float
    ) -> BalanceHistory:
        """잔고 기록 추가"""
        try:
            # PnL 계산
            pnl = 0.0
            pnl_pct = 0.0

            if self.balance_history:
                initial_balance = self.balance_history[0].total_balance
                pnl = total_balance - initial_balance
                pnl_pct = (pnl / initial_balance * 100) if initial_balance > 0 else 0

            # 새 기록 생성
            record = BalanceHistory(
                timestamp=datetime.now(),
                total_balance=total_balance,
                cash_balance=cash_balance,
                stock_value=stock_value,
                pnl=pnl,
                pnl_pct=pnl_pct,
                settings_used=self.current_settings
            )

            self.balance_history.append(record)

            # 히스토리 정리 (최근 100개만 유지)
            if len(self.balance_history) > 100:
                self.balance_history = self.balance_history[-100:]

            return record

        except Exception as e:
            self.logger.error(f"❌ 잔고 기록 추가 실패: {e}")
            raise

    async def _analyze_performance(self) -> Dict[str, Any]:
        """성과 분석"""
        try:
            if len(self.balance_history) < 2:
                return {"status": "insufficient_data"}

            # 최근 데이터 추출
            recent_records = self.balance_history[-30:]  # 최근 30건

            # 수익률 계산
            balances = [record.total_balance for record in recent_records]
            returns = []

            for i in range(1, len(balances)):
                daily_return = (balances[i] - balances[i-1]) / balances[i-1] * 100
                returns.append(daily_return)

            if not returns:
                return {"status": "insufficient_data"}

            # 통계 계산
            avg_return = np.mean(returns)
            volatility = np.std(returns)
            max_drawdown = self._calculate_max_drawdown(balances)

            # 샤프비율 (무위험 수익률 0% 가정)
            sharpe_ratio = avg_return / volatility if volatility > 0 else 0

            # 승률 계산
            win_rate = len([r for r in returns if r > 0]) / len(returns) * 100

            return {
                "status": "analyzed",
                "avg_return": avg_return,
                "volatility": volatility,
                "max_drawdown": max_drawdown,
                "sharpe_ratio": sharpe_ratio,
                "win_rate": win_rate,
                "total_trades": len(returns),
                "current_pnl_pct": recent_records[-1].pnl_pct if recent_records else 0
            }

        except Exception as e:
            self.logger.error(f"❌ 성과 분석 실패: {e}")
            return {"status": "error", "message": str(e)}

    def _calculate_max_drawdown(self, balances: List[float]) -> float:
        """최대 드로우다운 계산"""
        if not balances:
            return 0

        peak = balances[0]
        max_dd = 0

        for balance in balances:
            if balance > peak:
                peak = balance

            drawdown = (peak - balance) / peak * 100
            max_dd = max(max_dd, drawdown)

        return max_dd

    async def _analyze_market_volatility(self, trading_handler=None) -> Dict[str, Any]:
        """시장 변동성 분석"""
        try:
            # 기본 변동성 정보
            volatility_info = {
                "market_volatility": "medium",  # low, medium, high
                "volatility_score": 1.0,
                "adjustment_factor": 1.0
            }

            # TODO: 실제 시장 데이터를 사용한 변동성 계산
            # if trading_handler:
            #     # KOSPI/KOSDAQ 지수 데이터로 변동성 계산
            #     pass

            return volatility_info

        except Exception as e:
            self.logger.error(f"❌ 시장 변동성 분석 실패: {e}")
            return {
                "market_volatility": "medium",
                "volatility_score": 1.0,
                "adjustment_factor": 1.0
            }

    async def _adjust_settings(
        self,
        current_balance: float,
        performance_analysis: Dict[str, Any],
        volatility_analysis: Dict[str, Any]
    ) -> TradingSettings:
        """설정 조정"""
        try:
            # 기본 설정 (잔고 기반)
            base_settings = self._get_settings_by_balance(current_balance)

            # 성과 기반 조정
            performance_adjusted = self._adjust_by_performance(base_settings, performance_analysis)

            # 변동성 기반 조정
            final_settings = self._adjust_by_volatility(performance_adjusted, volatility_analysis)

            # 제약 조건 적용
            final_settings = self._apply_constraints(final_settings)

            return final_settings

        except Exception as e:
            self.logger.error(f"❌ 설정 조정 실패: {e}")
            return self.current_settings

    def _get_settings_by_balance(self, balance: float) -> TradingSettings:
        """잔고 기반 기본 설정"""
        for threshold in self.balance_thresholds:
            if threshold.min_balance <= balance < threshold.max_balance:
                return TradingSettings(
                    position_size_ratio=threshold.position_size_ratio,
                    max_positions=threshold.max_positions,
                    stop_loss_pct=threshold.stop_loss_pct,
                    take_profit_pct=threshold.take_profit_pct,
                    risk_level=threshold.risk_level
                )

        # 기본값 반환
        return TradingSettings()

    def _adjust_by_performance(
        self,
        settings: TradingSettings,
        performance: Dict[str, Any]
    ) -> TradingSettings:
        """성과 기반 조정"""
        if performance.get("status") != "analyzed":
            return settings

        # 성과가 좋으면 약간 공격적으로, 나쁘면 보수적으로
        current_pnl_pct = performance.get("current_pnl_pct", 0)
        win_rate = performance.get("win_rate", 50)
        sharpe_ratio = performance.get("sharpe_ratio", 0)

        # 조정 계수 계산
        performance_factor = 1.0

        if current_pnl_pct > 10 and win_rate > 60 and sharpe_ratio > 1.0:
            # 성과가 매우 좋음 - 약간 공격적
            performance_factor = 1.1
        elif current_pnl_pct < -5 or win_rate < 40 or sharpe_ratio < 0:
            # 성과가 나쁨 - 보수적
            performance_factor = 0.8

        # 설정 조정
        adjusted_settings = TradingSettings(
            position_size_ratio=min(settings.position_size_ratio * performance_factor, 0.2),
            max_positions=settings.max_positions,
            stop_loss_pct=settings.stop_loss_pct / performance_factor,
            take_profit_pct=settings.take_profit_pct * performance_factor,
            risk_level=settings.risk_level
        )

        return adjusted_settings

    def _adjust_by_volatility(
        self,
        settings: TradingSettings,
        volatility: Dict[str, Any]
    ) -> TradingSettings:
        """변동성 기반 조정"""
        volatility_factor = volatility.get("adjustment_factor", 1.0)

        # 변동성이 높으면 포지션 크기 줄이고 손절 빠르게
        adjusted_settings = TradingSettings(
            position_size_ratio=settings.position_size_ratio / volatility_factor,
            max_positions=settings.max_positions,
            stop_loss_pct=settings.stop_loss_pct / volatility_factor,
            take_profit_pct=settings.take_profit_pct,
            risk_level=settings.risk_level,
            volatility_adjustment=volatility_factor
        )

        return adjusted_settings

    def _apply_constraints(self, settings: TradingSettings) -> TradingSettings:
        """제약 조건 적용"""
        # 최소/최대 값 제한
        settings.position_size_ratio = max(0.01, min(settings.position_size_ratio, 0.25))  # 1-25%
        settings.max_positions = max(1, min(settings.max_positions, 15))  # 1-15개
        settings.stop_loss_pct = max(0.5, min(settings.stop_loss_pct, 10.0))  # 0.5-10%
        settings.take_profit_pct = max(2.0, min(settings.take_profit_pct, 30.0))  # 2-30%
        settings.min_cash_reserve = max(0.1, min(settings.min_cash_reserve, 0.5))  # 10-50%
        settings.max_daily_trades = max(1, min(settings.max_daily_trades, 50))  # 1-50개

        return settings

    def _get_setting_changes(
        self,
        old_settings: TradingSettings,
        new_settings: TradingSettings
    ) -> List[Dict[str, Any]]:
        """설정 변경사항 추출"""
        changes = []

        # 주요 설정 비교
        settings_to_check = [
            ("position_size_ratio", "포지션 크기 비율", "%"),
            ("max_positions", "최대 포지션 수", "개"),
            ("stop_loss_pct", "손절 비율", "%"),
            ("take_profit_pct", "익절 비율", "%"),
            ("risk_level", "리스크 레벨", "")
        ]

        for attr, name, unit in settings_to_check:
            old_val = getattr(old_settings, attr)
            new_val = getattr(new_settings, attr)

            if old_val != new_val:
                changes.append({
                    "setting": name,
                    "old_value": old_val,
                    "new_value": new_val,
                    "unit": unit,
                    "change_type": "increase" if new_val > old_val else "decrease" if isinstance(new_val, (int, float)) else "change"
                })

        return changes

    async def get_current_settings(self) -> TradingSettings:
        """현재 설정 반환"""
        return self.current_settings

    async def get_balance_summary(self) -> Dict[str, Any]:
        """잔고 요약 정보"""
        if not self.balance_history:
            return {"status": "no_data"}

        latest = self.balance_history[-1]

        return {
            "status": "available",
            "latest_balance": latest.total_balance,
            "latest_pnl": latest.pnl,
            "latest_pnl_pct": latest.pnl_pct,
            "cash_ratio": latest.cash_balance / latest.total_balance if latest.total_balance > 0 else 0,
            "stock_ratio": latest.stock_value / latest.total_balance if latest.total_balance > 0 else 0,
            "record_count": len(self.balance_history),
            "current_risk_level": self.current_settings.risk_level
        }

    async def _save_settings(self):
        """설정 저장"""
        try:
            settings_data = {
                "current_settings": self.current_settings.__dict__,
                "last_updated": datetime.now().isoformat()
            }

            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(settings_data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            self.logger.error(f"❌ 설정 저장 실패: {e}")

    async def _save_history(self):
        """히스토리 저장"""
        try:
            history_data = []
            for record in self.balance_history[-50:]:  # 최근 50개만 저장
                history_data.append({
                    "timestamp": record.timestamp.isoformat(),
                    "total_balance": record.total_balance,
                    "cash_balance": record.cash_balance,
                    "stock_value": record.stock_value,
                    "pnl": record.pnl,
                    "pnl_pct": record.pnl_pct,
                    "settings_used": record.settings_used.__dict__
                })

            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(history_data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            self.logger.error(f"❌ 히스토리 저장 실패: {e}")

    def _load_settings(self):
        """설정 로드"""
        try:
            if self.settings_file.exists():
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)

                settings_dict = data.get("current_settings", {})
                if settings_dict:
                    self.current_settings = TradingSettings(**settings_dict)
                    self.logger.info("✅ 저장된 설정을 로드했습니다")

        except Exception as e:
            self.logger.error(f"❌ 설정 로드 실패: {e}")

    def _load_history(self):
        """히스토리 로드"""
        try:
            if self.history_file.exists():
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    history_data = json.load(f)

                for item in history_data:
                    record = BalanceHistory(
                        timestamp=datetime.fromisoformat(item["timestamp"]),
                        total_balance=item["total_balance"],
                        cash_balance=item["cash_balance"],
                        stock_value=item["stock_value"],
                        pnl=item["pnl"],
                        pnl_pct=item["pnl_pct"],
                        settings_used=TradingSettings(**item["settings_used"])
                    )
                    self.balance_history.append(record)

                self.logger.info(f"✅ 잔고 히스토리 {len(self.balance_history)}건을 로드했습니다")

        except Exception as e:
            self.logger.error(f"❌ 히스토리 로드 실패: {e}")

    async def _send_notifications(self, adjustment_info: Dict[str, Any]):
        """설정 변경 알림 발송"""
        try:
            if not send_notification or not NotificationLevel:
                return

            adjustments = adjustment_info.get("adjustments_made", [])
            if not adjustments:
                return

            # 중요한 변경사항 확인
            important_changes = []
            risk_level_changed = False

            for change in adjustments:
                if change["setting"] == "리스크 레벨":
                    risk_level_changed = True
                    important_changes.append(f"• {change['setting']}: {change['old_value']} → {change['new_value']}")
                elif change["setting"] in ["포지션 크기 비율", "손절 비율"]:
                    change_pct = abs(change["new_value"] - change["old_value"]) / change["old_value"] * 100
                    if change_pct > 20:  # 20% 이상 변경시 중요 변경으로 간주
                        important_changes.append(f"• {change['setting']}: {change['old_value']}{change['unit']} → {change['new_value']}{change['unit']}")

            # 성과 기반 알림
            performance = adjustment_info.get("performance_analysis", {})
            current_pnl = performance.get("current_pnl_pct", 0)

            # 큰 손실 알림
            if current_pnl < -5.0:
                await send_notification(
                    "large_loss",
                    "큰 손실 발생",
                    f"현재 손실률: {current_pnl:.2f}% - 설정이 보수적으로 조정되었습니다",
                    NotificationLevel.WARNING,
                    {"loss_pct": abs(current_pnl), "balance_info": adjustment_info["balance_info"]}
                )

            # 큰 수익 알림
            elif current_pnl > 10.0:
                await send_notification(
                    "large_profit",
                    "큰 수익 발생",
                    f"현재 수익률: {current_pnl:.2f}% - 설정이 약간 공격적으로 조정되었습니다",
                    NotificationLevel.INFO,
                    {"profit_pct": current_pnl, "balance_info": adjustment_info["balance_info"]}
                )

            # 리스크 레벨 변경 알림
            if risk_level_changed:
                old_level = adjustment_info["previous_settings"]["risk_level"]
                new_level = adjustment_info["new_settings"]["risk_level"]

                await send_notification(
                    "risk_level_changed",
                    "리스크 레벨 변경",
                    f"리스크 레벨이 '{old_level}'에서 '{new_level}'로 변경되었습니다",
                    NotificationLevel.WARNING,
                    {
                        "old_risk_level": old_level,
                        "new_risk_level": new_level,
                        "balance_info": adjustment_info["balance_info"]
                    }
                )

            # 일반 설정 변경 알림
            elif important_changes:
                message = "중요한 거래 설정이 변경되었습니다:\n" + "\n".join(important_changes)

                await send_notification(
                    "settings_changed",
                    "거래 설정 변경",
                    message,
                    NotificationLevel.INFO,
                    {
                        "changes_count": len(important_changes),
                        "balance_info": adjustment_info["balance_info"]
                    }
                )

        except Exception as e:
            self.logger.error(f"❌ 알림 발송 실패: {e}")

    async def update_settings_based_on_balance(self, balance: float) -> bool:
        """잔고 기반 설정 업데이트 (호환성 메서드)"""
        try:
            # 기본값으로 현금 30%, 주식 70%로 가정
            cash_balance = balance * 0.3
            stock_value = balance * 0.7

            settings, _ = await self.update_balance_and_adjust_settings(
                current_balance=balance,
                cash_balance=cash_balance,
                stock_value=stock_value
            )

            self.logger.info(f"✅ 잔고 기반 설정 업데이트 완료: {balance:,.0f}원")
            return True
        except Exception as e:
            self.logger.error(f"❌ 잔고 기반 설정 업데이트 실패: {e}")
            return False

# 사용 예시
async def main():
    """테스트 함수"""
    manager = DynamicSettingsManager()

    # 잔고 업데이트 및 설정 조정
    settings, info = await manager.update_balance_and_adjust_settings(
        current_balance=10_000_000,  # 1000만원
        cash_balance=3_000_000,      # 300만원 현금
        stock_value=7_000_000        # 700만원 주식
    )

    print(f"조정된 설정: {settings}")
    print(f"조정 정보: {info}")

if __name__ == "__main__":
    asyncio.run(main())