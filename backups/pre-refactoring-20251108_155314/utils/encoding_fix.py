#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/utils/encoding_fix.py

UTF-8 인코딩 문제 해결 유틸리티
"""

import sys
import os
import locale
from typing import Optional

def setup_utf8_environment():
    """시스템 전체 UTF-8 환경 설정"""
    
    # 환경 변수 설정
    os.environ['PYTHONIOENCODING'] = 'utf-8'
    os.environ['PYTHONUTF8'] = '1'
    
    # Windows에서 UTF-8 코드페이지 설정
    if sys.platform.startswith('win'):
        try:
            # 콘솔 인코딩을 UTF-8로 설정
            os.system('chcp 65001 >nul 2>&1')
        except:
            pass
    
    # Python 표준 스트림 재설정
    try:
        # stdout, stderr를 UTF-8로 재설정
        if hasattr(sys.stdout, 'reconfigure'):
            sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stderr, 'reconfigure'):
            sys.stderr.reconfigure(encoding='utf-8', errors='replace')
        if hasattr(sys.stdin, 'reconfigure'):
            sys.stdin.reconfigure(encoding='utf-8', errors='replace')
    except:
        pass
    
    # 로케일 설정
    try:
        locale.setlocale(locale.LC_ALL, 'ko_KR.UTF-8')
    except:
        try:
            locale.setlocale(locale.LC_ALL, 'Korean_Korea.UTF-8')
        except:
            try:
                locale.setlocale(locale.LC_ALL, 'C.UTF-8')
            except:
                pass

def safe_print(text: str, end: str = '\n', file=None):
    """안전한 출력 함수 (UTF-8 인코딩 오류 방지)"""
    if file is None:
        file = sys.stdout
    
    try:
        print(text, end=end, file=file)
    except UnicodeEncodeError:
        # UTF-8 인코딩 실패 시 ASCII로 변환
        try:
            ascii_text = text.encode('ascii', errors='replace').decode('ascii')
            print(ascii_text, end=end, file=file)
        except:
            # 최후의 수단: 에러 메시지만 출력
            print(f"[Encoding Error] Unable to display text", end=end, file=file)

def safe_format(text: str) -> str:
    """안전한 문자열 포맷팅 (UTF-8 문자 제거)"""
    if not text:
        return text
    
    try:
        # UTF-8 인코딩 테스트
        text.encode('cp949')
        return text
    except UnicodeEncodeError:
        # cp949로 인코딩할 수 없는 문자 제거
        safe_text = ""
        for char in text:
            try:
                char.encode('cp949')
                safe_text += char
            except UnicodeEncodeError:
                # 문제 문자를 대체
                if ord(char) > 127:
                    safe_text += "?"  # 또는 다른 대체 문자
                else:
                    safe_text += char
        return safe_text

def clean_unicode_emojis(text: str) -> str:
    """유니코드 이모지 제거 및 대체"""
    replacements = {
        '✅': '[OK]',
        '❌': '[ERROR]',
        '⚠️': '[WARN]',
        'ℹ️': '[INFO]',
        '🔄': '[PROC]',
        '⏳': '[WAIT]',
        '🚨': '[ALERT]',
        '🎯': '[TARGET]',
        '📊': '[DATA]',
        '💡': '[IDEA]',
        '⚡': '[FAST]',
        '🔍': '[SEARCH]',
        '🔧': '[CONFIG]',
        '🛡️': '[SECURE]',
        '📦': '[PACKAGE]',
        '🌐': '[NETWORK]',
        '📱': '[MOBILE]',
        '🏗️': '[BUILD]',
        '🧩': '[COMPONENT]',
        '🎨': '[DESIGN]',
        '🤖': '[AI]',
        '🔮': '[PREDICT]',
        '💰': '[MONEY]',
        '📈': '[UP]',
        '📉': '[DOWN]',
        '🔥': '[HOT]',
        '❄️': '[COLD]',
        '🎉': '[SUCCESS]',
        '💎': '[PREMIUM]',
        '🚀': '[LAUNCH]',
        '🛑': '[STOP]',
        '💼': '[PORTFOLIO]',
        '🏦': '[BANK]',
        '📋': '[LIST]',
        '⭐': '[STAR]',
        '🔔': '[BELL]',
        '🎁': '[GIFT]',
        '🌟': '[SHINE]',
        '💪': '[STRONG]',
        '🎖️': '[MEDAL]',
        '🏆': '[TROPHY]',
        '💯': '[100]',
        '🔝': '[TOP]',
    }
    
    result = text
    for emoji, replacement in replacements.items():
        result = result.replace(emoji, replacement)
    
    return result

def init_encoding_fix():
    """인코딩 수정 초기화 - 프로그램 시작 시 호출"""
    setup_utf8_environment()
    
    # 기본 출력 테스트
    try:
        print("UTF-8 인코딩 테스트: 한글 출력 정상")
        return True
    except UnicodeEncodeError:
        print("UTF-8 encoding fix applied")
        return False

if __name__ == "__main__":
    # 테스트
    init_encoding_fix()
    
    test_text = "✅ 테스트 메시지 🚀 한글과 이모지 📊"
    print(f"원본: {test_text}")
    print(f"정리된 텍스트: {clean_unicode_emojis(test_text)}")
    print(f"안전한 텍스트: {safe_format(clean_unicode_emojis(test_text))}")