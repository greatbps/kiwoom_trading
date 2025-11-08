"""
스케줄링 로직 테스트
"""
from datetime import datetime, timedelta


def test_wait_until_logic():
    """시간 대기 로직 테스트"""
    print("=" * 80)
    print("스케줄링 로직 테스트")
    print("=" * 80)

    now = datetime.now()
    print(f"\n현재 시간: {now.strftime('%Y-%m-%d %H:%M:%S (%A)')}")
    print(f"요일: {now.weekday()} (0=월요일, 6=일요일)")

    # 테스트 1: 08:50까지 대기
    print("\n[테스트 1] 08:50까지 대기")
    target_time = now.replace(hour=8, minute=50, second=0, microsecond=0)

    if now >= target_time:
        target_time += timedelta(days=1)
        # 금요일이면 다음 월요일로
        if target_time.weekday() >= 5:
            days_until_monday = 7 - target_time.weekday()
            target_time += timedelta(days=days_until_monday)

    time_diff = (target_time - now).total_seconds()
    hours = int(time_diff // 3600)
    minutes = int((time_diff % 3600) // 60)

    print(f"  목표 시간: {target_time.strftime('%Y-%m-%d %H:%M:%S (%A)')}")
    print(f"  남은 시간: {hours}시간 {minutes}분")

    # 테스트 2: 09:00까지 대기
    print("\n[테스트 2] 09:00까지 대기")
    target_time = now.replace(hour=9, minute=0, second=0, microsecond=0)

    if now >= target_time:
        target_time += timedelta(days=1)
        if target_time.weekday() >= 5:
            days_until_monday = 7 - target_time.weekday()
            target_time += timedelta(days=days_until_monday)

    time_diff = (target_time - now).total_seconds()
    hours = int(time_diff // 3600)
    minutes = int((time_diff % 3600) // 60)

    print(f"  목표 시간: {target_time.strftime('%Y-%m-%d %H:%M:%S (%A)')}")
    print(f"  남은 시간: {hours}시간 {minutes}분")

    # 테스트 3: 주말 체크
    print("\n[테스트 3] 주말 처리")
    if now.weekday() >= 5:
        days_until_monday = 7 - now.weekday()
        next_monday = now + timedelta(days=days_until_monday)
        next_monday_0850 = next_monday.replace(hour=8, minute=50, second=0, microsecond=0)

        print(f"  현재 주말입니다!")
        print(f"  다음 월요일: {next_monday_0850.strftime('%Y-%m-%d %H:%M:%S')}")

        time_diff = (next_monday_0850 - now).total_seconds()
        hours = int(time_diff // 3600)
        minutes = int((time_diff % 3600) // 60)
        print(f"  남은 시간: {hours}시간 {minutes}분")
    else:
        print(f"  평일입니다. 정상 진행")

    print("\n" + "=" * 80)


def test_schedule_flow():
    """스케줄 흐름 시뮬레이션"""
    print("\n" + "=" * 80)
    print("스케줄 흐름 시뮬레이션")
    print("=" * 80)

    now = datetime.now()
    print(f"\n현재: {now.strftime('%Y-%m-%d %H:%M:%S (%A)')}")

    # 오늘 8시 50분
    today_0850 = now.replace(hour=8, minute=50, second=0, microsecond=0)
    if now >= today_0850:
        today_0850 += timedelta(days=1)
        if today_0850.weekday() >= 5:
            days_until_monday = 7 - today_0850.weekday()
            today_0850 += timedelta(days=days_until_monday)

    # 오늘 9시 00분
    today_0900 = now.replace(hour=9, minute=0, second=0, microsecond=0)
    if now >= today_0900:
        today_0900 += timedelta(days=1)
        if today_0900.weekday() >= 5:
            days_until_monday = 7 - today_0900.weekday()
            today_0900 += timedelta(days=days_until_monday)

    # 오늘 15시 30분
    today_1530 = now.replace(hour=15, minute=30, second=0, microsecond=0)
    if now >= today_1530:
        today_1530 += timedelta(days=1)
        if today_1530.weekday() >= 5:
            days_until_monday = 7 - today_1530.weekday()
            today_1530 += timedelta(days=days_until_monday)

    print(f"\n📅 예상 스케줄:")
    print(f"  1. 필터링 시작: {today_0850.strftime('%Y-%m-%d %H:%M (%A)')}")
    print(f"  2. 모니터링 시작: {today_0900.strftime('%Y-%m-%d %H:%M (%A)')}")
    print(f"  3. 거래 종료: {today_1530.strftime('%Y-%m-%d %H:%M (%A)')}")
    print(f"  4. 다음 사이클: {(today_1530 + timedelta(days=1)).replace(hour=8, minute=50).strftime('%Y-%m-%d %H:%M (%A)')}")

    print("\n" + "=" * 80)


if __name__ == "__main__":
    print("\n🚀 스케줄링 로직 테스트\n")

    test_wait_until_logic()
    test_schedule_flow()

    print("\n✅ 테스트 완료!\n")
