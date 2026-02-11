#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
trading_system/utils/safe_console.py

안전한 콘솔 출력 유틸리티 (UTF-8 인코딩 문제 해결)
"""

import sys
from typing import Any, Optional
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich.prompt import Prompt
from utils.encoding_fix import clean_unicode_emojis, safe_format

class SafeConsole:
    """UTF-8 인코딩 안전 콘솔"""
    
    def __init__(self):
        self.console = Console(
            force_terminal=True,
            legacy_windows=True
        )
    
    def print(self, *args, **kwargs):
        """안전한 출력"""
        try:
            # 모든 인자를 안전한 문자열로 변환
            safe_args = []
            for arg in args:
                if isinstance(arg, str):
                    # 이모지 제거 및 안전한 포맷으로 변환
                    safe_text = clean_unicode_emojis(str(arg))
                    safe_text = safe_format(safe_text)
                    safe_args.append(safe_text)
                else:
                    safe_args.append(arg)
            
            self.console.print(*safe_args, **kwargs)
        except UnicodeEncodeError:
            # 최후의 수단: 기본 print 사용
            try:
                safe_text = clean_unicode_emojis(str(args[0]) if args else "")
                print(safe_format(safe_text))
            except:
                print("[Encoding Error] Unable to display message")
    
    def ask_with_timeout(self, prompt: str, default: str = "", timeout: int = 30) -> str:
        """타임아웃이 있는 사용자 입력"""
        import signal
        import threading
        import time

        result = [default]  # 결과를 저장할 리스트

        def timeout_handler():
            time.sleep(timeout)
            if not result[0] or result[0] == default:
                print(f"\n[Timeout after {timeout}s] Using default: {default}")

        def input_handler():
            try:
                if hasattr(self, 'console'):
                    user_input = Prompt.ask(prompt, default=default)
                else:
                    user_input = input(f"{prompt} [{default}]: ") or default
                result[0] = user_input
            except (KeyboardInterrupt, EOFError):
                result[0] = default
            except Exception:
                result[0] = default

        # 입력 스레드 시작
        input_thread = threading.Thread(target=input_handler)
        input_thread.daemon = True
        input_thread.start()

        # 타임아웃 스레드 시작
        timeout_thread = threading.Thread(target=timeout_handler)
        timeout_thread.daemon = True
        timeout_thread.start()

        # 입력 대기 (최대 timeout초)
        input_thread.join(timeout)

        return result[0]

    def confirm_with_timeout(self, prompt: str, default: bool = False, timeout: int = 30) -> bool:
        """타임아웃이 있는 확인 입력"""
        default_str = "y" if default else "n"
        response = self.ask_with_timeout(f"{prompt} [y/n]", default_str, timeout)
        return response.lower() in ['y', 'yes', 'true', '1']

    def print_panel(self, content: str, title: str = "", style: str = "cyan"):
        """안전한 패널 출력"""
        try:
            safe_content = safe_format(clean_unicode_emojis(content))
            safe_title = safe_format(clean_unicode_emojis(title))
            
            panel = Panel(
                safe_content,
                title=safe_title,
                border_style=style
            )
            self.console.print(panel)
        except UnicodeEncodeError:
            print(f"[{title}] {content}")
    
    def print_table(self, table_data: list, headers: list, title: str = ""):
        """안전한 테이블 출력"""
        try:
            table = Table(title=safe_format(clean_unicode_emojis(title)))
            
            # 헤더 추가
            for header in headers:
                safe_header = safe_format(clean_unicode_emojis(header))
                table.add_column(safe_header)
            
            # 데이터 추가
            for row in table_data:
                safe_row = []
                for cell in row:
                    safe_cell = safe_format(clean_unicode_emojis(str(cell)))
                    safe_row.append(safe_cell)
                table.add_row(*safe_row)
            
            self.console.print(table)
        except UnicodeEncodeError:
            # 폴백: 간단한 테이블 출력
            print(f"\n{title}")
            print("-" * 50)
            for i, header in enumerate(headers):
                print(f"{header:<15}", end=" ")
            print()
            print("-" * 50)
            for row in table_data:
                for cell in row:
                    print(f"{str(cell):<15}", end=" ")
                print()
    
    def ask(self, question: str, default: str = "") -> str:
        """안전한 입력 요청"""
        try:
            safe_question = safe_format(clean_unicode_emojis(question))
            return Prompt.ask(safe_question, default=default)
        except (UnicodeEncodeError, EOFError):
            # 폴백: 기본 input 사용
            try:
                safe_question = safe_format(clean_unicode_emojis(question))
                result = input(f"{safe_question}: ").strip()
                return result if result else default
            except EOFError:
                return default
    
    def clear(self):
        """콘솔 클리어"""
        try:
            self.console.clear()
        except:
            import os
            os.system('cls' if os.name == 'nt' else 'clear')

# 전역 안전 콘솔 인스턴스
safe_console = SafeConsole()

def safe_print(*args, **kwargs):
    """전역 안전 출력 함수"""
    safe_console.print(*args, **kwargs)

def safe_print_panel(content: str, title: str = "", style: str = "cyan"):
    """전역 안전 패널 출력 함수"""
    safe_console.print_panel(content, title, style)

def safe_print_table(table_data: list, headers: list, title: str = ""):
    """전역 안전 테이블 출력 함수"""
    safe_console.print_table(table_data, headers, title)

def safe_ask(question: str, default: str = "") -> str:
    """전역 안전 입력 함수"""
    return safe_console.ask(question, default)

def safe_ask_with_timeout(question: str, default: str = "", timeout: int = 30) -> str:
    """전역 타임아웃 입력 함수"""
    return safe_console.ask_with_timeout(question, default, timeout)

def safe_confirm_with_timeout(question: str, default: bool = False, timeout: int = 30) -> bool:
    """전역 타임아웃 확인 함수"""
    return safe_console.confirm_with_timeout(question, default, timeout)

if __name__ == "__main__":
    # 테스트
    console = SafeConsole()
    
    console.print("[bold green]안전한 콘솔 테스트[/bold green]")
    console.print("✅ 이모지와 한글이 포함된 텍스트 🚀")
    
    console.print_panel(
        "✅ 패널 테스트\n📊 데이터 표시\n🎯 목표 달성",
        title="🔧 시스템 상태",
        style="green"
    )
    
    test_data = [
        ["✅ 항목1", "📊 데이터1", "🎯 상태1"],
        ["⚠️ 항목2", "📈 데이터2", "🔄 상태2"],
    ]
    
    console.print_table(
        test_data,
        ["🏷️ 이름", "📋 정보", "🔍 상태"],
        "🚀 테스트 테이블"
    )