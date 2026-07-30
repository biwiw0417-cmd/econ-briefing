# -*- coding: utf-8 -*-
"""주간 포트폴리오 코멘트 생성.

yfinance로 시세, Google 뉴스 RSS로 종목 뉴스 수집 → Gemini로 코멘트 생성
→ data/portfolio/YYYY-MM-DD.json 저장. 사이트 반영은 build_site.py가 담당.
"""
import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate import DATA, call_llm, extract_json, save_json, load_json, today_kst

import yfinance as yf

OUT_DIR = DATA / "portfolio"

PORT_SYSTEM = """당신은 20년 장기 가치투자자를 위한 주간 포트폴리오 모니터링 도우미입니다.

절대 규칙:
- 이것은 투자 자문이 아닙니다. 매수/매도/비중 축소 같은 매매 신호를 절대 제시하지 마세요.
- 사용자의 원칙: 20년 투자, 반기(6개월) 리밸런싱, 매도 없이 신규 납입금으로만 비중 조정. 이 원칙을 항상 상기시키세요.
- 단기 등락에 일희일비하지 않는 관점을 유지하세요. 큰 변동이 있어도 "장기 계획 안에서 어떻게 볼지"만 설명합니다.
- 뉴스가 자산의 장기 논지(thesis)를 바꿀 만한 구조적 변화인지, 단기 노이즈인지 구분해서 언급하세요.
- 경제 초보 이공계 대학원생에게 설명하듯 쉬운 존댓말로.

출력: 반드시 아래 스키마의 JSON 하나만 출력 (다른 텍스트 금지).
{
  "headline": "이번 주를 한 문장으로",
  "overview": "전체 시장/포트폴리오 흐름 요약 3~5문장",
  "asset_notes": [{"name": "자산명", "note": "해당 자산 1~3문장 코멘트 (뉴스가 구조적인지 노이즈인지 포함)"}],
  "reminder": "반기 리밸런싱·신규 납입 원칙 관점에서 이번 주에 기억할 것 1~2문장"
}
asset_notes는 의미 있는 변동이나 뉴스가 있는 자산만 3~6개 골라 쓰세요."""


def fetch_prices(assets: list) -> list:
    rows = []
    for a in assets:
        t = a.get("yahoo")
        if not t:
            continue
        try:
            h = yf.Ticker(t).history(period="1mo")
            close = h["Close"].dropna()
            if len(close) < 2:
                raise ValueError("데이터 없음")
            price = float(close.iloc[-1])
            week = float(close.iloc[-6]) if len(close) >= 6 else float(close.iloc[0])
            month = float(close.iloc[0])
            rows.append({
                "name": a["name"], "ticker": t, "target_pct": a["target_pct"],
                "qty": a.get("qty"),
                "price": round(price, 2),
                "week_pct": round((price / week - 1) * 100, 2),
                "month_pct": round((price / month - 1) * 100, 2),
                "currency": "KRW" if t.endswith(".KS") else "USD",
            })
        except Exception as e:
            print(f"[portfolio] 시세 실패 {t}: {e}")
    return rows


def fetch_news(assets: list) -> list:
    items = []
    for a in assets:
        q = a.get("news_query")
        if not q:
            continue
        hl = "ko&gl=KR&ceid=KR:ko" if a.get("lang") == "ko" else "en-US&gl=US&ceid=US:en"
        url = f"https://news.google.com/rss/search?q={urllib.parse.quote(q)}&hl={hl}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                root = ET.fromstring(resp.read().decode("utf-8", errors="replace"))
            for item in list(root.iter("item"))[:2]:
                title = (item.findtext("title") or "").strip()
                if title:
                    items.append({"asset": a["name"], "title": title})
        except Exception as e:
            print(f"[portfolio] 뉴스 실패 {q}: {e}")
    return items


def add_weights(prices: list, cash_krw: float) -> None:
    """보유 수량이 있으면 원화 환산 실제 비중과 목표 대비 드리프트를 계산."""
    if not any(p.get("qty") for p in prices):
        return
    try:
        fx = float(yf.Ticker("KRW=X").history(period="5d")["Close"].dropna().iloc[-1])
    except Exception:
        fx = 1450.0  # ponytail: 환율 조회 실패 시 대략값, 드리프트가 ±1%p쯤 틀릴 수 있음
    total = cash_krw or 0
    for p in prices:
        if p.get("qty"):
            p["value_krw"] = round(p["qty"] * p["price"] * (fx if p["currency"] == "USD" else 1))
            total += p["value_krw"]
    for p in prices:
        if p.get("value_krw"):
            p["current_pct"] = round(p["value_krw"] / total * 100, 1)


def build_prompt(cfg, prices, news, date_str) -> str:
    lines = [f"오늘 날짜: {date_str} (KST)", "",
             f"투자 원칙: {cfg['rebalance_policy']}, 투자 기간 {cfg['horizon_years']}년", "",
             "목표 배분과 최근 시세 (1주/1달 등락률 %):"]
    for p in prices:
        drift = (f", 실제 비중 {p['current_pct']}% (목표 대비 {p['current_pct'] - p['target_pct']:+.1f}%p)"
                 if p.get("current_pct") is not None else "")
        lines.append(f"- {p['name']} ({p['ticker']}): 목표 {p['target_pct']}%, "
                     f"현재가 {p['price']} {p['currency']}, 1주 {p['week_pct']:+.1f}%, 1달 {p['month_pct']:+.1f}%{drift}")
    no_ticker = [a["name"] for a in cfg["target_allocation"] if not a.get("yahoo")]
    if no_ticker:
        lines.append(f"- (티커 미정으로 시세 미조회: {', '.join(no_ticker)})")
    lines += ["", "최근 뉴스 헤드라인:"]
    for n in news:
        lines.append(f"- [{n['asset']}] {n['title']}")
    lines += ["", "위 정보로 이번 주 포트폴리오 코멘트 JSON을 작성하세요."]
    return "\n".join(lines)


def main() -> None:
    date_str = today_kst().strftime("%Y-%m-%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUT_DIR / f"{date_str}.json"
    if out_path.exists():
        print(f"[portfolio] {date_str} 코멘트가 이미 존재합니다. 건너뜀.")
        return

    cfg = load_json(DATA / "portfolio.json")
    prices = fetch_prices(cfg["target_allocation"])
    add_weights(prices, cfg.get("cash_krw", 0))
    news = fetch_news(cfg["target_allocation"])
    print(f"[portfolio] 시세 {len(prices)}건, 뉴스 {len(news)}건 수집")

    prompt = build_prompt(cfg, prices, news, date_str)
    try:
        comment = extract_json(call_llm(prompt, PORT_SYSTEM))
    except (json.JSONDecodeError, KeyError) as e:
        print(f"[portfolio] 1차 파싱 실패({e}) → 재시도")
        comment = extract_json(call_llm(prompt, PORT_SYSTEM))

    save_json(out_path, {"date": date_str, "prices": prices, "comment": comment})
    print(f"[portfolio] {out_path.name} 저장 완료")


if __name__ == "__main__":
    main()
