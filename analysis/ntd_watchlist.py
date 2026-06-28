"""
analysis/ntd_watchlist.py — Active Survivorship Queue (Phase 2 관측 레이어)

ntd_alpha_tracker(Event Recorder) 결과물로부터
종목별 시간축 생존 상태를 관리하는 State Manager.

핵심 개념:
  ntd_alpha_tracker = Event Recorder  (NTD 중 ACCEPT 이벤트 기록)
  ntd_watchlist     = State Manager   (종목 생존 상태 시간축 추적)

스키마 (종목당 1레코드):
  code               종목 코드
  archetype          현재 archetype (TYPE_AB/A/B/C)
  transition         archetype 변화 요약 (e.g. "TYPE_C→TYPE_B")
  first_seen         NTD ACCEPT 최초 등장일
  last_seen          NTD ACCEPT 최근 등장일
  appearances        등장 횟수
  consecutive_days   연속 NTD 등장 일수 (last_seen 기준)
  days_since_last    last_seen 이후 경과 거래일 수
  freshness_score    max(0, 1 - days_since_last / ttl)  0=만료
  status             active | expired
  history            [{date, archetype, alpha, day_return, recovery_strength, leader_score}]

출력: logs/ntd_watchlist_YYYYMMDD.json
사용: python3 -m analysis.ntd_watchlist [--ttl 5] [--date YYYYMMDD] [--no-save]
"""
import argparse
import json
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

LOG_DIR    = Path("logs")
DEFAULT_TTL = 5  # 거래일 (YAML 연동 전 단계 — shadow 기간 실험값)

ARCHETYPE_RANK = {"TYPE_AB": 0, "TYPE_A": 1, "TYPE_B": 2, "TYPE_C": 3}


# ── 날짜 유틸 ──────────────────────────────────────────────────────────────────

def _trading_days(start: date, end: date) -> list[date]:
    days, d = [], start
    while d <= end:
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return days


def _trading_days_elapsed(from_date: date, to_date: date) -> int:
    """from_date 다음 거래일부터 to_date까지 거래일 수.
    (from_date 당일은 미포함: '등장 이후 지나간 일수' 의미)
    """
    if to_date <= from_date:
        return 0
    return len(_trading_days(from_date + timedelta(days=1), to_date))


# ── 데이터 로드 ───────────────────────────────────────────────────────────────

def _find_latest_ntd_alpha(target_date: date) -> Path | None:
    """target_date 이하 가장 최신 ntd_alpha_YYYYMMDD.json 반환."""
    for p in sorted(LOG_DIR.glob("ntd_alpha_*.json"), reverse=True):
        try:
            file_date = datetime.strptime(p.stem.rsplit("_", 1)[-1], "%Y%m%d").date()
            if file_date <= target_date:
                return p
        except ValueError:
            continue
    return None


def _load_records(target_date: date) -> tuple[list[dict], str]:
    """최신 ntd_alpha JSON 로드. 유효 레코드 + 파일명 반환."""
    p = _find_latest_ntd_alpha(target_date)
    if not p:
        return [], ""
    try:
        records = json.loads(p.read_text(encoding="utf-8"))
        valid = [r for r in records
                 if not r.get("price_unavailable") and r.get("date") and r.get("code")]
        return valid, p.name
    except Exception as e:
        print(f"[WARN] ntd_alpha 로드 실패: {e}")
        return [], p.name if p else ""


# ── Watchlist 구축 ─────────────────────────────────────────────────────────────

def _build_watchlist(records: list[dict], target_date: date, ttl: int) -> list[dict]:
    """레코드 목록 → Active Survivorship Queue."""
    if ttl <= 0:
        ttl = 1  # ZeroDivisionError 방지

    # 종목별 레코드 그룹화 → 날짜순 정렬
    by_code: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_code[r["code"]].append(r)
    for code in by_code:
        by_code[code].sort(key=lambda x: x["date"])

    watchlist = []
    for code, recs in by_code.items():
        first = recs[0]
        last  = recs[-1]

        first_date = date.fromisoformat(first["date"])
        last_date  = date.fromisoformat(last["date"])

        days_elapsed  = _trading_days_elapsed(last_date, target_date)
        freshness     = round(max(0.0, 1.0 - days_elapsed / ttl), 3)
        status        = "active" if freshness > 0 else "expired"

        # archetype_history
        history = [
            {
                "date":              r["date"],
                "archetype":         r.get("archetype", "TYPE_C"),
                "alpha":             r.get("alpha"),
                "day_return":        r.get("day_return"),
                "recovery_strength": r.get("recovery_strength"),
                "consecutive_days":  r.get("consecutive_days", 1),
                "leader_score":      r.get("leader_score"),
            }
            for r in recs
        ]

        # archetype transition 요약 (e.g. "TYPE_C→TYPE_B")
        arch_seq = [h["archetype"] for h in history]
        if len(set(arch_seq)) > 1:
            # 중복 제거하며 순서 유지
            seen, deduped = set(), []
            for a in arch_seq:
                if a not in seen:
                    seen.add(a); deduped.append(a)
            transition = "→".join(deduped)
        else:
            transition = arch_seq[-1]

        watchlist.append({
            "code":              code,
            "archetype":         last.get("archetype", "TYPE_C"),
            "transition":        transition,
            "first_seen":        str(first_date),
            "last_seen":         str(last_date),
            "appearances":       len(recs),
            "consecutive_days":  last.get("consecutive_days", 1),
            "days_since_last":   days_elapsed,
            "freshness_score":   freshness,
            "status":            status,
            "last_alpha":        last.get("alpha"),
            "last_day_return":   last.get("day_return"),
            "last_recovery":     last.get("recovery_strength"),
            "last_leader_score": last.get("leader_score"),
            "history":           history,
        })

    # 정렬: active 우선 → archetype 순위 → freshness 높은 순
    watchlist.sort(key=lambda x: (
        0 if x["status"] == "active" else 1,
        ARCHETYPE_RANK.get(x["archetype"], 9),
        -x["freshness_score"],
    ))
    return watchlist


# ── 메인 ──────────────────────────────────────────────────────────────────────

def run(ttl: int = DEFAULT_TTL, target_date: date | None = None) -> dict:
    today   = target_date or date.today()
    records, source_file = _load_records(today)

    empty_result = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date":  str(today),
        "ttl_days":     ttl,
        "source_file":  source_file or "없음",
        "watchlist":    [],
        "summary":      {"active": 0, "expired": 0, "total": 0,
                         "by_archetype": {k: 0 for k in ARCHETYPE_RANK}},
    }

    if not records:
        return empty_result

    watchlist = _build_watchlist(records, today, ttl)
    active    = [w for w in watchlist if w["status"] == "active"]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "target_date":  str(today),
        "ttl_days":     ttl,
        "source_file":  source_file,
        "watchlist":    watchlist,
        "summary": {
            "active":       len(active),
            "expired":      len(watchlist) - len(active),
            "total":        len(watchlist),
            "by_archetype": {
                at: sum(1 for w in active if w["archetype"] == at)
                for at in ARCHETYPE_RANK
            },
        },
    }


# ── 출력 ──────────────────────────────────────────────────────────────────────

def print_summary(result: dict) -> None:
    wl  = result["watchlist"]
    sm  = result["summary"]

    print(f"\n{'='*68}")
    print(f"NTD Active Survivorship Watchlist  {result['target_date']}  "
          f"TTL={result['ttl_days']}거래일")
    print(f"  source: {result['source_file']}")
    print(f"  Active={sm['active']}  Expired={sm['expired']}  "
          f"by_arch={sm['by_archetype']}")
    print(f"{'='*68}")

    active  = [w for w in wl if w["status"] == "active"]
    expired = [w for w in wl if w["status"] == "expired"]

    if not active:
        print("\n  [활성 후보 없음 — 모두 TTL 초과 또는 데이터 없음]")
    else:
        print(f"\n[Active Survivorship Queue]")
        print(f"  {'종목':8s}  {'archetype':>9s}  {'transition':>18s}  "
              f"{'first':>10s}  {'last':>10s}  {'cons':>4s}  "
              f"{'elapsed':>7s}  {'fresh':>5s}  {'ldr_sc':>7s}")
        print(f"  {'-'*90}")
        for w in active:
            print(
                f"  {w['code']:8s}  {w['archetype']:>9s}  "
                f"{w['transition']:>18s}  "
                f"{w['first_seen']:>10s}  {w['last_seen']:>10s}  "
                f"{w['consecutive_days']:>4d}  "
                f"{w['days_since_last']:>7d}  "
                f"{w['freshness_score']:>5.2f}  "
                f"{w['last_leader_score'] or 0:>7.3f}"
            )

    # Archetype 진화 하이라이트
    evolved = [w for w in active if "→" in w.get("transition", "")]
    if evolved:
        print(f"\n[진화 중인 후보 (Archetype Transition)]")
        for w in evolved:
            hist_str = "  ".join(
                f"{h['date'][5:]} {h['archetype']} "
                f"(a={h['alpha']:+.2f} d={h['day_return'] or 0:+.1f}% "
                f"r={h['recovery_strength'] or 0:.2f})"
                for h in w["history"]
            )
            print(f"  {w['code']:8s}  {w['transition']}  cons={w['consecutive_days']}")
            print(f"             {hist_str}")

    if expired:
        print(f"\n[Expired (TTL={result['ttl_days']}일 초과)]")
        for w in expired:
            print(f"  {w['code']:8s}  {w['archetype']:>9s}  "
                  f"last={w['last_seen']}  elapsed={w['days_since_last']}d  fresh=0.00")

    print(f"\n{'='*68}\n")


def save_json(result: dict, target_date: date | None = None) -> Path:
    d   = target_date or date.today()
    out = LOG_DIR / f"ntd_watchlist_{d.strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NTD Active Survivorship Watchlist")
    parser.add_argument("--ttl",     type=int, default=DEFAULT_TTL,
                        help=f"Freshness TTL 거래일 수 (기본 {DEFAULT_TTL})")
    parser.add_argument("--date",    type=str, default=None, help="기준일 YYYYMMDD")
    parser.add_argument("--no-save", action="store_true",    help="JSON 저장 생략")
    args = parser.parse_args()

    target = date.today()
    if args.date:
        target = datetime.strptime(args.date, "%Y%m%d").date()

    result = run(ttl=args.ttl, target_date=target)
    print_summary(result)

    if not args.no_save:
        path = save_json(result, target)
        print(f"저장: {path}")
