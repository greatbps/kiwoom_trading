"""
analysis/session_verify.py — 장 후 자동 검증 (3-Layer Verification Loop)

목적: 매 거래 세션 종료 후 자동 실행, 이상 감지 시 텔레그램 알림

레이어:
  L1. DB 무결성  (중복 / NULL / BUY-SELL 미매칭)
  L2. drift_detector 일관성  (win 기록 누락 여부)
  L3. 당일 거래 요약  (신호→실행→기록 검증)

사용법:
    python3 -m analysis.session_verify [--date YYYYMMDD] [--no-alert]
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_ROOT    = Path(__file__).parent.parent
_DB_PATH = _ROOT / "data" / "trades.db"
_DRIFT   = _ROOT / "data" / "drift_detector_state.json"
_LOG_DIR = _ROOT / "logs"


# ──────────────────────────────────────────────────────────────
# L1. DB 무결성
# ──────────────────────────────────────────────────────────────

def check_db_integrity(date_str: str) -> dict:
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row

    results = {}

    # 1. 중복 신호 체크
    dups = conn.execute("""
        SELECT stock_code, trade_type, COUNT(*) as cnt
        FROM trades
        WHERE trade_date = ?
        GROUP BY stock_code, trade_type
        HAVING cnt > 1
    """, (date_str,)).fetchall()
    results['duplicate_signals'] = [dict(r) for r in dups]

    # 2. NULL / 0 가격 체크
    nulls = conn.execute("""
        SELECT stock_code, trade_type, price
        FROM trades
        WHERE trade_date = ? AND (price IS NULL OR price = 0)
    """, (date_str,)).fetchall()
    results['null_prices'] = [dict(r) for r in nulls]

    # 3. BUY-SELL 미매칭 (BUY만 있고 SELL 없는 것 = 미청산)
    buys = set(
        r['stock_code'] for r in
        conn.execute("SELECT stock_code FROM trades WHERE trade_date=? AND trade_type='BUY'", (date_str,)).fetchall()
    )
    sells = set(
        r['stock_code'] for r in
        conn.execute("SELECT stock_code FROM trades WHERE trade_date=? AND trade_type='SELL'", (date_str,)).fetchall()
    )
    results['unclosed_positions'] = list(buys - sells)   # BUY했는데 SELL 없음
    results['orphan_sells']       = list(sells - buys)   # SELL인데 대응 BUY 없음 (정상일 수 있음)

    # 4. 당일 거래 요약
    rows = conn.execute("""
        SELECT trade_type, COUNT(*) as cnt, SUM(realized_pnl) as total_pnl
        FROM trades WHERE trade_date = ?
        GROUP BY trade_type
    """, (date_str,)).fetchall()
    results['daily_summary'] = {r['trade_type']: {'count': r['cnt'], 'pnl': r['total_pnl']} for r in rows}

    conn.close()
    return results


# ──────────────────────────────────────────────────────────────
# L2. drift_detector 일관성
# ──────────────────────────────────────────────────────────────

def check_drift_consistency(date_str: str) -> dict:
    if not _DRIFT.exists():
        return {'error': 'drift_detector_state.json 없음'}

    try:
        drift = json.loads(_DRIFT.read_text())
    except Exception as e:
        return {'error': f'JSON 파싱 실패: {e}'}

    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row

    # DB의 당일 SELL 건수
    db_sells = conn.execute("""
        SELECT stock_code, realized_pnl FROM trades
        WHERE trade_date = ? AND trade_type = 'SELL'
    """, (date_str,)).fetchall()
    conn.close()

    # drift에 기록된 당일 거래
    drift_today = [
        t for t in drift.get('recent_trades', [])
        if t.get('timestamp', '')[:10] == date_str
    ]

    db_codes    = {r['stock_code'] for r in db_sells}
    drift_codes = {t.get('stock') for t in drift_today}

    missing_in_drift = db_codes - drift_codes
    extra_in_drift   = drift_codes - db_codes

    return {
        'db_sells_today':     len(db_sells),
        'drift_records_today': len(drift_today),
        'missing_in_drift':   list(missing_in_drift),
        'extra_in_drift':     list(extra_in_drift),
        'drift_level':        drift.get('drift_level', '?'),
        'consistent':         len(missing_in_drift) == 0,
    }


# ──────────────────────────────────────────────────────────────
# L3. 로그 기반 신호→실행 검증
# ──────────────────────────────────────────────────────────────

def check_log_signals(date_str: str) -> dict:
    log_path = _LOG_DIR / f"auto_trading_{date_str}.log"
    if not log_path.exists():
        return {'error': f'로그 파일 없음: {log_path.name}'}

    content = log_path.read_text(errors='replace')
    lines   = content.splitlines()

    # 신호 → 실행 카운트
    counters = {
        'exploration_entry': 0,
        'smc_sig':           0,
        'trend_sig':         0,
        'buy_complete':      0,
        'sell_complete':     0,
        'mfe_exit':          0,
        'carry_override':    0,
        'hard_stop_relax':   0,
        'overnight_retry':   0,
        'kill_switch_on':    0,
        'sell_token_fail3x': 0,
    }
    patterns = {
        'exploration_entry': '[EXPLORATION_ENTRY]',
        'smc_sig':           '[SMC_SIG]',
        'trend_sig':         '[TREND_SIG]',
        'buy_complete':      '매수완료',
        'sell_complete':     '매도완료',
        'mfe_exit':          '[MFE_EXIT]',
        'carry_override':    '[CARRY_OVERRIDE]',
        'hard_stop_relax':   '[HARD_STOP_RELAX]',
        'overnight_retry':   '[OVERNIGHT_RETRY_1455]',
        'kill_switch_on':    '[KILL_SWITCH_ON]',
        'sell_token_fail3x': '[SELL_TOKEN_FAIL_3X]',
    }
    for line in lines:
        for key, pattern in patterns.items():
            if pattern in line:
                counters[key] += 1

    # 신호→실행 불일치 체크 (진입 신호 > 매수 완료 = 일부 차단됨 → 정상)
    total_signals = counters['exploration_entry'] + counters['smc_sig'] + counters['trend_sig']
    signal_exec_ratio = (counters['buy_complete'] / total_signals * 100) if total_signals > 0 else 0

    # 이상 징후
    anomalies = []
    if counters['kill_switch_on'] > 0:
        anomalies.append(f"KILL_SWITCH 발동 {counters['kill_switch_on']}회")
    if counters['sell_token_fail3x'] > 0:
        anomalies.append(f"토큰 3회 실패 {counters['sell_token_fail3x']}회")
    if counters['overnight_retry'] > 0:
        anomalies.append(f"overnight 재시도 발생 {counters['overnight_retry']}회")
    if counters['carry_override'] > 2:
        anomalies.append(f"carry_override 과다 발동 {counters['carry_override']}회 (기준: ≤1)")

    return {
        'counters':          counters,
        'total_signals':     total_signals,
        'signal_exec_ratio': round(signal_exec_ratio, 1),
        'anomalies':         anomalies,
    }


# ──────────────────────────────────────────────────────────────
# 종합 리포트 + 텔레그램 알림
# ──────────────────────────────────────────────────────────────

def run_verification(date_str: str) -> dict:
    l1 = check_db_integrity(date_str)
    l2 = check_drift_consistency(date_str)
    l3 = check_log_signals(date_str)

    # 이상 여부 종합 판정
    issues = []
    if l1.get('duplicate_signals'):
        issues.append(f"중복신호 {len(l1['duplicate_signals'])}건")
    if l1.get('null_prices'):
        issues.append(f"가격NULL {len(l1['null_prices'])}건")
    if l1.get('unclosed_positions'):
        issues.append(f"미청산 {l1['unclosed_positions']}")
    if l2.get('missing_in_drift'):
        issues.append(f"drift누락 {l2['missing_in_drift']}")
    issues.extend(l3.get('anomalies', []))

    status = "⚠️ ISSUE" if issues else "✅ OK"

    return {
        'date':   date_str,
        'status': status,
        'issues': issues,
        'l1_db':  l1,
        'l2_drift': l2,
        'l3_log': l3,
    }


def print_report(r: dict):
    print(f"\n{'='*60}")
    print(f"  Session Verify — {r['date']}  {r['status']}")
    print(f"{'='*60}")

    if r['issues']:
        print(f"\n  🚨 이상 감지 ({len(r['issues'])}건):")
        for issue in r['issues']:
            print(f"    • {issue}")
    else:
        print("\n  이상 없음")

    # L1 요약
    l1 = r['l1_db']
    s  = l1.get('daily_summary', {})
    buy_cnt  = s.get('BUY',  {}).get('count', 0)
    sell_cnt = s.get('SELL', {}).get('count', 0)
    sell_pnl = s.get('SELL', {}).get('pnl',  0) or 0
    print(f"\n  [L1 DB]  BUY={buy_cnt}건  SELL={sell_cnt}건  PnL={sell_pnl:+,.0f}원")
    if l1.get('unclosed_positions'):
        print(f"    ⚠️  미청산: {l1['unclosed_positions']}")

    # L2 요약
    l2 = r['l2_drift']
    if 'error' not in l2:
        consistent = "✓" if l2['consistent'] else "✗"
        print(f"  [L2 Drift] {consistent} DB={l2['db_sells_today']}건 / "
              f"drift={l2['drift_records_today']}건  level={l2['drift_level']}")
        if l2.get('missing_in_drift'):
            print(f"    ⚠️  drift 미기록: {l2['missing_in_drift']}")

    # L3 요약
    l3 = r['l3_log']
    if 'error' not in l3:
        c = l3['counters']
        print(f"  [L3 Log]  신호={l3['total_signals']} → 실행={c['buy_complete']} "
              f"({l3['signal_exec_ratio']}%)")
        if c['mfe_exit']:    print(f"    MFE_EXIT: {c['mfe_exit']}회")
        if c['carry_override']: print(f"    CARRY_OVERRIDE: {c['carry_override']}회")
        if c['hard_stop_relax']: print(f"    HARD_STOP_RELAX: {c['hard_stop_relax']}회")

    print(f"{'='*60}\n")


def send_telegram_alert(r: dict):
    bot  = os.getenv("TELEGRAM_BOT_TOKEN", "")
    cids = os.getenv("TELEGRAM_CHAT_IDS", "")
    if not bot or not cids:
        return

    l3 = r['l3_log']
    c  = l3.get('counters', {})
    l1 = r['l1_db']
    s  = l1.get('daily_summary', {})
    sell_pnl = s.get('SELL', {}).get('pnl', 0) or 0

    status_icon = "🚨" if r['issues'] else "✅"
    msg = (
        f"{status_icon} *Session Verify {r['date']}*\n"
        f"상태: {r['status']}\n"
        f"BUY={s.get('BUY', {}).get('count', 0)} SELL={s.get('SELL', {}).get('count', 0)}"
        f" PnL={sell_pnl:+,.0f}원\n"
    )
    if r['issues']:
        msg += "이상:\n" + "\n".join(f"  • {i}" for i in r['issues']) + "\n"
    if c.get('mfe_exit'):
        msg += f"MFE_EXIT: {c['mfe_exit']}회\n"
    if c.get('carry_override'):
        msg += f"CARRY_OVERRIDE: {c['carry_override']}회\n"

    try:
        import requests
        for cid in cids.split(','):
            cid = cid.strip()
            if cid:
                requests.post(
                    f"https://api.telegram.org/bot{bot}/sendMessage",
                    json={"chat_id": cid, "text": msg, "parse_mode": "Markdown"},
                    timeout=5,
                )
    except Exception as e:
        logger.warning(f"[VERIFY_ALERT] 텔레그램 실패: {e}")


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING, format='%(message)s')

    parser = argparse.ArgumentParser(description='장 후 자동 검증')
    parser.add_argument('--date',     type=str, default='', help='YYYYMMDD (기본: 오늘)')
    parser.add_argument('--no-alert', action='store_true',  help='텔레그램 알림 억제')
    args = parser.parse_args()

    date_str = args.date or datetime.now().strftime('%Y%m%d')
    # DB 쿼리용 포맷: YYYY-MM-DD
    db_date  = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"

    result = run_verification(db_date)
    print_report(result)

    if not args.no_alert:
        send_telegram_alert(result)

    # 이상 있으면 종료 코드 1 (CI/스케줄러 연동용)
    raise SystemExit(1 if result['issues'] else 0)
