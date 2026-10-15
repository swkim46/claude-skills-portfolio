#!/usr/bin/env python3
"""교정 전파 — 판정표의 교정이 이번 run의 사본 전부에 들어갔는지 기계로 확인하고, 단순 교체는 대신 한다.

왜(2026-10-13): fact-check 감사 48건이 전부 FAIL이었고 가장 많이 걸린 유형이 '교정 전파'였다(7개 run 중 7개, 1차·2차 모두).
교정표가 자유 문장이라 기계가 적용할 수 없었고, 같은 사실이 노트·시그널·시장 지도·일정·논지·이어받기 등 10곳에 사본으로 있어
손으로 찾아 고쳤다. 261013 US는 2차 감사 뒤에도 노트 S9·시그널·market_map에 옛 값이 남았고, 261012 KR은 교정표에 '잔존'을
14번 적은 채 감사로 넘어갔다. 이 도구가 남은 것이 0이 될 때까지 제출을 막는다.

교정 블록(판정표 안 · 기계용 — 사람용 교정 표와 같이 둔다):
    ```corrections
    {"id": 11, "claim": 17, "old": ["10-28/29"], "new": "10-28 (est.)", "op": "replace"}
    {"id": 29, "old": ["not a rates repricing"], "op": "manual", "family": [["rate"], ["repricing|excluded|원인 아님"]], "marker": "partly"}
    ```
  - `old`: 사본에 남아 있으면 안 되는 문구(정확한 부분 문자열) · `new`: 교체 문구(`op: replace`일 때 apply가 대신 바꾼다)
  - `op`: replace(apply가 교체) · manual(사람이 고친다 — 삭제·강등·한정어 추가처럼 문장을 다시 써야 하는 것)
  - `family`(선택): 옛 문구를 바꿔 쓴 사본까지 잡는다 — 묶음마다 하나 이상 걸리는(정규식) 문장이 `marker` 없이 남아 있으면 위반
    (`marker`는 문자열 하나 또는 목록 — 예: ["est", "~"] · 묶음은 좁게 써라: 넓으면 무관한 문장까지 잡는다)

범위(이번 run이 쓴 파일 전부 + 지금 살아 있는 원장 행 — 지난 run 기록은 그때의 기록이라 고치지 않는다):
  최신 노트 · signals/{signal,approved}_<st>_<mkt>*.json · journal/carry_<st>_<mkt>.md · 전망_<st>_<mkt>.md · 최신 섹터_*_<st>
  · data/run_evidence/{forecast_update,events_add}_<st>_<mkt>.json · market_map.json(축의 note_<st> · 축·섹터의 현행 칸 전부)
  · theses.json(그날 손댄 논지 + 살아 있는 armed·held 논지 — history·corrections 제외)
  · calendar.json(그날 추가된 행 + 오늘 이후 날짜의 행) · sources.json(runs에 <mkt>:<st>)
  · scenarios.json(그 run이 연 행 · 그 run이 단 대응 · 열려 있는 행 — judgments 제외)
  ★ 2026-10-15 추가(감사 74회 FAIL 중 교정 전파가 16 run 중 15 — 감사자가 찾은 옛 값이 전부 이 도구가 안 훑던 파일에 있었다):
  · data/run_evidence/research_branch*_<st>_<mkt>.md(리서치 갈래) · srcledger_add*_<st>_<mkt>.jsonl(출처 등록 입력)
  · theses_add_<st>_<mkt>.json(논지 등록 입력) · journal/sector_watch.jsonl(이번 run 날짜·시장 행)
  · journal/worklog_<st>_<mkt>.md(작업기록) · sc_judge*_<st>_<mkt>.jsonl(시나리오 판정 입력) · journal/allocation.jsonl의 이번 run 행(AL-<st>-<mkt>)
  · 옛 문구는 날짜 표기 변형('10-15'↔'10/15')을 자동으로 같이 찾는다
  · journal/trades_<st>_<mkt>.json — **고치지 않는다**(보낸 주문의 기록). 옛 값이 있는 행에 `corrections_applied`를 덧붙이고, 덧붙인 행은 남은 곳으로 세지 않는다
  범위 밖(그대로 둔다 — OK 줄 다음 `PROPAGATE SCOPE` 줄에 훑은 파일과 같이 적는다): corpus_*(지난 파일 검색 결과 인용) ·
  tools_*·levels_*·exposure_* 등 도구 출력(숫자 원본) · AI클레임_*(판정 기록 — 원 문구를 남겨야 한다) · 지난 스탬프 파일

누가 부르나(2026-10-14 · trade-run fact-check 배경화): `plan`·`verify`는 배경 에이전트도 부른다. **`apply`는 본 세션만 부른다** —
apply는 노트·시그널·원장 파일을 통째로 다시 쓰므로, 본 세션이 같은 파일을 쓰는 동안 배경에서 돌리면 한쪽의 편집이 지워진다.
배경 에이전트는 교정이 있으면 `plan`까지만 하고 판정표에 '본 세션 적용 대기'를 남긴다(trade-run `stage4_note.md` 5-C).

사용:
    python3 fc_propagate.py verify <판정표.md> --stamp 261013 --market us [--write]   # 남은 것 0이면 PROPAGATE OK (--write면 판정표에 줄)
    python3 fc_propagate.py plan   <판정표.md> --stamp 261013 --market us             # 교정마다 적중 위치(교정 표 '위치' 칸용)
    python3 fc_propagate.py apply  <판정표.md> --stamp 261013 --market us             # op=replace를 범위 안에서 교체
    python3 fc_propagate.py --selftest
종료코드: 0 = 남은 것 0 · 1 = 남음/블록 오류 · 2 = 사용법
"""
import argparse
import json
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[4] / "ai-trading-bot"
BLOCK_RE = re.compile(r"```corrections\s*\n(.*?)```", re.S)
LINE_RE = re.compile(r"^PROPAGATE (OK|FAIL|SCOPE)\b.*$\n?", re.M)
OUT_OF_SCOPE = ("corpus_*(지난 파일 검색 결과 인용) · tools_*·levels_*·exposure_* 등 도구 출력(숫자 원본) · "
                "AI클레임_*(판정 기록) · 지난 스탬프 파일")
SENT_SPLIT = re.compile(r"(?<=[.;!?。])\s+|\n")


DATE_TOK = re.compile(r"(?<!\d)(\d{1,2})([-/])(\d{1,2})(?!\d)")


def expand_variants(olds: list) -> list:
    """옛 문구의 날짜 표기 변형을 같이 찾는다 — '10-15'와 '10/15'(2026-10-15 · 10-15 국내 감사: 'IBK(10-15)'를 고쳤는데
    '(10/15)' 사본 5곳이 남아 FAIL). 구분자 '-'·'/'를 서로 바꾼 문구를 더한다(소수점과 섞이는 '.'은 바꾸지 않는다)."""
    out = []
    for o in olds:
        for v in (o, DATE_TOK.sub(lambda m: m.group(1) + ("/" if m.group(2) == "-" else "-") + m.group(3), o)):
            if v not in out:
                out.append(v)
    return out


def load_corrections(verdict_text: str):
    """교정 블록 → (목록, 오류). 블록이 없으면 빈 목록."""
    out, errs = [], []
    for m in BLOCK_RE.finditer(verdict_text):
        for k, ln in enumerate(m.group(1).splitlines(), 1):
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            try:
                c = json.loads(ln)
            except json.JSONDecodeError as e:
                errs.append(f"교정 블록 {k}행 JSON 오류: {e}")
                continue
            if not c.get("id") or not isinstance(c.get("old"), list) or not c["old"]:
                errs.append(f"교정 블록 {k}행: id와 old(문구 목록)가 필요하다")
                continue
            if c.get("op", "replace") == "replace" and not isinstance(c.get("new"), str):
                errs.append(f"교정 {c['id']}: op=replace면 new(교체 문구)가 필요하다 — 다시 써야 하면 op=manual")
                continue
            c["old"] = expand_variants(c["old"])
            out.append(c)
    return out, errs


def _latest(paths):
    def ver(p):
        m = re.search(r"_v(\d+)_(\d+)\.md$", p.name)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    paths = [p for p in paths if p.is_file()]
    return max(paths, key=ver) if paths else None


def run_date(stamp: str) -> str:
    return f"20{stamp[:2]}-{stamp[2:4]}-{stamp[4:6]}"


def text_scope(proj: Path, st: str, mk: str) -> list:
    """범위 안의 텍스트 파일(통째로 이번 run 것)."""
    a = proj / "analysis"
    files = [_latest(a.glob(f"분석노트_{st}_{mk}_v*.md")), proj / "journal" / f"carry_{st}_{mk}.md",
             a / f"전망_{st}_{mk}.md", _latest(a.glob(f"섹터_*_{st}_v*.md"))]
    files += sorted((proj / "data" / "run_evidence").glob(f"research_branch*_{st}_{mk}.md"))   # 리서치 갈래(2026-10-15)
    files.append(proj / "journal" / f"worklog_{st}_{mk}.md")      # 작업기록 — 판단 문장의 사본이 남는다(10-15 감사)
    return [f for f in files if f and f.is_file()]


def jsonl_scope(proj: Path, st: str, mk: str) -> list:
    """(파일, 행 고르는 함수) — JSONL은 행마다 객체다(2026-10-15). 함수가 True인 행만 훑고 고친다."""
    ev = proj / "data" / "run_evidence"
    out = [(p, lambda r: True) for p in sorted(ev.glob(f"srcledger_add*_{st}_{mk}.jsonl"))]
    out += [(p, lambda r: True) for p in sorted(ev.glob(f"sc_judge*_{st}_{mk}.jsonl"))]       # 시나리오 판정 입력(10-15 감사)
    al = proj / "journal" / "allocation.jsonl"
    if al.is_file():
        out.append((al, lambda r, _id=f"AL-{st}-{mk}": r.get("id") == _id))                  # 이번 run 배분 판단 행
    day = run_date(st)
    sw = proj / "journal" / "sector_watch.jsonl"
    if sw.is_file():
        out.append((sw, lambda r: str(r.get("date", "")) == day and str(r.get("market", "")).lower() == mk))
    return [(p, f) for p, f in out if p.is_file()]


def trades_scope(proj: Path, st: str, mk: str):
    """이번 run 거래 기록 — 고치지 않고 `corrections_applied`를 덧붙인다(보낸 주문의 기록이라)."""
    p = proj / "journal" / f"trades_{st}_{mk}.json"
    return p if p.is_file() else None


def json_scope(proj: Path, st: str, mk: str) -> list:
    """(파일, 범위 안 하위 객체를 고르는 함수) — 함수는 파일 전체 dict를 받아 (경로표시, 객체) 목록을 돌려준다."""
    day = run_date(st)
    tag = f"{mk}:{st}"
    whole = lambda d: [("", d)]
    out = []
    for p in sorted((proj / "signals").glob(f"signal_{st}_{mk}*.json")) + \
            sorted((proj / "signals").glob(f"approved_{st}_{mk}*.json")):
        out.append((p, whole))
    for n in (f"forecast_update_{st}_{mk}.json", f"events_add_{st}_{mk}.json", f"theses_add_{st}_{mk}.json"):
        p = proj / "data" / "run_evidence" / n
        if p.is_file():
            out.append((p, whole))

    # ★ 살아 있는 원장 행(2026-10-15) — 옛 값이 지난 run이 쓴 '현행' 칸(축의 고리·마감일, 미래 일정, 열린 시나리오, armed·held 논지)에
    #   남아 있어도 다음 run이 그대로 읽는다. 감사(10-14 국내)가 calendar·scenarios·market_map의 그런 사본을 찾아 FAIL을 줬다.
    #   지난 run의 run별 칸(note_<다른 스탬프>·history·judgments·corrections)은 그때의 기록이라 범위 밖이다.
    def mm_pick(d):
        res = []
        for i, ax in enumerate(d.get("axes") or []):
            if f"note_{st}" in ax:
                res.append((f"axes[{ax.get('id', i)}]", {f"note_{st}": ax[f"note_{st}"], "__ref": (ax, f"note_{st}")}))
            res.append((f"axes[{ax.get('id', i)}]", {k: v for k, v in ax.items() if not k.startswith("note_")}))
        for i, s in enumerate(d.get("sectors") or []):
            res.append((f"sectors[{s.get('code', i)}]", {k: v for k, v in s.items() if not k.startswith("note_")}))
        return res

    def th_pick(d):
        res = []
        for t in d.get("theses") or []:
            touched = str(t.get("created", "")).startswith(day) or any(
                str(h.get("ts", h.get("at", ""))).startswith(day) for h in t.get("history") or [])
            if touched or t.get("status") in ("armed", "held"):
                res.append((f"theses[{t.get('id')}]", {k: v for k, v in t.items() if k not in ("history", "corrections")}))
        return res

    def cal_pick(d):
        return [(f"events[{e.get('key')}]", e) for e in d.get("events") or []
                if str(e.get("added", "")).startswith(day) or str(e.get("date", ""))[:10] >= day]

    def src_pick(d):
        rows = d if isinstance(d, list) else (d.get("sources") or d.get("rows") or [])
        return [(f"sources[{r.get('id')}]", r) for r in rows if tag in (r.get("runs") or [])]

    def sc_pick(d):
        res = []
        for s in d.get("scenarios") or []:
            if s.get("opened_by") == tag or s.get("status") == "open":
                res.append((f"scenarios[{s.get('id')}]", {k: v for k, v in s.items() if k != "judgments"}))
            else:
                for mk2, r in (s.get("responses") or {}).items():
                    if isinstance(r, dict) and r.get("by") == tag:
                        res.append((f"scenarios[{s.get('id')}].responses.{mk2}", r))
        return res

    for name, pick in (("market_map.json", mm_pick), ("theses.json", th_pick), ("calendar.json", cal_pick),
                       ("sources.json", src_pick), ("scenarios.json", sc_pick)):
        p = proj / "journal" / name
        if p.is_file():
            out.append((p, pick))
    return out


def _leaves(obj, path=""):
    """문자열 잎 (경로, 부모, 키, 값). 부모·키로 제자리 교체를 할 수 있다."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "__ref":
                continue
            if isinstance(v, str):
                yield f"{path}.{k}", obj, k, v
            else:
                yield from _leaves(v, f"{path}.{k}")
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if isinstance(v, str):
                yield f"{path}[{i}]", obj, i, v
            else:
                yield from _leaves(v, f"{path}[{i}]")


def _family_hit(sentence: str, c: dict) -> bool:
    fam = c.get("family") or []
    if not fam:
        return False
    marks = c.get("marker") or []
    marks = [marks] if isinstance(marks, str) else marks
    if any(mk in sentence for mk in marks):
        return False
    if c.get("new") and c["new"] in sentence:
        return False
    return all(any(re.search(alt, sentence, re.I) for alt in group) for group in fam)


def scan(proj: Path, st: str, mk: str, corrs: list, apply: bool = False) -> dict:
    """교정마다 남은 곳 목록. apply=True면 op=replace를 교체하고 파일을 쓴다."""
    hits = {c["id"]: [] for c in corrs}
    replaced = 0

    def check(where: str, value: str):
        found = []
        for c in corrs:
            for o in c["old"]:
                if o in value:
                    i = value.find(o)
                    found.append((c, "old", value[max(0, i - 40): i + len(o) + 40]))
            if c.get("family"):
                for sent in SENT_SPLIT.split(value):
                    if _family_hit(sent, c) and not any(o in sent for o in c["old"]):
                        found.append((c, "family", sent[:140]))
        for c, kind, snip in found:
            hits[c["id"]].append({"where": where, "kind": kind, "snippet": snip.replace("\n", " ")})

    def fix(value: str) -> str:
        nonlocal replaced
        for c in corrs:
            if c.get("op", "replace") != "replace":
                continue
            for o in c["old"]:
                if o in value and c["new"] not in value.replace(o, ""):
                    value = value.replace(o, c["new"])
                    replaced += 1
        return value

    for p in text_scope(proj, st, mk):
        txt = p.read_text(encoding="utf-8")
        if apply:
            new = fix(txt)
            if new != txt:
                p.write_text(new, encoding="utf-8")
                txt = new
        for k, ln in enumerate(txt.split("\n"), 1):
            check(f"{p.relative_to(proj)}:L{k}", ln)

    for p, pick in json_scope(proj, st, mk):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        changed = False
        for label, obj in pick(data):
            ref = obj.get("__ref") if isinstance(obj, dict) else None
            for path, parent, key, val in list(_leaves(obj)):
                if apply:
                    nv = fix(val)
                    if nv != val:
                        parent[key] = nv
                        if ref and key == ref[1]:
                            ref[0][ref[1]] = nv
                        val, changed = nv, True
                check(f"{p.relative_to(proj)}::{label}{path}", val)
        if apply and changed:
            p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    for p, keep in jsonl_scope(proj, st, mk):              # JSONL — 행마다 객체(2026-10-15)
        try:
            lines = p.read_text(encoding="utf-8").split("\n")
        except OSError:
            continue
        changed = False
        for k, ln in enumerate(lines):
            if not ln.strip():
                continue
            try:
                row = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if not isinstance(row, dict) or not keep(row):
                continue
            row_changed = False
            for path, parent, key, val in list(_leaves(row)):
                if apply:
                    nv = fix(val)
                    if nv != val:
                        parent[key], val, row_changed = nv, nv, True
                check(f"{p.relative_to(proj)}:L{k + 1}{path}", val)
            if row_changed:
                lines[k] = json.dumps(row, ensure_ascii=False)
                changed = True
        if apply and changed:
            p.write_text("\n".join(lines), encoding="utf-8")

    tp = trades_scope(proj, st, mk)                        # 거래 기록 — 고치지 않고 교정 표시만(2026-10-15)
    if tp:
        try:
            td = json.loads(tp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            td = {}
        changed = False
        for i, t in enumerate(td.get("trades") or []):
            if not isinstance(t, dict):
                continue
            done = {str(x.get("id")) for x in t.get("corrections_applied") or [] if isinstance(x, dict)}
            body = {k: v for k, v in t.items() if k not in ("corrections_applied", "fill", "result")}
            for path, _parent, _key, val in list(_leaves(body)):
                for c in corrs:
                    if not any(o in val for o in c["old"]) or str(c["id"]) in done:
                        continue
                    if apply:
                        t.setdefault("corrections_applied", []).append(
                            {"id": c["id"], "old": c["old"], "new": c.get("new"), "op": c.get("op", "replace"),
                             "where": path, "at": datetime.now().isoformat(timespec="seconds")})
                        done.add(str(c["id"]))
                        changed = True
                    else:
                        i0 = val.find(next(o for o in c["old"] if o in val))
                        hits[c["id"]].append({"where": f"{tp.relative_to(proj)}::trades[{i}:{t.get('ticker')}]{path}",
                                              "kind": "trades(표시 없음 — apply가 corrections_applied를 덧붙인다)",
                                              "snippet": val[max(0, i0 - 40): i0 + 80].replace("\n", " ")})
        if apply and changed:
            tp.write_text(json.dumps(td, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"hits": hits, "replaced": replaced}


def scope_files(proj: Path, st: str, mk: str) -> list:
    """훑은 파일(상대 경로) — OK 줄 다음 `PROPAGATE SCOPE` 줄에 적는다(감사자가 범위를 추측하지 않게)."""
    fs = [p for p in text_scope(proj, st, mk)] + [p for p, _ in json_scope(proj, st, mk)] + \
         [p for p, _ in jsonl_scope(proj, st, mk)] + ([trades_scope(proj, st, mk)] if trades_scope(proj, st, mk) else [])
    return [str(p.relative_to(proj)) for p in fs]


def residue_in_table(verdict_text: str) -> list:
    """교정 표 '위치' 칸에 '잔존'이 적혀 있으면 미완료다."""
    out = []
    in_corr = False
    for k, ln in enumerate(verdict_text.split("\n"), 1):
        if re.match(r"^##+\s*교정", ln):
            in_corr = True
            continue
        if in_corr and re.match(r"^##\s", ln):
            in_corr = False
        if in_corr and ln.startswith("|") and "잔존" in ln:
            out.append(k)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", choices=["verify", "plan", "apply"])
    ap.add_argument("verdict", nargs="?")
    ap.add_argument("--stamp")
    ap.add_argument("--market", choices=["kr", "us"])
    ap.add_argument("--project", default=str(PROJECT))
    ap.add_argument("--write", action="store_true", help="verify가 판정표에 PROPAGATE OK/FAIL 줄을 쓴다")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not (a.cmd and a.verdict and a.stamp and a.market):
        ap.print_usage()
        return 2
    vp, proj = Path(a.verdict), Path(a.project)
    vt = vp.read_text(encoding="utf-8")
    corrs, errs = load_corrections(vt)
    if errs:
        print("교정 블록 오류:")
        for e in errs:
            print("  - " + e)
        return 1
    res = scan(proj, a.stamp, a.market, corrs, apply=(a.cmd == "apply"))
    if a.cmd == "apply":
        print(f"교체 {res['replaced']}곳(op=replace) — 이어서 verify")
        res = scan(proj, a.stamp, a.market, corrs)
    left = {cid: h for cid, h in res["hits"].items() if h}
    resid = residue_in_table(vt)
    if a.cmd == "plan":
        for c in corrs:
            hs = res["hits"][c["id"]]
            print(f"교정 {c['id']} ({c.get('op', 'replace')}) — {len(hs)}곳")
            for h in hs:
                print(f"   {h['where']}  [{h['kind']}]  …{h['snippet']}…")
        return 0
    ok = not left and not resid
    scoped = scope_files(proj, a.stamp, a.market)
    n_files = len(scoped)
    line = (f"PROPAGATE {'OK' if ok else 'FAIL'} (fc_propagate.py {datetime.now():%Y-%m-%d %H:%M} · 교정 {len(corrs)}건 · "
            f"범위 {n_files}파일 · 남은 곳 {sum(len(h) for h in left.values())} · 표 '잔존' {len(resid)})")
    scope_line = f"PROPAGATE SCOPE 훑은 파일 {n_files}: " + " · ".join(scoped) + f" | 범위 밖: {OUT_OF_SCOPE}"
    for cid, hs in left.items():
        print(f"✗ 교정 {cid} — {len(hs)}곳 남음")
        for h in hs[:12]:
            print(f"   {h['where']}  [{h['kind']}]  …{h['snippet']}…")
    for k in resid:
        print(f"✗ 판정표 L{k} 교정 표에 '잔존' — 고친 뒤 위치 칸을 다시 써라")
    if not corrs:
        print("※ 교정 블록이 없다 — 교정이 0건이면 이대로 OK, 교정 표가 있으면 ```corrections 블록을 써라")
        if re.search(r"^##+\s*교정", vt, re.M) and re.search(r"^\|\s*\d+\s*\|", vt, re.M):
            ok = False
            line = line.replace("PROPAGATE OK", "PROPAGATE FAIL") + " · 교정 표는 있는데 기계용 블록이 없다"
    print(line)
    print(scope_line)
    if a.write:
        vt2 = LINE_RE.sub("", vt).rstrip("\n") + "\n\n" + line + "\n" + scope_line + "\n"
        vp.write_text(vt2, encoding="utf-8")
    return 0 if ok else 1


def selftest() -> int:
    with tempfile.TemporaryDirectory() as d:
        proj = Path(d)
        for sub in ("analysis", "signals", "journal", "data/run_evidence"):
            (proj / sub).mkdir(parents=True)
        (proj / "analysis/분석노트_261013_us_v1_0.md").write_text("old note — not a rates repricing\n", encoding="utf-8")
        (proj / "analysis/분석노트_261013_us_v1_1.md").write_text(
            "S9 capex (10-28/29)\nThe semis drop is a memory shock, not a rate story.\nfine line\n", encoding="utf-8")
        (proj / "signals/signal_261013_us.json").write_text(json.dumps(
            {"scenarios": [{"condition": "macro half pending 10-28/29"}]}), encoding="utf-8")
        (proj / "journal/market_map.json").write_text(json.dumps({"axes": [
            {"id": "a1", "note_261013": "capex 10-28/29", "updated": "2026-10-13"},
            {"id": "a0", "note_261009": "capex 10-28/29 (old run)", "updated": "2026-10-09"}], "sectors": []}), encoding="utf-8")
        (proj / "journal/theses.json").write_text(json.dumps({"theses": [
            {"id": "t1", "created": "2026-10-01", "thesis": "10-28/29 call", "history": [{"ts": "2026-10-05", "note": "10-28/29"}]}]}),
            encoding="utf-8")
        # 2026-10-15 범위 — 리서치 갈래·출처 등록 입력(JSONL)·섹터 기록(이번 run 행만)·미래 일정·거래 기록(표시만)
        (proj / "data/run_evidence/research_branch2_261013_us.md").write_text("R2-1 capex 10-28/29\n", encoding="utf-8")
        (proj / "data/run_evidence/srcledger_add_261013_us.jsonl").write_text(
            json.dumps({"fact": "capex call 10-28/29", "url": "https://x"}) + "\n", encoding="utf-8")
        (proj / "journal/sector_watch.jsonl").write_text(
            json.dumps({"date": "2026-10-13", "market": "us", "note": "SMH 10-28/29"}) + "\n"
            + json.dumps({"date": "2026-10-09", "market": "us", "note": "SMH 10-28/29 (old run)"}) + "\n", encoding="utf-8")
        (proj / "journal/calendar.json").write_text(json.dumps({"events": [
            {"key": "k1", "date": "2026-10-28", "event": "capex 10-28/29", "added": "2026-10-01"},
            {"key": "k0", "date": "2026-10-01", "event": "capex 10-28/29 (past)", "added": "2026-09-20"}]}), encoding="utf-8")
        (proj / "journal/trades_261013_us.json").write_text(json.dumps({"trades": [
            {"ticker": "AVGO", "action": "BUY", "proposal": {"thesis": "capex 10-28/29"}}]}), encoding="utf-8")
        blk = ('```corrections\n{"id": 11, "old": ["10-28/29"], "new": "10-28 (est.)", "op": "replace"}\n'
               '{"id": 29, "old": ["not a rates repricing"], "op": "manual", "family": [["rate"], ["memory"]], "marker": "partly"}\n```\n')
        v = proj / "analysis/AI클레임_261013_us_판정.md"
        v.write_text("# 판정\n\n## 교정\n\n| # | 심각도 | 위치 |\n|---|---|---|\n| 11 | 🟡 | 노트 |\n\n" + blk, encoding="utf-8")
        r1 = main(["verify", str(v), "--stamp", "261013", "--market", "us", "--project", str(proj)])
        rc_apply = main(["apply", str(v), "--stamp", "261013", "--market", "us", "--project", str(proj)])
        note = (proj / "analysis/분석노트_261013_us_v1_1.md").read_text(encoding="utf-8")
        mm = json.loads((proj / "journal/market_map.json").read_text(encoding="utf-8"))
        th = json.loads((proj / "journal/theses.json").read_text(encoding="utf-8"))
        ok_apply = ("10-28 (est.)" in note and mm["axes"][0]["note_261013"] == "capex 10-28 (est.)"
                    and mm["axes"][1]["note_261009"] == "capex 10-28/29 (old run)"
                    and th["theses"][0]["thesis"] == "10-28/29 call")
        rb = (proj / "data/run_evidence/research_branch2_261013_us.md").read_text(encoding="utf-8")
        sl = (proj / "data/run_evidence/srcledger_add_261013_us.jsonl").read_text(encoding="utf-8")
        sw = (proj / "journal/sector_watch.jsonl").read_text(encoding="utf-8")
        cal = json.loads((proj / "journal/calendar.json").read_text(encoding="utf-8"))["events"]
        tr = json.loads((proj / "journal/trades_261013_us.json").read_text(encoding="utf-8"))["trades"][0]
        ok_scope = ("10-28 (est.)" in rb and "10-28 (est.)" in sl and "SMH 10-28 (est.)" in sw and "(old run)" in sw
                    and "10-28/29 (old run)" in sw and cal[0]["event"] == "capex 10-28 (est.)" and cal[1]["event"] == "capex 10-28/29 (past)"
                    and tr["proposal"]["thesis"] == "capex 10-28/29" and any(x.get("id") == 11 for x in tr.get("corrections_applied") or []))
        ok_apply = ok_apply and ok_scope and expand_variants(["IBK(10-15)", "x"]) == ["IBK(10-15)", "IBK(10/15)", "x"]
        # 남은 것: 교정 29의 family(바꿔 쓴 사본) → 여전히 FAIL
        ok_family = rc_apply == 1
        (proj / "analysis/분석노트_261013_us_v1_1.md").write_text(note.replace("not a rate story", "partly a rate story"),
                                                                    encoding="utf-8")
        r3 = main(["verify", str(v), "--stamp", "261013", "--market", "us", "--project", str(proj)])
        ok = r1 == 1 and ok_apply and ok_family and r3 == 0
        print("selftest:", "OK" if ok else "FAIL",
              f"| 첫 verify FAIL={r1 == 1} | apply 범위만 교체={ok_apply} (새 범위 {ok_scope}) | 바꿔 쓴 사본 잡음={ok_family} | 고친 뒤 OK={r3 == 0}")
        return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
