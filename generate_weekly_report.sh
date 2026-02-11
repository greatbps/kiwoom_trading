#!/bin/bash
# 주간 리포트 생성 스크립트
# 매주 금요일 15:30 실행 권장 (crontab: 30 15 * * 5)

cd /home/greatbps/projects/kiwoom_trading
python3 reports/weekly_report_generator.py

echo ""
echo "📊 리포트가 reports/ 폴더에 저장되었습니다."
