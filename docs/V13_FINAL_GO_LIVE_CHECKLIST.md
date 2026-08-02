# v1.3.1-final Go-Live Checklist

> ruleset: v1.3.1-final  
> 이 문서의 모든 항목을 확인한 뒤에만 `rae.enabled: true`로 전환한다.

---

## A. 성능 기준 (python3 -m backtests.rae_validation_runner --days 60)

- [ ] RAE 거래수 ≥ 20건 확보
- [ ] RAE 단독 PF ≥ 1.10
- [ ] RAE 단독 승률 ≥ 35%
- [ ] RAE avg R ≥ 0.25
- [ ] 전체 PF가 S1 baseline 이상 유지 (악화 ≤ 5%)
- [ ] 전체 MDD 악화폭이 baseline 대비 +15% 이내
- [ ] RAE LCL/EF 비율 < 60% (손절 구조 건강)

→ **판정: `RAE_GO_LIVE_READY`** (위 전체 충족 시)

---

## B. 사이징 정합성 (python3 -m pytest tests/test_position_sizing_matrix.py)

- [ ] 모든 26개 테스트 케이스 PASS
- [ ] Primary + no penalty → base_size와 동일
- [ ] Primary + G3 penalty → base × 0.6
- [ ] RAE only → base × 0.7
- [ ] RAE + G3 penalty → base × 0.7 × 0.6
- [ ] RAE + G3 + CAUTION → base × 0.7 × 0.6 × 0.7
- [ ] HALT → size = 0
- [ ] Hard max cap 적용 확인

---

## C. 중복 진입 / 충돌 방지

- [ ] 같은 종목 포지션 보유 중 RAE 진입 시도 → `[RAE_BLOCK] RULE_A_POSITION_EXISTS` 로그 확인
- [ ] Primary LCL 청산 직후 15분 내 RAE 차단 → `[RAE_BLOCK] RULE_B` 로그 확인
- [ ] RAE 실패 후 당일 재RAE 차단 → `[RAE_BLOCK] RULE_C` 로그 확인
- [ ] Rule D (수익 청산) 후 재진입 허용 확인

---

## D. RAE 상태 리셋 / Orphan 정리

- [ ] 장 시작 전 `[RAE_RESET_DAILY]` 로그 확인
- [ ] 구조 붕괴 시 `[RAE_RESET_INVALIDATED]` 로그 확인
- [ ] 180분 이상 stale `[RAE_RESET_STALE]` 로그 확인
- [ ] 청산 후 `[RAE_RESET_AFTER_EXIT]` 로그 확인
- [ ] 장 종료 시 rae_detector._candidates 비어있음 확인

---

## E. Trade Tag 저장

- [ ] BUY 레코드의 `entry_features`에 `entry_route` 필드 존재
- [ ] RAE 진입 시 `entry_route = "RAE"`, `entry_setup = "SMC_RAE"` 저장됨
- [ ] Primary 진입 시 `entry_route = "PRIMARY"` 저장됨
- [ ] `size_components.route_mult = 0.7` (RAE) 또는 `1.0` (Primary) 확인
- [ ] `ted_score`, `rae_score` RAE 레코드에 저장됨

---

## F. 운영 로그 확인

- [ ] `[G3_SOFT_PENALTY]` 로그 정상 출력 (HIGH_PROX 시)
- [ ] `[SMC_PRIMARY]` 로그 Primary 진입 시 출력
- [ ] `[SMC_RAE]` + `[REACCEL_ENTRY]` 로그 RAE 진입 시 출력
- [ ] `[TED_BLOCK]` 로그 TED 실패 시 출력 (차단 아님, soft 감점)
- [ ] 로그 파일 구분: `auto_trading_YYYYMMDD.log` 정상 기록

---

## G. 리플레이 검증

- [ ] 5영업일 이상 리플레이 정상 동작 (오류 없음)
- [ ] 리플레이 중 RAE 후보 등록 → PULLBACK → REACCEL → 진입 흐름 최소 1회 확인
- [ ] 리플레이 리포트 JSON/MD 정상 생성

---

## H. 운영자 최종 확인

- [ ] `backtests/rae_validation_runner.py` 실행 결과 `RAE_GO_LIVE_READY` 확인
- [ ] `reports/rae_validation_summary_YYYYMMDD.md` 리포트 내용 검토
- [ ] 섹션 E(승인 판정)에서 모든 check ✅ 확인
- [ ] **수동 승인: `config/strategy_hybrid.yaml`에서 `rae.enabled: true`로 변경**

---

## 활성화 절차

```bash
# 1. 검증 실행
python3 -m backtests.rae_validation_runner --days 60

# 2. 사이징 테스트
python3 -m pytest tests/test_position_sizing_matrix.py -v

# 3. 결과 확인
cat reports/rae_validation_summary_$(date +%Y%m%d).md

# 4. 위 항목 전체 확인 후 YAML 수정
# config/strategy_hybrid.yaml → rae.enabled: true

# 5. 시스템 재시작
# kill $(pgrep -f main_auto_trading.py)
# python3 main_auto_trading.py &
```

---

*마지막 업데이트: 2026-07-05 | v1.3.1-final*
