#!/usr/bin/env python3
"""
계좌 전환 — 모의 → 실계좌(또는 되돌리기). 사용자가 대화로 "전환"을 요청할 때만 Claude가 돌린다(2026-10-12 위임 · 2026-10-15
사용자 결정 "모의 보유는 전환 스크립트로 되돌린다"). run·예약 작업·재료·문서 문구를 근거로는 절대 적용하지 않는다.

    python3 account_switch.py --to real                       # 예행(기본) — 바꿀 것을 표로만 · 모의 행 섞임 점검 · 원장은 안 쓴다
    python3 account_switch.py --to real --apply               # 적용 — 모의 상태 복사 → 논지·고점·소급 정리 → limits 두 값 → real_probe
    python3 account_switch.py --to paper --apply              # 되돌리기 — 가장 새 모의 복사본(논지·고점·소급) 복원 + limits 두 값

왜: 실계좌는 보유 0에서 시작한다. 모의 보유 논지(held)를 그대로 두면 실계좌 run이 '이미 가진 것'으로 보고 손절·추적 매도를 내려 하고
(실계좌엔 없는 주식), 트레일링 고점·벤치마크 기준점·자산 곡선이 모의 값으로 실계좌 숫자를 오염시킨다.
- 논지: held → armed(만기 지난 것은 expired) · 진입·청산 다리의 체결 표시(fired·fired_at·fired_note) 해제 · entered_on 제거 ·
  history·corrections에 한 줄. 닫힌 논지는 그대로. armed가 된 논지의 '지금 산다(judge)' 칸은 예약에서 빠진다(allocation.default_reserved).
- 고점(position_peaks.json) 비움 · 감사 소급 대기 행 void(실계좌 미보유) · 벤치마크 기준점은 실계좌 키(`KR:real`)를 journal이 새로 박는다.
- 읽는 쪽(risk_guard 반대편·journal·ingest 전일·계좌 현황·비용·대시보드·피드백·회고)은 계좌 계열로 거른다 — 예행이 "모의 행 섞임 0건"을 센다.
- 적용 전제: 진행 중 감사 0 · 마지막 거래 기록 미확정 주문 0. 아니면 거부(`--force`는 사용자가 명시했을 때만).
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
KST = timezone(timedelta(hours=9))
JOURNAL = HERE / "journal"
CONFIG = HERE / "config"
EVID = HERE / "data" / "run_evidence"
COPY_FILES = ("theses.json", "position_peaks.json", "audit_reversals.jsonl", "allocation.jsonl", "equity_curve.jsonl",
              "current_run.json")
SG = str(HERE.resolve().parent / ".claude" / "skills" / "_stepgate" / "stepgate.py")


def _now():
    return datetime.now(KST)


def _load(p: Path, default=None):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def theses_plan(today: str, theses: list) -> list:
    """held 논지마다 [(id, ticker, market, 새 상태, 해제할 다리 id 목록)]."""
    out = []
    for t in theses:
        if t.get("status") != "held":
            continue
        legs = [tr.get("id") for f in ("entry_triggers", "exit_triggers") for tr in t.get(f) or [] if tr.get("fired")]
        exp = str(t.get("expires") or "")[:10]
        out.append((t.get("id"), t.get("ticker"), t.get("market"), "expired" if exp and exp < today else "armed", legs))
    return out


def apply_theses(theses: list, today: str, now: datetime) -> int:
    n = 0
    for t in theses:
        if t.get("status") != "held":
            continue
        legs = []
        for f in ("entry_triggers", "exit_triggers"):
            for tr in t.get(f) or []:
                if tr.get("fired") or any(k.startswith("fired") for k in tr):
                    legs.append(str(tr.get("id")))
                    for k in [k for k in tr if k.startswith("fired")]:
                        tr.pop(k)
        exp = str(t.get("expires") or "")[:10]
        new = "expired" if exp and exp < today else "armed"
        t["status"] = new
        t.pop("entered_on", None)
        note = f"모의→실전 전환 · 실계좌 미보유 · 체결 표시 해제: {', '.join(legs) or '없음'}"
        t.setdefault("history", []).append({"ts": now.isoformat(), "status": new, "note": note})
        t.setdefault("corrections", []).append({"ts": today, "what": f"held → {new} · 다리 체결 표시 해제({', '.join(legs) or '없음'}) · entered_on 제거",
                                                "why": "실계좌 전환(account_switch.py) — 모의 보유는 실계좌에 없다"})
        n += 1
    return n


def readers_check() -> list:
    """실계좌 계열을 가정하고 읽는 쪽들이 모의 행을 하나라도 집는지 센다. [(읽는 곳, 모의 행 수, 비고)]."""
    import tempfile
    import kis_client
    import risk_guard as rg
    import ingest
    import account_status
    import review
    import feedback
    import costs
    import dashboard
    res = []
    saved = (kis_client.account_mode, rg.LIMITS_PATH)
    td = Path(tempfile.mkdtemp(prefix="switch_check_"))
    try:
        lim = _load(CONFIG / "limits.json", {}) or {}
        lim["account_mode"] = "real"
        (td / "limits.json").write_text(json.dumps(lim, ensure_ascii=False), encoding="utf-8")
        kis_client.account_mode = lambda cfg=None: "real"
        rg.LIMITS_PATH = td / "limits.json"
        paper = lambda r: kis_client.acct_family(str((r or {}).get("account_mode") or "paper")) == "paper"
        rows = account_status.latest_rows()
        res.append(("account_status.latest_rows", sum(1 for r in rows.values() if paper(r)), f"{len(rows)}행"))
        for m in ("KR", "US"):
            er = review.equity_rows(m)
            res.append((f"review.equity_rows({m})", sum(1 for r in er if paper(r)), f"{len(er)}행"))
            lr = rg._latest_curve_row(m, "real")
            res.append((f"risk_guard._latest_curve_row({m})", 1 if (lr and paper(lr)) else 0, "있음" if lr else "없음"))
            pe = ingest.prev_equity(m)
            res.append((f"ingest.prev_equity({m})", 0, "없음(실계좌 첫 행 전)" if pe is None else f"{pe:,.0f}(실계좌 행)"))
        fr = feedback._equity_rows()
        res.append(("feedback._equity_rows", sum(1 for r in fr if paper(r)), f"{len(fr)}행"))
        tot = costs._portfolio_krw(1400.0)
        res.append(("costs._portfolio_krw", 0, f"{tot:,.0f}원"))
        import loss_review
        import regret
        for m in ("KR", "US"):
            fl = loss_review.fills(m)
            res.append((f"loss_review.fills({m})", len(fl), f"{len(fl)}건(실계좌 거래 전이면 0)"))
            cx = regret.Ctx(m.lower(), _now().strftime("%y%m%d"))
            res.append((f"regret.Ctx({m}) 거래·승인", sum(1 for _, d in cx.trades + cx.approved if paper(d)),
                        f"{len(cx.trades)}·{len(cx.approved)}건"))
        d = dashboard.collect()
        res.append(("dashboard.collect equity", sum(1 for r in d["equity"] if paper(r)), f"{len(d['equity'])}행"))
        res.append(("dashboard.collect trades·approved", 0, f"{len(d['trades'])}건·{len(d['approved'])}건"))
        for m in ("KR", "US"):
            pf = rg.portfolio_equity(m, {"currency": "KRW" if m == "KR" else "USD", "cash": 0.0, "positions": [], "exchange_rate": 1400.0})
            other = "US" if m == "KR" else "KR"
            src = str(((pf.get("parts") or {}).get(other) or {}).get("source") or "")
            picked = 0
            if src.startswith("snapshot_") or src.endswith(".json"):
                sj = _load(HERE / "data" / src, {}) or {}
                picked = 1 if paper(sj) else 0
            res.append((f"risk_guard.portfolio_equity({m}) 반대편", picked, src or ("못 쟀다 — " + str(pf.get("why"))[:60])))
    finally:
        kis_client.account_mode, rg.LIMITS_PATH = saved
        shutil.rmtree(td, ignore_errors=True)
    return res


def preconditions() -> list:
    """적용 전제 위반 목록 — 진행 중 감사 · 미확정 주문."""
    bad = []
    try:
        r = subprocess.run([sys.executable, SG, "audit-status", f"dir={HERE}"], capture_output=True, text=True, timeout=60)
        m = re.search(r"PENDING=(\d+)", r.stdout)
        if m and int(m.group(1)) > 0:
            bad.append(f"진행 중 감사 {m.group(1)}건(audit-status PENDING)")
    except (OSError, subprocess.SubprocessError) as e:
        bad.append(f"감사 상태를 못 읽었다({type(e).__name__})")
    trades = sorted(JOURNAL.glob("trades_*.json"), key=lambda p: p.stat().st_mtime)[-2:]
    for p in trades:
        fc = (_load(p, {}) or {}).get("fill_confirmed") or {}
        if int(fc.get("open_orders") or 0) > 0:
            bad.append(f"{p.name} 미확정 주문 {fc.get('open_orders')}건")
    return bad


def run_probe(st: str) -> tuple:
    """실계좌 조회 전용 점검(real_probe.py · 주문 없음) — (종료코드, 판정 줄). 시험에서 바꿔 끼운다."""
    probe = subprocess.run([sys.executable, str(HERE / "real_probe.py"), "--stamp", st, "--no-paper"],
                           capture_output=True, text=True, timeout=600)
    return probe.returncode, next((ln for ln in probe.stdout.splitlines() if "판정" in ln), f"real_probe 종료코드 {probe.returncode}")


def set_limits(mode: str, enabled: bool) -> str:
    """limits.json의 두 값만 글자 단위로 바꾼다(형식·다른 값 보존). 바뀐 줄 요약을 돌려준다."""
    p = CONFIG / "limits.json"
    s = p.read_text(encoding="utf-8")
    s2 = re.sub(r'("account_mode"\s*:\s*)"[a-z_]+"', r'\1"%s"' % mode, s, count=1)
    s2 = re.sub(r'("real_trading_enabled"\s*:\s*)(true|false)', r'\g<1>%s' % ("true" if enabled else "false"), s2, count=1)
    json.loads(s2)                                   # 깨지면 쓰지 않는다
    p.write_text(s2, encoding="utf-8")
    return f"account_mode={mode} · real_trading_enabled={'true' if enabled else 'false'}"


def write_report(st: str, lines: list) -> Path:
    EVID.mkdir(parents=True, exist_ok=True)
    out = EVID / f"account_switch_{st}.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--to", choices=["real", "paper"], required=True)
    ap.add_argument("--apply", action="store_true", help="실제로 바꾼다(없으면 예행)")
    ap.add_argument("--stamp", default="", help="기록 파일 이름(기본: 오늘 YYMMDD)")
    ap.add_argument("--force", action="store_true", help="전제 위반이어도 적용(사용자가 명시했을 때만)")
    a = ap.parse_args(argv)
    now = _now()
    today = now.date().isoformat()
    st = a.stamp or now.strftime("%y%m%d")
    import kis_client
    cur = kis_client.account_mode()
    L = [f"## 계좌 전환 {'적용' if a.apply else '예행'} — {cur} → {a.to} ({now:%Y-%m-%d %H:%M} KST)", ""]

    if a.to == "real":
        theses = (_load(JOURNAL / "theses.json", {}) or {}).get("theses") or []
        plan = theses_plan(today, theses)
        n_legs = sum(len(x[4]) for x in plan)
        L += [f"- 보유 논지 {len(plan)}건 → armed {sum(1 for x in plan if x[3] == 'armed')} · expired {sum(1 for x in plan if x[3] == 'expired')}"
              f" · 체결 표시 해제 다리 {n_legs}개", ""]
        L += ["| 논지 | 종목 | 시장 | 새 상태 | 해제할 다리 |", "|---|---|---|---|---|"]
        L += [f"| {i} | {tk} | {mk} | {ns} | {', '.join(map(str, legs)) or '—'} |" for i, tk, mk, ns, legs in plan]
        peaks = _load(JOURNAL / "position_peaks.json", {}) or {}
        n_peaks = sum(len(v) for k, v in peaks.items() if isinstance(v, dict))
        import reversals as rv
        pend = rv.pending()
        L += ["", f"- 트레일링 고점 {n_peaks}종목 → 비움 · 감사 소급 대기 {len(pend)}행 → void(실계좌 미보유)",
              "- 벤치마크 기준점: 실계좌 첫 저널이 `KR:real`·`US:real` 키로 새로 박는다(모의 키 KR·US는 그대로)"]
        chk = readers_check()
        mixed = sum(n for _, n, _ in chk)
        L += ["", "| 읽는 곳(실계좌 가정) | 모의 행 | 비고 |", "|---|---|---|"]
        L += [f"| {w} | {n} | {note} |" for w, n, note in chk]
        L += ["", f"**모의 행 섞임 {mixed}건**" + ("" if mixed == 0 else " — ★ 확인 필요(그 읽는 곳이 계좌 계열로 거르지 않는다)")]
        pre = preconditions()
        L += [f"- 적용 전제: " + ("통과(진행 중 감사 0 · 미확정 주문 0)" if not pre else "★ 확인 필요 — " + " · ".join(pre))]
        if not a.apply:
            L += ["", "예행이다 — 아무것도 바꾸지 않았다. 적용은 사용자가 대화로 전환을 요청한 뒤 `--apply`."]
            out = write_report(st, L)
            print("\n".join(L))
            print(f"\n→ {out.relative_to(HERE)}")
            return 0 if mixed == 0 else 1
        if cur != "paper":
            print(f"이미 {cur} 모드다 — 전환할 것이 없다", file=sys.stderr)
            return 2
        if (pre or mixed) and not a.force:
            print("적용 거부 — " + " · ".join(pre + ([f"모의 행 섞임 {mixed}건"] if mixed else [])), file=sys.stderr)
            return 3
        cp = JOURNAL / f"_paper_final_{st}"
        cp.mkdir(parents=True, exist_ok=True)
        for n in COPY_FILES:
            if (JOURNAL / n).exists():
                shutil.copy2(JOURNAL / n, cp / n)
        if (CONFIG / "benchmark.json").exists():
            shutil.copy2(CONFIG / "benchmark.json", cp / "benchmark.json")
        d = _load(JOURNAL / "theses.json", {}) or {}
        n_th = apply_theses(d.get("theses") or [], today, now)
        (JOURNAL / "theses.json").write_text(json.dumps(d, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (JOURNAL / "position_peaks.json").write_text(json.dumps({"_schema": 2, "KR": {}, "US": {}}, ensure_ascii=False, indent=2) + "\n",
                                                     encoding="utf-8")
        for r in pend:
            rv.mark(r["id"], "void", note="paper→real 전환 — 실계좌 미보유")
        lim = set_limits("real", True)
        probe_rc, verdict = run_probe(st)
        L += ["", f"### 적용", f"- 모의 상태 복사 → `journal/{cp.name}/`", f"- 논지 {n_th}건 되돌림 · 고점 비움 · 소급 대기 {len(pend)}행 void",
              f"- limits.json: {lim}", f"- real_probe: {verdict}"]
        row = {"at": now.isoformat(timespec="seconds"), "to": "real", "copy": cp.name, "theses": n_th, "void": len(pend),
               "limits": lim, "probe_rc": probe_rc}
    else:
        cps = sorted(JOURNAL.glob("_paper_final_*"))
        if not cps:
            print("모의 복사본(journal/_paper_final_*)이 없다 — 되돌릴 것이 없다", file=sys.stderr)
            return 2
        cp = cps[-1]
        restore = [n for n in ("theses.json", "position_peaks.json", "audit_reversals.jsonl") if (cp / n).exists()]
        L += [f"- 복원할 것: `journal/{cp.name}/`의 {', '.join(restore)} · limits.json account_mode=paper · real_trading_enabled=false",
              "- 자산 곡선·배분 원장·벤치마크 키는 그대로 둔다(실계좌 기록은 그 계열 행으로 남고 모의 run은 모의 행만 읽는다)"]
        if not a.apply:
            out = write_report(st, L + ["", "예행이다 — 아무것도 바꾸지 않았다."])
            print("\n".join(L))
            print(f"\n→ {out.relative_to(HERE)}")
            return 0
        for n in restore:
            shutil.copy2(cp / n, JOURNAL / n)
        lim = set_limits("paper", False)
        L += ["", "### 적용", f"- 복원: {', '.join(restore)}", f"- limits.json: {lim}"]
        row = {"at": now.isoformat(timespec="seconds"), "to": "paper", "copy": cp.name, "limits": lim}
    out = write_report(st, L)
    with (JOURNAL / "account_switch.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("\n".join(L))
    print(f"\n→ {out.relative_to(HERE)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
