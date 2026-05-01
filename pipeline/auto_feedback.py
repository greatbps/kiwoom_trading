"""
pipeline/auto_feedback.py — 자동 피드백 루프

매일 장 마감 후 실행 → 전략 가중치 자동 조정 + 이상 패턴 알림.

실행:
  python3 -m pipeline.auto_feedback          # 분석만
  python3 -m pipeline.auto_feedback --apply  # YAML 자동 반영
  python3 -m pipeline.auto_feedback --days 30

흐름:
  ml_dataset + trades + trade_signals
    → 패턴별 평균 손익 계산
    → 음수 패턴 → YAML 임계값 강화 제안
    → 양수 패턴 → 가중치 상향 제안
    → strategy_change_log 기록
"""

import os
import sys
import argparse
import psycopg2
import psycopg2.extras
import yaml
import logging
from pathlib import Path
from datetime import datetime, timedelta

PROJECT = Path(__file__).parent.parent
YAML_PATH = PROJECT / "config" / "strategy_hybrid.yaml"
logger = logging.getLogger(__name__)

_PG_DSN = {
    "host":     os.getenv("POSTGRES_HOST", "localhost"),
    "dbname":   os.getenv("POSTGRES_DB", "trading_system"),
    "user":     os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}


def _get_conn():
    return psycopg2.connect(**_PG_DSN)


# ─────────────────────────────────────────────────────────────
# 1. 데이터 수집
# ─────────────────────────────────────────────────────────────

def load_trade_stats(days: int = 14) -> dict:
    """최근 N일 거래 통계 수집"""
    cutoff = (datetime.now() - timedelta(days=days)).date()
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # 전략별 성과
    cur.execute("""
        SELECT
            COALESCE(s.strategy_name, 'UNKNOWN')            AS strategy,
            COUNT(*)                                         AS trades,
            ROUND(AVG(t.realized_profit)::numeric, 0)       AS avg_pnl,
            ROUND(AVG(t.profit_rate)::numeric, 2)           AS avg_pnl_pct,
            SUM(CASE WHEN t.realized_profit > 0 THEN 1 ELSE 0 END) AS wins,
            ROUND(100.0 * SUM(CASE WHEN t.realized_profit > 0 THEN 1 ELSE 0 END)
                  / NULLIF(COUNT(*), 0), 1)                  AS win_rate
        FROM trades t
        LEFT JOIN trade_signals s ON t.entry_signal_id = s.signal_id
        WHERE t.trade_type = 'SELL'
          AND t.realized_profit IS NOT NULL
          AND DATE(t.trade_time) >= %s
        GROUP BY strategy
        ORDER BY avg_pnl DESC
    """, (cutoff,))
    strategy_stats = cur.fetchall()

    # 시간대별 성과
    cur.execute("""
        SELECT
            EXTRACT(HOUR FROM t.trade_time)::int AS hour,
            COUNT(*) AS trades,
            ROUND(AVG(t.realized_profit)::numeric, 0) AS avg_pnl
        FROM trades t
        WHERE t.trade_type = 'SELL'
          AND t.realized_profit IS NOT NULL
          AND DATE(t.trade_time) >= %s
        GROUP BY hour
        ORDER BY hour
    """, (cutoff,))
    hourly_stats = cur.fetchall()

    # market_context별 성과
    cur.execute("""
        SELECT
            s.market_context,
            COUNT(*) AS trades,
            ROUND(AVG(t.realized_profit)::numeric, 0) AS avg_pnl,
            ROUND(100.0 * SUM(CASE WHEN t.realized_profit > 0 THEN 1 ELSE 0 END)
                  / NULLIF(COUNT(*), 0), 1) AS win_rate
        FROM trades t
        JOIN trade_signals s ON t.entry_signal_id = s.signal_id
        WHERE t.trade_type = 'SELL'
          AND t.realized_profit IS NOT NULL
          AND DATE(t.trade_time) >= %s
        GROUP BY s.market_context
    """, (cutoff,))
    context_stats = cur.fetchall()

    # ML 라벨 분포
    cur.execute("""
        SELECT source_type, label_quality, COUNT(*) AS cnt
        FROM ml_dataset
        WHERE created_at >= %s
        GROUP BY source_type, label_quality
        ORDER BY source_type, label_quality
    """, (cutoff,))
    label_dist = cur.fetchall()

    conn.close()
    return {
        'days': days,
        'cutoff': str(cutoff),
        'strategy_stats': [dict(r) for r in strategy_stats],
        'hourly_stats':   [dict(r) for r in hourly_stats],
        'context_stats':  [dict(r) for r in context_stats],
        'label_dist':     [dict(r) for r in label_dist],
    }


# ─────────────────────────────────────────────────────────────
# 2. 개선 제안 생성
# ─────────────────────────────────────────────────────────────

def generate_suggestions(stats: dict) -> list[dict]:
    """
    통계 → 구체적 YAML 파라미터 변경 제안.
    각 제안: {strategy, param_key, current_val, suggested_val, reason, confidence}
    """
    suggestions = []
    config = yaml.safe_load(YAML_PATH.read_text())

    # ── 전략별 분석
    for row in stats['strategy_stats']:
        strategy = row['strategy']
        trades   = row['trades'] or 0
        avg_pnl  = float(row['avg_pnl'] or 0)
        win_rate = float(row['win_rate'] or 0)

        if trades < 3:
            continue  # 샘플 부족

        if strategy == 'EXPLORATION' and avg_pnl < -500:
            curr = config.get('exploration', {}).get('min_rvol', 2.0)
            suggestions.append({
                'strategy': 'exploration',
                'param_key': 'min_rvol',
                'current_val': curr,
                'suggested_val': round(min(curr + 0.5, 4.0), 1),
                'reason': f'EXPLORATION 평균손익 {avg_pnl:+,.0f}원 (승률 {win_rate}%) → RVOL 임계값 상향',
                'confidence': 'HIGH' if trades >= 5 else 'MEDIUM',
            })

        if strategy == 'TREND' and win_rate < 40 and trades >= 3:
            curr = config.get('trend', {}).get('fake_breakout_filter', {}).get('min_volume_ratio', 2.0)
            suggestions.append({
                'strategy': 'trend.fake_breakout_filter',
                'param_key': 'min_volume_ratio',
                'current_val': curr,
                'suggested_val': round(min(curr + 0.3, 3.5), 1),
                'reason': f'TREND 승률 {win_rate}% < 40% → 거짓돌파 필터 강화',
                'confidence': 'HIGH' if trades >= 5 else 'MEDIUM',
            })

        if strategy == 'SMC' and avg_pnl < -300 and trades >= 3:
            curr_cutoff = config.get('smc', {}).get('choch_grade', {}).get('grade_b_cutoff', '11:30')
            # 30분 앞당기기
            h, m = map(int, curr_cutoff.split(':'))
            total = h * 60 + m - 30
            new_cutoff = f"{total//60:02d}:{total%60:02d}"
            suggestions.append({
                'strategy': 'smc.choch_grade',
                'param_key': 'grade_b_cutoff',
                'current_val': curr_cutoff,
                'suggested_val': new_cutoff,
                'reason': f'SMC 평균손익 {avg_pnl:+,.0f}원 → B급 진입 시간 30분 단축',
                'confidence': 'MEDIUM',
            })

    # ── 시간대별 분석
    bad_hours = [r for r in stats['hourly_stats']
                 if float(r['avg_pnl'] or 0) < -500 and int(r['trades']) >= 3]
    for r in bad_hours:
        suggestions.append({
            'strategy': 'time_filter',
            'param_key': f"hour_{r['hour']}_block",
            'current_val': 'enabled',
            'suggested_val': 'consider_block',
            'reason': f"{r['hour']}시대 평균손익 {float(r['avg_pnl']):+,.0f}원 ({r['trades']}건)",
            'confidence': 'LOW',
        })

    # ── BAD_MARKET 분석
    for r in stats['context_stats']:
        if r['market_context'] == 'BAD_MARKET':
            wr = float(r['win_rate'] or 0)
            avg = float(r['avg_pnl'] or 0)
            if wr < 30 and int(r['trades']) >= 3:
                curr = config.get('experiment', {}).get('bad_market_size_mult', 0.3)
                suggestions.append({
                    'strategy': 'experiment',
                    'param_key': 'bad_market_size_mult',
                    'current_val': curr,
                    'suggested_val': round(max(curr - 0.1, 0.1), 1),
                    'reason': f'BAD_MARKET 승률 {wr}% → size_mult 추가 축소',
                    'confidence': 'HIGH',
                })

    return suggestions


# ─────────────────────────────────────────────────────────────
# 3. 리포트 출력
# ─────────────────────────────────────────────────────────────

def print_report(stats: dict, suggestions: list[dict]):
    print(f"\n{'='*60}")
    print(f"[AUTO FEEDBACK] 최근 {stats['days']}일 분석 ({stats['cutoff']} ~)")
    print(f"{'='*60}")

    print("\n■ 전략별 성과")
    for r in stats['strategy_stats']:
        t = r['trades'] or 0
        if t == 0:
            continue
        print(f"  {r['strategy']:15s} | {t:3d}건 | 승률 {r['win_rate'] or 0:5.1f}% | "
              f"평균 {float(r['avg_pnl'] or 0):+7,.0f}원")

    print("\n■ ML 라벨 분포")
    for r in stats['label_dist']:
        q_map = {2: 'GOOD', 1: 'NORMAL', 0: 'BAD/REJECTED'}
        print(f"  {r['source_type']:15s} | quality={q_map.get(r['label_quality'], '?'):10s} | {r['cnt']}건")

    if not suggestions:
        print("\n✅ 개선 제안 없음 (현재 파라미터 적절)")
        return

    print(f"\n■ 개선 제안 ({len(suggestions)}건)")
    for i, s in enumerate(suggestions, 1):
        conf_icon = {'HIGH': '🔴', 'MEDIUM': '🟡', 'LOW': '⚪'}.get(s['confidence'], '⚪')
        print(f"\n  [{i}] {conf_icon} {s['confidence']}")
        print(f"      파라미터: {s['strategy']}.{s['param_key']}")
        print(f"      변경:     {s['current_val']} → {s['suggested_val']}")
        print(f"      이유:     {s['reason']}")


# ─────────────────────────────────────────────────────────────
# 4. YAML 자동 반영 (HIGH confidence만)
# ─────────────────────────────────────────────────────────────

def apply_suggestions(suggestions: list[dict], dry_run: bool = True):
    """
    HIGH confidence 제안만 YAML에 자동 반영.
    dry_run=True → 출력만, False → 실제 파일 수정 + strategy_change_log 기록.
    """
    from database.decision_trace import log_strategy_change

    high = [s for s in suggestions if s['confidence'] == 'HIGH']
    if not high:
        print("\n[AUTO FEEDBACK] HIGH confidence 제안 없음 → YAML 변경 없음")
        return

    config = yaml.safe_load(YAML_PATH.read_text())

    for s in high:
        keys = s['strategy'].split('.') + [s['param_key']]
        node = config
        try:
            for k in keys[:-1]:
                node = node[k]
            old_val = node.get(keys[-1])
            if dry_run:
                print(f"  [DRY RUN] {'.'.join(keys)}: {old_val} → {s['suggested_val']}")
            else:
                node[keys[-1]] = s['suggested_val']
                log_strategy_change(
                    strategy_name=s['strategy'],
                    param_key=s['param_key'],
                    value_before=old_val,
                    value_after=s['suggested_val'],
                    change_type='auto_feedback',
                    description=s['reason'],
                    expected_effect='자동 피드백 적용',
                )
                print(f"  [APPLIED] {'.'.join(keys)}: {old_val} → {s['suggested_val']}")
        except (KeyError, TypeError) as e:
            print(f"  [SKIP] {'.'.join(keys)}: YAML 경로 없음 ({e})")

    if not dry_run and high:
        YAML_PATH.write_text(yaml.dump(config, allow_unicode=True, sort_keys=False))
        print(f"\n✅ strategy_hybrid.yaml 업데이트 완료")


# ─────────────────────────────────────────────────────────────
# 5. Replay 시스템
# ─────────────────────────────────────────────────────────────

def replay_run(run_id: int) -> dict:
    """
    특정 filter_pipeline_run 시점의 피처/필터/신호를 완전 재현.
    과거 특정 run_id 조건 변경 시뮬레이션에 활용.
    """
    conn = _get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("SELECT * FROM filter_pipeline_runs WHERE run_id = %s", (run_id,))
    run = cur.fetchone()
    if not run:
        return {'error': f'run_id {run_id} 없음'}

    cur.execute("""
        SELECT * FROM filter_feature_snapshot
        WHERE run_id = %s ORDER BY snap_time
    """, (run_id,))
    features = cur.fetchall()

    cur.execute("""
        SELECT * FROM filter_stage_results
        WHERE run_id = %s ORDER BY stage, stock_code
    """, (run_id,))
    stages = cur.fetchall()

    cur.execute("""
        SELECT s.*, t.trade_id, t.realized_profit, t.profit_rate
        FROM trade_signals s
        LEFT JOIN trades t ON t.entry_signal_id = s.signal_id
        WHERE s.signal_time::date = %s::date
        ORDER BY s.signal_time
    """, (run['run_time'],))
    signals = cur.fetchall()

    conn.close()

    result = {
        'run': dict(run),
        'features': [dict(f) for f in features],
        'filter_stages': [dict(s) for s in stages],
        'signals': [dict(s) for s in signals],
        'summary': {
            'total_candidates': len(features),
            'stage1_passed': sum(1 for s in stages if s['stage'] == 1 and s['passed']),
            'stage2_passed': sum(1 for s in stages if s['stage'] == 2 and s['passed']),
            'entry_signals': sum(1 for s in signals if s['signal_type'] == 'entry'),
        }
    }
    return result


def print_replay(run_id: int):
    r = replay_run(run_id)
    if 'error' in r:
        print(r['error'])
        return

    run = r['run']
    s   = r['summary']
    print(f"\n{'='*60}")
    print(f"[REPLAY] run_id={run_id}  {run['run_time']}  phase={run['market_phase']}")
    print(f"{'='*60}")
    print(f"  후보 종목: {s['total_candidates']}개")
    print(f"  1차 통과:  {s['stage1_passed']}개")
    print(f"  2차 통과:  {s['stage2_passed']}개")
    print(f"  진입 신호: {s['entry_signals']}개")

    print("\n  [진입 신호 상세]")
    for sig in r['signals']:
        if sig['signal_type'] != 'entry':
            continue
        pnl = sig.get('realized_profit')
        pnl_str = f"PnL={pnl:+,.0f}원" if pnl is not None else "미청산"
        print(f"    {sig['signal_time'].strftime('%H:%M')} | {sig['stock_code']} "
              f"| {sig['strategy_name']} | @{sig['price']:,.0f} | {pnl_str}")


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

if __name__ == '__main__':
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description='자동 피드백 루프')
    parser.add_argument('--days',   type=int, default=14, help='분석 기간 (기본 14일)')
    parser.add_argument('--apply',  action='store_true',  help='HIGH 제안 YAML 자동 반영')
    parser.add_argument('--replay', type=int, default=0,  help='특정 run_id 재현')
    args = parser.parse_args()

    if args.replay:
        print_replay(args.replay)
        sys.exit(0)

    stats = load_trade_stats(days=args.days)
    suggestions = generate_suggestions(stats)
    print_report(stats, suggestions)

    if args.apply:
        print("\n[YAML 자동 반영 시작]")
        apply_suggestions(suggestions, dry_run=False)
    elif suggestions:
        print("\n[DRY RUN 미리보기]")
        apply_suggestions(suggestions, dry_run=True)
        print("\n→ 실제 반영: python3 -m pipeline.auto_feedback --apply")
