#!/bin/bash
# api_server.py 데일리 자동 재시작
#
# 장시간(1~2일) 실행되면 내부 Kiwoom API 토큰 상태가 막혀서
# 대시보드가 stale DB 스냅샷/파일 폴백으로만 응답하는 문제가 반복 발생함
# (2026-07-14, 2026-07-16 재발). 근본 원인(토큰 재시도 로직) 수정 전까지
# 매일 한 번 재시작해서 예방한다. 실거래(main_auto_trading.py)와는 무관한
# 대시보드 백엔드만 재시작 — 실거래 로직에 영향 없음.
#
# 크론: 0 6 * * *  cd /home/greatbps/projects/kiwoom_trading && ./restart_api_server.sh >> logs/api_server_restart.log 2>&1

cd /home/greatbps/projects/kiwoom_trading || exit 1

echo "=== $(date '+%Y-%m-%d %H:%M:%S') api_server.py 재시작 시작 ==="

OLD_PID=$(pgrep -f "venv/bin/python api_server.py")
if [ -n "$OLD_PID" ]; then
    echo "기존 프로세스 종료: PID $OLD_PID"
    kill "$OLD_PID"
    sleep 3
    if pgrep -f "venv/bin/python api_server.py" > /dev/null; then
        echo "정상 종료 실패, 강제 종료(SIGKILL)"
        kill -9 "$OLD_PID" 2>/dev/null
        sleep 1
    fi
else
    echo "기존 프로세스 없음 (최초 시작)"
fi

nohup venv/bin/python api_server.py >> logs/api_server.log 2>&1 &
disown
sleep 5

NEW_PID=$(pgrep -f "venv/bin/python api_server.py")
if [ -n "$NEW_PID" ]; then
    HEALTH=$(curl -s http://localhost:8765/api/health)
    echo "재시작 완료: PID $NEW_PID / health: $HEALTH"
else
    echo "⚠️ 재시작 실패 — 프로세스가 뜨지 않음"
fi
echo ""
