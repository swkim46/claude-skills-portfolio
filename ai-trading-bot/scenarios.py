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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import db

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
# factor = 요인 행(FB-261017-7) — '<요인>가 꺾이면' 행의 요인 id. 요인 행은 가격 조건(1단 `## 요인 상태`)으로 매 run 판정하고
# 실현(꺾임)돼도 닫지 않는다 — 대응은 미리 정한 방향이 아니라 그때의 `[요인 판정]`이다.
FIELDS = ("condition", "expected", "response", "invalidation", "factor")


def load() -> dict:
    """원장(저장소 jd_scenario)을 옛 scenarios.json 모양 {scenarios: [...]}로 읽는다. `save()`와 짝.
    ★ 저장소가 깨졌으면 여기서 멈춘다 — 빈 원장으로 이어 가면 판정 이력이 없는 것처럼 보인다."""
    return {"scenarios": db.scenarios_load()}


def save(d: dict) -> None:
    """옛 통째 쓰기 자리 — 바뀐 행만 쓴다. 읽은 뒤 다른 프로세스(배경 판정)가 먼저 고친 행을 덮으려 하면 StaleWrite.
    여러 행을 같이 고치는 명령(promote·merge)은 `with db.tx():` 안에서 load→save 해 끼어들 틈을 없앤다."""
    db.scenarios_save(d["scenarios"])


def _rows() -> list:
    """읽기만 하는 자리(목록·감사·집행 대기)."""
    return db.scenarios_all()


class _Stop(Exception):
    """한 행 갱신을 쓰지 않고 멈춘다(그런 id 없음·병합된 행·범위 밖) — 트랜잭션은 되돌린다."""

    def __init__(self, msg: str, rc: int = 2):
        super().__init__(msg)
        self.msg, self.rc = msg, rc


def _update(sid: str, fn) -> dict:
    """시나리오 한 행을 한 트랜잭션 안에서 읽고-고치고-쓴다 — 배경 판정 셋이 서로 다른 행을 동시에 고쳐도 덮지 않는다."""
    try:
        return db.scenario_update(sid, fn)
    except KeyError:
        if db.scenario_get(sid) is None:
            raise _Stop(f"그런 id가 없다: {sid}") from None
        raise


def sig_meta(p: Path) -> tuple:
    """(stamp, market) — 파일명 `signal_<YYMMDD>_<mkt>*.json`에서."""
    m = re.match(r"signal_(\d{6})_(kr|us)", p.name)
    return (m.group(1), m.group(2)) if m else ("", "")


def norm_text(s: str) -> str:
    """조건·대응 문자열 정규화(2026-10-19 · Codex S04) — 공백·가운뎃점·백틱·별표·줄표(—)만 지운다. 부호(-·+·±)·소수점·쉼표·%·
    비교 기호·날짜 하이픈은 남긴다(예전 norm은 '-5%'와 '5%', '5.0%'와 '50%'를 같은 조건으로 봤다). 유니코드 마이너스·en 대시는 '-'로,
    ≥·≤는 >=·<=로 맞춘다(표기만 다른 같은 조건이 두 id를 받지 않게)."""
    import unicodedata
    t = unicodedata.normalize("NFKC", str(s or ""))
    t = t.replace("\u2212", "-").replace("\u2013", "-").replace("≥", ">=").replace("≤", "<=")
    return re.sub(r"[\s·`*—]+", "", t).lower()


norm = norm_text           # 옛 이름(리뷰 재현 스크립트·외부 호출) — 같은 함수


def is_noop(action: dict) -> bool:
    """'영향 없음 — …' 대응 — 대기 목록에 안 나오고 대응 완료로 센다(v6)."""
    return bool((action or {}).get("noop")) or str((action or {}).get("what") or "").strip().startswith("영향 없음")


def _resp_set_row(row: dict) -> set:
    return {(str(x.get("market") or "").lower(), norm_text(x.get("what"))) for x in (row.get("actions") or []) if x.get("what")}


def _resp_set_signal(r: dict, market: str) -> set:
    if isinstance(r.get("responses"), dict) and r["responses"]:
        return {(str(k).lower(), norm_text(str(v)[:160])) for k, v in r["responses"].items() if str(v or "").strip()}
    acts = r.get("actions") if isinstance(r.get("actions"), list) else extract_actions(r.get("response") or r.get("action"), market)
    return {(str(x.get("market") or "").lower(), norm_text(x.get("what"))) for x in acts if x.get("what")}


# ── 구조화 조건(v6 · J02) — 시그널 시나리오의 cond[] 원자(AND). 산문은 파싱하지 않는다(없거나 틀리면 text_only + 경고 · 승격은 막지 않는다).
COND_OPS = ("<", "<=", ">", ">=", "==", "between", "in", "is", "delayed", "else")
_NUM_COND = re.compile(r"[-+±\u2212\u2013]?\d+(?:\.\d+)?\s*%|[<>≥≤]|이상|이하|미만|초과")


def parse_cond(r: dict, metrics: list = None) -> tuple:
    """(원자 목록, 상태 structured|else|delayed|text_only, 문제 목록). metrics = 그 사건의 지표 정의([{name, domain, values}])."""
    raw = r.get("cond")
    if not raw:
        return [], "text_only", []
    if not isinstance(raw, list):
        return [], "text_only", ["cond는 원자 목록이어야 한다"]
    mm = {m["name"]: m for m in (metrics or []) if isinstance(m, dict) and m.get("name")}
    atoms, probs = [], []
    for x in raw:
        if not isinstance(x, dict) or x.get("op") not in COND_OPS:
            probs.append(f"모르는 원자 {x!r}")
            continue
        op = x["op"]
        if op in ("delayed", "else"):
            atoms.append({"op": op})
            continue
        m = x.get("metric")
        if not m or (mm and m not in mm):
            probs.append(f"사건 지표에 없는 metric {m!r}")
            continue
        a = {"metric": m, "op": op, "unit": x.get("unit"), "window": x.get("window")}
        if op in ("<", "<=", ">", ">=", "==", "between"):
            try:
                a["value"] = float(x.get("value"))
                if op == "between":
                    a["value_hi"] = float(x.get("value_hi"))
            except (TypeError, ValueError):
                probs.append(f"{m} {op}: 숫자 값이 아니다")
                continue
        else:
            v = x.get("value") if x.get("value_text") is None else x.get("value_text")
            a["value_text"] = json.dumps(v, ensure_ascii=False) if isinstance(v, list) else str(v)
        atoms.append(a)
    if probs:
        return [], "text_only", probs
    ops = {a["op"] for a in atoms}
    return atoms, ("else" if ops == {"else"} else "delayed" if ops == {"delayed"} else "structured"), []


def _cond_key(atoms: list) -> str:
    def one(a):
        v = a.get("value")
        return [a.get("metric") or "", a["op"], None if v is None else round(float(v), 6),
                None if a.get("value_hi") is None else round(float(a["value_hi"]), 6), a.get("value_text"),
                a.get("unit") or "", a.get("window") or ""]
    return json.dumps(sorted((one(a) for a in atoms), key=lambda z: json.dumps(z, ensure_ascii=False)), ensure_ascii=False)


def _atom_holds(a: dict, cell: dict) -> bool:
    if a["op"] in ("else", "delayed"):
        return False
    v = cell.get(a.get("metric"))
    if v is None:
        return True                                  # 그 지표를 말하지 않는 칸(다른 지표만 있는 사건) — 제약 없음
    op = a["op"]
    if op in ("in", "is"):
        want = a.get("value_text")
        try:
            want_l = json.loads(want) if want and want.startswith("[") else [want]
        except ValueError:
            want_l = [want]
        return str(v) in [str(w) for w in want_l]
    x = float(v)
    return {"<": x < a["value"], "<=": x <= a["value"], ">": x > a["value"], ">=": x >= a["value"], "==": x == a["value"],
            "between": a["value"] <= x <= a.get("value_hi", a["value"])}[op]


def _cells(event: dict, branches: list) -> list:
    """사건 지표 공간의 기본 칸 — 숫자 지표는 가지들의 경계값으로 나눈 구간 + 경계점, 열거 지표는 값."""
    import itertools
    axes = []
    for m in event.get("metrics") or []:
        if m.get("domain") == "enum":
            axes.append([(m["name"], v, f"{m['name']}={v}") for v in (m.get("values") or [])])
            continue
        bps = sorted({float(a[k]) for b in branches for a in b["atoms"] if a.get("metric") == m["name"]
                      for k in ("value", "value_hi") if a.get(k) is not None})
        if not bps:
            continue
        cells = [(m["name"], bps[0] - 1, f"{m['name']} < {bps[0]:g}")]
        for i, x in enumerate(bps):
            cells.append((m["name"], x, f"{m['name']} = {x:g}"))
            hi = bps[i + 1] if i + 1 < len(bps) else None
            cells.append((m["name"], (x + hi) / 2 if hi is not None else x + 1,
                          f"{x:g} < {m['name']} < {hi:g}" if hi is not None else f"{m['name']} > {x:g}"))
        axes.append(cells)
    out = []
    for combo in itertools.product(*axes) if axes else [()]:
        out.append(({n: v for n, v, _ in combo}, " ∧ ".join(lbl for _, _, lbl in combo) or "(지표 없음)"))
    return out


def coverage(event: dict, branches: list) -> dict:
    """★ 가지 완결성(2026-10-19 · Codex J02) — branches = [{id, atoms, status, resp}]. 사건 지표 공간의 칸마다 가지가 하나 이상인가 ·
    예정일이 있는 사건엔 지연 가지(delayed)가 있는가 · 같은 칸을 덮는 가지들의 대응이 다른가(겹침). else 가지는 남는 칸을 다 덮는다.
    구조화 조건이 없는 가지뿐이면 판정하지 않는다(완결성 미판정)."""
    struct = [b for b in branches if b["status"] in ("structured", "else", "delayed")]
    if not struct:
        return {"judged": False, "uncovered": [], "overlaps": [], "has_delayed": False, "has_else": False}
    has_else = any(b["status"] == "else" for b in struct)
    has_delayed = any(b["status"] == "delayed" for b in struct)
    normal = [b for b in struct if b["status"] == "structured"]
    uncovered, overlaps = [], []
    for cell, label in _cells(event, normal):
        hits = [b for b in normal if all(_atom_holds(a, cell) for a in b["atoms"])]
        if not hits and not has_else:
            uncovered.append(label)
        if len({frozenset(b["resp"]) for b in hits}) > 1:
            overlaps.append(f"{label} — {', '.join(b['id'] for b in hits)}")
    if event.get("scheduled") and not has_delayed:
        uncovered.append("발표 지연(관측 마감까지 결과 없음)")
    return {"judged": True, "uncovered": uncovered, "overlaps": overlaps, "has_delayed": has_delayed, "has_else": has_else}


def match_cell(event: dict, branches: list, observation: dict) -> list:
    """관측값이 해당하는 가지 id들(구조화 가지가 없으면 else) — judge --observation이 대조한다."""
    normal = [b for b in branches if b["status"] == "structured"]
    hits = [b["id"] for b in normal if all(_atom_holds(a, observation) for a in b["atoms"])]
    if not hits:
        hits = [b["id"] for b in branches if b["status"] == "else"]
    return hits


# ------------------------------------------------- 대응 추출 (조건은 시장에 속하지 않는다)

def _universe_index() -> list:
    """`(시장, 티커, 표기)` 목록 — 유니버스의 티커·이름·별칭을 전부 편다."""
    wl = db.watchlist_doc()
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

    events = sig.get("events") or []
    # ★ 여러 행(출처·별칭 덧붙이기 + 새 행)을 같이 고친다 — 읽기부터 쓰기까지 한 트랜잭션으로 묶어 배경 판정이 끼지 못하게 한다.
    with db.tx():
        return _promote(p, stamp, market, rows, events if isinstance(events, list) else [])


def _event_map(events: list, market: str, stamp: str) -> dict:
    """시그널 events[] → {시그널 안 id: 원장 사건(id·지표)}. 자연 열쇠(subject, kind, scheduled)로 하나(v6)."""
    out = {}
    for e in events:
        if not isinstance(e, dict) or not e.get("id") or not e.get("subject") or e.get("kind") not in (
                "earnings", "macro", "policy", "corporate", "factor", "price"):
            if isinstance(e, dict) and e.get("id"):
                print(f"  ★ 사건 {e.get('id')}: subject·kind(earnings|macro|policy|corporate|factor|price)가 필요하다 — 건너뜀", file=sys.stderr)
            continue
        sch = str(e.get("scheduled") or "")[:10] or None
        eid = db.event_upsert(str(e["subject"]), e["kind"], sch, str(e.get("window_end") or "")[:10] or None,
                              e.get("source"), f"{market}:{stamp}", e.get("metrics"))
        out[str(e["id"])] = db.event_get(eid)
    return out


def _promote(p: Path, stamp: str, market: str, rows: list, events: list = None) -> int:
    d = load()
    by_id = {s.get("id"): s for s in d["scenarios"]}
    evmap = _event_map(events or [], market, stamp)
    # 조건 문구 → 원장 행 목록(같은 문구 · 다른 대응이면 둘 이상). 병합된 행은 목적지로 넘긴다. 별칭(`aliases`)도 같은 행을 가리킨다.
    have, by_tag = {}, {}
    for row_ in d["scenarios"]:
        tgt = _resolve_merged(by_id, row_)
        for cond in [row_.get("condition")] + list(row_.get("aliases") or []):
            k = norm_text(cond)
            if k and tgt is not None and tgt not in have.setdefault(k, []):
                have[k].append(tgt)
        for t in row_.get("sources") or []:
            by_tag.setdefault(t, tgt)
    seen_now = {}                                   # 이번 파일에서 새로 만든 행 — 같은 파일 안 행끼리는 병합하지 않는다(한 표 = 한 run의 서로 다른 가지)
    n_new = n_dup = n_pid = n_alias = n_dupev = n_same = 0
    used = {int(m.group(1)) for s in d["scenarios"]
            for m in [re.match(rf"SC-{stamp}-(\d+)$", s.get("id", ""))] if m}
    nxt = max(used) + 1 if used else 1
    for r in rows:
        if not isinstance(r, dict):
            continue
        key = norm_text(r.get("condition"))
        if not key:
            continue
        tag = f"{p.name}#{r.get('id')}"
        ev = evmap.get(str(r.get("event") or "")) if r.get("event") else None
        atoms, cstatus, cprobs = parse_cond(r, (ev or {}).get("metrics"))
        for pr in cprobs:
            print(f"  ★ {tag}: 조건 구조화 실패 — {pr} · text_only로 둔다(조건 문장은 그대로)", file=sys.stderr)
        if tag in by_tag and by_tag[tag] is not None:
            n_dup += 1                               # 같은 파일을 다시 승격 — 이미 실린 행(멱등)
            continue
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
                if r.get("factor") and not tgt.get("factor"):
                    # 이어받은 행을 요인 행으로(FB-261017-7) — 옛 '꺾이면' 행이 가격 조건 없이 자동 이월되지 않게
                    tgt["factor"], tgt["price_cond"] = r["factor"], True
                if atoms and not tgt.get("cond"):
                    tgt["cond"], tgt["cond_status"] = atoms, cstatus
                    if ev:
                        tgt["event_id"] = ev["id"]
                elif atoms and _cond_key(atoms) != _cond_key(tgt.get("cond") or []):
                    print(f"  ★ {pid}: 시그널 조건 구조가 원장과 다르다 — 바꾸지 않는다(`scenarios.py set-cond --id {pid}`로 고친다)",
                          file=sys.stderr)
                if tgt not in have.get(key, []):
                    if key != norm_text(tgt.get("condition")) and r.get("condition") not in (tgt.get("aliases") or []):
                        tgt.setdefault("aliases", []).append(r.get("condition"))
                        n_alias += 1
                    have.setdefault(key, []).append(tgt)
                by_tag[tag] = tgt
                n_pid += 1
                continue
            claimed_missing = pid
            print(f"  ★ persistent_id {pid}가 원장에 없다 — 새 id로 승격하고 출처에 남긴다 ({tag})",
                  file=sys.stderr)
        # ★ 병합 규칙(2026-10-19 · Codex S04) — 조건이 같다는 것만으로 합치지 않는다. 구조화 행은 같은 사건 + 같은 조건 원자,
        #   글 행은 부호·소수점을 남긴 같은 문장 — 그리고 **대응이 새로 더하는 것이 없을 때**만 출처를 덧붙인다. 조건은 같은데 대응이
        #   다르면 새 행 + possible_duplicate_of(이어받을 것이면 persistent_id + respond --replace).
        rset = _resp_set_signal(r, market)
        if atoms and cstatus == "structured" and ev:
            cands = [x for x in d["scenarios"] if x.get("event_id") == ev["id"] and x.get("status") != "merged"
                     and _cond_key(x.get("cond") or []) == _cond_key(atoms)]
        else:
            cands = list(have.get(key, []))
        same_file = [x for x in seen_now.get(key, [])]
        merge = next((x for x in cands if rset <= _resp_set_row(x)), None)
        if merge is not None:
            src = merge.setdefault("sources", [])
            if tag not in src:
                src.append(tag)
            if r.get("factor") and not merge.get("factor"):
                merge["factor"], merge["price_cond"] = r["factor"], True
            by_tag[tag] = merge
            n_dup += 1
            continue
        sid = f"SC-{stamp}-{nxt}"
        nxt += 1
        row = {"id": sid, "opened_by": f"{market}:{stamp}", "market": market,
               "opened": stamp, "local_id": r.get("id"), "status": "open",
               "realized": None, "acted": None,
               "axis": r.get("axis_id") or r.get("axis") or "",
               "sources": [f"{p.name}#{r.get('id')}"],
               "judgments": []}
        row.update({k: r.get(k) for k in FIELDS if r.get(k)})
        if ev:
            row["event_id"] = ev["id"]
        row["cond_status"] = cstatus
        if atoms:
            row["cond"] = atoms
        if r.get("expected_reaction"):
            row["expected_reaction"] = str(r["expected_reaction"])[:300]
        # 시그널이 적은 다음 판정일·가격 조건도 원장으로 — 빠지면 지연 가지의 예정일이 사라져 매 run 판정 대상이 된다
        nc = str(r.get("next_check") or "").strip()[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", nc):
            row["next_check"] = nc
        elif nc:
            print(f"  ★ {sid}: next_check {r.get('next_check')!r}는 YYYY-MM-DD가 아니다 — 비워 둔다(매 run 판정 대상)",
                  file=sys.stderr)
        if cstatus == "delayed" and ev and ev.get("window_end") and row.get("next_check") != ev["window_end"]:
            print(f"  ★ {sid}: 지연 가지의 next_check는 사건의 관측 마감({ev['window_end']})이어야 한다", file=sys.stderr)
        if r.get("price_cond") is not None:
            row["price_cond"] = r.get("price_cond") in (True, "y", "yes", "true", "1", 1)
        if row.get("factor") and not row.get("price_cond"):
            if r.get("price_cond") is None:
                print(f"  ★ {sid}: 요인 행(factor {row['factor']})인데 price_cond가 없다 — 요인 행은 가격 조건이다(y로 둔다)",
                      file=sys.stderr)
            row["price_cond"] = True
        if claimed_missing:
            row["claimed_persistent_id"] = claimed_missing
            row["sources"].append(f"persistent_id:{claimed_missing}(원장에 없음)")
        # 대응은 시그널이 실어 보냈으면 그대로, 아니면 response 문장에서 뽑는다.
        # ★ 국내 시그널은 대응을 `action`(단수)에 싣기도 한다 — 2026-09-29 KR S7·S8이 그 키라
        #   actions가 빈 채로 승격됐다(대응 없는 행 = 판정해도 집행할 것이 안 보인다).
        if isinstance(r.get("responses"), dict) and r["responses"]:
            # ★ 이벤트 단위(2026-10-12) — 한 행에 두 시장 대응. 키가 시장이다.
            row["actions"] = [{"market": str(k).lower(), "ticker": "", "what": str(v)[:160], "done": False}
                              for k, v in r["responses"].items() if str(v or "").strip()]
        else:
            row["actions"] = (r.get("actions") if isinstance(r.get("actions"), list)
                              else extract_actions(r.get("response") or r.get("action"), market))
        for x in row["actions"]:
            if is_noop(x):
                x["noop"] = True                     # '영향 없음' — 대기가 아니다(v6)
        dup = _same_event_rows(d["scenarios"], row, market)
        dup += [x["id"] for x in cands + same_file if x.get("id") not in dup]
        if dup:
            row["possible_duplicate_of"] = dup
            n_dupev += 1
            why = ("같은 조건·다른 대응" if (cands or same_file) else "같은 이벤트로 보이는 반대 시장 행")
            print(f"  ★ {sid}: {why} {', '.join(dup)} — 같은 가지면 다음엔 `persistent_id`로 이어받고 "
                  f"`scenarios.py respond`(--replace)로 대응만 바꿔라(같은 가지를 두 행으로 쓰지 않는다)", file=sys.stderr)
            if same_file:
                n_same += 1
        d["scenarios"].append(row)
        seen_now.setdefault(key, []).append(row)
        by_id[sid] = row
        by_tag[tag] = row
        n_new += 1
    save(d)
    print(f"{p.name}: 신규 {n_new}건 승격 · 기존 조건 재등장 {n_dup}건(출처만 추가) "
          f"· persistent_id 매칭 {n_pid}건(별칭 추가 {n_alias}) · 중복 의심 {n_dupev}건(같은 파일 {n_same}) · 사건 {len(evmap)} "
          f"· 원장 총 {len(d['scenarios'])}건")
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
    with db.tx():                       # 두 행을 같이 고친다 — 한 트랜잭션(반쪽 병합이 남지 않는다)
        return _merge(a)


def _merge(a) -> int:
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
        if cond and norm_text(cond) != norm_text(dst.get("condition")) and cond not in (dst.get("aliases") or []):
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
    print(f"\n원장 총 {len(_rows())}건 → 저장소 jd_scenario")
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


def _axis_driver(axis_id: str) -> str:
    if not axis_id:
        return ""
    a = db.axis_get(axis_id) or {}
    return str(a.get("driver") or axis_id or "")


def _same_event_rows(rows: list, row: dict, market: str) -> list:
    """같은 이벤트로 보이는 **반대 시장의 열린 행** — 조건의 날짜가 같고, 둘 다 축이 있으면 요인도 같은 것(2026-10-12).
    실사례: 10-14 CPI를 미국 run은 '핵심 CPI 0.2%'로, 국내 run은 '10년물 5.0%/5.2%'로 각자 두 행씩 따로 열었다."""
    ev = _cond_event_date(row)
    if ev is None:
        return []
    d0 = _axis_driver(row.get("axis") or "") if row.get("axis") else ""
    out = []
    for r in rows:
        if r.get("status") != "open" or str(r.get("market") or "").lower() == market.lower():
            continue
        if _cond_event_date(r) != ev:
            continue
        if d0 and r.get("axis"):
            if _axis_driver(r["axis"]) != d0:
                continue
        else:
            a_terms = {t.lower() for t in _EVENT_TERMS.findall(str(row.get("condition") or ""))}
            b_terms = {t.lower() for t in _EVENT_TERMS.findall(str(r.get("condition") or ""))}
            if not (a_terms & b_terms):
                continue
        out.append(r["id"])
    return out


def cmd_respond(a) -> int:
    """반대 시장이 연 시나리오 행에 **이 시장 대응**을 더한다 — 같은 이벤트를 새 행으로 다시 쓰지 않는다(2026-10-12)."""
    mk = a.market.lower()
    import sessions as _ss
    added_by = f"{mk}:{_ss.run_stamp(mk)}"                    # 그 시장의 현재 run 스탬프(현지 거래일)

    def fn(r):
        if r.get("status") == "merged":
            raise _Stop(f"{a.id}는 {r.get('merged_into')}에 병합됐다 — 그 id로")
        acts = r.setdefault("actions", [])
        mine = [x for x in acts if str(x.get("market") or "").lower() == mk]
        if mine and not a.replace:
            raise _Stop(f"{a.id}에 이미 {mk.upper()} 대응이 있다: {mine[0].get('what')} — 바꾸려면 --replace")
        if a.replace:
            r["actions"] = acts = [x for x in acts if str(x.get("market") or "").lower() != mk]
        acts.append({"market": mk, "ticker": a.ticker or "", "what": a.what[:160], "done": False,
                     "added_by": added_by})
        r.setdefault("judgments", []).append({"when": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "realized": r.get("realized"),
                                              "acted": r.get("acted"), "note": f"{mk.upper()} 대응 추가 — {a.what[:80]}"
                                              + (f" · {a.note}" if a.note else "")})
        return r
    try:
        _update(a.id, fn)
    except _Stop as e:
        print(e.msg, file=sys.stderr)
        return e.rc
    print(f"{a.id}: {mk.upper()} 대응 추가 — {a.what[:80]}")
    return 0


STALE_TRADING_DAYS = 20     # 이 이상 열린 채 실현도 반증도 안 된 시나리오 = '지연·무소식' 가지가 없었다는 신호(목록만 · 자동으로 닫지 않는다)
EVENT_GRACE_DAYS = 10       # 조건에 적힌 예정일 + 이 거래일이 지나도 미실현이면 같은 신호(지연 가지의 기본 예정일과 같은 값)
# 앞이 글자·숫자·하이픈이면 날짜가 아니다 — 리서치 꼬리표(`R3-14`)·id(`SEC-EE-1`)를 날짜로 읽지 않는다.
_COND_DATE = re.compile(r"(?<![\w-])(1[0-2]|0?[1-9])[/-](3[01]|[12]\d|0?[1-9])(?![\d-])")
_EVENT_TERMS = re.compile(r"CPI|PCE|FOMC|PPI|고용|비농업|payroll|실적|[1-4]Q|MOU|관세|tariff|금리|10년물|10-year|국채|OAS|정기변경|"
                          r"rebalanc|만기|선거|midterm|STEO|TrendForce|가이던스|guid|계약가|OPEC|휴전|ceasefire", re.I)


def _cond_event_date(r: dict):
    """조건 문구에 적힌 **가장 늦은** 날짜(M/D · MM-DD). 연도는 연 날짜(opened)의 해 — 연 달보다 앞이면 다음 해."""
    try:
        op = datetime.strptime(str(r.get("opened") or ""), "%y%m%d").date()
    except ValueError:
        return None
    best = None
    for mo, dd in _COND_DATE.findall(str(r.get("condition") or "")):
        y = op.year + (1 if int(mo) < op.month - 6 else 0)
        try:
            d = date(y, int(mo), int(dd))
        except ValueError:
            continue
        best = d if best is None or d > best else best
    return best


def _weekdays_between(a: date, b: date) -> int:
    n, d = 0, a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return n


def stale(days: int = STALE_TRADING_DAYS, today: date = None) -> list:
    """열린 지 `days` 거래일(평일)을 넘겼는데 실현되지 않은 열린 시나리오(FB-261012-1).
    실사례(2026-10-12): 9-15에 연 MOU 두 갈래(좋은 내용·나쁜 내용)가 4주째 열려 있었다 — 실제로 온 것은 둘 다 아닌
    '지연'이었고 그 행이 없어 닫을 수가 없었다. 이 목록이 다음 run에 '지연 가지를 세우거나 닫으라'고 붙는다."""
    today = today or datetime.now(KST).date()
    out = []
    for r in _rows():
        if r.get("status") != "open" or r.get("realized") is True:
            continue
        try:
            op = datetime.strptime(str(r.get("opened") or ""), "%y%m%d").date()
        except ValueError:
            continue
        n = _weekdays_between(op, today)
        ev = _cond_event_date(r)
        past = _weekdays_between(ev, today) if ev and ev < today else 0
        if n >= days or past >= EVENT_GRACE_DAYS:
            why = (f"열린 지 {n}거래일" + (f" · 예정일 {ev:%m-%d}에서 {past}거래일 지남" if past >= EVENT_GRACE_DAYS else ""))
            out.append({**r, "_age_td": n, "_why": why})
    return sorted(out, key=lambda r: -r["_age_td"])


def cmd_list(a) -> int:
    rows = _rows()
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
    if getattr(a, "stale", False):
        keep = {r["id"]: r["_why"] for r in stale(getattr(a, "days", None) or STALE_TRADING_DAYS)}
        rows = [r for r in rows if r.get("id") in keep]
        print(f"■ 오래 열린 시나리오 — {len(rows)}건 (열린 지 {getattr(a, 'days', None) or STALE_TRADING_DAYS}거래일 이상, "
              f"또는 조건의 예정일에서 {EVENT_GRACE_DAYS}거래일 지남 · 미실현) — "
              f"지연·무소식 가지를 세우거나(새 행 · next_check에 예정일) 닫는다\n")
        for r in rows:
            print(f"  {r['id']} [{r.get('market')}] {keep[r['id']]} · {str(r.get('condition') or '')[:100]}")
        return 0
    nc = getattr(a, "needs_check", "")
    if nc:
        # ★ 2026-10-01: 열린 시나리오 134건을 매 run 전건 다시 판정했다(대부분 '판정일 미도래'). 다음 확인일이
        # 지나지 않았고 가격 조건도 없는 것은 판정이 바뀔 수 없으니 판정 대상에서 뺀다 — `judge --carry-forward`가 이월.
        rows = [r for r in rows if r.get("status") == "open" and _needs_check(r, nc)]
    print(f"■ 시나리오 원장 — {len(rows)}건"
          + (f" (판정 필요 — next_check ≤ {nc} · 날짜 없음 · 가격 조건)" if nc else "")
          + (" (열린 것만)" if a.open else "")
          + (f" · 대응 시장 {a.affects.upper()}" if a.affects else " · 전 시장") + "\n")
    if not rows:
        print("  없음.")
        return 0
    brief = bool(getattr(a, "brief", False))
    if brief:
        print("  (--brief: 행마다 조건·대응·마지막 판정 한 줄 — 판정 이력 전체는 `list --id <id>`)")
    for r in rows:
        flag = {"open": "☐", "closed": "☑", "expired": "⌛"}.get(r.get("status"), "?")
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
                  + (f" 요인 {r['factor']} ·" if r.get("factor") else "")
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


def _needs_check(r, day: str) -> bool:
    """판정이 필요한가: 다음 확인일이 없거나 지났거나, 가격 조건이 걸려 있거나 요인 행이면 True(보수적)."""
    if r.get("price_cond") or r.get("factor"):
        return True
    nx = str(r.get("next_check") or "")
    return (not re.fullmatch(r"\d{4}-\d{2}-\d{2}", nx)) or nx <= day


def cmd_carry_forward(day: str) -> int:
    n = 0

    def fn(r):
        nonlocal n
        if r.get("status") != "open" or _needs_check(r, day):
            raise _Stop("")                      # 그 사이 배경 판정이 고쳐 이월 대상이 아니게 됐다 — 쓰지 않는다
        r.setdefault("judgments", []).append({
            "when": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "realized": False, "acted": r.get("acted"),
            "note": f"미도래 — next_check {r.get('next_check')} (자동 이월 · 가격 조건 없음)", "auto": True})
        if r.get("realized") is None:
            r["realized"] = False
        n += 1
        return r
    # 행마다 따로 쓴다 — 이월하는 동안 배경 판정이 다른 행에 남긴 판정을 덮지 않는다.
    for r in _rows():
        if r.get("status") != "open" or _needs_check(r, day):
            continue
        try:
            _update(r["id"], fn)
        except _Stop:
            continue
    print(f"자동 이월 {n}건 — next_check가 {day} 뒤이고 가격 조건이 없는 열린 시나리오")
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
                note=row.get("note") or "", batch="", carry_forward="",
                next_check=row.get("next_check") or "", price_cond=row.get("price_cond"),
                expire=bool(row.get("expire")), observation=row.get("observation"), price_reaction=row.get("price_reaction"))
            rc = max(rc, cmd_judge(sub))
        print(f"batch 판정 {len(rows)}건 ← {a.batch}")
        return rc
    if getattr(a, "carry_forward", ""):
        return cmd_carry_forward(a.carry_forward)
    try:
        r = _update(a.id, lambda r: _judge_row(r, a))
    except _Stop as e:
        print(e.msg, file=sys.stderr)
        return e.rc
    if r["realized"] and r.get("acted") is False:
        print(f"★ {a.id}: **실현됐는데 대응하지 않았다** — 이것이 회피 지표에 잡힌다.")
    else:
        print(f"{a.id}: 실현={r['realized']} · 대응={r.get('acted')} 로 기록했다.")
    return 0


def _judge_row(r: dict, a) -> dict:
    """판정 한 건을 행에 적는다 — `_update`가 한 트랜잭션 안에서 부른다(배경 판정끼리 덮지 않는다)."""
    if r.get("status") == "merged":
        raise _Stop(f"{a.id}는 {r.get('merged_into')}에 병합됐다 — 그 id로 판정하라")
    yes = {"y": True, "n": False}
    r["realized"] = yes[a.realized]
    if a.acted:
        r["acted"] = yes[a.acted]
    # 실현되지 않은 조건은 계속 기다릴 수 있다 — 닫는 것은 실현된 것뿐이다.
    # ★ 단 **예정일이 지나 다시 올 수 없는 가지**는 `--expire`로 닫는다(status `expired`, 실현 아님). 예전엔 미실현 행을
    #   닫을 길이 없어 지나간 이벤트의 가지가 열린 채 쌓였다 — 10-12에 48건(FB-261012-1).
    expire = bool(getattr(a, "expire", False))
    if expire and r["realized"]:
        print(f"  ※ {a.id}: 실현된 행은 --expire가 아니라 그대로 닫힌다", file=sys.stderr)
        expire = False
    r["status"] = "closed" if r["realized"] else ("expired" if expire else "open")
    if r.get("factor") and r["status"] == "closed":
        # 요인 행(FB-261017-7) — 꺾임이 실현돼도 닫지 않는다: 다음 run도 요인 상태를 보고 다시 판정한다(그 요인 보유가 끝나 행을
        # 접을 때는 --expire)
        print(f"  ※ {a.id}: 요인 행(factor {r['factor']})은 실현(꺾임)돼도 닫지 않는다 — 다음 run도 판정한다", file=sys.stderr)
        r["status"] = "open"
    nx = str(getattr(a, "next_check", "") or "")
    if nx and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", nx):
        print(f"  ※ {a.id}: next_check 형식이 YYYY-MM-DD가 아니다({nx!r}) — 기록하지 않고 매 run 판정 대상으로 둔다",
              file=sys.stderr)
        nx = ""
    if r["realized"]:
        r.pop("next_check", None)
    elif nx:
        r["next_check"] = nx
    pc = getattr(a, "price_cond", None)
    if pc is not None and pc != "":
        r["price_cond"] = pc in (True, "y", "yes", "true", "1")
    # ★ 발생(2026-10-19 · Codex S05) — 실현(y)인데 열린 발생이 없으면 새 발생(n+1). 첫 발생은 그 전에 표시한 대응 완료를 이어받고,
    #   다시 온 발생(n ≥ 2)은 대응을 새로 기다린다(예전엔 지난번 '완료'가 남아 재발한 조건의 대응이 대기에 안 떴다). 미실현(n)은 발생을 닫는다.
    #   같은 발생이 계속되는 동안(y·y·y)은 같은 발생 — 대응을 두 번 요구하지 않는다.
    run_ = _run_label()
    obs = getattr(a, "observation", None)
    if isinstance(obs, str) and obs.strip():
        try:
            obs = json.loads(obs)
        except ValueError:
            print(f"  ※ {a.id}: --observation은 JSON이어야 한다 — 문장으로 남긴다", file=sys.stderr)
    if r["realized"]:
        ep = db.episode_current(r["id"])
        if ep is None:
            ep = db.episode_open(r["id"], run=run_, observation=obs, price_reaction=getattr(a, "price_reaction", None),
                                 note=a.note or None)
            if ep["n"] == 1:
                for x in r.get("actions") or []:
                    if x.get("done"):
                        db.response_done(ep["id"], x, x.get("done_note") or "발생 전 표시", run_)
            else:
                for x in r.get("actions") or []:
                    x["done"] = False
                    x.pop("done_at", None)
                    x.pop("done_note", None)
                if not a.acted:
                    r["acted"] = None
                print(f"  ※ {a.id}: 다시 일어난 조건 — 발생 {ep['n']} · 대응을 새로 기다린다(지난 발생의 완료는 그 발생의 기록)", file=sys.stderr)
        if a.acted == "y":
            for x in r.get("actions") or []:
                x["done"] = True
                x.setdefault("done_at", datetime.now(KST).strftime("%Y-%m-%d %H:%M"))
                db.response_done(ep["id"], x, a.note or "judge --acted y", run_)
        if isinstance(obs, dict) and r.get("event_id"):
            ev = db.event_get(r["event_id"])
            branches = _event_branches(r["event_id"])
            hits = match_cell(ev or {}, branches, obs) if ev else []
            if hits and r["id"] not in hits:
                print(f"  ★ {a.id}: 관측값은 {', '.join(hits)}에 해당하는데 {a.id}를 실현으로 판정했다 — 가지를 확인하라", file=sys.stderr)
    else:
        db.episode_clear(r["id"], run_)
    r.setdefault("judgments", []).append({
        "when": datetime.now(KST).strftime("%Y-%m-%d %H:%M"),
        "realized": r["realized"], "acted": r.get("acted"), "note": a.note or ""})
    return r


def _run_label():
    try:
        cur = db.current_run_read() or {}
        return db.run_id(cur["stamp"], cur["market"]) if cur.get("stamp") and cur.get("market") else None
    except Exception:                                # noqa: BLE001
        return None


def _event_branches(event_id: str) -> list:
    """원장에서 그 사건의 가지들 [{id, atoms, status, resp}](병합·닫힌 행 제외 안 함 — 가지 정의는 상태와 무관)."""
    out = []
    for x in _rows():
        if x.get("event_id") != event_id or x.get("status") == "merged":
            continue
        atoms = x.get("cond") or []
        st = x.get("cond_status") or ("structured" if atoms else "text_only")
        out.append({"id": x["id"], "atoms": atoms, "status": st, "resp": _resp_set_row(x)})
    return out


def cmd_audit(a) -> int:
    import io as _io
    since = (datetime.now(KST) - timedelta(days=a.days)).strftime("%y%m%d")
    rows = [r for r in _rows() if str(r.get("opened", "")) >= since
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
    out = []
    for r in _rows():
        if r.get("realized") is not True or r.get("status") == "merged":
            continue
        ep = db.episode_current(r["id"])
        for x in (r.get("actions") or []):
            if x.get("done") or is_noop(x):          # 끝난 것 · '영향 없음' 대응은 대기가 아니다(v6)
                continue
            # 시장 코드는 대소문자가 섞여 저장된다(구 "kr" · 신 "KR") — 소문자로 맞춰 비교한다.
            # 2026-09-21: "US"로 저장된 CEG x2 대응이 `due --market us`에서 빠져 보였다.
            if a.market and str(x.get("market") or "").lower() not in (a.market, "?"):
                continue
            out.append((r, x, ep))
    print(f"■ 집행 대기 — 실현됐는데 대응이 안 끝난 것 {len(out)}건"
          + (f" · {a.market.upper()}" if a.market else " · 전 시장") + "\n")
    if not out:
        print("  없음. (실현된 조건의 대응이 전부 끝났다)")
        return 0
    for r, x, ep in out:
        print(f"  ☐ {r['id']}" + (f" 발생 {ep['n']}(실현 {str(ep['realized_at'])[:16]})" if ep else "")
              + f"  [{x.get('market', '?').upper()}] {(x.get('ticker') or '—'):8} {x.get('what', '')[:86]}")
        print(f"     조건: {str(r.get('condition'))[:96]}")
    print("\n  **이 목록이 0이 아니면 오늘 run은 여기서부터 시작한다.** "
          "조건이 실현됐는데 대응하지 않는 것이 이 시스템의 실패 양상이다.")
    # 대기가 있으면 비-0으로 — 게이트가 종료코드로 받는다.
    return 3


def cmd_act(a) -> int:
    """대응 하나를 집행 완료로 표시한다."""
    idx = a.n - 1

    def fn(r):
        if r.get("status") == "merged":
            raise _Stop(f"{a.id}는 {r.get('merged_into')}에 병합됐다 — 그 id로 표시하라")
        acts = r.get("actions") or []
        if not (0 <= idx < len(acts)):
            raise _Stop(f"대응 번호가 범위 밖이다 (1~{len(acts)})")
        acts[idx]["done"] = True
        acts[idx]["done_at"] = datetime.now(KST).strftime("%Y-%m-%d %H:%M")
        acts[idx]["done_note"] = a.note or ""
        ep = db.episode_current(r["id"])
        if ep:                                       # 대응 완료는 발생 단위(v6) — 주문 참조를 남길 수 있다
            db.response_done(ep["id"], acts[idx], a.note or None, _run_label(), getattr(a, "order_ref", None) or None)
        if all(x.get("done") or is_noop(x) for x in acts):
            r["acted"] = True
        return r
    try:
        acts = _update(a.id, fn).get("actions") or []
    except _Stop as e:
        print(e.msg, file=sys.stderr)
        return e.rc
    print(f"{a.id} 대응 {a.n} 집행 표시: [{acts[idx].get('market','?').upper()}] "
          f"{acts[idx].get('what','')[:70]}")
    return 0


def cmd_check(a) -> int:
    """★ 가지 완결성 검사(v6 · Codex J02) — 사건마다 지표 공간을 가지들이 다 덮는가 · 지연 가지가 있는가 · 겹치는 가지의 대응이 다른가.
    --signal: 그 시그널의 events[]·scenarios[] · --event: 원장 사건 하나 · --open(기본): 열린 원장 사건 전부. 종료코드 0 깨끗 · 3 미커버·겹침."""
    blocks = []                                      # (사건 dict, 가지 목록)
    numeric_text = []
    if getattr(a, "signal", ""):
        sp = Path(a.signal)
        sp = sp if sp.is_absolute() else HERE / sp
        sig = json.loads(sp.read_text(encoding="utf-8"))
        evs = {str(e.get("id")): {**e, "id": str(e.get("id"))} for e in (sig.get("events") or []) if isinstance(e, dict)}
        rows = [r for r in (sig.get("scenarios") or []) if isinstance(r, dict)]
        for eid, ev in evs.items():
            br = []
            for r in rows:
                if str(r.get("event") or "") != eid:
                    continue
                atoms, st, _ = parse_cond(r, ev.get("metrics"))
                br.append({"id": str(r.get("id")), "atoms": atoms, "status": st, "resp": _resp_set_signal(r, sig.get("market") or "")})
            blocks.append((ev, br))
        numeric_text = [str(r.get("id")) for r in rows if not r.get("cond") and _NUM_COND.search(str(r.get("condition") or ""))]
    else:
        ids = [a.event] if getattr(a, "event", "") else [e["id"] for e in db.q("SELECT id FROM jd_event WHERE status='open' ORDER BY id")]
        for eid in ids:
            ev = db.event_get(eid)
            if ev:
                blocks.append((ev, _event_branches(eid)))
        numeric_text = [r["id"] for r in _rows() if r.get("status") == "open" and not r.get("cond")
                        and _NUM_COND.search(str(r.get("condition") or ""))]
    lines, bad = [], 0
    for ev, br in blocks:
        cv = coverage(ev, br)
        head = f"[가지] {ev['id']} {ev.get('subject')} {ev.get('kind')} {str(ev.get('scheduled') or '-')[5:]} · 가지 {len(br)}"
        if not cv["judged"]:
            lines.append(head + " · 완결성 미판정(구조화 조건 없음)")
            continue
        lines.append(head + f" · 미커버 {len(cv['uncovered'])} · " + ("지연 가지 있음" if cv["has_delayed"] else "지연 가지 없음")
                     + f" · 겹침 {len(cv['overlaps'])}")
        lines += [f"    미커버: {x}" for x in cv["uncovered"]] + [f"    겹침(대응 다름): {x}" for x in cv["overlaps"]]
        if cv["uncovered"] or cv["overlaps"]:
            bad += 1
    lines.append(f"[가지] 사건 {len(blocks)} · 미커버·겹침 사건 {bad} · 구조화 없는 숫자 조건 {len(numeric_text)}"
                 + (f" ({', '.join(numeric_text[:12])})" if numeric_text else ""))
    out = "\n".join(lines)
    print(out)
    if getattr(a, "out", ""):
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(out + "\n", encoding="utf-8")
    return 3 if bad else 0


def cmd_set_cond(a) -> int:
    """원장 행의 구조화 조건을 바꾼다(승격이 조용히 덮지 않는다) — --cond '<JSON 원자 목록>' [--event EV-…]."""
    try:
        cond = json.loads(a.cond)
    except ValueError as e:
        print(f"--cond는 JSON 원자 목록이어야 한다: {e}", file=sys.stderr)
        return 2
    ev = db.event_get(a.event) if a.event else None
    if a.event and not ev:
        print(f"없는 사건 {a.event}", file=sys.stderr)
        return 2

    def fn(r):
        atoms, st, probs = parse_cond({"cond": cond}, (ev or db.event_get(r.get("event_id") or "") or {}).get("metrics"))
        if probs:
            raise _Stop("조건 구조 오류 — " + "; ".join(probs))
        r["cond"], r["cond_status"] = atoms, st
        if ev:
            r["event_id"] = ev["id"]
        r.setdefault("judgments", []).append({"when": datetime.now(KST).strftime("%Y-%m-%d %H:%M"), "realized": r.get("realized"),
                                              "acted": r.get("acted"), "note": f"[조건 구조] {a.note or 'set-cond'}"})
        return r
    try:
        _update(a.id, fn)
    except _Stop as e:
        print(e.msg, file=sys.stderr)
        return e.rc
    print(f"{a.id}: 조건 구조 {len(cond)}개 원자" + (f" · 사건 {a.event}" if a.event else ""))
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
    l.add_argument("--stale", action="store_true", help="열린 지 오래됐는데 실현 안 된 것(지연 가지 누락 후보)")
    l.add_argument("--days", type=int, default=None, help="--stale 문턱(거래일, 기본 20)")
    l.add_argument("--needs-check", default="", metavar="YYYY-MM-DD",
                   help="그날 판정이 필요한 열린 것만(next_check ≤ 날짜 · 날짜 없음 · 가격 조건)")
    j = sub.add_parser("judge", help="실현·대응 판정 (--batch <jsonl>로 여러 건)")
    j.add_argument("--id", default="")
    j.add_argument("--realized", choices=["y", "n"], default="")
    j.add_argument("--batch", default="", help="JSONL: 행마다 {\"id\",\"realized\":\"y|n\",\"acted\"?,\"note\"?}")
    j.add_argument("--acted", choices=["y", "n"])
    j.add_argument("--note", default="")
    j.add_argument("--next-check", default="", metavar="YYYY-MM-DD",
                   help="조건이 바뀔 수 있는 가장 이른 날(지표·실적 발표일). 모르면 비운다 — 매 run 판정 대상")
    j.add_argument("--price-cond", choices=["y", "n"], default=None, help="가격 조건이 걸렸는가(y면 매 run 판정)")
    j.add_argument("--expire", action="store_true",
                   help="미실현인 채 예정일이 지나 다시 올 수 없는 가지를 닫는다(status expired) — 배치는 행에 \"expire\": true")
    j.add_argument("--observation", default=None, help="관측값 JSON(사건 지표 이름: 값) — 해당 가지와 대조해 경고 · 발생에 남는다")
    j.add_argument("--price-reaction", default=None, help="가격 반응(문장) — 발생에 남는다")
    j.add_argument("--carry-forward", default="", metavar="YYYY-MM-DD",
                   help="next_check가 그날 뒤이고 가격 조건이 없는 열린 것을 '미도래'로 일괄 이월 기록")
    rp = sub.add_parser("respond", help="반대 시장이 연 행에 이 시장 대응을 더한다(같은 이벤트는 한 행)")
    rp.add_argument("--id", required=True)
    rp.add_argument("--market", choices=["kr", "us"], required=True)
    rp.add_argument("--what", required=True, help="대응 문장 — 무엇을 하나(종목·크기·다리)")
    rp.add_argument("--ticker", default="")
    rp.add_argument("--note", default="")
    rp.add_argument("--replace", action="store_true", help="이 시장 대응을 바꾼다")
    du = sub.add_parser("due", help="실현됐는데 대응이 안 끝난 것")
    du.add_argument("--market", choices=["kr", "us"], default="")
    ac = sub.add_parser("act", help="대응 하나를 집행 완료로 표시")
    ac.add_argument("--id", required=True)
    ac.add_argument("--n", type=int, required=True, help="대응 번호(1부터)")
    ac.add_argument("--note", default="")
    ac.add_argument("--order-ref", default="", help="그 대응을 집행한 주문(trades_<st>_<mkt>.json#n 등) — 발생 단위 완료 기록에 남는다")
    ck = sub.add_parser("check", help="가지 완결성 — 사건 지표 공간을 가지들이 다 덮는가(미커버·지연·겹침)")
    ck.add_argument("--signal", default="", help="시그널 파일의 events[]·scenarios[]")
    ck.add_argument("--event", default="", help="원장 사건 하나(EV-…)")
    ck.add_argument("--open", action="store_true", help="열린 원장 사건 전부(기본)")
    ck.add_argument("--out", default="")
    scd = sub.add_parser("set-cond", help="원장 행의 구조화 조건을 바꾼다")
    scd.add_argument("--id", required=True)
    scd.add_argument("--cond", required=True, help="JSON 원자 목록 — [{\"metric\",\"op\",\"value\"}…]")
    scd.add_argument("--event", default="")
    scd.add_argument("--note", default="")
    mg = sub.add_parser("merge", help="같은 조건이 두 id를 받았을 때 --from을 --into에 병합")
    mg.add_argument("--from", dest="src", required=True)
    mg.add_argument("--into", required=True)
    au = sub.add_parser("audit", help="회피 감사 지표")
    au.add_argument("--days", type=int, default=14)
    au.add_argument("--affects", choices=["kr", "us"],
                    help="그 시장에 대응이 걸린 것만")
    au.add_argument("--out", default="", help="게이트가 읽는 파일로 저장")
    a = ap.parse_args()
    if a.cmd == "judge" and not a.batch and not a.carry_forward and not (a.id and a.realized):
        ap.error("judge에는 --id와 --realized, --batch <jsonl>, 또는 --carry-forward <날짜>가 필요하다")
    return {"promote": cmd_promote, "backfill": cmd_backfill, "list": cmd_list,
            "judge": cmd_judge, "audit": cmd_audit,
            "due": cmd_due, "act": cmd_act, "merge": cmd_merge, "respond": cmd_respond,
            "check": cmd_check, "set-cond": cmd_set_cond}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
