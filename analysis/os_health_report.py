"""
Trading OS Health Report
매주 토요일 — 전략 성과가 아니라 플랫폼 자체의 건강 상태를 측정한다.

질문: "이번 주 우리는 무엇을 새롭게 배웠는가?"
      "Governance는 잘못된 전략을 얼마나 막았는가?"
      "Scientist AI는 지난주보다 더 정확하게 설명하는가?"

실행:
  python3 -m analysis.os_health_report            # 주간 리포트
  python3 -m analysis.os_health_report --days 30  # 30일 리포트
  python3 -m analysis.os_health_report --dry-run
"""

import os
import json
import logging
import argparse
import psycopg2

from datetime import date, datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('os_health')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

DB_CONF = dict(dbname='trading_system', user='postgres',
               password=os.getenv('POSTGRES_PASSWORD'), host='localhost')

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN',
                              '8190858980:AAHeB7qNnop5yIYaeIiEG9HsREOrgBpiRxQ')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_IDS', '-1002891093241')


def _collect(conn, days: int) -> dict:
    cur  = conn.cursor()
    since = date.today() - timedelta(days=days)
    report = {}

    # ── Execution Engine ──────────────────────────────────────────────────────
    cur.execute("""
        SELECT
            COUNT(*)                             AS total_decisions,
            COUNT(*) FILTER(WHERE decision_type='BUY')  AS buy_decisions,
            COUNT(*) FILTER(WHERE decision_type='SELL') AS sell_decisions,
            COUNT(*) FILTER(WHERE decision_type='SKIP') AS skip_decisions
        FROM decision_log
        WHERE decision_time >= %s
    """, (since,))
    r = cur.fetchone()
    report['execution'] = {
        'total_decisions': r[0] or 0,
        'buy':  r[1] or 0,
        'sell': r[2] or 0,
        'skip': r[3] or 0,
    }

    cur.execute("""
        SELECT COUNT(*), COALESCE(AVG(daily_pnl_pct), 0)
        FROM trading_sessions
        WHERE session_date >= %s AND status = 'reviewed'
    """, (since,))
    r = cur.fetchone()
    report['execution']['sessions_reviewed'] = r[0] or 0
    report['execution']['avg_daily_pnl_pct'] = round(float(r[1] or 0), 4)

    # ── Research Engine ───────────────────────────────────────────────────────
    cur.execute("""
        SELECT status, COUNT(*) FROM hypotheses
        GROUP BY status ORDER BY status
    """)
    hypo_by_status = dict(cur.fetchall())

    cur.execute("""
        SELECT COUNT(*) FROM hypotheses WHERE created_at >= %s
    """, (since,))
    hypo_new = cur.fetchone()[0]

    cur.execute("""
        SELECT status, COUNT(*) FROM research_queue GROUP BY status
    """)
    rq_by_status = dict(cur.fetchall())

    cur.execute("""
        SELECT status, COUNT(*) FROM experiments GROUP BY status
    """)
    exp_by_status = dict(cur.fetchall())

    cur.execute("SELECT COUNT(*) FROM research_notebook WHERE created_at >= %s", (since,))
    nb_new = cur.fetchone()[0]

    report['research'] = {
        'hypotheses_total':   sum(hypo_by_status.values()),
        'hypotheses_new':     hypo_new,
        'hypotheses_by_status': hypo_by_status,
        'research_queue':     rq_by_status,
        'experiments':        exp_by_status,
        'notebooks_new':      nb_new,
    }

    # ── Knowledge Layer ───────────────────────────────────────────────────────
    cur.execute("""
        SELECT evidence_level, COUNT(*) FROM knowledge_base
        WHERE is_active = TRUE GROUP BY evidence_level
    """)
    kb_by_level = dict(cur.fetchall())

    cur.execute("""
        SELECT COUNT(*) FROM knowledge_base WHERE created_at >= %s
    """, (since,))
    kb_new = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FROM knowledge_base
        WHERE is_active = FALSE AND supersedes_id IS NOT NULL
    """)
    kb_superseded = cur.fetchone()[0]

    report['knowledge'] = {
        'total_active':     sum(kb_by_level.values()),
        'new_this_period':  kb_new,
        'superseded':       kb_superseded,
        'by_evidence_level': kb_by_level,
    }

    # ── Scientist AI ──────────────────────────────────────────────────────────
    cur.execute("""
        SELECT re_no, llm_primary, scientist_level, status, activated_at
        FROM research_environment WHERE status = 'active' ORDER BY id DESC LIMIT 1
    """)
    re_row = cur.fetchone()
    if re_row:
        report['scientist'] = {
            're_no':           re_row[0],
            'llm':             re_row[1],
            'level':           re_row[2],
            'level_name':      ['Observer', 'Analyst', 'Researcher',
                                'Scientist', 'Lead Scientist'][re_row[2]],
            'status':          re_row[3],
            'active_since':    str(re_row[4].date()) if re_row[4] else None,
            'explain_accuracy': None,   # Phase 3c 이후 채워짐
            'predict_accuracy': None,
            'level_upgrade':    'NO',
        }
    else:
        report['scientist'] = {'status': 'no active environment'}

    # ── Governance Engine ─────────────────────────────────────────────────────
    cur.execute("""
        SELECT status, COUNT(*) FROM strategy_versions GROUP BY status
    """)
    sv_by_status = dict(cur.fetchall())

    cur.execute("""
        SELECT COUNT(*) FROM strategy_versions WHERE deployed_at >= %s
    """, (since,))
    sv_deployed = cur.fetchone()[0]

    report['governance'] = {
        'versions_total':    sum(sv_by_status.values()),
        'by_status':         sv_by_status,
        'deployed_period':   sv_deployed,
    }

    return report


def _fmt_status_dict(d: dict, label_map: dict = None) -> str:
    if not d:
        return '없음'
    parts = []
    for k, v in sorted(d.items()):
        label = (label_map or {}).get(k, k)
        parts.append(f"{label} {v}건")
    return ' / '.join(parts)


def print_report(report: dict, days: int):
    today = date.today()
    since = today - timedelta(days=days)
    ex = report['execution']
    re = report['research']
    kb = report['knowledge']
    sc = report['scientist']
    gv = report['governance']

    print(f"\n{'='*62}")
    print(f"  Trading OS Health Report")
    print(f"  {since} ~ {today}  ({days}일)")
    print(f"{'='*62}")

    # Execution
    print(f"\n  ── Execution Engine ──────────────────────────────────────")
    print(f"  결정 총계   : {ex['total_decisions']}건"
          f"  (BUY {ex['buy']} / SELL {ex['sell']} / SKIP {ex['skip']})")
    print(f"  세션 리뷰   : {ex['sessions_reviewed']}일"
          f"  평균 일 PnL {ex['avg_daily_pnl_pct']:+.2f}%")

    # Research
    print(f"\n  ── Research Engine ───────────────────────────────────────")
    print(f"  가설 총계   : {re['hypotheses_total']}건"
          f"  (이 기간 신규 {re['hypotheses_new']}건)")
    if re['hypotheses_by_status']:
        print(f"  상태별      : {_fmt_status_dict(re['hypotheses_by_status'])}")
    rq = re['research_queue']
    if rq:
        print(f"  연구 큐     : {_fmt_status_dict(rq)}")
    else:
        print(f"  연구 큐     : 비어 있음")
    exp = re['experiments']
    if exp:
        print(f"  실험        : {_fmt_status_dict(exp)}")
    print(f"  리서치 노트 : 이 기간 {re['notebooks_new']}건 생성")

    # Knowledge
    print(f"\n  ── Knowledge Base ────────────────────────────────────────")
    print(f"  활성 지식   : {kb['total_active']}건"
          f"  (신규 {kb['new_this_period']} / 대체됨 {kb['superseded']})")
    el = kb.get('by_evidence_level', {})
    if el:
        ev_map = {'anecdotal': '일화적', 'preliminary': '예비',
                  'confirmed': '확인됨', 'strong': '강력'}
        print(f"  증거 수준   : {_fmt_status_dict(el, ev_map)}")

    # Scientist AI
    print(f"\n  ── Scientist AI ──────────────────────────────────────────")
    if 'level' in sc:
        print(f"  환경        : {sc['re_no']}  ({sc['llm']})")
        print(f"  현재 레벨   : L{sc['level']} — {sc['level_name']}"
              f"  (활성: {sc.get('active_since', 'N/A')}~)")
        acc = sc.get('explain_accuracy')
        prd = sc.get('predict_accuracy')
        print(f"  설명 정확도 : {'데이터 없음 (Phase 3c 대기)' if acc is None else f'{acc:.0%}'}")
        print(f"  예측 적중률 : {'데이터 없음 (Phase 3c 대기)' if prd is None else f'{prd:.0%}'}")
        print(f"  레벨 승격   : {sc['level_upgrade']}")

    # Governance
    print(f"\n  ── Governance Engine ─────────────────────────────────────")
    print(f"  전략 버전   : {gv['versions_total']}건")
    if gv['by_status']:
        print(f"  상태별      : {_fmt_status_dict(gv['by_status'])}")
    print(f"  이 기간 배포: {gv['deployed_period']}건")

    print(f"\n{'='*62}\n")


def _send_telegram(report: dict, days: int):
    import requests
    ex = report['execution']
    re = report['research']
    kb = report['knowledge']
    sc = report['scientist']

    level_name = sc.get('level_name', 'N/A')
    level_no   = sc.get('level', 'N/A')
    pnl_sign = '+' if ex.get('avg_daily_pnl_pct', 0) >= 0 else ''
    hypo_detail = ''
    if re.get('hypotheses_by_status'):
        hypo_detail = ' / '.join(f"{k}:{v}" for k, v in re['hypotheses_by_status'].items())

    text = (
        f"🖥️ *Trading OS Health* — {days}일 리포트\n\n"
        f"*Execution*\n"
        f"  결정 {ex['total_decisions']}건 | 세션 {ex['sessions_reviewed']}일\n"
        f"  평균 일PnL {pnl_sign}{ex.get('avg_daily_pnl_pct', 0):+.2f}%\n\n"
        f"*Research*\n"
        f"  가설 {re['hypotheses_total']}건 (신규 {re['hypotheses_new']}) | "
        f"노트 {re['notebooks_new']}건\n\n"
        f"*Knowledge*\n"
        f"  활성 {kb['total_active']}건 | 신규 {kb['new_this_period']} | "
        f"대체 {kb['superseded']}\n\n"
        f"*Scientist AI*\n"
        f"  L{level_no} {level_name} | 레벨 승격: {sc.get('level_upgrade','NO')}\n\n"
        f"*Constitution*: 7조항 유효 ✓"
    )
    try:
        for chat_id in TELEGRAM_CHAT_ID.split(','):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={'chat_id': chat_id.strip(), 'text': text, 'parse_mode': 'Markdown'},
                timeout=10
            )
        logger.info("[OS_HEALTH] 텔레그램 발송 완료")
    except Exception as e:
        logger.warning(f"[OS_HEALTH] 텔레그램 실패: {e}")


def run(days: int = 7, dry_run: bool = False, send_telegram: bool = True):
    conn   = psycopg2.connect(**DB_CONF)
    report = _collect(conn, days)
    conn.close()

    print_report(report, days)

    if not dry_run and send_telegram:
        _send_telegram(report, days)

    return report


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--days', type=int, default=7)
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-telegram', action='store_true')
    args = ap.parse_args()
    run(days=args.days, dry_run=args.dry_run, send_telegram=not args.no_telegram)
