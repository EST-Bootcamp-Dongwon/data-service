# data-service — 문서 지도

이 모듈의 문서는 **Diátaxis** 4분류를 따른다. 단 **빈 폴더는 만들지 않는다** —
내용이 생길 때 폴더를 만든다 (02 검증본 §5.1).

| 폴더 | 무엇을 넣는가 | 문체 |
|---|---|---|
| `tutorials/` | 처음 온 사람이 순서대로 따라 하면 되는 것 | "-습니다" |
| `how-to/` | 특정 문제를 푸는 절차 | 개조식 |
| `reference/` | 스키마·API·설정 등 찾아보는 것 | 개조식 |
| `explanation/` | 왜 이렇게 설계했는가 | 개조식 |
| `decisions/` | ADR (ADR-DS-*) | MADR-minimal |

## 작성 예정

| 순서 | 파일 | 상태 |
|---|---|---|
| 1 | `decisions/0001` ~ `0004` (ADR 재배치 결과) | ☐ |
| 2 | `reference/data-model.md` (securities · ohlcv · watermark DDL) | ☐ |
| 3 | `how-to/sync-to-supabase.md` (저장소 이원화 동기화) | ☐ |
| 4 | `how-to/add-new-data-source.md` | ☐ |
| 5 | `explanation/architecture.md` | ☐ |
| 6 | `tutorials/getting-started.md` | ☐ |

## 규칙

- 다이어그램은 Mermaid `flowchart` + `subgraph`. **`C4Container` 문법 금지** (GitHub 미렌더링)
- 숫자가 들어가는 절은 목차 단계에서라도 **추정치 한 줄**을 넣는다 (01 검증본 §9-3)
- 외부 정책(무료 티어·요금제·세율)에 근거한 문서에는 **"이 근거는 X를 전제하며,
  X가 바뀌면 재검토"** 를 본문에 적는다 (01 검증본 §9-1)
