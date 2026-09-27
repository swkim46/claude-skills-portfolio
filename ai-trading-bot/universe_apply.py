#!/usr/bin/env python3
"""시그널의 편입·제외 제안을 **규칙으로 검증해** watchlist.json에 반영한다.

이 파일이 존재하는 이유 — 무인 운영과 인젝션 방어를 동시에 만족시키기 위해서다.

  · 워치리스트는 뉴스레터발 인젝션이 주문으로 번지는 것을 막는 **유일한 관문**이었고,
    그 방어는 "사람이 승인한다"에 기대고 있었다.
  · 그런데 무인으로 돌리면 사람이 병목이 된다. 승인을 기다리는 동안 유니버스가 굳고,
    재료가 다루지 않는 종목만 남아 판단 자체가 불가능해진다.
  · 그래서 방어를 **사람의 승인에서 코드의 검증으로 옮긴다.** LLM은 후보를 제안할 뿐이고,
    편입 여부는 **브로커가 주는 사실**로만 판정한다 — 뉴스레터 본문은 지수 편입도,
    시가총액도, 관리종목 지정도 바꿀 수 없다.

이 스크립트에는 LLM 호출이 없다. 판정은 `config/universe_rules.json`(운영자 규칙)과
브로커 조회 결과만으로 이뤄진다. risk_guard가 주문에 대해 하는 일을 유니버스에 대해 한다.

정직한 한계: 이 방어는 **공격 표면을 좁히는 것이지 없애는 것이 아니다.** 인젝션이 임의의
잡주를 넣는 것은 막지만, 공격자가 이미 대형 지수편입 종목을 노린다면 "AI가 그 종목을
더 자주 보게 만드는" 정도는 여전히 가능하다. 냉각 기간·run당 상한이 그 속도를 늦출 뿐이다.
금액 상한이 꺼져 있으면 이 잔여 위험의 폭발 반경은 계좌 전체다.

사용:
    python3 universe_apply.py --signal signals/signal_260908_kr_v2.json          # dry-run
    python3 universe_apply.py --signal signals/signal_260908_kr_v2.json --apply
"""
import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from kis_client import KisClient, KisError, quote_excd

HERE = Path(__file__).parent
CONFIG = HERE / "config"
WATCHLIST = CONFIG / "watchlist.json"
RULES = CONFIG / "universe_rules.json"
KILL = CONFIG / "KILL"
JOURNAL = HERE / "journal"
PENDING = JOURNAL / "universe_pending.json"
COVERAGE = JOURNAL / "universe_coverage.json"
CHANGELOG = JOURNAL / "universe_log.jsonl"
KST = timezone(timedelta(hours=9))


def _read(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def _read_required(path: Path) -> tuple:
    """반드시 읽혀야 하는 상태 파일 — **(데이터, 읽었는가)**. 못 읽으면 옆으로 보존한다.

    `_read`는 실패를 기본값으로 갈음한다. 그래도 되는 파일(커버리지·대기열)과
    그러면 **이력이 통째로 사라지는** 파일(유니버스)이 있어서 후자만 이 함수를 쓴다.
    """
    if not path.exists():
        return {}, True
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(d, dict):
            raise ValueError("최상위가 dict가 아니다")
        return d, True
    except (json.JSONDecodeError, OSError, ValueError) as e:
        aside = path.with_name(f"{path.stem}.corrupt_{datetime.now(KST):%y%m%d_%H%M%S}.json")
        try:
            path.rename(aside)
            where = aside.name
        except OSError:
            where = "(보존 실패)"
        print(f"\n★ {path.name}을 읽을 수 없어 {where}로 옮겨 보존했다 ({e}).", file=sys.stderr)
        return {}, False


def _write_watchlist_guarded(wl: dict, before: dict, n_removed: int) -> bool:
    """유니버스를 쓰기 **직전에** 규모가 무너지지 않았는지 본다.

    읽기가 성공해도 중간 로직이 잘못되면 규모가 붕괴할 수 있다. 유니버스는 이 시스템의
    주문 가능 범위이므로, **줄어드는 것은 제거 건수만큼만**이어야 한다.
    """
    def size(d):
        return sum(len(v) for k, v in d.items() if isinstance(v, list))
    lost = size(before) - size(wl)
    if lost > max(n_removed, 0):
        print(f"★ 유니버스가 {lost}종목 줄었는데 제거 판정은 {n_removed}건뿐이다 — "
              f"쓰지 않는다(계산이 틀렸다).", file=sys.stderr)
        return False
    for key in ("benchmarks", "schema_version"):
        if key in before and key not in wl:
            print(f"★ 유니버스에서 `{key}`가 사라졌다 — 쓰지 않는다.", file=sys.stderr)
            return False
    WATCHLIST.write_text(json.dumps(wl, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


class Unknown(Exception):
    """**물어보지 못했다** — 거부와 다르다. 관문 판정이 아니라 조회 실패이므로 사유를 그렇게 기록한다."""


class Reject(Exception):
    pass


def _flags_ok(o: dict, want: dict) -> None:
    for field, expected in want.items():
        got = str(o.get(field, "")).strip()
        if got != expected:
            raise Reject(f"{field}={got!r} (요구 {expected!r})")


def check_kr(client: KisClient, ticker: str, rules: dict, equity: float) -> dict:
    res = client._request(
        "GET", "/uapi/domestic-stock/v1/quotations/inquire-price",
        headers=client._headers(client.tr["dom_price"]),
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker})
    client._check(res, f"국내 현재가({ticker})")
    o = res.get("output", {}) or {}
    r = rules["KR"]

    _flags_ok(o, r["forbid_flags"])
    market = o.get("rprs_mrkt_kor_name", "")
    if market not in r["require_market"]:
        raise Reject(f"소속 시장 {market!r} — 요구 {r['require_market']}")
    mcap = float(o.get("hts_avls") or 0)          # 억원
    if mcap < r["min_market_cap_100m_krw"]:
        raise Reject(f"시가총액 {mcap:,.0f}억 < {r['min_market_cap_100m_krw']:,}억")
    turnover = float(o.get("acml_tr_pbmn") or 0)
    if turnover < r["min_daily_turnover_krw"]:
        raise Reject(f"거래대금 {turnover / 1e8:,.0f}억 < {r['min_daily_turnover_krw'] / 1e8:,.0f}억")
    price = float(o.get("stck_prpr") or 0)
    if price <= 0:
        raise Reject("현재가 조회 실패")
    return {"price": price, "market": market, "sector": o.get("bstp_kor_isnm", ""),
            "mcap_100m_krw": mcap, "turnover_krw": turnover, "currency": "KRW"}


def check_us(client: KisClient, ticker: str, excd: str, rules: dict) -> dict:
    # ★ 시세 TR은 **시세용 코드**(NAS/NYS/AMS)를 받는다. 주문용(NASD/NYSE/AMEX)을 주면 에러가 아니라 `rt_cd=0`에
    #   **빈 output**이 와서, 4 run 동안 "브로커 매매 판정 ''"으로 기록됐다 — 브로커의 거부가 아니라 우리가 못 물은 것.
    q_excd = quote_excd(excd)
    res = client._request(
        "GET", "/uapi/overseas-price/v1/quotations/price-detail",
        headers=client._headers("HHDFS76200200"),
        params={"AUTH": "", "EXCD": q_excd, "SYMB": ticker})
    client._check(res, f"해외 현재가({ticker})")
    o = res.get("output", {}) or {}
    r = rules["US"]

    # ★ 빈 응답은 **거부가 아니라 미확인**이다(원칙 ㊶ — 실패를 '없음'으로 기록하지 않는다).
    if not o or not str(o.get("last") or "").strip():
        raise Unknown(f"조회 실패 — 응답이 비었다(EXCD={q_excd} · TR HHDFS76200200). 거래소 코드·티커를 확인할 것")
    ordyn = str(o.get("e_ordyn") or "").strip()
    if r.get("require_orderable"):
        if not ordyn:
            raise Unknown(f"매매 판정 공백 — 브로커가 플래그를 주지 않았다(EXCD={q_excd}). 거부가 아니다")
        if "가능" not in ordyn:
            raise Reject(f"브로커 매매 판정 {ordyn!r}")
    mcap = float(o.get("tomv") or 0)
    # ETF는 tomv가 AUM으로 온다 — 개별주 기준(500억 달러)을 그대로 대면 SOXL도 못 들어온다.
    nm = str(o.get("etyp_nm") or o.get("name") or ticker).upper()
    is_etf = any(x in nm for x in ("ETF", "SHARES", "FUND", "TRUST")) or \
             ticker.upper() in {x.upper() for k, v in (rules.get("leverage_patterns") or {}).items() for x in v}
    floor = r.get("min_market_cap_usd_etf", r["min_market_cap_usd"]) if is_etf else r["min_market_cap_usd"]
    if mcap < floor:
        raise Reject(f"{'AUM' if is_etf else '시가총액'} ${mcap / 1e9:,.1f}B < ${floor / 1e9:,.0f}B")
    turnover = float(o.get("tamt") or 0)
    if turnover < r["min_daily_turnover_usd"]:
        raise Reject(f"거래대금 ${turnover / 1e6:,.0f}M < ${r['min_daily_turnover_usd'] / 1e6:,.0f}M")
    price = float(o.get("last") or 0)
    if price <= 0:
        raise Reject("현재가 조회 실패")
    return {"price": price, "market": excd, "sector": o.get("e_icod", ""),
            "mcap_usd": mcap, "turnover_usd": turnover, "currency": "USD",
            "per": o.get("perx"), "pbr": o.get("pbrx")}


def forbidden_name(name: str, ticker: str, patterns: list) -> str:
    blob = f"{name} {ticker}".upper()
    for p in patterns:
        if p.upper() in blob:
            return p
    return ""


def run(signal_path: Path, apply: bool) -> int:
    if KILL.exists():
        print(f"KILL 존재 ({KILL}) — 유니버스를 건드리지 않는다.")
        return 0

    rules = _read(RULES)
    if not rules:
        print(f"규칙 파일을 읽을 수 없다: {RULES}", file=sys.stderr)
        return 2
    if not rules.get("auto_apply"):
        print("universe_rules.auto_apply=false — 자동 반영 꺼짐. 제안만 보고한다.")
        apply = False

    sig = _read(signal_path)
    if not sig:
        print(f"시그널을 읽을 수 없다: {signal_path}", file=sys.stderr)
        return 2
    # ★ 유니버스는 **인젝션 방어 경계**다. 못 읽는 파일을 `{}`로 갈음하면
    #   아래 `wl.setdefault(mkt, []).append(...)`가 **41종목·벤치마크·schema_version을
    #   1종목 파일로 덮어쓴다** — 그리고 이 스크립트는 무인으로 `--apply`와 함께 돈다.
    #   '아직 없음'과 '읽지 못함'을 구분하고, 읽지 못했으면 **쓰지 않고 중단한다.**
    wl, wl_ok = _read_required(WATCHLIST)
    if not wl_ok:
        print("★ 유니버스를 읽지 못했다 — 반영을 중단한다(덮어쓰면 전 종목이 사라진다). "
              "옆으로 보존한 파일을 복구한 뒤 다시 돌려라.", file=sys.stderr)
        return 2
    pending = _read(PENDING, {}) or {}
    coverage = _read(COVERAGE, {}) or {}
    run_id = sig.get("run_id", signal_path.stem)
    # 냉각은 날짜로 센다 — 같은 날 두 시장을 도는 것으로 끝나면 안 된다.
    session_date = (sig.get("generated_at") or "")[:10] or datetime.now(KST).strftime("%Y-%m-%d")

    # 보유 종목 — 제외 판정에 필요하다. 스냅샷에서 읽는다(네트워크 불필요).
    # ★ 스냅샷이 없으면 **반영을 하지 않는다.** 예전에는 조용히 넘어갔고, 그 결과
    #   ① `held`가 비어 **보유 종목도 제거 대상**이 되고(`never_if_held`가 무력)
    #   ② `equity=0`이 되어 **1주가 자산의 N%를 넘는지 보는 상한 검사가 꺼졌다.**
    #   둘 다 "검사가 통과했다"와 "검사를 안 했다"가 구분되지 않는 자리다.
    # ★ `snapshot_ref`가 없으면 **시그널 파일명의 스탬프로** 찾는다(`signal_<YYMMDD>_<mkt>*.json` →
    #   `data/snapshot_<YYMMDD>_<mkt>.json`). 시그널 견본에 이 필드가 없어 매번 '경로 없음'으로 거부됐고,
    #   그래서 수동 파이프라인에서 이 스크립트가 한 번도 실제로 반영한 적이 없었다(2026-09-15 확인).
    ref = sig.get("snapshot_ref") or ""
    if not ref:
        m = re.match(r"signal_(\d{6})_(kr|us)", signal_path.name)
        if m:
            cand = HERE / "data" / f"snapshot_{m.group(1)}_{m.group(2)}.json"
            if cand.exists():
                ref = str(cand)
    snap = _read(Path(ref)) if ref else None
    if not snap:
        print(f"★ 스냅샷을 읽을 수 없다({ref or '경로 없음'}) — "
              f"보유 판정과 자산 대비 상한 검사를 할 수 없으므로 **반영하지 않는다.** "
              f"제안만 보려면 `--apply` 없이 돌려라.", file=sys.stderr)
        if apply:
            return 2
    held = {p.get("ticker") for p in (snap.get("balance") or {}).get("positions", [])} if snap else set()
    equity = 0.0
    if snap:
        b = snap.get("balance") or {}
        equity = (b.get("cash") or 0) + sum(p.get("eval_amt", 0) or 0 for p in b.get("positions", []))

    client = KisClient(svr="paper")
    added, removed, rejected = [], [], []

    # ---------------------------------------------------------------- 편입
    # ★ 이미 유니버스에 있는 pending 행은 **영원히 안 지워졌다**(09-08 MU·AVGO·TSLA·SNOW) — 매 실행에 정리한다.
    in_uni = {f"{m}:{e.get('ticker')}" for m in ("KR", "US") for e in (wl.get(m) or []) if e.get("ticker")}
    stale = [k for k in pending if k in in_uni]
    for k in stale:
        pending.pop(k, None)
    if stale:
        print(f"  · 대기열 정리 {len(stale)}건 — 이미 유니버스에 있다: {', '.join(sorted(stale))}")

    cands = sig.get("watchlist_candidates") or []
    for c in cands:
        ticker = c.get("ticker", "")
        # ★ 시장 추론: 명시값 > 거래소 코드 > **티커 모양**(6자리 숫자 = KR). 예전엔 둘 다 없으면 KR로 떨어져
        #   2026-09-22에 US 후보 10건이 KR로 기록되고 냉각 카운터가 초기화됐다.
        market = (c.get("market") or ("US" if c.get("excd") else "")).upper()
        if market not in ("KR", "US"):
            market = "KR" if (ticker.isdigit() and len(ticker) == 6) else "US"
            print(f"  · {ticker}: market/excd가 없어 티커 모양으로 {market}로 판정했다 — 시그널에 market을 적어라",
                  file=sys.stderr)
        name = c.get("name", "")
        cur = wl.get(market) or []
        key = f"{market}:{ticker}"

        def rej(why):
            rejected.append({"ticker": ticker, "market": market, "action": "add", "why": why})

        if any(e["ticker"] == ticker for e in cur):
            rej("이미 유니버스에 있다")
            continue
        bad = forbidden_name(name, ticker, rules.get("forbidden_name_patterns", []))
        if bad:
            rej(f"금지 패턴 '{bad}'")
            continue
        if len(cur) >= rules["max_universe"].get(market, 99):
            rej(f"유니버스 상한 {rules['max_universe'][market]}종목 도달")
            continue
        if len([a for a in added if a["market"] == market]) >= rules["max_adds_per_run"]:
            rej(f"이번 run 편입 상한 {rules['max_adds_per_run']}건 도달")
            continue

        # 냉각 — **서로 다른 날짜**의 run에서 살아남아야 편입된다.
        #
        # run_id로 세면 같은 날 국내장·미국장 두 번 도는 것만으로 냉각이 끝나서
        # "시간을 산다"는 의도가 무너진다(2026-09-08에 발견). 날짜로 센다.
        #
        # 정직한 한계: 이건 벽이 아니라 과속방지턱이다. 재료 수집 창이 여러 날이라
        # 악의적 뉴스레터 한 통이 며칠 재료에 남고, 그러면 같은 후보가 연속으로 제안돼
        # 냉각을 통과한다. 실제 방어의 중심은 브로커 검증(대형 지수편입 종목은 애초에
        # 펌핑 표적이 아니다)과 금액 상한이고, 냉각은 속도를 늦추고 사람이 대시보드에서
        # 볼 시간을 벌 뿐이다.
        seen = set(pending.get(key, {}).get("dates", []))
        seen.add(session_date)
        runs = sorted(set(pending.get(key, {}).get("runs", [])) | {run_id})
        prev_check = (pending.get(key) or {}).get("last_check")
        pending[key] = {"dates": sorted(seen), "runs": runs, "name": name,
                        "axis": c.get("axis", ""), "excd": c.get("excd", "")}
        if prev_check:
            pending[key]["last_check"] = prev_check
        if len(seen) < rules["cooling_days"]:
            rej(f"냉각 중 — 서로 다른 {len(seen)}/{rules['cooling_days']}일 제안됨")
            continue

        def _note_check(kind, why):
            """왜 못 들어왔는지를 **pending 행에 남긴다** — 예전엔 사유가 stdout과 로그에만 있어
            '몇 run째 무엇에 막혔나'를 파일에서 볼 수 없었다(GEV 4 run)."""
            pending[key]["last_check"] = {"at": datetime.now(KST).isoformat(),
                                          "kind": kind, "why": why, "run": run_id}

        try:
            if market == "KR":
                info = check_kr(client, ticker, rules, equity)
            else:
                info = check_us(client, ticker, c.get("excd", "NASD"), rules)
        except Unknown as e:
            _note_check("미확인", str(e))
            rej(f"**미확인**(거부 아님) — {e}")
            continue
        except Reject as e:
            _note_check("거부", str(e))
            rej(str(e))
            continue
        except KisError as e:
            _note_check("미확인", f"브로커 조회 실패: {e}")
            rej(f"**미확인**(거부 아님) — 브로커 조회 실패: {e}")
            continue

        cap = rules.get("max_single_share_pct_of_equity")
        if cap and equity > 0 and info["currency"] == "KRW" and info["price"] / equity * 100 > cap:
            rej(f"1주가 자산의 {info['price'] / equity * 100:.1f}% > 상한 {cap}%")
            continue

        entry = {"ticker": ticker, "name": name,
                 "added": datetime.now(KST).strftime("%Y-%m-%d"),
                 "why": c.get("why", ""), "axis": c.get("axis", ""),
                 "aliases": c.get("aliases") or ([name] if name else []),
                 "added_by": "universe_apply", "run_id": run_id,
                 "gates": {k: v for k, v in info.items() if k != "price"}}
        if c.get("excd"):
            entry["excd"] = c["excd"]
        added.append({"market": market, "entry": entry, "price": info["price"]})
        pending.pop(key, None)

    # ---------------------------------------------------------------- 제외
    rr = rules.get("remove", {})
    for r_ in sig.get("watchlist_removals") or []:
        ticker = r_.get("ticker", "")
        market = (r_.get("market") or "KR").upper()
        reason = r_.get("reason_code") or ""
        cur = wl.get(market) or []

        def rej_r(why):
            rejected.append({"ticker": ticker, "market": market, "action": "remove", "why": why})

        if not any(e["ticker"] == ticker for e in cur):
            rej_r("유니버스에 없다")
            continue
        if rr.get("never_if_held", True) and ticker in held:
            rej_r("보유 중 — 빼면 추가 매수가 막힌다")
            continue
        if reason not in rr.get("allowed_reasons", []):
            rej_r(f"사유 코드 {reason!r}가 허용 목록에 없다 {rr.get('allowed_reasons')}")
            continue
        if reason == "coverage_gone":
            absent = int(coverage.get(f"{market}:{ticker}", {}).get("absent_runs", 0))
            need = rr.get("min_absent_runs_for_coverage_gone", 5)
            if absent < need:
                rej_r(f"연속 미등장 {absent}회 < {need}회")
                continue
        if len(cur) - len([x for x in removed if x["market"] == market]) <= \
                rules["min_universe"].get(market, 1):
            rej_r(f"유니버스 하한 {rules['min_universe'][market]}종목 — 더 뺄 수 없다")
            continue
        if len([x for x in removed if x["market"] == market]) >= rules["max_removes_per_run"]:
            rej_r(f"이번 run 제외 상한 {rules['max_removes_per_run']}건 도달")
            continue
        removed.append({"market": market, "ticker": ticker, "reason": reason,
                        "detail": r_.get("reason", "")})

    # ---------------------------------------------------------------- 보고·반영
    print(f"유니버스 자동 반영 — {signal_path.name} (run {run_id})"
          f"{'' if apply else '  [DRY-RUN]'}\n")
    for a in added:
        e = a["entry"]
        print(f"  + {a['market']} {e['ticker']} {e['name']}  [{e.get('axis', '')}]  "
              f"{a['price']:,.2f}")
    for r_ in removed:
        print(f"  − {r_['market']} {r_['ticker']}  ({r_['reason']})")
    for x in rejected:
        sign = "+" if x["action"] == "add" else "−"
        print(f"  · 보류 {sign} {x['market']} {x['ticker']}: {x['why']}")
    if not (added or removed):
        print("  변경 없음.")

    if not apply:
        print("\n실제로 반영하려면 --apply 를 붙여라.")
        return 0

    import copy
    wl_before = copy.deepcopy(wl)
    for a in added:
        wl.setdefault(a["market"], []).append(a["entry"])
    for r_ in removed:
        wl[r_["market"]] = [e for e in wl[r_["market"]] if e["ticker"] != r_["ticker"]]
    if not _write_watchlist_guarded(wl, wl_before, len(removed)):
        return 2
    PENDING.parent.mkdir(parents=True, exist_ok=True)
    PENDING.write_text(json.dumps(pending, ensure_ascii=False, indent=2), encoding="utf-8")
    with CHANGELOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": datetime.now(KST).isoformat(), "run_id": run_id,
                            "signal": str(signal_path), "added": added,
                            "removed": removed, "rejected": rejected},
                           ensure_ascii=False) + "\n")
    print(f"\n반영 완료 — {WATCHLIST.name} · 이력 {CHANGELOG.name}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="시그널의 유니버스 제안을 규칙으로 검증해 반영한다")
    ap.add_argument("--signal", required=True)
    ap.add_argument("--apply", action="store_true", help="실제로 watchlist.json을 고친다")
    args = ap.parse_args()
    p = Path(args.signal)
    if not p.is_absolute():
        p = HERE / p
    if not p.exists():
        print(f"시그널 없음: {p}", file=sys.stderr)
        return 2
    return run(p, args.apply)


if __name__ == "__main__":
    sys.exit(main())
