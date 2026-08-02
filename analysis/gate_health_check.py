"""
Gate Chain Health Check

장 종료 후 자동 실행 — GLOBAL_GATE 체인에서 특정 게이트가 파이프라인을
장기간 독점 차단하거나, 하위 게이트(v1.4 Regime 등)가 한 번도 평가되지
않는 상황(2026-07 EC_HALT 사고 유형)을 조기 발견하기 위한 운영 검증.

읽기 전용 분석만 수행한다. 실거래 로직/게이트에는 관여하지 않는다.

실행:
    python3 -m analysis.gate_health_check          # 오늘
    python3 -m analysis.gate_health_check --date 2026-07-12
    python3 -m analysis.gate_health_check --json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

load_dotenv()
BASE = Path(__file__).parent.parent
sys.path.insert(0, str(BASE))

import psycopg2


def _get_conn():
    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )


_GATE_TAG_RE = re.compile(r'^\[([A-Z_]+)\]')

# _check_global_risk_gates()의 각 return False 사유 [TAG] → 표준 라벨
_GATE_LABELS = {
    'TIME_PHYSICAL':    'TIME_PHYSICAL',
    'KILL_SWITCH':      'KILL_SWITCH',
    'ORPHAN_HALT':       'ORPHAN',
    'DAILY_LOSS_LIMIT': 'DAILY_LOSS',
    'MAX_TRADES':        'MAX_TRADES',
    'MS_BLOCK':          'MS_BLOCK',
    'DD_HALT':           'DD_HALT',
    'EC_HALT':           'EC_HALT',
}
_PRINT_ORDER = ['EC_HALT', 'DD_HALT', 'KILL_SWITCH', 'DAILY_LOSS', 'MAX_TRADES', 'MS_BLOCK', 'ORPHAN', 'TIME_PHYSICAL']


# ─── DB Metric Collectors ────────────────────────────────────────

def _candidate_count(cur, d: date) -> int:
    cur.execute("SELECT COUNT(*) FROM research.candidates WHERE observed_at::date=%s", (str(d),))
    return cur.fetchone()[0]


def _gate_breakdown(cur, d: date) -> Dict[str, int]:
    """GLOBAL_GATE_BLOCKED 건을 gate_reason 앞 [TAG] 기준으로 집계."""
    cur.execute(
        "SELECT feature_snapshot->>'gate_reason' FROM research.decision_ledger "
        "WHERE decided_at::date=%s AND decision_reason_code='GLOBAL_GATE_BLOCKED'",
        (str(d),)
    )
    breakdown: Dict[str, int] = {k: 0 for k in _GATE_LABELS.values()}
    total = 0
    for (reason,) in cur.fetchall():
        total += 1
        m = _GATE_TAG_RE.match(reason or '')
        tag = m.group(1) if m else 'OTHER'
        label = _GATE_LABELS.get(tag, tag)
        breakdown[label] = breakdown.get(label, 0) + 1
    breakdown['__total__'] = total
    return breakdown


def _passed_global_gate(cur, d: date) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM research.decision_ledger "
        "WHERE decided_at::date=%s AND decision_reason_code != 'GLOBAL_GATE_BLOCKED'",
        (str(d),)
    )
    return cur.fetchone()[0]


def _orders_submitted(cur, d: date) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM trades WHERE trade_time::date=%s AND trade_type='BUY'",
        (str(d),)
    )
    return cur.fetchone()[0]


# 🔧 2026-07-28 [BUG-04] execute_buy() 단계에서 기록되는 reason_code (migration 005).
# 이 코드가 붙은 decision = "후보가 실제 execute_buy()까지 도달해 평가받았다"는 증거.
_ENTRY_STAGE_REASONS = (
    'PASS', 'COOLDOWN_ACTIVE', 'RISK_BLOCKED', 'DUPLICATE_POSITION', 'MAX_POSITIONS',
    'INSUFFICIENT_CAPITAL', 'ENTRY_QUALITY_BLOCKED', 'API_FAILURE', 'ORDER_FAILURE',
)


def _decision_total(cur, d: date) -> int:
    """[BUG-04] 당일 생성된 decision 총건수 (DB 단일 출처)."""
    cur.execute(
        "SELECT COUNT(*) FROM research.decision_ledger WHERE decided_at::date=%s",
        (str(d),)
    )
    return cur.fetchone()[0]


def _entry_stage_count(cur, d: date) -> int:
    """[BUG-04] execute_buy() 단계까지 도달한 decision 건수 (DB 단일 출처)."""
    cur.execute(
        "SELECT COUNT(*) FROM research.decision_ledger "
        "WHERE decided_at::date=%s AND decision_reason_code = ANY(%s)",
        (str(d), list(_ENTRY_STAGE_REASONS))
    )
    return cur.fetchone()[0]


def _reason_breakdown(cur, d: date) -> Dict[str, int]:
    """[BUG-04] reason_code별 집계 (DB 단일 출처)."""
    cur.execute(
        "SELECT decision_reason_code, COUNT(*) FROM research.decision_ledger "
        "WHERE decided_at::date=%s GROUP BY 1 ORDER BY 2 DESC",
        (str(d),)
    )
    return {r[0]: r[1] for r in cur.fetchall()}


def _recent_trading_days(cur, upto: date, n: int) -> List[date]:
    """candidates가 있었던 최근 n개 거래일 (upto 포함, 과거→최근 순)."""
    cur.execute(
        "SELECT DISTINCT observed_at::date FROM research.candidates "
        "WHERE observed_at::date <= %s ORDER BY 1 DESC LIMIT %s",
        (str(upto), n)
    )
    days = [r[0] for r in cur.fetchall()]
    days.reverse()
    return days


# ─── Log-based Metrics (DB에 없는 값 — v1.4 Regime Gate는 decision_ledger에 안 남음) ──

def _log_tag_counts(d: date) -> Dict[str, int]:
    log_path = BASE / 'logs' / f'auto_trading_{d.strftime("%Y%m%d")}.log'
    counts = {'regime_evaluated': 0, 'regime_block': 0, 'afternoon_cutoff_block': 0,
              'early_window_block': 0, 'gap_block': 0}
    if not log_path.exists():
        return counts
    try:
        with open(log_path, encoding='utf-8', errors='replace') as f:
            for line in f:
                if '[REGIME_BLOCK]' in line:
                    counts['regime_block'] += 1
                elif '[REGIME]' in line:
                    counts['regime_evaluated'] += 1
                elif '[AFTERNOON_CUTOFF_BLOCK]' in line:
                    counts['afternoon_cutoff_block'] += 1
                elif '[EARLY_WINDOW_BLOCK]' in line:
                    counts['early_window_block'] += 1
                elif '[GAP_BLOCK]' in line or '[EXPLORATION_GAP_BLOCK]' in line:
                    counts['gap_block'] += 1
    except Exception:
        pass
    return counts


# ─── Liveness Probe (BUG-03) ─────────────────────────────────────
# candidate=0 이 "전략 필터가 다 걸러낸 정상 상황"인지 "파이프라인이 안 돈 장애"인지
# 구분하기 위한 생존신호. 이 신호가 없으면 candidate=0을 NORMAL로 오판하게 된다.

_HEARTBEAT_PATH = Path('/tmp/kiwoom_heartbeat.json')


def collect_liveness(target_date: date, regime_evaluated: int) -> Dict[str, Any]:
    """
    파이프라인이 실제로 돌았는지 보여주는 신호들을 수집한다.
    (파일/로그만 확인 — 실거래 로직에 관여하지 않음)
    """
    log_path = BASE / 'logs' / f'auto_trading_{target_date.strftime("%Y%m%d")}.log'
    trading_log_present = log_path.exists() and log_path.stat().st_size > 0

    # daily_scan 산출물이 해당일자인지 (스캐너 실행 여부).
    # 주의: 이 파일들은 매 실행마다 덮어써지므로 "오늘" 검사일 때만 의미가 있다.
    # 과거 날짜 조회 시엔 판정 불가(None) — ERROR 오탐을 만들지 않는다.
    scan_ran: Optional[bool] = None
    if target_date == date.today():
        scan_ran = False
        for name in ('daily_patterns.json', 'daily_watchlist.json'):
            p = BASE / 'data' / name
            if p.exists():
                mtime = datetime.fromtimestamp(p.stat().st_mtime).date()
                if mtime == target_date:
                    scan_ran = True
                    break

    # 하트비트는 현재 시점 기준이라 오늘 검사일 때만 의미가 있다.
    # 🔧 2026-07-28 [v2.2 검증에서 수정] 키 이름이 'time'이다(watchdog.py:89와 동일).
    # 기존엔 last_heartbeat/timestamp를 찾아 항상 False로 떨어졌다.
    heartbeat_fresh = False
    if target_date == date.today() and _HEARTBEAT_PATH.exists():
        try:
            hb = json.loads(_HEARTBEAT_PATH.read_text())
            ts = hb.get('time') or hb.get('last_heartbeat') or hb.get('timestamp') or ''
            heartbeat_fresh = str(ts).startswith(target_date.isoformat())
        except Exception:
            heartbeat_fresh = False

    return {
        'trading_log_present': trading_log_present,
        'regime_evaluated':    regime_evaluated,
        'scanner_ran':         scan_ran,
        'heartbeat_fresh':     heartbeat_fresh,
    }


def classify_zero_candidate(liveness: Dict[str, Any]) -> tuple:
    """
    candidate=0 상황의 원인을 분류한다.

    Returns: (severity, reason)
        severity: 'ERROR'   — 파이프라인이 돌지 않음 (스캐너/프로세스 장애)
                  'WARNING' — 파이프라인은 정상, 전략 필터가 전부 거절 (정상일 수 있음)
    """
    if not liveness['trading_log_present']:
        return 'ERROR', 'Scanner not executed (no trading log for the day)'
    if liveness['regime_evaluated'] == 0:
        return 'ERROR', 'Trading loop produced no regime evaluation (pipeline stalled)'
    # scanner_ran은 오늘 검사일 때만 판정 가능 (산출물 파일이 매일 덮어써짐).
    # None = 판정 불가 → ERROR로 올리지 않는다.
    if liveness.get('scanner_ran') is False:
        return 'ERROR', 'daily_scan produced no output for the day'
    return 'WARNING', 'Pipeline alive (regime evaluated) — strategy filters rejected all candidates'


# ─── Report Builder (순수 함수 — DB/파일 I/O 없음, 테스트에서 직접 호출) ──

def build_report(
    target_date: date,
    candidates: int,
    gate_bd: Dict[str, int],
    passed: int,
    regime_evaluated: int,
    entry_signal_checked: int,
    orders: int,
    history_days: List[date],
    history: List[Dict[str, Any]],
    liveness: Optional[Dict[str, Any]] = None,
    decision_total: int = 0,
    reason_bd: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """수집된 지표로부터 warnings/system_status를 판정해 최종 리포트 dict를 만든다.
    DB나 로그 파일에 접근하지 않는 순수 함수 — 테스트는 이 함수를 직접 호출한다.

    🔧 2026-07-28 [BUG-03/04]:
      - liveness: candidate=0 원인 분류용 생존신호 (collect_liveness() 결과)
      - decision_total / reason_bd: DB 단일 출처 퍼널 지표
      - entry_signal_checked는 이제 DB(decision_ledger) 기준으로 전달된다.
        regime_evaluated는 여전히 로그 기준이며 참고값으로만 표시한다.
    """
    warnings: List[str] = []
    errors: List[str] = []
    full_history = len(history_days) >= 5
    ec_halt_today = gate_bd.get('EC_HALT', 0)
    reason_bd = reason_bd or {}

    # ── 당일 즉시 경고 (v1.1) ──────────────────────────────────
    ec_halt_immediate = candidates >= 100 and ec_halt_today == candidates
    if ec_halt_immediate:
        warnings.append("EC_HALT is blocking 100% of candidates today.")

    dead_gate_immediate = candidates > 0 and regime_evaluated == 0
    if dead_gate_immediate:
        warnings.append("v1.4 Regime Gate was not evaluated today (Candidate > 0).")

    entry_immediate = candidates > 0 and entry_signal_checked == 0
    if entry_immediate:
        warnings.append(
            f"Candidate : {candidates}\n\nEntry Evaluation : 0\n\nPossible upstream gate lock detected."
        )

    # ── [BUG-03] candidate=0 은 더 이상 무조건 NORMAL이 아니다 ──
    # 기존: 모든 즉시경고가 `candidates > 0` 을 전제로 해서, candidate가 0이면
    #       (= 가장 의심스러운 상황) 어떤 알람도 울리지 않았다.
    zero_candidate_severity = None
    if candidates == 0:
        probe = liveness or {}
        if not probe:
            # 생존신호가 없으면 판정 불가 — 침묵하지 말고 명시적으로 알린다
            zero_candidate_severity = 'WARNING'
            warnings.append(
                "Candidate zero detected\n\nReason: liveness probe unavailable (cannot classify)"
            )
        else:
            zero_candidate_severity, zero_reason = classify_zero_candidate(probe)
            msg = f"Candidate zero detected\n\nReason: {zero_reason}"
            (errors if zero_candidate_severity == 'ERROR' else warnings).append(msg)

    # ── 5거래일 연속 경고 (기존, 유지) ─────────────────────────
    if full_history:
        for gname in _PRINT_ORDER:
            all_100 = all(
                h['gate_breakdown'].get('__total__', 0) > 0
                and h['gate_breakdown'].get(gname, 0) == h['gate_breakdown'].get('__total__', 0)
                for h in history
            )
            if all_100:
                warnings.append(
                    f"{gname} has blocked 100% of candidates\nfor {len(history_days)} consecutive trading days."
                )

        if all(h['regime_evaluated'] == 0 for h in history):
            warnings.append(
                f"v1.4 Market Regime Gate\nhas never been evaluated\nfor {len(history_days)} trading days."
            )

    # ── System Status 한 줄 요약 ────────────────────────────────
    # [BUG-03] ERROR가 WARNING보다 우선한다 (파이프라인 미실행 > 전략 필터 거절)
    if errors:
        system_status = f"ERROR\n\n{errors[0].splitlines()[0]}"
    elif ec_halt_immediate:
        system_status = "WARNING\n\nEC_HALT blocking all candidates"
    elif dead_gate_immediate:
        system_status = "WARNING\n\nv1.4 Regime Gate never evaluated today"
    elif entry_immediate:
        system_status = "WARNING\n\nNo candidate reached Entry Evaluation"
    elif warnings:
        system_status = "WARNING\n\nSee warnings above"
    else:
        system_status = "NORMAL"

    return {
        'date':                  str(target_date),
        'generated_at':          datetime.now().isoformat(timespec='seconds'),
        'candidates':            candidates,
        'gate_breakdown':        gate_bd,
        'passed_global_gate':    passed,
        'v14_regime_evaluated':  regime_evaluated,
        'entry_signal_checked':  entry_signal_checked,
        'orders_submitted':      orders,
        'warnings':              warnings,
        # 🔧 2026-07-28 [BUG-03/04] 신규 필드
        'errors':                errors,
        'zero_candidate_severity': zero_candidate_severity,
        'liveness':              liveness or {},
        'decision_total':        decision_total,
        'reason_breakdown':      reason_bd,
        'system_status':         system_status,
        'history_days':          [str(d) for d in history_days],
    }


def run_gate_health_check(target_date: Optional[date] = None) -> Dict[str, Any]:
    """DB/로그에서 실제 지표를 수집해 build_report()로 리포트를 만든다."""
    target_date = target_date or date.today()
    conn = _get_conn()
    try:
        cur = conn.cursor()

        candidates = _candidate_count(cur, target_date)
        gate_bd    = _gate_breakdown(cur, target_date)
        passed     = _passed_global_gate(cur, target_date)
        orders     = _orders_submitted(cur, target_date)
        logs       = _log_tag_counts(target_date)

        # 🔧 2026-07-28 [BUG-04] Entry Evaluation을 DB 단일 출처로 전환.
        # 기존엔 로그 태그 산술(regime_evaluated - regime_block - ...)로 추정했는데,
        # 로그 태그는 스캔 사이클마다 종목별로 반복 발생해 candidate(DB, 1건=1행)와
        # 분모가 달랐다(예: 2026-07-13 REGIME_BLOCK 707 vs Candidate 293 = 241%).
        decision_total       = _decision_total(cur, target_date)
        reason_bd            = _reason_breakdown(cur, target_date)
        entry_signal_checked = _entry_stage_count(cur, target_date)

        # [BUG-03] candidate=0 원인 분류용 생존신호
        liveness = collect_liveness(target_date, logs['regime_evaluated'])

        # ── 최근 5거래일 추이 (경고 판정용) ──
        history_days = _recent_trading_days(cur, target_date, 5)
        history: List[Dict[str, Any]] = []
        for d in history_days:
            bd = gate_bd if d == target_date else _gate_breakdown(cur, d)
            lg = logs if d == target_date else _log_tag_counts(d)
            history.append({'date': str(d), 'gate_breakdown': bd, 'regime_evaluated': lg['regime_evaluated']})

        return build_report(
            target_date, candidates, gate_bd, passed,
            logs['regime_evaluated'], entry_signal_checked, orders,
            history_days, history,
            liveness=liveness,
            decision_total=decision_total,
            reason_bd=reason_bd,
        )
    finally:
        conn.close()


# ─── Printer ─────────────────────────────────────────────────────

def print_report(report: Dict[str, Any]) -> None:
    bd = report['gate_breakdown']
    total_blocked = bd.get('__total__', 0)
    cand = report['candidates']

    def pct(n: int) -> str:
        return f"({n / cand * 100:.0f}%)" if cand > 0 else "(-)"

    print(f"\n{'='*42}")
    print("Gate Chain Health Check")
    print(f"Date : {report['date']}")
    print(f"{'='*42}\n")

    # ── Funnel (DB 단일 출처: research.candidates / decision_ledger / trades) ──
    print("[Funnel — source: DB only]\n")
    print(f"Candidate                : {cand:,}\n")
    print(f"GLOBAL_GATE Passed       : {report['passed_global_gate']:,} {pct(report['passed_global_gate'])}")
    print(f"GLOBAL_GATE Blocked      : {total_blocked:,} {pct(total_blocked)}\n")
    for gname in _PRINT_ORDER:
        n = bd.get(gname, 0)
        print(f"  {gname:<20} : {n:,} {pct(n)}")

    print(f"\nEntry Evaluation         : {report['entry_signal_checked']:,} {pct(report['entry_signal_checked'])}")
    print(f"Orders Submitted         : {report['orders_submitted']:,} {pct(report['orders_submitted'])}")

    # 🔧 2026-07-28 [BUG-04] 로그 기반 값은 분모가 달라 pct()를 적용하지 않는다.
    print(f"\n{'-'*42}")
    print("[Reference — source: LOG, different denominator]")
    print("  (스캔 사이클마다 종목별 반복 발생 — Candidate 대비 비율로 해석 금지)")
    print(f"  regime_evaluated (log)  : {report['v14_regime_evaluated']:,}")

    lv = report.get('liveness') or {}
    if lv:
        print(f"\n{'-'*42}")
        print("[Liveness Probe]")
        print(f"  trading_log_present    : {lv.get('trading_log_present')}")
        print(f"  scanner_ran            : {lv.get('scanner_ran')}")
        print(f"  heartbeat_fresh        : {lv.get('heartbeat_fresh')}")

    print(f"\n{'='*42}")

    for e in report.get('errors', []):
        print(f"\nERROR\n\n{e}")

    if report['warnings']:
        for w in report['warnings']:
            print(f"\nWARNING\n\n{w}")
        print()

    print("System Status\n")
    print(f"{report['system_status']}\n")


# ─── Entry Point ─────────────────────────────────────────────────

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Gate Chain Health Check')
    parser.add_argument('--date', default=None, help='날짜 (YYYY-MM-DD). 기본: 오늘')
    parser.add_argument('--json', action='store_true', help='JSON 출력')
    args = parser.parse_args()

    target = date.fromisoformat(args.date) if args.date else date.today()
    report = run_gate_health_check(target)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)
