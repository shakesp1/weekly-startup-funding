"""
주간 기술 동향 (국내 대학 연구 발표) 자동 수집 스크립트 (AI 미사용, 규칙 기반)

동작 방식:
1. 네이버 뉴스 검색 API + 구글 뉴스 검색 RSS로 대학 연구/기술 발표 관련 뉴스를 수집
2. 최근 7일 이내 뉴스만 필터링, 중복 링크 제거
3. 정규식 + 대학교 목록으로 대학교 / 연구자명 / 연구실명 / 기술명을 최대한 추출
   (완벽하지 않음 - 기사마다 표현이 달라서 일부는 "확인 필요"로 표시됨)
4. 기존 데이터(docs/data/tech.json)와 합쳐서 중복 없이 저장

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

DATA_PATH = os.path.join(os.path.dirname(__file__), "..", "docs", "data", "tech.json")

LOOKBACK_DAYS = 8

SEARCH_KEYWORDS = [
    "대학교 연구팀 기술 개발",
    "국내 연구진 세계 최초 개발",
    "KAIST 연구팀 개발",
    "포스텍 연구팀 개발",
    "서울대 연구팀 개발",
    "연세대 연구팀 개발",
    "고려대 연구팀 개발",
    "국내 대학 신기술 개발",
    "연구팀 네이처 논문 게재",
    "연구팀 사이언스 논문 게재",
    "국내 대학 기술이전",
]

GOOGLE_NEWS_QUERIES = [
    "대학교 연구팀 기술 개발",
    "국내 연구진 세계 최초 개발",
    "KAIST 연구팀 개발",
    "서울대 연구팀 개발",
    "연구팀 네이처 논문",
]

RSS_FEEDS = [
    "http://platum.kr/feed",
    "http://www.venturesquare.net/feed",
]

# 주요 대학교 목록 (정식명칭 + 흔히 쓰는 축약형 모두 포함, substring 매칭)
KNOWN_UNIVERSITIES = [
    "서울대학교", "서울대", "KAIST", "카이스트", "포항공과대학교", "포스텍", "POSTECH",
    "연세대학교", "연세대", "고려대학교", "고려대", "성균관대학교", "성균관대",
    "한양대학교", "한양대", "중앙대학교", "중앙대", "경희대학교", "경희대",
    "서강대학교", "서강대", "이화여자대학교", "이화여대", "울산과학기술원", "UNIST",
    "광주과학기술원", "GIST", "대구경북과학기술원", "DGIST", "부산대학교", "부산대",
    "전남대학교", "전남대", "전북대학교", "전북대", "충남대학교", "충남대",
    "충북대학교", "충북대", "경북대학교", "경북대", "경상국립대학교", "경상국립대",
    "인하대학교", "인하대", "아주대학교", "아주대", "건국대학교", "건국대",
    "동국대학교", "동국대", "숭실대학교", "숭실대", "홍익대학교", "홍익대",
    "세종대학교", "세종대", "광운대학교", "광운대", "한국외국어대학교", "한국외대",
    "서울시립대학교", "서울시립대", "명지대학교", "명지대", "가톨릭대학교", "가톨릭대",
]

STOPWORDS_TITLE = {"연구팀", "대학교", "국내"}

# 기술 카테고리 키워드 매핑. 위에서부터 순서대로 검사하며, 먼저 매칭되는 카테고리로 분류됨
CATEGORY_KEYWORDS = [
    ("AI·소프트웨어", ["인공지능", "AI", "딥러닝", "머신러닝", "생성형", "알고리즘", "소프트웨어"]),
    ("반도체", ["반도체", "웨이퍼", "트랜지스터", "파운드리", "낸드", "D램"]),
    ("배터리·에너지", ["배터리", "이차전지", "태양광", "수소", "연료전지", "에너지저장"]),
    ("바이오·헬스케어", ["바이오", "신약", "백신", "유전자", "세포", "의료", "헬스케어", "치료제", "진단", "항암"]),
    ("로봇", ["로봇", "로보틱스"]),
    ("모빌리티", ["자율주행", "모빌리티", "전기차", "드론", "UAM"]),
    ("소재·화학", ["신소재", "나노", "화학", "촉매", "그래핀", "반도체 소재"]),
    ("양자기술", ["양자", "퀀텀"]),
    ("통신·네트워크", ["5G", "6G", "통신", "네트워크"]),
    ("우주·항공", ["우주", "위성", "항공", "발사체"]),
]


def extract_category(text: str) -> str:
    for label, keywords in CATEGORY_KEYWORDS:
        for kw in keywords:
            if re.fullmatch(r"[A-Za-z0-9]+", kw):
                # 영문/숫자로만 된 짧은 키워드(AI, 5G 등)는 다른 단어 안에 우연히 포함되는 걸
                # 막기 위해 앞뒤가 영문/숫자가 아닐 때만 매칭 (예: "KAIST" 안의 "AI"는 제외)
                pattern = r"(?<![A-Za-z0-9])" + re.escape(kw) + r"(?![A-Za-z0-9])"
                if re.search(pattern, text, re.IGNORECASE):
                    return label
            else:
                if kw in text:
                    return label
    return "기타"


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


def parse_naver_pubdate(pubdate_str: str) -> datetime.datetime:
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
                pub = parse_naver_pubdate(item.get("pubDate", ""))
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


def _collect_from_feed_entries(entries, cutoff) -> list:
    collected = []
    for entry in entries:
        link = entry.get("link")
        if not link or not entry.get("published_parsed"):
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
    return collected


def collect_from_rss_feeds(cutoff: datetime.datetime) -> list:
    collected = []
    for url in RSS_FEEDS:
        try:
            feed = feedparser.parse(url)
        except Exception as e:
            print(f"[경고] RSS 수집 실패 ({url}): {e}")
            continue
        collected += _collect_from_feed_entries(feed.entries, cutoff)
    print(f"[정보] RSS 피드에서 후보 {len(collected)}건")
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
        collected += _collect_from_feed_entries(feed.entries, cutoff)
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


def extract_university(text: str):
    for uni in KNOWN_UNIVERSITIES:
        if uni in text:
            return uni
    return None


def extract_researcher(text: str):
    m = re.search(r"([가-힣]{2,4})\s*교수", text)
    if m:
        return f"{m.group(1)} 교수"
    return None


def extract_lab(text: str):
    m = re.search(r"([가-힣A-Za-z0-9]{2,20})\s*(연구실|연구센터|랩)\b", text)
    if m:
        return f"{m.group(1)}{m.group(2)}"
    return None


def extract_tech_name(title: str):
    # 따옴표(", ', 「」)로 묶인 기술명 우선 추출
    m = re.search(r"[\"'「]([^\"'」]{2,30})[\"'」]", title)
    if m:
        return m.group(1)
    return title


def build_summary(title: str, description: str) -> str:
    text = description if description else title
    if len(text) > 140:
        text = text[:140].rsplit(" ", 1)[0] + "…"
    return text


def process_news(raw_news: list) -> list:
    results = []
    for item in raw_news:
        combined_text = f"{item['title']} {item['description']}"

        university = extract_university(combined_text)
        researcher = extract_researcher(combined_text)

        # 최소 관련성 필터: 대학명 또는 교수명 중 하나는 감지되어야 채택
        if not university and not researcher:
            continue

        results.append(
            {
                "category": extract_category(combined_text),
                "university": university or "확인 필요",
                "lab": extract_lab(combined_text) or "확인 필요",
                "researcher": researcher or "확인 필요",
                "tech_name": extract_tech_name(item["title"]),
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
    return entry["source_url"]


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
    print(f"[정보] 규칙 기반으로 추출된 기술동향 후보: {len(extracted)}건")

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
