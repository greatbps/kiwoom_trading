"""
Scientist Scorecard — Monthly KPI Report
ADR-006: Scientist AI 자체 품질 측정

역할: Scientist AI의 KPI를 매월 측정 및 보고
  - Unknown Declaration Rate   (목표: 15~30%)
  - Coverage Rate              (목표: ≥1 NB/주)
  - Calibration Error          (목표: ≤15%p, L1+ 이후)
  - L1 Readiness               (목표: NB ≥ 10건)
  - Data Quality Gap           (regime/MFE/pattern fill% 추이)

출력: research_notebook Append-Only (Article 3)

실행:
  python3 -m analysis.scientist_scorecard              # 이번달 집계
  python3 -m analysis.scientist_scorecard --dry-run
  python3 -m analysis.scientist_scorecard --months 3   # 최근 3개월
"""

import os
import json
import logging
import argparse
import psycopg2
import anthropic

from datetime import date, datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('scientist_scorecard')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

DB_CONF = dict(dbname='trading_system', user='postgres',
               password=os.getenv('POSTGRES_PASSWORD'), host='localhost')

CLAUDE_MODEL = 'claude-haiku-4-5-20251001'

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN',
                              '8190858980:AAHeB7qNnop5yIYaeIiEG9HsREOrgBpiRxQ')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_IDS', '-1002891093241')

# KPI 목표값
TARGET_UNKNOWN_RATE_MIN = 15   # %
TARGET_UNKNOWN_RATE_MAX = 30   # %
TARGET_CALIBRATION_ERR  = 15   # %p
TARGET_NB_PER_WEEK      = 1.0
TARGET_L1_NB_COUNT      = 10


# ── 1. 데이터 수집 ─────────────────────────────────────────────────────────────

def _collect_scorecard_data(conn, months: int) -> dict:
    cur = conn.cursor()
    since = date.today() - timedelta(days=30 * months)
    data  = {'since': str(since), 'months': months, 'today': str(date.today())}

    # ── Scientist NB 전체 이력 ──────────────────────────────────────────────────
    cur.execute("""
        SELECT
            notebook_no, confidence, created_at,
            data_scope->>'unknown_declaration' AS unknown_decl,
            data_scope->>'mode'                AS mode,
            data_scope->'data_quality'         AS dq
        FROM research_notebook
        WHERE tags @> ARRAY['scientist_review']
        ORDER BY created_at
    """)
    rows = cur.fetchall()
    nb_entries = [
        {
            'no':        r[0],
            'confidence': r[1],
            'date':       r[2].date() if r[2] else None,
            'unknown':    r[3],
            'mode':       r[4],
            'dq':         r[5],
        }
        for r in rows
    ]
    data['nb_entries'] = nb_entries
    data['nb_total']   = len(nb_entries)

    # ── Unknown Declaration Rate ───────────────────────────────────────────────
    # "모른다 선언"은: confidence < 40 OR unknown_declaration IN ('불충분', '부분')
    declared_unknown = [
        e for e in nb_entries
        if (e['confidence'] is not None and e['confidence'] < 40)
        or e['unknown'] in ('불충분', '부분')
    ]
    data['unknown_rate'] = {
        'total':           len(nb_entries),
        'declared':        len(declared_unknown),
        'rate_pct':        round(len(declared_unknown) / len(nb_entries) * 100, 1)
                           if nb_entries else 0,
        'target_min':      TARGET_UNKNOWN_RATE_MIN,
        'target_max':      TARGET_UNKNOWN_RATE_MAX,
    }

    # ── Coverage Rate ──────────────────────────────────────────────────────────
    if nb_entries:
        first_date = nb_entries[0]['date']
        days_since = (date.today() - first_date).days or 1
        weeks_since = max(days_since / 7, 1)
        nb_per_week = round(len(nb_entries) / weeks_since, 2)
    else:
        weeks_since = nb_per_week = 0.0

    data['coverage'] = {
        'nb_per_week':  nb_per_week,
        'target':       TARGET_NB_PER_WEEK,
        'weeks_active': round(weeks_since, 1),
        'status': (
            'ABOVE_TARGET' if nb_per_week >= TARGET_NB_PER_WEEK
            else 'BELOW_TARGET'
        ),
    }

    # ── L1 Readiness ──────────────────────────────────────────────────────────
    data['l1_readiness'] = {
        'nb_count':           len(nb_entries),
        'nb_needed':          TARGET_L1_NB_COUNT,
        'pct_complete':       round(len(nb_entries) / TARGET_L1_NB_COUNT * 100),
        'governance_needed':  len(nb_entries) >= TARGET_L1_NB_COUNT,
    }

    # ── Calibration (scientist_predictions) ──────────────────────────────────
    cur.execute("""
        SELECT
            COUNT(*)                                        AS total,
            COUNT(*) FILTER (WHERE status='evaluated')     AS evaluated,
            ROUND(AVG(calibration_error)
                  FILTER (WHERE status='evaluated')::numeric, 2)  AS avg_calib,
            COUNT(*) FILTER (WHERE status='evaluated'
                             AND outcome_correct=TRUE)     AS correct,
            COUNT(*) FILTER (WHERE status='pending')       AS pending
        FROM scientist_predictions
        WHERE created_at >= %s
    """, (since,))
    r = cur.fetchone()
    data['calibration'] = {
        'total':           r[0] or 0,
        'evaluated':       r[1] or 0,
        'avg_error_pct':   float(r[2]) if r[2] is not None else None,
        'correct':         r[3] or 0,
        'pending':         r[4] or 0,
        'target_error':    TARGET_CALIBRATION_ERR,
        'available':       (r[1] or 0) > 0,
    }

    # ── Data Quality Gap ──────────────────────────────────────────────────────
    # 최신 Scientist NB의 data_quality + 추이 비교
    recent_dq = None
    if nb_entries:
        latest = nb_entries[-1]
        if latest.get('dq'):
            try:
                recent_dq = latest['dq'] if isinstance(latest['dq'], dict) else json.loads(latest['dq'])
            except Exception:
                pass

    # 비교: 첫 번째 NB와 최신 NB의 data_quality 변화
    first_dq = None
    if len(nb_entries) > 1:
        first = nb_entries[0]
        if first.get('dq'):
            try:
                first_dq = first['dq'] if isinstance(first['dq'], dict) else json.loads(first['dq'])
            except Exception:
                pass

    data['data_quality'] = {
        'latest':  recent_dq,
        'first':   first_dq,
        'improving': None,  # 비교 불가 at L0 (only 1 entry)
    }

    # ── decision_log 현황 ──────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM decision_log WHERE decision_time >= %s", (since,))
    data['decision_log_count'] = cur.fetchone()[0] or 0

    return data


# ── 2. KPI 상태 판정 ──────────────────────────────────────────────────────────

def _rate_kpi(value, target_min=None, target_max=None, lower_is_better=False) -> str:
    if value is None:
        return 'N/A'
    if lower_is_better:
        return 'OK' if value <= target_min else 'WARN'
    if target_min is not None and target_max is not None:
        return 'OK' if target_min <= value <= target_max else 'WARN'
    if target_min is not None:
        return 'OK' if value >= target_min else 'WARN'
    return 'N/A'


def _compute_kpi_status(data: dict) -> dict:
    unk  = data['unknown_rate']
    cov  = data['coverage']
    cal  = data['calibration']
    l1   = data['l1_readiness']

    return {
        'unknown_rate': _rate_kpi(
            unk['rate_pct'],
            target_min=TARGET_UNKNOWN_RATE_MIN,
            target_max=TARGET_UNKNOWN_RATE_MAX
        ),
        'coverage':     _rate_kpi(cov['nb_per_week'], target_min=TARGET_NB_PER_WEEK),
        'calibration':  'N/A' if not cal['available']
                        else _rate_kpi(cal['avg_error_pct'],
                                       target_min=TARGET_CALIBRATION_ERR,
                                       lower_is_better=True),
        'l1_readiness': _rate_kpi(l1['nb_count'], target_min=TARGET_L1_NB_COUNT),
    }


# ── 3. Claude 내러티브 (간략) ──────────────────────────────────────────────────

def _get_narrative(data: dict, kpis: dict) -> str:
    unk  = data['unknown_rate']
    cov  = data['coverage']
    cal  = data['calibration']
    l1   = data['l1_readiness']
    dq   = data['data_quality'].get('latest') or {}

    prompt = f"""You are evaluating the Scientist AI's monthly scorecard for a Korean swing-trading system.

KPI RESULTS:
  Unknown Declaration Rate: {unk['rate_pct']}%  (target: {TARGET_UNKNOWN_RATE_MIN}~{TARGET_UNKNOWN_RATE_MAX}%)  [{kpis['unknown_rate']}]
  Coverage (NB/week):       {cov['nb_per_week']}  (target: ≥{TARGET_NB_PER_WEEK})  [{kpis['coverage']}]
  Calibration Error:        {"N/A (L0 — 예측 없음)" if not cal['available'] else f"{cal['avg_error_pct']}%p (target: ≤{TARGET_CALIBRATION_ERR}%p)"}  [{kpis['calibration']}]
  L1 Readiness:             {l1['nb_count']}/{l1['nb_needed']}건  [{kpis['l1_readiness']}]

DATA QUALITY (latest NB):
  regime_fill:   {dq.get('regime_fill_pct', 0)}%
  mfe_fill:      {dq.get('mfe_fill_pct', 0)}%
  pattern_fill:  {dq.get('pattern_fill_pct', 0)}%

Return a 2-sentence Korean narrative. Focus on:
  1. What the KPIs tell us about the Scientist AI's current state
  2. One concrete next step to improve the weakest KPI

Rules: NO strategy change suggestions. Observe and interpret only.
Return plain text, no JSON, no markdown."""

    try:
        client = anthropic.Anthropic()
        resp   = client.messages.create(
            model=CLAUDE_MODEL, max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        logger.warning(f"[SCORECARD] Claude 내러티브 실패: {e}")
        return "(내러티브 생성 실패)"


# ── 4. 콘솔 출력 ──────────────────────────────────────────────────────────────

_STATUS_ICON = {'OK': '✓', 'WARN': '!', 'N/A': '-'}

def _print_scorecard(data: dict, kpis: dict, narrative: str):
    unk  = data['unknown_rate']
    cov  = data['coverage']
    cal  = data['calibration']
    l1   = data['l1_readiness']
    dq   = data['data_quality'].get('latest') or {}

    print(f"\n{'='*65}")
    print(f"  SCIENTIST SCORECARD — {data['today']}  (최근 {data['months']}개월)")
    print(f"{'='*65}")

    def row(label, value, target, status):
        icon = _STATUS_ICON.get(status, '?')
        print(f"  [{icon}] {label:<30s}  {str(value):<12s}  (목표: {target})")

    row('Unknown Declaration Rate',
        f"{unk['rate_pct']}%",
        f"{TARGET_UNKNOWN_RATE_MIN}~{TARGET_UNKNOWN_RATE_MAX}%",
        kpis['unknown_rate'])

    row('Coverage (NB/주)',
        f"{cov['nb_per_week']}건/주",
        f"≥{TARGET_NB_PER_WEEK}건",
        kpis['coverage'])

    row('Calibration Error',
        f"{cal['avg_error_pct']}%p" if cal['available'] else 'N/A (L0)',
        f"≤{TARGET_CALIBRATION_ERR}%p",
        kpis['calibration'])

    row('L1 Readiness (NB count)',
        f"{l1['nb_count']}/{l1['nb_needed']}건",
        f"≥{TARGET_L1_NB_COUNT}건",
        kpis['l1_readiness'])

    print(f"\n  Data Quality (최신 NB 기준):")
    print(f"    regime_fill:   {dq.get('regime_fill_pct', 0)}%"
          f"  |  mfe_fill: {dq.get('mfe_fill_pct', 0)}%"
          f"  |  pattern_fill: {dq.get('pattern_fill_pct', 0)}%")
    print(f"    decision_log:  {data['decision_log_count']}건 (분석 기간)")

    print(f"\n  Narrative:")
    print(f"  {narrative}")

    if l1['governance_needed']:
        print(f"\n  🔔 L1 승격 조건 충족 — Governance AI 검토 필요")
        print(f"     python3 -m analysis.governance_ai "
              f"--proposal 'Scientist AI L0→L1 승격'")
    print()


# ── 5. research_notebook 저장 ──────────────────────────────────────────────────

def _next_notebook_no(cur) -> str:
    cur.execute("""
        SELECT MAX(CAST(SUBSTRING(notebook_no FROM 4) AS INTEGER))
        FROM research_notebook WHERE notebook_no ~ '^NB-[0-9]+$'
    """)
    return f"NB-{(cur.fetchone()[0] or 0) + 1:03d}"


def _save_notebook(conn, data: dict, kpis: dict, narrative: str) -> tuple:
    cur   = conn.cursor()
    nb_no = _next_notebook_no(cur)

    cur.execute("SELECT id FROM research_environment WHERE status='active' LIMIT 1")
    row   = cur.fetchone()
    re_id = row[0] if row else None

    title = f"[Scientist Scorecard] {data['today']} — {data['months']}개월 집계"

    data_scope = {
        'period_months':    data['months'],
        'since':            data['since'],
        'nb_total':         data['nb_total'],
        'unknown_rate':     data['unknown_rate'],
        'coverage':         data['coverage'],
        'calibration':      data['calibration'],
        'l1_readiness':     data['l1_readiness'],
        'data_quality':     data['data_quality'],
        'decision_log_cnt': data['decision_log_count'],
        'kpi_status':       kpis,
    }

    overall_ok  = sum(1 for v in kpis.values() if v == 'OK')
    overall_warn = sum(1 for v in kpis.values() if v == 'WARN')
    tags = ['scientist_scorecard',
            f'ok_{overall_ok}', f'warn_{overall_warn}']

    cur.execute("""
        INSERT INTO research_notebook
          (notebook_no, title, research_question,
           data_scope, findings, conclusion,
           confidence, tags, model_used,
           research_environment_id, session_id,
           created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NULL,NOW(),NOW())
        RETURNING id
    """, (
        nb_no, title,
        'Scientist AI의 KPI가 목표 범위 내에 있는가?',
        json.dumps(data_scope, ensure_ascii=False, default=str),
        narrative,
        f"OK={overall_ok} WARN={overall_warn} N/A={sum(1 for v in kpis.values() if v=='N/A')}",
        None,
        tags, CLAUDE_MODEL,
        re_id,
    ))
    nb_id = cur.fetchone()[0]
    conn.commit()
    return nb_id, nb_no


# ── 6. Telegram 발송 ──────────────────────────────────────────────────────────

def _send_telegram(data: dict, kpis: dict, narrative: str, nb_no: str):
    import requests
    unk  = data['unknown_rate']
    cov  = data['coverage']
    cal  = data['calibration']
    l1   = data['l1_readiness']

    def icon(k): return {'OK': '✅', 'WARN': '⚠️', 'N/A': '⚪'}[kpis.get(k, 'N/A')]

    text = (
        f"📊 *Scientist Scorecard* — {nb_no} [{data['today']}]\n\n"
        f"{icon('unknown_rate')} Unknown Rate: {unk['rate_pct']}%"
        f" (목표 {TARGET_UNKNOWN_RATE_MIN}~{TARGET_UNKNOWN_RATE_MAX}%)\n"
        f"{icon('coverage')} Coverage: {cov['nb_per_week']}건/주\n"
        f"{icon('calibration')} Calibration: "
        f"{'N/A (L0)' if not cal['available'] else str(cal['avg_error_pct']) + '%p'}\n"
        f"{icon('l1_readiness')} L1 Readiness: {l1['nb_count']}/{l1['nb_needed']}건\n\n"
        f"_{narrative}_"
    )
    try:
        import requests as req
        for chat_id in TELEGRAM_CHAT_ID.split(','):
            req.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={'chat_id': chat_id.strip(), 'text': text, 'parse_mode': 'Markdown'},
                timeout=10
            )
        logger.info("[SCORECARD] 텔레그램 발송 완료")
    except Exception as e:
        logger.warning(f"[SCORECARD] 텔레그램 실패: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def run_scorecard(months: int = 1, dry_run: bool = False,
                  send_telegram: bool = True) -> dict:
    logger.info(f"[SCORECARD] Scientist KPI 집계 시작 (months={months})")

    conn = psycopg2.connect(**DB_CONF)
    data = _collect_scorecard_data(conn, months)

    logger.info(
        f"[SCORECARD] NB={data['nb_total']}건  "
        f"predictions={data['calibration']['total']}건  "
        f"decision_log={data['decision_log_count']}건"
    )

    kpis      = _compute_kpi_status(data)
    narrative = _get_narrative(data, kpis)

    _print_scorecard(data, kpis, narrative)

    nb_id = nb_no = None
    if not dry_run:
        nb_id, nb_no = _save_notebook(conn, data, kpis, narrative)
        logger.info(f"[SCORECARD] research_notebook {nb_no} (id={nb_id}) 저장 완료")
        if send_telegram:
            _send_telegram(data, kpis, narrative, nb_no)
    else:
        print("  [DRY RUN — DB/Telegram 미저장]")

    conn.close()
    return {
        'status':    'ok',
        'nb_id':     nb_id,
        'nb_no':     nb_no,
        'kpis':      kpis,
        'nb_total':  data['nb_total'],
        'unknown_rate_pct': data['unknown_rate']['rate_pct'],
        'l1_readiness':     data['l1_readiness'],
    }


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run',     action='store_true')
    ap.add_argument('--no-telegram', action='store_true')
    ap.add_argument('--months',      type=int, default=1)
    args = ap.parse_args()

    run_scorecard(
        months=args.months,
        dry_run=args.dry_run,
        send_telegram=not args.no_telegram,
    )
