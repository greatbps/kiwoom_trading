# Backup and Testing Strategy

**프로젝트**: Kiwoom Trading System
**작성일**: 2025-11-08
**목적**: 안전한 리팩토링을 위한 백업 및 테스트 전략

---

## 🛡️ 백업 전략 (Backup Strategy)

### 1. 사전 백업 (Pre-Refactoring Backup)

#### 1.1 전체 프로젝트 백업

```bash
#!/bin/bash
# scripts/backup_project.sh

# 백업 디렉토리 생성
BACKUP_DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="backups/pre-refactoring-$BACKUP_DATE"
mkdir -p "$BACKUP_DIR"

# 프로젝트 전체 복사 (venv 제외)
rsync -av \
  --exclude='venv/' \
  --exclude='__pycache__/' \
  --exclude='*.pyc' \
  --exclude='.pytest_cache/' \
  --exclude='logs/' \
  --exclude='*.log' \
  . "$BACKUP_DIR/"

# 백업 검증
if [ $? -eq 0 ]; then
    echo "✅ Backup completed: $BACKUP_DIR"

    # 백업 메타데이터 저장
    cat > "$BACKUP_DIR/BACKUP_INFO.txt" << EOF
Backup Date: $BACKUP_DATE
Backup Type: Full Project Backup
Git Commit: $(git rev-parse HEAD)
Git Branch: $(git branch --show-current)
Total Size: $(du -sh "$BACKUP_DIR" | cut -f1)
Files Count: $(find "$BACKUP_DIR" -type f | wc -l)
EOF

else
    echo "❌ Backup failed!"
    exit 1
fi
```

**실행**:
```bash
chmod +x scripts/backup_project.sh
./scripts/backup_project.sh
```

---

#### 1.2 데이터베이스 백업

```bash
#!/bin/bash
# scripts/backup_database.sh

BACKUP_DATE=$(date +%Y%m%d_%H%M%S)
DB_BACKUP_DIR="backups/database"
mkdir -p "$DB_BACKUP_DIR"

# SQLite 데이터베이스 백업
DB_FILES=(
    "database/kiwoom_trading.db"
    "db/trading.db"
    "kiwoom_trading.db"
)

for db_file in "${DB_FILES[@]}"; do
    if [ -f "$db_file" ]; then
        echo "Backing up $db_file..."

        # 파일 복사
        cp "$db_file" "$DB_BACKUP_DIR/$(basename $db_file).$BACKUP_DATE"

        # SQL 덤프
        sqlite3 "$db_file" ".dump" > "$DB_BACKUP_DIR/$(basename $db_file).$BACKUP_DATE.sql"

        echo "✅ Backed up: $db_file"
    fi
done

# 압축
tar -czf "$DB_BACKUP_DIR/db_backup_$BACKUP_DATE.tar.gz" "$DB_BACKUP_DIR"/*.{db,$BACKUP_DATE,sql}
echo "✅ Database backup compressed: db_backup_$BACKUP_DATE.tar.gz"
```

**실행**:
```bash
chmod +x scripts/backup_database.sh
./scripts/backup_database.sh
```

---

#### 1.3 Git 태그 생성

```bash
#!/bin/bash
# scripts/create_git_tag.sh

TAG_NAME="v1.0-pre-refactoring"
TAG_MESSAGE="Snapshot before major refactoring ($(date +%Y-%m-%d))"

# Git 상태 확인
if [ -n "$(git status --porcelain)" ]; then
    echo "⚠️  Uncommitted changes detected. Please commit or stash first."
    git status
    exit 1
fi

# 태그 생성
git tag -a "$TAG_NAME" -m "$TAG_MESSAGE"

if [ $? -eq 0 ]; then
    echo "✅ Git tag created: $TAG_NAME"
    echo "Push tag with: git push origin $TAG_NAME"
else
    echo "❌ Failed to create tag"
    exit 1
fi
```

**실행**:
```bash
chmod +x scripts/create_git_tag.sh
./scripts/create_git_tag.sh
```

---

### 2. 증분 백업 (Incremental Backup)

#### 2.1 일일 백업 (Daily Backup)

```bash
#!/bin/bash
# scripts/daily_backup.sh

BACKUP_DATE=$(date +%Y%m%d)
DAILY_BACKUP_DIR="backups/daily/$BACKUP_DATE"
mkdir -p "$DAILY_BACKUP_DIR"

# 변경된 파일만 백업 (지난 24시간)
find . -type f -mtime -1 \
    -not -path "./venv/*" \
    -not -path "./__pycache__/*" \
    -not -path "./backups/*" \
    -exec cp --parents {} "$DAILY_BACKUP_DIR/" \;

# 데이터베이스는 항상 백업
./scripts/backup_database.sh

echo "✅ Daily backup completed: $DAILY_BACKUP_DIR"
```

**Cron 설정** (매일 자정):
```bash
0 0 * * * cd /home/greatbps/projects/kiwoom_trading && ./scripts/daily_backup.sh
```

---

#### 2.2 Sprint 백업 (Sprint Backup)

각 Sprint 시작 전:
```bash
#!/bin/bash
# scripts/sprint_backup.sh

SPRINT_NUM=$1
if [ -z "$SPRINT_NUM" ]; then
    echo "Usage: ./sprint_backup.sh <sprint_number>"
    exit 1
fi

SPRINT_BACKUP_DIR="backups/sprint_$SPRINT_NUM"
mkdir -p "$SPRINT_BACKUP_DIR"

# 전체 백업
./scripts/backup_project.sh
mv backups/pre-refactoring-* "$SPRINT_BACKUP_DIR/"

# Git 브랜치 생성
git checkout -b "backup/sprint-$SPRINT_NUM"
git add .
git commit -m "Backup before Sprint $SPRINT_NUM"
git checkout -

echo "✅ Sprint $SPRINT_NUM backup completed"
```

**실행**:
```bash
./scripts/sprint_backup.sh 1
```

---

### 3. 백업 보존 정책 (Retention Policy)

| 백업 타입 | 보존 기간 | 저장 위치 |
|----------|----------|----------|
| Pre-Refactoring | 영구 | `backups/pre-refactoring-*` |
| Sprint Backup | 6개월 | `backups/sprint_*` |
| Daily Backup | 30일 | `backups/daily/` |
| Database Backup | 90일 | `backups/database/` |

```bash
#!/bin/bash
# scripts/cleanup_old_backups.sh

# 30일 이상된 일일 백업 삭제
find backups/daily/ -type d -mtime +30 -exec rm -rf {} \;

# 90일 이상된 DB 백업 삭제
find backups/database/ -type f -mtime +90 -delete

echo "✅ Old backups cleaned up"
```

---

### 4. 복원 절차 (Restore Procedure)

#### 4.1 전체 복원

```bash
#!/bin/bash
# scripts/restore_full.sh

BACKUP_DIR=$1

if [ -z "$BACKUP_DIR" ]; then
    echo "Usage: ./restore_full.sh <backup_directory>"
    echo "Example: ./restore_full.sh backups/pre-refactoring-20251108_100000"
    exit 1
fi

if [ ! -d "$BACKUP_DIR" ]; then
    echo "❌ Backup directory not found: $BACKUP_DIR"
    exit 1
fi

# 현재 상태 백업
echo "Creating safety backup of current state..."
./scripts/backup_project.sh

# 복원 실행
echo "Restoring from: $BACKUP_DIR"
rsync -av --delete \
    --exclude='venv/' \
    --exclude='backups/' \
    "$BACKUP_DIR/" .

# 데이터베이스 복원
if [ -f "$BACKUP_DIR/database/kiwoom_trading.db" ]; then
    cp "$BACKUP_DIR/database/kiwoom_trading.db" database/
fi

echo "✅ Restore completed from: $BACKUP_DIR"
echo "⚠️  Please verify functionality before proceeding"
```

---

#### 4.2 Git 태그로 복원

```bash
#!/bin/bash
# scripts/restore_from_tag.sh

TAG_NAME=$1

if [ -z "$TAG_NAME" ]; then
    echo "Usage: ./restore_from_tag.sh <tag_name>"
    echo "Example: ./restore_from_tag.sh v1.0-pre-refactoring"
    exit 1
fi

# 현재 변경사항 확인
if [ -n "$(git status --porcelain)" ]; then
    echo "⚠️  Uncommitted changes detected. Stashing..."
    git stash save "Auto-stash before restore from $TAG_NAME"
fi

# 태그로 체크아웃
git checkout "$TAG_NAME"

echo "✅ Restored to tag: $TAG_NAME"
echo "⚠️  You are now in 'detached HEAD' state"
echo "To return to main branch: git checkout main"
```

---

#### 4.3 데이터베이스만 복원

```bash
#!/bin/bash
# scripts/restore_database.sh

BACKUP_DATE=$1

if [ -z "$BACKUP_DATE" ]; then
    echo "Usage: ./restore_database.sh <YYYYMMDD_HHMMSS>"
    echo "Available backups:"
    ls -1 backups/database/*.db.* | head -10
    exit 1
fi

DB_BACKUP="backups/database/kiwoom_trading.db.$BACKUP_DATE"

if [ ! -f "$DB_BACKUP" ]; then
    echo "❌ Database backup not found: $DB_BACKUP"
    exit 1
fi

# 현재 DB 백업
cp database/kiwoom_trading.db database/kiwoom_trading.db.before_restore

# 복원
cp "$DB_BACKUP" database/kiwoom_trading.db

echo "✅ Database restored from: $DB_BACKUP"
echo "Previous DB saved as: database/kiwoom_trading.db.before_restore"
```

---

## 🧪 테스트 전략 (Testing Strategy)

### 1. 테스트 피라미드

```
        /\
       /  \  E2E (5%)
      /────\
     / Inte \  Integration (15%)
    / gration\
   /──────────\
  /   Unit     \  Unit (80%)
 /    Tests     \
/────────────────\
```

**목표 분포**:
- Unit Tests: 80% (200+ 테스트)
- Integration Tests: 15% (40+ 테스트)
- E2E Tests: 5% (10+ 테스트)

---

### 2. 테스트 환경 구축

#### 2.1 pytest 설정

```ini
# pytest.ini
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*

# Asyncio 지원
asyncio_mode = auto

# 커버리지
addopts =
    --cov=.
    --cov-report=html
    --cov-report=term-missing
    --cov-report=xml
    --cov-fail-under=80
    --ignore=venv
    --ignore=backups
    -v
    --tb=short

# 마커
markers =
    unit: Unit tests
    integration: Integration tests
    e2e: End-to-end tests
    slow: Slow running tests
    api: Tests that call external API
```

---

#### 2.2 Coverage 설정

```ini
# .coveragerc
[run]
source = .
omit =
    */venv/*
    */tests/*
    */backups/*
    */__pycache__/*
    */site-packages/*
    setup.py

[report]
precision = 2
exclude_lines =
    pragma: no cover
    def __repr__
    raise AssertionError
    raise NotImplementedError
    if __name__ == .__main__.:
    if TYPE_CHECKING:
    @abstractmethod

[html]
directory = htmlcov
```

---

#### 2.3 의존성 설치

```bash
# requirements-dev.txt
pytest>=7.4.0
pytest-asyncio>=0.21.0
pytest-cov>=4.1.0
pytest-mock>=3.11.0
pytest-timeout>=2.1.0
pytest-xdist>=3.3.0  # 병렬 테스트
coverage>=7.3.0

# 코드 품질
black>=23.0.0
flake8>=6.0.0
pylint>=2.17.0
mypy>=1.5.0

# Mock 라이브러리
responses>=0.23.0  # HTTP mock
freezegun>=1.2.0   # 시간 mock
```

**설치**:
```bash
pip install -r requirements-dev.txt
```

---

### 3. 테스트 작성 가이드

#### 3.1 Unit Test 예시

```python
# tests/utils/test_stock_data_fetcher.py
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import pandas as pd
from utils.stock_data_fetcher import StockDataFetcher

class TestStockDataFetcher:
    """StockDataFetcher 단위 테스트"""

    @pytest.fixture
    def mock_kiwoom_api(self):
        """Mock Kiwoom API"""
        api = MagicMock()
        api.get_minute_chart = AsyncMock(return_value={
            'stk_min_pole_chart_qry': [
                {'time': '09:00', 'open': 70000, 'high': 71000,
                 'low': 69500, 'close': 70500, 'volume': 1000}
            ]
        })
        return api

    @pytest.fixture
    def fetcher(self, mock_kiwoom_api):
        """Fetcher 인스턴스"""
        return StockDataFetcher(kiwoom_api=mock_kiwoom_api)

    @pytest.mark.asyncio
    async def test_fetch_from_kiwoom_success(self, fetcher):
        """Kiwoom API에서 데이터 수집 성공"""
        # Given
        stock_code = '005930'
        days = 7

        # When
        result = await fetcher.fetch(stock_code, days, source='kiwoom')

        # Then
        assert result is not None
        assert isinstance(result, pd.DataFrame)
        assert len(result) > 0
        assert 'Close' in result.columns

    @pytest.mark.asyncio
    async def test_fetch_from_kiwoom_failure(self, fetcher):
        """Kiwoom API 실패 시 None 반환"""
        # Given
        fetcher.kiwoom_api.get_minute_chart = AsyncMock(return_value=None)

        # When
        result = await fetcher.fetch('005930', 7, source='kiwoom')

        # Then
        assert result is None

    @pytest.mark.asyncio
    @patch('yfinance.Ticker')
    async def test_fetch_from_yahoo_success(self, mock_ticker, fetcher):
        """Yahoo Finance에서 데이터 수집 성공"""
        # Given
        mock_data = pd.DataFrame({
            'Open': [70000],
            'High': [71000],
            'Low': [69500],
            'Close': [70500],
            'Volume': [1000]
        })
        mock_ticker.return_value.history.return_value = mock_data

        # When
        result = await fetcher.fetch('005930', 7, source='yahoo')

        # Then
        assert result is not None
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_fetch_auto_fallback(self, fetcher):
        """Kiwoom 실패 시 Yahoo로 fallback"""
        # Given
        fetcher.kiwoom_api.get_minute_chart = AsyncMock(return_value=None)

        with patch('yfinance.Ticker') as mock_ticker:
            mock_data = pd.DataFrame({
                'Close': [70000],
                'Volume': [1000]
            })
            mock_ticker.return_value.history.return_value = mock_data

            # When
            result = await fetcher.fetch('005930', 7, source='auto')

            # Then
            assert result is not None
            # Yahoo가 호출되었는지 확인
            assert mock_ticker.called
```

---

#### 3.2 Integration Test 예시

```python
# tests/integration/test_trading_flow.py
import pytest
import asyncio
from trading.trading_coordinator import TradingCoordinator
from kiwoom_api import KiwoomAPI
from config.env_config import env

@pytest.mark.integration
@pytest.mark.asyncio
class TestTradingFlow:
    """거래 흐름 통합 테스트"""

    @pytest.fixture
    async def coordinator(self):
        """실제 Coordinator 인스턴스 (테스트 모드)"""
        credentials = {
            'app_key': env.KIWOOM_APP_KEY,
            'app_secret': env.KIWOOM_APP_SECRET,
            'account_no': 'TEST_ACCOUNT'  # 테스트 계좌
        }

        coordinator = TradingCoordinator(credentials)
        yield coordinator

        # Cleanup
        await coordinator.stop()

    @pytest.mark.slow
    async def test_full_trading_cycle(self, coordinator):
        """전체 거래 사이클 테스트"""
        # 1. 시스템 시작
        await coordinator.start()
        assert coordinator.running is True

        # 2. Watchlist 생성
        await asyncio.sleep(5)  # 필터링 대기
        assert len(coordinator.watchlist) > 0

        # 3. 매수 주문
        stock_code = coordinator.watchlist[0]
        order = await coordinator.execute_trade(stock_code, quantity=1)
        assert order is not None
        assert order.order_id is not None

        # 4. 포지션 확인
        await asyncio.sleep(2)
        positions = coordinator.monitoring_service.positions
        assert stock_code in positions

        # 5. 시스템 종료
        await coordinator.stop()
        assert coordinator.running is False
```

---

#### 3.3 E2E Test 예시

```python
# tests/e2e/test_auto_trading_system.py
import pytest
import subprocess
import time
import requests

@pytest.mark.e2e
@pytest.mark.slow
class TestAutoTradingSystem:
    """자동매매 시스템 E2E 테스트"""

    def test_system_startup_and_shutdown(self):
        """시스템 시작 및 종료"""
        # 시스템 시작
        process = subprocess.Popen(
            ['python', 'trading/main.py'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )

        # 시작 대기
        time.sleep(10)

        # 헬스체크
        response = requests.get('http://localhost:8000/health')
        assert response.status_code == 200
        assert response.json()['status'] == 'healthy'

        # 종료
        process.terminate()
        process.wait(timeout=5)

        assert process.returncode == 0

    def test_end_to_end_trading(self):
        """End-to-End 거래 테스트 (Mock 시장)"""
        # 1. 시스템 시작
        # 2. 조건 필터링 실행
        # 3. VWAP 검증
        # 4. 매수 주문
        # 5. 포지션 모니터링
        # 6. 손절/익절 확인
        # 7. 시스템 종료
        pass  # 구현 필요
```

---

### 4. 회귀 테스트 (Regression Testing)

#### 4.1 회귀 테스트 스위트

```python
# tests/regression/test_core_functionality.py
import pytest
from trading.trading_coordinator import TradingCoordinator
from utils.stock_data_fetcher import StockDataFetcher

@pytest.mark.regression
class TestCoreRegression:
    """핵심 기능 회귀 테스트"""

    def test_data_fetching_still_works(self):
        """데이터 수집 기능 정상 동작"""
        fetcher = StockDataFetcher()
        # 기존 동작 확인
        pass

    def test_order_execution_still_works(self):
        """주문 실행 기능 정상 동작"""
        # 기존 동작 확인
        pass

    def test_monitoring_still_works(self):
        """모니터링 기능 정상 동작"""
        # 기존 동작 확인
        pass
```

**실행**:
```bash
# 회귀 테스트만 실행
pytest -m regression

# 각 리팩토링 후 자동 실행
./scripts/run_regression_tests.sh
```

---

#### 4.2 자동 회귀 테스트 스크립트

```bash
#!/bin/bash
# scripts/run_regression_tests.sh

echo "🧪 Running Regression Tests..."

# 1. 회귀 테스트 실행
pytest -m regression -v --tb=short

if [ $? -ne 0 ]; then
    echo "❌ Regression tests FAILED!"
    echo "⚠️  Do NOT proceed with refactoring"
    exit 1
fi

# 2. 커버리지 체크
pytest --cov=. --cov-fail-under=80

if [ $? -ne 0 ]; then
    echo "❌ Coverage below 80%"
    exit 1
fi

echo "✅ All regression tests PASSED"
```

---

### 5. 성능 테스트 (Performance Testing)

#### 5.1 벤치마크 테스트

```python
# tests/performance/test_benchmarks.py
import pytest
import time
from utils.stock_data_fetcher import StockDataFetcher

@pytest.mark.benchmark
class TestPerformance:
    """성능 벤치마크"""

    def test_data_fetch_performance(self, benchmark):
        """데이터 수집 성능"""
        fetcher = StockDataFetcher()

        # 벤치마크 실행
        result = benchmark(fetcher.fetch, '005930', 7)

        # 성능 기준
        assert benchmark.stats['mean'] < 2.0  # 평균 2초 이내

    @pytest.mark.asyncio
    async def test_concurrent_fetches(self):
        """동시 데이터 수집 성능"""
        import asyncio

        fetcher = StockDataFetcher()
        stock_codes = ['005930', '000660', '035420']

        start = time.time()
        tasks = [fetcher.fetch(code, 7) for code in stock_codes]
        results = await asyncio.gather(*tasks)
        duration = time.time() - start

        # 병렬 처리로 3초 이내
        assert duration < 3.0
        assert all(r is not None for r in results)
```

**실행**:
```bash
pip install pytest-benchmark
pytest tests/performance/ --benchmark-only
```

---

### 6. 테스트 실행 전략

#### 6.1 로컬 개발

```bash
# 빠른 테스트 (Unit만)
pytest -m unit

# 특정 파일 테스트
pytest tests/utils/test_stock_data_fetcher.py

# 특정 테스트 함수
pytest tests/utils/test_stock_data_fetcher.py::test_fetch_success

# 병렬 실행 (빠름)
pytest -n auto
```

---

#### 6.2 Pre-Commit Hook

```bash
# .git/hooks/pre-commit
#!/bin/bash

echo "Running pre-commit tests..."

# Unit 테스트만 실행 (빠름)
pytest -m unit --tb=short

if [ $? -ne 0 ]; then
    echo "❌ Tests failed. Commit aborted."
    exit 1
fi

# 코드 포맷 체크
black --check .

if [ $? -ne 0 ]; then
    echo "❌ Code formatting failed. Run 'black .'"
    exit 1
fi

echo "✅ Pre-commit checks passed"
```

**설치**:
```bash
chmod +x .git/hooks/pre-commit
```

---

#### 6.3 CI 파이프라인

```yaml
# .github/workflows/test.yml
name: Tests

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest

    steps:
      - uses: actions/checkout@v2

      - name: Set up Python
        uses: actions/setup-python@v2
        with:
          python-version: 3.12

      - name: Install dependencies
        run: |
          pip install -r requirements.txt
          pip install -r requirements-dev.txt

      - name: Run Unit Tests
        run: pytest -m unit --cov=. --cov-report=xml

      - name: Run Integration Tests
        run: pytest -m integration

      - name: Run Regression Tests
        run: pytest -m regression

      - name: Upload Coverage
        uses: codecov/codecov-action@v2
        with:
          files: ./coverage.xml
```

---

### 7. 테스트 데이터 관리

#### 7.1 Fixture 데이터

```python
# tests/fixtures/stock_data.py
import pandas as pd
from datetime import datetime, timedelta

def create_sample_ohlcv(days=7):
    """샘플 OHLCV 데이터 생성"""
    dates = pd.date_range(end=datetime.now(), periods=days, freq='D')

    return pd.DataFrame({
        'Open': [70000 + i*100 for i in range(days)],
        'High': [71000 + i*100 for i in range(days)],
        'Low': [69000 + i*100 for i in range(days)],
        'Close': [70500 + i*100 for i in range(days)],
        'Volume': [1000000 + i*10000 for i in range(days)]
    }, index=dates)

def create_sample_trade():
    """샘플 거래 데이터"""
    return {
        'stock_code': '005930',
        'quantity': 10,
        'entry_price': 70000,
        'exit_price': 72000,
        'profit': 20000,
        'profit_rate': 2.86
    }
```

**사용**:
```python
# tests/test_example.py
from tests.fixtures.stock_data import create_sample_ohlcv

def test_with_sample_data():
    data = create_sample_ohlcv(days=10)
    assert len(data) == 10
```

---

#### 7.2 Mock 데이터베이스

```python
# tests/conftest.py
import pytest
import sqlite3
from pathlib import Path

@pytest.fixture
def temp_db(tmp_path):
    """임시 테스트 데이터베이스"""
    db_path = tmp_path / "test.db"

    # 스키마 생성
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE stocks (
            code TEXT PRIMARY KEY,
            name TEXT,
            market TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            stock_code TEXT,
            quantity INTEGER,
            price REAL,
            created_at TIMESTAMP
        )
    """)

    conn.commit()
    conn.close()

    yield str(db_path)

    # Cleanup
    db_path.unlink()
```

---

### 8. 테스트 결과 리포팅

#### 8.1 HTML 리포트

```bash
# 커버리지 HTML 리포트 생성
pytest --cov=. --cov-report=html

# 리포트 열기
open htmlcov/index.html
```

---

#### 8.2 주간 테스트 리포트

```bash
#!/bin/bash
# scripts/generate_test_report.sh

REPORT_DATE=$(date +%Y%m%d)
REPORT_FILE="reports/test_report_$REPORT_DATE.md"

mkdir -p reports

# 테스트 실행 및 결과 수집
pytest --cov=. --cov-report=term > temp_report.txt

# 마크다운 리포트 생성
cat > "$REPORT_FILE" << EOF
# Test Report - $REPORT_DATE

## Summary

\`\`\`
$(cat temp_report.txt)
\`\`\`

## Coverage

- Total Coverage: $(grep "TOTAL" temp_report.txt | awk '{print $4}')

## Failed Tests

$(grep "FAILED" temp_report.txt || echo "None")

## Recommendations

- [ ] Increase coverage for modules below 80%
- [ ] Fix all failed tests
- [ ] Review slow tests (>1s)

EOF

rm temp_report.txt
echo "✅ Test report generated: $REPORT_FILE"
```

---

## 📊 체크리스트

### 백업 체크리스트

- [ ] 전체 프로젝트 백업 완료
- [ ] 데이터베이스 백업 완료
- [ ] Git 태그 생성
- [ ] 백업 검증 완료
- [ ] 복원 테스트 완료

### 테스트 체크리스트

- [ ] pytest 환경 구축
- [ ] Unit 테스트 작성 (커버리지 > 80%)
- [ ] Integration 테스트 작성
- [ ] 회귀 테스트 스위트 준비
- [ ] Pre-commit hook 설정
- [ ] CI 파이프라인 구축

---

**작성자**: Claude Code Assistant
**버전**: 1.0
**최종 업데이트**: 2025-11-08
