#!/usr/bin/env python3
"""
수정된 로직으로 어제(2026-01-16) 전체 시뮬레이션
- 실제 골든크로스 발생 시점 탐지
- 14:59 이전 진입만 허용
- Squeeze 조건 체크
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from datetime import datetime, time as time_class
from kiwoom_api import KiwoomAPI
import pandas as pd
import numpy as np
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()

def calculate_squeeze_momentum(df, bb_length=20, bb_mult=2.0, kc_length=20, kc_mult=1.5):
    """Squeeze Momentum 계산"""
    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)

    # Bollinger Bands
    bb_basis = close.rolling(window=bb_length).mean()
    bb_dev = close.rolling(window=bb_length).std() * bb_mult
    bb_upper = bb_basis + bb_dev
    bb_lower = bb_basis - bb_dev

    # Keltner Channel
    tr1 = high - low
    tr2 = abs(high - close.shift(1))
    tr3 = abs(low - close.shift(1))
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=kc_length).mean()

    kc_basis = close.rolling(window=kc_length).mean()
    kc_upper = kc_basis + atr * kc_mult
    kc_lower = kc_basis - atr * kc_mult

    # Squeeze 상태: BB가 KC 안에 있으면 squeeze
    squeeze_on = (bb_lower > kc_lower) & (bb_upper < kc_upper)
    squeeze_off = ~squeeze_on

    # Momentum (Linear Regression)
    highest_high = high.rolling(window=kc_length).max()
    lowest_low = low.rolling(window=kc_length).min()
    avg_hl = (highest_high + lowest_low) / 2
    avg_close = close.rolling(window=kc_length).mean()
    val = close - ((avg_hl + avg_close) / 2)

    return squeeze_on, squeeze_off, val

def get_chart_data(api, stock_code, tic_scope="30"):
    """차트 데이터 조회"""
    try:
        result = api.get_minute_chart(stock_code, tic_scope=tic_scope)
        if result and 'stk_min_pole_chart_qry' in result:
            raw_data = result['stk_min_pole_chart_qry']

            df = pd.DataFrame(raw_data)

            # API 응답 컬럼명 매핑
            column_mapping = {
                'cur_prc': 'close',
                'high_pric': 'high',
                'low_pric': 'low',
                'open_pric': 'open',
                'trde_qty': 'volume',
                'cntr_tm': 'datetime'
            }
            df = df.rename(columns=column_mapping)

            # 숫자 변환 (+/- 부호 제거)
            for col in ['close', 'high', 'low', 'open', 'volume']:
                if col in df.columns:
                    df[col] = df[col].astype(str).str.replace('+', '').str.replace('-', '')
                    df[col] = pd.to_numeric(df[col], errors='coerce')

            # datetime에서 date, time 분리 (예: 20260116153000 -> date=20260116, time=153000)
            if 'datetime' in df.columns:
                df['date'] = df['datetime'].astype(str).str[:8]
                df['time'] = df['datetime'].astype(str).str[8:14]

            df = df.sort_values(['date', 'time']).reset_index(drop=True)
            return df
    except Exception as e:
        console.print(f"[red]차트 조회 오류: {e}[/red]")
        import traceback
        traceback.print_exc()
    return None

def find_golden_crosses(df):
    """실제 골든크로스 발생 시점 찾기"""
    df = df.copy()
    df['ma5'] = df['close'].rolling(window=5).mean()
    df['ma20'] = df['close'].rolling(window=20).mean()

    # Squeeze 계산
    squeeze_on, squeeze_off, momentum = calculate_squeeze_momentum(df)
    df['squeeze_on'] = squeeze_on
    df['squeeze_off'] = squeeze_off
    df['momentum'] = momentum

    golden_crosses = []

    for i in range(1, len(df)):
        ma5_prev = df['ma5'].iloc[i-1]
        ma20_prev = df['ma20'].iloc[i-1]
        ma5_curr = df['ma5'].iloc[i]
        ma20_curr = df['ma20'].iloc[i]

        if pd.isna(ma5_prev) or pd.isna(ma20_prev) or pd.isna(ma5_curr) or pd.isna(ma20_curr):
            continue

        # 실제 골든크로스: 이전에 MA5 <= MA20이었고, 현재 MA5 > MA20
        if ma5_prev <= ma20_prev and ma5_curr > ma20_curr:
            row = df.iloc[i]
            golden_crosses.append({
                'index': i,
                'date': row.get('date', ''),
                'time': row.get('time', ''),
                'close': row['close'],
                'ma5': ma5_curr,
                'ma20': ma20_curr,
                'squeeze_on': row.get('squeeze_on', False),
                'squeeze_off': row.get('squeeze_off', True),
                'momentum': row.get('momentum', 0)
            })

    return golden_crosses, df

def simulate_trade(df, entry_idx, entry_price, stop_loss_pct=0.02, take_profit_pct=0.03):
    """거래 시뮬레이션 - 진입 후 결과 계산"""
    exit_time = time_class(15, 20, 0)  # 장 마감 청산

    for i in range(entry_idx + 1, len(df)):
        row = df.iloc[i]
        current_price = row['close']
        current_time_str = str(row.get('time', ''))

        # 시간 파싱
        if len(current_time_str) >= 4:
            hour = int(current_time_str[:2])
            minute = int(current_time_str[2:4])
            current_time = time_class(hour, minute)
        else:
            continue

        # 손익률 계산
        pnl_pct = (current_price - entry_price) / entry_price

        # 손절
        if pnl_pct <= -stop_loss_pct:
            return {
                'exit_idx': i,
                'exit_time': current_time_str,
                'exit_price': current_price,
                'exit_reason': '손절',
                'pnl_pct': pnl_pct,
                'pnl_amount': current_price - entry_price
            }

        # 익절
        if pnl_pct >= take_profit_pct:
            return {
                'exit_idx': i,
                'exit_time': current_time_str,
                'exit_price': current_price,
                'exit_reason': '익절',
                'pnl_pct': pnl_pct,
                'pnl_amount': current_price - entry_price
            }

        # 장 마감 청산
        if current_time >= exit_time:
            return {
                'exit_idx': i,
                'exit_time': current_time_str,
                'exit_price': current_price,
                'exit_reason': '장마감',
                'pnl_pct': pnl_pct,
                'pnl_amount': current_price - entry_price
            }

    # 마지막 봉에서 청산
    last_row = df.iloc[-1]
    pnl_pct = (last_row['close'] - entry_price) / entry_price
    return {
        'exit_idx': len(df) - 1,
        'exit_time': str(last_row.get('time', '')),
        'exit_price': last_row['close'],
        'exit_reason': '시뮬종료',
        'pnl_pct': pnl_pct,
        'pnl_amount': last_row['close'] - entry_price
    }

def run_simulation(target_date="20260116"):
    """전체 시뮬레이션 실행"""
    date_display = f"{target_date[:4]}-{target_date[4:6]}-{target_date[6:]}"

    console.print()
    console.print("="*100, style="bold cyan")
    console.print(f"📊 수정된 로직 시뮬레이션 ({date_display})", style="bold cyan")
    console.print("="*100, style="bold cyan")
    console.print()

    # API 초기화
    api = KiwoomAPI()

    # 코스닥 주요 종목들 (다양한 섹터)
    target_stocks = [
        # 어제 거래한 종목
        ('004310', '현대약품'),
        ('056080', '유진로봇'),
        ('085910', '네오티스'),
        ('007810', '코리아써키트'),
        # 최근 주간 거래 종목
        ('023810', '인팩'),
        ('053700', '삼보모터스'),
        ('232680', '라온테크'),
        ('437730', '삼현'),
        ('318060', '그래피'),
        ('389500', '에스비비테크'),
        ('396470', '워트'),
        ('124500', '아이티센글로벌'),
        ('019180', '티에이치엔'),
        ('082920', '비츠로셀'),
        # 추가 코스닥 종목들
        ('005930', '삼성전자'),
        ('000660', 'SK하이닉스'),
        ('035720', '카카오'),
        ('035420', 'NAVER'),
        ('068270', '셀트리온'),
        ('028300', 'HLB'),
        ('247540', '에코프로비엠'),
        ('086520', '에코프로'),
        ('373220', 'LG에너지솔루션'),
        ('006400', '삼성SDI'),
        ('207940', '삼성바이오로직스'),
        ('003670', '포스코퓨처엠'),
        ('196170', '알테오젠'),
        ('145020', '휴젤'),
        ('041510', '에스엠'),
        ('352820', '하이브'),
        ('293490', '카카오게임즈'),
        ('263750', '펄어비스'),
        ('112040', '위메이드'),
        ('095340', 'ISC'),
        ('214150', '클래시스'),
        ('039030', '이오테크닉스'),
        ('005290', '동진쎄미켐'),
        ('240810', '원익IPS'),
        ('036930', '주성엔지니어링'),
        ('000990', 'DB하이텍'),
        ('058470', '리노공업'),
        ('357780', '솔브레인'),
        ('067160', '아프리카TV'),
        ('041920', '메디아나'),
        ('122870', '와이지엔터테인먼트'),
        ('314930', '바이오다인'),
    ]

    all_trades = []

    total_stocks = len(target_stocks)
    for idx, (stock_code, stock_name) in enumerate(target_stocks, 1):
        console.print(f"[dim][{idx}/{total_stocks}] {stock_name} ({stock_code}) 분석 중...[/dim]", end="")

        # 30분봉 데이터 조회
        df = get_chart_data(api, stock_code, tic_scope="30")

        if df is None or len(df) < 25:
            console.print(f" [red]데이터 부족[/red]")
            continue

        # 전체 데이터 사용 (MA20 계산을 위해)
        df['date'] = df['date'].astype(str)

        # MA 계산을 위해 전체 데이터 사용
        df_today = df.copy()

        # 골든크로스 찾기
        golden_crosses, df_with_ma = find_golden_crosses(df_today)

        # 해당 날짜의 골든크로스만 필터링
        golden_crosses_today = [gc for gc in golden_crosses if gc['date'] == target_date]

        if not golden_crosses_today:
            console.print(f" [dim]골든크로스 없음[/dim]")
            continue

        console.print()  # 줄바꿈
        console.print(f"  [green]✅ {stock_name}: 골든크로스 {len(golden_crosses_today)}개 발견![/green]")

        for gc in golden_crosses_today:
            gc_time = str(gc['time'])
            if len(gc_time) >= 4:
                hour = int(gc_time[:2])
                minute = int(gc_time[2:4])
                time_str = f"{hour:02d}:{minute:02d}"
                gc_time_obj = time_class(hour, minute)
            else:
                time_str = gc_time
                gc_time_obj = time_class(15, 0)  # 기본값

            # 14:59 이전인지 체크
            late_cutoff = time_class(14, 59, 0)
            if gc_time_obj > late_cutoff:
                console.print(f"    ❌ {time_str} @ {gc['close']:,}원 [red](14:59 이후 차단)[/red]")
                continue

            # 12:00 이전만 진입 (오전장)
            morning_cutoff = time_class(12, 0, 0)
            if gc_time_obj > morning_cutoff:
                console.print(f"    ❌ {time_str} @ {gc['close']:,}원 [yellow](12:00 이후 - 오후장 차단)[/yellow]")
                continue

            # 모멘텀 상승 조건 체크
            if gc['momentum'] <= 0:
                console.print(f"    ❌ {time_str} @ {gc['close']:,}원 [yellow](모멘텀 하락 - 차단)[/yellow]")
                continue

            # 진입 가능하면 시뮬레이션
            entry_price = gc['close']
            trade_result = simulate_trade(df_with_ma, gc['index'], entry_price)

            # 80,000원 기준 투자 시 실제 손익
            investment = 80000
            qty = int(investment / entry_price)
            actual_pnl = qty * trade_result['pnl_amount']

            pnl_color = "green" if trade_result['pnl_pct'] >= 0 else "red"
            exit_time_str = f"{trade_result['exit_time'][:2]}:{trade_result['exit_time'][2:4]}"

            console.print(f"    → {time_str} 진입 @ {entry_price:,}원 → {exit_time_str} 청산 @ {trade_result['exit_price']:,}원 ({trade_result['exit_reason']})")
            console.print(f"      [{pnl_color}]손익: {trade_result['pnl_pct']*100:+.2f}% / 8만원 투자 시: {actual_pnl:+,}원[/{pnl_color}]")

            all_trades.append({
                'stock_code': stock_code,
                'stock_name': stock_name,
                'entry_time': time_str,
                'entry_price': entry_price,
                'exit_time': exit_time_str,
                'exit_price': trade_result['exit_price'],
                'exit_reason': trade_result['exit_reason'],
                'pnl_pct': trade_result['pnl_pct'],
                'pnl_amount': trade_result['pnl_amount'],
                'qty': qty,
                'actual_pnl': actual_pnl
            })

    # 최종 결과 요약
    console.print()
    console.print("="*100, style="bold cyan")
    console.print(f"📋 시뮬레이션 최종 결과 ({date_display})", style="bold cyan")
    console.print("="*100, style="bold cyan")
    console.print()

    console.print(f"분석 종목: {total_stocks}개")
    console.print(f"골든크로스 발생 종목: {len(set(t['stock_code'] for t in all_trades))}개")
    console.print(f"진입 가능 거래: {len(all_trades)}건")
    console.print()

    # 결과 반환용
    result_summary = {
        'date': date_display,
        'total_stocks': total_stocks,
        'trades': len(all_trades),
        'total_pnl': 0,
        'win_count': 0,
        'loss_count': 0
    }

    if not all_trades:
        console.print(f"[yellow]수정된 로직으로는 {date_display}에 진입 가능한 종목이 없었습니다.[/yellow]")
    else:
        table = Table(title="수정된 로직 거래 결과 (8만원씩 투자 가정)")
        table.add_column("종목", style="cyan")
        table.add_column("진입", style="white")
        table.add_column("진입가", justify="right")
        table.add_column("청산", style="white")
        table.add_column("청산가", justify="right")
        table.add_column("사유", style="yellow")
        table.add_column("수익률", justify="right")
        table.add_column("손익", justify="right")

        total_pnl = 0
        win_count = 0
        loss_count = 0

        for trade in all_trades:
            pnl_color = "green" if trade['pnl_pct'] >= 0 else "red"
            if trade['pnl_pct'] >= 0:
                win_count += 1
            else:
                loss_count += 1

            table.add_row(
                trade['stock_name'],
                trade['entry_time'],
                f"{trade['entry_price']:,}",
                trade['exit_time'],
                f"{trade['exit_price']:,}",
                trade['exit_reason'],
                f"[{pnl_color}]{trade['pnl_pct']*100:+.2f}%[/{pnl_color}]",
                f"[{pnl_color}]{trade['actual_pnl']:+,}원[/{pnl_color}]"
            )
            total_pnl += trade['actual_pnl']

        console.print(table)
        console.print()

        # 통계
        win_rate = (win_count / len(all_trades)) * 100 if all_trades else 0
        avg_pnl = total_pnl / len(all_trades) if all_trades else 0

        total_color = "green" if total_pnl >= 0 else "red"
        console.print(f"승률: {win_rate:.1f}% ({win_count}승 {loss_count}패)")
        console.print(f"평균 손익: {avg_pnl:+,.0f}원")
        console.print(f"[{total_color}][bold]총 손익: {total_pnl:+,.0f}원[/bold][/{total_color}]")

        result_summary['total_pnl'] = total_pnl
        result_summary['win_count'] = win_count
        result_summary['loss_count'] = loss_count

    console.print()
    return result_summary


def run_multi_day_simulation():
    """여러 날짜 시뮬레이션"""
    # 최근 거래일들
    dates = [
        "20260116",  # 목요일
        "20260115",  # 수요일
        "20260114",  # 화요일
        "20260113",  # 월요일
        "20260112",  # 일요일 (거래 없음)
        "20260110",  # 금요일
        "20260109",  # 목요일
        "20260108",  # 수요일
    ]

    all_results = []

    for date in dates:
        result = run_simulation(date)
        all_results.append(result)

    # 전체 요약
    console.print()
    console.print("="*100, style="bold green")
    console.print("📊 다중 날짜 시뮬레이션 종합 결과", style="bold green")
    console.print("="*100, style="bold green")
    console.print()

    table = Table(title="날짜별 시뮬레이션 결과")
    table.add_column("날짜", style="cyan")
    table.add_column("거래 수", justify="right")
    table.add_column("승", justify="right", style="green")
    table.add_column("패", justify="right", style="red")
    table.add_column("승률", justify="right")
    table.add_column("총 손익", justify="right")

    grand_total_pnl = 0
    grand_total_trades = 0
    grand_total_wins = 0
    grand_total_losses = 0

    for r in all_results:
        if r['trades'] > 0:
            win_rate = (r['win_count'] / r['trades']) * 100
            pnl_color = "green" if r['total_pnl'] >= 0 else "red"
        else:
            win_rate = 0
            pnl_color = "white"

        table.add_row(
            r['date'],
            str(r['trades']),
            str(r['win_count']),
            str(r['loss_count']),
            f"{win_rate:.1f}%",
            f"[{pnl_color}]{r['total_pnl']:+,}원[/{pnl_color}]"
        )

        grand_total_pnl += r['total_pnl']
        grand_total_trades += r['trades']
        grand_total_wins += r['win_count']
        grand_total_losses += r['loss_count']

    console.print(table)
    console.print()

    # 전체 통계
    if grand_total_trades > 0:
        overall_win_rate = (grand_total_wins / grand_total_trades) * 100
    else:
        overall_win_rate = 0

    total_color = "green" if grand_total_pnl >= 0 else "red"

    console.print(f"[bold]전체 기간 통계:[/bold]")
    console.print(f"  총 거래: {grand_total_trades}건")
    console.print(f"  승률: {overall_win_rate:.1f}% ({grand_total_wins}승 {grand_total_losses}패)")
    console.print(f"  [{total_color}][bold]총 손익: {grand_total_pnl:+,}원[/bold][/{total_color}]")

    if grand_total_trades > 0:
        avg_pnl = grand_total_pnl / grand_total_trades
        console.print(f"  평균 거래당 손익: {avg_pnl:+,.0f}원")

    console.print()


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "multi":
        run_multi_day_simulation()
    else:
        run_simulation()
