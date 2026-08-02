"""
Trading OS Live Status

새로운 기능이 아닌 관찰 화면이다.
DB를 읽고 시스템 상태를 출력한다. 아무것도 쓰지 않는다.

  python3 -m analysis.os_status
"""

import os
import sys
import json
import subprocess
from datetime import date, datetime
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_CONFIG = dict(dbname='trading_system', user='postgres', password=os.getenv('POSTGRES_PASSWORD'), host='localhost')
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def conn():
    import psycopg2
    return psycopg2.connect(**DB_CONFIG)

def q(sql, *args):
    with conn() as c:
        cur = c.cursor()
        cur.execute(sql, args)
        return cur.fetchone()

def ok(cond): return '🟢' if cond else '🔴'
def na():     return '⚪ N/A'

# ── 데이터 수집 ─────────────────────────────────────────────────────────────

# Execution: main_auto_trading.py 프로세스
exec_running = subprocess.run(
    ['pgrep', '-f', 'main_auto_trading.py'],
    capture_output=True
).returncode == 0

# Research: 오늘 market_context 생성 여부
ctx_today = q("SELECT COUNT(*) FROM market_context WHERE context_date = CURRENT_DATE")[0]

# Research: 마지막 MIE 실행
last_mie = q("SELECT MAX(context_date) FROM market_context")
last_mie = last_mie[0].strftime('%Y-%m-%d') if last_mie and last_mie[0] else 'None'

# Governance: 활성 strategy_versions
active_strat = q("SELECT COUNT(*) FROM strategy_versions WHERE status='active'")[0]

# Session: 오늘 세션 상태
session_today = q("SELECT status FROM trading_sessions WHERE session_date = CURRENT_DATE")
session_status = session_today[0] if session_today else 'none'

# Scientist Level
re_row = q("SELECT scientist_level, re_no FROM research_environment WHERE status='active' LIMIT 1")
scientist_level = f"L{re_row[0]} ({re_row[1]})" if re_row else 'N/A'

# Calibration: 평가 완료된 predictions의 평균 오차
cal_row = q("""
    SELECT COUNT(*), AVG(ABS(confidence_pct - COALESCE(outcome_correct::int * 100, 0)))
    FROM scientist_predictions
    WHERE status = 'evaluated'
""")
if cal_row and cal_row[0] and cal_row[0] > 0:
    calibration = f"{cal_row[1]:.1f}%p 오차 ({cal_row[0]}건)"
else:
    calibration = 'N/A  (Phase 3c 대기)'

# Acceptance Test: 마지막 결과
hist_path = os.path.join(BASE, 'logs', 'acceptance_history.jsonl')
acceptance = 'N/A'
if os.path.exists(hist_path):
    with open(hist_path) as f:
        lines = [l for l in f if l.strip()]
    if lines:
        last = json.loads(lines[-1])
        c_fail = last.get('critical_fail', 0)
        acceptance = (f"{last['passed']}/{last['total']} PASS"
                      f"  [{last['run_at'][:10]}]"
                      f"  {'🟢' if c_fail == 0 else '🔴'}")

# Knowledge Base + Decay/Expiry (Evidence Inflation 감시)
kb_total   = q("SELECT COUNT(*) FROM knowledge_base WHERE is_active=TRUE")[0]
kb_expired = q("SELECT COUNT(*) FROM knowledge_base WHERE is_active=TRUE AND valid_until < NOW()")[0]
kb_retired = q("SELECT COUNT(*) FROM knowledge_base WHERE is_active=FALSE")[0]
kb_by_level = {}
for level in ['anecdotal','preliminary','confirmed','strong']:
    row = q("SELECT COUNT(*) FROM knowledge_base WHERE is_active=TRUE AND evidence_level=%s", level)
    kb_by_level[level] = row[0] if row else 0
kb_decay_risk = kb_expired > 0  # valid_until 경과했지만 아직 is_active=TRUE

# Hypothesis
hypo_pending = q("""
    SELECT COUNT(*) FROM hypotheses
    WHERE status IN ('draft','pending_review','queued')
""")[0]
hypo_total = q("SELECT COUNT(*) FROM hypotheses")[0]

# Experiments
exp_running = q("SELECT COUNT(*) FROM experiments WHERE status='running'")[0] if \
    q("SELECT COUNT(*) FROM pg_tables WHERE tablename='experiments'")[0] else 0
exp_total   = q("SELECT COUNT(*) FROM experiments")[0] if exp_running is not None else 0

# decision_log: 최근 BUY 건수
dl_buy_total = q("SELECT COUNT(*) FROM decision_log WHERE decision_type='BUY'")[0]
dl_buy_week  = q("""
    SELECT COUNT(*) FROM decision_log
    WHERE decision_type='BUY' AND decision_time > NOW() - INTERVAL '7 days'
""")[0]

# System Events: 최근
last_event = q("SELECT event_type, emitted_at FROM system_events ORDER BY id DESC LIMIT 1")

# Architecture Stability Index (ASI) — logs/asi.jsonl 수동 추적
asi_path = os.path.join(BASE, 'logs', 'asi.jsonl')
asi_history = []
if os.path.exists(asi_path):
    with open(asi_path) as f:
        asi_history = [json.loads(l) for l in f if l.strip()]
arch_changes = sum(e.get('changes', 0) for e in asi_history)
current_quarter = asi_history[-1] if asi_history else {}

# Release
release_ver = 'v2.8.0-beta'

# ── 출력 ────────────────────────────────────────────────────────────────────

now = datetime.now().strftime('%Y-%m-%d %H:%M')
print(f"""
╔══════════════════════════════════════════════════════════╗
║          Trading OS Live Status  [{now}]          ║
╠══════════════════════════════════════════════════════════╣
║  LAYERS                                                  ║
║                                                          ║
║  Execution    {ok(exec_running)} {'Running' if exec_running else 'Stopped — check watchdog'}
║  Research     {ok(ctx_today > 0)} MIE last: {last_mie}
║  Governance   {ok(active_strat > 0)} {active_strat} active strategy
║  Session      {ok(session_status == 'reviewed')} today: {session_status}
║                                                          ║
╠══════════════════════════════════════════════════════════╣
║  SCIENTIST                                               ║
║                                                          ║
║  Level        {scientist_level}
║  Calibration  {calibration}
║                                                          ║
╠══════════════════════════════════════════════════════════╣
║  RESEARCH                                                ║
║                                                          ║
║  Acceptance   {acceptance}
║  Knowledge    {kb_total} active / {kb_retired} retired
║    {ok(not kb_decay_risk)} 만료(valid_until 경과): {kb_expired}건{'  ← 폐기 처리 필요' if kb_decay_risk else ''}
║    evidence: anecdotal:{kb_by_level['anecdotal']} / preliminary:{kb_by_level['preliminary']} / confirmed:{kb_by_level['confirmed']} / strong:{kb_by_level['strong']}
║  Hypothesis   {hypo_pending} pending / {hypo_total} total
║  Experiments  {exp_running} running / {exp_total} total
║                                                          ║
╠══════════════════════════════════════════════════════════╣
║  DECISIONS                                               ║
║                                                          ║
║  decision_log BUY  {dl_buy_total} total / {dl_buy_week} (7d)
║  Last Event        {f"{last_event[0]} @ {last_event[1].strftime('%m-%d %H:%M')}" if last_event else 'None'}
║                                                          ║
╠══════════════════════════════════════════════════════════╣
║  PLATFORM                                                ║
║                                                          ║
║  Release      {release_ver}
║  ASI (Architecture Stability Index)                      ║
║    {current_quarter.get('quarter','?')}  변경 {current_quarter.get('changes',0)}회  {'🟢' if current_quarter.get('changes',0) == 0 else '🔴 Evidence 있음?'}
║    누적 변경: {arch_changes}회  (변경 시 logs/asi.jsonl에 기록)
║                                                          ║
║  v2.8.0 릴리스 조건                                      ║
║    BUY ≥ 100:  {ok(dl_buy_total >= 100)} {dl_buy_total}/100
║    Predictions ≥ 50: {ok((q("SELECT COUNT(*) FROM scientist_predictions")[0]) >= 50)} {q("SELECT COUNT(*) FROM scientist_predictions")[0]}/50
║    Calibration 산출: {ok(cal_row and cal_row[0] and cal_row[0] > 0)}
║    Health ≥ 8주: ⚪ 수동 확인
║    Hypo→Deploy 1회: ⚪ 수동 확인
║                                                          ║
╚══════════════════════════════════════════════════════════╝
""".strip())
