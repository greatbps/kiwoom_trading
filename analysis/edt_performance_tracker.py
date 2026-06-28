"""
EDT (Early Downtrend) 차단 성과 트래커
======================================
logs/edt_blocked_candidates.jsonl 에서 차단 기록을 읽어
yfinance로 5일 후 수익률을 채우고 KPI 리포트를 출력한다.

사용법:
    python -m analysis.edt_performance_tracker [--days 30] [--no-fill]

출력 KPI:
  ① 필터 적중률  — 차단 후 하락 비율 (목표: 60%+)
  ② 기회 손실률  — 차단 후 상승 비율 (목표: 20%-)
  ③ 반전 성공률  — REVERSAL 이후 상승 비율 (목표: 최대화)
  ④ 예외 성공률  — UPTREND_EXEMPT 이후 상승 비율
"""

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, date

TRACK_FILE  = os.path.join('logs', 'edt_blocked_candidates.jsonl')
FILL_DAYS   = [3, 5, 10]   # next_3d / next_5d / next_10d


def _load_records(days: int) -> list[dict]:
    if not os.path.exists(TRACK_FILE):
        print(f"[WARN] 파일 없음: {TRACK_FILE}")
        return []
    cutoff = datetime.now() - timedelta(days=days)
    records = []
    with open(TRACK_FILE, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
                ts = r.get('ts', '')
                if ts and datetime.fromisoformat(ts[:19]) >= cutoff:
                    records.append(r)
            except Exception:
                pass
    return records


def _fill_returns(records: list[dict]) -> list[dict]:
    """yfinance로 수익률 채우기 (null인 것만). hold_days: 수익률이 처음 +로 전환되는 거래일 수."""
    to_fill = [
        r for r in records
        if r.get('ts') and (
            any(r.get(f'next_{d}d_return') is None for d in FILL_DAYS)
            or r.get('hold_days') is None
        )
    ]
    if not to_fill:
        return records

    try:
        import yfinance as yf
    except ImportError:
        print("[WARN] yfinance 미설치 — pip install yfinance")
        return records

    # 심볼별 날짜 묶음
    by_symbol: dict[str, list] = defaultdict(list)
    for r in to_fill:
        sym = r.get('symbol', '')
        if sym:
            by_symbol[sym].append(r)

    for symbol, recs in by_symbol.items():
        # 키움 6자리 → 야후 코스피/코스닥 suffix
        ticker_ks = f"{symbol}.KS"
        ticker_kq = f"{symbol}.KQ"

        for r in recs:
            entry_ts   = datetime.fromisoformat(r['ts'][:19])
            entry_date = entry_ts.date()
            max_days   = max(FILL_DAYS)
            end_date   = entry_date + timedelta(days=max_days + 7)

            for ticker in (ticker_ks, ticker_kq):
                try:
                    hist = yf.download(
                        ticker,
                        start=str(entry_date),
                        end=str(end_date),
                        interval='1d',
                        progress=False,
                        auto_adjust=True,
                    )
                    if hist is None or hist.empty:
                        continue
                    closes = hist['Close'].dropna()
                    if len(closes) < 2:
                        continue
                    entry_price = float(closes.iloc[0])
                    for d in FILL_DAYS:
                        key = f'next_{d}d_return'
                        if r.get(key) is None:
                            idx = min(d, len(closes) - 1)
                            r[key] = round(
                                (float(closes.iloc[idx]) - entry_price) / entry_price, 4
                            )
                    # hold_days: 수익률이 처음 +로 전환되는 거래일 수
                    if r.get('hold_days') is None:
                        for day_idx in range(1, len(closes)):
                            if float(closes.iloc[day_idx]) > entry_price:
                                r['hold_days'] = day_idx
                                break
                    break
                except Exception:
                    continue

    # 파일 덮어쓰기
    with open(TRACK_FILE, 'w', encoding='utf-8') as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    filled = sum(1 for r in to_fill if r.get('next_5d_return') is not None)
    print(f"[INFO] {filled}/{len(to_fill)}건 수익률 채움")
    return records


def _report(records: list[dict]):
    by_tag = defaultdict(list)
    for r in records:
        by_tag[r.get('tag', '')].append(r)

    print(f"\n{'='*55}")
    print(f"  EDT 차단 성과 리포트  (최근 분석 대상: {len(records)}건)")
    print(f"{'='*55}")

    tag_labels = {
        'EARLY_DOWNTREND':                ('🚫 차단',        '목표: 적중률 60%+, 손실률 20%-'),
        'EARLY_DOWNTREND_UPTREND_EXEMPT': ('✅ 상승추세예외', '목표: 예외 성공률 50%+'),
        'EARLY_DOWNTREND_REVERSAL':       ('✅ 반전신호',     '목표: 반전 성공률 최대화'),
        'EARLY_DOWNTREND_HEALTHY_PULLBACK': ('✅ 건강한눌림', '목표: 성공률 50%+, 기회손실률 감소'),
    }

    for tag, (label, target) in tag_labels.items():
        recs = by_tag.get(tag, [])
        if not recs:
            print(f"\n{label}: 기록 없음")
            continue

        with_ret = [r for r in recs if r.get('next_5d_return') is not None]
        total    = len(recs)
        filled   = len(with_ret)

        print(f"\n{label}  ({total}건, 수익률 집계 {filled}건) — {target}")
        if not with_ret:
            print("  수익률 데이터 없음 (--no-fill 해제 후 재실행)")
            continue

        # 3d/5d/10d 수익률 테이블
        for d in FILL_DAYS:
            key     = f'next_{d}d_return'
            d_recs  = [r for r in with_ret if r.get(key) is not None]
            if not d_recs:
                continue
            d_rets    = [r[key] for r in d_recs]
            d_avg     = sum(d_rets) / len(d_rets)
            d_pos     = sum(1 for x in d_rets if x > 0)
            d_neg     = sum(1 for x in d_rets if x < 0)
            d_pos_pct = d_pos / len(d_rets) * 100
            d_neg_pct = d_neg / len(d_rets) * 100
            print(
                f"  [{d:2d}일] avg={d_avg*100:+.2f}%  "
                f"상승={d_pos_pct:.0f}%({d_pos})  하락={d_neg_pct:.0f}%({d_neg})"
            )

        # 3d↓/10d↑ 패턴 감지 (건강한 눌림 vs 데드캣 판별)
        pattern_recs = [
            r for r in with_ret
            if r.get('next_3d_return') is not None and r.get('next_10d_return') is not None
        ]
        if pattern_recs:
            healthy = sum(
                1 for r in pattern_recs
                if r['next_3d_return'] < 0 and r['next_10d_return'] > 0
            )
            deadcat = sum(
                1 for r in pattern_recs
                if r['next_3d_return'] > 0 and r['next_10d_return'] < 0
            )
            n = len(pattern_recs)
            print(f"  패턴: 건강한눌림(3d↓/10d↑)={healthy/n*100:.0f}%  "
                  f"데드캣(3d↑/10d↓)={deadcat/n*100:.0f}%")

        # 5일 기준 주요 KPI
        rets      = [r['next_5d_return'] for r in with_ret]
        pos_count = sum(1 for r in rets if r > 0)
        neg_count = sum(1 for r in rets if r < 0)
        pos_rate  = pos_count / len(rets) * 100
        neg_rate  = neg_count / len(rets) * 100

        if tag == 'EARLY_DOWNTREND':
            print(f"  ── KPI(5일 기준) ──")
            print(f"  적중률(차단→하락): {neg_rate:.1f}%  {'✅' if neg_rate >= 60 else '⚠️'}")
            print(f"  기회손실률(차단→상승): {pos_rate:.1f}%  {'✅' if pos_rate <= 20 else '⚠️'}")
        else:
            print(f"  성공률(이후 상승, 5일): {pos_rate:.1f}%  {'✅' if pos_rate >= 50 else '⚠️'}")

        # hold_days 분석 (빠른 수익 vs 느린 수익)
        hd_recs = [r for r in with_ret if r.get('hold_days') is not None]
        if hd_recs:
            hd_vals = [r['hold_days'] for r in hd_recs]
            hd_avg  = sum(hd_vals) / len(hd_vals)
            hd_fast = sum(1 for h in hd_vals if h <= 3)   # 3일 내 수익 전환
            print(
                f"  수익전환 평균={hd_avg:.1f}일  "
                f"빠른전환(≤3일)={hd_fast/len(hd_vals)*100:.0f}% ({hd_fast}건)"
            )

        # score별 분석 (EARLY_DOWNTREND만)
        if tag == 'EARLY_DOWNTREND':
            by_score = defaultdict(list)
            for r in with_ret:
                by_score[r.get('score', '?')].append(r['next_5d_return'])
            if by_score:
                print("  score별 평균 5일 수익률:")
                for sc in sorted(by_score.keys()):
                    sc_rets = by_score[sc]
                    sc_avg  = sum(sc_rets) / len(sc_rets)
                    sc_neg  = sum(1 for x in sc_rets if x < 0) / len(sc_rets) * 100
                    print(f"    score={sc}: avg={sc_avg*100:+.2f}%  적중률={sc_neg:.0f}% ({len(sc_rets)}건)")

            # 레짐별 분석
            by_regime = defaultdict(list)
            for r in with_ret:
                regime = r.get('regime', '?').split('(')[0]
                by_regime[regime].append(r['next_5d_return'])
            if len(by_regime) > 1:
                print("  레짐별 평균 5일 수익률:")
                for reg, reg_rets in by_regime.items():
                    reg_avg = sum(reg_rets) / len(reg_rets)
                    reg_neg = sum(1 for x in reg_rets if x < 0) / len(reg_rets) * 100
                    print(f"    {reg}: avg={reg_avg*100:+.2f}%  적중률={reg_neg:.0f}% ({len(reg_rets)}건)")

    print(f"\n{'='*55}\n")


# ── Kelly 사이징 ────────────────────────────────────────────────────────────

def _kelly_sizing(records: list[dict]) -> dict:
    """
    태그별 Half-Kelly 분수 계산 → 권장 size_mult 반환.

    반환: {tag: {'win_rate': float, 'kelly_f': float, 'recommended_mult': float}}
    """
    ENTRY_TAGS = {
        'EARLY_DOWNTREND_HEALTHY_PULLBACK',
        'EARLY_DOWNTREND_REVERSAL',
        'EARLY_DOWNTREND_UPTREND_EXEMPT',
    }
    results = {}
    by_tag = defaultdict(list)
    for r in records:
        tag = r.get('tag', '')
        ret = r.get('next_5d_return')
        if tag in ENTRY_TAGS and ret is not None:
            by_tag[tag].append(ret)

    for tag, rets in by_tag.items():
        if len(rets) < 5:
            continue
        wins   = [r for r in rets if r > 0]
        losses = [abs(r) for r in rets if r < 0]
        if not wins or not losses:
            continue
        p       = len(wins) / len(rets)
        q       = 1 - p
        b       = (sum(wins) / len(wins)) / (sum(losses) / len(losses))  # win/loss ratio
        kelly_f = max(0.0, p - q / b)
        half_k  = kelly_f * 0.5
        rec_mult = round(min(1.5, max(1.0, 1.0 + half_k)), 3)
        results[tag] = {
            'n':               len(rets),
            'win_rate':        round(p * 100, 1),
            'avg_win':         round(sum(wins) / len(wins) * 100, 2),
            'avg_loss':        round(sum(losses) / len(losses) * 100, 2),
            'b_ratio':         round(b, 3),
            'kelly_f':         round(kelly_f, 4),
            'half_kelly':      round(half_k, 4),
            'recommended_mult': rec_mult,
        }
    return results


def _symbol_weights(records: list[dict]) -> dict:
    """
    종목별 승률 기반 weight 계산 → logs/edt_symbol_weights.json 저장.

    반환: {symbol: {win_rate, count, avg_5d_return, weight}}
    """
    ENTRY_TAGS = {
        'EARLY_DOWNTREND_HEALTHY_PULLBACK',
        'EARLY_DOWNTREND_REVERSAL',
    }
    by_sym = defaultdict(list)
    for r in records:
        sym = r.get('symbol', '')
        ret = r.get('next_5d_return')
        if r.get('tag') in ENTRY_TAGS and sym and ret is not None:
            by_sym[sym].append(ret)

    weights = {}
    for sym, rets in by_sym.items():
        if len(rets) < 3:
            continue
        wins     = sum(1 for r in rets if r > 0)
        win_rate = wins / len(rets)
        avg_ret  = sum(rets) / len(rets)
        weights[sym] = {
            'count':        len(rets),
            'win_rate':     round(win_rate * 100, 1),
            'avg_5d_return': round(avg_ret * 100, 2),
            'weight':       round(win_rate, 3),
        }

    _path = os.path.join('logs', 'edt_symbol_weights.json')
    with open(_path, 'w', encoding='utf-8') as f:
        json.dump(weights, f, ensure_ascii=False, indent=2)
    print(f"[INFO] 종목별 weight {len(weights)}건 → {_path}")
    return weights


def _kelly_report(records: list[dict], kelly: dict, weights: dict):
    """Kelly 사이징 + 종목 weight 리포트."""
    print(f"\n{'='*55}")
    print(f"  Kelly 사이징 권고  (Half-Kelly, 5일 기준)")
    print(f"{'='*55}")

    tag_short = {
        'EARLY_DOWNTREND_HEALTHY_PULLBACK': 'HEALTHY_PULLBACK',
        'EARLY_DOWNTREND_REVERSAL':         'REVERSAL',
        'EARLY_DOWNTREND_UPTREND_EXEMPT':   'UPTREND_EXEMPT',
    }
    for tag, k in kelly.items():
        label = tag_short.get(tag, tag)
        flag  = '✅' if k['recommended_mult'] > 1.1 else '⚠️'
        print(
            f"  {label} ({k['n']}건)\n"
            f"    승률={k['win_rate']}%  b={k['b_ratio']}  "
            f"kelly_f={k['kelly_f']:.3f}  half={k['half_kelly']:.3f}\n"
            f"    권장 size_mult: {k['recommended_mult']}  {flag}"
        )

    # Drawdown 연동: 현재 DrawdownEngine 상태 읽어서 보정
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        import yaml
        with open('config/strategy_hybrid.yaml') as f:
            cfg = yaml.safe_load(f)
        from core.drawdown_engine import DrawdownEngine
        import json as _json
        rlog = _json.load(open('data/risk_log.json'))
        dd_engine  = DrawdownEngine(cfg)
        daily_pnl  = float(rlog.get('daily_realized_pnl', 0))
        dd_engine.record_pnl(daily_pnl, strategy='smc')
        dd_mult, dd_level = dd_engine.get_size_mult(strategy='smc')
        print(f"\n  현재 DrawdownEngine 상태: {dd_level}  size×{dd_mult}")
        if dd_mult < 1.0 and kelly:
            print("  ── Drawdown 보정 후 권장 size_mult ──")
            for tag, k in kelly.items():
                label  = tag_short.get(tag, tag)
                adj    = round(k['recommended_mult'] * dd_mult, 3)
                print(f"    {label}: {k['recommended_mult']} × {dd_mult} = {adj}")
    except Exception as e:
        print(f"  [DrawdownEngine 연동 생략: {e}]")

    # 상위 종목 weight
    if weights:
        top = sorted(weights.items(), key=lambda x: x[1]['weight'], reverse=True)[:5]
        print(f"\n  종목별 상위 5 weight")
        for sym, w in top:
            print(
                f"    {sym}: 승률={w['win_rate']}%  "
                f"avg={w['avg_5d_return']:+.2f}%  "
                f"weight={w['weight']}  ({w['count']}건)"
            )

    print(f"\n{'='*55}\n")


def main():
    parser = argparse.ArgumentParser(description='EDT 차단 성과 트래커')
    parser.add_argument('--days',    type=int, default=30, help='분석 기간 (기본 30일)')
    parser.add_argument('--no-fill', action='store_true',  help='yfinance 수익률 채우기 건너뜀')
    parser.add_argument('--no-kelly', action='store_true', help='Kelly 사이징 리포트 건너뜀')
    args = parser.parse_args()

    records = _load_records(args.days)
    print(f"[INFO] {args.days}일치 기록 {len(records)}건 로드")

    if not args.no_fill:
        records = _fill_returns(records)

    _report(records)

    if not args.no_kelly:
        kelly   = _kelly_sizing(records)
        weights = _symbol_weights(records)
        _kelly_report(records, kelly, weights)


if __name__ == '__main__':
    main()
