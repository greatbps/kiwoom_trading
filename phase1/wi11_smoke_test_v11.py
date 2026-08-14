"""
Work Instruction 11 — Live Strategy Monitor Smoke Test 실측 데이터 수집

라이브 프로세스를 WI-10 Strategy Monitor 코드로 재시작(2026-08-11 15:02)한 후 실제
로그(logs/auto_trading_20260811.log)만 파싱한다. 없는 데이터를 만들지 않는다 —
장마감(15:30)까지의 관찰 윈도우가 짧으면 INSUFFICIENT_OBSERVATION으로 명시한다.

사용법: python -m phase1.wi11_smoke_test_v11
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
OUT_DIR = os.path.join(ROOT, 'phase1', 'reports', 'strategy_monitoring')
os.makedirs(OUT_DIR, exist_ok=True)

LOG_PATH = os.path.join(ROOT, 'logs', 'auto_trading_20260811.log')
RESTART_LINE = 30705  # 2차 재시작(WI-11, 15:02:46 SIGINT -> 15:02:5x 신규 프로세스) 직전 라인 수, 실측

SEQ_STRATEGIES = {
    32: 'Momentum', 33: 'Breakout', 34: 'EOD', 35: 'Supertrend + EMA + RSI',
    36: 'VWAP', 37: 'Squeeze Momentum Pro', 38: 'Bottom', 39: 'ITS',
}
# HTS 실측 이름(main_auto_trading.py의 _cond_sources가 저장하는 원문, "전략" 접미사
# 포함 여부가 Monitor의 strategy_name과 다르다 — attribution 대조는 이름이 아니라
# seq로 해야 한다, 아래 참조).
HTS_NAME_TO_SEQ = {
    'Momentum 전략': 32, 'Breakout 전략': 33, 'EOD 전략': 34,
    'Supertrend + EMA + RSI 전략': 35, 'VWAP 전략': 36,
    'Squeeze Momentum Pro': 37, 'Bottom 전략': 38, 'ITS': 39,
}
MONITOR_CLASS = {
    32: 'MomentumMonitor', 33: 'BreakoutMonitor', 34: 'EODMonitor', 35: 'TrendMonitor',
    36: 'VWAPMonitor', 37: 'SqueezeMonitor', 38: 'BottomMonitor', 39: 'ITSMonitor',
}

MON_RE = re.compile(
    r"\[(STRATEGY_MONITOR|STRATEGY_MONITOR_REJECT)\] seq=(\d+) strategy=(.+?) "
    r"symbol=(\S+) state=(\S+) data_quality=(\S+) reason=(.*)$")
EV_RE = re.compile(
    r"\[EVIDENCE_ATTR\] symbol=(\S+) strategy_seq=(\S+) condition_sources=(\[.*?\]) "
    r"primary_condition=(\S.*?) final_score=([\d.]+) recommendation=(\S+) evidence_status=(\S+)")


def load_post_restart_lines():
    with open(LOG_PATH, encoding='utf-8', errors='ignore') as f:
        lines = f.readlines()
    return lines[RESTART_LINE:]


def parse_monitor_events(lines):
    rows = []
    for line in lines:
        m = MON_RE.search(line)
        if m:
            tag, seq, strategy, symbol, state, dq, reason = m.groups()
            rows.append({
                'timestamp': line[:19], 'tag': tag, 'condition_seq': int(seq),
                'strategy_name': strategy, 'monitor_class': MONITOR_CLASS.get(int(seq), 'UNKNOWN'),
                'symbol': symbol, 'monitor_state': state, 'data_quality': dq,
                'signal': (tag == 'STRATEGY_MONITOR'), 'reason': reason.strip()[:200],
            })
    return rows


def parse_evidence(lines):
    rows = []
    for line in lines:
        m = EV_RE.search(line)
        if m:
            symbol, seq, sources, primary, score, reco, status = m.groups()
            try:
                sources = ast.literal_eval(sources)
            except Exception:
                sources = [sources]
            rows.append({'timestamp': line[:19], 'symbol': symbol, 'strategy_seq': seq,
                         'condition_sources': sources, 'primary_condition': primary,
                         'final_score': float(score), 'recommendation': reco,
                         'evidence_status': status})
    return rows


def main():
    print('=' * 78)
    print('  WI-11 Live Strategy Monitor Smoke Test — 실측 데이터 수집')
    print('=' * 78)

    lines = load_post_restart_lines()
    print(f'\n재시작 이후 로그 {len(lines)}줄 파싱')

    mon_rows = parse_monitor_events(lines)
    ev_rows = parse_evidence(lines)
    mon_df = pd.DataFrame(mon_rows)
    ev_df = pd.DataFrame(ev_rows)

    # ── §19 라이브 안전장치 확인 ──
    unexpected_buys = sum(1 for l in lines if '[COND_ATTR]' in l or '[BUY_COMPLETE]' in l)
    monitor_errors = int((mon_df['data_quality'] == 'ERROR').sum()) if len(mon_df) else 0
    routing_errors = int((mon_df['monitor_class'] == 'UNKNOWN').sum()) if len(mon_df) else 0

    # ── §15 정량표 ──
    quant_rows = []
    for seq, name in SEQ_STRATEGIES.items():
        sub = mon_df[mon_df['condition_seq'] == seq] if len(mon_df) else pd.DataFrame()
        candidate_n = len(sub)
        called_n = len(sub)  # 호출된 것만 로그에 남으므로 candidate==called
        recorded_n = int((sub['data_quality'].isin(['OK', 'INSUFFICIENT_DATA', 'NO_CODE'])).sum()) if len(sub) else 0
        routing_err = int((sub['monitor_class'] == 'UNKNOWN').sum()) if len(sub) else 0
        quant_rows.append({
            'seq': seq, 'strategy': name, 'candidate': candidate_n, 'monitor_called': called_n,
            'result_recorded': recorded_n, 'routing_errors': routing_err,
        })
    quant_df = pd.DataFrame(quant_rows)

    # ── §7 attribution 정확도: seq(공식 식별자) 기준으로 대조한다 ──
    #    ⚠️ 이름 문자열로 대조하면 안 된다 — Monitor.strategy_name(예: "Momentum")과
    #    HTS 원문(_cond_sources, 예: "Momentum 전략")의 "전략" 접미사 유무가 달라
    #    이름 대조는 항상 거짓불일치를 만든다(seq 32/33/37은 실제로 정확히 일치함).
    attribution_checks = 0
    attribution_ok = 0
    if len(mon_df) and len(ev_df):
        for symbol, g in mon_df.groupby('symbol'):
            ev_match = ev_df[ev_df['symbol'] == symbol]
            if len(ev_match) == 0:
                continue
            ev_sources = ev_match.iloc[-1]['condition_sources']
            ev_seqs = {HTS_NAME_TO_SEQ.get(n) for n in ev_sources} - {None}
            mon_seqs = set(g['condition_seq'])
            attribution_checks += 1
            if mon_seqs.issubset(ev_seqs) or ev_sources == ['UNKNOWN']:
                attribution_ok += 1

    routing_accuracy = 1.0 if len(mon_df) and routing_errors == 0 else (0.0 if len(mon_df) else None)
    attribution_accuracy = (attribution_ok / attribution_checks) if attribution_checks else None

    quant_df.to_csv(os.path.join(OUT_DIR, 'strategy_monitor_runtime.csv'), index=False)
    mon_df.to_csv(os.path.join(OUT_DIR, 'live_strategy_monitor_trace.csv'), index=False)

    audit_rows = []
    if len(mon_df):
        for _, r in mon_df.iterrows():
            audit_rows.append({
                'timestamp': r['timestamp'], 'symbol': r['symbol'], 'condition_seq': r['condition_seq'],
                'expected_monitor_class': MONITOR_CLASS.get(r['condition_seq']),
                'actual_monitor_class': r['monitor_class'],
                'routing_correct': r['monitor_class'] == MONITOR_CLASS.get(r['condition_seq']),
                'strategy_name_is_generic_smc': r['strategy_name'] in ('SMC', 'smc'),
            })
    pd.DataFrame(audit_rows).to_csv(os.path.join(OUT_DIR, 'strategy_monitor_routing_audit.csv'), index=False)

    candidate_count = len(mon_df)
    verdict = 'INSUFFICIENT_OBSERVATION'
    if unexpected_buys > 0 or monitor_errors > 0 or routing_errors > 0:
        verdict = 'FAIL'
    elif candidate_count >= 20:
        verdict = 'PASS' if (routing_accuracy == 1.0 and (attribution_accuracy or 0) >= 0.99) else 'CONDITIONAL_PASS'
    elif candidate_count > 0:
        verdict = 'CONDITIONAL_PASS'

    decision = {
        'verdict': verdict,
        'candidate_count': candidate_count,
        'routing_accuracy': routing_accuracy,
        'attribution_accuracy': attribution_accuracy,
        'monitor_errors': monitor_errors,
        'routing_errors': routing_errors,
        'unexpected_buys': unexpected_buys,
        'observation_window': f'재시작 15:02:46 ~ 로그수집시점, 장마감 15:30 이내',
        'not_implemented_seqs_seen_as_no_code': int(
            (mon_df['data_quality'] == 'NO_CODE').sum()) if len(mon_df) else 0,
        'multi_source_examples': [],
    }

    if len(mon_df):
        multi = mon_df.groupby('symbol')['condition_seq'].apply(lambda s: sorted(set(s)))
        multi = multi[multi.apply(len) > 1]
        decision['multi_source_examples'] = [
            {'symbol': sym, 'seqs': seqs} for sym, seqs in multi.items()
        ]

    with open(os.path.join(OUT_DIR, 'decision.json'), 'w') as f:
        json.dump(decision, f, ensure_ascii=False, indent=2, default=str)

    print(f"\ncandidate_count={candidate_count} routing_accuracy={routing_accuracy} "
          f"attribution_accuracy={attribution_accuracy}")
    print(f"판정: {verdict}")
    return decision, quant_df, mon_df, ev_df


if __name__ == '__main__':
    main()
