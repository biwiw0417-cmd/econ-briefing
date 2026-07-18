# -*- coding: utf-8 -*-
"""매일 아침 경제 브리핑 JSON 생성 스크립트 (Gemini 무료 티어 버전).

1. Google 뉴스 RSS에서 오늘의 경제 뉴스 헤드라인 수집 (무료, 키 불필요)
2. Gemini API(무료 티어)로 브리핑 JSON 생성
3. data/briefings/YYYY-MM-DD.json 저장 + curriculum.json 진도/terms.json 용어 갱신

환경변수: GEMINI_API_KEY (필수), BRIEFING_DATE (테스트용, YYYY-MM-DD)
"""
import json
import os
import re
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KST = timezone(timedelta(hours=9))

# LLM 설정 — 나중에 Claude 등으로 바꾸려면 call_llm()만 교체하면 됨
# 앞의 모델이 혼잡(503/타임아웃)하면 뒤의 모델로 자동 폴백
GEMINI_MODELS = [
    "gemini-flash-latest",
    "gemini-3-flash-preview",
    "gemini-flash-lite-latest",
]
GEMINI_URL_TMPL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)

NEWS_FEEDS = [
    ("한국", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=ko&gl=KR&ceid=KR:ko"),
    ("미국/글로벌", "https://news.google.com/rss/headlines/section/topic/BUSINESS?hl=en-US&gl=US&ceid=US:en"),
]
MAX_HEADLINES_PER_FEED = 15

SYSTEM_PROMPT = """당신은 경제를 전혀 모르는 이공계 대학원생을 위한 아침 경제 브리핑 작가입니다.

톤 지시:
- 경제를 전혀 모르는 이공계 대학원생에게 설명하듯, 전문용어는 반드시 풀어서 설명합니다.
- 비유는 일상 생활이나 실험실 상황에서 가져옵니다.
- 존댓말을 사용합니다.

출력 규칙 (매우 중요):
- 반드시 아래 스키마의 JSON **하나만** 출력합니다. JSON 앞뒤에 설명, 인사, 마크다운 펜스 등 다른 텍스트를 절대 붙이지 마세요.

{
  "date": "YYYY-MM-DD",
  "news": [
    {"title": "", "summary": "", "why_it_matters": "", "link_to_lesson": "", "source_name": ""},
    {"title": "", "summary": "", "why_it_matters": "", "link_to_lesson": "", "source_name": ""}
  ],
  "lesson": {"unit": 0, "topic": "", "body": "", "news_connection": ""},
  "term": {"term": "", "definition": "", "analogy": "", "usage": ""},
  "review": [{"term": "", "learned_date": "", "refresher": ""}],
  "quiz": [
    {"question": "", "choices": ["", "", "", ""], "answer_index": 0, "explanation": ""},
    {"question": "", "choices": ["", "", "", ""], "answer_index": 0, "explanation": ""}
  ]
}

섹션별 작성 기준:
- news: 사용자 프롬프트에 제공된 실제 뉴스 헤드라인 목록에서 한국 1건 + 미국/글로벌 1건을 고릅니다.
  초보자가 알아두면 좋은 구조적 이슈(금리, 물가, 환율, 고용, 대형 정책)를 우선하고,
  단타성 종목 뉴스나 루머성 기사는 제외합니다.
  기사 본문은 제공되지 않으므로, 헤드라인이 다루는 이슈의 일반적으로 알려진 배경과 맥락을 쉽게 설명하되,
  확실하지 않은 구체적 수치나 세부 사실은 지어내지 마세요.
  summary는 3~4문장의 쉬운 설명, why_it_matters는 "왜 중요한가" 2문장,
  link_to_lesson은 오늘의 커리큘럼/용어와의 연결 1문장, source_name은 언론사 이름입니다.
  글로벌 뉴스의 title은 한국어로 번역해서 넣으세요.
- lesson: body는 400~600자. 일상 비유로 도입 → 개념 정의 → 실제 경제/시장에서 어떻게 작동하는지 순서로 강의식 설명.
  news_connection은 오늘 뉴스와 연결점이 있으면 한 줄, 없으면 빈 문자열.
- term: 오늘 뉴스 또는 lesson에 등장한 용어 1개를 플래시카드식으로 정리.
  최근에 이미 다룬 용어(사용자 프롬프트에 목록 제공)와 중복되지 않게 고르세요.
- review: 사용자 프롬프트에 주어진 복습 대상 용어 각각에 대해 "기억나시나요?" 형식의 재노출 문단(refresher)을 씁니다.
  해당 용어가 오늘 뉴스와 연결되면 그 맥락으로, 아니면 새 예문으로 설명합니다. 복습 대상이 없으면 빈 배열 [].
- quiz: 정확히 2문제. 1번은 오늘의 lesson에서, 2번은 오늘의 news에서 출제. 객관식 4지선다.
  answer_index는 0~3, explanation은 초보자용 해설.
"""


def today_kst() -> datetime:
    override = os.environ.get("BRIEFING_DATE")  # 테스트용: YYYY-MM-DD
    if override:
        return datetime.strptime(override, "%Y-%m-%d").replace(tzinfo=KST)
    return datetime.now(KST)


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def save_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- 뉴스 수집

def fetch_headlines() -> list:
    """Google 뉴스 RSS에서 (지역, 제목, 언론사) 목록을 수집."""
    headlines = []
    for region, url in NEWS_FEEDS:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                xml_text = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            print(f"[generate] RSS 수집 실패({region}): {e}")
            continue
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            source = (item.findtext("source") or "").strip()
            if title:
                headlines.append({"region": region, "title": title, "source": source})
            if sum(1 for h in headlines if h["region"] == region) >= MAX_HEADLINES_PER_FEED:
                break
    return headlines


# ---------------------------------------------------------------- LLM 호출

def call_llm(user_prompt: str) -> str:
    """Gemini API 호출 (JSON 모드). 429/일시 오류는 재시도."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("GEMINI_API_KEY 환경변수가 설정되지 않았습니다.")
        sys.exit(1)

    body = json.dumps({
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": [{"text": user_prompt}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 8192,
        },
    }).encode("utf-8")

    last_err = None
    for attempt in range(3):
        for model in GEMINI_MODELS:
            try:
                req = urllib.request.Request(
                    GEMINI_URL_TMPL.format(model=model),
                    data=body,
                    headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
                )
                with urllib.request.urlopen(req, timeout=180) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                print(f"[generate] 모델 사용: {model}")
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except Exception as e:
                last_err = e
                print(f"[generate] {model} 호출 실패({e}) → 다음 모델/재시도")
        time.sleep(20 * (attempt + 1))
    raise RuntimeError(f"Gemini API 호출 실패 (모든 모델/재시도 소진): {last_err}")


# ---------------------------------------------------------------- 프롬프트 구성

def pick_lesson(curriculum: list, is_weekend: bool, today: datetime):
    """평일: 미완료 첫 항목. 주말: 이번 주에 완료한 항목들(복습 대상)."""
    if not is_weekend:
        for item in curriculum:
            if not item["completed"]:
                return item, []
        return None, []  # 40일 완주
    cutoff = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    week_topics = [it for it in curriculum if it["completed"] and it["date"] and it["date"] >= cutoff]
    return None, week_topics


def find_review_terms(terms: list, today: datetime):
    """3일 전, 7일 전에 배운 용어를 간격 반복용으로 추출."""
    targets = []
    for days in (3, 7):
        d = (today - timedelta(days=days)).strftime("%Y-%m-%d")
        for t in terms:
            if t["date"] == d:
                targets.append(t)
                break
    return targets


def build_user_prompt(today, lesson_item, week_topics, review_terms, recent_terms, headlines):
    date_str = today.strftime("%Y-%m-%d")
    weekday_kr = "월화수목금토일"[today.weekday()]
    lines = [f"오늘 날짜: {date_str} ({weekday_kr}요일, KST)", ""]

    lines.append("오늘의 뉴스 헤드라인 후보 (여기서 한국 1건 + 미국/글로벌 1건 선정):")
    if headlines:
        for h in headlines:
            src = f" ({h['source']})" if h["source"] else ""
            lines.append(f"- [{h['region']}] {h['title']}{src}")
    else:
        lines.append("- (수집 실패: 오늘의 일반적인 경제 이슈 중 교육적으로 의미 있는 주제 2개를 대신 다루세요)")

    lines.append("")
    if lesson_item:
        lines += [
            "오늘의 커리큘럼 (lesson 섹션 주제):",
            f"- Unit {lesson_item['unit']}. {lesson_item['unit_title']} — 「{lesson_item['topic']}」",
            f"- lesson.unit은 {lesson_item['unit']}, lesson.topic은 \"{lesson_item['topic']}\"으로 그대로 넣으세요.",
        ]
    else:
        topics = ", ".join(f"「{t['topic']}」" for t in week_topics) or "(이번 주 학습 기록 없음)"
        lines += [
            "오늘은 주말입니다. 새 진도 대신 이번 주에 배운 개념들의 복습 요약을 lesson 섹션에 씁니다.",
            f"- 이번 주 학습한 개념: {topics}",
            "- lesson.unit은 0, lesson.topic은 \"주간 복습\"으로 넣고, body에서 위 개념들을 서로 연결하며 복습 요약하세요.",
        ]

    lines.append("")
    if review_terms:
        lines.append("복습 코너(review 섹션) 대상 용어:")
        for t in review_terms:
            lines.append(f"- {t['term']} (배운 날짜: {t['date']}, 정의: {t['definition']})")
    else:
        lines.append("복습 대상 용어가 아직 없습니다. review는 빈 배열 []로 하세요.")

    lines.append("")
    if recent_terms:
        lines.append("최근 7일간 이미 다룬 용어 (term 섹션에서 중복 금지): " + ", ".join(recent_terms))
    else:
        lines.append("아직 다룬 용어가 없습니다. term은 자유롭게 고르세요.")

    lines += ["", "위 조건으로 오늘의 브리핑 JSON을 생성하세요."]
    return "\n".join(lines)


# ---------------------------------------------------------------- 파싱/검증

def extract_json(text: str) -> dict:
    """```json 펜스 제거 후 파싱. 실패 시 첫 '{'~마지막 '}' 구간 재시도."""
    text = re.sub(r"```(?:json)?", "", text).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            return json.loads(text[start:end + 1])
        raise


def validate(briefing: dict) -> None:
    for key in ("date", "news", "lesson", "term", "review", "quiz"):
        if key not in briefing:
            raise ValueError(f"브리핑 JSON에 '{key}' 키가 없습니다")
    if len(briefing["news"]) < 2:
        raise ValueError("뉴스가 2건 미만입니다")
    if len(briefing["quiz"]) < 2:
        raise ValueError("퀴즈가 2문제 미만입니다")


# ---------------------------------------------------------------- 메인

def main() -> None:
    today = today_kst()
    date_str = today.strftime("%Y-%m-%d")
    is_weekend = today.weekday() >= 5

    curriculum = load_json(DATA / "curriculum.json")
    terms = load_json(DATA / "terms.json")

    lesson_item, week_topics = pick_lesson(curriculum, is_weekend, today)
    if not is_weekend and lesson_item is None:
        print("커리큘럼 40개 주제를 모두 완료했습니다. 심화 유닛을 추가해 주세요.")
        sys.exit(1)

    review_terms = find_review_terms(terms, today)
    cutoff = (today - timedelta(days=7)).strftime("%Y-%m-%d")
    recent_terms = [t["term"] for t in terms if t["date"] >= cutoff]

    print(f"[generate] {date_str} 브리핑 생성 시작 (주말: {is_weekend})")
    headlines = fetch_headlines()
    print(f"[generate] 뉴스 헤드라인 {len(headlines)}건 수집")

    user_prompt = build_user_prompt(today, lesson_item, week_topics,
                                    review_terms, recent_terms, headlines)

    try:
        briefing = extract_json(call_llm(user_prompt))
        validate(briefing)
    except (json.JSONDecodeError, ValueError, KeyError) as e:
        print(f"[generate] 1차 파싱 실패({e}) → 재시도")
        briefing = extract_json(call_llm(user_prompt))
        validate(briefing)

    briefing["date"] = date_str
    save_json(DATA / "briefings" / f"{date_str}.json", briefing)
    print(f"[generate] data/briefings/{date_str}.json 저장 완료")

    # 진도 갱신 (평일만)
    if lesson_item:
        lesson_item["completed"] = True
        lesson_item["date"] = date_str
        lesson_item["briefing_url"] = f"/archive/{date_str}/"
        save_json(DATA / "curriculum.json", curriculum)
        print(f"[generate] 진도 갱신: {lesson_item['topic']}")

    # 용어 누적 (같은 날 재실행 시 중복 방지)
    t = briefing["term"]
    if not any(x["term"] == t["term"] for x in terms):
        terms.append({"term": t["term"], "definition": t["definition"],
                      "analogy": t["analogy"], "date": date_str})
        save_json(DATA / "terms.json", terms)
        print(f"[generate] 용어 누적: {t['term']}")


if __name__ == "__main__":
    main()
