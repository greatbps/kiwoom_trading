"""
쿨다운 최적화 분석 모듈 (오프라인)

Usage:
    python -m analysis.cooldown_optimizer [--days 7]

실매매 SELL 거래 로그 기반 가상 재진입 시뮬레이션으로
exit_reason 카테고리별 최적 쿨다운 시간을 역산한다.

yfinance 5분봉 최대 7일 lookback 제약.
"""

import argparse
import json
import os
import warnings
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings('ignore')

# 쿨다운 그리드: bars (5분봉 기준) → 분 환산
COOLDOWN_GRID_BARS = [0, 2, 4, 6, 8, 12]  # 0, 10, 20, 30, 40, 60분


class CooldownOptimizer:
    """실매매 로그 기반 쿨다운 최적화 분석"""

    def __init__(self, days: int = 7):
        self.days = days
        self.results: List[Dict] = []
        self.sell_trades: List[Dict] = []

    def load_sell_trades(self) -> int:
        """PostgreSQL trades 테이블에서 SELL 거래 로드"""
        from database.trading_db import TradingDatabase

        db = TradingDatabase()
        end_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        start_date = (datetime.now() - timedelta(days=self.days)).strftime('%Y-%m-%d %H:%M:%S')

        all_trades = db.get_trades(start_date=start_date, end_date=end_date)

        # SELL 거래만 필터
        self.sell_trades = [
            t for t in all_trades
            if str(t.get('trade_type', '')).upper() == 'SELL'
        ]

        print(f"  SELL 거래 로드: {len(self.sell_trades)}건 (최근 {self.days}일)")
        return len(self.sell_trades)

    def _fetch_post_exit_data(
        self,
        stock_code: str,
        exit_time: datetime
    ) -> Optional[pd.DataFrame]:
        """yfinance 5분봉으로 exit 후 60bars(5시간) 데이터 조회"""
        try:
            import yfinance as yf

            # KRX 종목코드 → yfinance ticker
            ticker = f"{stock_code}.KS"
            stock = yf.Ticker(ticker)
            df = stock.history(period=f"{self.days}d", interval="5m")

            if df.empty:
                # KOSDAQ 시도
                ticker = f"{stock_code}.KQ"
                stock = yf.Ticker(ticker)
                df = stock.history(period=f"{self.days}d", interval="5m")

            if df.empty or len(df) < 10:
                return None

            df.reset_index(inplace=True)
            df.columns = [col.lower() for col in df.columns]

            # exit_time 이후 데이터만 (최대 60 bars)
            if 'datetime' in df.columns:
                dt_col = 'datetime'
            elif 'date' in df.columns:
                dt_col = 'date'
            else:
                return None

            # timezone-aware 비교를 위해 tz 통일
            if df[dt_col].dt.tz is not None:
                exit_time_tz = pd.Timestamp(exit_time).tz_localize(df[dt_col].dt.tz)
            else:
                exit_time_tz = pd.Timestamp(exit_time)

            post_exit = df[df[dt_col] > exit_time_tz].head(60)

            if len(post_exit) < 3:
                return None

            # ATR 계산
            high_low = post_exit['high'] - post_exit['low']
            high_close = abs(post_exit['high'] - post_exit['close'].shift(1))
            low_close = abs(post_exit['low'] - post_exit['close'].shift(1))
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            post_exit = post_exit.copy()
            post_exit['atr'] = tr.rolling(14).mean()

            return post_exit

        except Exception as e:
            print(f"    [skip] {stock_code} 데이터 조회 실패: {e}")
            return None

    def simulate_reentry(
        self,
        df: pd.DataFrame,
        entry_bar_idx: int,
        atr: float,
        direction: str = 'long'
    ) -> Dict:
        """
        ATR 기반 가상 재진입 시뮬레이션.

        SL = entry - 0.7*ATR, TP = entry + 1.0*ATR (long 기준)
        high/low 기반 체결 판정 (국장 특화).
        """
        if entry_bar_idx >= len(df):
            return {'result': 'no_data', 'pnl_pct': 0.0}

        entry_price = df['close'].iloc[entry_bar_idx]
        if entry_price <= 0 or atr <= 0:
            return {'result': 'invalid', 'pnl_pct': 0.0}

        if direction == 'long':
            sl_price = entry_price - 0.7 * atr
            tp_price = entry_price + 1.0 * atr
        else:
            sl_price = entry_price + 0.7 * atr
            tp_price = entry_price - 1.0 * atr

        # 진입 이후 bar들에서 체결 판정
        for i in range(entry_bar_idx + 1, len(df)):
            high = df['high'].iloc[i]
            low = df['low'].iloc[i]

            if direction == 'long':
                # SL 먼저 체크 (국장 하방 우선)
                if low <= sl_price:
                    pnl_pct = ((sl_price - entry_price) / entry_price) * 100
                    return {'result': 'sl', 'pnl_pct': pnl_pct, 'bars': i - entry_bar_idx}
                if high >= tp_price:
                    pnl_pct = ((tp_price - entry_price) / entry_price) * 100
                    return {'result': 'tp', 'pnl_pct': pnl_pct, 'bars': i - entry_bar_idx}
            else:
                if high >= sl_price:
                    pnl_pct = ((entry_price - sl_price) / entry_price) * 100
                    return {'result': 'sl', 'pnl_pct': pnl_pct, 'bars': i - entry_bar_idx}
                if low <= tp_price:
                    pnl_pct = ((entry_price - tp_price) / entry_price) * 100
                    return {'result': 'tp', 'pnl_pct': pnl_pct, 'bars': i - entry_bar_idx}

        # 미체결: 마지막 봉 종가 기준
        last_close = df['close'].iloc[-1]
        if direction == 'long':
            pnl_pct = ((last_close - entry_price) / entry_price) * 100
        else:
            pnl_pct = ((entry_price - last_close) / entry_price) * 100
        return {'result': 'timeout', 'pnl_pct': pnl_pct, 'bars': len(df) - entry_bar_idx}

    def run(self):
        """쿨다운 그리드 sweep 실행"""
        from metrics.reentry_metrics import categorize_exit_reason

        if not self.sell_trades:
            print("  SELL 거래가 없습니다.")
            return

        # 종목별 데이터 캐시 (같은 종목 반복 조회 방지)
        data_cache: Dict[str, Optional[pd.DataFrame]] = {}

        for idx, trade in enumerate(self.sell_trades):
            stock_code = trade.get('stock_code', '')
            trade_time = trade.get('trade_time')
            exit_reason = trade.get('exit_reason', '')
            exit_price = float(trade.get('price', 0))

            if not stock_code or not trade_time:
                continue

            # trade_time을 datetime으로 변환
            if isinstance(trade_time, str):
                try:
                    trade_time = datetime.fromisoformat(trade_time)
                except ValueError:
                    continue

            reason_cat = categorize_exit_reason(exit_reason)

            print(f"  [{idx+1}/{len(self.sell_trades)}] {stock_code} "
                  f"({reason_cat}) {trade_time.strftime('%m/%d %H:%M')}", end="")

            # 데이터 조회 (캐시)
            if stock_code not in data_cache:
                data_cache[stock_code] = self._fetch_post_exit_data(stock_code, trade_time)
            post_df = data_cache[stock_code]

            if post_df is None or len(post_df) < 3:
                print(" → skip (데이터 없음)")
                continue

            # ATR 값 (exit 직후 bar 기준)
            atr_values = post_df['atr'].dropna()
            if len(atr_values) == 0:
                atr = exit_price * 0.015  # fallback 1.5%
            else:
                atr = atr_values.iloc[0]

            # 각 쿨다운 레벨에서 가상 재진입
            for cooldown_bars in COOLDOWN_GRID_BARS:
                sim = self.simulate_reentry(
                    df=post_df,
                    entry_bar_idx=cooldown_bars,
                    atr=atr,
                    direction='long'
                )

                self.results.append({
                    'stock_code': stock_code,
                    'trade_time': trade_time.isoformat(),
                    'exit_reason': exit_reason,
                    'reason_category': reason_cat,
                    'cooldown_bars': cooldown_bars,
                    'cooldown_minutes': cooldown_bars * 5,
                    'entry_price': float(post_df['close'].iloc[cooldown_bars])
                                   if cooldown_bars < len(post_df) else 0,
                    'sim_result': sim['result'],
                    'pnl_pct': sim['pnl_pct'],
                    'sim_bars': sim.get('bars', 0),
                })

            print(f" → {len(COOLDOWN_GRID_BARS)} 시뮬레이션 완료")

    def analyze(self) -> Dict:
        """exit_reason 카테고리별 최적 쿨다운 분석 (max avg_pnl)"""
        if not self.results:
            return {}

        df = pd.DataFrame(self.results)

        analysis = {}
        categories = df['reason_category'].unique()

        for cat in categories:
            cat_df = df[df['reason_category'] == cat]
            cat_analysis = {}

            for cd_bars in COOLDOWN_GRID_BARS:
                cd_df = cat_df[cat_df['cooldown_bars'] == cd_bars]
                if len(cd_df) == 0:
                    continue

                cd_minutes = cd_bars * 5
                count = len(cd_df)
                avg_pnl = cd_df['pnl_pct'].mean()
                win_rate = (cd_df['pnl_pct'] > 0).sum() / count * 100
                tp_count = (cd_df['sim_result'] == 'tp').sum()
                sl_count = (cd_df['sim_result'] == 'sl').sum()

                cat_analysis[cd_minutes] = {
                    'count': count,
                    'avg_pnl': round(avg_pnl, 3),
                    'win_rate': round(win_rate, 1),
                    'tp': tp_count,
                    'sl': sl_count,
                }

            # 최적 쿨다운 찾기 (count >= 5인 것 중 max avg_pnl)
            valid = {k: v for k, v in cat_analysis.items() if v['count'] >= 5}
            if valid:
                best_cd = max(valid.keys(), key=lambda k: valid[k]['avg_pnl'])
                optimal = {
                    'optimal_cooldown_min': best_cd,
                    'avg_pnl': valid[best_cd]['avg_pnl'],
                    'win_rate': valid[best_cd]['win_rate'],
                    'sample_count': valid[best_cd]['count'],
                }
            else:
                optimal = {'optimal_cooldown_min': None, 'note': 'insufficient data (< 5 trades)'}

            analysis[cat] = {
                'grid': cat_analysis,
                'optimal': optimal,
            }

        return analysis

    def print_report(self):
        """Rich 테이블로 결과 출력"""
        analysis = self.analyze()
        if not analysis:
            print("  분석 결과 없음")
            return

        try:
            from rich.console import Console
            from rich.table import Table
            console = Console()
        except ImportError:
            # Rich 미설치 시 plain text
            self._print_report_plain(analysis)
            return

        table = Table(title=f"쿨다운 최적화 분석 (최근 {self.days}일)")
        table.add_column("Category", style="bold")
        for cd_bars in COOLDOWN_GRID_BARS:
            table.add_column(f"{cd_bars*5}분", justify="center")
        table.add_column("최적", style="bold green")

        for cat, data in sorted(analysis.items()):
            row = [cat]
            for cd_bars in COOLDOWN_GRID_BARS:
                cd_min = cd_bars * 5
                if cd_min in data['grid']:
                    g = data['grid'][cd_min]
                    cell = f"{g['avg_pnl']:+.2f}%\n{g['win_rate']:.0f}% (n={g['count']})"
                else:
                    cell = "-"
                row.append(cell)

            opt = data['optimal']
            if opt.get('optimal_cooldown_min') is not None:
                row.append(f"{opt['optimal_cooldown_min']}분\n({opt['avg_pnl']:+.2f}%)")
            else:
                row.append("N/A")

            table.add_row(*row)

        console.print(table)

    def _print_report_plain(self, analysis: Dict):
        """Plain text 출력 (Rich 미설치 시)"""
        print(f"\n{'='*70}")
        print(f"  쿨다운 최적화 분석 (최근 {self.days}일)")
        print(f"{'='*70}")

        for cat, data in sorted(analysis.items()):
            print(f"\n  [{cat}]")
            for cd_bars in COOLDOWN_GRID_BARS:
                cd_min = cd_bars * 5
                if cd_min in data['grid']:
                    g = data['grid'][cd_min]
                    print(f"    {cd_min:3d}분: avg={g['avg_pnl']:+.3f}%  "
                          f"WR={g['win_rate']:.0f}%  TP={g['tp']}  SL={g['sl']}  n={g['count']}")
            opt = data['optimal']
            if opt.get('optimal_cooldown_min') is not None:
                print(f"    → 최적: {opt['optimal_cooldown_min']}분 "
                      f"(avg={opt['avg_pnl']:+.3f}%, WR={opt['win_rate']:.0f}%, n={opt['sample_count']})")
            else:
                print(f"    → 최적: N/A (데이터 부족)")

        print(f"\n{'='*70}\n")

    def visualize(self, log_dir: str = "logs"):
        """기대값 곡선 + 승률 차트 시각화 (PNG 저장)"""
        analysis = self.analyze()
        if not analysis:
            print("  시각화할 데이터 없음")
            return None

        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            from matplotlib import rcParams
        except ImportError:
            print("  matplotlib 미설치 — 시각화 생략")
            return None

        # 한글 폰트 설정
        for font in ['NanumGothic', 'Malgun Gothic', 'AppleGothic', 'DejaVu Sans']:
            try:
                rcParams['font.family'] = font
                break
            except Exception:
                continue
        rcParams['axes.unicode_minus'] = False

        categories = sorted(analysis.keys())
        n_cats = len(categories)
        if n_cats == 0:
            return None

        os.makedirs(log_dir, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')

        # ── 차트 1: 기대값(Avg PnL) 곡선 ──
        fig, axes = plt.subplots(1, 2, figsize=(16, 6))

        # 색상 맵
        colors = ['#e74c3c', '#e67e22', '#2ecc71', '#3498db', '#9b59b6', '#1abc9c', '#7f8c8d']

        ax1 = axes[0]
        ax1.set_title('Exit Reason Category Avg PnL by Cooldown', fontsize=13, fontweight='bold')
        ax1.set_xlabel('Cooldown (min)')
        ax1.set_ylabel('Avg PnL (%)')
        ax1.axhline(y=0, color='gray', linestyle='--', linewidth=0.8, alpha=0.6)

        for i, cat in enumerate(categories):
            grid = analysis[cat]['grid']
            x = sorted(grid.keys())
            y = [grid[k]['avg_pnl'] for k in x]
            color = colors[i % len(colors)]
            ax1.plot(x, y, 'o-', label=cat, color=color, linewidth=2, markersize=7)

            # 최적점 강조
            opt = analysis[cat]['optimal']
            if opt.get('optimal_cooldown_min') is not None:
                opt_x = opt['optimal_cooldown_min']
                opt_y = opt['avg_pnl']
                ax1.plot(opt_x, opt_y, '*', color=color, markersize=16, zorder=5)
                ax1.annotate(
                    f'{opt_x}min\n{opt_y:+.2f}%',
                    xy=(opt_x, opt_y),
                    xytext=(8, 10),
                    textcoords='offset points',
                    fontsize=8,
                    fontweight='bold',
                    color=color,
                )

        ax1.legend(fontsize=9, loc='best')
        ax1.grid(True, alpha=0.3)

        # ── 차트 2: 승률 곡선 ──
        ax2 = axes[1]
        ax2.set_title('Win Rate by Cooldown', fontsize=13, fontweight='bold')
        ax2.set_xlabel('Cooldown (min)')
        ax2.set_ylabel('Win Rate (%)')
        ax2.axhline(y=50, color='gray', linestyle='--', linewidth=0.8, alpha=0.6)

        for i, cat in enumerate(categories):
            grid = analysis[cat]['grid']
            x = sorted(grid.keys())
            y = [grid[k]['win_rate'] for k in x]
            n = [grid[k]['count'] for k in x]
            color = colors[i % len(colors)]
            ax2.plot(x, y, 's-', label=cat, color=color, linewidth=2, markersize=6)

            # 샘플 수 표시
            for xi, yi, ni in zip(x, y, n):
                ax2.annotate(f'n={ni}', xy=(xi, yi), xytext=(0, -14),
                             textcoords='offset points', fontsize=7,
                             ha='center', color=color, alpha=0.7)

        ax2.legend(fontsize=9, loc='best')
        ax2.grid(True, alpha=0.3)
        ax2.set_ylim(0, 100)

        plt.tight_layout()

        chart_path = os.path.join(log_dir, f"cooldown_curves_{today}.png")
        plt.savefig(chart_path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"  [Chart] saved: {chart_path}")
        return chart_path

    def generate_narrative_report(self, log_dir: str = "logs") -> str:
        """
        "왜 이 쿨다운이 최적인가" 해석 narrative 리포트 생성.
        Markdown 형식 → 파일 저장 + 반환.
        """
        analysis = self.analyze()
        if not analysis:
            return ""

        today = datetime.now().strftime('%Y-%m-%d')
        lines = []
        lines.append(f"# Cooldown Optimization Report ({today})")
        lines.append(f"")
        lines.append(f"- Analysis period: {self.days} days")
        lines.append(f"- SELL trades analyzed: {len(self.sell_trades)}")
        lines.append(f"- Total simulations: {len(self.results)}")
        lines.append(f"- Cooldown grid: {[b*5 for b in COOLDOWN_GRID_BARS]} minutes")
        lines.append(f"- SL = entry - 0.7*ATR, TP = entry + 1.0*ATR")
        lines.append(f"")

        for cat in sorted(analysis.keys()):
            data = analysis[cat]
            grid = data['grid']
            opt = data['optimal']

            lines.append(f"---")
            lines.append(f"## {cat}")
            lines.append(f"")

            # 데이터 테이블
            lines.append(f"| Cooldown | Avg PnL | Win Rate | TP | SL | Trades |")
            lines.append(f"|----------|---------|----------|----|----|--------|")
            for cd_min in sorted(grid.keys()):
                g = grid[cd_min]
                marker = " **" if opt.get('optimal_cooldown_min') == cd_min else ""
                lines.append(
                    f"| {cd_min}min{marker} | {g['avg_pnl']:+.3f}% | "
                    f"{g['win_rate']:.0f}% | {g['tp']} | {g['sl']} | {g['count']} |"
                )
            lines.append(f"")

            # Narrative 해석
            if opt.get('optimal_cooldown_min') is not None:
                opt_min = opt['optimal_cooldown_min']
                opt_pnl = opt['avg_pnl']
                opt_wr = opt['win_rate']
                opt_n = opt['sample_count']

                lines.append(f"**Optimal: {opt_min}min (Avg PnL {opt_pnl:+.3f}%, "
                             f"WR {opt_wr:.0f}%, n={opt_n})**")
                lines.append(f"")

                # 구간별 해석 생성
                narrative = self._generate_category_narrative(cat, grid, opt_min)
                lines.append(narrative)
            else:
                lines.append(f"**Optimal: N/A** (insufficient data, < 5 trades per level)")
            lines.append(f"")

        # 종합 권장사항
        lines.append(f"---")
        lines.append(f"## Summary: Recommended YAML Changes")
        lines.append(f"")
        lines.append(f"```yaml")
        lines.append(f"re_entry:")
        lines.append(f"  reentry_cooldown:")
        lines.append(f"    by_exit_reason:")
        for cat in sorted(analysis.keys()):
            opt = analysis[cat]['optimal']
            if opt.get('optimal_cooldown_min') is not None:
                lines.append(f"      {cat}: {opt['optimal_cooldown_min']}  "
                             f"# avg={opt['avg_pnl']:+.3f}%, WR={opt['win_rate']:.0f}%, "
                             f"n={opt['sample_count']}")
            else:
                lines.append(f"      {cat}: 30  # data insufficient, keep default")
        lines.append(f"```")
        lines.append(f"")
        lines.append(f"> **Note:** Auto-apply is NOT recommended. "
                     f"Review this report, update YAML manually, observe 1-2 weeks.")
        lines.append(f"")

        report_text = "\n".join(lines)

        # 파일 저장
        os.makedirs(log_dir, exist_ok=True)
        report_path = os.path.join(log_dir, f"cooldown_report_{today}.md")
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"  [Report] saved: {report_path}")

        return report_text

    def _generate_category_narrative(
        self,
        category: str,
        grid: Dict,
        optimal_min: int
    ) -> str:
        """카테고리별 "왜 이 쿨다운이 최적인가" narrative 생성"""
        sorted_keys = sorted(grid.keys())
        if not sorted_keys:
            return ""

        # 구간 분석
        pnl_values = {k: grid[k]['avg_pnl'] for k in sorted_keys}
        wr_values = {k: grid[k]['win_rate'] for k in sorted_keys}

        # 0분 기대값
        zero_pnl = pnl_values.get(0, 0)
        opt_pnl = pnl_values.get(optimal_min, 0)

        # PnL 전환점 찾기 (음 → 양)
        crossover_min = None
        for k in sorted_keys:
            if pnl_values[k] > 0 and (crossover_min is None):
                crossover_min = k

        # 피크 이후 감소 구간 (기회 감소 영역)
        decline_start = None
        for i, k in enumerate(sorted_keys):
            if k > optimal_min and pnl_values[k] < opt_pnl:
                decline_start = k
                break

        # Narrative 구성
        parts = []

        # 카테고리별 맞춤 설명
        category_context = {
            'ef_no_follow': 'EF(추종실패) — 타이밍은 맞았으나 추세 지속 실패 후 재진입',
            'ef_no_demand': 'EF(수급부재) — 애초에 수급이 없었던 가짜 신호 후 재진입',
            'early_failure': '구조적 실패 후 동일 종목 재진입',
            'stop_loss': '손절 이후 재진입',
            'trailing_stop': '트레일링 스탑 이후 재진입',
            'time_exit': '시간 기반 청산 이후 재진입',
            'take_profit': '익절 이후 재진입',
            'partial_exit': '부분 청산 이후 재진입',
            'default': '기타 사유 이후 재진입',
        }
        context = category_context.get(category, f'{category} 이후 재진입')

        # Phase 1: 위험 구간
        if zero_pnl < 0:
            parts.append(
                f"**{context}** 직후(0min)의 기대값은 **{zero_pnl:+.3f}%**로 음수입니다. "
                f"청산 직후 추세 재확인 전 단기 반등/되밀림이 반복되어 재진입 시 손실 확률이 높습니다."
            )

        # Phase 2: 전환점
        if crossover_min is not None and crossover_min > 0:
            parts.append(
                f"{crossover_min}분 이후 기대값이 플러스로 전환됩니다. "
                f"이 시점부터 VWAP/EMA 재정렬과 거래량 방향성이 재형성됩니다."
            )

        # Phase 3: 최적점 설명
        parts.append(
            f"**{optimal_min}분**에서 기대값 {opt_pnl:+.3f}%, "
            f"승률 {wr_values.get(optimal_min, 0):.0f}%로 최적점에 도달합니다. "
            f"이 시간은 손실 구간을 건너뛰면서 기회를 놓치지 않는 최소 지연 시간입니다."
        )

        # Phase 4: 효율 저하
        if decline_start is not None:
            decline_pnl = pnl_values.get(decline_start, 0)
            parts.append(
                f"{decline_start}분 이후부터 기대값이 {decline_pnl:+.3f}%로 하락합니다. "
                f"기회 자체가 감소하여 기대값은 유지되나 효율이 저하됩니다."
            )

        # 결론
        pnl_improvement = opt_pnl - zero_pnl
        parts.append(
            f"따라서 **{optimal_min}분** 쿨다운으로 "
            f"기대값 {pnl_improvement:+.3f}%p 개선 효과를 얻을 수 있습니다."
        )

        return "\n\n".join(parts)

    def save_results(self, log_dir: str = "logs"):
        """결과를 JSON으로 저장"""
        os.makedirs(log_dir, exist_ok=True)

        analysis = self.analyze()
        today = datetime.now().strftime('%Y-%m-%d')

        output = {
            'date': today,
            'days_analyzed': self.days,
            'total_sell_trades': len(self.sell_trades),
            'total_simulations': len(self.results),
            'analysis': analysis,
        }

        filepath = os.path.join(log_dir, f"cooldown_optimization_{today}.json")
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False, default=str)

        print(f"  [Cooldown Optimizer] saved: {filepath}")


def main():
    parser = argparse.ArgumentParser(description='쿨다운 최적화 분석')
    parser.add_argument('--days', type=int, default=7, help='분석 기간 (일, 기본 7)')
    parser.add_argument('--no-chart', action='store_true', help='차트 생성 생략')
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  쿨다운 최적화 분석기 (Cooldown Optimizer)")
    print(f"  분석 기간: 최근 {args.days}일")
    print("=" * 60)
    print()

    optimizer = CooldownOptimizer(days=args.days)

    # 1. SELL 거래 로드
    count = optimizer.load_sell_trades()
    if count == 0:
        print("  분석할 SELL 거래가 없습니다.")
        return

    # 2. 시뮬레이션 실행
    print()
    print("  시뮬레이션 실행 중...")
    optimizer.run()

    # 3. 결과 분석 및 출력
    print()
    optimizer.print_report()

    # 4. 기대값 곡선 차트
    if not args.no_chart:
        print()
        optimizer.visualize()

    # 5. Narrative 해석 리포트 (Markdown)
    print()
    optimizer.generate_narrative_report()

    # 6. 원시 결과 JSON 저장
    optimizer.save_results()


if __name__ == '__main__':
    main()
