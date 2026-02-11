#!/bin/bash
# scripts/restore_full.sh
# 전체 복원 스크립트

set -e

# 색상 정의
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

BACKUP_DIR=$1

if [ -z "$BACKUP_DIR" ]; then
    echo -e "${RED}Usage: ./restore_full.sh <backup_directory>${NC}"
    echo ""
    echo -e "${YELLOW}Available backups:${NC}"
    ls -ld backups/pre-refactoring-* 2>/dev/null | tail -5 || echo "No backups found"
    exit 1
fi

if [ ! -d "$BACKUP_DIR" ]; then
    echo -e "${RED}❌ Backup directory not found: $BACKUP_DIR${NC}"
    exit 1
fi

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  Project Restore${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""
echo -e "${YELLOW}Restoring from: $BACKUP_DIR${NC}"
echo ""

# 백업 정보 표시
if [ -f "$BACKUP_DIR/BACKUP_INFO.txt" ]; then
    cat "$BACKUP_DIR/BACKUP_INFO.txt"
    echo ""
fi

# 확인
read -p "Continue with restore? (yes/no): " confirm
if [ "$confirm" != "yes" ]; then
    echo -e "${YELLOW}Restore cancelled${NC}"
    exit 0
fi

# 현재 상태 백업
echo -e "${YELLOW}Creating safety backup of current state...${NC}"
./scripts/backup_project.sh

# 복원 실행
echo -e "${YELLOW}Restoring files...${NC}"
rsync -av --delete \
    --exclude='venv/' \
    --exclude='backups/' \
    --exclude='.git/' \
    "$BACKUP_DIR/" . 2>&1 | head -20

# 데이터베이스 복원
if [ -d "$BACKUP_DIR/database" ]; then
    echo -e "${YELLOW}Restoring databases...${NC}"
    cp -r "$BACKUP_DIR/database/"* database/ 2>/dev/null || true
fi

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✅ Restore completed!${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "${YELLOW}⚠️  Please verify functionality:${NC}"
echo -e "${YELLOW}  1. Check that files are restored${NC}"
echo -e "${YELLOW}  2. Run tests: pytest${NC}"
echo -e "${YELLOW}  3. Test application startup${NC}"
