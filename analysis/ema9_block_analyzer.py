"""
analysis/ema9_block_analyzer.py — EMA9 Block 필터 효율 분석

사용법:
    python -m analysis.ema9_block_analyzer            # 오늘 로그
    python -m analysis.ema9_block_analyzer --date 20260403
    python -m analysis.ema9_block_analyzer --days 3   # 최근 3일 합산

출력:
    - 전체 차단 성능 (하락 비율 → 유지/경계/과필터 판정)
    - grade별 분리 (STRONG / NORMAL)
    - 평균 상승률 / 하락률
    - +2% 이상 놓침 목록
    - 최종 권고 (유지 / EMA5 완화 / A+급 bypass)
"""

import re
import glob
import argparse
from datetime import datetime, timedelta
from collections import defaultdict


# ─── 파싱 패턴 ──────────────────────────────────────────────────────────────

# [EMA9_BLOCK_RESULT] 120110 코오롱인더: 블록후17분 | 81900→83200 (+1.59%) | 결과=상승✅ | grade=NORMAL
_RESULT_RE = re.compile(
    r'\[EMA9_BLOCK_RESULT\]\s+(\S+)\s+([^:]+):\s+'
    r'블록후(\d+)분\s*\|\s*[\d.]+→([\d.]+)\s*\(([+-][\d.]+)%\)\s*\|\s*결과=(\S+)\s*\|\s*grade=(\S+)'
)

# [MISSED_BY_EMA9] 120110 코오롱인더: price=81900 grade=NORMAL → 15분 추적 시작
_MISSED_RE = re.compile(
    r'\[MISSED_BY_EMA9\]\s+(\S+)\s+([^:]+):\s+price=([\d.]+)\s+grade=(\S+)'
)

# [TREND_EMA9_BLOCK] TREND: EMA9 하단 (81900 < 82150) ...
_BLOCK_RE = re.compile(r'\[TREND_EMA9_BLOCK\]')


def parse_log(filepath: str) -> dict:
    """로그 파일 파싱 → 결과 dict 반환."""
    results = []
    missed_count = 0
    block_count = 0

    with open(filepath, 'r', errors='ignore') as f:
        for line in f:
            # 블록 발생 카운트
            if _BLOCK_RE.search(line):
                block_count += 1

            # 추적 시작 카운트
            if _MISSED_RE.search(line):
                missed_count += 1

            # 결과 파싱
            m = _RESULT_RE.search(line)
            if m:
                code, name, elapsed_m, cur_px, chg_pct, outcome, grade = m.groups()
                results.append({
                    'code': code,
                    'name': name.strip(),
                    'elapsed_m': int(elapsed_m),
                    'cur_price': float(cur_px),
                    'chg_pct': float(chg_pct),
                    'outcome': outcome,  # 상승✅ / 횡보 / 하락❌
                    'grade': grade,
                    'is_up': float(chg_pct) >= 0.5,
                    'is_big_miss': float(chg_pct) >= 2.0,
                })

    return {
        'results': results,
        'missed_count': missed_count,
        'block_count': block_count,
    }


def analyze(data_list: list[dict]) -> dict:
    """여러 날 파싱 결과 합산 분석."""
    all_results = []
    total_blocks = 0
    total_missed = 0

    for d in data_list:
        all_results.extend(d['results'])
        total_blocks += d['block_count']
        total_missed += d['missed_count']

    if not all_results:
        return {'total_blocks': total_blocks, 'total_missed': total_missed, 'results': []}

    n = len(all_results)
    up_list   = [r for r in all_results if r['is_up']]
    down_list = [r for r in all_results if not r['is_up']]
    big_miss  = [r for r in all_results if r['is_big_miss']]

    up_pct   = len(up_list)   / n * 100
    down_pct = len(down_list) / n * 100

    avg_up   = sum(r['chg_pct'] for r in up_list)   / len(up_list)   if up_list   else 0.0
    avg_down = sum(r['chg_pct'] for r in down_list) / len(down_list) if down_list else 0.0

    # Grade별 분리
    by_grade = defaultdict(list)
    for r in all_results:
        by_grade[r['grade']].append(r)

    # 최종 판정
    if down_pct >= 65:
        verdict = "✅ 유지 (필터 유효: 막힌 것 중 {:.0f}%가 실패 구간)".format(down_pct)
        action  = "KEEP"
    elif down_pct >= 50:
        verdict = "⚠️  경계 (데이터 2~3일 더 필요, 하락률 {:.0f}%)".format(down_pct)
        action  = "WATCH"
    else:
        verdict = "❌ 과필터 의심 (막은 것 중 {:.0f}%가 상승 → 완화 검토)".format(up_pct)
        action  = "LOOSEN"

    return {
        'total_blocks': total_blocks,
        'total_missed': total_missed,
        'total_evaluated': n,
        'up_count': len(up_list),
        'down_count': len(down_list),
        'up_pct': up_pct,
        'down_pct': down_pct,
        'avg_up': avg_up,
        'avg_down': avg_down,
        'big_miss': big_miss,
        'by_grade': dict(by_grade),
        'verdict': verdict,
        'action': action,
        'results': all_results,
    }


def print_report(stats: dict):
    """분석 결과 출력."""
    SEP = "=" * 65

    print()
    print(SEP)
    print("  EMA9 Block Filter 효율 분석")
    print(SEP)

    if not stats.get('results'):
        print(f"\n  [EMA9_BLOCK_RESULT] 로그 없음")
        print(f"  총 차단 이벤트: {stats['total_blocks']}건")
        print(f"  MISSED_BY_EMA9 추적: {stats['total_missed']}건")
        print(f"\n  → 데이터 축적 후 재실행 필요")
        print(SEP)
        return

    n = stats['total_evaluated']
    print(f"\n  총 차단: {stats['total_blocks']}건 | 추적완료: {n}건 | 미완료: {stats['total_missed'] - n}건")

    # ── 전체 성능 ──────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"  [전체 성능]")
    print(f"{'─'*65}")
    bar_d = '█' * int(stats['down_pct'] / 5)
    bar_u = '░' * int(stats['up_pct']   / 5)
    print(f"  하락❌  {stats['down_count']:3d}건  {stats['down_pct']:5.1f}%  {bar_d}")
    print(f"  상승✅  {stats['up_count']:3d}건  {stats['up_pct']:5.1f}%  {bar_u}")
    print(f"\n  평균 하락: {stats['avg_down']:+.2f}%  |  평균 상승: {stats['avg_up']:+.2f}%")

    # ── 판정 ────────────────────────────────────────────────────────────────
    print(f"\n  {'─'*63}")
    print(f"  판정: {stats['verdict']}")
    print(f"  {'─'*63}")

    # ── Grade별 분리 ────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"  [Grade별 분리]")
    print(f"{'─'*65}")
    for grade in ['STRONG', 'NORMAL', 'WEAK']:
        rows = stats['by_grade'].get(grade, [])
        if not rows:
            continue
        g_up   = sum(1 for r in rows if r['is_up'])
        g_down = len(rows) - g_up
        g_up_pct = g_up / len(rows) * 100
        g_avg_up   = sum(r['chg_pct'] for r in rows if r['is_up'])   / g_up   if g_up   else 0
        g_avg_down = sum(r['chg_pct'] for r in rows if not r['is_up']) / g_down if g_down else 0

        if g_up_pct >= 50:
            grade_note = "⚠️  bypass 검토"
        else:
            grade_note = "✅ 필터 유효"
        print(f"\n  {grade:8s}: {len(rows)}건 | 상승 {g_up_pct:.0f}% | 하락 {100-g_up_pct:.0f}%  {grade_note}")
        print(f"           avg_up={g_avg_up:+.2f}%  avg_down={g_avg_down:+.2f}%")

    # ── +2% 이상 놓침 ───────────────────────────────────────────────────────
    big = stats['big_miss']
    if big:
        print(f"\n{'─'*65}")
        print(f"  [+2% 이상 놓침 ({len(big)}건)] ← 진짜 아까운 케이스")
        print(f"{'─'*65}")
        for r in sorted(big, key=lambda x: -x['chg_pct']):
            print(f"  {r['code']} {r['name'][:10]:10s} | {r['chg_pct']:+.2f}% | grade={r['grade']}")
    else:
        print(f"\n  +2% 이상 놓침: 없음 ✅")

    # ── 권고 액션 ────────────────────────────────────────────────────────────
    print(f"\n{'─'*65}")
    print(f"  [다음 단계 권고]")
    print(f"{'─'*65}")

    action = stats['action']
    strong_rows = stats['by_grade'].get('STRONG', [])
    strong_up_pct = sum(1 for r in strong_rows if r['is_up']) / len(strong_rows) * 100 if strong_rows else 0

    if action == 'KEEP':
        print(f"  ✅ EMA9 필터 유지")
        if strong_up_pct >= 50 and strong_rows:
            print(f"  ⚠️  STRONG 등급 상승 {strong_up_pct:.0f}% → STRONG만 EMA9 bypass 검토")
            print(f"      YAML: ema9_filter.bypass_grades: [STRONG]")
    elif action == 'WATCH':
        print(f"  ⏳ 2~3일 더 데이터 수집 후 재판정")
        print(f"  현재는 유지 권고")
    else:  # LOOSEN
        print(f"  ❌ 과필터 — 완화 옵션:")
        print(f"     A. EMA9 → EMA5로 완화 (YAML: ema9_filter.period: 5)")
        print(f"     B. STRONG 등급 bypass (YAML: ema9_filter.bypass_grades: [STRONG])")

    print(SEP)
    print()


def main():
    parser = argparse.ArgumentParser(description='EMA9 Block 필터 효율 분석')
    parser.add_argument('--date', type=str, default=None, help='분석 날짜 (YYYYMMDD)')
    parser.add_argument('--days', type=int, default=1,   help='최근 N일 합산')
    args = parser.parse_args()

    import os
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    log_dir = os.path.join(base, 'logs')

    if args.date:
        dates = [args.date]
    else:
        today = datetime.now()
        dates = [(today - timedelta(days=i)).strftime('%Y%m%d') for i in range(args.days)]

    data_list = []
    for d in dates:
        path = os.path.join(log_dir, f'auto_trading_{d}.log')
        if os.path.exists(path):
            print(f"  로그 파싱: {path}")
            data_list.append(parse_log(path))
        else:
            print(f"  로그 없음: {path}")

    stats = analyze(data_list)
    print_report(stats)


if __name__ == '__main__':
    main()
