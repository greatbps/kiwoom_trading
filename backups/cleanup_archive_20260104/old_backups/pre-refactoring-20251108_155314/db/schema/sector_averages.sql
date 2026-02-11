-- 업종별 평균 지표 테이블
CREATE TABLE IF NOT EXISTS sector_averages (
    id SERIAL PRIMARY KEY,
    sector_code VARCHAR(20) NOT NULL,           -- 업종 코드
    sector_name VARCHAR(100) NOT NULL,          -- 업종명
    market_type VARCHAR(10) NOT NULL,           -- 시장구분 (0:코스피, 10:코스닥 등)

    -- 평균 지표
    avg_per DECIMAL(10, 2),                     -- 평균 PER
    avg_pbr DECIMAL(10, 2),                     -- 평균 PBR
    avg_roe DECIMAL(10, 2),                     -- 평균 ROE
    avg_eps DECIMAL(10, 2),                     -- 평균 EPS
    avg_bps DECIMAL(10, 2),                     -- 평균 BPS

    -- 통계 정보
    stock_count INTEGER,                        -- 업종 내 종목 수
    valid_per_count INTEGER,                    -- PER 유효 종목 수
    valid_pbr_count INTEGER,                    -- PBR 유효 종목 수
    valid_roe_count INTEGER,                    -- ROE 유효 종목 수

    -- 메타데이터
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 갱신일시
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,  -- 생성일시

    -- 유니크 제약: 같은 업종/시장은 하나만
    UNIQUE(sector_code, market_type)
);

-- 인덱스 생성
CREATE INDEX IF NOT EXISTS idx_sector_code ON sector_averages(sector_code);
CREATE INDEX IF NOT EXISTS idx_market_type ON sector_averages(market_type);
CREATE INDEX IF NOT EXISTS idx_updated_at ON sector_averages(updated_at);

-- 코멘트
COMMENT ON TABLE sector_averages IS '업종별 평균 재무지표 (월 1회 갱신)';
COMMENT ON COLUMN sector_averages.sector_code IS '업종 코드';
COMMENT ON COLUMN sector_averages.sector_name IS '업종명';
COMMENT ON COLUMN sector_averages.avg_per IS '업종 평균 PER (Price to Earnings Ratio)';
COMMENT ON COLUMN sector_averages.avg_pbr IS '업종 평균 PBR (Price to Book Ratio)';
COMMENT ON COLUMN sector_averages.avg_roe IS '업종 평균 ROE (Return on Equity, %)';
COMMENT ON COLUMN sector_averages.updated_at IS '마지막 갱신 일시';
