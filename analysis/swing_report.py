"""
스윙 전략 진단 리포트

사용법:
    python3 -m analysis.swing_report          # 최근 30일
    python3 -m analysis.swing_report --days 60

출력:
    [EXIT ATTRIBUTION]  — 청산 사유별 손익·승률·평균 보유일
    [CANDIDATE STATUS]  — 신호 후보 상태 집계 (SELECTED/COOLDOWN/EXPOSURE_LIMIT 등)
    [진단]              — 임계값 기반 자동 진단 메시지

데이터 소스:
    logs/swing_orders_YYYY-MM-DD.json  — SELL 주문 (reason/pnl_pct)
    logs/swing_runner.log              — CANDIDATE_RANK 라인

주의:
    2026-06-12 이전 SELL 기록의 reason은 'MA5_EXIT' 하드코딩 오염.
    pnl_pct 필드도 2026-06-12부터 저장됨 (이전은 추정값 사용).
    CANDIDATE_RANK 로그도 2026-06-12부터 기록됨.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

LOG_DIR = Path("logs")
DATA_CLEAN_DATE = date(2026, 6, 12)   # Exit reason + pnl_pct 정확화 날짜


# ── SELL 주문 수집 ──────────────────────────────────────────────────────────

def load_sell_orders(days: int) -> list[dict]:
    """swing_orders JSON에서 SELL 주문 수집. pnl_pct 없는 경우 추정."""
    cutoff = date.today() - timedelta(days=days)
    sells: list[dict] = []

    for path in sorted(LOG_DIR.glob("swing_orders_*.json")):
        try:
            raw = json.loads(path.read_text(encoding='utf-8'))
            run_date = date.fromisoformat(raw.get('run_date', ''))
        except Exception:
            continue
        if run_date < cutoff:
            continue

        for order in raw.get('orders', []):
            if order.get('action') != 'SELL':
                continue

            # pnl_pct: 2026-06-12부터 저장. 이전은 max_profit - drawdown으로 추정
            pnl = order.get('pnl_pct')
            if pnl is None:
                mp = order.get('max_profit_pct', 0.0) or 0.0
                dd = order.get('drawdown_pct', 0.0) or 0.0
                pnl = round(mp - dd, 2)

            sells.append({
                'date': run_date.isoformat(),
                'code': order.get('code', ''),
                'name': order.get('name', ''),
                'reason': order.get('reason', 'UNKNOWN'),
                'pnl_pct': pnl,
                'holding_days': order.get('holding_days', 0) or 0,
                'is_clean': run_date >= DATA_CLEAN_DATE,
            })

    return sells


# ── CANDIDATE_RANK 로그 파싱 ────────────────────────────────────────────────

_DATE_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})')
_RANK_RE = re.compile(
    r'#(\d+)\s+(\S+)\s+(.+?)\s{2,}score=([\d.]+)\s+'
    r'(SELECTED|COOLDOWN|SCORE_MISS|EXPOSURE_LIMIT|SECTOR_LIMIT|TOP_N_FULL)'
)
_CANDIDATE_TAG = '[SWING_CANDIDATE_RANK]'


def load_candidate_statuses(days: int) -> list[dict]:
    """swing_runner.log에서 CANDIDATE_RANK 라인 파싱."""
    log_path = LOG_DIR / "swing_runner.log"
    if not log_path.exists():
        return []

    cutoff = date.today() - timedelta(days=days)
    statuses: list[dict] = []
    current_date: date | None = None

    for line in log_path.read_text(encoding='utf-8').splitlines():
        dm = _DATE_RE.match(line)
        if dm:
            try:
                current_date = date.fromisoformat(dm.group(1))
            except Exception:
                pass

        if current_date is None or current_date < cutoff:
            continue
        if _CANDIDATE_TAG not in line or '===' in line:
            continue

        rm = _RANK_RE.search(line)
        if rm:
            statuses.append({
                'date': current_date.isoformat(),
                'rank': int(rm.group(1)),
                'code': rm.group(2),
                'name': rm.group(3).strip(),
                'score': float(rm.group(4)),
                'status': rm.group(5),
            })

    return statuses


# ── 리포트 테이블 ────────────────────────────────────────────────────────────

def _exit_table(sells: list[dict]) -> str:
    stats: dict[str, dict] = defaultdict(
        lambda: {'count': 0, 'pnl_sum': 0.0, 'wins': 0, 'hold_sum': 0, 'dirty': 0}
    )

    for s in sells:
        r = s['reason']
        stats[r]['count'] += 1
        stats[r]['pnl_sum'] += s['pnl_pct']
        stats[r]['hold_sum'] += s['holding_days']
        if s['pnl_pct'] > 0:
            stats[r]['wins'] += 1
        if not s['is_clean']:
            stats[r]['dirty'] += 1

    if not stats:
        return "  데이터 없음\n"

    header = f"  {'Reason':<20} {'N':>4}  {'Avg P&L':>8}  {'Win%':>5}  {'Avg Hold':>8}  {'오염¹':>5}"
    sep = "  " + "-" * 58
    lines = [header, sep]

    total = defaultdict(float)
    for reason, d in sorted(stats.items(), key=lambda x: -x[1]['count']):
        avg_pnl  = d['pnl_sum'] / d['count']
        win_pct  = d['wins'] / d['count'] * 100
        avg_hold = d['hold_sum'] / d['count']
        dirty_n  = d['dirty']
        lines.append(
            f"  {reason:<20} {d['count']:>4}  {avg_pnl:>+7.1f}%  {win_pct:>4.0f}%  "
            f"{avg_hold:>6.1f}일  {dirty_n:>5}"
        )
        for k in ('count', 'pnl_sum', 'wins', 'hold_sum'):
            total[k] += d[k]

    lines.append(sep)
    n = total['count']
    lines.append(
        f"  {'합계':<20} {int(n):>4}  {total['pnl_sum']/n:>+7.1f}%  "
        f"{total['wins']/n*100:>4.0f}%  {total['hold_sum']/n:>6.1f}일"
    )
    lines.append(f"  ¹ 오염 = {DATA_CLEAN_DATE} 이전 기록 (reason=MA5_EXIT 하드코딩)")
    return "\n".join(lines)


def _status_table(statuses: list[dict]) -> tuple[str, list[str]]:
    counter: dict[str, int] = defaultdict(int)
    for s in statuses:
        counter[s['status']] += 1

    total = sum(counter.values())
    if total == 0:
        return "  데이터 없음 (CANDIDATE_RANK 로그는 2026-06-12부터 기록)\n", []

    order = ['SELECTED', 'COOLDOWN', 'EXPOSURE_LIMIT', 'SECTOR_LIMIT', 'SCORE_MISS', 'TOP_N_FULL']
    header = f"  {'Status':<20} {'Count':>6}  {'%':>6}"
    sep = "  " + "-" * 36
    lines = [header, sep]

    for status in order:
        cnt = counter.get(status, 0)
        if cnt == 0:
            continue
        pct = cnt / total * 100
        lines.append(f"  {status:<20} {cnt:>6}  {pct:>5.1f}%")

    lines.append(sep)
    lines.append(f"  {'합계':<20} {total:>6}  100.0%")

    # 자동 진단
    diag: list[str] = []
    score_miss_pct   = counter.get('SCORE_MISS', 0) / total * 100
    exposure_pct     = counter.get('EXPOSURE_LIMIT', 0) / total * 100
    cooldown_pct     = counter.get('COOLDOWN', 0) / total * 100
    selected_pct     = counter.get('SELECTED', 0) / total * 100

    if score_miss_pct > 70:
        diag.append(f"⚠  SCORE_MISS {score_miss_pct:.0f}% → 진입 기준 과도하거나 유니버스 적음")
    if exposure_pct > 10:
        diag.append(f"⚠  EXPOSURE_LIMIT {exposure_pct:.0f}% → size 규칙이 분산 투자 제약")
    if cooldown_pct > 15:
        diag.append(f"⚠  COOLDOWN {cooldown_pct:.0f}% → 쿨다운 기간 과도하거나 반복 종목 집중")
    if selected_pct < 3 and total > 20:
        diag.append(f"⚠  SELECTED {selected_pct:.1f}% → 전체 신호 대비 진입 비율 매우 낮음")
    if not diag:
        diag.append("✅ 특이 패턴 없음")

    return "\n".join(lines), diag


def _ticker_cooldown_stats(statuses: list[dict]) -> str:
    """COOLDOWN 상태 종목별 빈도 (반복 차단 패턴 감지)."""
    cooldowns = [s for s in statuses if s['status'] == 'COOLDOWN']
    if not cooldowns:
        return "  없음"
    from collections import Counter
    cnt = Counter(f"{s['code']} {s['name']}" for s in cooldowns)
    lines = []
    for ticker, n in cnt.most_common(5):
        lines.append(f"  {ticker:<25} {n}회")
    return "\n".join(lines)


# ── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="스윙 전략 진단 리포트")
    parser.add_argument('--days', type=int, default=30)
    args = parser.parse_args()

    cutoff = date.today() - timedelta(days=args.days)

    print(f"\n{'='*62}")
    print(f"  Swing Strategy Report  ({cutoff} ~ {date.today()}, {args.days}일)")
    print(f"{'='*62}\n")

    sells    = load_sell_orders(args.days)
    statuses = load_candidate_statuses(args.days)

    # EXIT ATTRIBUTION
    clean_n = sum(1 for s in sells if s['is_clean'])
    print(f"[EXIT ATTRIBUTION]  총 {len(sells)}건  (clean: {clean_n}건 / 오염: {len(sells)-clean_n}건)")
    print(_exit_table(sells))

    # CANDIDATE STATUS
    print(f"\n[CANDIDATE STATUS]  총 {len(statuses)}건")
    table, diag = _status_table(statuses)
    print(table)

    # COOLDOWN 반복 종목
    print(f"\n[COOLDOWN 반복 종목 Top-5]")
    print(_ticker_cooldown_stats(statuses))

    # DIAGNOSTICS
    print(f"\n[진단]")
    for d in diag:
        print(f"  {d}")

    print()


if __name__ == '__main__':
    main()
