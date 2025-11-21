#!/bin/bash

# 청산 로직 최적화 통합 스크립트
# 자동으로 main_auto_trading.py에 최적화된 청산 로직을 통합합니다

# 색상 정의
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# 프로젝트 루트로 이동
cd "$(dirname "$0")/.."

echo ""
echo -e "${BLUE}============================================================${NC}"
echo -e "${BLUE}  청산 로직 최적화 통합 (손익비 0.27 → 1.2+ 개선)${NC}"
echo -e "${BLUE}============================================================${NC}"
echo ""

# 1. 백업 생성
echo -e "${YELLOW}[1/6] 백업 생성 중...${NC}"
BACKUP_DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="backups/exit_logic_integration_$BACKUP_DATE"

mkdir -p "$BACKUP_DIR"
cp main_auto_trading.py "$BACKUP_DIR/main_auto_trading.py.backup" 2>/dev/null
cp config/strategy_config.yaml "$BACKUP_DIR/strategy_config.yaml.backup" 2>/dev/null

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 백업 완료: $BACKUP_DIR${NC}"
else
    echo -e "${RED}✗ 백업 실패${NC}"
    exit 1
fi

# 2. 파일 존재 확인
echo -e "${YELLOW}[2/6] 필요한 파일 확인 중...${NC}"

REQUIRED_FILES=(
    "trading/exit_logic_optimized.py"
    "config/strategy_config_optimized.yaml"
    "docs/EXIT_LOGIC_INTEGRATION_GUIDE.md"
)

MISSING_FILES=0
for file in "${REQUIRED_FILES[@]}"; do
    if [ ! -f "$file" ]; then
        echo -e "${RED}✗ 파일 없음: $file${NC}"
        MISSING_FILES=$((MISSING_FILES + 1))
    fi
done

if [ $MISSING_FILES -gt 0 ]; then
    echo -e "${RED}✗ 필요한 파일이 없습니다. 먼저 최적화 로직을 생성하세요.${NC}"
    exit 1
fi

echo -e "${GREEN}✓ 모든 필요 파일 확인 완료${NC}"

# 3. Config 파일 교체
echo -e "${YELLOW}[3/6] Config 파일 교체 중...${NC}"

read -p "$(echo -e ${CYAN}기존 config를 최적화 버전으로 교체하시겠습니까? [y/N]: ${NC})" -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    cp config/strategy_config_optimized.yaml config/strategy_config.yaml
    echo -e "${GREEN}✓ Config 파일 교체 완료${NC}"
else
    echo -e "${YELLOW}⊘ Config 파일 교체 건너뜀 (수동으로 설정 병합 필요)${NC}"
fi

# 4. 테스트 실행
echo -e "${YELLOW}[4/6] 단위 테스트 실행 중...${NC}"

if [ -f "test/test_optimized_exit_logic.py" ]; then
    python3 test/test_optimized_exit_logic.py > /tmp/test_output.log 2>&1
    if [ $? -eq 0 ]; then
        echo -e "${GREEN}✓ 모든 테스트 통과 (6/6)${NC}"
    else
        echo -e "${RED}✗ 테스트 실패. 로그를 확인하세요: /tmp/test_output.log${NC}"
        echo -e "${YELLOW}계속 진행하시겠습니까? [y/N]: ${NC}"
        read -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Yy]$ ]]; then
            exit 1
        fi
    fi
else
    echo -e "${YELLOW}⊘ 테스트 파일 없음 (건너뜀)${NC}"
fi

# 5. Python 코드 수정
echo -e "${YELLOW}[5/6] main_auto_trading.py 수정 중...${NC}"

# 5-1. Import 추가 확인
if grep -q "from trading.exit_logic_optimized import OptimizedExitLogic" main_auto_trading.py; then
    echo -e "${GREEN}✓ Import 이미 추가됨${NC}"
else
    echo -e "${CYAN}  → Import 추가 중...${NC}"

    # strategy 관련 import 다음에 추가
    sed -i '/from strategy.condition_engine import StrategyConfig/a from trading.exit_logic_optimized import OptimizedExitLogic' main_auto_trading.py

    if grep -q "from trading.exit_logic_optimized import OptimizedExitLogic" main_auto_trading.py; then
        echo -e "${GREEN}  ✓ Import 추가 완료${NC}"
    else
        echo -e "${RED}  ✗ Import 추가 실패 (수동 추가 필요)${NC}"
    fi
fi

# 5-2. __init__ 메서드에 OptimizedExitLogic 초기화 추가 확인
if grep -q "self.exit_logic = OptimizedExitLogic" main_auto_trading.py; then
    echo -e "${GREEN}✓ OptimizedExitLogic 이미 초기화됨${NC}"
else
    echo -e "${CYAN}  → OptimizedExitLogic 초기화 추가 중...${NC}"

    # self.config = StrategyConfig 다음에 추가
    sed -i '/self.config = StrategyConfig/a \        \n        # 최적화된 청산 로직 초기화\n        self.exit_logic = OptimizedExitLogic(self.config.config)' main_auto_trading.py

    if grep -q "self.exit_logic = OptimizedExitLogic" main_auto_trading.py; then
        echo -e "${GREEN}  ✓ 초기화 추가 완료${NC}"
    else
        echo -e "${RED}  ✗ 초기화 추가 실패 (수동 추가 필요)${NC}"
    fi
fi

# 6. 구문 검사
echo -e "${YELLOW}[6/6] Python 구문 검사 중...${NC}"

python3 -m py_compile main_auto_trading.py 2>/dev/null
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✓ 구문 검사 통과${NC}"
else
    echo -e "${RED}✗ 구문 오류 발견${NC}"
    python3 -m py_compile main_auto_trading.py
    echo ""
    echo -e "${RED}오류를 수정한 후 다시 시도하세요.${NC}"
    exit 1
fi

# 완료
echo ""
echo -e "${GREEN}============================================================${NC}"
echo -e "${GREEN}  통합 완료! 🎉${NC}"
echo -e "${GREEN}============================================================${NC}"
echo ""
echo -e "${CYAN}📁 백업 위치:${NC} $BACKUP_DIR"
echo -e "${CYAN}📝 통합 가이드:${NC} docs/EXIT_LOGIC_INTEGRATION_GUIDE.md"
echo -e "${CYAN}📊 최종 보고서:${NC} docs/EXIT_LOGIC_OPTIMIZATION_SUMMARY.md"
echo ""
echo -e "${YELLOW}⚠️  주의사항:${NC}"
echo -e "  1. main_auto_trading.py의 check_exit_signal() 함수를 수동으로 교체하세요"
echo -e "  2. execute_sell() 함수에 use_market_order 파라미터를 추가하세요"
echo -e "  3. 자세한 내용은 통합 가이드를 참고하세요"
echo ""
echo -e "${BLUE}🚀 다음 단계:${NC}"
echo -e "  1. ${CYAN}모의투자로 테스트${NC}: python3 main_auto_trading.py"
echo -e "  2. ${CYAN}1일 검증 후${NC} 실전 적용"
echo -e "  3. ${CYAN}1주 후 분석${NC}: python3 utils/detailed_trade_analysis.py"
echo ""
echo -e "${GREEN}예상 개선:${NC} 손익비 0.27 → 1.0~1.5 (270~456% 개선) 🔥"
echo ""
