#!/usr/bin/env python3
"""
실제 계좌 체결 내역 조회 (kt00007)
- HTS와 시스템 기록을 비교하기 위함
"""

import os
import json
from datetime import datetime
from dotenv import load_dotenv
from kiwoom_api import KiwoomAPI
from rich.console import Console
from rich.table import Table

console = Console()

# .env 로드
load_dotenv()

# 환경변수
APP_KEY = os.getenv('KIWOOM_APP_KEY')
APP_SECRET = os.getenv('KIWOOM_APP_SECRET')
ACCOUNT_NUMBER = os.getenv('KIWOOM_ACCOUNT_NUMBER')

def get_executed_trades_today():
    """오늘 실제 체결 내역 조회"""

    console.print("\n[bold cyan]=" * 60 + "[/bold cyan]")
    console.print("[bold cyan]📊 계좌 실제 체결 내역 조회 (kt00007)[/bold cyan]")
    console.print("[bold cyan]=" * 60 + "[/bold cyan]\n")

    # API 초기화
    api = KiwoomAPI(APP_KEY, APP_SECRET, ACCOUNT_NUMBER)

    # 1. Access Token 발급
    console.print("[yellow]1단계: AccessToken 발급 중...[/yellow]")
    try:
        access_token = api.get_access_token()
        console.print(f"[green]✅ 토큰 발급 성공[/green]\n")
    except Exception as e:
        console.print(f"[red]❌ 토큰 발급 실패: {e}[/red]")
        return

    # 2. 오늘 날짜
    today = datetime.now().strftime('%Y%m%d')
    console.print(f"[cyan]조회 날짜: {today}[/cyan]\n")

    # 3. 체결 내역 조회
    console.print("[yellow]2단계: 체결 내역 조회 중...[/yellow]")

    url = f"{api.BASE_URL}/api/dostk/acnt"

    headers = {
        'Content-Type': 'application/json',
        'api-id': 'kt00007',
        'authorization': f'Bearer {access_token}'
    }

    body = {
        'ord_dt': today,        # 주문일자 (오늘)
        'qry_tp': '4',          # 4: 체결내역만
        'stk_bond_tp': '1',     # 1: 주식
        'sell_tp': '0',         # 0: 전체 (매수+매도)
        'dmst_stex_tp': '%'     # %: 전체 거래소
    }

    try:
        response = api.session.post(url, headers=headers, json=body)
        result = response.json()

        console.print(f"[dim]API 응답 코드: {response.status_code}[/dim]")

        if result.get('return_code') != 0:
            console.print(f"[red]❌ 조회 실패: {result.get('return_msg')}[/red]")
            console.print(f"[dim]{json.dumps(result, indent=2, ensure_ascii=False)}[/dim]")
            return

        # 4. 결과 파싱
        trades = result.get('acnt_ord_cntr_prps_dtl', [])

        console.print(f"\n[green]✅ 총 {len(trades)}건의 체결 내역 조회 완료[/green]\n")

        if not trades:
            console.print("[yellow]⚠️  오늘 체결 내역이 없습니다.[/yellow]")
            return

        # 5. 테이블로 출력
        table = Table(title=f"{today} 실제 체결 내역", show_header=True, header_style="bold magenta")

        table.add_column("#", style="dim", width=3)
        table.add_column("시간", width=8)
        table.add_column("종목명", width=15)
        table.add_column("종목코드", width=10)
        table.add_column("구분", width=6)
        table.add_column("수량", justify="right", width=6)
        table.add_column("체결가", justify="right", width=10)
        table.add_column("체결금액", justify="right", width=12)
        table.add_column("주문번호", width=8)

        # 매수/매도 집계
        buy_count = 0
        sell_count = 0
        buy_amount = 0
        sell_amount = 0

        for i, trade in enumerate(trades, 1):
            # 필드 추출
            ord_no = trade.get('ord_no', '')          # 주문번호
            stk_cd = trade.get('stk_cd', '')          # 종목코드
            stk_nm = trade.get('stk_nm', '')          # 종목명
            io_tp_nm = trade.get('io_tp_nm', '')      # 입출금구분 (매수/매도 구분)
            ord_tm = trade.get('ord_tm', '')          # 주문시간
            cntr_qty = trade.get('cntr_qty', '0')     # 체결수량
            cntr_uv = trade.get('cntr_uv', '0')       # 체결단가
            ord_qty = trade.get('ord_qty', '0')       # 주문수량

            # 수량과 가격을 int로 변환
            qty = int(cntr_qty)
            price = int(cntr_uv)
            amount = qty * price

            # 매매구분: io_tp_nm 필드 사용 (현금매수/현금매도)
            if '매도' in io_tp_nm:
                trade_type = "매도"
                trade_color = "red"
                sell_count += 1
                sell_amount += amount
            else:
                trade_type = "매수"
                trade_color = "green"
                buy_count += 1
                buy_amount += amount

            # 시간 포맷 (HH:MM:SS)
            time_str = ord_tm if ord_tm else ""

            table.add_row(
                str(i),
                time_str,
                stk_nm,
                stk_cd,
                f"[{trade_color}]{trade_type}[/{trade_color}]",
                f"{qty}주",
                f"{price:,.0f}원",
                f"{amount:,.0f}원",
                ord_no
            )

        console.print(table)

        # 6. 요약
        console.print()
        console.print("[bold cyan]=" * 60 + "[/bold cyan]")
        console.print("[bold cyan]📊 체결 요약[/bold cyan]")
        console.print("[bold cyan]=" * 60 + "[/bold cyan]")
        console.print(f"[green]매수: {buy_count}건, {buy_amount:,}원[/green]")
        console.print(f"[red]매도: {sell_count}건, {sell_amount:,}원[/red]")

        realized_pnl = sell_amount - buy_amount
        pnl_color = "green" if realized_pnl >= 0 else "red"
        console.print(f"[{pnl_color}]실현 손익: {realized_pnl:+,}원[/{pnl_color}]")

        # 7. risk_log.json과 비교
        console.print()
        console.print("[bold yellow]=" * 60 + "[/bold yellow]")
        console.print("[bold yellow]🔍 시스템 기록(risk_log.json)과 비교[/bold yellow]")
        console.print("[bold yellow]=" * 60 + "[/bold yellow]")

        try:
            with open('data/risk_log.json', 'r', encoding='utf-8') as f:
                risk_log = json.load(f)

            # 🔧 2026-07-27: today 불일치 시 daily_trades/daily_realized_pnl은
            # 지난 거래일의 스탈 값이므로 "오늘" 비교로 쓰지 않는다 (freshness 미검증 버그)
            if risk_log.get('today') != datetime.now().strftime('%Y-%m-%d'):
                console.print(f"[yellow]⚠️  risk_log.json 이 오늘자가 아님 (기록일: {risk_log.get('today')}) "
                              f"— 스탈 데이터라 비교 생략[/yellow]")
            else:
                logged_trades = risk_log.get('daily_trades', [])
                logged_pnl = risk_log.get('daily_realized_pnl', 0.0)

                console.print(f"시스템 기록: {len(logged_trades)}건")
                console.print(f"실제 체결: {len(trades)}건")
                console.print()

                if len(logged_trades) != len(trades):
                    console.print(f"[red]❌ 불일치! 차이: {len(trades) - len(logged_trades)}건[/red]")
                    console.print(f"[red]   시스템이 {abs(len(trades) - len(logged_trades))}건의 거래를 기록하지 못했습니다![/red]")
                else:
                    console.print("[green]✅ 거래 건수 일치[/green]")

                if abs(logged_pnl - realized_pnl) > 0.01:
                    console.print(f"[red]❌ 손익 불일치![/red]")
                    console.print(f"   시스템 기록: {logged_pnl:+,}원")
                    console.print(f"   실제 손익: {realized_pnl:+,}원")
                else:
                    console.print(f"[green]✅ 손익 일치: {realized_pnl:+,}원[/green]")

        except Exception as e:
            console.print(f"[yellow]⚠️  risk_log.json 비교 실패: {e}[/yellow]")

        console.print()

        # 8. 전체 응답 저장 (디버깅용)
        with open('/tmp/real_trades.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        console.print("[dim]💾 전체 응답 저장: /tmp/real_trades.json[/dim]")

    except Exception as e:
        console.print(f"[red]❌ 오류 발생: {e}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    get_executed_trades_today()
