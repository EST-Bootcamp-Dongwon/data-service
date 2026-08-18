# ADR-DS-0005: `ruff format` 을 검증 경로에 넣지 않는다

## 상태

채택됨 (2026-08-17)

## 맥락

공통 규칙(`quant-contract/AGENTS.md`)은 검증 정본을
`ruff check → ruff format --check → pytest → uv export → docker build` 로 규정한다.

이 모듈에 그대로 적용하려고 실측했더니 **93개 파일 15,442줄**이 바뀐다.
전체 파이썬 코드가 25,722줄이므로 약 60%다. 그리고 그 변경의 거의 전부가
**인라인 주석 정렬을 뭉개는 것**이었다.

```python
# 현행 — 주석이 세로로 맞아 있다
app = FastAPI(
    lifespan=lifespan,               # 위에서 정의한 시작 준비 작업
    title="My FastAPI Backend",          # 문서 최상단 제목
    description=API_DESCRIPTION,         # 제목 아래 마크다운 설명
    docs_url="/docs",      # Swagger UI 경로
)

# ruff format 적용 후 — 전부 한 칸으로 정규화된다
app = FastAPI(
    lifespan=lifespan,  # 위에서 정의한 시작 준비 작업
    title="My FastAPI Backend",  # 문서 최상단 제목
    description=API_DESCRIPTION,  # 제목 아래 마크다운 설명
    docs_url="/docs",  # Swagger UI 경로
)
```

기능은 한 줄도 바뀌지 않는다. 얻는 것은 포맷 일관성이고,
잃는 것은 **정렬된 주석 전체와 git blame** 이다.

## 결정

1. `invoke check` 에서 `ruff format --check` 단계를 **뺀다.**
2. 품질 게이트는 `ruff check`(lint)가 맡는다. 이쪽은 현재 **전부 통과**한다.
3. 포맷이 필요하면 `invoke format` 으로 **의도적으로만** 돌린다.
   돌릴 경우 단독 커밋으로 하고 그 해시를 `.git-blame-ignore-revs` 에 넣는다.
4. `pyproject.toml` 의 `[tool.ruff.lint]` 에서 `UP`(pyupgrade)도 함께 뺀다 —
   `List[str]` → `list[str]` 류 표기 현대화가 1,975건이고 같은 성격의 변경이다.

## 근거

- **이 레포의 주석은 부산물이 아니라 산출물이다.** 강의 실습에서 출발한 코드라
  거의 모든 블록에 한국어 설명이 붙어 있고, 세로 정렬이 그 가독성의 일부다.
  포맷터는 그 정렬을 표현할 방법이 없다.
- **리팩터링 diff 를 오염시킨다.** 저장 계층 교체·페이지네이션 추가와 같은 시기에
  2천~1만 5천 줄짜리 표기 변경이 섞이면 `git diff` 에서 진짜 변경을 못 읽는다.
- **게이트가 사라지는 것이 아니다.** `ruff check` 는 `E`·`F`·`W`·`I`·`B` 를 켜 두었고
  이번에 110건을 실제로 잡아 고쳤다(죽은 import 32건 포함). 그중 하나는
  `app/services/research/stages.py:31` 의 `hf_data` 미사용 import 였다 —
  지우면서 2,279행 파일의 HuggingFace 의존이 사라졌다.
- **되돌릴 수 있는 결정이다.** 나중에 켜기로 하면 `ruff format .` 한 번이면 되고,
  `tests/` 의 계약 스냅샷(엔드포인트 51개 + 쿼리 파라미터)이 회귀를 잡아 준다.

## 결과

- 이 모듈의 `invoke check` 는 공통 규칙과 **한 단계 다르다.** `AGENTS.md` 의
  "이 모듈 특화" 절에 그 사실을 적어 둔다. 모르고 보면 빠뜨린 것처럼 보인다.
- 새 코드의 포맷은 사람이 맞춰야 한다. 기존 코드 스타일(4 spaces, 주석 세로 정렬)을 따른다.
- 포맷 불일치가 쌓여 견디기 어려워지는 시점이 오면 이 ADR 을 `superseded` 로 바꾸고
  단독 커밋으로 일괄 적용한다. 그때가 언제인지는 지금 정하지 않는다.
