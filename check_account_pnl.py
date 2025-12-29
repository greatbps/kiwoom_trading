#!/usr/bin/env python3
"""
계좌수익률 및 당일매도손익 조회 (ka10085)
"""

import os
import json
from dotenv import load_dotenv
from kiwoom_api import KiwoomAPI
from rich.console import Console
from rich.table import Table

console = Console()

# .env 로드
load_dotenv()

APP_KEY = os.getenv('KIWOOM_APP_KEY')
APP_SECRET = os.getenv('KIWOOM_APP_SECRET')
ACCOUNT_NUMBER = os.getenv('KIWOOM_ACCOUNT_NUMBER')

def check_account_pnl():
    """계좌수익률 및 당일매도손익 조회"""

    console.print("\n[bold cyan]=" * 60 + "[/bold cyan]")
    console.print("[bold cyan]💰 계좌수익률 및 당일매도손익 조회 (ka10085)[/bold cyan]")
    console.print("[bold cyan]=" * 60 + "[/bold cyan]\n")

    # API 초기화
    api = KiwoomAPI(APP_KEY, APP_SECRET, ACCOUNT_NUMBER)

    # 1. Access Token 발급
    console.print("[yellow]AccessToken 발급 중...[/yellow]")
    try:
        access_token = api.get_access_token()
        console.print(f"[green]✅ 토큰 발급 성공[/green]\n")
    except Exception as e:
        console.print(f"[red]❌ 토큰 발급 실패: {e}[/red]")
        return

    # 2. 계좌수익률 조회
    console.print("[yellow]계좌수익률 조회 중...[/yellow]")

    url = f"{api.BASE_URL}/api/dostk/acnt"

    headers = {
        'Content-Type': 'application/json',
        'api-id': 'ka10085',
        'authorization': f'Bearer {access_token}'
    }

    body = {
        'stex_tp': '0'  # 0: 통합 (KRX + NXT)
    }

    try:
        response = api.session.post(url, headers=headers, json=body)
        result = response.json()

        console.print(f"[dim]API 응답 코드: {response.status_code}[/dim]")

        if result.get('return_code') != 0:
            console.print(f"[red]❌ 조회 실패: {result.get('return_msg')}[/red]")
            console.print(f"[dim]{json.dumps(result, indent=2, ensure_ascii=False)}[/dim]")
            return

        # 3. 결과 파싱
        positions = result.get('acnt_prft_rt', [])

        console.print(f"\n[green]✅ 총 {len(positions)}개 포지션 조회 완료[/green]\n")

        if not positions:
            console.print("[yellow]⚠️  보유 포지션이 없습니다.[/yellow]")
            return

        # 4. 테이블로 출력
        table = Table(title="계좌 포지션 및 당일 손익", show_header=True, header_style="bold magenta")

        table.add_column("#", style="dim", width=3)
        table.add_column("종목명", width=15)
        table.add_column("종목코드", width=10)
        table.add_column("보유수량", justify="right", width=8)
        table.add_column("매입가", justify="right", width=10)
        table.add_column("현재가", justify="right", width=10)
        table.add_column("평가손익", justify="right", width=12)
        table.add_column("당일매도손익", justify="right", width=12)
        table.add_column("수수료", justify="right", width=10)
        table.add_column("세금", justify="right", width=10)

        total_tdy_pnl = 0
        total_tdy_cmsn = 0
        total_tdy_tax = 0

        for i, pos in enumerate(positions, 1):
            stk_cd = pos.get('stk_cd', '')
            stk_nm = pos.get('stk_nm', '')
            rmnd_qty = pos.get('rmnd_qty', '0')
            pur_pric = pos.get('pur_pric', '0')
            cur_prc = pos.get('cur_prc', '0')
            tdy_sel_pl = pos.get('tdy_sel_pl', '0')
            tdy_trde_cmsn = pos.get('tdy_trde_cmsn', '0')
            tdy_trde_tax = pos.get('tdy_trde_tax', '0')

            # 숫자 변환
            qty = int(rmnd_qty)
            buy_price = int(pur_pric)
            current_price = int(cur_prc)
            tdy_pnl = int(tdy_sel_pl)
            tdy_cmsn = int(tdy_trde_cmsn)
            tdy_tax = int(tdy_trde_tax)

            # 평가손익 계산
            eval_pnl = (current_price - buy_price) * qty

            # 집계
            total_tdy_pnl += tdy_pnl
            total_tdy_cmsn += tdy_cmsn
            total_tdy_tax += tdy_tax

            # 색상 결정
            eval_color = "green" if eval_pnl >= 0 else "red"
            tdy_color = "green" if tdy_pnl >= 0 else "red"

            table.add_row(
                str(i),
                stk_nm,
                stk_cd,
                f"{qty}주",
                f"{buy_price:,}원",
                f"{current_price:,}원",
                f"[{eval_color}]{eval_pnl:+,}원[/{eval_color}]",
                f"[{tdy_color}]{tdy_pnl:+,}원[/{tdy_color}]",
                f"{tdy_cmsn:,}원",
                f"{tdy_tax:,}원"
            )

        console.print(table)

        # 5. 당일 손익 요약
        console.print()
        console.print("[bold cyan]=" * 60 + "[/bold cyan]")
        console.print("[bold cyan]📊 당일 매도손익 요약[/bold cyan]")
        console.print("[bold cyan]=" * 60 + "[/bold cyan]")

        net_pnl = total_tdy_pnl - total_tdy_cmsn - total_tdy_tax
        pnl_color = "green" if net_pnl >= 0 else "red"

        console.print(f"[yellow]총 매도손익:[/yellow] {total_tdy_pnl:+,}원")
        console.print(f"[yellow]총 수수료:[/yellow] {total_tdy_cmsn:,}원")
        console.print(f"[yellow]총 세금:[/yellow] {total_tdy_tax:,}원")
        console.print(f"[bold {pnl_color}]순 손익:[/bold {pnl_color}] [{pnl_color}]{net_pnl:+,}원[/{pnl_color}]")

        # 6. risk_log.json과 비교
        console.print()
        console.print("[bold yellow]=" * 60 + "[/bold yellow]")
        console.print("[bold yellow]🔍 시스템 기록과 비교[/bold yellow]")
        console.print("[bold yellow]=" * 60 + "[/bold yellow]")

        try:
            with open('data/risk_log.json', 'r', encoding='utf-8') as f:
                risk_log = json.load(f)

            logged_pnl = risk_log.get('daily_realized_pnl', 0.0)

            console.print(f"시스템 기록 손익: {logged_pnl:+,}원")
            console.print(f"실제 당일 손익: {net_pnl:+,}원")
            console.print()

            if abs(logged_pnl - net_pnl) > 0.01:
                console.print(f"[red]❌ 불일치! 차이: {net_pnl - logged_pnl:+,}원[/red]")
            else:
                console.print(f"[green]✅ 일치[/green]")

        except Exception as e:
            console.print(f"[yellow]⚠️  risk_log.json 비교 실패: {e}[/yellow]")

        console.print()

        # 7. 전체 응답 저장
        with open('/tmp/account_pnl.json', 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        console.print("[dim]💾 전체 응답 저장: /tmp/account_pnl.json[/dim]")
        console.print()

    except Exception as e:
        console.print(f"[red]❌ 오류 발생: {e}[/red]")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    check_account_pnl()
