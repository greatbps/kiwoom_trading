"""
Analyst AI — During/After Market (15:30)
ADR-006: Time-Cycle Layer ② — 장중/장 마감 직전

역할: "오늘의 거래 결정이 시장 컨텍스트와 얼마나 일치했는가?"
KPI:  Explain Accuracy (사후 검증 n≥30 후)
      Decision Consistency (동일 레짐에서 일관성)

입력 우선순위:
  1. decision_log (Phase B1 이후 누적된 경우)
  2. trades 테이블 (폴백)
  3. market_context (오늘 → 없으면 최신)

출력: research_notebook (Append-Only, Article 3)
      decision_log 수정 없음 (Article 3)

실행:
  python3 -m analysis.analyst_ai              # 오늘 분석
  python3 -m analysis.analyst_ai --dry-run
  python3 -m analysis.analyst_ai --date 2026-06-27
"""

import os
import json
import logging
import argparse
import psycopg2
import anthropic
import requests

from datetime import date, datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('analyst_ai')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

DB_CONF = dict(dbname='trading_system', user='postgres',
               password=os.getenv('POSTGRES_PASSWORD'), host='localhost')

CLAUDE_MODEL = 'claude-haiku-4-5-20251001'

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN',
                              '8190858980:AAHeB7qNnop5yIYaeIiEG9HsREOrgBpiRxQ')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_IDS', '-1002891093241')


# ── 1. 데이터 수집 ─────────────────────────────────────────────────────────────

def _collect_today(conn, target_date: date) -> dict:
    cur = conn.cursor()
    data = {'target_date': str(target_date)}

    # ── market_context: 오늘 → 없으면 최신 ────────────────────────────────────
    cur.execute("""
        SELECT id, context_date, market_regime, volatility_level,
               recommended_bias, risk_score, opportunity_score,
               sector_rotation, caution_factors, briefing_text
        FROM market_context
        WHERE context_date <= %s
        ORDER BY context_date DESC LIMIT 1
    """, (target_date,))
    row = cur.fetchone()
    if row:
        data['market_context'] = {
            'id': row[0], 'date': str(row[1]),
            'regime': row[2], 'volatility': row[3],
            'bias': row[4], 'risk': row[5], 'opportunity': row[6],
            'sectors': row[7] or [],
            'cautions': row[8] or [],
            'brief': (row[9] or '')[:200],
            'is_stale': row[1] != target_date,
        }
    else:
        data['market_context'] = None

    # ── decision_log: 오늘치 (Phase B1 이후) ──────────────────────────────────
    cur.execute("""
        SELECT id, stock_code, stock_name, decision_type,
               signals, decision_reason, confidence,
               outcome_pnl_pct, decision_time::time
        FROM decision_log
        WHERE DATE(decision_time) = %s
        ORDER BY decision_time
    """, (target_date,))
    rows = cur.fetchall()
    data['decisions'] = [
        {
            'id': r[0], 'code': r[1], 'name': r[2],
            'type': r[3], 'signals': r[4] or {},
            'reason': r[5], 'confidence': r[6],
            'pnl': float(r[7]) if r[7] is not None else None,
            'time': str(r[8]),
        }
        for r in rows
    ]

    # ── trades 폴백: decision_log가 비면 trades로 ─────────────────────────────
    cur.execute("""
        SELECT trade_id, stock_code, stock_name, trade_type,
               created_at::time, exit_reason, profit_rate,
               holding_minutes, entry_context, exit_context
        FROM trades
        WHERE DATE(created_at) = %s
        ORDER BY created_at
    """, (target_date,))
    rows = cur.fetchall()
    data['trades_today'] = [
        {
            'id': r[0], 'code': r[1], 'name': r[2],
            'type': r[3], 'time': str(r[4]),
            'exit_reason': r[5],
            'pnl': float(r[6]) if r[6] is not None else None,
            'hold_min': r[7],
            'entry_ctx': r[8] or {},
            'exit_ctx': r[9] or {},
        }
        for r in rows
    ]

    # ── session review (있으면) ────────────────────────────────────────────────
    cur.execute("""
        SELECT id, status, regime, daily_pnl_pct, buy_count, sell_count,
               major_theme, review_text
        FROM trading_sessions WHERE session_date = %s
    """, (target_date,))
    row = cur.fetchone()
    data['session'] = {
        'id': row[0], 'status': row[1], 'regime': row[2],
        'daily_pnl': float(row[3]) if row[3] else None,
        'buys': row[4], 'sells': row[5],
        'theme': row[6], 'review': row[7],
    } if row else None

    # ── 소스 선택 (decision_log 우선, 없으면 trades) ──────────────────────────
    data['source'] = 'decision_log' if data['decisions'] else 'trades_fallback'
    data['trade_events'] = (
        data['decisions'] if data['decisions'] else data['trades_today']
    )

    return data


# ── 2. 프롬프트 구성 ───────────────────────────────────────────────────────────

def _fmt_decision(ev: dict, source: str) -> str:
    if source == 'decision_log':
        sigs = ev.get('signals', {})
        return (
            f"  [{ev['time']}] {ev['type']} {ev['name']}({ev['code']}) "
            f"reason={ev['reason']} conf={ev['confidence']} "
            f"pnl={ev['pnl']}% | signals={json.dumps(sigs, ensure_ascii=False)[:120]}"
        )
    else:
        return (
            f"  [{ev['time']}] {ev['type']} {ev['name']}({ev['code']}) "
            f"exit={ev.get('exit_reason','?')} hold={ev.get('hold_min','?')}min "
            f"pnl={ev['pnl']}%"
        )


def _build_prompt(data: dict) -> str:
    ctx = data.get('market_context')
    events = data.get('trade_events', [])
    source = data['source']

    stale_warning = ''
    if ctx and ctx.get('is_stale'):
        stale_warning = f"\n⚠️ CONTEXT IS STALE: from {ctx['date']}, not today {data['target_date']}"

    ctx_text = (
        f"  Regime: {ctx['regime']} | Volatility: {ctx['volatility']}\n"
        f"  Bias: {ctx['bias']} | Risk: {ctx['risk']} | Opportunity: {ctx['opportunity']}\n"
        f"  Sectors: {', '.join(ctx['sectors'][:3])}\n"
        f"  Cautions: {', '.join(ctx['cautions'][:2])}\n"
        f"  Brief: {ctx['brief']}"
        if ctx else "  (시장 컨텍스트 없음 — MIE 미실행)"
    ) + stale_warning

    events_text = '\n'.join(_fmt_decision(ev, source) for ev in events) \
        if events else '  (오늘 거래 없음)'

    return f"""You are the Analyst AI (During/After Market) for a Korean swing-trading system.

ROLE: Decision Quality Analyst
QUESTION: "오늘의 거래 결정이 시장 컨텍스트와 얼마나 일치했는가?"

KPI YOU ARE MEASURED BY:
  - Explain Accuracy: 당신의 설명이 n≥30건 사후 검증에서 맞는가?
  - Decision Consistency: 같은 레짐에서 일관된 해석인가?

STRICT RULES:
  ✓ 각 결정의 컨텍스트 정합성을 0~100으로 점수화
  ✓ 왜 그 점수인지 1~2문장으로 설명
  ✓ 데이터 한계를 명시 (stale context, 신호 부재 등)
  ✗ 전략 변경 제안 금지
  ✗ 파라미터 수정 제안 금지
  ✗ "이 결정은 잘못됐다" 판단 금지 — 설명만

DATA SOURCE: {source}
  (decision_log = 신호 풍부 / trades_fallback = 신호 제한)

DATE: {data['target_date']}

═══ MARKET CONTEXT ═══
{ctx_text}

═══ TODAY'S TRADE DECISIONS ({len(events)}건) ═══
{events_text}

Return ONLY valid JSON:
{{
  "session_summary": "<오늘 세션 한 줄 요약 (한국어)>",
  "decisions": [
    {{
      "code": "<종목코드>",
      "name": "<종목명>",
      "type": "<BUY|SELL>",
      "context_alignment_score": <0~100>,
      "explanation": "<컨텍스트 정합성 설명 1~2문장 (한국어)>"
    }}
  ],
  "session_quality": "<good|neutral|poor>",
  "overall_alignment_score": <0~100, 오늘 전체 결정의 평균 정합성>,
  "key_observation": "<오늘 결정에서 가장 주목할 패턴 1문장 (한국어)>",
  "data_limitations": ["<한계 1>", "<한계 2>"],
  "confidence": <0~100>
}}"""


# ── 3. Claude API 호출 ─────────────────────────────────────────────────────────

def _call_claude(data: dict) -> dict:
    client = anthropic.Anthropic()
    prompt = _build_prompt(data)
    resp   = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=1024,
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
        FROM research_notebook WHERE notebook_no ~ '^NB-[0-9]+$'
    """)
    row = cur.fetchone()
    return f"NB-{(row[0] or 0) + 1:03d}"


def _save_notebook(conn, data: dict, result: dict) -> tuple:
    cur = conn.cursor()
    nb_no = _next_notebook_no(cur)

    cur.execute("SELECT id FROM research_environment WHERE status='active' LIMIT 1")
    row = cur.fetchone()
    re_id = row[0] if row else None

    session_id = data['session']['id'] if data.get('session') else None

    ctx_date = data['market_context']['date'] if data.get('market_context') else '?'
    is_stale = data['market_context']['is_stale'] if data.get('market_context') else True
    stale_tag = ['stale_context'] if is_stale else []

    title = f"[Analyst] 결정 컨텍스트 분석 — {data['target_date']}"

    data_scope = {
        'target_date':     data['target_date'],
        'source':          data['source'],
        'trade_count':     len(data['trade_events']),
        'context_date':    ctx_date,
        'context_stale':   is_stale,
        'per_decision':    result.get('decisions', []),
        'overall_score':   result.get('overall_alignment_score'),
        'session_quality': result.get('session_quality'),
    }

    tags = ['analyst_review', 'context_alignment', data['source']] + stale_tag
    if result.get('session_quality'):
        tags.append(f"quality_{result['session_quality']}")

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
        '오늘의 거래 결정이 시장 컨텍스트와 얼마나 일치했는가?',
        json.dumps(data_scope, ensure_ascii=False, default=str),
        result.get('key_observation', ''),
        result.get('session_summary', ''),
        result.get('confidence'),
        tags, CLAUDE_MODEL,
        re_id, session_id,
    ))
    nb_id = cur.fetchone()[0]
    conn.commit()
    return nb_id, nb_no


# ── 5. Telegram 발송 ───────────────────────────────────────────────────────────

def _send_telegram(result: dict, nb_no: str, target_date: date):
    q_emoji = {'good': '🟢', 'neutral': '🟡', 'poor': '🔴'}.get(
        result.get('session_quality', ''), '⚪'
    )
    score = result.get('overall_alignment_score', '?')
    decisions_txt = '\n'.join(
        f"  {d['code']} {d['name']} [{d['type']}] "
        f"정합성={d['context_alignment_score']} — {d['explanation'][:50]}"
        for d in result.get('decisions', [])[:5]
    ) or '  거래 없음'

    text = (
        f"🔍 *Analyst AI* — {nb_no} [{target_date.strftime('%m/%d')}]\n\n"
        f"*{result.get('session_summary', '')}*\n\n"
        f"{decisions_txt}\n\n"
        f"{q_emoji} 세션 품질: {result.get('session_quality', '?')} | "
        f"전체 정합성: {score}\n"
        f"📌 {result.get('key_observation', '')}"
    )
    try:
        for chat_id in TELEGRAM_CHAT_ID.split(','):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={'chat_id': chat_id.strip(), 'text': text, 'parse_mode': 'Markdown'},
                timeout=10
            )
        logger.info("[ANALYST] 텔레그램 발송 완료")
    except Exception as e:
        logger.warning(f"[ANALYST] 텔레그램 실패: {e}")


# ── Main ───────────────────────────────────────────────────────────────────────

def run_analyst(target_date: date = None, dry_run: bool = False,
                send_telegram: bool = True) -> dict:
    if target_date is None:
        target_date = date.today()

    logger.info(f"[ANALYST] {target_date} 결정 분석 시작")

    conn = psycopg2.connect(**DB_CONF)
    data = _collect_today(conn, target_date)

    events   = data['trade_events']
    source   = data['source']
    ctx      = data.get('market_context')
    ctx_info = f"context={ctx['date']}{'(stale)' if ctx and ctx['is_stale'] else ''}" if ctx else "context=없음"

    logger.info(
        f"[ANALYST] 수집 완료 — events={len(events)}건 source={source} {ctx_info}"
    )

    # 거래가 없으면 no-op (오늘 휴장 or 미거래)
    if not events:
        logger.info("[ANALYST] 오늘 거래 없음 — 분석 스킵")
        conn.close()
        return {'status': 'no_trades', 'target_date': str(target_date)}

    logger.info("[ANALYST] Claude API 호출 중...")
    result = _call_claude(data)

    # 콘솔 출력
    print(f"\n{'='*65}")
    print(f"  Analyst AI — {target_date}  [{source}]")
    print(f"{'='*65}")
    print(f"  Summary: {result.get('session_summary', '')}")
    print(f"  Quality: {result.get('session_quality')}  | Alignment: {result.get('overall_alignment_score')}")
    print()
    for d in result.get('decisions', []):
        print(f"  [{d['type']}] {d['name']}  score={d['context_alignment_score']}")
        print(f"       {d['explanation']}")
    if result.get('data_limitations'):
        print(f"\n  Limits: {' / '.join(result['data_limitations'][:2])}")
    print(f"  Key: {result.get('key_observation', '')}")
    print()

    nb_id = nb_no = None
    if not dry_run:
        nb_id, nb_no = _save_notebook(conn, data, result)
        logger.info(f"[ANALYST] research_notebook {nb_no} (id={nb_id}) 저장 완료")
        if send_telegram:
            _send_telegram(result, nb_no, target_date)
    else:
        print("  [DRY RUN — DB/Telegram 미저장]")

    conn.close()
    return {
        'status':    'ok',
        'nb_id':     nb_id,
        'nb_no':     nb_no,
        'source':    source,
        'events':    len(events),
        'alignment': result.get('overall_alignment_score'),
        'quality':   result.get('session_quality'),
    }


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run',     action='store_true')
    ap.add_argument('--no-telegram', action='store_true')
    ap.add_argument('--date', type=str, help='YYYY-MM-DD')
    args = ap.parse_args()

    d = date.fromisoformat(args.date) if args.date else None
    run_analyst(
        target_date=d,
        dry_run=args.dry_run,
        send_telegram=not args.no_telegram,
    )
