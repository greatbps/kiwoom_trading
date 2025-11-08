#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""상태 정의 시스템"""

class StatusDefinitions:
    STATUS_KOREAN_MAP = {
        "ACTIVE": "감시중", "monitoring": "감시중", "watching": "감시중",
        "RISK": "위험", "HIGH_RISK": "위험", "DANGER": "위험",
        "STOP_LOSS": "손절됨", "SOLD": "손절됨", "LIQUIDATED": "손절됨",
        "TARGET_ACHIEVED": "목표달성", "PROFIT_TAKEN": "목표달성", "COMPLETED": "목표달성"
    }
    
    STATUS_DESCRIPTIONS = {
        "감시중": {"color": "green", "icon": "👁️", "description": "정상 추적 중"},
        "위험": {"color": "red", "icon": "⚠️", "description": "손실 임계점 근접 (-5~-10%)"},
        "손절됨": {"color": "bright_red", "icon": "✂️", "description": "자동 손절 실행"},
        "목표달성": {"color": "bright_green", "icon": "🎯", "description": "목표 수익률 달성"}
    }
    
    @classmethod
    def get_korean_status(cls, status: str) -> str:
        return cls.STATUS_KOREAN_MAP.get(status.upper(), status)
    
    @classmethod
    def get_status_display(cls, status: str, include_icon: bool = True) -> str:
        korean_status = cls.get_korean_status(status)
        if include_icon:
            info = cls.STATUS_DESCRIPTIONS.get(korean_status, {})
            icon = info.get("icon", "")
            return f"{icon} {korean_status}" if icon else korean_status
        return korean_status
    
    @classmethod
    def get_status_color(cls, status: str) -> str:
        korean_status = cls.get_korean_status(status)
        info = cls.STATUS_DESCRIPTIONS.get(korean_status, {})
        return info.get("color", "white")

status_definitions = StatusDefinitions()
