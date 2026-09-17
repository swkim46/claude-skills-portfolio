#!/usr/bin/env python3
"""
현황 페이지 생성기 — 로컬 기록 → 자족 HTML 한 장.

읽기 전용이다. 매매 상태를 바꾸지 않고, 네트워크도 쓰지 않는다.
출력은 `dashboard/index.html` 하나이며 데이터가 안에 박혀 있어 `file://`로도 열린다
(자족 HTML 한 장 패턴). 그 파일을 Artifact로 올리면 폰에서 본다.

★ 절대 읽지 않는 것: `.env` · 토큰 캐시 · `data/`(스냅샷 원본).
   코드로 막는다 — 이 페이지는 공유될 수 있는 물건이므로 "안 읽는다"를 규율이 아니라
   함수로 강제한다. 스냅샷이 없어도 되는 이유: equity_curve의 positions[]에 가격·손익이
   이미 있다.

이 페이지가 답해야 하는 질문은 "얼마 벌었나"가 아니라
  ① 규율대로 집행했나 ② 사고 없었나 ③ 기록이 빠짐없나
다(조사 리포트 §5). 수익률은 벤치마크와 나란히 놓일 때만 의미가 있다.

사용:
    python3 dashboard.py            # dashboard/index.html 생성
    python3 dashboard.py --open     # 만들고 브라우저로 열기
"""
import argparse
import html
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
CONFIG_DIR = HERE / "config"
JOURNAL_DIR = HERE / "journal"
SIGNALS_DIR = HERE / "signals"
OUT_DIR = HERE / "dashboard"
KST = timezone(timedelta(hours=9))

MIN_POINTS_FOR_CHART = 5      # 점 2개로 선을 그리면 시각화가 아니라 거짓말이다.
RECENT_RUNS = 10

# 읽으면 안 되는 경로. 부분 문자열로 막는다.
_FORBIDDEN = ("/.env", ".token_cache.json", "/data/")


def _read(path: Path, default=None):
    """민감 경로를 코드로 차단하는 유일한 읽기 통로.

    상대경로로 넘어와도 뚫리지 않게 절대경로로 정규화한 뒤 검사한다
    (`data/x.json`은 `/data/`를 포함하지 않는다).
    """
    s = str(Path(path).expanduser().resolve())
    for f in _FORBIDDEN:
        if f in s:
            raise RuntimeError(f"대시보드는 이 파일을 읽지 않는다: {path}")
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _read_jsonl(path: Path) -> list:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def latest_per_day(rows: list) -> list:
    """(날짜, 시장)마다 마지막 행만. journal.latest_per_day와 같은 규칙이다."""
    keep = {}
    for r in rows:
        keep[(r.get("date"), r.get("market"))] = r
    return list(keep.values())


def e(x) -> str:
    return html.escape("" if x is None else str(x), quote=True)


def money(v, cur="KRW") -> str:
    if v is None:
        return "미확인"
    return f"{v:,.0f}" if cur == "KRW" else f"{v:,.2f}"


def pct(v, digits=2) -> str:
    return "—" if v is None else f"{v:+.{digits}f}%"


# ------------------------------------------------------------------ 데이터 수집

def collect() -> dict:
    # equity_curve는 append-only라 같은 날 행이 여러 개 있을 수 있다(하루 두 슬롯·재시도).
    # 그대로 그리면 추이에 같은 날 점이 겹치고 누적 수치가 부풀어 보인다 → 날짜별 마지막만.
    equity = latest_per_day(_read_jsonl(JOURNAL_DIR / "equity_curve.jsonl"))
    runs = _read_jsonl(JOURNAL_DIR / "run_log.jsonl")
    limits = _read(CONFIG_DIR / "limits.json", {}) or {}
    bench = _read(CONFIG_DIR / "benchmark.json", {}) or {}

    trades = []
    for p in sorted(JOURNAL_DIR.glob("trades_*.json")):
        d = _read(p, {}) or {}
        for t in d.get("trades", []):
            trades.append({**t, "_file": p.name})

    approved = []
    for p in sorted(SIGNALS_DIR.glob("approved_*.json")):
        d = _read(p, {}) or {}
        approved.append({"file": p.name, "market": d.get("market"),
                         "generated_at": d.get("generated_at"),
                         "orders": d.get("orders", []), "rejected": d.get("rejected", [])})
    approved.sort(key=lambda a: a.get("generated_at") or "")

    fails = sorted(p.name for p in CONFIG_DIR.glob("FAIL_*"))
    return {
        "equity": equity, "runs": runs, "trades": trades, "approved": approved,
        "limits": limits, "benchmark": bench,
        "kill": (CONFIG_DIR / "KILL").exists(), "fail_markers": fails,
        "generated_at": datetime.now(KST),
    }


def latest_by_market(rows: list) -> dict:
    out = {}
    for r in rows:
        m = r.get("market")
        if m:
            out[m] = r
    return out


def discipline_stats(d: dict) -> dict:
    """성공 지표 3종. 수익률이 아니라 이게 이 시스템의 채점표다."""
    trades = d["trades"]
    # status는 전송 상태다 — 9/15 세션이 손수 FILLED로 고쳐 쓴 행도 '나간 것'으로 센다(fill.py 규칙).
    sent = [t for t in trades if t.get("status") not in ("FAILED", "UNKNOWN", None)]
    failed = [t for t in trades if t.get("status") == "FAILED"]
    unknown = [t for t in trades if t.get("status") == "UNKNOWN"]
    disc = [t for t in trades if t.get("source") == "discipline"]
    disc_done = [t for t in disc if t.get("status") not in ("FAILED", "UNKNOWN", None)]

    runs = d["runs"]
    real_runs = [r for r in runs if not str(r.get("outcome", "")).startswith("skipped")
                 and r.get("outcome") != "self_test"]
    logged = [r for r in real_runs if (r.get("journal") or {}).get("equity_row_added")]
    consec = runs[-1].get("consecutive_failures", 0) if runs else 0

    anchors = [m for m in ("KR", "US") if m in (d["benchmark"] or {})]
    return {
        "discipline_total": len(disc), "discipline_done": len(disc_done),
        "discipline_rate": (100.0 * len(disc_done) / len(disc)) if disc else None,
        "sent": len(sent), "failed": len(failed), "unknown": len(unknown),
        "runs_total": len(real_runs), "runs_logged": len(logged),
        "equity_rows": len(d["equity"]), "anchors": anchors,
        "consecutive_failures": consec,
    }


# ------------------------------------------------------------------ 상태 판정

OUTCOME_LABEL = {
    "ok": ("정상", "good"), "no_trade": ("안 사는 날", "neutral"),
    "market_closed": ("휴장·장마감", "muted"), "skipped_weekend": ("주말", "muted"),
    "skipped_closed": ("장 시간 아님", "muted"),
    "skipped_already_ran": ("오늘 이미 실행됨", "muted"),
    "skipped_kill": ("정지(KILL)", "critical"), "halted_selfstop": ("자가정지", "critical"),
    "self_test": ("셀프테스트", "muted"),
}


def outcome_view(outcome: str) -> tuple:
    if not outcome:
        return ("기록 없음", "muted")
    if outcome in OUTCOME_LABEL:
        return OUTCOME_LABEL[outcome]
    if outcome.startswith("failed_"):
        return (f"실패 ({outcome[7:]})", "critical")
    return (outcome, "neutral")


def today_rows(d: dict) -> dict:
    """각 시장의 최신 run. 오늘 것이 없으면 마지막 것을 날짜와 함께 보여준다 —
    '기록 없음'보다 '9/5 이후 안 돌았음'이 훨씬 쓸모 있는 정보다(결측 자체가 신호)."""
    today = d["generated_at"].strftime("%Y-%m-%d")
    out = {}
    for r in d["runs"]:
        m = r.get("market")
        if m:
            out[m] = {**r, "_stale": r.get("session_date") != today}
    return out


# ------------------------------------------------------------------ SVG 차트

def sparkline(points: list, bench_points: list, width=680, height=170) -> str:
    """equity와 그림자 벤치마크를 **같은 축**에 겹쳐 그린다.

    벤치마크 병기가 이 프로젝트의 성공 지표라 equity 단독 차트는 그리지 않는다.
    이중 축은 쓰지 않는다 — 둘 다 같은 통화의 금액이라 한 축이 맞다.
    """
    vals = [v for v in points + bench_points if v is not None]
    if len(points) < MIN_POINTS_FOR_CHART or not vals:
        return ""
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.12 or (abs(hi) * 0.02 or 1)
    lo, hi = lo - pad, hi + pad
    ml, mr, mt, mb = 8, 8, 10, 18
    iw, ih = width - ml - mr, height - mt - mb

    def path(series):
        pts = []
        n = max(len(series) - 1, 1)
        for i, v in enumerate(series):
            if v is None:
                continue
            x = ml + iw * i / n
            y = mt + ih * (1 - (v - lo) / (hi - lo))
            pts.append(f"{x:.1f},{y:.1f}")
        return "M" + " L".join(pts) if pts else ""

    p1, p2 = path(points), path(bench_points)
    return f'''<svg class="spark" viewBox="0 0 {width} {height}" role="img"
     aria-label="계좌 평가액과 그림자 벤치마크 추이">
  <path d="{p2}" fill="none" stroke="var(--series-2)" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="5 4"/>
  <path d="{p1}" fill="none" stroke="var(--series-1)" stroke-width="2"
        stroke-linecap="round" stroke-linejoin="round"/>
</svg>'''


# ------------------------------------------------------------------ HTML

CSS = """
:root{color-scheme:light dark;
 --page:#f7f6f2; --surface:#fcfcfb; --ink:#12120f; --ink2:#4d4c47; --ink3:#84837a;
 --rule:#e6e4dc; --rule2:#d5d2c7;
 --series-1:#2a78d6; --series-2:#eb6834;
 --good:#0ca30c; --warning:#fab219; --serious:#ec835a; --critical:#d03b3b;
 --f-sans:"IBM Plex Sans KR","IBM Plex Sans",-apple-system,BlinkMacSystemFont,
   "Apple SD Gothic Neo","Malgun Gothic",sans-serif;
 --f-mono:"IBM Plex Mono",ui-monospace,SFMono-Regular,Menlo,monospace;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){
 --page:#111211; --surface:#1a1a19; --ink:#f4f3ee; --ink2:#c3c2b7; --ink3:#8b8a81;
 --rule:#2c2c29; --rule2:#3c3b37;
 --series-1:#3987e5; --series-2:#d95926;}}
:root[data-theme="dark"]{
 --page:#111211; --surface:#1a1a19; --ink:#f4f3ee; --ink2:#c3c2b7; --ink3:#8b8a81;
 --rule:#2c2c29; --rule2:#3c3b37;
 --series-1:#3987e5; --series-2:#d95926;}
*{box-sizing:border-box}
body{margin:0;background:var(--page);color:var(--ink);font-family:var(--f-sans);
 font-size:16px;line-height:1.55;-webkit-text-size-adjust:100%}
.wrap{max-width:720px;margin:0 auto;padding:26px 18px 60px}

/* 머리 — 장부의 표제부 */
.head{display:flex;align-items:baseline;justify-content:space-between;gap:12px;
 padding-bottom:12px;border-bottom:2px solid var(--ink);margin-bottom:20px;flex-wrap:wrap}
h1{font-size:19px;font-weight:600;margin:0;letter-spacing:-.015em}
.mode{font-family:var(--f-mono);font-size:11px;letter-spacing:.08em;text-transform:uppercase;
 color:var(--ink2);border:1px solid var(--rule2);border-radius:3px;padding:2px 7px}
.stamp{font-family:var(--f-mono);font-size:11.5px;color:var(--ink3)}

/* 상태 — 카드가 아니라 심각도 스트라이프 */
.rail{margin-bottom:22px}
.row{display:flex;align-items:baseline;gap:11px;padding:9px 0 9px 13px;
 border-left:3px solid var(--rule2);border-bottom:1px solid var(--rule)}
.row:last-child{border-bottom:0}
.row .mk{font-family:var(--f-mono);font-size:11px;letter-spacing:.06em;color:var(--ink3);
 min-width:3.2em;text-transform:uppercase}
.row .st{font-weight:550;font-size:15px}
.row .why{color:var(--ink3);font-size:13px;margin-left:auto;text-align:right}
.t-good{border-left-color:var(--good)} .t-good .st{color:var(--good)}
.t-critical{border-left-color:var(--critical)} .t-critical .st{color:var(--critical)}
.t-warning{border-left-color:var(--warning)} .t-warning .st{color:var(--serious)}
.t-neutral{border-left-color:var(--series-1)} .t-neutral .st{color:var(--series-1)}
.t-muted{border-left-color:var(--rule2)} .t-muted .st{color:var(--ink2)}

/* 성공 지표 — 이 페이지의 본론이라 유일하게 들어올린다 */
.kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--rule);
 border:1px solid var(--rule);border-radius:6px;overflow:hidden;margin-bottom:26px}
.kpi{background:var(--surface);padding:14px 15px 15px}
.kpi .lab{font-family:var(--f-mono);font-size:10.5px;letter-spacing:.07em;
 text-transform:uppercase;color:var(--ink3);margin-bottom:7px}
.kpi .big{font-family:var(--f-mono);font-size:26px;font-weight:500;line-height:1.05;
 letter-spacing:-.02em;font-variant-numeric:tabular-nums}
.kpi .note{font-size:12px;color:var(--ink3);margin-top:6px;line-height:1.45}

/* 절 — 괘선으로 나눈다 */
section{margin-bottom:26px}
h2{font-family:var(--f-mono);font-size:11px;letter-spacing:.09em;text-transform:uppercase;
 color:var(--ink3);margin:0 0 9px;padding-bottom:6px;border-bottom:1px solid var(--rule2);
 font-weight:500}

table{width:100%;border-collapse:collapse;font-size:14px}
th{text-align:left;font-family:var(--f-mono);font-weight:400;color:var(--ink3);
 font-size:10.5px;letter-spacing:.05em;text-transform:uppercase;
 padding:0 10px 6px 0;border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:8px 10px 8px 0;border-bottom:1px solid var(--rule);vertical-align:baseline}
tr:last-child td{border-bottom:0}
.num{text-align:right;white-space:nowrap;font-family:var(--f-mono);
 font-variant-numeric:tabular-nums;font-size:13.5px}
.pos{color:var(--good)} .neg{color:var(--critical)}
.tick{font-family:var(--f-mono);font-size:12.5px;color:var(--ink2)}

.legend{display:flex;gap:15px;font-size:12px;color:var(--ink2);margin:0 0 6px}
.legend i{display:inline-block;width:15px;height:0;border-top:2px solid;
 vertical-align:middle;margin-right:6px}
.spark{width:100%;height:auto;display:block;margin-bottom:2px}
.empty{color:var(--ink3);font-size:13px;padding:6px 0;line-height:1.5}
.why{color:var(--ink2);font-size:13px}
.tag{font-family:var(--f-mono);font-size:10px;letter-spacing:.05em;text-transform:uppercase;
 padding:2px 6px;border-radius:3px;border:1px solid var(--rule2);color:var(--ink3);
 white-space:nowrap}
.tag.rej{border-color:var(--critical);color:var(--critical)}
footer{color:var(--ink3);font-size:12px;margin-top:30px;padding-top:14px;
 border-top:1px solid var(--rule);line-height:1.65}
code{font-family:var(--f-mono);font-size:11.5px;background:var(--surface);
 border:1px solid var(--rule);border-radius:3px;padding:1px 4px}
@media (max-width:560px){
 .kpis{grid-template-columns:1fr} .wrap{padding:20px 14px 46px}
 table{font-size:13px} .kpi .big{font-size:23px}
 .row .why{margin-left:0;width:100%;text-align:left}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""


def render(d: dict) -> str:
    st = discipline_stats(d)
    now = d["generated_at"]
    last = latest_by_market(d["equity"])
    today = today_rows(d)
    L = []

    L.append(f'<h1>AI 자동매매 현황</h1>')
    mode = "실계좌" if d["limits"].get("real_trading_enabled") else "모의투자"
    L.append(f'<p class="sub">{e(mode)} · 갱신 {now:%Y-%m-%d %H:%M} KST</p>')

    # ① 상태 배너
    if d["kill"]:
        L.append('<div class="banner s-critical"><span class="dot"></span>'
                 '<span class="mk">전체</span><span class="st">정지됨 — '
                 'config/KILL 파일이 있습니다. 지우면 재개됩니다.</span></div>')
    for m, label in (("KR", "국내"), ("US", "미국")):
        r = today.get(m)
        text, tone = outcome_view(r.get("outcome") if r else None)
        extra = ""
        if r:
            o = r.get("orders") or {}
            if o.get("sent"):
                extra = f' · 주문 {o["sent"]}건'
            elif o.get("rejected"):
                extra = f' · 거부 {o["rejected"]}건'
            if r.get("_stale"):
                # 오늘 것이 아니면 그렇다고 말한다 — 결측을 정상처럼 보이게 두지 않는다.
                text, tone = f"{text} ({r.get('session_date')})", "warning"
                extra = (extra + " · 오늘 실행 없음").strip(" ·")
        L.append(f'<div class="banner s-{tone}"><span class="dot"></span>'
                 f'<span class="mk">{label}</span>'
                 f'<span class="st">{e(text)}</span><span class="why">{e(extra)}</span></div>')
    if st["consecutive_failures"]:
        L.append(f'<div class="banner s-critical"><span class="dot"></span>'
                 f'<span class="mk">경고</span><span class="st">연속 실패 '
                 f'{st["consecutive_failures"]}회</span>'
                 f'<span class="why">3회가 되면 스스로 멈춥니다</span></div>')
    if d["fail_markers"]:
        L.append(f'<div class="banner s-critical"><span class="dot"></span>'
                 f'<span class="mk">기록</span><span class="st">미확인 실패 마커 '
                 f'{len(d["fail_markers"])}건</span>'
                 f'<span class="why">{e(", ".join(d["fail_markers"][:3]))}</span></div>')

    # ② 성공 지표 3카드
    rate = st["discipline_rate"]
    rate_txt = "해당 없음" if rate is None else f"{rate:.0f}%"
    incidents = st["failed"] + st["unknown"]
    L.append('<div class="kpis">')
    L.append(f'<div class="kpi"><div class="lab">규율 집행률</div>'
             f'<div class="big">{e(rate_txt)}</div>'
             f'<div class="note">규율 매도 {st["discipline_done"]}/{st["discipline_total"]}건'
             f'{" · 아직 발동 없음" if not st["discipline_total"] else ""}</div></div>')
    L.append(f'<div class="kpi"><div class="lab">무사고</div>'
             f'<div class="big">{"예" if incidents == 0 else "아니오"}</div>'
             f'<div class="note">주문 실패 {st["failed"]}건 · 확인필요 {st["unknown"]}건'
             f' · 성공 {st["sent"]}건</div></div>')
    anch = "·".join(st["anchors"]) or "없음"
    L.append(f'<div class="kpi"><div class="lab">기록 완전성</div>'
             f'<div class="big">{st["runs_logged"]}/{st["runs_total"]}</div>'
             f'<div class="note">저널 {st["equity_rows"]}행 · 기준점 {e(anch)}</div></div>')
    L.append('</div>')

    # ③ 계좌
    L.append('<div class="card"><h2>계좌</h2><table><thead><tr>'
             '<th>시장</th><th class="num">평가액</th><th class="num">현금</th>'
             '<th class="num">누적</th><th class="num">벤치마크</th><th class="num">초과</th>'
             '</tr></thead><tbody>')
    if not last:
        L.append('<tr><td colspan="6" class="empty">아직 기록이 없습니다.</td></tr>')
    for m, label in (("KR", "국내"), ("US", "미국")):
        r = last.get(m)
        if not r:
            L.append(f'<tr><td>{label}</td><td colspan="5" class="empty">'
                     f'기준점 미기록</td></tr>')
            continue
        cur = r.get("currency", "KRW")
        cum, bmk, exc = r.get("cum_pnl_pct"), r.get("cum_benchmark_pct"), r.get("excess_pct")
        cls = "" if exc is None else (" pos" if exc >= 0 else " neg")
        L.append(f'<tr><td>{label}</td>'
                 f'<td class="num">{e(money(r.get("equity"), cur))}</td>'
                 f'<td class="num">{e(money(r.get("cash"), cur))}</td>'
                 f'<td class="num">{e(pct(cum))}</td>'
                 f'<td class="num">{e(pct(bmk))}</td>'
                 f'<td class="num{cls}">{e(pct(exc))}</td></tr>')
    L.append('</tbody></table></div>')

    # ③-b 거래비용 — 손익 옆에 나란히 둬야 "판단이 나빴다"와 "비용이 먹었다"가 갈린다.
    # 왕복 0.22%(2026-09-08 실측)라 매일 돌면 연 50%대 회전비용이 된다. 조사에서
    # StockBench가 "거래비용·슬리피지를 모델링하지 않았다"고 명시한 그 자리다.
    L.append('<div class="card"><h2>거래비용</h2><table><thead><tr>'
             '<th>시장</th><th class="num">누적 제비용</th><th class="num">자산 대비</th>'
             '<th class="num">누적 회전</th><th class="num">체결 대비 요율</th>'
             '</tr></thead><tbody>')
    any_cost = False
    for m, label in (("KR", "국내"), ("US", "미국")):
        r = last.get(m)
        if not r:
            continue
        any_cost = True
        cur = r.get("currency", "KRW")
        cc, tv = r.get("cum_costs"), r.get("cum_turnover")
        rate = f"{cc / tv * 100:.3f}%" if cc and tv else "—"
        xs = r.get("cum_turnover_x")
        L.append(f'<tr><td>{label}</td>'
                 f'<td class="num">{e(money(cc, cur))}</td>'
                 f'<td class="num neg">{e(pct(r.get("cost_drag_pct"), 3))}</td>'
                 f'<td class="num">{"—" if xs is None else e(f"{xs:.2f}배")}</td>'
                 f'<td class="num">{e(rate)}</td></tr>')
        if r.get("costs_today") is None and r.get("turnover_today"):
            L.append(f'<tr><td colspan="5" class="empty">{label}: '
                     f'{e(r.get("costs_source", ""))}</td></tr>')
    if not any_cost:
        L.append('<tr><td colspan="5" class="empty">아직 기록이 없습니다.</td></tr>')
    L.append('</tbody></table>'
             '<div class="note">비용은 매도 시 증권거래세가 대부분입니다. '
             '회전이 늘면 같은 비율로 늘어납니다.</div></div>')

    # ④ 추이 — 점이 적으면 차트 대신 표
    kr = [r for r in d["equity"] if r.get("market") == "KR"]
    L.append('<div class="card"><h2>추이 (국내)</h2>')
    if len(kr) >= MIN_POINTS_FOR_CHART:
        L.append('<div class="legend">'
                 '<span><i style="background:var(--series-1)"></i>내 계좌</span>'
                 '<span><i style="background:var(--series-2)"></i>그냥 ETF 샀다면</span></div>')
        L.append(sparkline([r.get("equity") for r in kr],
                           [r.get("shadow_benchmark_equity") for r in kr]))
    else:
        L.append(f'<p class="empty">관측 {len(kr)}일 — 그래프는 '
                 f'{MIN_POINTS_FOR_CHART}일부터 그립니다. 점 몇 개로 그린 선은 '
                 f'추세가 아니라 착시입니다.</p>')
        if kr:
            L.append('<table><thead><tr><th>날짜</th><th class="num">평가액</th>'
                     '<th class="num">벤치마크 환산</th><th class="num">초과</th>'
                     '</tr></thead><tbody>')
            for r in kr[-8:]:
                cur = r.get("currency", "KRW")
                exc = r.get("excess_pct")
                cls = "" if exc is None else (" pos" if exc >= 0 else " neg")
                L.append(f'<tr><td>{e(r.get("date"))}</td>'
                         f'<td class="num">{e(money(r.get("equity"), cur))}</td>'
                         f'<td class="num">{e(money(r.get("shadow_benchmark_equity"), cur))}</td>'
                         f'<td class="num{cls}">{e(pct(exc))}</td></tr>')
            L.append('</tbody></table>')
    L.append('</div>')

    # ⑤ 오늘의 판정 — 검사기가 무엇을 막았나
    L.append('<div class="card"><h2>가장 최근 판정 — 무엇을 통과시키고 무엇을 막았나</h2>')
    if not d["approved"]:
        L.append('<p class="empty">아직 판정 기록이 없습니다.</p>')
    else:
        a = d["approved"][-1]
        L.append(f'<p class="why">{e(a.get("market") or "")} · '
                 f'승인 {len(a["orders"])}건 / 거부 {len(a["rejected"])}건 '
                 f'<span class="tag">{e(a["file"])}</span></p>')
        if a["orders"] or a["rejected"]:
            L.append('<table><tbody>')
            for o in a["orders"]:
                src = "규율" if o.get("source") == "discipline" else "제안"
                L.append(f'<tr><td><span class="tag">{src}</span></td>'
                         f'<td>{e(o.get("action"))} {e(o.get("ticker"))} '
                         f'{e(o.get("name") or "")}</td>'
                         f'<td class="num">{o.get("qty")}주 @ '
                         f'{e(money(o.get("price"), "KRW" if a.get("market") == "KR" else "USD"))}</td>'
                         f'</tr>')
            for r in a["rejected"]:
                L.append(f'<tr><td><span class="tag">거부</span></td>'
                         f'<td>{e(r.get("action"))} {e(r.get("ticker"))}</td>'
                         f'<td class="why">{e(r.get("why"))}</td></tr>')
            L.append('</tbody></table>')
    L.append('</div>')

    # ⑥ 보유
    L.append('<div class="card"><h2>보유 종목</h2>')
    held = []
    for m in ("KR", "US"):
        r = last.get(m)
        for p in (r or {}).get("positions", []) or []:
            held.append((m, r.get("currency", "KRW"), p))
    if not held:
        L.append('<p class="empty">보유 없음.</p>')
    else:
        L.append('<table><thead><tr><th>종목</th><th class="num">수량</th>'
                 '<th class="num">현재가</th><th class="num">평가액</th>'
                 '<th class="num">손익</th></tr></thead><tbody>')
        for m, cur, p in held:
            pnl = p.get("pnl_pct")
            cls = "" if pnl is None else (" pos" if pnl >= 0 else " neg")
            L.append(f'<tr><td>{e(p.get("ticker"))} {e(p.get("name") or "")}</td>'
                     f'<td class="num">{p.get("qty")}</td>'
                     f'<td class="num">{e(money(p.get("price"), cur))}</td>'
                     f'<td class="num">{e(money(p.get("eval_amt"), cur))}</td>'
                     f'<td class="num{cls}">{e(pct(pnl))}</td></tr>')
        L.append('</tbody></table>')
    L.append('</div>')

    # ⑦ 최근 실행
    L.append('<div class="card"><h2>최근 실행</h2>')
    if not d["runs"]:
        L.append('<p class="empty">아직 자동 실행 기록이 없습니다.</p>')
    else:
        L.append('<table><thead><tr><th>일시</th><th>시장</th><th>결과</th>'
                 '<th class="num">주문</th></tr></thead><tbody>')
        for r in d["runs"][-RECENT_RUNS:][::-1]:
            text, tone = outcome_view(r.get("outcome"))
            o = r.get("orders") or {}
            ts = str(r.get("ts") or "")[:16].replace("T", " ")
            L.append(f'<tr><td>{e(ts)}</td><td>{e(r.get("market") or "")}</td>'
                     f'<td class="s-{tone}"><span class="dot"></span>{e(text)}</td>'
                     f'<td class="num">{o.get("sent", 0)}/{o.get("approved", 0)}</td></tr>')
        L.append('</tbody></table>')
    L.append('</div>')

    L.append('<footer>비공개 페이지입니다. 계좌번호·API 키·뉴스 원문은 이 페이지에 '
             '담지 않습니다.<br>원장은 맥의 <code>ai-trading-bot/journal/</code>에 있습니다. '
             '멈추려면 <code>config/KILL</code> 파일을 만드세요.</footer>')

    # 폰트는 링크가 있어야 뜬다. CSS만 "IBM Plex"라고 적어두면 조용히 기본 폰트로
    # 떨어지고, 표의 숫자가 자릿수 맞춰 정렬되지 않는다(고정폭이 아니게 되므로).
    # 네트워크가 없으면 --f-sans/--f-mono의 뒤쪽 폴백(-apple-system, Menlo)이 받는다.
    fonts = ('<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
             '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
             'family=IBM+Plex+Mono:wght@400;500&'
             'family=IBM+Plex+Sans+KR:wght@400;500;600&display=swap">')
    return (f'<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>AI 자동매매 현황</title>{fonts}<style>{CSS}</style></head>'
            f'<body><div class="wrap">{"".join(L)}</div></body></html>')


def main() -> int:
    ap = argparse.ArgumentParser(description="현황 페이지를 만든다 (읽기 전용)")
    ap.add_argument("--open", action="store_true", help="만든 뒤 브라우저로 연다")
    ap.add_argument("--out", help="기본: dashboard/index.html")
    args = ap.parse_args()

    d = collect()
    doc = render(d)
    out = Path(args.out) if args.out else (OUT_DIR / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(doc, encoding="utf-8")

    kb = len(doc.encode("utf-8")) / 1024
    st = discipline_stats(d)
    print(f"{out}  ({kb:.1f} KB)")
    print(f"  저널 {st['equity_rows']}행 · run {st['runs_total']}회 · "
          f"주문 성공 {st['sent']} 실패 {st['failed']} 확인필요 {st['unknown']}")
    if kb > 40:
        print(f"  경고: 40KB를 넘었다 ({kb:.1f}KB) — 표를 줄일 것", file=sys.stderr)
    if args.open:
        subprocess.run(["open", str(out)], check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
