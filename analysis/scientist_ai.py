"""
Scientist AI — Level 0 (Pattern Discovery)
After Market: 16:40 (주 1회, 금요일) + 22:00 (심화 분석)

L0 역할: 패턴 설명 생성 (가설 생성 아님)
  "전략이 잘 작동한/실패한 조건은 무엇인가?"

절대 금지:
  전략 수정 제안, YAML 파라미터 변경 제안, 실주문 연결

출력: research_notebook (Append-Only, Article 3)

실행:
  python3 -m analysis.scientist_ai                   # 오늘 분석
  python3 -m analysis.scientist_ai --dry-run         # DB 저장 없이 출력만
  python3 -m analysis.scientist_ai --weeks 12        # 최근 12주 데이터
  python3 -m analysis.scientist_ai --mode deep       # 22:00 심화 분석
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

logger = logging.getLogger('scientist_ai')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

DB_CONF = dict(dbname='trading_system', user='postgres',
               password=os.getenv('POSTGRES_PASSWORD'), host='localhost')

CLAUDE_MODEL = 'claude-haiku-4-5-20251001'

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN',
                              '8190858980:AAHeB7qNnop5yIYaeIiEG9HsREOrgBpiRxQ')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_IDS', '-1002891093241')


# ── 1. 데이터 수집 ─────────────────────────────────────────────────────────────

def _collect_data(conn, weeks: int) -> dict:
    cur = conn.cursor()
    since = date.today() - timedelta(weeks=weeks)
    data = {}

    # ── 전체 요약 ──────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE trade_type='BUY')  AS total_buys,
            COUNT(*) FILTER (WHERE trade_type='SELL') AS total_sells,
            COUNT(*) FILTER (WHERE trade_type='SELL' AND profit_rate > 0) AS wins,
            COUNT(*) FILTER (WHERE trade_type='SELL' AND profit_rate IS NOT NULL) AS sells_with_pnl,
            ROUND(AVG(profit_rate) FILTER (WHERE trade_type='SELL')::numeric, 3) AS avg_pnl,
            MIN(created_at)::date AS first_date,
            MAX(created_at)::date AS last_date
        FROM trades
        WHERE created_at >= %s
    """, (since,))
    r = cur.fetchone()
    data['summary'] = {
        'period_weeks': weeks,
        'since':        str(since),
        'total_buys':   r[0] or 0,
        'total_sells':  r[1] or 0,
        'wins':         r[2] or 0,
        'sells_w_pnl':  r[3] or 0,
        'avg_pnl':      float(r[4]) if r[4] else None,
        'win_rate_pct': round(r[2] / r[3] * 100, 1) if r[3] and r[2] else None,
        'first_date':   str(r[5]) if r[5] else None,
        'last_date':    str(r[6]) if r[6] else None,
    }

    # ── 월별 추세 ──────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT
            DATE_TRUNC('month', created_at)::date AS month,
            COUNT(*) FILTER (WHERE trade_type='BUY')  AS buys,
            COUNT(*) FILTER (WHERE trade_type='SELL') AS sells,
            ROUND(AVG(profit_rate) FILTER (WHERE trade_type='SELL' AND profit_rate IS NOT NULL)::numeric, 3) AS avg_pnl,
            COUNT(*) FILTER (WHERE trade_type='SELL' AND profit_rate > 0) AS wins,
            COUNT(*) FILTER (WHERE trade_type='SELL' AND profit_rate IS NOT NULL) AS sells_w_pnl
        FROM trades
        WHERE created_at >= %s
        GROUP BY 1 ORDER BY 1
    """, (since,))
    data['monthly'] = [
        {'month': str(r[0]), 'buys': r[1], 'sells': r[2],
         'avg_pnl': float(r[3]) if r[3] else None,
         'wins': r[4],
         'win_rate_pct': round(r[4] / r[5] * 100, 1) if r[5] and r[4] is not None else None}
        for r in cur.fetchall()
    ]

    # ── 청산 사유 분포 ─────────────────────────────────────────────────────────
    cur.execute("""
        SELECT
            COALESCE(exit_reason, '(미기록)') AS exit_reason,
            COUNT(*) AS cnt,
            ROUND(AVG(profit_rate)::numeric, 3) AS avg_pnl,
            ROUND((COUNT(*) FILTER (WHERE profit_rate > 0)::float
                   / NULLIF(COUNT(*), 0) * 100)::numeric, 1) AS win_pct
        FROM trades
        WHERE trade_type='SELL' AND created_at >= %s
        GROUP BY 1
        ORDER BY cnt DESC
        LIMIT 15
    """, (since,))
    data['exit_reasons'] = [
        {'reason': r[0], 'cnt': r[1],
         'avg_pnl': float(r[2]) if r[2] else None,
         'win_pct': float(r[3]) if r[3] else None}
        for r in cur.fetchall()
    ]

    # ── 보유시간 버킷 (데이터 있는 경우만) ────────────────────────────────────
    cur.execute("""
        SELECT
            CASE
                WHEN holding_minutes < 60   THEN '< 1h'
                WHEN holding_minutes < 240  THEN '1~4h'
                WHEN holding_minutes < 480  THEN '4~8h'
                WHEN holding_minutes < 1440 THEN '< 1day'
                ELSE '1day+'
            END AS bucket,
            COUNT(*) AS cnt,
            ROUND(AVG(profit_rate)::numeric, 3) AS avg_pnl,
            ROUND((COUNT(*) FILTER (WHERE profit_rate > 0)::float
                   / NULLIF(COUNT(*), 0) * 100)::numeric, 1) AS win_pct
        FROM trades
        WHERE trade_type='SELL' AND profit_rate IS NOT NULL
          AND holding_minutes IS NOT NULL AND created_at >= %s
        GROUP BY 1 ORDER BY 2 DESC
    """, (since,))
    rows = cur.fetchall()
    data['hold_time'] = [
        {'bucket': r[0], 'cnt': r[1],
         'avg_pnl': float(r[2]) if r[2] else None,
         'win_pct': float(r[3]) if r[3] else None}
        for r in rows
    ] if rows else []

    # ── 데이터 품질 현황 ───────────────────────────────────────────────────────
    cur.execute("""
        SELECT
            COUNT(*) AS total,
            COUNT(*) FILTER (WHERE market_regime IS NOT NULL) AS has_regime,
            COUNT(*) FILTER (WHERE mfe_pct       IS NOT NULL) AS has_mfe,
            COUNT(*) FILTER (WHERE swing_pattern IS NOT NULL) AS has_pattern,
            COUNT(*) FILTER (WHERE entry_reason  IS NOT NULL) AS has_entry_reason,
            COUNT(*) FILTER (WHERE holding_minutes IS NOT NULL) AS has_hold
        FROM trades
        WHERE trade_type='SELL' AND created_at >= %s
    """, (since,))
    r = cur.fetchone()
    total = r[0] or 1
    data['data_quality'] = {
        'total_sells':        r[0],
        'regime_fill_pct':    round(r[1] / total * 100, 1),
        'mfe_fill_pct':       round(r[2] / total * 100, 1),
        'pattern_fill_pct':   round(r[3] / total * 100, 1),
        'entry_reason_pct':   round(r[4] / total * 100, 1),
        'hold_time_fill_pct': round(r[5] / total * 100, 1),
    }

    # ── 세션 리뷰 현황 ─────────────────────────────────────────────────────────
    cur.execute("""
        SELECT
            COUNT(*) AS total_sessions,
            COUNT(*) FILTER (WHERE status='reviewed') AS reviewed,
            ROUND(AVG(daily_pnl_pct) FILTER (WHERE status='reviewed')::numeric, 3) AS avg_pnl,
            COUNT(*) FILTER (WHERE status='reviewed' AND daily_pnl_pct > 0) AS profit_days
        FROM trading_sessions
        WHERE session_date >= %s
    """, (since,))
    r = cur.fetchone()
    data['sessions'] = {
        'total':       r[0] or 0,
        'reviewed':    r[1] or 0,
        'avg_pnl':     float(r[2]) if r[2] else None,
        'profit_days': r[3] or 0,
    }

    # ── decision_log 현황 (Phase B1 이후 누적) ────────────────────────────────
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE decision_type='BUY'),
               COUNT(*) FILTER (WHERE decision_type='SELL')
        FROM decision_log
        WHERE decision_time >= %s
    """, (since,))
    r = cur.fetchone()
    data['decision_log'] = {'buy_decisions': r[0] or 0, 'sell_decisions': r[1] or 0}

    return data


# ── 2. 프롬프트 구성 ───────────────────────────────────────────────────────────

def _build_prompt(data: dict, mode: str) -> str:
    dq = data['data_quality']
    sm = data['summary']

    # 데이터 품질 경고 구성
    gaps = []
    if dq['regime_fill_pct'] < 20:
        gaps.append(f"market_regime 미기록({dq['regime_fill_pct']}%) — 레짐별 분석 불가")
    if dq['mfe_fill_pct'] < 20:
        gaps.append(f"MFE/MAE 미기록({dq['mfe_fill_pct']}%) — 진입 타이밍 분석 불가")
    if dq['pattern_fill_pct'] < 20:
        gaps.append(f"swing_pattern 미기록({dq['pattern_fill_pct']}%) — 패턴별 분석 불가")
    if sm['sells_w_pnl'] < 20:
        gaps.append(f"PnL 기록 {sm['sells_w_pnl']}건 — 통계적 유의성 부족")

    gaps_text = '\n'.join(f'  - {g}' for g in gaps) if gaps else '  - 없음 (데이터 충분)'

    mode_instruction = (
        "16:40 장 마감 직후 분석 — 당일 거래 포함 직접 관찰 우선."
        if mode == 'initial'
        else
        "22:00 심화 분석 — 패턴의 구조적 원인, 해외시장 연관성, 장기 추세 중심."
    )

    return f"""You are the Scientist AI (Level 0) for a Korean swing-trading system.

ROLE CONSTRAINTS (strictly enforced):
  - You EXPLAIN observed patterns only
  - You DO NOT recommend strategy changes
  - You DO NOT suggest YAML parameter adjustments
  - You DO NOT propose buy/sell rules
  - You MUST explicitly declare data gaps that prevent meaningful analysis

MODE: {mode_instruction}

ANALYSIS PERIOD: Last {data['summary']['period_weeks']} weeks
  ({data['summary']['since']} ~ {data['summary']['last_date'] or 'today'})

═══ TRADE SUMMARY ═══
  Total BUY:    {sm['total_buys']}
  Total SELL:   {sm['total_sells']} (with PnL: {sm['sells_w_pnl']})
  Win Rate:     {sm['win_rate_pct']}%
  Avg PnL:      {sm['avg_pnl']}%

═══ MONTHLY TREND ═══
{chr(10).join(
    f"  {m['month']}: sells={m['sells']} avg_pnl={m['avg_pnl']}% win_rate={m['win_rate_pct']}%"
    for m in data['monthly']
) or '  (데이터 없음)'}

═══ EXIT REASON DISTRIBUTION ═══
{chr(10).join(
    f"  [{r['cnt']:3d}건] avg_pnl={str(r['avg_pnl']):>7}% win={r['win_pct']}% | {r['reason']}"
    for r in data['exit_reasons']
) or '  (데이터 없음)'}

═══ HOLD TIME vs PnL ═══
{chr(10).join(
    f"  {h['bucket']:8s}: cnt={h['cnt']:3d} avg_pnl={str(h['avg_pnl']):>7}% win={h['win_pct']}%"
    for h in data['hold_time']
) if data['hold_time'] else '  (hold_time 미기록 — 분석 불가)'}

═══ SESSION REVIEW ═══
  Sessions: {data['sessions']['total']} total / {data['sessions']['reviewed']} reviewed
  Session avg PnL: {data['sessions']['avg_pnl']}%
  Profit days: {data['sessions']['profit_days']}

═══ DATA QUALITY ═══
  market_regime:  {dq['regime_fill_pct']}% 기록
  MFE/MAE:        {dq['mfe_fill_pct']}% 기록
  swing_pattern:  {dq['pattern_fill_pct']}% 기록
  hold_time:      {dq['hold_time_fill_pct']}% 기록
  decision_log:   BUY {data['decision_log']['buy_decisions']}건 / SELL {data['decision_log']['sell_decisions']}건

KNOWN DATA GAPS (declare these as unknown in your output):
{gaps_text}

───────────────────────────────────────────────
Return ONLY valid JSON, no markdown:
{{
  "research_question": "<핵심 분석 질문 1문장 (한국어)>",
  "findings": "<관찰된 패턴 설명 3~6문장 (한국어). 전략 변경 제안 금지.>",
  "conclusion": "<결론 1~2문장. 데이터가 부족하면 '불충분'이라고 명시.>",
  "data_gaps": ["<분석 불가 항목 1>", "<분석 불가 항목 2>"],
  "confidence": <0~100, 데이터 품질 기반>,
  "tags": ["<tag1>", "<tag2>", "<tag3>"],
  "unknown_declaration": "<'충분' | '불충분' | '부분'>"
}}

Tags 예시: declining_performance, forced_close_dominant, data_quality_issue,
           hold_time_pattern, exit_reason_analysis, win_rate_trend
"""


# ── 3. Claude API 호출 ─────────────────────────────────────────────────────────

def _call_claude(data: dict, mode: str) -> dict:
    client  = anthropic.Anthropic()
    prompt  = _build_prompt(data, mode)
    resp    = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )
    text = resp.content[0].text.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    return json.loads(text)


# ── 4. research_notebook 저장 ──────────────────────────────────────────────────

def _next_notebook_no(cur) -> str:
    cur.execute("""
        SELECT MAX(CAST(SUBSTRING(notebook_no FROM 4) AS INTEGER))
        FROM research_notebook
        WHERE notebook_no ~ '^NB-[0-9]+$'
    """)
    row = cur.fetchone()
    n = (row[0] or 0) + 1
    return f"NB-{n:03d}"


def _save_notebook(conn, data: dict, result: dict, mode: str) -> int:
    cur = conn.cursor()

    nb_no = _next_notebook_no(cur)

    # research_environment id
    cur.execute("SELECT id FROM research_environment WHERE status='active' LIMIT 1")
    row = cur.fetchone()
    re_id = row[0] if row else None

    # 오늘 session_id (없으면 NULL)
    cur.execute("SELECT id FROM trading_sessions WHERE session_date = CURRENT_DATE")
    row = cur.fetchone()
    session_id = row[0] if row else None

    mode_label = '심화' if mode == 'deep' else '기본'
    title = (
        f"[Scientist L0] 스윙 전략 패턴 분석 "
        f"({data['summary']['period_weeks']}주, {mode_label}) — {date.today()}"
    )

    data_scope = {
        'period_weeks':        data['summary']['period_weeks'],
        'since':               data['summary']['since'],
        'total_sells':         data['summary']['total_sells'],
        'sells_w_pnl':         data['summary']['sells_w_pnl'],
        'decision_log':        data['decision_log'],
        'data_quality':        data['data_quality'],
        'mode':                mode,
        'unknown_declaration': result.get('unknown_declaration', ''),
        'data_gaps':           result.get('data_gaps', []),
    }

    tags = ['scientist_review'] + result.get('tags', [])

    cur.execute("""
        INSERT INTO research_notebook
          (notebook_no, title, research_question,
           data_scope, findings, conclusion,
           confidence, tags, model_used,
           research_environment_id, session_id,
           created_at, updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW())
        RETURNING id
    """, (
        nb_no, title,
        result.get('research_question', ''),
        json.dumps(data_scope, ensure_ascii=False, default=str),
        result.get('findings', ''),
        result.get('conclusion', ''),
        result.get('confidence'),
        tags,
        CLAUDE_MODEL,
        re_id, session_id,
    ))
    nb_id = cur.fetchone()[0]
    conn.commit()
    return nb_id, nb_no


# ── 5. Telegram 발송 ───────────────────────────────────────────────────────────

def _send_telegram(result: dict, nb_no: str, data: dict):
    import requests
    sm = data['summary']
    conf = result.get('confidence', '?')
    unk  = result.get('unknown_declaration', '?')
    gaps = result.get('data_gaps', [])
    tags = ' '.join(f'#{t}' for t in result.get('tags', [])[:4])

    conf_emoji = '🟢' if conf >= 60 else '🟡' if conf >= 40 else '🔴'
    unk_emoji  = {'충분': '✅', '부분': '⚠️', '불충분': '❌'}.get(unk, '⚪')

    text = (
        f"🔬 *Scientist AI L0* — {nb_no} [{date.today().strftime('%m/%d')}]\n\n"
        f"*{result.get('research_question', '')}*\n\n"
        f"{result.get('conclusion', '')}\n\n"
        f"데이터: {sm['sells_w_pnl']}건 | "
        f"승률 {sm['win_rate_pct']}% | "
        f"평균 {sm['avg_pnl']}%\n"
        f"{conf_emoji} 신뢰도 {conf} | "
        f"{unk_emoji} 데이터충분도: {unk}\n"
    )
    if gaps:
        text += f"⚠️ 미분석: {', '.join(gaps[:2])}\n"
    text += f"\n{tags}"

    try:
        for chat_id in TELEGRAM_CHAT_ID.split(','):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={'chat_id': chat_id.strip(), 'text': text, 'parse_mode': 'Markdown'},
                timeout=10
            )
        logger.info("[SCIENTIST] 텔레그램 발송 완료")
    except Exception as e:
        logger.warning(f"[SCIENTIST] 텔레그램 실패: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def run_scientist(weeks: int = 12, mode: str = 'initial',
                  dry_run: bool = False, send_telegram: bool = True) -> dict:
    today = date.today()
    logger.info(f"[SCIENTIST L0] {today} 분석 시작 (weeks={weeks} mode={mode})")

    conn = psycopg2.connect(**DB_CONF)
    data = _collect_data(conn, weeks)

    sm = data['summary']
    logger.info(
        f"[SCIENTIST L0] 수집 완료 — SELL {sm['total_sells']}건 "
        f"(PnL 기록: {sm['sells_w_pnl']}건) "
        f"승률 {sm['win_rate_pct']}%"
    )

    # 최소 데이터 체크 — Article 4: 모른다는 것은 유효한 상태
    if sm['sells_w_pnl'] < 5:
        logger.warning(
            f"[SCIENTIST L0] PnL 기록 {sm['sells_w_pnl']}건 — "
            f"분석 최소 기준 미달 (필요: 5건). 실행 중단."
        )
        conn.close()
        return {'status': 'insufficient_data', 'sells_w_pnl': sm['sells_w_pnl']}

    logger.info("[SCIENTIST L0] Claude API 호출 중...")
    result = _call_claude(data, mode)

    # 콘솔 출력
    print(f"\n{'='*65}")
    print(f"  Scientist AI L0 — {today}  [{mode}]")
    print(f"{'='*65}")
    print(f"  Q: {result.get('research_question', '')}")
    print(f"\n  Findings:")
    print(f"  {result.get('findings', '')}")
    print(f"\n  Conclusion: {result.get('conclusion', '')}")
    print(f"\n  Confidence: {result.get('confidence')}  |  Data: {result.get('unknown_declaration')}")
    if result.get('data_gaps'):
        print(f"  Gaps: {', '.join(result['data_gaps'])}")
    print(f"  Tags: {', '.join(result.get('tags', []))}")
    print()

    nb_id = nb_no = None
    if not dry_run:
        nb_id, nb_no = _save_notebook(conn, data, result, mode)
        logger.info(f"[SCIENTIST L0] research_notebook {nb_no} (id={nb_id}) 저장 완료")
        if send_telegram:
            _send_telegram(result, nb_no, data)
    else:
        print("  [DRY RUN — DB/Telegram 미저장]")

    conn.close()
    return {
        'status':   'ok',
        'nb_id':    nb_id,
        'nb_no':    nb_no,
        'findings': result.get('findings'),
        'confidence': result.get('confidence'),
        'unknown_declaration': result.get('unknown_declaration'),
        'data_gaps': result.get('data_gaps', []),
    }


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run',      action='store_true')
    ap.add_argument('--no-telegram',  action='store_true')
    ap.add_argument('--weeks',        type=int, default=12)
    ap.add_argument('--mode',         choices=['initial', 'deep'], default='initial')
    args = ap.parse_args()

    run_scientist(
        weeks=args.weeks,
        mode=args.mode,
        dry_run=args.dry_run,
        send_telegram=not args.no_telegram,
    )
