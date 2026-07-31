"""애플리케이션 패키지 (레이어드 아키텍처)

요청이 흐르는 순서대로 폴더를 나눴다.

    요청 → routers(컨트롤러) → services(계산) → repositories(SQLite) → clients(KRX 호출)

| 폴더 | 계층 | 역할 |
|---|---|---|
| `routers/` | 컨트롤러 | 요청 검증 · DTO · 엔드포인트 |
| `services/` | 서비스 | 비즈니스 로직(스크리닝 · 포트폴리오 · 팩터) |
| `repositories/` | 저장소 | SQLite 캐시 저장 · 조회 |
| `clients/` | 외부 연동 | KRX OpenAPI HTTP 호출 · 응답 정규화 |
| `core/` | 공통 | 거래일 · KST 등 계층에 속하지 않는 유틸 |

아래 계층은 위 계층을 import 하지 않는다. 이 방향을 지켜야 순환 import 가 생기지 않는다.
"""
