"""
RSVI Phase 1 백테스트 스크립트

목적:
- 현재 로직 (백테스트 confidence만 사용) vs
- RSVI 로직 (0.3 * BT + 0.7 * RSVI, threshold=0.4) 비교

사용법:
    python3 scripts/backtest_rsvi.py

데이터:
    - PostgreSQL trading_system.trades (2025-11-14 ~ 11-28)
    - 각 거래 시점의 5분봉 데이터 (RSVI 계산용)

작성일: 2025-11-30
"""

import sys
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional
import asyncio
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

# 프로젝트 루트 경로 추가
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from analyzers.volume_indicators import attach_rsvi_indicators, calculate_rsvi_score
from utils.stock_data_fetcher import StockDataFetcher

# 환경 변수 로드
load_dotenv()

console = Console()


@dataclass
class TradeRSVI:
    """거래 + RSVI 데이터"""
    trade_id: int
    stock_code: str
    stock_name: str
    trade_time: datetime
    realized_profit: float
    backtest_conf: float
    rsvi_score: float
    vol_z20: float
    vroc10: float


def get_db_connection():
    """PostgreSQL 연결"""
    # 비밀번호의 따옴표 제거 (dotenv가 따옴표 포함해서 읽는 경우 대비)
    password = os.getenv('POSTGRES_PASSWORD', '')
    password = password.strip('"').strip("'")

    return psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=int(os.getenv('POSTGRES_PORT', 5432)),
        database=os.getenv('POSTGRES_DB', 'trading_system'),
        user=os.getenv('POSTGRES_USER', 'postgres'),
        password=password
    )


def load_trades(conn, start_date: str, end_date: str) -> pd.DataFrame:
    """
    거래 데이터 로드 (매도 완료된 거래만)

    Args:
        conn: PostgreSQL 연결
        start_date: 시작일 (YYYY-MM-DD)
        end_date: 종료일 (YYYY-MM-DD)

    Returns:
        DataFrame with columns: trade_id, stock_code, stock_name, trade_time, realized_profit
    """
    query = """
        SELECT
            trade_id,
            stock_code,
            stock_name,
            trade_time,
            realized_profit,
            COALESCE(sim_profit_factor, 1.0) AS profit_factor
        FROM trades
        WHERE trade_time BETWEEN %s AND %s
          AND trade_type = 'SELL'
          AND realized_profit IS NOT NULL
        ORDER BY trade_time;
    """

    with conn.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute(query, [start_date, end_date])
        rows = cursor.fetchall()

    df = pd.DataFrame(rows)
    console.print(f"[dim]📊 거래 데이터 로드: {len(df)}건 (매도 완료)[/dim]")
    return df


async def compute_trade_rsvi(
    fetcher: StockDataFetcher,
    row: dict,
    lookback_days: int = 7
) -> Tuple[float, float, float]:
    """
    개별 거래에 대해 RSVI 점수 계산

    Args:
        fetcher: StockDataFetcher 인스턴스
        row: 거래 데이터 (dict)
        lookback_days: 과거 데이터 조회 일수

    Returns:
        (vol_z20, vroc10, rsvi_score)
    """
    stock_code = row["stock_code"]
    stock_name = row.get("stock_name", "")
    trade_time: datetime = row["trade_time"]

    try:
        # 5분봉 데이터 로드 (과거 7일)
        df = await fetcher.fetch(
            stock_code=stock_code,
            days=lookback_days,
            source='auto',
            interval='5m'
        )

        if df is None or df.empty or len(df) < 25:
            console.print(
                f"[dim yellow]⚠️  {stock_code} {stock_name}: 데이터 부족 (len={len(df) if df is not None else 0})[/dim yellow]"
            )
            return 0.0, 0.0, 0.0

        # RSVI 지표 추가
        df = attach_rsvi_indicators(df)
        df = df.dropna(subset=["vol_z20", "vroc10"])

        if df.empty:
            return 0.0, 0.0, 0.0

        # 거래 시점 기준 가장 최근 봉 선택
        # (trade_time보다 이전의 봉 중 가장 최근 것)
        if hasattr(df.index, 'tz_localize'):
            # datetime index인 경우
            df_before = df[df.index <= trade_time]
        else:
            # datetime 컬럼이 따로 있는 경우
            if 'datetime' in df.columns:
                df_before = df[df['datetime'] <= trade_time]
            else:
                df_before = df

        if df_before.empty:
            latest = df.iloc[-1]
        else:
            latest = df_before.iloc[-1]

        vol_z20 = float(latest["vol_z20"])
        vroc10 = float(latest["vroc10"])
        rsvi_score = float(calculate_rsvi_score(vol_z20, vroc10))

        return vol_z20, vroc10, rsvi_score

    except Exception as e:
        stock_name = row.get("stock_name", "")
        console.print(
            f"[dim red]⚠️  {stock_code} {stock_name}: RSVI 계산 실패 - {e}[/dim red]"
        )
        return 0.0, 0.0, 0.0


async def build_trade_rsvi_records(
    trades: pd.DataFrame,
    fetcher: StockDataFetcher
) -> List[TradeRSVI]:
    """
    모든 거래에 대해 RSVI 계산

    Args:
        trades: 거래 DataFrame
        fetcher: StockDataFetcher 인스턴스

    Returns:
        List[TradeRSVI]
    """
    records: List[TradeRSVI] = []

    console.print(f"\n[cyan]📈 RSVI 계산 시작 ({len(trades)}건)[/cyan]")

    for idx, row in trades.iterrows():
        # RSVI 계산
        vol_z20, vroc10, rsvi_score = await compute_trade_rsvi(fetcher, row)

        # 백테스트 confidence 추정
        # (실제로는 sim_profit_factor, sim_win_rate 등을 조합해야 하지만,
        #  간단하게 profit_factor만 사용)
        pf = float(row.get("profit_factor", 1.0))
        if pf >= 1.5:
            backtest_conf = 0.8
        elif pf >= 1.15:
            backtest_conf = 0.6
        else:
            backtest_conf = 0.4

        records.append(
            TradeRSVI(
                trade_id=row["trade_id"],
                stock_code=row["stock_code"],
                stock_name=row.get("stock_name", ""),
                trade_time=row["trade_time"],
                realized_profit=float(row["realized_profit"]),
                backtest_conf=backtest_conf,
                rsvi_score=rsvi_score,
                vol_z20=vol_z20,
                vroc10=vroc10,
            )
        )

        # 진행 상황 표시
        if (idx + 1) % 10 == 0:
            console.print(f"[dim]  → {idx + 1}/{len(trades)} 완료[/dim]")

    console.print(f"[green]✓ RSVI 계산 완료 ({len(records)}건)[/green]\n")
    return records


def evaluate_current_logic(records: List[TradeRSVI], threshold: float = 0.4) -> Dict:
    """
    현재 로직 평가 (백테스트 confidence만 사용)

    Args:
        records: TradeRSVI 리스트
        threshold: Confidence 임계값

    Returns:
        성과 딕셔너리
    """
    df = pd.DataFrame([r.__dict__ for r in records])

    executed = df[df["backtest_conf"] >= threshold]
    win_trades = executed[executed["realized_profit"] > 0]

    return {
        "name": "Current Logic (Backtest Only)",
        "threshold": threshold,
        "n_trades": len(executed),
        "n_wins": len(win_trades),
        "win_rate": (len(win_trades) / len(executed) * 100.0) if len(executed) else 0.0,
        "total_pnl": executed["realized_profit"].sum(),
    }


def evaluate_rsvi_logic(
    records: List[TradeRSVI],
    bt_weight: float = 0.3,
    rsvi_weight: float = 0.7,
    threshold: float = 0.4,
) -> Dict:
    """
    RSVI 통합 로직 평가 (0.3 * BT + 0.7 * RSVI)

    Args:
        records: TradeRSVI 리스트
        bt_weight: 백테스트 가중치
        rsvi_weight: RSVI 가중치
        threshold: Confidence 임계값

    Returns:
        성과 딕셔너리
    """
    df = pd.DataFrame([r.__dict__ for r in records])
    df["final_conf"] = (
        bt_weight * df["backtest_conf"] + rsvi_weight * df["rsvi_score"]
    )

    executed = df[df["final_conf"] >= threshold]
    blocked = df[df["final_conf"] < threshold]
    win_trades = executed[executed["realized_profit"] > 0]

    blocked_loss = blocked[blocked["realized_profit"] <= 0]
    blocked_loss_ratio = (
        len(blocked_loss) / len(blocked) * 100.0 if len(blocked) else np.nan
    )

    return {
        "name": "RSVI Logic (0.3*BT + 0.7*RSVI)",
        "threshold": threshold,
        "bt_weight": bt_weight,
        "rsvi_weight": rsvi_weight,
        "n_trades": len(executed),
        "n_wins": len(win_trades),
        "win_rate": (len(win_trades) / len(executed) * 100.0) if len(executed) else 0.0,
        "total_pnl": executed["realized_profit"].sum(),
        "n_blocked": len(blocked),
        "blocked_loss_trades": len(blocked_loss),
        "blocked_loss_ratio": blocked_loss_ratio,
        "blocked_pnl": blocked["realized_profit"].sum(),
    }


def print_backtest_summary(current_stats: Dict, rsvi_stats: Dict) -> None:
    """
    백테스트 결과 출력

    Args:
        current_stats: 현재 로직 통계
        rsvi_stats: RSVI 로직 통계
    """
    # 결과 테이블 생성
    table = Table(title="📊 RSVI Phase 1 백테스트 결과", show_header=True, header_style="bold magenta")
    table.add_column("구분", style="cyan", width=20)
    table.add_column("현재 로직", justify="right", style="yellow")
    table.add_column("RSVI 로직", justify="right", style="green")
    table.add_column("개선폭", justify="right", style="bold green")

    # 거래 건수
    table.add_row(
        "거래 건수",
        f"{current_stats['n_trades']}건",
        f"{rsvi_stats['n_trades']}건",
        f"{rsvi_stats['n_trades'] - current_stats['n_trades']:+d}건"
    )

    # 승률
    win_rate_diff = rsvi_stats['win_rate'] - current_stats['win_rate']
    table.add_row(
        "승률",
        f"{current_stats['win_rate']:.1f}% ({current_stats['n_wins']}/{current_stats['n_trades']})",
        f"{rsvi_stats['win_rate']:.1f}% ({rsvi_stats['n_wins']}/{rsvi_stats['n_trades']})",
        f"{win_rate_diff:+.1f}%p"
    )

    # 총 손익
    pnl_diff = rsvi_stats['total_pnl'] - current_stats['total_pnl']
    table.add_row(
        "총 손익",
        f"{current_stats['total_pnl']:,.0f}원",
        f"{rsvi_stats['total_pnl']:,.0f}원",
        f"{pnl_diff:+,.0f}원"
    )

    # 차단 건수
    table.add_row(
        "차단 건수",
        "-",
        f"{rsvi_stats['n_blocked']}건",
        f"손실 {rsvi_stats['blocked_loss_trades']}건 ({rsvi_stats['blocked_loss_ratio']:.1f}%)"
    )

    console.print("\n")
    console.print(table)

    # 요약 패널
    if pnl_diff > 0:
        summary_style = "bold green"
        summary_icon = "✓"
    elif pnl_diff < 0:
        summary_style = "bold red"
        summary_icon = "✗"
    else:
        summary_style = "bold yellow"
        summary_icon = "="

    summary_text = f"""
{summary_icon} 총 손익 개선: {pnl_diff:+,.0f}원
{summary_icon} 승률 개선: {win_rate_diff:+.1f}%p
{summary_icon} 차단된 손실 거래: {rsvi_stats['blocked_loss_trades']}건 (절감: {-rsvi_stats['blocked_pnl']:,.0f}원)

[dim]* RSVI 가중치: BT {rsvi_stats['bt_weight']:.1f} / RSVI {rsvi_stats['rsvi_weight']:.1f}
* Threshold: {rsvi_stats['threshold']:.2f}[/dim]
    """

    console.print(Panel(summary_text.strip(), title="📈 요약", style=summary_style, expand=False))


async def main():
    """메인 실행 함수"""
    console.print("\n[bold cyan]" + "=" * 80 + "[/bold cyan]")
    console.print("[bold cyan]🧪 RSVI Phase 1 백테스트[/bold cyan]")
    console.print("[bold cyan]" + "=" * 80 + "[/bold cyan]\n")

    # 날짜 범위
    start_date = "2025-11-14"
    end_date = "2025-11-28"
    console.print(f"[dim]📅 분석 기간: {start_date} ~ {end_date}[/dim]\n")

    # PostgreSQL 연결
    try:
        conn = get_db_connection()
        console.print("[green]✓ PostgreSQL 연결 성공[/green]")
    except Exception as e:
        console.print(f"[bold red]✗ PostgreSQL 연결 실패: {e}[/bold red]")
        return

    # 거래 데이터 로드
    trades_df = load_trades(conn, start_date, end_date)

    if trades_df.empty:
        console.print("[yellow]⚠️  거래 데이터 없음[/yellow]")
        conn.close()
        return

    # StockDataFetcher 초기화
    fetcher = StockDataFetcher(kiwoom_api=None, verbose=False)

    # RSVI 계산
    records = await build_trade_rsvi_records(trades_df, fetcher)

    # 현재 로직 평가
    current_stats = evaluate_current_logic(records, threshold=0.4)

    # RSVI 로직 평가
    rsvi_stats = evaluate_rsvi_logic(
        records,
        bt_weight=0.3,
        rsvi_weight=0.7,
        threshold=0.4,
    )

    # 결과 출력
    print_backtest_summary(current_stats, rsvi_stats)

    # 연결 종료
    conn.close()
    console.print("\n[dim]✓ 백테스트 완료[/dim]\n")


if __name__ == "__main__":
    asyncio.run(main())
