# ACCOUNT_FIELD_INVENTORY

Iteration 7-2 · 2026-08-03 실측 · 조회 전용
민감정보(`acnt_nm`, `brch_nm`, 계좌번호, 토큰)는 마스킹 후 저장했다.

## 조사한 TR

| TR | 이름 | 결과 | 필드 수 |
|---|---|---|---|
| kt00001 | 예수금상세현황 | OK (rc=0) | 72 |
| kt00004 | 계좌평가현황 | OK (rc=0) | 21 |
| ka01690 | 일별잔고수익률 | OK (rc=0) | 11 |

⚠️ `kiwoom_api.get_account_evaluation()` 은 `qry_tp` 를 안 보내
`rc=2 필수입력 파라미터=qry_tp` 로 실패한다. 감사 스크립트는
`qry_tp='0', dmst_stex_tp='KRX'` 를 직접 붙여 호출했다. (운영 코드 무수정)

## kt00001 — 예수금상세현황 (Equity 핵심)

| 필드 | 의미 | 형 | 2026-08-03 값 |
|---|---|---|---|
| `entr` | 예수금 | str(15) | 1,857,799 |
| `pymn_alow_amt` | **출금가능금액 — 현재 운영이 쓰는 값** | str(15) | 1,857,799 |
| `ord_alow_amt` | 주문가능금액 | str(15) | 1,857,799 |
| `d1_entra` | D+1 추정예수금 | str(15) | 1,857,799 |
| `d1_pymn_alow_amt` | D+1 출금가능금액 | str(15) | 1,857,799 |
| `d1_sel_exct_amt` | D+1 매도정산금 | str(15) | 0 |
| `d1_buy_exct_amt` | D+1 매수정산금 | str(15) | 0 |
| `d1_slby_exct_amt` | D+1 매도매수정산금 | str(15) | 0 |
| **`d2_entra`** | **D+2 추정예수금** | str(15) | 1,857,799 |
| `d2_pymn_alow_amt` | D+2 출금가능금액 | str(15) | 1,857,799 |
| `d2_sel_exct_amt` | D+2 매도정산금 | str(15) | 0 |
| `d2_buy_exct_amt` | D+2 매수정산금 | str(15) | 0 |
| `d2_slby_exct_amt` | D+2 매도매수정산금 | str(15) | 0 |
| `repl_amt` | 대용금 | str(15) | 1,366,200 |
| `remn_repl_evlta` | 잔고대용평가금액 | str(15) | 1,366,200 |
| `elwdpst_evlta` | ELW예탁평가금액 | str(15) | 3,223,999 |
| `uncl_stk_amt` | 미수금 | str(15) | 0 |
| `ch_uncla_tot` | 현금미수총액 | str(15) | 0 |
| `loan_sum` / `nrpy_loan` | 대출합/미상환대출 | str(15) | 0 |
| `crd_grnt_rt` | 신용담보비율 | str | 0.00 |
| `mdstrm_usfe` | 중도이용료 | str(15) | 616 |
| `stk_entr_prst` | 종목별예수금현황 | list(1) | — |

**Q(D+2 필드 존재 여부) → 존재한다.** `d2_entra`, `d2_pymn_alow_amt`,
`d2_sel_exct_amt`, `d2_buy_exct_amt`, `d2_slby_exct_amt`, `d2_out_rep_mor`.
(Iteration 7-1 에서 "확인 불가" 로 남겼던 항목이 해소됐다.)

## kt00004 — 계좌평가현황 (총자산 계열)

| 필드 | 의미 | 값 |
|---|---|---|
| `entr` | 예수금 | 1,857,799 |
| `d2_entra` | D+2 추정예수금 | 1,857,799 |
| `tot_est_amt` | 총추정금액(주식) | 1,826,340 |
| **`aset_evlt_amt`** | **자산평가금액** | **3,684,799** |
| `prsm_dpst_aset_amt` | 추정예탁자산금액 | 3,684,529 |
| `tot_pur_amt` | 총매입금액 | 2,625,300 |
| `tdy_lspft_amt` / `lspft_amt` | 당일/누적 손익금액 | 0 |
| `stk_acnt_evlt_prst` | 종목별 평가현황 | list(1) |

오늘 실측 항등식 (계산으로 확인, 추정 아님):
```
aset_evlt_amt 3,684,799 = entr 1,857,799 + tot_evlt_amt(ka01690) 1,827,000
elwdpst_evlta 3,223,999 = entr 1,857,799 + repl_amt 1,366,200
```

## ka01690 — 일별잔고수익률 (평가액 계열)

| 필드 | 의미 | 값 |
|---|---|---|
| `dbst_bal` | 예수금잔고 | 1,857,799 |
| `tot_evlt_amt` | 총평가금액 | 1,827,000 |
| `tot_buy_amt` | 총매수금액 | 2,625,300 |
| `tot_evltv_prft` | 총평가손익 | -798,960 |
| `tot_prft_rt` | 총수익률 | -30.43 |
| `day_stk_asst` | 당일주식자산 | 3,684,529 |
| `day_bal_rt[]` | 종목별 (evlt_amt/cur_prc/rmnd_qty …) | 1종목 |

## 관측 조건

2026-08-03 기준 **미결제 잔액이 없다**
(`d1_*_exct_amt`, `d2_*_exct_amt` 전부 0, 당일 체결 0건).
그래서 `entr = pymn_alow_amt = ord_alow_amt = d1_entra = d2_entra` 로
전부 같다. **이 날짜의 스냅샷만으로는 후보를 판별할 수 없다.**
판별에는 미결제 잔액이 있는 날의 재측정이 1회 필요하다.
