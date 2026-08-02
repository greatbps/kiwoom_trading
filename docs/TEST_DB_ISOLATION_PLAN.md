# 테스트/운영 DB 분리 계획 (설계 문서, 미구현)

> 작성일: 2026-07-27 | 운영 안정성 감사 v3.1 — 작업 2-5
> 상태: 설계만 완료. 실제 DB 생성/이관은 이번 작업 범위 밖.

## 배경

`tests/test_research_schema.py`, `tests/test_decision_service.py`가 실거래 DB(`trading_system`)에
직접 연결해 `research.candidates`/`decision_ledger`/`event_store`를 `TRUNCATE`하는 사고가 있었다.
`utils/database_guard.py`로 즉시 차단 조치는 완료했지만(POSTGRES_DB에 'test' 문자열이 없으면
스킵), 이는 "사고를 막는" 임시 방어책이지 "테스트가 원래 별도 환경에서 돌아가는" 근본 해결은
아니다. 지금은 테스트를 돌리면 그냥 전부 스킵되는 상태 — 이 테스트들의 원래 목적(스키마
검증, 라이프사이클 smoke test)은 아무도 실행하지 않고 있다.

## 현황

```
PostgreSQL 인스턴스: 1개
  └── trading_system (운영 DB, 실계좌 연동)
        └── research 스키마 (candidates/decision_ledger/event_store/...)
```

테스트 DB 없음. `.env`의 `POSTGRES_DB=trading_system` 하나만 존재.

## 장기 목표 (택 1)

### 옵션 A — 동일 PostgreSQL 인스턴스에 별도 DB 추가

```
PostgreSQL 인스턴스: 1개 (기존과 동일)
  ├── trading_system       (운영)
  └── trading_system_test  (신규, 테스트 전용)
```

장점:
- 인프라 추가 없음, `createdb trading_system_test` + 스키마 마이그레이션 재실행만 하면 됨
- `.env.test`(또는 pytest용 `.env`) 하나만 두면 됨

단점:
- 같은 PostgreSQL 프로세스를 공유 — 인스턴스 자체 장애(디스크풀 등) 시 운영에도 영향
- 운영 DB 자격증명과 테스트 DB 자격증명이 같은 서버에 있어, 실수로 잘못된 dbname을
  넘기는 휴먼 에러 가능성은 완전히 사라지지 않음(다만 `database_guard`가 이 케이스를
  막아준다)

### 옵션 B — Docker PostgreSQL 테스트 인스턴스

```
docker-compose.test.yml
  └── postgres_test 컨테이너 (별도 포트, 예: 5433)
```

장점:
- 물리적으로 완전히 분리 — 운영 DB 자격증명 자체가 테스트 프로세스에 존재하지 않음
- CI 환경에서 매번 새로 띄우고 버릴 수 있어 테스트 격리도가 가장 높음
- 스키마 드리프트 감지에도 유리(매번 `db/schema/*.sql`을 처음부터 적용해야 하므로
  마이그레이션 누락을 CI에서 바로 발견)

단점:
- Docker 설정/CI 파이프라인 추가 작업 필요
- 로컬 개발 시 `docker compose up` 선행 필요(온보딩 비용 소폭 증가)

## 권장안

**옵션 B(Docker)를 최종 목표로, 옵션 A를 임시 다리로 사용**하는 단계적 접근을 권장한다.

1. 단기(즉시 가능): `createdb trading_system_test` 로 옵션 A부터 적용 — 기존
   `test_research_schema.py`/`test_decision_service.py`가 스킵되지 않고 실제로 돌게 만드는
   데 필요한 최소 작업.
2. 중기: CI(있다면)에 Docker 기반 옵션 B 파이프라인 추가, 로컬 개발은 옵션 A 유지.

## 구현 시 필요한 작업 (실제 착수 시, 이번 세션 범위 아님)

- [ ] `trading_system_test` DB 생성 + `db/schema/research_schema_v1.sql` 및
      `db/migrations/*.sql` 전체 적용
- [ ] 테스트 실행용 환경변수 오버라이드 방법 결정(`.env.test` vs pytest fixture에서
      `monkeypatch.setenv('POSTGRES_DB', 'trading_system_test')`)
- [ ] `tests/test_research_schema.py`/`test_decision_service.py`가 기본적으로
      테스트 DB를 바라보도록 conftest.py에 세션 전역 fixture 추가 검토
- [ ] CI(만약 구축 시) 파이프라인에 "테스트 DB 준비" 단계 추가
- [ ] `analysis/check_destructive_tests.py`(v3.1에서 신규 작성)를 pre-commit 또는 CI
      게이트로 등록

## 이번 작업에서 하지 않는 것

- 실제 `trading_system_test` DB 생성
- CI 파이프라인 구축(현재 이 프로젝트에 CI 자체가 없을 수 있음 — 별도 확인 필요)
- `conftest.py` 작성
