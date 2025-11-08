#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Notification Manager
Telegram & Slack 알림 통합 관리
"""

import os
import requests
from typing import Optional, List
from datetime import datetime
from enum import Enum


class AlertLevel(Enum):
    """알림 레벨"""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class NotificationManager:
    """통합 알림 관리자"""

    def __init__(self):
        """환경변수에서 설정 로드"""
        # Telegram 설정
        self.telegram_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.telegram_chat_ids = self._parse_chat_ids(os.getenv('TELEGRAM_CHAT_IDS', ''))
        self.telegram_enabled = os.getenv('TELEGRAM_ENABLED', 'false').lower() == 'true'

        # Slack 설정
        self.slack_webhook = os.getenv('SLACK_WEBHOOK_URL')
        self.slack_enabled = os.getenv('SLACK_ENABLED', 'false').lower() == 'true'

        # 알림 레벨 설정
        alert_levels_str = os.getenv('ALERT_LEVELS', 'WARNING,ERROR,CRITICAL')
        self.alert_levels = [
            AlertLevel[level.strip()] for level in alert_levels_str.split(',')
            if level.strip() in AlertLevel.__members__
        ]

    def _parse_chat_ids(self, chat_ids_str: str) -> List[str]:
        """Chat ID 문자열 파싱"""
        if not chat_ids_str:
            return []
        return [cid.strip() for cid in chat_ids_str.split(',') if cid.strip()]

    def should_alert(self, level: AlertLevel) -> bool:
        """알림을 보내야 하는지 판단"""
        return level in self.alert_levels

    def send_alert(
        self,
        message: str,
        level: AlertLevel = AlertLevel.INFO,
        title: Optional[str] = None,
        markdown: bool = False
    ) -> dict:
        """
        알림 전송 (Telegram + Slack 통합)

        Args:
            message: 알림 메시지
            level: 알림 레벨
            title: 알림 제목 (선택)
            markdown: 마크다운 사용 여부

        Returns:
            전송 결과 딕셔너리
        """
        # 레벨 체크
        if not self.should_alert(level):
            return {'sent': False, 'reason': 'Level not in alert_levels'}

        results = {'telegram': None, 'slack': None}

        # Telegram 전송
        if self.telegram_enabled and self.telegram_token and self.telegram_chat_ids:
            results['telegram'] = self._send_telegram(message, level, title, markdown)

        # Slack 전송
        if self.slack_enabled and self.slack_webhook:
            results['slack'] = self._send_slack(message, level, title)

        return results

    def _send_telegram(
        self,
        message: str,
        level: AlertLevel,
        title: Optional[str],
        markdown: bool
    ) -> dict:
        """Telegram 전송"""
        if not self.telegram_token:
            return {'success': False, 'error': 'No token'}

        # 메시지 포맷팅
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        emoji = self._get_level_emoji(level)

        formatted_message = f"{emoji} *[{level.value}]*\n"
        if title:
            formatted_message += f"*{title}*\n\n"
        formatted_message += f"{message}\n\n"
        formatted_message += f"_Time: {timestamp}_"

        results = []
        for chat_id in self.telegram_chat_ids:
            try:
                url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
                data = {
                    'chat_id': chat_id,
                    'text': formatted_message,
                    'parse_mode': 'Markdown' if markdown else None
                }

                response = requests.post(url, json=data, timeout=5)
                results.append({
                    'chat_id': chat_id,
                    'success': response.status_code == 200,
                    'response': response.json() if response.status_code == 200 else None
                })
            except Exception as e:
                results.append({
                    'chat_id': chat_id,
                    'success': False,
                    'error': str(e)
                })

        return {'success': any(r['success'] for r in results), 'results': results}

    def _send_slack(
        self,
        message: str,
        level: AlertLevel,
        title: Optional[str]
    ) -> dict:
        """Slack 전송"""
        if not self.slack_webhook:
            return {'success': False, 'error': 'No webhook'}

        try:
            # Color mapping
            color_map = {
                AlertLevel.DEBUG: '#808080',      # Gray
                AlertLevel.INFO: '#36a64f',       # Green
                AlertLevel.WARNING: '#ff9900',    # Orange
                AlertLevel.ERROR: '#ff0000',      # Red
                AlertLevel.CRITICAL: '#8b0000'    # Dark Red
            }

            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            attachment = {
                'color': color_map.get(level, '#808080'),
                'title': title or f"Alert: {level.value}",
                'text': message,
                'footer': f"Trading System Alert • {timestamp}",
                'mrkdwn_in': ['text']
            }

            payload = {'attachments': [attachment]}

            response = requests.post(
                self.slack_webhook,
                json=payload,
                timeout=5
            )

            return {
                'success': response.status_code == 200,
                'status_code': response.status_code,
                'response': response.text
            }

        except Exception as e:
            return {'success': False, 'error': str(e)}

    def _get_level_emoji(self, level: AlertLevel) -> str:
        """레벨별 이모지 반환"""
        emoji_map = {
            AlertLevel.DEBUG: '🔍',
            AlertLevel.INFO: 'ℹ️',
            AlertLevel.WARNING: '⚠️',
            AlertLevel.ERROR: '❌',
            AlertLevel.CRITICAL: '🚨'
        }
        return emoji_map.get(level, 'ℹ️')

    # Convenience methods
    def info(self, message: str, title: Optional[str] = None):
        """INFO 레벨 알림"""
        return self.send_alert(message, AlertLevel.INFO, title)

    def warning(self, message: str, title: Optional[str] = None):
        """WARNING 레벨 알림"""
        return self.send_alert(message, AlertLevel.WARNING, title)

    def error(self, message: str, title: Optional[str] = None):
        """ERROR 레벨 알림"""
        return self.send_alert(message, AlertLevel.ERROR, title)

    def critical(self, message: str, title: Optional[str] = None):
        """CRITICAL 레벨 알림"""
        return self.send_alert(message, AlertLevel.CRITICAL, title)


# 전역 인스턴스 (싱글톤 패턴)
_notification_manager = None


def get_notification_manager() -> NotificationManager:
    """NotificationManager 싱글톤 인스턴스 가져오기"""
    global _notification_manager
    if _notification_manager is None:
        _notification_manager = NotificationManager()
    return _notification_manager


# 편의 함수들
def send_alert(message: str, level: AlertLevel = AlertLevel.INFO, title: Optional[str] = None):
    """전역 알림 전송 함수"""
    manager = get_notification_manager()
    return manager.send_alert(message, level, title)


def alert_info(message: str, title: Optional[str] = None):
    """INFO 알림"""
    return send_alert(message, AlertLevel.INFO, title)


def alert_warning(message: str, title: Optional[str] = None):
    """WARNING 알림"""
    return send_alert(message, AlertLevel.WARNING, title)


def alert_error(message: str, title: Optional[str] = None):
    """ERROR 알림"""
    return send_alert(message, AlertLevel.ERROR, title)


def alert_critical(message: str, title: Optional[str] = None):
    """CRITICAL 알림"""
    return send_alert(message, AlertLevel.CRITICAL, title)


if __name__ == "__main__":
    # 테스트 코드
    import sys

    print("Notification Manager 테스트")
    print("=" * 50)

    manager = NotificationManager()

    print(f"Telegram 활성화: {manager.telegram_enabled}")
    print(f"Slack 활성화: {manager.slack_enabled}")
    print(f"알림 레벨: {[l.value for l in manager.alert_levels]}")

    if len(sys.argv) > 1 and sys.argv[1] == '--test-send':
        print("\n테스트 알림 전송 중...")

        # INFO 테스트
        result = manager.info("테스트 INFO 메시지입니다.", "INFO 테스트")
        print(f"INFO 전송 결과: {result}")

        # WARNING 테스트
        result = manager.warning("유동성 게이트 실패: TEST001", "게이트 경고")
        print(f"WARNING 전송 결과: {result}")

        # ERROR 테스트
        result = manager.error("DB 연결 실패", "시스템 오류")
        print(f"ERROR 전송 결과: {result}")
    else:
        print("\n테스트 알림을 보내려면: python notification_manager.py --test-send")
