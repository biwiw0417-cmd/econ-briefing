# -*- coding: utf-8 -*-
"""브리핑 JSON → 정적 HTML 사이트 빌드 (docs/ 폴더, GitHub Pages 배포 대상).

생성 페이지:
  docs/index.html                 오늘(최신) 브리핑
  docs/archive/index.html         지난 브리핑 목록 (월별 그룹)
  docs/archive/<date>/index.html  개별 브리핑 (+ Word 다운로드 링크)
  docs/glossary/index.html        용어사전 (가나다순, 검색)
  docs/curriculum/index.html      커리큘럼 진도표
  docs/assets/style.css
"""
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
DOCS = ROOT / "docs"

WEEKDAY_KR = "월화수목금토일"

# 유닛별 강조색 (0 = 주말 복습)
UNIT_COLORS = {
    0: "#6E6E6E", 1: "#C2543A", 2: "#A87B2D", 3: "#2F6F5E", 4: "#B04A6E",
    5: "#3E6FA8", 6: "#6B5AA8", 7: "#2E8B8B", 8: "#7A8B2E",
}

FAVICON = ("data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' "
           "viewBox='0 0 100 100'><text y='.9em' font-size='90'>📈</text></svg>")


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def date_kr(date_str: str) -> str:
    y, m, d = date_str.split("-")
    from datetime import date as _date
    wd = WEEKDAY_KR[_date(int(y), int(m), int(d)).weekday()]
    return f"{y}년 {int(m)}월 {int(d)}일 ({wd})"


def nav(root: str, active: str) -> str:
    items = [
        ("today", f"{root}/index.html", "오늘"),
        ("archive", f"{root}/archive/index.html", "아카이브"),
        ("glossary", f"{root}/glossary/index.html", "용어사전"),
        ("curriculum", f"{root}/curriculum/index.html", "진도"),
    ]
    links = "".join(
        f'<a href="{href}" class="{"active" if key == active else ""}">{label}</a>'
        for key, href, label in items
    )
    return f'<nav class="bottom-nav">{links}</nav>'


def page(title: str, body: str, root: str, active: str, accent: str = "#2F6F5E") -> str:
    return f"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="{accent}">
<title>{esc(title)}</title>
<link rel="icon" href="{FAVICON}">
<link rel="stylesheet" href="{root}/assets/style.css">
<style>:root {{ --accent: {accent}; }}</style>
</head>
<body>
<main>
{body}
</main>
{nav(root, active)}
</body>
</html>
"""


# ---------------------------------------------------------------- 브리핑 본문

def render_briefing(b: dict, root: str, docx_href: str) -> str:
    lesson = b["lesson"]
    unit = lesson.get("unit", 0)
    unit_label = "주간 복습" if unit == 0 else f"Unit {unit}"

    parts = [f"""
<header class="brief-head">
  <p class="date">{esc(date_kr(b["date"]))}</p>
  <h1>{esc(lesson["topic"])}</h1>
  <p class="unit-badge">{esc(unit_label)}</p>
</header>"""]

    # ① 뉴스
    news_html = ""
    for n in b["news"]:
        src = f'<span class="src">{esc(n["source_name"])}</span>' if n.get("source_name") else ""
        link = (f'<p class="link-lesson">🔗 {esc(n["link_to_lesson"])}</p>'
                if n.get("link_to_lesson") else "")
        news_html += f"""
<article class="news">
  <h3>{esc(n["title"])} {src}</h3>
  <p>{esc(n["summary"])}</p>
  <p class="why"><strong>왜 중요한가요?</strong> {esc(n["why_it_matters"])}</p>
  {link}
</article>"""
    parts.append(f'<section><h2 class="label">오늘의 경제 뉴스</h2>{news_html}</section>')

    # ② 지식
    conn = (f'<p class="link-lesson">🔗 {esc(lesson["news_connection"])}</p>'
            if lesson.get("news_connection") else "")
    parts.append(f"""
<section>
  <h2 class="label">오늘의 경제 지식</h2>
  <article class="lesson">
    <h3>[{esc(unit_label)}] {esc(lesson["topic"])}</h3>
    <p>{esc(lesson["body"])}</p>
    {conn}
  </article>
</section>""")

    # ③ 용어
    t = b["term"]
    parts.append(f"""
<section>
  <h2 class="label">오늘의 용어</h2>
  <article class="term-card">
    <h3>{esc(t["term"])}</h3>
    <p><strong>정의</strong> {esc(t["definition"])}</p>
    <p><strong>비유</strong> {esc(t["analogy"])}</p>
    <p><strong>사용 예</strong> {esc(t["usage"])}</p>
  </article>
</section>""")

    # ④ 복습
    if b["review"]:
        rv_html = "".join(f"""
<article class="review">
  <h3>기억나시나요? — {esc(rv["term"])}</h3>
  <p class="date-small">배운 날: {esc(rv.get("learned_date", ""))}</p>
  <p>{esc(rv["refresher"])}</p>
</article>""" for rv in b["review"])
    else:
        rv_html = '<p class="muted">아직 복습할 용어가 없어요. 용어가 쌓이면 3일/7일 간격으로 다시 만나요.</p>'
    parts.append(f'<section><h2 class="label">복습 코너</h2>{rv_html}</section>')

    # ⑤ 퀴즈
    quiz_html = ""
    for i, q in enumerate(b["quiz"], 1):
        choices = "".join(f"<li>{esc(c)}</li>" for c in q["choices"])
        answer = q["choices"][q["answer_index"]]
        quiz_html += f"""
<article class="quiz">
  <p class="q"><strong>Q{i}.</strong> {esc(q["question"])}</p>
  <ol>{choices}</ol>
  <details>
    <summary>정답 보기</summary>
    <p><strong>정답: {q["answer_index"] + 1}) {esc(answer)}</strong></p>
    <p>{esc(q["explanation"])}</p>
  </details>
</article>"""
    parts.append(f'<section><h2 class="label">확인 퀴즈</h2>{quiz_html}</section>')

    parts.append(f'<p class="download"><a href="{docx_href}" download>📄 Word로 다운로드</a></p>')
    return "\n".join(parts)


# ---------------------------------------------------------------- 페이지 빌드

def build_briefing_pages(briefings: list) -> None:
    for b in briefings:
        d = b["date"]
        out = DOCS / "archive" / d / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        accent = UNIT_COLORS.get(b["lesson"].get("unit", 0), "#2F6F5E")
        body = render_briefing(b, "../..", f"../../files/{d}_경제브리핑.docx")
        out.write_text(
            page(f"{d} 경제 브리핑", body, "../..", "archive", accent), encoding="utf-8")

    # 최신 글 = 홈
    latest = briefings[0]
    accent = UNIT_COLORS.get(latest["lesson"].get("unit", 0), "#2F6F5E")
    body = render_briefing(latest, ".", f"files/{latest['date']}_경제브리핑.docx")
    (DOCS / "index.html").write_text(
        page("아침 경제 브리핑", body, ".", "today", accent), encoding="utf-8")


def build_archive_index(briefings: list) -> None:
    by_month = {}
    for b in briefings:
        by_month.setdefault(b["date"][:7], []).append(b)

    body = ["<header class='brief-head'><h1>아카이브</h1></header>"]
    for month in sorted(by_month, reverse=True):
        y, m = month.split("-")
        items = "".join(f"""
<li><a href="{b["date"]}/index.html">
  <span class="date-small">{b["date"]}</span>
  <span>{esc(b["lesson"]["topic"])}</span>
</a></li>""" for b in by_month[month])
        body.append(f"<section><h2 class='label'>{y}년 {int(m)}월</h2><ul class='list'>{items}</ul></section>")

    out = DOCS / "archive" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page("아카이브 — 경제 브리핑", "\n".join(body), "..", "archive"),
                   encoding="utf-8")


def build_glossary(terms: list) -> None:
    terms_sorted = sorted(terms, key=lambda t: t["term"])
    items = "".join(f"""
<article class="term-card gl-item" data-term="{esc(t["term"])}">
  <h3>{esc(t["term"])} <span class="date-small">{esc(t["date"])}</span></h3>
  <p><strong>정의</strong> {esc(t["definition"])}</p>
  <p><strong>비유</strong> {esc(t["analogy"])}</p>
</article>""" for t in terms_sorted)

    body = f"""
<header class='brief-head'><h1>용어사전</h1>
<p class="muted">지금까지 배운 용어 {len(terms_sorted)}개</p></header>
<input type="search" id="q" placeholder="용어 검색..." class="search">
<div id="terms">{items if items else '<p class="muted">아직 배운 용어가 없습니다.</p>'}</div>
<script>
document.getElementById('q').addEventListener('input', function() {{
  var q = this.value.trim().toLowerCase();
  document.querySelectorAll('.gl-item').forEach(function(el) {{
    el.style.display = el.textContent.toLowerCase().includes(q) ? '' : 'none';
  }});
}});
</script>"""
    out = DOCS / "glossary" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page("용어사전 — 경제 브리핑", body, "..", "glossary"), encoding="utf-8")


def build_curriculum(curriculum: list) -> None:
    done = sum(1 for c in curriculum if c["completed"])
    total = len(curriculum)
    pct = round(done / total * 100) if total else 0

    by_unit = {}
    for c in curriculum:
        by_unit.setdefault((c["unit"], c["unit_title"]), []).append(c)

    sections = []
    for (unit, unit_title), items in sorted(by_unit.items()):
        color = UNIT_COLORS.get(unit, "#2F6F5E")
        rows = ""
        for c in items:
            if c["completed"]:
                rows += f"""
<li class="done"><a href="../archive/{c["date"]}/index.html">
  <span class="check">✓</span><span>{esc(c["topic"])}</span>
  <span class="date-small">{esc(c["date"])}</span>
</a></li>"""
            else:
                rows += f'<li><span class="check">·</span><span>{esc(c["topic"])}</span></li>'
        sections.append(f"""
<section>
  <h2 class="label" style="color:{color}">Unit {unit}. {esc(unit_title)}</h2>
  <ul class="list curriculum">{rows}</ul>
</section>""")

    body = f"""
<header class='brief-head'><h1>커리큘럼 진도</h1>
<p class="muted">{done} / {total} 완료 ({pct}%)</p>
<div class="progress"><div class="bar" style="width:{pct}%"></div></div>
</header>
{"".join(sections)}"""
    out = DOCS / "curriculum" / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(page("커리큘럼 진도 — 경제 브리핑", body, "..", "curriculum"),
                   encoding="utf-8")


CSS = """
:root {
  --bg: #ffffff; --fg: #1c1c1e; --muted: #8e8e93;
  --card: #f6f6f4; --border: #e5e5e2; --accent: #2F6F5E;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #16161a; --fg: #e8e8ea; --muted: #98989e;
          --card: #232328; --border: #333338; }
}
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  background: var(--bg); color: var(--fg);
  font-family: -apple-system, "Apple SD Gothic Neo", Pretendard,
               "Noto Sans KR", "Malgun Gothic", sans-serif;
  font-size: 17px; line-height: 1.75;
  -webkit-font-smoothing: antialiased;
}
main {
  max-width: 680px; margin: 0 auto;
  padding: 28px 20px calc(110px + env(safe-area-inset-bottom, 0px));
}
a { color: var(--accent); text-decoration: none; }

.brief-head { margin-bottom: 8px; }
.brief-head .date { color: var(--muted); font-size: 15px; }
.brief-head h1 { font-size: 26px; line-height: 1.35; margin: 4px 0; }
.unit-badge {
  display: inline-block; font-size: 13px; font-weight: 700;
  color: var(--accent); border: 1.5px solid var(--accent);
  border-radius: 99px; padding: 1px 12px; margin-top: 4px;
}

section { margin-top: 36px; }
.label {
  font-size: 14px; font-weight: 800; letter-spacing: .06em;
  color: var(--accent); text-transform: uppercase;
  border-bottom: 2px solid var(--accent); padding-bottom: 6px; margin-bottom: 16px;
}
article { margin-bottom: 20px; }
article h3 { font-size: 18px; line-height: 1.45; margin-bottom: 8px; }
article p { margin-bottom: 10px; }
.src { font-size: 13px; font-weight: 400; color: var(--muted); white-space: nowrap; }
.why { background: var(--card); border-radius: 10px; padding: 10px 14px; }
.link-lesson { font-size: 15px; color: var(--muted); }
.muted { color: var(--muted); }
.date-small { font-size: 13px; color: var(--muted); }

.term-card, .review, .lesson {
  background: var(--card); border-radius: 14px; padding: 16px 18px;
}
.quiz { border: 1px solid var(--border); border-radius: 14px; padding: 16px 18px; }
.quiz ol { padding-left: 24px; margin: 8px 0; }
.quiz li { margin-bottom: 4px; }
details { margin-top: 10px; }
summary {
  cursor: pointer; font-weight: 700; color: var(--accent);
  padding: 8px 0; user-select: none;
}
details[open] summary { margin-bottom: 6px; }

.download { margin-top: 36px; text-align: center; }
.download a {
  display: inline-block; background: var(--accent); color: #fff;
  border-radius: 12px; padding: 12px 22px; font-weight: 700;
}

.list { list-style: none; }
.list li { border-bottom: 1px solid var(--border); }
.list a, .list li > span {
  display: flex; gap: 12px; align-items: baseline; padding: 12px 4px; color: var(--fg);
}
.list.curriculum li { display: flex; gap: 10px; align-items: baseline; padding: 10px 4px; }
.list.curriculum li a { display: flex; gap: 10px; padding: 0; flex: 1; }
.check { color: var(--accent); font-weight: 800; width: 18px; flex: none; }
.list.curriculum li:not(.done) { color: var(--muted); }
.list.curriculum .date-small { margin-left: auto; }

.search {
  width: 100%; font-size: 17px; padding: 12px 16px; margin-bottom: 20px;
  border: 1.5px solid var(--border); border-radius: 12px;
  background: var(--card); color: var(--fg);
}
.progress {
  height: 8px; background: var(--card); border-radius: 99px;
  overflow: hidden; margin-top: 10px;
}
.progress .bar { height: 100%; background: var(--accent); border-radius: 99px; }

.bottom-nav {
  position: fixed; bottom: 0; left: 0; right: 0;
  display: flex; background: var(--bg);
  border-top: 1px solid var(--border);
  padding-bottom: max(10px, env(safe-area-inset-bottom, 10px));
}
.bottom-nav a {
  flex: 1; text-align: center; padding: 13px 0 11px;
  font-size: 14px; font-weight: 600; color: var(--muted);
}
.bottom-nav a.active { color: var(--accent); }
"""


def main() -> None:
    briefings = []
    for path in sorted((DATA / "briefings").glob("*.json"), reverse=True):
        briefings.append(json.loads(path.read_text(encoding="utf-8")))
    if not briefings:
        print("[site] 브리핑 데이터가 없습니다")
        return

    curriculum = json.loads((DATA / "curriculum.json").read_text(encoding="utf-8"))
    terms = json.loads((DATA / "terms.json").read_text(encoding="utf-8"))

    (DOCS / "assets").mkdir(parents=True, exist_ok=True)
    (DOCS / "assets" / "style.css").write_text(CSS, encoding="utf-8")

    build_briefing_pages(briefings)
    build_archive_index(briefings)
    build_glossary(terms)
    build_curriculum(curriculum)
    print(f"[site] 빌드 완료: 브리핑 {len(briefings)}건, 용어 {len(terms)}개")


if __name__ == "__main__":
    main()
