"""
analysis/daily_report.py

Hermes가 호출하는 당일 로그 수집기.
사용법:
  python3 analysis/daily_report.py
  python3 analysis/daily_report.py --date 20260403
"""

import os, json, argparse
from datetime import datetime, timedelta
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"

def find_log(pattern, date_str):
    fmt = date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:]
    name = pattern.replace("YYYYMMDD", date_str).replace("YYYY-MM-DD", fmt)
    p = LOG_DIR / name
    return p if p.exists() else None

def read_tail(path, chars=6000):
    text = path.read_text(encoding="utf-8", errors="ignore")
    return text[-chars:] if len(text) > chars else text

def extract_trades(log_text):
    lines = log_text.splitlines()
    trade_lines = []   # 매수/매도 핵심
    reject_lines = []  # REJECT (별도 집계)

    for line in lines:
        s = line.strip()
        if any(kw in s for kw in [
            "매도수량", "수익률:", "사유:", "실현손익",
            "매수 주문 성공", "Hard Stop", "Early Failure",
            "HALT", "CAUTION", "DANGER", "SMC_SIG", "TREND_SIG"
        ]):
            trade_lines.append(s)
        elif "REJECT" in s:
            reject_lines.append(s)

    # REJECT는 사유별 집계로 압축
    reject_summary = {}
    for l in reject_lines:
        reason = l.split("|")[-1].strip() if "|" in l else "기타"
        reject_summary[reason] = reject_summary.get(reason, 0) + 1

    result = trade_lines
    if reject_summary:
        result += ["\n[REJECT 사유 집계]"]
        for r, cnt in sorted(reject_summary.items(), key=lambda x: -x[1])[:10]:
            result.append(f"  {cnt}회 | {r}")

    return "\n".join(result)

def load_reentry(date_str):
    fmt = date_str[:4] + "-" + date_str[4:6] + "-" + date_str[6:]
    p = LOG_DIR / f"reentry_report_{fmt}.json"
    if not p.exists():
        return "[없음]"
    try:
        d = json.loads(p.read_text())
        return json.dumps({
            "block_count": d.get("block_count", 0),
            "ef_triggered": d.get("ef_triggered", 0),
            "market_sensor": d.get("market_sensor", {}),
            "by_reason": d.get("blocked_by_reason", {}),
        }, ensure_ascii=False, indent=2)
    except:
        return "[파싱 실패]"

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=None)
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime("%Y%m%d")
    main_log = find_log("auto_trading_YYYYMMDD.log", date_str)
    if not main_log:
        yd = (datetime.now() - timedelta(days=1)).strftime("%Y%m%d")
        main_log = find_log("auto_trading_YYYYMMDD.log", yd)
        date_str = yd

    smc_log = find_log("smc_decision_YYYYMMDD.log", date_str)

    print(f"=== 키움 매매 로그 [{date_str}] ===\n")

    print("--- [매매 핵심 이벤트] ---")
    if main_log:
        full_text = main_log.read_text(encoding="utf-8", errors="ignore")
        print(extract_trades(full_text))
    else:
        print("[없음]")

    print("\n--- [SMC 신호] ---")
    print(read_tail(smc_log, 2000) if smc_log else "[없음]")

    print("\n--- [재진입 리포트] ---")
    print(load_reentry(date_str))

    print("\n--- [Harness 상태] ---")
    hp = Path("/home/greatbps/projects/harness/state/state.json")
    print(hp.read_text() if hp.exists() else "[없음]")

if __name__ == "__main__":
    main()
