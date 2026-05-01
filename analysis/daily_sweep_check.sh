#!/bin/bash
# 일일 Sweep 품질 점검 스크립트
# 사용법: bash analysis/daily_sweep_check.sh [YYYYMMDD]

DATE=${1:-$(date +%Y%m%d)}
LOG="logs/sweep_attempt_${DATE}.log"

count() { grep -c "$1" "$2" 2>/dev/null || true; }
pct() { [ "$2" -gt 0 ] && echo $(( $1 * 100 / $2 )) || echo 0; }

if [ ! -f "$LOG" ]; then
    echo "❌ 로그 없음: $LOG"
    exit 1
fi

echo "=== SWEEP QUALITY REPORT: $DATE ==="
echo ""

# 1. SWEEP_RESULT 유형 분포
echo "--- 1. SWEEP_RESULT 유형 분포 ---"
TOTAL=$(count 'SWEEP_RESULT' "$LOG")
PENET=$(grep 'SWEEP_RESULT' "$LOG" 2>/dev/null | grep -c 'type=penetration' || true)
EQUAL=$(grep 'SWEEP_RESULT' "$LOG" 2>/dev/null | grep -c 'type=equal_level' || true)
FILTD=$(grep 'SWEEP_RESULT' "$LOG" 2>/dev/null | grep -c 'type=filtered' || true)

echo "총 Sweep 결과: $TOTAL"
if [ "$TOTAL" -gt 0 ]; then
    P=$(pct $PENET $TOTAL); E=$(pct $EQUAL $TOTAL); F=$(pct $FILTD $TOTAL)
    echo "  penetration : $PENET (${P}%)"
    echo "  equal_level : $EQUAL (${E}%)"
    echo "  filtered    : $FILTD (${F}%)"
    echo "  [기준] penetration 20~40% / equal_level 40~60% / filtered 20~40%"
    [ "$E" -ge 80 ] && echo "  ⚠️  equal_level 과다 → E2 필터링 필요"
    [ "$F" -ge 60 ] && echo "  ⚠️  filtered 과다 → distance 범위 재검토"
else
    echo "  ❌ SWEEP_RESULT 없음 (오늘 첫 장 or 파이프라인 확인)"
fi

echo ""

# 2. Sweep 총량 판단
echo "--- 2. SWEEP_DETECTED 총량 ---"
DETECTED=$(count 'SWEEP_DETECTED' "$LOG")
echo "SWEEP_DETECTED: $DETECTED"
if   [ "$DETECTED" -eq 0 ];   then echo "  ❌ 0건 — 임계값 or 스윙 좌표 확인"
elif [ "$DETECTED" -lt 20 ];  then echo "  ⚠️  부족 (목표: 20~60건)"
elif [ "$DETECTED" -gt 100 ]; then echo "  ⚠️  과다 (목표: 20~60건)"
else echo "  ✅ 정상 범위"
fi

echo ""

# 3. FILTERED_OUT 상세 사유
echo "--- 3. FILTERED_OUT 사유 분포 ---"
FOUT=$(count 'FILTERED_OUT' "$LOG")
echo "FILTERED_OUT: $FOUT"
grep 'FILTERED_OUT' "$LOG" 2>/dev/null | grep -o 'reason=[^ ]*' | sort | uniq -c | sort -rn | head -5

echo ""

# 4. Entry 발생 확인
echo "--- 4. Entry 발생 확인 ---"
ENTRY_LOG=$(ls logs/auto_trading_${DATE}*.log 2>/dev/null | head -1)
if [ -n "$ENTRY_LOG" ]; then
    ENTRIES=$(grep -c 'execute_buy\|BUY_ORDER\|\[BUY\]' "$ENTRY_LOG" 2>/dev/null || true)
    echo "Entry 건수: $ENTRIES  ($(basename $ENTRY_LOG))"
    if   [ "$ENTRIES" -eq 0 ]; then echo "  ❌ Entry 없음 → OB/Pullback 단계 확인"
    elif [ "$ENTRIES" -gt 20 ]; then echo "  ⚠️  과다 진입 (20건↑) → 품질 필터 검토"
    else echo "  ✅ 정상 범위 (3~20건)"
    fi
else
    echo "  (auto_trading 로그 없음)"
fi

echo ""
echo "=== END ==="
