#!/usr/bin/env python3
"""직전 세션의 판단을 되짚는다 — 결과가 아니라 **근거**를 채점한다.

왜 있는가: 지금까지 판단을 내리고 나면 그걸로 끝이었다. 맞았는지 틀렸는지, 틀렸다면
근거가 틀린 건지 운이 나빴던 건지 아무도 세지 않았다. 그러면 같은 실수를 계속 한다.
*실사례(2026-09-08): 10:41 장중 +2.04%를 보고 "오늘도 오르는 중이라 비싸다"고 판단했는데
종가는 −0.19%였다. 회고가 있었으면 그날 안에 잡혔다.*

**언제 도는가** — 그 시장이 닫힌 뒤, **다른 시장을 준비하는 시각**에 돈다.
  · 국내 오전(09:35)  = 국내 준비 + **미국장 회고**(미국은 05:00 KST에 마감)
  · 미국 개장 전(22:35) = 미국 준비 + **국내장 회고**(국내는 15:30에 마감)
회고할 때 그 시장의 종가가 이미 확정돼 있어야 하기 때문이다.

**핵심 규율 — 결과와 과정을 갈라 본다.** 좋은 판단이 나쁜 결과를 낼 수 있고 그 반대도 된다.
결과만 보고 규칙을 고치면 소음을 쫓게 된다. 그래서 이 스크립트는 **숫자만** 내고,
"근거가 옳았는가"는 분석 단계가 답한다. 숫자는 기계가, 판단은 사람(과 LLM)이.

사용:
    python3 review.py --market kr --session 2026-09-08
    python3 review.py --market kr --session 2026-09-08 --record
"""
import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_client import KisClient, KisError

HERE = Path(__file__).parent
JOURNAL = HERE / "journal"
SIGNALS = HERE / "signals"
CONFIG = HERE / "config"
REVIEWS = JOURNAL / "reviews.jsonl"
KST = timezone(timedelta(hours=9))


def _read(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def find_signal(market: str, session: str):
    """그 세션의 시그널. 같은 날 여러 판이면 가장 최근 것."""
    stamp = session[2:].replace("-", "")
    cands = sorted(SIGNALS.glob(f"signal_{stamp}_{market.lower()}*.json"),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for p in cands:
        sig = _read(p)
        if sig:
            return p, sig
    return None, None


def trade_rows(market: str, session: str):
    """그 세션에 **실제로 전송된** 주문과 체결 판정. 파일이 없으면 None.

    시그널(제안)만 보고 회고하면 거부·미체결까지 '집행한 결정'으로 읽는다 —
    그러면 실행되지도 않은 논지의 성과를 채점하게 된다.
    """
    stamp = session[2:].replace("-", "")
    p = JOURNAL / f"trades_{stamp}_{market.lower()}.json"
    if not p.exists():
        # ★ 미국 run은 KST 자정을 넘겨 다음 날짜 파일에 기록될 수 있다(9/15 run → `trades_260916_us.json`).
        #   다음 날 파일의 `signal_ref`/`approved_ref`가 이 세션 스탬프를 가리키면 그 파일이 이 세션 것이다.
        from datetime import datetime as _dt, timedelta as _td
        try:
            nxt = (_dt.strptime(session, "%Y-%m-%d") + _td(days=1)).strftime("%y%m%d")
        except ValueError:
            return None
        q = JOURNAL / f"trades_{nxt}_{market.lower()}.json"
        rec = _read(q, {}) if q.exists() else {}
        refs = " ".join(str(rec.get(k) or "") for k in ("signal_ref", "approved_ref")) + " " + \
            " ".join(str(r.get("signal_ref") or "") for r in (rec.get("runs") or []))
        if f"_{stamp}_" not in refs:
            return None
        rows = [t for t in (rec.get("trades") or [])
                if f"_{stamp}_" in str((t.get("proposal") or {}).get("signal_ref") or refs)]
        return rows or (rec.get("trades") or [])
    return (_read(p, {}) or {}).get("trades") or []


def equity_rows(market: str) -> list:
    rows = []
    path = JOURNAL / "equity_curve.jsonl"
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        if r.get("market") == market:
            rows.append(r)
    keep = {}
    for r in rows:                       # 같은 날 여러 기록이면 마지막만
        keep[r["date"]] = r
    return sorted(keep.values(), key=lambda r: r["date"])


def review_without_signal(market: str, session: str, record: bool = False) -> int:
    """**판단이 없었던 run도 회고한다.**

    ★ 예전에는 시그널이 없으면 `return 2`로 회고 자체를 거부했다. 그런데 반쪽으로 죽은
    run이야말로 회고가 필요하다 — 왜 멈췄는지, 무엇을 남겼는지, 그중 오늘 쓸 것이
    무엇인지. *실측(2026-09-11): run 10개 중 8개가 회고된 적이 없고, 그중 미국 9/8(3단)·
    9/9(1단)·9/11(4단)은 시그널이 없어 **구조적으로 회고 불가**였다.*

    회고 대상은 시그널이 아니라 **run**이다.
    """
    stamp = session[2:].replace("-", "")
    print(f"회고 — {market} · {session}  **판단 없음(시그널 미발행)**\n")
    print("① 그날의 결정 — **없다.** 이 run은 결정까지 가지 못했다.")
    print("   → 그 자체가 회고 대상이다. '거래가 없었다'와 '판단을 못 했다'는 다른 사건이다.\n")

    print("② 무엇을 남겼나 — 오늘 쓸 수 있는 것")
    left = []
    for label, pat in (("재료", f"data/material_{stamp}_{market.lower()}*.md"),
                       ("스냅샷", f"data/snapshot_{stamp}_{market.lower()}*.json"),
                       ("캡처", f"data/run_evidence/tools_{stamp}_{market.lower()}*.md"),
                       ("이어받기", f"journal/carry_{stamp}_{market.lower()}*.md"),
                       ("섹터 리서치", f"analysis/섹터_*_{stamp}_*.md"),
                       ("분석노트", f"analysis/분석노트_{stamp}_{market.lower()}_v*.md"),
                       ("작업기록", f"journal/worklog_{stamp}_{market.lower()}*.md")):
        hits = sorted(HERE.glob(pat))
        if hits:
            left.append(label)
            for h in hits:
                print(f"   · {label:10s} {h.relative_to(HERE)}  ({h.stat().st_size:,}B)")
    if not left:
        print("   (아무것도 남기지 않았다 — 시작 전에 죽었다)")
    else:
        print(f"\n   → **{', '.join(left)}는 오늘 재료다.** 같은 조사를 다시 하지 말고 "
              f"`corpus.py search`로 먼저 뒤져라.")

    print("\n③ 왜 멈췄나 — 작업기록의 `## 중단·재개`와 마지막 게이트 판정을 본다")
    wl = sorted(HERE.glob(f"journal/worklog_{stamp}_{market.lower()}*.md"))
    if wl:
        txt = wl[-1].read_text(encoding="utf-8", errors="ignore")
        i = txt.find("중단·재개")
        print("   " + (txt[i:i + 300].replace("\n", "\n   ") if i >= 0
                       else "(작업기록에 중단·재개 블록이 없다 — 왜 멈췄는지 안 적혔다)"))
    else:
        print("   (작업기록이 없다)")
    print("\n④ 오늘 이어받을 것 — 그 run이 세우려던 논지·조건이 아직 유효한가")
    print("   → 유효하면 오늘 시그널에 잇고, 아니면 왜 접는지 적는다.")
    if record:
        # 기록하지 않으면 이 run은 영원히 '미회고'로 남고, 게이트가 매번 같은 것을 요구한다.
        REVIEWS.parent.mkdir(parents=True, exist_ok=True)
        with REVIEWS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(KST).isoformat(), "market": market, "session": session,
                "signal": None, "no_trade": None, "proposals": [], "executed": [],
                "outcome": {"reached_decision": False,
                            "left_artifacts": left,
                            "note": "판단 없음 — 시그널 미발행. run 자체를 회고했다."},
            }, ensure_ascii=False) + "\n")
        print(f"\n기록: {REVIEWS.name} (판단 없음 회고)")
    return 0


def review(market: str, session: str, record: bool) -> int:
    sig_path, sig = find_signal(market, session)
    if not sig:
        # ★ 판단이 없었어도 회고한다 — 시그널이 아니라 run이 대상이다.
        return review_without_signal(market, session, record)

    print(f"회고 — {market} · {session}  (시그널 {sig_path.name})\n")

    # ── 1. 무엇을 결정했나
    props = sig.get("proposals") or []
    print("① 그날의 결정")
    if sig.get("no_trade"):
        print("   no_trade — 매매하지 않음")
        why = (sig.get("no_trade_reason") or "").strip()
        if why:
            print(f"   사유: {why[:220]}{'…' if len(why) > 220 else ''}")
    for p in props:
        print(f"   {p.get('action')} {p.get('ticker')} {p.get('name', '')} "
              f"비중 {p.get('weight_target_pct')}% · confidence {p.get('confidence')}")

    # ── 1-b. 제안이 실제로 집행됐나 — **제안과 집행은 다른 사건이다.**
    # 이걸 안 보면 risk_guard가 거부했거나 미체결로 남은 제안까지 "그날의 결정"으로 채점된다.
    executed = trade_rows(market, session)
    print("\n①-b 그 결정이 실제로 집행됐나")
    if executed is None:
        print("   거래 기록 파일이 없다 — 전송하지 않은 run이거나 기록이 유실됐다.")
    elif not executed:
        print("   전송된 주문 0건. 제안이 있었다면 risk_guard에서 거부됐거나 집행되지 않았다.")
    else:
        done = set()
        for t in executed:
            f = t.get("fill") or {}
            v = f.get("verdict", "UNKNOWN")
            # 거부(REJECTED)를 '미확인'으로 뭉개지 않는다 — 거부는 **확인된 사실**이고,
            # 미체결처럼 기다릴 주문이 남아 있지도 않다. 행선지가 다르므로 표기도 다르다.
            mark = {"FILLED": "✓ 체결", "UNFILLED": "✗ 미체결(미확정 — fill.py 미완)",
                    "PARTIAL": "△ 부분체결",
                    "CANCELLED": "✂ 취소(대기 초과)", "EXPIRED": "⌛ 만료(장 마감)",
                    "REJECTED": "⊘ 브로커 거부"}.get(v, "? 미확인")
            qty = f.get("filled_qty") if f.get("filled_qty") is not None else f.get("delta")
            print(f"   {mark}  {t.get('action')} {t.get('ticker')} {t.get('name','')} "
                  f"요청 {t.get('qty')}주 @{t.get('price'):,.0f}"
                  + (f" · 실제 {abs(qty)}주" if isinstance(qty, int) and qty else ""))
            done.add(t.get("ticker"))
        for p in props:
            if p.get("ticker") not in done:
                print(f"   ⊘ 미집행  {p.get('action')} {p.get('ticker')} {p.get('name','')}"
                      " — 제안했으나 전송되지 않았다(거부·크기 미달 등). 사유를 노트 §11에서 확인하라.")
        print("   ※ 미체결·미집행 건은 **그날 그 판단을 실행한 것으로 채점하지 않는다.**"
              " 논지가 맞았는지와 집행이 됐는지는 다른 문제다.")

    # ── 2. 무엇이 일어났나 (기계가 세는 부분)
    rows = equity_rows(market)
    idx = next((i for i, r in enumerate(rows) if r["date"] == session), None)
    print("\n② 그 뒤 무슨 일이 있었나")
    outcome = {}
    if idx is None or idx + 1 >= len(rows):
        print("   아직 다음 거래일 기록이 없다 — 결과 판정 보류(다음 회고에서 다시 본다).")
    else:
        a, b = rows[idx], rows[idx + 1]
        d_eq = (b["equity"] - a["equity"]) / a["equity"] * 100 if a.get("equity") else None
        bench_a = (a.get("benchmark") or {}).get("price")
        bench_b = (b.get("benchmark") or {}).get("price")
        d_bm = (bench_b - bench_a) / bench_a * 100 if bench_a and bench_b else None
        outcome = {"from": a["date"], "to": b["date"], "equity_pct": d_eq, "benchmark_pct": d_bm}
        print(f"   {a['date']} → {b['date']}")
        print(f"   계좌 {d_eq:+.2f}%  vs  벤치마크 {d_bm:+.2f}%  "
              f"(초과 {d_eq - d_bm:+.2f}%p)" if d_eq is not None and d_bm is not None else "")
        if sig.get("no_trade") and d_bm is not None:
            verdict = "기다린 것이 맞았다" if d_bm < 0 else "기다린 비용이 발생했다"
            print(f"   → no_trade였으므로 **{verdict}** (벤치마크 {d_bm:+.2f}%)")

    # ── 3. 시나리오 조건이 실현됐나
    print("\n③ 그날 적어둔 시나리오 — 지금 어느 것이 실현됐는가")
    scen = sig.get("scenarios") or []
    if not scen:
        print("   (시나리오가 없다 — 다음 판단이 이어받을 것이 없었다는 뜻이다)")
    for s in scen:
        print(f"   [{s.get('id')}] {s.get('condition')}")
        print(f"        대응: {s.get('response', '')[:120]}")
    if scen:
        print("   ※ 실현 여부는 분석 단계가 재료를 보고 판정한다. 실현됐는데 대응하지 않았으면")
        print("     그것은 판단이 아니라 연기다 — 회피 감사(stage6_execute)에 걸린다.")

    # ── 4. 근거는 유지되나
    print("\n④ 근거 점검 (분석 단계가 답할 것)")
    print("   · 그날 근거로 삼은 사실 중 **틀린 것으로 드러난 것**이 있는가")
    print("   · 결과가 좋았다면 **근거가 맞아서인가, 운인가**")
    print("   · 결과가 나빴다면 **근거가 틀린 것인가, 옳았는데 안 통한 것인가**")
    print("   → 근거가 틀렸을 때만 규칙을 고친다. 결과만 보고 고치면 소음을 쫓는다.")

    gaps = sig.get("material_gaps") or []
    if gaps:
        print("\n⑤ 그날 스스로 적어둔 한계 — 지금 메워졌는가")
        for g in gaps:
            print(f"   · {g[:150]}")

    if record:
        REVIEWS.parent.mkdir(parents=True, exist_ok=True)
        with REVIEWS.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": datetime.now(KST).isoformat(), "market": market, "session": session,
                "signal": sig_path.name, "no_trade": bool(sig.get("no_trade")),
                "proposals": [{"ticker": p.get("ticker"), "action": p.get("action"),
                               "weight_target_pct": p.get("weight_target_pct"),
                               "confidence": p.get("confidence")} for p in props],
                "executed": [{"ticker": t.get("ticker"), "action": t.get("action"),
                              "qty": t.get("qty"), "price": t.get("price"),
                              "verdict": (t.get("fill") or {}).get("verdict", "UNKNOWN")}
                             for t in (executed or [])],
                "outcome": outcome,
            }, ensure_ascii=False) + "\n")
        print(f"\n기록: {REVIEWS.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="직전 세션의 판단을 되짚는다")
    ap.add_argument("--market", choices=["kr", "us"], required=True)
    ap.add_argument("--session", required=True, help="회고할 세션 날짜 YYYY-MM-DD")
    ap.add_argument("--record", action="store_true", help="journal/reviews.jsonl에 남긴다")
    args = ap.parse_args()
    return review(args.market.upper(), args.session, args.record)


if __name__ == "__main__":
    sys.exit(main())
