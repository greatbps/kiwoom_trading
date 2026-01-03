"""
병렬 처리 벤치마크 결과를 Rich로 출력
"""
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.text import Text

console = Console()

# 실측 결과 데이터
results = [
    {"rank": 1, "code": "001440", "name": "대한전선", "score": 73.15},
    {"rank": 2, "code": "005070", "name": "코스모신소재", "score": 76.73},
    {"rank": 3, "code": "005690", "name": "파미셀", "score": 71.72},
    {"rank": 4, "code": "007660", "name": "이수페타시스", "score": 72.73},
    {"rank": 5, "code": "009830", "name": "한화솔루션", "score": 65.67},
    {"rank": 6, "code": "010140", "name": "삼성중공업", "score": 71.00},
    {"rank": 7, "code": "011080", "name": "형지I&C", "score": 70.03},
    {"rank": 8, "code": "022100", "name": "포스코DX", "score": 71.90},
    {"rank": 9, "code": "062040", "name": "산일전기", "score": 73.18},
    {"rank": 10, "code": "090360", "name": "로보스타", "score": 77.43},
]

# 점수 순으로 정렬
results_sorted = sorted(results, key=lambda x: x["score"], reverse=True)

# 타이틀
console.print()
console.print(Panel.fit(
    "[bold cyan]🚀 병렬 처리 성능 벤치마크 결과[/bold cyan]",
    border_style="cyan"
))
console.print()

# 종목별 결과 테이블
table = Table(
    title="📊 10개 종목 분석 결과 (점수순)",
    box=box.ROUNDED,
    border_style="cyan",
    show_header=True,
    header_style="bold magenta"
)

table.add_column("순위", justify="center", style="cyan", width=6)
table.add_column("종목코드", justify="center", style="yellow", width=10)
table.add_column("종목명", justify="left", style="white", width=15)
table.add_column("점수", justify="right", style="green", width=10)
table.add_column("메달", justify="center", width=6)
table.add_column("추천", justify="center", width=12)

for idx, r in enumerate(results_sorted, 1):
    # 메달
    if idx == 1:
        medal = "🥇"
    elif idx == 2:
        medal = "🥈"
    elif idx == 3:
        medal = "🥉"
    else:
        medal = ""

    # 점수 색상
    score = r["score"]
    if score >= 75:
        score_style = "[bold green]"
        recommendation = "[bold green]적극 매수[/bold green]"
    elif score >= 70:
        score_style = "[green]"
        recommendation = "[green]적극 매수[/green]"
    else:
        score_style = "[yellow]"
        recommendation = "[yellow]매수[/yellow]"

    table.add_row(
        f"{idx}",
        r["code"],
        r["name"],
        f"{score_style}{score:.2f}점[/]",
        medal,
        recommendation
    )

console.print(table)
console.print()

# 성능 비교 테이블
perf_table = Table(
    title="⚡ 성능 비교",
    box=box.DOUBLE_EDGE,
    border_style="green",
    show_header=True,
    header_style="bold cyan"
)

perf_table.add_column("처리 방식", justify="left", style="cyan", width=15)
perf_table.add_column("10개 종목", justify="right", style="white", width=15)
perf_table.add_column("100개 예상", justify="right", style="white", width=15)
perf_table.add_column("비고", justify="left", width=20)

perf_table.add_row(
    "[red]순차 처리[/red]",
    "[red]102.7초 (1.7분)[/red]",
    "[red]1,027초 (17.1분)[/red]",
    "느림 😴"
)
perf_table.add_row(
    "[bold green]병렬 처리[/bold green]",
    "[bold green]22.8초 (0.4분)[/bold green]",
    "[bold green]227초 (3.8분)[/bold green]",
    "빠름 🚀"
)

console.print(perf_table)
console.print()

# 개선 효과 패널
improvement_text = Text()
improvement_text.append("속도 향상: ", style="bold white")
improvement_text.append("4.51배 ", style="bold green")
improvement_text.append("빠름!\n", style="bold white")
improvement_text.append("시간 단축: ", style="bold white")
improvement_text.append("77.9% ", style="bold cyan")
improvement_text.append("절약\n\n", style="bold white")
improvement_text.append("기술 스택:\n", style="bold yellow")
improvement_text.append("  • Python asyncio + ThreadPoolExecutor\n", style="white")
improvement_text.append("  • 최대 5개 종목 동시 처리\n", style="white")
improvement_text.append("  • API 호출 제한 준수 (초당 5회)", style="white")

console.print(Panel(
    improvement_text,
    title="[bold green]✨ 병렬 처리 최적화 성공![/bold green]",
    border_style="green",
    box=box.DOUBLE
))
console.print()

# 100개 종목 예상 패널
estimate_text = Text()
estimate_text.append("순차 처리: ", style="bold white")
estimate_text.append("17.1분 ", style="red")
estimate_text.append("→ ", style="white")
estimate_text.append("병렬 처리: ", style="bold white")
estimate_text.append("3.8분\n\n", style="bold green")
estimate_text.append("🎯 실전 적용 시 ", style="bold yellow")
estimate_text.append("13.3분 ", style="bold green")
estimate_text.append("절약!", style="bold yellow")

console.print(Panel(
    estimate_text,
    title="[bold cyan]📈 100개 종목 예상 소요 시간[/bold cyan]",
    border_style="cyan",
    box=box.ROUNDED
))
console.print()
