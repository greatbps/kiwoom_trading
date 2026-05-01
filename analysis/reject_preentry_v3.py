"""
reject_preentry_v3.py — 올바른 EXIT 포맷 기반

EXIT 형식:
   수익률: -2.18%
   실현손익: -1,200원
   사유: ...
"""

import re, os, glob
from datetime import datetime, timedelta
from collections import defaultdict

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")

TIME_RE   = re.compile(r'(\d{2}:\d{2}:\d{2})')
REJECT_RE = re.compile(r'REJECT (\d{6})')
# 매도수량 → 종목코드 찾기용 (매도수량 앞 라인에 종목코드 나옴)
SELL_CODE_RE = re.compile(r'(\d{6})[^\d].*매도|매도.*(\d{6})')
YIELD_RE  = re.compile(r'^\s*수익률:\s*([-+]?\d+\.\d+)%')
REASON_RE = re.compile(r'^\s*사유:')
QTY_RE    = re.compile(r'^\s*매도수량:')


def parse_file(filepath):
    date_m = re.search(r'(\d{8})', os.path.basename(filepath))
    if not date_m: return {}, {}
    log_date = date_m.group(1)

    rejects = defaultdict(list)  # code → [datetime]
    exits   = {}                 # code → (datetime, pct) 최초만

    cur_time = None
    cur_code = None
    pending_yield = None         # 수익률 라인 감지 후 사유 라인으로 확정

    lines = []
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()

    i = 0
    while i < len(lines):
        line = lines[i]

        tm = TIME_RE.search(line)
        if tm:
            try:
                cur_time = datetime.strptime(f"{log_date} {tm.group(1)}", "%Y%m%d %H:%M:%S")
            except: pass

        # 종목코드 추적 (REJECT, 매도 관련 라인)
        r = REJECT_RE.search(line)
        if r and cur_time:
            code = r.group(1)
            rejects[code].append(cur_time)
            i += 1; continue

        # 매도수량 라인 시작 → 앞뒤로 종목코드 + 수익률 파싱
        if QTY_RE.match(line):
            # 수익률은 다음 라인
            if i+1 < len(lines):
                y = YIELD_RE.match(lines[i+1])
                if y:
                    pending_yield = float(y.group(1))
                    # 종목코드: 이 블록 직전 로그에서 찾기 (역방향 5줄)
                    found_code = None
                    for back in range(max(0, i-10), i):
                        cm = re.search(r'\b(\d{6})\b', lines[back])
                        if cm:
                            found_code = cm.group(1)
                    if found_code and cur_time and found_code not in exits:
                        exits[found_code] = (cur_time, pending_yield)

        i += 1

    return rejects, exits


def analyze(log_files, window_min=30, min_rejects=2):
    results = []

    for fpath in sorted(log_files):
        rejects, exits = parse_file(fpath)

        for code, (exit_time, pct) in exits.items():
            entry_est    = exit_time - timedelta(minutes=20)
            window_start = entry_est - timedelta(minutes=window_min)

            rej_before = [t for t in rejects.get(code, [])
                          if window_start <= t <= entry_est]

            results.append({
                'date'   : exit_time.strftime('%Y-%m-%d'),
                'code'   : code,
                'pct'    : pct,
                'rej_cnt': len(rej_before),
                'flagged': len(rej_before) >= min_rejects,
                'is_loss': pct < 0,
            })

    return results


def report(results, min_rejects=2):
    if not results: print("결과 없음"); return

    total    = len(results)
    losses   = [r for r in results if r['is_loss']]
    wins     = [r for r in results if not r['is_loss']]
    flagged  = [r for r in results if r['flagged']]
    fl_loss  = [r for r in results if r['flagged'] and r['is_loss']]
    fl_win   = [r for r in results if r['flagged'] and not r['is_loss']]

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
    print()
    if flagged:
        print("─" * 62)
        print("  플래그 케이스 (손실 먼저):")
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
