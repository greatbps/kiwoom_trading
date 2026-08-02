"""
Session Review — Phase 3a EOD
Trading Session 마감 + AI 리뷰 생성

실행:
  python3 -m analysis.session_review           # 오늘 세션 리뷰
  python3 -m analysis.session_review --dry-run # 콘솔 출력만
  python3 -m analysis.session_review --date 2026-06-27
"""

import os
import json
import logging
import argparse
import psycopg2
import anthropic

from datetime import date, datetime
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('session_review')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

DB_CONF = dict(dbname='trading_system', user='postgres',
               password=os.getenv('POSTGRES_PASSWORD'), host='localhost')

TELEGRAM_TOKEN   = os.getenv('TELEGRAM_BOT_TOKEN',
                              '8190858980:AAHeB7qNnop5yIYaeIiEG9HsREOrgBpiRxQ')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_IDS', '-1002891093241')

CLAUDE_MODEL = 'claude-haiku-4-5-20251001'


def _gather_session_data(conn, target_date: date) -> dict:
    cur = conn.cursor()

    # 1. 세션 + 시장 컨텍스트
    cur.execute("""
        SELECT ts.id, ts.regime, ts.volatility_level, ts.market_context_id,
               mc.risk_score, mc.opportunity_score, mc.sector_rotation,
               mc.briefing_text
        FROM trading_sessions ts
        LEFT JOIN market_context mc ON mc.id = ts.market_context_id
        WHERE ts.session_date = %s
    """, (target_date,))
    row = cur.fetchone()
    if not row:
        return {}

    session_id, regime, vol_level, ctx_id, risk, opp, sectors, briefing = row

    # 2. 당일 거래 집계
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE trade_type = 'BUY')  AS buy_cnt,
            COUNT(*) FILTER (WHERE trade_type = 'SELL') AS sell_cnt,
            COALESCE(SUM(profit_rate) FILTER (WHERE trade_type = 'SELL'), 0) AS total_pnl_pct,
            COALESCE(SUM(realized_profit) FILTER (WHERE trade_type = 'SELL'), 0) AS total_pnl_krw
        FROM trades
        WHERE DATE(created_at) = %s
    """, (target_date,))
    t = cur.fetchone()
    buy_cnt, sell_cnt, pnl_pct, pnl_krw = (t[0] or 0, t[1] or 0,
                                             float(t[2] or 0), int(t[3] or 0))

    # 3. Decision Log 집계
    cur.execute("""
        SELECT decision_type, COUNT(*) AS cnt
        FROM decision_log
        WHERE session_id = %s
        GROUP BY decision_type
    """, (session_id,))
    decisions = {r[0]: r[1] for r in cur.fetchall()}

    # 4. 거래된 종목 목록 (최대 5개)
    cur.execute("""
        SELECT DISTINCT stock_name
        FROM trades
        WHERE DATE(created_at) = %s AND trade_type = 'BUY'
        LIMIT 5
    """, (target_date,))
    traded_stocks = [r[0] for r in cur.fetchall()]

    return {
        'session_id':    session_id,
        'session_date':  str(target_date),
        'regime':        regime or 'Unknown',
        'volatility':    vol_level or 'Unknown',
        'risk_score':    risk,
        'opp_score':     opp,
        'sectors':       sectors or [],
        'morning_brief': briefing or '',
        'buy_count':     buy_cnt,
        'sell_count':    sell_cnt,
        'daily_pnl_pct': round(pnl_pct, 4),
        'daily_pnl_krw': pnl_krw,
        'decisions':     decisions,
        'traded_stocks': traded_stocks,
    }


def _build_review_prompt(data: dict) -> str:
    stocks_str = ', '.join(data['traded_stocks']) or '없음'
    pnl_sign   = '+' if data['daily_pnl_pct'] >= 0 else ''

    return f"""You are the Session Reviewer for a Korean swing-trading system.
Today ({data['session_date']}) trading session summary:

Morning Context:
- Market Regime: {data['regime']}
- Risk Score: {data['risk_score']} | Opportunity: {data['opp_score']}
- Key Sectors: {', '.join(data['sectors'][:3])}
- Morning Brief: {data['morning_brief'][:200]}

Trading Results:
- Trades: {data['buy_count']} BUY, {data['sell_count']} SELL
- Daily PnL: {pnl_sign}{data['daily_pnl_pct']:.2f}%  ({pnl_sign}{data['daily_pnl_krw']:,} KRW)
- Traded stocks: {stocks_str}

Write a concise EOD session review. Return ONLY valid JSON:
{{
  "major_theme": "<1 Korean phrase describing today's session, e.g. '반도체 반등', 'FOMC 경계'>"  ,
  "review_text": "<Korean 3-5 sentences: what happened, what worked/failed, key observation>",
  "tags": ["<tag1>", "<tag2>", "<tag3>"],
  "signal_quality": "<good|neutral|poor>",
  "next_session_note": "<Korean 1 sentence: what to watch tomorrow>"
}}

Tag examples: risk_off_day, risk_on_day, strong_open, weak_close, fomc_eve, monday_effect,
              high_volatility, low_volatility, sector_rotation, profit_day, loss_day"""


def _call_claude_review(data: dict) -> dict:
    client   = anthropic.Anthropic()
    prompt   = _build_review_prompt(data)
    response = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=512,
        messages=[{"role": "user", "content": prompt}]
    )
    text = response.content[0].text.strip()
    if text.startswith('```'):
        text = text.split('```')[1]
        if text.startswith('json'):
            text = text[4:]
    return json.loads(text)


def _save_review(conn, data: dict, review: dict):
    cur = conn.cursor()
    cur.execute("""
        UPDATE trading_sessions SET
            buy_count        = %s,
            sell_count       = %s,
            daily_pnl_pct    = %s,
            daily_pnl_krw    = %s,
            major_theme      = %s,
            review_text      = %s,
            review_created_at= NOW(),
            review_tags      = %s,
            status           = 'reviewed'
        WHERE id = %s
    """, (
        data['buy_count'], data['sell_count'],
        data['daily_pnl_pct'], data['daily_pnl_krw'],
        review.get('major_theme'),
        review.get('review_text'),
        review.get('tags', []),
        data['session_id']
    ))
    conn.commit()


def _send_telegram(data: dict, review: dict, target_date: date):
    import requests
    pnl_sign = '+' if data['daily_pnl_pct'] >= 0 else ''
    pnl_emoji = '📈' if data['daily_pnl_pct'] >= 0 else '📉'
    tags_str = ' '.join(f'#{t}' for t in review.get('tags', [])[:4])

    text = (
        f"{pnl_emoji} *Session Review* — {target_date.strftime('%m/%d')}\n\n"
        f"{review.get('review_text', '')}\n\n"
        f"*{pnl_sign}{data['daily_pnl_pct']:.2f}%* "
        f"| BUY {data['buy_count']} SELL {data['sell_count']}\n"
        f"{tags_str}\n\n"
        f"*내일 주목*: {review.get('next_session_note', '')}"
    )
    try:
        for chat_id in TELEGRAM_CHAT_ID.split(','):
            requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={'chat_id': chat_id.strip(), 'text': text, 'parse_mode': 'Markdown'},
                timeout=10
            )
        logger.info("[SESSION_REVIEW] 텔레그램 발송 완료")
    except Exception as e:
        logger.warning(f"[SESSION_REVIEW] 텔레그램 실패: {e}")


def run_session_review(target_date: date = None, dry_run: bool = False,
                       send_telegram: bool = True):
    if target_date is None:
        target_date = date.today()

    conn = psycopg2.connect(**DB_CONF)
    data = _gather_session_data(conn, target_date)

    if not data:
        logger.warning(f"[SESSION_REVIEW] {target_date} 세션 없음 — MIE가 실행되었나요?")
        conn.close()
        return None

    logger.info(f"[SESSION_REVIEW] {target_date} 데이터 수집 완료 "
                f"(BUY {data['buy_count']} / SELL {data['sell_count']})")

    review = _call_claude_review(data)

    # 콘솔 출력
    pnl_sign = '+' if data['daily_pnl_pct'] >= 0 else ''
    print(f"\n{'='*60}")
    print(f"  Session Review — {target_date}")
    print(f"{'='*60}")
    print(f"  Theme  : {review.get('major_theme')}")
    print(f"  Regime : {data['regime']} | Signal: {review.get('signal_quality')}")
    print(f"  PnL    : {pnl_sign}{data['daily_pnl_pct']:.2f}% "
          f"({pnl_sign}{data['daily_pnl_krw']:,} KRW)")
    print(f"  Trades : BUY {data['buy_count']} / SELL {data['sell_count']}")
    print(f"\n  {review.get('review_text')}")
    print(f"\n  내일: {review.get('next_session_note')}")
    print(f"  Tags: {', '.join(review.get('tags', []))}")
    print()

    if not dry_run:
        _save_review(conn, data, review)
        logger.info(f"[SESSION_REVIEW] session id={data['session_id']} 'reviewed'로 마감")
        if send_telegram:
            _send_telegram(data, review, target_date)
    else:
        print("  [DRY RUN — DB/Telegram 미저장]")

    conn.close()
    return review


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--no-telegram', action='store_true')
    ap.add_argument('--date', type=str, help='YYYY-MM-DD')
    args = ap.parse_args()

    d = date.fromisoformat(args.date) if args.date else None
    run_session_review(target_date=d, dry_run=args.dry_run,
                       send_telegram=not args.no_telegram)
