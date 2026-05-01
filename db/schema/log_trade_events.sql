CREATE TABLE IF NOT EXISTS log_trade_events (
    id BIGSERIAL PRIMARY KEY,
    timestamp TIMESTAMP,
    trade_date DATE,
    time_bucket VARCHAR(5),
    source_file TEXT NOT NULL,
    source_tag VARCHAR(40) NOT NULL,
    kind VARCHAR(20) NOT NULL,
    ticker VARCHAR(32),
    symbol TEXT,
    regime VARCHAR(32),
    market_context VARCHAR(32),
    entry_reason TEXT,
    exit_reason TEXT,
    price NUMERIC(18, 4),
    volume_spike SMALLINT,
    result VARCHAR(10),
    pnl NUMERIC(10, 4),
    duration_min NUMERIC(10, 2),
    ingested_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_log_trade_events_ts ON log_trade_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_log_trade_events_date ON log_trade_events(trade_date);
CREATE INDEX IF NOT EXISTS idx_log_trade_events_tag ON log_trade_events(source_tag);
CREATE INDEX IF NOT EXISTS idx_log_trade_events_ticker ON log_trade_events(ticker);
CREATE INDEX IF NOT EXISTS idx_log_trade_events_regime ON log_trade_events(regime);
