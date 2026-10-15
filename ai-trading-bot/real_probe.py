#!/usr/bin/env python3
"""
실계좌 조회 전용 점검 — 조회 전용 주간(account_mode real_readonly)에 매일 한 번, 실계좌가 모의와 다르게 답하는 것을 잡는다.

왜(2026-10-12 실계좌 전환 계획 7-b): 모의에서 돌던 코드가 실계좌에서 조용히 틀리는 자리는 응답 필드가 다른 곳이다.
실제로 첫 조회에서 통합증거금 계좌의 미국 구매력이 `ord_psbl_frcr_amt`(0)가 아니라 `frcr_ord_psbl_amt1`에 있었다 —
그대로 켰으면 미국 매수가 전부 0주로 막혔다. 이 도구는 **주문을 하지 않는다**(조회 전용 클라이언트 — 주문 메서드가 거부).

    python3 real_probe.py --stamp 261015            # data/run_evidence/real_probe_<st>.json(원본) · .md(판정)
    python3 real_probe.py --stamp 261015 --expect-krw <원화 예수금>
    python3 real_probe.py --stamp 261015 --shadow kr # 그림자 점검 — 그 run의 모의 시그널을 실계좌 잔고로 risk_guard에 다시 넣는다

★ 그림자 점검(2026-10-12 사용자: "실전 전환 직전까지 모의 매매를 계속하고 싶다") — 조회 전용 주간 대신, 모의 run이 낸 시그널을
**실계좌 잔고·실계좌 규칙**(주문당 상한·세후 바닥·통합증거금 공유 현금)으로 risk_guard에 한 번 더 넣어 본다. 주문은 없고, 원장
(배분·저널)에도 쓰지 않는다. 결과는 `data/run_evidence/real_shadow_<st>_<mkt>.md` — 실전 전환 전 관문의 증거다.
`journal.py --daily`가 모의 모드에서 `limits.real_shadow_check`가 켜져 있으면 자동으로 부른다.

판정은 넷 — 계좌(예수금) · 해외 거래 · 통합증거금 · 체결조회 TR. 그리고 모의와 다른 응답 필드 목록 · 세션 창.
오류 코드(msg_cd·msg1)는 그대로 적는다 — "미신청"으로 보이는 것만 사용자 안내로 올린다.
"""
import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_client import (KisClient, KisError, market_session, us_daytime_session, orderable_from_psamount,
                        kr_cash_from_margin, account_mode)

HERE = Path(__file__).parent
EVID = HERE / "data" / "run_evidence"
KST = timezone(timedelta(hours=9))


def _call(c: KisClient, path: str, tr: str, params: dict) -> dict:
    try:
        return c._request("GET", path, headers=c._headers(tr), params=params)
    except KisError as e:
        return {"rt_cd": "ERR", "msg1": str(e)[:300]}


def _out(res: dict, key: str = "output"):
    o = res.get(key)
    return (o[0] if o else {}) if isinstance(o, list) else (o or {})


def probe(c: KisClient, day: str) -> dict:
    """한 계좌에 같은 조회 묶음을 던진다. 반환 {name: 원본 응답}."""
    a = {"CANO": c.cano, "ACNT_PRDT_CD": c.acnt_prdt_cd}
    raw = {}
    raw["kr_balance"] = _call(c, "/uapi/domestic-stock/v1/trading/inquire-balance", c.tr["dom_balance"], {
        **a, "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
        "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""})
    raw["us_psamount"] = _call(c, "/uapi/overseas-stock/v1/trading/inquire-psamount", c.tr["ov_psamount"], {
        **a, "OVRS_EXCG_CD": "NASD", "OVRS_ORD_UNPR": "250.0000", "ITEM_CD": "AAPL"})
    raw["us_balance"] = _call(c, "/uapi/overseas-stock/v1/trading/inquire-balance", c.tr["ov_balance"], {
        **a, "OVRS_EXCG_CD": "NASD", "TR_CRCY_CD": "USD", "CTX_AREA_FK200": "", "CTX_AREA_NK200": ""})
    ccld = {**a, "INQR_STRT_DT": day, "INQR_END_DT": day, "SLL_BUY_DVSN_CD": "00", "INQR_DVSN": "00", "PDNO": "",
            "CCLD_DVSN": "00", "ORD_GNO_BRNO": "", "ODNO": "", "INQR_DVSN_3": "00", "INQR_DVSN_1": "",
            "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
    if c.svr == "real":
        ccld["EXCG_ID_DVSN_CD"] = "ALL"
    raw["kr_ccld"] = _call(c, "/uapi/domestic-stock/v1/trading/inquire-daily-ccld", c.tr["dom_ccld"], ccld)
    d0 = (datetime.strptime(day, "%Y%m%d") - timedelta(days=3)).strftime("%Y%m%d")
    raw["us_ccnl"] = _call(c, "/uapi/overseas-stock/v1/trading/inquire-ccnl", c.tr["ov_ccld"], {
        **a, "PDNO": "", "ORD_STRT_DT": d0, "ORD_END_DT": day, "SLL_BUY_DVSN": "00", "CCLD_NCCS_DVSN": "00",
        "OVRS_EXCG_CD": "", "SORT_SQN": "DS", "ORD_DT": "", "ORD_GNO_BRNO": "", "ODNO": "",
        "CTX_AREA_NK200": "", "CTX_AREA_FK200": ""})
    if c.svr == "real":
        raw["intgr_margin"] = _call(c, "/uapi/domestic-stock/v1/trading/intgr-margin", "TTTC0869R", {
            **a, "CMA_EVLU_AMT_ICLD_YN": "N", "WCRC_FRCR_DVSN_CD": "02", "FWEX_CTRT_FRCR_DVSN_CD": "02"})
    return raw


def keyset(res: dict) -> dict:
    """응답의 output 계열 키 목록."""
    out = {}
    for k in ("output", "output1", "output2", "output3"):
        o = res.get(k)
        if isinstance(o, list):
            if not o:
                out[k] = None                       # 행 없음 — 필드를 비교할 수 없다(없다는 뜻이 아니다)
                continue
            o = o[0]
        if isinstance(o, dict) and o:
            out[k] = sorted(o.keys())
    return out


def verdicts(raw: dict, expect_krw: float = None) -> list:
    rows = []
    kb = raw.get("kr_balance") or {}
    s2 = _out(kb, "output2")
    dep = s2.get("dnca_tot_amt")
    ok = str(kb.get("rt_cd")) == "0"
    note = f"예수금 {float(dep or 0):,.0f}원 · 보유 {len(kb.get('output1') or [])}종목"
    if ok and expect_krw is not None and dep not in (None, ""):
        ok = abs(float(dep) - expect_krw) < 1 or len(kb.get("output1") or []) > 0
        note += f" (기대 {expect_krw:,.0f})"
    rows.append(("계좌·예수금", ok, note, f"{kb.get('msg_cd')} {kb.get('msg1', '')}".strip()))
    ps = raw.get("us_psamount") or {}
    o = orderable_from_psamount(_out(ps), "real")
    rows.append(("해외 거래", str(ps.get("rt_cd")) == "0",
                 f"미국 주문가능 {o['orderable_cash']:,.2f} USD({o['field']}) · 최대 {o['max_qty']}주 · 환율 {o['exchange_rate']:,.2f}",
                 f"{ps.get('msg_cd')} {ps.get('msg1', '')}".strip()))
    im = _out(raw.get("intgr_margin") or {})
    pool = kr_cash_from_margin(im) if im else None
    itgr = bool(str(im.get("ovrs_stck_itgr_mgna_dvsn_name") or "").strip())
    rows.append(("통합증거금", itgr and o["orderable_cash"] > 0,
                 (f"{im.get('ovrs_stck_itgr_mgna_dvsn_name') or '미신청(구분명 공란)'} · 국내 주문가능 "
                  f"{(pool or {}).get('cash', 0):,.0f}원 · 미국 통합 주문가능 {float(im.get('usd_itgr_ord_psbl_amt') or 0):,.0f}원"),
                 "" if itgr else "앱 '통합증거금 신청'이 필요하다"))
    for nm, key in (("국내 체결조회", "kr_ccld"), ("미국 체결조회", "us_ccnl")):
        r = raw.get(key) or {}
        rows_ = r.get("output1") if key == "kr_ccld" else r.get("output")
        n = len(rows_ or []) if isinstance(rows_, list) else (1 if rows_ else 0)
        sample = (rows_[0] if isinstance(rows_, list) and rows_ else {}) or {}
        settle = [k for k in sample if any(x in k for x in ("stl", "sttl", "setl", "dt"))]
        rows.append((nm, str(r.get("rt_cd")) == "0", f"{n}건" + (f" · 날짜 필드 {', '.join(settle)}" if settle else ""),
                     f"{r.get('msg_cd')} {r.get('msg1', '')}".strip()))
    return rows


def shadow(stamp: str, market: str, quiet: bool = True) -> dict:
    """모의 시그널을 실계좌 잔고·실계좌 규칙으로 risk_guard에 다시 넣는다(주문·원장 쓰기 없음). 반환 요약 dict."""
    import contextlib
    import io
    import shutil
    import tempfile
    import risk_guard as rg
    import allocation as _al
    from kis_client import us_buying_power

    mk = market.lower()
    sig_path = HERE / "signals" / f"signal_{stamp}_{mk}.json"
    paper_snap = HERE / "data" / f"snapshot_{stamp}_{mk}.json"
    if not sig_path.exists():
        return {"ok": False, "why": f"시그널 없음 — {sig_path.name}"}
    now = datetime.now(KST)
    real = KisClient(svr="real", allow_real=True, read_only=True)
    wl = json.loads((HERE / "config" / "watchlist.json").read_text(encoding="utf-8"))
    kb = real.domestic_balance()
    ub = real.overseas_balance()
    bp = us_buying_power(real, wl)
    ub.update({"cash": bp["cash"], "cash_field": bp["cash_field"], "exchange_rate": bp["exchange_rate"],
               "cash_shared": bp.get("cash_shared", False)})
    kb["exchange_rate"] = bp["exchange_rate"]
    kb["cash_shared"] = bp.get("cash_shared", False)      # 통합증거금 — 국내 쪽도 같은 주머니(시장 몫 규칙이 양쪽에 걸린다 · 2026-10-15)
    prices = {}
    if paper_snap.exists():
        prices = (json.loads(paper_snap.read_text(encoding="utf-8")).get("prices") or {})
    td = Path(tempfile.mkdtemp(prefix="real_shadow_"))
    try:
        for m, bal in (("kr", kb), ("us", ub)):
            (td / f"snapshot_{stamp}_{m}.json").write_text(json.dumps({
                "generated_at": now.isoformat(), "market": m.upper(), "svr": "real", "account_mode": "real", "balance": bal,
                "prices": prices if m == mk else {}, "day_pnl_pct": None}, ensure_ascii=False), encoding="utf-8")
        lim = json.loads(rg.LIMITS_PATH.read_text(encoding="utf-8"))
        lim["account_mode"] = "real"                       # 실계좌 규칙(주문당 상한·세후 바닥)으로 잰다 — 이 사본에만
        lim["signal_max_age_minutes"] = 100000             # 판단이 아니라 기계 점검이다 — 시그널 나이로 막지 않는다
        lim_path = td / "limits.json"
        lim_path.write_text(json.dumps(lim, ensure_ascii=False), encoding="utf-8")
        out = td / f"approved_{stamp}_{mk}.json"
        # 시장 몫(2026-10-15) — 실계좌 규칙은 몫 판단을 요구한다. 모의 시그널엔 없으니 그림자에서만 표시용 임시 가정(판단 아님)을 넣는다.
        sig_d = json.loads(sig_path.read_text(encoding="utf-8"))
        al_d = sig_d.get("allocation") or {}
        share_note = "시그널 판단"
        if not isinstance(al_d.get("market_share"), dict):
            al_d["market_share"] = {"KR": 50, "US": 50, "why": "그림자 표시용 임시 가정 — 판단 아님(모의 시그널에 시장 몫이 없다)"}
            sig_d["allocation"] = al_d
            share_note = "임시 가정 KR 50 · US 50(그림자 표시용 — 판단 아님)"
        sig_run = td / sig_path.name
        sig_run.write_text(json.dumps(sig_d, ensure_ascii=False), encoding="utf-8")
        saved = (rg.DATA_DIR, rg.LIMITS_PATH, _al.record)
        rg.DATA_DIR, rg.LIMITS_PATH = td, lim_path
        _al.record = lambda *a, **k: {"id": "shadow", "target_invested_pct": None, "based_on": None}
        buf = io.StringIO()
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                rc = rg.run(sig_run, td / f"snapshot_{stamp}_{mk}.json", out)
        except rg.Rejection as e:
            rc, buf = 2, io.StringIO(f"거부(전체): {e}")
        finally:
            rg.DATA_DIR, rg.LIMITS_PATH, _al.record = saved
        ap = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    finally:
        shutil.rmtree(td, ignore_errors=True)
    fx = float(bp.get("exchange_rate") or 0)
    mult = 1.0 if mk == "kr" else fx
    orders = ap.get("orders") or []
    buys = [o for o in orders if o.get("action") == "BUY"]
    over = [o for o in buys if float(o["price"]) * mult > (ap.get("order_cap") or {}).get("max_krw", 1e18)
            and int(o["qty"]) > int((ap.get("order_cap") or {}).get("max_shares", 0))
            or (float(o["price"]) * mult <= (ap.get("order_cap") or {}).get("max_krw", 1e18)
                and float(o["price"]) * int(o["qty"]) * mult > (ap.get("order_cap") or {}).get("max_krw", 1e18) + 1)]
    summ = {"ok": rc == 0 and not over, "rc": rc, "stamp": stamp, "market": mk.upper(), "at": now.isoformat(),
            "kr_cash": kb.get("cash"), "us_cash": ub.get("cash"), "fx": fx,
            "buys": [(o["ticker"], int(o["qty"]), round(float(o["price"]) * int(o["qty"]) * mult)) for o in buys],
            "capped": [(o["ticker"], o["order_capped"]["from_qty"], o["order_capped"]["to_qty"]) for o in buys if o.get("order_capped")],
            "rejected": [(r.get("ticker"), str(r.get("why"))[:90]) for r in ap.get("rejected") or []],
            "cap_over": [o["ticker"] for o in over], "deficit_short": ap.get("deficit_short"),
            "pf_why": next((ln.strip(" ·") for ln in buf.getvalue().splitlines() if "포트폴리오 자산" in ln), "")}
    L = [f"## 그림자 점검 — {stamp} {mk.upper()} ({now:%m-%d %H:%M} · 모의 시그널을 실계좌 잔고·규칙으로 · 주문 없음)", "",
         f"- 실계좌 현금: 국내 {float(kb.get('cash') or 0):,.0f}원 · 미국 {float(ub.get('cash') or 0):,.2f} USD(같은 돈 · 환율 {fx:,.2f})",
         f"- 합산 분모: {summ['pf_why'] or '?'}",
         f"- risk_guard 종료코드 {rc} · 승인 매수 {len(buys)}건 · 상한 초과 {len(over)}건 · 미달 {'예' if summ['deficit_short'] else '아니오'}",
         f"- 시장 몫: {share_note} · 주 단위 1주 올림 {sum(1 for o in buys if o.get('rounded_up_to_1'))}건"]
    for t, q, krw in summ["buys"]:
        L.append(f"  - BUY {t} {q}주 ≈ {krw:,}원")
    for t, a, b in summ["capped"]:
        L.append(f"  - 상한으로 깎임 {t} {a}→{b}주")
    for t, w in summ["rejected"][:15]:
        L.append(f"  - 거부 {t}: {w}")
    if rc == 2:
        L.append(f"  - {buf.getvalue()[:300]}")
    EVID.mkdir(parents=True, exist_ok=True)
    mp = EVID / f"real_shadow_{stamp}_{mk}.md"
    prev = mp.read_text(encoding="utf-8").rstrip() + "\n\n" if mp.exists() else ""
    mp.write_text(prev + "\n".join(L) + "\n", encoding="utf-8")
    summ["file"] = str(mp.relative_to(HERE))
    if not quiet:
        print("\n".join(L))
    return summ


def main() -> int:
    ap = argparse.ArgumentParser(description="실계좌 조회 전용 점검(주문 없음)")
    ap.add_argument("--stamp", required=True, help="YYMMDD")
    ap.add_argument("--expect-krw", type=float, help="기대 예수금(입금 직후 대조용)")
    ap.add_argument("--no-paper", action="store_true", help="모의 계좌와의 필드 대조를 건너뛴다")
    ap.add_argument("--shadow", choices=["kr", "us"], help="그림자 점검만 — 그 run의 모의 시그널을 실계좌 잔고·규칙으로")
    a = ap.parse_args()
    if a.shadow:
        r = shadow(a.stamp, a.shadow, quiet=False)
        if not r.get("ok") and r.get("why"):
            print(r["why"])
        return 0 if r.get("ok") else 1
    now = datetime.now(KST)
    day = now.strftime("%Y%m%d")
    real = KisClient(svr="real", allow_real=True, read_only=True)
    raw = {"real": probe(real, day)}
    diffs, nocmp = [], []
    if not a.no_paper:
        try:
            raw["paper"] = probe(KisClient(svr="paper"), day)
            for k in raw["real"]:
                if k not in raw["paper"]:
                    continue
                rk, pk = keyset(raw["real"][k]), keyset(raw["paper"][k])
                for part in sorted(set(rk) | set(pk)):
                    if rk.get(part) is None or pk.get(part) is None:
                        if part in rk and part in pk:
                            nocmp.append(f"{k}.{part}")
                        continue
                    only_r = sorted(set(rk.get(part, [])) - set(pk.get(part, [])))
                    only_p = sorted(set(pk.get(part, [])) - set(rk.get(part, [])))
                    if only_r or only_p:
                        diffs.append((k, part, only_r, only_p))
        except KisError as e:
            diffs.append(("paper", "-", [f"모의 조회 실패: {str(e)[:120]}"], []))
    v = verdicts(raw["real"], a.expect_krw)
    sess = [market_session("KR", now), market_session("US", now), us_daytime_session(now)]
    EVID.mkdir(parents=True, exist_ok=True)
    jp = EVID / f"real_probe_{a.stamp}.json"
    jp.write_text(json.dumps({"generated_at": now.isoformat(), "account_mode": account_mode(),
                              "account_tail": real.cano[-4:] + "-" + real.acnt_prdt_cd, "raw": raw},
                             ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    L = [f"# 실계좌 조회 전용 점검 — {a.stamp} ({now:%Y-%m-%d %H:%M} KST · 계좌 모드 {account_mode()} · 주문 없음)", "",
         f"원본 응답: `{jp.relative_to(HERE)}`", "", "| 항목 | 판정 | 내용 | 응답 코드 |", "|---|---|---|---|"]
    for nm, ok, note, code in v:
        L.append(f"| {nm} | {'✓' if ok else '**✗**'} | {note} | {code} |")
    L += ["", "## 세션 창", ""]
    for s_ in sess:
        L.append(f"- {s_['why']} — 지금 {'열림' if s_['is_open'] else '닫힘'}")
    L += ["", "## 모의와 다른 응답 필드", ""]
    if not diffs:
        L.append("- 없음" if not a.no_paper else "- 대조 생략(--no-paper)")
    for k, part, only_r, only_p in diffs:
        L.append(f"- `{k}.{part}` — 실계좌에만: {', '.join(only_r) or '없음'} · 모의에만: {', '.join(only_p) or '없음'}")
    if nocmp:
        L.append(f"- 비교 불가(한쪽에 행이 없다 — 보유·체결이 생기면 다시 잰다): {', '.join(nocmp)}")
    bad = [nm for nm, ok, _, _ in v if not ok]
    L += ["", f"**판정: {'오류 0' if not bad else '확인 필요 — ' + ', '.join(bad)}**"]
    mp = EVID / f"real_probe_{a.stamp}.md"
    if mp.exists():
        prev = mp.read_text(encoding="utf-8")
        mp.write_text(prev.rstrip() + "\n\n---\n\n" + "\n".join(L) + "\n", encoding="utf-8")
    else:
        mp.write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
