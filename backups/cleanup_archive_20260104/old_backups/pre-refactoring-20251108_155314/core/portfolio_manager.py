#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portfolio_manager.py

포트폴리오 관리자 - 정리 전략 통합
"""

import asyncio
from typing import Dict, List, Any
from utils.logger import get_logger
from strategies.portfolio_cleanup_strategy import PortfolioCleanupStrategy
from strategies.advanced_exit_strategy import AdvancedExitStrategy

class PortfolioManager:
    """포트폴리오 통합 관리자"""

    def __init__(self, trading_handler=None, config=None):
        self.logger = get_logger("PortfolioManager")
        self.trading_handler = trading_handler
        self.config = config

        # 전략 인스턴스
        self.cleanup_strategy = PortfolioCleanupStrategy(config)
        self.exit_strategy = AdvancedExitStrategy(config)

    async def analyze_and_cleanup_portfolio(self) -> Dict[str, Any]:
        """포트폴리오 분석 및 정리 실행"""
        try:
            self.logger.info("포트폴리오 정리 분석 시작")

            # 현재 보유 종목 조회
            holdings = await self._get_current_holdings()
            if not holdings:
                self.logger.warning("📋 현재 보유 중인 종목이 없습니다. 자동매도할 종목이 없습니다.")
                return {
                    "status": "no_holdings",
                    "message": "현재 보유 중인 종목이 없어 정리할 내용이 없습니다.",
                    "recommendations": [
                        "1. KIS API 연동 상태를 확인해주세요",
                        "2. 실제 보유 종목이 있는지 HTS에서 확인해주세요",
                        "3. 모의투자/실거래 모드가 올바른지 확인해주세요",
                        "4. 보유 종목이 있다면 거래시간 중인지 확인해주세요"
                    ]
                }

            # 정리 계획 생성
            cleanup_plan = await self.cleanup_strategy.generate_cleanup_plan(holdings)

            # 실행 가능한 신호만 필터링
            executable_signals = [
                signal for signal in cleanup_plan['signals']
                if signal['priority'] <= 2  # 우선순위 1,2만 실행
            ]

            result = {
                "status": "analyzed",
                "cleanup_plan": cleanup_plan,
                "executable_signals": len(executable_signals),
                "execution_results": []
            }

            # 자동 실행 (실거래 모드일 때만) - TRADING_ENABLED 확인 강화
            trading_enabled = False
            if self.config:
                # config 객체의 다양한 형태 지원
                if hasattr(self.config, 'trading') and hasattr(self.config.trading, 'TRADING_ENABLED'):
                    trading_enabled = self.config.trading.TRADING_ENABLED
                elif hasattr(self.config, 'TRADING_ENABLED'):
                    trading_enabled = self.config.TRADING_ENABLED
                else:
                    trading_enabled = getattr(self.config, 'TRADING_ENABLED', False)

            self.logger.info(f"💰 매매 모드 확인: {trading_enabled} (config: {type(self.config)})")

            if trading_enabled and executable_signals:
                self.logger.info(f"🚀 자동매도 실행: {len(executable_signals)}개 신호")
                execution_results = await self._execute_cleanup_signals(executable_signals)
                result["execution_results"] = execution_results
                result["status"] = "executed"
            elif not trading_enabled:
                self.logger.warning("⚠️ 매매 모드가 비활성화되어 있어 자동매도를 건너뜁니다")
            elif not executable_signals:
                self.logger.info("ℹ️ 실행할 매도 신호가 없습니다")

            return result

        except Exception as e:
            self.logger.error(f"포트폴리오 정리 실패: {e}")
            return {"status": "error", "message": str(e)}

    async def _get_current_holdings(self) -> List[Dict[str, Any]]:
        """현재 보유 종목 조회"""
        try:
            if not self.trading_handler:
                self.logger.warning("거래 핸들러가 초기화되지 않았습니다. 시스템 설정을 확인해주세요.")
                return []

            # 보유 종목 조회
            holdings_response = await self.trading_handler.get_balance()
            self.logger.info(f"📞 get_balance() 응답: {holdings_response}")

            if not holdings_response or not holdings_response.get('success'):
                self.logger.error(f"보유 종목 조회 실패: {holdings_response}")
                return []

            holdings_data = holdings_response.get('data', [])
            if not holdings_data:
                self.logger.warning("📋 holdings_data가 비어있음 - 상위 호출자에서 처리")
                return []

            # 디버깅: holdings_data 내용 상세 로깅
            self.logger.info(f"📊 보유 종목 원본 데이터 {len(holdings_data)}개 조회 완료")

            # 첫 번째 종목 데이터 샘플 출력 (디버깅용) - 더 자세하게
            if holdings_data:
                sample = holdings_data[0]
                self.logger.info(f"🔍 첫 번째 종목 데이터 샘플 (처음 5개 필드만): {dict(list(sample.items())[:5]) if isinstance(sample, dict) else sample}")
                self.logger.info(f"🔍 사용 가능한 모든 필드들 ({len(sample.keys()) if isinstance(sample, dict) else 0}개): {list(sample.keys()) if isinstance(sample, dict) else 'dict가 아님'}")

                # 수량 관련 필드만 별도 로깅
                if isinstance(sample, dict):
                    qty_fields = {k: v for k, v in sample.items() if 'qty' in k.lower() or 'quantity' in k.lower() or 'psbl' in k.lower()}
                    self.logger.info(f"🔢 수량 관련 필드들: {qty_fields}")

                    # 종목 정보 필드들
                    symbol_fields = {k: v for k, v in sample.items() if any(x in k.lower() for x in ['pdno', 'symbol', 'code', 'name', 'prdt'])}
                    self.logger.info(f"📈 종목 정보 필드들: {symbol_fields}")

                    # 전체 데이터 로깅 (디버깅용)
                    self.logger.info(f"🔍 첫 번째 종목 전체 데이터: {sample}")

            # 모든 종목의 수량 상태 확인
            self.logger.info("🔍 전체 종목 수량 분석:")
            for i, holding in enumerate(holdings_data[:5]):  # 처음 5개만
                if isinstance(holding, dict):
                    symbol = holding.get('pdno', holding.get('symbol', f'item_{i}'))
                    qty_fields = {k: v for k, v in holding.items() if 'qty' in k.lower() or 'quantity' in k.lower()}
                    self.logger.info(f"  종목 {i+1}: {symbol} - 수량 필드들: {qty_fields}")

            # ✅ 강화된 보유수량 필터링 로직 - KIS API 응답 형식 다양성 완전 대응
            actual_holdings = []
            for i, holding in enumerate(holdings_data):
                quantity, found_field = self._extract_quantity_safely(holding)
                symbol = self._extract_symbol_safely(holding, i)

                # 상세 디버깅 정보 (필요시만 출력)
                if self.logger.isEnabledFor(10):  # DEBUG 레벨일 때만
                    quantity_fields = ['hldg_qty', 'quantity', 'qty', 'holding_qty', 'pchs_qty', 'ord_psbl_qty', 'sellable_qty']
                    debug_info = {field: holding.get(field) for field in quantity_fields if field in holding}
                    self.logger.debug(f"🔢 종목 {symbol}: 수량={quantity} (필드: {found_field}) | 전체: {debug_info}")

                if quantity > 0:
                    self.logger.info(f"✅ 보유 종목: {symbol} ({quantity}주)")
                    # 표준화된 형태로 수량 정보 추가
                    holding_copy = holding.copy()
                    holding_copy['_normalized_quantity'] = quantity
                    holding_copy['_quantity_field'] = found_field
                    actual_holdings.append(holding_copy)
                else:
                    self.logger.debug(f"❌ 수량 0: {symbol}")

            if actual_holdings:
                self.logger.info(f"✅ 실제 보유 종목 {len(actual_holdings)}개 (수량 > 0)")
            else:
                self.logger.warning("⚠️ 모든 종목의 보유수량이 0입니다. 실제 보유 종목이 없습니다.")
                # 원본 데이터 샘플 로깅 (디버깅용)
                if holdings_data:
                    sample = holdings_data[0]
                    self.logger.warning(f"❓ 데이터 샘플 (수량이 0인 이유 확인용): {sample}")
                    self.logger.warning(f"❓ 데이터 타입 정보: {type(sample)} - {list(sample.keys()) if isinstance(sample, dict) else 'dict가 아님'}")

                    # 모든 수량 필드 값 확인
                    quantity_fields = ['hldg_qty', 'quantity', 'qty', 'holding_qty']
                    for field in quantity_fields:
                        if field in sample:
                            value = sample[field]
                            self.logger.warning(f"❓ {field} = {value} (타입: {type(value)})")

            return actual_holdings

        except Exception as e:
            self.logger.error(f"보유 종목 조회 실패: {e}")
            return []

    def _extract_quantity_safely(self, holding: Dict[str, Any]) -> tuple[int, str]:
        """안전하게 보유수량 추출 - KIS API 응답 형식 다양성 대응"""
        # 확장된 수량 필드명 목록 (우선순위 순) - KIS API 실제 필드명 기준으로 강화
        quantity_fields = [
            # KIS API 표준 필드명
            'hldg_qty',          # 보유수량 (KIS API 주요)
            'ord_psbl_qty',      # 주문가능수량 (실제 매도 가능)
            'sellable_qty',      # 매도가능수량
            'pchs_qty',          # 매수수량
            'psbl_qty',          # 가능수량

            # 일반적인 필드명
            'quantity',          # 일반 수량
            'qty',               # 축약 수량
            'holding_qty',       # 보유 수량
            'balance_qty',       # 잔고 수량
            'own_qty',           # 보유 수량 (다른 표현)

            # 추가 가능한 필드명 (fallback)
            'current_qty',       # 현재 수량
            'stock_qty',         # 주식 수량
        ]

        # 디버깅: 사용 가능한 모든 필드를 로깅 (DEBUG 레벨)
        if self.logger.isEnabledFor(10):  # DEBUG 레벨
            available_fields = list(holding.keys())
            self.logger.debug(f"🔍 사용 가능한 모든 필드: {available_fields}")

        for field in quantity_fields:
            if field in holding:
                try:
                    qty_val = holding[field]

                    # None 체크
                    if qty_val is None:
                        continue

                    # 문자열인 경우 숫자 변환 시도 (쉼표, 공백 제거)
                    if isinstance(qty_val, str):
                        qty_val = qty_val.replace(',', '').replace(' ', '').strip()
                        if not qty_val or qty_val == '' or qty_val == '0':
                            continue

                    quantity = int(float(qty_val))
                    if quantity > 0:
                        self.logger.debug(f"✅ 수량 추출 성공: {field} = {quantity}")
                        return quantity, field
                    else:
                        self.logger.debug(f"❌ 수량이 0: {field} = {quantity}")

                except (ValueError, TypeError, AttributeError) as e:
                    self.logger.debug(f"❌ 수량 변환 실패: {field} = {holding[field]} ({e})")
                    continue

        # 모든 필드에서 수량을 찾지 못한 경우 경고 로깅
        self.logger.warning(f"⚠️ 보유수량 필드를 찾을 수 없음. 시도한 필드: {quantity_fields}")
        self.logger.warning(f"⚠️ 실제 데이터 필드: {list(holding.keys()) if isinstance(holding, dict) else 'dict가 아님'}")

        return 0, 'none'

    def _extract_symbol_safely(self, holding: Dict[str, Any], index: int) -> str:
        """안전하게 종목코드 추출"""
        symbol_fields = ['pdno', 'symbol', 'code', 'stock_code']

        for field in symbol_fields:
            if field in holding and holding[field]:
                return str(holding[field])

        return f'Unknown_{index}'

    async def _execute_cleanup_signals(self, signals: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """정리 신호 실행"""
        execution_results = []

        for signal in signals:
            try:
                result = await self._execute_single_signal(signal)
                execution_results.append({
                    "signal": signal,
                    "execution_result": result
                })

                # 실행 간격 (과도한 API 호출 방지)
                await asyncio.sleep(1.0)

            except Exception as e:
                self.logger.error(f"신호 실행 실패 {signal['symbol']}: {e}")
                execution_results.append({
                    "signal": signal,
                    "execution_result": {"success": False, "error": str(e)}
                })

        return execution_results

    async def _execute_single_signal(self, signal: Dict[str, Any]) -> Dict[str, Any]:
        """단일 신호 실행"""
        try:
            symbol = signal['symbol']
            quantity_ratio = signal['quantity_ratio']

            if not self.trading_handler:
                return {"success": False, "error": "거래 핸들러가 없습니다"}

            # 현재 보유 수량 확인 (표준화된 방식) - 신호 실행 시점에 재조회
            holdings = await self._get_current_holdings()
            self.logger.info(f"🔍 매도 실행 전 보유종목 재조회: {len(holdings)}개 종목")
            current_holding = None

            # 종목 찾기 (다양한 필드명 지원)
            for holding in holdings:
                if (holding.get('pdno') == symbol or
                    holding.get('symbol') == symbol or
                    holding.get('code') == symbol):
                    current_holding = holding
                    break

            if not current_holding:
                # 디버깅을 위한 상세 로그
                self.logger.error(f"❌ 보유 종목 {symbol}을 찾을 수 없습니다")
                self.logger.error(f"🔍 현재 보유 종목 목록 ({len(holdings)}개):")
                for i, holding in enumerate(holdings[:5]):  # 최대 5개까지만
                    symbols = {
                        'pdno': holding.get('pdno', 'N/A'),
                        'symbol': holding.get('symbol', 'N/A'),
                        'code': holding.get('code', 'N/A')
                    }
                    quantity = holding.get('_normalized_quantity', holding.get('hldg_qty', holding.get('quantity', 0)))
                    self.logger.error(f"  [{i+1}] {symbols}, qty={quantity}")

                # 실제로는 보유하지 않은 종목에 대한 매도 신호가 생성된 경우
                # 이는 신호 생성과 실행 간의 시간차나 데이터 불일치로 발생할 수 있음
                self.logger.warning(f"⚠️ 종목 {symbol}: 매도 신호는 있지만 현재 보유하지 않음 (이미 매도됨 또는 데이터 불일치)")
                return {"success": False, "error": f"보유 종목 {symbol}을 찾을 수 없습니다", "quantity": 0}

            # 표준화된 수량 정보 사용
            current_qty = current_holding.get('_normalized_quantity', 0)
            if current_qty == 0:
                # 백업: 직접 추출 시도
                current_qty, _ = self._extract_quantity_safely(current_holding)

            # 매도 수량 계산 (반올림 + 최소 1주 보장)
            calculated_qty = current_qty * quantity_ratio
            sell_qty = max(1, round(calculated_qty)) if calculated_qty > 0.1 else 0

            # 보유 수량을 초과하지 않도록 제한
            sell_qty = min(sell_qty, current_qty)

            # 디버깅을 위한 상세 로그
            self.logger.info(f"🔢 수량 계산: 보유수량={current_qty}, 매도비율={quantity_ratio:.3f}, 계산값={calculated_qty:.3f}, 최종수량={sell_qty}")

            if sell_qty <= 0:
                self.logger.error(f"❌ 매도 수량이 0 이하: {sell_qty} (보유: {current_qty}, 비율: {quantity_ratio})")
                return {"success": False, "error": "매도 수량이 0 이하입니다", "quantity": sell_qty}

            # 매도 주문 실행
            self.logger.info(f"🚀 매도 주문 실행: {symbol} {sell_qty}주 ({signal['reason']})")
            self.logger.info(f"   📊 현재 보유: {current_qty}주 → 매도: {sell_qty}주 (비율: {quantity_ratio:.1%})")

            order_result = await self.trading_handler.place_sell_order(
                symbol=symbol,
                quantity=sell_qty,
                price=None,  # 시장가
                order_type="market"
            )

            self.logger.info(f"📤 매도 주문 결과: {order_result}")

            result = {
                "success": order_result.get('success', False),
                "order_id": order_result.get('order_id'),
                "message": f"{symbol} {sell_qty}주 매도 주문 완료",
                "quantity": sell_qty,
                "reason": signal['reason']
            }

            # 결과 확인 로그
            self.logger.info(f"📊 실행 결과 생성: success={result['success']}, quantity={result['quantity']}")
            return result

        except Exception as e:
            self.logger.error(f"신호 실행 중 오류 {signal.get('symbol', 'Unknown')}: {e}")
            return {"success": False, "error": str(e), "quantity": 0}

    async def get_portfolio_status(self) -> Dict[str, Any]:
        """포트폴리오 현재 상태"""
        try:
            holdings = await self._get_current_holdings()

            if not holdings:
                return {
                    "status": "empty",
                    "holdings_count": 0,
                    "message": "현재 보유 중인 종목이 없습니다.",
                    "suggestions": [
                        "종목을 매수하거나 감시 목록에 종목을 추가해보세요",
                        "KIS API 연동 상태와 계좌 정보를 확인해주세요"
                    ]
                }

            # cleanup_strategy에 holdings 데이터를 먼저 분석시켜서 summary를 생성
            await self.cleanup_strategy.analyze_portfolio(holdings)
            summary = self.cleanup_strategy.get_portfolio_summary()

            return {
                "status": "active",
                "holdings_count": len(holdings),
                "summary": summary,
                "cleanup_recommendation": summary.get('cleanup_needed', False)
            }

        except Exception as e:
            self.logger.error(f"포트폴리오 상태 조회 실패: {e}")
            return {"status": "error", "message": str(e)}

if __name__ == "__main__":
    # 테스트 실행
    async def test_portfolio_manager():
        manager = PortfolioManager()
        status = await manager.get_portfolio_status()
        print(f"포트폴리오 상태: {status}")

    asyncio.run(test_portfolio_manager())