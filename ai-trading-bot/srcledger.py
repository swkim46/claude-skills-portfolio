#!/usr/bin/env python3
"""출처 원장 — 사실마다 **세 시점**을 기록한다.

왜 세 개인가: 노트만 보고는 "삼성생명 305,000"이 **언제 값인지** 알 수 없었다.
실제로 한 노트 안에 섞여 있었다 — 캡처 13:30 · `price_levels` 재호출 15:20(308,500으로
값이 바뀜) · 종가 15:30 · 리츠 통계 as-of 2026-08말 · 정제마진 기사 as-of 2026-08-31 ·
리밸런싱 자료 fetch 2026-09-09. 다음에 그 노트를 볼 때 판단이 어긋나는 이유가 이것이다.

| 필드 | 뜻 | 어디서 오나 |
|---|---|---|
| `as-of` | 그 사실이 **사실인** 시점(기준일·발표일) | **수동** — 출처가 말하는 날짜 |
| `retrieved` | **우리가 가져온** 시점 | 자동 — 이 스크립트를 부른 시각 |
| `written` | **노트에 쓴** 시점 | 자동 — `render`가 찍는 시각 |

`fact-check`의 시효 2축(as-of × 이용 시점)과 같은 축이고, 여기서 '이용 시점'을
`retrieved`/`written` 둘로 쪼갠 것이다 — 가져온 뒤 며칠 지나 쓰는 일이 실제로 있으므로.

사용:
    python3 srcledger.py add --stamp 260910 --market kr \
        --fact "상장 리츠 23개 · 전체 476개 · AUM 129.5조원" \
        --as-of 2026-08-31 --source "한국리츠협회 통계" --tier 1 \
        --url https://www.kareit.or.kr/reference/page1.php
    python3 srcledger.py render --stamp 260910 --market kr     # 노트에 붙일 표
    python3 srcledger.py check  --stamp 260910 --market kr     # 게이트용 대조
"""
from __future__ import annotations

import argparse
import io
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
EVID = HERE / "data" / "run_evidence"
ANALYSIS = HERE / "analysis"
KST = timezone(timedelta(hours=9))
# 이 정규식이 '링크'의 정의다. 노트의 마크다운 링크와 맨 URL 둘 다 센다.
RX_URL = re.compile(r"https?://[^\s)\]\"'<>]+")
# 완료 표시와 작성 시각은 **두 줄로 분리**돼 있다(`stage.py stamp()`).
#   <!-- ✓ §4 -->
#   <!-- written §4 2026-09-11 02:44 -->
# ★ 한 줄로 합치면 안 된다 — 게이트의 기준들이 `<!-- ✓ §N -->`을 글자 그대로 찾으므로
#   안에 시각을 끼워 넣으면 그 패턴이 깨진다(2026-09-11에 실제로 막혔다).
# `머리말`도 절이다 — 예전 정규식은 `§`로 시작하는 것만 잡아 머리말을 통째로 놓쳤다.
_SEC = r"(§[0-9A-Za-z-]+|머리말|작업기록-\w+)"
RX_DONE = re.compile(r"<!--\s*✓\s*" + _SEC + r"\s*-->")
RX_WRITTEN = re.compile(r"<!--\s*written\s+" + _SEC +
                        r"\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})\s*-->")


SHARED = HERE / "journal" / "sources.json"


def _norm_fact(s: str) -> str:
    """같은 사실을 알아보기 위한 정규화 — 공백·문장부호를 지운 소문자."""
    return re.sub(r"[\s·,.()\[\]{}\"'`]+", "", (s or "").lower())


def load_shared() -> dict:
    """누적 공용 원장. **사실은 시장에 속하지 않는다.**

    ★ 왜 공용인가 — 예전 원장은 `(날짜, 시장)`으로 갈려서, 어젯밤 미국 run이 세운
    `as-of`/`retrieved`를 아침 국내 run이 못 봤다. 같은 사실(유가·CPI·환율)을 두 번
    조사하게 되고, 두 번째 조사가 첫 번째와 다른 시점을 적으면 **어느 것이 맞는지
    알 수 없게 된다.** 사실마다 영속 id를 주고 어느 run이 인용했는지를 태그로 남긴다.
    """
    if not SHARED.exists():
        return {"schema_version": "1.1", "sources": []}
    try:
        d = json.loads(SHARED.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("sources"), list):
            return d
        raise ValueError("구조가 {'sources': [...]}가 아니다")
    except (json.JSONDecodeError, OSError, ValueError) as e:
        aside = SHARED.with_name(f"{SHARED.stem}.corrupt_{datetime.now(KST):%y%m%d_%H%M%S}.json")
        try:
            SHARED.rename(aside); where = aside.name
        except OSError:
            where = "(보존 실패)"
        print(f"★ 공용 출처 원장을 읽을 수 없어 {where}로 옮겨 보존했다 ({e}) — "
              f"누적 이력이 있으니 지우지 말 것.", file=sys.stderr)
        return {"schema_version": "1.1", "sources": []}


def save_shared(d: dict) -> None:
    SHARED.parent.mkdir(parents=True, exist_ok=True)
    SHARED.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def next_src_id(rows: list) -> str:
    used = {int(m.group(1)) for r in rows
            for m in [re.match(r"SRC-(\d+)$", r.get("id", ""))] if m}
    return f"SRC-{(max(used) + 1 if used else 1):04d}"


def upsert_fact(row: dict, stamp: str, market: str) -> tuple:
    """사실 하나를 공용 원장에 넣거나 **기존 것을 재사용**한다. 반환 (행, 신규 여부).

    ★ 재사용할 때 `retrieved`는 **원래 시점을 유지한다.** 그게 세 시점의 취지다 —
    `retrieved`는 '이 정보를 언제 가져왔는가'이지 '언제 다시 인용했는가'가 아니다.
    새로 찍히는 것은 `written`뿐이고, 그건 render가 매번 지금 시각으로 찍는다.
    """
    d = load_shared()
    key = _norm_fact(row.get("fact"))
    tag = f"{market}:{stamp}"
    for r in d["sources"]:
        # ★ **사실 단위로만 대조한다.** URL을 키로 쓰면 한 기사에서 나온 여러 사실이
        # 하나로 합쳐져 나머지가 사라진다(실측: Axios 기사 하나에 사실 6건 → 1건으로
        # 뭉개짐). 같은 출처에서 여러 사실이 나오는 것이 정상이다.
        if _norm_fact(r.get("fact")) != key:
            continue
        if tag not in r.setdefault("runs", []):
            r["runs"].append(tag)
        # 비어 있던 칸만 채운다 — 기존 시점을 덮지 않는다.
        for k in ("as_of", "source", "url", "tier"):
            if not r.get(k) or r.get(k) == "미확인":
                if row.get(k):
                    r[k] = row[k]
        save_shared(d)
        return r, False
    r = dict(row)
    r["id"] = next_src_id(d["sources"])
    r["runs"] = [tag]
    d["sources"].append(r)
    save_shared(d)
    return r, True


def store(stamp: str, market: str) -> Path:
    return EVID / f"sources_{stamp}_{market}.json"


def load(stamp: str, market: str) -> dict:
    """이 run의 원장 뷰 — **공용 원장에서 이 run 태그가 붙은 것**을 꺼낸다.
    구 per-run 파일이 남아 있으면 같이 읽어 합친다(이관 전 run도 깨지지 않게).
    """
    tag = f"{market}:{stamp}"
    rows = [r for r in load_shared()["sources"] if tag in (r.get("runs") or [])]
    legacy = _load_legacy(stamp, market)
    have = {_norm_fact(r.get("fact")) for r in rows}
    rows += [r for r in legacy["rows"] if _norm_fact(r.get("fact")) not in have]
    return {"stamp": stamp, "market": market, "rows": rows}


def _load_legacy(stamp: str, market: str) -> dict:
    p = store(stamp, market)
    if not p.exists():
        return {"stamp": stamp, "market": market, "rows": []}
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("rows"), list):
            return d
        raise ValueError("구조가 {'rows': [...]}가 아니다")
    except (json.JSONDecodeError, OSError, ValueError) as e:
        aside = p.with_name(f"{p.stem}.corrupt_{datetime.now(KST):%H%M%S}.json")
        try:
            p.rename(aside)
            where = aside.name
        except OSError:
            where = "(보존 실패)"
        print(f"★ 출처 원장을 읽을 수 없어 {where}로 옮겨 보존했다 ({e}) — 덮어쓰지 않았다.",
              file=sys.stderr)
        return {"stamp": stamp, "market": market, "rows": []}


def save(d: dict) -> None:
    p = store(d["stamp"], d["market"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _tier(v):
    """1·2·3 또는 'data'(브로커 API 값 — 2026-09-22: 시세·기저율·수준값을 T1로 태그해 등급이 흐려졌다)."""
    if v is None:
        return None
    sv = str(v).strip().lower()
    if sv == "data":
        return "data"
    if sv in ("1", "2", "3"):
        return int(sv)
    raise argparse.ArgumentTypeError(f"tier는 1|2|3|data: {v!r}")


def cmd_add(a) -> int:
    # ★ --batch: 사실을 JSONL(행마다 {"fact","as_of"?,"source"?,"url"?,"tier"?,"minor"?})로 한 번에.
    #   20건을 셸 변수 명령으로 부르다 전부 실패해 §12가 "출처 없음"으로 나왔다(2026-09-16 KR run).
    if getattr(a, "batch", ""):
        import types as _types
        txt = Path(a.batch).read_text(encoding="utf-8")
        rows = json.loads(txt) if txt.lstrip().startswith("[") else [
            json.loads(ln) for ln in txt.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        rc = 0
        for i, row in enumerate(rows, 1):
            if not row.get("fact"):
                print(f"  [{i}] 건너뜀 — fact 필요: {row}", file=sys.stderr)
                rc = 2
                continue
            sub = _types.SimpleNamespace(stamp=a.stamp, market=a.market, fact=row["fact"],
                                         as_of=row.get("as_of") or row.get("as-of") or "",
                                         source=row.get("source") or "", url=row.get("url") or "",
                                         tier=_tier(row.get("tier")) if row.get("tier") is not None else None,
                                         minor=bool(row.get("minor")), batch="")
            rc = max(rc, cmd_add(sub))
        print(f"batch 기록 {len(rows)}건 ← {a.batch}")
        return rc
    d = load(a.stamp, a.market)
    now = datetime.now(KST)
    row = {
        "fact": a.fact.strip(),
        "as_of": (a.as_of or "").strip(),          # 수동 — 출처가 말하는 시점
        "retrieved": now.strftime("%Y-%m-%d %H:%M"),   # 자동 — 지금 가져왔다
        "source": (a.source or "").strip(),
        "url": (a.url or "").strip(),
        "tier": a.tier,
        "load_bearing": not a.minor,
    }
    if not row["as_of"]:
        # ★ 막지는 않는다(모르는 것을 아는 척 적게 만들면 더 나쁘다). 대신 남긴다.
        row["as_of"] = "미확인"
        print("  ※ as-of를 안 줬다 → '미확인'으로 기록했다. **load-bearing 사실이면 "
              "게이트가 막는다** — 출처의 기준일·발표일을 찾아 다시 넣어라.", file=sys.stderr)
    saved, is_new = upsert_fact(row, a.stamp, a.market)
    n = len(load(a.stamp, a.market)["rows"])
    if is_new:
        print(f"기록: {saved['id']} {saved['fact'][:56]} · as-of {saved['as_of']} "
              f"· retrieved {saved['retrieved']} (이 run {n}행)")
    else:
        print(f"재사용: {saved['id']} {saved['fact'][:56]} — 이미 원장에 있다. "
              f"as-of {saved['as_of']} · retrieved {saved['retrieved']}(원래 시점 유지) "
              f"· 인용 run {len(saved.get('runs') or [])}개")
        print("  ※ **다시 조사하지 않아도 된다.** 세 시점 중 새로 찍히는 건 written뿐이다.")
    return 0


def cmd_render(a) -> int:
    """노트 말미에 붙일 5열 표. `written`은 **이 시각**이다."""
    d = load(a.stamp, a.market)
    written = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    buf = io.StringIO()
    print("## §12 출처 원장 — 세 시점\n", file=buf)
    print("> 각 사실이 **어느 시점의 정보인지**(as-of), **언제 가져왔는지**(retrieved),",
          file=buf)
    print("> **언제 이 노트에 썼는지**(written)를 분리해 적는다. 다음에 이 노트를 볼 때",
          file=buf)
    print("> 어느 숫자가 낡았는지 판단할 수 있어야 하기 때문이다.\n", file=buf)
    if not d["rows"]:
        print("*(이번 run에 기록된 외부 출처 없음 — 캡처 파일의 시세만 사용했다면 그렇게 적는다)*",
              file=buf)
    else:
        print("| 사실 | as-of | retrieved | written | 출처 |", file=buf)
        print("|---|---|---|---|---|", file=buf)
        for r in d["rows"]:
            src = r.get("source") or "—"
            if r.get("url"):
                src = f"[{src}]({r['url']})"
            if r.get("tier"):
                src += f" ({'data' if r['tier'] == 'data' else 'T' + str(r['tier'])})"
            flag = "" if r.get("load_bearing", True) else " *(참고)*"
            print(f"| {r['fact']}{flag} | {r.get('as_of') or '미확인'} "
                  f"| {r.get('retrieved') or '미기록'} | {written} | {src} |", file=buf)
    # 두 줄로 — `✓` 줄은 게이트가 글자 그대로 찾는 계약이라 안을 고치면 안 된다.
    print(f"\n<!-- ✓ §12 -->\n<!-- written §12 {written} -->", file=buf)
    out = buf.getvalue()
    print(out)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(out, encoding="utf-8")
        print(f"→ {a.out}")
    return 0


def note_path(stamp: str, market: str, given: str = "") -> Path:
    if given:
        return Path(given)
    c = sorted(ANALYSIS.glob(f"분석노트_{stamp}_{market}_v*.md"))
    return c[-1] if c else ANALYSIS / f"분석노트_{stamp}_{market}_없음.md"


def cmd_check(a) -> int:
    """게이트용 대조. 셋을 본다 — 원장 규모 · as-of 누락 · 스탬프 시각."""
    d = load(a.stamp, a.market)
    np_ = note_path(a.stamp, a.market, a.note)
    if not np_.exists():
        print(f"분석노트가 없다: {np_}", file=sys.stderr)
        return 2
    note = np_.read_text(encoding="utf-8", errors="ignore")
    rows = d["rows"]

    # ① 노트가 인용한 URL이 원장에 있는가 — 원장은 링크 수 이상이어야 한다.
    note_urls = {u.rstrip(".,);") for u in RX_URL.findall(note)}
    led_urls = {r["url"].rstrip(".,);") for r in rows if r.get("url")}
    missing = sorted(note_urls - led_urls)

    # ② load-bearing 사실에 as-of가 있는가.
    no_asof = [r["fact"][:60] for r in rows
               if r.get("load_bearing", True) and (r.get("as_of") in ("", "미확인", None))]

    # ③ 노트의 완료 스탬프에 시각이 붙었는가 — **절 단위로 본다.**
    #
    # ★ 왜 스탬프 단위가 아니라 절 단위인가 (2026-09-11):
    #   stepgate의 봉인 루브릭은 절마다 **시각 없는** `<!-- ✓ §N -->`을 찾고,
    #   이 검사는 **시각 있는** `<!-- ✓ §N · written ... -->`을 요구한다.
    #   한 노트가 둘을 동시에 만족하려면 절마다 두 형태를 다 두는 수밖에 없는데,
    #   스탬프 단위로 세면 그 순간 이 검사가 실패해 **두 게이트가 서로를 막는다**.
    #   이 검사의 취지는 "각 절이 **언제 쓰였는지** 남는가"이지 "스탬프가 한 개인가"가
    #   아니므로, **그 절에 시각 있는 스탬프가 하나라도 있으면 통과**로 본다.
    #   시각이 아예 없는 절만 실패로 센다 — 취지는 그대로 지켜진다.
    done = {m.group(1) for m in RX_DONE.finditer(note)}
    timed_sections = {m.group(1) for m in RX_WRITTEN.finditer(note)}
    stamps = sorted(done)                      # 완료 표시가 붙은 절
    timeless = sorted(done - timed_sections)   # 완료는 됐는데 작성 시각이 없는 절

    checks = [
        (f"원장 {len(rows)}행 · 노트 링크 {len(note_urls)}개",
         not missing,
         "링크 전건이 원장에 있다" if not missing else
         f"**원장에 없는 링크 {len(missing)}개**: {', '.join(u[:60] for u in missing[:4])}"
         + (" …" if len(missing) > 4 else "")),
        (f"load-bearing as-of ({len(rows) - len(no_asof)}/{len(rows)})",
         not no_asof,
         "전건에 as-of가 있다" if not no_asof else
         f"**as-of 미확인 {len(no_asof)}건**: {'; '.join(no_asof[:3])}"),
        (f"스탬프 시각 ({len(stamps) - len(timeless)}/{len(stamps)})",
         bool(stamps) and not timeless,
         "전 스탬프에 written 시각이 있다" if stamps and not timeless else
         (f"**시각 없는 스탬프 {len(timeless)}개**: {', '.join(timeless[:6])}"
          if timeless else "스탬프가 하나도 없다")),
    ]
    n_fail = sum(1 for _, ok, _ in checks if not ok)
    buf = io.StringIO()
    print(f"# 세 시점 대조 — {a.stamp} · {a.market.upper()}\n", file=buf)
    print(f"> 대상 `{np_.name}` · 실행 {datetime.now(KST):%Y-%m-%d %H:%M:%S} KST\n", file=buf)
    print(f"[세시점] 검사 {len(checks)}건 · 실패 {n_fail}건\n", file=buf)
    print("| 검사 | 결과 | 근거 |\n|---|---|---|", file=buf)
    for name, ok, why in checks:
        print(f"| {name} | {'✓' if ok else '**✗**'} | {why} |", file=buf)
    if n_fail:
        print("\n**세 시점 중 하나라도 비면 다음에 이 노트를 볼 때 어느 숫자가 낡았는지 "
              "판단할 수 없다.** `srcledger.py add`로 채우고, 스탬프에는 "
              "`<!-- ✓ §N · written YYYY-MM-DD HH:MM -->` 형식으로 시각을 붙여라.", file=buf)
    out = buf.getvalue()
    print(out)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(out, encoding="utf-8")
        print(f"→ {a.out}")
    return 1 if n_fail else 0


def cmd_reuse(a) -> int:
    """다른 run이 이미 세운 사실을 보여준다 — **조사 전에 여는 목록**이다.

    ★ 왜 자동 일치에 맡기지 않는가 — 노트 언어를 재료 언어에 맞추므로 같은 사실이
    국내 run에서는 한국어로, 미국 run에서는 영어로 적힌다("브렌트유 108달러" vs
    "Brent at $108"). 문자열로는 영원히 안 겹친다. 그러니 **사람이(모델이) 보고
    알아보게** 한다 — 겹치면 `--as-of`를 다시 파지 말고 그 시점을 그대로 쓴다.
    """
    d = load_shared()
    tag = f"{a.market}:{a.stamp}"
    cutoff = (datetime.now(KST) - timedelta(days=a.days)).strftime("%y%m%d")
    rows = []
    for r in d["sources"]:
        runs = r.get("runs") or []
        if tag in runs:
            continue                       # 이번 run이 이미 인용한 것
        if not any(x.split(":")[1] >= cutoff for x in runs if ":" in x):
            continue
        rows.append(r)
    rows.sort(key=lambda r: (not r.get("load_bearing", True), r.get("as_of") or ""), reverse=False)
    print(f"■ 재사용 후보 — 최근 {a.days}일 다른 run이 세운 사실 {len(rows)}건 "
          f"(이번 run {tag} 기준)\n")
    if not rows:
        print("  없음.")
        return 0
    brief = bool(getattr(a, "brief", False))
    for r in rows:
        flag = "" if r.get("load_bearing", True) else " *(참고)*"
        if brief:
            # ★ 2026-09-22 실측: 176건×3줄(48KB)이 노트에 그대로 쓰인 적 0 — 목적(중복 조사 방지)은 한 줄이면 된다.
            t = r.get("tier")
            tt = "data" if t == "data" else (f"T{t}" if t else "—")
            print(f"  {r['id']} {tt} as-of {r.get('as_of') or '?'} | {r['fact'][:90]}{flag}")
            continue
        print(f"  {r['id']}  [{' · '.join(r.get('runs') or [])}]{flag}")
        print(f"     {r['fact'][:104]}")
        print(f"     as-of {r.get('as_of') or '미확인'} · retrieved {r.get('retrieved') or '미기록'}"
              f" · {r.get('source') or '—'}")
    print("\n  **겹치는 사실은 다시 조사하지 않는다.** 같은 문구로 `add`하면 "
          "원장이 알아보고 as-of·retrieved를 원래 시점 그대로 유지한다 — "
          "새로 찍히는 것은 written뿐이다.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("add", "사실 1건 기록(retrieved 자동)"),
                        ("render", "노트용 5열 표(written 자동)"),
                        ("check", "게이트용 대조"),
                        ("reuse", "다른 run이 이미 세운 사실 — 조사 전에 연다")):
        s = sub.add_parser(name, help=help_)
        s.add_argument("--stamp", required=True)
        s.add_argument("--market", required=True, choices=["kr", "us"])
        if name == "add":
            s.add_argument("--fact", default="")
            s.add_argument("--batch", default="", help="JSONL: 행마다 {fact, as_of?, source?, url?, tier?, minor?}")
            s.add_argument("--as-of", dest="as_of", default="",
                           help="출처가 말하는 기준일·발표일 (수동 — 이것만 자동이 아니다)")
            s.add_argument("--source", default="")
            s.add_argument("--url", default="")
            s.add_argument("--tier", type=_tier, choices=[1, 2, 3, "data"],
                           help="1 공식·1차 / 2 언론·집계 / 3 UGC·요약 / data = 브로커 API 시세·기저율·수준값(논지 근거가 아니라 데이터)")
            s.add_argument("--minor", action="store_true",
                           help="load-bearing이 아니면(참고용) as-of 강제에서 빠진다")
        elif name == "reuse":
            s.add_argument("--days", type=int, default=7)
            s.add_argument("--brief", action="store_true", help="사실당 한 줄(id·등급·as-of·사실)")
        else:
            s.add_argument("--out", default="")
        if name == "check":
            s.add_argument("--note", default="")
    a = ap.parse_args()
    if a.cmd == "add" and not a.batch and not a.fact:
        ap.error("add에는 --fact 또는 --batch <jsonl>이 필요하다")
    return {"add": cmd_add, "render": cmd_render, "check": cmd_check,
            "reuse": cmd_reuse}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
