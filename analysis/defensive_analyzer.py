"""
DEFENSIVE 모드 성능 분석기
auto_trading_YYYYMMDD.log에서 [DEF_RESULT] 태그를 파싱하여 성능 리포트 생성.
20건 이상 체결 시 자동 실행 가능.

사용법:
    python3 -m analysis.defensive_analyzer                    # 오늘 로그
    python3 -m analysis.defensive_analyzer --date 20260403   # 특정 날짜
    python3 -m analysis.defensive_analyzer --days 7          # 최근 7일 합산
    python3 -m analysis.defensive_analyzer --min-trades 1    # 최소 거래 기준 낮춤
"""
import re
import sys
import glob
import argparse
from datetime import datetime, timedelta
from pathlib import Path


LOG_DIR = Path(__file__).parent.parent / "logs"
MIN_TRADES_FOR_FULL_REPORT = 20

# [DEF_RESULT] 파싱 패턴
# 예: [DEF_RESULT] 005930 | exit=TP | pnl=0.98% | hold=7.3m | MFE=1.234% MAE=0.021% | entry=DEFENSIVE:10:42 RSI=28.3...
DEF_RESULT_PATTERN = re.compile(
    r"\[DEF_RESULT\] (\w+) \| exit=(\w+) \| "
    r"pnl=(-?\d+\.\d+)% \| hold=(\d+\.\d+)m \| "
    r"MFE=(-?\d+\.\d+)% MAE=(-?\d+\.\d+)% \| "
    r"entry=(.+)"
)

# [DEF_SIG] 진입 시도 카운트용
DEF_SIG_PATTERN = re.compile(r"\[DEF_SIG\] (\w+) ")


def parse_log_file(log_path: Path) -> dict:
    """단일 로그 파일 파싱 → 거래 결과 리스트 반환"""
    trades = []
    sig_count = 0

    if not log_path.exists():
        return {"trades": [], "sig_count": 0, "date": log_path.stem[-8:]}

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            if "[DEF_RESULT]" in line:
                m = DEF_RESULT_PATTERN.search(line)
                if m:
                    trades.append({
                        "code": m.group(1),
                        "exit_type": m.group(2),
                        "pnl_pct": float(m.group(3)),
                        "hold_min": float(m.group(4)),
                        "mfe_pct": float(m.group(5)),
                        "mae_pct": float(m.group(6)),
                        "entry_reason": m.group(7).strip(),
                    })
            elif "[DEF_SIG]" in line:
                sig_count += 1

    return {"trades": trades, "sig_count": sig_count, "date": log_path.stem[-8:]}


def compute_stats(trades: list) -> dict:
    """거래 리스트 → 성능 지표"""
    if not trades:
        return {}

    n = len(trades)
    wins = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]

    win_rate = len(wins) / n * 100
    avg_pnl = sum(t["pnl_pct"] for t in trades) / n
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0.0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0.0
    rr = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    avg_mfe = sum(t["mfe_pct"] for t in trades) / n
    avg_mae = sum(t["mae_pct"] for t in trades) / n
    avg_hold = sum(t["hold_min"] for t in trades) / n

    # 청산 유형 분포
    exit_dist = {}
    for t in trades:
        exit_dist[t["exit_type"]] = exit_dist.get(t["exit_type"], 0) + 1

    # MFE/MAE 효율성: TP는 MFE 대비 얼마나 잡았는가?
    tp_trades = [t for t in trades if t["exit_type"] == "TP"]
    tp_efficiency = (
        sum(t["pnl_pct"] / t["mfe_pct"] for t in tp_trades if t["mfe_pct"] > 0) / len(tp_trades)
        if tp_trades else 0.0
    )

    # 손절 시 MAE vs pnl (손절이 너무 늦지 않은가?)
    stop_trades = [t for t in trades if t["exit_type"] == "STOP"]
    avg_stop_delay = (
        sum(abs(t["pnl_pct"]) - t["mae_pct"] for t in stop_trades) / len(stop_trades)
        if stop_trades else 0.0
    )

    return {
        "n": n,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr": rr,
        "avg_mfe": avg_mfe,
        "avg_mae": avg_mae,
        "avg_hold": avg_hold,
        "exit_dist": exit_dist,
        "tp_efficiency": tp_efficiency,
        "avg_stop_delay": avg_stop_delay,
    }


def print_report(stats: dict, trade_count: int, sig_count: int, period: str):
    """콘솔 리포트 출력"""
    sep = "=" * 62

    print(f"\n{sep}")
    print(f"  DEFENSIVE 성능 분석 ({period})")
    print(sep)

    if trade_count == 0:
        print("  ⚠️  체결 기록 없음 (DEF_RESULT 0건)")
        print(f"{sep}\n")
        return

    if trade_count < MIN_TRADES_FOR_FULL_REPORT:
        print(f"  ℹ️  샘플 {trade_count}건 (신뢰도 낮음, 목표 {MIN_TRADES_FOR_FULL_REPORT}건)")
    else:
        print(f"  ✅ 충분한 샘플: {trade_count}건")

    print()
    print(f"  신호 시도:  {sig_count}회  체결: {trade_count}건")
    print(f"  ─────────────────────────────────────────────────")
    print(f"  승  률:    {stats['win_rate']:.1f}%  ({int(stats['win_rate']*trade_count/100)}승 {trade_count - int(stats['win_rate']*trade_count/100)}패)")
    print(f"  평균 손익:  {stats['avg_pnl']:+.3f}%")
    print(f"  평균 수익:  +{stats['avg_win']:.3f}%   평균 손실: {stats['avg_loss']:.3f}%")
    print(f"  손익비(RR): {stats['rr']:.2f}")
    print()
    print(f"  ─────────────  MFE / MAE  ────────────────────────")
    print(f"  평균 MFE:  +{stats['avg_mfe']:.3f}%  (최대 유리 움직임)")
    print(f"  평균 MAE:   {stats['avg_mae']:.3f}%  (최대 불리 움직임)")
    print(f"  TP 효율:   {stats['tp_efficiency']*100:.1f}%  (TP 체결 시 MFE 대비 수익 비율)")
    print(f"  손절 지연:  {stats['avg_stop_delay']:+.3f}%  (MAE 대비 추가 낙폭)")
    print()
    print(f"  ─────────────  청산 유형  ────────────────────────")
    for etype, cnt in sorted(stats["exit_dist"].items(), key=lambda x: -x[1]):
        bar = "█" * int(cnt / trade_count * 20)
        print(f"  {etype:<8} {cnt:3d}건  {bar}")
    print()
    print(f"  평균 보유:  {stats['avg_hold']:.1f}분")

    # 권고사항
    print()
    print(f"  ─────────────  권고사항  ────────────────────────")
    if stats["win_rate"] < 45:
        print(f"  ⚠️  승률 {stats['win_rate']:.1f}% < 45% → 진입 조건 강화 검토")
        print(f"     (max_rsi 낮추기, min_volume_ratio 올리기)")
    if stats["rr"] < 0.8:
        print(f"  ⚠️  RR {stats['rr']:.2f} < 0.8 → 손절/익절 비율 재조정")
        print(f"     (take_profit_pct 올리기 또는 stop_loss_pct 줄이기)")
    if stats["tp_efficiency"] < 0.6 and stats["exit_dist"].get("TP", 0) > 0:
        print(f"  ⚠️  TP 효율 {stats['tp_efficiency']*100:.1f}% < 60% → TP 너무 일찍 설정됨")
        print(f"     (take_profit_pct 올리기 검토)")
    if stats["avg_stop_delay"] > 0.2:
        print(f"  ⚠️  손절 지연 {stats['avg_stop_delay']:.3f}% → 손절 가격이 너무 낮음")
        print(f"     (stop_loss_pct 줄이기 검토)")
    if stats["win_rate"] >= 55 and stats["rr"] >= 1.0:
        print(f"  ✅ 전략 양호 (승률 {stats['win_rate']:.1f}%, RR {stats['rr']:.2f})")

    print(f"{sep}\n")


def collect_log_paths(args) -> list:
    """인자 기반 로그 파일 경로 수집"""
    if args.date:
        return [LOG_DIR / f"auto_trading_{args.date}.log"]

    if args.days:
        paths = []
        today = datetime.today()
        for i in range(args.days):
            d = today - timedelta(days=i)
            paths.append(LOG_DIR / f"auto_trading_{d.strftime('%Y%m%d')}.log")
        return paths

    # 기본: 오늘
    today = datetime.today().strftime("%Y%m%d")
    return [LOG_DIR / f"auto_trading_{today}.log"]


def main():
    parser = argparse.ArgumentParser(description="DEFENSIVE 모드 성능 분석기")
    parser.add_argument("--date", type=str, help="특정 날짜 (YYYYMMDD)")
    parser.add_argument("--days", type=int, help="최근 N일 합산")
    parser.add_argument("--min-trades", type=int, default=1, help="최소 거래 기준 (기본 1)")
    args = parser.parse_args()

    log_paths = collect_log_paths(args)

    all_trades = []
    total_sigs = 0
    parsed_dates = []

    for lp in log_paths:
        result = parse_log_file(lp)
        if result["trades"] or result["sig_count"] > 0:
            all_trades.extend(result["trades"])
            total_sigs += result["sig_count"]
            parsed_dates.append(result["date"])

    if len(parsed_dates) == 1:
        period = parsed_dates[0]
    elif parsed_dates:
        period = f"{parsed_dates[-1]}~{parsed_dates[0]}"
    else:
        period = "기록 없음"

    if len(all_trades) < args.min_trades:
        print(f"\n⚠️  DEFENSIVE 체결 {len(all_trades)}건 (기준 {args.min_trades}건 미달)")
        print(f"   신호 시도: {total_sigs}건\n")
        return

    stats = compute_stats(all_trades)
    print_report(stats, len(all_trades), total_sigs, period)


if __name__ == "__main__":
    main()
