"""
analysis/telegram_report.py

당일/최근 매매 분석 결과를 텔레그램으로 전송.

사용법:
  python3 analysis/telegram_report.py
  python3 analysis/telegram_report.py --date 20260129
  python3 analysis/telegram_report.py --days 7 --dry-run
"""

import argparse
import sys
import requests
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from analysis.log_trade_analytics import (
    evaluate_hard_stop,
    fetch_report,
    format_telegram_report,
)

# .env 로드
ENV_PATH = Path(__file__).parent.parent / ".env"
def load_env(path):
    env = {}
    for line in path.read_text(errors="ignore").splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

ENV = load_env(ENV_PATH)
BOT_TOKEN = ENV.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = ENV.get("TELEGRAM_CHAT_IDS", "").split(",")[0].strip()


def send_telegram(text: str) -> bool:
    if not BOT_TOKEN or not CHAT_ID:
        print("[ERROR] TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_IDS 없음")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
    }, timeout=10)
    return resp.status_code == 200


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    parser.add_argument("--days", type=int, default=0, help="분석 기간(일), 0=당일만, 1+=최근N일")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y%m%d")

    print(f"[{date_str}] DB 리포트 생성 중...")
    report = fetch_report(days=args.days, top_n=5)
    hard_stop = evaluate_hard_stop(
        lookback_hours=24,
        consecutive_loss_limit=5,
        regime_loss_limit=-5.0,
        regime_min_trades=2,
    )
    msg = format_telegram_report(report, hard_stop)
    print("\n--- 전송 메시지 미리보기 ---")
    print(msg)
    print("----------------------------\n")

    if args.dry_run:
        print("[dry-run] 전송 안 함")
        return

    print("텔레그램 전송 중...")
    ok = send_telegram(msg)
    print("✅ 전송 성공" if ok else "❌ 전송 실패")


if __name__ == "__main__":
    main()
