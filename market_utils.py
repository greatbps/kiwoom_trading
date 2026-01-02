#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
시장 유틸리티 함수
거래일 체크, 휴장일 확인 등
"""
from datetime import datetime, date
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()


def get_db_connection():
    """PostgreSQL 데이터베이스 연결"""
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=os.getenv('POSTGRES_PORT', '5432'),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD')
    )


def is_weekend(target_date=None):
    """주말 여부 확인 (토요일=5, 일요일=6)"""
    if target_date is None:
        target_date = date.today()
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    return target_date.weekday() >= 5


def is_holiday(target_date=None):
    """휴장일 여부 확인 (DB 조회)"""
    if target_date is None:
        target_date = date.today()
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT holiday_name
            FROM market_holidays
            WHERE holiday_date = %s
        """, (target_date,))

        result = cur.fetchone()
        return result[0] if result else None
    finally:
        conn.close()


def is_trading_day(target_date=None):
    """거래일 여부 확인 (주말 + 휴장일 체크)

    Returns:
        tuple: (is_trading, reason)
            - is_trading (bool): True면 거래일, False면 휴장일
            - reason (str): 휴장 사유 (거래일이면 None)
    """
    if target_date is None:
        target_date = date.today()
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    # 주말 체크
    if is_weekend(target_date):
        weekday = target_date.weekday()
        day_name = '토요일' if weekday == 5 else '일요일'
        return False, f'주말 ({day_name})'

    # 휴장일 체크
    holiday_name = is_holiday(target_date)
    if holiday_name:
        return False, f'휴장일 ({holiday_name})'

    return True, None


def get_holiday_info(target_date=None):
    """휴장일 정보 조회

    Returns:
        dict or None: 휴장일 정보 (거래일이면 None)
    """
    if target_date is None:
        target_date = date.today()
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    is_trading, reason = is_trading_day(target_date)

    if is_trading:
        return None

    return {
        'date': target_date,
        'is_trading_day': False,
        'reason': reason
    }


def get_next_trading_day(target_date=None):
    """다음 거래일 찾기"""
    from datetime import timedelta

    if target_date is None:
        target_date = date.today()
    elif isinstance(target_date, datetime):
        target_date = target_date.date()

    # 최대 10일까지만 검색
    for i in range(1, 11):
        next_date = target_date + timedelta(days=i)
        is_trading, _ = is_trading_day(next_date)
        if is_trading:
            return next_date

    return None


def get_upcoming_holidays(days=30):
    """다가오는 휴장일 조회

    Args:
        days (int): 조회 기간 (일)

    Returns:
        list: 휴장일 목록 (dict)
    """
    from datetime import timedelta

    today = date.today()
    end_date = today + timedelta(days=days)

    conn = get_db_connection()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT holiday_date, holiday_name, holiday_type
            FROM market_holidays
            WHERE holiday_date BETWEEN %s AND %s
            ORDER BY holiday_date
        """, (today, end_date))

        holidays = []
        for row in cur.fetchall():
            holidays.append({
                'date': row[0],
                'name': row[1],
                'type': row[2]
            })

        return holidays
    finally:
        conn.close()


if __name__ == "__main__":
    # 테스트
    print("=== 거래일 체크 테스트 ===\n")

    # 오늘
    today = date.today()
    is_trading, reason = is_trading_day(today)
    print(f"오늘 ({today}): {'거래일' if is_trading else f'휴장 - {reason}'}")

    # 다음 거래일
    next_day = get_next_trading_day(today)
    print(f"다음 거래일: {next_day}")

    # 다가오는 휴장일
    print("\n다가오는 휴장일 (30일):")
    holidays = get_upcoming_holidays(30)
    for h in holidays:
        print(f"  {h['date']} - {h['name']}")
