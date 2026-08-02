"""
analysis/memprof_report.py

main_auto_trading.py 프로세스의 메모리/리소스 실측 데이터 분석 (Production Audit v2.1)

입력: logs/profiling/memprof_YYYYMMDD.csv
      (5분 간격 샘플러가 기록 — ts,pid,rss_kb,vsz_kb,threads,fds,db_conn,cpu_pct,uptime_s)

누수 판정 방법:
  기동 직후는 캐시/커넥션 워밍업으로 RSS가 자연 증가하므로, 전체 구간 기울기로
  판정하면 워밍업을 누수로 오판한다. 따라서 **워밍업 구간(기본 60분)을 제외한
  안정 구간의 기울기**로 판정한다.

  LEAK_SUSPECTED : 안정구간 +20MB/h 이상 단조 증가
  WATCH          : 안정구간 +5~20MB/h
  STABLE         : 안정구간 +5MB/h 미만

실행:
    python3 -m analysis.memprof_report
    python3 -m analysis.memprof_report --date 20260728 --warmup-min 60
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from pathlib import Path
from typing import List, Dict

BASE = Path(__file__).parent.parent
PROF_DIR = BASE / 'logs' / 'profiling'

LEAK_MB_PER_H = 20.0
WATCH_MB_PER_H = 5.0


def load_samples(target: str) -> List[Dict]:
    path = PROF_DIR / f'memprof_{target}.csv'
    if not path.exists():
        print(f'[ERROR] 샘플 파일 없음: {path}')
        sys.exit(2)
    rows = []
    with open(path, newline='', encoding='utf-8') as f:
        for r in csv.DictReader(f):
            if not r.get('rss_kb'):
                continue          # NOPROC 행 (프로세스 미실행) 건너뜀
            try:
                r['rss_mb'] = int(r['rss_kb']) / 1024
                r['up_min'] = int(r['uptime_s']) / 60
                rows.append(r)
            except (ValueError, TypeError):
                continue
    return rows


def _slope_mb_per_h(rows: List[Dict]) -> float:
    """최소제곱 기울기 (MB/시간). 단순 양끝 차분보다 노이즈에 강하다."""
    n = len(rows)
    if n < 2:
        return 0.0
    xs = [r['up_min'] / 60 for r in rows]      # 시간 단위
    ys = [r['rss_mb'] for r in rows]
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def analyze(rows: List[Dict], warmup_min: float) -> Dict:
    if not rows:
        return {'status': 'NO_DATA'}

    stable = [r for r in rows if r['up_min'] >= warmup_min]
    first, last = rows[0], rows[-1]
    span_min = last['up_min'] - first['up_min']

    overall = _slope_mb_per_h(rows)
    stable_slope = _slope_mb_per_h(stable) if len(stable) >= 3 else None

    # 판정은 안정 구간 기준. 샘플이 부족하면 판정 보류.
    if stable_slope is None:
        status = 'INSUFFICIENT_STABLE_SAMPLES'
    elif stable_slope >= LEAK_MB_PER_H:
        status = 'LEAK_SUSPECTED'
    elif stable_slope >= WATCH_MB_PER_H:
        status = 'WATCH'
    else:
        status = 'STABLE'

    # 리소스 핸들 추이 (누수 신호가 RSS보다 먼저 나타나는 경우가 많다)
    def _rng(key):
        vals = [int(r[key]) for r in rows if r.get(key, '').isdigit()]
        return (min(vals), max(vals), vals[-1]) if vals else (0, 0, 0)

    return {
        'status':        status,
        'n_samples':     len(rows),
        'n_stable':      len(stable),
        'span_min':      span_min,
        'warmup_min':    warmup_min,
        'rss_first':     first['rss_mb'],
        'rss_last':      last['rss_mb'],
        'rss_peak':      max(r['rss_mb'] for r in rows),
        'overall_slope': overall,
        'stable_slope':  stable_slope,
        'ts_first':      first['ts'],
        'ts_last':       last['ts'],
        'threads':       _rng('threads'),
        'fds':           _rng('fds'),
        'db_conn':       _rng('db_conn'),
        'restarts':      len({r['pid'] for r in rows}) - 1,
    }


def print_report(a: Dict, target: str) -> None:
    print()
    print('=' * 60)
    print(f'  Memory / Resource Profiling Report — {target}')
    print('=' * 60)

    if a['status'] == 'NO_DATA':
        print('\n  샘플 없음\n')
        return

    print(f"\n  구간      : {a['ts_first']} → {a['ts_last']}  ({a['span_min']:.0f}분)")
    print(f"  샘플      : {a['n_samples']}개 (안정구간 {a['n_stable']}개, 워밍업 {a['warmup_min']:.0f}분 제외)")
    if a['restarts']:
        print(f"  ⚠️ 프로세스 재시작 {a['restarts']}회 감지 — 구간별 해석 주의")

    print(f"\n  RSS       : {a['rss_first']:.0f}MB → {a['rss_last']:.0f}MB  (peak {a['rss_peak']:.0f}MB)")
    print(f"  전체 기울기      : {a['overall_slope']:+.1f} MB/시간  (워밍업 포함 — 판정용 아님)")
    if a['stable_slope'] is None:
        print(f"  안정구간 기울기  : 샘플 부족")
    else:
        print(f"  안정구간 기울기  : {a['stable_slope']:+.1f} MB/시간   ← 판정 기준")

    t, f, d = a['threads'], a['fds'], a['db_conn']
    print(f"\n  Threads   : min {t[0]} / max {t[1]} / last {t[2]}")
    print(f"  FD        : min {f[0]} / max {f[1]} / last {f[2]}")
    print(f"  DB conn   : min {d[0]} / max {d[1]} / last {d[2]}")

    print()
    verdict = {
        'STABLE':                      '✅ STABLE — 메모리 누수 징후 없음',
        'WATCH':                       '🟡 WATCH — 완만한 증가, 다음 거래일 재확인 권장',
        'LEAK_SUSPECTED':              '🔴 LEAK_SUSPECTED — 누수 의심, 조사 필요',
        'INSUFFICIENT_STABLE_SAMPLES': '⚪ 판정 보류 — 안정 구간 샘플 부족(3개 이상 필요)',
    }[a['status']]
    print(f"  STATUS : {verdict}")

    # 핸들 누수는 RSS보다 먼저 드러나는 경우가 많아 별도 경고
    if f[2] > f[0] * 2 and f[2] >= 30:
        print(f"  ⚠️ FD가 초기 대비 2배 이상({f[0]}→{f[2]}) — 소켓/파일 미해제 가능성 확인 필요")
    if t[2] > t[0] + 5:
        print(f"  ⚠️ Thread가 {t[0]}→{t[2]}로 증가 — 스레드 누수 가능성 확인 필요")
    print()


def main():
    p = argparse.ArgumentParser(description='메모리/리소스 프로파일 분석')
    p.add_argument('--date', default=date.today().strftime('%Y%m%d'), help='YYYYMMDD')
    p.add_argument('--warmup-min', type=float, default=60.0,
                   help='워밍업으로 간주해 판정에서 제외할 기동 후 분 (기본 60)')
    args = p.parse_args()

    rows = load_samples(args.date)
    a = analyze(rows, args.warmup_min)
    print_report(a, args.date)
    sys.exit(0 if a['status'] in ('STABLE', 'WATCH', 'INSUFFICIENT_STABLE_SAMPLES') else 1)


if __name__ == '__main__':
    main()
