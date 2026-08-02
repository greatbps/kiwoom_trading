"""
overnight_tracker.py — 14:50 오버나이트 결정 결과 자동 기록

매일 장 종료 후 실행:
  python3 -m analysis.overnight_tracker

overnight_tracking 테이블에서 outcome 미기록 행을 찾아
yfinance로 익일/3일/5일 수익률을 채우고, outcome_category를 분류한다.

분류:
  FORCED_CLOSE + 익일 < threshold_correct → correct_stop
  FORCED_CLOSE + 익일 > threshold_wrong   → wrong_stop (missed winner)
  FORCED_CLOSE + 나머지                   → neutral
  KEPT         + 익일 > threshold_winner  → saved_winner
  KEPT         + 익일 < 0                 → saved_loser
  KEPT         + 나머지                   → neutral
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2
import yfinance as yf
import datetime
import yaml
import logging
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger('overnight_tracker')
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')

DB_CONF = dict(dbname='trading_system', user='postgres', password=os.getenv('POSTGRES_PASSWORD'), host='localhost')
YAML_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config', 'strategy_hybrid.yaml')


def _load_thresholds():
    with open(YAML_PATH) as f:
        cfg = yaml.safe_load(f)
    t = (cfg.get('overnight_close') or {}).get('tracking', {})
    return {
        'saved_winner': t.get('saved_winner_threshold', 5.0),
        'wrong_stop':   t.get('wrong_stop_threshold', 3.0),
        'correct_stop': t.get('correct_stop_threshold', -1.0),
    }


def _yahoo_ticker(stock_code: str) -> str:
    return f"{stock_code}.KS"


def _fetch_kospi_return(from_date: datetime.date):
    """결정일 KOSPI 등락률 (시가 대비 종가 %)"""
    start = from_date.isoformat()
    end   = (from_date + datetime.timedelta(days=3)).isoformat()
    df = yf.download('^KS11', start=start, end=end, progress=False, auto_adjust=True)
    if df.empty or len(df) < 1:
        return None
    row = df.iloc[0]
    try:
        # MultiIndex 또는 단일 컬럼 모두 대응
        o = float(row['Open']) if 'Open' in row.index else float(row.iloc[0])
        c = float(row['Close']) if 'Close' in row.index else float(row.iloc[3])
        return (c - o) / o * 100 if o > 0 else None
    except Exception:
        return None


def _fetch_returns(ticker: str, from_date: datetime.date, exit_price: float,
                   n_trading_days: int = 25):
    """exit_price 대비 익일/3일/5일/10일/20일 + MFE/MAE + days_new_high 반환
    from_date = 청산일, 익일 = 첫 번째 거래 영업일
    """
    start = from_date + datetime.timedelta(days=1)
    end   = from_date + datetime.timedelta(days=n_trading_days * 2 + 5)
    df = yf.download(ticker, start=start.isoformat(), end=end.isoformat(),
                     progress=False, auto_adjust=True)
    empty = (None,) * 13
    if df.empty:
        return empty

    # yfinance MultiIndex 대응 — 항상 1D Series로 추출
    def _col(df_, name):
        if name in df_.columns:
            s = df_[name]
        else:
            matches = [c for c in df_.columns if (isinstance(c, tuple) and c[0] == name)]
            if not matches:
                return None
            s = df_[matches[0]]
        # DataFrame → Series squeeze
        if hasattr(s, 'squeeze'):
            s = s.squeeze()
        return s

    close_s = _col(df, 'Close')
    high_s  = _col(df, 'High')
    low_s   = _col(df, 'Low')
    if close_s is None:
        return empty

    closes = close_s.dropna()
    if len(closes) < 1 or not hasattr(closes, 'iloc'):
        return empty

    base = exit_price  # 청산가 기준

    def pct(idx):
        if len(closes) > idx:
            return float(closes.iloc[idx]) / base * 100 - 100
        return None

    # 20일 구간 MFE / MAE + 발생 시점
    window = df.iloc[:20]
    mfe20 = mae20 = mfe_day = mae_day = None
    days_new_high = 0
    if len(window) > 0 and high_s is not None and low_s is not None:
        highs  = high_s.iloc[:20].dropna()
        lows   = low_s.iloc[:20].dropna()
        peak_idx   = int(highs.values.argmax())
        trough_idx = int(lows.values.argmin())
        mfe20   = (float(highs.iloc[peak_idx])   - base) / base * 100
        mae20   = (float(lows.iloc[trough_idx])  - base) / base * 100
        mfe_day = peak_idx   + 1
        mae_day = trough_idx + 1

        # 추세 지속성: 종가 기준 새 고점을 몇 번 갱신했는가
        running_max = base
        for c_val in closes.iloc[:20]:
            v = float(c_val)
            if v > running_max:
                running_max = v
                days_new_high += 1

    # Path Shape 지표
    days_to_10pct = None
    for i, h_val in enumerate(high_s.iloc[:20].dropna() if high_s is not None else []):
        if float(h_val) >= base * 1.10:
            days_to_10pct = i + 1  # 1-indexed
            break

    # MFE 이후 하락폭 / Retention (MFE < 3%면 스킵 — 소규모 MFE에서 비율 왜곡)
    ret20_val = pct(19)
    drawdown_after_mfe = None
    mfe_retention = None
    if mfe20 is not None and mfe20 >= 3.0:
        final = ret20_val if ret20_val is not None else pct(min(len(closes)-1, 18))
        if final is not None:
            drawdown_after_mfe = final - mfe20           # 음수 = MFE에서 얼마나 하락
            mfe_retention = final / mfe20 * 100          # 비츠로셀 68%, 엑스큐어 -84%

    # 익일(0), 3일후(2), 5일후(4), 10일후(9), 20일후(19)
    return (pct(0), pct(2), pct(4), pct(9), pct(19),
            mfe20, mae20, mfe_day, mae_day, days_new_high,
            days_to_10pct, drawdown_after_mfe, mfe_retention)


def _classify(decision: str, ret1, thresholds: dict) -> str:
    if ret1 is None:
        return 'unknown'
    if decision == 'FORCED_CLOSE':
        if ret1 <= thresholds['correct_stop']:
            return 'correct_stop'
        if ret1 >= thresholds['wrong_stop']:
            return 'wrong_stop'
        return 'neutral'
    else:  # KEPT
        if ret1 >= thresholds['saved_winner']:
            return 'saved_winner'
        if ret1 < 0:
            return 'saved_loser'
        return 'neutral'


def fill_outcomes(dry_run: bool = False):
    thresholds = _load_thresholds()
    conn = psycopg2.connect(**DB_CONF)
    cur  = conn.cursor()

    cur.execute("""
        SELECT id, decision_date, stock_code, stock_name, decision, close_price, pnl_pct
        FROM overnight_tracking
        WHERE outcome_filled_at IS NULL
          AND decision_date < CURRENT_DATE
        ORDER BY decision_date
    """)
    rows = cur.fetchall()
    logger.info(f"결과 미기록 {len(rows)}건 처리 시작")

    updated = 0
    for row_id, dec_date, code, name, decision, close_price, pnl_pct_db in rows:
        ticker = _yahoo_ticker(code)
        exit_px = float(close_price) if close_price else None
        if not exit_px:
            logger.warning(f"  {name} close_price 없음 — 스킵")
            continue
        exit_px_pct = float(pnl_pct_db) if pnl_pct_db is not None else None
        ret1, ret3, ret5, ret10, ret20, mfe20, mae20, mfe_day, mae_day, dnh, \
            d10, dd_after_mfe, mfe_ret = \
            _fetch_returns(ticker, dec_date, exit_price=exit_px)
        kospi_ret = _fetch_kospi_return(dec_date)
        category = _classify(decision, ret1, thresholds)

        # EOL: 보유했을 때 기대수익 - 실제 실현수익 (FORCED·KEPT 공통)
        # FORCED_CLOSE: EOL>0=기회비용, EOL<0=손실 방어
        # KEPT:         EOL>0=보유효과, EOL<0=잘못된 보유
        eol = round(float(ret20) - float(exit_px_pct), 4) \
            if ret20 is not None and exit_px_pct is not None else None

        ret1s = f"{ret1:+.2f}%"  if ret1  is not None else "N/A"
        ret20s= f"{ret20:+.2f}%" if ret20 is not None else "N/A"
        eols  = f"EOL={eol:+.2f}%" if eol  is not None else ""
        mfes  = f"MFE={mfe20:+.2f}%(D{mfe_day})" if mfe20 is not None else ""
        dnhs  = f"newH={dnh}회" if dnh is not None else ""
        cat_symbol = {'correct_stop':'✅', 'wrong_stop':'❌', 'saved_winner':'🌟',
                      'saved_loser':'⚠️', 'neutral':'➖', 'unknown':'?'}.get(category, ' ')
        logger.info(f"  {cat_symbol} {dec_date} {name} ({decision}) → "
                    f"익일={ret1s} 20일={ret20s} {eols} {mfes} {dnhs} [{category}]")

        if not dry_run:
            cur.execute("""
                UPDATE overnight_tracking
                SET next_day_return        = %s,
                    three_day_return       = %s,
                    five_day_return        = %s,
                    ten_day_return         = %s,
                    twenty_day_return      = %s,
                    window20_mfe_pct       = %s,
                    window20_mae_pct       = %s,
                    window20_mfe_day       = %s,
                    window20_mae_day       = %s,
                    days_new_high          = %s,
                    days_to_10pct          = %s,
                    drawdown_after_mfe     = %s,
                    mfe_retention_pct      = %s,
                    kospi_decision_return  = %s,
                    eol_pct                = %s,
                    outcome_category       = %s,
                    outcome_filled_at      = NOW()
                WHERE id = %s
            """, (ret1, ret3, ret5, ret10, ret20,
                  mfe20, mae20, mfe_day, mae_day,
                  dnh, d10, dd_after_mfe, mfe_ret,
                  kospi_ret, eol, category, row_id))
            updated += 1

    if not dry_run:
        conn.commit()
    conn.close()
    logger.info(f"완료: {updated}건 업데이트" if not dry_run else "dry_run — DB 미저장")
    return updated


def print_report(days: int = 90):
    """최근 N일 overnight 결정 요약 리포트"""
    conn = psycopg2.connect(**DB_CONF)
    cur  = conn.cursor()
    cur.execute("""
        SELECT decision, outcome_category,
               COUNT(*) AS n,
               AVG(next_day_return)    AS avg_r1,
               AVG(three_day_return)   AS avg_r3,
               AVG(twenty_day_return)  AS avg_r20,
               AVG(window20_mfe_pct)   AS avg_mfe,
               AVG(window20_mae_pct)   AS avg_mae
        FROM overnight_tracking
        WHERE decision_date >= CURRENT_DATE - %s
          AND outcome_filled_at IS NOT NULL
        GROUP BY decision, outcome_category
        ORDER BY decision, outcome_category
    """, (days,))
    rows = cur.fetchall()

    # wrong_stop 상세: 전체 Path Shape 포함
    cur.execute("""
        SELECT stock_name, next_day_return, twenty_day_return,
               window20_mfe_pct, window20_mae_pct,
               window20_mfe_day, window20_mae_day,
               days_new_high, days_to_10pct,
               drawdown_after_mfe, mfe_retention_pct,
               kospi_decision_return, decision_date
        FROM overnight_tracking
        WHERE decision_date >= CURRENT_DATE - %s
          AND outcome_category = 'wrong_stop'
          AND outcome_filled_at IS NOT NULL
        ORDER BY window20_mfe_pct DESC NULLS LAST
    """, (days,))
    wrong_stops = cur.fetchall()

    cur.execute("""
        SELECT COUNT(*) FROM overnight_tracking
        WHERE decision_date >= CURRENT_DATE - %s
          AND outcome_filled_at IS NULL
    """, (days,))
    pending = cur.fetchone()[0]
    conn.close()

    print(f"\n{'='*80}")
    print(f"  14:50 오버나이트 정책 리포트 (최근 {days}일)")
    print(f"{'='*80}")
    print(f"{'결정':<15} {'분류':<18} {'건수':>4} {'평균익일':>9} {'평균20일':>9} {'MFE20':>8} {'MAE20':>8}")
    print(f"{'-'*80}")

    ev_kept = 0.0; n_kept = 0
    ev_forced = 0.0; n_forced = 0

    for decision, category, n, avg1, avg3, avg20, avg_mfe, avg_mae in rows:
        a1   = f"{float(avg1):+.2f}%"   if avg1   else "  N/A"
        a20  = f"{float(avg20):+.2f}%"  if avg20  else "  N/A"
        amfe = f"{float(avg_mfe):+.2f}%" if avg_mfe else "  N/A"
        amae = f"{float(avg_mae):+.2f}%" if avg_mae else "  N/A"
        print(f"{decision:<15} {category:<18} {n:>4} {a1:>9} {a20:>9} {amfe:>8} {amae:>8}")
        if avg1:
            if decision == 'KEPT':
                ev_kept += float(avg1) * n; n_kept += n
            else:
                ev_forced += float(avg1) * n; n_forced += n

    print(f"{'-'*80}")
    if n_kept:
        print(f"KEPT 기대값: {ev_kept/n_kept:+.2f}% / 건  (n={n_kept})")
    if n_forced:
        print(f"FORCED 기대값 (기회비용): {ev_forced/n_forced:+.2f}% / 건  (n={n_forced})")

    if wrong_stops:
        print(f"\n  [wrong_stop 상세 — 정책이 차단한 수익 기회]")
        hdr = f"  {'날짜':<12} {'종목':<10} {'MFE':>7} {'MFE일':>5} {'newH':>5} {'D10':>5} {'MAE':>7} {'Ret%':>6} {'20일':>7} {'DDaft':>7}"
        print(hdr)
        print(f"  {'-'*len(hdr.rstrip())}")
        for row in wrong_stops:
            sname, r1, r20, mfe, mae, mfe_d, mae_d, dnh, d10, ddaft, ret_pct, kospi, ddate = row
            mfes  = f"{float(mfe):+.1f}%"    if mfe    is not None else "  N/A"
            mfd   = f"D{mfe_d}"              if mfe_d  is not None else "  N/A"
            dnhs  = f"{int(dnh)}회"          if dnh    is not None else "  N/A"
            d10s  = f"D{d10}"               if d10    is not None else " None"
            maes  = f"{float(mae):+.1f}%"    if mae    is not None else "  N/A"
            rets  = f"{float(ret_pct):.0f}%" if ret_pct is not None else "  N/A"
            r20s  = f"{float(r20):+.1f}%"   if r20    is not None else "  N/A"
            ddafts= f"{float(ddaft):+.1f}%" if ddaft  is not None else "  N/A"
            print(f"  {str(ddate):<12} {sname:<10} {mfes:>7} {mfd:>5} {dnhs:>5} {d10s:>5} {maes:>7} {rets:>6} {r20s:>7} {ddafts:>7}")

    if pending:
        print(f"\n  ※ 결과 미기록 {pending}건 (아직 20일 미경과)")
    print()


def print_weekly_report():
    """주간 정책 리포트 — 토요일 자동 실행 대상"""
    import datetime as _dt
    today = _dt.date.today()
    week_start = today - _dt.timedelta(days=7)

    conn = psycopg2.connect(**DB_CONF)
    cur  = conn.cursor()

    # ── Health Check ──────────────────────────────────────────────────────
    hc_status = {}
    hc_warnings = []
    try:
        cur.execute("""
            SELECT
                COUNT(*)                                                      AS total,
                COUNT(*) FILTER (WHERE outcome_filled_at IS NOT NULL)         AS filled,
                COUNT(*) FILTER (WHERE outcome_filled_at IS NULL
                                   AND decision_date < CURRENT_DATE)          AS pending,
                COUNT(*) FILTER (WHERE eol_pct IS NOT NULL)                   AS with_eol,
                COUNT(*) FILTER (WHERE decision_confidence IS NOT NULL)       AS with_conf
            FROM overnight_tracking
        """)
        hc = cur.fetchone()
        total, filled, pending, with_eol, with_conf = hc
        hc_status['Data Collection']  = (total > 0, f"{total}건")
        hc_status['EOL Calculation']  = (with_eol > 0, f"{with_eol}/{filled}건 계산됨")
        hc_status['Calibration']      = (with_conf > 0, f"{with_conf}건")
        hc_status['Policy Comparison']= (filled > 0, f"{filled}건 결과 확정")
        if pending > 0:
            hc_warnings.append(f"결과 대기 {pending}건 (20일 미경과)")
        if total == 0:
            hc_warnings.append("overnight_tracking 데이터 없음")
        if filled > 0 and with_eol == 0:
            hc_warnings.append("EOL 미계산 — fill_outcomes() 재실행 필요")
    except Exception as _e:
        hc_status['Data Collection'] = (False, str(_e))
        hc_warnings.append(f"DB 오류: {_e}")

    # Monthly PnL 접근 가능 여부
    try:
        cur.execute("SELECT 1 FROM trades LIMIT 1")
        cur.fetchone()
        hc_status['Monthly PnL'] = (True, "trades 테이블 접근 OK")
    except Exception as _e:
        hc_status['Monthly PnL'] = (False, str(_e))
        hc_warnings.append(f"trades 테이블 접근 불가: {_e}")

    # ── 이번 주 신규 결정 요약
    cur.execute("""
        SELECT decision, COUNT(*),
               SUM(CASE WHEN outcome_category='wrong_stop'    THEN 1 ELSE 0 END),
               SUM(CASE WHEN outcome_category='correct_stop'  THEN 1 ELSE 0 END),
               SUM(CASE WHEN outcome_category='saved_winner'  THEN 1 ELSE 0 END),
               SUM(CASE WHEN outcome_category='saved_loser'   THEN 1 ELSE 0 END)
        FROM overnight_tracking
        WHERE decision_date >= %s
        GROUP BY decision
    """, (week_start,))
    week_rows = cur.fetchall()

    # KPI 1+2: Confidence 구간별 wrong_stop 비율 + Opportunity Loss
    cur.execute("""
        SELECT
            CASE
                WHEN decision_confidence < 60 THEN '1. 50~59 (경계)'
                WHEN decision_confidence < 70 THEN '2. 60~69 (보통)'
                WHEN decision_confidence < 80 THEN '3. 70~79 (확실)'
                ELSE                               '4. 80~100 (매우확실)'
            END AS band,
            decision,
            COUNT(*) AS n,
            SUM(CASE WHEN outcome_category = 'wrong_stop'   THEN 1 ELSE 0 END) AS wrong,
            SUM(CASE WHEN outcome_category = 'correct_stop' THEN 1 ELSE 0 END) AS correct,
            SUM(CASE WHEN outcome_category = 'saved_winner' THEN 1 ELSE 0 END) AS saved,
            -- wrong_stop에서 발생한 기회비용 합계
            SUM(CASE WHEN outcome_category = 'wrong_stop' AND decision = 'FORCED_CLOSE'
                     THEN eol_pct ELSE 0 END) AS total_eol,
            -- 정책이 구한 손실 합계 (correct_stop에서 eol<0 = 우리가 피한 손실)
            SUM(CASE WHEN outcome_category = 'correct_stop' AND decision = 'FORCED_CLOSE'
                     THEN ABS(COALESCE(eol_pct, 0)) ELSE 0 END) AS total_saved_loss
        FROM overnight_tracking
        WHERE decision_confidence IS NOT NULL
          AND outcome_filled_at IS NOT NULL
        GROUP BY 1, 2
        ORDER BY 1, 2
    """)
    conf_rows = cur.fetchall()

    # KPI 3: Policy Version별 Expectancy (평균 EOL)
    cur.execute("""
        SELECT policy_version,
               COUNT(*) AS n,
               AVG(eol_pct) AS avg_eol,
               SUM(CASE WHEN decision='FORCED_CLOSE' AND outcome_category='wrong_stop'
                        THEN eol_pct ELSE 0 END) AS total_opportunity_loss,
               SUM(CASE WHEN decision='FORCED_CLOSE' AND outcome_category='correct_stop'
                        THEN ABS(COALESCE(eol_pct, 0)) ELSE 0 END) AS total_saved_loss,
               SUM(CASE WHEN outcome_category='wrong_stop'   THEN 1 ELSE 0 END) AS wrong,
               SUM(CASE WHEN outcome_category='saved_winner' THEN 1 ELSE 0 END) AS saved
        FROM overnight_tracking
        WHERE outcome_filled_at IS NOT NULL
        GROUP BY policy_version
        ORDER BY avg_eol ASC NULLS LAST
    """)
    policy_rows = cur.fetchall()

    # Confidence Calibration: 점수가 실제 결과를 얼마나 잘 예측하는가
    cur.execute("""
        SELECT
            CASE
                WHEN decision_confidence < 60 THEN '50~59'
                WHEN decision_confidence < 70 THEN '60~69'
                WHEN decision_confidence < 80 THEN '70~79'
                ELSE                               '80~100'
            END AS band,
            COUNT(*) AS n,
            ROUND(
                SUM(CASE
                    WHEN decision = 'FORCED_CLOSE' AND outcome_category <> 'wrong_stop' THEN 1.0
                    WHEN decision = 'KEPT'         AND outcome_category <> 'saved_loser' THEN 1.0
                    ELSE 0 END
                ) / NULLIF(COUNT(*), 0) * 100, 1
            ) AS success_rate,
            AVG(eol_pct) AS avg_eol
        FROM overnight_tracking
        WHERE decision_confidence IS NOT NULL
          AND outcome_filled_at IS NOT NULL
        GROUP BY 1
        ORDER BY 1
    """)
    calib_rows = cur.fetchall()

    # KPI 4: 월간 누적 PnL (trades 테이블)
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE realized_profit > 0) AS win,
            COUNT(*) FILTER (WHERE realized_profit <= 0) AS lose,
            COALESCE(SUM(realized_profit), 0) AS total_pnl,
            COALESCE(AVG(profit_rate), 0) AS avg_pct
        FROM trades
        WHERE trade_time >= DATE_TRUNC('month', CURRENT_DATE)
          AND trade_type = 'SELL'
    """)
    pnl_row = cur.fetchone()
    conn.close()

    print(f"\n{'='*72}")
    print(f"  주간 오버나이트 정책 리포트 ({week_start} ~ {today})")
    print(f"{'='*72}")

    # Health Check 블록
    print(f"\n  Report Status")
    print(f"  {'─'*44}")
    for name, (ok, detail) in hc_status.items():
        mark = "✓" if ok else "✗"
        print(f"  {mark} {name:<22} {detail}")
    print(f"  Generated: {_dt.datetime.now().strftime('%Y-%m-%d %H:%M')}")
    hc_total = hc_status.get('Data Collection', (False, '0건'))[1]
    print(f"  Records:   {hc_total}")
    if hc_warnings:
        print(f"  Warnings:  {len(hc_warnings)}")
        for w in hc_warnings:
            print(f"    ⚠ {w}")
    else:
        print(f"  Warnings:  0")
    print()

    # 이번 주 결정 요약
    print(f"  [이번 주 결정 ({week_start}~)]")
    if not week_rows:
        print(f"  (이번 주 overnight 결정 없음)")
    for dec, n, wrong, correct, saved, loser in week_rows:
        print(f"  {dec:<15} {n}건 | wrong={wrong} correct={correct} saved={saved} loser={loser}")

    # KPI 1+2: Confidence 구간별
    if conf_rows:
        print(f"\n  ━━ KPI 1+2: Confidence 구간별 wrong 비율 + 기회비용 (EOL) ━━")
        print(f"  ※ 연구 자원은 '경계' 구간에 집중 (50건 이상 시 통계적 의미)")
        print(f"  {'구간':<20} {'결정':<14} {'n':>4} {'wrong%':>8} {'기회비용(합)':>13} {'방어손실(합)':>13}")
        print(f"  {'-'*75}")
        for band, dec, n, wrong, correct, saved, t_eol, t_saved in conf_rows:
            n = int(n)
            wrong_pct = f"{int(wrong)/n*100:.0f}%" if n > 0 else "  -"
            eol_s   = f"+{float(t_eol):.1f}%"   if t_eol  and float(t_eol)  > 0 else "-"
            saved_s = f"+{float(t_saved):.1f}%" if t_saved and float(t_saved) > 0 else "-"
            print(f"  {band:<20} {dec:<14} {n:>4} {wrong_pct:>8} {eol_s:>13} {saved_s:>13}")

    # KPI 3: Policy Version별 Expectancy
    if policy_rows:
        print(f"\n  ━━ KPI 3: Policy Version별 Expectancy ━━")
        print(f"  ※ avg_eol<0 = 정책이 평균적으로 이득 (기회비용 < 방어손실)")
        print(f"  {'버전':<22} {'n':>4} {'평균EOL':>9} {'기회비용(합)':>13} {'방어(합)':>10} {'wrong':>6} {'saved':>6}")
        print(f"  {'-'*75}")
        for ver, n, avg_eol, t_opp, t_save, wrong, saved in policy_rows:
            eol_s  = f"{float(avg_eol):+.2f}%"  if avg_eol  is not None else "  N/A"
            opp_s  = f"+{float(t_opp):.1f}%"   if t_opp  and float(t_opp)  > 0 else "-"
            save_s = f"+{float(t_save):.1f}%"  if t_save and float(t_save) > 0 else "-"
            print(f"  {str(ver):<22} {int(n):>4} {eol_s:>9} {opp_s:>13} {save_s:>10} "
                  f"{int(wrong or 0):>6} {int(saved or 0):>6}")
        print(f"  ※ Champion 기준: avg_eol 가장 낮은(음수) 버전")

    # Confidence Calibration
    if calib_rows:
        print(f"\n  ━━ Confidence Calibration ━━")
        print(f"  ※ 점수와 실제 성공률이 일치할수록 모델이 잘 보정된 것")
        print(f"  ※ 50건↑ 시 의미있는 비교 가능 — 현재는 추세 파악용")
        print(f"  {'Conf 구간':<12} {'n':>4} {'실제 성공률':>12} {'평균 EOL':>10}  {'상태'}")
        print(f"  {'-'*58}")
        for band, n, success_rate, avg_eol in calib_rows:
            n = int(n)
            sr  = float(success_rate) if success_rate is not None else None
            eol = float(avg_eol)      if avg_eol      is not None else None
            sr_s  = f"{sr:.1f}%"    if sr  is not None else "  N/A"
            eol_s = f"{eol:+.2f}%"  if eol is not None else "  N/A"
            # 이상적: 구간 중앙값 (55, 65, 75, 90) ≈ 성공률
            ideal = {'50~59': 55, '60~69': 65, '70~79': 75, '80~100': 90}.get(band, 70)
            if sr is None:
                status = "데이터 없음"
            elif n < 10:
                status = f"표본 부족({n}건)"
            elif abs(sr - ideal) <= 10:
                status = "잘 보정됨 ✓"
            elif sr < ideal - 10:
                status = f"과신 (실제={sr:.0f}% < 목표={ideal}%)"
            else:
                status = f"과소평가 (실제={sr:.0f}% > 목표={ideal}%)"
            print(f"  {band:<12} {n:>4} {sr_s:>12} {eol_s:>10}  {status}")

    # KPI 4: 월간 누적 PnL
    if pnl_row:
        win, lose, total_pnl, avg_pct = pnl_row
        print(f"\n  ━━ KPI 4: 이번 달 실현 PnL ━━")
        print(f"  승{int(win)}건 / 패{int(lose)}건  합계={int(total_pnl):+,}원  평균={float(avg_pct):+.2f}%")
    print()


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--report', action='store_true')
    ap.add_argument('--days', type=int, default=90)
    ap.add_argument('--weekly', action='store_true', help='주간 Champion/Challenger 리포트')
    args = ap.parse_args()

    if args.weekly:
        fill_outcomes(dry_run=False)
        print_weekly_report()
        print_report(args.days)
    elif args.report:
        print_report(args.days)
    else:
        fill_outcomes(dry_run=args.dry_run)
        print_report(args.days)
