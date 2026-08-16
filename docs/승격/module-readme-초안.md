# data-service — 모듈 README 초안

> ⚠️ 이 파일은 **초안**이다. 레포 루트의 `README.md`는 아직 `api-test` 시절 내용이라
> 덮어쓰지 않았다. 승격이 확정되면 이 내용을 루트 `README.md`로 올리고
> 기존 내용 중 살릴 것(실행 절차·API 목록 등)을 흡수하세요.

> 국내 시장 데이터의 **수집 · 정규화 · 조회 API**. ★1순위 모듈.

## 이게 왜 1순위인가

이 레포는 ④ `api-test`를 **개명해 승격한 것**이다(2026-08-16). 복사본이 아니라
같은 저장소이고 히스토리가 이어진다.

이미 **70% 완성 상태**다. DART / KRX / ECOS / FRED / KOSIS **5종 클라이언트**(`app/clients/`),
한국 휴장일 계산(`app/core/trading_calendar.py`), FastAPI 라우터 7종(`app/routers/`),
파일 캐시(`app/repositories/`)가 이미 있다.
신규 개발이 아니라 **계약(OpenAPI)을 정리하고 저장 계층을 붙이는 작업**이다.

그리고 나머지 모듈이 전부 이걸 쓴다.

## 저장소 이원화 (ADR-CT-0007)

```
로컬 Postgres 컨테이너   ← 정본. 전종목 10년(full). 백테스트·팩터 계산은 여기서
        │  (단방향 동기화)
        ▼
Supabase                 ← 배포 데모용. core 유니버스 서브셋만 (약 25~80MB)
```

Supabase Free는 프로젝트당 **500MB**이고 전종목 10년은 634MB~1.1GB다.
게다가 ③ `insight`의 fastembed 임베딩이 같은 500MB를 이미 쓰고 있다.

## 유니버스 2단계 (ADR-CT-0010)

| 단계 | 범위 | 저장 위치 | 용도 |
|---|---|---|---|
| `core` | KOSPI200 + KOSDAQ150 (약 350) | 로컬 + Supabase | API·대시보드·데모 |
| `full` | 전종목 + 상장폐지 | 로컬 전용 | 백테스트·팩터 |

## 실행

```powershell
docker compose up -d                    # 앱만 (원격 Supabase)
docker compose --profile local-db up -d # 앱 + 로컬 Postgres
invoke check                            # 검증 정본
```

## 다음에 읽을 것

- `research/01-data-service-검증완료.md` — 확정 산출물(compose·Dockerfile·settings.py)이 그대로 있다
- `docs/decisions/` — ADR-DS-0001 ~ 0004

## 승격 시 정리할 것

- [ ] 루트 `README.md`를 이 초안으로 교체(기존 내용 흡수)
- [ ] `lecture/` 하위 강의 원본 — 유지할지 별도 브랜치로 뺄지 결정
- [ ] `app/services/timeseries/*` — C generator 베이스라인이므로 `factor-service`로 이관
- [ ] `docs/md/` · `docs/todo.md` · `docs/작업-프롬프트-기록.txt` — Diátaxis 구조로 재배치
- [ ] `.vercel/` · `uvicorn.log` · `node_modules/` — `.gitignore` 확인
- [ ] `프로젝트 작업 프롬프트 (...).txt` — `docs/` 로 이동하거나 제거
