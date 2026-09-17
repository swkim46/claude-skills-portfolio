#!/usr/bin/env python3
"""
Survey recent mail and propose a newsletter sender roster.

The analyzer is sender-driven, so it needs a declared list of newsletter senders.
Rather than guess what counts as a newsletter, this groups real mail by sender and
scores each one on bulk-mail signals.

Primary signal is RFC 2369 List-Unsubscribe / List-Id. Those headers are what
bulk senders set and ordinary correspondents do not, which separates newsletters
from personal mail far more reliably than address patterns like "no-reply@".

★ 이 스캐너가 영구히 놓치는 부류가 있다: **Apple Hide My Email 릴레이를 거친 발신**.
릴레이가 List-Unsubscribe·List-Id를 제거하므로 primary signal이 통째로 사라지고,
발신 주소도 `<원local>_at_<원도메인>_<계정키>_<별칭해시>@icloud.com`으로 재작성된다.
newsletters.json의 Axios 3종이 그 사례이며 **수동 등록**이다 —
이 스캐너 결과로 그것들을 excluded로 되돌리지 말 것.

Usage:
    python3 scan_senders.py [--days 90] [--min-count 2]
    python3 scan_senders.py --axios --days 14      # 릴레이별 도착·스팸함 점검 표(파일 안 씀)
"""
import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import datetime

import gmail_imap as g

HEADERS = "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE LIST-UNSUBSCRIBE LIST-ID)])"
BATCH = 200


def parse_addr(raw: str):
    """Split a From header into (display name, lowercased email)."""
    decoded = g._header(raw)
    match = re.search(r"<([^>]+)>", decoded)
    addr = (match.group(1) if match else decoded).strip().lower()
    name = decoded.split("<")[0].strip().strip('"') if match else ""
    return name, addr


def collect(imap, days: int, quiet: bool = False, query: str = None) -> dict:
    """Fetch headers for the window and group them by sender address.

    `query` overrides the default `newer_than:{days}d` (digest.py passes a date
    range so its roster-drift check sees exactly the window it collected).
    """
    uids = g.search(imap, query or f"newer_than:{days}d", max_results=100000)
    if not quiet:
        print(f"  {len(uids)} messages in the last {days} days", file=sys.stderr)

    senders = defaultdict(lambda: {
        "name": "", "count": 0, "list_unsub": 0, "list_id": "",
        "subjects": [], "last_seen": "",
    })

    import email as email_mod
    for i in range(0, len(uids), BATCH):
        chunk = uids[i:i + BATCH]
        typ, data = imap.uid("FETCH", ",".join(chunk), HEADERS)
        if typ != "OK":
            raise RuntimeError(f"FETCH failed: {data}")
        if not quiet:
            print(f"  fetched {min(i + BATCH, len(uids))}/{len(uids)}", file=sys.stderr)

        for item in data:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            msg = email_mod.message_from_bytes(item[1])
            name, addr = parse_addr(msg.get("From", ""))
            if not addr:
                continue

            s = senders[addr]
            s["count"] += 1
            if name and not s["name"]:
                s["name"] = name
            if msg.get("List-Unsubscribe"):
                s["list_unsub"] += 1
            if msg.get("List-Id") and not s["list_id"]:
                s["list_id"] = g._header(msg.get("List-Id"))[:60]
            if len(s["subjects"]) < 3:
                subject = g._header(msg.get("Subject"))
                if subject:
                    s["subjects"].append(subject[:70])
            date = msg.get("Date", "")
            if date > s["last_seen"]:
                s["last_seen"] = date
    return senders


def classify(addr: str, s: dict, days: int, min_count: int) -> tuple:
    """Return (verdict, reason). Bulk headers decide; frequency breaks ties."""
    bulk = s["list_unsub"] > 0 or bool(s["list_id"])
    recurring = s["count"] >= min_count

    if bulk and recurring:
        return "NEWSLETTER", f"bulk headers + {s['count']}x in {days}d"
    if bulk:
        return "MAYBE", "bulk headers, but only seen once"
    if recurring:
        return "MAYBE", f"{s['count']}x but no bulk headers — likely a service or a person"
    return "NO", "one-off, no bulk headers"


def relay_report(days: int, keyword: str = "axios") -> int:
    """릴레이 발신자별 (명단 등록 여부 · 도착일 · 스팸함 여부) 표. 파일은 쓰지 않는다.

    "또 안 본 발행물이 있는지"를 사람이 한눈에 보는 자리. digest.py가 매 수집마다 미등록·스팸함
    경고를 내지만, 며칠 조용하면 이 표로 릴레이별 도착 흐름을 본다.
    2026-09-16 실측: Markets 미등록 7통 · Macro/Closer 9/12부터 스팸함 — 둘 다 여기서 드러남.
    from: 부분일치가 안 되므로 전문 검색(뉴스레터 본문에 발행사 이름이 들어 있음)으로 모은다.
    """
    cfg = json.loads((g.HERE / "newsletters.json").read_text(encoding="utf-8"))
    known = {n["sender"].lower(): n["name"] for n in cfg["newsletters"]}
    excluded = {e["sender"].lower() for e in cfg.get("excluded", [])}
    query = f"{keyword} newer_than:{days}d"
    rows = defaultdict(lambda: {"name": "", "days": set(), "spam_days": set(), "subjects": []})

    def scan(imap, spam):
        for m in g.fetch_metadata(imap, g.search(imap, query, 2000)):
            name, addr = parse_addr(m.get("from", ""))
            if not addr:
                continue
            dt = g.message_kst(m)
            day = dt.strftime("%m/%d") if dt else "?"
            r = rows[addr]
            r["name"] = r["name"] or name
            (r["spam_days"] if spam else r["days"]).add(day)
            if len(r["subjects"]) < 2:
                r["subjects"].append(str(m.get("subject", ""))[:40])

    with g.connect() as imap:
        scan(imap, spam=False)
        spam_box = g.find_spam(imap)
    if spam_box:
        with g.connect(spam_box) as imap:
            scan(imap, spam=True)

    problems = 0
    print(f"\n'{keyword}' 전문검색 최근 {days}일 — 발신 주소 {len(rows)}개"
          f"{'' if spam_box else ' (스팸함 없음)'}\n")
    for addr, r in sorted(rows.items(), key=lambda kv: -(len(kv[1]['days']) + len(kv[1]['spam_days']))):
        if addr in known:
            tag = "등록 " + known[addr]
        elif addr in excluded:
            tag = "제외"
        else:
            tag = "★ 미등록"
            problems += 1
        spam = ", ".join(sorted(r["spam_days"]))
        if spam:
            problems += 1
        print(f"[{tag}] {addr}")
        print(f"    {r['name'][:24]} | 도착 {len(r['days'])}통: {', '.join(sorted(r['days'])) or '-'}")
        if spam:
            print(f"    ★ 스팸함 {len(r['spam_days'])}통: {spam}")
        print(f"    예: {' / '.join(r['subjects'])}")
    if problems:
        print("\n→ 미등록이면 newsletters.json에 등록, 스팸함이면 Gmail 필터('스팸함으로 보내지 않음') 생성.")
    else:
        print("\n→ 미등록·스팸함 없음.")
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--min-count", type=int, default=2)
    ap.add_argument("--axios", nargs="?", const="axios", metavar="KEYWORD",
                    help="릴레이 발신자 점검 표(전체보관함+스팸함, 파일 안 씀). 기본 키워드 axios.")
    args = ap.parse_args()

    if args.axios:
        sys.exit(relay_report(args.days, args.axios))

    print("Scanning…", file=sys.stderr)
    with g.connect() as imap:
        senders = collect(imap, args.days)

    rows = []
    for addr, s in senders.items():
        verdict, reason = classify(addr, s, args.days, args.min_count)
        rows.append({"address": addr, "verdict": verdict, "reason": reason, **s})
    rows.sort(key=lambda r: (r["verdict"] != "NEWSLETTER", -r["count"]))

    out = g.HERE / "_raw_sources" / f"sender_scan_{datetime.now().strftime('%Y%m%d')}.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    counts = defaultdict(int)
    for r in rows:
        counts[r["verdict"]] += 1
    print(
        f"\n{len(rows)} distinct senders — "
        f"{counts['NEWSLETTER']} newsletter, {counts['MAYBE']} maybe, {counts['NO']} no",
        file=sys.stderr,
    )
    print(f"full scan written to {out}", file=sys.stderr)
    print(json.dumps(rows, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
