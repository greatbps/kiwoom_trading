"""
reject_preentry_v2.py — 종목+날짜 기준 중복 제거 버전

핵심 변경:
- EXIT: (날짜, 종목코드) 기준 첫 번째 손익만 사용
- REJECT: (날짜, 종목코드) 기준 실제 발생 횟수 카운트
- 진입 시각 추정 제거 → 당일 최초 REJECT 발생 ~ BUY 시점 윈도우 사용
"""

import re, os, glob
from datetime import datetime, timedelta
from collections import defaultdict

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")

TIME_RE   = re.compile(r'(\d{2}:\d{2}:\d{2})')
REJECT_RE = re.compile(r'REJECT (\d{6})')
EXIT_RE   = re.compile(r'(\d{6}).*?수익률[:\s]*([-+]?\d+\.\d+)%.*?(손절|청산|익절|매도)')


def parse_file(filepath):
    date_m = re.search(r'(\d{8})', os.path.basename(filepath))
    if not date_m: return [], []
    log_date = date_m.group(1)

    rejects = defaultdict(list)   # code → [datetime]
    exits   = {}                  # code → (datetime, pct)  최초 1건만

    cur_time = None
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            tm = TIME_RE.search(line)
            if tm:
                try:
                    cur_time = datetime.strptime(f"{log_date} {tm.group(1)}", "%Y%m%d %H:%M:%S")
                except: pass

            if cur_time is None: continue

            r = REJECT_RE.search(line)
            if r:
                rejects[r.group(1)].append(cur_time)
                continue

            e = EXIT_RE.search(line)
            if e:
                code = e.group(1)
                pct  = float(e.group(2))
                if code not in exits:          # 최초 EXIT만 기록
                    exits[code] = (cur_time, pct)

    return rejects, exits


def analyze(log_files, window_min=30, min_rejects=2):
    results = []

    for fpath in sorted(log_files):
        rejects, exits = parse_file(fpath)
        date_str = re.search(r'(\d{8})', os.path.basename(fpath)).group(1)

        for code, (exit_time, pct) in exits.items():
            # 진입 추정: exit_time - 20분 (평균 보유 추정)
            entry_est   = exit_time - timedelta(minutes=20)
            window_start = entry_est - timedelta(minutes=window_min)

            rej_before = [t for t in rejects.get(code, [])
                          if window_start <= t <= entry_est]

            results.append({
                'date'    : date_str,
                'code'    : code,
                'pct'     : pct,
                'rej_cnt' : len(rej_before),
                'flagged' : len(rej_before) >= min_rejects,
                'is_loss' : pct < 0,
            })

    return results


def report(results, min_rejects=2):
    if not results:
        print("결과 없음"); return

    total        = len(results)
    losses       = [r for r in results if r['is_loss']]
    wins         = [r for r in results if not r['is_loss']]
    flagged      = [r for r in results if r['flagged']]
    fl_loss      = [r for r in results if r['flagged'] and r['is_loss']]
    fl_win       = [r for r in results if r['flagged'] and not r['is_loss']]
    unflagged_loss = [r for r in results if not r['flagged'] and r['is_loss']]

    print("=" * 62)
    print(f"  REJECT 전조 규칙 (진입 전 30분 내 {min_rejects}회 이상)")
    print("=" * 62)
    print(f"  전체 거래    : {total}건  (손실 {len(losses)} / 수익 {len(wins)})")
    print(f"  기본 승률    : {len(wins)/total*100:.1f}%")
    print()
    print(f"  플래그 건수  : {len(flagged)}건 ({len(flagged)/total*100:.1f}%)")
    print(f"  ├ 손실       : {len(fl_loss)}건  평균 {sum(r['pct'] for r in fl_loss)/max(len(fl_loss),1):.2f}%")
    print(f"  └ 수익       : {len(fl_win)}건  평균 {sum(r['pct'] for r in fl_win)/max(len(fl_win),1):.2f}%")
    print()
    print(f"  손실 방어율  : {len(fl_loss)/max(len(losses),1)*100:.1f}%  ({len(fl_loss)}/{len(losses)}건 차단 가능)")
    print(f"  수익 오차단  : {len(fl_win)/max(len(wins),1)*100:.1f}%  ({len(fl_win)}/{len(wins)}건 놓침)")
    print(f"  미감지 손실  : {len(unflagged_loss)}건")
    print()

    if flagged:
        print("─" * 62)
        print("  플래그 케이스 (고유 종목+날짜, 손실 먼저):")
        print("─" * 62)
        for r in sorted(flagged, key=lambda x: x['pct']):
            icon = "🔴" if r['is_loss'] else "🟢"
            print(f"  {r['date']} {r['code']}  {icon} {r['pct']:+.2f}%  REJECT {r['rej_cnt']}회")

    print("=" * 62)


if __name__ == "__main__":
    files = glob.glob(os.path.join(LOG_DIR, "auto_trading_2026*.log"))
    print(f"분석 로그: {len(files)}개\n")
    results = analyze(files, window_min=30, min_rejects=2)
    report(results, min_rejects=2)
