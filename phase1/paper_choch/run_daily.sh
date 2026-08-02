#!/usr/bin/env bash
# CHoCH Paper Trading — 매 거래일 장 종료 후 1회
#
# ⚠️ dry-run 전용. paper_choch.py 는 KiwoomAPI 를 import 하지 않으므로
#    주문 경로 자체가 없다. 실주문은 발생할 수 없다.
#
# 크론 등록 예 (아직 등록하지 않았다 — 사용자 승인 필요):
#   40 15 * * 1-5 /home/greatbps/projects/kiwoom_trading/phase1/paper_choch/run_daily.sh
set -euo pipefail
cd /home/greatbps/projects/kiwoom_trading
export SWING_ENTRY_ENGINE=choch
python3 -m phase1.paper_choch --daily >> logs/paper_choch.log 2>&1
