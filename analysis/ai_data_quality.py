"""
analysis/ai_data_quality.py — AI-ready DB Quality Checker

DB가 머신러닝 학습에 사용 가능한 수준인지 자동 판별.

4가지 관점:
  L1. Time Integrity     — timestamp 순서·갭·중복
  L2. Schema Consistency — strategy값·price·trade_type 일관성
  L3. Label Quality      — is_loss/exit_reason/BUY-SELL 매칭 신뢰도
  L4. Feature Coverage   — ML에 필요한 파생피처 도출 가능 여부

AI Readiness Score: 0~100
  90+  : 학습 바로 가능
  70~89: 보정 후 가능
  50~69: 데이터 정리 필요
  <50  : 학습 시 모델 망가질 위험

사용법:
    python3 -m analysis.ai_data_quality [--days N] [--db PATH] [--verbose]
"""

from __future__ import annotations

import argparse
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

_ROOT    = Path(__file__).parent.parent
_DB_PATH = _ROOT / "data" / "trades.db"

_VALID_STRATEGIES   = {"SMC", "EXPLORATION", "TREND", "EXIT", "MOMENTUM", "SWEEP", "MANUAL"}
_VALID_TRADE_TYPES  = {"BUY", "SELL"}

# exit reason → ML 카테고리 매핑 (학습용 라벨)
_EXIT_CATEGORY_MAP = [
    (r"Early Failure\[no_demand\]",  "ef_no_demand"),
    (r"Early Failure\[no_follow\]",  "ef_no_follow"),
    (r"Early Failure",               "early_failure"),
    (r"Hard Stop",                   "hard_stop"),
    (r"ATR 트레일링|Trailing",        "trailing_stop"),
    (r"오버나이트|overnight",         "overnight_close"),
    (r"Take Profit|익절",             "take_profit"),
    (r"Time Exit|시간 청산|15:00",     "time_exit"),
    (r"강제청산",                     "forced_close"),
    (r"MFE_EXIT",                    "mfe_exit"),
    (r"HTS_IMPORT",                  "manual_hts"),   # HTS에서 수동 체결된 거래
    (r"손절|stop.?loss",              "stop_loss"),
    (r"보수|conservative",            "conservative_exit"),
]


# ──────────────────────────────────────────────────────────────
# 결과 컨테이너
# ──────────────────────────────────────────────────────────────

@dataclass
class CheckResult:
    name: str
    passed: bool
    score_impact: int      # 점수 차감 (음수)
    detail: dict[str, Any] = field(default_factory=dict)
    note: str = ""


@dataclass
class QualityReport:
    score: float
    row_count: int
    sell_count: int
    date_range: tuple[str, str]
    checks: list[CheckResult]
    derived_features: list[str]
    missing_features: list[str]
    recommendations: list[str]


# ──────────────────────────────────────────────────────────────
# 핵심 클래스
# ──────────────────────────────────────────────────────────────

class AIDataQualityChecker:
    """
    trades.db → AI 학습 가능 여부 자동 판별.
    실제 DB 스키마 (trade_date, timestamp, stock_code, stock_name,
    trade_type, quantity, price, amount, realized_pnl, reason, strategy)
    에 특화됨.
    """

    def __init__(self, db_path: str = None, days: int = 90):
        self.db_path = Path(db_path) if db_path else _DB_PATH
        self.days    = days

    # ──────────────────────────────────────
    # 데이터 로드
    # ──────────────────────────────────────

    def _load(self) -> list[dict]:
        if not self.db_path.exists():
            raise FileNotFoundError(f"DB 없음: {self.db_path}")
        cutoff = (datetime.now() - timedelta(days=self.days)).strftime("%Y-%m-%d")
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM trades WHERE trade_date >= ? ORDER BY timestamp",
            (cutoff,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    # ──────────────────────────────────────
    # L1. Time Integrity
    # ──────────────────────────────────────

    def _check_time_integrity(self, rows: list[dict]) -> CheckResult:
        if not rows:
            return CheckResult("time_integrity", False, -30, note="데이터 없음")

        timestamps = [r["timestamp"] for r in rows]
        parsed = []
        for ts in timestamps:
            try:
                parsed.append(datetime.fromisoformat(ts))
            except Exception:
                parsed.append(None)

        valid = [t for t in parsed if t is not None]
        null_ts = len(parsed) - len(valid)

        # 정렬 여부
        not_sorted_cnt = sum(
            1 for i in range(1, len(valid)) if valid[i] < valid[i - 1]
        )

        # 중복 timestamp (09:00:00 정각은 배치 마이그레이션 아티팩트 → 실 중복에서 제외)
        non_migration = [t for t in valid if not (t.hour == 9 and t.minute == 0 and t.second == 0)]
        dup_cnt = len(non_migration) - len(set(non_migration))

        # 거래일 기준 비정상 갭 (>3 영업일 연속 공백)
        dates = sorted({t.date() for t in valid})
        max_gap_days = 0
        for i in range(1, len(dates)):
            gap = (dates[i] - dates[i - 1]).days
            if gap > max_gap_days:
                max_gap_days = gap

        impact = 0
        issues: dict[str, Any] = {}

        if null_ts:
            impact -= 15
            issues["null_timestamps"] = null_ts

        if not_sorted_cnt:
            impact -= 20
            issues["out_of_order"] = not_sorted_cnt

        if dup_cnt:
            impact -= 10
            issues["duplicate_timestamps"] = dup_cnt

        issues["max_gap_days"]     = max_gap_days
        issues["unique_trade_days"] = len(dates)

        passed = (not_sorted_cnt == 0 and dup_cnt == 0 and null_ts == 0)
        note = "정상" if passed else (
            f"역전 {not_sorted_cnt}건 / 중복 {dup_cnt}건 / NULL {null_ts}건"
        )
        return CheckResult("time_integrity", passed, impact, issues, note)

    # ──────────────────────────────────────
    # L2. Schema Consistency
    # ──────────────────────────────────────

    def _check_schema_consistency(self, rows: list[dict]) -> CheckResult:
        issues: dict[str, Any] = {}
        impact = 0

        # trade_type 이상값
        bad_types = [r["trade_type"] for r in rows if r["trade_type"] not in _VALID_TRADE_TYPES]
        if bad_types:
            impact -= 20
            issues["invalid_trade_type"] = list(set(bad_types))

        # price 0 또는 NULL
        bad_price = [r["stock_code"] for r in rows if not r.get("price") or r["price"] <= 0]
        if bad_price:
            impact -= 15
            issues["zero_or_null_price"] = bad_price

        # strategy 이상값 ('kiwoom', NULL, UNKNOWN)
        bad_strategy = {}
        for r in rows:
            s = r.get("strategy") or "NULL"
            if s not in _VALID_STRATEGIES:
                bad_strategy[s] = bad_strategy.get(s, 0) + 1
        if bad_strategy:
            impact -= 10
            issues["invalid_strategy_values"] = bad_strategy
            issues["strategy_fix"] = (
                "strategy='kiwoom' 등 비표준값 → migrate_from_risk_log() 재실행 또는 "
                "_extract_strategy() 패턴 확장 필요"
            )

        # realized_pnl: SELL인데 항상 0이면 누락 의심
        sell_pnl = [r["realized_pnl"] for r in rows if r["trade_type"] == "SELL"]
        if sell_pnl and all(p == 0 for p in sell_pnl):
            impact -= 20
            issues["sell_pnl_all_zero"] = True
            issues["sell_pnl_note"] = "모든 SELL의 realized_pnl=0 → execute_sell is_loss 버그 의심"

        # strategy 분포
        dist: dict[str, int] = {}
        for r in rows:
            s = r.get("strategy") or "NULL"
            dist[s] = dist.get(s, 0) + 1
        issues["strategy_distribution"] = dist
        total = len(rows)
        if total > 0:
            dominant = max(dist.values()) / total
            if dominant > 0.8:
                impact -= 10
                issues["strategy_imbalance"] = f"단일 전략 {dominant:.0%} 과점 — 편향 학습 위험"

        passed = impact == 0
        note = "정상" if passed else f"전략이상:{len(bad_strategy)}종 / 가격이상:{len(bad_price)}건"
        return CheckResult("schema_consistency", passed, impact, issues, note)

    # ──────────────────────────────────────
    # L3. Label Quality
    # ──────────────────────────────────────

    def _check_label_quality(self, rows: list[dict]) -> CheckResult:
        issues: dict[str, Any] = {}
        impact = 0

        sells = [r for r in rows if r["trade_type"] == "SELL"]
        buys  = [r for r in rows if r["trade_type"] == "BUY"]

        if not sells:
            return CheckResult("label_quality", False, -30, note="SELL 데이터 없음")

        # is_loss 도출 가능 여부 (realized_pnl 기반)
        pnl_nulls = sum(1 for r in sells if r.get("realized_pnl") is None)
        if pnl_nulls:
            impact -= 15
            issues["realized_pnl_null"] = pnl_nulls

        # 실제 라벨 분포
        wins   = sum(1 for r in sells if (r.get("realized_pnl") or 0) > 0)
        losses = sum(1 for r in sells if (r.get("realized_pnl") or 0) < 0)
        draws  = len(sells) - wins - losses
        win_rate = wins / len(sells) * 100 if sells else 0
        issues["label_distribution"] = {
            "win": wins, "loss": losses, "draw(pnl=0)": draws,
            "win_rate_pct": round(win_rate, 1)
        }

        # draw(pnl=0) 과다 → is_loss 버그 흔적
        if draws / len(sells) > 0.3:
            impact -= 20
            issues["excessive_draws"] = (
                f"SELL {len(sells)}건 중 PnL=0이 {draws}건({draws/len(sells):.0%}) "
                "→ is_loss UnboundLocalError 버그 흔적 (B1 버그)"
            )

        # exit reason → ML 카테고리 파싱 가능 여부
        categorized = 0
        category_dist: dict[str, int] = {}
        for r in sells:
            reason = r.get("reason") or ""
            cat = _categorize_exit(reason)
            category_dist[cat] = category_dist.get(cat, 0) + 1
            if cat != "unknown":
                categorized += 1

        issues["exit_category_distribution"] = category_dist
        parse_rate = categorized / len(sells) * 100
        issues["exit_reason_parse_rate_pct"] = round(parse_rate, 1)
        if parse_rate < 70:
            impact -= 10
            issues["exit_parse_low"] = f"exit reason 파싱 {parse_rate:.0f}% — 패턴 추가 필요"

        # BUY-SELL 날짜별 매칭 (같은 stock_code × trade_date)
        buy_keys  = {(r["stock_code"], r["trade_date"]) for r in buys}
        sell_keys = {(r["stock_code"], r["trade_date"]) for r in sells}
        # SELL인데 당일 BUY 없으면 오버나이트 (정상), 완전 미매칭은 이상
        all_buy_codes  = {r["stock_code"] for r in buys}
        all_sell_codes = {r["stock_code"] for r in sells}
        orphan_sells   = all_sell_codes - all_buy_codes
        if orphan_sells:
            impact -= 5
            issues["orphan_sells"] = list(orphan_sells)
            issues["orphan_note"] = "대응 BUY 없는 SELL — 오버나이트 포지션이거나 마이그레이션 누락"

        passed = impact >= -5  # 경미한 이슈는 허용
        note = (
            f"WR={win_rate:.0f}% / exit파싱={parse_rate:.0f}% / PnL=0:{draws}건"
        )
        return CheckResult("label_quality", passed, impact, issues, note)

    # ──────────────────────────────────────
    # L4. Feature Coverage (ML 피처 도출 가능성)
    # ──────────────────────────────────────

    def _check_feature_coverage(self, rows: list[dict]) -> tuple[CheckResult, list[str], list[str]]:
        sells = [r for r in rows if r["trade_type"] == "SELL"]
        buys  = [r for r in rows if r["trade_type"] == "BUY"]

        # 현재 스키마에서 도출 가능한 피처
        derivable = []
        if sells:
            derivable += ["is_win", "is_loss", "profit_pct", "exit_category"]
        if buys and sells:
            derivable.append("hold_time_min (BUY-SELL 타임스탬프 차이)")
        if any(r.get("reason") for r in sells):
            derivable.append("exit_reason_text (NLP 가능)")
        if any(r.get("strategy") for r in rows):
            derivable.append("entry_strategy (원-핫 인코딩)")
        if any(r.get("amount") for r in rows):
            derivable.append("trade_amount (포지션 크기)")

        # DB 실제 컬럼 확인 (마이그레이션 완료 여부)
        try:
            import sqlite3 as _sq
            _conn = _sq.connect(str(self.db_path))
            _cols = {row[1] for row in _conn.execute("PRAGMA table_info(trades)")}
            _conn.close()
        except Exception:
            _cols = set()

        def _col_status(col: str, label: str) -> str:
            return f"{col} ✓ (저장 중)" if col in _cols else f"{col} — {label}"

        # 현재 스키마에 없는 중요 ML 피처
        missing = [
            m for m in [
                _col_status("mfe_pct",        "진입 후 최고수익률 미저장"),
                _col_status("mae_pct",        "진입 후 최대 손실폭 미저장"),
                _col_status("choch_grade",    "A/B/C 등급 미저장"),
                _col_status("market_regime",  "TREND/NEUTRAL/REVERSAL 미저장"),
                _col_status("rvol_at_entry",  "상대 거래량 미저장"),
                "entry_score (CHoCH 점수 — position dict에 있으나 DB 미저장)",
                "htf_aligned (HTF 정배열 여부 — 미저장)",
            ]
            if "✓" not in m  # 이미 저장 중이면 missing에서 제외
        ]
        # 저장 중인 항목은 derivable로 이동
        for col, label in [
            ("choch_grade",   "진입 등급 (A/A+/B/C)"),
            ("market_regime", "진입 시 레짐"),
            ("rvol_at_entry", "상대거래량"),
            ("mfe_pct",       "최고 수익률 (MFE %)"),
            ("mae_pct",       "최대 손실폭 (MAE %)"),
        ]:
            if col in _cols and label not in " ".join(derivable):
                derivable.append(f"{col} ({label})")

        impact = 0
        issues: dict[str, Any] = {}
        issues["derivable_features"] = derivable
        issues["missing_features"]   = missing

        # 도출 가능 피처가 최소 4개 미만이면 감점
        if len(derivable) < 4:
            impact -= 15
            issues["feature_count_low"] = f"도출 가능 피처 {len(derivable)}개 — 최소 4개 권장"

        # 중요 누락 피처 경고 (mfe, choch_grade가 없으면 ML 품질 제한)
        critical_missing = ["mfe_pct", "choch_grade"]
        for cm in critical_missing:
            if any(cm in m for m in missing):
                issues[f"critical_missing_{cm}"] = (
                    f"{cm} 미저장 → execute_buy/execute_sell에서 DB에 추가 컬럼 저장 권장"
                )

        passed = len(derivable) >= 4
        note = f"도출가능 {len(derivable)}개 / 누락 {len(missing)}개"
        return (
            CheckResult("feature_coverage", passed, impact, issues, note),
            derivable,
            missing,
        )

    # ──────────────────────────────────────
    # 종합 점수 계산
    # ──────────────────────────────────────

    def _compute_score(self, checks: list[CheckResult]) -> float:
        total = 100 + sum(c.score_impact for c in checks)
        return max(0.0, min(100.0, float(total)))

    # ──────────────────────────────────────
    # 권고사항 생성
    # ──────────────────────────────────────

    def _make_recommendations(
        self,
        checks: list[CheckResult],
        score: float,
    ) -> list[str]:
        recs = []
        check_map = {c.name: c for c in checks}

        ti = check_map.get("time_integrity")
        if ti and not ti.passed:
            d = ti.detail
            if d.get("out_of_order", 0) > 0:
                recs.append(
                    f"[즉시] timestamp 역전 {d['out_of_order']}건 — "
                    "INSERT 순서 보장 또는 ORDER BY timestamp 쿼리 강제 적용"
                )
            if d.get("duplicate_timestamps", 0) > 0:
                recs.append(
                    f"[해결됨] timestamp 중복 {d['duplicate_timestamps']}건 — "
                    "동일 초 복수 체결 (정상). ML 로드 시 'ORDER BY trade_date, id' 사용 → 결정론적 순서 보장"
                )

        sc = check_map.get("schema_consistency")
        if sc and sc.detail.get("invalid_strategy_values"):
            bad = sc.detail["invalid_strategy_values"]
            recs.append(
                f"[높음] strategy='{list(bad.keys())}' 비표준값 {sum(bad.values())}건 — "
                "_extract_strategy() 패턴 추가 또는 수동 UPDATE"
            )
        if sc and sc.detail.get("sell_pnl_all_zero"):
            recs.append(
                "[긴급] 모든 SELL realized_pnl=0 — "
                "is_loss UnboundLocalError 버그 재발 가능성, execute_sell 로그 확인"
            )

        lq = check_map.get("label_quality")
        if lq and lq.detail.get("excessive_draws"):
            recs.append(
                "[높음] PnL=0 SELL 과다 — drift_detector 수동 복구 후 DB UPDATE 필요"
            )
        if lq and lq.detail.get("exit_parse_low"):
            recs.append(
                "[보통] exit reason 파싱률 낮음 — "
                "_EXIT_CATEGORY_MAP에 패턴 추가 (ai_data_quality.py)"
            )

        fc = check_map.get("feature_coverage")
        if fc:
            still_missing = [m for m in fc.detail.get("missing_features", []) if "✓" not in m]
            if still_missing:
                recs.append(
                    f"[중장기] 미저장 피처 {len(still_missing)}개 추가 저장 권장: "
                    + ", ".join(m.split(" (")[0] for m in still_missing[:3])
                )
            else:
                recs.append(
                    "✅ 핵심 ML 피처 (choch_grade/market_regime/rvol/mfe/mae) 모두 DB 저장 중"
                )

        if score >= 90:
            recs.append("✅ AI 학습 준비 완료 — train/test split 후 학습 진행 가능")
        elif score >= 70:
            recs.append("⚠️ 보정 후 학습 가능 — 위 이슈 해결 후 재점검 권장")
        elif score >= 50:
            recs.append("🔶 데이터 정리 후 재시도 — 지금 학습하면 편향 모델 위험")
        else:
            recs.append("🔴 학습 불가 — 데이터 구조적 문제 해결 필수")

        return recs

    # ──────────────────────────────────────
    # 공개 API
    # ──────────────────────────────────────

    def run(self) -> QualityReport:
        rows = self._load()

        date_range = ("", "")
        if rows:
            dates = [r["trade_date"] for r in rows if r.get("trade_date")]
            if dates:
                date_range = (min(dates), max(dates))

        checks = []
        checks.append(self._check_time_integrity(rows))
        checks.append(self._check_schema_consistency(rows))
        checks.append(self._check_label_quality(rows))

        fc_check, derivable, missing = self._check_feature_coverage(rows)
        checks.append(fc_check)

        score = self._compute_score(checks)
        recs  = self._make_recommendations(checks, score)

        return QualityReport(
            score=score,
            row_count=len(rows),
            sell_count=sum(1 for r in rows if r["trade_type"] == "SELL"),
            date_range=date_range,
            checks=checks,
            derived_features=derivable,
            missing_features=missing,
            recommendations=recs,
        )


# ──────────────────────────────────────────────────────────────
# 출력
# ──────────────────────────────────────────────────────────────

def print_report(r: QualityReport, verbose: bool = False):
    bar = "=" * 62
    score_icon = (
        "✅" if r.score >= 90 else
        "⚠️" if r.score >= 70 else
        "🔶" if r.score >= 50 else
        "🔴"
    )
    print(f"\n{bar}")
    print(f"  AI Data Quality Report  {score_icon}  Score: {r.score:.0f}/100")
    print(f"  기간: {r.date_range[0]} ~ {r.date_range[1]}  |  총 {r.row_count}건 (SELL {r.sell_count}건)")
    print(bar)

    for c in r.checks:
        status = "✓" if c.passed else "✗"
        impact = f"({c.score_impact:+d}점)" if c.score_impact else "(±0점)"
        print(f"\n  [{status}] {c.name:<22} {impact}  {c.note}")
        if verbose and c.detail:
            for k, v in c.detail.items():
                if isinstance(v, (dict, list)) and len(str(v)) > 80:
                    print(f"        {k}:")
                    if isinstance(v, dict):
                        for kk, vv in v.items():
                            print(f"          {kk}: {vv}")
                    else:
                        for item in v:
                            print(f"          • {item}")
                else:
                    print(f"        {k}: {v}")

    print(f"\n  [도출 가능 ML 피처 {len(r.derived_features)}개]")
    for f in r.derived_features:
        print(f"    + {f}")

    print(f"\n  [누락 피처 {len(r.missing_features)}개 — 추가 저장 권장]")
    for f in r.missing_features:
        print(f"    - {f}")

    print(f"\n  [권고사항]")
    for rec in r.recommendations:
        print(f"    • {rec}")

    print(f"\n{bar}\n")


# ──────────────────────────────────────────────────────────────
# 헬퍼 (모듈 공개)
# ──────────────────────────────────────────────────────────────

def _categorize_exit(reason: str) -> str:
    """exit reason 문자열 → ML 라벨 카테고리"""
    for pattern, cat in _EXIT_CATEGORY_MAP:
        if re.search(pattern, reason, re.IGNORECASE):
            return cat
    return "unknown"


# ──────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import logging
    logging.basicConfig(level=logging.WARNING)

    parser = argparse.ArgumentParser(description="AI DB Quality Checker")
    parser.add_argument("--days",    type=int, default=90,       help="조회 기간 (일)")
    parser.add_argument("--db",      type=str, default="",       help="DB 경로 (기본: data/trades.db)")
    parser.add_argument("--verbose", action="store_true",         help="상세 출력")
    args = parser.parse_args()

    checker = AIDataQualityChecker(
        db_path=args.db or None,
        days=args.days,
    )
    report = checker.run()
    print_report(report, verbose=args.verbose)

    raise SystemExit(0 if report.score >= 70 else 1)
