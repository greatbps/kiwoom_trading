-- 종목-업종 매핑 테이블
CREATE TABLE IF NOT EXISTS stock_sector_mapping (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(20) NOT NULL UNIQUE,     -- 종목코드
    stock_name VARCHAR(100),                    -- 종목명
    sector_name VARCHAR(100),                   -- 업종명
    market_type VARCHAR(10),                    -- 시장구분 (0:코스피, 10:코스닥)

    -- 메타데이터
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_stock_code ON stock_sector_mapping(stock_code);
CREATE INDEX IF NOT EXISTS idx_sector_name ON stock_sector_mapping(sector_name);
CREATE INDEX IF NOT EXISTS idx_market_type_mapping ON stock_sector_mapping(market_type);

-- 코멘트
COMMENT ON TABLE stock_sector_mapping IS '종목-업종 매핑 테이블 (ka10099로 수집)';
COMMENT ON COLUMN stock_sector_mapping.stock_code IS '종목코드 (예: 005930)';
COMMENT ON COLUMN stock_sector_mapping.sector_name IS '업종명 (예: 전기/전자)';
COMMENT ON COLUMN stock_sector_mapping.market_type IS '시장구분 (0:코스피, 10:코스닥)';
