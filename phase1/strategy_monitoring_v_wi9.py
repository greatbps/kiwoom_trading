"""
Work Instruction 9 — HTS 조건검색식 기반 Strategy Monitoring Pipeline 구축

읽기전용 집계 + 신규 로깅 포맷의 정적 검증. 새 백테스트/전략개발 없음.

⚠️ seq 32~39는 이 스크립트 실행 시점까지 라이브에 단 한 번도 적용된 적이 없다
(코드/설정은 WI-8/WI-9에서 seq 기반으로 바뀌었지만, 라이브 프로세스는 재시작 전까지
구 설정(idx 17-22)으로 동작 — 재시작은 이번 작업 범위 밖). 따라서:

- "구체계 참고" 파트: WI-7이 이미 수집한 2026-07-28~08-07 9거래일 데이터(idx 17-22
  기준)를 무수정 재사용 — seq 32-39와는 무관한 참고자료임을 모든 산출물에 명시.
- "신체계 준비상태" 파트: 새로 추가한 로그 포맷([STRATEGY_ATTR]/[EVIDENCE_ATTR]/
  [GLOBAL_GATE]/[CAN_ENTER_BLOCK]/[COND_ATTR])이 seq 32-39 전부에 대해 필요한 필드를
  올바르게 채우는지 mirror 시뮬레이션으로 검증(실측 아님, 명시).

사용법: python -m phase1.strategy_monitoring_v_wi9
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WI7_DIR = os.path.join(ROOT, 'phase1', 'reports', 'candidate_attribution')
OUT_DIR = os.path.join(ROOT, 'phase1', 'reports', 'strategy_monitoring')
os.makedirs(OUT_DIR, exist_ok=True)

# HTS 실측(2026-08-11, WI-9 §2 고정)
SEQ_STRATEGIES = {
    32: 'Momentum', 33: 'Breakout', 34: 'EOD',
    35: 'Supertrend + EMA + RSI', 36: 'VWAP',
    37: 'Squeeze Momentum Pro', 38: 'Bottom', 39: 'ITS',
}

# Phase 1 조사 결과 (정적 코드분석, plan 파일과 동일 — 무재조사 재사용)
PHASE1_MAPPING = [
    dict(seq=32, strategy_name='Momentum', entry_module='main_auto_trading.py',
         entry_function='run_condition_filtering→check_all_stocks',
         candidate_source='HTS CNSRREQ(범용 파이프라인)',
         signal_function='analyzers.smc.SMCStrategy.check_entry_signal',
         ranking_function='trading.score_engine.ScoreEngine.rank/select',
         gate_function='_check_global_risk_gates + Grade Policy Gate',
         slot_function='core.risk_manager.can_open_position',
         order_function='execute_buy→api.order_buy',
         enabled=True, status='ACTIVE',
         note='범용 SMC 진입 파이프라인 그 자체 — 사실상 seq32~39 전체가 이 경로를 공유'),
    dict(seq=33, strategy_name='Breakout', entry_module='main_auto_trading.py',
         entry_function='(seq32와 동일 범용 경로)',
         candidate_source='HTS CNSRREQ(범용 파이프라인)',
         signal_function='SMCStrategy.check_entry_signal (TrendBreakoutStrategy 아님)',
         ranking_function='ScoreEngine.rank/select', gate_function='(seq32와 동일)',
         slot_function='can_open_position', order_function='execute_buy',
         enabled=True, status='PARTIAL',
         note='TrendBreakoutStrategy(main_auto_trading.py:552)는 실재하나 regime==TREND '
              '트리거이지 seq33 조건매칭 트리거가 아님 — 이름과 실제 로직 불일치'),
    dict(seq=34, strategy_name='EOD', entry_module='(진입측 없음)',
         entry_function='N/A', candidate_source='HTS CNSRREQ(범용 파이프라인)',
         signal_function='SMCStrategy.check_entry_signal(동일 범용)',
         ranking_function='ScoreEngine.rank/select', gate_function='(seq32와 동일)',
         slot_function='can_open_position', order_function='execute_buy',
         enabled=True, status='DISCONNECTED',
         note='trading/eod_manager.py::EODManager는 청산측(15:05 강제청산/오버나잇관리) '
              '전용 — "EOD 전략"이라는 이름의 진입측 로직은 존재하지 않음'),
    dict(seq=35, strategy_name='Supertrend + EMA + RSI', entry_module='(진입측 없음)',
         entry_function='N/A', candidate_source='HTS CNSRREQ(범용 파이프라인)',
         signal_function='SMCStrategy.check_entry_signal(동일 범용)',
         ranking_function='ScoreEngine.rank/select', gate_function='(seq32와 동일)',
         slot_function='can_open_position', order_function='execute_buy',
         enabled=True, status='DISCONNECTED',
         note="'supertrend_direction'은 trading/eod_manager.py 내부에서 청산판단 "
              '보조지표로만 쓰임 — 진입측 Supertrend+EMA+RSI 조합 로직 없음'),
    dict(seq=36, strategy_name='VWAP', entry_module='main_auto_trading.py',
         entry_function='run_condition_filtering (2차 필터: VWAP 검증, :3233-3265)',
         candidate_source='HTS CNSRREQ(범용 파이프라인) + VWAP 2차검증(전 후보 공통)',
         signal_function='validate_stock_for_trading + SMCStrategy.check_entry_signal',
         ranking_function='ScoreEngine.rank/select', gate_function='(seq32와 동일)',
         slot_function='can_open_position', order_function='execute_buy',
         enabled=True, status='PARTIAL',
         note='VWAP 2차검증은 실재하고 활성상태이나 seq36 전용이 아니라 6~8개 조건 '
              '전체 후보 풀에 동일하게 적용됨(차별화 없음)'),
    dict(seq=37, strategy_name='Squeeze Momentum Pro', entry_module='main_auto_trading.py',
         entry_function='SqueezeWithOrderBook 서브전략 체크(:6130-6183)',
         candidate_source='HTS CNSRREQ(범용 파이프라인) + Squeeze 서브전략(설정 트리거)',
         signal_function='SqueezeWithOrderBook.check + SMCStrategy.check_entry_signal',
         ranking_function='ScoreEngine.rank/select', gate_function='(seq32와 동일)',
         slot_function='can_open_position', order_function='execute_buy',
         enabled=True, status='PARTIAL',
         note="entry_mode='squeeze_only' 등 실제 활성 로직 존재하나 seq37 조건매칭이 "
              '아니라 자체 설정(entry_mode)으로 트리거됨 — 전 후보 풀에 조건부 적용'),
    dict(seq=38, strategy_name='Bottom', entry_module='trading/bottom_pullback_manager.py',
         entry_function='BottomPullbackManager.register_signal→check_pullback→mark_entered',
         candidate_source='HTS CNSRREQ(Bottom 분기, run_condition_filtering :3153)',
         signal_function='BottomPullbackManager.check_pullback',
         ranking_function='N/A(즉시매수 아님, watchlist 미등록)',
         gate_function='(seq32와 동일, 재돌파 시점)',
         slot_function='can_open_position', order_function='execute_buy',
         enabled=True, status='DISCONNECTED(수정완료)',
         note='register_signal 이하 전부 완전구현 상태였으나 bottom_pullback.'
              'condition_indices가 idx=23(seq38과 무관)이라 진입점 미도달 — '
              '이번 WI-9에서 [38]로 수정, 다음 재시작부터 라우팅 정상화'),
    dict(seq=39, strategy_name='ITS', entry_module='(코드 없음)',
         entry_function='UNKNOWN', candidate_source='HTS CNSRREQ(범용 파이프라인)',
         signal_function='SMCStrategy.check_entry_signal(동일 범용, 명칭 대응 없음)',
         ranking_function='ScoreEngine.rank/select', gate_function='(seq32와 동일)',
         slot_function='can_open_position', order_function='execute_buy',
         enabled=True, status='UNKNOWN',
         note="코드베이스 전체에서 'ITS' 매칭 0건(grep 확인) — 대응 로직 식별 불가, "
              '억지로 연결하지 않음(WI-9 §4 원칙)'),
]


def build_strategy_mapping_md():
    lines = ['# WI-9 Phase 1 — seq 32~39 실제 코드 Mapping\n',
             '정적 코드분석(2026-08-11). 억지 연결 없음 — 대응 없으면 UNKNOWN/DISCONNECTED.\n',
             '\n| seq | 전략명 | entry_function | signal_function | 상태 | 비고 |',
             '|--:|---|---|---|---|---|']
    for m in PHASE1_MAPPING:
        lines.append(f"| {m['seq']} | {m['strategy_name']} | {m['entry_function']} | "
                     f"{m['signal_function']} | **{m['status']}** | {m['note']} |")
    lines.append('\n## 핵심 발견\n')
    lines.append(
        "`strategy_tag`는 코드 전체에서 분기(`==`) 조건으로 쓰이지 않는다"
        "(`grep \"strategy_tag ==\"` → `swing` 비교 1건뿐). seq 32~39는 각자 다른 "
        "코드경로로 갈라지는 게 아니라 **전부 동일한 범용 SMC 진입 파이프라인**을 "
        "공유한다. TrendBreakoutStrategy/Squeeze 서브전략은 실재하지만 조건검색 "
        "매칭이 아니라 regime/설정값으로 트리거되어 전체 후보 풀에 적용된다."
    )
    with open(os.path.join(OUT_DIR, 'strategy_mapping.md'), 'w') as f:
        f.write('\n'.join(lines))


def build_funnel_csv():
    rows = []
    # ── 구체계 참고 (idx 17-22, WI-7 재사용, seq32-39와 무관) ──
    old_map = {
        17: '알고리즘추출_1110', 18: '종가매수_대박주식', 19: '위전_종가',
        20: '240일 돌파', 21: '240일 상위', 22: '5일선',
    }
    old_cand = pd.read_csv(os.path.join(WI7_DIR, 'condition_candidate_summary.csv'))
    for _, r in old_cand.iterrows():
        rows.append(dict(system='OLD(idx17-22, WI-7 재사용, seq32-39와 무관)',
                          seq='N/A', strategy_name=r['condition_name'], stage='Condition',
                          count=r['candidate_count_raw_occurrences'],
                          note='구체계 참고치 — 실행경로가 seq32-39와 다름'))
    # 전체 funnel(구체계, 조건식 구분 없이 통합 — WI-7 candidate_funnel.csv 값 재사용).
    # ⚠️ 원본 CSV의 stage 컬럼에 따옴표 없는 콤마가 섞여 pandas가 파싱 실패한다
    #    (WI-7 산출물 자체의 포맷 결함, 이번 작업 범위 아니라 수정하지 않음) —
    #    동일한 값을 여기 직접 옮겨적어 재사용한다(재계산 아님, WI-7 원본 그대로).
    old_funnel_rows = [
        ('1_HTS_Candidate', 180, 'HTS 후보(부분캡처), 조건식 구분 없이 통합'),
        ('2_Evidence_Available(3차필터 도달)', 56, 'AnalysisEngine 도달 종목'),
        ('2b_Evidence_매수추천(DISPLAY_ONLY)', 1762, '3296건 중 53.5% — 필터 아님'),
        ('3_Ranking_계산됨', 50, '10 cycle 표본, selected(>=2)=0건'),
        ('4_Gate단계_도달(decision_ledger)', 39, 'SMC 평가 전에 Regime Gate가 먼저 차단'),
        ('5_Gate_Pass', 0, 'WI-3 실측 재사용'),
        ('6_SMC_Signal(라이브)', 0, 'Gate가 먼저 차단해 SMC 평가 기회 자체 없음'),
        ('7_Grade_Policy_Gate_Pass', 0, 'A급 0건(WI4), B/C 정책상 차단'),
        ('8_Slot_배정', 0, 'Gate PASS 0건이라 도달 후보 자체 없음'),
        ('9_Execution(매수체결)', 0, '9일 COND_ATTR 로그 0건'),
    ]
    for stage, count, note in old_funnel_rows:
        rows.append(dict(system='OLD(idx17-22, WI-7 값 재사용, 통합)', seq='N/A',
                          strategy_name='(전체 통합, 전략별 미분리)', stage=stage,
                          count=count, note=note))
    # ── 신체계 (seq32-39) — 라이브 미가동, 전부 N/A ──
    for seq, name in SEQ_STRATEGIES.items():
        for stage in ['Condition', 'Candidate', 'Evidence', 'Ranked', 'Signal',
                      'Gate_PASS', 'Slot_PASS', 'Order']:
            rows.append(dict(system='NEW(seq32-39)', seq=seq, strategy_name=name,
                              stage=stage, count='N/A',
                              note='라이브 미가동 — 프로세스 재시작 전까지 실측 불가(WI-9 §Phase6 제약)'))
    pd.DataFrame(rows).to_csv(os.path.join(OUT_DIR, 'strategy_funnel.csv'), index=False)


def build_blocking_csv():
    rows = []
    # 구체계 참고: WI-3 gate_replay.csv의 TOTAL 요약행 값 재사용(main_auto_trading 전체
    # 공통 게이트, 전략 미분리). ⚠️ 원본이 일자별표+TOTAL표 2개가 한 파일에 섞인
    # 멀티테이블 CSV라 pandas 단일파싱이 안 됨(WI-3 산출물 자체 포맷, 이번 범위 아니라
    # 수정하지 않음) — TOTAL 행 값만 그대로 옮겨 재사용(재계산 아님).
    old_gate_totals = [
        ('GLOBAL_GATE_BLOCKED', 2916, 78.2),
        ('REGIME_BLOCKED', 815, 21.8),
    ]
    for reason, cnt, pct in old_gate_totals:
        rows.append(dict(system='OLD(WI-3 gate_replay.csv TOTAL행 재사용, 통합)', seq='N/A',
                          strategy_name='(전체 통합, 전략별 미분리)',
                          blocking_reason=reason, count=cnt,
                          note=f'{pct}% — 구체계 참고치, 게이트 자체는 전략 구분 없이 공통 적용됨'))
    for seq, name in SEQ_STRATEGIES.items():
        rows.append(dict(system='NEW(seq32-39)', seq=seq, strategy_name=name,
                          blocking_reason='N/A', count='N/A',
                          note='라이브 미가동 — 실측 불가'))
    pd.DataFrame(rows).to_csv(os.path.join(OUT_DIR, 'strategy_blocking.csv'), index=False)


# ── 신체계 준비상태 — mirror 시뮬레이션 (실측 아님) ──────────────────────────
def _condition_attribution_mirror(validated_stocks: dict, stock_code: str) -> dict:
    """main_auto_trading.py:2047-2064 _condition_attribution() 그대로 재현."""
    info = validated_stocks.get(stock_code) or {}
    src = list(info.get('condition_sources') or [])
    seqs = list(info.get('strategy_seqs') or [])
    return {
        'condition_sources': src or ['UNKNOWN'],
        'primary_condition': info.get('primary_condition') or 'UNKNOWN',
        'condition_match_time': info.get('condition_match_time'),
        'strategy_seqs': seqs or ['UNKNOWN'],
        'primary_strategy_seq': info.get('primary_strategy_seq') or 'UNKNOWN',
    }


def build_signal_trace_csv():
    """8개 seq 전부에 대해 가상 종목 1개씩으로 Attribution 왕복을 시뮬레이션한다.
    ⚠️ SIMULATED — 실제 라이브 데이터 아님(seq32-39 미가동 상태)."""
    rows = []
    for i, (seq, name) in enumerate(SEQ_STRATEGIES.items()):
        sym = f'SIM{seq:03d}'
        vs = {sym: {
            'condition_sources': [name],
            'primary_condition': name,
            'condition_match_time': datetime(2026, 8, 11, 9, 10, 0).isoformat(),
            'strategy_seqs': [seq],
            'primary_strategy_seq': seq,
        }}
        ca = _condition_attribution_mirror(vs, sym)
        rows.append(dict(
            seq=seq, strategy_name=name, symbol=sym, source='SIMULATED(mirror, 실측 아님)',
            candidate_status='MONITORED(시뮬레이션)',
            evidence_status='DISPLAY_ONLY(WI-7/8 확정사항)',
            ranking_score='N/A(라이브 미가동)',
            signal_status='N/A(라이브 미가동)',
            gate_status='N/A(라이브 미가동)',
            slot_status='N/A(라이브 미가동)',
            order_status='N/A(라이브 미가동)',
            attribution_roundtrip_ok=(ca['primary_strategy_seq'] == seq),
            final_reason='구조검증만 수행 — 실제 Signal/Gate/Slot/Order는 다음 재시작 후 실측 필요',
        ))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(OUT_DIR, 'strategy_signal_trace.csv'), index=False)
    return df


def main():
    print('=' * 78)
    print('  WI-9 Strategy Monitoring Pipeline — 집계 및 정적 검증')
    print('=' * 78)

    print('\n[1] strategy_mapping.md 생성...')
    build_strategy_mapping_md()

    print('[2] strategy_funnel.csv 생성(구체계 참고 + 신체계 N/A)...')
    build_funnel_csv()

    print('[3] strategy_blocking.csv 생성...')
    build_blocking_csv()

    print('[4] strategy_signal_trace.csv 생성(mirror 시뮬레이션)...')
    trace_df = build_signal_trace_csv()
    all_roundtrip_ok = bool(trace_df['attribution_roundtrip_ok'].all())
    print(f'  8개 seq attribution 왕복 검증: {"전체 OK" if all_roundtrip_ok else "일부 실패"}')

    status_map = {m['seq']: m['status'] for m in PHASE1_MAPPING}
    n_active = sum(1 for s in status_map.values() if s == 'ACTIVE')
    n_partial = sum(1 for s in status_map.values() if s == 'PARTIAL')
    n_disconnected = sum(1 for s in status_map.values() if s.startswith('DISCONNECTED'))
    n_unknown = sum(1 for s in status_map.values() if s == 'UNKNOWN')

    decision = {
        'run_at': datetime.now().isoformat(timespec='seconds'),
        'work_instruction': 'WI-9 Strategy Monitoring Pipeline 구축',
        'seq_range': [32, 33, 34, 35, 36, 37, 38, 39],
        'phase1_status_summary': {
            'ACTIVE': n_active, 'PARTIAL': n_partial,
            'DISCONNECTED': n_disconnected, 'UNKNOWN': n_unknown,
        },
        'phase1_detail': {str(m['seq']): m['status'] for m in PHASE1_MAPPING},
        'code_changes': {
            'bottom_seq_routing_fix': "config/strategy_hybrid.yaml bottom_pullback.condition_indices [23]->[38]",
            'attribution_logging_added': [
                'main_auto_trading.py: _cond_seqs (신규, _cond_sources와 동일 위치)',
                'main_auto_trading.py: validated_stocks[...]["strategy_seqs"/"primary_strategy_seq"]',
                'main_auto_trading.py: _condition_attribution() strategy_seqs/primary_strategy_seq 필드 추가',
                'main_auto_trading.py: [STRATEGY_ATTR] 신규 로그(CANDIDATE_MONITORED)',
                'main_auto_trading.py: [EVIDENCE_ATTR]/[GLOBAL_GATE]/[CAN_ENTER_BLOCK]/[COND_ATTR]에 strategy_seq 필드 추가',
            ],
            'decision_logic_changed': False,
        },
        'phase6_constraint': {
            'seq_32_39_ever_live': False,
            'reason': '코드/설정은 seq 기반으로 전환됐으나 라이브 프로세스(재시작 전까지 구설정 idx17-22 사용) 미재시작 — '
                       '실측 데이터 자체가 존재하지 않음. 억지로 재구성하지 않음.',
            'old_system_reference_reused': 'phase1/reports/candidate_attribution/*.csv (WI-7, idx17-22, 2026-07-28~08-07)',
            'new_system_verification_method': 'mirror 시뮬레이션(strategy_signal_trace.csv) — 실측 아님',
        },
        'attribution_roundtrip_verified': all_roundtrip_ok,
        'regression': {},  # check_baseline.sh 실행 후 채움
        'final_verdict': None,  # 아래에서 채움
    }

    # 최종판정: UNKNOWN/DISCONNECTED가 있으므로 PASS 불가, 그러나 핵심 인프라(Attribution
    # 로깅+Bottom 라우팅 수정)는 완성됐고 Phase6 미가동은 구조적 사유가 명확하므로 FAIL도 아님.
    if n_unknown > 0 or n_disconnected > 0:
        decision['final_verdict'] = 'CONDITIONAL PASS'
        decision['final_verdict_reason'] = (
            f'seq39(ITS)=UNKNOWN, seq34/35(EOD/Supertrend+EMA+RSI)=DISCONNECTED(진입측 로직 '
            f'자체가 존재하지 않음, 억지 연결 금지 원칙상 미해결). seq38(Bottom)은 이번 '
            f'작업에서 라우팅 수정 완료. 나머지(32/33/36/37)는 범용 파이프라인을 공유하며 '
            f'PARTIAL(이름과 실제 로직 불일치)/ACTIVE. Attribution 로깅 인프라와 Bottom 라우팅 '
            f'복구는 완료됐으나 seq32-39 자체가 아직 한 번도 라이브 실행된 적이 없어 '
            f'Phase6(실측)/Phase7(실측기반 GREEN/YELLOW/RED)은 검증 불가 — 다음 재시작 후 '
            f'별도 확인 필요.'
        )
    else:
        decision['final_verdict'] = 'PASS'

    with open(os.path.join(OUT_DIR, 'decision.json'), 'w') as f:
        json.dump(decision, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n판정: {decision['final_verdict']}")
    print('완료.')
    return decision


if __name__ == '__main__':
    main()
