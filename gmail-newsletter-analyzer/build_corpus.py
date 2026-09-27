#!/usr/bin/env python3
"""
Build the paired (newsletter -> note) corpus used to derive the transformation rules.

For each date in the `세상 공부` note, this pairs that day's newsletters with what
운영자 actually wrote. The rules for the analyzer are derived by comparing the two
sides, not by guessing.

Fetching reuses gmail_imap.fetch_body and digest.clean — no new fetch code.

Usage:
    python3 build_corpus.py            # build missing days only
    python3 build_corpus.py --force    # rebuild every day
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta
from html.parser import HTMLParser

import gmail_imap as g
import digest

NOTE_FILE = g.HERE / "_raw_sources" / "세상공부_원본.html"
OUT_DIR = g.HERE / "_raw_sources" / "corpus"
DATE_RE = re.compile(r"^2[0-9]{5}$")


class NoteParser(HTMLParser):
    """Parse the Apple Notes HTML, keeping list nesting depth.

    Depth matters: the note is not a flat list. A typical entry runs
    fact -> cause -> concept definition -> non-obvious detail, expressed as up to
    three levels of nested <ul>. `plaintext of note` discards all of it, which is
    why this reads `body of note` instead.

    Notes emits nested <ul> as a *sibling* of <li>, not inside it, so tracking
    open list tags is enough to recover the level.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.entries, self.current = {}, None
        self.depth, self.buf = 0, None
        self.in_head = False

    def handle_starttag(self, tag, attrs):
        if tag in ("ul", "ol"):
            self.depth += 1
        elif tag == "li":
            self.buf = []
        elif tag in ("h1", "h2", "h3"):
            self.in_head, self.buf = True, []

    def handle_endtag(self, tag):
        if tag in ("ul", "ol"):
            self.depth = max(0, self.depth - 1)
        elif tag == "li" and self.buf is not None:
            text = " ".join("".join(self.buf).split())
            if text and self.current:
                # depth 1 == top level item
                self.entries[self.current].append((max(0, self.depth - 1), text))
            self.buf = None
        elif tag in ("h1", "h2", "h3") and self.buf is not None:
            text = "".join(self.buf).strip()
            if DATE_RE.match(text):
                self.current = text
                self.entries.setdefault(text, [])
            self.in_head, self.buf = False, None

    def handle_data(self, data):
        if self.buf is not None:
            self.buf.append(data)


def parse_note() -> dict:
    """Return {YYMMDD: [(depth, text), ...]} from the note HTML."""
    if not NOTE_FILE.exists():
        raise SystemExit(
            f"{NOTE_FILE} 없음. Apple Notes에서 내려받을 것 "
            "(plaintext 아님 — 계층이 사라짐):\n"
            "  osascript -e 'tell application \"Notes\" to return body of note 60' "
            "> _raw_sources/세상공부_원본.html"
        )
    p = NoteParser()
    p.feed(NOTE_FILE.read_text(encoding="utf-8"))
    return p.entries


MAX_WINDOW = 10          # 260303 sits 19 days after the previous entry; cap the backlog


def window_days(yymmdd: str, prev: str = None) -> list:
    """Days a note entry could draw on: everything since the previous entry.

    운영자는 뉴스레터로만 뉴스를 본다. 그래서 노트에 있는데 그날 원문에 없는 항목은
    외부 출처가 아니라 **밀린 뉴스레터**다. 실제로 260127 노트의 스테이블코인·
    롯데시네마-메가박스·GDP 역성장은 전부 260126 발행분에 있었고, 260125~260126에는
    노트가 없다(백로그를 260127에 몰아서 쓴 것).

    그러므로 짝짓기 창은 당일이 아니라 (직전 노트일, 당일] 이다.
    """
    end = datetime.strptime(yymmdd, "%y%m%d")
    span = (end - datetime.strptime(prev, "%y%m%d")).days if prev else 3
    span = max(1, min(span, MAX_WINDOW))
    return [(end - timedelta(days=i)).strftime("%Y/%m/%d") for i in range(span - 1, -1, -1)]


def fetch_window(imap, days: list) -> list:
    """Fetch every enabled newsletter across the given days, oldest first."""
    cfg = json.loads((g.HERE / "newsletters.json").read_text(encoding="utf-8"))
    active = [n for n in cfg["newsletters"] if n.get("enabled")]

    found = []
    for day in days:
        for n in active:
            uids = g.search(imap, g.build_query(sender=n["sender"], date=day), 20)
            if not uids:
                continue
            for m in g.filter_to_kst_day(g.fetch_metadata(imap, uids), day):
                full = g.fetch_body(imap, m["uid"])
                found.append({
                    "source": n["name"], "topic": n["topic"], "day": day,
                    "subject": full["subject"], "body": digest.clean(full["body"]),
                })
    return found


def write_pair(yymmdd: str, note_lines: list, newsletters: list, days: list):
    span = f"{days[0]} ~ {days[-1]}" if len(days) > 1 else days[0]

    parts = [
        f"# {yymmdd} — 원문 ↔ 노트 대조\n",
        f"- 노트 항목: **{len(note_lines)}줄**",
        f"- 뉴스레터: **{len(newsletters)}통** · 대상 구간 **{span}** ({len(days)}일)",
    ]
    if len(days) > 1:
        parts.append(
            f"- 직전 노트 이후 {len(days)}일치가 쌓인 **백로그**. "
            "노트 항목이 당일이 아닌 이전 발행분에서 올 수 있다"
        )
    parts.append("\n---\n\n## A. 운영자가 실제로 쓴 것 (정답지 — 들여쓰기가 사고 구조)\n")
    parts += [f"{'    ' * d}- {t}" for d, t in note_lines] or ["(항목 없음)"]
    parts.append("\n---\n\n## B. 구간 내 원문 (오래된 것부터)\n")

    for nl in newsletters:
        parts.append(f"\n### [{nl['day']}] [{nl['topic']}] {nl['source']} — {nl['subject']}\n")
        parts.append(nl["body"])

    (OUT_DIR / f"{yymmdd}.md").write_text("\n".join(parts), encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    entries = parse_note()
    ordered = sorted(entries)                       # windows depend on the previous entry
    todo = [d for d in ordered if args.force or not (OUT_DIR / f"{d}.md").exists()]
    print(f"노트 날짜 {len(entries)}일 / 생성 대상 {len(todo)}일", file=sys.stderr)
    if not todo:
        return

    total_n = 0
    with g.connect() as imap:
        for d in todo:
            i = ordered.index(d)
            days = window_days(d, ordered[i - 1] if i else None)
            newsletters = fetch_window(imap, days)
            if not newsletters:
                # Nothing was delivered in the window (e.g. 260124, a Saturday).
                # Reach further back — the entry had to come from somewhere.
                days = window_days(d, None)          # default 3-day lookback
                newsletters = fetch_window(imap, days)
            write_pair(d, entries[d], newsletters, days)
            flag = f" ←백로그 {len(days)}일" if len(days) > 1 else ""
            print(f"  {d}  노트 {len(entries[d]):2d}줄 / 원문 {len(newsletters):2d}통{flag}",
                  file=sys.stderr)
            total_n += len(newsletters)

    print(f"\n{len(todo)}일 · 뉴스레터 {total_n}통 → {OUT_DIR}", file=sys.stderr)


if __name__ == "__main__":
    main()
