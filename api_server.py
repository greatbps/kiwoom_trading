"""
Trading Dashboard API Server
- Reads live data from kiwoom_trading system files
- Exposes REST endpoints for the Next.js dashboard
- Run: uvicorn api_server:app --host 0.0.0.0 --port 8000
"""

import sys
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
POSITIONS_PATH            = BASE / 'data' / 'positions_state.json'
ACCOUNT_SNAPSHOT_PATH_K   = BASE / 'data' / 'account_snapshot.json'
WATCHLIST_PATH            = BASE / 'data' / 'watchlist.json'
MONITORING_WATCHLIST_PATH = BASE / 'data' / 'monitoring_watchlist.json'
LOGS_DIR                  = BASE / 'logs'
CONFIG_PATH               = BASE / 'config' / 'strategy_hybrid.yaml'

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ─── Kiwoom API 실시간 연동 (캐시 TTL 30s) ──────────────────────────────────

_kiwoom_cache: dict = {}        # key → (data, timestamp)
_KIWOOM_TTL = 30                # seconds (candidates/stats 등 일반 TTL)


def _kiwoom_api():
    """KiwoomAPI 싱글턴 (프로세스 내 재사용)."""
    if not hasattr(_kiwoom_api, '_inst'):
        from kiwoom_api import KiwoomAPI
        _kiwoom_api._inst = KiwoomAPI()
    return _kiwoom_api._inst


def _kiwoom_cached(key: str, fn, ttl: int = _KIWOOM_TTL):
    """TTL 캐시로 Kiwoom API 중복 호출 방지."""
    now = datetime.now()
    if key in _kiwoom_cache:
        data, ts = _kiwoom_cache[key]
        if (now - ts).total_seconds() < ttl:
            return data
    try:
        data = fn()
        _kiwoom_cache[key] = (data, now)
        return data
    except Exception as e:
        logger.warning(f'[KIWOOM] {key} 조회 실패: {e}')
        # 캐시 만료돼도 마지막 값 반환 (완전 실패보다 stale이 나음)
        if key in _kiwoom_cache:
            return _kiwoom_cache[key][0]
        return None


def fetch_kiwoom_positions() -> list[dict]:
    """
    Kiwoom API (ka01690) → 포지션 목록.
    반환: [{'symbol', 'name', 'qty', 'entry_price', 'current_price',
             'eval_profit', 'profit_rate', 'eval_amount'}, ...]
    """
    def _call():
        result = _kiwoom_api().get_account_info()  # ka01690 — WORKS
        rows = result.get('day_bal_rt', [])
        out = []
        for r in rows:
            qty = int(r.get('rmnd_qty', 0) or 0)
            if qty <= 0:
                continue
            out.append({
                'symbol':        r.get('stk_cd', '').strip(),
                'name':          r.get('stk_nm', '').strip(),
                'qty':           qty,
                'entry_price':   float(r.get('buy_uv', 0) or 0),
                'current_price': float(r.get('cur_prc', 0) or 0),
                'eval_profit':   float(r.get('evltv_prft', 0) or 0),
                'profit_rate':   float(r.get('prft_rt', 0) or 0),
                'eval_amount':   float(r.get('evlt_amt', 0) or 0),
            })
        return out

    return _kiwoom_cached('positions', _call, ttl=_POSITIONS_TTL) or []


def _get_pg_conn():
    """PostgreSQL 연결 반환 (psycopg2)."""
    import os, psycopg2
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        dbname=os.getenv("POSTGRES_DB", "trading_system"),
        user=os.getenv("POSTGRES_USER", "postgres"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )


def _read_account_snapshot_from_db() -> dict:
    """account_snapshot 테이블에서 최신 유효값 읽기."""
    try:
        conn = _get_pg_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT deposit, holding_value, total_assets, eval_profit, snapshot_at
            FROM account_snapshot
            WHERE total_assets > 0
            ORDER BY snapshot_at DESC
            LIMIT 1
        """)
        row = cur.fetchone()
        conn.close()
        if row:
            return {
                'deposit':       int(row[0]),
                'holding_value': int(row[1]),
                'total_assets':  int(row[2]),
                'eval_profit':   int(row[3]),
                '_from_db':      True,
                '_snapshot_at':  row[4].isoformat() if row[4] else None,
            }
    except Exception as e:
        logger.debug(f'[ACCT_DB] 읽기 실패: {e}')
    return {}


def fetch_kiwoom_balance() -> dict:
    """
    1순위: account_snapshot DB (항상 최신 유효값)
    2순위: Kiwoom API 직접 호출 (DB가 비었을 때만)
    반환: {'deposit', 'holding_value', 'total_assets', 'eval_profit'}
    """
    # DB에서 먼저 읽기
    db_val = _read_account_snapshot_from_db()
    if db_val:
        return db_val

    # DB 없을 때만 API 직접 호출
    def _call():
        acct = _kiwoom_api().get_account_info()
        holding_value = int(float(acct.get('tot_evlt_amt',   0) or 0))
        eval_profit   = int(float(acct.get('tot_evltv_prft', 0) or 0))
        deposit = 0
        try:
            bal = _kiwoom_api().get_balance()
            deposit = int(float(bal.get('fc_stk_krw_repl_set_amt', 0) or 0))
        except Exception:
            pass
        return {
            'deposit':       deposit,
            'holding_value': holding_value,
            'total_assets':  deposit + holding_value,
            'eval_profit':   eval_profit,
        }

    return _kiwoom_cached('balance', _call, ttl=_BALANCE_TTL) or {}



app = FastAPI(title='Kiwoom Trading API')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_methods=['*'],
    allow_headers=['*'],
)

_BALANCE_TTL   = 15   # 잔고/예수금 캐시 TTL (초)
_POSITIONS_TTL = 15   # 보유종목 현재가 캐시 TTL (초)


def _invalidate_cache(*keys: str):
    """지정된 캐시 키 즉시 만료."""
    for k in keys:
        _kiwoom_cache.pop(k, None)


async def _background_refresh():
    """백그라운드: 15초마다 잔고·포지션 현재가 선제 갱신."""
    await asyncio.sleep(5)   # 서버 완전 기동 후 시작
    while True:
        try:
            _invalidate_cache('balance', 'positions')
            fetch_kiwoom_balance()
            fetch_kiwoom_positions()
        except Exception as e:
            logger.debug(f'[BG_REFRESH] 갱신 실패: {e}')
        await asyncio.sleep(_POSITIONS_TTL)


@app.on_event('startup')
async def on_startup():
    """서버 기동 시 캐시 선제 적재 + 백그라운드 갱신 시작."""
    try:
        fetch_kiwoom_balance()
        fetch_kiwoom_positions()
        logger.info('[STARTUP] 계좌·포지션 캐시 적재 완료')
    except Exception as e:
        logger.warning(f'[STARTUP] 초기 캐시 적재 실패: {e}')

    asyncio.create_task(_background_refresh())

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
    import datetime as dt
    from datetime import timezone, timedelta
    KST = timezone(timedelta(hours=9))

    def _val(v):
        try:
            return int(float(v))
        except Exception:
            return 0

    for suffix in ['.KS', '.KQ']:
        try:
            ticker = yf.Ticker(f'{code}{suffix}')
            df = ticker.history(period='5d', interval='5m')
            if df.empty:
                continue

            # Convert UTC index → KST, filter trading hours only (09:00~15:35)
            if df.index.tz is None:
                df.index = df.index.tz_localize('UTC').tz_convert(KST)
            else:
                df.index = df.index.tz_convert(KST)

            df = df[
                (df.index.time >= dt.time(9, 0)) &
                (df.index.time <= dt.time(15, 35))
            ]
            if df.empty:
                continue

            # Show most recent trading day's full session
            latest_date = df.index.date[-1]
            df = df[df.index.date == latest_date]
            # If fewer than 3 bars, likely wrong exchange data — skip
            if len(df) < 3:
                continue

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
    """Fetch daily stats via Kiwoom API: vol_ratio, rsi, change_pct, sparkline."""
    default = {'vol_ratio': 1.0, 'rsi': 50.0, 'change_pct': 0.0, 'sparkline': [50] * 10}
    try:
        result = _kiwoom_api().get_daily_chart(stock_code=code)
        if not result or result.get('return_code') != 0:
            return default
        rows = result.get('stk_dt_pole_chart_qry', [])
        if not rows or len(rows) < 5:
            return default

        # 오름차순 정렬 (오래된 것 먼저)
        rows = sorted(rows, key=lambda r: r.get('dt', ''))

        closes  = [abs(float(r.get('cur_prc',  0) or 0)) for r in rows]
        volumes = [abs(float(r.get('trde_qty', 0) or 0)) for r in rows]

        # change_pct: 가장 최근 봉의 등락률
        change_pct = _safe_float(rows[-1].get('trde_tern_rt', 0), 0.0)

        # vol_ratio: 오늘 / 직전 19일 평균
        if len(volumes) >= 2:
            avg_vol = sum(volumes[:-1]) / len(volumes[:-1]) if volumes[:-1] else 1.0
            vol_ratio = round(min(volumes[-1] / avg_vol, 9.9) if avg_vol > 0 else 1.0, 1)
        else:
            vol_ratio = 1.0

        # RSI-14 from close prices
        if len(closes) >= 15:
            import numpy as np
            arr   = np.array(closes, dtype=float)
            delta = np.diff(arr)
            gain  = np.where(delta > 0, delta, 0.0)
            loss  = np.where(delta < 0, -delta, 0.0)
            avg_g = gain[-14:].mean()
            avg_l = loss[-14:].mean()
            rsi   = round(100 - 100 / (1 + avg_g / avg_l), 1) if avg_l > 0 else 100.0
        else:
            rsi = 50.0

        # sparkline: 최근 10일 종가
        sparkline = [_safe_float(v, 0.0) for v in closes[-10:]]
        if not sparkline:
            sparkline = [50.0] * 10

        return {
            'vol_ratio': vol_ratio,
            'rsi': _safe_float(rsi, 50.0),
            'change_pct': round(change_pct, 2),
            'sparkline': sparkline,
        }
    except Exception as e:
        logger.debug(f'[STATS] {code} Kiwoom 조회 실패: {e}')
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
    """Return most recent non-empty auto_trading log (today or last trading day)."""
    for i in range(7):
        d = date.today() - timedelta(days=i)
        p = LOGS_DIR / f'auto_trading_{d.strftime("%Y%m%d")}.log'
        if p.exists() and p.stat().st_size > 0:
            return p
    return None


def _parse_smc_decision_log() -> list[dict]:
    """Parse smc_decision log for CHoCH/SWEEP score events."""
    smc_path = LOGS_DIR / f'smc_decision_{today_str()}.log'
    if not smc_path.exists():
        return []

    events = []
    seen_ids: set = set()
    # Format: "HH:MM:SS [CHOCH] XXXXXX | bullish | level=NNN | ..."
    pat_choch = re.compile(r'(\d{2}:\d{2}:\d{2}) \[CHOCH\] (\d{6}) \| (\w+) \| level=([\d]+) \| .* penetration=([\d.]+)%')

    with open(smc_path, encoding='utf-8') as f:
        lines = f.readlines()

    for line in lines[-200:]:
        line = line.strip()
        m = pat_choch.match(line)
        if m:
            uid = f"{m.group(1)}_{m.group(2)}_choch"
            if uid not in seen_ids:
                seen_ids.add(uid)
                events.append({
                    'type': 'SCORE', 'event': 'CHoCH',
                    'symbol': m.group(2),
                    'params': f'{m.group(3)}  level:{m.group(4)}  pen:{m.group(5)}%',
                    'result': f'CHoCH {m.group(3)}', 'resultClass': 'score',
                    'time': m.group(1), 'fnRef': 'smc_decision.log',
                })

    return events


def parse_today_log() -> list[dict]:
    """Parse latest auto_trading log into decision events.

    Reads the entire file but only matches lines with known keywords (fast grep-style).
    Avoids the 'last N lines' trap where post-market news logs bury trading events.
    """
    log_path = _latest_log_path()
    if not log_path:
        return _parse_smc_decision_log()

    # Log format: "2026-04-10 09:17:47,014 - INFO - <message>"
    TS = r'\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}),\d+'

    # Each entry: (keyword_for_fast_filter, compiled_re, builder_fn)
    # NOTE: ACCEPT/REJECT are parsed from signal_orchestrator.log (full 8:30-16:00 coverage)
    patterns = [
        # ── Market Context ───────────────────────────────────────────────────
        ('[MKT_CTX]',
         re.compile(TS + r' - \w+ - \[MKT_CTX\] (?!캐시)(.+)'),
         lambda m: {
             'type': 'SYSTEM', 'event': 'MKT_CTX', 'symbol': '——',
             'params': m.group(2)[:80],
             'result': 'BLOCK' if 'NO_TRADE' in m.group(2) else 'INFO',
             'resultClass': 'block' if 'NO_TRADE' in m.group(2) else 'info',
             'time': m.group(1),
         }),
        # ── Trend Market Block ───────────────────────────────────────────────
        ('[TREND_MKT_BLOCK]',
         re.compile(TS + r' - \w+ - \[TREND_MKT_BLOCK\] (\d{6}) [^:]+: (.+)'),
         lambda m: {
             'type': 'BLOCK', 'event': 'TREND_BLOCK', 'symbol': m.group(2),
             'params': m.group(3)[:60], 'result': 'BLOCK',
             'resultClass': 'block', 'time': m.group(1),
         }),
        # ── SMC Entry ───────────────────────────────────────────────────────
        ('매수완료',
         re.compile(TS + r' - \w+ - .*매수완료.* (\d{6}) .*([\d,]+)원'),
         lambda m: {
             'type': 'EXEC', 'event': 'BUY', 'symbol': m.group(2),
             'params': f'price:{m.group(3)}원', 'result': 'EXEC',
             'resultClass': 'exec', 'time': m.group(1),
         }),
        ('매도완료',
         re.compile(TS + r' - \w+ - .*매도완료.* (\d{6}) .*([\d,]+)원'),
         lambda m: {
             'type': 'EXEC', 'event': 'SELL', 'symbol': m.group(2),
             'params': f'price:{m.group(3)}원', 'result': 'EXEC',
             'resultClass': 'exec', 'time': m.group(1),
         }),
        # ── System Events ────────────────────────────────────────────────────
        ('[SYSTEM_START]',
         re.compile(TS + r' - \w+ - \[SYSTEM_START\] (.+)'),
         lambda m: {
             'type': 'SYSTEM', 'event': 'START', 'symbol': '——',
             'params': m.group(2)[:80], 'result': 'INIT',
             'resultClass': 'info', 'time': m.group(1),
         }),
        ('[LOOP_BLOCKED]',
         re.compile(TS + r' - \w+ - \[LOOP_BLOCKED\] (.+)'),
         lambda m: {
             'type': 'SYSTEM', 'event': 'LOOP_LAG', 'symbol': '——',
             'params': m.group(2)[:60], 'result': 'WARN',
             'resultClass': 'info', 'time': m.group(1),
         }),
        ('[CAPITAL_SNAPSHOT]',
         re.compile(TS + r' - \w+ - \[CAPITAL_SNAPSHOT\] (.+)'),
         lambda m: {
             'type': 'SYSTEM', 'event': 'SNAPSHOT', 'symbol': '——',
             'params': m.group(2)[:80], 'result': 'OK',
             'resultClass': 'info', 'time': m.group(1),
         }),
        ('[TRADING_HALT]',
         re.compile(TS + r' - \w+ - \[TRADING_HALT\] (.+)'),
         lambda m: {
             'type': 'SYSTEM', 'event': 'HALT', 'symbol': '——',
             'params': m.group(2)[:60], 'result': 'HALT',
             'resultClass': 'block', 'time': m.group(1),
         }),
    ]

    # Collect matched lines per pattern (keep last 20 each → max ~200 events)
    per_pattern: list[list[dict]] = [[] for _ in patterns]
    seen_ids: set = set()

    with open(log_path, encoding='utf-8', errors='replace') as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line:
                continue
            for idx, (keyword, pat, builder) in enumerate(patterns):
                if keyword not in line:
                    continue
                m = pat.match(line)
                if m:
                    uid = f"{m.group(1)}_{line[:80]}"
                    if uid not in seen_ids:
                        seen_ids.add(uid)
                        ev = builder(m)
                        ev['id'] = f'log_{idx}_{hash(line) & 0xffff}'
                        ev['fnRef'] = 'main_auto_trading.py'
                        per_pattern[idx].append(ev)
                    break

    # Take last 15 per pattern to keep diversity
    events = [ev for bucket in per_pattern for ev in bucket[-15:]]

    # Merge: main log events + SMC decision log + orchestrator ACCEPT/REJECT
    smc_events = _parse_smc_decision_log()
    orch_events = _parse_orchestrator_log()
    all_events = sorted(events + smc_events + orch_events, key=lambda e: e['time'])

    return list(reversed(all_events[-80:]))  # newest first


def _parse_orchestrator_log() -> list[dict]:
    """Parse signal_orchestrator.log for ACCEPT/REJECT events (8:30–16:00 coverage).

    Uses the most recent day that has orchestrator events (falls back up to 7 days),
    so the feed stays populated before today's trading session starts.
    """
    orch_path = LOGS_DIR / 'signal_orchestrator.log'
    if not orch_path.exists():
        return []

    # Build stock_code → name lookup from watchlist
    _wl = read_json(WATCHLIST_PATH)
    _name_map: dict[str, str] = {}
    if isinstance(_wl, list):
        for _w in _wl:
            _code = _w.get('stock_code', '')
            _name = _w.get('stock_name', '')
            if _code and _name:
                _name_map[_code] = _name

    # Find the most recent date in the orchestrator log (read tail, then verify it's ≤7 days old)
    target_date: str | None = None
    cutoff = (date.today() - timedelta(days=7)).strftime('%Y-%m-%d')
    with open(orch_path, encoding='utf-8', errors='replace') as f:
        # Seek near end to find the last non-empty line efficiently
        try:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 4096))
        except OSError:
            pass
        tail = f.read()
    for raw in reversed(tail.splitlines()):
        raw = raw.strip()
        if len(raw) >= 10 and raw[:10] >= cutoff:
            target_date = raw[:10]
            break

    if not target_date:
        return []

    today = target_date
    TS = r'\d{4}-\d{2}-\d{2} (\d{2}:\d{2}:\d{2}),\d+'

    # Supports both old format (no PID) and new format (with PID)
    # Old: ✅ ACCEPT 009420 @45750원 | conf=0.49 alpha=+1.55 pos_mult=0.40
    # New: ✅ ACCEPT 056360 @12410원 | PID:2379128 | conf=0.52 alpha=+1.65 pos_mult=0.62
    pat_accept = re.compile(
        TS + r' - \w+ - .*✅ ACCEPT (\d{6}) @([\d,]+)원 \| (?:PID:\d+ \| )?conf=([\d.]+) alpha=([+\-\d.]+)'
    )
    # REJECT: ❌ REJECT 218410 | PID:2379128 | L0 | 진입 시간 외 (09:17, 10:00 이전)
    # Also without PID: ❌ REJECT 218410 | L0 | 진입 시간 외 ...
    pat_reject = re.compile(
        TS + r' - \w+ - .*❌ REJECT (\d{6}) \| (?:PID:\d+ \| )?(.+)'
    )

    accept_events: list[dict] = []
    reject_events: list[dict] = []
    seen: set = set()

    with open(orch_path, encoding='utf-8', errors='replace') as f:
        for raw_line in f:
            if not raw_line.startswith(today):
                continue
            line = raw_line.strip()
            if 'ACCEPT' in line:
                m = pat_accept.match(line)
                if m:
                    uid = f"{m.group(1)}_{m.group(2)}"
                    if uid not in seen:
                        seen.add(uid)
                        _sym_a = m.group(2)
                        accept_events.append({
                            'type': 'FILTER', 'event': 'ACCEPT', 'symbol': _sym_a,
                            'symbolName': _name_map.get(_sym_a, ''),
                            'params': f'price:{m.group(3)}원  conf:{m.group(4)}  alpha:{m.group(5)}',
                            'result': 'PASS', 'resultClass': 'pass', 'time': m.group(1),
                            'id': f'orch_accept_{hash(line) & 0xffff}',
                            'fnRef': 'signal_orchestrator',
                        })
            elif 'REJECT' in line:
                m = pat_reject.match(line)
                if m:
                    uid = f"{m.group(1)}_{m.group(2)}"
                    if uid not in seen:
                        seen.add(uid)
                        _sym_r = m.group(2)
                        reject_events.append({
                            'type': 'FILTER', 'event': 'REJECT', 'symbol': _sym_r,
                            'symbolName': _name_map.get(_sym_r, ''),
                            'params': m.group(3)[:60],
                            'result': 'REJECT', 'resultClass': 'reject', 'time': m.group(1),
                            'id': f'orch_reject_{hash(line) & 0xffff}',
                            'fnRef': 'signal_orchestrator',
                        })

    # Keep last 30 of each type → spread across full trading day
    return accept_events[-30:] + reject_events[-30:]


# ─── Performance from trades ─────────────────────────────────────────────────

def compute_performance() -> dict:
    try:
        conn = _get_pg_conn()
        cur  = conn.cursor()
        today      = today_iso()
        week_start = (date.today() - timedelta(days=7)).isoformat()
        month_start= (date.today() - timedelta(days=30)).isoformat()

        def fetch(since: str) -> list:
            cur.execute(
                "SELECT realized_profit FROM trades WHERE trade_time::date >= %s AND trade_type='SELL'",
                (since,)
            )
            return cur.fetchall()

        def summarize(rows: list) -> dict:
            pnls = [float(r[0]) for r in rows if r[0] is not None]
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

        # Strategy breakdown
        cur.execute(
            "SELECT trade_id, stock_code, trade_type, realized_profit, strategy_name FROM trades ORDER BY trade_id"
        )
        all_rows = cur.fetchall()
        conn.close()

        from collections import defaultdict
        import statistics
        last_buy_strat: dict[str, str] = {}
        strat_map: dict = defaultdict(list)
        for _, code, ttype, pnl, strat in all_rows:
            if ttype == 'BUY' and strat and strat != 'EXIT':
                last_buy_strat[code] = strat
            elif ttype == 'SELL' and pnl is not None:
                entry_strat = last_buy_strat.get(code, strat or 'SMC')
                strat_map[entry_strat].append(float(pnl))

        strategies = []
        for strat_key, pnls in sorted(strat_map.items()):
            wins = [p for p in pnls if p > 0]
            avg_ret = round(sum(pnls) / len(pnls) / 5_000_000 * 100, 2) if pnls else 0
            win_rate = round(len(wins) / len(pnls) * 100) if pnls else 0
            std = statistics.stdev(pnls) if len(pnls) >= 2 else 0.0
            sharpe = round(abs(sum(pnls) / len(pnls)) / std, 2) if std > 0 else 0.0
            strategies.append({
                'code': (strat_key or 'SMC')[:6],
                'winRate': win_rate,
                'avgReturn': avg_ret,
                'sharpe': sharpe,
                'trades': len(pnls),
            })

        return {'today': perf_today, 'week': perf_week, 'month': perf_month, 'strategies': strategies}
    except Exception as e:
        logger.warning(f'[PERF] PostgreSQL 조회 실패: {e}')
        return _empty_perf()


def _empty_perf() -> dict:
    z = {'pnl': 0, 'pct': 0.0, 'trades': 0, 'winRate': 0}
    return {'today': z, 'week': z, 'month': z}


# ─── Candidates from watchlist + live prices ─────────────────────────────────

DAILY_WATCHLIST_PATH = BASE / 'data' / 'daily_watchlist.json'


async def build_candidates() -> list[dict]:
    # ① monitoring_watchlist.json — main_auto_trading.py가 매 루프마다 저장하는 실시간 감시 목록
    watchlist: list[dict] = []
    mon = read_json(MONITORING_WATCHLIST_PATH)
    if isinstance(mon, dict) and mon.get('symbols'):
        for item in mon['symbols']:
            if isinstance(item, dict) and item.get('stock_code'):
                watchlist.append({
                    'stock_code': item['stock_code'],
                    'stock_name': item.get('stock_name', item['stock_code']),
                })

    # ② fallback: watchlist.json (validated_stocks 스냅샷)
    if not watchlist:
        raw = read_json(WATCHLIST_PATH)
        if isinstance(raw, list):
            watchlist = raw

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

    tasks = [enrich(item) for item in watchlist]
    results = await asyncio.gather(*tasks)
    candidates = [r for r in results if r is not None]
    # 진입스코어 내림차순 정렬 → 상위 종목이 항상 Top 3에 표시
    # 실거래 AI점수 × 승률 기반 정렬 (yfinance 임시점수보다 신뢰도 높음)
    wl_map = {item['stock_code']: item for item in watchlist}
    def rank_score(c: dict) -> float:
        w      = wl_map.get(c['symbol'], {})
        ai     = w.get('ai_score', 0) or 0
        wr     = (w.get('win_rate', 0) or 0) / 100
        trades = w.get('total_trades', 0) or 0
        if ai > 0 and wr > 0:
            reliability = min(trades / 5.0, 1.0)   # 5건 미만 신뢰도 패널티
            return ai * wr * reliability
        return c['entryScore'] * 0.1               # 실거래 데이터 없으면 후순위
    candidates.sort(key=rank_score, reverse=True)
    return candidates


# ─── Positions ────────────────────────────────────────────────────────────────

def _is_ghost_position(code: str, entry_date_str: str) -> bool:
    """DB에 해당 종목의 SELL 기록이 진입일 이후에 있으면 유령 포지션으로 판정."""
    if not entry_date_str:
        return False
    try:
        entry_ts = entry_date_str[:19]   # 'YYYY-MM-DDTHH:MM:SS'
        conn = _get_pg_conn()
        cur  = conn.cursor()
        cur.execute(
            "SELECT trade_id FROM trades WHERE stock_code=%s AND trade_type='SELL' AND trade_time >= %s LIMIT 1",
            (code, entry_ts),
        )
        row = cur.fetchone()
        conn.close()
        return row is not None
    except Exception:
        return False


def build_positions() -> list[dict]:
    """
    포지션 목록.
    ① Kiwoom API (kt00004) 실시간 조회 (30s 캐시)
    ② 실패 시: positions_state.json + 유령 포지션 필터 폴백
    positions_state의 SL/TP/전략 메타는 API 결과에 보완.
    """
    # ── ① Kiwoom API ──────────────────────────────────────────────────────────
    kiwoom_pos = fetch_kiwoom_positions()
    if kiwoom_pos:
        # positions_state 메타 (SL, TP, strategy, entry_date, choch_grade 등) 보완
        state_raw: dict = read_json(POSITIONS_PATH)
        result = []
        for kp in kiwoom_pos:
            code    = kp['symbol']
            entry   = kp['entry_price']
            current = kp['current_price']
            qty     = kp['qty']
            pnl     = kp['eval_profit']
            pnl_pct = kp['profit_rate']

            # positions_state에서 추가 메타 읽기
            meta = state_raw.get(code, {}) if isinstance(state_raw, dict) else {}
            trailing_stop = meta.get('trailing_stop_price')
            stop_loss     = meta.get('stop_loss_price')
            sl = (
                round(float(trailing_stop)) if trailing_stop and float(trailing_stop) > 0
                else round(float(stop_loss)) if stop_loss and float(stop_loss) > 0
                else round(entry * 0.97)
            )
            tp_raw           = meta.get('take_profit_price')
            trailing_active  = bool(meta.get('trailing_active', False))
            highest          = float(meta.get('highest_price') or current)
            if tp_raw and float(tp_raw) > 0:
                tp = round(float(tp_raw))
            elif trailing_active:
                tp = round(highest * 1.02)
            else:
                static_tp = round(entry * 1.05)
                tp = static_tp if static_tp > current else round(current * 1.03)

            result.append({
                'symbol':         code,
                'name':           kp['name'],
                'entryPrice':     entry,
                'currentPrice':   current,
                'quantity':       qty,
                'sl':             sl,
                'tp':             tp,
                'pnl':            round(pnl),
                'pnlPct':         round(pnl_pct, 2),
                'strategy':       meta.get('strategy') or meta.get('entry_reason', 'SMC'),
                'holdMinutes':    _calc_hold_minutes(meta.get('entry_date', '')),
                'chochGrade':     str(meta.get('choch_grade') or 'B'),
                'trailingActive': trailing_active,
                'highestPrice':   highest,
                'source':         'kiwoom_api',
            })
        return result

    # ── ② 폴백: positions_state.json + 유령 필터 ──────────────────────────────
    logger.warning('[POSITIONS] Kiwoom API 실패 — 파일 폴백')
    raw: dict = read_json(POSITIONS_PATH)
    result = []
    for code, pos in raw.items():
        if not isinstance(pos, dict):
            continue
        entry   = float(pos.get('entry_price') or pos.get('avg_price') or 0)
        current = float(pos.get('current_price') or entry)
        qty     = int(pos.get('quantity') or 0)
        if qty <= 0:
            continue
        if _is_ghost_position(code, pos.get('entry_date', '')):
            logger.info(f'[GHOST_POS] {code} — DB SELL 기록 있음, 표시 제외')
            continue
        pnl     = (current - entry) * qty
        pnl_pct = (current / entry - 1) * 100 if entry else 0
        trailing_stop = pos.get('trailing_stop_price')
        stop_loss     = pos.get('stop_loss_price')
        sl = (
            round(float(trailing_stop)) if trailing_stop and float(trailing_stop) > 0
            else round(float(stop_loss)) if stop_loss and float(stop_loss) > 0
            else round(entry * 0.97)
        )
        tp_raw           = pos.get('take_profit_price')
        trailing_active  = bool(pos.get('trailing_active', False))
        highest          = float(pos.get('highest_price') or current)
        if tp_raw and float(tp_raw) > 0:
            tp = round(float(tp_raw))
        elif trailing_active:
            tp = round(highest * 1.02)
        else:
            static_tp = round(entry * 1.05)
            tp = static_tp if static_tp > current else round(current * 1.03)
        result.append({
            'symbol':         code,
            'name':           pos.get('name') or pos.get('stock_name', code),
            'entryPrice':     entry,
            'currentPrice':   current,
            'quantity':       qty,
            'sl':             sl,
            'tp':             tp,
            'pnl':            round(pnl),
            'pnlPct':         round(pnl_pct, 2),
            'strategy':       pos.get('strategy') or pos.get('entry_reason', 'SMC'),
            'holdMinutes':    _calc_hold_minutes(pos.get('entry_date', '')),
            'chochGrade':     str(pos.get('choch_grade') or 'B'),
            'trailingActive': trailing_active,
            'highestPrice':   highest,
            'source':         'file',
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

def build_trades(limit: int = 50, days: int = 7,
                 from_date: str = None, to_date: str = None) -> list[dict]:
    """
    from_date~to_date 범위 거래 반환. 미지정 시 최근 days일.
    limit은 최종 건수 상한.
    """
    try:
        conn = _get_pg_conn()
        cur  = conn.cursor()
        start = from_date or (date.today() - timedelta(days=days)).isoformat()
        end   = to_date   or today_iso()
        cur.execute(
            """SELECT trade_id,
                      trade_time::date,
                      trade_time,
                      stock_code, stock_name, trade_type,
                      quantity, price, realized_profit,
                      COALESCE(exit_reason, entry_reason, ''),
                      COALESCE(strategy_name, 'SMC')
               FROM trades
               WHERE trade_time::date >= %s AND trade_time::date <= %s
               ORDER BY trade_id ASC""",
            (start, end),
        )
        all_rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f'[BUILD_TRADES] PostgreSQL 조회 실패: {e}')
        return []

    raw = []
    for r in all_rows:
        ts_val = r[2].isoformat() if r[2] else ''
        raw.append({
            'id': r[0], 'date': str(r[1]), 'ts': ts_val, 'code': r[3], 'name': r[4],
            'type': r[5], 'qty': r[6], 'price': float(r[7] or 0), 'pnl': float(r[8] or 0),
            'reason': r[9] or '', 'strategy': r[10] or 'SMC',
        })

    # Match each SELL to the latest BUY of the same stock
    # BUY가 없는 경우: positions_state에서 entry_price 보완
    positions_raw: dict = read_json(POSITIONS_PATH)
    last_buy: dict[str, dict] = {}
    pairs: list[tuple] = []
    for t in raw:
        if t['type'] == 'BUY':
            last_buy[t['code']] = t
        elif t['type'] == 'SELL':
            buy = last_buy.get(t['code'])
            # BUY 없으면 positions_state entry_price로 보완
            if buy is None and t['code'] in positions_raw:
                pos = positions_raw[t['code']]
                ep = float(pos.get('entry_price') or pos.get('avg_price') or 0)
                if ep > 0:
                    buy = {'price': ep, 'strategy': pos.get('entry_reason', 'SMC'), 'ts': pos.get('entry_date', ''), 'qty': t['qty']}
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

    # Also count today's trades from PostgreSQL as proxy for passed signals
    exec_today = 0
    try:
        conn = _get_pg_conn()
        cur  = conn.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM trades WHERE trade_time::date=%s AND trade_type='BUY'", (today_iso(),)
        )
        row = cur.fetchone()
        conn.close()
        exec_today = row[0] if row else 0
    except Exception:
        pass

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
def api_trades(days: int = 7, limit: int = 50,
               from_date: str = None, to_date: str = None):
    """
    거래 목록.
    ?from_date=2026-04-01&to_date=2026-04-24  → 날짜 범위
    ?days=7  → 최근 N일 (from_date 미지정 시)
    """
    start = from_date or (date.today() - timedelta(days=days)).isoformat()
    end   = to_date   or today_iso()
    trades = build_trades(limit=limit, from_date=start, to_date=end)
    actual_days = (date.fromisoformat(end) - date.fromisoformat(start)).days + 1
    return {
        'period': {
            'days':  actual_days,
            'from':  start,
            'to':    end,
            'label': f'{start} ~ {end}',
        },
        'total':  len(trades),
        'trades': trades,
    }


def _compute_risk_metrics() -> list[dict]:
    """Compute risk metrics from PostgreSQL trades."""
    try:
        conn = _get_pg_conn()
        cur  = conn.cursor()
        cur.execute(
            "SELECT realized_profit FROM trades WHERE trade_type='SELL' AND realized_profit IS NOT NULL ORDER BY trade_id"
        )
        rows = cur.fetchall()
        conn.close()
    except Exception as e:
        logger.warning(f'[RISK_METRICS] PostgreSQL 조회 실패: {e}')
        return []
    # consecutive losses
    risk_row = {}
    try:
        import json as _json
        rlog = BASE / 'data' / 'risk_log.json'
        if rlog.exists():
            risk_row = _json.loads(rlog.read_text())
    except Exception:
        pass

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


@app.post('/api/refresh')
def api_refresh():
    """거래 발생 시: 잔고·포지션 캐시 무효화."""
    _invalidate_cache('balance', 'positions')
    balance   = fetch_kiwoom_balance()
    positions = fetch_kiwoom_positions()
    return {
        'refreshed': ['balance', 'positions', 'trades'],
        'deposit':       balance.get('deposit', 0),
        'holdingValue':  balance.get('holding_value', 0),
        'totalAssets':   balance.get('total_assets', 0),
        'positionCount': len(positions),
    }


ACCOUNT_SNAPSHOT_PATH = BASE / 'data' / 'account_snapshot.json'
_SNAPSHOT_TTL_SECONDS = 300   # 5분 초과 시 stale 처리


def _today_realized_pnl_from_db() -> int:
    """오늘 실현 손익 합계 (SELL trades, PostgreSQL)."""
    try:
        conn = _get_pg_conn()
        cur  = conn.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(realized_profit), 0) FROM trades WHERE trade_type='SELL' AND trade_time::date=%s",
            (today_iso(),),
        )
        row = cur.fetchone()
        conn.close()
        return int(float(row[0] or 0))
    except Exception:
        return 0


@app.get('/api/account')
def api_account():
    """Return account summary: 예수금, 보유평가, 총자산, 일/주간 손익."""
    # ── 1. risk_log: 주간손익 / 연패 ─────────────────────────────────────────
    rlog_path = BASE / 'data' / 'risk_log.json'
    weekly_pnl = consecutive_losses = 0
    if rlog_path.exists():
        try:
            rlog = json.loads(rlog_path.read_text(encoding='utf-8'))
            weekly_pnl         = int(rlog.get('weekly_realized_pnl', 0) or 0)
            consecutive_losses = int(rlog.get('consecutive_losses', 0) or 0)
        except Exception:
            pass

    # ── 2. DB 스냅샷 조회 (1순위) ────────────────────────────────────────────
    deposit = holding_value = total_assets = 0
    daily_pnl = 0
    snapshot_age = None
    data_source  = 'estimate'

    kbal = fetch_kiwoom_balance()   # DB 우선, 없으면 Kiwoom API
    if kbal and kbal.get('total_assets', 0) > 0:
        deposit       = kbal['deposit']
        holding_value = kbal['holding_value']
        total_assets  = kbal['total_assets']
        daily_pnl     = kbal.get('eval_profit', 0) + _today_realized_pnl_from_db()
        data_source   = 'db_snapshot' if kbal.get('_from_db') else 'kiwoom_api'
        snapshot_age  = 0

    # ── 3. Fallback: account_snapshot.json ──────────────────────────────────
    if data_source == 'estimate' and ACCOUNT_SNAPSHOT_PATH_K.exists():
        try:
            snap = json.loads(ACCOUNT_SNAPSHOT_PATH_K.read_text(encoding='utf-8'))
            updated_at = snap.get('updated_at')
            if updated_at:
                age = (datetime.now() - datetime.fromisoformat(updated_at)).total_seconds()
                snapshot_age = int(age)
                if age < _SNAPSHOT_TTL_SECONDS:
                    deposit       = int(snap.get('deposit', 0) or 0)
                    holding_value = int(snap.get('holding_value', 0) or 0)
                    total_assets  = int(snap.get('total_assets', 0) or 0)
                    data_source   = 'snapshot'
        except Exception:
            pass

    # ── 4. 최후 폴백: 로그 파싱 추정 ────────────────────────────────────────
    if data_source == 'estimate':
        active_positions = build_positions()
        for pos in active_positions:
            holding_value += int(pos.get('entryPrice', 0) * pos.get('quantity', 0))
        pat_snap = re.compile(r'\[CAPITAL_SNAPSHOT\] .+총자산=([\d,]+)')
        for lp in sorted(LOGS_DIR.glob('auto_trading_????????.log'), reverse=True)[:3]:
            last_m = None
            try:
                for line in open(lp, encoding='utf-8', errors='replace'):
                    if '[CAPITAL_SNAPSHOT]' in line:
                        m = pat_snap.search(line)
                        if m:
                            last_m = m
            except Exception:
                continue
            if last_m:
                total_assets = int(last_m.group(1).replace(',', ''))
                break
        if holding_value > total_assets:
            total_assets = holding_value
        deposit = max(0, total_assets - holding_value)

    return {
        'deposit':           deposit,
        'holdingValue':      holding_value,
        'totalAssets':       total_assets,
        'dailyPnl':          daily_pnl,
        'weeklyPnl':         weekly_pnl,
        'consecutiveLosses': consecutive_losses,
        'dataSource':        data_source,       # 'kiwoom_api' | 'estimate'
        'snapshotAge':       snapshot_age,      # 초 (None = 스냅샷 없음)
    }


# ─── Optimize (Grid Search) ───────────────────────────────────────────────────

import uuid
import threading
from pydantic import BaseModel

# Job store: {job_id: {status, progress, total, current_tp, current_sl, results, error}}
_optimize_jobs: dict[str, dict] = {}


class OptimizeRequest(BaseModel):
    strategy:         str         = 'B+VOL'
    start:            str         = '2022-01-01'
    end:              str         = '2024-12-31'
    mode:             str         = 'tp_sl'   # 'tp_sl' | 'swing'
    tp_range:         list[float] = [0.02, 0.03, 0.04, 0.05]
    sl_range:         list[float] = [0.01, 0.015, 0.02, 0.025, 0.03]
    min_hold_range:   list[int]   = []
    trailing_range:   list[float] = []
    be_trigger_range: list[float] = []
    symbols:          list[str] | None = None


def _run_optimize_job(job_id: str, req: OptimizeRequest):
    job = _optimize_jobs[job_id]
    try:
        sys.path.insert(0, str(BASE))
        from backtest.runner import run_grid_search

        # 백테스트 관련 logger 전부 억제
        import logging as _log
        for _name in ('backtest', 'yfinance', 'peewee', 'urllib3'):
            _log.getLogger(_name).setLevel(_log.CRITICAL)

        def on_progress(done, total, p1, p2, row):
            if row is None:
                job.update({'status': 'running', 'message': '데이터 로드 완료, 백테스트 시작...'})
            else:
                if req.mode == 'swing':
                    msg = f'hold={p1}봉 / trail={p2*100:.0f}%'
                else:
                    msg = f'TP {p1*100:.1f}% / SL {p2*100:.1f}%'
                job.update({
                    'progress': done,
                    'total': total,
                    'message': msg,
                })
                job['results'].append(row)

        job.update({'status': 'running', 'message': '데이터 다운로드 중...'})
        run_grid_search(
            symbols          = req.symbols,
            start            = req.start,
            end              = req.end,
            tp_range         = req.tp_range or None,
            sl_range         = req.sl_range or None,
            min_hold_range   = req.min_hold_range or None,
            trailing_range   = req.trailing_range or None,
            be_trigger_range = req.be_trigger_range or None,
            strategy         = req.strategy,
            mode             = req.mode,
            on_progress      = on_progress,
        )
        job['status'] = 'done'
        job['message'] = '완료'
    except Exception as e:
        job['status'] = 'error'
        job['error'] = str(e)


@app.post('/api/optimize/run')
def api_optimize_run(req: OptimizeRequest):
    job_id = str(uuid.uuid4())[:8]
    if req.mode == 'swing':
        _tp = req.tp_range or [0.08, 0.12, 0.18, 0.25]
        _mh = req.min_hold_range or [8, 24, 48]
        _tr = req.trailing_range or [0.05, 0.08, 0.12]
        _be = req.be_trigger_range or [0.03, 0.05]
        _job_total = len(_tp) * len(_mh) * len(_tr) * len(_be)
    else:
        _job_total = len(req.tp_range) * len(req.sl_range)
    _optimize_jobs[job_id] = {
        'status': 'queued',
        'progress': 0,
        'total': _job_total,
        'message': '대기 중...',
        'results': [],
        'error': None,
        'strategy': req.strategy,
        'start': req.start,
        'end': req.end,
    }
    t = threading.Thread(target=_run_optimize_job, args=(job_id, req), daemon=True)
    t.start()
    return {'job_id': job_id}


@app.get('/api/optimize/status/{job_id}')
def api_optimize_status(job_id: str):
    job = _optimize_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='job not found')
    return {
        'job_id':     job_id,
        'status':     job['status'],
        'progress':   job['progress'],
        'total':      job['total'],
        'current_tp': job['current_tp'],
        'current_sl': job['current_sl'],
        'message':    job['message'],
        'error':      job['error'],
        'result_count': len(job['results']),
    }


@app.get('/api/optimize/results/{job_id}')
def api_optimize_results(job_id: str):
    job = _optimize_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail='job not found')

    results = sorted(job['results'], key=lambda r: r['score'], reverse=True)
    for rank, r in enumerate(results, 1):
        r['rank'] = rank

    return {
        'job_id':   job_id,
        'status':   job['status'],
        'strategy': job.get('strategy'),
        'period':   f"{job.get('start')} ~ {job.get('end')}",
        'results':  results,
        'best':     results[0] if results else None,
    }


@app.delete('/api/optimize/jobs/{job_id}')
def api_optimize_cancel(job_id: str):
    if job_id in _optimize_jobs:
        del _optimize_jobs[job_id]
    return {'ok': True}


class ApplyRequest(BaseModel):
    tp: float   # e.g. 0.05
    sl: float   # e.g. 0.01


@app.post('/api/optimize/apply')
def api_optimize_apply(req: ApplyRequest):
    """Apply TP/SL from optimization result to strategy_hybrid.yaml.

    Updates trailing.activation_pct (TP) and trailing.stop_loss_pct (SL)
    using regex replacement to preserve all comments and structure.
    """
    if not CONFIG_PATH.exists():
        raise HTTPException(status_code=404, detail='strategy_hybrid.yaml not found')

    tp_pct = round(req.tp * 100, 2)   # 0.05 → 5.0
    sl_pct = round(req.sl * 100, 2)   # 0.01 → 1.0

    text = CONFIG_PATH.read_text(encoding='utf-8')

    # Replace trailing.activation_pct
    text = re.sub(
        r'^(\s*activation_pct:\s*)[\d.]+',
        lambda m: f'{m.group(1)}{tp_pct}',
        text, count=1, flags=re.MULTILINE,
    )
    # Replace trailing.stop_loss_pct (first occurrence = base SL)
    text = re.sub(
        r'^(\s*stop_loss_pct:\s*)[\d.]+',
        lambda m: f'{m.group(1)}{sl_pct}',
        text, count=1, flags=re.MULTILINE,
    )

    CONFIG_PATH.write_text(text, encoding='utf-8')
    logger.info(f'[APPLY] TP={tp_pct}% SL={sl_pct}% written to strategy_hybrid.yaml')

    return {'ok': True, 'tp_pct': tp_pct, 'sl_pct': sl_pct}


@app.get('/api/daily-report')
def api_daily_report(date: Optional[str] = None):
    """
    스윙 트레이딩 전용 일일 리포트
    date: YYYYMMDD (기본값: 오늘)
    """
    from collections import defaultdict

    target_date = date or today_str()
    log_path = LOGS_DIR / f'auto_trading_{target_date}.log'

    if not log_path.exists():
        return {
            'date': target_date,
            'error': f'로그 없음: auto_trading_{target_date}.log',
            'trades': [], 'summary': {}, 'swing': {}, 'mfemae': {},
            'early_exit': {}, 'axis2': {}, 'axis3': {}, 'axis4': [],
        }

    lines = log_path.read_text(encoding='utf-8', errors='ignore').splitlines()

    # ── 파싱 (mfe/mae/overnight 필드 선택적 파싱 — 하위 호환) ────────────────
    import re as _re
    _RE = _re.compile(
        r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[TRADE_RESULT\] '
        r'(\w+) \| pnl=([+-]?\d+\.\d+)% \| reason_tag=([^|]+?) \| '
        r'eq=(\S+) \| choch=(\S+) \| hold=(\d+)m'
        r'(?:\s*\|\s*mfe=([+-]?\d+\.\d+))?'
        r'(?:\s*\|\s*mae=([+-]?\d+\.\d+))?'
        r'(?:\s*\|\s*overnight=(\d))?'
        r'\s*\|\s*r_pct=(\S+)'
    )
    trades = []
    for ln in lines:
        m = _RE.search(ln)
        if not m:
            continue
        ts, code, pnl, tag, eq, choch, hold, mfe, mae, overnight, r_pct = m.groups()
        try:
            trades.append({
                'ts': ts, 'code': code, 'pnl': float(pnl), 'tag': tag,
                'eq': eq, 'choch': choch, 'hold_min': int(hold),
                'mfe':       float(mfe)       if mfe is not None       else None,
                'mae':       float(mae)       if mae is not None       else None,
                'overnight': bool(int(overnight)) if overnight is not None else False,
                'r_pct':     float(r_pct)     if r_pct not in ('-', '') else 0.0,
            })
        except Exception:
            pass

    # 거래세 + 수수료: 매수 0.015% + 매도 0.015% + 거래세 0.18% = 0.21% 왕복
    TRANSACTION_COST_PCT = 0.21
    for t in trades:
        t['net_pnl'] = round(t['pnl'] - TRANSACTION_COST_PCT, 2)

    total   = len(trades)
    winners = [t for t in trades if t['pnl'] > 0]
    wr      = round(len(winners) / total * 100, 1) if total else 0
    avg_pnl = round(sum(t['pnl'] for t in trades) / total, 2) if total else 0
    avg_net_pnl = round(sum(t['net_pnl'] for t in trades) / total, 2) if total else 0

    # ── 보조 함수 ────────────────────────────────────────────────────────────────
    BUCKET_MAP = {
        'R_TP2': 'TP2', 'R_TP1': 'TP1', 'BE_STOP': 'BE_STOP',
        'TRAILING': 'TRAILING', 'PROFIT_LOCK': 'PROFIT_LOCK',
        'STRUCTURE': 'STRUCT_STOP', 'HARD_STOP': 'HARD_STOP',
        'EMERGENCY': 'HARD_STOP', 'NO_PROGRESS': 'NO_PROGRESS',
        'A_FORCE': 'A_FORCE', 'Early Failure': 'EF', 'EOD': 'EOD',
    }

    def _bucket(tag: str) -> str:
        for k, v in BUCKET_MAP.items():
            if k in tag:
                return v
        return '기타'

    def _grade(t: dict) -> str:
        g = t['choch']
        return g if g not in ('-', '', None) else '미분류'

    def hold_h(t: dict) -> float:
        return t['hold_min'] / 60.0

    # ── 스윙 보유 시간 분석 ─────────────────────────────────────────────────────
    short_hold     = [t for t in trades if hold_h(t) < 1]
    mid_hold       = [t for t in trades if 1 <= hold_h(t) < 8]
    long_hold      = [t for t in trades if hold_h(t) >= 8]
    overnight_list = [t for t in trades if t.get('overnight')]
    avg_hold_h     = round(sum(hold_h(t) for t in trades) / total, 1) if total else 0

    def _wr(lst):
        return round(sum(1 for t in lst if t['pnl'] > 0) / len(lst) * 100, 1) if lst else None

    def _avg(lst):
        return round(sum(t['pnl'] for t in lst) / len(lst), 2) if lst else None

    swing_metrics = {
        'avg_hold_h':      avg_hold_h,
        'overnight_count': len(overnight_list),
        'hold_dist': {
            'short': len(short_hold),   # < 1h: 단타형 청산 (스윙 실패)
            'mid':   len(mid_hold),     # 1–8h: 반나절
            'long':  len(long_hold),    # 8h+: 진정한 스윙
        },
        'short_win_rate': _wr(short_hold),
        'mid_win_rate':   _wr(mid_hold),
        'long_win_rate':  _wr(long_hold),
        'short_avg_pnl':  _avg(short_hold),
        'mid_avg_pnl':    _avg(mid_hold),
        'long_avg_pnl':   _avg(long_hold),
    }

    # ── MFE / MAE 분석 ─────────────────────────────────────────────────────────
    mfe_trades      = [t for t in trades if t.get('mfe') is not None]
    avg_mfe         = round(sum(t['mfe']       for t in mfe_trades) / len(mfe_trades), 2) if mfe_trades else None
    avg_mae         = round(sum(abs(t['mae'])   for t in mfe_trades) / len(mfe_trades), 2) if mfe_trades else None
    entry_ok_list   = [t for t in mfe_trades if t['mfe'] >= 1.0]       # 방향 맞았음
    held_wrong_list = [t for t in entry_ok_list if t['pnl'] <= 0]      # 갔다가 손절
    no_demand_list  = [t for t in mfe_trades if t['mfe'] < 0.5 and t['pnl'] < 0]  # 방향 자체 불발

    capture_values = [
        min(max(t['pnl'] / t['mfe'], 0), 1)
        for t in entry_ok_list if t['mfe'] > 0
    ]
    avg_capture_rate = round(sum(capture_values) / len(capture_values) * 100, 1) if capture_values else None

    mfemae = {
        'sample':           len(mfe_trades),
        'avg_mfe':          avg_mfe,
        'avg_mae':          avg_mae,
        'entry_ok_count':   len(entry_ok_list),
        'held_wrong_count': len(held_wrong_list),
        'no_demand_count':  len(no_demand_list),
        'avg_capture_rate': avg_capture_rate,
    }

    # ── 조기 청산 분류 ──────────────────────────────────────────────────────────
    ef_count     = sum(1 for t in trades if 'EF' in _bucket(t['tag']) or 'Early Failure' in t['tag'])
    ef_pct       = round(ef_count / total * 100) if total else 0
    ef_no_follow = sum(1 for t in trades if 'no_follow' in t['tag'].lower())
    ef_no_demand = sum(1 for t in trades if 'no_demand' in t['tag'].lower())
    ef_generic   = ef_count - ef_no_follow - ef_no_demand

    # reentry_report JSON에서 EF subtype 보강
    reentry_date = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"
    reentry_path = LOGS_DIR / f'reentry_report_{reentry_date}.json'
    if reentry_path.exists():
        try:
            import json as _json
            rd = _json.loads(reentry_path.read_text())
            ef_sub = rd.get('ef_subtype_ratio', {})
            if ef_sub.get('ef_total', 0) > 0:
                ef_no_follow = ef_sub.get('ef_no_follow', 0)
                ef_no_demand = ef_sub.get('ef_no_demand', 0)
                ef_generic   = ef_sub.get('ef_unclassified', 0)
        except Exception:
            pass

    hs_trades   = [t for t in trades if 'HARD_STOP' in t['tag']]
    eod_trades  = [t for t in trades if 'EOD' in t['tag'] or '시간' in t['tag']]
    prof_trades = [t for t in trades if any(k in t['tag'] for k in ['R_TP1', 'R_TP2', 'TRAILING', 'PROFIT_LOCK'])]

    early_exit = {
        'structure_break': len(hs_trades) + sum(1 for t in trades if 'STRUCTURE' in t['tag']),
        'ef_shakeout':     ef_no_follow,   # 흔들림 — 버텼어야 했음
        'ef_no_demand':    ef_no_demand,   # 가짜 신호 — 진입 자체 문제
        'ef_generic':      ef_generic,     # 미분류 EF
        'time_exit':       len(eod_trades),
        'profit_exit':     len(prof_trades),
        'ef_total':        ef_count,
        'ef_pct':          ef_pct,
    }

    # ── Axis 2: Hard Stop ──────────────────────────────────────────────────────
    emg_deferred = sum(1 for ln in lines if '긴급손절 유예' in ln)
    emg_fired    = sum(1 for ln in lines if '[HARD_STOP_EMERGENCY]' in ln and 'candle confirmed' in ln)
    hs_avg_loss  = round(sum(t['pnl'] for t in hs_trades) / len(hs_trades), 2) if hs_trades else 0

    # ── Axis 3: A급 보호 ───────────────────────────────────────────────────────
    buf_applied = sum(1 for ln in lines if '[A_STOP_BUFFER]' in ln and 'TP1 전 손절 완화' in ln)
    buf_skipped = sum(1 for ln in lines if '[A_STOP_BUFFER_SKIP]' in ln)
    a_trades    = [t for t in trades if t['choch'] in ('A', 'A+') or t['eq'] == 'A']
    a_wr        = round(sum(1 for t in a_trades if t['pnl'] > 0) / len(a_trades) * 100, 1) if a_trades else 0
    a_avg       = round(sum(t['pnl'] for t in a_trades) / len(a_trades), 2) if a_trades else 0
    a_stops     = sum(1 for t in a_trades if 'STOP' in t['tag'])

    # ── Axis 4: 등급 × 청산유형 교차표 ───────────────────────────────────────
    cross: dict = defaultdict(lambda: defaultdict(list))
    for t in trades:
        cross[_grade(t)][_bucket(t['tag'])].append(t['pnl'])

    axis4 = []
    for grade in sorted(cross.keys()):
        for exit_type, pnls in sorted(cross[grade].items()):
            n = len(pnls)
            axis4.append({
                'grade':     grade,
                'exit_type': exit_type,
                'count':     n,
                'win_rate':  round(sum(1 for p in pnls if p > 0) / n * 100, 1) if n else 0,
                'avg_pnl':   round(sum(pnls) / n, 2) if n else 0,
            })

    # ── 진단 내러티브 (스윙 전용) ────────────────────────────────────────────────
    tp1_count = sum(1 for t in trades if 'R_TP1' in t['tag'])
    tp2_count = sum(1 for t in trades if 'R_TP2' in t['tag'])
    be_count  = sum(1 for t in trades if 'BE_STOP' in t['tag'])

    positives: list = []
    warnings:  list = []
    actions:   list = []

    # ① 샘플 크기
    if total < 3:
        verdict = f'샘플 부족 ({total}건) — 정밀 판단 보류'
        warnings.append(f'거래 {total}건은 통계적으로 유의하지 않음.')
    elif total < 7:
        verdict = f'샘플 제한 ({total}건) — 방향성 참고만'
    else:
        verdict = '정상 분석 가능'

    # ② 손실 통제
    if total > 0 and avg_pnl >= -1.5:
        positives.append(f'평균손익 {avg_pnl:+.2f}% → 손실 통제 범위 내.')
    elif total > 0 and avg_pnl < -3.0:
        warnings.append(f'평균손익 {avg_pnl:+.2f}% → 스윙 기준으로도 손실 과다. 구조손절 점검 필요.')
        actions.append('Hold 기록 없는 항목 확인 — 진입 후 즉시 반전 패턴 여부 점검.')

    # ③ 보유 시간: 스윙 운영의 핵심 지표
    if total > 0:
        short_pct = round(len(short_hold) / total * 100)
        if short_pct >= 50:
            warnings.append(
                f'보유 1시간 미만 {short_pct}% ({len(short_hold)}건) → '
                f'스윙 전략을 단타처럼 운영 중. 핵심 문제.'
            )
            actions.append('EF no_follow(흔들림) 비율 확인. 구조 붕괴 아닌 이유로 조기 청산하면 버텨야 함.')
            actions.append('EF 임계값 상향 검토 (ef_threshold 3→4) — 스윙에서 일시적 눌림은 정상.')
            if total < 5:
                verdict = '단타형 운영 패턴 (샘플 부족)'
            else:
                verdict = '단타형 운영 패턴 — 스윙 전략 위반'
        elif short_pct >= 30:
            warnings.append(f'보유 1시간 미만 {short_pct}% ({len(short_hold)}건) — 주의 필요.')
        else:
            if avg_hold_h >= 4:
                positives.append(f'평균 보유 {avg_hold_h:.1f}h → 스윙 전략 운영 정상.')

    # ④ MFE/MAE 분석
    if mfe_trades:
        if held_wrong_list:
            pct_hw = round(len(held_wrong_list) / len(entry_ok_list) * 100) if entry_ok_list else 0
            warnings.append(
                f'방향 맞고 손절 {len(held_wrong_list)}건 ({pct_hw}%) → '
                f'수익 구간에서 시간 못 버팀. 스탑 너무 좁거나 EF 과작동.'
            )
            actions.append('해당 종목 MAE 확인 — 스탑 범위 내 흔들림인지 vs 구조 붕괴인지 구분 필요.')
        if no_demand_list:
            warnings.append(f'MFE 0.5% 미만 손절 {len(no_demand_list)}건 → 진입 후 방향 자체 불발. 가짜 신호.')
        if avg_capture_rate is not None and avg_capture_rate < 40:
            warnings.append(
                f'MFE 포착률 {avg_capture_rate}% → 방향은 맞았으나 수익의 '
                f'{100 - int(avg_capture_rate)}%를 반납. 청산 타이밍 문제.'
            )
        elif avg_capture_rate is not None and avg_capture_rate >= 60:
            positives.append(f'MFE 포착률 {avg_capture_rate}% → 수익 구간 절반 이상 확보. 청산 구조 양호.')

    # ⑤ 조기 청산 분류
    if ef_count > 0:
        if ef_no_demand > ef_no_follow:
            warnings.append(
                f'EF no_demand {ef_no_demand}건 > no_follow {ef_no_follow}건 → '
                f'가짜 신호 진입 비중 높음. 진입 조건 강화 필요.'
            )
            actions.append('displacement 필터 + CHoCH B급 이상 유지. C_FALLBACK 비율 점검.')
        elif ef_no_follow >= 2:
            warnings.append(
                f'EF no_follow {ef_no_follow}건 → 타이밍은 맞았으나 보유 실패. '
                f'스탑/EF 임계값 스윙 기준으로 재조정 필요.'
            )
            actions.append('no_follow EF 줄이기 = 버티는 운영. ef_threshold 올리거나 trailing_tiers 조정.')

    # ⑥ Hard Stop
    if len(hs_trades) > 2:
        warnings.append(f'Hard Stop {len(hs_trades)}건 → 구조적 문제 또는 진입 품질 저하.')
        actions.append('Hard Stop 2회 초과 시 당일 거래 중단 정책(trading_halt_threshold) 확인.')

    # ⑦ A급 분석
    if a_trades and a_wr < 40:
        warnings.append(f'A급 승률 {a_wr}% < 40% → A급 기준 재검토 필요.')
    elif a_trades and a_wr >= 60:
        positives.append(f'A급 승률 {a_wr}% ({len(a_trades)}건) → 등급 필터 효과적으로 작동 중.')

    # ⑧ 오버나이트 의도
    if total >= 3 and len(overnight_list) == 0:
        warnings.append('오버나이트 보유 0건 → 스윙 매매 의도 미실현. allow_overnight 플래그 또는 진입 조건 확인.')

    # ── 파라미터 권고 ───────────────────────────────────────────────────────────
    recommendations = []
    if emg_deferred > 0 and emg_fired == 0:
        recommendations.append(f'긴급손절 유예 {emg_deferred}건, 즉시발동 0건 → emergency_stop_candle_confirm 재검토')
    if a_trades and a_wr < 40:
        recommendations.append(f'A급 승률 {a_wr}% < 40% → 진입 기준 재검토 필요')
    if total > 0 and len(short_hold) / total >= 0.5:
        recommendations.append('보유 <1h 비율 50%+ → EF 임계값 3→4 상향 테스트, 스윙 운영 개선')
    if mfe_trades and avg_capture_rate is not None and avg_capture_rate < 40:
        recommendations.append(f'MFE 포착률 {avg_capture_rate}% 저조 → trailing_tiers.base_mult 또는 TP2 거리 재설정')

    diagnosis = {
        'verdict':   verdict if total > 0 else '거래 없음',
        'positives': positives,
        'warnings':  warnings,
        'actions':   actions,
    }

    # ── Strategy Health Score ────────────────────────────────────────────────
    score = 50
    score_breakdown = []

    _entry_ok_pct    = mfemae['entry_ok_count']   / mfemae['sample'] * 100 if mfemae['sample'] > 0 else None
    _shakeout_pct    = early_exit['ef_shakeout']  / total * 100 if total > 0 else 0
    _no_demand_pct   = early_exit['ef_no_demand'] / total * 100 if total > 0 else 0
    _short_pct       = swing_metrics['hold_dist']['short'] / total * 100 if total > 0 else 0
    _cr              = mfemae['avg_capture_rate']

    # ① 진입 방향 정확도 (MFE ≥ 1% 비율)
    if _entry_ok_pct is not None:
        if _entry_ok_pct >= 60:
            score += 15; score_breakdown.append({'label': '방향 정확도 우수 (MFE≥1% 60%+)', 'delta': +15})
        elif _entry_ok_pct >= 40:
            score += 8;  score_breakdown.append({'label': '방향 정확도 보통', 'delta': +8})
        elif _entry_ok_pct < 20:
            score -= 15; score_breakdown.append({'label': '방향 정확도 낮음 (가짜신호)', 'delta': -15})

    # ② MFE 포착률
    if _cr is not None:
        if _cr >= 70:
            score += 15; score_breakdown.append({'label': f'MFE 포착률 {_cr}% — 우수', 'delta': +15})
        elif _cr >= 40:
            score += 7;  score_breakdown.append({'label': f'MFE 포착률 {_cr}% — 보통', 'delta': +7})
        else:
            score -= 10; score_breakdown.append({'label': f'MFE 포착률 {_cr}% — 조기청산 과다', 'delta': -10})

    # ③ 흔들림 청산 (ef_shakeout)
    if _shakeout_pct < 10:
        score += 10; score_breakdown.append({'label': '흔들림 청산 적음', 'delta': +10})
    elif _shakeout_pct >= 30:
        score -= 15; score_breakdown.append({'label': f'흔들림 청산 {round(_shakeout_pct)}% — 과다', 'delta': -15})

    # ④ 가짜 신호 (ef_no_demand)
    if _no_demand_pct < 10:
        score += 10; score_breakdown.append({'label': '가짜 신호 적음', 'delta': +10})
    elif _no_demand_pct >= 25:
        score -= 15; score_breakdown.append({'label': f'가짜 신호 {round(_no_demand_pct)}% — 과다', 'delta': -15})

    # ⑤ 단타형 청산 비율 (<1h)
    if _short_pct < 20:
        score += 10; score_breakdown.append({'label': '보유 시간 우수 (<1h 20% 미만)', 'delta': +10})
    elif _short_pct >= 50:
        score -= 15; score_breakdown.append({'label': f'단타형 청산 {round(_short_pct)}% — 스윙 위반', 'delta': -15})

    # ⑥ 승률 보너스
    if total >= 5:
        if wr >= 50:
            score += 5;  score_breakdown.append({'label': f'승률 {wr}%', 'delta': +5})
        elif wr < 35:
            score -= 5;  score_breakdown.append({'label': f'승률 {wr}% — 저조', 'delta': -5})

    # ⑦ 평균 보유 시간
    if avg_hold_h >= 4:
        score += 5;  score_breakdown.append({'label': f'평균 보유 {avg_hold_h}h — 스윙 적합', 'delta': +5})
    elif avg_hold_h < 1 and total > 0:
        score -= 10; score_breakdown.append({'label': f'평균 보유 {avg_hold_h}h — 단타 수준', 'delta': -10})

    health_score = max(0, min(100, score))
    health_level = '유지' if health_score >= 80 else '미세조정' if health_score >= 50 else '전략 문제'
    health_color = 'green'  if health_score >= 80 else 'amber'   if health_score >= 50 else 'red'

    # ── 운영 vs 전략 자동 판별 ───────────────────────────────────────────────
    _entry_ok_n  = mfemae['entry_ok_count']
    _no_dem_n    = early_exit['ef_no_demand']
    _shakeout_n  = early_exit['ef_shakeout']
    _held_wrong  = mfemae['held_wrong_count']

    if total < 3:
        ops_verdict = '샘플 부족'
        ops_type    = 'unknown'
        ops_detail  = '최소 3건 이상 거래 후 판별 가능'
    elif (_entry_ok_pct or 0) >= 40 and _held_wrong >= max(1, _entry_ok_n * 0.35):
        # 방향 맞았는데 손절 → 손절 위치 / EF 과작동
        ops_verdict = '손절 위치 문제'
        ops_type    = 'stoploss'
        ops_detail  = f'방향은 맞음 (MFE≥1% {_entry_ok_n}건). 근데 {_held_wrong}건 손절. 스탑이 너무 좁거나 EF가 스윙 흔들림을 손절로 처리 중.'
    elif (_entry_ok_pct or 0) >= 40 and _shakeout_n >= 2:
        # 방향 맞았는데 흔들림에 청산 → 운영 실패
        ops_verdict = '운영 실패'
        ops_type    = 'ops'
        ops_detail  = f'방향 정확도 {round(_entry_ok_pct or 0)}% — 신호는 살아있음. EF no_follow {_shakeout_n}건. 스탑/EF 임계값이 너무 좁아서 수익 구간에서 떨어지는 중.'
    elif _no_dem_n >= max(2, total * 0.25) and (_entry_ok_pct or 100) < 40:
        # 방향 자체가 안 나옴 → 전략(신호) 실패
        ops_verdict = '전략 실패'
        ops_type    = 'strategy'
        ops_detail  = f'no_demand {_no_dem_n}건 — 진입 후 방향 자체 불발. 신호 품질 문제. CHoCH 조건 강화 또는 displacement 필터 점검 필요.'
    elif _short_pct >= 50:
        ops_verdict = '단타형 운영'
        ops_type    = 'daytrading'
        ops_detail  = f'<1h 청산 {round(_short_pct)}%. 스윙 전략으로 진입하나 단타처럼 청산됨. EF 임계값 상향 또는 trailing 구조 재검토.'
    elif (_cr or 0) >= 60 and _short_pct < 30 and _no_dem_n <= 1:
        ops_verdict = '건강한 운영'
        ops_type    = 'healthy'
        ops_detail  = f'포착률 {_cr}%, <1h 청산 {round(_short_pct)}%. 전략과 운영 모두 정상 수준. 현재 구조 유지.'
    else:
        ops_verdict = '복합 문제'
        ops_type    = 'mixed'
        ops_detail  = '여러 원인 복합. 샘플 누적 후 재분석 권장.'

    # ── Action Confidence ───────────────────────────────────────────────────
    if total < 5:
        confidence      = 'LOW'
        confidence_note = f'{total}건 — 통계 불충분'
    elif total < 20:
        confidence      = 'MID'
        confidence_note = f'{total}건 — 방향성 참고 가능'
    else:
        confidence      = 'HIGH'
        confidence_note = f'{total}건 — 통계적으로 신뢰 가능'

    # ── Action Mapping ───────────────────────────────────────────────────────
    _ACTION_MAP: dict = {
        'stoploss': [
            {'param': 'exit_logic.structure_stop_atr_mult', 'direction': '↑', 'note': '1.0 → 1.5 — 손절 거리 확대'},
            {'param': 'ef_threshold',                       'direction': '↑', 'note': '3 → 4 — 흔들림 내성 강화'},
            {'param': 'exit_logic.hard_stop_pct',           'direction': '↑', 'note': '현재값 +0.3%p — SL 범위 확대'},
        ],
        'ops': [
            {'param': 'exit_logic.min_hold_minutes',        'direction': '↑', 'note': '→ 120 — 2h 최소 보유 강제'},
            {'param': 'ef_threshold',                       'direction': '↑', 'note': '3 → 4 — 흔들림 청산 억제'},
            {'param': 'exit_logic.be_stop_delay_minutes',   'direction': '↑', 'note': 'BE 전환 지연 설정'},
        ],
        'strategy': [
            {'param': 'smc.choch_grade.min_grade',          'direction': 'B→A', 'note': 'A급만 허용'},
            {'param': 'smc.max_c_fallback_per_day',         'direction': '↓', 'note': '2 → 0 — C_FALLBACK 차단'},
            {'param': 'smc.displacement_filter',            'direction': 'ON',  'note': 'displacement 필터 강화'},
        ],
        'daytrading': [
            {'param': 'exit_logic.min_hold_minutes',        'direction': '↑', 'note': '→ 120 이상 — 스윙 보유 강제'},
            {'param': 'ef_threshold',                       'direction': '↑', 'note': '3 → 4'},
            {'param': 'smc.smc_afternoon_cutoff',           'direction': '←', 'note': '12:30 → 11:30 — 오후 진입 차단'},
        ],
        'healthy': [
            {'param': 'risk_control.position_size_pct',     'direction': '↑', 'note': '현재값 +10~20% — 포지션 확대'},
            {'param': 'smc.max_fallback_per_day',           'direction': '↑', 'note': '3 → 4 — 기회 확대'},
        ],
        'mixed':   [],
        'unknown': [],
    }
    actions = _ACTION_MAP.get(ops_type, [])

    # ── 안전 게이트 + auto_apply 활성화 체크 ────────────────────────────────
    try:
        from core.param_tuner import ParamTuner as _PT
        _tuner        = _PT()
        _auto_enabled = _tuner.is_auto_apply_enabled()
        _safety       = _tuner.check_safety_gates(ops_type)
    except Exception:
        _auto_enabled = False
        _safety       = {
            'can_apply': True, 'blocked_by': None,
            'cooldown_remaining': 0, 'trades_since': 0, 'trades_required': 10,
            'repeat_max': 0, 'repeat_limit': 3,
            'kill_switch': False, 'kill_switch_reasons': [],
        }

    _has_patch     = bool(actions)
    can_auto_apply = _has_patch and confidence != 'LOW' and health_score < 80 and _safety['can_apply']

    health = {
        'score':              health_score,
        'level':              health_level,
        'color':              health_color,
        'breakdown':          score_breakdown,
        'ops_verdict':        ops_verdict,
        'ops_type':           ops_type,
        'ops_detail':         ops_detail,
        'confidence':         confidence,
        'confidence_note':    confidence_note,
        'actions':            actions,
        'can_auto_apply':     can_auto_apply,
        'auto_apply_enabled': _auto_enabled,
        'safety':             _safety,
    }

    # ── 일일 헬스 기록 저장 (trend용) ────────────────────────────────────────
    import json as _json
    _history_path = BASE_DIR / 'data' / 'health_history.json'
    try:
        _history: list = _json.loads(_history_path.read_text()) if _history_path.exists() else []
        # 중복 날짜 제거 후 최신 항목 추가
        _history = [h for h in _history if h.get('date') != target_date]
        _history.append({
            'date':       target_date,
            'score':      health_score,
            'level':      health_level,
            'ops_type':   ops_type,
            'total':      total,
            'wr':         wr,
            'avg_hold_h': avg_hold_h,
        })
        # 최근 30일만 유지
        _history = sorted(_history, key=lambda x: x['date'])[-30:]
        _history_path.write_text(_json.dumps(_history, ensure_ascii=False))
    except Exception:
        pass

    return {
        'date': target_date,
        'summary': {
            'total': total, 'win_rate': wr, 'avg_pnl': avg_pnl,
            'avg_net_pnl': avg_net_pnl,
            'transaction_cost_pct': TRANSACTION_COST_PCT,
            'tp1_count': tp1_count, 'tp2_count': tp2_count,
            'be_count': be_count,   'hs_count':  len(hs_trades),
        },
        'health':     health,
        'swing':      swing_metrics,
        'mfemae':     mfemae,
        'early_exit': early_exit,
        'axis2': {'deferred': emg_deferred, 'fired': emg_fired, 'hs_count': len(hs_trades), 'hs_avg_loss': hs_avg_loss},
        'axis3': {'buf_applied': buf_applied, 'buf_skipped': buf_skipped,
                  'a_count': len(a_trades), 'a_win_rate': a_wr, 'a_avg_pnl': a_avg, 'a_stops': a_stops},
        'axis4':           axis4,
        'diagnosis':       diagnosis,
        'recommendations': recommendations,
        'trades':          trades[-50:],
    }


@app.post('/api/auto-apply')
def api_auto_apply(body: dict):
    """
    Health 분석 기반 파라미터 자동 적용.
    strategy_hybrid.yaml 의 risk_control.auto_apply_enabled: true 이거나
    force=true 로 호출한 경우에만 실행.

    body: {ops_type, health_score, confidence, ops_verdict, force?}
    """
    from core.param_tuner import ParamTuner
    tuner = ParamTuner()

    force      = bool(body.get('force', False))
    enabled    = tuner.is_auto_apply_enabled()
    confidence = body.get('confidence', 'LOW')
    score      = int(body.get('health_score', 100))

    if not force and not enabled:
        return {'success': False, 'error': 'auto_apply_enabled: false — 수동 적용만 허용 (force=true 로 강제 실행)'}
    if confidence == 'LOW':
        return {'success': False, 'error': f'LOW confidence ({body.get("total", "?")}건) — 자동 적용 거부'}

    # Safety Gates 체크 (Kill Switch 는 force 로도 우회 불가)
    ops_type_in = body.get('ops_type', 'unknown')
    safety      = tuner.check_safety_gates(ops_type_in)

    # Kill Switch: recovery_eligible이 아니면 force로도 우회 불가
    if safety['kill_switch'] and not safety.get('recovery_eligible'):
        return {'success': False, 'error': f'Kill Switch 활성 — 자동 적용 전면 차단: {safety["blocked_by"]}', 'safety': safety}

    if not force and not safety['can_apply']:
        return {'success': False, 'error': safety['blocked_by'], 'safety': safety}

    # Force 남용 방지: force=True 이면 횟수 한도 체크
    if force and not safety.get('force_ok', True):
        return {'success': False, 'error': f'Force 한도 초과: {safety["force_reason"]}', 'safety': safety}

    result = tuner.apply_patch(
        ops_type     = body.get('ops_type', 'unknown'),
        health_score = score,
        ops_verdict  = body.get('ops_verdict', ''),
        confidence   = confidence,
        force        = force,
    )
    return result


@app.post('/api/rollback')
def api_rollback():
    """마지막 파라미터 변경 롤백"""
    from core.param_tuner import ParamTuner
    return ParamTuner().rollback_last()


@app.get('/api/param-changes')
def api_param_changes(limit: int = 20):
    """파라미터 변경 이력 조회"""
    from core.param_tuner import ParamTuner
    log = ParamTuner().load_change_log()
    return {'changes': list(reversed(log[-limit:]))}


@app.get('/api/before-after')
def api_before_after():
    """
    마지막 파라미터 변경 전/후 성과 비교.
    health_history.json + param_changes.json 기반.
    """
    import json as _json
    from core.param_tuner import ParamTuner

    tuner   = ParamTuner()
    cl      = tuner.load_change_log()
    history_path = BASE_DIR / 'data' / 'health_history.json'

    applied = [e for e in cl if e.get('ops_type') != 'ROLLBACK' and e.get('applied')]
    if not applied:
        return {'has_data': False, 'reason': '적용된 변경 이력 없음'}

    last_change = applied[-1]
    change_date = last_change['date']

    try:
        history: list = _json.loads(history_path.read_text()) if history_path.exists() else []
    except Exception:
        history = []

    before = [h for h in history if h['date'] < change_date]
    after  = [h for h in history if h['date'] >= change_date]

    def _avg_score(lst):
        return round(sum(x['score'] for x in lst) / len(lst), 1) if lst else None

    def _avg_wr(lst):
        valid = [x for x in lst if x.get('wr') is not None]
        return round(sum(x['wr'] for x in valid) / len(valid), 1) if valid else None

    before_score = _avg_score(before)
    after_score  = _avg_score(after)
    delta        = round(after_score - before_score, 1) if (before_score is not None and after_score is not None) else None

    verdict = (
        '개선' if delta is not None and delta > 3 else
        '악화' if delta is not None and delta < -3 else
        '유지'
    ) if delta is not None else '데이터 부족'

    return {
        'has_data':      True,
        'change_date':   change_date,
        'change':        last_change,
        'before': {
            'count':      len(before),
            'avg_score':  before_score,
            'avg_wr':     _avg_wr(before),
        },
        'after': {
            'count':      len(after),
            'avg_score':  after_score,
            'avg_wr':     _avg_wr(after),
        },
        'delta':   delta,
        'verdict': verdict,
    }


@app.get('/api/health-trend')
def api_health_trend(days: int = 7):
    """
    최근 N일 전략 건강도 추세
    """
    import json as _json
    _history_path = BASE_DIR / 'data' / 'health_history.json'
    if not _history_path.exists():
        return {'trend': [], 'avg_7d': None, 'direction': 'unknown'}

    try:
        history: list = _json.loads(_history_path.read_text())
    except Exception:
        return {'trend': [], 'avg_7d': None, 'direction': 'unknown'}

    recent = sorted(history, key=lambda x: x['date'])[-days:]
    if not recent:
        return {'trend': [], 'avg_7d': None, 'direction': 'unknown'}

    scores = [r['score'] for r in recent]
    avg    = round(sum(scores) / len(scores), 1)

    # 추세: 최근 절반 vs 이전 절반
    mid = len(scores) // 2
    if mid > 0 and len(scores) > 2:
        prev_avg  = sum(scores[:mid])  / mid
        later_avg = sum(scores[mid:])  / (len(scores) - mid)
        direction = 'up' if later_avg - prev_avg > 3 else 'down' if prev_avg - later_avg > 3 else 'flat'
    else:
        direction = 'flat'

    return {
        'trend':     recent,
        'avg_7d':    avg,
        'direction': direction,
    }


if __name__ == '__main__':
    import uvicorn
    uvicorn.run('api_server:app', host='0.0.0.0', port=8765, reload=False)
