"""
Trading Dashboard API Server
- Reads live data from kiwoom_trading system files
- Exposes REST endpoints for the Next.js dashboard
- Run: uvicorn api_server:app --host 0.0.0.0 --port 8000
"""

import sys
import sqlite3
import json
import re
import asyncio
import logging
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Optional

import yfinance as yf
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

# Add kiwoom_trading to path for internal modules
sys.path.insert(0, str(Path('/home/greatbps/projects/kiwoom_trading')))
try:
    from dotenv import load_dotenv
    load_dotenv(Path('/home/greatbps/projects/kiwoom_trading/.env'))
except Exception:
    pass

# ─── Config ──────────────────────────────────────────────────────────────────

BASE = Path('/home/greatbps/projects/kiwoom_trading')
POSITIONS_PATH  = BASE / 'data' / 'positions_state.json'
WATCHLIST_PATH  = BASE / 'data' / 'watchlist.json'
TRADES_DB_PATH  = BASE / 'data' / 'trades.db'
LOGS_DIR        = BASE / 'logs'
CONFIG_PATH     = BASE / 'config' / 'strategy_hybrid.yaml'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title='Kiwoom Trading API')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

# ─── Helpers ─────────────────────────────────────────────────────────────────

def today_str() -> str:
    return date.today().strftime('%Y%m%d')

def today_iso() -> str:
    return date.today().isoformat()


def read_json(path: Path) -> dict | list:
    if not path.exists():
        return {}
    with open(path, encoding='utf-8') as f:
        return json.load(f)


def get_trades_db():
    return sqlite3.connect(str(TRADES_DB_PATH))


# ─── yfinance cache (TTL 60s) ─────────────────────────────────────────────────

_price_cache: dict[str, tuple[float, datetime]] = {}
_OHLCV_CACHE: dict[str, list] = {}
_OHLCV_TS: dict[str, datetime] = {}
_STATS_CACHE: dict[str, tuple[dict, datetime]] = {}
CACHE_TTL = 60  # seconds


async def get_current_price(code: str) -> float:
    """Fetch current price via yfinance with in-memory cache."""
    now = datetime.now()
    if code in _price_cache:
        price, ts = _price_cache[code]
        if (now - ts).seconds < CACHE_TTL:
            return price

    loop = asyncio.get_event_loop()
    price = await loop.run_in_executor(None, _fetch_price_sync, code)
    _price_cache[code] = (price, now)
    return price


def _fetch_price_sync(code: str) -> float:
    for suffix in ['.KS', '.KQ']:
        try:
            ticker = yf.Ticker(f'{code}{suffix}')
            info = ticker.fast_info
            p = getattr(info, 'last_price', None) or getattr(info, 'previous_close', None)
            if p and p > 0:
                return float(p)
        except Exception:
            pass
    return 0.0


async def get_ohlcv(code: str, current_price: float) -> list[dict]:
    """Fetch 5-min OHLCV (40 bars). Cache 5 min."""
    now = datetime.now()
    if code in _OHLCV_TS and (now - _OHLCV_TS[code]).seconds < 300:
        return _OHLCV_CACHE.get(code, [])

    loop = asyncio.get_event_loop()
    bars = await loop.run_in_executor(None, _fetch_ohlcv_sync, code)
    _OHLCV_CACHE[code] = bars
    _OHLCV_TS[code] = now
    return bars


def _fetch_ohlcv_sync(code: str) -> list[dict]:
    from datetime import timezone, timedelta
    KST = timezone(timedelta(hours=9))
    for suffix in ['.KS', '.KQ']:
        try:
            df = yf.download(f'{code}{suffix}', period='3d', interval='5m',
                             progress=False, auto_adjust=True)
            if df.empty:
                continue

            # Convert UTC index → KST, filter trading hours only (09:00~15:35)
            df.index = df.index.tz_convert(KST)
            df = df[
                (df.index.time >= __import__('datetime').time(9, 0)) &
                (df.index.time <= __import__('datetime').time(15, 35))
            ]
            if df.empty:
                continue
            df = df.tail(40)

            def _val(v):
                return int(v.iloc[0]) if hasattr(v, 'iloc') else int(v)

            result = []
            for ts, row in df.iterrows():
                result.append({
                    'time':   ts.strftime('%H:%M'),
                    'open':   _val(row['Open']),
                    'high':   _val(row['High']),
                    'low':    _val(row['Low']),
                    'close':  _val(row['Close']),
                    'volume': _val(row['Volume']),
                })
            return result
        except Exception as e:
            logger.warning(f'OHLCV fetch failed {code}{suffix}: {e}')
    return []


# ─── Supply / Demand ─────────────────────────────────────────────────────────

_supply_cache: dict[str, tuple[dict, datetime]] = {}
_SUPPLY_TTL = 300  # 5 minutes


async def fetch_supply(stock_code: str) -> dict:
    """Fetch investor trend (5-day) via Kiwoom API."""
    now = datetime.now()
    if stock_code in _supply_cache:
        data, ts = _supply_cache[stock_code]
        if (now - ts).seconds < _SUPPLY_TTL:
            return data

    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(None, _fetch_supply_sync, stock_code)
    _supply_cache[stock_code] = (data, now)
    return data


def _fetch_supply_sync(stock_code: str) -> dict:
    try:
        from kiwoom_api import KiwoomAPI
        api = KiwoomAPI()
        result = api.get_investor_trend(stock_code)
        rows = result.get('stk_invsr_orgn', [])

        # Aggregate last 5 days (단위: 천주 → 억원 근사: ×현재가/1000/10000)
        foreign_5d = sum(int(r.get('frgnr_invsr', 0) or 0) for r in rows[:5])
        inst_5d    = sum(int(r.get('orgn', 0) or 0) for r in rows[:5])
        program_5d = sum(int(r.get('fnnc_invt', 0) or 0) for r in rows[:5])  # 금융투자(프로그램 대리)
        retail_5d  = sum(int(r.get('ind_invsr', 0) or 0) for r in rows[:5])
        days       = min(5, len(rows))

        # 단위: 천주 → 표시는 천주 그대로 (충분히 의미있음)
        # 부호: + = 순매수, - = 순매도
        return {
            'foreign':    foreign_5d,
            'institution': inst_5d,
            'program':    program_5d,
            'retailNet':  retail_5d,
            'days':       days,
            'raw':        rows[:5],
        }
    except Exception as e:
        logger.warning(f'Supply fetch failed {stock_code}: {e}')
        return {'foreign': 0, 'institution': 0, 'program': 0, 'retailNet': 0, 'days': 0, 'raw': []}


def _make_supply_summary(supply: dict, stock_name: str) -> str:
    f = supply['foreign']
    i = supply['institution']
    days = supply['days']
    if f == 0 and i == 0:
        return f'{stock_name} 수급 데이터 없음'
    f_str = f"외국인 {'+' if f>=0 else ''}{f:,}천주"
    i_str = f"기관 {'+' if i>=0 else ''}{i:,}천주"
    tone = '동반매수' if f > 0 and i > 0 else ('동반매도' if f < 0 and i < 0 else '엇갈림')
    return f'{days}일 수급: {f_str} / {i_str} ({tone})'


def _calc_supply_score(supply: dict) -> float:
    """Foreign + institution combined → 1~10 score."""
    f = supply.get('foreign', 0)
    i = supply.get('institution', 0)
    combined = f + i
    if combined > 5000:   return 9.5
    if combined > 2000:   return 8.5
    if combined > 500:    return 7.5
    if combined > 0:      return 6.5
    if combined > -500:   return 5.0
    if combined > -2000:  return 3.5
    return 2.0


# ─── Candidate Stats (vol_ratio, rsi, change_pct, sparkline) ─────────────────

def _safe_float(v, default: float) -> float:
    """Return default if v is NaN/Inf/None."""
    import math
    try:
        f = float(v)
        return default if (math.isnan(f) or math.isinf(f)) else f
    except Exception:
        return default


def _fetch_stats_sync(code: str) -> dict:
    """Fetch daily stats for candidate: vol_ratio, rsi, change_pct, sparkline."""
    default = {'vol_ratio': 1.0, 'rsi': 50.0, 'change_pct': 0.0, 'sparkline': [50] * 10}
    for suffix in ['.KS', '.KQ']:
        try:
            df = yf.download(f'{code}{suffix}', period='30d', interval='1d',
                             progress=False, auto_adjust=True)
            if df.empty or len(df) < 5:
                continue
            close = df['Close'].squeeze()
            volume = df['Volume'].squeeze()

            # change_pct
            change_pct = _safe_float(
                (close.iloc[-1] - close.iloc[-2]) / close.iloc[-2] * 100 if len(close) >= 2 else 0.0,
                0.0
            )

            # vol_ratio
            vol_avg = _safe_float(volume.iloc[:-1].mean() if len(volume) > 1 else volume.iloc[-1], 0.0)
            vol_today = _safe_float(volume.iloc[-1], 0.0)
            vol_ratio = round(min(_safe_float(vol_today / vol_avg, 1.0) if vol_avg > 0 else 1.0, 9.9), 1)

            # RSI-14
            delta = close.diff().dropna()
            gain = delta.clip(lower=0).rolling(14).mean().dropna()
            loss = (-delta.clip(upper=0)).rolling(14).mean().dropna()
            loss_val = _safe_float(loss.iloc[-1], 0.0) if len(loss) > 0 else 0.0
            gain_val = _safe_float(gain.iloc[-1], 0.0) if len(gain) > 0 else 0.0
            if len(gain) > 0 and loss_val > 0:
                rs = gain_val / loss_val
                rsi = round(100 - 100 / (1 + rs), 1)
            elif len(gain) > 0:
                rsi = 100.0
            else:
                rsi = 50.0
            rsi = _safe_float(rsi, 50.0)

            # sparkline: last 10 days close (sanitize each value)
            raw = close.tail(10).tolist()
            sparkline = [_safe_float(v, 0.0) for v in raw]
            if not sparkline:
                sparkline = [50.0] * 10

            return {
                'vol_ratio': vol_ratio,
                'rsi': rsi,
                'change_pct': round(change_pct, 2),
                'sparkline': sparkline,
            }
        except Exception as e:
            logger.debug(f'stats fetch failed {code}{suffix}: {e}')
    return default


async def get_stats(code: str) -> dict:
    now = datetime.now()
    if code in _STATS_CACHE:
        cached, ts = _STATS_CACHE[code]
        if (now - ts).seconds < 300:
            return cached
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, _fetch_stats_sync, code)
    _STATS_CACHE[code] = (result, now)
    return result


def _calc_entry_score(news_score: float, supply_score: float, vol_ratio: float, rsi: float) -> int:
    """Compute 0-99 entry score from available real data."""
    n = (news_score / 10) * 100                        # 0-100
    s = (supply_score / 10) * 100                      # 0-100
    v = min(vol_ratio / 3.0, 1.0) * 100               # 0-100 (3x = max)
    r_dist = abs(rsi - 55)                             # ideal RSI ~55
    r = max(0.0, 100.0 - r_dist * 2.5)                # ±40 → 0
    score = n * 0.25 + s * 0.30 + v * 0.20 + r * 0.25
    return max(10, min(99, int(score)))


# ─── News ────────────────────────────────────────────────────────────────────

_POS_WORDS = ['상승','급등','호재','수주','매출','흑자','성장','확대','증가','신고가',
              '돌파','기대','계약','투자','개발','출시','호실적','개선','회복','강세',
              '수혜','부각','급반등','신제품','특허','순매수','매집','호조','상향',
              '외국인 매수','기관 매수','강력','급반등','최대','사상','레벨업','돌파',
              '목표가 상향','어닝서프라이즈','흑자전환','실적개선']
_NEG_WORDS = ['하락','급락','악재','적자','감소','손실','위기','우려','규제','소송',
              '수사','충당금','경고','리스크','부진','실망','하향','취소','지연','폐쇄',
              '리콜','제재','벌금','손해','순매도','매도','급락','하향','목표가 하향',
              '어닝쇼크','적자전환','실적부진','배임','횡령','경영권']

_news_cache: dict[str, tuple[list, datetime]] = {}
_NEWS_TTL = 600  # 10 minutes

# 한국투자증권 뉴스 제공사 코드 → 이름
_KIS_SOURCE_MAP = {
    '1': '연합뉴스', '2': '연합인포맥스', '3': '뉴스핌', '4': '뉴시스',
    '5': '머니투데이', '6': '이데일리', '7': '한국경제', '8': '매일경제',
    '9': '파이낸셜뉴스', 'A': 'MBN', 'B': '서울경제', 'C': '아시아경제',
    'D': '조선비즈', 'E': '전자신문', 'F': '헤럴드경제', 'G': '공시',
    'H': '한국거래소', 'I': '금융감독원', 'J': '기획재정부',
}


def _score_sentiment(title: str) -> tuple[str, float]:
    """Keyword-based sentiment → (label, impact 0~1)."""
    text = title.lower()
    pos = sum(1 for w in _POS_WORDS if w in text)
    neg = sum(1 for w in _NEG_WORDS if w in text)
    total = pos + neg or 1
    if pos > neg:
        return 'positive', round(min(0.95, 0.45 + pos / total * 0.5), 2)
    elif neg > pos:
        return 'negative', round(min(0.95, 0.45 + neg / total * 0.5), 2)
    return 'neutral', 0.3


def _fmt_kis_time(date_str: str, time_str: str) -> str:
    """'20260410', '155025' → '04/10 15:50'"""
    try:
        return f"{date_str[4:6]}/{date_str[6:8]} {time_str[:2]}:{time_str[2:4]}"
    except Exception:
        return date_str


async def fetch_news(stock_code: str, display: int = 6) -> list[dict]:
    """Fetch news via KIS API with cache (keyed by stock_code)."""
    now = datetime.now()
    if stock_code in _news_cache:
        items, ts = _news_cache[stock_code]
        if (now - ts).seconds < _NEWS_TTL:
            return items

    loop = asyncio.get_event_loop()
    items = await loop.run_in_executor(None, _fetch_news_sync, stock_code, display)
    _news_cache[stock_code] = (items, now)
    return items


def _fetch_news_sync(stock_code: str, display: int) -> list[dict]:
    try:
        from korea_invest_api import KoreaInvestAPI
        api = KoreaInvestAPI()
        raw = api.get_news_titles(stock_code=stock_code, count=display)
        result = []
        for n in raw:
            title = n.get('title', '')
            sentiment, impact = _score_sentiment(title)
            source_code = n.get('source_code', '')
            source = _KIS_SOURCE_MAP.get(source_code, f'KIS-{source_code}')
            result.append({
                'text':      title[:60],
                'sentiment': sentiment,
                'impact':    impact if sentiment != 'negative' else -impact,
                'source':    source,
                'time':      _fmt_kis_time(n.get('date', ''), n.get('time', '')),
            })
        return result
    except Exception as e:
        logger.warning(f'KIS news fetch failed for {stock_code}: {e}')
        return []


def _calc_news_score(news_items: list[dict]) -> float:
    """Average sentiment score → 0~10."""
    if not news_items:
        return 5.0
    pos = sum(1 for n in news_items if n['sentiment'] == 'positive')
    neg = sum(1 for n in news_items if n['sentiment'] == 'negative')
    total = len(news_items)
    score = 5.0 + (pos - neg) / total * 3.5
    return round(max(1.0, min(10.0, score)), 1)


# ─── Log parsing ──────────────────────────────────────────────────────────────

def _latest_log_path() -> Path | None:
    """Return most recent auto_trading log (today or last trading day)."""
    for i in range(7):
        d = date.today() - timedelta(days=i)
        p = LOGS_DIR / f'auto_trading_{d.strftime("%Y%m%d")}.log'
        if p.exists():
            return p
    return None


def parse_today_log(max_lines: int = 400) -> list[dict]:
    """Parse latest auto_trading log into decision events."""
    log_path = _latest_log_path()
    if not log_path:
        return []

    events = []
    # Read last max_lines to keep it fast
    with open(log_path, encoding='utf-8') as f:
        lines = f.readlines()
    lines = lines[-max_lines:]

    # Log format: "2026-04-10 09:17:47,014 - INFO - <message>"
    TS = r'\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}),\d+'
    pat_accept = re.compile(TS + r' - \w+ - .*✅ ACCEPT (\d{6}) @([\d,]+)원 \| .* conf=([\d.]+) alpha=([+\-\d.]+)')
    pat_reject = re.compile(TS + r' - \w+ - .*❌ REJECT (\d{6}) \| [^|]+ \| (.+)')
    pat_mktctx = re.compile(TS + r' - \w+ - \[MKT_CTX\] (.+)')

    seen_ids = set()
    for line in lines:
        line = line.strip()
        if not line:
            continue

        for pat, builder in [
            (pat_accept, lambda m: {
                'type': 'FILTER', 'event': 'ACCEPT',
                'symbol': m.group(2),
                'params': f'price:{m.group(3)}원  conf:{m.group(4)}  alpha:{m.group(5)}',
                'result': 'PASS', 'resultClass': 'pass', 'time': m.group(1),
            }),
            (pat_reject, lambda m: {
                'type': 'FILTER', 'event': 'REJECT',
                'symbol': m.group(2), 'params': m.group(3)[:60],
                'result': 'REJECT', 'resultClass': 'reject', 'time': m.group(1),
            }),
            (pat_mktctx, lambda m: {
                'type': 'SYSTEM', 'event': 'MKT_CTX',
                'symbol': '——', 'params': m.group(2)[:80],
                'result': 'INFO', 'resultClass': 'info', 'time': m.group(1),
            }),
        ]:
            m = pat.match(line)
            if m:
                uid = f"{m.group(1)}_{line[:60]}"
                if uid not in seen_ids:
                    seen_ids.add(uid)
                    ev = builder(m)
                    ev['id'] = f'log_{len(events)}_{hash(line) & 0xffff}'
                    ev['fnRef'] = 'main_auto_trading.py'
                    events.append(ev)
                break

    return events[-50:]  # Return last 50 events


# ─── Performance from trades ─────────────────────────────────────────────────

def compute_performance() -> dict:
    if not TRADES_DB_PATH.exists():
        return _empty_perf()

    conn = get_trades_db()
    today = today_iso()
    week_start = (date.today() - timedelta(days=7)).isoformat()
    month_start = (date.today() - timedelta(days=30)).isoformat()

    def fetch(since: str) -> list:
        return conn.execute(
            "SELECT realized_pnl, trade_type FROM trades WHERE trade_date >= ? AND trade_type='SELL'",
            (since,)
        ).fetchall()

    def summarize(rows: list) -> dict:
        pnls = [r[0] for r in rows if r[0] is not None]
        wins = [p for p in pnls if p > 0]
        return {
            'pnl': int(sum(pnls)),
            'pct': round(sum(pnls) / 5_000_000 * 100, 2) if pnls else 0,
            'trades': len(pnls),
            'winRate': round(len(wins) / len(pnls) * 100) if pnls else 0,
        }

    perf_today = summarize(fetch(today))
    perf_week  = summarize(fetch(week_start))
    perf_month = summarize(fetch(month_start))

    # Strategy breakdown: match each SELL to the most recent BUY of the same stock
    all_rows = conn.execute(
        "SELECT id, stock_code, trade_type, realized_pnl, strategy, trade_date FROM trades ORDER BY id"
    ).fetchall()
    conn.close()

    # Build map: stock_code → last BUY strategy seen
    from collections import defaultdict
    import statistics
    last_buy_strat: dict[str, str] = {}
    strat_map: dict = defaultdict(list)
    for _, code, ttype, pnl, strat, _ in all_rows:
        if ttype == 'BUY' and strat and strat != 'EXIT':
            last_buy_strat[code] = strat
        elif ttype == 'SELL' and pnl is not None:
            entry_strat = last_buy_strat.get(code, strat or 'SMC')
            strat_map[entry_strat].append(float(pnl))

    strategies = []
    for code, pnls in sorted(strat_map.items()):
        wins = [p for p in pnls if p > 0]
        avg_ret = round(sum(pnls) / len(pnls) / 5_000_000 * 100, 2) if pnls else 0
        win_rate = round(len(wins) / len(pnls) * 100) if pnls else 0
        std = statistics.stdev(pnls) if len(pnls) >= 2 else 0.0
        sharpe = round(abs(sum(pnls) / len(pnls)) / std, 2) if std > 0 else 0.0
        strategies.append({
            'code': code[:6],
            'winRate': win_rate,
            'avgReturn': avg_ret,
            'sharpe': sharpe,
            'trades': len(pnls),
        })

    return {'today': perf_today, 'week': perf_week, 'month': perf_month, 'strategies': strategies}


def _empty_perf() -> dict:
    z = {'pnl': 0, 'pct': 0.0, 'trades': 0, 'winRate': 0}
    return {'today': z, 'week': z, 'month': z}


# ─── Candidates from watchlist + live prices ─────────────────────────────────

async def build_candidates() -> list[dict]:
    watchlist = read_json(WATCHLIST_PATH)
    if not isinstance(watchlist, list):
        watchlist = []

    positions_raw: dict = read_json(POSITIONS_PATH)

    # Fetch prices + news concurrently
    async def enrich(item: dict) -> dict | None:
        code = item.get('stock_code', '')
        name = item.get('stock_name', code)
        if not code:
            return None

        price, news_items, supply, stats = await asyncio.gather(
            get_current_price(code),
            fetch_news(code, display=5),
            fetch_supply(code),
            get_stats(code),
        )
        in_position  = code in positions_raw
        news_score   = _calc_news_score(news_items)
        supply_score = _calc_supply_score(supply)
        vol_ratio    = stats['vol_ratio']
        rsi          = stats['rsi']
        change_pct   = stats['change_pct']
        sparkline    = stats['sparkline']
        entry_score  = _calc_entry_score(news_score, supply_score, vol_ratio, rsi)
        choch_grade  = 'A' if entry_score >= 70 else 'B'

        return {
            'symbol': code,
            'name': name,
            'price': price,
            'changePct': change_pct,
            'sparkline': sparkline,
            'entryScore': entry_score,
            'riskScore': 30,
            'confidence': item.get('win_rate', 0) / 100 if item.get('win_rate', 0) > 0 else 0.5,
            'chochGrade': choch_grade,
            'volRatio': vol_ratio,
            'rsi': rsi,
            'conditions': [
                {'key': 'choch',   'label': 'CHoCH',  'pass': entry_score >= 60, 'value': choch_grade},
                {'key': 'vol',     'label': 'VOL',    'pass': vol_ratio >= 1.3,  'value': f'{vol_ratio}x'},
                {'key': 'news',    'label': 'NEWS',   'pass': news_score >= 5.5,   'value': f'{news_score}'},
                {'key': 'supply',  'label': 'SUPPLY', 'pass': supply_score >= 5.5, 'value': f'{supply_score:.1f}'},
            ],
            'nearMiss': entry_score >= 50 and entry_score < 60,
            'strategy': 'SMC',
            'newsScore': news_score,
            'supplyScore': supply_score,
            'technicalScore': entry_score / 10.0,
            'filterStage': 2 if in_position else 1,
            'aiExpectation': item.get('avg_profit_pct', 0) or 1.0,
        }

    tasks = [enrich(item) for item in watchlist[:15]]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]


# ─── Positions ────────────────────────────────────────────────────────────────

def build_positions() -> list[dict]:
    raw: dict = read_json(POSITIONS_PATH)
    result = []
    for code, pos in raw.items():
        if not isinstance(pos, dict):
            continue
        entry = pos.get('entry_price') or pos.get('avg_price', 0)
        current = pos.get('current_price', entry)
        qty = pos.get('quantity', 0)
        pnl = (current - entry) * qty
        pnl_pct = (current / entry - 1) * 100 if entry else 0
        trailing_stop = pos.get('trailing_stop_price')

        result.append({
            'symbol': code,
            'name': pos.get('name') or pos.get('stock_name', code),
            'entryPrice': entry,
            'currentPrice': current,
            'quantity': qty,
            'sl': trailing_stop or round(entry * 0.97),
            'tp': round(entry * 1.05),
            'pnl': round(pnl),
            'pnlPct': round(pnl_pct, 2),
            'strategy': 'SMC',
            'holdMinutes': _calc_hold_minutes(pos.get('entry_date', '')),
            'chochGrade': 'A',
        })
    return result


def _calc_hold_minutes(entry_date_str: str) -> int:
    if not entry_date_str:
        return 0
    try:
        entry_dt = datetime.fromisoformat(entry_date_str.replace('Z', ''))
        delta = datetime.now() - entry_dt
        return max(0, int(delta.total_seconds() / 60))
    except Exception:
        return 0


# ─── Trades ───────────────────────────────────────────────────────────────────

def build_trades(limit: int = 20) -> list[dict]:
    if not TRADES_DB_PATH.exists():
        return []
    conn = get_trades_db()
    # Fetch all rows ASC to pair BUY→SELL correctly
    all_rows = conn.execute(
        """SELECT id, trade_date, timestamp, stock_code, stock_name, trade_type,
                  quantity, price, realized_pnl, reason, strategy
           FROM trades ORDER BY id ASC"""
    ).fetchall()
    conn.close()

    raw = []
    for r in all_rows:
        raw.append({
            'id': r[0], 'date': r[1], 'ts': r[2], 'code': r[3], 'name': r[4],
            'type': r[5], 'qty': r[6], 'price': float(r[7] or 0), 'pnl': float(r[8] or 0),
            'reason': r[9] or '', 'strategy': r[10] or 'SMC',
        })

    # Match each SELL to the latest BUY of the same stock
    last_buy: dict[str, dict] = {}
    pairs: list[tuple] = []
    for t in raw:
        if t['type'] == 'BUY':
            last_buy[t['code']] = t
        elif t['type'] == 'SELL':
            buy = last_buy.get(t['code'])
            pairs.append((buy, t))

    # Sort by SELL id DESC, take limit
    pairs.sort(key=lambda p: p[1]['id'], reverse=True)
    pairs = pairs[:limit]

    trades = []
    for buy, sell in pairs:
        entry_price = buy['price'] if buy else sell['price']
        entry_strat = (buy['strategy'] if buy and buy['strategy'] not in ('EXIT', None) else sell['strategy']) or 'SMC'
        pnl = sell['pnl']
        qty = sell['qty']

        # Duration
        if buy and buy['ts'] and sell['ts']:
            try:
                from dateutil import parser as dparser
                t1 = dparser.parse(buy['ts'])
                t2 = dparser.parse(sell['ts'])
                mins = int((t2 - t1).total_seconds() / 60)
                duration = f"{mins}분" if mins < 60 else f"{mins//60}h{mins%60}m"
            except Exception:
                duration = '—'
        else:
            duration = '—'

        exit_tag = 'TRAIL' if 'trail'  in sell['reason'].lower() else \
                   'HARD'  if 'hard'   in sell['reason'].lower() else \
                   'EF'    if 'early'  in sell['reason'].lower() else \
                   'EOD'   if '강제'   in sell['reason'] else \
                   'OVN'   if '오버나이트' in sell['reason'] else 'EXIT'

        pnl_pct = round(pnl / (entry_price * qty) * 100, 2) if entry_price and qty else 0

        trades.append({
            'id': f"t{sell['id']}",
            'symbol': sell['code'],
            'name': sell['name'],
            'strategy': entry_strat,
            'entryPrice': entry_price,
            'exitPrice': sell['price'],
            'quantity': qty,
            'pnl': int(pnl),
            'pnlPct': pnl_pct,
            'win': pnl > 0,
            'duration': duration,
            'exitReason': sell['reason'][:60],
            'exitTag': exit_tag,
            'time': sell['ts'][11:16] if sell['ts'] else '',
            'newsScoreAtEntry': 0,
            'supplyScoreAtEntry': 0,
            'technicalScoreAtEntry': 0,
        })

    return trades


# ─── Market Regime ────────────────────────────────────────────────────────────

_regime_cache: dict = {}
_regime_ts: Optional[datetime] = None


def get_market_regime() -> dict:
    global _regime_cache, _regime_ts
    if _regime_ts and (datetime.now() - _regime_ts).seconds < 300:
        return _regime_cache

    try:
        # KOSPI ADX approximation via ^KS11
        df = yf.download('^KS11', period='30d', interval='1d', progress=False, auto_adjust=True)
        if not df.empty and len(df) >= 14:
            close = df['Close'].squeeze()
            adx_val = _calc_adx(close)
            latest_close = float(close.iloc[-1])
            prev_close = float(close.iloc[-2]) if len(close) > 1 else latest_close
            change_pct = (latest_close / prev_close - 1) * 100

            # VIX (^VIX) - global fear gauge
            vix_df = yf.download('^VIX', period='5d', interval='1d', progress=False, auto_adjust=True)
            if not vix_df.empty:
                vix_raw = vix_df['Close'].iloc[-1]
                vix_val = float(vix_raw.iloc[0]) if hasattr(vix_raw, 'iloc') else float(vix_raw)
            else:
                vix_val = 20.0

            if adx_val > 25 and change_pct > 0:
                regime = 'TREND'
                score = min(95, int(adx_val * 2))
            elif adx_val > 25 and change_pct < -0.5:
                regime = 'VOLATILE'
                score = 40
            else:
                regime = 'SIDEWAYS'
                score = 55

            _regime_cache = {
                'regime': {'regime': regime, 'score': score,
                           'vix': round(vix_val, 1), 'adx': round(adx_val, 1),
                           'trend': min(100, int(adx_val * 2.5)),
                           'momentum': min(100, int(abs(change_pct) * 20 + 40)),
                           'volume': 60, 'risk': min(100, int(vix_val * 3))},
                'health': _get_health(),
            }
            _regime_ts = datetime.now()
            return _regime_cache
    except Exception as e:
        logger.warning(f'Market regime fetch failed: {e}')

    # Fallback
    _regime_cache = {
        'regime': {'regime': 'TREND', 'score': 65, 'vix': 20.0, 'adx': 28.0,
                   'trend': 70, 'momentum': 60, 'volume': 55, 'risk': 42},
        'health': _get_health(),
    }
    _regime_ts = datetime.now()
    return _regime_cache


def _calc_adx(close: pd.Series, period: int = 14) -> float:
    """Simple ADX approximation from close prices."""
    try:
        close = close.squeeze()  # ensure 1-D
        diff = close.diff().abs()
        smooth = diff.rolling(period).mean()
        val = smooth.dropna().iloc[-1]
        close_val = float(close.iloc[-1])
        adx = float(val) / close_val * 1000 if close_val else 28.0
        return min(50, max(10, round(adx, 1)))
    except Exception:
        return 28.0


def _get_health() -> dict:
    """Check if kiwoom trading process is alive via heartbeat."""
    hb_path = Path('/tmp/kiwoom_heartbeat.json')
    if hb_path.exists():
        try:
            hb = json.loads(hb_path.read_text())
            hb_time = datetime.fromisoformat(hb.get('time', '2000-01-01'))
            age_sec = (datetime.now() - hb_time).seconds
            status = 'ok' if age_sec < 120 else 'warn'
            latency = min(age_sec, 999)
        except Exception:
            status, latency = 'warn', 0
    else:
        status, latency = 'warn', 0

    return {
        'dataFeed':    {'status': status, 'latency': latency},
        'orderRouter': {'status': 'ok',   'latency': 5},
        'riskEngine':  {'status': 'ok',   'latency': 2},
        'signalGen':   {'status': status, 'latency': latency},
    }


# ─── Filter Stats ─────────────────────────────────────────────────────────────

def get_filter_stats() -> list[dict]:
    # Fallback: scan up to 7 days back for latest report
    report = {}
    for i in range(7):
        d = date.today() - timedelta(days=i)
        p = LOGS_DIR / f'reentry_report_{d.isoformat()}.json'
        if p.exists():
            report = read_json(p)
            break

    # Also count today's trades from DB as proxy for passed signals
    exec_today = 0
    if TRADES_DB_PATH.exists():
        conn = get_trades_db()
        row = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE trade_date=? AND trade_type='BUY'", (today_iso(),)
        ).fetchone()
        conn.close()
        exec_today = row[0] if row else 0

    total_signals = report.get('total_entry_signals', 0) or exec_today
    ef_count = report.get('ef_triggered_count', 0)
    blocked = report.get('reentry_blocked_count', 0)
    passed2 = max(0, total_signals - ef_count - blocked)

    # If report is empty, derive from log file
    if total_signals == 0:
        log = _latest_log_path()
        if log:
            accept = sum(1 for l in log.read_text(errors='ignore').splitlines() if '✅ ACCEPT' in l)
            reject = sum(1 for l in log.read_text(errors='ignore').splitlines() if '❌ REJECT' in l)
            total_signals = accept + reject
            passed2 = accept

    return [
        {'stage': 1, 'total': max(total_signals + blocked, total_signals), 'passed': total_signals},
        {'stage': 2, 'total': total_signals, 'passed': passed2},
    ]


# ─── Params from YAML ────────────────────────────────────────────────────────

def get_params() -> list[dict]:
    if not CONFIG_PATH.exists():
        return []
    try:
        import yaml
        with open(CONFIG_PATH, encoding='utf-8') as f:
            cfg = yaml.safe_load(f)

        trailing = cfg.get('trailing', {})
        risk     = cfg.get('risk_control', {})
        smc      = cfg.get('smc', {}).get('choch_grade', {})
        tf       = cfg.get('time_filter', {})

        return [
            {'key': 'trailing_activation', 'label': 'Trailing 활성화 (%)', 'value': trailing.get('activation_pct', 1.5),
             'defaultValue': 1.5, 'type': 'number', 'min': 0.5, 'max': 5.0, 'step': 0.1,
             'description': '트레일링 스탑 활성화 기준 수익률'},
            {'key': 'trailing_stop_pct', 'label': 'Stop Loss (%)', 'value': trailing.get('stop_loss_pct', 3.0),
             'defaultValue': 3.0, 'type': 'number', 'min': 1.0, 'max': 5.0, 'step': 0.5,
             'description': '기본 손절선'},
            {'key': 'max_positions', 'label': 'Max Positions', 'value': risk.get('max_positions', 3),
             'defaultValue': 3, 'type': 'number', 'min': 1, 'max': 10, 'step': 1,
             'description': '동시 최대 보유 종목 수'},
            {'key': 'smc_cutoff', 'label': 'SMC 진입 마감', 'value': tf.get('smc_afternoon_cutoff', '12:30'),
             'defaultValue': '12:30', 'type': 'select', 'options': ['10:30', '11:00', '11:30', '12:00', '12:30', '13:00'],
             'description': 'SMC A급 진입 마감 시각'},
            {'key': 'grade_b_cutoff', 'label': 'B급 CHoCH 마감', 'value': smc.get('grade_b_cutoff', '11:00'),
             'defaultValue': '11:00', 'type': 'select', 'options': ['10:30', '11:00', '11:30'],
             'description': 'B급 CHoCH 진입 마감 시각'},
            {'key': 'dry_run', 'label': 'DRY RUN', 'value': cfg.get('dry_run', False),
             'defaultValue': False, 'type': 'boolean',
             'description': '주문 없이 신호만 로깅 (테스트 모드)'},
        ]
    except Exception as e:
        logger.warning(f'Params read failed: {e}')
        return []


# ─── Endpoints ────────────────────────────────────────────────────────────────

@app.get('/api/market/regime')
async def api_market_regime():
    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, get_market_regime)
    return result


@app.get('/api/candidates')
async def api_candidates():
    return await build_candidates()


@app.get('/api/candidates/{symbol}')
async def api_candidate_detail(symbol: str):
    positions_raw: dict = read_json(POSITIONS_PATH)
    pos = positions_raw.get(symbol)

    # Find stock name from watchlist
    watchlist = read_json(WATCHLIST_PATH)
    stock_name = symbol
    if isinstance(watchlist, list):
        for w in watchlist:
            if w.get('stock_code') == symbol:
                stock_name = w.get('stock_name', symbol)
                break
    # Also check positions
    if pos:
        stock_name = pos.get('name') or pos.get('stock_name', stock_name)

    # Fetch price, OHLCV, news, supply concurrently
    price, bars, news_items, supply = await asyncio.gather(
        get_current_price(symbol),
        get_ohlcv(symbol, 0),
        fetch_news(symbol, display=6),
        fetch_supply(symbol),
    )

    if not bars and price > 0:
        bars = [{'time': '09:00', 'open': price, 'high': price, 'low': price,
                 'close': price, 'volume': 0}]

    entry_idx    = max(0, len(bars) - 12)
    entry_price  = bars[entry_idx]['close'] if bars else price
    sl  = round(entry_price * 0.979)
    tp  = round(entry_price * 1.026)
    rr  = round((tp - entry_price) / max(1, entry_price - sl), 1)

    news_score    = _calc_news_score(news_items)
    supply_score  = _calc_supply_score(supply)
    news_summary  = _make_news_summary(news_items, stock_name)
    supply_summary = _make_supply_summary(supply, stock_name)

    # supplyFlow 단위: 천주 (양수=순매수, 음수=순매도)
    supply_flow = {
        'foreign':    supply.get('foreign', 0),
        'institution': supply.get('institution', 0),
        'program':    supply.get('program', 0),
        'retailNet':  supply.get('retailNet', 0),
        'days':       supply.get('days', 0),
    }

    news_s  = round(news_score * 10)
    supply_s = round(supply_score * 10)
    total_s  = round(news_s * 0.25 + supply_s * 0.30 + 65 * 0.30 + 60 * 0.15)

    return {
        'symbol': symbol,
        'ohlcv': bars,
        'entryIndex': entry_idx,
        'scores': [
            {'name': 'NEWS',   'score': news_s,   'weight': 25, 'details': news_summary[:40]},
            {'name': 'SUPPLY', 'score': supply_s, 'weight': 30, 'details': supply_summary[:40]},
            {'name': 'TECH',   'score': 65,        'weight': 30, 'details': f'현재가 {price:,}원'},
            {'name': 'VOLUME', 'score': 60,        'weight': 15, 'details': '거래량 연동 예정'},
        ],
        'totalScore': total_s,
        'entryReason': f'Watchlist 스캔 종목',
        'sl': sl, 'tp': tp, 'rrRatio': rr, 'positionSize': 10,
        'newsSummary': news_summary,
        'newsItems': news_items,
        'supplySummary': supply_summary,
        'supplyFlow': supply_flow,
        'technicalSummary': f'현재가: {price:,}원',
        'aiExpectation': 1.5,
        'similarPatterns': [],
        'filterPassReason': f'뉴스 {len(news_items)}건 / 수급 {supply_flow["days"]}일치 수집',
    }


def _make_news_summary(news_items: list[dict], stock_name: str) -> str:
    if not news_items:
        return f'{stock_name} 관련 뉴스 없음'
    pos = sum(1 for n in news_items if n['sentiment'] == 'positive')
    neg = sum(1 for n in news_items if n['sentiment'] == 'negative')
    total = len(news_items)
    tone = '긍정적' if pos > neg else ('부정적' if neg > pos else '중립적')
    latest = news_items[0]['text'][:30]
    return f'{stock_name}: {tone} 뉴스 우세 ({pos}↑{neg}↓/{total}건). 최신: {latest}'


@app.get('/api/decision-log')
def api_decision_log():
    return parse_today_log()


@app.get('/api/positions')
def api_positions():
    return build_positions()


@app.get('/api/trades')
def api_trades():
    return build_trades()


def _compute_risk_metrics() -> list[dict]:
    """Compute risk metrics from trades DB."""
    if not TRADES_DB_PATH.exists():
        return []
    conn = get_trades_db()
    rows = conn.execute(
        "SELECT realized_pnl FROM trades WHERE trade_type='SELL' AND realized_pnl IS NOT NULL ORDER BY id"
    ).fetchall()
    # consecutive losses
    risk_row = {}
    try:
        import json as _json
        rlog = BASE / 'data' / 'risk_log.json'
        if rlog.exists():
            risk_row = _json.loads(rlog.read_text())
    except Exception:
        pass
    conn.close()

    pnls = [float(r[0]) for r in rows]
    if not pnls:
        return []

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    win_rate = round(len(wins) / len(pnls) * 100)
    avg_win  = round(sum(wins) / len(wins) / 10000, 2) if wins else 0
    avg_loss = round(sum(losses) / len(losses) / 10000, 2) if losses else 0
    rr = round(abs(avg_win / avg_loss), 2) if avg_loss else 0

    # Max drawdown (running cumulative)
    cum = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in pnls:
        cum += p
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    max_dd_pct = round(max_dd / 5_000_000 * 100, 1)

    consec = risk_row.get('consecutive_losses', 0)

    metrics = [
        {'label': 'WIN%',   'value': f'{win_rate}%',
         'status': 'ok' if win_rate >= 50 else 'warn' if win_rate >= 35 else 'err',
         'description': f'승률 (총 {len(pnls)}건)'},
        {'label': 'R:R',    'value': str(rr),
         'status': 'ok' if rr >= 1.5 else 'warn' if rr >= 1.0 else 'err',
         'description': f'평균손익비 (승:{avg_win}만 / 패:{avg_loss}만)'},
        {'label': 'MDD',    'value': f'{max_dd_pct}%',
         'status': 'ok' if max_dd_pct < 2 else 'warn' if max_dd_pct < 5 else 'err',
         'description': f'최대낙폭 ({int(max_dd/10000)}만원)'},
        {'label': 'STREAK', 'value': str(consec),
         'status': 'ok' if consec < 2 else 'warn' if consec < 3 else 'err',
         'description': f'현재 연패 {consec}회'},
    ]
    return metrics


@app.get('/api/performance')
def api_performance():
    perf = compute_performance()
    return {**perf, 'riskMetrics': _compute_risk_metrics()}


@app.get('/api/filter-stats')
def api_filter_stats():
    return get_filter_stats()


@app.get('/api/params')
def api_params():
    return get_params()


@app.get('/api/health')
def api_health():
    return {'status': 'ok', 'time': datetime.now().isoformat()}


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('api_server:app', host='0.0.0.0', port=8000, reload=False)
