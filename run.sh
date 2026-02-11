#!/bin/bash
#
# 통합 트레이딩 시스템
#
# ./run.sh              1.대시보드 → 2.파이프라인+모니터링 (백그라운드)
# ./run.sh -f           1.대시보드 → 2.파이프라인+모니터링 (포그라운드)
# ./run.sh status       상태 확인
# ./run.sh stop         중지
# ./run.sh logs         로그
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

LOG_DIR="$SCRIPT_DIR/logs"
PID_FILE="$SCRIPT_DIR/.auto_trading.pid"

mkdir -p "$LOG_DIR"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

is_running() {
    [ -f "$PID_FILE" ] && ps -p "$(cat $PID_FILE)" > /dev/null 2>&1
}

# 1단계: 대시보드 (포트폴리오 현황)
run_dashboard() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  [1/2] 포트폴리오 현황${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    source venv/bin/activate 2>/dev/null || true
    python3 main_trading.py
}

# 2단계: 파이프라인 + 모니터링 (포그라운드)
run_pipeline_foreground() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  [2/2] 파이프라인 + 모니터링 시작${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo ""
    echo "  • 조건검색식 1차 필터링"
    echo "  • VWAP 2차 필터링"
    echo "  • 실시간 모니터링 + 자동매매"
    echo ""
    echo -e "${YELLOW}  Ctrl+C로 종료${NC}"
    echo ""

    source venv/bin/activate 2>/dev/null || true
    python3 main_auto_trading.py
}

# 2단계: 파이프라인 + 모니터링 (백그라운드)
run_pipeline_daemon() {
    if is_running; then
        echo -e "${GREEN}[파이프라인]${NC} 이미 실행 중 (PID: $(cat $PID_FILE))"
        return 0
    fi

    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo -e "${GREEN}  [2/2] 파이프라인 + 모니터링 (백그라운드)${NC}"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo ""

    LOG_FILE="$LOG_DIR/auto_trading_$(date '+%Y%m%d').log"

    source venv/bin/activate 2>/dev/null || true
    nohup python3 main_auto_trading.py >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"

    sleep 3
    if is_running; then
        echo -e "${GREEN}[성공]${NC} PID: $(cat $PID_FILE)"
        echo ""
        echo "  로그: tail -f $LOG_FILE"
        echo "  상태: ./run.sh status"
        echo "  중지: ./run.sh stop"
        echo ""
    else
        echo -e "${RED}[실패]${NC} 시작 실패. 로그 확인: $LOG_FILE"
        rm -f "$PID_FILE"
    fi
}

stop_pipeline() {
    if ! is_running; then
        echo -e "${YELLOW}[알림]${NC} 실행 중 아님"
        rm -f "$PID_FILE" 2>/dev/null
        return 0
    fi

    PID=$(cat "$PID_FILE")
    echo -e "${YELLOW}[중지]${NC} PID: $PID"
    kill -15 "$PID" 2>/dev/null
    sleep 3
    ps -p "$PID" > /dev/null 2>&1 && kill -9 "$PID" 2>/dev/null
    rm -f "$PID_FILE"
    echo -e "${GREEN}[완료]${NC}"
}

show_status() {
    echo ""
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo "현재: $(date '+%Y-%m-%d %H:%M:%S') KST"
    echo "미국: $(TZ='America/New_York' date '+%H:%M') ET"
    echo -e "${CYAN}═══════════════════════════════════════════════════════════${NC}"
    echo ""

    if is_running; then
        PID=$(cat "$PID_FILE")
        UPTIME=$(ps -o etime= -p $PID 2>/dev/null | xargs)
        echo -e "${GREEN}●${NC} 파이프라인 실행 중 (PID: $PID, 가동: $UPTIME)"
    else
        echo -e "${RED}●${NC} 파이프라인 중지"
    fi

    echo ""
}

show_logs() {
    LOG_FILE="$LOG_DIR/auto_trading_$(date '+%Y%m%d').log"
    [ -f "$LOG_FILE" ] || LOG_FILE=$(ls -t "$LOG_DIR"/auto_trading_*.log 2>/dev/null | head -1)

    if [ -f "$LOG_FILE" ]; then
        echo -e "${GREEN}[로그]${NC} $LOG_FILE"
        tail -f "$LOG_FILE"
    else
        echo "로그 없음"
    fi
}

case "$1" in
    -f|--foreground)
        run_dashboard
        run_pipeline_foreground
        ;;
    status)
        show_status
        ;;
    stop)
        stop_pipeline
        ;;
    logs|log)
        show_logs
        ;;
    help|-h)
        echo ""
        echo "사용법: ./run.sh [옵션]"
        echo ""
        echo "  (없음)    대시보드 → 파이프라인 (백그라운드)"
        echo "  -f        대시보드 → 파이프라인 (포그라운드)"
        echo "  status    상태 확인"
        echo "  stop      중지"
        echo "  logs      로그 보기"
        echo ""
        ;;
    "")
        run_dashboard
        run_pipeline_daemon
        ;;
    *)
        echo -e "${RED}알 수 없는 옵션: $1${NC}"
        echo "./run.sh help"
        ;;
esac
