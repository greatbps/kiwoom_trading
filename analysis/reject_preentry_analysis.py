"""
analysis/reject_preentry_analysis.py

손실 거래 진입 전 30분 내 동일 종목 REJECT 2회 이상 → 손실 전조 규칙 검증

출력:
  - 전체 거래 중 "REJECT 2회 이상" 케이스
  - 해당 케이스의 손익 분포
  - 룰 적용 시 방어 가능한 손실 건수
"""

import re
import os
import glob
from datetime import datetime, timedelta
from collections import defaultdict

LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")

# 로그에서 타임스탬프 추출
TIME_PATTERN = re.compile(r'(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}|\d{2}:\d{2}:\d{2})')
REJECT_PATTERN = re.compile(r'REJECT (\d{6})')
BUY_PATTERN = re.compile(r'매수 주문 성공.*주문번호')
PROFIT_PATTERN = re.compile(r'(\d{6}).*수익률[:\s]*([-+]?\d+\.\d+)%')
EXIT_PATTERN = re.compile(r'(\d{6}).*?(손절|청산|매도완료|익절).*?([-+]?\d+\.\d+)%')

def parse_log_file(filepath):
    """로그 파일에서 (시각, 이벤트유형, 종목코드, 수익률) 파싱"""
    events = []
    filename = os.path.basename(filepath)
    date_str = re.search(r'(\d{8})', filename)
    log_date = date_str.group(1) if date_str else "20260101"

    current_time = None
    with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
        for line in f:
            # 타임스탬프 추출
            tm = TIME_PATTERN.search(line)
            if tm:
                t = tm.group(1)
                if len(t) == 8:  # HH:MM:SS
                    try:
                        current_time = datetime.strptime(f"{log_date} {t}", "%Y%m%d %H:%M:%S")
                    except:
                        pass
                else:
                    try:
                        current_time = datetime.strptime(t, "%Y-%m-%d %H:%M:%S")
                    except:
                        pass

            if current_time is None:
                continue

            # REJECT 이벤트
            r = REJECT_PATTERN.search(line)
            if r:
                events.append(('REJECT', current_time, r.group(1), None))
                continue

            # 매수 주문 성공 (종목코드는 앞 라인에서 추적 필요 → 간단히 근처 종목 매칭)
            if '매수 주문 성공' in line or '✓ 매수 주문 성공' in line:
                events.append(('BUY', current_time, None, None))
                continue

            # 손익 확정 (청산/손절)
            e = EXIT_PATTERN.search(line)
            if e:
                try:
                    pct = float(e.group(3))
                    events.append(('EXIT', current_time, e.group(1), pct))
                except:
                    pass

    return events


def analyze_reject_rule(log_files, window_min=30, min_rejects=2):
    """
    각 종목별 진입 전 window_min 이내 REJECT >= min_rejects 이면 경고
    → 실제 결과(손익)와 매칭
    """
    results = []

    for fpath in sorted(log_files):
        events = parse_log_file(fpath)

        # 종목별 REJECT 타임스탬프 수집
        reject_times = defaultdict(list)
        buy_times = []
        exits = []

        for etype, etime, code, pct in events:
            if etype == 'REJECT' and code:
                reject_times[code].append(etime)
            elif etype == 'BUY':
                buy_times.append(etime)
            elif etype == 'EXIT' and code and pct is not None:
                exits.append((etime, code, pct))

        # 각 EXIT에 대해: 진입 추정 시각(EXIT - 보유시간 추정) 기준 30분 전 REJECT 카운트
        for exit_time, code, pct in exits:
            # 진입 시각 = EXIT 시각 - 평균 보유(30분 추정)
            entry_est = exit_time - timedelta(minutes=30)
            window_start = entry_est - timedelta(minutes=window_min)

            rejects_before = [
                t for t in reject_times.get(code, [])
                if window_start <= t <= entry_est
            ]

            results.append({
                'date': exit_time.strftime('%Y-%m-%d'),
                'code': code,
                'exit_time': exit_time,
                'pct': pct,
                'reject_count': len(rejects_before),
                'flagged': len(rejects_before) >= min_rejects,
                'is_loss': pct < 0,
            })

    return results


def print_report(results):
    if not results:
        print("분석 결과 없음")
        return

    total = len(results)
    losses = [r for r in results if r['is_loss']]
    flagged = [r for r in results if r['flagged']]
    flagged_loss = [r for r in results if r['flagged'] and r['is_loss']]
    flagged_win  = [r for r in results if r['flagged'] and not r['is_loss']]

    print("=" * 60)
    print("  REJECT 전조 규칙 검증 (진입 전 30분 내 2회 이상)")
    print("=" * 60)
    print(f"\n전체 EXIT 건수    : {total}")
    print(f"손실 건수         : {len(losses)} ({len(losses)/total*100:.1f}%)")
    print(f"REJECT 2회+ 플래그: {len(flagged)} ({len(flagged)/total*100:.1f}%)")
    print()
    print(f"  플래그 → 손실   : {len(flagged_loss)} / {len(flagged)} ({len(flagged_loss)/max(len(flagged),1)*100:.1f}%)")
    print(f"  플래그 → 수익   : {len(flagged_win)} / {len(flagged)} ({len(flagged_win)/max(len(flagged),1)*100:.1f}%)")
    print()

    if flagged_loss:
        avg_loss = sum(r['pct'] for r in flagged_loss) / len(flagged_loss)
        print(f"  플래그 손실 평균 : {avg_loss:.2f}%")

    # 방어율: 전체 손실 중 플래그로 잡히는 비율
    defense_rate = len(flagged_loss) / max(len(losses), 1) * 100
    print(f"\n손실 방어율       : {defense_rate:.1f}% ({len(flagged_loss)}/{len(losses)}건)")

    # 오탐율: 플래그 중 수익인 비율 (차단 시 놓치는 수익)
    false_block = len(flagged_win) / max(len(flagged), 1) * 100
    print(f"수익 오차단율     : {false_block:.1f}% ({len(flagged_win)}/{len(flagged)}건)")

    print()
    print("─" * 60)
    print("플래그 케이스 상세 (손실 먼저):")
    print("─" * 60)
    for r in sorted(flagged, key=lambda x: x['pct']):
        flag = "🔴손실" if r['is_loss'] else "🟢수익"
        print(f"  {r['date']} {r['code']} {flag} {r['pct']:+.2f}%  REJECT {r['reject_count']}회")

    print("=" * 60)


if __name__ == "__main__":
    log_files = glob.glob(os.path.join(LOG_DIR, "auto_trading_2026*.log"))
    print(f"분석 대상 로그: {len(log_files)}개")

    results = analyze_reject_rule(log_files, window_min=30, min_rejects=2)
    print_report(results)
