"""
Work Instruction 7 — HTS 조건검색식 → Evidence → Ranking Attribution 검증

읽기전용 감사. 운영 코드 무수정, 백테스트 아님. 실제 운영 로그
(data/debug_log.txt, logs/auto_trading_YYYYMMDD.log)에서 Candidate/Evidence/Ranking
Attribution을 추출한다. 추정/재구성하지 않는다 — 로그에 없는 것은 N/A로 남긴다.

사용법: python -m phase1.candidate_attribution_v_wi7
"""
from __future__ import annotations

import ast
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT_DIR = os.path.join(ROOT, 'phase1', 'reports', 'candidate_attribution')
os.makedirs(OUT_DIR, exist_ok=True)

WINDOW_DAYS = ['20260728', '20260729', '20260730', '20260731',
               '20260803', '20260804', '20260805', '20260806', '20260807']
WINDOW_DAYS_SET = set(WINDOW_DAYS)

# config/strategy_hybrid.yaml condition_indices: [17,18,19,20,21,22] — 운영 설정에서
# 직접 확인(2026-08-11), 이름은 실제 로그(logs/auto_trading_20260728.log:215-225)에서
# 실측(임의 추정 아님).
CONDITIONS = {
    17: '알고리즘추출_1110',
    18: '종가매수_대박주식',
    19: '위전_종가',
    20: '240일 돌파',
    21: '240일 상위',
    22: '5일선',
}

MIN_SCORE_TO_SELECT = 2  # trading/score_engine.py MIN_SCORE
GRADE_SD_MIN = 50        # main_auto_trading.py:7729 min_supply_demand_for_a 기본값


# ── 1. HTS Candidate — data/debug_log.txt ───────────────────────────────────
COND_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2}) ([\d:.]+)\] 조건식 \[(\d+)\] '(.+?)' → (\d+)개 종목$")
CODES_RE = re.compile(r"^\s+종목코드: (\[.*\])$")


def parse_debug_log():
    path = os.path.join(ROOT, 'data', 'debug_log.txt')
    with open(path, encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    records = []
    for i, line in enumerate(lines):
        m = COND_RE.match(line.rstrip('\n'))
        if not m:
            continue
        date_s, time_s, idx_s, name, count_s = m.groups()
        date = date_s.replace('-', '')
        if date not in WINDOW_DAYS_SET:
            continue
        codes, truncated = None, False
        count = int(count_s)
        if count > 0 and i + 1 < len(lines):
            cm = CODES_RE.match(lines[i + 1].rstrip('\n'))
            if cm:
                try:
                    codes = ast.literal_eval(cm.group(1))
                    truncated = count > len(codes)
                except Exception:
                    codes = None
        records.append({
            'timestamp': f'{date_s} {time_s}', 'date': date,
            'condition_id': int(idx_s), 'condition_name': name,
            'count': count, 'codes': codes, 'truncated': truncated,
        })
    return pd.DataFrame(records)


def build_condition_summary(df_cycles):
    rows = []
    for cid, cname in CONDITIONS.items():
        sub = df_cycles[df_cycles['condition_id'] == cid]
        total_occ = int(sub['count'].sum())
        active_sub = sub[sub['count'] > 0]
        active_days = active_sub['date'].nunique()
        seen = set()
        for codes in active_sub['codes']:
            if codes:
                seen.update(codes)
        n_trunc = int(sub['truncated'].sum())
        rows.append({
            'condition_id': cid, 'condition_name': cname,
            'candidate_count_raw_occurrences': total_occ,
            'unique_symbol_count_partial': len(seen),
            'active_day_count': int(active_days),
            'total_days_in_window': len(WINDOW_DAYS),
            'zero_activity': bool(total_occ == 0),
            'first_seen': active_sub['timestamp'].min() if len(active_sub) else None,
            'last_seen': active_sub['timestamp'].max() if len(active_sub) else None,
            'total_cycles_observed': len(sub),
            'truncated_cycles': n_trunc,
            'coverage_note': (
                f'{n_trunc}개 사이클에서 종목코드가 5개 초과 발생 — 로그가 상위 5개만 '
                f'기록해 unique_symbol_count는 하한값(실제보다 적거나 같음)'
                if n_trunc > 0 else '전체 캡처(5개 초과 사이클 없음, unique_symbol_count 정확)'
            ),
        })
    return pd.DataFrame(rows)


def build_candidate_and_overlap(df_cycles):
    symbol_conditions = defaultdict(lambda: defaultdict(list))
    for _, r in df_cycles[df_cycles['count'] > 0].iterrows():
        if not r['codes']:
            continue
        for sym in r['codes']:
            symbol_conditions[sym][r['condition_id']].append(r['timestamp'])

    cand_rows, overlap_rows = [], []
    for sym, conds in symbol_conditions.items():
        cond_ids = sorted(conds.keys())
        all_ts = [t for lst in conds.values() for t in lst]
        cand_rows.append({
            'symbol': sym,
            'condition_ids': ','.join(str(c) for c in cond_ids),
            'condition_names': ','.join(CONDITIONS[c] for c in cond_ids),
            'first_seen': min(all_ts), 'last_seen': max(all_ts),
            'duplicate_condition_count': len(cond_ids),
            'candidate_source': 'HTS_조건검색(data/debug_log.txt, 5개초과 사이클은 부분캡처)',
        })
        if len(cond_ids) > 1:
            for cid in cond_ids:
                overlap_rows.append({
                    'symbol': sym, 'condition_id': cid, 'condition_name': CONDITIONS[cid],
                    'total_conditions_matched': len(cond_ids),
                    'all_condition_ids': ','.join(str(c) for c in cond_ids),
                })
    return pd.DataFrame(cand_rows), pd.DataFrame(overlap_rows)


# ── 2. Evidence Attribution — logs/auto_trading_YYYYMMDD.log ────────────────
ANALYZE_START_RE = re.compile(r"^분석 중: (.+?) \((\d{6})\)$")
FINAL_SCORE_RE = re.compile(r"^최종 점수: ([\d.]+)/100$")
RECO_RE = re.compile(r"^투자 추천: (.+)$")
SUMMARY_RE = re.compile(r"뉴스: (\d+) \| 기술: (\d+) \| 수급: (\d+) \| 기본: (\d+)")


def parse_evidence_logs():
    rows = []
    for day in WINDOW_DAYS:
        path = os.path.join(ROOT, 'logs', f'auto_trading_{day}.log')
        if not os.path.exists(path):
            continue
        cur_sym = cur_name = cur_final = cur_reco = None
        with open(path, encoding='utf-8', errors='ignore') as f:
            for line in f:
                s = line.rstrip('\n')
                m = ANALYZE_START_RE.match(s.strip())
                if m:
                    cur_name, cur_sym = m.group(1), m.group(2)
                    cur_final = cur_reco = None
                    continue
                m2 = FINAL_SCORE_RE.match(s.strip())
                if m2:
                    cur_final = float(m2.group(1))
                    continue
                m3 = RECO_RE.match(s.strip())
                if m3:
                    cur_reco = m3.group(1)
                    continue
                m4 = SUMMARY_RE.search(s)
                if m4 and cur_sym:
                    rows.append({
                        'date': day, 'symbol': cur_sym, 'name': cur_name,
                        'news_score': int(m4.group(1)), 'technical_score': int(m4.group(2)),
                        'supply_demand_score': int(m4.group(3)),
                        'fundamental_score': int(m4.group(4)),
                        'final_score': cur_final, 'recommendation': cur_reco,
                        'sd_score_below_grade_threshold':
                            int(m4.group(3)) < GRADE_SD_MIN,
                    })
    return pd.DataFrame(rows)


# ── 3. Ranking Attribution — [SCORE_INPUT]/[SCORE_DETAIL] 로그 ──────────────
SCORE_INPUT_RE = re.compile(r"\[SCORE_INPUT\]")
SCORE_DETAIL_RE = re.compile(
    r"\[SCORE_DETAIL\] (\d{6}) volume=(\d+) ma50=(\d+) smc=([\d.]+) total=([\d.]+)")


def parse_ranking_logs():
    rows = []
    for day in WINDOW_DAYS:
        path = os.path.join(ROOT, 'logs', f'auto_trading_{day}.log')
        if not os.path.exists(path):
            continue
        cur_cycle_ts = None
        with open(path, encoding='utf-8', errors='ignore') as f:
            for line in f:
                if SCORE_INPUT_RE.search(line):
                    cur_cycle_ts = line.split(' - ')[0].strip()
                    continue
                m = SCORE_DETAIL_RE.search(line)
                if m and cur_cycle_ts:
                    rows.append({
                        'date': day, 'cycle_ts': cur_cycle_ts, 'symbol': m.group(1),
                        'volume': int(m.group(2)), 'ma50': int(m.group(3)),
                        'smc': float(m.group(4)), 'total': float(m.group(5)),
                    })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    out = []
    for cycle_ts, g in df.groupby('cycle_ts'):
        ranked = g.sort_values(['total', 'symbol'], ascending=[False, True]).reset_index(drop=True)
        for rank, r in ranked.iterrows():
            out.append({
                'date': r['date'], 'cycle_ts': cycle_ts, 'symbol': r['symbol'],
                'total_score': r['total'], 'rank': rank + 1,
                'selected': bool(r['total'] >= MIN_SCORE_TO_SELECT),
            })
    return pd.DataFrame(out)


def check_ranking_determinism(df_ranking):
    """동일 cycle 내 정렬이 Score DESC / Symbol ASC 규칙을 어기지 않는지 재확인
    (watchlist_rank_key와 동일 규칙, WI3 test_ranking_determinism.py와 별개의
    실측 로그 기반 교차검증)."""
    violations = []
    for cycle_ts, g in df_ranking.groupby('cycle_ts'):
        g = g.sort_values('rank')
        prev = None
        for _, r in g.iterrows():
            if prev is not None:
                ok = (r['total_score'] < prev['total_score']) or (
                    r['total_score'] == prev['total_score'] and r['symbol'] > prev['symbol'])
                if not ok:
                    violations.append({'cycle_ts': cycle_ts, 'symbol': r['symbol']})
            prev = r
    return violations


def main():
    print('=' * 78)
    print('  WI-7 HTS 조건검색 → Evidence → Ranking Attribution 검증')
    print('=' * 78)
    print(f'  대상 조건식: {CONDITIONS}')
    print(f'  검증기간: 2026-07-28 ~ 2026-08-07 ({len(WINDOW_DAYS)}거래일)')

    print('\n[1] HTS Candidate 추출 (data/debug_log.txt)...')
    df_cycles = parse_debug_log()
    print(f'  조건식-사이클 관측 {len(df_cycles)}건')
    cond_summary = build_condition_summary(df_cycles)
    cand_df, overlap_df = build_candidate_and_overlap(df_cycles)
    print(f'  고유 후보(부분캡처) {len(cand_df)}건, 중복(2개+ 조건식) {cand_df["duplicate_condition_count"].gt(1).sum()}건')

    print('\n[2] Evidence Attribution 추출 (logs/auto_trading_*.log)...')
    evidence_df = parse_evidence_logs()
    print(f'  분석 레코드 {len(evidence_df)}건')
    if len(evidence_df):
        sd_below = evidence_df['sd_score_below_grade_threshold'].mean() * 100
        print(f'  실제 수급점수 < {GRADE_SD_MIN}(A→B 강등 기준): {sd_below:.1f}%')

    print('\n[3] Ranking Attribution 추출 ([SCORE_INPUT]/[SCORE_DETAIL] 로그)...')
    ranking_df = parse_ranking_logs()
    print(f'  랭킹 레코드 {len(ranking_df)}건 ({ranking_df["cycle_ts"].nunique() if len(ranking_df) else 0} 사이클)')
    violations = check_ranking_determinism(ranking_df) if len(ranking_df) else []
    print(f'  결정성 위반: {len(violations)}건')

    # ── 저장 ─────────────────────────────────────────────────────────────
    cond_summary.to_csv(os.path.join(OUT_DIR, 'condition_candidate_summary.csv'), index=False)
    evidence_df.to_csv(os.path.join(OUT_DIR, 'candidate_evidence_attribution.csv'), index=False)
    ranking_df.to_csv(os.path.join(OUT_DIR, 'ranking_attribution.csv'), index=False)
    overlap_df.to_csv(os.path.join(OUT_DIR, 'condition_overlap.csv'), index=False)
    cand_df.to_csv(os.path.join(OUT_DIR, 'candidate_symbol_attribution.csv'), index=False)

    with open(os.path.join(OUT_DIR, 'ranking_determinism_violations.json'), 'w') as f:
        json.dump(violations, f, ensure_ascii=False, indent=2, default=str)

    print('\n완료.')
    return {
        'cond_summary': cond_summary, 'cand_df': cand_df, 'overlap_df': overlap_df,
        'evidence_df': evidence_df, 'ranking_df': ranking_df, 'violations': violations,
    }


if __name__ == '__main__':
    main()
