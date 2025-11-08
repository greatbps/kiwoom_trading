#!/bin/bash
# scripts/backup_database.sh
# 데이터베이스 백업 스크립트

set -e

# 색상 정의
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  Database Backup${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

BACKUP_DATE=$(date +%Y%m%d_%H%M%S)
DB_BACKUP_DIR="backups/database"
mkdir -p "$DB_BACKUP_DIR"

# SQLite 데이터베이스 파일 목록
DB_FILES=(
    "database/kiwoom_trading.db"
    "db/trading.db"
    "kiwoom_trading.db"
)

BACKUP_COUNT=0

for db_file in "${DB_FILES[@]}"; do
    if [ -f "$db_file" ]; then
        echo -e "${YELLOW}Backing up: $db_file${NC}"

        # 파일 복사
        cp "$db_file" "$DB_BACKUP_DIR/$(basename $db_file).$BACKUP_DATE"

        # SQL 덤프
        if command -v sqlite3 &> /dev/null; then
            sqlite3 "$db_file" ".dump" > "$DB_BACKUP_DIR/$(basename $db_file).$BACKUP_DATE.sql"
            echo -e "${GREEN}✅ Backed up (with SQL dump): $db_file${NC}"
        else
            echo -e "${GREEN}✅ Backed up (binary only): $db_file${NC}"
            echo -e "${YELLOW}⚠️  sqlite3 not found, SQL dump skipped${NC}"
        fi

        BACKUP_COUNT=$((BACKUP_COUNT + 1))
    fi
done

if [ $BACKUP_COUNT -eq 0 ]; then
    echo -e "${YELLOW}⚠️  No database files found${NC}"
else
    # 백업 정보 저장
    cat > "$DB_BACKUP_DIR/DB_BACKUP_INFO_$BACKUP_DATE.txt" << EOF
Backup Date: $BACKUP_DATE
Databases Backed Up: $BACKUP_COUNT
Backup Location: $DB_BACKUP_DIR
Files:
$(ls -lh $DB_BACKUP_DIR/*.$BACKUP_DATE* 2>/dev/null || echo "None")
EOF

    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}Database backup completed!${NC}"
    echo -e "${GREEN}Total databases: $BACKUP_COUNT${NC}"
    echo -e "${GREEN}Location: $DB_BACKUP_DIR${NC}"
    echo -e "${GREEN}========================================${NC}"
fi
