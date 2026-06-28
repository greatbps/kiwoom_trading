"""
Market Intelligence Engine (MIE) — Phase 3a
Trading OS의 Context Layer.

출력 3가지:
  1. market_context DB 레코드 (First-Class Object)
  2. 텔레그램 브리핑 (부산물)
  3. knowledge_base 항목 (시장 레짐 이력)

실행:
  python3 -m analysis.market_intelligence           # 실행 + DB 저장
  python3 -m analysis.market_intelligence --dry-run # DB 저장 없이 출력만
"""

import os
import json
import logging
import argparse
import psycopg2
import yfinance as yf
import requests
import anthropic

from datetime import date, datetime, timedelta
from time import time

logger = logging.getLogger('mie')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

# ── Config ────────────────────────────────────────────────────────────────────
DB_CONF = dict(dbname='trading_system', user='postgres',
               password='killer99!!', host='localhost')

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN',
                              '8190858980:AAHeB7qNnop5yIYaeIiEG9HsREOrgBpiRxQ')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_IDS', '-1002891093241')

CLAUDE_MODEL = 'claude-haiku-4-5-20251001'   # 빠르고 저렴, 일일 브리핑 최적

MARKET_TICKERS = {
    'kospi':  '^KS11',
    'kosdaq': '^KQ11',
    'usdkrw': 'KRW=X',
    'vix':    '^VIX',
    'sp500f': 'ES=F',
    'nqf':    'NQ=F',
}


# ── 1. 시장 데이터 수집 ────────────────────────────────────────────────────────
def _fetch_market_data() -> dict:
    raw = {}
    for name, ticker in MARKET_TICKERS.items():
        try:
            hist = yf.Ticker(ticker).history(period='3d')
            if len(hist) >= 2:
                prev  = float(hist['Close'].iloc[-2])
                curr  = float(hist['Close'].iloc[-1])
                raw[name] = {
                    'prev_close':  round(prev, 2),
                    'current':     round(curr, 2),
                    'change_pct':  round((curr / prev - 1) * 100, 3),
                }
            elif len(hist) == 1:
                raw[name] = {'current': float(hist['Close'].iloc[-1]), 'change_pct': 0.0}
        except Exception as e:
            logger.warning(f"[MIE] {name}({ticker}) 수집 실패: {e}")
            raw[name] = None
    return raw


# ── 2. Claude API 호출 → Context Object 생성 ──────────────────────────────────
def _build_prompt(raw: dict, today: date) -> str:
    def fmt(d):
        if not d:
            return 'N/A'
        c = d.get('change_pct', 0)
        return f"{d.get('current', 'N/A')} ({c:+.2f}%)"

    return f"""You are the Market Intelligence Engine for a Korean swing-trading system (hold 1-5 days).
Today: {today.strftime('%Y-%m-%d (%A)')}

Current Market Snapshot:
- KOSPI:        {fmt(raw.get('kospi'))}
- KOSDAQ:       {fmt(raw.get('kosdaq'))}
- USD/KRW:      {fmt(raw.get('usdkrw'))}
- VIX:          {fmt(raw.get('vix'))}
- S&P500 Fut:   {fmt(raw.get('sp500f'))}
- NASDAQ Fut:   {fmt(raw.get('nqf'))}

Based on this data, generate a market intelligence report.
Return ONLY valid JSON (no markdown, no explanation):
{{
  "context": {{
    "market_regime": "<Risk-On|Risk-Off|Neutral|Crisis>",
    "volatility_level": "<Low|Medium|High|Extreme>",
    "risk_score": <0-100, 높을수록 위험>,
    "opportunity_score": <0-100, 높을수록 기회>,
    "recommended_bias": "<LONG|SHORT|NEUTRAL|AVOID>",
    "sector_rotation": ["<top Korean sectors likely to outperform today>"],
    "major_events": ["<key market events or catalysts today>"],
    "caution_factors": ["<risk factors to watch>"],
    "watch_sectors": ["<sectors to monitor closely>"]
  }},
  "briefing": "<Korean 3-4 sentence trading brief, plain text, under 400 chars>"
}}"""


def _call_claude(raw: dict, today: date) -> dict:
    client = anthropic.Anthropic()
    prompt = _build_prompt(raw, today)
    response = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text.strip()
    # JSON 파싱 안전 처리
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    return json.loads(text)


# ── 3. Telegram 발송 ───────────────────────────────────────────────────────────
def _send_telegram(briefing: str, ctx: dict, today: date):
    regime_emoji = {
        'Risk-On': '🟢', 'Risk-Off': '🔴',
        'Neutral': '🟡', 'Crisis': '🚨'
    }.get(ctx.get('market_regime', ''), '⚪')

    bias_emoji = {
        'LONG': '📈', 'SHORT': '📉',
        'NEUTRAL': '➡️', 'AVOID': '⛔'
    }.get(ctx.get('recommended_bias', ''), '')

    text = (
        f"{regime_emoji} *MIE 모닝 브리핑* — {today.strftime('%m/%d')}\n\n"
        f"{briefing}\n\n"
        f"*Regime* {ctx.get('market_regime', 'N/A')}  "
        f"*Bias* {bias_emoji}{ctx.get('recommended_bias', 'N/A')}  "
        f"*Risk* {ctx.get('risk_score', 'N/A')}  "
        f"*Opp* {ctx.get('opportunity_score', 'N/A')}\n"
        f"*섹터* {', '.join(ctx.get('sector_rotation', []))}"
    )
    try:
        for chat_id in TELEGRAM_CHAT_ID.split(','):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={'chat_id': chat_id.strip(), 'text': text, 'parse_mode': 'Markdown'},
                timeout=10
            )
        logger.info("[MIE] 텔레그램 발송 완료")
    except Exception as e:
        logger.warning(f"[MIE] 텔레그램 발송 실패: {e}")


# ── 4. DB 저장 ─────────────────────────────────────────────────────────────────
def _save_to_db(today: date, ctx: dict, briefing: str,
                raw: dict, gen_ms: int) -> int:
    def cpct(key):
        d = raw.get(key)
        return d['change_pct'] if d else None

    def cval(key, field='current'):
        d = raw.get(key)
        return d[field] if d else None

    conn = psycopg2.connect(**DB_CONF)
    cur  = conn.cursor()
    cur.execute("""
        INSERT INTO market_context (
            context_date, market_regime, volatility_level, recommended_bias,
            risk_score, opportunity_score,
            kospi_prev_close, kospi_change_pct, kosdaq_change_pct,
            usdkrw, vix, sp500f_change_pct,
            sector_rotation, major_events, caution_factors, watch_sectors,
            context_json, briefing_text, raw_data, model_used, generation_time_ms
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (context_date) DO UPDATE SET
            market_regime     = EXCLUDED.market_regime,
            volatility_level  = EXCLUDED.volatility_level,
            recommended_bias  = EXCLUDED.recommended_bias,
            risk_score        = EXCLUDED.risk_score,
            opportunity_score = EXCLUDED.opportunity_score,
            kospi_change_pct  = EXCLUDED.kospi_change_pct,
            usdkrw            = EXCLUDED.usdkrw,
            vix               = EXCLUDED.vix,
            sector_rotation   = EXCLUDED.sector_rotation,
            major_events      = EXCLUDED.major_events,
            caution_factors   = EXCLUDED.caution_factors,
            watch_sectors     = EXCLUDED.watch_sectors,
            context_json      = EXCLUDED.context_json,
            briefing_text     = EXCLUDED.briefing_text,
            raw_data          = EXCLUDED.raw_data,
            generation_time_ms= EXCLUDED.generation_time_ms
        RETURNING id
    """, (
        today,
        ctx.get('market_regime'), ctx.get('volatility_level'), ctx.get('recommended_bias'),
        ctx.get('risk_score'), ctx.get('opportunity_score'),
        cval('kospi', 'prev_close'), cpct('kospi'), cpct('kosdaq'),
        cval('usdkrw'), cval('vix'), cpct('sp500f'),
        ctx.get('sector_rotation', []), ctx.get('major_events', []),
        ctx.get('caution_factors', []), ctx.get('watch_sectors', []),
        json.dumps(ctx, ensure_ascii=False),
        briefing,
        json.dumps(raw, ensure_ascii=False, default=str),
        CLAUDE_MODEL, gen_ms
    ))
    ctx_id = cur.fetchone()[0]

    # 5. Knowledge Base 기록 (레짐 이력)
    cur.execute("""
        INSERT INTO knowledge_base (
            category, finding_type, title, description, evidence, tags, confidence
        ) VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING
    """, (
        'market_regime',
        'conditional',
        f"{today} 시장 레짐: {ctx.get('market_regime')}",
        f"Risk={ctx.get('risk_score')} Opp={ctx.get('opportunity_score')} "
        f"Sectors={ctx.get('sector_rotation')}",
        json.dumps({
            'regime':  ctx.get('market_regime'),
            'risk':    ctx.get('risk_score'),
            'opp':     ctx.get('opportunity_score'),
            'bias':    ctx.get('recommended_bias'),
        }),
        ['market_regime', ctx.get('market_regime', '').lower().replace('-', '_'),
         ctx.get('volatility_level', '').lower()],
        60
    ))

    # ── Trading Session 오픈 (당일 1건, 중복 무시) ──────────────────────────
    cur.execute("""
        INSERT INTO trading_sessions (
            session_date, market_context_id,
            regime, volatility_level, status
        ) VALUES (%s, %s, %s, %s, 'open')
        ON CONFLICT (session_date) DO UPDATE SET
            market_context_id = EXCLUDED.market_context_id,
            regime            = EXCLUDED.regime,
            volatility_level  = EXCLUDED.volatility_level
        RETURNING id
    """, (today, ctx_id, ctx.get('market_regime'), ctx.get('volatility_level')))
    session_id = cur.fetchone()[0]

    conn.commit()
    conn.close()
    return ctx_id, session_id


# ── Main ───────────────────────────────────────────────────────────────────────
def run_mie(dry_run: bool = False, send_telegram: bool = True) -> dict:
    today = date.today()
    t0 = time()

    logger.info(f"[MIE] {today} 시장 데이터 수집 중...")
    raw = _fetch_market_data()

    logger.info("[MIE] Claude API 호출 중...")
    result  = _call_claude(raw, today)
    ctx     = result['context']
    briefing = result['briefing']
    gen_ms  = int((time() - t0) * 1000)

    logger.info(f"[MIE] Regime={ctx.get('market_regime')} "
                f"Risk={ctx.get('risk_score')} Opp={ctx.get('opportunity_score')} "
                f"Bias={ctx.get('recommended_bias')} ({gen_ms}ms)")

    ctx_id = session_id = None
    if not dry_run:
        ctx_id, session_id = _save_to_db(today, ctx, briefing, raw, gen_ms)
        logger.info(f"[MIE] market_context id={ctx_id}  session id={session_id} 저장 완료")

    if send_telegram and not dry_run:
        _send_telegram(briefing, ctx, today)

    # 콘솔 출력
    print(f"\n{'='*60}")
    print(f"  Market Intelligence — {today}")
    print(f"{'='*60}")
    print(f"  Regime : {ctx.get('market_regime')} | Bias: {ctx.get('recommended_bias')}")
    print(f"  Risk   : {ctx.get('risk_score')} | Opportunity: {ctx.get('opportunity_score')}")
    print(f"  Sectors: {', '.join(ctx.get('sector_rotation', []))}")
    if ctx.get('caution_factors'):
        print(f"  Caution: {', '.join(ctx.get('caution_factors', []))}")
    print(f"\n  {briefing}")
    print()
    if dry_run:
        print("  [DRY RUN — DB/Telegram 미저장]")

    return {'context': ctx, 'briefing': briefing, 'context_id': ctx_id, 'session_id': session_id}


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-telegram', action='store_true')
    args = ap.parse_args()
    run_mie(dry_run=args.dry_run, send_telegram=not args.no_telegram)
