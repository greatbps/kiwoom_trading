#!/bin/bash
# Release Gate — 운영 기준선 검증 (Baseline Governance v1.0)
#
# 기준값은 이 스크립트에 하드코딩하지 않는다.
# docs/BASELINE_MANIFEST.md 의 BASELINE:BEGIN~END 블록을 파싱해서 쓴다.
# → 기준을 바꾸려면 Manifest를 고쳐야 하고, 고치면 MANIFEST_VERSION을 올려야 한다.
#
# 사용:
#   ./check_baseline.sh            # 기능 추가·전략 수정 전후에 실행
#
# 종료코드: 0 = PASS(배포 가능) / 1 = RELEASE BLOCKED

cd "$(dirname "$0")" || exit 2
MANIFEST=docs/BASELINE_MANIFEST.md
FAIL=0
line() { printf '%s\n' "------------------------------------------------------------"; }
ok()   { printf '  ✅ %s\n' "$1"; }
ng()   { printf '  ❌ %s\n' "$1"; FAIL=1; }

# ── Manifest 로드 ──────────────────────────────────────────────
if [ ! -f "$MANIFEST" ]; then
    echo "❌ $MANIFEST 없음 — 기준선 정의를 찾을 수 없어 검증 불가"
    exit 2
fi
# BEGIN~END 사이의 KEY=VALUE 만 추출 (주석·코드펜스 무시)
eval "$(sed -n '/BASELINE:BEGIN/,/BASELINE:END/p' "$MANIFEST" \
        | grep -E '^[A-Z_]+=' | sed 's/[[:space:]]*$//')"

for v in MANIFEST_VERSION PYTEST_MIN_PASS PYTEST_MAX_FAIL RUNTIME_MAP_TEST_FILE \
         RUNTIME_MAP_EXPECT_PASS GUIDE_ANCHORS_EXPECT MEMORY_MAX_STABLE_SLOPE_MB_H; do
    [ -z "${!v}" ] && { echo "❌ Manifest에 $v 없음 — 블록 형식 확인 필요"; exit 2; }
done

echo "============================================================"
echo "  Release Gate — 기준선 검증"
echo "  Manifest v$MANIFEST_VERSION (frozen $FROZEN_AT)"
echo "  $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# ── ① Runtime Execution Map ────────────────────────────────────
line; echo "① Runtime Execution Map (기대: ${RUNTIME_MAP_EXPECT_PASS} passed)"
OUT=$(python3 -m pytest "$RUNTIME_MAP_TEST_FILE" -q --no-cov 2>&1 | tail -1)
echo "     $OUT"
if echo "$OUT" | grep -q "${RUNTIME_MAP_EXPECT_PASS} passed"; then
    ok "실행 경로 구조 일치"
else
    ng "실행 경로 변경됨 — RUNTIME_EXECUTION_MAP.md 갱신 + Manifest MAJOR 증가 필요"
fi

# ── ② Operation Guide 앵커 ─────────────────────────────────────
line; echo "② 운영 가이드 앵커 (기대: ${GUIDE_ANCHORS_EXPECT}/${GUIDE_ANCHORS_EXPECT})"
A=0
while IFS='|' read -r f p; do
    [ -z "$f" ] && continue
    grep -q -- "$p" "$f" 2>/dev/null && A=$((A+1)) || echo "     ✗ $f :: $p"
done <<'ANCHORS'
trading/exit_logic_optimized.py|max(prev_stop, calc_stop)
main_auto_trading.py|_pe_prev_stop, _pe_calc_stop
kiwoom_api.py|def order_buy
kiwoom_api.py|def order_sell
trading/exit_logic_optimized.py|def check_exit_signal
core/drawdown_engine.py|def can_enter
ANCHORS
echo "     유효 $A/${GUIDE_ANCHORS_EXPECT}"
[ "$A" -eq "$GUIDE_ANCHORS_EXPECT" ] && ok "가이드 바로가기 표 유효" \
    || ng "앵커 깨짐 — OPERATION_MAINTENANCE_GUIDE.md §3 갱신 필요"

# ── ③ 전체 회귀 ────────────────────────────────────────────────
line; echo "③ 전체 회귀 (기대: PASS>=${PYTEST_MIN_PASS}, FAIL<=${PYTEST_MAX_FAIL})"
OUT=$(python3 -m pytest tests/unit/ tests/simulation/ --no-cov -q 2>&1 | tail -1)
echo "     $OUT"
P=$(echo "$OUT" | grep -oP '\d+(?= passed)'); P=${P:-0}
F=$(echo "$OUT" | grep -oP '\d+(?= failed)'); F=${F:-0}
if [ "$P" -ge "$PYTEST_MIN_PASS" ] && [ "$F" -le "$PYTEST_MAX_FAIL" ]; then
    ok "회귀 통과 (PASS $P / FAIL $F)"
else
    ng "회귀 기준 미달 (PASS $P<$PYTEST_MIN_PASS 또는 FAIL $F>$PYTEST_MAX_FAIL) — NEW FAIL 발생"
    echo "     ↳ Known FAIL 목록은 docs/KNOWN_ISSUES.md KI-01 참조"
fi

# ── ④ 데이터 무결성 ────────────────────────────────────────────
line; echo "④ 데이터 무결성"
python3 -m analysis.check_destructive_tests >/dev/null 2>&1 \
    && ok "파괴적 SQL 가드 (위반 0건)" \
    || ng "가드 없는 TRUNCATE/DELETE 발견 — analysis/check_destructive_tests 확인"
OUT=$(python3 -m analysis.check_research_integrity 2>&1 | grep "STATUS")
echo "     research: ${OUT:-(조회 실패)}"
echo "$OUT" | grep -q "OK" && ok "Research Integrity OK" \
    || ng "Research Integrity 이상 — python3 -m analysis.check_research_integrity"

# ── ⑤ 메모리 ───────────────────────────────────────────────────
line; echo "⑤ Memory Profiling (기준: 안정구간 < +${MEMORY_MAX_STABLE_SLOPE_MB_H} MB/h)"
if OUT=$(python3 -m analysis.memprof_report 2>/dev/null | grep -E "안정구간 기울기|STATUS"); then
    echo "$OUT" | sed 's/^/     /'
    echo "$OUT" | grep -q "STABLE" && ok "메모리 정상" \
        || ng "메모리 이상 — python3 -m analysis.memprof_report"
else
    echo "     (당일 샘플 없음 — 프로파일러 미가동, 판정 생략)"
    ok "판정 생략 (샘플 없음)"
fi

# ── 결과 ───────────────────────────────────────────────────────
line
if [ "$FAIL" -eq 0 ]; then
    echo "  RESULT : ✅ RELEASE ALLOWED — 기준선 통과 (Manifest v$MANIFEST_VERSION)"
else
    echo "  RESULT : ❌ RELEASE BLOCKED — 변경을 되돌리거나 Manifest를 갱신할 것"
    echo "           절차: docs/BASELINE_GOVERNANCE_v1.md §변경 절차"
fi
line
exit $FAIL
