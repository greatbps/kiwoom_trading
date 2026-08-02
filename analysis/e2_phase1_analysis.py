"""
E2 Phase 1 분석 스크립트 — GD-008 늦은 진입 개선 프로젝트
======================================================
실행 조건: instrumented cohort (candidate_first_time IS NOT NULL) 30건 이상
실행 방법: python -m analysis.e2_phase1_analysis [--min-trades N] [--no-chart]

출력:
  - H-002: 후보 과열 분석 (candidate 시점 5일/10일 상승률)
  - H-003: 후보→진입 지연 분석 (cand_to_entry_min/pct)
  - H-005: 진입 시간대 분석 (entry_time_bucket별 승률)
  - logs/e2_phase1_report_YYYYMMDD.md
"""

import argparse
import sys
import json
import warnings
from pathlib import Path
from datetime import datetime, timedelta, date

import psycopg2
import pandas as pd
import os
from dotenv import load_dotenv

load_dotenv()

warnings.filterwarnings('ignore')

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

DB_DSN = dict(dbname='trading_system', user='postgres', password=os.getenv('POSTGRES_PASSWORD'), host='localhost')
INSTRUMENTATION_START = date(2026, 7, 4)   # candidate_first_time 기록 시작일


# ─────────────────────────────────────────────────────────────────────────────
# 데이터 로드
# ─────────────────────────────────────────────────────────────────────────────

def load_trades(min_trades: int = 30) -> pd.DataFrame:
    """
    instrumented cohort BUY+SELL 페어 로드.
    candidate_first_time IS NOT NULL 조건 필수 (H-003 오염 방지).
    """
    conn = psycopg2.connect(**DB_DSN)
    try:
        df = pd.read_sql("""
            SELECT
                b.trade_id,
                b.stock_code                                            AS symbol,
                b.stock_name                                            AS name,
                b.candidate_first_time,
                b.candidate_first_price,
                b.entry_signal_time,
                b.entry_signal_price,
                b.entry_time                                            AS buy_time,
                b.price                                                 AS buy_price,

                -- H-003 gap
                EXTRACT(EPOCH FROM (b.entry_signal_time - b.candidate_first_time)) / 60.0
                                                                        AS cand_to_entry_min,
                ROUND(
                    (b.entry_signal_price::numeric - b.candidate_first_price)
                    / NULLIF(b.candidate_first_price, 0) * 100, 2
                )                                                       AS cand_to_entry_pct,

                -- H-005 시간대
                TO_CHAR(b.entry_signal_time AT TIME ZONE 'Asia/Seoul', 'HH24:MI')
                                                                        AS entry_time_kst,
                CASE
                    WHEN EXTRACT(HOUR FROM b.entry_signal_time AT TIME ZONE 'Asia/Seoul') = 9
                     AND EXTRACT(MINUTE FROM b.entry_signal_time AT TIME ZONE 'Asia/Seoul') < 30
                    THEN 'A_09:00-09:30'
                    WHEN EXTRACT(HOUR FROM b.entry_signal_time AT TIME ZONE 'Asia/Seoul') = 9
                     AND EXTRACT(MINUTE FROM b.entry_signal_time AT TIME ZONE 'Asia/Seoul') >= 30
                    THEN 'B_09:30-10:30'
                    WHEN EXTRACT(HOUR FROM b.entry_signal_time AT TIME ZONE 'Asia/Seoul') = 10
                    THEN 'B_09:30-10:30'
                    WHEN EXTRACT(HOUR FROM b.entry_signal_time AT TIME ZONE 'Asia/Seoul') = 11
                     AND EXTRACT(MINUTE FROM b.entry_signal_time AT TIME ZONE 'Asia/Seoul') < 30
                    THEN 'C_10:30-11:30'
                    ELSE 'D_11:30+'
                END                                                     AS entry_bucket,

                -- 결과
                s.price                                                 AS exit_price,
                s.profit_rate,
                b.mfe_pct                                               AS mfe,
                b.mae_pct                                               AS mae,
                s.exit_reason,
                CASE
                    WHEN s.exit_reason ILIKE '%%Early Failure%%' THEN 'EF'
                    WHEN (s.profit_rate) > 0 THEN 'WIN'
                    WHEN (s.profit_rate) < 0 THEN 'LOSS'
                    ELSE 'FLAT'
                END                                                     AS result_class

            FROM trades b
            LEFT JOIN trades s
                ON b.stock_code = s.stock_code
               AND s.trade_type = 'SELL'
               AND s.entry_time BETWEEN b.entry_time AND b.entry_time + INTERVAL '30 days'
            WHERE b.trade_type = 'BUY'
              AND b.candidate_first_time IS NOT NULL
              AND DATE(b.entry_time) >= %s
            ORDER BY b.entry_time
        """, conn, params=[INSTRUMENTATION_START])
    finally:
        conn.close()

    # 중복 제거 (BUY에 SELL이 여러 개 붙는 경우)
    df = df.drop_duplicates(subset='trade_id')

    n = len(df)
    print(f"\n[데이터] instrumented cohort: {n}건 (기준일: {INSTRUMENTATION_START})")
    if n < min_trades:
        print(f"⚠  분석 최소 건수 {min_trades}건 미달 ({n}건). E2 도달 후 실행하세요.")
        sys.exit(0)
    return df


def fetch_prior_return(symbol: str, ref_date: date, days: int) -> float | None:
    """yfinance로 ref_date 기준 days일 전 대비 상승률(%) 반환."""
    try:
        import yfinance as yf
        for suffix in ['.KS', '.KQ']:
            df = yf.download(
                f"{symbol}{suffix}",
                start=ref_date - timedelta(days=days + 10),
                end=ref_date + timedelta(days=1),
                interval='1d', progress=False, auto_adjust=True
            )
            if not df.empty and len(df) >= days + 1:
                break
        if df.empty:
            return None
        before = df[df.index.date < ref_date]
        if len(before) < days:
            return None
        ref_px = float(before['Close'].iloc[-days])
        cur_px = float(before['Close'].iloc[-1])
        return round((cur_px - ref_px) / ref_px * 100, 2)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# H-002: 후보 과열 분석
# ─────────────────────────────────────────────────────────────────────────────

def analyze_h002(df: pd.DataFrame) -> dict:
    """candidate_first_price 기준 5일/10일 상승률과 결과 상관 분석."""
    print("\n" + "=" * 55)
    print("H-002: 후보 과열 가설 분석")
    print("=" * 55)

    records = []
    for _, row in df.iterrows():
        sym  = row['symbol']
        cand_date = pd.Timestamp(row['candidate_first_time']).date()
        r5  = fetch_prior_return(sym, cand_date, 5)
        r10 = fetch_prior_return(sym, cand_date, 10)
        records.append({
            'symbol': sym,
            'result': row['result_class'],
            'profit_rate': row['profit_rate'],
            'rise_5d':  r5,
            'rise_10d': r10,
        })

    rdf = pd.DataFrame(records).dropna(subset=['rise_5d'])
    wins   = rdf[rdf['result'] == 'WIN']
    losses = rdf[rdf['result'].isin(['LOSS', 'EF'])]

    print(f"\n  {'':12} {'승매':>8} {'패매':>8}")
    print(f"  {'5일前比':12} {wins['rise_5d'].mean():>+7.1f}%  {losses['rise_5d'].mean():>+7.1f}%")
    print(f"  {'10일前比':12} {wins['rise_10d'].mean():>+7.1f}%  {losses['rise_10d'].mean():>+7.1f}%")

    # 임계값 후보 탐색: 5일 상승률 구간별 승률
    print("\n  5일 상승률 구간별 승률:")
    bins = [(-999, 0), (0, 5), (5, 10), (10, 20), (20, 999)]
    rows = []
    for lo, hi in bins:
        seg = rdf[(rdf['rise_5d'] >= lo) & (rdf['rise_5d'] < hi)]
        if len(seg) == 0:
            continue
        n_win = (seg['result'] == 'WIN').sum()
        wr = n_win / len(seg) * 100
        rows.append({'구간': f"{lo:+d}~{hi:+d}%", 'n': len(seg), '승률': f"{wr:.0f}%",
                     '평균손익': f"{seg['profit_rate'].mean():+.2f}%"})
        print(f"    {lo:+4d}~{hi:+4d}%  n={len(seg):2d}  승률={wr:.0f}%  평균={seg['profit_rate'].mean():+.2f}%")

    return {'wins_5d': float(wins['rise_5d'].mean()), 'losses_5d': float(losses['rise_5d'].mean()),
            'wins_10d': float(wins['rise_10d'].mean()), 'losses_10d': float(losses['rise_10d'].mean()),
            'n_total': len(rdf), 'bins': rows}


# ─────────────────────────────────────────────────────────────────────────────
# H-003: 후보→진입 지연 분석
# ─────────────────────────────────────────────────────────────────────────────

def analyze_h003(df: pd.DataFrame) -> dict:
    """cand_to_entry_min / cand_to_entry_pct 승/패/EF 비교."""
    print("\n" + "=" * 55)
    print("H-003: SMC 추가 지연 가설 분석")
    print("=" * 55)

    sub = df[df['cand_to_entry_min'].notna()].copy()
    sub['cand_to_entry_min']  = sub['cand_to_entry_min'].astype(float)
    sub['cand_to_entry_pct']  = sub['cand_to_entry_pct'].astype(float)

    groups = {
        'WIN':  sub[sub['result_class'] == 'WIN'],
        'LOSS': sub[sub['result_class'] == 'LOSS'],
        'EF':   sub[sub['result_class'] == 'EF'],
    }

    print(f"\n  {'':6} {'n':>4} {'지연(분)':>10} {'가격지연(%)':>12} {'avg손익':>10}")
    print("  " + "-" * 50)
    results = {}
    for label, g in groups.items():
        if len(g) == 0:
            continue
        avg_min = g['cand_to_entry_min'].mean()
        avg_pct = g['cand_to_entry_pct'].mean()
        avg_pnl = g['profit_rate'].mean()
        print(f"  {label:6} {len(g):>4}  {avg_min:>9.1f}분  {avg_pct:>+10.2f}%  {avg_pnl:>+9.2f}%")
        results[label] = {'n': len(g), 'avg_min': avg_min, 'avg_pct': float(avg_pct), 'avg_pnl': float(avg_pnl)}

    # 지연 구간별 분포
    print("\n  지연(분) 구간별 승률:")
    bins_min = [(0, 5), (5, 15), (15, 30), (30, 60), (60, 999)]
    bin_rows = []
    for lo, hi in bins_min:
        seg = sub[(sub['cand_to_entry_min'] >= lo) & (sub['cand_to_entry_min'] < hi)]
        if len(seg) == 0:
            continue
        wr = (seg['result_class'] == 'WIN').sum() / len(seg) * 100
        bin_rows.append({'구간': f"{lo}~{hi}분", 'n': len(seg), '승률': f"{wr:.0f}%"})
        print(f"    {lo:3d}~{hi:3d}분  n={len(seg):2d}  승률={wr:.0f}%  "
              f"avg_pct={seg['cand_to_entry_pct'].mean():+.2f}%")

    return {'groups': results, 'bins': bin_rows}


# ─────────────────────────────────────────────────────────────────────────────
# H-005: 진입 시간대 분석
# ─────────────────────────────────────────────────────────────────────────────

def analyze_h005(df: pd.DataFrame) -> dict:
    """entry_bucket별 승률 / 평균손익."""
    print("\n" + "=" * 55)
    print("H-005: 진입 시간대 가설 분석")
    print("=" * 55)

    sub = df[df['entry_bucket'].notna()].copy()
    bucket_order = ['A_09:00-09:30', 'B_09:30-10:30', 'C_10:30-11:30', 'D_11:30+']

    print(f"\n  {'버킷':20} {'n':>4} {'승률':>8} {'평균손익':>10} {'MAE':>8}")
    print("  " + "-" * 56)
    results = {}
    for b in bucket_order:
        g = sub[sub['entry_bucket'] == b]
        if len(g) == 0:
            continue
        wr  = (g['result_class'] == 'WIN').sum() / len(g) * 100
        pnl = g['profit_rate'].mean()
        mae = g['mae'].mean() if g['mae'].notna().any() else float('nan')
        label = b.split('_', 1)[1]
        print(f"  {label:20} {len(g):>4}  {wr:>7.0f}%  {pnl:>+9.2f}%  {mae:>+7.1f}%")
        results[b] = {'n': len(g), 'win_rate': wr, 'avg_pnl': float(pnl)}

    return results


# ─────────────────────────────────────────────────────────────────────────────
# Before/After 비교표 (Phase 3 검증용 — 현재는 Before만)
# ─────────────────────────────────────────────────────────────────────────────

def print_baseline_table(df: pd.DataFrame):
    """Phase 3 이전 베이스라인 성과 테이블."""
    print("\n" + "=" * 55)
    print("베이스라인 성과 (Before — Phase 3 개선 전)")
    print("=" * 55)

    total = len(df)
    wins  = df[df['result_class'] == 'WIN']
    losses= df[df['result_class'].isin(['LOSS', 'EF'])]
    ef    = df[df['result_class'] == 'EF']

    wr    = len(wins) / total * 100 if total else 0
    avg_p = df['profit_rate'].mean()
    gains = wins['profit_rate'].mean() if len(wins) else 0
    losss = losses['profit_rate'].mean() if len(losses) else 0
    pf    = abs(gains / losss) if losss != 0 else float('nan')
    ef_r  = len(ef) / total * 100 if total else 0
    mae   = df['mae'].mean() if df['mae'].notna().any() else float('nan')
    mfe   = df['mfe'].mean() if df['mfe'].notna().any() else float('nan')
    avg_delay = df['cand_to_entry_min'].mean() if df['cand_to_entry_min'].notna().any() else float('nan')

    rows = [
        ("승률",               f"{wr:.1f}%"),
        ("평균손익",           f"{avg_p:+.2f}%"),
        ("Profit Factor",      f"{pf:.2f}"),
        ("Early Failure 비율", f"{ef_r:.1f}%"),
        ("평균 MAE",           f"{mae:+.1f}%"),
        ("평균 MFE",           f"{mfe:+.1f}%"),
        ("평균 후보→진입 지연",f"{avg_delay:.1f}분"),
        ("총 거래",            f"{total}건"),
    ]
    for label, val in rows:
        print(f"  {label:22} {val}")


# ─────────────────────────────────────────────────────────────────────────────
# 리포트 저장
# ─────────────────────────────────────────────────────────────────────────────

def save_report(h002: dict, h003: dict, h005: dict, df: pd.DataFrame):
    report_path = project_root / 'logs' / f"e2_phase1_report_{datetime.now().strftime('%Y%m%d')}.md"

    lines = [
        f"# E2 Phase 1 분석 리포트\n",
        f"생성일시: {datetime.now().strftime('%Y-%m-%d %H:%M')}\n",
        f"분석 건수: {len(df)}건 (instrumented cohort, {INSTRUMENTATION_START} 이후)\n",
        "\n---\n",
        "## H-002: 후보 과열 가설\n",
        f"- 승매 5일前比: {h002['wins_5d']:+.1f}%\n",
        f"- 패매 5일前比: {h002['losses_5d']:+.1f}%\n",
        f"- 승매 10일前比: {h002['wins_10d']:+.1f}%\n",
        f"- 패매 10일前比: {h002['losses_10d']:+.1f}%\n",
        "\n## H-003: SMC 추가 지연 가설\n",
    ]
    for g, v in h003.get('groups', {}).items():
        lines.append(f"- {g}: 지연 {v['avg_min']:.1f}분, 가격지연 {v['avg_pct']:+.2f}%, 평균손익 {v['avg_pnl']:+.2f}%\n")

    lines += ["\n## H-005: 진입 시간대 가설\n"]
    for b, v in h005.items():
        label = b.split('_', 1)[1]
        lines.append(f"- {label}: n={v['n']}, 승률={v['win_rate']:.0f}%, 평균손익={v['avg_pnl']:+.2f}%\n")

    lines += ["\n## 다음 단계\n",
              "1. H-002 임계값 제안 → 개선안 A(과열 필터) 설계\n",
              "2. H-003 지연 분포 확인 → 개선안 B(SMC 앞당기기) 검토\n",
              "3. H-005 버킷 비교 → 개선안 C(시간대 필터) 검토\n",
              "\n참조: GD-008, NB-007\n"]

    report_path.write_text(''.join(lines), encoding='utf-8')
    print(f"\n리포트 저장: {report_path}")


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='E2 Phase 1 늦은 진입 분석')
    parser.add_argument('--min-trades', type=int, default=30,
                        help='분석 최소 건수 (권고: 50~60건)')
    parser.add_argument('--no-chart', action='store_true')
    args = parser.parse_args()

    print("E2 Phase 1 분석 — 늦은 진입 개선 프로젝트 (GD-008)")
    print(f"분석 대상: instrumented cohort ({INSTRUMENTATION_START} 이후, candidate_first_time IS NOT NULL)")
    print(f"최소 건수: {args.min_trades}건 (권고: 50~60건 확보 후 임계값 결정)")

    df = load_trades(min_trades=args.min_trades)
    print_baseline_table(df)

    h002 = analyze_h002(df)
    h003 = analyze_h003(df)
    h005 = analyze_h005(df)
    save_report(h002, h003, h005, df)

    print("\n" + "=" * 55)
    print("Phase 1 분석 완료. GD-008 Phase 2로 진행하려면:")
    print("  개선안 선택 후 approve_proposal.py --proposal <설계안> 실행")
    print("=" * 55)


if __name__ == '__main__':
    main()
