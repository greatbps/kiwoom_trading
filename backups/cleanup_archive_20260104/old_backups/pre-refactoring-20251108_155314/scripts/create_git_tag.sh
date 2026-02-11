#!/bin/bash
# scripts/create_git_tag.sh
# Git 태그 생성 스크립트

set -e

# 색상 정의
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}========================================${NC}"
echo -e "${YELLOW}  Git Tag Creation${NC}"
echo -e "${YELLOW}========================================${NC}"
echo ""

TAG_NAME="v1.0-pre-refactoring"
TAG_MESSAGE="Snapshot before major refactoring ($(date +%Y-%m-%d))"

# Git 저장소 확인
if [ ! -d .git ]; then
    echo -e "${YELLOW}⚠️  Not a git repository${NC}"
    echo -e "${YELLOW}Initializing git repository...${NC}"
    git init
    git add .
    git commit -m "Initial commit before refactoring"
fi

# Git 상태 확인
if [ -n "$(git status --porcelain)" ]; then
    echo -e "${YELLOW}⚠️  Uncommitted changes detected${NC}"
    git status --short
    echo ""
    echo -e "${YELLOW}Creating commit before tagging...${NC}"
    git add .
    git commit -m "Pre-refactoring snapshot"
fi

# 기존 태그 확인
if git rev-parse "$TAG_NAME" >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️  Tag '$TAG_NAME' already exists${NC}"
    echo -e "${YELLOW}Deleting old tag...${NC}"
    git tag -d "$TAG_NAME"
fi

# 태그 생성
git tag -a "$TAG_NAME" -m "$TAG_MESSAGE"

if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Git tag created: $TAG_NAME${NC}"
    echo ""
    echo -e "${GREEN}Tag details:${NC}"
    git show "$TAG_NAME" --no-patch
    echo ""
    echo -e "${YELLOW}To push tag to remote:${NC}"
    echo -e "${YELLOW}  git push origin $TAG_NAME${NC}"
else
    echo -e "${RED}❌ Failed to create tag${NC}"
    exit 1
fi
