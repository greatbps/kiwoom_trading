"""
RS 전략 성능 분석기
auto_trading_YYYYMMDD.log에서 [RS_RESULT] 태그를 파싱하여 성능 리포트 생성.

사용법:
    python3 -m analysis.rs_analyzer                    # 오늘 로그
    python3 -m analysis.rs_analyzer --date 20260403   # 특정 날짜
    python3 -m analysis.rs_analyzer --days 7          # 최근 7일 합산
    python3 -m analysis.rs_analyzer --min-trades 1    # 최소 거래 기준 낮춤
"""
import re
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

LOG_DIR = Path(__file__).parent.parent / "logs"
MIN_TRADES_FOR_FULL_REPORT = 10   # DEF(20)보다 낮게 — RS는 빈도 낮음

# [RS_RESULT] 파싱 패턴
# 예: [RS_RESULT] 005930 | exit=TRAIL | pnl=2.15% | hold=18.3m |
#     MFE=2.890% MAE=0.120% | rs_score=1.45 | entry=RS:10:42 ...
RS_RESULT_PATTERN = re.compile(
    r"\[RS_RESULT\] (\w+) \| exit=(\w+) \| "
    r"pnl=(-?\d+\.\d+)% \| hold=(\d+\.\d+)m \| "
    r"MFE=(-?\d+\.\d+)% MAE=(-?\d+\.\d+)% \| "
    r"rs_score=([0-9.?]+) \| "
    r"entry=(.+)"
)

# [RS_SIG] 진입 시도 카운트 + score 추출
RS_SIG_PATTERN = re.compile(r"\[RS_SIG\] (\w+).*?score=([0-9.]+)")

# [RS_NO_SIG] 차단 이유
RS_NO_SIG_PATTERN = re.compile(r"\[RS_NO_SIG\] (\w+): (.+)")


def parse_log_file(log_path: Path) -> dict:
    trades = []
    sig_count = 0
    no_sig_reasons = {}

    if not log_path.exists():
        return {"trades": [], "sig_count": 0, "no_sig_reasons": {}, "date": log_path.stem[-8:]}

    with open(log_path, "r", encoding="utf-8") as f:
        for line in f:
            if "[RS_RESULT]" in line:
                m = RS_RESULT_PATTERN.search(line)
                if m:
                    rs_score_raw = m.group(7)
                    try:
                        rs_score_val = float(rs_score_raw)
                    except ValueError:
                        rs_score_val = None
                    trades.append({
                        "code": m.group(1),
                        "exit_type": m.group(2),
                        "pnl_pct": float(m.group(3)),
                        "hold_min": float(m.group(4)),
                        "mfe_pct": float(m.group(5)),
                        "mae_pct": float(m.group(6)),
                        "rs_score": rs_score_val,
                        "entry_reason": m.group(8).strip(),
                    })
            elif "[RS_SIG]" in line:
                sig_count += 1
            elif "[RS_NO_SIG]" in line:
                m2 = RS_NO_SIG_PATTERN.search(line)
                if m2:
                    reason = m2.group(2).split("(")[0].strip()  # 핵심 이유만
                    no_sig_reasons[reason] = no_sig_reasons.get(reason, 0) + 1

    return {
        "trades": trades,
        "sig_count": sig_count,
        "no_sig_reasons": no_sig_reasons,
        "date": log_path.stem[-8:],
    }


def compute_stats(trades: list) -> dict:
    if not trades:
        return {}

    n = len(trades)
    wins   = [t for t in trades if t["pnl_pct"] > 0]
    losses = [t for t in trades if t["pnl_pct"] <= 0]

    win_rate = len(wins) / n * 100
    avg_pnl  = sum(t["pnl_pct"] for t in trades) / n
    avg_win  = sum(t["pnl_pct"] for t in wins) / len(wins)   if wins   else 0.0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0.0
    rr       = abs(avg_win / avg_loss) if avg_loss != 0 else float("inf")

    avg_hold = sum(t["hold_min"] for t in trades) / n
    avg_mfe  = sum(t["mfe_pct"] for t in trades) / n
    avg_mae  = sum(t["mae_pct"] for t in trades) / n

    # 청산 유형 분포
    exit_dist = {}
    for t in trades:
        exit_dist[t["exit_type"]] = exit_dist.get(t["exit_type"], 0) + 1

    # 홀딩 시간 분포 (추세 전략 핵심)
    hold_lt5  = sum(1 for t in trades if t["hold_min"] < 5)
    hold_5_30 = sum(1 for t in trades if 5 <= t["hold_min"] < 30)
    hold_gt30 = sum(1 for t in trades if t["hold_min"] >= 30)

    # MFE vs TP 효율 (trailing이 충분히 늦게 작동했는가?)
    trail_trades = [t for t in trades if t["exit_type"] == "TRAIL"]
    trail_efficiency = (
        sum(t["pnl_pct"] / t["mfe_pct"] for t in trail_trades if t["mfe_pct"] > 0) / len(trail_trades)
        if trail_trades else 0.0
    )

    # RS 약세 조기 이탈 비율
    rs_weak_cnt = exit_dist.get("RS_WEAK", 0)
    rs_weak_ratio = rs_weak_cnt / n * 100

    # 평균 진입 RS Score
    scored = [t for t in trades if t["rs_score"] is not None]
    avg_rs_score = sum(t["rs_score"] for t in scored) / len(scored) if scored else None

    return {
        "n": n,
        "win_rate": win_rate,
        "avg_pnl": avg_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "rr": rr,
        "avg_hold": avg_hold,
        "avg_mfe": avg_mfe,
        "avg_mae": avg_mae,
        "exit_dist": exit_dist,
        "hold_dist": (hold_lt5, hold_5_30, hold_gt30),
        "trail_efficiency": trail_efficiency,
        "rs_weak_ratio": rs_weak_ratio,
        "avg_rs_score": avg_rs_score,
    }


def print_report(stats: dict, trade_count: int, sig_count: int, no_sig_reasons: dict, period: str):
    sep = "=" * 62

    print(f"\n{sep}")
    print(f"  RS 전략 성능 분석 ({period})")
    print(sep)

    if trade_count == 0:
        print("  ⚠️  체결 기록 없음 (RS_RESULT 0건)")
        _print_no_sig(no_sig_reasons, sig_count)
        print(f"{sep}\n")
        return

    if trade_count < MIN_TRADES_FOR_FULL_REPORT:
        print(f"  ℹ️  샘플 {trade_count}건 (신뢰도 낮음, 목표 {MIN_TRADES_FOR_FULL_REPORT}건)")
    else:
        print(f"  ✅ 충분한 샘플: {trade_count}건")

    print()
    print(f"  신호 시도:  {sig_count}회  체결: {trade_count}건")
    if stats.get("avg_rs_score"):
        print(f"  평균 진입 RS:  {stats['avg_rs_score']:.3f}")
    print()
    print(f"  ─────────────  수익 지표  ───────────────────────────")
    print(f"  승  률:    {stats['win_rate']:.1f}%  ({int(stats['win_rate']*trade_count/100)}승 {trade_count - int(stats['win_rate']*trade_count/100)}패)")
    print(f"  평균 손익:  {stats['avg_pnl']:+.3f}%")
    print(f"  평균 수익:  +{stats['avg_win']:.3f}%   평균 손실: {stats['avg_loss']:.3f}%")
    print(f"  손익비(RR): {stats['rr']:.2f}")
    print()
    print(f"  ─────────────  RS 전략 핵심 지표  ──────────────────")
    hold_lt5, hold_5_30, hold_gt30 = stats["hold_dist"]
    print(f"  평균 홀딩:  {stats['avg_hold']:.1f}분")
    print(f"  홀딩 분포:  <5m={hold_lt5}건  5~30m={hold_5_30}건  >30m={hold_gt30}건")
    print(f"  평균 MFE:  +{stats['avg_mfe']:.3f}%  (최대 유리 움직임)")
    print(f"  평균 MAE:   {stats['avg_mae']:.3f}%  (최대 불리 움직임)")
    print(f"  Trail 효율: {stats['trail_efficiency']*100:.1f}%  (MFE 대비 수익 보존)")
    print(f"  RS 약세 이탈: {stats['rs_weak_ratio']:.1f}%  (RS < 1.1 빠른 소멸)")
    print()
    print(f"  ─────────────  청산 유형  ────────────────────────")
    for etype, cnt in sorted(stats["exit_dist"].items(), key=lambda x: -x[1]):
        bar = "█" * int(cnt / trade_count * 20)
        print(f"  {etype:<14} {cnt:3d}건  {bar}")

    # 차단 이유
    _print_no_sig(no_sig_reasons, sig_count)

    # 권고사항
    print()
    print(f"  ─────────────  권고사항  ────────────────────────")
    recs = _generate_recommendations(stats, hold_lt5, hold_gt30, trade_count)
    if recs:
        for r in recs:
            print(f"  {r}")
    else:
        print(f"  ✅ 전략 양호 (추가 데이터 수집 후 재평가 권장)")

    print(f"{sep}\n")


def _print_no_sig(no_sig_reasons: dict, sig_count: int):
    if not no_sig_reasons:
        return
    total_no = sum(no_sig_reasons.values())
    print()
    print(f"  ─────────────  차단 이유 (RS_NO_SIG {total_no}건)  ───────")
    for reason, cnt in sorted(no_sig_reasons.items(), key=lambda x: -x[1])[:5]:
        bar = "▪" * min(cnt, 20)
        print(f"  {reason[:40]:<40} {cnt:3d}건  {bar}")


def _generate_recommendations(stats: dict, hold_lt5: int, hold_gt30: int, n: int) -> list:
    recs = []

    if stats["win_rate"] < 45:
        recs.append(f"⚠️  승률 {stats['win_rate']:.1f}% < 45% → rs_threshold 1.3→1.35 또는 VWAP 강화")

    if stats["rr"] < 1.0:
        recs.append(f"⚠️  RR {stats['rr']:.2f} < 1.0 → trailing_stop_wide_pct 3.5→4.0 검토")

    if hold_lt5 / n > 0.4:
        recs.append(f"⚠️  진입 직후 조기 청산 {hold_lt5}건({hold_lt5/n*100:.0f}%) → 진입 타이밍 문제 (VWAP breakout 강화)")

    if stats["avg_hold"] > 45:
        recs.append(f"ℹ️  평균 홀딩 {stats['avg_hold']:.1f}분 → 추세 잘 먹는 중")

    if hold_gt30 / n >= 0.3:
        recs.append(f"✅ 30분+ 홀딩 {hold_gt30}건({hold_gt30/n*100:.0f}%) → trailing_stop_wide_pct 완화 검토")

    if stats["trail_efficiency"] < 0.5 and stats["exit_dist"].get("TRAIL", 0) > 0:
        recs.append(f"⚠️  Trail 효율 {stats['trail_efficiency']*100:.1f}% < 50% → trailing이 너무 타이트 (-2% → -1.5% 검토)")

    if stats["rs_weak_ratio"] > 30:
        recs.append(f"⚠️  RS 약세 이탈 {stats['rs_weak_ratio']:.1f}% → rs_threshold 상향 또는 rs_deteriorate 0.9↑")

    if stats["avg_mfe"] > 3.0 and stats["avg_win"] < stats["avg_mfe"] * 0.6:
        recs.append(f"⚠️  MFE {stats['avg_mfe']:.2f}% 대비 수익 {stats['avg_win']:.2f}% → trailing 완화 (wide: 3.5→4.5)")

    if stats["win_rate"] >= 55 and stats["rr"] >= 1.2 and stats["avg_hold"] >= 15:
        recs.append(f"✅ 전략 우수 → position_size_mult 0.15→0.25, max_per_day 1→2 확대 고려")

    return recs


def collect_log_paths(args) -> list:
    if args.date:
        return [LOG_DIR / f"auto_trading_{args.date}.log"]
    if args.days:
        today = datetime.today()
        return [LOG_DIR / f"auto_trading_{(today - timedelta(days=i)).strftime('%Y%m%d')}.log"
                for i in range(args.days)]
    return [LOG_DIR / f"auto_trading_{datetime.today().strftime('%Y%m%d')}.log"]


def main():
    parser = argparse.ArgumentParser(description="RS 전략 성능 분석기")
    parser.add_argument("--date",       type=str, help="특정 날짜 (YYYYMMDD)")
    parser.add_argument("--days",       type=int, help="최근 N일 합산")
    parser.add_argument("--min-trades", type=int, default=1, help="최소 거래 기준")
    args = parser.parse_args()

    log_paths = collect_log_paths(args)

    all_trades, total_sigs, all_no_sigs, parsed_dates = [], 0, {}, []

    for lp in log_paths:
        r = parse_log_file(lp)
        if r["trades"] or r["sig_count"] > 0 or r["no_sig_reasons"]:
            all_trades.extend(r["trades"])
            total_sigs += r["sig_count"]
            for k, v in r["no_sig_reasons"].items():
                all_no_sigs[k] = all_no_sigs.get(k, 0) + v
            parsed_dates.append(r["date"])

    period = (
        parsed_dates[0] if len(parsed_dates) == 1
        else f"{parsed_dates[-1]}~{parsed_dates[0]}" if parsed_dates
        else "기록 없음"
    )

    if len(all_trades) < args.min_trades:
        print(f"\n⚠️  RS 체결 {len(all_trades)}건 (기준 {args.min_trades}건 미달)")
        print(f"   신호 시도: {total_sigs}건")
        if all_no_sigs:
            print(f"   주요 차단: {sorted(all_no_sigs.items(), key=lambda x:-x[1])[:3]}")
        return

    stats = compute_stats(all_trades)
    print_report(stats, len(all_trades), total_sigs, all_no_sigs, period)


if __name__ == "__main__":
    main()
