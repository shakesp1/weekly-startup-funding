"""
주간 스타트업 투자유치 현황 자동 수집 스크립트 (AI 미사용, 규칙 기반)

동작 방식:
1. 네이버 뉴스 검색 API로 투자유치 관련 키워드 뉴스를 수집
2. 최근 7일 이내 뉴스만 필터링, 중복 링크 제거
3. 정규식 + 키워드 목록으로 회사명 / 투자단계 / 투자금액 / 투자자를 최대한 추출
4. 사업내용·투자포인트는 별도로 "요약"해내지 않고, 뉴스 자체의 요약문(description)을 정리해서 사용
   (AI를 쓰지 않기 때문에 완벽한 요약은 아니며, 회사명 추출도 100% 정확하지 않을 수 있음)
5. 기존 데이터(docs/data/all.json)와 합쳐서 중복 없이 저장

필요한 환경변수:
- NAVER_CLIENT_ID, NAVER_CLIENT_SECRET : 네이버 개발자센터에서 발급
"""

import os
import re
import json
import time
import html
import datetime
import urllib.parse
import requests
import feedparser

NAVER_CLIENT_ID = os.environ.get("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.environ.get("NAVER_CLIENT_SECRET")

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "all.json")

SEARCH_KEYWORDS = [
    "스타트업 시드 투자유치",
    "스타트업 프리시리즈A 투자유치",
    "스타트업 시리즈A 투자유치",
    "스타트업 시리즈B 투자유치",
    "스타트업 시리즈C 투자유치",
    "스타트업 시리즈D 투자유치",
    "스타트업 투자유치",
    "스타트업 투자 유치",
    "스타트업 전략적투자",
]

LOOKBACK_DAYS = 8

# 스타트업 전문 매체가 공식 제공하는 RSS 피드 (크롤링이 아니라 정식 배포 채널이라 안전함)
RSS_FEEDS = [
    "http://platum.kr/feed",
    "http://www.venturesquare.net/feed",
]

# 구글 뉴스도 공식 RSS 검색 결과를 제공함 (역시 정식 배포 채널)
GOOGLE_NEWS_QUERIES = [
    "스타트업 시드 투자유치",
    "스타트업 시리즈A 투자유치",
    "스타트업 시리즈B 투자유치",
    "스타트업 시리즈C 투자유치",
    "스타트업 투자유치",
]

# 투자단계 키워드. 긴 표현을 먼저 검사해야 "시리즈A"가 "프리시리즈A"를 가리는 실수를 막을 수 있음
STAGE_PATTERNS = [
    ("프리시리즈A", ["프리시리즈A", "프리시리즈 A", "pre-series a"]),
    ("프리시리즈B", ["프리시리즈B", "프리시리즈 B", "pre-series b"]),
    ("시리즈A", ["시리즈A", "시리즈 A", "series a"]),
    ("시리즈B", ["시리즈B", "시리즈 B", "series b"]),
    ("시리즈C", ["시리즈C", "시리즈 C", "series c"]),
    ("시리즈D", ["시리즈D", "시리즈 D", "series d"]),
    ("시리즈E", ["시리즈E", "시리즈 E", "series e"]),
    ("브릿지", ["브릿지 투자", "브릿지투자"]),
    ("전략적투자", ["전략적투자", "전략적 투자"]),
    ("시드", ["시드투자", "시드 투자"]),
]

# 주요 투자자(VC/액셀러레이터/CVC 등) 목록. 필요하면 자유롭게 추가하세요.
KNOWN_INVESTORS = [
    "한국투자액셀러레이터", "블루포인트파트너스", "퓨처플레이", "슈미츠", "ZDVC",
    "노바벤처스", "LB인베스트먼트", "알바트로스인베스트먼트", "멜리오라파트너스", "SBI인베스트먼트",
    "DSC인베스트먼트", "IMM인베스트먼트", "KDB산업은행", "SV인베스트먼트", "에이티넘인베스트먼트",
    "GFT벤처스", "미래에셋벤처투자", "미래에셋캐피탈", "스마일게이트인베스트먼트", "스파크랩",
    "NEA", "네이버벤처스", "래디컬벤처스", "한국투자파트너스", "인덱스벤처스",
    "코오롱인베스트먼트", "신영증권", "티인베스트먼트", "IBK기업은행", "데브시스터즈벤처스",
    "코메스인베스트먼트", "호라이즌인베스트먼트", "한국산업은행", "두나무앤파트너스", "티에이오소시에이츠",
    "카카오벤처스", "알토스벤처스", "소프트뱅크벤처스", "프라이머", "하나벤처스",
    "본엔젤스", "SK증권", "메리츠증권", "우리금융캐피탈", "신한캐피탈",
    "KB인베스트먼트", "산업은행", "중소기업은행", "한국벤처투자", "케이런벤처스",
    "빅베이슨캐피탈", "에스티캐피탈", "타임와이즈인베스트먼트", "SL인베스트먼트", "패스파인더H",
    "디에스씨인베스트먼트", "쿼드자산운용", "우신벤처투자", "인터베스트", "라구나인베스트먼트",
]

STOPWORDS_FOR_COMPANY = {"스타트업", "한편", "이번", "국내", "관련", "업계", "기업"}

# 이런 접미사로 끝나면 회사명이 아니라 투자자(VC/증권/은행 등)일 가능성이 높음
INVESTOR_SUFFIX_HINTS = [
    "인베스트먼트", "벤처스", "캐피탈", "파트너스", "액셀러레이터", "자산운용",
    "증권", "은행", "벤처투자", "인베스트", "창투", "기술투자", "펀드",
]


def _looks_like_investor(name: str) -> bool:
    if name in KNOWN_INVESTORS:
        return True
    return any(hint in name for hint in INVESTOR_SUFFIX_HINTS)


# 정부기관/지자체/대기업 등 "스타트업"이 아닌 주체. 뉴스 제목 맨 앞에 흔히 등장해서
# 회사명으로 잘못 추출되는 경우를 막기 위한 차단 목록 (정확히 일치할 때만 차단)
ENTITY_BLOCKLIST = {
    # 정부/공공기관
    "정부", "청와대", "국회", "중기부", "중소벤처기업부", "금융위", "금융위원회",
    "기획재정부", "산업통상자원부", "과학기술정보통신부", "고용노동부", "국토교통부",
    "특허청", "관세청", "국세청",
    # 광역자치단체 (시/도 표기 여러 형태 포함)
    "서울", "서울시", "부산", "부산시", "대구", "대구시", "인천", "인천시",
    "광주", "광주시", "대전", "대전시", "울산", "울산시", "세종", "세종시",
    "경기", "경기도", "강원", "강원도", "충북", "충북도", "충청북도",
    "충남", "충남도", "충청남도", "전북", "전북도", "전라북도",
    "전남", "전남도", "전라남도", "경북", "경북도", "경상북도",
    "경남", "경남도", "경상남도", "제주", "제주도",
    # 대기업 그룹 (전략적투자 뉴스에서 주어로 자주 등장)
    "삼성", "삼성전자", "삼성SDS", "삼성SDI", "LG", "LG전자", "LG유플러스",
    "SK", "SK그룹", "SK텔레콤", "SK하이닉스", "현대", "현대차", "현대자동차",
    "현대모비스", "롯데", "롯데그룹", "한화", "한화그룹", "GS", "GS그룹",
    "신세계", "두산", "포스코", "KT", "CJ", "카카오", "네이버", "쿠팡",
}


def clean_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", "", raw or "")
    return html.unescape(text).strip()


def search_naver_news(query: str) -> list:
    url = "https://openapi.naver.com/v1/search/news.json"
    headers = {
        "X-Naver-Client-Id": NAVER_CLIENT_ID,
        "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    }
    params = {"query": query, "display": 100, "sort": "date"}
    resp = requests.get(url, headers=headers, params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("items", [])


def parse_pubdate(pubdate_str: str) -> datetime.datetime:
    return datetime.datetime.strptime(pubdate_str, "%a, %d %b %Y %H:%M:%S %z")


def _within_cutoff(pub: datetime.datetime, cutoff: datetime.datetime) -> bool:
    if pub.tzinfo is None:
        pub = pub.replace(tzinfo=datetime.timezone(datetime.timedelta(hours=9)))
    return pub >= cutoff


def collect_from_naver(cutoff: datetime.datetime) -> list:
    collected = []
    for kw in SEARCH_KEYWORDS:
        try:
            items = search_naver_news(kw)
        except requests.HTTPError as e:
            print(f"[경고] 네이버 '{kw}' 검색 실패: {e}")
            continue

        for item in items:
            link = item.get("originallink") or item.get("link")
            if not link:
                continue
            try:
                pub = parse_pubdate(item.get("pubDate", ""))
            except ValueError:
                continue
            if not _within_cutoff(pub, cutoff):
                continue
            collected.append(
                {
                    "title": clean_text(item.get("title", "")),
                    "description": clean_text(item.get("description", "")),
                    "link": link,
                    "pub_date": pub.strftime("%Y-%m-%d"),
                }
            )
        time.sleep(0.2)
    print(f"[정보] 네이버 뉴스에서 후보 {len(collected)}건")
    return collected


def collect_from_rss_feeds(cutoff: datetime.datetime) -> list:
    collected = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[경고] RSS 수집 실패 ({url}): {e}")
            continue

        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue
            if not entry.get("published_parsed"):
                continue
            pub = datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc).astimezone(
                datetime.timezone(datetime.timedelta(hours=9))
            )
            if not _within_cutoff(pub, cutoff):
                continue
            collected.append(
                {
                    "title": clean_text(entry.get("title", "")),
                    "description": clean_text(entry.get("summary", "")),
                    "link": link,
                    "pub_date": pub.strftime("%Y-%m-%d"),
                }
            )
    print(f"[정보] RSS 피드({len(RSS_FEEDS)}개)에서 후보 {len(collected)}건")
    return collected


def collect_from_google_news(cutoff: datetime.datetime) -> list:
    collected = []
    for query in GOOGLE_NEWS_QUERIES:
        encoded = urllib.parse.quote(query)
        url = f"https://news.google.com/rss/search?q={encoded}&hl=ko&gl=KR&ceid=KR:ko"
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[경고] 구글 뉴스 검색 실패 ('{query}'): {e}")
            continue

        for entry in feed.entries:
            link = entry.get("link")
            if not link:
                continue
            if not entry.get("published_parsed"):
                continue
            pub = datetime.datetime(*entry.published_parsed[:6], tzinfo=datetime.timezone.utc).astimezone(
                datetime.timezone(datetime.timedelta(hours=9))
            )
            if not _within_cutoff(pub, cutoff):
                continue
            collected.append(
                {
                    "title": clean_text(entry.get("title", "")),
                    "description": clean_text(entry.get("summary", "")),
                    "link": link,
                    "pub_date": pub.strftime("%Y-%m-%d"),
                }
            )
        time.sleep(0.2)
    print(f"[정보] 구글 뉴스 검색에서 후보 {len(collected)}건")
    return collected


def collect_raw_news() -> list:
    cutoff = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=9))) - datetime.timedelta(
        days=LOOKBACK_DAYS
    )

    all_items = []
    all_items += collect_from_naver(cutoff)
    all_items += collect_from_rss_feeds(cutoff)
    all_items += collect_from_google_news(cutoff)

    seen_links = set()
    deduped = []
    for item in all_items:
        if item["link"] in seen_links:
            continue
        seen_links.add(item["link"])
        deduped.append(item)

    print(f"[정보] 전체 소스 합산 후 중복 제거된 후보 뉴스: {len(deduped)}건")
    return deduped


def extract_stage(text: str):
    lowered = text.lower()
    for label, variants in STAGE_PATTERNS:
        for v in variants:
            if v.lower() in lowered:
                return label
    return None


def extract_amount(text: str):
    # 예: "77억원", "1,500억원", "3조원", "2조5000억원"
    m = re.search(r"(\d[\d,]{0,4}\s*조\s*\d[\d,]{0,4}\s*억\s*원)", text)
    if m:
        return re.sub(r"\s+", "", m.group(1))
    m = re.search(r"(\d[\d,]{0,6}\s*(?:억|조)\s*원)", text)
    if m:
        return re.sub(r"\s+", "", m.group(1))
    if "비공개" in text:
        return "비공개"
    return None


def extract_investors(text: str):
    found = [name for name in KNOWN_INVESTORS if name in text]
    # 등장 순서 유지 + 중복 제거
    seen = set()
    ordered = []
    for name in found:
        if name not in seen:
            seen.add(name)
            ordered.append(name)
    return ", ".join(ordered) if ordered else None


def extract_company(title: str):
    candidates = []

    # 패턴 1: "회사명, ~~" 또는 "회사명·~~" 형태 (문장 맨 앞)
    m = re.match(r"^([가-힣A-Za-z0-9&\-]{2,20})\s*[,·]", title)
    if m:
        candidates.append(m.group(1))

    # 패턴 2: "회사명(이)가/은/는 ~~" 형태 (문장 맨 앞)
    m = re.match(r"^([가-힣A-Za-z0-9&\-]{2,20})\s*(가|은|는|이)\s", title)
    if m:
        candidates.append(m.group(1))

    # 패턴 3: "~~가 [회사명]에 [금액/투자단계 등] 투자" 형태 (투자자가 주어로 오는 문장에서
    # 실제 투자받은 회사명을 "~에" 앞에서 찾음)
    m = re.search(
        r"([가-힣A-Za-z0-9&\-]{2,20})\s*에\s*[가-힣A-Za-z0-9,\.\s]{0,20}?투자",
        title,
    )
    if m:
        candidates.append(m.group(1))

    for cand in candidates:
        if cand in STOPWORDS_FOR_COMPANY:
            continue
        if cand in ENTITY_BLOCKLIST:
            continue
        if _looks_like_investor(cand):
            continue
        return cand

    return None


def build_summary(title: str, description: str) -> str:
    # 제목과 요약문을 합쳐서 너무 길지 않게 정리 (AI 요약 대신 원문 요약문을 그대로 사용)
    text = description if description else title
    if len(text) > 140:
        text = text[:140].rsplit(" ", 1)[0] + "…"
    return text


def process_news(raw_news: list) -> list:
    results = []
    for item in raw_news:
        combined_text = f"{item['title']} {item['description']}"

        stage = extract_stage(combined_text)
        amount = extract_amount(combined_text)

        # 최소한의 관련성 필터: 투자단계 또는 투자금액 중 하나는 감지되어야 채택
        if not stage and not amount:
            continue

        company = extract_company(item["title"])
        if not company:
            continue

        investors = extract_investors(combined_text) or "확인 필요 (원문 참조)"

        results.append(
            {
                "company": company,
                "stage": stage or "미상",
                "amount": amount or "비공개",
                "investors": investors,
                "summary": build_summary(item["title"], item["description"]),
                "source_title": item["title"],
                "source_url": item["link"],
                "pub_date": item["pub_date"],
            }
        )
    return results


def load_existing() -> list:
    if os.path.exists(DATA_PATH):
        with open(DATA_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def dedup_key(entry: dict) -> str:
    return f"{entry['company']}|{entry['stage']}|{entry['amount']}"


def iso_week_label(date_str: str) -> str:
    d = datetime.datetime.strptime(date_str, "%Y-%m-%d")
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def main():
    missing = [
        name
        for name, val in [
            ("NAVER_CLIENT_ID", NAVER_CLIENT_ID),
            ("NAVER_CLIENT_SECRET", NAVER_CLIENT_SECRET),
        ]
        if not val
    ]
    if missing:
        raise SystemExit(f"환경변수가 설정되지 않았습니다: {', '.join(missing)}")

    raw_news = collect_raw_news()
    extracted = process_news(raw_news)
    print(f"[정보] 규칙 기반으로 추출된 투자유치 후보: {len(extracted)}건")

    existing = load_existing()
    existing_keys = {dedup_key(e) for e in existing}

    new_entries = []
    for e in extracted:
        e["week"] = iso_week_label(e["pub_date"])
        key = dedup_key(e)
        if key in existing_keys:
            continue
        existing_keys.add(key)
        new_entries.append(e)

    print(f"[정보] 신규 항목 (중복 제외): {len(new_entries)}")

    combined = existing + new_entries
    combined.sort(key=lambda e: e["pub_date"], reverse=True)

    os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(combined, f, ensure_ascii=False, indent=2)

    print(f"[완료] 총 {len(combined)}건 저장 -> {DATA_PATH}")


if __name__ == "__main__":
    main()
