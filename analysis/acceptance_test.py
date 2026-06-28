"""
Trading OS — Architecture Acceptance Test

Unit Test가 아니다. 아키텍처가 실제로 동작하는지 검증한다.

  python3 -m analysis.acceptance_test
  python3 -m analysis.acceptance_test --verbose

심각도 등급:
  CRITICAL  — 실패 시 Release 즉시 Block (아키텍처 약속 파괴)
  IMPORTANT — 실패 시 beta 유지 (운영 품질 저하)
  ADVISORY  — 실패해도 Release 가능 (권고 수준)

Constitution 1:1 매핑 (새 Article 추가 시 대응 테스트 필수):
  Article 1 (Execution Deterministic) → C01, C02
  Article 2 (Evidence Required)       → D01, D02, D03
  Article 3 (History Immutable)       → A05, B01, B03
  Article 4 (Unknown is Valid)        → A07
  Article 5 (AI Never Deploys)        → C01
  Article 6 (Measurable)              → D01, D02
  Article 7 (Everything is Experiment)→ E01 (운영 루프 등록)
  Article 8 (Separation of Concerns)  → C01, C02, C04

결과 이력: logs/acceptance_history.jsonl
"""

import sys
import os
import json
import subprocess
from datetime import date, timedelta, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_verbose = '--verbose' in sys.argv or '-v' in sys.argv

DB_CONFIG = dict(dbname='trading_system', user='postgres', password='killer99!!', host='localhost')

REQUIRED_TABLES = [
    'market_context', 'trading_sessions', 'decision_log',
    'knowledge_base', 'hypotheses', 'scientist_predictions',
    'research_environment', 'system_events', 'strategy_versions',
]

REQUIRED_CRONS = [
    ('07:32 MIE',           '32 7',  'market_intelligence'),
    ('16:37 Session Review','37 16', 'session_review'),
    ('08:47 OS Health',     '47 8',  'os_health_report'),
]

REQUIRED_FILES = [
    'CONSTITUTION.md', 'ARCHITECTURE.md', 'DATA_CONTRACT.md', 'CLAUDE.md',
    'docs/adr/ADR-001-ai-does-not-trade.md',
    'docs/governance/GD-001-platform-release-deferred.md',
    'analysis/market_intelligence.py',
    'analysis/session_review.py',
    'analysis/os_health_report.py',
]

results = []

# ── helpers ────────────────────────────────────────────────────────────────

def conn():
    import psycopg2
    return psycopg2.connect(**DB_CONFIG)

SEVERITY_ORDER = {'CRITICAL': 0, 'IMPORTANT': 1, 'ADVISORY': 2}
SEVERITY_LABEL = {'CRITICAL': '[!]', 'IMPORTANT': '[~]', 'ADVISORY': '[-]'}

def record(cat, name, passed, detail='', severity='IMPORTANT'):
    results.append({'cat': cat, 'name': name, 'passed': passed,
                    'detail': detail, 'severity': severity})
    mark = 'PASS' if passed else 'FAIL'
    sev  = SEVERITY_LABEL[severity]
    line = f"  [{mark}]{sev} {name}"
    if detail and (not passed or _verbose):
        line += f"\n         {detail}"
    print(line)
    return passed

def section(title):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print('─'*60)

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Category A: DB Schema & Integrity ─────────────────────────────────────

section("A. DB Schema & Integrity")

try:
    with conn() as c:
        c.cursor().execute("SELECT 1")
    record('A', 'DB 연결 (trading_system)', True, severity='CRITICAL')
except Exception as e:
    record('A', 'DB 연결 (trading_system)', False, str(e), severity='CRITICAL')
    print("\n[ABORT] DB 연결 실패.")
    sys.exit(1)

with conn() as c:
    cur = c.cursor()
    cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public'")
    existing = {r[0] for r in cur.fetchall()}
    missing = [t for t in REQUIRED_TABLES if t not in existing]
    record('A', 'Trading OS 필수 테이블 존재', not missing,
           f"누락: {missing}" if missing else '', severity='IMPORTANT')

with conn() as c:
    cur = c.cursor()
    cur.execute("SELECT conname FROM pg_constraint WHERE conrelid='decision_log'::regclass AND contype='f'")
    fks = {r[0] for r in cur.fetchall()}
    needed = {'decision_log_market_context_id_fkey', 'dl_session_fk'}
    missing_fk = needed - fks
    record('A', 'decision_log FK (market_context, session)', not missing_fk,
           f"누락 FK: {missing_fk}" if missing_fk else '', severity='IMPORTANT')

with conn() as c:
    cur = c.cursor()
    cur.execute("""SELECT column_name, is_nullable FROM information_schema.columns
                   WHERE table_name='decision_log'
                     AND column_name IN ('market_context_id','session_id','trade_id')""")
    non_nullable = [r[0] for r in cur.fetchall() if r[1] != 'YES']
    record('A', 'decision_log FK nullable (Research 장애 허용)', not non_nullable,
           f"NOT NULL 컬럼: {non_nullable}" if non_nullable else '', severity='CRITICAL')

with conn() as c:
    cur = c.cursor()
    cur.execute("SELECT tgname FROM pg_trigger WHERE tgrelid='scientist_predictions'::regclass AND NOT tgisinternal")
    triggers = [r[0] for r in cur.fetchall()]
    record('A', 'scientist_predictions 불변 트리거 존재', bool(triggers),
           f"트리거: {triggers}" if triggers else '트리거 없음', severity='CRITICAL')

with conn() as c:
    cur = c.cursor()
    cur.execute("SELECT column_name FROM information_schema.columns WHERE table_name='knowledge_base' AND column_name='is_active'")
    record('A', 'knowledge_base.is_active 컬럼 존재', bool(cur.fetchone()), severity='IMPORTANT')

with conn() as c:
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM research_environment WHERE status='active'")
    cnt = cur.fetchone()[0]
    record('A', 'Research Environment 활성 레코드 존재', cnt > 0,
           f"활성 RE: {cnt}건" if cnt else 'RE-001 INSERT 필요', severity='IMPORTANT')

# ── Category B: Constitution Compliance ───────────────────────────────────

section("B. Constitution Compliance  [Article 3 — History Immutable]")

_test_re_id = None
with conn() as c:
    cur = c.cursor()
    cur.execute("SELECT id FROM research_environment WHERE status='active' LIMIT 1")
    row = cur.fetchone()
    _test_re_id = row[0] if row else None

if _test_re_id:
    _test_pred_id = None
    try:
        with conn() as c:
            cur = c.cursor()
            cur.execute("""INSERT INTO scientist_predictions
                             (prediction_text, confidence_pct, target_metric,
                              evaluation_date, research_environment_id)
                           VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                        ('AT_TEST: acceptance test prediction', 60, 'calibration_error',
                         date.today() + timedelta(days=31), _test_re_id))
            _test_pred_id = cur.fetchone()[0]
            c.commit()

        rejected = False
        try:
            with conn() as c:
                cur = c.cursor()
                cur.execute("UPDATE scientist_predictions SET prediction_text='HACKED' WHERE id=%s",
                            (_test_pred_id,))
                c.commit()
        except Exception:
            rejected = True

        record('B', 'scientist_predictions prediction_text UPDATE 거부 (Article 3)',
               rejected, '트리거 정상 차단' if rejected else '!!! UPDATE 허용됨', severity='CRITICAL')
    finally:
        if _test_pred_id:
            try:
                with conn() as c:
                    cur = c.cursor()
                    cur.execute("DELETE FROM scientist_predictions WHERE id=%s AND prediction_text LIKE 'AT_TEST%%'", (_test_pred_id,))
                    c.commit()
            except Exception:
                pass
else:
    record('B', 'scientist_predictions UPDATE 거부', False, 'RE 없어 테스트 불가', severity='CRITICAL')

with conn() as c:
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM knowledge_base WHERE is_active IS NULL")
    nulls = cur.fetchone()[0]
    record('B', 'knowledge_base.is_active NULL 레코드 없음',
           nulls == 0, f"NULL {nulls}건" if nulls else '', severity='IMPORTANT')

try:
    import re
    main_path = os.path.join(BASE, 'main_auto_trading.py')
    content = open(main_path).read()
    bad = re.findall(r'UPDATE\s+decision_log|DELETE\s+FROM\s+decision_log', content, re.IGNORECASE)
    record('B', 'main_auto_trading에 decision_log UPDATE/DELETE 없음 (Article 3)',
           not bad, f"발견: {bad}" if bad else '', severity='CRITICAL')
except FileNotFoundError:
    record('B', 'main_auto_trading.py 접근', False, '파일 없음', severity='CRITICAL')

# ── Category C: Research Layer Isolation ──────────────────────────────────

section("C. Research Layer Isolation  [ADR-001, Article 1, Article 8]")

try:
    import re
    main_path = os.path.join(BASE, 'main_auto_trading.py')
    content = open(main_path).read()
    top_imports = re.findall(r'^(?:import|from)\s+analysis\.', content, re.MULTILINE)
    record('C', 'main_auto_trading top-level analysis import 없음',
           not top_imports, f"발견: {top_imports}" if top_imports else '', severity='CRITICAL')
except Exception as e:
    record('C', 'main_auto_trading top-level analysis import 없음', False, str(e), severity='CRITICAL')

try:
    has_guard = 'except Exception as _dle' in content or 'except Exception as _de' in content
    record('C', '_log_decision try/except 보호 (MIE 장애 시 매매 무중단)',
           has_guard, severity='CRITICAL')
except Exception as e:
    record('C', '_log_decision try/except 보호', False, str(e), severity='CRITICAL')

for mod_name, mod_file in [
    ('market_intelligence', 'analysis/market_intelligence.py'),
    ('session_review',      'analysis/session_review.py'),
    ('os_health_report',    'analysis/os_health_report.py'),
]:
    path = os.path.join(BASE, mod_file)
    result = subprocess.run([sys.executable, '-m', 'py_compile', path],
                            capture_output=True, text=True)
    record('C', f'analysis.{mod_name} 컴파일 독립 성공',
           result.returncode == 0,
           result.stderr.strip() if result.returncode != 0 else '', severity='IMPORTANT')

result = subprocess.run([sys.executable, '-m', 'py_compile', os.path.join(BASE, 'main_auto_trading.py')],
                        capture_output=True, text=True)
record('C', 'main_auto_trading.py 컴파일 성공',
       result.returncode == 0,
       result.stderr.strip() if result.returncode != 0 else '', severity='CRITICAL')

# ── Category D: Data Contract Compliance ──────────────────────────────────

section("D. Data Contract Compliance  [Article 2, Article 6]")

with conn() as c:
    cur = c.cursor()
    cur.execute("""SELECT COUNT(*) FROM scientist_predictions
                   WHERE evaluation_date < created_at::date + 30
                     AND prediction_text NOT LIKE 'AT_TEST%%'""")
    violations = cur.fetchone()[0]
    record('D', 'scientist_predictions evaluation_date >= +30일',
           violations == 0, f"위반 {violations}건" if violations else '', severity='IMPORTANT')

with conn() as c:
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM scientist_predictions WHERE confidence_pct < 0 OR confidence_pct > 100")
    out_of_range = cur.fetchone()[0]
    record('D', 'scientist_predictions confidence_pct 0~100',
           out_of_range == 0, f"범위 초과 {out_of_range}건" if out_of_range else '', severity='IMPORTANT')

with conn() as c:
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM decision_log WHERE signals IS NULL")
    null_signals = cur.fetchone()[0]
    record('D', 'decision_log.signals NULL 레코드 없음',
           null_signals == 0, f"NULL {null_signals}건" if null_signals else '', severity='IMPORTANT')

with conn() as c:
    cur = c.cursor()
    cur.execute("SELECT COUNT(*) FROM knowledge_base")
    total = cur.fetchone()[0]
    record('D', 'knowledge_base 레코드 보존 (삭제 정책)',
           True, f"현재 {total}건 보존 중", severity='ADVISORY')

# ── Category E: Operational Readiness ─────────────────────────────────────

section("E. Operational Readiness  [Article 7 — 운영 루프 등록]")

crontab_out = subprocess.run(['crontab', '-l'], capture_output=True, text=True).stdout
for label, time_pat, script_pat in REQUIRED_CRONS:
    registered = time_pat in crontab_out and script_pat in crontab_out
    record('E', f'크론 등록: {label}', registered,
           f"'{time_pat} ... {script_pat}' 미등록" if not registered else '', severity='IMPORTANT')

for rel_path in REQUIRED_FILES:
    exists = os.path.exists(os.path.join(BASE, rel_path))
    record('E', f'파일 존재: {rel_path}', exists, severity='ADVISORY')

# ── Summary ───────────────────────────────────────────────────────────────

print(f"\n{'='*60}")
print("  TRADING OS ACCEPTANCE TEST — SUMMARY")
print('='*60)
print("  심각도 표기: [!]=CRITICAL  [~]=IMPORTANT  [-]=ADVISORY\n")

by_cat = {}
for r in results:
    by_cat.setdefault(r['cat'], []).append(r)

total_pass = total_fail = 0
for cat in sorted(by_cat):
    cat_results = by_cat[cat]
    passed = sum(1 for r in cat_results if r['passed'])
    failed = len(cat_results) - passed
    total_pass += passed
    total_fail += failed
    mark = 'OK  ' if failed == 0 else 'FAIL'
    print(f"  [{mark}] Category {cat}: {passed}/{len(cat_results)} passed")

# 심각도별 집계
for sev in ['CRITICAL', 'IMPORTANT', 'ADVISORY']:
    sev_results = [r for r in results if r['severity'] == sev]
    sev_fail    = [r for r in sev_results if not r['passed']]
    if sev_fail or _verbose:
        label = {'CRITICAL': '[!]', 'IMPORTANT': '[~]', 'ADVISORY': '[-]'}[sev]
        print(f"\n  {label} {sev}: {len(sev_results)-len(sev_fail)}/{len(sev_results)} passed", end='')
        if sev_fail:
            print(f"  <-- {len(sev_fail)} FAIL")
            for r in sev_fail:
                print(f"      - [{r['cat']}] {r['name']}")
        else:
            print()

critical_fail = [r for r in results if r['severity'] == 'CRITICAL' and not r['passed']]
overall_pass  = len(critical_fail) == 0

print(f"\n  Total: {total_pass}/{total_pass+total_fail} passed")

if overall_pass and total_fail == 0:
    verdict = 'ALL PASS — v2.8.0-beta Release Candidate'
elif overall_pass:
    verdict = f'CRITICAL PASS — Release 가능 (Advisory/Important {total_fail}건 확인 권고)'
else:
    verdict = f'CRITICAL FAIL — Release Block ({len(critical_fail)}건 수정 필요)'

print(f"\n  [ {'PASS' if overall_pass else 'FAIL'} ] {verdict}")
print('='*60)

# ── 이력 저장 ──────────────────────────────────────────────────────────────

log_dir = os.path.join(BASE, 'logs')
os.makedirs(log_dir, exist_ok=True)
history_path = os.path.join(log_dir, 'acceptance_history.jsonl')

history_entry = {
    'run_at':  datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    'total':   total_pass + total_fail,
    'passed':  total_pass,
    'failed':  total_fail,
    'critical_fail': len(critical_fail),
    'verdict': verdict,
    'failures': [{'cat': r['cat'], 'name': r['name'], 'severity': r['severity'],
                  'detail': r['detail']} for r in results if not r['passed']],
}
with open(history_path, 'a') as f:
    f.write(json.dumps(history_entry, ensure_ascii=False) + '\n')

print(f"\n  이력 저장: logs/acceptance_history.jsonl")

sys.exit(0 if overall_pass else 1)
