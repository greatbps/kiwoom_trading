#!/bin/bash
# scripts/backup_project.sh
# 전체 프로젝트 백업 스크립트

set -e  # 에러 발생 시 스크립트 중단

# 색상 정의
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  Kiwoom Trading - Project Backup${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

# 백업 디렉토리 생성
BACKUP_DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="backups/pre-refactoring-$BACKUP_DATE"

echo -e "${YELLOW}Creating backup directory: ${BACKUP_DIR}${NC}"
mkdir -p "$BACKUP_DIR"

# 프로젝트 전체 복사 (venv 제외)
echo -e "${YELLOW}Copying project files...${NC}"
rsync -av \
  --exclude='venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='logs/' \
  --exclude='*.log' \
  --exclude='backups/' \
  --exclude='.git/' \
  . "$BACKUP_DIR/" 2>&1 | grep -v "sending incremental file list" | head -20

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Backup completed successfully!${NC}"
    echo ""

    # 백업 메타데이터 저장
    cat > "$BACKUP_DIR/BACKUP_INFO.txt" << EOF
Backup Date: $BACKUP_DATE
Backup Type: Full Project Backup (Pre-Refactoring)
Git Commit: $(git rev-parse HEAD 2>/dev/null || echo "N/A")
Git Branch: $(git branch --show-current 2>/dev/null || echo "N/A")
Total Size: $(du -sh "$BACKUP_DIR" 2>/dev/null | cut -f1)
Files Count: $(find "$BACKUP_DIR" -type f 2>/dev/null | wc -l)
Python Version: $(python3 --version)
Created By: $(whoami)
Hostname: $(hostname)
EOF

    echo -e "${GREEN}Backup Information:${NC}"
    cat "$BACKUP_DIR/BACKUP_INFO.txt"
    echo ""
    echo -e "${GREEN}Backup location: ${BACKUP_DIR}${NC}"

else
    echo -e "${RED}❌ Backup failed!${NC}"
    exit 1
fi
