#!/usr/bin/env python3
"""
노트 필수 요소 검사 — 노트가 **매매에 실제로 쓰인 요소**를 빠짐없이 담고, 요소마다 **출처**가 남았는지 기계로 본다.

왜(2026-10-13 사용자): "노트 읽어볼 생각이 딱히 없다. 매매에 필요한 요소들만 제대로 들어가 있으면 다른 감사는 하지 마라.
대신 매매에 필수적인 요소들은 무조건 노트에 남겨야 한다 — 그동안 실제로 사용한 정보와 그 출처를 중심으로." 그래서 노트 게이트의
깊이·형식 검문(심층 수·스탬프·바이트·링크 수)을 걷어내고, 이 검사가 필수 요소의 **존재와 출처**만 본다.

필수 요소(지난 3개 run의 시그널·risk_guard·다음 run 입력에서 실제로 쓰인 것 — 계획 2026-10-13):
  제안(근거·크기 사유·기대 상승·매도 다리) · 배분 판단(목표 비율·국면 숫자) · 시나리오(조건→대응) · 보유 판단 · 후보 판정 사유
  (섹터 보드·자본 줄·유니버스 생성줄·되돌림 원인 줄은 crosscheck.py가 이미 본다)

검사 — 시그널은 5단에서 생기므로 두 번 나눠 돈다:
  note   (4단 끝 · note 게이트 전) 노트만 — §0 목표 투자 비중 줄 · §7 제안·시나리오 표 · [AI] 문장 끝 [S번호] + 노트 끝 출처 표
         → 노트 맨 끝에 `<!-- NOTE-LINT OK … -->` 줄(note 게이트가 찾는다)
  signal (5단 · 갈래 ① decide 뒤 · risk_guard 전) 노트↔시그널 — 시그널의 제안 종목·시나리오 id·보유·거부 후보가 노트에 있고
         §0에 목표 비율이 있다 · 출처 칸(제안 basis_refs · 배분 regime_refs · 시나리오·거부 후보·보유 sources)
         → 시그널 `note_lint` 칸(risk_guard가 승인 파일에 옮기고 dispatch 게이트가 찾는다)
  출처 검사는 `config/limits.json` `note_sources_required`가 false면 경고만(형식이 자리잡기 전 첫날). 주문 근거 자체는
  갈래 ①(fc_track1)이 판정표 행으로 막는다.

    python3 note_lint.py note   --stamp 261013 --market us
    python3 note_lint.py signal --stamp 261013 --market us
종료코드: 0 = NOTE-LINT OK · 1 = 위반 · 2 = 파일 없음
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
KST = timezone(timedelta(hours=9))
REF_RE = re.compile(r"^(S\d+|SRC-\S+|R\d+-\d+|data/\S+|tools:\S+|cal:\S+|https?://\S+|signal:\S+|theses:\S+|snapshot)$")


def latest_note(st: str, mk: str):
    def ver(p):
        m = re.search(r"_v(\d+)_(\d+)\.md$", p.name)
        return (int(m.group(1)), int(m.group(2))) if m else (0, 0)
    c = sorted((HERE / "analysis").glob(f"분석노트_{st}_{mk}_v*.md"), key=ver)
    return c[-1] if c else None


def section(note: str, head: str) -> str:
    m = re.search(rf"^##\s*{re.escape(head)}\b.*?(?=^##\s|\Z)", note, re.M | re.S)
    return m.group(0) if m else ""


def refs_ok(v) -> bool:
    return isinstance(v, list) and bool(v) and all(isinstance(x, str) and REF_RE.match(x.strip()) for x in v)


def lint_note(note: str, sources_required: bool) -> tuple:
    """노트만 — 필수 요소의 자리와 [AI]→[S번호]."""
    errs, src = [], []
    s0, s7 = section(note, "§0"), section(note, "§7")
    if not s0:
        errs.append("§0 절이 없다")
    elif not re.search(r"\d+(\.\d+)?\s*%", s0):
        errs.append("§0에 목표 투자 비중(%) 줄이 없다 — 배분 판단은 모든 매수 크기를 정하므로 §0 넷째 줄에 숫자로")
    if not s7:
        errs.append("§7 절이 없다")
    elif not re.search(r"^\|\s*S1\s*\|", s7, re.M):
        errs.append("§7에 시나리오 행(| S1 |)이 없다 — 다음 run이 실현 여부를 먼저 판정하는 표다")
    errs2, src2 = _ai_links(note)
    errs += errs2
    src += src2
    qe, qw = s_quality(note)
    src += qe
    if sources_required:
        return errs + [f"[출처] {x}" for x in src], qw
    return errs, src + qw


def _ai_links(note: str) -> tuple:
    src = []
    ai = [ln for ln in note.split("\n") if "[AI]" in ln]
    no_s = [ln for ln in ai if not re.search(r"\[S\d+\]", ln)]
    if ai and no_s:
        src.append(f"[AI] 문장 {len(no_s)}/{len(ai)}개에 출처 번호 [S번호]가 없다")
    if ai and not re.search(r"^##\s*(§12|출처|Sources)", note, re.M):
        src.append("노트 끝에 출처 표가 없다 — srcledger.py render의 §12(S 칸이 [S번호]의 대상)를 붙여라")
    ms = re.search(r"^##\s*(§12|출처|Sources).*", note, re.M | re.S)            # 시나리오 행(| S1 |)과 섞이지 않게 출처 절 안만
    have = set(re.findall(r"^\|\s*(S\d+)\s*\|", ms.group(0) if ms else "", re.M))
    cited = sorted(set(re.findall(r"\[(S\d+)\]", note)) - have, key=lambda x: int(x[1:]))
    if ai and cited:
        src.append(f"[S번호] {len(cited)}개가 출처 표에 없다({', '.join(cited[:6])}) — srcledger.py add가 알려 준 번호를 쓴다")
    return [], src


QUOTE_FROM = "2026-10-15 12:00"     # 이 시각 뒤에 가져온 사실부터 인용 필수(그 전 사실의 재인용은 경고)


def s_quality(note: str, shared: list = None) -> tuple:
    """[AI] 줄이 가리키는 출처 행의 품질(2026-10-15) — (위반, 경고). 노트 §12 표의 원장 id(SRC-…)로 공용 원장 행을 찾는다
    (원장 순서는 재사용 사실이 끼면 밀릴 수 있어 번호가 아니라 id로). 새 사실(QUOTE_FROM 뒤)은 URL·원문 인용(T1·T2)·as-of가 없으면 위반."""
    ms = re.search(r"^##\s*(§12|출처|Sources).*", note, re.M | re.S)
    if not ms:
        return [], []
    if shared is None:
        try:
            shared = (json.loads((HERE / "journal" / "sources.json").read_text(encoding="utf-8")) or {}).get("sources") or []
        except (OSError, json.JSONDecodeError):
            return [], ["출처 원장(journal/sources.json)을 못 읽어 출처 행 품질을 못 봤다"]
    by_id = {r.get("id"): r for r in shared}
    s2id = {}
    for ln in ms.group(0).split("\n"):
        m = re.match(r"^\|\s*(S\d+)\s*\|", ln)
        mi = re.search(r"\bSRC-\d+\b", ln)
        if m and mi:
            s2id[m.group(1)] = mi.group(0)
    cited = sorted({s for ln in note.split("\n") if "[AI]" in ln for s in re.findall(r"\[(S\d+)\]", ln)},
                   key=lambda x: int(x[1:]))
    errs, warns = [], []
    for s in cited:
        r = by_id.get(s2id.get(s))
        if not r:
            continue                                   # 표에 없는 번호는 _ai_links가 잡는다
        tier = str(r.get("tier") or "")
        miss = []
        if tier != "data" and not r.get("url"):
            miss.append("URL")
        if tier in ("1", "2") and r.get("load_bearing", True) and len(str(r.get("quote") or "").strip()) < 20:
            miss.append("원문 인용")
        if str(r.get("as_of") or "미확인") == "미확인":
            miss.append("as-of")
        if miss:
            msg = f"{s}({r.get('id')}) {str(r.get('fact') or '')[:40]} — {'·'.join(miss)} 없음"
            (errs if str(r.get("retrieved") or "") >= QUOTE_FROM else warns).append(msg)
    e = [f"[AI] 줄이 가리키는 새 출처 행 {len(errs)}개가 인용과 함께 등록되지 않았다(srcledger.py add --quote · 못 받은 사실은 [AI]가 아니라 "
         f"[확인 안 됨]): " + " / ".join(errs[:6])] if errs else []
    w = [f"옛 출처 행 재인용 {len(warns)}개에 인용·링크·기준일이 비었다: " + " / ".join(warns[:4])] if warns else []
    return e, w


def lint(sig: dict, note: str, sources_required: bool) -> tuple:
    errs, src = [], []
    s7, s0 = section(note, "§7"), section(note, "§0")
    for p in (sig.get("proposals") or []) + (sig.get("refill_candidates") or []):   # 재투자 후보도(2026-10-15)
        if p.get("action") not in ("BUY", "SELL") or p.get("source") == "discipline":
            continue
        t = str(p.get("ticker"))
        if t not in s7 and t not in note:
            errs.append(f"제안 {p['action']} {t}가 노트에 없다 — §7 제안 표에 근거·크기 사유와 함께 적어라")
        if not refs_ok(p.get("basis_refs")):
            src.append(f"제안 {p['action']} {t}: basis_refs(크기 사유·집중 명분이 기대는 출처 — S번호·R행·도구 파일)가 없다")
    for sc in sig.get("scenarios") or []:
        sid = str(sc.get("id") or "")
        if sid and not re.search(rf"\|\s*{re.escape(sid)}\s*\|", s7 or note):
            errs.append(f"시나리오 {sid}가 노트 §7 표에 없다")
        if not refs_ok(sc.get("sources")):
            src.append(f"시나리오 {sid or '?'}: sources(조건이 기대는 일정·출처)가 없다")
    al = sig.get("allocation") or {}
    tgt = al.get("target_invested_pct")
    if tgt is not None and str(int(float(tgt))) not in s0:
        errs.append(f"배분 목표 {tgt}%가 노트 §0에 없다 — §0 넷째 줄에 그대로")
    if al and not refs_ok(al.get("regime_refs")):
        src.append("배분: regime_refs(국면 숫자의 출처 — tools:국면재료·R행·S번호)가 없다")
    for h in sig.get("holds") or []:
        t = str(h.get("ticker"))
        if t not in note:
            errs.append(f"보유 판단 {t}가 노트에 없다")
        if re.search(r"\d", str(h.get("why") or "")) and not refs_ok(h.get("sources")):
            src.append(f"보유 {t}: 판단 근거 숫자의 sources가 없다")
    for r in sig.get("rejected_candidates") or []:
        t = str(r.get("ticker"))
        if t and t not in note:
            errs.append(f"거부 후보 {t}가 노트에 없다 — §6 표 판정 칸 또는 §8에 사유와 함께")
        if not refs_ok(r.get("sources")):
            src.append(f"거부 후보 {t}: sources(거부 사유의 출처 — levels·base_rates·R행)가 없다")
    src += _ai_links(note)[1]
    if sources_required:
        errs += [f"[출처] {x}" for x in src]
        return errs, []
    return errs, src


def sources_required(val, today: str = "") -> bool:
    """`limits.note_sources_required` — true/false, 또는 'YYYY-MM-DD'(그날 KST부터 위반 · 2026-10-13: 형식 첫날은 경고만이고
    다음 날부터 강제 — 사람이 바꾸는 단계를 남기지 않는다)."""
    if isinstance(val, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", val.strip()):
        return (today or datetime.now(KST).strftime("%Y-%m-%d")) >= val.strip()
    return bool(val)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["note", "signal"])
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--market", required=True, choices=["kr", "us"])
    ap.add_argument("--no-write", action="store_true", help="노트·시그널에 결과를 쓰지 않는다")
    a = ap.parse_args(argv)
    sp = HERE / "signals" / f"signal_{a.stamp}_{a.market}.json"
    np_ = latest_note(a.stamp, a.market)
    if not np_ or (a.cmd == "signal" and not sp.exists()):
        print(f"없음: {'노트' if not np_ else sp.name}", file=sys.stderr)
        return 2
    try:
        lim = json.loads((HERE / "config" / "limits.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        lim = {}
    note = np_.read_text(encoding="utf-8")
    req = sources_required(lim.get("note_sources_required"))
    if a.cmd == "note":
        errs, warns = lint_note(note, req)
    else:
        sig = json.loads(sp.read_text(encoding="utf-8"))
        errs, warns = lint(sig, note, req)
    ok = not errs
    line = (f"NOTE-LINT {'OK' if ok else 'FAIL'} (note_lint.py {a.cmd} {datetime.now(KST):%Y-%m-%d %H:%M} · {np_.name} · "
            f"위반 {len(errs)} · 출처 경고 {len(warns)})")
    for e in errs:
        print("  ✗ " + e)
    for w in warns:
        print("  ※ " + w)
    print(line)
    if not a.no_write and a.cmd == "note":
        body = re.sub(r"^<!-- NOTE-LINT (OK|FAIL)\b.*-->\n?", "", note, flags=re.M).rstrip("\n")
        np_.write_text(body + "\n\n<!-- " + line + " -->\n", encoding="utf-8")
    elif not a.no_write:
        sig["note_lint"] = line
        sp.write_text(json.dumps(sig, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
