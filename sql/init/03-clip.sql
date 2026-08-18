-- data-service — clip: 화면에서 담아 두는 자료 보관함
--
-- 01-schema.sql 다음에 실행된다(파일명 순서). securities 를 참조하므로 순서가 중요하다.
--
-- ─────────────────────────────────────────────────────────────────────────────
-- 왜 한 표인가 (ADR-DS-0008)
--
-- 담는 것은 네 종류다 — 뉴스·공시 링크 / 데이터셋 스냅샷 / 리서치 리포트 / 메모.
-- 종류마다 표를 나누면 "이 화면에서 담은 것 전부"를 낼 때마다 UNION 넷이 된다.
-- 그런데 요구의 핵심이 바로 **화면별로 나눠 본다**는 것이라 그 질의가 가장 잦다.
-- 그래서 kind 로 구분하고 종류별 가변 필드는 payload(jsonb)로 흘린다.
--
-- 날짜를 두 축으로 나눈 이유
--   occurred_at — 자료 자체의 날짜(기사 발행일·공시일·데이터 기준일). 날짜별/월별 묶기용
--   saved_at    — 내가 담은 시각. "어제 내가 뭘 봤더라"는 이쪽이다
-- 하나로 합치면 둘 중 하나를 반드시 잃는다.
--
-- ⚠️ 뉴스 본문은 담지 않는다. 링크·제목·출처·발행일·내 메모까지다.
--    언론사 본문 저장은 저작권 문제가 실재한다.
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE clip (
    clip_id         bigserial   PRIMARY KEY,

    -- 무엇을 담았는가
    kind            text        NOT NULL,
    screen          text        NOT NULL,   -- 담은 화면. krx·market·stock·quant·research·timeseries·kosis·yf·dashboard
    title           text        NOT NULL,

    -- 링크형(news·filing)의 본체
    url             text,
    url_key         text,                   -- 정규화 URL(스킴·www·추적파라미터 제거). 중복 차단용
    source          text,                   -- '연합뉴스' · 'DART' · 'KRX' · 'FRED' · 'NAVER'

    -- 시간 두 축
    occurred_at     date,                   -- 자료 자체의 날짜
    saved_at        timestamptz NOT NULL DEFAULT now(),

    -- 무엇에 관한 자료인가
    security_id     integer     REFERENCES securities(security_id),
    industry_code   text,                   -- KSIC. industry_map 과 같은 체계
    industry_source text        NOT NULL DEFAULT 'auto',   -- 'auto' | 'manual'

    -- 사람이 붙이는 것
    note            text,
    tags            text[]      NOT NULL DEFAULT '{}',

    -- 종류별 가변 필드
    payload         jsonb       NOT NULL DEFAULT '{}'::jsonb,

    CONSTRAINT clip_kind_ck
        CHECK (kind IN ('news', 'filing', 'dataset', 'report', 'memo')),
    CONSTRAINT clip_industry_source_ck
        CHECK (industry_source IN ('auto', 'manual')),
    -- 링크형은 URL 이 있어야 한다. 나머지는 없어도 된다.
    CONSTRAINT clip_link_needs_url_ck
        CHECK (kind NOT IN ('news', 'filing') OR url IS NOT NULL)
);

-- 같은 기사를 두 번 담지 않는다.
-- ⚠️ url_key 가 NULL 인 행(dataset·report·memo)은 Postgres 가 서로 다른 것으로 보므로
--    이 제약에 걸리지 않는다 — 의도한 동작이다. 스냅샷은 여러 번 담을 수 있어야 한다.
CREATE UNIQUE INDEX clip_kind_urlkey_uq ON clip (kind, url_key) WHERE url_key IS NOT NULL;

-- ── 조회 축 네 개 ───────────────────────────────────────────────────────────
-- 화면별 + 종류별 + 최신순 (가장 잦은 질의)
CREATE INDEX clip_screen_kind_idx  ON clip (screen, kind, occurred_at DESC NULLS LAST);
-- 산업별. KSIC 접두어 LIKE '26%' 가 그대로 탄다
CREATE INDEX clip_industry_idx     ON clip (industry_code, occurred_at DESC NULLS LAST)
                                   WHERE industry_code IS NOT NULL;
-- 종목별
CREATE INDEX clip_security_idx     ON clip (security_id, occurred_at DESC NULLS LAST)
                                   WHERE security_id IS NOT NULL;
-- 내가 담은 순서
CREATE INDEX clip_saved_idx        ON clip (saved_at DESC);
-- 태그
CREATE INDEX clip_tags_idx         ON clip USING gin (tags);

COMMENT ON TABLE  clip IS
    '화면에서 담아 두는 자료 보관함. 뉴스·공시 링크 / 데이터셋 스냅샷 / 리서치 리포트 / 메모 (ADR-DS-0008).';
COMMENT ON COLUMN clip.occurred_at IS
    '자료 자체의 날짜 — 기사 발행일·공시일·데이터 기준일. 날짜별/월별 묶기는 이 컬럼으로 한다.';
COMMENT ON COLUMN clip.saved_at IS
    '내가 담은 시각. occurred_at 과 다른 질문에 답한다.';
COMMENT ON COLUMN clip.industry_source IS
    'auto=securities 에서 유도 / manual=사용자가 고침. 재유도 배치가 manual 을 덮어쓰지 않게 하는 표시다.';
COMMENT ON COLUMN clip.url_key IS
    '정규화 URL. 네이버는 link 와 originallink 를 함께 주므로 originallink 를 우선해 정규화한다.';
COMMENT ON COLUMN clip.payload IS
    'kind 별 가변 필드. dataset={source,params,summary} · report={run_id,workstream,format} · filing={rcept_no,report_nm}';
