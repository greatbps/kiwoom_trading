#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
auto_stop_loss_system.py

자동 손절 시스템 - 손절 기준 도달 시 즉시 자동매도
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple, Any
from dataclasses import dataclass, field
from pathlib import Path
import json

# Rich for beautiful output
try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    from rich.progress import Progress
    from rich.live import Live
    from rich.layout import Layout
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False

from utils.logger import get_logger

@dataclass
class StopLossRule:
    """손절 규칙"""
    stock_code: str
    stock_name: str
    stop_loss_price: float
    stop_loss_pct: float  # 손절 비율 (음수)
    purchase_price: float
    quantity: int
    rule_type: str = "PERCENTAGE"  # PERCENTAGE, ABSOLUTE, TRAILING
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    last_checked: Optional[datetime] = None

@dataclass
class StopLossExecution:
    """손절 실행 기록"""
    stock_code: str
    stock_name: str
    trigger_price: float
    stop_loss_price: float
    quantity: int
    order_id: Optional[str] = None
    execution_price: Optional[float] = None
    execution_amount: Optional[float] = None
    status: str = "PENDING"  # PENDING, SUBMITTED, FILLED, FAILED
    executed_at: datetime = field(default_factory=datetime.now)
    error_message: Optional[str] = None

class AutoStopLossSystem:
    """자동 손절 시스템"""

    def __init__(self, config=None, trading_handler=None):
        """자동 손절 시스템 초기화"""
        self.logger = get_logger("AutoStopLossSystem")
        self.console = Console() if RICH_AVAILABLE else None
        self.config = config
        self.trading_handler = trading_handler

        # 데이터 저장 경로
        self.data_dir = Path("data")
        self.data_dir.mkdir(exist_ok=True)
        self.rules_file = self.data_dir / "stop_loss_rules.json"
        self.executions_file = self.data_dir / "stop_loss_executions.json"

        # 손절 규칙과 실행 기록
        self.stop_loss_rules: Dict[str, StopLossRule] = {}
        self.executions: List[StopLossExecution] = []

        # 모니터링 설정
        self.monitoring_enabled = False
        self.monitoring_interval = 5  # 5초마다 체크
        self.monitoring_task = None

        # 안전 장치
        self.max_executions_per_minute = 10
        self.recent_executions = []

        # 로드 기존 데이터
        self._load_rules()
        self._load_executions()

    async def add_stop_loss_rule(
        self,
        stock_code: str,
        stock_name: str,
        purchase_price: float,
        quantity: int,
        stop_loss_pct: float = -5.0,  # 기본 5% 손절
        rule_type: str = "PERCENTAGE"
    ) -> bool:
        """
        손절 규칙 추가

        Args:
            stock_code: 종목 코드
            stock_name: 종목명
            purchase_price: 매수가
            quantity: 수량
            stop_loss_pct: 손절 비율 (음수, 예: -5.0)
            rule_type: 규칙 타입

        Returns:
            규칙 추가 성공 여부
        """
        try:
            # 손절가 계산
            stop_loss_price = purchase_price * (1 + stop_loss_pct / 100)

            # 규칙 생성
            rule = StopLossRule(
                stock_code=stock_code,
                stock_name=stock_name,
                stop_loss_price=stop_loss_price,
                stop_loss_pct=stop_loss_pct,
                purchase_price=purchase_price,
                quantity=quantity,
                rule_type=rule_type
            )

            self.stop_loss_rules[stock_code] = rule

            # 저장
            await self._save_rules()

            self.logger.info(
                f"✅ 손절 규칙 추가: {stock_name}({stock_code}) "
                f"매수가: {purchase_price:,.0f}원, 손절가: {stop_loss_price:,.0f}원 "
                f"({stop_loss_pct:.1f}%)"
            )

            if self.console:
                self.console.print(
                    f"[green]✅ 손절 규칙 추가: {stock_name}({stock_code})[/green]\n"
                    f"매수가: ₩{purchase_price:,.0f} → 손절가: ₩{stop_loss_price:,.0f} ({stop_loss_pct:.1f}%)"
                )

            return True

        except Exception as e:
            self.logger.error(f"❌ 손절 규칙 추가 실패: {e}")
            return False

    async def remove_stop_loss_rule(self, stock_code: str) -> bool:
        """손절 규칙 제거"""
        try:
            if stock_code in self.stop_loss_rules:
                rule = self.stop_loss_rules.pop(stock_code)
                await self._save_rules()

                self.logger.info(f"🗑️ 손절 규칙 제거: {rule.stock_name}({stock_code})")

                if self.console:
                    self.console.print(f"[yellow]🗑️ 손절 규칙 제거: {rule.stock_name}({stock_code})[/yellow]")

                return True
            else:
                self.logger.warning(f"⚠️ 손절 규칙을 찾을 수 없음: {stock_code}")
                return False

        except Exception as e:
            self.logger.error(f"❌ 손절 규칙 제거 실패: {e}")
            return False

    async def start_monitoring(self):
        """손절 모니터링 시작"""
        try:
            if self.monitoring_enabled:
                self.logger.warning("⚠️ 이미 모니터링이 실행 중입니다")
                return

            self.monitoring_enabled = True
            self.monitoring_task = asyncio.create_task(self._monitoring_loop())

            self.logger.info("🚀 자동 손절 모니터링 시작")

            if self.console:
                self.console.print(Panel.fit(
                    "🚀 자동 손절 모니터링 시작\n"
                    f"모니터링 대상: {len(self.stop_loss_rules)}개 종목\n"
                    f"체크 간격: {self.monitoring_interval}초",
                    style="bold green"
                ))

        except Exception as e:
            self.logger.error(f"❌ 모니터링 시작 실패: {e}")
            self.monitoring_enabled = False

    async def stop_monitoring(self):
        """손절 모니터링 중지"""
        try:
            self.monitoring_enabled = False

            if self.monitoring_task and not self.monitoring_task.done():
                self.monitoring_task.cancel()
                try:
                    await self.monitoring_task
                except asyncio.CancelledError:
                    pass

            self.logger.info("⏹️ 자동 손절 모니터링 중지")

            if self.console:
                self.console.print("[yellow]⏹️ 자동 손절 모니터링 중지[/yellow]")

        except Exception as e:
            self.logger.error(f"❌ 모니터링 중지 실패: {e}")

    async def _monitoring_loop(self):
        """모니터링 루프"""
        try:
            while self.monitoring_enabled:
                try:
                    # 손절 조건 체크
                    await self._check_stop_loss_conditions()

                    # 체크 간격 대기
                    await asyncio.sleep(self.monitoring_interval)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    self.logger.error(f"❌ 모니터링 루프 오류: {e}")
                    await asyncio.sleep(self.monitoring_interval)

        except asyncio.CancelledError:
            self.logger.info("📱 모니터링 루프가 취소되었습니다")
        except Exception as e:
            self.logger.error(f"❌ 모니터링 루프 실패: {e}")

    async def _check_stop_loss_conditions(self):
        """손절 조건 체크"""
        try:
            if not self.stop_loss_rules:
                return

            # 현재 보유 종목 조회
            current_holdings = await self._get_current_holdings()
            if not current_holdings:
                return

            # 각 손절 규칙 확인
            for stock_code, rule in self.stop_loss_rules.items():
                try:
                    # 현재가 조회
                    current_price = await self._get_current_price(stock_code)
                    if not current_price:
                        continue

                    # 손절 조건 확인
                    if current_price <= rule.stop_loss_price:
                        self.logger.warning(
                            f"🚨 손절 조건 감지: {rule.stock_name}({stock_code}) "
                            f"현재가: {current_price:,.0f}원 ≤ 손절가: {rule.stop_loss_price:,.0f}원"
                        )

                        # 실제 보유 수량 확인
                        actual_quantity = self._get_holding_quantity(current_holdings, stock_code)
                        if actual_quantity > 0:
                            # 즉시 손절 실행
                            await self._execute_stop_loss(rule, current_price, actual_quantity)
                        else:
                            # 보유하지 않은 종목의 손절 규칙 제거
                            self.logger.info(f"📋 보유하지 않은 종목의 손절 규칙 제거: {stock_code}")
                            await self.remove_stop_loss_rule(stock_code)

                    # 마지막 체크 시간 업데이트
                    rule.last_checked = datetime.now()

                except Exception as e:
                    self.logger.error(f"❌ {stock_code} 손절 조건 체크 실패: {e}")

        except Exception as e:
            self.logger.error(f"❌ 손절 조건 체크 실패: {e}")

    async def _execute_stop_loss(self, rule: StopLossRule, trigger_price: float, actual_quantity: int):
        """손절 실행"""
        try:
            # 안전 장치 체크
            if not await self._check_safety_limits():
                self.logger.warning("⚠️ 안전 장치로 인해 손절 실행을 연기합니다")
                return

            self.logger.info(f"💰 손절 실행 시작: {rule.stock_name}({rule.stock_code})")

            # 실행 기록 생성
            execution = StopLossExecution(
                stock_code=rule.stock_code,
                stock_name=rule.stock_name,
                trigger_price=trigger_price,
                stop_loss_price=rule.stop_loss_price,
                quantity=actual_quantity
            )

            try:
                # 실제 매도 주문 실행
                if self.trading_handler:
                    # 시장가 매도 주문
                    result = await self.trading_handler.sell_stock(
                        stock_code=rule.stock_code,
                        quantity=actual_quantity,
                        order_type="MARKET",
                        reason="자동손절"
                    )

                    if result and result.get("success"):
                        execution.order_id = result.get("order_id")
                        execution.status = "SUBMITTED"
                        execution.execution_price = result.get("price", trigger_price)
                        execution.execution_amount = execution.execution_price * actual_quantity

                        self.logger.info(
                            f"✅ 손절 주문 체결: {rule.stock_name} "
                            f"{actual_quantity}주 @ {execution.execution_price:,.0f}원"
                        )

                        if self.console:
                            self.console.print(
                                Panel.fit(
                                    f"🚨 자동 손절 실행 완료\n\n"
                                    f"종목: {rule.stock_name}({rule.stock_code})\n"
                                    f"수량: {actual_quantity:,}주\n"
                                    f"체결가: ₩{execution.execution_price:,.0f}\n"
                                    f"체결금액: ₩{execution.execution_amount:,.0f}",
                                    style="bold red"
                                )
                            )
                    else:
                        execution.status = "FAILED"
                        execution.error_message = result.get("message", "매도 주문 실패")
                        self.logger.error(f"❌ 손절 주문 실패: {execution.error_message}")

                else:
                    # 데모 모드
                    execution.status = "FILLED"
                    execution.execution_price = trigger_price * 0.99  # 1% 슬리피지
                    execution.execution_amount = execution.execution_price * actual_quantity

                    self.logger.info(
                        f"✅ [데모] 손절 실행: {rule.stock_name} "
                        f"{actual_quantity}주 @ {execution.execution_price:,.0f}원"
                    )

                # 실행 기록 저장
                self.executions.append(execution)
                await self._save_executions()

                # 안전 장치용 최근 실행 기록
                self.recent_executions.append(datetime.now())

                # 성공적으로 실행된 경우 손절 규칙 제거
                if execution.status in ["SUBMITTED", "FILLED"]:
                    await self.remove_stop_loss_rule(rule.stock_code)

            except Exception as e:
                execution.status = "FAILED"
                execution.error_message = str(e)
                self.executions.append(execution)
                await self._save_executions()

                self.logger.error(f"❌ 손절 실행 중 오류: {e}")

        except Exception as e:
            self.logger.error(f"❌ 손절 실행 실패: {e}")

    async def _get_current_holdings(self) -> List[Dict[str, Any]]:
        """현재 보유 종목 조회"""
        try:
            if self.trading_handler:
                response = await self.trading_handler.get_balance()
                return response.get('holdings', [])

            # 데모 데이터
            return [
                {
                    'stock_code': '005930',
                    'quantity': 100,
                    'current_price': 72000
                },
                {
                    'stock_code': '000660',
                    'quantity': 50,
                    'current_price': 115000
                }
            ]

        except Exception as e:
            self.logger.error(f"❌ 보유 종목 조회 실패: {e}")
            return []

    async def _get_current_price(self, stock_code: str) -> Optional[float]:
        """현재가 조회"""
        try:
            if self.trading_handler and hasattr(self.trading_handler, 'get_current_price'):
                return await self.trading_handler.get_current_price(stock_code)

            # 데모 데이터
            demo_prices = {
                '005930': 71500,  # 삼성전자 (손절가 71250 이하)
                '000660': 113000  # SK하이닉스 (손절가 114000 이하)
            }

            return demo_prices.get(stock_code)

        except Exception as e:
            self.logger.error(f"❌ {stock_code} 현재가 조회 실패: {e}")
            return None

    def _get_holding_quantity(self, holdings: List[Dict[str, Any]], stock_code: str) -> int:
        """보유 수량 조회"""
        for holding in holdings:
            if holding.get('stock_code') == stock_code:
                return holding.get('quantity', 0)
        return 0

    async def _check_safety_limits(self) -> bool:
        """안전 장치 체크"""
        try:
            # 최근 1분간 실행 횟수 체크
            now = datetime.now()
            one_minute_ago = now - timedelta(minutes=1)

            recent_count = len([
                exec_time for exec_time in self.recent_executions
                if exec_time > one_minute_ago
            ])

            if recent_count >= self.max_executions_per_minute:
                self.logger.warning(f"⚠️ 안전 장치: 최근 1분간 실행 횟수 초과 ({recent_count}회)")
                return False

            # 오래된 기록 정리
            self.recent_executions = [
                exec_time for exec_time in self.recent_executions
                if exec_time > one_minute_ago
            ]

            return True

        except Exception as e:
            self.logger.error(f"❌ 안전 장치 체크 실패: {e}")
            return False

    async def get_status_summary(self) -> Dict[str, Any]:
        """상태 요약 정보"""
        try:
            total_rules = len(self.stop_loss_rules)
            active_rules = len([r for r in self.stop_loss_rules.values() if r.is_active])
            total_executions = len(self.executions)
            successful_executions = len([e for e in self.executions if e.status == "FILLED"])

            return {
                "monitoring_enabled": self.monitoring_enabled,
                "total_rules": total_rules,
                "active_rules": active_rules,
                "total_executions": total_executions,
                "successful_executions": successful_executions,
                "success_rate": (successful_executions / total_executions * 100) if total_executions > 0 else 0,
                "last_check": max([r.last_checked for r in self.stop_loss_rules.values() if r.last_checked], default=None)
            }

        except Exception as e:
            self.logger.error(f"❌ 상태 요약 생성 실패: {e}")
            return {}

    async def display_status(self):
        """상태 표시"""
        try:
            summary = await self.get_status_summary()

            if self.console:
                # 상태 패널
                status_text = "🟢 모니터링 활성" if self.monitoring_enabled else "🔴 모니터링 비활성"

                self.console.print(Panel.fit(
                    f"🛡️ 자동 손절 시스템 상태\n\n"
                    f"상태: {status_text}\n"
                    f"등록된 규칙: {summary.get('total_rules', 0)}개\n"
                    f"활성 규칙: {summary.get('active_rules', 0)}개\n"
                    f"총 실행: {summary.get('total_executions', 0)}회\n"
                    f"성공률: {summary.get('success_rate', 0):.1f}%",
                    style="bold blue"
                ))

                # 규칙 테이블
                if self.stop_loss_rules:
                    rules_table = Table(title="손절 규칙 목록")
                    rules_table.add_column("종목", style="cyan")
                    rules_table.add_column("매수가", style="yellow", justify="right")
                    rules_table.add_column("손절가", style="red", justify="right")
                    rules_table.add_column("손절율", style="red", justify="right")
                    rules_table.add_column("수량", style="magenta", justify="right")
                    rules_table.add_column("상태", style="bold")

                    for rule in self.stop_loss_rules.values():
                        status_text = "🟢 활성" if rule.is_active else "🔴 비활성"

                        rules_table.add_row(
                            f"{rule.stock_name}\n({rule.stock_code})",
                            f"₩{rule.purchase_price:,.0f}",
                            f"₩{rule.stop_loss_price:,.0f}",
                            f"{rule.stop_loss_pct:.1f}%",
                            f"{rule.quantity:,}주",
                            status_text
                        )

                    self.console.print(rules_table)

        except Exception as e:
            self.logger.error(f"❌ 상태 표시 실패: {e}")

    async def _save_rules(self):
        """손절 규칙 저장"""
        try:
            rules_data = []
            for rule in self.stop_loss_rules.values():
                rule_data = {
                    "stock_code": rule.stock_code,
                    "stock_name": rule.stock_name,
                    "stop_loss_price": rule.stop_loss_price,
                    "stop_loss_pct": rule.stop_loss_pct,
                    "purchase_price": rule.purchase_price,
                    "quantity": rule.quantity,
                    "rule_type": rule.rule_type,
                    "is_active": rule.is_active,
                    "created_at": rule.created_at.isoformat(),
                    "last_checked": rule.last_checked.isoformat() if rule.last_checked else None
                }
                rules_data.append(rule_data)

            with open(self.rules_file, 'w', encoding='utf-8') as f:
                json.dump(rules_data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            self.logger.error(f"❌ 손절 규칙 저장 실패: {e}")

    async def _save_executions(self):
        """실행 기록 저장"""
        try:
            executions_data = []
            for execution in self.executions[-100:]:  # 최근 100개만 저장
                execution_data = {
                    "stock_code": execution.stock_code,
                    "stock_name": execution.stock_name,
                    "trigger_price": execution.trigger_price,
                    "stop_loss_price": execution.stop_loss_price,
                    "quantity": execution.quantity,
                    "order_id": execution.order_id,
                    "execution_price": execution.execution_price,
                    "execution_amount": execution.execution_amount,
                    "status": execution.status,
                    "executed_at": execution.executed_at.isoformat(),
                    "error_message": execution.error_message
                }
                executions_data.append(execution_data)

            with open(self.executions_file, 'w', encoding='utf-8') as f:
                json.dump(executions_data, f, ensure_ascii=False, indent=2)

        except Exception as e:
            self.logger.error(f"❌ 실행 기록 저장 실패: {e}")

    def _load_rules(self):
        """손절 규칙 로드"""
        try:
            if self.rules_file.exists():
                with open(self.rules_file, 'r', encoding='utf-8') as f:
                    rules_data = json.load(f)

                for rule_data in rules_data:
                    rule = StopLossRule(
                        stock_code=rule_data["stock_code"],
                        stock_name=rule_data["stock_name"],
                        stop_loss_price=rule_data["stop_loss_price"],
                        stop_loss_pct=rule_data["stop_loss_pct"],
                        purchase_price=rule_data["purchase_price"],
                        quantity=rule_data["quantity"],
                        rule_type=rule_data["rule_type"],
                        is_active=rule_data["is_active"],
                        created_at=datetime.fromisoformat(rule_data["created_at"]),
                        last_checked=datetime.fromisoformat(rule_data["last_checked"]) if rule_data["last_checked"] else None
                    )
                    self.stop_loss_rules[rule.stock_code] = rule

                self.logger.info(f"✅ 손절 규칙 {len(self.stop_loss_rules)}개 로드 완료")

        except Exception as e:
            self.logger.error(f"❌ 손절 규칙 로드 실패: {e}")

    def _load_executions(self):
        """실행 기록 로드"""
        try:
            if self.executions_file.exists():
                with open(self.executions_file, 'r', encoding='utf-8') as f:
                    executions_data = json.load(f)

                for execution_data in executions_data:
                    execution = StopLossExecution(
                        stock_code=execution_data["stock_code"],
                        stock_name=execution_data["stock_name"],
                        trigger_price=execution_data["trigger_price"],
                        stop_loss_price=execution_data["stop_loss_price"],
                        quantity=execution_data["quantity"],
                        order_id=execution_data["order_id"],
                        execution_price=execution_data["execution_price"],
                        execution_amount=execution_data["execution_amount"],
                        status=execution_data["status"],
                        executed_at=datetime.fromisoformat(execution_data["executed_at"]),
                        error_message=execution_data["error_message"]
                    )
                    self.executions.append(execution)

                self.logger.info(f"✅ 실행 기록 {len(self.executions)}개 로드 완료")

        except Exception as e:
            self.logger.error(f"❌ 실행 기록 로드 실패: {e}")

# 사용 예시
async def main():
    """테스트 함수"""
    try:
        # 자동 손절 시스템 초기화
        stop_loss_system = AutoStopLossSystem()

        # 손절 규칙 추가
        await stop_loss_system.add_stop_loss_rule(
            stock_code="005930",
            stock_name="삼성전자",
            purchase_price=75000,
            quantity=100,
            stop_loss_pct=-5.0  # 5% 손절
        )

        await stop_loss_system.add_stop_loss_rule(
            stock_code="000660",
            stock_name="SK하이닉스",
            purchase_price=120000,
            quantity=50,
            stop_loss_pct=-5.0  # 5% 손절
        )

        # 상태 표시
        await stop_loss_system.display_status()

        # 모니터링 시작
        await stop_loss_system.start_monitoring()

        # 10초간 모니터링 (데모)
        await asyncio.sleep(10)

        # 모니터링 중지
        await stop_loss_system.stop_monitoring()

    except KeyboardInterrupt:
        print("\n👋 사용자가 중단했습니다.")
    except Exception as e:
        print(f"❌ 실행 중 오류 발생: {e}")

if __name__ == "__main__":
    # 로깅 설정
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # 테스트 실행
    asyncio.run(main())