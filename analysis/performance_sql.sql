-- ============================================================
-- performance_sql.sql — 전략 성능 분석 쿼리 모음
-- DB: trading_system (PostgreSQL)
-- ============================================================

-- ────────────────────────────────────────
-- 1. 전략별 승률 / 평균 손익
-- ────────────────────────────────────────
SELECT
    COALESCE(strategy_name, _extract_strategy(entry_reason)) AS strategy,
    COUNT(*)                                                   AS total_sells,
    SUM(CASE WHEN realized_profit > 0 THEN 1 ELSE 0 END)     AS wins,
    ROUND(100.0 * SUM(CASE WHEN realized_profit > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
    ROUND(AVG(realized_profit), 0)                            AS avg_pnl,
    ROUND(AVG(CASE WHEN realized_profit > 0 THEN realized_profit END), 0) AS avg_win,
    ROUND(AVG(CASE WHEN realized_profit < 0 THEN realized_profit END), 0) AS avg_loss,
    ROUND(SUM(realized_profit), 0)                            AS total_pnl
FROM trades
WHERE trade_type = 'SELL'
  AND realized_profit IS NOT NULL
GROUP BY strategy
ORDER BY total_pnl DESC;


-- ────────────────────────────────────────
-- 2. 일별 손익 추이
-- ────────────────────────────────────────
SELECT
    DATE(trade_time)               AS trade_date,
    COUNT(*)                       AS sell_count,
    SUM(realized_profit)           AS daily_pnl,
    ROUND(AVG(profit_rate), 2)     AS avg_pnl_pct
FROM trades
WHERE trade_type = 'SELL'
  AND realized_profit IS NOT NULL
  AND trade_time >= NOW() - INTERVAL '30 days'
GROUP BY trade_date
ORDER BY trade_date;


-- ────────────────────────────────────────
-- 3. 시간대별 성과 (오전 / 오후)
-- ────────────────────────────────────────
SELECT
    CASE
        WHEN EXTRACT(HOUR FROM trade_time) < 11 THEN '09~11시'
        WHEN EXTRACT(HOUR FROM trade_time) < 13 THEN '11~13시'
        ELSE '13시~'
    END AS time_slot,
    COUNT(*) AS trades,
    ROUND(100.0 * SUM(CASE WHEN realized_profit > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct,
    ROUND(SUM(realized_profit), 0) AS total_pnl
FROM trades
WHERE trade_type = 'SELL' AND realized_profit IS NOT NULL
GROUP BY time_slot
ORDER BY time_slot;


-- ────────────────────────────────────────
-- 4. 필터 단계별 통과율
-- ────────────────────────────────────────
SELECT
    stage,
    COUNT(*) FILTER (WHERE passed = true)  AS passed,
    COUNT(*)                                AS total,
    ROUND(100.0 * COUNT(*) FILTER (WHERE passed = true) / COUNT(*), 1) AS pass_rate_pct
FROM filter_stage_results
WHERE created_at >= NOW() - INTERVAL '7 days'
GROUP BY stage
ORDER BY stage;


-- ────────────────────────────────────────
-- 5. "1차 통과 → 2차 탈락" 종목 분석 (기회 손실)
-- ────────────────────────────────────────
SELECT
    s1.stock_code,
    s1.stock_name,
    s1.created_at::date AS filter_date,
    array_agg(DISTINCT unnest(s2.reason_tags)) AS rejection_reasons
FROM filter_stage_results s1
JOIN filter_stage_results s2
  ON s1.run_id = s2.run_id AND s1.stock_code = s2.stock_code
WHERE s1.stage = 1 AND s1.passed = true
  AND s2.stage = 2 AND s2.passed = false
  AND s1.created_at >= NOW() - INTERVAL '7 days'
GROUP BY s1.stock_code, s1.stock_name, s1.created_at::date
ORDER BY filter_date DESC
LIMIT 50;


-- ────────────────────────────────────────
-- 6. 진입 사유별 평균 손익 (Top 패턴)
-- ────────────────────────────────────────
SELECT
    s.strategy_name,
    LEFT(s.trigger_reason, 60)  AS trigger_pattern,
    COUNT(*)                     AS trades,
    ROUND(AVG(t.realized_profit), 0) AS avg_pnl,
    ROUND(100.0 * SUM(CASE WHEN t.realized_profit > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct
FROM trade_signals s
JOIN trades t ON t.entry_signal_id = s.signal_id
WHERE s.signal_type = 'entry'
  AND t.trade_type = 'SELL'
  AND t.realized_profit IS NOT NULL
GROUP BY s.strategy_name, trigger_pattern
HAVING COUNT(*) >= 2
ORDER BY avg_pnl DESC
LIMIT 20;


-- ────────────────────────────────────────
-- 7. 청산 사유별 분포
-- ────────────────────────────────────────
SELECT
    s.strategy_name              AS exit_type,
    COUNT(*)                     AS count,
    ROUND(AVG(t.realized_profit), 0) AS avg_pnl,
    ROUND(SUM(t.realized_profit), 0) AS total_pnl
FROM trade_signals s
JOIN trades t ON t.exit_signal_id = s.signal_id
WHERE s.signal_type = 'exit'
GROUP BY exit_type
ORDER BY count DESC;


-- ────────────────────────────────────────
-- 8. 보유시간 vs 손익 관계
-- ────────────────────────────────────────
SELECT
    CASE
        WHEN holding_minutes < 15  THEN '0~15분'
        WHEN holding_minutes < 30  THEN '15~30분'
        WHEN holding_minutes < 60  THEN '30~60분'
        WHEN holding_minutes < 120 THEN '60~120분'
        ELSE '120분+'
    END AS hold_bucket,
    COUNT(*) AS trades,
    ROUND(AVG(realized_profit), 0) AS avg_pnl,
    ROUND(100.0 * SUM(CASE WHEN realized_profit > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct
FROM trades
WHERE trade_type = 'SELL'
  AND realized_profit IS NOT NULL
  AND holding_minutes IS NOT NULL
GROUP BY hold_bucket
ORDER BY MIN(holding_minutes);


-- ────────────────────────────────────────
-- 9. 시장 상황별 성과 (GOOD vs BAD market)
-- ────────────────────────────────────────
SELECT
    s.market_context,
    COUNT(*)                     AS trades,
    ROUND(AVG(t.realized_profit), 0) AS avg_pnl,
    ROUND(100.0 * SUM(CASE WHEN t.realized_profit > 0 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate_pct
FROM trade_signals s
JOIN trades t ON t.entry_signal_id = s.signal_id
WHERE s.signal_type = 'entry'
  AND t.realized_profit IS NOT NULL
GROUP BY s.market_context;


-- ────────────────────────────────────────
-- 10. ML 학습 데이터 확인
-- ────────────────────────────────────────
SELECT
    label_binary,
    COUNT(*)                     AS count,
    ROUND(AVG(label_pnl), 0)    AS avg_pnl,
    ROUND(AVG(label_pnl_pct), 2) AS avg_pnl_pct
FROM ml_dataset
GROUP BY label_binary;
