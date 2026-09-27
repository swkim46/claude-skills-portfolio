#!/usr/bin/env python3
"""시나리오 영속 원장 — **id가 run을 넘어 살아남는다.**

왜 있는가: 시그널의 `scenarios`는 매 run **id를 재사용한다**(실측: 9/8 `A~E` →
9/9 `A~F` → 9/10 오전 `A~G` → 9/10 오후 `S1~S8`). 같은 조건이 매번 다른 이름을 갖고
어제의 `A`와 오늘의 `A`가 다른 것을 가리키므로, **"기다리기로 한 것이 실현됐는데
대응했는가"를 셀 방법이 없다** — 회피 감사 3대 지표 중 하나가 계산 불가였다.

그래서 조건을 한 번 **영속 id `SC-<YYMMDD>-<n>`**로 승격하고, 이후 run은 그 id를
판정한다. 판정은 두 축이다: **실현됐는가(realized)** 와 **대응했는가(acted)**.
둘을 갈라야 "안 일어났다"와 "일어났는데 또 안 했다"가 구분된다 — 후자가 회피다.

사용:
    python3 scenarios.py promote --signal signals/signal_260910_kr_v2.json
    python3 scenarios.py backfill                  # 기존 시그널 전부 이관(1회)
    python3 scenarios.py list --open --market kr   # 2단 이어받기가 읽는다
    python3 scenarios.py judge --id SC-260910-1 --realized y --acted y --note "e2 발화·4주 체결"
    python3 scenarios.py audit --days 14           # 회피 감사 지표
"""
from __future__ import annotations

import argparse
import json
import types
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
JOURNAL = HERE / "journal"
STORE = JOURNAL / "scenarios.json"
KST = timezone(timedelta(hours=9))
FIELDS = ("condition", "expected", "response", "invalidation")


def load() -> dict:
    """원장을 읽는다. **못 읽으면 덮어쓰지 않고 옆으로 치운다** — 판정 이력이 사라지면
    회피 감사가 영구히 못 돈다(`execute.py`의 거래기록 보존과 같은 처방)."""
    if not STORE.exists():
        return {"scenarios": []}
    try:
        d = json.loads(STORE.read_text(encoding="utf-8"))
        if isinstance(d, dict) and isinstance(d.get("scenarios"), list):
            return d
        raise ValueError("최상위 구조가 {'scenarios': [...]}가 아니다")
    except (json.JSONDecodeError, OSError, ValueError) as e:
        aside = STORE.with_name(f"scenarios.corrupt_{datetime.now(KST):%y%m%d_%H%M%S}.json")
        STORE.rename(aside)
        print(f"\n★ 시나리오 원장을 읽을 수 없어 {aside.name}로 옮겨 보존했다 ({e}).\n"
              f"  판정 이력이 이 파일에 있으니 **지우지 말고** 사람이 확인할 것.", file=sys.stderr)
        return {"scenarios": []}


def save(d: dict) -> None:
    STORE.parent.mkdir(parents=True, exist_ok=True)
    STORE.write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sig_meta(p: Path) -> tuple:
    """(stamp, market) — 파일명 `signal_<YYMMDD>_<mkt>*.json`에서."""
    m = re.match(r"signal_(\d{6})_(kr|us)", p.name)
    return (m.group(1), m.group(2)) if m else ("", "")


def norm(s: str) -> str:
    """조건 문자열 정규화 — 표기가 조금 달라도 같은 조건을 두 번 승격하지 않기 위함."""
    return re.sub(r"[\s·,.**`\-—]+", "", str(s or "")).lower()


# ------------------------------------------------- 대응 추출 (조건은 시장에 속하지 않는다)

def _universe_index() -> list:
    """`(시장, 티커, 표기)` 목록 — 유니버스의 티커·이름·별칭을 전부 편다."""
    try:
        wl = json.loads((HERE / "config" / "watchlist.json").read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out = []
    for mk in ("KR", "US"):
        for r in (wl.get(mk) or []):
            tk = str(r.get("ticker") or "").strip()
            if not tk:
                continue
            forms = {tk, r.get("name") or ""} | set(r.get("aliases") or [])
            for f in forms:
                f = str(f).strip()
                if len(f) >= 2:
                    out.append((mk.lower(), tk, f))
    # 긴 표기를 먼저 본다 — '삼성전자우'가 '삼성전자'로 잡히지 않게.
    return sorted(out, key=lambda x: -len(x[2]))


def _market_marker(clause: str) -> str:
    """절 안의 시장 표지(`KR`·`US`·국내·미국)를 읽는다. 없으면 빈 문자열."""
    low = clause.lower()
    if re.search(r"\bkr\b|\bkorea\b|\bkospi\b|국내|한국", low):
        return "kr"
    if re.search(r"\bus\b|\busa\b|\bnasdaq\b|미국|해외", low):
        return "us"
    return ""


def extract_actions(text: str, fallback_market: str = "") -> list:
    """대응 문장에서 `{market, ticker, what}`을 뽑는다. **절 단위로 가른다.**

    ★ **조건은 시장에 속하지 않고 대응만 속한다.** 미국 run이 세운 조건이
    "KR Samsung ladder e1 fires; MSFT position held."처럼 **두 시장의 행동을 한 문장에**
    담는 일이 실제로 있었는데, 행이 `market: "us"` 하나뿐이라 국내 run의 필터에서
    통째로 빠졌다.

    한 문장을 통으로 보면 안 되는 이유가 그 예에 다 있다 — 통으로 보면 MSFT만 잡히고
    앞 절의 국내 행동이 미국 것으로 삼켜진다. **유니버스 표기는 한글뿐이라 영어 노트의
    'Samsung'은 티커로 안 잡히므로, 티커를 못 찾아도 시장 표지는 살린다.**

    못 가른 것은 `{"market": "?"}`로 남긴다 — 조용히 추측하지 않는다. 게이트가 센다.
    """
    s = (text or "").strip()
    if not s:
        return []
    uni = _universe_index()
    out, seen = [], set()
    for clause in [c.strip() for c in re.split(r"[;·\n]|(?<=[.。])\s+", s) if c.strip()]:
        mk_mark = _market_marker(clause)
        found = False
        for mk, tk, form in uni:
            if form in clause and (mk, tk) not in seen:
                seen.add((mk, tk))
                out.append({"market": mk_mark or mk, "ticker": tk,
                            "what": clause[:160], "done": False})
                found = True
        if not found and mk_mark:
            out.append({"market": mk_mark, "ticker": "",
                        "what": clause[:160], "done": False})
    if out:
        return out
    return [{"market": fallback_market or "?", "ticker": "",
             "what": s[:160], "done": False}]


def cmd_promote(a) -> int:
    p = Path(a.signal)
    if not p.is_absolute():
        p = HERE / p
    if not p.is_file():
        print(f"시그널이 없다: {p}", file=sys.stderr)
        return 2
    stamp, market = sig_meta(p)
    if not stamp:
        print(f"파일명에서 날짜·시장을 못 읽었다: {p.name}", file=sys.stderr)
        return 2
    try:
        sig = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"시그널을 읽을 수 없다: {e}", file=sys.stderr)
        return 2
    rows = sig.get("scenarios") or []
    if not isinstance(rows, list):
        print("scenarios가 리스트가 아니다.", file=sys.stderr)
        return 2

    d = load()
    by_id = {s.get("id"): s for s in d["scenarios"]}
    # 조건 문구 → 원장 행. 병합된 행은 목적지로 넘긴다. 별칭(`aliases`)도 같은 행을 가리킨다.
    have = {}
    for row_ in d["scenarios"]:
        tgt = _resolve_merged(by_id, row_)
        for cond in [row_.get("condition")] + list(row_.get("aliases") or []):
            k = norm(cond)
            if k and k not in have:
                have[k] = tgt
    n_new = n_dup = n_pid = n_alias = 0
    used = {int(m.group(1)) for s in d["scenarios"]
            for m in [re.match(rf"SC-{stamp}-(\d+)$", s.get("id", ""))] if m}
    nxt = max(used) + 1 if used else 1
    for r in rows:
        if not isinstance(r, dict):
            continue
        key = norm(r.get("condition"))
        if not key:
            continue
        tag = f"{p.name}#{r.get('id')}"
        # ★ `persistent_id`가 원장 id를 가리키면 **그 행이 같은 조건이다** — 문구를 다듬어도 새 id를 주지 않는다.
        #   2026-09-21: 문구만 손본 이월 8건이 새 id를 받아 손으로 병합했다(promote가 문구로만 동일성을 봤다).
        pid = str(r.get("persistent_id") or "").strip()
        claimed_missing = ""
        if pid:
            tgt = _resolve_merged(by_id, by_id.get(pid)) if pid in by_id else None
            if tgt is not None:
                src = tgt.setdefault("sources", [])
                if tag not in src:
                    src.append(tag)
                if key not in have or have[key] is not tgt:
                    if key != norm(tgt.get("condition")) and r.get("condition") not in (tgt.get("aliases") or []):
                        tgt.setdefault("aliases", []).append(r.get("condition"))
                        n_alias += 1
                    have[key] = tgt
                n_pid += 1
                continue
            claimed_missing = pid
            print(f"  ★ persistent_id {pid}가 원장에 없다 — 새 id로 승격하고 출처에 남긴다 ({tag})",
                  file=sys.stderr)
        if key in have:
            # 같은 조건이 다시 실렸다 — 새 id를 주지 않고 **출처만 덧붙인다.**
            src = have[key].setdefault("sources", [])
            if tag not in src:
                src.append(tag)
            n_dup += 1
            continue
        sid = f"SC-{stamp}-{nxt}"
        nxt += 1
        row = {"id": sid, "opened_by": f"{market}:{stamp}", "market": market,
               "opened": stamp, "local_id": r.get("id"), "status": "open",
               "realized": None, "acted": None,
               "axis": r.get("axis") or "",
               "sources": [f"{p.name}#{r.get('id')}"],
               "judgments": []}
        row.update({k: r.get(k) for k in FIELDS if r.get(k)})
        if claimed_missing:
            row["claimed_persistent_id"] = claimed_missing
            row["sources"].append(f"persistent_id:{claimed_missing}(원장에 없음)")
        # 대응은 시그널이 실어 보냈으면 그대로, 아니면 response 문장에서 뽑는다.
        row["actions"] = (r.get("actions") if isinstance(r.get("actions"), list)
                          else extract_actions(r.get("response"), market))
        d["scenarios"].append(row)
        have[key] = row
        by_id[sid] = row
        n_new += 1
    save(d)
    print(f"{p.name}: 신규 {n_new}건 승격 · 기존 조건 재등장 {n_dup}건(출처만 추가) "
          f"· persistent_id 매칭 {n_pid}건(별칭 추가 {n_alias}) · 원장 총 {len(d['scenarios'])}건")
    return 0


def _resolve_merged(by_id: dict, row, hops: int = 5):
    """병합된 행(`status: merged` · `merged_into`)은 목적지 행으로 — 최대 hops."""
    while row is not None and row.get("status") == "merged" and row.get("merged_into") and hops > 0:
        row = by_id.get(row["merged_into"])
        hops -= 1
    return row


def cmd_merge(a) -> int:
    """`--from` 행을 `--into` 행에 병합한다 — 같은 조건이 두 id를 받았을 때(손편집 대신 명령).
    from은 `status: merged` + `merged_into`, into는 별칭·출처·대응을 흡수한다."""
    d = load()
    by_id = {s.get("id"): s for s in d["scenarios"]}
    src, dst = by_id.get(a.src), by_id.get(a.into)
    if src is None or dst is None:
        print(f"그런 id가 없다: {a.src if src is None else a.into}", file=sys.stderr)
        return 2
    if a.src == a.into:
        print("같은 id끼리는 병합할 수 없다", file=sys.stderr)
        return 2
    if dst.get("status") == "merged":
        print(f"{a.into}는 이미 {dst.get('merged_into')}에 병합됐다 — 그 id로 병합하라", file=sys.stderr)
        return 2
    now = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    for cond in [src.get("condition")] + list(src.get("aliases") or []):
        if cond and norm(cond) != norm(dst.get("condition")) and cond not in (dst.get("aliases") or []):
            dst.setdefault("aliases", []).append(cond)
    for tag in src.get("sources") or []:
        if tag not in dst.setdefault("sources", []):
            dst["sources"].append(tag)
    if not dst.get("actions") and src.get("actions"):
        dst["actions"] = src["actions"]
    dst.setdefault("judgments", []).append({"when": now, "note": f"{a.src} 병합(merge)"})
    src["status"] = "merged"
    src["merged_into"] = a.into
    src["merged_at"] = now
    save(d)
    print(f"{a.src} → {a.into} 병합 (별칭 {len(dst.get('aliases') or [])} · 출처 {len(dst.get('sources') or [])})")
    return 0


def cmd_backfill(a) -> int:
    files = sorted((HERE / "signals").glob("signal_*.json"))
    files = [f for f in files if sig_meta(f)[0]]
    print(f"■ 기존 시그널 {len(files)}개를 영속 id로 이관한다\n")
    rc = 0
    for f in files:
        ns = argparse.Namespace(signal=str(f))
        rc |= cmd_promote(ns)
    d = load()
    print(f"\n원장 총 {len(d['scenarios'])}건 → {STORE.relative_to(HERE)}")
    return rc


def _read_batch(path: Path) -> list:
    """JSONL 또는 JSON 배열. 빈 줄·`#` 줄은 건너뛴다."""
    txt = path.read_text(encoding="utf-8")
    if txt.lstrip().startswith("["):
        return json.loads(txt)
    out = []
    for ln in txt.splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            out.append(json.loads(ln))
    return out


def cmd_list(a) -> int:
    d = load()
    rows = d["scenarios"]
    if getattr(a, "id", ""):                      # 한 건만 — 판정 전에 조건·대응을 다시 볼 때
        rows = [r for r in rows if r.get("id") == a.id]
        if not rows:
            print(f"그런 id가 없다: {a.id}", file=sys.stderr)
            return 2
    # ★ **기본은 전 시장이다.** 조건은 시장에 속하지 않으므로 매 run이 열린 것 전부를
    # 판정한다. 예전에는 `--market`이 *연 시장*으로 걸러서, 미국 run이 세운 조건에 달린
    # 국내 대응이 국내 run의 목록에서 통째로 빠졌다.
    if a.affects:
        rows = [r for r in rows
                if any(str(x.get("market") or "").lower() == a.affects for x in (r.get("actions") or []))]
    if a.open:
        rows = [r for r in rows if r.get("status") == "open"]
    print(f"■ 시나리오 원장 — {len(rows)}건"
          + (" (열린 것만)" if a.open else "")
          + (f" · 대응 시장 {a.affects.upper()}" if a.affects else " · 전 시장") + "\n")
    if not rows:
        print("  없음.")
        return 0
    brief = bool(getattr(a, "brief", False))
    if brief:
        print("  (--brief: 행마다 조건·대응·마지막 판정 한 줄 — 판정 이력 전체는 `list --id <id>`)")
    for r in rows:
        flag = {"open": "☐", "closed": "☑"}.get(r.get("status"), "?")
        rz = {True: "실현", False: "미실현", None: "미판정"}[r.get("realized")]
        ac = {True: "대응함", False: "**대응 안 함**", None: "—"}[r.get("acted")]
        mks = sorted({x.get("market", "?") for x in (r.get("actions") or [])})
        js = r.get("judgments", [])
        if brief:
            # ★ 2026-09-22 실측: 열린 시나리오 목록은 판단에 쓰이지만(95건 중 31건 언급) 판정 이력 441줄은 인용 0건이었다.
            #   행은 전부 남기고 이력만 마지막 한 줄로 접는다 — 읽는 분량이 74KB→~15KB.
            acts = " · ".join(f"[{x.get('market', '?').upper()}]{x.get('ticker') or '—'} {x.get('what', '')[:40]}"
                              + ("✔" if x.get("done") else "") for x in (r.get("actions") or [])) or "대응 없음"
            lastj = f" · 마지막 판정 {js[-1].get('when', '?')[:10]} {js[-1].get('note', '')[:50]}" if js else ""
            print(f"  {flag} {r['id']} {rz}·{ac}"
                  + (f" 축 {r['axis']}" if r.get("axis") else "")
                  + f" | {str(r.get('condition'))[:90]} | {acts}{lastj}")
            continue
        print(f"  {flag} {r['id']}  연 곳 {r.get('opened_by', r.get('market', '?'))}"
              f" → 대응 [{' · '.join(m.upper() for m in mks) or '없음'}]  {rz} · {ac}"
              + (f"  축 {r['axis']}" if r.get("axis") else ""))
        print(f"     조건: {str(r.get('condition'))[:110]}")
        for x in (r.get("actions") or []):
            mark = "✔" if x.get("done") else "☐"
            print(f"     {mark} [{x.get('market', '?').upper()}] "
                  f"{(x.get('ticker') or '—'):8} {x.get('what', '')[:80]}")
        for j in js:
            print(f"     · {j.get('when','?')} {j.get('note','')[:90]}")
    print("\n  **열린 시나리오를 판정하지 않고 넘기면 그건 판단이 아니라 연기다.**")
    return 0


def cmd_judge(a) -> int:
    # ★ --batch: 판정을 JSONL(행마다 {"id","realized","acted"?,"note"?})로 한 번에 받는다.
    #   45건을 셸에서 하나씩 부르다 변수 명령(`J="python3 …"; $J`)이 전부 죽었다(2026-09-16 KR run).
    #   반복 호출은 파일로 — Bash 호출마다 새 셸이라 변수·함수·alias가 남지 않는다.
    if getattr(a, "batch", ""):
        rows = _read_batch(Path(a.batch))
        rc = 0
        for i, row in enumerate(rows, 1):
            if not row.get("id") or row.get("realized") not in ("y", "n", True, False):
                print(f"  [{i}] 건너뜀 — id·realized(y/n) 필요: {row}", file=sys.stderr)
                rc = 2
                continue
            sub = types.SimpleNamespace(
                id=row["id"], realized={True: "y", False: "n"}.get(row["realized"], row["realized"]),
                acted={True: "y", False: "n"}.get(row.get("acted"), row.get("acted")),
                note=row.get("note") or "", batch="")
            rc = max(rc, cmd_judge(sub))
        print(f"batch 판정 {len(rows)}건 ← {a.batch}")
        return rc
    d = load()
    hit = [r for r in d["scenarios"] if r.get("id") == a.id]
    if not hit:
        print(f"그런 id가 없다: {a.id}", file=sys.stderr)
        return 2
    r = hit[0]
    if r.get("status") == "merged":
        print(f"{a.id}는 {r.get('merged_into')}에 병합됐다 — 그 id로 판정하라", file=sys.stderr)
        return 2
    yes = {"y": True, "n": False}
    r["realized"] = yes[a.realized]
    if a.acted:
        r["acted"] = yes[a.acted]
    # 실현되지 않은 조건은 계속 기다릴 수 있다 — 닫는 것은 실현된 것뿐이다.
    r["status"] = "closed" if r["realized"] else "open"
    r.setdefault("judgments", []).append({
        "when": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "realized": r["realized"], "acted": r.get("acted"), "note": a.note or ""})
    save(d)
    if r["realized"] and r.get("acted") is False:
        print(f"★ {a.id}: **실현됐는데 대응하지 않았다** — 이것이 회피 지표에 잡힌다.")
    else:
        print(f"{a.id}: 실현={r['realized']} · 대응={r.get('acted')} 로 기록했다.")
    return 0


def cmd_audit(a) -> int:
    import io as _io
    d = load()
    since = (datetime.now(KST) - timedelta(days=a.days)).strftime("%y%m%d")
    rows = [r for r in d["scenarios"] if str(r.get("opened", "")) >= since
            and r.get("status") != "merged"]         # 병합된 행은 목적지가 대신 센다 — 이중 계수 방지
    if a.affects:
        rows = [r for r in rows
                if any(str(x.get("market") or "").lower() == a.affects for x in (r.get("actions") or []))]
    realized = [r for r in rows if r.get("realized") is True]
    acted = [r for r in realized if r.get("acted") is True]
    # **열려 있으면서 미판정**인 것만 센다 — 닫힌 건은 이미 판정된 것이다.
    unjudged = [r for r in rows if r.get("realized") is None and r.get("status") == "open"]
    buf = _io.StringIO()
    scope = f" · 대응 시장 {a.affects.upper()}" if a.affects else " · 전 시장"
    print(f"■ 회피 감사 — 최근 {a.days}일 승격분 {len(rows)}건{scope}\n", file=buf)
    # 게이트가 읽는 기계 판독 줄. 문구를 바꾸면 게이트 기준도 같이 바꿔야 한다.
    print(f"[회피] 승격 {len(rows)} · 실현 {len(realized)} · 대응 {len(acted)} "
          f"· 미판정 {len(unjudged)}\n", file=buf)
    print(f"  실현된 조건        {len(realized)}건", file=buf)
    print(f"  그중 대응한 것     {len(acted)}건"
          + (f"  ({100 * len(acted) / len(realized):.0f}%)" if realized else ""), file=buf)
    print(f"  **미판정으로 남은 것 {len(unjudged)}건**"
          + ("  ← 판정하지 않으면 회피가 안 보인다" if unjudged else ""), file=buf)
    if unjudged:
        print("\n  판정하지 않은 열린 조건:", file=buf)
        for r in unjudged:
            print(f"   ☐ {r['id']} {str(r.get('condition'))[:90]}", file=buf)
    if realized and len(acted) < len(realized):
        print("\n  실현됐는데 대응하지 않은 조건:", file=buf)
        for r in realized:
            if r.get("acted") is not True:
                print(f"   · {r['id']} {str(r.get('condition'))[:90]}", file=buf)
        print("\n  **연기가 규칙을 이긴 것이다.** 다음 run은 이 조건들부터 판정한다.", file=buf)
    out = buf.getvalue()
    print(out)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(out, encoding="utf-8")
        print(f"→ {a.out}")
    return 0


def cmd_due(a) -> int:
    """**조건이 실현됐는데 그 시장 대응이 미집행**인 것을 낸다.

    ★ 이것이 "미국 조건 → 국내 대응"의 기계 경로다. 실현 판정과 대응 집행은 다른
    run에서 일어나므로(밤에 실현되고 아침에 집행된다), 그 사이를 잇는 목록이 없으면
    **실현은 기록됐는데 대응은 아무도 안 하는** 상태가 조용히 남는다.
    """
    d = load()
    out = []
    for r in d["scenarios"]:
        if r.get("realized") is not True or r.get("status") == "merged":
            continue
        for x in (r.get("actions") or []):
            if x.get("done"):
                continue
            # 시장 코드는 대소문자가 섞여 저장된다(구 "kr" · 신 "KR") — 소문자로 맞춰 비교한다.
            # 2026-09-21: "US"로 저장된 CEG x2 대응이 `due --market us`에서 빠져 보였다.
            if a.market and str(x.get("market") or "").lower() not in (a.market, "?"):
                continue
            out.append((r, x))
    print(f"■ 집행 대기 — 실현됐는데 대응이 안 끝난 것 {len(out)}건"
          + (f" · {a.market.upper()}" if a.market else " · 전 시장") + "\n")
    if not out:
        print("  없음. (실현된 조건의 대응이 전부 끝났다)")
        return 0
    for r, x in out:
        print(f"  ☐ {r['id']}  [{x.get('market', '?').upper()}] "
              f"{(x.get('ticker') or '—'):8} {x.get('what', '')[:86]}")
        print(f"     조건: {str(r.get('condition'))[:96]}")
    print("\n  **이 목록이 0이 아니면 오늘 run은 여기서부터 시작한다.** "
          "조건이 실현됐는데 대응하지 않는 것이 이 시스템의 실패 양상이다.")
    # 대기가 있으면 비-0으로 — 게이트가 종료코드로 받는다.
    return 3


def cmd_act(a) -> int:
    """대응 하나를 집행 완료로 표시한다."""
    d = load()
    hit = [r for r in d["scenarios"] if r.get("id") == a.id]
    if not hit:
        print(f"그런 id가 없다: {a.id}", file=sys.stderr)
        return 2
    if hit[0].get("status") == "merged":
        print(f"{a.id}는 {hit[0].get('merged_into')}에 병합됐다 — 그 id로 표시하라", file=sys.stderr)
        return 2
    acts = hit[0].get("actions") or []
    idx = a.n - 1
    if not (0 <= idx < len(acts)):
        print(f"대응 번호가 범위 밖이다 (1~{len(acts)})", file=sys.stderr)
        return 2
    acts[idx]["done"] = True
    acts[idx]["done_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
    acts[idx]["done_note"] = a.note or ""
    if all(x.get("done") for x in acts):
        hit[0]["acted"] = True
    save(d)
    print(f"{a.id} 대응 {a.n} 집행 표시: [{acts[idx].get('market','?').upper()}] "
          f"{acts[idx].get('what','')[:70]}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("promote", help="시그널의 scenarios를 영속 id로 승격")
    p.add_argument("--signal", required=True)
    sub.add_parser("backfill", help="signals/의 기존 시그널 전부 이관")
    l = sub.add_parser("list", help="원장 조회 — 기본 전 시장")
    l.add_argument("--affects", choices=["kr", "us"],
                   help="그 시장에 **대응이 걸린** 것만 (연 시장이 아니다)")
    l.add_argument("--open", action="store_true")
    l.add_argument("--id", default="", help="한 건만")
    l.add_argument("--brief", action="store_true", help="행마다 한 줄(조건·대응·마지막 판정) — 이력은 --id로")
    j = sub.add_parser("judge", help="실현·대응 판정 (--batch <jsonl>로 여러 건)")
    j.add_argument("--id", default="")
    j.add_argument("--realized", choices=["y", "n"], default="")
    j.add_argument("--batch", default="", help="JSONL: 행마다 {\"id\",\"realized\":\"y|n\",\"acted\"?,\"note\"?}")
    j.add_argument("--acted", choices=["y", "n"])
    j.add_argument("--note", default="")
    du = sub.add_parser("due", help="실현됐는데 대응이 안 끝난 것")
    du.add_argument("--market", choices=["kr", "us"], default="")
    ac = sub.add_parser("act", help="대응 하나를 집행 완료로 표시")
    ac.add_argument("--id", required=True)
    ac.add_argument("--n", type=int, required=True, help="대응 번호(1부터)")
    ac.add_argument("--note", default="")
    mg = sub.add_parser("merge", help="같은 조건이 두 id를 받았을 때 --from을 --into에 병합")
    mg.add_argument("--from", dest="src", required=True)
    mg.add_argument("--into", required=True)
    au = sub.add_parser("audit", help="회피 감사 지표")
    au.add_argument("--days", type=int, default=14)
    au.add_argument("--affects", choices=["kr", "us"],
                    help="그 시장에 대응이 걸린 것만")
    au.add_argument("--out", default="", help="게이트가 읽는 파일로 저장")
    a = ap.parse_args()
    if a.cmd == "judge" and not a.batch and not (a.id and a.realized):
        ap.error("judge에는 --id와 --realized, 또는 --batch <jsonl>이 필요하다")
    return {"promote": cmd_promote, "backfill": cmd_backfill, "list": cmd_list,
            "judge": cmd_judge, "audit": cmd_audit,
            "due": cmd_due, "act": cmd_act, "merge": cmd_merge}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
