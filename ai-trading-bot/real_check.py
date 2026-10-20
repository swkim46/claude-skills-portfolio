#!/usr/bin/env python3
"""
실전 점검표 — 모의 run 하나가 **실계좌였다면 문제없이 돌았을까**를 파일에서 기계로 센다(12항목).

왜(2026-10-15 사용자): "실전으로 돌리기 전에 모의 투자에서 최대한 테스트하고 문제는 토요일 trade-feedback으로 고치고
다음 주 월요일 run부터 실전으로." → run마다 이 표를 `data/run_evidence/real_check_<st>_<mkt>.md`에 남기고 보고의 〈계좌 현황〉 바로 앞에
그대로 붙인다(기억으로 쓰지 않는다). 토요일 피드백이 그 주의 표를 모아 실전 준비를 판정한다. 목표에 못 미친 줄은 ★로 맨 앞에 온다.

    python3 real_check.py --stamp 261015 --market kr          # 표를 쓰고 출력한다 — `stage.py close`가 부른다

항목: ① 실계좌라면 샀을까(그림자 점검) ② 매도 뒤 재투자 ③ 시각 ④ 감사 ⑤ 체결 ⑥ 손절 오차 ⑦ 실계좌 조회 전용 점검
⑧ 날짜 규칙 ⑨ 테스트 ⑩ 보고 전달(푸시) ⑪ 이어받기 새 절 ⑫ 전환 예행.
판정 값: ✓(목표 충족) · ★(못 미침 — 맨 앞) · —(해당 없음·아직 없음).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
EVID = HERE / "data" / "run_evidence"
JOURNAL = HERE / "journal"
SIGNALS = HERE / "signals"
ANALYSIS = HERE / "analysis"
LEDGER = HERE.parent / ".claude" / "skills" / "_stepgate" / "promotion_ledger.jsonl"
sys.path.insert(0, str(HERE))
import db  # noqa: E402 — 재투자 대기·논지·포인터·자산 곡선·테스트·푸시 기록은 저장소에서 읽는다


def _read(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _dt(s):
    try:
        d = datetime.fromisoformat(str(s))
        return d if d.tzinfo else d.replace(tzinfo=KST)
    except (TypeError, ValueError):
        return None


# ── 항목들 — 각 함수는 (판정 ✓|★|—, 결과 문장) ─────────────────────────────

def c1_shadow(st, mk):
    t = _text(EVID / f"real_shadow_{st}_{mk}.md")
    if not t:
        return "—", "그림자 점검 파일 없음(`journal --daily`가 모의 시그널마다 돈다 — 시그널이 없었거나 아직 저널 전)"
    blk = t.split("## 그림자 점검")[-1]
    m = re.search(r"승인 매수 (\d+)건 · 상한 초과 (\d+)건 · 미달 (예|아니오)", blk)
    zero = len(re.findall(r"매수 가능 수량 0주", blk))
    floor = len(re.findall(r"상승 여지 바닥 미달", blk))
    capped = len(re.findall(r"주문당 상한", blk))
    whole = re.search(r"거부\(전체\)|REJECT ALL|change\.why", blk)
    if not m:
        return "★", "그림자 점검 결과 줄을 못 읽었다" + (" — 시그널 전체 거부" if whole else "")
    buys, short = int(m.group(1)), m.group(3) == "예"
    ok = buys >= 1 and zero == 0
    return ("✓" if ok else "★"), (f"승인 매수 {buys}건 · 0주 거부 {zero} · 세후 바닥 거부 {floor} · 상한 언급 {capped} · "
                                   f"미달 {'예' if short else '아니오'}")


def _filled(t) -> int:
    from stops import filled_of                          # 체결 확정 형식 둘(filled_qty · delta)을 같이 읽는다(2026-10-15)
    return filled_of(t)[0]


def c2_refill(st, mk):
    d = db.refill_get(st, mk)                            # 재투자 대기(ex_refill)
    refills = sorted(SIGNALS.glob(f"approved_{st}_{mk}_refill_*.json"))
    # 매수 단계 뒤 매도 전용 pass가 체결한 매도가 있었나(재투자 기록이 생기기 전 run도 잡는다)
    tr = _read(JOURNAL / f"trades_{st}_{mk}.json", {}) or {}
    late = [t for t in tr.get("trades") or [] if t.get("action") == "SELL"
            and re.search(r"_(stops|daytime)_", str(t.get("approved_ref") or ""))
            and _filled(t) > 0]
    if d is None:
        if late:
            return "★", f"매도 전용 pass 매도 {len(late)}건이 체결됐는데 재투자 기록이 없다(재투자 기록 도입 전 코드?)"
        return "—", "이 run 안에서 매수 뒤 나간 매도 없음"
    st_ = d.get("status")
    proceeds = sum(float(e.get("proceeds") or 0) for e in d.get("events") or [])
    msg = f"상태 {st_} · 판 돈 {proceeds:,.2f} · 재투자 승인 {len(refills)}건" + (f" · 미룬 사유 {d.get('deferred_why')}" if d.get("deferred_why") else "")
    if d.get("spent") is not None:
        msg += f" · 재투자 {float(d['spent']):,.2f}(" + ", ".join(f"{x.get('ticker')} {x.get('qty')}주" for x in d.get("bought") or []) + ")"
    return ("★" if st_ == "open" else "✓"), msg


def c3_timing(st, mk):
    from kis_client import market_session
    wl = _text(JOURNAL / f"worklog_{st}_{mk}.md")
    mcap = re.search(r"written 작업기록-capture (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", wl)
    ap = _read(SIGNALS / f"approved_{st}_{mk}.json", {}) or {}
    sig = _read(SIGNALS / f"signal_{st}_{mk}.json", {}) or {}
    tr = _read(JOURNAL / f"trades_{st}_{mk}.json", {}) or {}
    main = [t for t in tr.get("trades") or [] if str(t.get("approved_ref") or "").split("/")[-1] == f"approved_{st}_{mk}.json"]
    if not ap or not main:
        return "—", "이 run의 주 승인·전송 없음(기권 또는 아직 전)"
    sent = min((_dt(t.get("ts")) for t in main if _dt(t.get("ts"))), default=None)
    cap = _dt(mcap.group(1) + ":00+09:00") if mcap else None
    app_at, sig_at = _dt(ap.get("generated_at")), _dt(sig.get("generated_at"))
    parts, bad = [], []
    if cap and sent:
        mins = (sent - cap).total_seconds() / 60
        parts.append(f"수집→전송 {mins:.0f}분")
        if mins > 90:
            bad.append("90분 초과")
    if app_at and sent:
        age = (sent - app_at).total_seconds() / 60
        parts.append(f"승인 나이 {age:.0f}분")
        if age > 30:
            bad.append("승인 30분 초과")
    if sig_at and app_at:
        sa = (app_at - sig_at).total_seconds() / 60
        parts.append(f"시그널 나이 {sa:.0f}분")
        if sa > 180:
            bad.append("시그널 180분 초과")
    if sent:
        ok_s = market_session(mk.upper(), sent)["is_open"]
        parts.append(f"전송 {sent:%H:%M} {'장중' if ok_s else '장외'}")
        if not ok_s:
            bad.append("장외 전송")
    return ("★" if bad else "✓"), " · ".join(parts) + (f" — {', '.join(bad)}" if bad else "")


def _series(name: str) -> str:
    return re.sub(r"_v\d+_\d+(?=\.md$)", "", name).lower()


def c4_audit(st, mk):
    # 감사 기록은 저장소에서 읽는다(v4 · Codex U03) — 원장을 먼저 복사하고(멱등) db.audit_rows 하나로 센다(다른 폴더 예행 감사 제외).
    try:
        db.audit_mirror(st, mk)
    except Exception as e:                       # noqa: BLE001 — 복사 실패면 이미 복사된 행으로 센다
        print(f"(감사 원장 복사 실패: {type(e).__name__}: {e})", file=sys.stderr)
    rows = []
    for row in db.audit_rows(db.run_id(st, mk), "fact-check"):
        r = row.get("body_obj") or {}
        run = str(r.get("run") or "").lower()
        if r.get("type") == "finding" and r.get("skill") == "fact-check" and f"ai클레임_{st}_{mk}" in run \
                and not r.get("superseded"):
            rows.append({**r, "_current_ok": bool(row.get("current_ok"))})     # 지금 판정표 판을 본 감사인가(W06)
    if not rows:
        return "—", "이 run의 감사 기록 없음(아직 안 왔거나 [AI] 0건)"
    by = {}
    for r in sorted(rows, key=lambda x: x.get("ts", "")):
        by.setdefault(_series(str(r.get("run"))), []).append(r)
    parts, bad = [], []
    for ser, rs in by.items():
        order = "주문근거" in ser
        n, last = len(rs), rs[-1].get("status")
        cur = [r for r in rs if r.get("_current_ok")]
        last_cur = cur[-1].get("status") if cur else None              # 지금 판정표 판의 감사 결과(W06) — 이력의 마지막 줄과 다를 수 있다
        first_fail = [r for r in rs if r.get("status") == "FAIL"][:1]
        lenses = ",".join((first_fail[0].get("lenses") or [])[:6]) if first_fail else ""
        tag = "①" if order else ("②-" + ser.split("_")[-2].upper() if re.search(r"_[ab]_판정", ser) else "②")
        parts.append(f"{tag} {n}회 · 마지막 {last}" + ("" if last_cur == last and cur and cur[-1] is rs[-1]
                                                         else f" · 지금 판 {last_cur or '감사 없음(다른 판을 본 감사)'}")
                     + (f" · 1차 FAIL lens {lenses}" if lenses else ""))
        if order and (last_cur != "PASS" or n > 2):
            bad.append(f"{tag} 2회 안 PASS 못 함" if last_cur == last else f"{tag} 지금 판정표 판의 PASS 없음")
        if not order and rs[0].get("status") != "PASS":
            bad.append(f"{tag} 1차 FAIL")
    pre = sorted(EVID.glob(f"preaudit_AI클레임_{st}_{mk}*"))
    parts.append(f"모의 감사 파일 {len(pre)}개")
    return ("★" if bad else "✓"), " · ".join(parts)


def c5_fills(st, mk):
    tr = _read(JOURNAL / f"trades_{st}_{mk}.json", {}) or {}
    if not tr.get("trades"):
        return "—", "전송 없음"
    oo = (tr.get("fill_confirmed") or {}).get("open_orders")
    chains = [len((t.get("fill") or {}).get("order_chain") or []) for t in tr["trades"]]
    re_n = sum(max(0, c - 1) for c in chains)
    over = [k for k, v in (tr.get("reconcile") or {}).items() if isinstance(v, dict) and v.get("over")] \
        if isinstance(tr.get("reconcile"), dict) else []
    inc = len(re.findall(r"INC-\d{6}", _text(JOURNAL / f"worklog_{st}_{mk}.md")))
    ok = oo == 0 and not over and not inc
    return ("✓" if ok else "★"), (f"미확정 {oo if oo is not None else '미기록'} · 재발주·추격 {re_n}회 · 승인 초과 {len(over)} · 사고 {inc}")


def c6_slip(st, mk):
    tr = _read(JOURNAL / f"trades_{st}_{mk}.json", {}) or {}
    th = {t.get("id"): t for t in db.theses_all()}
    slips = []
    for t in tr.get("trades") or []:
        if t.get("action") != "SELL" or t.get("source") not in ("discipline",):
            continue
        f = t.get("fill") or {}
        px = float(f.get("avg_price") or 0)
        approx = not px                                  # 체결가가 없는 확정(delta 형식)이면 지정가로 근사 — 표시한다
        px = px or float(f.get("limit_price") or 0)
        leg = next((g for g in (th.get(t.get("thesis_id")) or {}).get("exit_triggers") or []
                    if g.get("id") == t.get("trigger_id")), None)
        m = re.match(r"\s*price\s*<=\s*([\d.,]+)", str((leg or {}).get("check") or ""))
        if not (px and m):
            continue
        lvl = float(m.group(1).replace(",", ""))
        slips.append((t.get("ticker") + ("~" if approx else ""), (px - lvl) / lvl * 100))
    if not slips:
        return "—", "가격 손절 다리 체결 없음"
    avg = sum(s for _, s in slips) / len(slips)
    return ("✓" if avg >= -2.0 else "★"), (f"평균 {avg:+.2f}%p(손절선 대비 체결가) · " +
                                          ", ".join(f"{k} {v:+.2f}%p" for k, v in slips[:6]))


def c7_probe(st, mk):
    t = _text(EVID / f"real_probe_{st}.md")
    if not t:
        return "★", "실계좌 조회 전용 점검 파일 없음 — `python3 real_probe.py --stamp " + st + " --no-paper`"
    m = list(re.finditer(r"\*\*판정: ([^*]+)\*\*", t))
    v = m[-1].group(1) if m else "판정 줄 없음"
    return ("✓" if v.startswith("오류 0") else "★"), v


def c8_dates(st, mk):
    import sessions as ss
    cur = db.current_run_read() or {}                     # 현재 run 포인터(op_current_run)
    sess = ss.session_iso(st, mk)
    rows = db.equity_rows()
    mine = [r for r in rows if str(r.get("market", "")).upper() == mk.upper() and r.get("date") == sess]
    bad = []
    if cur.get("stamp") not in (st, None) and cur.get("market") == mk:
        bad.append(f"포인터 스탬프 {cur.get('stamp')} ≠ {st}")
    if not mine:
        bad.append(f"자산 곡선에 {sess} 행 없음")
    return ("★" if bad else "✓"), (f"스탬프 {st} = 세션 {sess} · 자산 곡선 그 날 행 {len(mine)}개" + (f" — {'; '.join(bad)}" if bad else ""))


def c9_tests(st, mk):
    rows = db.log_rows("test_runs")
    if not rows:
        return "★", "테스트 기록 없음"
    last = {}
    for r in rows:
        last[r.get("file")] = r
    bad = [f for f, r in last.items() if not r.get("completed") or r.get("failed")]
    newest = max((_dt(r.get("ts")) for r in last.values() if _dt(r.get("ts"))), default=None)
    age_d = (datetime.now(KST) - newest).days if newest else None
    msg = f"파일 {len(last)}개 마지막 결과 · 실패·멈춤 {len(bad)}" + (f" · 가장 최근 {age_d}일 전" if age_d is not None else "")
    return ("★" if bad or (age_d is not None and age_d > 7) else "✓"), msg + (f" — {', '.join(bad)}" if bad else "")


def c10_push(st, mk):
    rows = db.log_rows("push_log")
    mine = [r for r in rows if r.get("stamp") == st and r.get("market") == mk]
    if mine:
        sent = [r for r in mine if r.get("sent", True)]
        return ("✓" if sent else "★"), (f"푸시 {len(sent)}건 전송 · 실패 {len(mine) - len(sent)} · 마지막 {mine[-1].get('at', '')[:16]}"
                                         + ("" if sent else f" — {mine[-1].get('text', '')[:60]}"))
    return "★", "푸시 기록 없음 — 보고 푸시 직후 `python3 push_log.py --stamp <st> --market <mkt> --text \"<첫 줄>\"`"


def c11_carry(st, mk):
    t = _text(JOURNAL / f"carry_{st}_{mk}.md")
    if not t:
        return "—", "이어받기 파일 없음"
    need = ["## 감사 소급", "## 지지 이탈", "## 재투자 대기"]
    miss = [h for h in need if h not in t]
    batches = []
    for v in sorted(ANALYSIS.glob(f"AI클레임_{st}_{mk}*_판정.md")):
        mm = re.findall(r"<!-- 배치 완료 (\d{2}:\d{2})", _text(v))
        if mm:
            batches.append(f"{v.stem.replace(f'AI클레임_{st}_{mk}', '').strip('_') or '본'} {mm[-1]}")
    return ("★" if miss else "✓"), (("절 누락 " + ", ".join(miss) + " · ") if miss else "새 절 3개 있음 · ") + \
        ("배치 완료 " + ", ".join(batches) if batches else "배경 묶음 완료 표시 없음")


def c12_switch(st, mk):
    files = sorted(EVID.glob("account_switch_*.md"))
    if not files:
        return "—", "전환 예행 전(금요일 밤 미국 run 뒤 `account_switch.py --to real`)"
    t = _text(files[-1])
    ok = "모의 행 섞임 0건" in t and "확인 필요" not in t
    return ("✓" if ok else "★"), f"{files[-1].name} — " + ("통과" if ok else "확인 필요")


ITEMS = [("① 실계좌라면 샀을까", c1_shadow), ("② 매도 뒤 재투자", c2_refill), ("③ 시각", c3_timing),
         ("④ 감사", c4_audit), ("⑤ 체결", c5_fills), ("⑥ 손절 오차", c6_slip), ("⑦ 실계좌 조회", c7_probe),
         ("⑧ 날짜 규칙", c8_dates), ("⑨ 테스트", c9_tests), ("⑩ 보고 전달", c10_push), ("⑪ 이어받기 새 절", c11_carry),
         ("⑫ 전환 예행", c12_switch)]


def results(st: str, mk: str) -> list:
    """12항목 판정 — [(항목, ✓|★|—, 결과 문장)] · ITEMS 순서."""
    res = []
    for name, fn in ITEMS:
        try:
            v, why = fn(st, mk)
        except Exception as e:                       # noqa: BLE001 — 한 항목이 죽어도 표는 낸다(그 항목은 ★)
            v, why = "★", f"점검 실패 — {type(e).__name__}: {str(e)[:120]}"
        res.append((name, v, why))
    return res


def head_line(st: str, mk: str, res: list) -> str:
    """머리줄 — 판정 행만으로 다시 만들 수 있다(토요일 피드백이 저장소 행에서 같은 줄을 만든다)."""
    stars = [r for r in res if r[1] == "★"]
    ok = sum(1 for r in res if r[1] == "✓")
    return (f"[실전점검] {st} {mk.upper()} · 통과 {ok}/{len(res)} · 해당 없음 {sum(1 for r in res if r[1] == '—')}"
            + (f" · ★ {', '.join(r[0] for r in stars)}" if stars else " · ★ 0"))


def record(st: str, mk: str, res: list) -> None:
    """판정 행을 저장소(op_real_check)에 — 토요일 `feedback.py collect`가 이 표를 읽는다. 마크다운 표는 그대로 run 증거다."""
    db.real_check_put(st, mk, [{"item": n, "status": v, "note": w} for n, v, w in res])


def build(st: str, mk: str, res: list = None) -> tuple:
    res = res if res is not None else results(st, mk)
    stars = [r for r in res if r[1] == "★"]
    head = head_line(st, mk, res)
    ordered = stars + [r for r in res if r[1] != "★"]
    L = [f"# 실전 점검표 — {st} · {mk.upper()} ({datetime.now(KST):%Y-%m-%d %H:%M} KST · 파일에서 기계로)", "", head, "",
         "| 항목 | 판정 | 결과 |", "|---|---|---|"]
    L += [f"| {n} | {v} | {w.replace('|', '·')} |" for n, v, w in ordered]
    return head, "\n".join(L) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stamp", required=True)
    ap.add_argument("--market", choices=["kr", "us"], required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    res = results(a.stamp, a.market)
    head, md = build(a.stamp, a.market, res)
    out = Path(a.out) if a.out else EVID / f"real_check_{a.stamp}_{a.market}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    # close 게이트가 보는 증거 파일을 먼저 남긴다(저장소 기록은 그 뒤)
    out.write_text(md, encoding="utf-8")
    try:
        record(a.stamp, a.market, res)
    except sqlite3.Error as e:                    # 저장소 기록 실패가 점검표 파일을 막지 않는다(토요일 집계만 빠진다)
        print(f"★ 실전 점검표를 저장소에 못 남겼다({type(e).__name__}: {e}) — 파일은 남았다", file=sys.stderr)
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
