#!/bin/bash

# 키움 통합 자동매매 시스템 실행 스크립트

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 프로젝트 루트 디렉토리로 이동
cd "$(dirname "$0")"

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

# 5. 실전 자동매매 실행
echo -e "${GREEN}실전 자동매매를 시작합니다...${NC}"
echo -e "${RED}※ 실제 계좌로 거래합니다! 주의하세요!${NC}"
echo -e "${YELLOW}※ 종료하려면 Ctrl+C를 누르세요${NC}"
echo ""
python3 main_auto_trading.py --conditions 17,18,19,20,21,22

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
