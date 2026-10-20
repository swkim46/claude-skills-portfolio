"""테스트용 임시 저장소 — 테스트는 실제 journal/trading.db를 절대 열지 않는다.

옛 테스트는 원장 경로(JOURNAL_DIR 등)를 임시 폴더로 돌리고 JSON 파일을 심었다. 저장소로 바뀐 뒤에는
그 방식이 조용히 실패한다(모듈은 파일이 아니라 저장소를 읽는다) — 심기에 실패하면 테스트가 실제 데이터로
통과하는 사고가 된다. 그래서 테스트는 이 모듈로 빈 저장소를 열고, 필요한 행을 직접 넣는다.

    from testkit import db_case, seed
    with db_case() as tmp:
        seed(theses=[{...}], allocation=[{...}], calendar=[{...}])
        ...검사...
"""
from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
from pathlib import Path

import db


@contextlib.contextmanager
def db_case(tmp: Path | str | None = None, keep: bool = False):
    """빈 저장소를 임시 폴더에 열고, 끝나면 이전 AITB_DB로 되돌린다."""
    made = tmp is None
    tmp = Path(tmp) if tmp else Path(tempfile.mkdtemp(prefix="aitb_test_"))
    tmp.mkdir(parents=True, exist_ok=True)
    old = os.environ.get("AITB_DB")
    path = tmp / "trading.db"
    for p in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        if p.exists():
            p.unlink()
    os.environ["AITB_DB"] = str(path)
    db.reset()
    db.connect()
    try:
        yield tmp
    finally:
        db.reset()
        if old is None:
            os.environ.pop("AITB_DB", None)
        else:
            os.environ["AITB_DB"] = old
        if made and not keep:
            shutil.rmtree(tmp, ignore_errors=True)


def _stub_facts(theses) -> None:
    """논지 근거(`evidence[].fact_id`)가 가리키는 사실이 빈 저장소에 없으면 빈 사실 머리 행을 먼저 둔다 —
    `jd_thesis_fact.fact_id`는 `ev_fact(id)`를 참조하므로(외래 키) 실제 원장 사본을 심으면 깨진다(2026-10-19: 근거에 사실 번호를
    단 첫 논지가 생기자 t13·교차 시장 사슬이 FK로 중단됐다). 테스트가 그 사실의 내용을 볼 때는 직접 심는다."""
    ids = {str(e.get("fact_id")) for t in theses or [] for e in (t.get("evidence") or [])
           if isinstance(e, dict) and e.get("fact_id")}
    c = db.connect()
    for fid in sorted(ids):
        c.execute("INSERT OR IGNORE INTO ev_fact (id, kind, legacy_text) VALUES (?, 'legacy', '테스트 사본 — 원 사실 없음')", (fid,))


@contextlib.contextmanager
def paper_limits():
    """계좌 모드를 모의로 고정한다(실제 계좌 모드와 무관하게) — 실제 `config/limits.json`을 `account_mode: paper`·실전 래치 끔으로 바꾼
    사본을 임시 폴더에 두고, 계좌 모드를 읽는 자리 둘(`kis_client.CONFIG_DIR`의 limits.json · `risk_guard.LIMITS_PATH`)을 그 사본으로
    돌린다. 실계좌 규칙을 재는 케이스는 `{**limits, "account_mode": "real"}`처럼 스스로 켠다."""
    import kis_client
    import risk_guard
    tmp = Path(tempfile.mkdtemp(prefix="aitb_limits_"))
    for f in Path(kis_client.CONFIG_DIR).glob("*.json"):        # 다른 설정(universe_rules·holidays…)도 같은 자리에서 읽는다 — 숨김 파일(토큰)은 빼고
        shutil.copy2(f, tmp / f.name)
    lim = json.loads(risk_guard.LIMITS_PATH.read_text(encoding="utf-8"))
    lim["account_mode"], lim["real_trading_enabled"] = "paper", False
    lim["scenario_cond_required"] = False          # 형식 적용일(cond·목표 기준·논지 구조)은 그 형식을 재는 시험이 켠다 — 날짜가 지나도 다른 시험이 안 깨진다
    (tmp / "limits.json").write_text(json.dumps(lim, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    saved = (kis_client.CONFIG_DIR, risk_guard.LIMITS_PATH)
    kis_client.CONFIG_DIR, risk_guard.LIMITS_PATH = tmp, tmp / "limits.json"
    try:
        yield tmp / "limits.json"
    finally:
        kis_client.CONFIG_DIR, risk_guard.LIMITS_PATH = saved
        shutil.rmtree(tmp, ignore_errors=True)


def seed(*, theses=None, scenarios=None, axes=None, sectors=None, calendar=None, allocation=None, equity=None,
         peaks=None, watchlist=None, coverage=None, pending=None, benchmark=None, current_run=None,
         reversals=None, refill=None, logs=None, sources=None, bridge=None, fetches=None) -> None:
    """원장 모양 dict를 그대로 넣는다. 각 인자는 옛 파일의 목록(또는 dict) 모양.
    peaks={'KR': {ticker: {peak_price, peak_at}}} · refill={(stamp, market): {...}} · logs={'stops_log': [row…]}
    fetches=[{url, text?, err?, kind?, pages?, section?, at?}] — 원문 시도·판(v3 · `db.evidence_record` · 보관 파일은 임시 저장소 옆)"""
    with db.tx():
        _stub_facts(theses)
        for i, d in enumerate(theses or []):
            db.theses.put(d, ord_=i)
        for i, d in enumerate(scenarios or []):
            db.scenarios.put(d, ord_=i)
        for i, d in enumerate(axes or []):
            db.axes.put(d, ord_=i)
        for i, d in enumerate(sectors or []):
            db.sectors.put(d, ord_=i)
        for i, d in enumerate(allocation or []):
            db.STORES["allocation"].put(d, ord_=i)
        for i, d in enumerate(sources or []):
            db.facts_store.put(d, ord_=i)
    if calendar is not None:
        db.calendar_save(calendar if isinstance(calendar, dict) else {"schema_version": "1.0", "events": calendar})
    for r in equity or []:
        with db.tx():
            n = db.connect().execute("SELECT COALESCE(MAX(ord), -1) + 1 FROM ex_equity_point").fetchone()[0]
            db._insert_rowdoc(db.SPEC["equity"], r, n)
    for m, rows in (peaks or {}).items():
        if m.startswith("_"):
            continue
        for t, v in rows.items():
            db.peaks_set(m, t, v.get("peak_price"), v.get("peak_at"))
    if watchlist is not None:
        db.watchlist_save(watchlist)
    if coverage is not None:
        db.kv_save("op_coverage", coverage)
    if pending is not None:
        db.kv_save("op_pending", pending)
    for m, v in (benchmark or {}).items():
        db.benchmark_set(m, v)
    if current_run is not None:
        db.current_run_write(current_run)
    for r in reversals or []:
        db.log_append("audit_reversals", r)
    for (st, mk), v in (refill or {}).items():
        db.refill_put(st, mk, v)
    for name, rows in (logs or {}).items():
        for r in rows:
            db.log_append(name, r)
    for f in fetches or []:
        db.evidence_record(f["url"], f.get("text") or "", f.get("err") or "",
                           {k: f[k] for k in ("kind", "pages", "via", "title") if k in f}, section=f.get("section") or "",
                           agent=f.get("agent") or "fetch.py", at=f.get("at"))
    if bridge is not None:
        db.sector_bridge_save(bridge)


def load_ledgers(journal: Path, config: Path | None = None) -> dict:
    """실제 원장 사본(파일)에서 판단 원장을 저장소로 읽어 들인다 — 실데이터 정합성을 보는 테스트용.
    이전 도구(migrate.py)의 전체 이전과 달리 판단 원장만 다룬다."""
    journal = Path(journal)

    def load(name):
        p = journal / name
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    n = {}
    th = load("theses.json")
    if th:
        with db.tx():
            _stub_facts(th["theses"])
            db.docmeta_put("theses", {**{k: v for k, v in th.items() if k != "theses"}, "_ko": list(th)})
            for i, t in enumerate(th["theses"]):
                db.theses.put(t, ord_=i)
        n["theses"] = len(th["theses"])
    sc = load("scenarios.json")
    if sc:
        with db.tx():
            db.docmeta_put("scenarios", {**{k: v for k, v in sc.items() if k != "scenarios"}, "_ko": list(sc)})
            for i, t in enumerate(sc["scenarios"]):
                db.scenarios.put(t, ord_=i)
        n["scenarios"] = len(sc["scenarios"])
    mm = load("market_map.json")
    if mm:
        db.market_map_save(mm)
        n["axes"] = len(mm.get("axes") or [])
    cal = load("calendar.json")
    if cal:
        db.calendar_save(cal)
        n["calendar"] = len(cal.get("events") or [])
    ap = journal / "allocation.jsonl"
    if ap.exists():
        rows = [json.loads(x) for x in ap.read_text(encoding="utf-8").splitlines() if x.strip()]
        with db.tx():
            for i, r in enumerate(rows):
                db.STORES["allocation"].put(r, ord_=i)
        n["allocation"] = len(rows)
    for name, (table, _ao, fname) in db.LOGS.items():
        p = journal / fname
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    db.log_append(name, json.loads(line))
    if config:
        w = Path(config) / "watchlist.json"
        if w.exists():
            db.watchlist_save(json.loads(w.read_text(encoding="utf-8")))
    return n
