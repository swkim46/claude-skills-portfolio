#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""자가 피드백 — 안 샀는데 크게 오른 종목을 **규칙 수정**으로 되먹인다(`trade-feedback` 스킬의 도구).

    python3 feedback.py collect --week 261004 [--days 20] [--threshold 10]      # → data/feedback/수집_<week>.md · .json
    python3 feedback.py trail --market kr --ticker 005930 [--peak 260923] [--days 35]   # 평가 이력
    python3 feedback.py propose add --file <json> [--week 261004]                # 원장에 제안(스키마 검증 · 거부)
    python3 feedback.py propose set --id FB-261004-3 --status applied --note "…"
    python3 feedback.py propose set --id FB-261004-3 --next-check 2026-10-17 --note "…"   # 검증일 연기
    python3 feedback.py propose list [--status proposed]
    python3 feedback.py metrics                                                   # 지표 현재값(오프라인 항목)
    python3 feedback.py verify --week 261011                                      # 검증일 도래 행 → 유지/철회 후보(적용 후 사건으로)
    python3 feedback.py status                                                    # 보고 첫 줄 + 승인 대기

왜 있는가 (2026-10-02 사용자): "매수하지 않았는데 엄청 오른 종목들이 있더라고 … 그걸 안 산 혹은 못 산 이유랑 과거에
어떤 평가들을 했어야 했는지에 대해 스스로 피드백하고 필요하면 스킬을 고치는 한이 있더라도 돈을 더 잘 벌 수 있는 방법을
끊임없이 연구해." 회고·아쉬움·주간 리포트는 숫자를 냈지만 **규칙으로 돌아가는 길**이 없었다 — 규칙은 사용자가 지적할
때만 바뀌었다. 이 도구가 그 길이다: 수집(스크립트) → 진단(모델) → 제안(원장) → 적용(skill-maintainer) → 검증(다음 주).

원장 규칙(`propose add`가 기계로 거부한다):
- **근거가 틀렸을 때만 고친다** — `verdict`가 `defect`가 아니면 `change`는 비어 있어야 한다(맞은 판단·운도 기록은 한다).
- **사례가 아니라 유형** — `cause`에 증거 종목의 이름·티커가 있으면 거부(고유명사를 바꿔도 성립해야 규칙이다).
- **되돌릴 수 있는 것만 자동** — `config/feedback_policy.json`이 없으면 전부 `confirm`(승인 대기). 있어도 안전장치·숫자
  상수(`safety`·`param`)와 정책의 `confirm_if` 낱말이 걸리면 `confirm`.
- **결함이면 기대 효과·검증일 필수** — `expected{metric, baseline, target}` · `next_check{date, metric}`.
"""
import argparse
import glob
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import regret as rg  # noqa: E402

KST = timezone(timedelta(hours=9))
LEDGER = HERE / "journal" / "feedback.jsonl"
POLICY = HERE / "config" / "feedback_policy.json"
OUTDIR = HERE / "data" / "feedback"
WEEK_DAYS = 20                 # 주간 창(거래일)
WEEK_THRESHOLD = 10.0          # 창 안 저점→고점 최대 상승폭
EARLY_PCT = 8.0                # 판 뒤 10거래일 안 고가가 매도가 대비 이만큼 오르면 '조기 매도'
LATE_PCT = -8.0                # 보유 중 고점 대비 이만큼 되돌렸는데 매도 다리가 안 나갔으면 '늦은 매도'
KINDS = ("rule", "tool", "universe", "param", "safety")
VERDICTS = ("defect", "right_call", "luck")
STATUSES = ("proposed", "confirmed", "applied", "verified", "reverted", "rejected")

METRICS = {
    "entry_reach_rate": "진입 기준가 도달률(%) — 최근 45일 논지의 `price <=` 진입 다리 중 발화",
    "miss_forecast_no_thesis_pct": "놓친 급등 중 전망O·논지X(%)",
    "miss_no_axis_pct": "놓친 급등 중 축 없음(%)",
    "miss_universe_out_pct": "놓친 급등 중 유니버스 밖(%)",
    "miss_entry_unreached_pct": "놓친 급등 중 진입가 미도달(%)",
    "early_sell_rate": "조기 매도 비율(%) — 체결된 매도 중 판 뒤 10거래일 고가 +8% 이상",
    "late_sell_rate": "늦은 매도 비율(%) — 보유 급등 중 고점 대비 −8% 되돌림에 매도 다리 미발화",
    "first_entry_avg_pct": "첫 진입 다리 평균 크기(%) — 최근 45일 논지",
    "excess_kr_pct": "주간 초과수익 KR(%p) — 계좌 vs 그림자 벤치마크",
    "excess_us_pct": "주간 초과수익 US(%p)",
    "invested_gap_kr_pct": "투자비중 목표 대비 부족 KR(%p)",
    "invested_gap_us_pct": "투자비중 목표 대비 부족 US(%p)",
    "return_30d_total_pct": "계좌 30일 수익률(%) — 양 시장 원화 합산 · 10%는 바닥(하한)이지 목표가 아니다 · 가동 30일 미만이면 가동 이후",
    # 도구 결함 검증용 — 도구를 고쳤으면 다음 주에 이 값이 움직여야 한다
    "watch_loop_checks_7d": "장중 감시 루프 점검 횟수(7일) — run 안의 1회성 점검(mode=intraday) 제외",
    "sell_rows_untagged_14d": "논지가 있는 종목의 모델 매도 체결 중 논지 꼬리표(thesis_id) 없는 행 수(14일)",
    "rate_limit_failed_orders_14d": "초당 한도(EGW00201)로 실패한 주문 수(14일)",
    "funds_rejects_with_sells_14d": "같은 승인에 매도가 있는데 자금 부족으로 거부된 매수 수(14일)",
    "snap_eval_total_mismatch_7d": "스냅샷 잔고 eval_total이 종목 평가금액 합+현금과 20% 넘게 어긋난 수(7일) — 아쉬움 감사 비중의 분모",
    "unsent_zero_qty_7d": "승인 수량이 남았는데 '0주 — 전송 안 함'으로 끝난 주문 수(7일)",
    "verify_pre_mixed_rows": "검증일이 도래한 적용 행 중 적용 전 사건이 섞인 창 지표로만 판정되는 행 수",
    # 시나리오·손실 차선 — FB-261012
    "scenario_open_over_20td": "오래 열린 시나리오 수 — 열린 지 20거래일 이상 또는 조건의 예정일에서 10거래일 지났는데 미실현(지연 가지 누락 후보)",
    "fired_retrace_no_cause_7d": "7일간 발화한 되돌림·지지 매수 다리 중 노트 §6 `[하락 원인]`이 없던 수(crosscheck 결과)",
    "held_without_trail": "보유 논지 중 고점 대비 추적 다리(from_peak_pct)가 없는 수",
    "loss_misjudged_rate": "최근 손실 판정 중 오판 비율(%) — 오판 / (오판+일시적+관찰) · 가격 하락만으로는 오판을 주지 않는다",
    "loss_verdict_flip_rate": "지난주 손실 판정이 뒤집힌 비율(%) — 일시적→손실로 닫힘(느슨) + 오판→고점 재돌파(빡빡)",
    "forecast_horizon_hit_rate": "기한이 끝나 판정된 전망 중 hit 비율(%)",
    # 교차 시장(2026-10-12 사용자: "국내장과 미국장은 독립적이지 않다")
    "forecast_single_market": "열린 전망 중 교차 검증 실패 수 — 한 시장 종목뿐인데 사유 없음 + 같은 요인 반대 방향 미연결",
    "scenario_rows_one_market_pct": "최근 7일 시그널 시나리오 행 중 한 시장 대응뿐이고 '영향 없음' 사유도 없는 비율(%)",
    # 실계좌 전환(2026-10-12) — 회전이 잦으면 비용이 수익을 먹는다
    "net_cost_drag_30d_pct": "최근 30일 체결 비용 추정(수수료·국내 매도세·환전) ÷ 자산(%) — config/costs.json 요율",
}


# 적용일 이후 사건만으로 다시 잴 수 있는 지표(검증용 · `post_metric`). 여기 없는 지표로 판정하면 적용 전 사건이 섞인
# 창 지표를 그대로 비교하게 된다 — 그 행 수가 `verify_pre_mixed_rows`다(FB-261010-4).
POST_EVENT = {"early_sell_rate", "late_sell_rate", "first_entry_avg_pct", "entry_reach_rate",
              "miss_forecast_no_thesis_pct", "miss_no_axis_pct", "miss_universe_out_pct", "miss_entry_unreached_pct",
              "sell_rows_untagged_14d", "rate_limit_failed_orders_14d", "funds_rejects_with_sells_14d",
              "watch_loop_checks_7d", "snap_eval_total_mismatch_7d", "unsent_zero_qty_7d"}
POST_STATE = {"excess_kr_pct", "excess_us_pct", "invested_gap_kr_pct", "invested_gap_us_pct", "return_30d_total_pct",
              "verify_pre_mixed_rows", "scenario_open_over_20td", "fired_retrace_no_cause_7d", "held_without_trail",
              "loss_misjudged_rate", "loss_verdict_flip_rate", "forecast_horizon_hit_rate",
              "forecast_single_market", "scenario_rows_one_market_pct",
              "net_cost_drag_30d_pct"}      # 시점 값 — 지금 값이 곧 적용 후 값
POST_METRICS = POST_EVENT | POST_STATE
MATURE_DAYS = 14                            # 조기 매도(판 뒤 10거래일)·진입 도달은 적어도 이만큼(달력일) 지난 사건만 센다
SNAP_MISMATCH = 0.2                         # 스냅샷 eval_total이 '종목 합+현금'과 이 비율 넘게 어긋나면 분모 오류로 센다 —
                                            # 정상 미국 스냅샷은 현금 몫(1~10%)만큼만 어긋나고, 오류였을 땐 17~109배였다


def _j(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _trades_same(f) -> list:
    """거래 기록의 trades — 지금 계좌 계열의 파일만(2026-10-15 · 실계좌 전환 뒤 모의 거래를 실계좌 지표로 세지 않게 · 표시 없는 옛 파일은 모의)."""
    from kis_client import same_account
    d = _j(f, {}) or {}
    return (d.get("trades") or []) if same_account(d) else []


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


# ───────────────────────────────────────────────────────────── 평가 이력

class Corpus:
    """평가 이력을 찾을 문서 묶음 — 재료 · 분석노트(날짜별 최신판) · 섹터 리서치 · 작업기록 · 시그널."""

    def __init__(self, lo: str, hi: str):
        self.docs = []                                  # (stamp, 종류, 파일명, [줄])

        def add(pattern, rx, kind, latest=False):
            picked = {}
            for f in sorted(glob.glob(str(HERE / pattern))):
                m = re.search(rx, Path(f).name)
                if not m or not (lo <= m.group(1) <= hi):
                    continue
                key = (m.group(1), Path(f).name.split("_v")[0]) if latest else f
                picked[key] = f
            for f in picked.values():
                m = re.search(rx, Path(f).name)
                try:
                    self.docs.append((m.group(1), kind, Path(f).name,
                                      Path(f).read_text(encoding="utf-8", errors="ignore").splitlines()))
                except OSError:
                    pass
        add("data/material_*.md", r"material_(\d{6})_", "재료")
        add("analysis/분석노트_*_v*.md", r"분석노트_(\d{6})_", "노트", latest=True)
        add("analysis/섹터_*.md", r"_(\d{6})_v", "리서치")
        add("journal/worklog_*.md", r"worklog_(\d{6})_", "작업기록")
        for f in sorted(glob.glob(str(HERE / "signals" / "signal_*.json"))):
            m = re.search(r"signal_(\d{6})_", Path(f).name)
            if m and lo <= m.group(1) <= hi:
                d = _j(f, {}) or {}
                lines = [json.dumps(p, ensure_ascii=False) for p in (d.get("proposals") or [])]
                lines += [f"no_trade_reason: {d.get('no_trade_reason')}"] if d.get("no_trade_reason") else []
                self.docs.append((m.group(1), "시그널", Path(f).name, lines))
        self.docs.sort(key=lambda x: (x[0], x[1]))


def trail(corpus: Corpus, ticker: str, name: str, theses: list = None, cap: int = 40) -> list:
    """`[{date, kind, file, section, line}]` — 그 이름이 우리 기록에 **언제 어떻게** 등장했는가."""
    out = []
    for st, kind, fn, lines in corpus.docs:
        head = ""
        hit_file = False
        for ln in lines:
            if ln.startswith("#"):
                head = ln.lstrip("# ").strip()[:40]
            if rg._mentions(ln, ticker, name):
                hit_file = True
                out.append({"date": st, "kind": kind, "file": fn, "section": head,
                            "line": re.sub(r"\s+", " ", ln.replace("|", "·")).strip()[:180]})
        if not hit_file:
            continue
    for t in theses or []:
        if t.get("ticker") != ticker:
            continue
        out.append({"date": rg._st(t.get("created")), "kind": "논지", "file": "theses.json", "section": t.get("id", ""),
                    "line": f"논지 등록 {t.get('id')} · {str(t.get('thesis') or '')[:120]}"})
        for h in t.get("history") or []:
            out.append({"date": rg._st(h.get("ts")), "kind": "논지", "file": "theses.json", "section": t.get("id", ""),
                        "line": f"{h.get('status')} · {str(h.get('note') or '')[:140]}"})
    out.sort(key=lambda r: (r["date"], r["kind"]))
    seen, uniq = set(), []
    for r in out:                                        # 같은 날 국내·미국 재료에 똑같이 실린 줄은 하나로
        k = (r["date"], r["kind"], r["line"])
        if k not in seen:
            seen.add(k); uniq.append(r)
    out = uniq
    if len(out) > cap:                                   # 처음·끝을 남긴다 — 첫 평가와 마지막 평가가 사후분석의 양 끝이다
        out = out[: cap // 2] + [{"date": "…", "kind": "", "file": "", "section": "",
                                  "line": f"(중간 {len(out) - cap}줄 생략)"}] + out[-cap // 2:]
    return out


def trail_summary(rows: list) -> str:
    if not rows:
        return "기록에 등장한 적 없음"
    cnt = {}
    for r in rows:
        if r["kind"]:
            cnt[r["kind"]] = cnt.get(r["kind"], 0) + 1
    first = next((r for r in rows if r["kind"]), rows[0])
    return (f"첫 등장 {first['date']} {first['kind']} · " +
            " · ".join(f"{k} {v}" for k, v in sorted(cnt.items(), key=lambda kv: -kv[1])))


# ───────────────────────────────────────────────────────────── 수집

def _sells(ctx, lo: str, hi: str) -> list:
    out = []
    for s, d in ctx.trades:
        if not (lo <= s <= hi):
            continue
        for t in d.get("trades") or []:
            f = t.get("fill") or {}
            if t.get("action") != "SELL" or (f.get("verdict") not in ("FILLED", "PARTIAL") and t.get("status") != "FILLED"):
                continue
            px = float(f.get("avg_price") or t.get("price") or 0)
            out.append({"stamp": s, "ticker": t.get("ticker"), "name": t.get("name", ""), "qty": f.get("filled_qty") or t.get("qty"),
                        "price": px, "source": t.get("source"), "trigger_id": t.get("trigger_id"),
                        "thesis_id": t.get("thesis_id")})
    return out


def early_sells(ctx, keep: dict, lo: str, hi: str) -> tuple:
    """판 뒤 10거래일 안에 고가가 매도가 대비 +8% 이상 간 매도. 반환 (조기 매도 목록, 체결 매도 수)."""
    sells = _sells(ctx, lo, hi)
    out = []
    for sv in sells:
        rows = keep.get(sv["ticker"]) or []
        after = [r for r in rows if rg._st(r["date"]) > sv["stamp"]][:10]
        if not after or not sv["price"]:
            continue
        top = max(after, key=lambda r: max(float(r.get("high") or 0), float(r["close"])))
        hi_px = max(float(top.get("high") or 0), float(top["close"]))
        g = (hi_px / sv["price"] - 1) * 100
        if g >= EARLY_PCT:
            out.append({**sv, "after_high": hi_px, "after_high_date": rg._st(top["date"]), "gain_after_pct": round(g, 2),
                        "days_seen": len(after)})
    return out, len(sells)


def late_sells(ctx, mvs: list) -> tuple:
    """보유 중이던 종목이 창 안 고점 대비 −8% 이상 되돌렸는데 고점 뒤 매도가 없던 것. 반환 (목록, 보유 급등 수)."""
    out, n_held = [], 0
    for mv in mvs:
        held, w, _ = rg._weight(ctx, mv["ticker"], mv.get("trough_date", ""), mv.get("peak_date", ""))
        if not held or mv["ret"] < EARLY_PCT:
            continue
        n_held += 1
        if mv.get("retrace_pct", 0) > LATE_PCT:
            continue
        sold_after = [s for s in _sells(ctx, mv["peak_date"], ctx.stamp) if s["ticker"] == mv["ticker"]]
        if sold_after:
            continue
        legs = []
        for t in ctx.theses:
            if t.get("ticker") != mv["ticker"] or t.get("status") == "closed":
                continue
            for x in t.get("exit_triggers") or []:
                m = re.search(r"price\s*>=\s*([\d.]+)", str(x.get("check") or ""))
                if m:
                    lvl = float(m.group(1))
                    legs.append(f"{t.get('id')} {x.get('id')} ≥ {lvl:,.2f} (고점 {mv['peak']:,.2f} 대비 "
                                f"{(lvl / mv['peak'] - 1) * 100:+.1f}%{' · 발화됨' if x.get('fired') else ''})")
        out.append({"ticker": mv["ticker"], "name": mv.get("name", ""), "peak": mv["peak"], "peak_date": mv["peak_date"],
                    "retrace_pct": round(mv["retrace_pct"], 2), "ret": round(mv["ret"], 2), "weight_max_pct": round(w, 2),
                    "profit_legs": legs or ["이익실현 다리 없음"]})
    return out, n_held


def _equity_rows() -> list:
    rows = []
    try:
        from kis_client import same_account                 # 지금 계좌 계열만(2026-10-15 · 실계좌 전환)
        for ln in (HERE / "journal" / "equity_curve.jsonl").read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(ln)
            except json.JSONDecodeError:
                continue
            if same_account(r):
                rows.append(r)
    except OSError:
        pass
    return rows


def _profit_floor() -> float:
    """수익 바닥(사용자 2026-10-03 — 하한이지 목표가 아니다 · 상한 없음). 값은 한도 파일 하나에서 읽는다."""
    try:
        return float(((_j(HERE / "config" / "limits.json", {}) or {}).get("position_sizing") or {}).get("min_target_gap_pct") or 10.0)
    except (TypeError, ValueError):
        return 10.0


PROFIT_FLOOR_PCT = _profit_floor()


def account_return(days: int = 30) -> dict:
    """양 시장 합산(원화) 수익률 — 최근 `days`일, 가동 기간이 짧으면 가동 이후. 환율은 최신 국내 스냅샷 값 하나로 양 끝을 잰다
    (환율 효과를 빼고 운용 성과만 본다). 반환 {pct, since, until, krw_now, floor_gap_pp}."""
    eq = _equity_rows()
    fx = 0.0
    snaps = sorted((HERE / "data").glob("snapshot_2*_kr.json"))
    for f in reversed(snaps[-3:]):
        fx = float(((_j(f, {}) or {}).get("balance") or {}).get("exchange_rate") or 0)
        if fx:
            break
    by = {}
    for r in eq:
        if r.get("equity") and r.get("market") in ("KR", "US") and r.get("date"):
            by.setdefault(r["market"], []).append(r)
    if not fx or set(by) != {"KR", "US"}:
        return {"pct": None}
    last_d = min(by["KR"][-1]["date"], by["US"][-1]["date"])
    cut = (datetime.strptime(last_d, "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
    def at(mk, d, first=False):
        rows = [r for r in by[mk] if (r["date"] >= d if first else r["date"] <= d)]
        return (rows[0] if first else rows[-1]) if rows else None
    k0 = at("KR", cut) or by["KR"][0]; u0 = at("US", cut) or by["US"][0]
    k1, u1 = at("KR", last_d), at("US", last_d)
    v0 = float(k0["equity"]) + float(u0["equity"]) * fx
    v1 = float(k1["equity"]) + float(u1["equity"]) * fx
    pct = (v1 / v0 - 1) * 100 if v0 > 0 else None
    return {"pct": round(pct, 2) if pct is not None else None, "since": max(k0["date"], u0["date"]), "until": last_d,
            "krw_now": round(v1), "floor_gap_pp": round(pct - PROFIT_FLOOR_PCT, 2) if pct is not None else None}


def account_line(days: int = 30) -> str:
    a = account_return(days)
    if a.get("pct") is None:
        return "계좌 30일 수익률 — 산정 불가(양 시장 저널 행 또는 환율 없음)"
    tag = "**바닥 미달**" if a["pct"] < PROFIT_FLOOR_PCT else "바닥 위"
    return (f"계좌 {a['since']}~{a['until']} 수익률 **{a['pct']:+.2f}%** (원화 합산 {a['krw_now']:,}원) · "
            f"바닥 {PROFIT_FLOOR_PCT:g}% 대비 {a['floor_gap_pp']:+.2f}%p — {tag} · 10%는 하한이지 목표가 아니다(상한 없음)")


def post_metric(metric: str, since: str, col: dict = None, cur: dict = None) -> tuple:
    """적용일(`since`, YYMMDD) 이후 사건만으로 지표를 다시 잰다 → (값, 표본 수, 메모). 표본이 없으면 값 None.

    창 지표(20거래일·14일·45일)는 대부분이 적용 전 사건이라 지난주 변경의 효과를 가린다 — 적용 전 매도만으로
    '철회 후보'가 나왔다(2026-10-10 · 조기 매도 7건 전부 10-03 적용 전). 그래서 검증은 이 값으로 한다.
    """
    cur = cur or {}
    if metric in POST_STATE:
        return cur.get(metric), 1, "시점 값"
    now = datetime.now(KST)
    mature = (now - timedelta(days=MATURE_DAYS)).strftime("%y%m%d")

    def stamped(pattern, rx):
        for f in sorted(glob.glob(str(HERE / pattern))):
            m = re.search(rx, f)
            if m and m.group(1) >= since:
                yield m.group(1), f
    if metric == "early_sell_rate":
        n = 0
        for st, f in stamped("journal/trades_*.json", r"trades_(\d{6})_"):
            if st > mature:
                continue
            n += sum(1 for t in _trades_same(f)
                     if t.get("action") == "SELL" and (t.get("fill") or {}).get("verdict") in ("FILLED", "PARTIAL"))
        e = sum(1 for x in ((col or {}).get("early_sells") or {}).get("rows") or [] if since <= x.get("stamp", "") <= mature)
        return (round(100 * e / n, 1) if n else None), n, f"판 지 {MATURE_DAYS}일 지난 적용 후 매도만"
    if metric in ("first_entry_avg_pct", "entry_reach_rate"):
        th = (_j(HERE / "journal" / "theses.json", {}) or {}).get("theses") or []
        th = [t for t in th if rg._st(t.get("created")) >= since]
        if metric == "first_entry_avg_pct":
            xs = [float(t["entry_triggers"][0]["size_pct"]) for t in th
                  if (t.get("entry_triggers") or [{}])[0].get("size_pct") is not None]
            return (round(sum(xs) / len(xs), 2) if xs else None), len(xs), "적용 후 세운 논지"
        legs = [e for t in th if rg._st(t.get("created")) <= mature for e in (t.get("entry_triggers") or [])
                if re.search(r"price\s*<=", str(e.get("check") or ""))]
        return (round(100 * sum(bool(e.get("fired")) for e in legs) / len(legs), 1) if legs else None), len(legs), \
            f"세운 지 {MATURE_DAYS}일 지난 적용 후 논지의 진입 다리"
    if metric.startswith("miss_") or metric == "late_sell_rate":
        mvs = [r for mk in ("kr", "us") for r in ((col or {}).get("markets", {}).get(mk, {}).get("movers") or [])
               if r.get("peak_date", "") >= since]
        if metric == "late_sell_rate":
            held = [r for r in mvs if r.get("position")]
            late = [x for x in ((col or {}).get("late_sells") or {}).get("rows") or [] if x.get("peak_date", "") >= since]
            return (round(100 * len(late) / len(held), 1) if held else None), len(held), "고점이 적용 후인 보유 급등"
        miss = [r for r in mvs if not r.get("position") or r.get("position_max_pct", 0) < rg.TINY_POS_PCT]
        if not miss:
            return None, 0, "고점이 적용 후인 놓친 급등 없음"
        f = {"miss_forecast_no_thesis_pct": lambda r: r["forecast"] and not r["thesis_live"],
             "miss_no_axis_pct": lambda r: not r["axis"],
             "miss_universe_out_pct": lambda r: rg._primary(r) == "universe_out",
             "miss_entry_unreached_pct": lambda r: any(b["type"] == "entry_unreached" for b in r["blockers"])}[metric]
        return round(100 * sum(bool(f(r)) for r in miss) / len(miss), 1), len(miss), "고점이 적용 후인 놓친 급등"
    if metric in ("sell_rows_untagged_14d", "rate_limit_failed_orders_14d", "unsent_zero_qty_7d"):
        th = (_j(HERE / "journal" / "theses.json", {}) or {}).get("theses") or []
        tk_th = {t.get("ticker") for t in th}
        v = n = 0
        for _, f in stamped("journal/trades_*.json", r"trades_(\d{6})_"):
            for t in _trades_same(f):
                txt = json.dumps(t.get("result") or {}, ensure_ascii=False) + str(t.get("error", ""))
                if metric == "sell_rows_untagged_14d":
                    if t.get("action") == "SELL" and t.get("source") == "llm" and t.get("ticker") in tk_th \
                            and (t.get("fill") or {}).get("verdict") in ("FILLED", "PARTIAL"):
                        n += 1; v += not t.get("thesis_id")
                else:
                    n += 1
                    v += (t.get("status") == "FAILED" and ("EGW00201" in txt if metric.startswith("rate") else "0주" in txt))
        return v, n, "적용 후 주문 기록"
    if metric == "funds_rejects_with_sells_14d":
        v = n = 0
        for _, f in stamped("signals/approved_*.json", r"approved_(\d{6})_"):
            a = _j(f, {}) or {}
            if any(o.get("action") == "SELL" and o.get("source") != "funding" for o in a.get("orders") or []):
                n += 1
                v += sum(1 for r in a.get("rejected") or [] if re.search(r"자금|현금", str(r.get("why") or "")))
        return v, n, "비회전 매도가 있던 적용 후 승인"
    if metric == "watch_loop_checks_7d":
        try:
            logs = [json.loads(l) for l in (HERE / "journal" / "stops_log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        except (OSError, json.JSONDecodeError):
            return None, 0, "stops_log 없음"
        lo = max(since, (now - timedelta(days=7)).strftime("%y%m%d"))
        return sum(1 for r in logs if rg._st(r.get("ts")) >= lo and r.get("mode") == "loop" and r.get("open")), 1, "최근 7일·적용 후"
    if metric == "snap_eval_total_mismatch_7d":
        v = n = 0
        for _, f in stamped("data/snapshot_2*_*.json", r"snapshot_(\d{6})_"):
            b = (_j(f, {}) or {}).get("balance") or {}
            tot = sum(float(p.get("eval_amt") or 0) for p in b.get("positions") or []) + max(0.0, float(b.get("cash") or 0))
            if tot > 0:
                n += 1; v += abs(float(b.get("eval_total") or 0) - tot) / tot > SNAP_MISMATCH
        return v, n, "적용 후 스냅샷"
    return None, 0, "적용 후 계산 없음"


def metrics(collected: dict = None) -> dict:
    """지표 현재값. 수집 결과가 있으면 급등·매도 항목도 채우고, 없으면 그 항목은 None(미측정 — 0이 아니다)."""
    out = {k: None for k in METRICS}
    since = (datetime.now(KST) - timedelta(days=45)).strftime("%y%m%d")
    th = (_j(HERE / "journal" / "theses.json", {}) or {}).get("theses") or []
    legs, fired, firsts = 0, 0, []
    for t in th:
        if rg._st(t.get("created")) < since:
            continue
        ent = t.get("entry_triggers") or []
        if ent and ent[0].get("size_pct") is not None:
            firsts.append(float(ent[0]["size_pct"]))
        for e in ent:
            if re.search(r"price\s*<=", str(e.get("check") or "")):
                legs += 1
                fired += bool(e.get("fired"))
    out["entry_reach_rate"] = round(100 * fired / legs, 1) if legs else None
    out["first_entry_avg_pct"] = round(sum(firsts) / len(firsts), 2) if firsts else None
    eq = _equity_rows()
    for mk in ("KR", "US"):
        rows = [r for r in eq if r.get("market") == mk and r.get("equity") and r.get("shadow_benchmark_equity")]
        if len(rows) >= 2:
            last = rows[-1]
            cut = (datetime.strptime(last["date"], "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")
            base = next((r for r in reversed(rows) if r["date"] <= cut), rows[0])
            out[f"excess_{mk.lower()}_pct"] = round(((last["equity"] / base["equity"]) -
                                                     (last["shadow_benchmark_equity"] / base["shadow_benchmark_equity"])) * 100, 2)
            al = [json.loads(l) for l in (HERE / "journal" / "allocation.jsonl").read_text(encoding="utf-8").splitlines()
                  if l.strip() and f'"market": "{mk.lower()}"' in l] if (HERE / "journal" / "allocation.jsonl").exists() else []
            if al and last.get("equity"):
                inv = (last["equity"] - float(last.get("cash") or 0)) / last["equity"] * 100
                out[f"invested_gap_{mk.lower()}_pct"] = round(float(al[-1].get("target_invested_pct") or 0) - inv, 1)
    s14 = (datetime.now(KST) - timedelta(days=14)).strftime("%y%m%d")
    s7 = (datetime.now(KST) - timedelta(days=7)).isoformat()
    try:
        logs = [json.loads(l) for l in (HERE / "journal" / "stops_log.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
        out["watch_loop_checks_7d"] = sum(1 for r in logs if str(r.get("ts", "")) >= s7 and r.get("mode") == "loop" and r.get("open"))
    except (OSError, json.JSONDecodeError):
        pass
    tk_th = {t.get("ticker") for t in th}
    untag, rl, fr = 0, 0, 0
    for f in sorted(glob.glob(str(HERE / "journal" / "trades_*.json"))):
        m = re.search(r"trades_(\d{6})_", f)
        if not m or m.group(1) < s14:
            continue
        for t in _trades_same(f):
            if (t.get("action") == "SELL" and t.get("source") == "llm" and not t.get("thesis_id")
                    and t.get("ticker") in tk_th and (t.get("fill") or {}).get("verdict") in ("FILLED", "PARTIAL")):
                untag += 1
            if t.get("status") == "FAILED" and "EGW00201" in json.dumps(t.get("result") or {}, ensure_ascii=False) + str(t.get("error", "")):
                rl += 1
    for f in sorted(glob.glob(str(HERE / "signals" / "approved_*.json"))):
        m = re.search(r"approved_(\d{6})_", f)
        if not m or m.group(1) < s14:
            continue
        a = _j(f, {}) or {}
        if any(o.get("action") == "SELL" and o.get("source") != "funding" for o in a.get("orders") or []):
            fr += sum(1 for r in a.get("rejected") or [] if re.search(r"자금|현금", str(r.get("why") or "")))
    out["sell_rows_untagged_14d"], out["rate_limit_failed_orders_14d"], out["funds_rejects_with_sells_14d"] = untag, rl, fr
    # 도구 결함 검증 — FB-261010
    s7 = (datetime.now(KST) - timedelta(days=7)).strftime("%y%m%d")
    mism = 0
    for f in sorted(glob.glob(str(HERE / "data" / "snapshot_2*_*.json"))):
        m = re.search(r"snapshot_(\d{6})_", f)
        if not m or m.group(1) < s7:
            continue
        b = (_j(f, {}) or {}).get("balance") or {}
        tot = sum(float(p.get("eval_amt") or 0) for p in b.get("positions") or []) + max(0.0, float(b.get("cash") or 0))
        if tot > 0 and abs(float(b.get("eval_total") or 0) - tot) / tot > SNAP_MISMATCH:
            mism += 1
    out["snap_eval_total_mismatch_7d"] = mism
    z = 0
    for f in sorted(glob.glob(str(HERE / "journal" / "trades_*.json"))):
        m = re.search(r"trades_(\d{6})_", f)
        if not m or m.group(1) < s7:
            continue
        for t in _trades_same(f):
            if t.get("status") == "FAILED" and "0주" in json.dumps(t.get("result") or {}, ensure_ascii=False) + str(t.get("error", "")):
                z += 1
    out["unsent_zero_qty_7d"] = z
    today = datetime.now(KST).strftime("%y%m%d")
    out["verify_pre_mixed_rows"] = sum(1 for r in ledger() if r.get("status") == "applied"
                                       and rg._st((r.get("next_check") or {}).get("date", "")) <= today
                                       and (r.get("expected") or {}).get("metric") not in POST_METRICS)
    out["return_30d_total_pct"] = account_return(30).get("pct")
    # 시나리오·손실 차선(FB-261012) — 도구가 못 돌면 None(미측정 — 0이 아니다)
    try:
        import loss_review as lr
        import theses as _th
        out["scenario_open_over_20td"] = len([r for r in lr.stale_scenarios() if r.get("id") != "?"])
        out["held_without_trail"] = len(_th.held_without_trail(th))
        out["loss_misjudged_rate"] = lr.misjudged_rate(datetime.now(KST).strftime("%y%m%d"))
        fl = ((collected or {}).get("losses") or {}).get("flips") or {}
        out["loss_verdict_flip_rate"] = fl.get("rate")
        try:
            import costs as _costs
            out["net_cost_drag_30d_pct"] = _costs.drag(30).get("pct")
        except Exception:                               # noqa: BLE001
            out["net_cost_drag_30d_pct"] = None
        ax = (_j(HERE / "journal" / "market_map.json", {}) or {}).get("axes") or []
        judged = [a for a in ax if a.get("status") in ("hit", "miss")]
        out["forecast_horizon_hit_rate"] = round(100 * sum(a["status"] == "hit" for a in judged) / len(judged), 1) if judged else None
        n_nc = 0
        for f in sorted(glob.glob(str(HERE / "data" / "run_evidence" / "crosscheck_*.md"))):
            m = re.search(r"crosscheck_(\d{6})_", f)
            if not m or m.group(1) < s7:
                continue
            mm = re.search(r"\[하락 원인\][^|]*\|\s*\*\*✗\*\*\s*\|[^|]*없는 (\d+)건", Path(f).read_text(encoding="utf-8", errors="ignore"))
            n_nc += int(mm.group(1)) if mm else 0
        out["fired_retrace_no_cause_7d"] = n_nc
        import market_map as _mm
        _ax = _mm._read(_mm.MAP, {"axes": []}).get("axes") or []
        out["forecast_single_market"] = sum(1 for a in _ax if a.get("direction") and (a.get("status") or "open") == "open"
                                            and _mm.forecast_validate(a, _ax))
        tot = one = 0
        for f in sorted(glob.glob(str(HERE / "signals" / "signal_2*_*.json"))):
            mm_ = re.search(r"signal_(\d{6})_", f)
            if not mm_ or mm_.group(1) < s7:
                continue
            for r_ in (_j(f, {}) or {}).get("scenarios") or []:
                if not isinstance(r_, dict):
                    continue
                txt = json.dumps(r_, ensure_ascii=False)
                mks = {str(k).lower() for k in (r_.get("responses") or {})} if isinstance(r_.get("responses"), dict) else set()
                mks |= {m_.lower() for m_ in re.findall(r"\[(KR|US)(?:\]|\s)", txt)}
                tot += 1
                one += len(mks) < 2
        out["scenario_rows_one_market_pct"] = round(100 * one / tot, 1) if tot else None
    except Exception as e:                                   # noqa: BLE001
        print(f"  (시나리오·손실 지표 일부 미측정: {type(e).__name__}: {e})", file=sys.stderr)
    if collected:
        miss = [r for mk in ("kr", "us") for r in (collected.get("markets", {}).get(mk, {}).get("movers") or [])
                if not r.get("position") or r.get("position_max_pct", 0) < rg.TINY_POS_PCT]
        n = len(miss)
        if n:
            pr = lambda r: rg._primary(r)
            out["miss_forecast_no_thesis_pct"] = round(100 * sum(r["forecast"] and not r["thesis_live"] for r in miss) / n, 1)
            out["miss_no_axis_pct"] = round(100 * sum(not r["axis"] for r in miss) / n, 1)
            out["miss_universe_out_pct"] = round(100 * sum(pr(r) == "universe_out" for r in miss) / n, 1)
            out["miss_entry_unreached_pct"] = round(100 * sum(any(b["type"] == "entry_unreached" for b in r["blockers"])
                                                              for r in miss) / n, 1)
        es, ls = collected.get("early_sells") or {}, collected.get("late_sells") or {}
        if es.get("n_sells"):
            out["early_sell_rate"] = round(100 * len(es.get("rows") or []) / es["n_sells"], 1)
        if ls.get("n_held"):
            out["late_sell_rate"] = round(100 * len(ls.get("rows") or []) / ls["n_held"], 1)
    return out


def _weekly_excerpt() -> str:
    files = sorted((HERE / "journal" / "weekly").glob("주간리포트_*.md"))
    if not files:
        return "(주간 리포트 없음)"
    txt = files[-1].read_text(encoding="utf-8", errors="ignore")
    i = txt.find("## 전망")
    return f"`{files[-1].name}`\n\n" + (txt[i:].strip()[:4000] if i >= 0 else "(④절 없음 — 주간 리포트가 중간에 멈췄을 수 있다)")


REAL_ROW = re.compile(r"^\|\s*(.+?)\s*\|\s*(★|✓|—)\s*\|\s*(.*?)\s*\|\s*$")


def real_checks(week: str, days: int = 7) -> dict:
    """이번 주 실전 점검표(`data/run_evidence/real_check_<st>_<mkt>.md` · 2026-10-15) — run별 머리줄 · 항목별 ★ 수와 예.
    왜: 사용자(10-15) "이번 주 남은 모의 run에서 실전 관점으로 점검·기록하고 토요일 trade-feedback으로 고친다." 점검표가 run마다 쌓이지만
    토요일이 읽지 않으면 기록으로 끝난다."""
    lo = rg._prev_stamp(week, days)
    runs, items = [], {}
    for f in sorted((HERE / "data" / "run_evidence").glob("real_check_*_*.md")):
        m = re.match(r"real_check_(\d{6})_(kr|us)\.md$", f.name)
        if not m or not (lo <= m.group(1) <= week):
            continue
        txt = f.read_text(encoding="utf-8", errors="ignore")
        head = next((ln.strip() for ln in txt.splitlines() if ln.startswith("[실전점검]")), "")
        runs.append({"stamp": m.group(1), "market": m.group(2), "head": head, "file": f.name})
        for ln in txt.splitlines():
            mm = REAL_ROW.match(ln)
            if not mm or mm.group(1) in ("항목", "---"):
                continue
            it = items.setdefault(mm.group(1), {"runs": 0, "star": 0, "examples": []})
            it["runs"] += 1
            if mm.group(2) == "★":
                it["star"] += 1
                it["examples"].append(f"{m.group(1)} {m.group(2).upper()}: {mm.group(3)[:110]}")
    return {"runs": runs, "items": items}


def collect(week: str, days: int = WEEK_DAYS, threshold: float = WEEK_THRESHOLD) -> dict:
    from kis_client import KisClient
    client = KisClient.for_mode()
    res = {"week": week, "at": _now(), "days": days, "threshold": threshold, "markets": {}}
    th = (_j(HERE / "journal" / "theses.json", {}) or {}).get("theses") or []
    corpus = Corpus(rg._prev_stamp(week, days * 2 + 10), week)
    es_rows, es_n, ls_rows, ls_n = [], 0, [], 0
    for mk in ("kr", "us"):
        errors, keep = [], {}
        mvs = rg.moves_fetch(mk, week, days, errors, client=client, keep=keep)
        ctx = rg.Ctx(mk, week)
        movers = rg.funnel(mk, week, threshold, days=days, mvs=mvs, ctx=ctx)
        for r in movers:
            tr = trail(corpus, r["ticker"], r["name"], th)
            r["trail"] = tr
            r["trail_summary"] = trail_summary(tr)
        below = [{k: m.get(k) for k in ("ticker", "name", "ret", "trough_date", "peak_date", "retrace_pct", "in_universe")}
                 for m in mvs if m["ret"] < threshold][:5]
        lo = min((m.get("from") or week) for m in mvs) if mvs else rg._prev_stamp(week, days * 2)
        e, n = early_sells(ctx, keep, lo, week)
        l2, n2 = late_sells(ctx, mvs)
        for x in e:
            x["market"] = mk
        for x in l2:
            x["market"] = mk
        es_rows += e; es_n += n; ls_rows += l2; ls_n += n2
        res["markets"][mk] = {"movers": movers, "below": below, "errors": errors, "measured": len(mvs)}
    res["early_sells"] = {"rows": es_rows, "n_sells": es_n}
    res["late_sells"] = {"rows": ls_rows, "n_held": ls_n}
    res["due"] = [r for r in ledger() if r.get("status") == "applied"
                  and rg._st((r.get("next_check") or {}).get("date", "")) <= week]
    # ★ 손실·시나리오 오판 차선(FB-261012-9) — 놓친 급등과 나란히 센다. 판정은 보고서 §2-b가 한다(가격 하락만으로 오판 금지).
    try:
        import loss_review as lr
        res["losses"] = lr.collect(week, days)
    except Exception as e:                                   # noqa: BLE001
        res["losses"] = {"error": f"{type(e).__name__}: {e}", "rows": [], "forecasts_due": [], "stale_scenarios": [],
                         "flips": {}, "net_realized": {}}
    res["metrics"] = metrics(res)
    res["weekly_excerpt"] = _weekly_excerpt()
    try:
        res["real_checks"] = real_checks(week)
    except Exception as e:                                   # noqa: BLE001
        res["real_checks"] = {"error": f"{type(e).__name__}: {e}", "runs": [], "items": {}}
    return res


def render_collect(res: dict) -> str:
    wk, days, th = res["week"], res["days"], res["threshold"]
    m = res["markets"]
    held_ok = sum(1 for mk in m for r in m[mk]["movers"] if r.get("position") and r.get("position_max_pct", 0) >= rg.TINY_POS_PCT)
    n_err = sum(len(m[mk]["errors"]) for mk in m)
    try:
        import run_tests as _rt                     # 2026-10-11 — 테스트가 멈춘 것을 모르고 지나가지 않게(수집 머리에 박는다)
        test_line = _rt.last_line()
    except Exception as e:                          # noqa: BLE001
        test_line = f"★ [테스트] 기록을 못 읽었다 — {type(e).__name__}: {e}"
    L = [f"# 피드백 수집 — {wk}", "", account_line(30), "", test_line, "",
         f"[수집] KR 급등 {len(m['kr']['movers'])} · US 급등 {len(m['us']['movers'])} · 그중 제대로 보유 {held_ok} · "
         f"조기 매도 {len(res['early_sells']['rows'])}/{res['early_sells']['n_sells']} · "
         f"늦은 매도 {len(res['late_sells']['rows'])}/{res['late_sells']['n_held']} · 지난주 적용 검증 대상 {len(res['due'])} · "
         f"손실·약세 후보 {len((res.get('losses') or {}).get('rows') or [])} · 기한 도래 전망 "
         f"{len((res.get('losses') or {}).get('forecasts_due') or [])} · 오래 열린 시나리오 "
         f"{len((res.get('losses') or {}).get('stale_scenarios') or [])} · 조회 실패 {n_err}", "",
         f"> 창 {days}거래일 · **저점→고점 최대 상승폭** ≥ +{th:.0f}% — 종가 대 종가가 아니므로 올랐다가 되돌린 종목도 들어온다. "
         f"측정 대상 = 유니버스 + 지도 + 시총 상위(`config/main_names.json`). 수집은 판정하지 않는다 — 결함·맞은 판단·운은 "
         f"`analysis/피드백_{wk}.md` §2가 가른다. 생성 {res['at']}", ""]
    for mk in ("kr", "us"):
        rows = m[mk]["movers"]
        L += [f"## {mk.upper()} — 급등 {len(rows)}건 (측정 {m[mk]['measured']}종목)", ""]
        body = rg.render(rows, mk, wk, days, th).split("\n", 2)[2] if rows else "*(해당 없음)*\n"
        L += [body.strip(), ""]
        if rows:
            L += ["**평가 이력 요약** (전체 줄은 수집 JSON의 `trail`):", ""]
            for r in rows:
                L.append(f"- {r['ticker']} {r['name']} — {r['trail_summary']}")
            L.append("")
        if m[mk]["below"]:
            L.append("문턱 아래 상위: " + " · ".join(f"{b['ticker']} {b['name']} {b['ret']:+.1f}%" for b in m[mk]["below"]))
            L.append("")
    L += ["## 조기 매도 — 판 뒤 10거래일 안 고가 +8% 이상", ""]
    es = res["early_sells"]["rows"]
    if es:
        L += ["| 시장 | 종목 | 매도일 | 매도가 | 원인 | 그 뒤 고가(일자) | 매도가 대비 |", "|---|---|---|---|---|---|---|"]
        for x in es:
            L.append(f"| {x['market'].upper()} | {x['ticker']} {x['name']} | {x['stamp']} | {x['price']:,.2f} | "
                     f"{x['source']}{(' ' + x['trigger_id']) if x.get('trigger_id') else ''} | {x['after_high']:,.2f} ({x['after_high_date']}) | "
                     f"{x['gain_after_pct']:+.1f}% |")
    else:
        L.append("*(없음)*")
    L += ["", "## 늦은 매도 — 보유 중 고점 대비 −8% 이상 되돌렸는데 고점 뒤 매도 없음", ""]
    ls = res["late_sells"]["rows"]
    if ls:
        L += ["| 시장 | 종목 | 고점(일자) | 되돌림 | 최대 비중 | 이익실현 다리 |", "|---|---|---|---|---|---|"]
        for x in ls:
            L.append(f"| {x['market'].upper()} | {x['ticker']} {x['name']} | {x['peak']:,.2f} ({x['peak_date']}) | "
                     f"{x['retrace_pct']:+.1f}% | {x['weight_max_pct']:.1f}% | {' / '.join(x['profit_legs'])} |")
    else:
        L.append("*(없음)*")
    lo_ = res.get("losses") or {}
    if lo_.get("error"):
        L += ["", "## 손실·약세 후보", "", f"**수집 실패 — {lo_['error']}** ('없음'으로 읽지 말 것)"]
    elif lo_:
        import loss_review as lr
        L += ["", lr.render(lo_).rstrip()]
    L += ["", "## 지표 — 현재값 (제안의 baseline·검증의 비교값)", "", "| 지표 | 값 |", "|---|---|"]
    for k, v in res["metrics"].items():
        L.append(f"| `{k}` {METRICS[k]} | {'미측정' if v is None else v} |")
    L += ["", "## 지난주 적용분 — 검증일 도래", ""]
    if res["due"]:
        L += ["| id | 변경 | 지표 | baseline → target | 현재 |", "|---|---|---|---|---|"]
        for r in res["due"]:
            e = r.get("expected") or {}
            cur = res["metrics"].get(e.get("metric"))
            L.append(f"| {r['id']} | {(r.get('change') or {}).get('summary', '')[:60]} | {e.get('metric')} | "
                     f"{e.get('baseline')} → {e.get('target')} | {'미측정' if cur is None else cur} |")
    else:
        L.append("*(없음)*")
    rc_ = res.get("real_checks") or {}
    L += ["", "## 실전 준비 — 이번 주 실전 점검표(2026-10-15 · 토요일이 못 미친 항목을 고친다)", ""]
    if rc_.get("error"):
        L.append(f"**수집 실패 — {rc_['error']}** ('없음'으로 읽지 말 것)")
    elif not rc_.get("runs"):
        L.append("*(이번 주 점검표 없음 — `stage.py close`가 run마다 만든다)*")
    else:
        for r in rc_["runs"]:
            L.append(f"- {r['head'] or r['file']}")
        L += ["", "| 항목 | run | ★ | ★ 예(최근 둘) |", "|---|---|---|---|"]
        for name, it in sorted(rc_["items"].items(), key=lambda kv: (-kv[1]["star"], kv[0])):
            L.append(f"| {name} | {it['runs']} | {it['star']} | {' / '.join(it['examples'][-2:]) or '—'} |")
    L += ["", "## 주간 리포트 ④ 발췌", "", res["weekly_excerpt"], ""]
    errs = [f"{mk.upper()} {e}" for mk in m for e in m[mk]["errors"]]
    if errs:
        L += ["## 조회 실패 — 급등 여부 미확인('없음'으로 읽지 말 것)", "", " · ".join(errs), ""]
    return "\n".join(L) + "\n"


# ───────────────────────────────────────────────────────────── 원장

def ledger() -> list:
    rows = []
    if LEDGER.exists():
        for ln in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError:
                pass
    return rows


def _write(rows: list) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def policy() -> dict:
    return _j(POLICY, None) or {}


def autonomy_for(row: dict, pol: dict) -> tuple:
    """(autonomy, 이유). 정책이 없으면 전부 confirm."""
    if not pol:
        return "confirm", "권한 정책 미정(config/feedback_policy.json 없음) — 전부 승인 대기"
    if row.get("kind") in ("safety", "param") or row.get("kind") not in (pol.get("auto") or []):
        return "confirm", f"종류 {row.get('kind')} — 정책상 승인 대상"
    text = json.dumps(row.get("change") or {}, ensure_ascii=False)
    hit = [w for w in (pol.get("confirm_if") or []) if w in text]
    if hit:
        return "confirm", f"변경 문구에 {hit} — 정책상 승인 대상"
    return (row.get("autonomy") or "auto"), "정책 auto 범위"


def validate(row: dict) -> list:
    """거부 사유 목록(빈 목록이면 통과)."""
    errs = []
    if row.get("kind") not in KINDS:
        errs.append(f"kind는 {KINDS} 중 하나")
    if row.get("verdict") not in VERDICTS:
        errs.append(f"verdict는 {VERDICTS} 중 하나")
    ev = row.get("evidence") or []
    if not ev:
        errs.append("evidence가 비었다 — 어느 종목·창·막은 유형에서 나온 제안인가")
    cause = str(row.get("cause") or "")
    if not cause.strip():
        errs.append("cause가 비었다 — 유형 문장으로")
    for e in ev:
        for tok in (e.get("ticker"), e.get("name")):
            tok = str(tok or "").strip()
            if len(tok) >= 2 and tok in cause:
                errs.append(f"cause에 증거 종목 '{tok}'이 있다 — 사례가 아니라 유형으로 써라(고유명사를 바꿔도 성립해야 한다)")
    if row.get("verdict") != "defect":
        if row.get("change"):
            errs.append("결함이 아닌데 change가 있다 — 근거가 틀렸을 때만 규칙을 고친다(맞은 판단·운은 기록만)")
    else:
        ch = row.get("change") or {}
        if not (ch.get("file") and ch.get("summary")):
            errs.append("결함이면 change{file, section, summary}가 필요하다")
        ex, nc = row.get("expected") or {}, row.get("next_check") or {}
        if ex.get("metric") not in METRICS:
            errs.append(f"expected.metric은 {list(METRICS)} 중 하나")
        if ex.get("baseline") is None or ex.get("target") is None:
            errs.append("expected.baseline·target이 필요하다(지금 값 → 기대 값)")
        if not nc.get("date"):
            errs.append("next_check.date가 필요하다(언제 검증하나)")
    return errs


def cmd_propose_add(path: str, week: str) -> int:
    data = _j(path, None)
    if data is None:
        print(f"파일을 못 읽었다: {path}", file=sys.stderr); return 2
    items = data if isinstance(data, list) else [data]
    rows, pol, bad = ledger(), policy(), 0
    for it in items:
        wk = str(it.get("week") or week or "")
        if not re.fullmatch(r"\d{6}", wk):
            print(f"✗ week(YYMMDD)가 없다: {str(it)[:80]}", file=sys.stderr); bad += 1; continue
        errs = validate(it)
        if errs:
            bad += 1
            print(f"✗ 거부 — {str(it.get('cause'))[:60]}\n    " + "\n    ".join(errs), file=sys.stderr)
            continue
        n = 1 + sum(1 for r in rows if r.get("week") == wk)
        row = {"id": f"FB-{wk}-{n}", "week": wk, **{k: it.get(k) for k in
               ("kind", "verdict", "evidence", "cause", "change", "expected", "next_check", "note")},
               "status": "proposed", "created_at": _now(), "applied_at": None, "history": []}
        if row["verdict"] == "defect":
            row["autonomy"], why = autonomy_for({**it, **row}, pol)
            row["autonomy_why"] = why
        else:
            row["autonomy"], row["autonomy_why"] = None, "결함 아님 — 고칠 것 없음(기록)"
        rows.append(row)
        print(f"✓ {row['id']} · {row['kind']} · {row['verdict']} · {row['autonomy'] or '-'} — {row['cause'][:70]}")
    _write(rows)
    return 1 if bad else 0


def cmd_propose_set(rid: str, status: str, note: str, next_check: str = "") -> int:
    if next_check:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", next_check):
            print("--next-check는 YYYY-MM-DD", file=sys.stderr); return 2
        rows = ledger()
        hit = [r for r in rows if r.get("id") == rid]
        if not hit:
            print(f"그런 id가 없다: {rid}", file=sys.stderr); return 2
        r = hit[0]
        old = (r.get("next_check") or {}).get("date")
        r["next_check"] = {**(r.get("next_check") or {}), "date": next_check}
        r.setdefault("history", []).append({"at": _now(), "from": r.get("status"), "to": r.get("status"),
                                            "note": f"검증일 {old} → {next_check}" + (f" · {note}" if note else "")})
        _write(rows)
        print(f"✓ {rid}: 검증일 {old} → {next_check}")
        if not status:
            return 0
    if status not in STATUSES:
        print(f"status는 {STATUSES} 중 하나", file=sys.stderr); return 2
    rows = ledger()
    hit = [r for r in rows if r.get("id") == rid]
    if not hit:
        print(f"그런 id가 없다: {rid}", file=sys.stderr); return 2
    r = hit[0]
    if status == "applied" and r.get("autonomy") == "confirm" and r.get("status") != "confirmed":
        print(f"✗ {rid}는 승인 대기(confirm)다 — 사용자 승인(`--status confirmed`) 없이 적용 표시를 할 수 없다", file=sys.stderr)
        return 3
    if status == "applied" and r.get("verdict") != "defect":
        print(f"✗ {rid}는 결함 판정이 아니다 — 적용할 변경이 없다", file=sys.stderr); return 3
    r.setdefault("history", []).append({"at": _now(), "from": r.get("status"), "to": status, "note": note})
    r["status"] = status
    if status == "applied":
        r["applied_at"] = _now()
    _write(rows)
    print(f"✓ {rid}: {r['history'][-1]['from']} → {status}")
    return 0


def cmd_list(status: str) -> int:
    rows = [r for r in ledger() if not status or r.get("status") == status]
    print(f"■ 피드백 원장 — {len(rows)}행" + (f" (status={status})" if status else ""))
    for r in rows:
        print(f"  {r['id']} · {r.get('kind')} · {r.get('verdict')} · {r.get('autonomy') or '-'} · {r.get('status')} — "
              f"{str(r.get('cause'))[:80]}")
    return 0


def cmd_verify(week: str) -> int:
    """검증일이 도래한 적용 행 → 유지·철회 후보. **판정은 적용일 이후 사건만으로 다시 잰 값(`post_metric`)으로 한다** —
    창 지표(수집값)는 참고로만 보인다. 적용 후 표본이 없으면 '미측정 — 한 주 연기'(`propose set --next-check`)."""
    col = _j(OUTDIR / f"수집_{week}.json", None)
    cur = metrics(col) if col is None else (col.get("metrics") or metrics(col))
    due = [r for r in ledger() if r.get("status") == "applied"
           and rg._st((r.get("next_check") or {}).get("date", "")) <= week]
    print(f"■ 검증 — {week} · 도래 {len(due)}행" + ("" if col else " (수집 JSON 없음 — 급등·매도 지표는 미측정)"))
    if not due:
        print("  없음."); return 0
    print("| id | 지표 | baseline → target | 창 지표(참고) | 적용 후 값(표본) | 판정 |\n|---|---|---|---|---|---|")
    for r in due:
        e = r.get("expected") or {}
        b, t, m = e.get("baseline"), e.get("target"), e.get("metric")
        since = rg._st(r.get("applied_at") or r.get("created_at") or "")
        c, n, memo = post_metric(m, since, col, cur)
        if c is None or b is None or t is None:
            v = f"미측정({memo}) — 한 주 연기"
        else:
            d = (float(c) - float(b)) * (1 if float(t) >= float(b) else -1)
            v = "유지(verified 후보)" if d > 0 else "철회 후보(revert)" if d < 0 else "변화 없음 — 한 주 더"
        print(f"| {r['id']} | {m} | {b} → {t} | {'미측정' if cur.get(m) is None else cur.get(m)} | "
              f"{'—' if c is None else c} (n={n} · {memo}) | {v} |")
    return 0


def status_line() -> str:
    rows = ledger()
    c = lambda s: sum(1 for r in rows if r.get("status") == s)
    wait = [r for r in rows if r.get("status") == "proposed" and r.get("autonomy") == "confirm"]
    return (f"[피드백] 원장 {len(rows)}행 · 승인 대기 {len(wait)} · 적용 {c('applied')} · 검증 완료 {c('verified')} · "
            f"철회 {c('reverted')} · 기각 {c('rejected')}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("collect"); c.add_argument("--week", default=datetime.now(KST).strftime("%y%m%d"))
    c.add_argument("--days", type=int, default=WEEK_DAYS); c.add_argument("--threshold", type=float, default=WEEK_THRESHOLD)
    c.add_argument("--out", default="")
    t = sub.add_parser("trail"); t.add_argument("--market", choices=["kr", "us"], required=True)
    t.add_argument("--ticker", required=True); t.add_argument("--name", default="")
    t.add_argument("--peak", default=datetime.now(KST).strftime("%y%m%d")); t.add_argument("--days", type=int, default=35)
    p = sub.add_parser("propose"); ps = p.add_subparsers(dest="pcmd", required=True)
    pa = ps.add_parser("add"); pa.add_argument("--file", required=True); pa.add_argument("--week", default="")
    pst = ps.add_parser("set"); pst.add_argument("--id", required=True); pst.add_argument("--status", default="")
    pst.add_argument("--note", default=""); pst.add_argument("--next-check", default="", help="검증일을 미룬다(YYYY-MM-DD)")
    pl = ps.add_parser("list"); pl.add_argument("--status", default="")
    sub.add_parser("metrics")
    v = sub.add_parser("verify"); v.add_argument("--week", default=datetime.now(KST).strftime("%y%m%d"))
    sub.add_parser("status")
    a = ap.parse_args()

    if a.cmd == "collect":
        res = collect(a.week, a.days, a.threshold)
        OUTDIR.mkdir(parents=True, exist_ok=True)
        md = Path(a.out) if a.out else OUTDIR / f"수집_{a.week}.md"
        md.write_text(render_collect(res), encoding="utf-8")
        md.with_suffix(".json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
        print(render_collect(res).split("\n")[2])
        print(f"→ {md}\n→ {md.with_suffix('.json')}")
        return 0
    if a.cmd == "trail":
        wl = {r["ticker"]: r.get("name", "") for r in ((_j(HERE / "config" / "watchlist.json", {}) or {}).get(a.market.upper()) or [])}
        name = a.name or wl.get(a.ticker, "")
        rows = trail(Corpus(rg._prev_stamp(a.peak, a.days), a.peak), a.ticker, name,
                     (_j(HERE / "journal" / "theses.json", {}) or {}).get("theses") or [], cap=200)
        print(f"■ 평가 이력 — {a.ticker} {name} · {rg._prev_stamp(a.peak, a.days)}~{a.peak} · {len(rows)}줄")
        print("| 날짜 | 종류 | 파일 | 절 | 줄 |\n|---|---|---|---|---|")
        for r in rows:
            print(f"| {r['date']} | {r['kind']} | {r['file']} | {r['section']} | {r['line']} |")
        return 0
    if a.cmd == "propose":
        if a.pcmd == "add":
            return cmd_propose_add(a.file, a.week)
        if a.pcmd == "set":
            return cmd_propose_set(a.id, a.status, a.note, a.next_check)
        return cmd_list(a.status)
    if a.cmd == "metrics":
        for k, v in metrics().items():
            print(f"  {k:30s} {'미측정' if v is None else v}   — {METRICS[k]}")
        return 0
    if a.cmd == "verify":
        return cmd_verify(a.week)
    if a.cmd == "status":
        print(status_line())
        for r in ledger():
            if r.get("status") == "proposed" and r.get("autonomy") == "confirm":
                print(f"  승인 대기 {r['id']} · {r.get('kind')} — {str(r.get('cause'))[:70]} → "
                      f"{(r.get('change') or {}).get('summary', '')[:70]}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
