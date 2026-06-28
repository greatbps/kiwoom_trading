#!/bin/bash
set -e
cd /home/greatbps/projects/kiwoom_trading
echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> logs/watchlist_cron.log
python3 -m analysis.ntd_alpha_tracker --days 20 >> logs/watchlist_cron.log 2>&1
python3 -m analysis.ntd_watchlist --ttl 5 >> logs/watchlist_cron.log 2>&1
python3 -m analysis.compression_shadow >> logs/watchlist_cron.log 2>&1
