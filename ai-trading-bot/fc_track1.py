#!/usr/bin/env python3
"""
주문 근거 검증(갈래 ①) — 주문·보유 판단의 근거를 **주문 전에 판정표로 검증**하고, 감사는 **주문 뒤에** 받아 결정을 뒤집는 결과만 소급한다.

★ 2026-10-13 17:30 개정(사용자): "감사 완료하고 매매하는 건 너무 오래 걸린다 — 매매 관련 팩트체크는 배경보다 빡빡하게 감사하되,
매매하고 나서 감사를 마무리하는 이전 구조로 · 7차는 길고 5차까지 · 매매 의사결정을 바꿔야 하는 감사 결과가 나오면 소급 적용."
(그날 국내 run: 감사 두 번이 배분 설명의 숫자만 짚는 사이 5시간이 지나 시그널이 302분 묵어 전부 거부됐다.) 그래서
decide --mode pre(5단 · 감사 없이 판정표 기준) → 집행 → 감사(최대 5회) → decide --mode post(재채점된 표로 다시 계산 → 체결된
매수의 근거가 무너졌으면 `reversals.py` 원장에 되돌림 매도). 아래는 처음 판(같은 날 낮)의 사유다.

왜(2026-10-13 사용자): fact-check를 나눈다 — "주문·보유 조건 근거 fact-check는 주문 전에 무조건 pass가 떠야 한다.
7차까지 고치고 안 되면 넘어간다." 지금까지 감사는 주문 **뒤에** 돌았고(02:41 주문 → 02:49 감사), 시그널의 근거 행은 모델이 스스로
'MATCH'를 적었다 — VST 근거 한 행은 `https://www.ferc.gov/` 루트 주소만으로 T1·MATCH였고, 매수 근거 6행 중 그 run에서 실제로
검증된 것은 2행이었다. 이 도구는 근거를 뽑아 같은 fact-check 절차에 태우고, **판정표의 실제 판정을 시그널에 덮어쓴다**.

흐름(4단 · 시그널 초안 뒤 · risk_guard 전):
    python3 fc_track1.py extract --stamp 261013 --market us
        → analysis/AI클레임_<st>_<mkt>_주문근거.md (고정 목록 · fact-check 그룹이 이 원 문구로 판정)
          + .json(클레임 번호 → 제안 근거·보유 판단·배분 사실 대응)
    (fact-check — 배경 에이전트(2026-10-14): 조달 → 판정표 analysis/AI클레임_<st>_<mkt>_주문근거_판정.md → lint_verdict →
     corrections 블록 → fc_propagate plan → 교정 0이면 verify --write · 게이트 ② · 감사 발사까지 · 교정이 있으면 본 세션이
     risk_guard 직전에 apply·verify 뒤 게이트 ②·감사 발사. 주문은 감사를 기다리지 않고, run을 닫기 전에 감사 결과를 기다린다)
    python3 fc_track1.py decide --mode pre  --stamp 261013 --market us    # 5단 · 감사 없이 판정표 기준
        → 시그널 근거 행에 판정표의 판정·등급·행 번호·검증 주소를 쓰고 `track1` 블록(audit=PENDING)을 남긴다(risk_guard가 읽는다)
    python3 fc_track1.py decide --mode post --stamp 261013 --market us    # 6단 · 감사 기록이 생길 때마다(FAIL 뒤 재채점 · PASS)
        → 재채점된 표로 통과/제외를 다시 계산 · 체결된 매수가 이제 '제외'면 되돌림 매도 원장(reversals.py)에 행 · `TRACK1 POST` 줄
    python3 fc_track1.py reverse --stamp … --market … --ticker T --qty N --why "…"   # 보유 판단 재판정 SELL(모델이 판단)
    python3 fc_track1.py pending --market kr      # 대기 중인 되돌림(이어받기 절과 같은 표)
    python3 fc_track1.py settle  --stamp … --market …   # trades의 되돌림 매도 체결로 원장을 닫는다(fill이 이미 하므로 보조)

대상: ① 제안(BUY·LLM SELL)의 evidence 행 ② 보유 판단(holds[].why)에 숫자·출처가 있는 것 ③ 배분 판단(allocation.regime ·
cash_reason)의 외부 사실 문장(도구 캡처 숫자만인 문장은 제외 — 캡처가 출처다). 가격 조건(손절·추적)은 근거 문장이 없어 대상 밖.
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
KST = timezone(timedelta(hours=9))
FC_SCRIPTS = HERE.parent / ".claude" / "skills" / "fact-check" / "scripts"
LEDGER = HERE.resolve().parent / ".claude" / "skills" / "_stepgate" / "promotion_ledger.jsonl"
MAX_ROUNDS_DEFAULT = 5                    # 2026-10-13 17:30 — 7 → 5(사용자)
TOOL_ONLY = re.compile(r"(tools_\d{6}_\w+\.md|〈국면 재료〉|base_rates_|levels_|exposure_|intraday_touch_|기준일 \d{8})")
EXTERNAL = re.compile(r"(R\d-\d+|https?://|\b(?:VIX|HYG|Kalshi|CME|FedWatch|Treasury|EIA|BLS|SEC|8-K|10-Q|IR)\b|"
                      r"보도|발표|according|reported|\bper\b)", re.I)
NUM = re.compile(r"\d")


def paths(st: str, mk: str) -> dict:
    a = HERE / "analysis"
    return {"signal": HERE / "signals" / f"signal_{st}_{mk}.json",
            "claims": a / f"AI클레임_{st}_{mk}_주문근거.md",
            "map": a / f"AI클레임_{st}_{mk}_주문근거.json",
            "verdict": a / f"AI클레임_{st}_{mk}_주문근거_판정.md"}


def _sentences(text: str) -> list:
    return [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z가-힣(])", str(text or "")) if s.strip()]


def collect(sig: dict) -> list:
    """시그널 → 검증 대상 [{kind, ref, text, url}]."""
    items = []
    # 재투자 후보(refill_candidates · 2026-10-15)도 제안과 같은 검증을 받는다 — 재투자 pass가 그 근거로 산다
    for p in (sig.get("proposals") or []) + (sig.get("refill_candidates") or []):
        if p.get("action") not in ("BUY", "SELL") or p.get("source") == "discipline":
            continue
        for i, e in enumerate(p.get("evidence") or []):
            claim = str(e.get("claim") or "").strip()
            if claim:
                items.append({"kind": "proposal", "ref": f"{p['action']} {p['ticker']} E{i + 1}",
                              "ticker": p["ticker"], "action": p["action"], "idx": i,
                              "text": claim, "url": e.get("source_url") or ""})
    for h in sig.get("holds") or []:
        why = str(h.get("why") or "").strip()
        if why and NUM.search(why) and (EXTERNAL.search(why) or re.search(r"\$|%|bbl|GW|MW", why)):
            items.append({"kind": "hold", "ref": f"HOLD {h.get('ticker')}", "ticker": h.get("ticker"),
                          "text": why, "url": ""})
    al = sig.get("allocation") or {}
    for field in ("regime", "cash_reason"):
        for k, s in enumerate(_sentences(al.get(field))):
            if NUM.search(s) and EXTERNAL.search(s) and not (TOOL_ONLY.search(s) and not re.search(r"R\d-\d+|https?://", s)):
                items.append({"kind": "allocation", "ref": f"ALLOC {field} S{k + 1}", "text": s, "url": ""})
    return items


def _same(a: dict, b: dict) -> bool:
    return all(a.get(k) == b.get(k) for k in ("kind", "ref", "ticker", "action", "idx", "text"))


def cmd_extract_append(st: str, mk: str, P: dict, sig: dict) -> int:
    """`extract --append`(2026-10-15) — 고정본을 다시 쓰지 않고 **새 번호로 덧붙인다**.
    시그널 근거 문구를 고쳤거나(교정 반영) `refill_candidates`를 늦게 더했을 때. 같은 자리(종목·행동·근거 번호)의 문구가 바뀌었으면
    옛 번호에 `superseded_by`를 남기고, 판정(`decide`)은 그 자리의 가장 새 번호를 쓴다. 옛 번호의 판정 행은 그대로 남는다(원 문구 기록)."""
    try:
        mp = json.loads(P["map"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print(f"고정본이 없다: {P['map'].name} — 처음이면 --append 없이 extract", file=sys.stderr)
        return 2
    old = mp.get("items") or []
    now = datetime.now(KST)
    nxt = max((int(x.get("n") or 0) for x in old), default=0)
    added = []
    for it in collect(sig):
        if any(_same(it, o) for o in old):
            continue
        nxt += 1
        it["n"] = nxt
        for o in old:
            if not o.get("superseded_by") and all(o.get(k) == it.get(k) for k in ("kind", "ticker", "action", "idx")) \
                    and o.get("kind") == "proposal":
                o["superseded_by"] = nxt
                it["supersedes"] = o.get("n")
        added.append(it)
    if not added:
        print("덧붙일 새 근거 없음 — 고정본 그대로")
        return 0
    mp["items"] = old + added
    mp.setdefault("appended", []).append({"at": now.isoformat(), "n": [x["n"] for x in added]})
    P["map"].write_text(json.dumps(mp, ensure_ascii=False, indent=1), encoding="utf-8")
    lines = ["", f"## 덧붙임 {now:%Y-%m-%d %H:%M} KST (`extract --append`) — 앞 번호는 그대로 판정 기준으로 남는다", ""]
    for it in added:
        url = f" — 출처: {it['url']}" if it["url"] else ""
        sup = f" (클레임 {it['supersedes']}를 대신한다)" if it.get("supersedes") else ""
        lines.append(f"{it['n']}. [{it['ref']}] {it['text']}{url}{sup}")
    with P["claims"].open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"주문 근거 {len(added)}건 덧붙임(번호 {added[0]['n']}~{added[-1]['n']}) → {P['claims'].relative_to(HERE)} — 판정표에 이 번호의 행을 더한다")
    return 0


def cmd_extract(st: str, mk: str, append: bool = False) -> int:
    P = paths(st, mk)
    if not P["signal"].exists():
        print(f"시그널 없음: {P['signal'].name}", file=sys.stderr)
        return 2
    sig = json.loads(P["signal"].read_text(encoding="utf-8"))
    if append:
        return cmd_extract_append(st, mk, P, sig)
    # ★ 판정이 시작된 고정본은 다시 뽑지 않는다(2026-10-15) — 10-14 미국 run은 교정 5건 뒤 고정본을 두 번 다시 뽑아 교정 뒤 문구를
    #   판정했고, 감사가 grading-basis로 세 번 FAIL을 줬다(원 판정 MISMATCH 4건이 'MISMATCH 0건'으로 사라졌다).
    if P["claims"].exists() and P["verdict"].exists():
        print(f"고정본({P['claims'].name})과 판정표({P['verdict'].name})가 이미 있다 — 다시 뽑으면 교정 뒤 문구를 판정하게 된다. "
              f"근거 문구를 바꿨거나 후보를 더했으면 `fc_track1.py extract --append --stamp {st} --market {mk}`로 새 번호를 덧붙여라",
              file=sys.stderr)
        return 3
    items = collect(sig)
    now = datetime.now(KST)
    lines = [f"# AI 클레임 — 주문·보유 판단 근거 {st} {mk.upper()} (갈래 ①)", "",
             f"출처: `signals/{P['signal'].name}` · 총 {len(items)}건",
             f"고정: {now:%Y-%m-%d %H:%M} KST · 이 목록이 주문 근거 fact-check의 판정 기준(원 문구)이다 — 시그널을 고친 뒤 다시 만들지 않는다",
             "판정이 MATCH(T1/T2)가 아닌 근거는 주문에 쓰지 못한다 · 감사 PASS가 나야 주문이 나간다(최대 7회)", ""]
    for n, it in enumerate(items, 1):
        it["n"] = n
        url = f" — 출처: {it['url']}" if it["url"] else ""
        lines.append(f"{n}. [{it['ref']}] {it['text']}{url}")
    P["claims"].write_text("\n".join(lines) + "\n", encoding="utf-8")
    P["map"].write_text(json.dumps({"stamp": st, "market": mk, "at": now.isoformat(), "items": items},
                                   ensure_ascii=False, indent=1), encoding="utf-8")
    kinds = {k: sum(1 for x in items if x["kind"] == k) for k in ("proposal", "hold", "allocation")}
    print(f"주문 근거 {len(items)}건 고정 — 제안 근거 {kinds['proposal']} · 보유 판단 {kinds['hold']} · 배분 사실 {kinds['allocation']}"
          f" → {P['claims'].relative_to(HERE)}")
    return 0


def _series(name: str) -> str:
    return re.sub(r"_v\d+_\d+(?=\.md$)", "", name).lower()


def audit_rounds(verdict_name: str, ledger: Path = LEDGER) -> list:
    """이 판정표 계열의 감사 기록(시간순) [(ts, status, findings)]."""
    want = _series(verdict_name)
    out = []
    try:
        for ln in ledger.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            # 대체된 감사(같은 판정표가 다시 judge된 뒤 도착한 결과 · 2026-10-15)는 회차로 세지 않는다
            if r.get("type") == "finding" and r.get("skill") == "fact-check" and _series(str(r.get("run", ""))) == want \
                    and not r.get("superseded"):
                out.append((r.get("ts", ""), r.get("status"), r.get("findings", "")))
    except OSError:
        pass
    return sorted(out)


def verdict_rows(verdict: Path, claims: Path) -> dict:
    """판정표 → {클레임 번호: [(줄, 판정, 티어, URL)]} — lint_verdict의 표 해석을 그대로 쓴다."""
    sys.path.insert(0, str(FC_SCRIPTS))
    import lint_verdict as lv
    rows, _, _ = lv.parse_tables(verdict.read_text(encoding="utf-8").split("\n"))
    out = {}
    for r in rows:
        v = lv.verdict_of(lv.cell(r, "verdict"))
        _, loc = lv.excerpt_and_loc(lv.cell(r, "orig"))
        if not v or not loc or loc[0] != "claim":
            continue
        mt = re.match(r"\s*\**\s*(T[123])\b", lv.cell(r, "tier"))
        mu = lv.URL_RE.search(lv.cell(r, "url"))
        out.setdefault(loc[1], []).append((r["line"], v, mt.group(1) if mt else "", mu.group(0).rstrip(".,;)") if mu else ""))
    return out


def _grade(sig: dict, items: dict, claim_ok: dict, verdict_name: str) -> tuple:
    """판정표 기준 판정을 시그널(사본일 수 있다)의 근거 행에 쓰고 (통과, 제외, 보유 미검증, 배분 미검증)을 돌려준다."""
    dropped, passed = set(), set()
    # 재투자 후보(refill_candidates · 2026-10-15)도 제안과 같은 검증을 받는다 — 재투자 pass가 그 근거로 산다
    for p in (sig.get("proposals") or []) + (sig.get("refill_candidates") or []):
        if p.get("action") not in ("BUY", "SELL") or p.get("source") == "discipline":
            continue
        for i, e in enumerate(p.get("evidence") or []):
            # 같은 자리에 `--append`로 덧붙인 새 번호가 있으면 가장 새 번호(대신한 옛 번호는 superseded_by가 있다)
            n = max((k for k, it in items.items() if it["kind"] == "proposal" and it["ticker"] == p["ticker"]
                     and it["action"] == p["action"] and it.get("idx") == i and not it.get("superseded_by")), default=None)
            if n is None:
                e["verdict"], e["track1"] = "UNVERIFIED_T1", "근거 목록에 없다(extract 뒤 추가된 행)"
                continue
            ok, rs = claim_ok[n]
            if rs:
                worst = next((v for v in ("MISMATCH", "UNVERIFIABLE", "STALE") if any(v == x for _, x, _, _ in rs)), "MATCH")
                e["verdict"] = worst if ok or worst != "MATCH" else "UNVERIFIED_T1"
                e["tier"] = next((t for _, _, t, _ in rs if t in ("T1", "T2")), rs[0][2] or e.get("tier"))
                # 근거 주소도 판정표 행의 것으로 — 시그널에 모델이 적은 주소(10-13 VST: 도메인 루트)가 아니라 검증한 주소가 남게
                vurl = next((u for _, _, t, u in rs if u and t in ("T1", "T2")), "") or next((u for *_, u in rs if u), "")
                if vurl and vurl != e.get("source_url"):
                    e["source_url_signal"], e["source_url"] = e.get("source_url") or "", vurl
            else:
                e["verdict"] = "UNVERIFIED_T1"
            e["fc_row"] = [f"{verdict_name}:L{ln}" for ln, *_ in rs]
            e["track1"] = "PASS" if ok else "FAIL"
        if p.get("action") == "BUY":
            ev = p.get("evidence") or []
            if not any(e.get("track1") == "PASS" and e.get("verdict") == "MATCH" and e.get("tier") in ("T1", "T2") for e in ev) \
                    or any(e.get("verdict") in ("UNVERIFIED_T1", "MISMATCH") for e in ev):
                dropped.add(p["ticker"])
            else:
                passed.add(p["ticker"])
    holds_bad = []
    for h in sig.get("holds") or []:
        n = next((k for k, it in items.items() if it["kind"] == "hold" and it["ticker"] == h.get("ticker")), None)
        if n is not None:
            h["track1"] = "PASS" if claim_ok[n][0] else "FAIL"
            if not claim_ok[n][0]:
                holds_bad.append(h.get("ticker"))
    alloc_bad = [items[n]["ref"] for n in items if items[n]["kind"] == "allocation" and not claim_ok[n][0]]
    return passed, dropped, holds_bad, alloc_bad


def filled_buys(st: str, mk: str) -> dict:
    """이 run의 승인 파일(`approved_<st>_<mkt>.json`)로 체결된 매수 {종목: 체결 수량} — 자정을 넘긴 미국 run도 찾게 trades 전부를 본다."""
    out = {}
    want = f"approved_{st}_{mk}.json"
    for tp in sorted((HERE / "journal").glob(f"trades_*_{mk}.json")):
        try:
            rec = json.loads(tp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for t in rec.get("trades") or []:
            if t.get("action") != "BUY" or t.get("status") == "FAILED" or not str(t.get("approved_ref", "")).endswith(want):
                continue
            f = t.get("fill") or {}
            v = f.get("verdict")
            if v not in ("FILLED", "PARTIAL"):
                continue
            q = int(f.get("filled_qty") or abs(int(f.get("delta") or 0)) or (t.get("qty") if v == "FILLED" else 0) or 0)
            if q > 0:
                out[t.get("ticker")] = out.get(t.get("ticker"), 0) + q
    return out


def _market_open(mk: str) -> tuple:
    try:
        from kis_client import market_session
        sess = market_session(mk.upper())
        return bool(sess.get("is_open")), str(sess.get("why") or "")
    except Exception as e:                       # noqa: BLE001
        return False, f"장 상태를 못 읽었다({type(e).__name__}) — 닫힌 것으로 본다"


def cmd_decide(st: str, mk: str, max_rounds: int, ledger: Path = LEDGER, mode: str = "pre", jdir=None) -> int:
    P = paths(st, mk)
    for k in ("signal", "map", "verdict"):
        if not P[k].exists():
            print(f"없음: {P[k].name} — extract와 fact-check 판정표가 먼저다", file=sys.stderr)
            return 2
    vtxt = P["verdict"].read_text(encoding="utf-8")
    miss = [k for k, rx in (("LINT OK", r"^LINT OK\b"), ("PROPAGATE OK", r"^PROPAGATE OK\b")) if not re.search(rx, vtxt, re.M)]
    if miss:
        # 노트 게이트가 묻던 'MISMATCH 0 · 교정 전파'를 기계 줄로 — lint_verdict --write · fc_propagate verify --write가 쓴다
        print(f"아직 결정할 수 없다 — 판정표에 {' · '.join(miss)} 줄이 없다(lint_verdict.py --write · fc_propagate.py verify --write를 "
              f"먼저 통과시켜라)", file=sys.stderr)
        return 1
    mp = json.loads(P["map"].read_text(encoding="utf-8"))
    items = {it["n"]: it for it in mp["items"]}
    rounds = audit_rounds(P["verdict"].name, ledger)
    n_fail = sum(1 for _, s_, _ in rounds if s_ == "FAIL")
    last = rounds[-1][1] if rounds else None
    if mode == "post" and not rounds:
        print("아직 감사 기록이 없다 — 감사자가 audit-judge --record로 남긴 뒤에 post를 돌린다", file=sys.stderr)
        return 1
    # ★ 판정은 판정표만으로 한다(2026-10-13 17:30) — 감사 결과는 표를 재채점하는 입력이고, 결정은 재채점된 표가 한다.
    vr = verdict_rows(P["verdict"], P["claims"])
    claim_ok = {}
    for n in items:
        rs = vr.get(n) or []
        ok = bool(rs) and all(v == "MATCH" for _, v, _, _ in rs) and any(t in ("T1", "T2") for _, _, t, _ in rs)
        claim_ok[n] = (ok, rs)
    sig = json.loads(P["signal"].read_text(encoding="utf-8"))
    now = datetime.now(KST)
    if mode == "pre":
        passed, dropped, holds_bad, alloc_bad = _grade(sig, items, claim_ok, P["verdict"].name)
        sig["track1"] = {"result": "PASS" if not dropped else ("DROP" if not passed else "PARTIAL"),
                         "audit": "PENDING", "rounds": 0, "pass": sorted(passed), "drop": sorted(dropped),
                         "holds_unverified": holds_bad, "allocation_unverified": alloc_bad,
                         "ref": f"analysis/{P['verdict'].name}", "at": now.isoformat(timespec="seconds")}
        P["signal"].write_text(json.dumps(sig, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        line = (f"TRACK1 RESULT: audit=PENDING(집행 뒤 · 최대 {max_rounds}회) pass={','.join(sorted(passed)) or '-'} "
                f"drop={','.join(sorted(dropped)) or '-'} holds_unverified={','.join(map(str, holds_bad)) or '-'} "
                f"allocation_unverified={len(alloc_bad)} ({now:%Y-%m-%d %H:%M})")
        vt = re.sub(r"^TRACK1 RESULT:.*$\n?", "", P["verdict"].read_text(encoding="utf-8"), flags=re.M)
        P["verdict"].write_text(vt.rstrip("\n") + "\n\n" + line + "\n", encoding="utf-8")
        print(line)
        if dropped:
            print(f"  제외 — {', '.join(sorted(dropped))}: 판정표에서 근거가 서지 않았다(논지는 armed 유지 · 다음 run이 다시 본다)")
        return 0

    # ── post: 집행 뒤 감사 기록이 생길 때마다 — 재채점된 표로 다시 계산하고, 체결된 매수의 근거가 무너졌으면 되돌린다
    import copy
    import reversals as rv
    t1 = sig.get("track1") or {}
    pre_pass = set(t1.get("pass") or [])
    work = copy.deepcopy(sig)                     # 시그널의 근거 판정은 주문 당시 기록으로 남긴다 — 재채점 결과는 post 칸에
    passed, dropped, holds_bad, alloc_bad = _grade(work, items, claim_ok, P["verdict"].name)
    filled = filled_buys(st, mk)
    audit = "PASS" if last == "PASS" else f"FAIL×{n_fail}"
    over = n_fail > max_rounds
    rev_ids = list((t1.get("post") or {}).get("reversals") or [])
    targets = sorted(t for t in pre_pass & dropped if filled.get(t, 0) > 0)
    for tic in targets:
        prop = next((p for p in work.get("proposals") or [] if p.get("ticker") == tic and p.get("action") == "BUY"), {})
        bad = [e for e in prop.get("evidence") or [] if e.get("verdict") != "MATCH" or e.get("track1") != "PASS"]
        why = (f"감사 {len(rounds)}회차({audit}) 뒤 재채점 — 근거 " +
               "; ".join(f"{str(e.get('claim') or '')[:60]} → {e.get('verdict')}" for e in bad[:3]))
        rows_ = [r for e in bad for r in (e.get("fc_row") or [])]
        row, new = rv.add(st, mk.upper(), tic, filled[tic], why, kind="buy_unsupported", name=prop.get("name", ""),
                          round_=len(rounds), fc_rows=rows_, buy_ref=f"approved_{st}_{mk}.json",
                          thesis_id=prop.get("thesis_id") or "", jdir=jdir)
        if row["id"] not in rev_ids:
            rev_ids.append(row["id"])
        print(f"  ★ 감사 소급 {row['id']} {tic} {filled[tic]}주 — {'새로 기록' if new else '이미 기록됨'} · {why[:120]}")
    sig["track1"] = {**t1, "audit": audit, "rounds": len(rounds),
                     "post": {"audit": audit, "rounds": len(rounds), "pass": sorted(passed), "drop": sorted(dropped),
                              "reversals": rev_ids, "holds_unverified": holds_bad, "allocation_unverified": alloc_bad,
                              "over_limit": over, "at": now.isoformat(timespec="seconds")}}
    P["signal"].write_text(json.dumps(sig, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    line = (f"TRACK1 POST: audit={audit} rounds={len(rounds)}/{max_rounds} pass={','.join(sorted(passed)) or '-'} "
            f"drop={','.join(sorted(dropped)) or '-'} reversals={','.join(rev_ids) or '-'} "
            f"holds_unverified={','.join(map(str, holds_bad)) or '-'} allocation_unverified={len(alloc_bad)} ({now:%Y-%m-%d %H:%M})")
    vt = re.sub(r"^TRACK1 POST:.*$\n?", "", P["verdict"].read_text(encoding="utf-8"), flags=re.M)
    P["verdict"].write_text(vt.rstrip("\n") + "\n" + line + "\n", encoding="utf-8")
    print(line)
    if over:
        print(f"  ※ 감사 FAIL {n_fail}회 — 한도 {max_rounds}회를 넘겼다. 더 고치지 않는다(기록만 · 토요일 승격)")
    if targets:
        is_open, why_s = _market_open(mk)
        if is_open:
            print(f"  → 장중이다 — 지금 `python3 stops.py --market {mk} --send`로 되돌림 매도를 보낸다(같은 run · 사람 확인 없음)")
        else:
            print(f"  → 장이 닫혔다({why_s}) — 대기로 남긴다. 다음 {mk.upper()} run의 risk_guard가 자동으로 판다")
    for h in holds_bad:
        print(f"  ※ 보유 판단 재판정 필요: {h} — 근거가 무너졌으면 `fc_track1.py reverse --ticker {h} --qty N --why …`로 판다")
    return 0


def unreflected(mk: str, before: str, lookback: int = 3, ledger: Path = LEDGER, adir: Path = None) -> list:
    """같은 시장 직전 run 가운데 **감사 기록 수가 `TRACK1 POST`의 rounds보다 많은** 스탬프 — [(스탬프, 감사 수, 반영 수)].
    배경 감사가 run을 닫은 뒤에 도착하면(미수신으로 닫음) 다음 run 이어받기가 이것으로 받아 `decide --mode post`를 돌린다(2026-10-14)."""
    adir = adir or (HERE / "analysis")
    stamps = sorted({m.group(1) for v in adir.glob(f"AI클레임_*_{mk.lower()}_주문근거_판정.md")
                     for m in [re.match(r"AI클레임_(\d{6})_", v.name)] if m})
    out = []
    for s in [x for x in stamps if x < before][-lookback:]:
        vp = adir / f"AI클레임_{s}_{mk.lower()}_주문근거_판정.md"
        n = len(audit_rounds(vp.name, ledger))
        if not n:
            continue
        m = re.search(r"^TRACK1 POST:.*?rounds=(\d+)/", vp.read_text(encoding="utf-8"), re.M)
        done = int(m.group(1)) if m else 0
        if done < n:
            out.append((s, n, done))
    return out


def carry_note(mk: str, before: str, **kw) -> str:
    """이어받기 `## 감사 소급` 절 뒤에 붙는 줄 — 반영 안 된 주문 근거 감사가 있으면 이 run에서 먼저 돌린다."""
    rows = unreflected(mk, before, **kw)
    if not rows:
        return ""
    return ("\n**① 감사 결과 미반영** — 직전 run이 닫힌 뒤 도착한 주문 근거 감사가 있다. 이 run에서 먼저 돌린다"
            "(재채점이 체결 매수의 근거를 뒤집으면 소급 매도 · 사람 확인 없음):\n"
            + "\n".join(f"- `python3 fc_track1.py decide --mode post --stamp {s} --market {mk.lower()}` — 감사 {n}회 · 반영 {d}회"
                        for s, n, d in rows) + "\n")


def cmd_reverse(st: str, mk: str, ticker: str, qty: int, why: str, jdir=None) -> int:
    import reversals as rv
    if not ticker or qty <= 0 or not why.strip():
        print("reverse는 --ticker · --qty(>0) · --why가 필요하다", file=sys.stderr)
        return 2
    row, new = rv.add(st, mk.upper(), ticker, qty, why, kind="hold_overturned", jdir=jdir)
    print(f"{'기록' if new else '이미 있음'}: {row['id']} {ticker} {row['qty']}주 — {why[:100]}")
    return 0


def cmd_settle(st: str, mk: str, jdir=None) -> int:
    import reversals as rv
    n = 0
    for tp in sorted((HERE / "journal").glob(f"trades_*_{mk}.json")):
        try:
            rec = json.loads(tp.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for t in rec.get("trades") or []:
            if t.get("source") == rv.SOURCE:
                rv.settle_trade(t, jdir)
                n += 1
    print(f"되돌림 매도 행 {n}개 대조 · 대기 {len(rv.pending(mk, jdir))}건")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["extract", "decide", "reverse", "pending", "settle"])
    ap.add_argument("--stamp", default="")
    ap.add_argument("--market", required=True, choices=["kr", "us"])
    ap.add_argument("--mode", choices=["pre", "post"], default="pre", help="decide: pre=5단(감사 없이) · post=집행 뒤 감사마다")
    ap.add_argument("--ticker", default="")
    ap.add_argument("--qty", type=int, default=0)
    ap.add_argument("--why", default="")
    ap.add_argument("--append", action="store_true", help="extract: 고정본을 다시 쓰지 않고 새 근거를 새 번호로 덧붙인다(2026-10-15)")
    a = ap.parse_args(argv)
    if a.cmd == "pending":
        import reversals as rv
        print(rv.carry_block(a.market))
        return 0
    if not a.stamp:
        print("--stamp가 필요하다", file=sys.stderr)
        return 2
    if a.cmd == "extract":
        return cmd_extract(a.stamp, a.market, append=a.append)
    if a.cmd == "reverse":
        return cmd_reverse(a.stamp, a.market, a.ticker, a.qty, a.why)
    if a.cmd == "settle":
        return cmd_settle(a.stamp, a.market)
    try:
        lim = json.loads((HERE / "config" / "limits.json").read_text(encoding="utf-8"))
        mr = int(lim.get("fc_track1_max_rounds") or MAX_ROUNDS_DEFAULT)
    except (OSError, json.JSONDecodeError, ValueError):
        mr = MAX_ROUNDS_DEFAULT
    return cmd_decide(a.stamp, a.market, mr, mode=a.mode)


if __name__ == "__main__":
    sys.exit(main())
