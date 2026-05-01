"""
당일 실거래 4축 분석 리포트 (즉시 실행용)

Usage:
    python -m analysis.daily_trade_report          # 오늘
    python -m analysis.daily_trade_report --date 20260418

출력 4축:
  Axis 1. TP1 이후 흐름  — BE vs TP2 vs TRAILING
  Axis 2. Hard Stop 검증 — 유예 후 추가 하락 vs 반등
  Axis 3. A급 보호 효과  — 완화 적용 후 결과
  Axis 4. 진입 등급별 성과 — grade × exit 교차표

튜닝 권고:
  BE_STOP > 60%  → be_stop_buffer_pct ↑
  TP2 rate < 20% → trailing 느슨하게
  Hard Stop 유예 후 반등 많음 → candle_confirm 유지
"""

import argparse
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

LOG_DIR = Path(__file__).parent.parent / "logs"

# ── 로그 파싱 패턴 ─────────────────────────────────────────────────────────
_RE_TRADE = re.compile(
    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*\[TRADE_RESULT\] "
    r"(\w+) \| pnl=([+-]?\d+\.\d+)% \| reason_tag=(\S+) \| "
    r"eq=(\S+) \| choch=(\S+) \| hold=(\d+)m \| r_pct=(\S+)"
)
_RE_BUFFER_APPLIED  = re.compile(r"\[A_STOP_BUFFER\] (\w+)급 conf=[\d.]+≥[\d.]+ TP1 전 손절 완화")
_RE_BUFFER_SKIPPED  = re.compile(r"\[A_STOP_BUFFER_SKIP\] (\w+)급이지만 conf=([\d.]+)<")
_RE_EMG_DEFERRED    = re.compile(r"긴급손절 유예.*스파이크 의심")
_RE_EMG_FIRED       = re.compile(r"\[HARD_STOP_EMERGENCY\].*candle confirmed")


def _safe_float(s: str) -> float:
    try:
        return float(s)
    except Exception:
        return 0.0


def load_log(date_str: str) -> List[str]:
    path = LOG_DIR / f"auto_trading_{date_str}.log"
    if not path.exists():
        return []
    return path.read_text(encoding="utf-8", errors="ignore").splitlines()


def parse_trades(lines: List[str]) -> List[Dict]:
    trades = []
    for ln in lines:
        m = _RE_TRADE.search(ln)
        if not m:
            continue
        ts, code, pnl, tag, eq, choch, hold, r_pct = m.groups()
        trades.append({
            "ts": ts, "code": code,
            "pnl": _safe_float(pnl),
            "tag": tag,
            "eq": eq,
            "choch": choch,
            "hold_min": int(hold),
            "r_pct": _safe_float(r_pct),
        })
    return trades


def axis1_tp1_flow(trades: List[Dict]) -> None:
    """Axis 1: TP1 이후 흐름 (TP1 도달한 포지션이 이후 어떻게 됐나)"""
    print("\n" + "=" * 60)
    print("Axis 1. TP1 이후 흐름")
    print("=" * 60)

    # 같은 code에서 R_TP1 이후 다음 거래 찾기
    by_code: Dict[str, List[Dict]] = defaultdict(list)
    for t in trades:
        by_code[t["code"]].append(t)

    tp1_flows: List[Dict] = []
    for code, ts_list in by_code.items():
        ts_list.sort(key=lambda x: x["ts"])
        for i, t in enumerate(ts_list):
            if "R_TP1" in t["tag"] and i + 1 < len(ts_list):
                follow = ts_list[i + 1]
                tp1_flows.append({
                    "code": code,
                    "tp1_pnl": t["pnl"],
                    "follow_tag": follow["tag"],
                    "follow_pnl": follow["pnl"],
                    "eq": t["eq"],
                    "choch": t["choch"],
                })

    if not tp1_flows:
        print("  TP1 도달 후 속행 데이터 없음 (데이터 부족 또는 당일 TP1 미도달)")
        return

    tag_count: Dict[str, int] = defaultdict(int)
    tag_pnl:   Dict[str, List[float]] = defaultdict(list)
    for f in tp1_flows:
        tag_count[f["follow_tag"]] += 1
        tag_pnl[f["follow_tag"]].append(f["follow_pnl"])

    total = len(tp1_flows)
    print(f"  TP1 도달 후 속행 건수: {total}")
    print()
    rows = []
    for tag, cnt in sorted(tag_count.items(), key=lambda x: -x[1]):
        avg = sum(tag_pnl[tag]) / len(tag_pnl[tag])
        pct = cnt / total * 100
        rows.append((tag, cnt, pct, avg))

    print(f"  {'청산 태그':<22} {'건수':>5} {'비율':>7} {'평균PnL':>9}")
    print(f"  {'-'*22} {'-'*5} {'-'*7} {'-'*9}")
    for tag, cnt, pct, avg in rows:
        flag = ""
        if "BE_STOP" in tag and pct > 60:
            flag = " ← ⚠️ buffer ↑ 고려"
        elif "R_TP2" in tag and pct >= 30:
            flag = " ← ✅ 최적"
        print(f"  {tag:<22} {cnt:>5} {pct:>6.1f}% {avg:>+8.2f}%{flag}")

    be_pct = sum(1 for f in tp1_flows if "BE_STOP" in f["follow_tag"]) / total * 100
    tp2_pct = sum(1 for f in tp1_flows if "R_TP2" in f["follow_tag"]) / total * 100
    print()
    print("  [튜닝 권고]")
    if be_pct > 60:
        print(f"  ⚠️  BE_STOP {be_pct:.0f}% > 60% → be_stop_buffer_pct ↑ (0.2 → 0.3)")
    elif be_pct < 20:
        print(f"  ✅ BE_STOP {be_pct:.0f}% < 20% → buffer 적정")
    if tp2_pct < 20:
        print(f"  ⚠️  TP2 도달률 {tp2_pct:.0f}% < 20% → trailing 너무 타이트 or 추세 약함")
    elif tp2_pct >= 30:
        print(f"  ✅ TP2 도달률 {tp2_pct:.0f}% ≥ 30% → 트레일링 구조 정상")


def axis2_hard_stop(lines: List[str], trades: List[Dict]) -> None:
    """Axis 2: Hard Stop 검증 (유예 vs 즉시 발동)"""
    print("\n" + "=" * 60)
    print("Axis 2. Hard Stop 검증")
    print("=" * 60)

    deferred = sum(1 for ln in lines if _RE_EMG_DEFERRED.search(ln))
    fired    = sum(1 for ln in lines if _RE_EMG_FIRED.search(ln))
    hs_trades = [t for t in trades if "HARD_STOP" in t["tag"]]

    print(f"  긴급손절 유예 횟수: {deferred}")
    print(f"  긴급손절 즉시발동: {fired}")
    print(f"  HARD_STOP 최종 청산: {len(hs_trades)}건")

    if hs_trades:
        avg_loss = sum(t["pnl"] for t in hs_trades) / len(hs_trades)
        print(f"  Hard Stop 평균 손실: {avg_loss:+.2f}%")

    print()
    print("  [튜닝 권고]")
    if deferred > 0 and fired == 0:
        print("  ⚠️  유예만 발생, 즉시발동 없음 → 유예 후 결과 직접 확인 필요")
        print("      반등 많았으면 → candle_confirm 유지")
        print("      추가 하락 많았으면 → emergency_stop_candle_confirm: false")
    elif fired > 0:
        print(f"  ✅ 즉시발동 {fired}건 → 하향 이탈 확인 후 컷 작동 중")


def axis3_a_grade_buffer(lines: List[str], trades: List[Dict]) -> None:
    """Axis 3: A급 보호 효과"""
    print("\n" + "=" * 60)
    print("Axis 3. A급 손절 완화 효과")
    print("=" * 60)

    applied = sum(1 for ln in lines if _RE_BUFFER_APPLIED.search(ln))
    skipped = sum(1 for ln in lines if _RE_BUFFER_SKIPPED.search(ln))

    print(f"  완화 적용: {applied}건  |  confidence 미달 스킵: {skipped}건")

    # A급 trades 결과 분포
    a_trades = [t for t in trades if t["choch"] in ("A", "A+") or t["eq"] == "A"]
    if not a_trades:
        print("  A급 거래 없음")
        return

    winners = [t for t in a_trades if t["pnl"] > 0]
    losers  = [t for t in a_trades if t["pnl"] <= 0]
    stops   = [t for t in a_trades if "STOP" in t["tag"]]
    wr = len(winners) / len(a_trades) * 100 if a_trades else 0

    print(f"  A급 거래: {len(a_trades)}건 | 승률: {wr:.0f}% | 손절: {len(stops)}건")

    if a_trades:
        avg_pnl = sum(t["pnl"] for t in a_trades) / len(a_trades)
        print(f"  A급 평균 PnL: {avg_pnl:+.2f}%")

    print()
    print("  [튜닝 권고]")
    if stops and len(stops) / len(a_trades) > 0.5:
        print("  ⚠️  A급 손절 비율 > 50% → a_grade_stop_buffer_pct ↑ 또는 진입 기준 강화")
    elif wr < 40:
        print(f"  ⚠️  A급 승률 {wr:.0f}% < 40% → 진입 confidence 기준 재검토")
    else:
        print(f"  ✅ A급 승률 {wr:.0f}% — 정상 범위")


def axis4_grade_cross(trades: List[Dict]) -> None:
    """Axis 4: 진입 등급(eq × choch) × 청산 태그 교차표"""
    print("\n" + "=" * 60)
    print("Axis 4. 진입 등급 × 청산 결과 교차표")
    print("=" * 60)

    if not trades:
        print("  거래 없음")
        return

    # grade key = "eq=A choch=A+" 형식
    grade_tag: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
    grade_pnl: Dict[str, List[float]] = defaultdict(list)

    for t in trades:
        key = f"eq={t['eq']} choch={t['choch']}"
        # exit 태그 단순화
        raw_tag = t["tag"]
        if "R_TP2" in raw_tag:
            bucket = "TP2"
        elif "R_TP1" in raw_tag:
            bucket = "TP1(partial)"
        elif "BE_STOP" in raw_tag:
            bucket = "BE_STOP"
        elif "TRAILING" in raw_tag:
            bucket = "TRAILING"
        elif "PROFIT_LOCK" in raw_tag:
            bucket = "PROFIT_LOCK"
        elif "STRUCTURE" in raw_tag:
            bucket = "STRUCT_STOP"
        elif "HARD_STOP" in raw_tag or "EMERGENCY" in raw_tag:
            bucket = "HARD_STOP"
        elif "NO_PROGRESS" in raw_tag:
            bucket = "NO_PROGRESS"
        elif "A_FORCE" in raw_tag:
            bucket = "A_FORCE"
        else:
            bucket = "기타"
        grade_tag[key][bucket] += 1
        grade_pnl[key].append(t["pnl"])

    buckets = ["TP2", "TP1(partial)", "BE_STOP", "TRAILING", "PROFIT_LOCK",
               "STRUCT_STOP", "HARD_STOP", "NO_PROGRESS", "기타"]
    # 헤더
    hdr = f"  {'등급':<20} {'n':>3} {'WR%':>5} {'avgR':>7}"
    for b in buckets:
        hdr += f" {b[:8]:>9}"
    print(hdr)
    print("  " + "-" * (20 + 3 + 5 + 7 + 9 * len(buckets) + 4))

    # 등급별 행
    for key in sorted(grade_tag.keys()):
        pnls = grade_pnl[key]
        total = len(pnls)
        wr    = sum(1 for p in pnls if p > 0) / total * 100 if total else 0
        avg   = sum(pnls) / total if total else 0
        row = f"  {key:<20} {total:>3} {wr:>4.0f}% {avg:>+6.1f}%"
        for b in buckets:
            cnt = grade_tag[key].get(b, 0)
            row += f" {cnt:>9}" if cnt > 0 else f" {'·':>9}"
        print(row)

    # 전체 합계
    all_pnl = [t["pnl"] for t in trades]
    total = len(all_pnl)
    wr    = sum(1 for p in all_pnl if p > 0) / total * 100 if total else 0
    avg   = sum(all_pnl) / total if total else 0
    print("  " + "-" * (20 + 3 + 5 + 7 + 9 * len(buckets) + 4))
    row = f"  {'[전체]':<20} {total:>3} {wr:>4.0f}% {avg:>+6.1f}%"
    for b in buckets:
        cnt = sum(grade_tag[k].get(b, 0) for k in grade_tag)
        row += f" {cnt:>9}" if cnt > 0 else f" {'·':>9}"
    print(row)

    print()
    print("  [튜닝 권고]")
    # eq=C 기대값 음수이면 진입 차단 권장
    c_pnl = [t["pnl"] for t in trades if t["eq"] == "C"]
    if c_pnl and sum(c_pnl) / len(c_pnl) < 0:
        print(f"  ⚠️  eq=C 평균 PnL {sum(c_pnl)/len(c_pnl):+.2f}% — 진입 차단 검토")
    # B급 손절 과다
    b_stop = [t for t in trades if t["eq"] == "B" and "STOP" in t["tag"]]
    b_all  = [t for t in trades if t["eq"] == "B"]
    if b_all and len(b_stop) / len(b_all) > 0.6:
        print(f"  ⚠️  eq=B 손절률 {len(b_stop)/len(b_all)*100:.0f}% > 60% — B급 진입 기준 강화 검토")


def tuning_summary(trades: List[Dict]) -> None:
    """최종 튜닝 우선순위 요약"""
    print("\n" + "=" * 60)
    print("최종 튜닝 우선순위 요약")
    print("=" * 60)

    if not trades:
        print("  거래 없음 — 내일 다시 확인")
        return

    total = len(trades)
    wins  = sum(1 for t in trades if t["pnl"] > 0)
    wr    = wins / total * 100
    avg_r = sum(t["pnl"] for t in trades) / total

    be_cnt  = sum(1 for t in trades if "BE_STOP" in t["tag"])
    tp1_cnt = sum(1 for t in trades if "R_TP1" in t["tag"])
    tp2_cnt = sum(1 for t in trades if "R_TP2" in t["tag"])
    hs_cnt  = sum(1 for t in trades if "HARD_STOP" in t["tag"])

    print(f"  거래: {total}건 | 승률: {wr:.0f}% | 평균PnL: {avg_r:+.2f}%")
    print(f"  TP1: {tp1_cnt}건 | TP2: {tp2_cnt}건 | BE: {be_cnt}건 | HardStop: {hs_cnt}건")
    print()

    # 우선순위 판단
    items = []
    if tp1_cnt > 0:
        be_ratio = be_cnt / tp1_cnt * 100
        if be_ratio > 60:
            items.append(("1순위", "be_stop_buffer_pct", f"↑ 0.2→0.3 (BE율 {be_ratio:.0f}%)"))
        elif be_ratio < 20:
            items.append(("참고", "be_stop_buffer_pct", f"적정 (BE율 {be_ratio:.0f}%)"))

    if tp2_cnt < max(tp1_cnt * 0.2, 1) and tp1_cnt > 0:
        items.append(("2순위", "trailing_tiers.base_mult", "↑ 3.0→3.5 (TP2 도달 부족)"))

    if hs_cnt > 0 and avg_r < -1.0:
        items.append(("3순위", "a_grade_stop_buffer_pct", "재검토 (평균 손실 높음)"))

    if not items:
        items.append(("✅", "현재 파라미터", "정상 범위 — 유지"))

    for rank, param, action in items:
        print(f"  {rank:<6} {param:<35} {action}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default=datetime.now().strftime("%Y%m%d"))
    args = parser.parse_args()

    print(f"\n{'='*60}")
    print(f"  실거래 4축 분석 리포트  [{args.date}]")
    print(f"{'='*60}")

    lines  = load_log(args.date)
    trades = parse_trades(lines)

    print(f"\n  로그 라인: {len(lines):,}  |  TRADE_RESULT: {len(trades)}건")

    if not trades:
        print("\n  ⚠️  TRADE_RESULT 로그 없음 — 거래가 발생한 날에 실행하세요.")
        print(f"\n  로그 위치: {LOG_DIR / f'auto_trading_{args.date}.log'}")
        return

    axis1_tp1_flow(trades)
    axis2_hard_stop(lines, trades)
    axis3_a_grade_buffer(lines, trades)
    axis4_grade_cross(trades)
    tuning_summary(trades)

    print("\n" + "=" * 60)
    print("  완료. 튜닝 반영 후 config/strategy_hybrid.yaml 수정.")
    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
