# Kiwoom Trading System - 전체 리팩토링 마스터 플랜

**작성일**: 2025-11-08
**프로젝트**: Kiwoom Trading Automation System
**목적**: 체계적이고 단계적인 코드 품질 개선 및 유지보수성 향상

---

## 📊 현재 상태 분석

### 프로젝트 규모
- **총 Python 파일**: 183개 (venv 제외)
- **핵심 코드 라인**: 13,761 lines (4개 주요 파일)
- **주요 디렉토리**: 28개

### Critical Issues (즉시 조치 필요)
| 파일 | 라인 수 | 문제점 | 우선순위 |
|------|---------|--------|----------|
| `main_auto_trading.py` | 2,767 | 단일 책임 원칙 위반, God Class | 🔴 P0 |
| `core/db_auto_trading_handler.py` | 4,150 | God Object, 테스트 불가능 | 🔴 P0 |
| `core/menu_handlers.py` | 3,771 | 200KB 파일, 응집도 낮음 | 🔴 P0 |
| `core/trading_system.py` | 3,073 | 143KB, 복잡한 의존성 | 🔴 P0 |

### 코드 품질 메트릭
- ✅ **테스트 존재**: tests/ 디렉토리 확인
- ❌ **테스트 커버리지**: 미측정 (예상 < 30%)
- ❌ **Type Hints**: 부분적 사용
- ❌ **Documentation**: 최소한의 주석
- ⚠️ **중복 코드**: 10+ 인스턴스 확인됨

---

## 🎯 리팩토링 목표

### Phase 1: 안정화 (Stabilization)
**목표**: 현재 기능 보존하면서 기반 구조 정비
**기간**: 2-3주

### Phase 2: 구조 개선 (Restructuring)
**목표**: 아키텍처 패턴 적용 및 모듈 분리
**기간**: 4-6주

### Phase 3: 품질 향상 (Quality Enhancement)
**목표**: 테스트, 문서화, 성능 최적화
**기간**: 3-4주

### Phase 4: 고도화 (Advanced Features)
**목표**: 확장성, 모니터링, CI/CD
**기간**: 2-3주

**총 예상 기간**: 11-16주 (약 3-4개월)

---

## 📅 Phase 1: 안정화 (Week 1-3)

### Sprint 1.1: 백업 및 환경 설정 (3일)

#### Task 1.1.1: 전체 프로젝트 백업
```bash
# 백업 디렉토리 생성
mkdir -p backups/pre-refactoring-$(date +%Y%m%d)
cp -r . backups/pre-refactoring-$(date +%Y%m%d)/

# Git 태그 생성 (있을 경우)
git tag -a v1.0-pre-refactoring -m "Before major refactoring"
```

**결과물**:
- [ ] 완전한 코드 백업
- [ ] Git 태그 생성
- [ ] 백업 복원 테스트 완료

#### Task 1.1.2: 의존성 관리 개선
```bash
# 현재 의존성 고정
pip freeze > requirements-frozen.txt

# 개발 의존성 분리
echo "pytest>=7.4.0
pytest-asyncio>=0.21.0
pytest-cov>=4.1.0
black>=23.0.0
flake8>=6.0.0
mypy>=1.5.0
pylint>=2.17.0" > requirements-dev.txt
```

**결과물**:
- [ ] `requirements.txt` - 운영 의존성
- [ ] `requirements-dev.txt` - 개발 의존성
- [ ] `requirements-frozen.txt` - 정확한 버전 고정

#### Task 1.1.3: 테스트 환경 구축
```bash
# pytest 설정
cat > pytest.ini << EOF
[pytest]
testpaths = tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
asyncio_mode = auto
addopts =
    --cov=.
    --cov-report=html
    --cov-report=term-missing
    --ignore=venv
EOF
```

**결과물**:
- [ ] `pytest.ini` 설정
- [ ] `.coveragerc` 설정
- [ ] 테스트 실행 확인

---

### Sprint 1.2: 중복 코드 제거 (5일)

#### Task 1.2.1: 데이터 수집 통합
**현재 문제**: `download_stock_data_sync()` 함수가 3개 파일에 중복

**리팩토링 계획**:
```python
# 새 파일: utils/stock_data_fetcher.py
from typing import Optional, Literal
import pandas as pd
import yfinance as yf
from kiwoom_api import KiwoomAPI

class StockDataFetcher:
    """통합 주식 데이터 수집 클래스"""

    def __init__(self, kiwoom_api: Optional[KiwoomAPI] = None):
        self.kiwoom_api = kiwoom_api

    async def fetch(
        self,
        stock_code: str,
        days: int = 7,
        source: Literal['auto', 'kiwoom', 'yahoo'] = 'auto'
    ) -> Optional[pd.DataFrame]:
        """
        주식 데이터 수집 (우선순위: Kiwoom -> Yahoo .KS -> Yahoo .KQ)

        Args:
            stock_code: 종목 코드 (6자리)
            days: 조회 일수
            source: 데이터 소스 ('auto'=자동선택)

        Returns:
            OHLCV 데이터프레임 또는 None
        """
        if source == 'auto':
            # 1. Kiwoom API 시도
            if self.kiwoom_api:
                data = await self._fetch_from_kiwoom(stock_code, days)
                if data is not None and len(data) > 0:
                    return data

            # 2. Yahoo Finance 시도
            return await self._fetch_from_yahoo(stock_code, days)

        elif source == 'kiwoom':
            return await self._fetch_from_kiwoom(stock_code, days)

        elif source == 'yahoo':
            return await self._fetch_from_yahoo(stock_code, days)

    async def _fetch_from_kiwoom(
        self,
        stock_code: str,
        days: int
    ) -> Optional[pd.DataFrame]:
        """Kiwoom API에서 데이터 수집"""
        # 기존 get_kiwoom_minute_data() 로직 통합
        pass

    async def _fetch_from_yahoo(
        self,
        stock_code: str,
        days: int
    ) -> Optional[pd.DataFrame]:
        """Yahoo Finance에서 데이터 수집"""
        # 기존 download_stock_data_sync() 로직 통합
        # .KS -> .KQ 순서로 시도
        pass
```

**적용 범위**:
1. `main_auto_trading.py:50-81` 제거
2. `main_condition_filter.py:49-74` 제거
3. `analyzers/entry_timing_analyzer.py` 수정

**테스트**:
```python
# tests/utils/test_stock_data_fetcher.py
import pytest
from utils.stock_data_fetcher import StockDataFetcher

@pytest.mark.asyncio
async def test_fetch_from_yahoo():
    fetcher = StockDataFetcher()
    data = await fetcher.fetch('005930', days=5, source='yahoo')
    assert data is not None
    assert len(data) > 0
    assert 'Close' in data.columns
```

**결과물**:
- [ ] `utils/stock_data_fetcher.py` 생성
- [ ] 3개 파일에서 중복 코드 제거
- [ ] 단위 테스트 작성 (커버리지 > 80%)
- [ ] 기존 기능 동작 확인

---

#### Task 1.2.2: 검증 로직 통합
**현재 문제**: `validate_stock_for_trading()` 함수 중복

**리팩토링 계획**:
```python
# 새 파일: validators/stock_validator.py
from dataclasses import dataclass
from typing import Optional
import pandas as pd

@dataclass
class ValidationResult:
    """검증 결과"""
    is_valid: bool
    reason: Optional[str] = None
    data: Optional[pd.DataFrame] = None

class StockValidator:
    """주식 거래 검증 클래스"""

    def __init__(self, config: dict):
        self.min_data_points = config.get('min_data_points', 100)
        self.min_volume = config.get('min_volume', 1000)

    async def validate_for_trading(
        self,
        stock_code: str,
        data: pd.DataFrame
    ) -> ValidationResult:
        """
        거래 가능 여부 검증

        Checks:
        1. 데이터 충분성 (최소 데이터 포인트)
        2. 거래량 충족
        3. 가격 이상치 확인
        4. VWAP 계산 가능성
        """
        # 1. 데이터 충분성
        if len(data) < self.min_data_points:
            return ValidationResult(
                is_valid=False,
                reason=f"Insufficient data: {len(data)} < {self.min_data_points}"
            )

        # 2. 거래량 확인
        avg_volume = data['Volume'].mean()
        if avg_volume < self.min_volume:
            return ValidationResult(
                is_valid=False,
                reason=f"Low volume: {avg_volume} < {self.min_volume}"
            )

        # 3. 가격 이상치
        if (data['Close'] <= 0).any():
            return ValidationResult(
                is_valid=False,
                reason="Negative or zero prices detected"
            )

        return ValidationResult(is_valid=True, data=data)
```

**결과물**:
- [ ] `validators/stock_validator.py` 생성
- [ ] 중복 검증 로직 통합
- [ ] 테스트 작성

---

#### Task 1.2.3: WebSocket 연결 관리 통합
**현재 문제**: WebSocket 연결 로직이 2개 클래스에 중복

**리팩토링 계획**:
```python
# 새 파일: core/websocket_manager.py
import asyncio
import websockets
import json
from typing import Optional, Callable, Any

class WebSocketManager:
    """WebSocket 연결 관리자"""

    def __init__(self, url: str):
        self.url = url
        self.ws: Optional[websockets.WebSocketClientProtocol] = None
        self.is_connected = False

    async def connect(self) -> bool:
        """WebSocket 연결"""
        try:
            self.ws = await websockets.connect(self.url)
            self.is_connected = True
            return True
        except Exception as e:
            print(f"Connection failed: {e}")
            return False

    async def disconnect(self):
        """WebSocket 연결 종료"""
        if self.ws:
            await self.ws.close()
            self.is_connected = False

    async def send_message(self, message: dict) -> bool:
        """메시지 전송"""
        if not self.is_connected:
            return False

        try:
            await self.ws.send(json.dumps(message))
            return True
        except Exception as e:
            print(f"Send failed: {e}")
            return False

    async def receive_message(self) -> Optional[dict]:
        """메시지 수신"""
        if not self.is_connected:
            return None

        try:
            message = await self.ws.recv()
            return json.loads(message)
        except Exception as e:
            print(f"Receive failed: {e}")
            return None

    async def login(self, credentials: dict) -> bool:
        """로그인"""
        login_msg = {
            "header": {"function": "login"},
            "body": credentials
        }

        if not await self.send_message(login_msg):
            return False

        response = await self.receive_message()
        return response and response.get('body', {}).get('result') == 'success'
```

**적용 범위**:
1. `main_auto_trading.py:IntegratedTradingSystem` 수정
2. `main_condition_filter.py:KiwoomVWAPPipeline` 수정

**결과물**:
- [ ] `core/websocket_manager.py` 생성
- [ ] 2개 클래스에서 중복 제거
- [ ] 연결 재시도 로직 추가
- [ ] 테스트 작성 (mock WebSocket)

---

### Sprint 1.3: 설정 관리 개선 (3일)

#### Task 1.3.1: Magic Numbers 제거
**현재 문제**: 하드코딩된 설정값 (lookback_days=10, min_win_rate=40.0 등)

**리팩토링 계획**:
```yaml
# 새 파일: config/trading_config.yaml
trading:
  # VWAP 검증 설정
  vwap_validation:
    lookback_days: 10        # 과거 N일 시뮬레이션
    min_trades: 6            # 최소 거래 횟수 (통계적 유의성)
    min_win_rate: 40.0       # 최소 승률 (%)
    min_avg_profit: 0.3      # 최소 평균 수익률 (%)
    min_profit_factor: 1.15  # 최소 수익 팩터

  # 리스크 관리
  risk_management:
    max_position_size: 0.1   # 계좌 대비 최대 포지션 비율 (10%)
    max_daily_loss: 0.05     # 일일 최대 손실 비율 (5%)
    stop_loss: 0.03          # 손절 비율 (3%)
    take_profit: 0.05        # 익절 비율 (5%)

  # 모니터링
  monitoring:
    check_interval: 60       # 모니터링 주기 (초)
    max_retries: 3           # API 실패 시 재시도 횟수
    timeout: 30              # API 타임아웃 (초)

data:
  # 데이터 수집
  fetching:
    default_days: 7          # 기본 조회 일수
    min_data_points: 100     # 최소 데이터 포인트
    cache_ttl: 300           # 캐시 유지 시간 (초)

  # Yahoo Finance
  yahoo:
    suffixes: ['.KS', '.KQ'] # 시도할 suffix 순서
    retry_delay: 1           # 재시도 대기 시간 (초)

database:
  path: './database/kiwoom_trading.db'
  backup_interval: 3600      # 백업 주기 (초)

logging:
  level: 'INFO'
  format: '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
  file: './logs/trading.log'
  max_size: 10485760         # 10MB
  backup_count: 5
```

```python
# 새 파일: config/config_loader.py
import yaml
from pathlib import Path
from typing import Any, Dict

class ConfigLoader:
    """설정 파일 로더"""

    _instance = None
    _config = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def load(self, config_path: str = 'config/trading_config.yaml') -> Dict[str, Any]:
        """설정 파일 로드 (Singleton)"""
        if self._config is None:
            path = Path(config_path)
            if not path.exists():
                raise FileNotFoundError(f"Config file not found: {config_path}")

            with open(path, 'r', encoding='utf-8') as f:
                self._config = yaml.safe_load(f)

        return self._config

    def get(self, key_path: str, default: Any = None) -> Any:
        """
        중첩된 키 값 가져오기

        Example:
            config.get('trading.vwap_validation.lookback_days')
        """
        if self._config is None:
            self.load()

        keys = key_path.split('.')
        value = self._config

        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default

        return value if value is not None else default

# 전역 인스턴스
config = ConfigLoader()
```

**사용 예시**:
```python
# Before
self.validator = PreTradeValidator(
    config=self.config,
    lookback_days=10,
    min_trades=6,
    min_win_rate=40.0
)

# After
from config.config_loader import config

self.validator = PreTradeValidator(
    config=self.config,
    lookback_days=config.get('trading.vwap_validation.lookback_days'),
    min_trades=config.get('trading.vwap_validation.min_trades'),
    min_win_rate=config.get('trading.vwap_validation.min_win_rate')
)

# 또는 더 간단하게
vwap_config = config.get('trading.vwap_validation')
self.validator = PreTradeValidator(config=self.config, **vwap_config)
```

**적용 범위**:
1. `main_auto_trading.py` - 모든 magic numbers
2. `main_condition_filter.py` - 설정값
3. `analyzers/` - 분석기 파라미터

**결과물**:
- [ ] `config/trading_config.yaml` 생성
- [ ] `config/config_loader.py` 생성
- [ ] 모든 magic numbers 제거
- [ ] 환경별 설정 지원 (dev/prod)
- [ ] 테스트 작성

---

#### Task 1.3.2: 환경 변수 관리 개선
```python
# config/env_config.py
from pydantic_settings import BaseSettings
from typing import Optional

class EnvironmentConfig(BaseSettings):
    """환경 변수 설정"""

    # Kiwoom API
    KIWOOM_APP_KEY: str
    KIWOOM_APP_SECRET: str
    KIWOOM_ACCOUNT_NO: Optional[str] = None

    # Database
    DATABASE_PATH: str = './database/kiwoom_trading.db'

    # Logging
    LOG_LEVEL: str = 'INFO'
    LOG_FILE: str = './logs/trading.log'

    # WebSocket
    WEBSOCKET_URL: str = 'wss://openapi.kiwoom.com:9443/websocket'
    WEBSOCKET_TIMEOUT: int = 30

    # Environment
    ENVIRONMENT: str = 'development'  # development, production
    DEBUG: bool = False

    class Config:
        env_file = '.env'
        env_file_encoding = 'utf-8'

# 전역 인스턴스
env = EnvironmentConfig()
```

**결과물**:
- [ ] `config/env_config.py` 생성
- [ ] `.env.example` 템플릿
- [ ] 환경 변수 문서화

---

### Sprint 1.4: 에러 처리 표준화 (4일)

#### Task 1.4.1: 커스텀 예외 정의
```python
# 새 파일: exceptions/trading_exceptions.py
class TradingException(Exception):
    """거래 관련 기본 예외"""
    pass

class APIException(TradingException):
    """API 호출 예외"""
    def __init__(self, message: str, status_code: int = None):
        self.status_code = status_code
        super().__init__(message)

class InsufficientFundsError(TradingException):
    """잔고 부족"""
    pass

class InvalidStockCodeError(TradingException):
    """유효하지 않은 종목 코드"""
    pass

class OrderFailedError(TradingException):
    """주문 실패"""
    def __init__(self, message: str, order_id: str = None):
        self.order_id = order_id
        super().__init__(message)

class DataValidationError(TradingException):
    """데이터 검증 실패"""
    pass

class ConnectionError(TradingException):
    """WebSocket/API 연결 실패"""
    pass

class TimeoutError(TradingException):
    """타임아웃"""
    pass

class AuthenticationError(TradingException):
    """인증 실패"""
    pass
```

**결과물**:
- [ ] `exceptions/trading_exceptions.py` 생성
- [ ] 모든 예외 문서화
- [ ] 예외 계층 구조 정의

---

#### Task 1.4.2: 에러 핸들러 구현
```python
# 새 파일: exceptions/error_handler.py
import logging
from typing import Callable, Any
from functools import wraps
from exceptions.trading_exceptions import *

logger = logging.getLogger(__name__)

def handle_api_errors(func: Callable) -> Callable:
    """API 호출 에러 처리 데코레이터"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except requests.HTTPError as e:
            if e.response.status_code == 401:
                raise AuthenticationError("API authentication failed")
            elif e.response.status_code == 429:
                raise APIException("Rate limit exceeded", status_code=429)
            else:
                raise APIException(f"API error: {e}", status_code=e.response.status_code)
        except requests.Timeout:
            raise TimeoutError(f"API timeout: {func.__name__}")
        except Exception as e:
            logger.error(f"Unexpected error in {func.__name__}: {e}")
            raise TradingException(f"Unexpected error: {e}")

    return wrapper

def handle_trading_errors(func: Callable) -> Callable:
    """거래 에러 처리 데코레이터"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except InsufficientFundsError as e:
            logger.warning(f"Insufficient funds: {e}")
            # Telegram 알림 등 추가 처리
            raise
        except OrderFailedError as e:
            logger.error(f"Order failed: {e}")
            # 주문 실패 로깅, 알림
            raise
        except Exception as e:
            logger.error(f"Trading error in {func.__name__}: {e}")
            raise TradingException(f"Trading error: {e}")

    return wrapper

# 사용 예시
class KiwoomAPI:
    @handle_api_errors
    async def order_buy(self, stock_code: str, quantity: int, price: int):
        response = await self.session.post(...)
        response.raise_for_status()

        result = response.json()
        if result.get('return_code') != 0:
            raise OrderFailedError(
                result.get('return_msg'),
                order_id=result.get('order_id')
            )

        return result
```

**결과물**:
- [ ] `exceptions/error_handler.py` 생성
- [ ] 모든 API 호출에 데코레이터 적용
- [ ] 에러 로깅 표준화

---

## 📅 Phase 2: 구조 개선 (Week 4-9)

### Sprint 2.1: main_auto_trading.py 분리 (10일)

#### Task 2.1.1: 아키텍처 설계
**목표**: 2,767 라인을 8-10개 모듈로 분리

**새로운 구조**:
```
trading/
├── __init__.py
├── main.py                      # 진입점 (< 100 lines)
├── websocket_manager.py         # WebSocket 연결 관리
├── condition_filter.py          # 조건 필터링
├── vwap_validator.py            # VWAP 검증
├── position_manager.py          # 포지션 관리
├── order_executor.py            # 주문 실행
├── monitoring_service.py        # 모니터링
└── trading_coordinator.py       # 전체 오케스트레이션
```

**의존성 다이어그램**:
```
main.py
  └── trading_coordinator.py
        ├── websocket_manager.py
        ├── condition_filter.py
        ├── vwap_validator.py
        ├── position_manager.py
        ├── order_executor.py
        └── monitoring_service.py
```

**결과물**:
- [ ] 아키텍처 설계 문서
- [ ] 모듈 책임 정의
- [ ] 인터페이스 정의

---

#### Task 2.1.2: 모듈별 구현

##### 2.1.2.1: websocket_manager.py
```python
# trading/websocket_manager.py
from core.websocket_manager import WebSocketManager
from typing import Optional, Callable
import asyncio

class TradingWebSocketManager(WebSocketManager):
    """거래용 WebSocket 관리자"""

    def __init__(self, url: str, credentials: dict):
        super().__init__(url)
        self.credentials = credentials
        self.subscriptions = set()

    async def start(self) -> bool:
        """WebSocket 시작 및 로그인"""
        if not await self.connect():
            return False

        if not await self.login(self.credentials):
            await self.disconnect()
            return False

        return True

    async def subscribe_price(self, stock_code: str, callback: Callable):
        """실시간 가격 구독"""
        subscribe_msg = {
            "header": {"function": "subscribe"},
            "body": {
                "type": "price",
                "code": stock_code
            }
        }

        if await self.send_message(subscribe_msg):
            self.subscriptions.add(stock_code)
            # 콜백 등록 로직
```

**라인 수**: ~200 lines
**책임**: WebSocket 연결, 구독 관리, 메시지 라우팅

---

##### 2.1.2.2: condition_filter.py
```python
# trading/condition_filter.py
from typing import List, Dict
from analyzers.pre_trade_validator import PreTradeValidator
from utils.stock_data_fetcher import StockDataFetcher
import pandas as pd

class ConditionFilter:
    """조건 필터링 서비스"""

    def __init__(self, validator: PreTradeValidator, fetcher: StockDataFetcher):
        self.validator = validator
        self.fetcher = fetcher
        self.filtered_stocks = []

    async def run_filtering(
        self,
        stock_universe: List[str],
        conditions: Dict
    ) -> List[str]:
        """
        조건 필터링 실행

        Args:
            stock_universe: 전체 종목 리스트
            conditions: 필터 조건

        Returns:
            필터링된 종목 코드 리스트
        """
        filtered = []

        for stock_code in stock_universe:
            if await self._check_conditions(stock_code, conditions):
                filtered.append(stock_code)

        self.filtered_stocks = filtered
        return filtered

    async def _check_conditions(
        self,
        stock_code: str,
        conditions: Dict
    ) -> bool:
        """개별 종목 조건 검사"""
        # 1. 데이터 수집
        data = await self.fetcher.fetch(stock_code)
        if data is None:
            return False

        # 2. 검증
        validation = await self.validator.validate_for_trading(stock_code, data)
        if not validation.is_valid:
            return False

        # 3. 추가 조건 검사
        # (기술적 분석, 거래량 등)

        return True
```

**라인 수**: ~250 lines
**책임**: 조건 필터링, 종목 스크리닝

---

##### 2.1.2.3: vwap_validator.py
```python
# trading/vwap_validator.py
from typing import Dict, List
import pandas as pd
from analyzers.entry_timing_analyzer import VWAPAnalyzer

class VWAPValidator:
    """VWAP 검증 서비스"""

    def __init__(self, config: Dict):
        self.config = config
        self.analyzer = VWAPAnalyzer()
        self.validation_results = {}

    async def validate_stocks(
        self,
        stock_codes: List[str]
    ) -> Dict[str, bool]:
        """
        여러 종목 VWAP 검증

        Returns:
            {stock_code: is_valid}
        """
        results = {}

        for stock_code in stock_codes:
            results[stock_code] = await self.validate_single(stock_code)

        return results

    async def validate_single(self, stock_code: str) -> bool:
        """단일 종목 VWAP 검증"""
        # VWAP 분석
        analysis = await self.analyzer.analyze(stock_code)

        # 검증 기준 체크
        if analysis.win_rate < self.config['min_win_rate']:
            return False

        if analysis.profit_factor < self.config['min_profit_factor']:
            return False

        # 검증 결과 저장
        self.validation_results[stock_code] = analysis

        return True
```

**라인 수**: ~200 lines
**책임**: VWAP 분석 및 검증

---

##### 2.1.2.4: order_executor.py
```python
# trading/order_executor.py
from typing import Optional
from kiwoom_api import KiwoomAPI
from exceptions.trading_exceptions import *
from dataclasses import dataclass

@dataclass
class Order:
    """주문 정보"""
    stock_code: str
    quantity: int
    price: int
    order_type: str  # 'buy' or 'sell'
    order_id: Optional[str] = None

class OrderExecutor:
    """주문 실행 서비스"""

    def __init__(self, api: KiwoomAPI):
        self.api = api
        self.pending_orders = {}
        self.executed_orders = {}

    async def execute_buy(
        self,
        stock_code: str,
        quantity: int,
        price: int = 0
    ) -> Order:
        """매수 주문 실행"""
        # 잔고 확인
        available_cash = await self._get_available_cash()
        required_cash = price * quantity if price > 0 else await self._estimate_cost(stock_code, quantity)

        if available_cash < required_cash:
            raise InsufficientFundsError(
                f"Required: {required_cash}, Available: {available_cash}"
            )

        # 주문 실행
        try:
            result = await self.api.order_buy(stock_code, quantity, price)
            order = Order(
                stock_code=stock_code,
                quantity=quantity,
                price=price,
                order_type='buy',
                order_id=result['order_id']
            )

            self.pending_orders[order.order_id] = order
            return order

        except Exception as e:
            raise OrderFailedError(f"Buy order failed: {e}")

    async def execute_sell(
        self,
        stock_code: str,
        quantity: int,
        price: int = 0
    ) -> Order:
        """매도 주문 실행"""
        # 보유 수량 확인
        holdings = await self._get_holdings(stock_code)

        if holdings < quantity:
            raise InsufficientFundsError(
                f"Insufficient holdings: {holdings} < {quantity}"
            )

        # 주문 실행
        try:
            result = await self.api.order_sell(stock_code, quantity, price)
            order = Order(
                stock_code=stock_code,
                quantity=quantity,
                price=price,
                order_type='sell',
                order_id=result['order_id']
            )

            self.pending_orders[order.order_id] = order
            return order

        except Exception as e:
            raise OrderFailedError(f"Sell order failed: {e}")
```

**라인 수**: ~300 lines
**책임**: 주문 실행, 잔고 관리

---

##### 2.1.2.5: monitoring_service.py
```python
# trading/monitoring_service.py
from typing import Dict, List, Callable
import asyncio
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class Position:
    """포지션 정보"""
    stock_code: str
    quantity: int
    entry_price: float
    current_price: float = 0.0
    unrealized_pnl: float = 0.0
    stop_loss_price: float = 0.0
    take_profit_price: float = 0.0
    entry_time: datetime = field(default_factory=datetime.now)

class MonitoringService:
    """포지션 모니터링 서비스"""

    def __init__(self, config: Dict):
        self.config = config
        self.positions: Dict[str, Position] = {}
        self.callbacks: Dict[str, List[Callable]] = {
            'stop_loss': [],
            'take_profit': [],
            'price_update': []
        }
        self.running = False

    def add_position(self, position: Position):
        """포지션 추가"""
        self.positions[position.stock_code] = position

    def remove_position(self, stock_code: str):
        """포지션 제거"""
        if stock_code in self.positions:
            del self.positions[stock_code]

    def register_callback(self, event_type: str, callback: Callable):
        """콜백 등록"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)

    async def start_monitoring(self):
        """모니터링 시작"""
        self.running = True

        while self.running:
            await self._check_positions()
            await asyncio.sleep(self.config['check_interval'])

    async def stop_monitoring(self):
        """모니터링 중지"""
        self.running = False

    async def _check_positions(self):
        """포지션 체크"""
        for stock_code, position in list(self.positions.items()):
            # 현재 가격 업데이트
            current_price = await self._get_current_price(stock_code)
            position.current_price = current_price

            # PnL 계산
            position.unrealized_pnl = (
                (current_price - position.entry_price) / position.entry_price * 100
            )

            # 손절/익절 체크
            if position.stop_loss_price > 0 and current_price <= position.stop_loss_price:
                await self._trigger_callback('stop_loss', position)

            if position.take_profit_price > 0 and current_price >= position.take_profit_price:
                await self._trigger_callback('take_profit', position)

            # 가격 업데이트 콜백
            await self._trigger_callback('price_update', position)

    async def _trigger_callback(self, event_type: str, position: Position):
        """콜백 실행"""
        for callback in self.callbacks[event_type]:
            await callback(position)
```

**라인 수**: ~250 lines
**책임**: 포지션 모니터링, 이벤트 트리거

---

##### 2.1.2.6: trading_coordinator.py
```python
# trading/trading_coordinator.py
from typing import Dict, List
from trading.websocket_manager import TradingWebSocketManager
from trading.condition_filter import ConditionFilter
from trading.vwap_validator import VWAPValidator
from trading.order_executor import OrderExecutor
from trading.monitoring_service import MonitoringService, Position
from config.config_loader import config

class TradingCoordinator:
    """거래 오케스트레이터"""

    def __init__(self, api_credentials: Dict):
        # 컴포넌트 초기화
        self.ws_manager = TradingWebSocketManager(
            url=config.get('websocket.url'),
            credentials=api_credentials
        )
        self.condition_filter = ConditionFilter(...)
        self.vwap_validator = VWAPValidator(...)
        self.order_executor = OrderExecutor(...)
        self.monitoring_service = MonitoringService(...)

        # 상태
        self.watchlist = []
        self.running = False

    async def start(self):
        """거래 시스템 시작"""
        # 1. WebSocket 연결
        if not await self.ws_manager.start():
            raise ConnectionError("Failed to connect WebSocket")

        # 2. 조건 필터링
        filtered_stocks = await self.condition_filter.run_filtering(...)

        # 3. VWAP 검증
        validated_stocks = await self.vwap_validator.validate_stocks(filtered_stocks)

        # 4. Watchlist 업데이트
        self.watchlist = [
            code for code, is_valid in validated_stocks.items() if is_valid
        ]

        # 5. 모니터링 시작
        self.monitoring_service.register_callback('stop_loss', self._handle_stop_loss)
        self.monitoring_service.register_callback('take_profit', self._handle_take_profit)
        await self.monitoring_service.start_monitoring()

        self.running = True

    async def stop(self):
        """거래 시스템 중지"""
        self.running = False
        await self.monitoring_service.stop_monitoring()
        await self.ws_manager.disconnect()

    async def execute_trade(self, stock_code: str, quantity: int):
        """거래 실행"""
        # 주문 실행
        order = await self.order_executor.execute_buy(stock_code, quantity)

        # 포지션 생성
        position = Position(
            stock_code=stock_code,
            quantity=quantity,
            entry_price=order.price,
            stop_loss_price=order.price * 0.97,  # -3%
            take_profit_price=order.price * 1.05  # +5%
        )

        # 모니터링 추가
        self.monitoring_service.add_position(position)

    async def _handle_stop_loss(self, position: Position):
        """손절 처리"""
        await self.order_executor.execute_sell(
            position.stock_code,
            position.quantity
        )
        self.monitoring_service.remove_position(position.stock_code)

    async def _handle_take_profit(self, position: Position):
        """익절 처리"""
        await self.order_executor.execute_sell(
            position.stock_code,
            position.quantity
        )
        self.monitoring_service.remove_position(position.stock_code)
```

**라인 수**: ~300 lines
**책임**: 전체 거래 흐름 조율

---

##### 2.1.2.7: main.py
```python
# trading/main.py
import asyncio
from trading.trading_coordinator import TradingCoordinator
from config.env_config import env
from rich.console import Console

console = Console()

async def main():
    """진입점"""
    # 인증 정보
    credentials = {
        'app_key': env.KIWOOM_APP_KEY,
        'app_secret': env.KIWOOM_APP_SECRET,
        'account_no': env.KIWOOM_ACCOUNT_NO
    }

    # Coordinator 생성
    coordinator = TradingCoordinator(credentials)

    try:
        console.print("[green]Starting trading system...[/green]")
        await coordinator.start()

        # 시스템 실행 유지
        while coordinator.running:
            await asyncio.sleep(1)

    except KeyboardInterrupt:
        console.print("[yellow]Shutting down...[/yellow]")

    finally:
        await coordinator.stop()
        console.print("[green]System stopped.[/green]")

if __name__ == "__main__":
    asyncio.run(main())
```

**라인 수**: ~50 lines
**책임**: 시스템 시작/종료

---

#### Task 2.1.3: 테스트 작성
```python
# tests/trading/test_order_executor.py
import pytest
from trading.order_executor import OrderExecutor, Order
from exceptions.trading_exceptions import InsufficientFundsError
from unittest.mock import AsyncMock, MagicMock

@pytest.mark.asyncio
async def test_execute_buy_success():
    """매수 주문 성공 테스트"""
    # Mock API
    mock_api = MagicMock()
    mock_api.order_buy = AsyncMock(return_value={'order_id': '12345'})
    mock_api.get_balance = AsyncMock(return_value={'available_cash': 1000000})

    executor = OrderExecutor(mock_api)
    order = await executor.execute_buy('005930', 10, 70000)

    assert order.stock_code == '005930'
    assert order.quantity == 10
    assert order.order_id == '12345'
    assert order.order_type == 'buy'

@pytest.mark.asyncio
async def test_execute_buy_insufficient_funds():
    """잔고 부족 시 예외 발생 테스트"""
    mock_api = MagicMock()
    mock_api.get_balance = AsyncMock(return_value={'available_cash': 100000})

    executor = OrderExecutor(mock_api)

    with pytest.raises(InsufficientFundsError):
        await executor.execute_buy('005930', 10, 70000)
```

**테스트 커버리지 목표**: > 80%

**결과물**:
- [ ] 8개 모듈 구현 완료
- [ ] 단위 테스트 작성 (커버리지 > 80%)
- [ ] 통합 테스트 작성
- [ ] 기존 기능 동작 확인

---

### Sprint 2.2: core/ 디렉토리 리팩토링 (10일)

#### Task 2.2.1: db_auto_trading_handler.py 분리 (4,150 lines)
**목표**: Repository Pattern 적용

**새로운 구조**:
```
repositories/
├── __init__.py
├── base_repository.py           # 기본 Repository 추상 클래스
├── stock_repository.py          # 종목 데이터
├── trade_repository.py          # 거래 내역
├── position_repository.py       # 포지션
├── candidate_repository.py      # 후보 종목
└── performance_repository.py    # 성과 데이터
```

**구현 예시**:
```python
# repositories/base_repository.py
from abc import ABC, abstractmethod
from typing import Generic, TypeVar, List, Optional
import sqlite3

T = TypeVar('T')

class BaseRepository(ABC, Generic[T]):
    """기본 Repository 추상 클래스"""

    def __init__(self, db_path: str):
        self.db_path = db_path

    def _get_connection(self) -> sqlite3.Connection:
        """데이터베이스 연결"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @abstractmethod
    def find_by_id(self, id: str) -> Optional[T]:
        """ID로 조회"""
        pass

    @abstractmethod
    def find_all(self) -> List[T]:
        """전체 조회"""
        pass

    @abstractmethod
    def save(self, entity: T) -> T:
        """저장"""
        pass

    @abstractmethod
    def delete(self, id: str) -> bool:
        """삭제"""
        pass
```

```python
# repositories/stock_repository.py
from repositories.base_repository import BaseRepository
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime

@dataclass
class Stock:
    """종목 엔티티"""
    code: str
    name: str
    market: str  # 'KOSPI' or 'KOSDAQ'
    sector: Optional[str] = None
    created_at: datetime = None

class StockRepository(BaseRepository[Stock]):
    """종목 Repository"""

    def find_by_id(self, code: str) -> Optional[Stock]:
        """종목 코드로 조회"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM stocks WHERE code = ?",
                (code,)
            )
            row = cursor.fetchone()

            if row:
                return Stock(**dict(row))
            return None

    def find_all(self) -> List[Stock]:
        """전체 종목 조회"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM stocks")
            rows = cursor.fetchall()

            return [Stock(**dict(row)) for row in rows]

    def find_by_market(self, market: str) -> List[Stock]:
        """시장별 조회"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM stocks WHERE market = ?",
                (market,)
            )
            rows = cursor.fetchall()

            return [Stock(**dict(row)) for row in rows]

    def save(self, stock: Stock) -> Stock:
        """종목 저장"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO stocks (code, name, market, sector, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(code) DO UPDATE SET
                    name = excluded.name,
                    market = excluded.market,
                    sector = excluded.sector
            """, (
                stock.code,
                stock.name,
                stock.market,
                stock.sector,
                stock.created_at or datetime.now()
            ))
            conn.commit()

        return stock

    def delete(self, code: str) -> bool:
        """종목 삭제"""
        with self._get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM stocks WHERE code = ?", (code,))
            conn.commit()

            return cursor.rowcount > 0
```

**적용 후**:
- 기존: `db_auto_trading_handler.py` (4,150 lines)
- 변경 후: 6개 Repository 파일 (각 200-300 lines)

**결과물**:
- [ ] 6개 Repository 구현
- [ ] 엔티티 클래스 정의
- [ ] 테스트 작성
- [ ] 마이그레이션 스크립트

---

#### Task 2.2.2: menu_handlers.py 분리 (3,771 lines)
**목표**: 메뉴별 핸들러 분리

**새로운 구조**:
```
handlers/
├── __init__.py
├── base_handler.py              # 기본 핸들러
├── trading_handler.py           # 거래 메뉴
├── analysis_handler.py          # 분석 메뉴
├── backtest_handler.py          # 백테스트 메뉴
├── ml_handler.py                # ML 메뉴
└── settings_handler.py          # 설정 메뉴
```

**결과물**:
- [ ] 6개 핸들러 구현
- [ ] 각 핸들러 테스트

---

#### Task 2.2.3: trading_system.py 분리 (3,073 lines)
**목표**: Service Layer 패턴 적용

**새로운 구조**:
```
services/
├── __init__.py
├── trading_service.py           # 거래 서비스
├── analysis_service.py          # 분석 서비스
├── data_service.py              # 데이터 서비스
└── notification_service.py      # 알림 서비스
```

**결과물**:
- [ ] 4개 서비스 구현
- [ ] 서비스 계층 테스트

---

### Sprint 2.3: 디자인 패턴 적용 (8일)

#### Task 2.3.1: Command Pattern (주문 관리)
```python
# patterns/commands.py
from abc import ABC, abstractmethod
from typing import Optional

class Command(ABC):
    """커맨드 인터페이스"""

    @abstractmethod
    async def execute(self):
        """실행"""
        pass

    @abstractmethod
    async def undo(self):
        """취소"""
        pass

class BuyOrderCommand(Command):
    """매수 주문 커맨드"""

    def __init__(self, executor, stock_code: str, quantity: int, price: int):
        self.executor = executor
        self.stock_code = stock_code
        self.quantity = quantity
        self.price = price
        self.order_id = None

    async def execute(self):
        """매수 실행"""
        order = await self.executor.execute_buy(
            self.stock_code,
            self.quantity,
            self.price
        )
        self.order_id = order.order_id
        return order

    async def undo(self):
        """매수 취소 (매도)"""
        if self.order_id:
            await self.executor.cancel_order(self.order_id)

class CommandInvoker:
    """커맨드 실행기"""

    def __init__(self):
        self.history = []

    async def execute(self, command: Command):
        """커맨드 실행 및 히스토리 저장"""
        result = await command.execute()
        self.history.append(command)
        return result

    async def undo_last(self):
        """마지막 커맨드 취소"""
        if self.history:
            command = self.history.pop()
            await command.undo()
```

**결과물**:
- [ ] Command Pattern 구현
- [ ] 주문 히스토리 관리
- [ ] 테스트 작성

---

#### Task 2.3.2: Observer Pattern (가격 모니터링)
```python
# patterns/observers.py
from abc import ABC, abstractmethod
from typing import List

class Observer(ABC):
    """관찰자 인터페이스"""

    @abstractmethod
    async def update(self, subject, data):
        """업데이트 알림"""
        pass

class Subject(ABC):
    """주제 인터페이스"""

    def __init__(self):
        self._observers: List[Observer] = []

    def attach(self, observer: Observer):
        """관찰자 등록"""
        if observer not in self._observers:
            self._observers.append(observer)

    def detach(self, observer: Observer):
        """관찰자 해제"""
        if observer in self._observers:
            self._observers.remove(observer)

    async def notify(self, data):
        """관찰자들에게 알림"""
        for observer in self._observers:
            await observer.update(self, data)

class PriceSubject(Subject):
    """가격 주제"""

    def __init__(self, stock_code: str):
        super().__init__()
        self.stock_code = stock_code
        self.current_price = 0.0

    async def update_price(self, price: float):
        """가격 업데이트 및 알림"""
        self.current_price = price
        await self.notify({'price': price, 'stock_code': self.stock_code})

class StopLossObserver(Observer):
    """손절 관찰자"""

    def __init__(self, stop_loss_price: float, callback):
        self.stop_loss_price = stop_loss_price
        self.callback = callback

    async def update(self, subject: PriceSubject, data):
        """가격 업데이트 받음"""
        if data['price'] <= self.stop_loss_price:
            await self.callback(subject.stock_code)

class TakeProfitObserver(Observer):
    """익절 관찰자"""

    def __init__(self, take_profit_price: float, callback):
        self.take_profit_price = take_profit_price
        self.callback = callback

    async def update(self, subject: PriceSubject, data):
        """가격 업데이트 받음"""
        if data['price'] >= self.take_profit_price:
            await self.callback(subject.stock_code)

# 사용 예시
price_subject = PriceSubject('005930')
price_subject.attach(StopLossObserver(68000, handle_stop_loss))
price_subject.attach(TakeProfitObserver(75000, handle_take_profit))

await price_subject.update_price(67500)  # 손절 트리거
```

**결과물**:
- [ ] Observer Pattern 구현
- [ ] 이벤트 기반 모니터링
- [ ] 테스트 작성

---

#### Task 2.3.3: State Pattern (거래 상태)
```python
# patterns/states.py
from abc import ABC, abstractmethod

class TradingState(ABC):
    """거래 상태 인터페이스"""

    @abstractmethod
    def can_buy(self) -> bool:
        """매수 가능 여부"""
        pass

    @abstractmethod
    def can_sell(self) -> bool:
        """매도 가능 여부"""
        pass

    @abstractmethod
    async def on_enter(self, context):
        """상태 진입"""
        pass

    @abstractmethod
    async def on_exit(self, context):
        """상태 종료"""
        pass

class WatchingState(TradingState):
    """관찰 중 상태"""

    def can_buy(self) -> bool:
        return True

    def can_sell(self) -> bool:
        return False

    async def on_enter(self, context):
        print(f"Watching {context.stock_code}")

    async def on_exit(self, context):
        pass

class HoldingState(TradingState):
    """보유 중 상태"""

    def can_buy(self) -> bool:
        return False

    def can_sell(self) -> bool:
        return True

    async def on_enter(self, context):
        print(f"Holding {context.stock_code}")

    async def on_exit(self, context):
        pass

class TradingContext:
    """거래 컨텍스트"""

    def __init__(self, stock_code: str):
        self.stock_code = stock_code
        self._state: TradingState = WatchingState()

    async def set_state(self, state: TradingState):
        """상태 변경"""
        await self._state.on_exit(self)
        self._state = state
        await self._state.on_enter(self)

    def can_buy(self) -> bool:
        return self._state.can_buy()

    def can_sell(self) -> bool:
        return self._state.can_sell()
```

**결과물**:
- [ ] State Pattern 구현
- [ ] 상태 전환 로직
- [ ] 테스트 작성

---

## 📅 Phase 3: 품질 향상 (Week 10-13)

### Sprint 3.1: 로깅 시스템 구축 (3일)

#### Task 3.1.1: 구조화된 로깅
```python
# utils/logger.py
import logging
import json
from datetime import datetime
from pathlib import Path

class StructuredLogger:
    """구조화된 로거"""

    def __init__(self, name: str, log_dir: str = './logs'):
        self.logger = logging.getLogger(name)
        self.logger.setLevel(logging.INFO)

        # 파일 핸들러
        log_path = Path(log_dir) / f'{name}.log'
        handler = logging.FileHandler(log_path)

        # JSON 포맷터
        formatter = JsonFormatter()
        handler.setFormatter(formatter)

        self.logger.addHandler(handler)

    def info(self, message: str, **kwargs):
        """Info 로그"""
        self.logger.info(message, extra={'data': kwargs})

    def error(self, message: str, **kwargs):
        """Error 로그"""
        self.logger.error(message, extra={'data': kwargs})

    def trade(self, action: str, stock_code: str, **kwargs):
        """거래 로그"""
        self.logger.info(
            f"TRADE: {action}",
            extra={
                'data': {
                    'action': action,
                    'stock_code': stock_code,
                    **kwargs
                }
            }
        )

class JsonFormatter(logging.Formatter):
    """JSON 포맷터"""

    def format(self, record):
        log_data = {
            'timestamp': datetime.utcnow().isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
        }

        if hasattr(record, 'data'):
            log_data['data'] = record.data

        return json.dumps(log_data, ensure_ascii=False)

# 사용 예시
logger = StructuredLogger('trading')
logger.trade(
    'BUY',
    '005930',
    quantity=10,
    price=70000,
    order_id='12345'
)
```

**결과물**:
- [ ] 구조화된 로깅 시스템
- [ ] JSON 로그 포맷
- [ ] 로그 레벨 관리
- [ ] 로그 로테이션

---

### Sprint 3.2: Type Hints 추가 (5일)

#### Task 3.2.1: 전체 코드베이스 Type Hints
```python
# Before
def calculate_profit(entry_price, exit_price, quantity):
    return (exit_price - entry_price) * quantity

# After
from decimal import Decimal

def calculate_profit(
    entry_price: Decimal,
    exit_price: Decimal,
    quantity: int
) -> Decimal:
    """
    수익 계산

    Args:
        entry_price: 진입 가격
        exit_price: 청산 가격
        quantity: 수량

    Returns:
        수익 금액
    """
    return (exit_price - entry_price) * Decimal(quantity)
```

**적용 도구**:
```bash
# mypy 설정
cat > mypy.ini << EOF
[mypy]
python_version = 3.12
warn_return_any = True
warn_unused_configs = True
disallow_untyped_defs = True
disallow_incomplete_defs = True
check_untyped_defs = True
disallow_untyped_calls = True

[mypy-yfinance.*]
ignore_missing_imports = True
EOF

# 실행
mypy .
```

**결과물**:
- [ ] 모든 함수에 Type Hints
- [ ] mypy 검사 통과
- [ ] Type stub 파일 생성

---

### Sprint 3.3: 테스트 커버리지 향상 (7일)

#### Task 3.3.1: 단위 테스트 작성
**목표**: 커버리지 > 80%

```bash
# 현재 커버리지 측정
pytest --cov=. --cov-report=html

# 목표
# TOTAL                      183     XX      XX%
```

**우선순위**:
1. 핵심 비즈니스 로직 (거래, 검증)
2. 데이터 처리 (Repository, Fetcher)
3. 유틸리티 함수

**결과물**:
- [ ] 200+ 테스트 케이스
- [ ] 커버리지 > 80%
- [ ] CI 통합

---

### Sprint 3.4: 문서화 (5일)

#### Task 3.4.1: API 문서 생성
```bash
# Sphinx 설정
pip install sphinx sphinx-rtd-theme

sphinx-quickstart docs

# 자동 문서 생성
sphinx-apidoc -o docs/source .
```

**결과물**:
- [ ] API 문서
- [ ] 아키텍처 문서
- [ ] 사용자 가이드
- [ ] 개발자 가이드

---

## 📅 Phase 4: 고도화 (Week 14-16)

### Sprint 4.1: 성능 최적화 (5일)

#### Task 4.1.1: 데이터베이스 최적화
```sql
-- 인덱스 추가
CREATE INDEX idx_trades_stock_code ON trades(stock_code);
CREATE INDEX idx_trades_created_at ON trades(created_at);
CREATE INDEX idx_positions_stock_code ON positions(stock_code);

-- 쿼리 최적화
EXPLAIN QUERY PLAN
SELECT * FROM trades WHERE stock_code = '005930';
```

**결과물**:
- [ ] 인덱스 최적화
- [ ] 쿼리 성능 개선
- [ ] 벤치마크 리포트

---

#### Task 4.1.2: 비동기 처리 최적화
```python
# Before: 순차 처리
for stock in stocks:
    data = await fetch_data(stock)

# After: 병렬 처리
tasks = [fetch_data(stock) for stock in stocks]
results = await asyncio.gather(*tasks)
```

**결과물**:
- [ ] 병렬 처리 적용
- [ ] 성능 벤치마크

---

### Sprint 4.2: 모니터링 및 알림 (4일)

#### Task 4.2.1: Prometheus 메트릭
```python
# monitoring/metrics.py
from prometheus_client import Counter, Histogram, Gauge

# 메트릭 정의
trades_total = Counter('trades_total', 'Total trades', ['action', 'stock_code'])
trade_duration = Histogram('trade_duration_seconds', 'Trade execution time')
active_positions = Gauge('active_positions', 'Active positions')

# 사용
trades_total.labels(action='buy', stock_code='005930').inc()
```

**결과물**:
- [ ] Prometheus 메트릭
- [ ] Grafana 대시보드
- [ ] 알림 규칙

---

### Sprint 4.3: CI/CD 구축 (5일)

#### Task 4.3.1: GitHub Actions
```yaml
# .github/workflows/ci.yml
name: CI

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

      - name: Run tests
        run: pytest --cov=. --cov-report=xml

      - name: Type check
        run: mypy .

      - name: Lint
        run: |
          flake8 .
          black --check .
```

**결과물**:
- [ ] CI 파이프라인
- [ ] 자동 테스트
- [ ] 코드 품질 검사

---

## 📊 진행 관리

### 주간 체크리스트

**Week 1-3 (Phase 1)**:
- [ ] 백업 완료
- [ ] 의존성 관리
- [ ] 중복 코드 제거 (3개 파일)
- [ ] 설정 관리 개선
- [ ] 에러 처리 표준화

**Week 4-9 (Phase 2)**:
- [ ] main_auto_trading.py 분리 (8개 모듈)
- [ ] core/ 디렉토리 리팩토링 (3개 파일)
- [ ] 디자인 패턴 적용 (Command, Observer, State)

**Week 10-13 (Phase 3)**:
- [ ] 로깅 시스템 구축
- [ ] Type Hints 추가
- [ ] 테스트 커버리지 > 80%
- [ ] 문서화 완료

**Week 14-16 (Phase 4)**:
- [ ] 성능 최적화
- [ ] 모니터링 구축
- [ ] CI/CD 구축

---

## 🎯 성공 기준

### 정량적 목표
- [ ] 파일 라인 수: 최대 500 lines/file
- [ ] 테스트 커버리지: > 80%
- [ ] Type Hints: 100% 함수
- [ ] 중복 코드: < 5%
- [ ] 순환 의존성: 0개

### 정성적 목표
- [ ] 코드 가독성 향상
- [ ] 유지보수 용이성 개선
- [ ] 테스트 가능성 확보
- [ ] 확장성 확보
- [ ] 문서화 완료

---

## 🚨 리스크 관리

### 리스크 및 대응 방안

| 리스크 | 확률 | 영향 | 대응 방안 |
|--------|------|------|-----------|
| 기능 손상 | 중 | 높음 | 각 스프린트 후 회귀 테스트 |
| 일정 지연 | 높음 | 중 | 버퍼 시간 확보 (20%) |
| 의존성 충돌 | 낮음 | 중 | 가상 환경 사용 |
| 데이터 손실 | 낮음 | 높음 | 정기 백업 (일 1회) |

### 롤백 전략
```bash
# Git 태그로 롤백
git checkout v1.0-pre-refactoring

# 데이터베이스 복원
cp backups/db_backup_YYYYMMDD.db database/kiwoom_trading.db
```

---

## 📝 다음 단계

1. **이 계획 검토 및 승인**
2. **Sprint 1.1 시작**: 백업 및 환경 설정
3. **주간 진행 상황 리뷰**
4. **필요 시 계획 조정**

---

**작성자**: Claude Code Assistant
**검토자**: [Your Name]
**승인일**: [Date]
**버전**: 1.0
