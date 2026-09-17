#!/usr/bin/env python3
"""
Collect the day's newsletters and prepare them for summarisation into a
"세상 공부" entry.

Target format, learned from the existing note (260112–260303, 21 entries):

    260210
    송파구 아파트 매물 21.6%, 성동구 19.6% 증가(1/21 ~ 2/8), 외곽 감소
    서울 1월 아파트 경매 낙찰가율 107.8%로 3년반만에 최고치
    ...

Terse declarative Korean, no bullet markers, numbers kept specific, roughly
8–9 lines a day. This script does the fetching and cleaning; the summarising
into that format is done by Claude reading the output file.

Usage:
    python3 digest.py                  # today (KST)
    python3 digest.py --days 3         # last 3 days, to catch weekly senders
    python3 digest.py --date 2026/08/25
    python3 digest.py --since "2026-09-16 12:04"          # 직전 노트 스탬프 이후 도착분만
    python3 digest.py --only "Axios Markets,Axios Macro" --since "2026-09-08 00:00"   # 보충용

수집 뒤 stderr의 두 경고를 먼저 본다 — ★ 미등록 발신자 / ★ 스팸함에서 건진 발행분.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta

import gmail_imap as g

# Lines that are navigation, legal, or marketing furniture rather than content.
NOISE = re.compile(
    r"(구독하기|수신거부|수신 거부|광고문의|문의하기|이메일 무단|무단수집|"
    r"unsubscribe|view in browser|잘림 없이|공유하기|카카오톡|페이스북|인스타|"
    r"저작권|Copyright|All rights reserved|개인정보처리방침|이용약관|"
    r"앱 다운로드|다운로드하기|바로가기|더보기|www\.|https?://)",
    re.I,
)

# Whole sections to drop, as (start, end) markers. Native ads read like editorial
# copy, so no line-level rule catches them — only the section boundary does.
# 머니로그 is one reader's personal financial consultation: real content, but not
# world news, and it never once made it into the note.
# Each rule is (start, end, max_lines). max_lines is a hard stop: if the closing
# marker never appears, the drop ends anyway. Without it a start marker with no
# closer eats the rest of the newsletter — STARTUP WEEKLY labels a couple of ads
# `스폰서드 포스팅` and never closes them, and an unbounded rule cut it from
# 5,680 to 633 characters.
SECTION_DROP = [
    (re.compile(r"Sponsored by"),
     re.compile(r"오늘 광고 어떠셨나요|제작 지원을 받아"), 60),
    (re.compile(r"스폰서드 포스팅"),
     re.compile(r"^$"), 3),                      # label + the ads directly under it
    (re.compile(r"머니 프로필"),
     re.compile(r"머니로그 신청하기|머니로그는 어피티"), 60),
]

# Reader feedback is deliberately KEPT. 260121's "엔화는 기축통화가 아니라 펀딩통화"
# — a concept worth knowing — came from exactly this section. Do not filter it.


def clean(text: str) -> str:
    """Strip boilerplate and sponsored sections; collapse whitespace and repeats."""
    out, seen = [], set()
    closer, budget = None, 0         # set while inside a section being dropped

    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()

        if closer is not None:       # skipping a sponsored / 머니로그 block
            budget -= 1
            if closer.search(line) or budget <= 0:
                closer = None
            continue

        hit = next(((end, n) for start, end, n in SECTION_DROP if start.search(line)), None)
        if hit is not None:
            closer, budget = hit
            continue

        if len(line) < 6:            # nav fragments, stray punctuation
            continue
        if NOISE.search(line):
            continue
        if line in seen:             # newsletters repeat headlines in their TOC
            continue
        seen.add(line)
        out.append(line)
    return "\n".join(out)


RELAY_RE = re.compile(r"_at_[a-z0-9]+_com_[a-z0-9]+_[0-9a-f]{8}@icloud\.com$", re.I)


def _source_name(n: dict, subject: str) -> str:
    """재료 헤더에 쓸 발행물 이름. 한 릴레이로 여러 발행물이 오면 제목에서 뽑는다.

    mike@axios.com 릴레이 하나로 Axios AM·PM·Finish Line 세 통이 와서 전부 `Axios PM`으로
    적혔다 — check_note.py의 커버리지가 세 통을 한 라벨로 합쳐 0항목 통을 못 잡았다(2026-09-16).
    명단의 `subject_name_re`(그룹 1)가 있으면 그것을, 없으면 name 그대로.
    """
    pat = n.get("subject_name_re")
    if pat:
        m = re.search(pat, subject or "")
        if m:
            return m.group(1)
    return n["name"]


def _gather(imap, active: list, window: list, since, spam: bool) -> list:
    """선택된 메일함에서 명단 소스의 발행분을 모은다. spam=True면 헤더에 ⚠스팸함을 붙인다."""
    found = []
    for n in active:
        for day in window:
            uids = g.search(imap, g.build_query(sender=n["sender"], date=day), 20)
            if not uids:
                continue
            # 날짜 질의는 앞뒤 하루씩 넉넉해서 이웃 날 질의와 겹친다 → 먼저 그날로 자르고,
            # since가 있으면 그 시각 이후만 남긴다(둘 다 적용해야 중복이 없다).
            metas = g.filter_to_kst_day(g.fetch_metadata(imap, uids), day)
            if since:
                metas = g.filter_since(metas, since)
            for m in metas:
                full = g.fetch_body(imap, m["uid"])
                body = clean(full["body"])
                dt = g.message_kst(m)
                found.append({
                    "source": _source_name(n, full["subject"]), "sender": n["sender"],
                    "topic": n["topic"], "day": dt.strftime("%Y/%m/%d") if dt else day,
                    "subject": full["subject"], "chars": len(body), "body": body,
                    "links": full.get("links", []), "date_hdr": m.get("date", ""),
                    "spam": spam,
                })
    return found


def _unregistered(imap, cfg: dict, query: str) -> list:
    """명단 밖인데 뉴스레터처럼 보이는 발신자.

    뉴스레터 신호 = Apple Hide My Email 릴레이 주소(List-* 헤더가 벗겨져 scan_senders가
    못 잡는 부류) **또는** List-Unsubscribe/List-Id 보유. 2026-09-16: Axios Markets가
    9/8부터 매일 왔는데 명단에 없어 7통을 못 봤다 — 명단 주석에 '도착하면 등록할 것'이라
    적혀 있었지만 아무 절차도 주석을 읽지 않는다. 그래서 수집 때마다 여기서 소리를 낸다.
    """
    import scan_senders as sc
    known = {n["sender"].lower() for n in cfg["newsletters"]}
    known |= {e["sender"].lower() for e in cfg.get("excluded", [])}
    out = []
    for addr, info in sc.collect(imap, days=0, quiet=True, query=query).items():
        if addr in known:
            continue
        bulk = info["list_unsub"] > 0 or bool(info["list_id"])
        relay = bool(RELAY_RE.search(addr))
        if not (bulk or relay):
            continue
        # 인증코드·환영 메일 같은 1회성은 소리 낼 가치가 없다 — 제목으로 걸러 낸다.
        subj = " ".join(info["subjects"]).lower()
        if re.search(r"verification code|verify your|welcome to", subj) and info["count"] <= 1:
            continue
        out.append({"address": addr, "name": info["name"], "count": info["count"],
                    "subject": info["subjects"][0] if info["subjects"] else "",
                    "why": "릴레이" if relay else "List-* 헤더"})
    return out


def collect(date_str: str, days: int, purpose: str = "study", since: str = None,
            only: list = None, roster_check: bool = True) -> dict:
    """purpose = 이 재료를 무엇에 쓸 것인가. 소스마다 `use_for`로 정한다.

    같은 메일함에서 두 가지 일을 한다 — 세상 공부 노트(study)와 매매 판단(trade).
    필요한 재료가 다르다: 부동산·여행·맛집은 공부에는 재료지만 반도체 주식 판단에는
    아무것도 보태지 않는다. 실측(2026-09-08): 매매용 재료 1,241행 중 290행이
    BOODING·잘쓸레터였고 판단에 한 줄도 쓰이지 않았다.

    `use_for`가 없는 소스는 **양쪽 모두**로 본다. 새 소스를 등록하고 태그를 깜빡했을 때
    조용히 빠지는 것보다, 필요 없는 게 섞여 들어오는 편이 낫기 때문이다
    (빠진 재료는 알아채기 어렵지만 남는 재료는 읽으면 보인다).

    since = "YYYY-MM-DD HH:MM"(KST). 있으면 창이 (since, 지금]이 된다 — 직전 노트의 수집
    스탬프를 그대로 넘기면 빈틈이 없다. `--days`는 날짜 단위라 직전 노트가 낮에 수집됐으면
    그날 저녁 도착분이 빠진다(2026-09-15 Axios AM이 그렇게 빠져 손으로 옮겨 붙였다).
    only = 명단 name 목록. 보충 노트처럼 특정 소스만 다시 모을 때.
    """
    cfg = json.loads((g.HERE / "newsletters.json").read_text(encoding="utf-8"))
    active = [n for n in cfg["newsletters"] if n.get("enabled")]
    if not active:
        raise SystemExit("newsletters.json has no enabled sources.")
    active = [n for n in active if purpose in n.get("use_for", ["study", "trade"])]
    if not active:
        raise SystemExit(f"newsletters.json에 use_for='{purpose}'인 소스가 없습니다.")
    if only:
        wanted = {x.strip().lower() for x in only}
        active = [n for n in active if n["name"].lower() in wanted]
        missing = wanted - {n["name"].lower() for n in active}
        if missing:
            raise SystemExit(f"--only에 명단에 없는 이름: {sorted(missing)}")

    end = datetime.strptime(date_str, "%Y/%m/%d")
    since_dt = None
    if since:
        since_dt = datetime.strptime(since, "%Y-%m-%d %H:%M").replace(tzinfo=g.KST)
        days = (end.date() - since_dt.date()).days + 1
        if days < 1:
            raise SystemExit(f"--since {since}가 --date {date_str}보다 뒤입니다.")
    window = [(end - timedelta(days=i)).strftime("%Y/%m/%d") for i in range(days)]
    # 미등록 발신자 검사는 수집 창과 같은 날짜 범위를 본다(after/before는 날짜 단위라 하루씩 넉넉히).
    drift_query = (f"after:{(end - timedelta(days=days)).strftime('%Y/%m/%d')} "
                   f"before:{(end + timedelta(days=1)).strftime('%Y/%m/%d')}")

    found, unregistered = [], []
    with g.connect() as imap:
        found += _gather(imap, active, window, since_dt, spam=False)
        if roster_check:
            unregistered += _unregistered(imap, cfg, drift_query)
        spam_box = g.find_spam(imap)

    # 스팸함은 전체보관함에 안 들어 있다 — 같은 명단·같은 창으로 한 번 더.
    spam_count = 0
    if spam_box:
        seen = {(it["sender"], it["subject"], it["date_hdr"]) for it in found}
        with g.connect(spam_box) as imap:
            for it in _gather(imap, active, window, since_dt, spam=True):
                key = (it["sender"], it["subject"], it["date_hdr"])
                if key in seen:
                    continue
                seen.add(key)
                found.append(it)
                spam_count += 1
            if roster_check:
                have = {u["address"] for u in unregistered}
                unregistered += [u for u in _unregistered(imap, cfg, drift_query)
                                 if u["address"] not in have]

    found.sort(key=lambda it: (it["day"], it["source"], it["date_hdr"]))
    return {"date": date_str, "days": days, "items": found, "purpose": purpose,
            "since": since, "only": only, "spam_count": spam_count,
            "unregistered": unregistered}


STAMP_RE = re.compile(r"^<!-- 수집 .*-->$", re.M)


def _issues(text: str) -> list:
    """재료에 담긴 발행분 헤더 목록. 무엇이 늘고 줄었는지 비교하는 데 쓴다."""
    # `###`(발행분의 링크 절)는 발행분이 아니다 — `^## `만으로 찾으면 그것까지 세어
    # 늘고 준 목록이 어지러워진다.
    return [m.group(1).strip() for m in re.finditer(r"^## (?!#)(.+)$", text, re.M)]


def _without_stamp(text: str) -> str:
    """수집 시각 줄을 뺀 본문. 시각만 달라진 것을 '내용이 바뀌었다'고 오인하지 않게."""
    return STAMP_RE.sub("", text).strip()


def _archive(path: "g.Path", old: str) -> "g.Path":
    """직전 재료를 시각까지 붙여 보관한다.

    같은 날짜 파일을 덮어쓰면 **이미 그 재료로 쓴 노트가 근거를 잃는다** — 노트에는 있는데
    재료에는 없거나, 재료에만 새 발행분이 있어 커버리지 검사가 헛울음을 운다.
    (실사례: 260907 노트를 쓴 뒤 Axios 4종이 붙어 재생성되며 그날 재료가 통째로 바뀌었다.)
    """
    m = STAMP_RE.search(old)
    when = re.search(r"수집 ([\d\- :]+)", m.group(0)).group(1).strip().replace(" ", "_").replace(":", "") \
        if m else datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d_%H%M")
    dest_dir = path.parent / "이전버전"
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / f"{path.stem}__{when}{path.suffix}"
    i = 2
    while dest.exists():
        dest = dest_dir / f"{path.stem}__{when}_{i}{path.suffix}"
        i += 1
    dest.write_text(old, encoding="utf-8")
    return dest


def write_input(result: dict) -> "g.Path":
    stamp = datetime.strptime(result["date"], "%Y/%m/%d").strftime("%y%m%d")
    # 용도가 다르면 파일도 다르다. 한 이름을 공유하면 수집 창이 다른 두 작업이
    # 서로의 재료를 덮어쓴다 — 2026-09-08에 실제로 났다(매매 --days 5로 12통을 받은 6분 뒤
    # 공부 --days 2가 9통으로 덮음). 나중에 돌린 쪽이 이기고, 먼저 쓴 노트는 참조하던
    # 재료가 조용히 바뀐다.
    suffix = "" if result.get("purpose", "study") == "study" else f"_{result['purpose']}"
    if result.get("only"):
        # 특정 소스만 다시 모은 것(보충 노트용)은 그날의 본 재료와 다른 파일이어야 한다 —
        # 같은 이름이면 본 노트가 참조하던 재료를 덮어쓴다.
        slug = result.get("slug") or re.sub(r"[^0-9A-Za-z가-힣]+", "", "".join(result["only"]))[:24]
        suffix += f"_{slug}"
    path = g.HERE / "_raw_sources" / f"digest_input_{stamp}{suffix}.md"

    collected = datetime.now().strftime("%Y-%m-%d %H:%M")
    title = {"study": "세상 공부 재료", "trade": "매매 판단 재료"}.get(
        result.get("purpose", "study"), "재료")
    span = (f"직전 스탬프 {result['since']} 이후" if result.get("since")
            else f"최근 {result['days']}일")
    if result.get("only"):
        span += " · " + "·".join(result["only"]) + "만"
    spam_note = f" · 스팸함에서 {result['spam_count']}통" if result.get("spam_count") else ""
    parts = [f"# {title} — {result['date']} ({span})",
             f"<!-- 수집 {collected} · 발행분 {len(result['items'])}통{spam_note} -->\n"]
    for it in result["items"]:
        flag = " ⚠스팸함" if it.get("spam") else ""
        parts.append(
            f"\n## [{it['topic']}] {it['source']}{flag} — {it['day']}\n"
            f"**{it['subject']}**\n\n{it['body']}\n"
        )
        if it.get("links"):
            # 본문과 분리해 붙인다. 전부 열지 말고, 파야 할 항목이 생겼을 때만 선별적으로.
            parts.append("\n### 이 발행분의 링크 (%d)\n" % len(it["links"]))
            parts.append("> 용어 해설(uppity.co.kr)은 §3 '용어 ②왜 그런 규칙인가'를 채울 때,")
            parts.append("> 원 기사·정부·통계 링크는 사건을 더 파거나 fact-check T1 조달에 쓴다.\n")
            for l in it["links"]:
                parts.append(f"- [{l['anchor']}] {l['url']}")
            parts.append("")
    new = "\n".join(parts)

    if path.is_file():
        old = path.read_text(encoding="utf-8")
        if _without_stamp(old) == _without_stamp(new):
            print("· 재료 변화 없음 — 기존 파일 유지 (%s)" % path.name, file=sys.stderr)
            return path
        dest = _archive(path, old)
        before, after = _issues(old), _issues(new)
        added = [x for x in after if x not in before]
        gone = [x for x in before if x not in after]
        print("\n★ 같은 날짜 재료가 **바뀌었습니다** — 직전 판을 보관했습니다: 이전버전/%s"
              % dest.name, file=sys.stderr)
        for x in added:
            print("    + %s" % x, file=sys.stderr)
        for x in gone:
            print("    - %s  (사라짐)" % x, file=sys.stderr)
        if not added and not gone:
            print("    (발행분 목록은 같고 본문·링크만 달라졌습니다)", file=sys.stderr)
        print("  이 날짜로 이미 노트를 썼다면 **그 노트는 옛 재료 기준**입니다 — "
              "달라진 발행분을 노트에 반영할지 확인하세요.\n", file=sys.stderr)

    path.write_text(new, encoding="utf-8")
    return path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=g.today_kst(), help="YYYY/MM/DD (KST)")
    ap.add_argument("--days", type=int, default=1, help="window size, for weekly senders")
    ap.add_argument("--since", default=None,
                    help='"YYYY-MM-DD HH:MM"(KST) — 이 시각 이후 도착분만. 직전 노트 헤더의 '
                         "수집 스탬프를 그대로 넘긴다. --days와 배타.")
    ap.add_argument("--only", default=None,
                    help="명단 name을 쉼표로 — 그 소스만 모은다(보충 노트용, 파일명에 slug가 붙음).")
    ap.add_argument("--slug", default=None, help="--only 파일명 꼬리(기본은 이름을 이어 붙임). 예: axios보충")
    ap.add_argument("--no-roster-check", action="store_true",
                    help="미등록 발신자 검사를 끈다(느린 회선에서만).")
    ap.add_argument("--for", dest="purpose", default="study", choices=["study", "trade"],
                    help="재료의 용도. newsletters.json의 use_for로 소스를 고르고 "
                         "파일 이름도 갈라진다(study=digest_input_YYMMDD.md, "
                         "trade=digest_input_YYMMDD_trade.md).")
    args = ap.parse_args()
    if args.since and args.days != 1:
        ap.error("--since와 --days는 같이 쓰지 않는다(--since가 창을 정한다).")
    only = [x for x in args.only.split(",") if x.strip()] if args.only else None

    result = collect(args.date, args.days, args.purpose, since=args.since, only=only,
                     roster_check=not args.no_roster_check)
    if args.slug:
        result["slug"] = re.sub(r"[^0-9A-Za-z가-힣]+", "", args.slug)[:24]

    # ★ 경고는 재료 유무와 무관하게 먼저 낸다 — 빠진 재료는 파일을 봐서는 안 보인다.
    if result["unregistered"]:
        print(f"\n★ 미등록 발신자 {len(result['unregistered'])}건 — 뉴스레터면 newsletters.json에 "
              "등록하고 다시 수집할 것(명단 밖은 조용히 빠진다):", file=sys.stderr)
        for u in result["unregistered"]:
            print(f"    {u['address']} | {u['name'][:24]} | {u['count']}통 | {u['subject'][:50]}"
                  f"  ({u['why']})", file=sys.stderr)
    if result["spam_count"]:
        print(f"\n★ 스팸함에서 건진 발행분 {result['spam_count']}통(헤더에 ⚠스팸함) — 재료에는 "
              "넣었으니 그대로 쓰되, Gmail 필터('스팸함으로 보내지 않음')를 만들라고 보고할 것.",
              file=sys.stderr)

    if not result["items"]:
        span = f"{args.since} 이후" if args.since else f"{args.date} 기준 최근 {args.days}일간"
        print(f"{span} 도착한 뉴스레터가 없습니다.", file=sys.stderr)
        return

    path = write_input(result)
    for it in result["items"]:
        flag = "⚠" if it.get("spam") else " "
        print(f" {flag}[{it['day']}] {it['source']:18} {it['chars']:6d}자  {it['subject'][:44]}",
              file=sys.stderr)
    print(f"\n{len(result['items'])}건 → {path}", file=sys.stderr)
    print(path)


if __name__ == "__main__":
    main()
