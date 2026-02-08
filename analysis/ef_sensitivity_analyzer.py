"""
Early Failure (EF) 민감도 분석 모듈 (오프라인)

Usage:
    python -m analysis.ef_sensitivity_analyzer [--days 7]

mfe_ratio × follow_through_candles 파라미터 조합별로
EF 발동률, false positive, PnL 영향을 분석한다.

DB BUY→SELL 거래 쌍 + yfinance 5분봉 기반.
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

# ── 스윕 파라미터 그리드 ──
MFE_RATIOS = [0.15, 0.20, 0.25, 0.30, 0.35]
FT_CANDLES = [2, 3, 4]


class EFSensitivityAnalyzer:
    """EF 파라미터 민감도 분석기"""

    def __init__(self, days: int = 7):
        self.days = days
        self.trade_pairs: List[Dict] = []   # {buy, sell, stock_code, ...}
        self.results: List[Dict] = []       # 조합별 시뮬레이션 결과
        self._data_cache: Dict[str, Optional[pd.DataFrame]] = {}

    # ────────────────────────────────────────
    # 1. 데이터 로드
    # ────────────────────────────────────────

    def load_trade_pairs(self) -> int:
        """DB에서 BUY→SELL 거래 쌍 로드"""
        from database.trading_db import TradingDatabase

        db = TradingDatabase()
        end_date = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        start_date = (datetime.now() - timedelta(days=self.days)).strftime('%Y-%m-%d %H:%M:%S')

        all_trades = db.get_trades(start_date=start_date, end_date=end_date)

        # 종목별로 BUY/SELL 분리 후 시간순 페어링
        by_stock: Dict[str, Dict[str, list]] = defaultdict(lambda: {'BUY': [], 'SELL': []})
        for t in all_trades:
            tt = str(t.get('trade_type', '')).upper()
            if tt in ('BUY', 'SELL'):
                by_stock[t['stock_code']][tt].append(t)

        for stock_code, trades in by_stock.items():
            buys = sorted(trades['BUY'], key=lambda x: x['trade_time'])
            sells = sorted(trades['SELL'], key=lambda x: x['trade_time'])

            # 단순 순서 매칭: BUY[0]→SELL[0], BUY[1]→SELL[1], ...
            for buy, sell in zip(buys, sells):
                buy_time = buy['trade_time']
                sell_time = sell['trade_time']
                if isinstance(buy_time, str):
                    buy_time = datetime.fromisoformat(buy_time)
                if isinstance(sell_time, str):
                    sell_time = datetime.fromisoformat(sell_time)

                # SELL이 BUY 이후인지 확인
                if sell_time <= buy_time:
                    continue

                entry_price = float(buy.get('price', 0))
                exit_price = float(sell.get('price', 0))
                if entry_price <= 0:
                    continue

                actual_pnl = ((exit_price - entry_price) / entry_price) * 100

                self.trade_pairs.append({
                    'stock_code': stock_code,
                    'stock_name': buy.get('stock_name', ''),
                    'entry_time': buy_time,
                    'exit_time': sell_time,
                    'entry_price': entry_price,
                    'exit_price': exit_price,
                    'actual_pnl': actual_pnl,
                    'exit_reason': sell.get('exit_reason', ''),
                    'holding_min': (sell_time - buy_time).total_seconds() / 60,
                })

        print(f"  거래 쌍 로드: {len(self.trade_pairs)}건 (최근 {self.days}일)")
        return len(self.trade_pairs)

    def _fetch_hold_data(self, stock_code: str, entry_time: datetime) -> Optional[pd.DataFrame]:
        """yfinance 5분봉: 진입 전후 데이터 조회 (캐시)"""
        if stock_code in self._data_cache:
            return self._data_cache[stock_code]

        try:
            import yfinance as yf

            for suffix in ['.KS', '.KQ']:
                ticker = f"{stock_code}{suffix}"
                stock = yf.Ticker(ticker)
                df = stock.history(period=f"{self.days}d", interval="5m")
                if not df.empty:
                    break

            if df.empty or len(df) < 30:
                self._data_cache[stock_code] = None
                return None

            df.reset_index(inplace=True)
            df.columns = [col.lower() for col in df.columns]

            # ATR 계산
            high_low = df['high'] - df['low']
            high_close = abs(df['high'] - df['close'].shift(1))
            low_close = abs(df['low'] - df['close'].shift(1))
            tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
            df['atr'] = tr.rolling(14).mean()

            self._data_cache[stock_code] = df
            return df

        except Exception as e:
            self._data_cache[stock_code] = None
            return None

    def _find_entry_bar(self, df: pd.DataFrame, entry_time: datetime) -> Optional[int]:
        """진입 시점에 가장 가까운 bar index 찾기"""
        dt_col = 'datetime' if 'datetime' in df.columns else 'date'
        if dt_col not in df.columns:
            return None

        entry_ts = pd.Timestamp(entry_time)
        if df[dt_col].dt.tz is not None:
            entry_ts = entry_ts.tz_localize(df[dt_col].dt.tz)

        # entry_time 이후 첫 번째 bar
        mask = df[dt_col] >= entry_ts
        if mask.sum() == 0:
            return None
        return mask.idxmax()

    # ────────────────────────────────────────
    # 2. EF 시그널 시뮬레이션
    # ────────────────────────────────────────

    def _simulate_ef_at_bar(
        self,
        df: pd.DataFrame,
        entry_idx: int,
        check_idx: int,
        entry_price: float,
        mfe_ratio: float,
        ft_candles: int
    ) -> Dict:
        """
        특정 bar에서 EF 시그널 D+E를 시뮬레이션.
        시그널 A(direction), B(atr_decay), C(volume_dry)는 고정 파라미터로 동일 평가.

        Returns:
            {score, signals, signal_d, signal_e, mfe_value, mfe_ratio_actual, ...}
        """
        if check_idx >= len(df) or check_idx <= entry_idx:
            return {'score': 0, 'signals': [], 'valid': False}

        score = 0
        signals = []

        # ATR at entry
        atr_at_entry = df['atr'].iloc[entry_idx] if 'atr' in df.columns else 0
        current_price = df['close'].iloc[check_idx]

        # 진입 이후 최고가 (long 기준)
        high_slice = df['high'].iloc[entry_idx:check_idx + 1]
        highest = high_slice.max() if len(high_slice) > 0 else entry_price

        # ── Signal A: Direction Failure (점수 2) ──
        if highest <= entry_price * 1.001:
            score += 2
            signals.append('A')

        # ── Signal B: ATR Decay (점수 1) ──
        if atr_at_entry > 0 and 'atr' in df.columns:
            current_atr = df['atr'].iloc[check_idx]
            if current_atr > 0 and current_atr / atr_at_entry < 0.85:
                score += 1
                signals.append('B')

        # ── Signal C: Volume Dry-up (점수 1) ──
        if 'volume' in df.columns and check_idx >= 20:
            current_vol = df['volume'].iloc[check_idx]
            avg_vol = df['volume'].iloc[check_idx - 20:check_idx].mean()
            if avg_vol > 0 and current_vol / avg_vol < 0.8:
                score += 1
                signals.append('C')

        # ── Signal D: MFE Failure (점수 1) — 변수 파라미터 ──
        mfe_value = highest - entry_price
        mfe_ratio_actual = mfe_value / atr_at_entry if atr_at_entry > 0 else 0
        signal_d = False
        if atr_at_entry > 0 and mfe_ratio_actual < mfe_ratio:
            score += 1
            signals.append('D')
            signal_d = True

        # ── Signal E: Follow-Through Failure (점수 1) — 변수 파라미터 ──
        signal_e = False
        if check_idx >= ft_candles and 'close' in df.columns:
            recent = df['close'].iloc[check_idx - ft_candles + 1:check_idx + 1]
            if len(recent) == ft_candles and (recent < entry_price).all():
                score += 1
                signals.append('E')
                signal_e = True

        # 현재 PnL
        pnl_at_check = ((current_price - entry_price) / entry_price) * 100

        return {
            'score': score,
            'signals': signals,
            'signal_d': signal_d,
            'signal_e': signal_e,
            'mfe_value': round(mfe_value, 2),
            'mfe_ratio_actual': round(mfe_ratio_actual, 4),
            'pnl_at_check': round(pnl_at_check, 3),
            'valid': True,
        }

    def _calc_subsequent_outcome(
        self,
        df: pd.DataFrame,
        check_idx: int,
        entry_price: float,
        lookforward: int = 24
    ) -> Dict:
        """
        EF 판정 시점 이후 가격이 어디까지 갔는지 계산.
        → false positive / true positive 판별 근거.

        lookforward: 판정 이후 관찰할 bar 수 (기본 24 = 2시간)
        """
        end_idx = min(check_idx + lookforward, len(df))
        future = df.iloc[check_idx + 1:end_idx]

        if len(future) == 0:
            return {'max_pnl': 0, 'min_pnl': 0, 'end_pnl': 0}

        max_price = future['high'].max()
        min_price = future['low'].min()
        end_price = future['close'].iloc[-1]

        max_pnl = ((max_price - entry_price) / entry_price) * 100
        min_pnl = ((min_price - entry_price) / entry_price) * 100
        end_pnl = ((end_price - entry_price) / entry_price) * 100

        return {
            'max_pnl': round(max_pnl, 3),
            'min_pnl': round(min_pnl, 3),
            'end_pnl': round(end_pnl, 3),
        }

    # ────────────────────────────────────────
    # 3. 스윕 실행
    # ────────────────────────────────────────

    def run(self):
        """모든 거래 × 모든 파라미터 조합 스윕"""
        if not self.trade_pairs:
            print("  거래 쌍이 없습니다.")
            return

        for idx, pair in enumerate(self.trade_pairs):
            stock_code = pair['stock_code']
            entry_time = pair['entry_time']
            entry_price = pair['entry_price']
            actual_pnl = pair['actual_pnl']

            print(f"  [{idx + 1}/{len(self.trade_pairs)}] "
                  f"{pair['stock_name']}({stock_code}) "
                  f"{entry_time.strftime('%m/%d %H:%M')} "
                  f"PnL={actual_pnl:+.2f}%", end="")

            df = self._fetch_hold_data(stock_code, entry_time)
            if df is None:
                print(" → skip (데이터 없음)")
                continue

            entry_idx = self._find_entry_bar(df, entry_time)
            if entry_idx is None:
                print(" → skip (진입 bar 미발견)")
                continue

            # 관찰 구간: 진입 후 1~3 bar (5~15분)
            observe_bars = range(1, 4)  # bar 1, 2, 3
            valid_checks = [
                entry_idx + b for b in observe_bars
                if entry_idx + b < len(df)
            ]

            if not valid_checks:
                print(" → skip (관찰 구간 부족)")
                continue

            # 각 파라미터 조합에 대해
            for mr in MFE_RATIOS:
                for ft in FT_CANDLES:
                    # 관찰 구간 내 최고 score 기준 판정 (실제 로직과 동일)
                    best_ef = None
                    for ci in valid_checks:
                        ef = self._simulate_ef_at_bar(
                            df, entry_idx, ci, entry_price, mr, ft
                        )
                        if not ef['valid']:
                            continue
                        if best_ef is None or ef['score'] > best_ef['score']:
                            best_ef = ef
                            best_check_idx = ci

                    if best_ef is None:
                        continue

                    triggered = best_ef['score'] >= 3
                    subsequent = self._calc_subsequent_outcome(
                        df, best_check_idx, entry_price
                    )

                    # false positive: EF가 발동했지만 이후 수익이 된 경우
                    # true positive: EF가 발동했고 실제로 손실인 경우
                    # false negative: EF 미발동이지만 결과가 손실
                    # true negative: EF 미발동이고 결과가 수익
                    is_fp = triggered and subsequent['max_pnl'] > 1.0
                    is_tp = triggered and actual_pnl < 0
                    saved_pnl = best_ef['pnl_at_check'] - actual_pnl if triggered else 0

                    self.results.append({
                        'stock_code': stock_code,
                        'stock_name': pair['stock_name'],
                        'entry_time': entry_time.isoformat(),
                        'mfe_ratio': mr,
                        'ft_candles': ft,
                        'ef_score': best_ef['score'],
                        'ef_triggered': triggered,
                        'ef_signals': '+'.join(best_ef['signals']),
                        'signal_d': best_ef['signal_d'],
                        'signal_e': best_ef['signal_e'],
                        'mfe_ratio_actual': best_ef['mfe_ratio_actual'],
                        'pnl_at_ef': best_ef['pnl_at_check'],
                        'actual_pnl': actual_pnl,
                        'subsequent_max_pnl': subsequent['max_pnl'],
                        'subsequent_min_pnl': subsequent['min_pnl'],
                        'subsequent_end_pnl': subsequent['end_pnl'],
                        'is_false_positive': is_fp,
                        'is_true_positive': is_tp,
                        'saved_pnl': round(saved_pnl, 3),
                    })

            print(f" → {len(MFE_RATIOS) * len(FT_CANDLES)} 조합 완료")

    # ────────────────────────────────────────
    # 4. 분석
    # ────────────────────────────────────────

    def analyze(self) -> Dict:
        """조합별 EF 발동률, FP률, PnL 영향 분석"""
        if not self.results:
            return {}

        df = pd.DataFrame(self.results)
        analysis = {}

        for mr in MFE_RATIOS:
            for ft in FT_CANDLES:
                combo_key = f"mfe={mr}_ft={ft}"
                subset = df[(df['mfe_ratio'] == mr) & (df['ft_candles'] == ft)]

                if len(subset) == 0:
                    continue

                total = len(subset)
                triggered = subset['ef_triggered'].sum()
                trigger_rate = (triggered / total * 100) if total > 0 else 0

                # EF 발동 건 분석
                ef_on = subset[subset['ef_triggered']]
                ef_off = subset[~subset['ef_triggered']]

                fp_count = ef_on['is_false_positive'].sum() if len(ef_on) > 0 else 0
                tp_count = ef_on['is_true_positive'].sum() if len(ef_on) > 0 else 0
                fp_rate = (fp_count / triggered * 100) if triggered > 0 else 0

                # 시그널별 빈도
                d_rate = (subset['signal_d'].sum() / total * 100) if total > 0 else 0
                e_rate = (subset['signal_e'].sum() / total * 100) if total > 0 else 0

                # PnL 영향
                avg_saved = ef_on['saved_pnl'].mean() if len(ef_on) > 0 else 0
                avg_pnl_triggered = ef_on['actual_pnl'].mean() if len(ef_on) > 0 else 0
                avg_pnl_not_triggered = ef_off['actual_pnl'].mean() if len(ef_off) > 0 else 0

                # 순 기대값: EF가 잡아낸 손실 방지 - FP로 인한 기회 비용
                missed_gains = ef_on[ef_on['is_false_positive']]['subsequent_max_pnl'].mean() \
                    if fp_count > 0 else 0
                net_value = avg_saved - (missed_gains * fp_rate / 100) if triggered > 0 else 0

                analysis[combo_key] = {
                    'mfe_ratio': mr,
                    'ft_candles': ft,
                    'total_trades': total,
                    'ef_triggered': int(triggered),
                    'trigger_rate_pct': round(trigger_rate, 1),
                    'true_positive': int(tp_count),
                    'false_positive': int(fp_count),
                    'fp_rate_pct': round(fp_rate, 1),
                    'signal_d_rate_pct': round(d_rate, 1),
                    'signal_e_rate_pct': round(e_rate, 1),
                    'avg_pnl_triggered': round(avg_pnl_triggered, 3),
                    'avg_pnl_not_triggered': round(avg_pnl_not_triggered, 3),
                    'avg_saved_pnl': round(avg_saved, 3),
                    'missed_gains_pct': round(missed_gains, 3),
                    'net_value': round(net_value, 3),
                }

        return analysis

    # ────────────────────────────────────────
    # 5. 출력
    # ────────────────────────────────────────

    def print_report(self):
        """Rich 테이블 출력"""
        analysis = self.analyze()
        if not analysis:
            print("  분석 결과 없음")
            return

        try:
            from rich.console import Console
            from rich.table import Table
            console = Console()
        except ImportError:
            self._print_report_plain(analysis)
            return

        table = Table(
            title=f"EF Sensitivity Analysis (last {self.days} days, {len(self.trade_pairs)} trades)"
        )
        table.add_column("mfe_ratio", style="bold")
        table.add_column("ft_candles", style="bold")
        table.add_column("EF Rate", justify="right")
        table.add_column("TP", justify="right", style="green")
        table.add_column("FP", justify="right", style="red")
        table.add_column("FP%", justify="right")
        table.add_column("Sig D%", justify="right")
        table.add_column("Sig E%", justify="right")
        table.add_column("Avg PnL\n(triggered)", justify="right")
        table.add_column("Avg PnL\n(not trig)", justify="right")
        table.add_column("Net Value", justify="right", style="bold")

        # 최적 조합 찾기 (min FP + max trigger rate 균형)
        best_key = None
        best_score = -999
        for key, data in analysis.items():
            # score = trigger_rate × (1 - fp_rate/100) — 높은 발동률 + 낮은 FP
            combo_score = data['trigger_rate_pct'] * (1 - data['fp_rate_pct'] / 100)
            if combo_score > best_score:
                best_score = combo_score
                best_key = key

        for key in sorted(analysis.keys()):
            d = analysis[key]
            marker = " *" if key == best_key else ""
            style = "bold green" if key == best_key else None

            table.add_row(
                f"{d['mfe_ratio']}{marker}",
                str(d['ft_candles']),
                f"{d['trigger_rate_pct']:.0f}% ({d['ef_triggered']}/{d['total_trades']})",
                str(d['true_positive']),
                str(d['false_positive']),
                f"{d['fp_rate_pct']:.0f}%",
                f"{d['signal_d_rate_pct']:.0f}%",
                f"{d['signal_e_rate_pct']:.0f}%",
                f"{d['avg_pnl_triggered']:+.2f}%",
                f"{d['avg_pnl_not_triggered']:+.2f}%",
                f"{d['net_value']:+.3f}",
                style=style,
            )

        console.print(table)
        if best_key:
            bd = analysis[best_key]
            console.print(
                f"\n  [bold green]* Best combo: mfe_ratio={bd['mfe_ratio']}, "
                f"ft_candles={bd['ft_candles']} "
                f"(EF {bd['trigger_rate_pct']:.0f}%, FP {bd['fp_rate_pct']:.0f}%, "
                f"Net {bd['net_value']:+.3f})[/bold green]\n"
            )

    def _print_report_plain(self, analysis: Dict):
        """Plain text 출력"""
        print(f"\n{'=' * 90}")
        print(f"  EF Sensitivity Analysis (last {self.days} days)")
        print(f"{'=' * 90}")
        print(f"  {'mfe':>5} {'ft':>3} | {'EF%':>5} {'TP':>4} {'FP':>4} {'FP%':>5} | "
              f"{'SigD%':>5} {'SigE%':>5} | {'PnL_on':>7} {'PnL_off':>8} {'NetVal':>7}")
        print(f"  {'-' * 80}")

        for key in sorted(analysis.keys()):
            d = analysis[key]
            print(f"  {d['mfe_ratio']:>5.2f} {d['ft_candles']:>3d} | "
                  f"{d['trigger_rate_pct']:>4.0f}% {d['true_positive']:>4d} "
                  f"{d['false_positive']:>4d} {d['fp_rate_pct']:>4.0f}% | "
                  f"{d['signal_d_rate_pct']:>4.0f}% {d['signal_e_rate_pct']:>4.0f}% | "
                  f"{d['avg_pnl_triggered']:>+6.2f}% {d['avg_pnl_not_triggered']:>+7.2f}% "
                  f"{d['net_value']:>+6.3f}")

        print(f"{'=' * 90}\n")

    # ────────────────────────────────────────
    # 6. 시각화
    # ────────────────────────────────────────

    def visualize(self, log_dir: str = "logs"):
        """히트맵: EF 발동률, FP율, Net Value"""
        analysis = self.analyze()
        if not analysis:
            return None

        try:
            import matplotlib
            matplotlib.use('Agg')
            import matplotlib.pyplot as plt
            from matplotlib import rcParams
        except ImportError:
            print("  matplotlib 미설치 — 시각화 생략")
            return None

        rcParams['axes.unicode_minus'] = False

        os.makedirs(log_dir, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')

        # 데이터 → 2D 행렬 (mfe_ratio × ft_candles)
        trigger_matrix = np.full((len(MFE_RATIOS), len(FT_CANDLES)), np.nan)
        fp_matrix = np.full_like(trigger_matrix, np.nan)
        net_matrix = np.full_like(trigger_matrix, np.nan)

        for key, d in analysis.items():
            r = MFE_RATIOS.index(d['mfe_ratio'])
            c = FT_CANDLES.index(d['ft_candles'])
            trigger_matrix[r, c] = d['trigger_rate_pct']
            fp_matrix[r, c] = d['fp_rate_pct']
            net_matrix[r, c] = d['net_value']

        fig, axes = plt.subplots(1, 3, figsize=(18, 5))
        titles = ['EF Trigger Rate (%)', 'False Positive Rate (%)', 'Net Value (PnL impact)']
        matrices = [trigger_matrix, fp_matrix, net_matrix]
        cmaps = ['YlOrRd', 'Reds', 'RdYlGn']

        for ax, matrix, title, cmap in zip(axes, matrices, titles, cmaps):
            im = ax.imshow(matrix, cmap=cmap, aspect='auto')
            ax.set_title(title, fontsize=12, fontweight='bold')
            ax.set_xticks(range(len(FT_CANDLES)))
            ax.set_xticklabels([str(f) for f in FT_CANDLES])
            ax.set_yticks(range(len(MFE_RATIOS)))
            ax.set_yticklabels([f'{m:.2f}' for m in MFE_RATIOS])
            ax.set_xlabel('follow_through_candles')
            ax.set_ylabel('mfe_ratio')

            # 셀 값 표시
            for i in range(len(MFE_RATIOS)):
                for j in range(len(FT_CANDLES)):
                    val = matrix[i, j]
                    if not np.isnan(val):
                        fmt = f'{val:.0f}%' if 'Rate' in title else f'{val:+.2f}'
                        ax.text(j, i, fmt, ha='center', va='center',
                                fontsize=9, fontweight='bold',
                                color='white' if val > matrix[~np.isnan(matrix)].mean() else 'black')

            fig.colorbar(im, ax=ax, shrink=0.8)

        plt.suptitle(f'EF Parameter Sensitivity ({len(self.trade_pairs)} trades, {self.days} days)',
                     fontsize=14, fontweight='bold')
        plt.tight_layout()

        path = os.path.join(log_dir, f"ef_sensitivity_{today}.png")
        plt.savefig(path, dpi=150, bbox_inches='tight')
        plt.close(fig)
        print(f"  [Chart] saved: {path}")
        return path

    # ────────────────────────────────────────
    # 7. Narrative 리포트
    # ────────────────────────────────────────

    def generate_narrative_report(self, log_dir: str = "logs") -> str:
        """Markdown 해석 리포트 생성"""
        analysis = self.analyze()
        if not analysis:
            return ""

        today = datetime.now().strftime('%Y-%m-%d')

        # 최적 조합 찾기
        best_key = None
        best_score = -999
        for key, d in analysis.items():
            combo_score = d['trigger_rate_pct'] * (1 - d['fp_rate_pct'] / 100)
            if combo_score > best_score:
                best_score = combo_score
                best_key = key

        best = analysis.get(best_key, {}) if best_key else {}

        lines = []
        lines.append(f"# EF Sensitivity Analysis Report ({today})")
        lines.append(f"")
        lines.append(f"- Analysis period: {self.days} days")
        lines.append(f"- Trade pairs analyzed: {len(self.trade_pairs)}")
        lines.append(f"- Parameter grid: mfe_ratio {MFE_RATIOS} x follow_through {FT_CANDLES}")
        lines.append(f"- Total combinations: {len(MFE_RATIOS) * len(FT_CANDLES)}")
        lines.append(f"")

        # 전체 매트릭스 테이블
        lines.append(f"## Sensitivity Matrix")
        lines.append(f"")
        lines.append(f"| mfe_ratio | ft_candles | EF Rate | TP | FP | FP% | Sig D% | Sig E% | Net Value |")
        lines.append(f"|-----------|-----------|---------|----|----|-----|--------|--------|-----------|")

        for key in sorted(analysis.keys()):
            d = analysis[key]
            marker = " **" if key == best_key else ""
            lines.append(
                f"| {d['mfe_ratio']}{marker} | {d['ft_candles']} | "
                f"{d['trigger_rate_pct']:.0f}% | {d['true_positive']} | {d['false_positive']} | "
                f"{d['fp_rate_pct']:.0f}% | {d['signal_d_rate_pct']:.0f}% | "
                f"{d['signal_e_rate_pct']:.0f}% | {d['net_value']:+.3f} |"
            )
        lines.append(f"")

        # 해석
        lines.append(f"## Interpretation")
        lines.append(f"")

        if best:
            lines.append(f"### Best Combination: mfe_ratio={best['mfe_ratio']}, "
                         f"ft_candles={best['ft_candles']}")
            lines.append(f"")

            lines.append(f"- **EF Trigger Rate**: {best['trigger_rate_pct']:.0f}% "
                         f"({best['ef_triggered']}/{best['total_trades']} trades)")
            lines.append(f"- **True Positive**: {best['true_positive']} "
                         f"(EF correctly identified failing trades)")
            lines.append(f"- **False Positive**: {best['false_positive']} "
                         f"({best['fp_rate_pct']:.0f}%, EF exited but trade would have recovered)")
            lines.append(f"- **Net Value**: {best['net_value']:+.3f}")
            lines.append(f"")

            # mfe_ratio 민감도 분석
            lines.append(f"### Signal D (MFE Failure) Sensitivity")
            lines.append(f"")
            for mr in MFE_RATIOS:
                key_mr = f"mfe={mr}_ft={best['ft_candles']}"
                if key_mr in analysis:
                    d = analysis[key_mr]
                    label = " ← current" if mr == 0.25 else ""
                    label += " ← optimal" if mr == best['mfe_ratio'] else ""
                    lines.append(f"- mfe_ratio={mr}: Sig D fires {d['signal_d_rate_pct']:.0f}%, "
                                 f"EF Rate {d['trigger_rate_pct']:.0f}%, "
                                 f"FP {d['fp_rate_pct']:.0f}%{label}")
            lines.append(f"")

            # follow_through 민감도 분석
            lines.append(f"### Signal E (Follow-Through) Sensitivity")
            lines.append(f"")
            for ft in FT_CANDLES:
                key_ft = f"mfe={best['mfe_ratio']}_ft={ft}"
                if key_ft in analysis:
                    d = analysis[key_ft]
                    label = " ← current" if ft == 3 else ""
                    label += " ← optimal" if ft == best['ft_candles'] else ""
                    lines.append(f"- ft_candles={ft}: Sig E fires {d['signal_e_rate_pct']:.0f}%, "
                                 f"EF Rate {d['trigger_rate_pct']:.0f}%, "
                                 f"FP {d['fp_rate_pct']:.0f}%{label}")
            lines.append(f"")

        # 권장사항
        lines.append(f"## Recommendation")
        lines.append(f"")
        if best:
            current_mr = 0.25
            current_ft = 3
            if best['mfe_ratio'] == current_mr and best['ft_candles'] == current_ft:
                lines.append(f"Current parameters (mfe_ratio={current_mr}, "
                             f"ft_candles={current_ft}) are already optimal. "
                             f"No change needed.")
            else:
                lines.append(f"```yaml")
                lines.append(f"risk_control:")
                lines.append(f"  early_failure_structure:")
                lines.append(f"    mfe_ratio: {best['mfe_ratio']}      "
                             f"# was {current_mr}, EF rate {best['trigger_rate_pct']:.0f}%")
                lines.append(f"    follow_through_candles: {best['ft_candles']}  "
                             f"# was {current_ft}, FP rate {best['fp_rate_pct']:.0f}%")
                lines.append(f"```")
                lines.append(f"")
                lines.append(f"> Apply change, observe 1-2 weeks, then re-run this analyzer.")
        lines.append(f"")

        report_text = "\n".join(lines)

        os.makedirs(log_dir, exist_ok=True)
        path = os.path.join(log_dir, f"ef_sensitivity_report_{today}.md")
        with open(path, 'w', encoding='utf-8') as f:
            f.write(report_text)
        print(f"  [Report] saved: {path}")

        return report_text

    # ────────────────────────────────────────
    # 8. JSON 저장
    # ────────────────────────────────────────

    def save_results(self, log_dir: str = "logs"):
        """원시 결과 JSON 저장"""
        os.makedirs(log_dir, exist_ok=True)
        today = datetime.now().strftime('%Y-%m-%d')

        output = {
            'date': today,
            'days_analyzed': self.days,
            'trade_pairs': len(self.trade_pairs),
            'total_simulations': len(self.results),
            'parameter_grid': {
                'mfe_ratios': MFE_RATIOS,
                'ft_candles': FT_CANDLES,
            },
            'analysis': self.analyze(),
        }

        path = os.path.join(log_dir, f"ef_sensitivity_{today}.json")
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(output, f, indent=2, ensure_ascii=False, default=str)
        print(f"  [JSON] saved: {path}")


def main():
    parser = argparse.ArgumentParser(description='EF 민감도 분석')
    parser.add_argument('--days', type=int, default=7, help='분석 기간 (일, 기본 7)')
    parser.add_argument('--no-chart', action='store_true', help='차트 생성 생략')
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  EF Sensitivity Analyzer")
    print(f"  mfe_ratio: {MFE_RATIOS}")
    print(f"  follow_through: {FT_CANDLES}")
    print(f"  Analysis period: {args.days} days")
    print("=" * 60)
    print()

    analyzer = EFSensitivityAnalyzer(days=args.days)

    # 1. 거래 쌍 로드
    count = analyzer.load_trade_pairs()
    if count == 0:
        print("  분석할 거래 쌍이 없습니다.")
        return

    # 2. 스윕 실행
    print()
    print("  Sensitivity sweep 실행 중...")
    analyzer.run()

    # 3. 테이블 출력
    print()
    analyzer.print_report()

    # 4. 히트맵 차트
    if not args.no_chart:
        print()
        analyzer.visualize()

    # 5. Narrative 리포트
    print()
    analyzer.generate_narrative_report()

    # 6. JSON 저장
    analyzer.save_results()


if __name__ == '__main__':
    main()
