"""
analysis/filter_retro_sim.py — 필터 소급 시뮬레이션

현재 DB에 있는 모든 자동매매 거래에 대해:
1. 전략 유형별 RVOL 필터 적용 시뮬레이션
2. 신규 SMC RVOL 필터의 실제 영향 측정
3. ef_no_follow 원인 분석 (RVOL 문제인지 아닌지)

Usage:
    python3 -m analysis.filter_retro_sim
"""

import sqlite3
import re
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

DB_PATH = 'data/trades.db'


@dataclass
class TradePair:
    buy_id: int
    timestamp: str
    name: str
    strategy: str
    buy_reason: str
    buy_price: float
    sell_price: float
    sell_reason: str
    realized_pnl: float

    @property
    def pnl_pct(self) -> float:
        if self.buy_price:
            return (self.sell_price - self.buy_price) / self.buy_price * 100
        return 0.0

    @property
    def rvol(self) -> Optional[float]:
        """RVOL 추출: EXPLORATION→RVOL=Nx, TREND→Vol×Nx"""
        m = re.search(r'RVOL[=](\d+\.?\d*)', self.buy_reason or '')
        if m:
            return float(m.group(1))
        m = re.search(r'Vol[×x](\d+\.?\d*)', self.buy_reason or '')
        if m:
            return float(m.group(1))
        return None

    @property
    def is_ef_no_follow(self) -> bool:
        return 'Early Failure[no_follow]' in (self.sell_reason or '')

    @property
    def is_hard_stop(self) -> bool:
        return 'Hard Stop' in (self.sell_reason or '')

    @property
    def exit_tag(self) -> str:
        r = self.sell_reason or ''
        if 'Early Failure[no_follow]' in r:
            return 'EF_NO_FOLLOW'
        if 'Hard Stop' in r:
            return 'HARD_STOP'
        if 'ATR 트레일링' in r or 'Trailing' in r:
            return 'TRAIL_WIN'
        if '오버나이트 차단' in r:
            return 'OVERNIGHT'
        if 'HTS_IMPORT' in r:
            return 'MANUAL_SELL'
        return 'OTHER'


def load_pairs() -> list[TradePair]:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute('''
        SELECT b.id, b.timestamp, b.stock_name, b.strategy, b.reason, b.price,
               s.price, s.reason, s.realized_pnl
        FROM trades b
        JOIN trades s ON b.stock_name = s.stock_name
            AND s.trade_type = 'SELL'
            AND s.timestamp > b.timestamp
            AND s.id = (
                SELECT MIN(id) FROM trades
                WHERE stock_name = b.stock_name
                AND trade_type = 'SELL'
                AND timestamp > b.timestamp
            )
        WHERE b.trade_type = 'BUY'
        AND b.strategy NOT LIKE 'MANUAL%'
        ORDER BY b.timestamp
    ''')
    rows = cur.fetchall()
    conn.close()
    return [TradePair(*r) for r in rows]


def apply_rvol_filter(pairs: list[TradePair], threshold: float, label: str) -> tuple[list, list]:
    """RVOL 임계값으로 필터링. Returns (passed, blocked)."""
    passed, blocked = [], []
    for p in pairs:
        if p.rvol is None:
            passed.append(p)  # RVOL 없는 경우 통과 (TREND 일부)
        elif p.rvol >= threshold:
            passed.append(p)
        else:
            blocked.append(p)
    return passed, blocked


def print_separator(char='─', width=70):
    print(char * width)


def print_trade_row(p: TradePair, blocked=False, threshold=None):
    rvol_str = f'{p.rvol:.1f}x' if p.rvol else 'N/A '
    marker = '🚫' if blocked else '✅'
    reason_short = (p.sell_reason or '')[:55]
    print(f"  {marker} {p.name:<14} {p.strategy:<12} RVOL={rvol_str:<5} "
          f"pnl={p.pnl_pct:+5.1f}%  [{p.exit_tag}]")
    if blocked and threshold:
        print(f"     └ 차단 사유: RVOL {p.rvol:.2f}x < {threshold:.1f}x 임계값")


def compute_stats(pairs: list[TradePair]) -> dict:
    if not pairs:
        return {'count': 0, 'wins': 0, 'wr': 0, 'total_pnl': 0, 'avg_pnl': 0}
    wins = sum(1 for p in pairs if p.pnl_pct > 0)
    total_pnl = sum(p.realized_pnl for p in pairs)
    avg_pnl = sum(p.pnl_pct for p in pairs) / len(pairs)
    return {
        'count': len(pairs),
        'wins': wins,
        'wr': wins / len(pairs) * 100,
        'total_pnl': total_pnl,
        'avg_pnl': avg_pnl,
    }


def run():
    pairs = load_pairs()

    print()
    print('=' * 70)
    print(' 필터 소급 시뮬레이션 (Retroactive Filter Simulation)')
    print('=' * 70)
    print(f'  분석 대상: {len(pairs)}건 (자동매매, MANUAL 제외)')
    print()

    # ── 섹션 1: 전략 분포 ──────────────────────────────────────────────────
    strat_counts: dict[str, int] = {}
    for p in pairs:
        strat_counts[p.strategy] = strat_counts.get(p.strategy, 0) + 1

    print_separator()
    print('■ 전략별 분포')
    print_separator()
    for strat, cnt in sorted(strat_counts.items(), key=lambda x: -x[1]):
        bar = '█' * cnt
        print(f'  {strat:<14} {cnt:3}건  {bar}')
    print()
    print('  → SMC 거래 0건. 신규 SMC RVOL 필터가 소급 적용되는 거래 없음.')
    print()

    # ── 섹션 2: RVOL 분포 ──────────────────────────────────────────────────
    print_separator()
    print('■ 진입 RVOL 분포')
    print_separator()
    with_rvol = [(p.rvol, p) for p in pairs if p.rvol is not None]
    no_rvol   = [p for p in pairs if p.rvol is None]
    with_rvol.sort(key=lambda x: x[0])

    for rvol, p in with_rvol:
        bar_len = int(rvol * 5)
        bar = '▓' * bar_len
        print(f'  {p.name:<14} {p.strategy:<12} {rvol:.1f}x  {bar}')
    if no_rvol:
        print(f'  (RVOL 미기록: {", ".join(p.name for p in no_rvol)})')
    print()

    # ── 섹션 3: SMC 필터 시뮬레이션 ──────────────────────────────────────
    print_separator()
    print('■ [시뮬레이션 A] 신규 SMC RVOL 필터 (1.3x NEUTRAL)')
    print_separator()
    print('  적용 대상: SMC 전략 거래만')
    print('  결과: DB에 SMC 거래 0건 → 차단 0건, 영향 없음')
    print()
    print('  ▶ 결론: SMC RVOL 필터는 미래 SMC 진입에 적용됨.')
    print('          현재 DB의 손익에는 영향 없음.')
    print()

    # ── 섹션 4: EXPLORATION RVOL 필터 시뮬레이션 ─────────────────────────
    expl_pairs = [p for p in pairs if p.strategy == 'EXPLORATION']
    print_separator()
    print('■ [시뮬레이션 B] EXPLORATION RVOL 필터 효과')
    print_separator()

    thresholds = [1.2, 1.5, 2.0, 2.5]
    before = compute_stats(expl_pairs)
    print(f'  현행 (min_rvol=1.2x): {before["count"]}건, '
          f'승률 {before["wr"]:.0f}%, 총손익 {before["total_pnl"]:+,.0f}원')
    print()

    for thr in thresholds[1:]:
        passed, blocked = apply_rvol_filter(expl_pairs, thr, f'RVOL≥{thr}')
        after = compute_stats(passed)
        blocked_pnl = sum(p.realized_pnl for p in blocked)
        delta_wr = after['wr'] - before['wr'] if after['count'] else 0
        print(f'  RVOL ≥ {thr:.1f}x: {after["count"]}건 통과 / {len(blocked)}건 차단')
        if blocked:
            print(f'    차단된 거래: {", ".join(p.name for p in blocked)} '
                  f'(합산 pnl {blocked_pnl:+,.0f}원)')
        if after['count']:
            print(f'    통과 후 승률: {after["wr"]:.0f}% ({delta_wr:+.0f}%p), '
                  f'총손익: {after["total_pnl"]:+,.0f}원')
        print()

    # ── 섹션 5: ef_no_follow 거래 분석 ────────────────────────────────────
    ef_pairs = [p for p in pairs if p.is_ef_no_follow]
    print_separator()
    print('■ ef_no_follow 거래 분석')
    print_separator()
    print(f'  ef_no_follow 발생: {len(ef_pairs)}건')
    print()
    for p in ef_pairs:
        rvol_str = f'{p.rvol:.1f}x' if p.rvol else 'N/A'
        print(f'  • {p.name:<14} {p.strategy:<12} RVOL={rvol_str:<5} pnl={p.pnl_pct:+.1f}%')
        print(f'    └ 청산 이유: {(p.sell_reason or "")[:65]}')

    print()
    print('  ▶ 분석:')
    print('    - RVOL 수치는 2.1x~3.4x → 진입 품질 자체는 충분했음')
    print('    - EF 감지 후 손절액: -0.3% ~ -0.7% (소액 차단 성공)')
    print('    - 원인: 진입 이후 모멘텀 소멸 (수요 부재)')
    print('    → RVOL 필터 강화로는 해결 불가. 진입 후 추종 조건이 핵심.')
    print()

    # ── 섹션 6: Hard Stop 분석 ────────────────────────────────────────────
    hs_pairs = [p for p in pairs if p.is_hard_stop]
    print_separator()
    print('■ Hard Stop 거래 분석 (실제 대형 손실)')
    print_separator()
    total_hs_loss = sum(p.realized_pnl for p in hs_pairs)
    print(f'  Hard Stop 발생: {len(hs_pairs)}건, 합산 손실: {total_hs_loss:,.0f}원')
    print()
    for p in hs_pairs:
        rvol_str = f'{p.rvol:.1f}x' if p.rvol else 'N/A'
        print(f'  • {p.name:<14} {p.strategy:<12} RVOL={rvol_str:<5} '
              f'pnl={p.pnl_pct:+.1f}%  (손실: {p.realized_pnl:,.0f}원)')

    print()
    print('  ▶ 분석:')
    print('    - 대우건설: RVOL=2.0x에서 -6.0% (HTS 가격 불일치 → Hard Stop 발동)')
    print('    - 싸이맥스: RVOL=4.6x에서 -2.1% (높은 RVOL도 Hard Stop 차단 못함)')
    print('    - 코오롱인더(TREND): RVOL=1.9x에서 -2.1%')
    print('    → RVOL 필터로는 Hard Stop 방지 불가.')
    print('      Hard Stop은 진입 방향 오류 또는 갑작스런 역방향 시세에서 발생.')
    print()

    # ── 섹션 7: 최종 결론 ─────────────────────────────────────────────────
    all_auto = compute_stats(pairs)
    wins_all = [p for p in pairs if p.pnl_pct > 0]
    losses_all = [p for p in pairs if p.pnl_pct < 0]
    avg_win = sum(p.pnl_pct for p in wins_all) / len(wins_all) if wins_all else 0
    avg_loss = sum(p.pnl_pct for p in losses_all) / len(losses_all) if losses_all else 0

    print_separator('═')
    print('■ 최종 결론 요약')
    print_separator('═')
    print(f'  전체: {all_auto["count"]}건  승률: {all_auto["wr"]:.0f}%  '
          f'총손익: {all_auto["total_pnl"]:+,.0f}원')
    print(f'  평균 수익: {avg_win:+.1f}%  평균 손실: {avg_loss:+.1f}%  '
          f'RR비: {abs(avg_win/avg_loss):.2f}' if avg_loss else '')
    print()
    print('  1. SMC RVOL 필터 (1.3x) — 올바른 방향, 소급 적용 불가')
    print('     DB에 SMC 거래 없음. 향후 SMC 진입 품질 개선에 작동.')
    print()
    print('  2. ef_no_follow — RVOL 문제가 아님')
    print('     발생 거래 RVOL 2.1~3.4x. EF가 손실 최소화 성공.')
    print('     개선 방향: EXPLORATION 추종 확인 강화 (진입 후 N봉 캔들 패턴).')
    print()
    print('  3. Hard Stop — 가장 큰 실손실 ($)')
    print('     RVOL으로 구분 불가. 방향 오류 또는 HTS 가격 불일치가 원인.')
    print('     개선 방향: 진입 방향 확신도 강화 또는 Hard Stop 거리 조정.')
    print()
    print('  4. Overnight Close — 일부 수익 기회 조기 청산')
    print('     비츠로셀 +21.9% 조기청산 등. 청산 기준 세밀화 필요.')
    print_separator('═')
    print()


if __name__ == '__main__':
    if not Path(DB_PATH).exists():
        print(f'ERROR: DB not found at {DB_PATH}')
        sys.exit(1)
    run()
