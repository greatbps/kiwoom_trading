#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
reporting/telegram_notifier.py

Telegram 알림 시스템
- 매매 신호 알림
- 거래 체결 알림
- 일일/주간 리포트 알림
"""

import os
import asyncio
import logging
from typing import Optional, List, Dict, Any
from datetime import datetime

import aiohttp

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram 알림 발송기"""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_ids: Optional[List[str]] = None,
    ):
        """
        초기화

        Args:
            bot_token: Telegram Bot Token
            chat_ids: Chat ID 리스트
        """
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN")
        self.chat_ids = chat_ids or self._parse_chat_ids(os.getenv("TELEGRAM_CHAT_IDS", ""))

        if not self.bot_token:
            logger.warning("Telegram Bot Token이 설정되지 않았습니다.")

        self.api_url = f"https://api.telegram.org/bot{self.bot_token}"

        # 통계
        self.total_sent = 0
        self.total_failed = 0

    def _parse_chat_ids(self, chat_ids_str: str) -> List[str]:
        """Chat ID 문자열 파싱"""
        if not chat_ids_str:
            return []
        return [cid.strip() for cid in chat_ids_str.split(',') if cid.strip()]

    async def send_message(
        self,
        text: str,
        parse_mode: str = "Markdown",
        disable_notification: bool = False,
    ) -> bool:
        """
        메시지 전송

        Args:
            text: 메시지 텍스트
            parse_mode: 파싱 모드 ("Markdown" 또는 "HTML")
            disable_notification: 무음 알림 여부

        Returns:
            전송 성공 여부
        """
        if not self.bot_token or not self.chat_ids:
            logger.warning("Telegram 설정이 없습니다.")
            return False

        success = True

        for chat_id in self.chat_ids:
            try:
                url = f"{self.api_url}/sendMessage"
                payload = {
                    "chat_id": chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_notification": disable_notification,
                }

                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                        if response.status == 200:
                            self.total_sent += 1
                            logger.debug(f"Telegram 전송 성공: {chat_id}")
                        else:
                            self.total_failed += 1
                            logger.error(f"Telegram 전송 실패: {chat_id}, Status={response.status}")
                            success = False

            except Exception as e:
                self.total_failed += 1
                logger.error(f"Telegram 전송 오류 ({chat_id}): {e}")
                success = False

        return success

    async def notify_signal(
        self,
        symbol: str,
        symbol_name: str,
        strategy: str,
        confidence: float,
        price: float,
        **kwargs
    ):
        """
        매매 신호 알림

        Args:
            symbol: 종목 코드
            symbol_name: 종목명
            strategy: 전략명
            confidence: 확신도 (0~100)
            price: 현재가
            **kwargs: 추가 정보
        """
        # 아이콘 선택
        icon = "🔥" if confidence >= 80 else "📈" if confidence >= 60 else "📊"

        message = f"""
{icon} *매매 신호 발생*

📌 종목: `{symbol_name}` ({symbol})
🎯 전략: {strategy}
💯 확신도: *{confidence:.1f}%*
💰 현재가: {price:,}원

⏰ 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        # 추가 정보
        if 'reason' in kwargs:
            message += f"\n💡 사유: {kwargs['reason']}"

        await self.send_message(message)

    async def notify_trade_execution(
        self,
        symbol: str,
        symbol_name: str,
        side: str,  # 'BUY' or 'SELL'
        quantity: int,
        price: float,
        profit: Optional[float] = None,
    ):
        """
        거래 체결 알림

        Args:
            symbol: 종목 코드
            symbol_name: 종목명
            side: 매매 방향
            quantity: 수량
            price: 체결가
            profit: 손익 (매도 시)
        """
        if side == 'BUY':
            icon = "🛒"
            action = "매수"
        else:
            icon = "💸"
            action = "매도"

        message = f"""
{icon} *거래 체결 알림*

📌 종목: `{symbol_name}` ({symbol})
🔹 방향: {action}
📊 수량: {quantity}주
💰 체결가: {price:,}원

⏰ 시간: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        # 손익 정보 (매도 시)
        if profit is not None:
            profit_icon = "🎉" if profit > 0 else "😢"
            profit_text = f"+{profit:,.0f}원" if profit > 0 else f"{profit:,.0f}원"
            message += f"\n{profit_icon} 손익: *{profit_text}*"

        await self.send_message(message)

    async def notify_daily_report(
        self,
        report: Dict[str, Any],
    ):
        """
        일일 리포트 알림

        Args:
            report: 일일 리포트 데이터
        """
        summary = report.get('summary', {})

        message = f"""
📊 *일일 트레이딩 리포트*

📅 날짜: {report.get('date')}

📈 요약
• 총 거래: {summary.get('total_trades')}건
• 승리: {summary.get('winning_trades')}건
• 승률: *{summary.get('win_rate')}*
• 총 손익: *{summary.get('total_profit')}*
• 평균 손익: {summary.get('avg_profit')}
• Profit Factor: {summary.get('profit_factor')}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        await self.send_message(message)

    async def notify_weekly_report(
        self,
        report: Dict[str, Any],
    ):
        """
        주간 리포트 알림

        Args:
            report: 주간 리포트 데이터
        """
        summary = report.get('summary', {})

        message = f"""
📊 *주간 트레이딩 리포트*

📅 기간: {report.get('week_start')} ~ {report.get('week_end')}

📈 요약
• 총 거래: {summary.get('total_trades')}건
• 승리: {summary.get('winning_trades')}건
• 승률: *{summary.get('win_rate')}*
• 총 손익: *{summary.get('total_profit')}*

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        # 전략별 통계
        strategy_stats = report.get('strategy_stats', {})
        if strategy_stats:
            message += "\n\n🎯 전략별 성과\n"
            for strategy, stats in strategy_stats.items():
                win_rate = (stats['wins'] / stats['trades'] * 100) if stats['trades'] > 0 else 0.0
                message += f"• {strategy}: {stats['trades']}건, 승률 {win_rate:.1f}%\n"

        await self.send_message(message)

    async def notify_error(
        self,
        error_message: str,
        error_type: str = "시스템 오류",
    ):
        """
        오류 알림

        Args:
            error_message: 오류 메시지
            error_type: 오류 타입
        """
        message = f"""
⚠️ *{error_type}*

{error_message}

⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
"""

        await self.send_message(message, disable_notification=False)

    def get_stats(self) -> Dict[str, int]:
        """통계 반환"""
        return {
            'total_sent': self.total_sent,
            'total_failed': self.total_failed,
            'success_rate': (
                self.total_sent / (self.total_sent + self.total_failed) * 100
                if (self.total_sent + self.total_failed) > 0 else 0.0
            ),
        }
