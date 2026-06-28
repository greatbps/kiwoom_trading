"""
reject_quality.py — SMC reject 품질 분석 (false reject 추적)

ACCEPT → SMC reject 된 종목의 이후 가격 결과를 추적해
어떤 필터가 alpha를 죽이는지 데이터로 검증.

Usage:
    python3 -m analysis.reject_quality                      # 오늘
    python3 -m analysis.reject_quality --date 2026-05-14    # 특정일
    python3 -m analysis.reject_quality --days 7             # 최근 N일 누적
    python3 -m analysis.reject_quality --summary            # 누적 reason 통계
    python3 -m analysis.reject_quality --no-price           # 가격 조회 없이 구조만
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT    = Path(__file__).parent.parent
LOG_DIR = ROOT / 'logs'
OUT_DIR = LOG_DIR


# ─── 로그 파싱 ────────────────────────────────────────────────────────────────

# MKT_CTX 상태 라인에서 간략 레짐 추출
_MKT_CTX_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - INFO - \[MKT_CTX\] 상태=(\S+) \| KOSPI (\S+)'
)
_KOSPI_REGIME = {
    'HH+HL': 'BULL',
    'LH+LL': 'BEAR',
    'HH+LL': 'RANGE',
    'LH+HL': 'RANGE',
}


def _build_regime_timeline(log_date: date) -> list[tuple[datetime, str]]:
    """auto_trading 로그에서 MKT_CTX 상태 이력을 시계열로 반환."""
    path = LOG_DIR / f'auto_trading_{log_date.strftime("%Y%m%d")}.log'
    if not path.exists():
        return []
    date_str = log_date.strftime('%Y-%m-%d')
    result: list[tuple[datetime, str]] = []
    seen: set[str] = set()
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        if date_str not in line:
            continue
        m = _MKT_CTX_RE.search(line)
        if not m:
            continue
        ts_str, mkt_state, kospi_struct = m.groups()
        # 중복 제거 (같은 분 내 반복 로그)
        ts_key = ts_str[:16]
        if ts_key in seen:
            continue
        seen.add(ts_key)
        dt = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        # KOSPI 구조에서 간략 레짐 결정
        struct_key = kospi_struct.split('상승')[0].split('하락')[0].strip()
        regime = _KOSPI_REGIME.get(struct_key, 'UNKNOWN')
        if mkt_state.startswith('NO_TRADE'):
            regime = 'NO_TRADE'
        result.append((dt, regime))
    return result


def _lookup_regime(timeline: list[tuple[datetime, str]], accept_dt: datetime) -> str:
    """ACCEPT 시점에 가장 가까운(이전) MKT_CTX 레짐 반환."""
    best = 'UNKNOWN'
    for dt, regime in timeline:
        if dt <= accept_dt:
            best = regime
        else:
            break
    return best

# auto_trading_YYYYMMDD.log ACCEPT 라인
_ACCEPT_RE = re.compile(
    r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ - INFO - '
    r'✅ ACCEPT (\w+) @([\d,]+)원 \|.*?conf=([\d.]+) alpha=([+-]?[\d.]+)'
)

# smc_decision_YYYYMMDD.log NO_SIG 라인 (log_no_sig 형식)
_NO_SIG_RE = re.compile(
    r'(\d{2}:\d{2}:\d{2}) \[NO_SIG\] (\w+) \| '
    r'choch=([TF]) bos=([TF]) sweep=([TF]) \| '
    r'pf=(\S+) htf=([TF]) reclaim=([TF]) rvol=(\S+) \| '
    r'(.+)'
)


def _parse_accept(log_date: date) -> list[dict]:
    """auto_trading 로그에서 ACCEPT 레코드 추출. 종목당 첫 번째만."""
    path = LOG_DIR / f'auto_trading_{log_date.strftime("%Y%m%d")}.log'
    if not path.exists():
        return []
    date_str = log_date.strftime('%Y-%m-%d')
    timeline = _build_regime_timeline(log_date)
    seen: set[str] = set()
    rows: list[dict] = []
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        if date_str not in line:
            continue
        m = _ACCEPT_RE.search(line)
        if not m:
            continue
        ts_str, sym, price_str, conf, alpha = m.groups()
        if sym in seen:
            continue
        seen.add(sym)
        accept_dt = datetime.strptime(ts_str, '%Y-%m-%d %H:%M:%S')
        rows.append({
            'date': log_date,
            'accept_ts': ts_str,
            'symbol': sym,
            'accept_price': int(price_str.replace(',', '')),
            'conf': float(conf),
            'alpha': float(alpha),
            'regime': _lookup_regime(timeline, accept_dt),
        })
    return rows


def _parse_no_sig(log_date: date) -> dict[str, dict]:
    """smc_decision 로그에서 NO_SIG 레코드 추출. symbol → 첫 번째 레코드."""
    path = LOG_DIR / f'smc_decision_{log_date.strftime("%Y%m%d")}.log'
    if not path.exists():
        return {}
    result: dict[str, dict] = {}
    for line in path.read_text(encoding='utf-8', errors='ignore').splitlines():
        m = _NO_SIG_RE.search(line)
        if not m:
            continue
        ts, sym, choch, bos, sweep, pf, htf, reclaim, rvol_s, reason = m.groups()
        if sym in result:
            continue
        pf_parts = pf.split('/')
        result[sym] = {
            'no_sig_ts': ts,
            'choch': choch == 'T',
            'bos':   bos   == 'T',
            'sweep': sweep == 'T',
            'pf_met': int(pf_parts[0]) if pf_parts[0].isdigit() else -1,
            'pf_req': int(pf_parts[1]) if len(pf_parts) > 1 and pf_parts[1].isdigit() else -1,
            'htf':    htf    == 'T',
            'reclaim': reclaim == 'T',
            'rvol': float(rvol_s) if rvol_s not in ('?', '') else None,
            'raw_reason': reason.strip(),
            'reason': _normalize_reason(choch, bos, sweep, htf, reclaim, rvol_s, reason),
        }
    return result


def _normalize_reason(
    choch: str, bos: str, sweep: str,
    htf: str, reclaim: str, rvol_s: str, reason: str,
) -> str:
    """로그 필드 → 표준 reason enum"""
    r = reason.lower()
    # 데이터 부족
    if '부족' in reason or 'data' in r:
        return 'DATA_SHORT'
    # CHoCH 없음
    if choch == 'F' and bos == 'F':
        return 'CHOCH_MISSING'
    # BOS만 있고 CHoCH 없음 (continuation 패턴)
    if choch == 'F' and bos == 'T':
        return 'BOS_ONLY'
    # CHoCH 있지만 이하 필터에서 막힘
    if 'edt' in r:
        return 'EDT_BLOCK'
    if 'rvol' in r:
        return 'RVOL_LOW'
    if sweep == 'F':
        return 'SWEEP_MISSING'
    if 'reclaim' in r:
        return 'RECLAIM_FAIL'
    if 'htf' in r:
        return 'HTF_FAIL'
    if 'disp' in r:
        return 'DISP_BLOCK'
    if 'grade' in r:
        return 'GRADE_FAIL'
    if 'prefilter' in r:
        return 'PREFILTER_FAIL'
    return 'OTHER'


# ─── 가격 조회 (yfinance) ─────────────────────────────────────────────────────

def _fetch_price_metrics(symbol: str, reject_dt: datetime, log_date: date) -> dict:
    """reject 시점 이후 MFE/결과 지표 계산. 실패 시 None 필드."""
    base: dict = dict(
        mfe_30m=None, mfe_1h=None, mfe_eod=None,
        close_ret=None, max_move=None, time_to_peak_min=None,
        time_to_3pct_min=None, mae_1h=None,
        next_day_gap=None, next_day_high_ret=None,
    )
    try:
        import yfinance as yf
        end_date = log_date + timedelta(days=4)  # 주말 포함
        df5: Optional[pd.DataFrame] = None
        for suffix in ('.KS', '.KQ'):
            t = yf.Ticker(f'{symbol}{suffix}')
            raw = t.history(
                start=log_date.strftime('%Y-%m-%d'),
                end=end_date.strftime('%Y-%m-%d'),
                interval='5m',
                auto_adjust=True,
            )
            if raw is not None and len(raw) > 5:
                df5 = raw
                break
        if df5 is None or df5.empty:
            return base

        df5.index = pd.to_datetime(df5.index)
        if df5.index.tz is not None:
            df5.index = df5.index.tz_convert('Asia/Seoul').tz_localize(None)

        # reject 이후 당일 데이터
        today = df5[
            (df5.index >= reject_dt) &
            (df5.index.normalize() == pd.Timestamp(log_date))
        ]
        if today.empty:
            return base

        entry = float(today['Close'].iloc[0])
        if entry <= 0:
            return base

        def _mfe(minutes: Optional[int]) -> Optional[float]:
            sub = today[today.index <= reject_dt + timedelta(minutes=minutes)] \
                if minutes else today
            if sub.empty:
                return None
            return round((float(sub['High'].max()) - entry) / entry * 100, 2)

        base['mfe_30m'] = _mfe(30)
        base['mfe_1h']  = _mfe(60)
        base['mfe_eod'] = _mfe(None)

        eod_close = float(today['Close'].iloc[-1])
        base['close_ret'] = round((eod_close - entry) / entry * 100, 2)

        peak_idx = today['High'].idxmax()
        base['max_move'] = base['mfe_eod']
        base['time_to_peak_min'] = max(0, int((peak_idx - reject_dt).total_seconds() / 60))

        # time_to_3pct_min: reject 이후 +3% 처음 도달까지 걸린 시간
        threshold_3 = entry * 1.03
        hit_rows = today[today['High'] >= threshold_3]
        if not hit_rows.empty:
            base['time_to_3pct_min'] = max(0, int(
                (hit_rows.index[0] - reject_dt).total_seconds() / 60
            ))

        # mae_1h: reject 후 1시간 최대 역방향 이동 (음수 = 손실)
        h1_data = today[today.index <= reject_dt + timedelta(minutes=60)]
        if not h1_data.empty:
            base['mae_1h'] = round(
                (float(h1_data['Low'].min()) - entry) / entry * 100, 2
            )

        # 다음 거래일
        nxt = df5[df5.index.normalize() > pd.Timestamp(log_date)]
        if not nxt.empty:
            nd_open = float(nxt['Open'].iloc[0])
            nd_high = float(nxt['High'].max())
            base['next_day_gap']      = round((nd_open - eod_close) / eod_close * 100, 2)
            base['next_day_high_ret'] = round((nd_high - entry) / entry * 100, 2)

    except Exception as e:
        print(f"  [WARN] {symbol} 가격 조회 실패: {e}", file=sys.stderr)

    return base


# ─── 단일 날짜 분석 ───────────────────────────────────────────────────────────

def analyze_date(log_date: date, fetch_price: bool = True, verbose: bool = True) -> list[dict]:
    """지정 날짜의 ACCEPT → SMC reject 케이스 분석. 행 목록 반환."""
    accepts = _parse_accept(log_date)
    no_sigs = _parse_no_sig(log_date)

    if verbose:
        print(f"\n[{log_date}] ACCEPT={len(accepts)} / NO_SIG={len(no_sigs)}")

    rows: list[dict] = []
    for rec in accepts:
        sym = rec['symbol']
        ns  = no_sigs.get(sym)
        if ns is None:
            # NO_SIG 없음 → 실제 매수됐거나 다른 게이트에서 차단
            continue

        reason = ns['reason']
        if verbose:
            print(
                f"  {sym} | reason={reason:<16} "
                f"choch={ns['choch']} bos={ns['bos']} sweep={ns['sweep']} "
                f"rvol={ns['rvol']}"
            )

        price_metrics: dict = {}
        if fetch_price:
            ts_str = ns['no_sig_ts']
            reject_dt = datetime.combine(log_date, datetime.strptime(ts_str, '%H:%M:%S').time())
            price_metrics = _fetch_price_metrics(sym, reject_dt, log_date)

        # accept_hour: 시간대 분석용 (HH 정수)
        try:
            _ah = int(rec['accept_ts'].split(' ')[1].split(':')[0])
        except Exception:
            _ah = -1

        rows.append({
            'date':         str(log_date),
            'symbol':       sym,
            'accept_price': rec['accept_price'],
            'conf':         rec['conf'],
            'alpha':        rec['alpha'],
            'regime':       rec.get('regime', 'UNKNOWN'),
            'accept_hour':  _ah,
            'reason':       reason,
            'choch':        ns['choch'],
            'bos':          ns['bos'],
            'sweep':        ns['sweep'],
            'htf':          ns['htf'],
            'reclaim':      ns['reclaim'],
            'rvol':         ns['rvol'],
            **price_metrics,
        })

    return rows


# ─── CSV 저장 / 누적 로드 ─────────────────────────────────────────────────────

def _csv_path(log_date: date) -> Path:
    return OUT_DIR / f'reject_quality_{log_date.strftime("%Y%m%d")}.csv'


def save_csv(rows: list[dict], log_date: date) -> Path:
    df = pd.DataFrame(rows)
    path = _csv_path(log_date)
    df.to_csv(path, index=False, encoding='utf-8-sig')
    return path


def load_all_csv() -> pd.DataFrame:
    paths = sorted(OUT_DIR.glob('reject_quality_*.csv'))
    if not paths:
        return pd.DataFrame()
    return pd.concat([pd.read_csv(p) for p in paths], ignore_index=True)


# ─── 요약 출력 ────────────────────────────────────────────────────────────────

_HIT_COLS = ['mfe_eod', 'max_move', 'mfe_1h', 'close_ret']


def print_summary(df: pd.DataFrame):
    if df.empty:
        print("데이터 없음 (로그 누적 후 재실행)")
        return

    total = len(df)
    has_price = df['mfe_eod'].notna().any()

    print(f"\n{'='*66}")
    print(f"  Reject Quality Report  |  총 {total}건  |  "
          f"{'가격 포함' if has_price else '가격 없음 (--no-price)'}")
    print(f"{'='*66}")

    grp = df.groupby('reason')

    if has_price:
        agg = grp.agg(
            count      = ('symbol', 'count'),
            hit3       = ('mfe_eod', lambda x: (x >= 3.0).sum()),
            hit5       = ('mfe_eod', lambda x: (x >= 5.0).sum()),
            avg_mfe_eod= ('mfe_eod', 'mean'),
            avg_mfe_1h = ('mfe_1h',  'mean'),
            avg_max    = ('max_move','mean'),
        ).sort_values('count', ascending=False)
        agg['hit3%'] = (agg['hit3'] / agg['count'] * 100).round(1)
        agg['hit5%'] = (agg['hit5'] / agg['count'] * 100).round(1)

        print(f"\n{'reason':<18} {'N':>4} {'hit3%':>6} {'hit5%':>6}"
              f" {'mfe_eod':>8} {'mfe_1h':>7} {'max_mv':>7}")
        print('-' * 66)
        for reason, r in agg.iterrows():
            print(
                f"{reason:<18} {int(r['count']):>4}"
                f" {r['hit3%']:>6.1f} {r['hit5%']:>6.1f}"
                f" {r['avg_mfe_eod']:>7.1f}% {r['avg_mfe_1h']:>6.1f}%"
                f" {r['avg_max']:>6.1f}%"
            )
    else:
        # 가격 없으면 구조 분포만
        cnt = grp['symbol'].count().sort_values(ascending=False)
        print(f"\n{'reason':<18} {'N':>4}")
        print('-' * 24)
        for reason, n in cnt.items():
            print(f"{reason:<18} {n:>4}")

    # ── BOS_ONLY 섹션 강조 ──────────────────────────────────────────────────
    bos = df[df['reason'] == 'BOS_ONLY']
    if not bos.empty:
        print(f"\n[BOS_ONLY] CHoCH 없이 차단 ({len(bos)}건) — continuation 검증 대상")
        for _, row in bos.iterrows():
            line = f"  {row['symbol']}"
            if has_price:
                line += (
                    f" | mfe_eod={row.get('mfe_eod','?')}%"
                    f" max={row.get('max_move','?')}%"
                    f" 1h={row.get('mfe_1h','?')}%"
                )
            line += f" | alpha={row['alpha']}"
            print(line)

    # ── Alpha Bucket 분석 ──────────────────────────────────────────────────
    if has_price and 'alpha' in df.columns:
        print(f"\n[Alpha Bucket × hit5%]")
        bins   = [0.0, 1.0, 1.5, 999.0]
        labels = ['0.5~1.0', '1.0~1.5', '1.5+']
        df2 = df.copy()
        df2['alpha_bucket'] = pd.cut(df2['alpha'].abs(), bins=bins, labels=labels, right=False)
        ab = df2.groupby('alpha_bucket', observed=True).agg(
            n    = ('symbol', 'count'),
            hit5 = ('mfe_eod', lambda x: (x >= 5.0).sum()),
            avg  = ('mfe_eod', 'mean'),
        )
        ab['hit5%'] = (ab['hit5'] / ab['n'] * 100).round(1)
        print(f"  {'bucket':<10} {'N':>4} {'hit5%':>6} {'avg_mfe':>8}")
        print(f"  {'-'*32}")
        for bkt, r in ab.iterrows():
            print(f"  {str(bkt):<10} {int(r['n']):>4} {r['hit5%']:>6.1f} {r['avg']:>7.1f}%")

    # ── Regime × Alpha 교차 ────────────────────────────────────────────────
    if has_price and 'alpha' in df.columns and 'regime' in df.columns:
        df2 = df.copy()
        bins   = [0.0, 1.0, 1.5, 999.0]
        labels = ['0.5~1.0', '1.0~1.5', '1.5+']
        df2['alpha_bucket'] = pd.cut(df2['alpha'].abs(), bins=bins, labels=labels, right=False)
        if df2['regime'].nunique() > 1:
            print(f"\n[Regime × Alpha → hit5%]")
            ra = df2.groupby(['regime', 'alpha_bucket'], observed=True).agg(
                n    = ('symbol', 'count'),
                hit5 = ('mfe_eod', lambda x: (x >= 5.0).sum()),
            )
            ra['hit5%'] = (ra['hit5'] / ra['n'] * 100).round(1)
            print(f"  {'regime':<8} {'alpha':<10} {'N':>4} {'hit5%':>6}")
            print(f"  {'-'*32}")
            for (rgm, bkt), r in ra.iterrows():
                print(f"  {str(rgm):<8} {str(bkt):<10} {int(r['n']):>4} {r['hit5%']:>6.1f}")

    # ── BOS_ONLY Forward Profile ────────────────────────────────────────────
    bos_df = df[df['reason'] == 'BOS_ONLY']
    if has_price and len(bos_df) >= 2:
        print(f"\n[BOS_ONLY Forward Profile] ({len(bos_df)}건)")
        print(f"  {'지표':<24} {'값'}")
        print(f"  {'-'*36}")
        cols = {
            'avg time_to_peak (분)': bos_df['time_to_peak_min'].mean(),
            'avg time_to_+3% (분)':  bos_df['time_to_3pct_min'].mean(),
            '+3% 도달률':            (bos_df['time_to_3pct_min'].notna().sum() / len(bos_df) * 100),
            'avg MAE 1h (%)':        bos_df['mae_1h'].mean(),
            'avg MFE 1h (%)':        bos_df['mfe_1h'].mean(),
            'avg MFE eod (%)':       bos_df['mfe_eod'].mean(),
            'breakout fail rate (%)': (
                (bos_df['mfe_1h'].fillna(0) < 1.0).sum() / len(bos_df) * 100
            ),
        }
        for label, val in cols.items():
            if val is not None and pd.notna(val):
                unit = '%' if '%' in label else '분' if '분' in label else ''
                print(f"  {label:<24} {val:>6.1f}{unit}")

    # ── 시간대 분석 ─────────────────────────────────────────────────────────
    if has_price and 'accept_hour' in df.columns and df['accept_hour'].gt(0).any():
        df3 = df[df['accept_hour'] > 0].copy()
        # 한국장 시간대 버킷
        def _hour_bucket(h: int) -> str:
            if h < 10:  return 'OR(<10시)'
            if h < 11:  return '10시'
            if h < 12:  return '11시'
            if h < 13:  return '12시'
            return '13시+'
        df3['hour_bucket'] = df3['accept_hour'].map(_hour_bucket)
        hg = df3.groupby('hour_bucket').agg(
            n    = ('symbol', 'count'),
            hit3 = ('mfe_eod', lambda x: (x >= 3.0).sum()),
            hit5 = ('mfe_eod', lambda x: (x >= 5.0).sum()),
            avg  = ('mfe_eod', 'mean'),
        ).reindex(['OR(<10시)', '10시', '11시', '12시', '13시+'], fill_value=0)
        hg['hit3%'] = (hg['hit3'] / hg['n'].replace(0, pd.NA) * 100).round(1)
        hg['hit5%'] = (hg['hit5'] / hg['n'].replace(0, pd.NA) * 100).round(1)
        print(f"\n[시간대별 reject outcome]")
        print(f"  {'시간대':<10} {'N':>4} {'hit3%':>6} {'hit5%':>6} {'avg_mfe':>8}")
        print(f"  {'-'*38}")
        for bucket, r in hg.iterrows():
            if r['n'] == 0:
                continue
            print(
                f"  {bucket:<10} {int(r['n']):>4}"
                f" {r['hit3%']:>6.1f} {r['hit5%']:>6.1f}"
                f" {r['avg']:>7.1f}%"
            )

    # ── Regime × Reason 크로스탭 ───────────────────────────────────────────
    if 'regime' in df.columns and df['regime'].nunique() > 1:
        print(f"\n[Regime × reason] (N 기준)")
        ct = pd.crosstab(df['regime'], df['reason'])
        print(ct.to_string())

        if has_price:
            print(f"\n[Regime × hit5%]")
            rg = df.groupby('regime').agg(
                n    = ('symbol', 'count'),
                hit5 = ('mfe_eod', lambda x: (x >= 5.0).sum()),
                avg  = ('mfe_eod', 'mean'),
            )
            rg['hit5%'] = (rg['hit5'] / rg['n'] * 100).round(1)
            print(f"  {'regime':<10} {'N':>4} {'hit5%':>6} {'avg_mfe':>8}")
            print(f"  {'-'*32}")
            for rgm, r in rg.iterrows():
                print(f"  {str(rgm):<10} {int(r['n']):>4} {r['hit5%']:>6.1f} {r['avg']:>7.1f}%")

    # ── 요약 해석 힌트 ─────────────────────────────────────────────────────
    if has_price:
        print()
        high_fn = df[df['mfe_eod'] >= 5.0]
        if not high_fn.empty:
            print(f"[!] false reject 후보 (+5% 이상): {len(high_fn)}건")
            for _, row in high_fn.iterrows():
                print(
                    f"    {row['symbol']} reason={row['reason']}"
                    f" mfe_eod={row['mfe_eod']}% max={row['max_move']}%"
                )
        else:
            print("[✓] +5% 이상 false reject 없음 — 현재 필터 방어적으로 작동 중")

    print(f"\n{'='*66}\n")


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description='SMC reject 품질 분석')
    ap.add_argument('--date', default=None, help='YYYY-MM-DD (기본: 오늘)')
    ap.add_argument('--days', type=int, default=1, help='최근 N일 분석 (기본: 1)')
    ap.add_argument('--summary', action='store_true', help='누적 CSV 통계 출력')
    ap.add_argument('--no-price', action='store_true', help='가격 조회 생략 (구조 확인만)')
    args = ap.parse_args()

    if args.summary:
        print_summary(load_all_csv())
        return

    if args.date:
        dates = [datetime.strptime(args.date, '%Y-%m-%d').date()]
    else:
        today = date.today()
        dates = [today - timedelta(days=i) for i in range(args.days - 1, -1, -1)]

    all_rows: list[dict] = []
    for d in dates:
        rows = analyze_date(d, fetch_price=not args.no_price)
        if rows:
            if not args.no_price:
                path = save_csv(rows, d)
                print(f"  → 저장: {path.name}")
            all_rows.extend(rows)
        else:
            print(f"  차단 케이스 없음 (전부 매수됐거나 NO_SIG 로그 없음)")

    if all_rows:
        print_summary(pd.DataFrame(all_rows))
    elif not args.summary:
        print("\n분석할 데이터 없음. smc_decision 로그가 생성된 날 재실행하세요.")


if __name__ == '__main__':
    main()
