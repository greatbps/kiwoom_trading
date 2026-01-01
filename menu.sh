#!/bin/bash

# 키움 트레이딩 시스템 메뉴

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# 프로젝트 루트로 이동
cd "$(dirname "$0")"

# 메뉴 표시
show_menu() {
    clear
    echo -e "${CYAN}╔════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${CYAN}║                                                            ║${NC}"
    echo -e "${CYAN}║          ${GREEN}🚀 키움 자동매매 시스템 메뉴${CYAN}                  ║${NC}"
    echo -e "${CYAN}║                                                            ║${NC}"
    echo -e "${CYAN}╚════════════════════════════════════════════════════════════╝${NC}"
    echo ""
    echo -e "${YELLOW}[거래 실행]${NC}"
    echo -e "  ${GREEN}1)${NC} 실전 자동매매 시작 (./run.sh)"
    echo -e "  ${GREEN}2)${NC} 자동 재시작 테스트"
    echo ""
    echo -e "${YELLOW}[분석 & 리포트]${NC}"
    echo -e "  ${BLUE}3)${NC} 오늘 거래 분석"
    echo -e "  ${BLUE}4)${NC} 특정 날짜 거래 분석"
    echo -e "  ${BLUE}5)${NC} 시뮬레이션 결과 보기"
    echo -e "  ${BLUE}6)${NC} 주간 거래 리포트"
    echo ""
    echo -e "${YELLOW}[시스템 관리]${NC}"
    echo -e "  ${MAGENTA}7)${NC} 실행 중인 프로세스 확인"
    echo -e "  ${MAGENTA}8)${NC} 실행 중인 프로세스 중지"
    echo -e "  ${MAGENTA}9)${NC} 로그 파일 보기"
    echo ""
    echo -e "  ${RED}0)${NC} 종료"
    echo ""
    echo -ne "${CYAN}선택: ${NC}"
}

# 오늘 거래 분석
analyze_today() {
    echo ""
    echo -e "${CYAN}📊 오늘 거래 분석 중...${NC}"
    echo ""
    python3 analyze_daily_trades.py
    echo ""
    read -p "Enter를 눌러 메뉴로 돌아가기..."
}

# 특정 날짜 거래 분석
analyze_date() {
    echo ""
    echo -e "${CYAN}📅 분석할 날짜를 입력하세요 (예: 2025-12-18)${NC}"
    read -p "날짜: " date
    echo ""
    python3 analyze_daily_trades.py "$date"
    echo ""
    read -p "Enter를 눌러 메뉴로 돌아가기..."
}

# 시뮬레이션 결과 보기
view_simulation() {
    echo ""
    if [ -f "data/simulation_result.json" ]; then
        echo -e "${CYAN}📊 시뮬레이션 결과:${NC}"
        echo ""
        cat data/simulation_result.json | python3 -m json.tool
    else
        echo -e "${RED}❌ 시뮬레이션 결과 파일이 없습니다.${NC}"
    fi
    echo ""
    read -p "Enter를 눌러 메뉴로 돌아가기..."
}

# 주간 리포트
weekly_report() {
    echo ""
    if [ -f "data/weekly_trade_report.json" ]; then
        echo -e "${CYAN}📊 주간 거래 리포트:${NC}"
        echo ""
        cat data/weekly_trade_report.json | python3 -m json.tool
    else
        echo -e "${RED}❌ 주간 리포트 파일이 없습니다.${NC}"
    fi
    echo ""
    read -p "Enter를 눌러 메뉴로 돌아가기..."
}

# 프로세스 확인
check_process() {
    echo ""
    echo -e "${CYAN}🔍 실행 중인 트레이딩 프로세스:${NC}"
    echo ""

    PROCESSES=$(ps aux | grep -E "main_auto_trading|python.*kiwoom" | grep -v grep | grep -v menu.sh)

    if [ -z "$PROCESSES" ]; then
        echo -e "${YELLOW}실행 중인 프로세스가 없습니다.${NC}"
    else
        echo "$PROCESSES"
    fi

    echo ""
    read -p "Enter를 눌러 메뉴로 돌아가기..."
}

# 프로세스 중지
stop_process() {
    echo ""
    PIDS=$(pgrep -f "main_auto_trading.py" || echo "")

    if [ -z "$PIDS" ]; then
        echo -e "${YELLOW}실행 중인 프로세스가 없습니다.${NC}"
    else
        echo -e "${CYAN}실행 중인 프로세스 (PID: $PIDS)${NC}"
        read -p "정말 중지하시겠습니까? (y/n): " confirm

        if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
            kill $PIDS
            echo -e "${GREEN}✓ 프로세스를 중지했습니다.${NC}"
            sleep 2

            # 종료 확인
            if pgrep -f "main_auto_trading.py" > /dev/null; then
                echo -e "${YELLOW}⚠️  강제 종료 시도...${NC}"
                kill -9 $PIDS
                sleep 1
                echo -e "${GREEN}✓ 강제 종료 완료${NC}"
            fi
        else
            echo -e "${YELLOW}취소되었습니다.${NC}"
        fi
    fi

    echo ""
    read -p "Enter를 눌러 메뉴로 돌아가기..."
}

# 로그 보기
view_logs() {
    echo ""
    echo -e "${CYAN}📋 최근 로그 파일:${NC}"
    echo ""

    ls -lt logs/*.log 2>/dev/null | head -5 | awk '{print NR") " $9}' || echo "로그 파일이 없습니다."

    echo ""
    read -p "보려는 로그 번호 (Enter=취소): " log_num

    if [ ! -z "$log_num" ]; then
        log_file=$(ls -lt logs/*.log 2>/dev/null | head -5 | sed -n "${log_num}p" | awk '{print $9}')
        if [ ! -z "$log_file" ]; then
            echo ""
            echo -e "${CYAN}📄 $log_file (최근 50줄)${NC}"
            echo ""
            tail -50 "$log_file"
        fi
    fi

    echo ""
    read -p "Enter를 눌러 메뉴로 돌아가기..."
}

# 실전 실행
run_trading() {
    echo ""
    echo -e "${RED}※ 실제 계좌로 거래합니다! 주의하세요!${NC}"
    read -p "계속하시겠습니까? (y/n): " confirm

    if [ "$confirm" = "y" ] || [ "$confirm" = "Y" ]; then
        ./run.sh
    else
        echo -e "${YELLOW}취소되었습니다.${NC}"
        sleep 1
    fi
}

# 테스트 실행
run_test() {
    echo ""
    echo -e "${CYAN}🧪 자동 재시작 테스트 실행 (75초)${NC}"
    echo -e "${YELLOW}Ctrl+C로 중지 가능${NC}"
    echo ""
    python3 test_auto_restart.py
    echo ""
    read -p "Enter를 눌러 메뉴로 돌아가기..."
}

# 메인 루프
while true; do
    show_menu
    read choice

    case $choice in
        1) run_trading ;;
        2) run_test ;;
        3) analyze_today ;;
        4) analyze_date ;;
        5) view_simulation ;;
        6) weekly_report ;;
        7) check_process ;;
        8) stop_process ;;
        9) view_logs ;;
        0)
            echo ""
            echo -e "${GREEN}👋 프로그램을 종료합니다.${NC}"
            echo ""
            exit 0
            ;;
        *)
            echo ""
            echo -e "${RED}잘못된 선택입니다.${NC}"
            sleep 1
            ;;
    esac
done
