"""
2주 운영 후 자동 분석 스크립트

사용법:
    python -m analysis.trade_analyzer             # 올해 전체
    python -m analysis.trade_analyzer --days 14   # 최근 14일
    python -m analysis.trade_analyzer --csv logs/trade_log_2026.csv

출력:
  1. 기본 성과 (거래 수, 승률, MDD, 평균 수익)
  2. 점수별 성과 → "score 2 vs 3+"
  3. 시간대별 성과 → "언제 들어가야 하나"
  4. 종목별 성과 → "쓰레기 종목 목록"
  5. 튜닝 권고 → "딱 1개 수정 가이드"
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOG_DIR = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / 'logs'

logger = logging.getLogger(__name__)

# ── 기준선 (백테스트 검증값) ──────────────────────────────────────────────
TARGET_WIN_RATE  = 0.30   # 목표 승률 30%
TARGET_RR        = 2.0    # 목표 RR 2.0
TARGET_MDD       = -0.05  # 허용 MDD -5%


def load_log(csv_path: str = None, days: int = None) -> pd.DataFrame:
    """trade_log CSV 로드."""
    if csv_path:
        path = Path(csv_path)
    else:
        year = datetime.today().year
        path = LOG_DIR / f'trade_log_{year}.csv'

    if not path.exists():
        print(f'[ERROR] 파일 없음: {path}')
        sys.exit(1)

    df = pd.read_csv(path, parse_dates=['date'])

    if days:
        cutoff = datetime.today() - timedelta(days=days)
        df = df[df['date'] >= cutoff]

    if df.empty:
        print('[INFO] 해당 기간 거래 없음')
        sys.exit(0)

    return df


def analyze(df: pd.DataFrame, verbose: bool = True) -> dict:
    """전체 분석 실행. Returns: summary dict."""

    total    = len(df)
    wins     = (df['pnl_pct'] > 0).sum()
    losses   = (df['pnl_pct'] <= 0).sum()
    win_rate = wins / total if total > 0 else 0

    avg_win  = df.loc[df['pnl_pct'] > 0, 'pnl_pct'].mean() if wins > 0 else 0
    avg_loss = df.loc[df['pnl_pct'] <= 0, 'pnl_pct'].mean() if losses > 0 else 0
    rr       = abs(avg_win / avg_loss) if avg_loss != 0 else 0

    cumulative = df['pnl_pct'].cumsum()
    mdd        = float((cumulative - cumulative.cummax()).min())

    if verbose:
        print(f'\n{"="*60}')
        print(f'  거래 성과 분석  (총 {total}건)')
        print(f'{"="*60}')
        print(f'  승률      : {win_rate*100:.1f}%  (목표 {TARGET_WIN_RATE*100:.0f}%+)')
        print(f'  R:R       : {rr:.2f}  (목표 {TARGET_RR}+)')
        print(f'  MDD       : {mdd*100:.1f}%  (허용 {TARGET_MDD*100:.0f}%)')
        print(f'  평균 수익 : {avg_win*100:+.2f}%  |  평균 손실: {avg_loss*100:+.2f}%')
        print(f'  누적 수익 : {cumulative.iloc[-1]*100:+.1f}%')

    # ── 점수별 성과 ───────────────────────────────────────────────────────
    if verbose:
        print(f'\n  [점수별 성과]')
        score_grp = df.groupby('score')['pnl_pct'].agg(['count', 'mean', lambda x: (x>0).mean()])
        score_grp.columns = ['count', 'avg_pnl', 'win_rate']
        for s, row in score_grp.iterrows():
            verdict = '✅ GOOD' if row['win_rate'] >= 0.35 else ('⚠️ OK' if row['win_rate'] >= 0.25 else '❌ BAD')
            print(f'    score={s}: {int(row["count"])}건  '
                  f'win={row["win_rate"]*100:.1f}%  '
                  f'avg={row["avg_pnl"]*100:+.2f}%  {verdict}')

    # ── 시간대별 성과 ─────────────────────────────────────────────────────
    if verbose and 'entry_time' in df.columns:
        print(f'\n  [시간대별 성과]')
        df['hour'] = pd.to_datetime(df['entry_time'], format='%H:%M', errors='coerce').dt.hour
        hour_grp = df.groupby('hour')['pnl_pct'].agg(['count', 'mean', lambda x: (x>0).mean()])
        hour_grp.columns = ['count', 'avg_pnl', 'win_rate']
        for h, row in hour_grp.iterrows():
            bar = '█' * int(row['win_rate'] * 10)
            print(f'    {h:02d}:xx  {int(row["count"]):3d}건  '
                  f'win={row["win_rate"]*100:.1f}%  avg={row["avg_pnl"]*100:+.2f}%  {bar}')

    # ── 종목별 성과 ───────────────────────────────────────────────────────
    if verbose:
        print(f'\n  [종목별 성과] (누적 손익 순)')
        sym_grp = df.groupby('symbol')['pnl_pct'].agg(['count', 'sum', 'mean',
                                                        lambda x: (x>0).mean()])
        sym_grp.columns = ['count', 'total', 'avg', 'win_rate']
        sym_grp = sym_grp.sort_values('total', ascending=False)
        for sym, row in sym_grp.iterrows():
            flag = '🔥' if row['total'] > 0.05 else ('💀' if row['total'] < -0.03 else '  ')
            print(f'    {flag} {sym:<8}  {int(row["count"])}건  '
                  f'win={row["win_rate"]*100:.0f}%  '
                  f'tot={row["total"]*100:+.1f}%  avg={row["avg"]*100:+.2f}%')

    # ── 청산 이유 분포 ────────────────────────────────────────────────────
    if verbose and 'exit_reason' in df.columns:
        print(f'\n  [청산 이유]')
        for reason, cnt in df['exit_reason'].value_counts().items():
            avg_pnl = df.loc[df['exit_reason'] == reason, 'pnl_pct'].mean()
            print(f'    {reason:<12}: {cnt:3d}건  avg={avg_pnl*100:+.2f}%')

    # ── 튜닝 권고 (딱 1개) ───────────────────────────────────────────────
    advice = _get_tuning_advice(win_rate, rr, mdd)
    if verbose:
        print(f'\n  {"="*55}')
        print(f'  [튜닝 권고 — 딱 1개만]')
        print(f'  {advice}')
        print(f'  {"="*55}')

    return {
        'total':    total,
        'win_rate': win_rate,
        'rr':       rr,
        'mdd':      mdd,
        'advice':   advice,
        'pass':     win_rate >= TARGET_WIN_RATE and rr >= TARGET_RR and mdd >= TARGET_MDD,
    }


def _get_tuning_advice(win_rate: float, rr: float, mdd: float) -> str:
    """
    3가지 케이스 중 가장 심각한 문제 하나만 권고.
    우선순위: MDD > 승률 > RR
    """
    if mdd < TARGET_MDD:
        return (
            f'❌ MDD {mdd*100:.1f}% 초과 → SL 줄이기: -1% → -0.7%\n'
            f'  변경: engine에서 sl_pct = -0.007 (현재 -0.01)'
        )
    if win_rate < 0.25:
        return (
            f'❌ 승률 {win_rate*100:.1f}% 너무 낮음 → 진입 기준 높이기: score >= 3\n'
            f'  변경: ScoreEngine(min_score=3)  또는 daily_scan 우선 종목만'
        )
    if rr < 1.5:
        return (
            f'⚠️ R:R {rr:.2f} 낮음 → TP 늘리기: +4% → +5~6%\n'
            f'  변경: tp_pct = 0.05 또는 0.06'
        )
    return f'✅ 모든 지표 정상 (win={win_rate*100:.1f}% RR={rr:.2f} MDD={mdd*100:.1f}%) → 수정 없이 유지'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='거래 결과 자동 분석')
    parser.add_argument('--csv',  type=str, default=None, help='CSV 파일 경로')
    parser.add_argument('--days', type=int, default=None, help='최근 N일')
    args = parser.parse_args()

    df = load_log(args.csv, args.days)
    print(f'  기간: {df["date"].min().date()} ~ {df["date"].max().date()}')
    result = analyze(df)

    sys.exit(0 if result['pass'] else 1)
