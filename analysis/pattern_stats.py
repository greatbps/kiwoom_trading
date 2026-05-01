"""
패턴 인식 레이어 성과 분석 + 자동 가중치 생성

사용법:
    python -m analysis.pattern_stats              # 오늘
    python -m analysis.pattern_stats --days 7     # 최근 7일
    python -m analysis.pattern_stats --date 2026-04-21

출력:
    1) 패턴 발생 vs 실제 진입 비율
    2) phase별 승률/기대값/MDD
    3) confidence 구간별 승률
    4) 패턴 있음 vs 없음 수익률 비교
    5) 자동 판단: data/pattern_weights.json 생성

데이터 소스:
    - logs/auto_trading_YYYYMMDD.log  → [PATTERN] 태그 파싱
    - data/trades.db                  → BUY/SELL 매칭 → realized_pnl
"""
import argparse
import json
import re
import sqlite3
from collections import defaultdict
from datetime import datetime, timedelta, date
from pathlib import Path

BASE       = Path(__file__).parent.parent
LOGS_DIR   = BASE / 'logs'
TRADES_DB  = BASE / 'data' / 'trades.db'
WEIGHTS_PATH = BASE / 'data' / 'pattern_weights.json'

# ─── 검증 기준 (전략 채택 최소 조건) ─────────────────────────────────────────
MIN_TRADES     = 20      # 최소 샘플 수
MIN_WINRATE    = 0.55    # 최소 승률 55%
MIN_EXPECTANCY = 0.20    # 최소 기대값 (R 단위)
MIN_RR         = 1.30    # 최소 손익비
MAX_DRAWDOWN   = -0.15   # 최대 허용 MDD (-15%)

# ─── 정규식 ───────────────────────────────────────────────────────────────────

_RE_PATTERN = re.compile(
    r'\[PATTERN\]\s+'
    r'(\w+)\s+'
    r'(.+?)\s+\|\s+'
    r'(?:LOG_ONLY|SCORE_ON)\s+\|\s+'
    r'(\w+)\((\w+)\)\s+'
    r'conf=([\d.]+)\s+'
    r'RR=([\d.]+)\s+'
    r'entry=([\d.]+)\s+'
    r'stop=([\d.]+)\s+'
    r'target=([\d.]+)'
)
_RE_TS = re.compile(r'^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})')


# ─── 로그 파싱 ────────────────────────────────────────────────────────────────

def parse_pattern_log(log_date: str) -> list[dict]:
    log_file = LOGS_DIR / f'auto_trading_{log_date.replace("-", "")}.log'
    if not log_file.exists():
        return []
    hits = []
    with open(log_file, encoding='utf-8', errors='replace') as f:
        for line in f:
            if '[PATTERN]' not in line:
                continue
            m = _RE_PATTERN.search(line)
            if not m:
                continue
            ts_m = _RE_TS.match(line)
            hits.append({
                'ts':         ts_m.group(1) if ts_m else None,
                'date':       log_date,
                'code':       m.group(1),
                'name':       m.group(2).strip(),
                'pattern':    m.group(3),
                'phase':      m.group(4),
                'phase_key':  f"{m.group(3)}({m.group(4)})",
                'confidence': float(m.group(5)),
                'rr':         float(m.group(6)),
                'entry':      float(m.group(7)),
                'stop':       float(m.group(8)),
                'target':     float(m.group(9)),
            })
    return hits


# ─── 거래 DB 조회 ─────────────────────────────────────────────────────────────

def fetch_trades(date_from: str, date_to: str) -> list[dict]:
    if not TRADES_DB.exists():
        return []
    conn = sqlite3.connect(str(TRADES_DB))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT stock_code, stock_name, trade_type, price,
               realized_pnl, reason, strategy, trade_date, timestamp
        FROM trades
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY timestamp
        """,
        (date_from, date_to),
    ).fetchall()
    conn.close()

    buys:  dict[tuple, dict] = {}
    pairs: list[dict] = []
    for r in rows:
        key = (r['stock_code'], r['trade_date'])
        if r['trade_type'] == 'BUY':
            buys[key] = dict(r)
        elif r['trade_type'] == 'SELL' and key in buys:
            buy = buys.pop(key)
            pnl_pct = (r['price'] - buy['price']) / buy['price'] * 100
            pairs.append({
                'code':      r['stock_code'],
                'name':      r['stock_name'],
                'date':      r['trade_date'],
                'buy_price': buy['price'],
                'sell_price': r['price'],
                'pnl':       float(r['realized_pnl'] or 0),
                'pnl_pct':   pnl_pct,
                'reason':    r['reason'],
                'strategy':  r['strategy'],
            })
    return pairs


# ─── 매칭 ────────────────────────────────────────────────────────────────────

def match_pattern_to_trades(
    hits: list[dict], trades: list[dict]
) -> tuple[list[dict], list[dict]]:
    trade_map = {(t['code'], t['date']): t for t in trades}
    hit_keys  = set()
    matched   = []
    for h in hits:
        key = (h['code'], h['date'])
        hit_keys.add(key)
        matched.append({**h, 'trade': trade_map.get(key)})
    unmatched = [t for t in trades if (t['code'], t['date']) not in hit_keys]
    return matched, unmatched


# ─── 통계 헬퍼 ───────────────────────────────────────────────────────────────

def _win_rate(pnls: list[float]) -> float:
    return sum(1 for p in pnls if p > 0) / len(pnls) if pnls else 0.0

def _avg(vals: list[float]) -> float:
    return sum(vals) / len(vals) if vals else 0.0

def _expectancy(pnls: list[float]) -> float:
    """기대값 (R 단위): win_rate * avg_win - loss_rate * abs(avg_loss)."""
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    wr  = len(wins) / len(pnls) if pnls else 0
    lr  = 1 - wr
    avg_w = _avg(wins)
    avg_l = abs(_avg(losses)) if losses else 0
    return round(wr * avg_w - lr * avg_l, 4)

def _mdd(pnls: list[float]) -> float:
    """최대 낙폭 (누적 pnl 기준)."""
    if not pnls:
        return 0.0
    equity = 0.0
    peak   = 0.0
    worst  = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return round(worst / 100, 4)  # % → ratio


# ─── 검증 ────────────────────────────────────────────────────────────────────

def is_valid_phase(s: dict) -> tuple[bool, list[str]]:
    """채택 기준 체크. Returns (valid, reasons_failed)."""
    reasons = []
    if s['n'] < MIN_TRADES:
        reasons.append(f'샘플 {s["n"]} < {MIN_TRADES}')
    if s['win_rate'] < MIN_WINRATE:
        reasons.append(f'승률 {s["win_rate"]:.0%} < {MIN_WINRATE:.0%}')
    if s['expectancy'] < MIN_EXPECTANCY:
        reasons.append(f'기대값 {s["expectancy"]:.3f} < {MIN_EXPECTANCY}')
    if s['avg_rr'] < MIN_RR:
        reasons.append(f'RR {s["avg_rr"]:.2f} < {MIN_RR}')
    if s['mdd'] < MAX_DRAWDOWN:
        reasons.append(f'MDD {s["mdd"]:.1%} < {MAX_DRAWDOWN:.1%}')
    return (len(reasons) == 0), reasons


# ─── 통계 계산 ───────────────────────────────────────────────────────────────

def compute_stats(matched: list[dict], unmatched: list[dict]) -> dict:
    total_hits  = len(matched)
    entered     = [m for m in matched if m['trade'] is not None]
    entry_rate  = len(entered) / total_hits * 100 if total_hits else 0

    # phase별
    by_phase_pnl: dict[str, list[float]] = defaultdict(list)
    by_phase_rr:  dict[str, list[float]] = defaultdict(list)
    for m in entered:
        pk = m['phase_key']
        by_phase_pnl[pk].append(m['trade']['pnl_pct'])
        by_phase_rr[pk].append(m['rr'])

    phase_stats = {}
    for pk, pnls in by_phase_pnl.items():
        rrs   = by_phase_rr[pk]
        _wins = [p for p in pnls if p > 0]
        _loss = [p for p in pnls if p <= 0]
        s = {
            'n':          len(pnls),
            'win_rate':   round(_win_rate(pnls), 4),
            'avg_pnl':    round(_avg(pnls), 4),
            'expectancy': _expectancy(pnls),
            'avg_rr':     round(_avg(rrs), 2),
            'mdd':        _mdd(pnls),
            'avg_win':    round(_avg(_wins), 4) if _wins else 0.0,   # Kelly RR용
            'avg_loss':   round(_avg(_loss), 4) if _loss else 0.0,   # 음수
        }
        valid, fail_reasons = is_valid_phase(s)
        s['valid']        = valid
        s['fail_reasons'] = fail_reasons
        phase_stats[pk]   = s

    # confidence 구간별
    buckets = [('≥0.85', lambda c: c >= 0.85),
               ('0.80~', lambda c: 0.80 <= c < 0.85),
               ('<0.80', lambda c: c < 0.80)]
    conf_stats = {}
    for label, fn in buckets:
        pnls = [m['trade']['pnl_pct'] for m in entered if fn(m['confidence'])]
        conf_stats[label] = {
            'n': len(pnls),
            'win_rate': round(_win_rate(pnls), 4),
            'avg_pnl':  round(_avg(pnls), 4),
        }

    # 패턴 있음 vs 없음
    with_pat = [m['trade']['pnl_pct'] for m in entered]
    without  = [t['pnl_pct'] for t in unmatched]
    comparison = {
        'with_pattern':    {'n': len(with_pat), 'win_rate': round(_win_rate(with_pat), 4), 'avg_pnl': round(_avg(with_pat), 4)},
        'without_pattern': {'n': len(without),  'win_rate': round(_win_rate(without),  4), 'avg_pnl': round(_avg(without),  4)},
    }

    return {
        'total_pattern_hits': total_hits,
        'total_entered':      len(entered),
        'total_not_entered':  len(matched) - len(entered),
        'entry_rate_pct':     round(entry_rate, 1),
        'by_phase':           phase_stats,
        'by_confidence':      conf_stats,
        'comparison':         comparison,
        'sample_warning':     len(entered) < MIN_TRADES,
    }


# ─── 가중치 안정화 헬퍼 ──────────────────────────────────────────────────────

EMA_ALPHA        = 0.30   # 신규 데이터 반영 비율 (낮을수록 안정)
CONFIDENCE_N_REF = 50     # 샘플 이 수량일 때 신뢰도 100%
MIN_ACTIVE_DAYS  = 3      # 일시적 조건 미달 시 최소 유지 일수


def _ema_smooth(prev: float, new: float, alpha: float = EMA_ALPHA) -> float:
    """지수평활: prev × (1-α) + new × α."""
    return prev * (1 - alpha) + new * alpha


def _confidence_factor(n: int) -> float:
    """샘플 수 기반 신뢰도 (n=50 → 1.0, n=20 → 0.4)."""
    return min(1.0, n / CONFIDENCE_N_REF)


def _load_prev_weights() -> dict:
    """기존 pattern_weights.json 로드. 없으면 {}."""
    if not WEIGHTS_PATH.exists():
        return {}
    try:
        data = json.loads(WEIGHTS_PATH.read_text(encoding='utf-8'))
        return data.get('weights', {})
    except Exception:
        return {}


# ─── 가중치 생성 ──────────────────────────────────────────────────────────────

def compute_weights(stats: dict) -> dict:
    """
    검증 + EMA 평활 + 샘플 보정 적용한 최종 가중치.

    weight 구조:
        {
          "double_bottom(confirmed)": {
            "weight":      0.21,   # confidence × ema_smoothed × sample_factor
            "raw_expect":  0.32,   # 이번 기간 기대값
            "n":           34,
            "win_rate":    0.62,
            "active_days": 4,
            "updated_at":  "2026-04-21"
          }
        }
    """
    today     = date.today().isoformat()
    prev      = _load_prev_weights()
    new_weights: dict = {}

    for phase, s in stats['by_phase'].items():
        valid = s['valid']
        prev_entry = prev.get(phase, {})
        prev_w     = prev_entry.get('weight', 0.0) if isinstance(prev_entry, dict) else float(prev_entry)
        active_days = prev_entry.get('active_days', 0) if isinstance(prev_entry, dict) else 0

        if valid:
            raw = s['expectancy']
            # 1. EMA 평활 (초기값: raw 그대로)
            smoothed = _ema_smooth(prev_w, raw) if prev_w > 0 else raw
            # 2. 샘플 수 신뢰도 보정
            final = round(smoothed * _confidence_factor(s['n']), 4)
            # 3. weight > 1.0 방지
            final = min(final, 1.0)
            new_weights[phase] = {
                'weight':      final,
                'raw_expect':  s['expectancy'],
                'n':           s['n'],
                'win_rate':    round(s['win_rate'], 4),
                'avg_win':     s.get('avg_win', 0.0),
                'avg_loss':    s.get('avg_loss', 0.0),
                'active_days': active_days + 1,
                'updated_at':  today,
            }
        elif active_days > 0 and active_days < MIN_ACTIVE_DAYS:
            # 일시적 조건 미달 → 최소 유지 기간 동안 보존 (weight 동결)
            new_weights[phase] = {
                **prev_entry,
                'active_days': active_days,   # 카운트 유지 (증가 안 함)
                'updated_at':  today,
                '_grace':      True,           # 유예 중 표시
            }

    return new_weights


def save_weights(weights: dict, stats: dict) -> None:
    """data/pattern_weights.json 저장."""
    payload = {
        'generated_at': datetime.now().isoformat(),
        'criteria': {
            'min_trades':      MIN_TRADES,
            'min_winrate':     MIN_WINRATE,
            'min_expectancy':  MIN_EXPECTANCY,
            'min_rr':          MIN_RR,
            'max_drawdown':    MAX_DRAWDOWN,
            'ema_alpha':       EMA_ALPHA,
            'confidence_n':    CONFIDENCE_N_REF,
            'min_active_days': MIN_ACTIVE_DAYS,
        },
        'weights':  weights,
        'phase_stats': {
            pk: {k: v for k, v in s.items() if k != 'fail_reasons'}
            for pk, s in stats['by_phase'].items()
        },
    }
    WEIGHTS_PATH.parent.mkdir(exist_ok=True)
    WEIGHTS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2))


# ─── 리포트 출력 ──────────────────────────────────────────────────────────────

def print_report(stats: dict, weights: dict, date_range: str):
    print(f'\n{"="*60}')
    print(f'  패턴 인식 성과 분석  ({date_range})')
    print(f'{"="*60}')

    if stats['sample_warning']:
        print(f'  ⚠️  샘플 {stats["total_entered"]}건 — {MIN_TRADES}건 미만, 해석 주의')

    print(f'\n[1] 패턴 발생 vs 실제 진입')
    print(f'  히트: {stats["total_pattern_hits"]}건  '
          f'진입: {stats["total_entered"]}건 ({stats["entry_rate_pct"]}%)  '
          f'미진입: {stats["total_not_entered"]}건')

    print(f'\n[2] phase별 성능')
    if stats['by_phase']:
        for pk, s in sorted(stats['by_phase'].items()):
            mark = '✅' if s['valid'] else '❌'
            fail = f'  ({", ".join(s["fail_reasons"])})' if s['fail_reasons'] else ''
            print(f'  {mark} {pk:<32}  n={s["n"]:>3}  '
                  f'승률={s["win_rate"]:.0%}  '
                  f'기대값={s["expectancy"]:>+.3f}  '
                  f'RR={s["avg_rr"]:.2f}  '
                  f'MDD={s["mdd"]:.1%}'
                  f'{fail}')
    else:
        print('  (데이터 없음)')

    print(f'\n[3] confidence 구간별 승률')
    for bucket, s in stats['by_confidence'].items():
        if s['n']:
            print(f'  conf {bucket:<8}  n={s["n"]:>3}  '
                  f'승률={s["win_rate"]:.0%}  평균={s["avg_pnl"]:>+.2f}%')
        else:
            print(f'  conf {bucket:<8}  n=  0  (없음)')

    print(f'\n[4] 패턴 있음 vs 없음')
    wp = stats['comparison']['with_pattern']
    wo = stats['comparison']['without_pattern']
    print(f'  패턴 있음  n={wp["n"]:>3}  승률={wp["win_rate"]:.0%}  평균={wp["avg_pnl"]:>+.2f}%')
    print(f'  패턴 없음  n={wo["n"]:>3}  승률={wo["win_rate"]:.0%}  평균={wo["avg_pnl"]:>+.2f}%')
    if wp['n'] and wo['n']:
        diff = wp['avg_pnl'] - wo['avg_pnl']
        verdict = ('✅ 패턴 유리' if diff > 0.2
                   else '⚠️  차이 미미' if abs(diff) <= 0.2
                   else '❌ 패턴 불리')
        print(f'  → 차이 {diff:>+.2f}%p  {verdict}')

    print(f'\n[5] 자동 가중치 (EMA평활 + 샘플보정)')
    if weights:
        active  = {k: v for k, v in weights.items() if not v.get('_grace')}
        graced  = {k: v for k, v in weights.items() if v.get('_grace')}
        if active:
            print(f'  ✅ {len(active)}개 채택')
            for pk, w in active.items():
                n_bar = '█' * min(10, int(_confidence_factor(w['n']) * 10))
                print(f'     {pk:<32}  weight={w["weight"]:.4f}  '
                      f'n={w["n"]:>3}  wr={w["win_rate"]:.0%}  '
                      f'days={w["active_days"]}  [{n_bar:<10}]')
        if graced:
            print(f'  ⏳ {len(graced)}개 유예 (조건 미달, active_days < {MIN_ACTIVE_DAYS})')
            for pk, w in graced.items():
                print(f'     {pk:<32}  weight={w["weight"]:.4f} (동결)  '
                      f'days={w["active_days"]}/{MIN_ACTIVE_DAYS}')
    else:
        print(f'  ❌ 채택 기준 미달 — 기존 가중치 유지 (score_enabled=false 유지)')

    print(f'\n{"="*60}\n')


# ─── 메인 ────────────────────────────────────────────────────────────────────

def run(days: int = 1, ref_date: str = None) -> dict:
    end   = ref_date or date.today().strftime('%Y-%m-%d')
    start = (datetime.strptime(end, '%Y-%m-%d') - timedelta(days=days - 1)).strftime('%Y-%m-%d')
    date_range = start if start == end else f'{start} ~ {end}'

    all_hits = []
    cur = datetime.strptime(start, '%Y-%m-%d')
    end_dt = datetime.strptime(end, '%Y-%m-%d')
    while cur <= end_dt:
        all_hits.extend(parse_pattern_log(cur.strftime('%Y-%m-%d')))
        cur += timedelta(days=1)

    trades   = fetch_trades(start, end)
    matched, unmatched = match_pattern_to_trades(all_hits, trades)
    stats    = compute_stats(matched, unmatched)
    weights  = compute_weights(stats)

    print_report(stats, weights, date_range)

    # weights 저장 (통과 phase 있을 때만 갱신)
    if weights:
        save_weights(weights, stats)
        print(f'  💾 가중치 저장: {WEIGHTS_PATH}')
    else:
        print(f'  ℹ️  가중치 미갱신 (조건 미달)')

    # 분석 리포트 JSON 저장
    report_path = LOGS_DIR / f'pattern_stats_{end.replace("-","")}.json'
    report_path.parent.mkdir(exist_ok=True)
    report_path.write_text(json.dumps({
        'generated_at': datetime.now().isoformat(),
        'date_range':   date_range,
        'stats':        stats,
        'weights':      weights,
    }, ensure_ascii=False, indent=2))
    print(f'  💾 리포트: {report_path}\n')

    return {'stats': stats, 'weights': weights}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='패턴 인식 성과 분석')
    parser.add_argument('--days', type=int, default=1)
    parser.add_argument('--date', type=str, default=None)
    args = parser.parse_args()
    run(days=args.days, ref_date=args.date)
