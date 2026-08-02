"""표준산업분류 맵 읽기 (M4 신설 · M5 확장)

`scripts/build_industry_map.py` 가 만든 `data/industry_map.json` 을 한 번만 올려 둔다.
없으면 **예외를 던지지 않는다** — 업종 기준 피어를 못 쓸 뿐, 나머지는 그대로 돌아야 한다.

접두어 매칭이 이 파일의 핵심이다. DART 업종코드는 자릿수가 섞여 있어서
(3자리 1,378 · 4자리 478 · 5자리 2,025 · 2자리 44 — 실측 3,925종목)
완전일치만 보면 삼성전자(264)와 SK하이닉스(2612)가 남남이 된다.
5→4→3→2 자리로 넓혀 가며 **후보가 찰 때까지** 찾는다.

M5 에서 더한 것 — 산업을 '대상' 으로 다루기 위한 것들
--------------------------------------------------
IND-R · IND-TP 는 종목이 아니라 **산업**이 분석 대상이다. 그러려면 코드만으로는 안 되고
이름·구성종목·목록이 있어야 한다. 그래서 아래 셋을 더했다.

    name_of(code)      업종코드 → 한국표준산업분류 이름 (5→3→2자리로 올라가며 찾는다)
    members(prefix)    그 업종에 속한 종목 목록
    directory()        산업 목록 (화면 선택 상자 · IND-R H01 이 쓴다)
    resolve(query)     '반도체' · '261' · '전자부품' → 업종 후보

⚠️ **이름표는 손으로 만들었다.** KSIC 이름을 주는 공개 API 가 마땅치 않아
(KOSIS 통계분류 API 는 별도 신청이 필요하다) 표를 코드에 뒀다. 대신 **지어내지 않았다** —
아래 3자리 이름은 전부 `data/industry_map.json` 의 실제 구성 종목으로 대조해 확인한 것이다.

    108 → 삼양식품 · 오리온 · CJ제일제당 · 농심 · 오뚜기    ⇒ '기타 식품 제조업' (사료가 아니다)
    264 → 삼성전자 · LG전자 · 인텔리안테크                 ⇒ '통신 및 방송 장비 제조업'
    715 → 솔브레인홀딩스 · NICE · 녹십자홀딩스             ⇒ '회사 본부 및 경영 컨설팅'

확인하지 못한 코드는 **빈 이름**으로 둔다. 코드만 보여 주고 이름을 만들지 않는다.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence

DATA_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "industry_map.json"

_cache: Optional[Dict] = None
_lock = threading.Lock()
_group_index: Optional[Dict[str, List[str]]] = None


# ─────────────────────────────────────────────────────────────
# 한국표준산업분류(KSIC 10차) 이름표
# ─────────────────────────────────────────────────────────────
# 대분류 — 알파벳 A~U. 2자리 중분류가 어느 대분류에 드는지는 아래 범위로 가른다.
KSIC_SECTIONS = [
    ("A", "농업, 임업 및 어업", 1, 3),
    ("B", "광업", 5, 8),
    ("C", "제조업", 10, 34),
    ("D", "전기, 가스, 증기 및 공기조절 공급업", 35, 35),
    ("E", "수도, 하수 및 폐기물 처리, 원료 재생업", 36, 39),
    ("F", "건설업", 41, 42),
    ("G", "도매 및 소매업", 45, 47),
    ("H", "운수 및 창고업", 49, 52),
    ("I", "숙박 및 음식점업", 55, 56),
    ("J", "정보통신업", 58, 63),
    ("K", "금융 및 보험업", 64, 66),
    ("L", "부동산업", 68, 68),
    ("M", "전문, 과학 및 기술 서비스업", 70, 73),
    ("N", "사업시설 관리, 사업 지원 및 임대 서비스업", 74, 76),
    ("O", "공공행정, 국방 및 사회보장 행정", 84, 84),
    ("P", "교육 서비스업", 85, 85),
    ("Q", "보건업 및 사회복지 서비스업", 86, 87),
    ("R", "예술, 스포츠 및 여가관련 서비스업", 90, 91),
    ("S", "협회 및 단체, 수리 및 기타 개인 서비스업", 94, 96),
    ("T", "가구 내 고용활동 및 자가소비 생산활동", 97, 98),
    ("U", "국제 및 외국기관", 99, 99),
]

# 중분류 (2자리)
KSIC_DIVISIONS = {
    "01": "농업", "02": "임업", "03": "어업",
    "05": "석탄, 원유 및 천연가스 광업", "06": "금속 광업",
    "07": "비금속광물 광업(연료용 제외)", "08": "광업 지원 서비스업",
    "10": "식료품 제조업", "11": "음료 제조업", "12": "담배 제조업",
    "13": "섬유제품 제조업(의복 제외)", "14": "의복, 의복 액세서리 및 모피제품 제조업",
    "15": "가죽, 가방 및 신발 제조업", "16": "목재 및 나무제품 제조업(가구 제외)",
    "17": "펄프, 종이 및 종이제품 제조업", "18": "인쇄 및 기록매체 복제업",
    "19": "코크스, 연탄 및 석유정제품 제조업",
    "20": "화학물질 및 화학제품 제조업(의약품 제외)",
    "21": "의료용 물질 및 의약품 제조업", "22": "고무 및 플라스틱제품 제조업",
    "23": "비금속 광물제품 제조업", "24": "1차 금속 제조업",
    "25": "금속가공제품 제조업(기계 및 가구 제외)",
    "26": "전자부품, 컴퓨터, 영상, 음향 및 통신장비 제조업",
    "27": "의료, 정밀, 광학기기 및 시계 제조업", "28": "전기장비 제조업",
    "29": "기타 기계 및 장비 제조업", "30": "자동차 및 트레일러 제조업",
    "31": "기타 운송장비 제조업", "32": "가구 제조업", "33": "기타 제품 제조업",
    "34": "산업용 기계 및 장비 수리업",
    "35": "전기, 가스, 증기 및 공기조절 공급업", "36": "수도업",
    "37": "하수, 폐수 및 분뇨 처리업",
    "38": "폐기물 수집, 운반, 처리 및 원료 재생업", "39": "환경 정화 및 복원업",
    "41": "종합 건설업", "42": "전문직별 공사업",
    "45": "자동차 및 부품 판매업", "46": "도매 및 상품 중개업", "47": "소매업(자동차 제외)",
    "49": "육상운송 및 파이프라인 운송업", "50": "수상 운송업", "51": "항공 운송업",
    "52": "창고 및 운송관련 서비스업",
    "55": "숙박업", "56": "음식점 및 주점업",
    "58": "출판업", "59": "영상·오디오 기록물 제작 및 배급업", "60": "방송업",
    "61": "우편 및 통신업", "62": "컴퓨터 프로그래밍, 시스템 통합 및 관리업",
    "63": "정보서비스업",
    "64": "금융업", "65": "보험 및 연금업", "66": "금융 및 보험 관련 서비스업",
    "68": "부동산업",
    "70": "연구개발업", "71": "전문서비스업",
    "72": "건축기술, 엔지니어링 및 기타 과학기술 서비스업",
    "73": "기타 전문, 과학 및 기술 서비스업",
    "74": "사업시설 관리 및 조경 서비스업", "75": "사업지원 서비스업",
    "76": "임대업(부동산 제외)",
    "84": "공공행정, 국방 및 사회보장 행정", "85": "교육 서비스업",
    "86": "보건업", "87": "사회복지 서비스업",
    "90": "창작, 예술 및 여가관련 서비스업", "91": "스포츠 및 오락관련 서비스업",
    "94": "협회 및 단체", "95": "개인 및 소비용품 수리업", "96": "기타 개인 서비스업",
}

# 소분류 (3자리) — **전부 실제 구성 종목으로 대조 확인한 것만** 둔다 (위 docstring 참고).
# 여기에 없는 코드는 이름을 비우고 중분류 이름으로 올라간다.
KSIC_GROUPS = {
    "107": "동물용 사료 및 조제식품 제조업",
    "108": "기타 식품 제조업",
    "139": "기타 섬유제품 제조업",
    "141": "봉제의복 제조업",
    "172": "골판지, 종이 상자 및 종이 용기 제조업",
    "201": "기초 화학물질 제조업",
    "204": "기타 화학제품 제조업",
    "205": "화학섬유 제조업",
    "221": "고무제품 제조업",
    "211": "기초 의약물질 및 생물학적 제제 제조업",
    "212": "의약품 제조업",
    "213": "의료용품 및 기타 의약 관련제품 제조업",
    "222": "플라스틱제품 제조업",
    "233": "시멘트, 석회, 플라스터 및 그 제품 제조업",
    "241": "1차 철강 제조업",
    "242": "1차 비철금속 제조업",
    "251": "구조용 금속제품, 탱크 및 증기발생기 제조업",
    "259": "기타 금속가공제품 제조업",
    "261": "반도체 제조업",
    "262": "전자부품 제조업",
    "263": "컴퓨터 및 주변장치 제조업",
    "264": "통신 및 방송 장비 제조업",
    "265": "영상 및 음향기기 제조업",
    "266": "마그네틱 및 광학 매체 제조업",
    "271": "의료용 기기 제조업",
    "272": "측정, 시험, 항해, 제어 및 기타 정밀기기 제조업",
    "281": "전동기, 발전기 및 전기 변환·공급·제어 장치 제조업",
    "282": "일차전지 및 축전지 제조업",
    "291": "일반 목적용 기계 제조업",
    "292": "특수 목적용 기계 제조업",
    "303": "자동차 신품 부품 제조업",
    "411": "건물 건설업",
    "412": "토목 건설업",
    "464": "생활용품 도매업",
    "465": "기계장비 및 관련 물품 도매업",
    "467": "기타 전문 도매업",
    "468": "상품 종합 도매업",
    "471": "종합 소매업",
    "582": "소프트웨어 개발 및 공급업",
    "591": "영화, 비디오물, 방송프로그램 제작 및 배급업",
    "620": "컴퓨터 프로그래밍, 시스템 통합 및 관리업",
    "631": "자료처리, 호스팅, 포털 및 기타 인터넷 정보매개 서비스업",
    "639": "기타 정보 서비스업",
    "641": "은행 및 저축기관",
    "642": "신탁업 및 집합투자업",
    "649": "기타 금융업",
    "661": "금융 지원 서비스업",
    "681": "부동산 임대 및 공급업",
    "701": "자연과학 및 공학 연구개발업",
    "715": "회사 본부 및 경영 컨설팅 서비스업",
    "721": "건축기술, 엔지니어링 및 관련 기술 서비스업",
    "739": "그 외 기타 전문, 과학 및 기술 서비스업",
    "761": "운송장비 임대업",
}


def load() -> Dict:
    global _cache
    if _cache is not None:
        return _cache
    with _lock:
        if _cache is not None:
            return _cache
        try:
            _cache = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            _cache = {"map": {}, "generated_at": "", "total": 0, "with_industry": 0}
    return _cache


def available() -> bool:
    return bool(load().get("map"))


def industry_of(code: str) -> str:
    """종목코드 → 표준산업분류 코드 (없으면 빈 문자열)."""
    entry = load().get("map", {}).get((code or "").strip())
    return str(entry.get("industry_code", "")) if entry else ""


def entry_of(code: str) -> Dict:
    return load().get("map", {}).get((code or "").strip(), {})


# ─────────────────────────────────────────────────────────────
# 이름 붙이기 (M5)
# ─────────────────────────────────────────────────────────────
def section_of(industry_code: str) -> Dict:
    """업종코드 → 대분류(알파벳) 정보."""
    major = str(industry_code or "")[:2]
    if not major.isdigit():
        return {"letter": "", "name": ""}
    number = int(major)
    for letter, name, low, high in KSIC_SECTIONS:
        if low <= number <= high:
            return {"letter": letter, "name": name}
    return {"letter": "", "name": ""}


def name_of(industry_code: str) -> Dict:
    """업종코드 → 이름. 3자리 소분류부터 보고 없으면 2자리 중분류로 올라간다.

    **어느 수준에서 찾았는지 함께 돌려준다.** '반도체 제조업(261)' 과
    '전자부품·컴퓨터·영상·음향·통신장비(26)' 는 폭이 완전히 다른데,
    이름만 보여 주면 읽는 사람이 그 차이를 알 수 없다.
    """
    code = str(industry_code or "").strip()
    if not code:
        return {"code": "", "name": "", "level": "", "resolved_digits": 0,
                "section": {"letter": "", "name": ""}, "found": False}

    group = KSIC_GROUPS.get(code[:3])
    division = KSIC_DIVISIONS.get(code[:2])
    section = section_of(code)

    if group:
        name, level, digits = group, "소분류", 3
    elif division:
        name, level, digits = division, "중분류", 2
    elif section["name"]:
        name, level, digits = section["name"], "대분류", 1
    else:
        name, level, digits = "", "", 0

    return {
        "code": code,
        "name": name,
        "level": level,
        "resolved_digits": digits,
        "division": division or "",
        "group": group or "",
        "section": section,
        "found": bool(name),
        "note": ("" if digits >= 3 else
                 "소분류 이름을 확인하지 못해 중분류 이름으로 표시한다 — 실제 업종은 더 좁다"
                 if digits == 2 else
                 "중분류 이름표에도 없어 대분류까지 올라갔다 — 업종이 매우 넓다"
                 if digits == 1 else
                 "이름표에 없는 코드다 — 코드만 표시하고 이름을 만들지 않는다"),
    }


def label(industry_code: str) -> str:
    """`261 반도체 제조업` 형태의 한 줄 표기 (리포트·차트 축 라벨용)."""
    row = name_of(industry_code)
    return f"{row['code']} {row['name']}".strip() if row["found"] else row["code"]


# ─────────────────────────────────────────────────────────────
# 구성 종목 · 산업 목록 (M5)
# ─────────────────────────────────────────────────────────────
def _groups(digits: int) -> Dict[str, List[str]]:
    """자릿수별 {접두어: [종목코드]} 색인. 자주 쓰이므로 만들어 둔다.

    ⚠️ **`load()` 를 잠금 밖에서 먼저 부른다.** `threading.Lock` 은 재진입이 안 돼서
    잠금을 쥔 채 `load()` 를 부르면 그쪽이 같은 잠금을 다시 잡으려다 영원히 멈춘다
    (`glossary._build_index` 에서 같은 이유로 배포본 504 가 났다).
    """
    global _group_index
    if _group_index is None:
        mapping = load().get("map", {})      # ← 잠금 밖에서 먼저 채운다
        with _lock:
            if _group_index is None:
                index: Dict[str, List[str]] = {}
                for code, entry in mapping.items():
                    industry = str(entry.get("industry_code", ""))
                    for width in range(2, 6):
                        if len(industry) >= width:
                            index.setdefault(f"{width}:{industry[:width]}", []).append(code)
                _group_index = index
    return _group_index


def members(prefix: str) -> List[Dict]:
    """그 업종 접두어에 속한 종목들 (`code`·`name`·`industry_code`·`market`)."""
    prefix = str(prefix or "").strip()
    if not prefix:
        return []
    mapping = load().get("map", {})
    index = _groups(len(prefix))
    codes = index.get(f"{len(prefix)}:{prefix}")
    if codes is None:                         # 5자리를 넘는 접두어 등 — 그때만 훑는다
        codes = [c for c, v in mapping.items()
                 if str(v.get("industry_code", "")).startswith(prefix)]
    return [{"code": c, **mapping[c]} for c in codes if c in mapping]


def directory(min_members: int = 3, digits: int = 3) -> List[Dict]:
    """산업 목록 — 화면 선택 상자와 IND-R H01 이 쓴다.

    `digits` 는 몇 자리를 한 산업으로 볼지다. 기본 3자리(소분류)이며,
    **구성 종목이 `min_members` 에 못 미치는 산업도 빼지 않고** 표에 남긴다.
    빼 버리면 "왜 내 업종이 목록에 없나" 를 설명할 수 없다.
    """
    mapping = load().get("map", {})
    buckets: Dict[str, List[str]] = {}
    for code, entry in mapping.items():
        industry = str(entry.get("industry_code", ""))
        if len(industry) >= digits:
            buckets.setdefault(industry[:digits], []).append(code)

    rows = []
    for prefix, codes in buckets.items():
        named = name_of(prefix)
        rows.append({
            "industry_code": prefix,
            "name": named["name"],
            "level": named["level"],
            "resolved_digits": named["resolved_digits"],
            "section": named["section"]["name"],
            "member_count": len(codes),
            "enough": len(codes) >= min_members,
            "sample": [mapping[c].get("name") for c in codes[:3] if c in mapping],
        })
    return sorted(rows, key=lambda r: (-r["member_count"], r["industry_code"]))


def resolve(query: str, digits: int = 3) -> Dict:
    """'261' · '반도체' · '반도체 제조업' → 업종 후보를 찾는다.

    숫자면 코드로, 글자면 이름으로 찾는다. 이름 검색은 **여러 개가 걸릴 수 있고**,
    그때 하나를 임의로 고르지 않고 후보를 다 돌려준다 (사람이 고른다 — 불변원칙 §2-7).
    """
    needle = str(query or "").strip()
    if not needle:
        return {"found": False, "reason": "산업명이나 업종코드가 비어 있다", "candidates": []}

    if needle.isdigit():
        named = name_of(needle)
        rows = members(needle)
        return {
            "found": bool(rows),
            "kind": "코드",
            "industry_code": needle,
            "name": named["name"],
            "level": named["level"],
            "member_count": len(rows),
            "candidates": [{"industry_code": needle, "name": named["name"],
                            "member_count": len(rows)}],
            "reason": "" if rows else f"업종코드 {needle} 로 시작하는 상장사가 맵에 없다",
        }

    lowered = needle.replace(" ", "").lower()
    catalog = directory(digits=digits)
    hits = [row for row in catalog
            if row["name"] and lowered in row["name"].replace(" ", "").lower()]
    if not hits:
        # 이름표에 없으면 **종목명**으로도 찾아 본다 ('삼성전자' 를 넣었을 때 그 업종을 준다)
        mapping = load().get("map", {})
        for code, entry in mapping.items():
            if lowered == str(entry.get("name", "")).replace(" ", "").lower():
                industry = str(entry.get("industry_code", ""))[:digits]
                named = name_of(industry)
                return {"found": True, "kind": "종목명", "industry_code": industry,
                        "name": named["name"], "level": named["level"],
                        "member_count": len(members(industry)),
                        "via": {"code": code, "name": entry.get("name")},
                        "candidates": [{"industry_code": industry, "name": named["name"],
                                        "member_count": len(members(industry))}]}
        return {"found": False, "kind": "이름",
                "reason": f"'{needle}' 에 해당하는 업종을 이름표에서 못 찾았다",
                "candidates": []}

    top = hits[0]
    return {
        "found": True,
        "kind": "이름",
        "industry_code": top["industry_code"],
        "name": top["name"],
        "level": top["level"],
        "member_count": top["member_count"],
        "candidates": [{"industry_code": r["industry_code"], "name": r["name"],
                        "member_count": r["member_count"]} for r in hits[:8]],
        "ambiguous": len(hits) > 1,
    }


def peers_by_industry(code: str, min_peers: int = 5) -> Dict:
    """같은 업종 종목코드 목록.

    접두어를 5자리부터 좁게 잡아 보고, 후보가 `min_peers` 에 못 미치면 한 자리씩 줄인다.
    **어느 자릿수에서 찾았는지 함께 돌려준다** — 2자리까지 내려갔다면 "같은 대분류일 뿐"
    이라는 뜻이고, 리포트가 그 사실을 밝혀야 한다.
    """
    mine = industry_of(code)
    if not mine:
        return {"available": False, "reason": "이 종목의 업종코드가 맵에 없다",
                "peers": [], "industry_code": "", "matched_digits": 0}

    mapping = load().get("map", {})
    for digits in range(len(mine), 1, -1):
        prefix = mine[:digits]
        peers = [c for c, v in mapping.items()
                 if c != code and str(v.get("industry_code", "")).startswith(prefix)]
        if len(peers) >= min_peers or digits == 2:
            named = name_of(prefix)
            return {
                "available": bool(peers),
                "peers": peers,
                "industry_code": mine,
                "industry_name": named["name"],
                "matched_prefix": prefix,
                "matched_digits": digits,
                # KSIC 는 2자리=중분류 · 3자리=소분류 · 4~5자리=세·세세분류다.
                # 2자리까지 내려갔으면 "같은 업종" 이라 부르기 어려우므로 그 사실을 밝힌다.
                "note": (f"업종코드 {digits}자리({prefix} {named['name']})까지 넓혀서 "
                         f"{len(peers)}곳을 찾았다"
                         + (" — 세분류가 아니라 **중분류** 수준이라 업종이 꽤 넓다"
                            if digits <= 2 else "")),
                "reason": "" if peers else "같은 중분류에도 다른 상장사가 없다",
            }
    return {"available": False, "reason": "같은 업종을 못 찾았다", "peers": [],
            "industry_code": mine, "matched_digits": 0}


def stats() -> Dict:
    data = load()
    catalog = directory()
    return {
        "available": available(),
        "path": str(DATA_FILE),
        "generated_at": data.get("generated_at", ""),
        "total": data.get("total", 0),
        "with_industry": data.get("with_industry", 0),
        "source": data.get("source", ""),
        "industries": len(catalog),
        "named_industries": sum(1 for row in catalog if row["name"]),
        "divisions_named": len(KSIC_DIVISIONS),
        "groups_named": len(KSIC_GROUPS),
        "note": ("KSIC 이름표는 코드에 두었다. 3자리 이름은 실제 구성 종목으로 대조 확인한 것만 "
                 "있고, 확인하지 못한 코드는 중분류 이름으로 올라간다."),
    }
