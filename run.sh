#!/bin/bash

# 키움 통합 자동매매 시스템 실행 스크립트

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
MAGENTA='\033[0;35m'
NC='\033[0m' # No Color

# 프로젝트 루트 디렉토리로 이동
cd "$(dirname "$0")"

# 환경 설정 함수
setup_environment() {
    echo -e "${BLUE}=====================================${NC}"
    echo -e "${BLUE}  키움 통합 자동매매 시스템 시작${NC}"
    echo -e "${BLUE}=====================================${NC}"
    echo ""

    # 1. 가상환경 확인 및 생성
    if [ ! -d "venv" ]; then
        echo -e "${YELLOW}[1/4] 가상환경이 없습니다. 생성 중...${NC}"
        python3 -m venv venv
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ 가상환경 생성 완료${NC}"
        else
            echo -e "${RED}✗ 가상환경 생성 실패${NC}"
            exit 1
        fi
    else
        echo -e "${GREEN}[1/4] 가상환경 확인 완료${NC}"
    fi

    # 2. 가상환경 활성화
    echo -e "${YELLOW}[2/4] 가상환경 활성화 중...${NC}"
    source venv/bin/activate
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ 가상환경 활성화 완료${NC}"
    else
        echo -e "${RED}✗ 가상환경 활성화 실패${NC}"
        exit 1
    fi

    # 3. 필수 패키지 확인 및 설치
    echo -e "${YELLOW}[3/4] 패키지 의존성 확인 중...${NC}"
    pip list 2>/dev/null | grep -q "requests" && pip list 2>/dev/null | grep -q "websockets" && pip list 2>/dev/null | grep -q "pandas"
    if [ $? -ne 0 ]; then
        echo -e "${YELLOW}필요한 패키지를 설치합니다...${NC}"
        pip install -q -r requirements.txt 2>&1 | grep -v "Requirement already satisfied" || true
        if [ $? -eq 0 ]; then
            echo -e "${GREEN}✓ 패키지 설치 완료${NC}"
        else
            echo -e "${RED}✗ 패키지 설치 실패${NC}"
            exit 1
        fi
    else
        echo -e "${GREEN}✓ 패키지 확인 완료${NC}"
    fi

    # 4. .env 파일 확인
    echo -e "${YELLOW}[4/4] 환경변수 확인 중...${NC}"
    if [ ! -f ".env" ]; then
        echo -e "${RED}✗ .env 파일이 없습니다!${NC}"
        echo -e "${YELLOW}다음 정보를 포함한 .env 파일을 생성하세요:${NC}"
        echo ""
        echo "KIWOOM_USER_ID=\"your_user_id\""
        echo "KIWOOM_APP_KEY=\"your_app_key\""
        echo "KIWOOM_APP_SECRET=\"your_app_secret\""
        echo "KIWOOM_ACCOUNT_NUMBER=\"0000-00\""
        echo ""
        exit 1
    else
        echo -e "${GREEN}✓ 환경변수 확인 완료${NC}"
    fi

    echo ""
    echo -e "${BLUE}=====================================${NC}"
    echo -e "${BLUE}  모든 준비 완료!${NC}"
    echo -e "${BLUE}=====================================${NC}"
    echo ""
}

# 트레이딩 시작 함수
# ✨ 적응형 대기 간격 적용:
#    - 남은 시간 > 1시간: 1시간마다 체크
#    - 남은 시간 10분~1시간: 10분마다 체크
#    - 남은 시간 1분~10분: 1분마다 체크
#    - 남은 시간 < 1분: 10초마다 체크
start_trading() {
    # 기존 프로세스 확인
    echo -e "${YELLOW}기존 프로세스 확인 중...${NC}"
    EXISTING_PID=$(pgrep -f "main_auto_trading.py --live" || echo "")

    if [ -n "$EXISTING_PID" ]; then
        echo -e "${RED}❌ 이미 실행 중인 프로세스 발견! (PID: $EXISTING_PID)${NC}"
        echo ""
        echo -e "${YELLOW}다음 중 선택하세요:${NC}"
        echo -e "  ${GREEN}1)${NC} 기존 프로세스 종료하고 재시작"
        echo -e "  ${RED}2)${NC} 취소 (기존 프로세스 유지)"
        echo ""
        read -p "선택 (1/2): " choice

        if [ "$choice" = "1" ]; then
            echo -e "${YELLOW}기존 프로세스 종료 중...${NC}"
            kill $EXISTING_PID
            sleep 3

            # 종료 확인
            if pgrep -f "main_auto_trading.py --live" > /dev/null; then
                echo -e "${RED}⚠️  프로세스가 종료되지 않았습니다. 강제 종료 시도...${NC}"
                kill -9 $EXISTING_PID
                sleep 2
            fi

            echo -e "${GREEN}✓ 프로세스 종료 완료${NC}"
        else
            echo -e "${YELLOW}취소되었습니다. 기존 프로세스는 계속 실행됩니다.${NC}"
            return
        fi
    else
        echo -e "${GREEN}✓ 중복 프로세스 없음${NC}"
    fi

    echo ""

    # 실전 자동매매 실행
    echo -e "${GREEN}실전 자동매매를 시작합니다...${NC}"
    echo -e "${RED}※ 실제 계좌로 거래합니다! 주의하세요!${NC}"
    echo -e "${YELLOW}※ 종료하려면 Ctrl+C를 누르세요${NC}"
    echo ""
    python3 main_auto_trading.py --live --conditions 17,18,19,20,21,22,23

    # 종료 코드 확인
    EXIT_CODE=$?
    echo ""
    if [ $EXIT_CODE -eq 0 ]; then
        echo -e "${GREEN}프로그램이 정상 종료되었습니다.${NC}"
    elif [ $EXIT_CODE -eq 130 ]; then
        echo -e "${YELLOW}사용자가 프로그램을 중지했습니다. (Ctrl+C)${NC}"
    else
        echo -e "${RED}프로그램이 오류로 종료되었습니다. (종료 코드: $EXIT_CODE)${NC}"
    fi
}

# 백그라운드로 트레이딩 시작 (nohup)
start_trading_background() {
    # 기존 프로세스 확인
    echo -e "${YELLOW}기존 프로세스 확인 중...${NC}"
    EXISTING_PID=$(pgrep -f "main_auto_trading.py --live" || echo "")

    if [ -n "$EXISTING_PID" ]; then
        echo -e "${RED}❌ 이미 실행 중인 프로세스 발견! (PID: $EXISTING_PID)${NC}"
        echo ""
        echo -e "${YELLOW}다음 중 선택하세요:${NC}"
        echo -e "  ${GREEN}1)${NC} 기존 프로세스 종료하고 재시작"
        echo -e "  ${RED}2)${NC} 취소 (기존 프로세스 유지)"
        echo ""
        read -p "선택 (1/2): " choice

        if [ "$choice" = "1" ]; then
            echo -e "${YELLOW}기존 프로세스 종료 중...${NC}"
            kill $EXISTING_PID
            sleep 3

            # 종료 확인
            if pgrep -f "main_auto_trading.py --live" > /dev/null; then
                echo -e "${RED}⚠️  프로세스가 종료되지 않았습니다. 강제 종료 시도...${NC}"
                kill -9 $EXISTING_PID
                sleep 2
            fi

            echo -e "${GREEN}✓ 프로세스 종료 완료${NC}"
        else
            echo -e "${YELLOW}취소되었습니다. 기존 프로세스는 계속 실행됩니다.${NC}"
            return
        fi
    else
        echo -e "${GREEN}✓ 중복 프로세스 없음${NC}"
    fi

    echo ""
    echo -e "${GREEN}백그라운드로 실전 자동매매를 시작합니다...${NC}"
    echo -e "${RED}※ 실제 계좌로 거래합니다! 주의하세요!${NC}"
    echo -e "${CYAN}※ 로그 확인: tail -f /tmp/trading_7strategies.log${NC}"
    echo ""

    # 백그라운드 실행
    nohup python3 main_auto_trading.py --live --conditions 17,18,19,20,21,22,23 > /tmp/trading_7strategies.log 2>&1 &
    NEW_PID=$!

    sleep 2

    # 실행 확인
    if ps -p $NEW_PID > /dev/null 2>&1; then
        echo -e "${GREEN}✓ 백그라운드 실행 성공! (PID: $NEW_PID)${NC}"
        echo -e "${CYAN}  로그 파일: /tmp/trading_7strategies.log${NC}"
        echo -e "${CYAN}  중지 명령: kill $NEW_PID${NC}"
    else
        echo -e "${RED}❌ 백그라운드 실행 실패!${NC}"
    fi

    echo ""
}

# 오늘 거래 분석
analyze_today() {
    echo ""
    echo -e "${CYAN}📊 오늘 거래 분석 중...${NC}"
    echo ""
    python3 analyze_daily_trades.py
}

# 특정 날짜 거래 분석
analyze_date() {
    echo ""
    echo -e "${CYAN}📅 분석할 날짜를 입력하세요 (예: 2025-12-18)${NC}"
    read -p "날짜: " date
    echo ""
    python3 analyze_daily_trades.py "$date"
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
}

# 프로세스 확인
check_process() {
    echo ""
    echo -e "${CYAN}🔍 실행 중인 트레이딩 프로세스:${NC}"
    echo ""

    PROCESSES=$(ps aux | grep -E "main_auto_trading|python.*kiwoom" | grep -v grep | grep -v run.sh)

    if [ -z "$PROCESSES" ]; then
        echo -e "${YELLOW}실행 중인 프로세스가 없습니다.${NC}"
    else
        echo "$PROCESSES"
    fi
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
}

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
    echo -e "  ${GREEN}1)${NC} 실전 자동매매 시작 (포그라운드)"
    echo -e "  ${GREEN}2)${NC} 백그라운드 자동매매 시작 (nohup)"
    echo -e "  ${GREEN}3)${NC} 자동 재시작 테스트"
    echo ""
    echo -e "${YELLOW}[분석 & 리포트]${NC}"
    echo -e "  ${BLUE}4)${NC} 오늘 거래 분석"
    echo -e "  ${BLUE}5)${NC} 특정 날짜 거래 분석"
    echo -e "  ${BLUE}6)${NC} 시뮬레이션 결과 보기"
    echo ""
    echo -e "${YELLOW}[시스템 관리]${NC}"
    echo -e "  ${MAGENTA}7)${NC} 실행 중인 프로세스 확인"
    echo -e "  ${MAGENTA}8)${NC} 실행 중인 프로세스 중지"
    echo ""
    echo -e "  ${RED}0)${NC} 종료"
    echo ""
    echo -ne "${CYAN}선택: ${NC}"
}

# 메인 로직
case "$1" in
    start)
        # 직접 시작 (./run.sh start)
        setup_environment
        start_trading
        ;;
    background|bg)
        # 백그라운드 시작 (./run.sh background 또는 ./run.sh bg)
        setup_environment
        start_trading_background
        ;;
    analyze)
        # 오늘 거래 분석 (./run.sh analyze)
        analyze_today
        ;;
    analyze-date)
        # 특정 날짜 분석 (./run.sh analyze-date 2025-12-18)
        if [ -z "$2" ]; then
            analyze_date
        else
            python3 analyze_daily_trades.py "$2"
        fi
        ;;
    simulation)
        # 시뮬레이션 결과 (./run.sh simulation)
        view_simulation
        ;;
    status)
        # 프로세스 상태 (./run.sh status)
        check_process
        ;;
    stop)
        # 프로세스 중지 (./run.sh stop)
        stop_process
        ;;
    test)
        # 재시작 테스트 (./run.sh test)
        python3 test_auto_restart.py
        ;;
    *)
        # 인자 없으면 바로 트레이딩 시작 (./run.sh)
        setup_environment
        start_trading

        # 거래 종료 후 메뉴 표시
        while true; do
            show_menu
            read choice

            case $choice in
                1)
                    start_trading
                    ;;
                2)
                    start_trading_background
                    read -p "Enter를 눌러 메뉴로 돌아가기..."
                    ;;
                3)
                    echo ""
                    echo -e "${CYAN}🧪 자동 재시작 테스트 실행 (75초)${NC}"
                    echo -e "${YELLOW}Ctrl+C로 중지 가능${NC}"
                    echo ""
                    python3 test_auto_restart.py
                    read -p "Enter를 눌러 메뉴로 돌아가기..."
                    ;;
                4)
                    python3 analyze_daily_trades_detailed.py
                    read -p "Enter를 눌러 메뉴로 돌아가기..."
                    ;;
                5)
                    analyze_date
                    read -p "Enter를 눌러 메뉴로 돌아가기..."
                    ;;
                6)
                    view_simulation
                    read -p "Enter를 눌러 메뉴로 돌아가기..."
                    ;;
                7)
                    check_process
                    read -p "Enter를 눌러 메뉴로 돌아가기..."
                    ;;
                8)
                    stop_process
                    read -p "Enter를 눌러 메뉴로 돌아가기..."
                    ;;
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
        ;;
esac
