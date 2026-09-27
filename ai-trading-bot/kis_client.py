#!/usr/bin/env python3
"""
한국투자증권 Open API REST 클라이언트 — stdlib only (urllib).

공식 저장소(github.com/koreainvestment/open-trading-api)를 import하지 않는 이유:
requests/pandas/yaml 의존 + ~/KIS/config 전제 + 백테스터의 Node18/Docker 요구까지
따라오는데, 우리가 실제로 쓰는 TR은 8개 남짓이다. 공식 코드는 TR ID와 필드명의
'참고 문헌'으로만 쓰고(2026-09-03 examples_llm에서 verbatim 추출), 런타임은
집 패턴대로 stdlib으로 간다. cf. gmail_imap.py가 OAuth 스택을 버린 것과 같은 판단.

기본값이 모의투자(paper)다. 실전 전환은 allow_real=True를 명시적으로 넘겨야만
가능하며, 그것만으로도 부족하게 설계했다 — 실전 키가 .env에 없으면 애초에 못 뜬다.
이 프로젝트는 6개월 모의 검증을 통과하기 전까지 실전 계좌를 쓰지 않는다.
(근거: AI자동매매_공개사례_성능조사_v1_0.md §5)
"""
import http.client
import json
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).parent
CONFIG_DIR = HERE / "config"
TOKEN_CACHE = CONFIG_DIR / ".token_cache.json"
# 프로젝트 로컬 .env가 이기고, 공유 life/.env가 폴백. gmail_imap.py와 같은 규칙.
ENV_CANDIDATES = [HERE / ".env", HERE.parent / ".env"]

KST = timezone(timedelta(hours=9))

BASE_URL = {
    "paper": "https://openapivts.koreainvestment.com:29443",
    "real": "https://openapi.koreainvestment.com:9443",
}

# TR ID 맵 — 2026-09-03 공식 examples_llm에서 추출.
# 주의: 국내 현금주문 TR은 구버전 문서의 TTTC0802U/0801U가 아니라 0012U/0011U다.
TR = {
    "paper": {
        "dom_price": "FHKST01010100",   # 시세는 실전/모의 동일
        "dom_balance": "VTTC8434R",
        "dom_buy": "VTTC0012U",
        "dom_sell": "VTTC0011U",
        "ov_price": "HHDFS00000300",    # 시세는 실전/모의 동일
        "ov_balance": "VTTS3012R",
        "ov_present": "VTRP6504R",      # 체결기준현재잔고
        "ov_psamount": "VTTS3007R",     # 주문가능금액 — 해외 매수 규모는 이걸로 잡는다
        "ov_buy_us": "VTTT1002U",
        "ov_sell_us": "VTTT1001U",
        # ★ 체결 확정용(2026-09-16) — 접수는 체결이 아니라서, 주문마다 체결·잔량을 이 TR로 본다.
        "dom_ccld": "VTTC8001R",        # 국내 주문체결조회(3개월 이내)
        "dom_rvsecncl": "VTTC0013U",    # 국내 정정·취소
        "ov_ccld": "VTTS3035R",         # 해외 주문체결내역
        "ov_rvsecncl_us": "VTTT1004U",  # 해외(미국) 정정·취소
    },
    "real": {
        "dom_price": "FHKST01010100",
        "dom_balance": "TTTC8434R",
        "dom_buy": "TTTC0012U",
        "dom_sell": "TTTC0011U",
        "ov_price": "HHDFS00000300",
        "ov_balance": "TTTS3012R",
        "ov_present": "CTRP6504R",
        "ov_psamount": "TTTS3007R",
        "ov_buy_us": "TTTT1002U",
        "ov_sell_us": "TTTT1006U",
        "dom_ccld": "TTTC8001R",
        "dom_rvsecncl": "TTTC0013U",
        "ov_ccld": "TTTS3035R",
        "ov_rvsecncl_us": "TTTT1004U",
    },
}

# 해외 예수금 후보 필드. 공식 샘플에 응답 필드명이 문서화되어 있지 않아
# (examples_llm/overseas_stock/inquire_present_balance/는 파라미터만 기술) 후보를
# 순서대로 시도하고, 원본 응답을 함께 돌려준다. Phase 0에서 실호출 1회로 확정할 것.
# 하나도 못 찾으면 0이 아니라 None을 반환한다 — '미확인'을 '0원'으로 바꾸면
# 현금하한 검사가 조용히 통과해버린다.
OV_CASH_FIELDS = ("frcr_dncl_amt_2", "frcr_dncl_amt", "frcr_evlu_amt2",
                  "frcr_pchs_amt", "dncl_amt")

# 모의투자 미국 주문은 지정가(00)만 받는다 — 공식 order.py 주석에 명시.
# 그래서 US 주문 경로는 limit price를 필수로 요구한다(시장가 폴백 없음).
ORD_DVSN_LIMIT = "00"
ORD_DVSN_MARKET = "01"          # 국내 전용
US_EXCHANGES = ("NASD", "NYSE", "AMEX")
# 시세용 코드(NAS/NYS/AMS) ↔ 주문용 코드(NASD/NYSE/AMEX). 시그널이 시세용 코드를 적어 오면
# 주문 직전에 바꿔 준다 — 2026-09-15 1차 전송이 'NYS'로 클라이언트에서 거부돼 6분을 잃었다.
EXCD_ORDER = {"NAS": "NASD", "NYS": "NYSE", "AMS": "AMEX"}
RVSE = "01"                     # 정정
CNCL = "02"                     # 취소

# 모의투자 계좌는 REST 호출 제한이 낮다(EGW00201: 초당 거래건수 초과).
# 2026-09-07 실측: 0.35초 간격에서 연속 2번째 호출이 EGW00201로 떨어졌다.
# 모의는 넉넉히 벌리고, 실전은 한도가 높으므로 좁게 둔다.
CALL_INTERVAL_SEC = {"paper": 0.8, "real": 0.2}

# EGW00201은 '지금 너무 빠르다'는 뜻이지 요청이 틀렸다는 뜻이 아니다 → 물러섰다가 다시 친다.
RATE_LIMIT_CODE = "EGW00201"
RATE_LIMIT_RETRIES = 4
RATE_LIMIT_BACKOFF_SEC = 1.0
# ★ 게이트웨이 라우팅 오류(EGW00300)와 이유 없는 5xx — KIS 쪽 일시 장애다. 조회(GET)는 멱등이라
#   물러섰다 다시 부르면 대개 된다. *실측(2026-09-16 00:23~00:27): 해외 잔고·구매력 TR에서
#   12회 연속 500이 났고 그 사이 체결 조회는 성공했다.* 주문(POST)은 재시도하지 않는다 —
#   500이 났어도 서버가 접수했을 수 있어 재전송하면 이중 주문이다(execute가 UNKNOWN으로 남긴다).
TRANSIENT_CODES = (RATE_LIMIT_CODE, "EGW00300")
TRANSIENT_RETRIES = 4
TRANSIENT_BACKOFF_SEC = 2.0

# ------------------------------------------------------------ KRX 호가가격단위
#
# 지정가는 이 격자 위에만 존재한다. 벗어난 값을 보내면 브로커가
# [40030000] "호가단위 오류"로 거부하고, 그 실패는 우리 코드가 아니라 거래소 규칙에서 온다.
#
# 왜 필요했나: 시세 API가 격자 밖의 값을 준다. 2026-09-08 실측 —
# 삼성전자 시세 273,750원인데 20만~50만 구간의 단위는 500원이라 존재할 수 없는 가격이었다.
# 같은 날 아침 매수는 잔고 쪽 가격(273,500)이 우연히 격자에 맞아 통과했을 뿐이다.
#
# (구간 상한, 단위) — 가격이 상한 **미만**이면 그 단위를 쓴다. 2023-01-25 개편 기준.
# 첫 구간(2,000원 미만)의 법정 단위는 1원이지만 여기선 5원을 쓴다:
# ETF·ETN은 전 구간 5원이고, 5원 격자 위의 값은 1원 격자 위에도 항상 있다.
# 즉 5원으로 통일하면 종목 유형을 몰라도 항상 유효한 가격이 나온다. 대신 2,000원 미만
# 일반 종목은 최대 4원(0.2%) 손해를 보는데, 유형 오판으로 주문이 통째로 막히는 것보다 낫다.
KR_TICK_TABLE = (
    (2_000, 5),
    (5_000, 5),
    (20_000, 10),
    (50_000, 50),
    (200_000, 100),
    (500_000, 500),
)
KR_TICK_ABOVE = 1_000       # 50만원 이상

# ------------------------------------------------------------ KOSPI 업종 로스터
#
# 브로커가 주는 38개 지수 중 **실제 업종만** 고른다. 나머지는 집계·파생이라
# 섞으면 이중계상이 된다: 0001 종합 · 0002~0004 규모별(대형/중형/소형) ·
# 0027 제조(0005~0015를 다시 합친 것) · 016x 배당지수 · 2xxx 파생·테마.
#
# 이 집합이 분석노트 §3 섹터 보드의 **행 수를 고정**한다. 로스터가 고정돼야
# "안 본 업종"이 빈칸으로 드러난다 — 목록이 매번 달라지면 누락이 다시 침묵이 된다.
# 이름은 하드코딩하지 않는다(브로커의 hts_kor_isnm을 그대로 쓴다) — 여기서 정하는 건
# **무엇을 세는가**뿐이고, 무엇이라 부르는가는 거래소가 정한다.
KR_SECTOR_CODES = frozenset({
    "0005", "0006", "0007", "0008", "0009", "0010", "0011", "0012",
    "0013", "0014", "0015", "0016", "0017", "0018", "0019", "0020",
    "0021", "0024", "0025", "0026", "0028", "0029", "0030",
})  # 23개 (2026-09-09 실측)


def kr_tick_size(price: float) -> int:
    """이 가격대의 호가단위."""
    for upper, tick in KR_TICK_TABLE:
        if price < upper:
            return tick
    return KR_TICK_ABOVE


def snap_kr_price(price: float, side: str) -> int:
    """지정가를 호가 격자에 올린다. 매수는 올리고 매도는 내린다.

    반올림이 아니라 방향을 정한 이유: 무인 운영에서는 **안 나간 주문이 손해보다 나쁘다**.
    체결 안 된 지정가는 오류도 안 내고 조용히 아무 일도 안 일으키는데, 로그에는
    '주문 성공'으로 남아 그날 판단이 집행된 것처럼 보인다. 그래서 시장가 쪽으로 붙여
    체결을 우선한다 — 비용은 최대 1틱이다.

    구간 경계값(2,000·5,000·20,000·50,000·200,000·500,000)은 모두 위 구간 단위의
    배수라, 올림이 다음 구간으로 넘어가도 그 구간의 격자 위에 그대로 있다.
    """
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    tick = kr_tick_size(price)
    if side == "BUY":
        snapped = int(-(-price // tick) * tick)      # 올림
    else:
        snapped = int(price // tick * tick)          # 내림
    return max(snapped, tick)


def order_excd(excd: str) -> str:
    """주문용 거래소 코드로 정규화한다. 시세용(NAS/NYS/AMS)이 오면 주문용으로 바꾼다."""
    e = (excd or "NASD").upper()
    return EXCD_ORDER.get(e, e)


# 주문용 → 시세용. `order_excd`의 짝이다.
EXCD_QUOTE = {v: k for k, v in EXCD_ORDER.items()}          # NASD→NAS · NYSE→NYS · AMEX→AMS


def quote_excd(excd: str) -> str:
    """**시세 TR용** 거래소 코드로 정규화한다. 주문용(NASD/NYSE/AMEX)이 오면 시세용(NAS/NYS/AMS)으로 바꾼다.

    ★ 왜 이 짝이 필요한가(2026-09-23). 시세 TR에 주문용 코드를 주면 **에러가 아니라 `rt_cd=0`에 빈 output**이 온다 —
    가격도 시가총액도 매매가능 플래그도 전부 공백이다. `universe_apply`가 `EXCD=NYSE`로 물어 4 run 동안
    "브로커 매매 판정 ''"(= 브로커가 거부했다)로 기록했는데, 실제로는 **우리가 못 물어본 것**이었다.
    올바른 코드(`NYS`)로는 GEV·CMI·VLO·IBKR 전부 `'매매 가능'`이 온다. 코드 계열을 섞지 말 것.
    """
    e = (excd or "NAS").upper()
    return EXCD_QUOTE.get(e, e)


def _us_dst(d) -> bool:
    """미국 서머타임 여부(3월 둘째 일요일 ~ 11월 첫째 일요일). d는 현지(ET) 날짜."""
    from datetime import date as _date
    y = d.year
    march = _date(y, 3, 1)
    second_sun = march + timedelta(days=(6 - march.weekday()) % 7 + 7)
    nov = _date(y, 11, 1)
    first_sun = nov + timedelta(days=(6 - nov.weekday()) % 7)
    return second_sun <= d < first_sun


HOLIDAYS_PATH = CONFIG_DIR / "holidays.json"
_HOL_CACHE = {"key": None, "data": {}}


def load_holidays() -> dict:
    """`config/holidays.json` — {"kr": [...], "us": [...]}. 없거나 깨졌으면 빈 dict(휴장을 모른다 = 예전 동작)."""
    try:
        key = (str(HOLIDAYS_PATH), HOLIDAYS_PATH.stat().st_mtime)
    except OSError:
        return {}
    if _HOL_CACHE["key"] != key:
        try:
            _HOL_CACHE["data"] = json.loads(HOLIDAYS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _HOL_CACHE["data"] = {}
        _HOL_CACHE["key"] = key
    return _HOL_CACHE["data"] or {}


def is_holiday(market: str, d) -> bool:
    """그 시장의 거래소 휴장일인가(주말은 여기서 보지 않는다)."""
    days = load_holidays().get((market or "KR").lower()) or []
    return d.isoformat() in days


def next_trading_day(market: str, d):
    """d **다음** 거래일(주말·휴장 제외)."""
    while True:
        d = d + timedelta(days=1)
        if d.weekday() < 5 and not is_holiday(market, d):
            return d


def market_session(market: str, now: datetime = None) -> dict:
    """이 시장의 **정규장** 창(KST). 반환 {open, close, is_open, session_date, holiday, why}.

    ★ 휴장(`config/holidays.json`)도 닫힌 날이다 — 2026-09-21까지 모든 개장 판정이 `weekday() < 5`뿐이라
    추석 휴장(09-24·25)에 `stops.py`·`stage.py market`이 국내장을 열린 것으로 봤다.

    KR 09:00–15:30 · US 09:30–16:00 ET(= KST 22:30–05:00 서머타임 / 23:30–06:00 표준시).
    왜 여기 있나: 장외에 보낸 주문은 브로커가 `[40580000] 모의투자 장종료`로 거부하고
    (2026-09-10 MSFT, 11:31 KST 전송), 장 마감 뒤 남은 지정가는 소멸한다 — 전송(execute)과
    체결 확정(fill) 둘 다 "지금 장이 열려 있나·언제 닫히나"를 알아야 한다.
    """
    now = now or datetime.now(KST)
    m = (market or "KR").upper()
    if m == "KR":
        d = now.date()
        o = datetime(d.year, d.month, d.day, 9, 0, tzinfo=KST)
        c = datetime(d.year, d.month, d.day, 15, 30, tzinfo=KST)
        hol = is_holiday("KR", d)
        is_open = d.weekday() < 5 and not hol and o <= now <= c
        return {"market": "KR", "open": o, "close": c, "is_open": is_open, "holiday": hol,
                "session_date": d, "why": "KRX 정규장 09:00–15:30 KST" + (" · 휴장(holidays.json)" if hol else "")}
    # US — ET 기준 날짜로 서머타임을 정하고 KST로 옮긴다.
    utc = now.astimezone(timezone.utc)
    et_off = -4 if _us_dst((utc - timedelta(hours=4)).date()) else -5
    et = utc + timedelta(hours=et_off)
    d = et.date()
    o_et = datetime(d.year, d.month, d.day, 9, 30, tzinfo=timezone(timedelta(hours=et_off)))
    c_et = datetime(d.year, d.month, d.day, 16, 0, tzinfo=timezone(timedelta(hours=et_off)))
    o, c = o_et.astimezone(KST), c_et.astimezone(KST)
    hol = is_holiday("US", d)
    is_open = d.weekday() < 5 and not hol and o <= now <= c
    return {"market": "US", "open": o, "close": c, "is_open": is_open, "session_date": d, "holiday": hol,
            "why": f"NYSE 정규장 09:30–16:00 ET = KST {o:%H:%M}–{c:%H:%M} ({'EDT' if et_off == -4 else 'EST'})"
                   + (" · 휴장(holidays.json)" if hol else "")}


class KisError(RuntimeError):
    """API가 성공(rt_cd=0) 이외를 돌려줬거나 HTTP가 실패한 경우."""


def _real_trading_enabled() -> bool:
    """limits.json의 실전 래치. 파일이 없거나 깨졌으면 False(fail-closed)."""
    try:
        cfg = json.loads((CONFIG_DIR / "limits.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return cfg.get("real_trading_enabled") is True


def load_env() -> dict:
    """.env를 병합해 읽는다(nearest-first). dotenv 의존 없음 — gmail_imap.py와 동일."""
    found = [p for p in ENV_CANDIDATES if p.exists()]
    if not found:
        raise FileNotFoundError(
            "No .env found. Looked in:\n  "
            + "\n  ".join(str(p) for p in ENV_CANDIDATES)
            + "\nAdd:\n  KIS_PAPER_APP_KEY=...\n  KIS_PAPER_APP_SECRET=...\n"
              "  KIS_PAPER_ACCOUNT=00000000-01"
        )
    env = {}
    for path in found:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip().strip("'\""))
    return env


class KisClient:
    def __init__(self, svr: str = "paper", allow_real: bool = False):
        if svr not in BASE_URL:
            raise ValueError(f"svr must be 'paper' or 'real', got {svr!r}")
        if svr == "real":
            # 래치 2개를 모두 넘어야 실전이 열린다.
            # ① 코드 경로가 명시적으로 allow_real=True를 넘길 것(--real 플래그)
            # ② config/limits.json의 real_trading_enabled가 true일 것(사람이 파일을 고칠 것)
            # .env에 실전 키를 넣어둔 뒤로는 '키가 없어서 못 나간다'는 방어가 사라졌으므로,
            # 그 자리를 이 두 번째 래치가 대신한다. 특히 헤드리스 자동 실행에서 중요하다.
            if not allow_real:
                raise KisError(
                    "실전 서버는 allow_real=True 없이는 열 수 없다. "
                    "이 프로젝트는 모의 검증을 통과하기 전까지 실전을 쓰지 않는다."
                )
            if not _real_trading_enabled():
                raise KisError(
                    "config/limits.json의 real_trading_enabled가 false다 — 실전 차단.\n"
                    "실계좌로 넘어갈 준비가 됐다고 판단했을 때 사람이 직접 true로 바꿀 것."
                )
        self.svr = svr
        self.base = BASE_URL[svr]
        self.tr = TR[svr]

        env = load_env()
        prefix = "KIS_PAPER" if svr == "paper" else "KIS_REAL"
        self.app_key = env.get(f"{prefix}_APP_KEY", "")
        self.app_secret = env.get(f"{prefix}_APP_SECRET", "")
        account = env.get(f"{prefix}_ACCOUNT", "")
        if not (self.app_key and self.app_secret and account):
            raise KisError(
                f"{prefix}_APP_KEY / {prefix}_APP_SECRET / {prefix}_ACCOUNT 가 .env에 필요하다. "
                f"계좌는 '00000000-01' 형식(앞 8자리-뒤 2자리)."
            )
        if "-" not in account:
            raise KisError(f"{prefix}_ACCOUNT 형식은 '00000000-01' 이어야 한다: {account!r}")
        self.cano, self.acnt_prdt_cd = account.split("-", 1)

        self._ctx = ssl.create_default_context()
        self._last_call = 0.0
        self._token = None

    # ------------------------------------------------------------- transport

    def _throttle(self):
        interval = CALL_INTERVAL_SEC.get(self.svr, 0.8)
        gap = time.monotonic() - self._last_call
        if gap < interval:
            time.sleep(interval - gap)
        self._last_call = time.monotonic()

    def _request(self, method: str, path: str, headers: dict,
                 params: dict = None, body: dict = None, timeout: int = 20) -> dict:
        """레이트리밋(EGW00201)은 물러섰다가 재시도한다. 나머지 오류는 즉시 올린다.

        주의: 재시도는 **조회에만** 안전하다. 주문은 재시도하면 이중 체결 위험이 있어
        POST는 재시도하지 않는다(아래 allow_retry 참조).
        """
        url = self.base + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode("utf-8") if body is not None else None
        allow_retry = method.upper() == "GET"

        last_err = None
        max_tries = max(RATE_LIMIT_RETRIES, TRANSIENT_RETRIES) + 1
        for attempt in range(max_tries):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            self._throttle()
            try:
                with urllib.request.urlopen(req, timeout=timeout, context=self._ctx) as r:
                    res = json.loads(r.read().decode("utf-8"))
                if (res.get("msg_cd") in TRANSIENT_CODES
                        and allow_retry and attempt < TRANSIENT_RETRIES):
                    time.sleep(TRANSIENT_BACKOFF_SEC * (attempt + 1))
                    last_err = KisError(f"{res.get('msg_cd')} on {method} {path}: {res.get('msg1')}")
                    continue
                return res
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:500]
                # KIS는 레이트리밋·게이트웨이 오류를 HTTP 500 + 본문 코드로 돌려준다. 코드가 없는
                # 5xx도 조회면 일시 장애로 보고 물러선다 — 4xx(인증·파라미터)는 다시 불러도 같다.
                transient = any(c in detail for c in TRANSIENT_CODES) or 500 <= e.code < 600
                if transient and allow_retry and attempt < TRANSIENT_RETRIES:
                    time.sleep(TRANSIENT_BACKOFF_SEC * (attempt + 1))
                    last_err = KisError(f"HTTP {e.code} on {method} {path}: {detail}")
                    continue
                raise KisError(f"HTTP {e.code} on {method} {path}: {detail}") from e
            except urllib.error.URLError as e:
                # 연결 자체가 안 된 것 — 조회면 한두 번 더 시도한다.
                if allow_retry and attempt < TRANSIENT_RETRIES:
                    time.sleep(TRANSIENT_BACKOFF_SEC * (attempt + 1))
                    last_err = KisError(f"network error on {method} {path}: {e.reason}")
                    continue
                raise KisError(f"network error on {method} {path}: {e.reason}") from e
            except (socket.timeout, TimeoutError, http.client.HTTPException, ConnectionError, OSError,
                    json.JSONDecodeError) as e:
                # ★ 읽기 타임아웃(`socket.timeout`)·끊긴 연결·깨진 본문은 URLError가 **아니라** 그대로 튀어 올라
                #   호출자 프로세스를 죽였다(2026-09-22 KR: 잔고 조회 13:39 · 상태 TR 15:23 → execute.py 예외 종료,
                #   매수 미전송). 전부 KisError로 감싸고, 조회면 재시도한다 — 주문(POST)은 재시도하지 않는다(중복 주문).
                if allow_retry and attempt < TRANSIENT_RETRIES:
                    time.sleep(TRANSIENT_BACKOFF_SEC * (attempt + 1))
                    last_err = KisError(f"network error on {method} {path}: {type(e).__name__}: {e}")
                    continue
                raise KisError(f"network error on {method} {path}: {type(e).__name__}: {e}") from e

        raise KisError(f"재시도 {TRANSIENT_RETRIES}회 소진: {method} {path} — 마지막: {last_err}")

    # ----------------------------------------------------------------- token

    def _load_cached_token(self):
        if not TOKEN_CACHE.exists():
            return None
        try:
            cache = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if cache.get("svr") != self.svr or cache.get("app_key_tail") != self.app_key[-6:]:
            return None
        try:
            expires = datetime.fromisoformat(cache["expires_at"])
        except (KeyError, ValueError):
            return None
        # 만료 10분 전부터는 새로 받는다.
        if expires - timedelta(minutes=10) <= datetime.now(KST):
            return None
        return cache.get("access_token")

    def token(self) -> str:
        """접근토큰. 24h 유효이고 재발급 빈도 제한이 있어 파일에 캐시한다."""
        if self._token:
            return self._token
        cached = self._load_cached_token()
        if cached:
            self._token = cached
            return cached

        res = self._request(
            "POST", "/oauth2/tokenP",
            headers={"Content-Type": "application/json"},
            body={"grant_type": "client_credentials",
                  "appkey": self.app_key, "appsecret": self.app_secret},
        )
        tok = res.get("access_token")
        if not tok:
            raise KisError(f"토큰 발급 실패: {res}")
        expires_in = int(res.get("expires_in", 86400))
        CONFIG_DIR.mkdir(exist_ok=True)
        TOKEN_CACHE.write_text(json.dumps({
            "svr": self.svr,
            "app_key_tail": self.app_key[-6:],
            "access_token": tok,
            "expires_at": (datetime.now(KST) + timedelta(seconds=expires_in)).isoformat(),
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            TOKEN_CACHE.chmod(0o600)
        except OSError:
            pass
        self._token = tok
        return tok

    def _headers(self, tr_id: str) -> dict:
        # hashkey 헤더는 넣지 않는다. 공식 주문 샘플(order_cash.py)도 보내지 않으며,
        # 빈 문자열로 넣는 것은 문서에 없는 미검증 동작이다.
        return {
            "Content-Type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token()}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    @staticmethod
    def _check(res: dict, what: str) -> dict:
        if str(res.get("rt_cd", "")) != "0":
            raise KisError(f"{what} 실패 [{res.get('msg_cd')}] {res.get('msg1')}")
        return res

    # ------------------------------------------------------------- 국내주식

    def domestic_price(self, ticker: str) -> dict:
        """현재가 1건. 반환: {ticker, price, change_pct, market, sector, volume}

        **종목명은 이 TR이 주지 않는다.** 예전엔 `rprs_mrkt_kor_name`을 name으로 담았는데
        그건 종목명이 아니라 소속 시장("KOSPI200")이다 — 워치리스트에 이름이 있는 종목은
        ingest가 그 이름으로 덮어써서 안 드러났고, 신규 종목을 조회할 때 드러났다
        (2026-09-08: 유니버스 후보 코드 확인 중 전 종목이 "KOSPI200"으로 나왔다).
        대신 업종명(`bstp_kor_isnm`)을 담는다 — 후보 종목이 어느 축에 걸리는지 확인할 때 쓴다.
        """
        res = self._request(
            "GET", "/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=self._headers(self.tr["dom_price"]),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
        )
        self._check(res, f"국내 현재가({ticker})")
        o = res.get("output", {}) or {}
        return {
            "ticker": ticker,
            "market": o.get("rprs_mrkt_kor_name", ""),
            "sector": o.get("bstp_kor_isnm", ""),
            "price": float(o.get("stck_prpr") or 0),
            "change_pct": float(o.get("prdy_ctrt") or 0),
            "volume": int(o.get("acml_vol") or 0),
        }

    def domestic_balance(self) -> dict:
        """잔고. 반환: {cash, positions:[{ticker,name,qty,avg_price,price,eval_amt,pnl_pct}]}"""
        res = self._request(
            "GET", "/uapi/domestic-stock/v1/trading/inquire-balance",
            headers=self._headers(self.tr["dom_balance"]),
            params={
                "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
                "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            },
        )
        self._check(res, "국내 잔고")
        positions = []
        for row in res.get("output1", []) or []:
            qty = int(float(row.get("hldg_qty") or 0))
            if qty <= 0:
                continue
            positions.append({
                "market": "KR",
                "ticker": row.get("pdno", ""),
                "name": row.get("prdt_name", ""),
                "qty": qty,
                "avg_price": float(row.get("pchs_avg_pric") or 0),
                "price": float(row.get("prpr") or 0),
                "eval_amt": float(row.get("evlu_amt") or 0),
                "pnl_pct": float(row.get("evlu_pfls_rt") or 0),
            })
        summary = (res.get("output2") or [{}])[0]
        cash, cash_field = kr_cash_from_summary(summary)
        return {
            "currency": "KRW",
            "cash": cash,
            "cash_field": cash_field,
            "deposit_total": float(summary.get("dnca_tot_amt") or 0),
            "eval_total": float(summary.get("tot_evlu_amt") or 0),
            # 당일 제비용과 체결금액. 브로커가 직접 계산해 주는 값이라 우리가 요율을
            # 추정할 필요가 없다. 저널이 "손익 중 얼마가 비용이었나"를 분리하는 근거다.
            # 2026-09-08 왕복 실측: 체결 547,250원에 제비용 606원(0.22%) — 거의 전부가
            # 매도 쪽 증권거래세다. 매일 왕복하면 연 50%대 회전비용이 되므로,
            # 이 숫자를 안 남기면 판단이 나빴는지 비용이 먹었는지 구분할 수 없다.
            "today_fees": float(summary.get("thdt_tlex_amt") or 0),
            "today_buy_amt": float(summary.get("thdt_buy_amt") or 0),
            "today_sell_amt": float(summary.get("thdt_sll_amt") or 0),
            "positions": positions,
        }

    def domestic_daily(self, ticker: str, start: str, end: str) -> list:
        """일별 시세(최신순). 반환 [{date, close, open, high, low, volume}].

        왜 필요한가: 트리거의 가격 기준을 **계산**하려면 과거가 있어야 한다. 스냅샷에는
        당일 값과 전일 종가밖에 없어서, 그것만으로 '급등 전 종가'나 '20일 저점' 같은
        기준을 잡을 수 없다 — 없으면 감으로 숫자를 고르게 되고 실제로 그렇게 됐다
        (2026-09-08: '급등분 되돌림'이라며 268,000을 썼는데 진짜 급등 전 종가는 255,500,
        그 값은 현재가 대비 -0.6%라 되돌림이 전혀 아니었다).
        """
        res = self._request(
            "GET", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=self._headers("FHKST03010100"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker,
                    "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end,
                    "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"},
        )
        self._check(res, f"국내 일별시세({ticker})")
        out = []
        for r in res.get("output2") or []:
            if not r.get("stck_clpr"):
                continue
            out.append({"date": r.get("stck_bsop_date", ""),
                        "close": int(r["stck_clpr"]), "open": int(r.get("stck_oprc") or 0),
                        "high": int(r.get("stck_hgpr") or 0), "low": int(r.get("stck_lwpr") or 0),
                        "volume": int(r.get("acml_vol") or 0)})
        return out

    def domestic_sector_board(self, market: str = "K") -> list:
        """KOSPI 전 업종 지수를 **한 번의 호출로** 받는다. 반환은 등락률 내림차순.

        왜 있는가: 노트가 "어떤 시장을 볼까"를 뉴스 언급 수로 정하면 뉴스가 안 쓴 곳은
        영원히 안 보인다. 실제로 2026-09-08 노트는 그날 1·2위로 오른 전기·가스와 건설을
        통째로 빠뜨리고, 유일하게 다룬 전기·전자는 그날 내렸다(-0.41%).
        **국면은 세는 게 아니라 재는 것이다.**

        업종명은 브로커가 준다(`hts_kor_isnm`) — 코드↔이름을 여기서 추측하지 않는다.
        """
        res = self._request(
            "GET", "/uapi/domestic-stock/v1/quotations/inquire-index-category-price",
            headers=self._headers("FHPUP02140000"),
            params={"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": "0001",
                    "FID_COND_SCR_DIV_CODE": "20214", "FID_MRKT_CLS_CODE": market,
                    "FID_BLNG_CLS_CODE": "0"},
        )
        self._check(res, "국내 업종 전체시세")
        out = []
        for r in res.get("output2") or []:
            code, name = r.get("bstp_cls_code", ""), r.get("hts_kor_isnm", "").strip()
            if not (code and name) or not r.get("bstp_nmix_prpr"):
                continue
            out.append({
                "code": code, "name": name,
                "close": float(r["bstp_nmix_prpr"]),
                "change_pct": float(r.get("bstp_nmix_prdy_ctrt") or 0),
                # acml_tr_pbmn 단위는 백만원이다. 억원으로 바꿔 다른 표와 자릿수를 맞춘다.
                "turnover_100m": float(r.get("acml_tr_pbmn") or 0) / 100,
                "is_sector": code in KR_SECTOR_CODES,
            })
        out.sort(key=lambda s: s["change_pct"], reverse=True)
        return out

    def domestic_index_daily(self, code: str, start: str, end: str) -> list:
        """업종 지수의 일별 이력(최신순). 5일·20일 등락과 추세를 여기서 계산한다.

        반환 모양을 `domestic_daily`와 맞춘다 — 같은 계산기를 두 곳에 쓰기 위해서다.
        """
        res = self._request(
            "GET", "/uapi/domestic-stock/v1/quotations/inquire-daily-indexchartprice",
            headers=self._headers("FHKUP03500100"),
            params={"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": start, "FID_INPUT_DATE_2": end,
                    "FID_PERIOD_DIV_CODE": "D"},
        )
        self._check(res, f"국내 업종 일별지수({code})")
        out = []
        for r in res.get("output2") or []:
            if not r.get("bstp_nmix_prpr"):
                continue
            out.append({"date": r.get("stck_bsop_date", ""),
                        "close": float(r["bstp_nmix_prpr"]),
                        "open": float(r.get("bstp_nmix_oprc") or 0),
                        "high": float(r.get("bstp_nmix_hgpr") or 0),
                        "low": float(r.get("bstp_nmix_lwpr") or 0),
                        "volume": int(float(r.get("acml_vol") or 0))})
        return out

    def overseas_daily(self, ticker: str, excd: str = "NAS", bymd: str = "") -> list:
        """해외 일별 시세(최신순, 최대 100행). 반환은 `domestic_daily`와 같은 형태다 —
        같은 계산기(`price_levels.py`)가 두 시장을 다 다루려면 모양이 같아야 한다.

        `bymd`(YYYYMMDD)는 조회 기준일. 비우면 최근이다. 100행이 상한이라 더 긴 이력이
        필요하면 기준일을 앞으로 옮겨 여러 번 부른다.
        """
        res = self._request(
            "GET", "/uapi/overseas-price/v1/quotations/dailyprice",
            headers=self._headers("HHDFS76240000"),
            params={"AUTH": "", "EXCD": excd, "SYMB": ticker,
                    "GUBN": "0", "BYMD": bymd, "MODP": "1"},
        )
        self._check(res, f"해외 일별시세({ticker})")
        out = []
        for r in res.get("output2") or []:
            if not r.get("clos"):
                continue
            out.append({"date": r.get("xymd", ""),
                        "close": float(r["clos"]), "open": float(r.get("open") or 0),
                        "high": float(r.get("high") or 0), "low": float(r.get("low") or 0),
                        "volume": int(float(r.get("tvol") or 0))})
        return out

    def domestic_order(self, ticker: str, side: str, qty: int,
                       price: int = 0, market_order: bool = False) -> dict:
        """국내 현금 매수/매도. market_order=True면 ORD_UNPR은 무시된다(시장가)."""
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        # 격자 밖 가격은 여기서 막는다. 정상 경로(risk_guard)는 이미 맞춰서 넘기므로
        # 이건 그 경로를 우회한 호출을 잡는 그물이다 — 브로커의 [40030000]보다
        # 여기서 나는 오류가 원인을 훨씬 빨리 알려준다.
        if not market_order:
            tick = kr_tick_size(price)
            if int(price) % tick:
                raise ValueError(
                    f"{ticker} 지정가 {int(price):,}원이 호가단위 {tick}원의 배수가 아니다. "
                    f"snap_kr_price()로 맞춰서 넘길 것 (가까운 값: "
                    f"{snap_kr_price(price, side):,})"
                )
        tr_id = self.tr["dom_buy"] if side == "BUY" else self.tr["dom_sell"]
        res = self._request(
            "POST", "/uapi/domestic-stock/v1/trading/order-cash",
            headers=self._headers(tr_id),
            body={
                "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "PDNO": ticker,
                "ORD_DVSN": ORD_DVSN_MARKET if market_order else ORD_DVSN_LIMIT,
                "ORD_QTY": str(int(qty)),
                "ORD_UNPR": "0" if market_order else str(int(price)),
                "EXCG_ID_DVSN_CD": "KRX",
                "SLL_TYPE": "01" if side == "SELL" else "",
                "CNDT_PRIC": "",
            },
        )
        self._check(res, f"국내 주문({side} {ticker} x{qty})")
        o = res.get("output", {}) or {}
        # raw는 응답 **전체**를 담는다. output만 남기면 주문번호를 못 읽었을 때
        # 무슨 일이 있었는지 사후에 복원할 수 없다(B3).
        return {"order_no": o.get("ODNO", ""), "order_time": o.get("ORD_TMD", ""),
                "orgno": o.get("KRX_FWDG_ORD_ORGNO", ""), "raw": res}

    def domestic_order_status(self, order_no: str, ticker: str = "", date: str = "") -> dict:
        """국내 주문 1건의 체결 상태. 반환 {order_no, ord_qty, filled_qty, remain_qty, avg_price,
        cancelled, rejected, found, raw}.

        **접수는 체결이 아니다** — 주문번호가 나온 뒤 실제로 몇 주가 체결됐는지는 이 TR만 안다.
        잔고 대조(전후 수량 차이)는 다른 주문·정정 체인이 섞이면 틀리고, 잔고 TR이 죽으면
        아예 못 한다(2026-09-16 00:23~00:27 실측). 못 찾으면 found=False로 돌려준다 —
        '없다'와 '0주 체결'은 다른 사실이다.
        """
        # ★ 모의서버는 이 TR로 **당일 주문만** 돌려준다(2026-09-16 실측: 9/8~9/14 주문 전부
        #   "조회할 내역이 없습니다"). 체결 확정은 그날 안에 하므로 문제는 없지만, 지난 주문을
        #   여기로 다시 확인할 수는 없다 — found=False가 '없었다'는 뜻이 아니다.
        day = date or datetime.now(KST).strftime("%Y%m%d")
        res = self._request(
            "GET", "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
            headers=self._headers(self.tr["dom_ccld"]),
            params={
                "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "INQR_STRT_DT": day, "INQR_END_DT": day,
                "SLL_BUY_DVSN_CD": "00", "INQR_DVSN": "00", "PDNO": ticker or "",
                "CCLD_DVSN": "00", "ORD_GNO_BRNO": "", "ODNO": "",
                "INQR_DVSN_3": "00", "INQR_DVSN_1": "",
                "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
            },
        )
        self._check(res, f"국내 주문체결조회({order_no})")
        rows = res.get("output1") or []
        want = str(order_no).lstrip("0") or "0"
        for r in rows:
            if str(r.get("odno", "")).lstrip("0") != want:
                continue
            ord_qty = int(float(r.get("ord_qty") or 0))
            filled = int(float(r.get("tot_ccld_qty") or 0))
            remain = int(float(r.get("rmn_qty") or 0))
            rejected_qty = int(float(r.get("rjct_qty") or 0))
            return {
                "order_no": order_no, "found": True,
                "ord_qty": ord_qty, "filled_qty": filled, "remain_qty": remain,
                "avg_price": float(r.get("avg_prvs") or 0),
                "cancelled": str(r.get("cncl_yn", "")).upper() == "Y",
                "rejected": rejected_qty > 0 and filled == 0,
                "orgno": r.get("ord_gno_brno", ""),
                "raw": r,
            }
        return {"order_no": order_no, "found": False, "ord_qty": 0, "filled_qty": 0,
                "remain_qty": 0, "avg_price": 0.0, "cancelled": False, "rejected": False,
                "orgno": "", "raw": {"rows": len(rows)}}

    def domestic_modify(self, order_no: str, orgno: str, qty: int, price: int = 0,
                        cancel: bool = False) -> dict:
        """국내 정정(price로) 또는 취소. 반환 {order_no(새 번호), raw}. **재시도 없음**(POST).

        정정·취소는 새 주문번호를 받는다 — 호출자가 체인으로 이어 붙여 체결을 합산해야 한다.
        `QTY_ALL_ORD_YN=Y`라 잔량 전부에 적용된다(부분 정정은 쓰지 않는다 — 주문이 둘로 갈라진다).
        """
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        if not cancel:
            tick = kr_tick_size(price)
            if int(price) % tick:
                raise ValueError(f"정정가 {int(price):,}원이 호가단위 {tick}원의 배수가 아니다")
        res = self._request(
            "POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl",
            headers=self._headers(self.tr["dom_rvsecncl"]),
            body={
                "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "KRX_FWDG_ORD_ORGNO": orgno or "", "ORGN_ODNO": str(order_no),
                "ORD_DVSN": ORD_DVSN_LIMIT,
                "RVSE_CNCL_DVSN_CD": CNCL if cancel else RVSE,
                "ORD_QTY": str(int(qty)), "ORD_UNPR": "0" if cancel else str(int(price)),
                "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX",
            },
        )
        self._check(res, f"국내 {'취소' if cancel else '정정'}({order_no})")
        o = res.get("output", {}) or {}
        return {"order_no": o.get("ODNO", ""), "order_time": o.get("ORD_TMD", ""),
                "orgno": o.get("KRX_FWDG_ORD_ORGNO", orgno), "raw": res}

    # ------------------------------------------------------------- 해외주식

    def overseas_price(self, ticker: str, excd: str = "NAS") -> dict:
        """해외 현재가. excd는 시세용 코드(NAS/NYS/AMS)로 주문용(NASD/NYSE/AMEX)과 다르다."""
        res = self._request(
            "GET", "/uapi/overseas-price/v1/quotations/price",
            headers=self._headers(self.tr["ov_price"]),
            params={"AUTH": "", "EXCD": excd, "SYMB": ticker},
        )
        self._check(res, f"해외 현재가({ticker})")
        o = res.get("output", {}) or {}
        return {
            "ticker": ticker,
            "price": float(o.get("last") or 0),
            "change_pct": float(o.get("rate") or 0),
            "currency": o.get("curr", "USD"),
        }

    def overseas_balance(self, excd: str = "NASD", currency: str = "USD") -> dict:
        res = self._request(
            "GET", "/uapi/overseas-stock/v1/trading/inquire-balance",
            headers=self._headers(self.tr["ov_balance"]),
            params={
                "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "OVRS_EXCG_CD": excd, "TR_CRCY_CD": currency,
                "CTX_AREA_FK200": "", "CTX_AREA_NK200": "",
            },
        )
        self._check(res, f"해외 잔고({excd})")
        positions = []
        for row in res.get("output1", []) or []:
            qty = int(float(row.get("ovrs_cblc_qty") or 0))
            if qty <= 0:
                continue
            positions.append({
                "market": "US",
                "ticker": row.get("ovrs_pdno", ""),
                "name": row.get("ovrs_item_name", ""),
                "qty": qty,
                "avg_price": float(row.get("pchs_avg_pric") or 0),
                "price": float(row.get("now_pric2") or 0),
                "eval_amt": float(row.get("ovrs_stck_evlu_amt") or 0),
                "pnl_pct": float(row.get("evlu_pfls_rt") or 0),
                "excd": excd,
            })
        summary = res.get("output2", {}) or {}
        # 이 TR의 output2에는 예수금이 없다(평가손익 요약이다). 현금은
        # overseas_present_balance()로 따로 읽는다 — cash를 여기서 추측해 채우면
        # 현금하한·서킷브레이커가 잘못된 값 위에서 돈다.
        return {
            "currency": currency,
            "cash": None,
            "eval_total": float(summary.get("tot_evlu_pfls_amt") or 0),
            "positions": positions,
        }

    def overseas_present_balance(self, natn_cd: str = "840",
                                 currency: str = "USD") -> dict:
        """해외 체결기준현재잔고 — 외화 예수금의 정본 출처.

        natn_cd: 000 전체 / 840 미국 / 344 홍콩 / 156 중국 / 392 일본 / 704 베트남.
        응답 필드명이 공식 샘플에 문서화돼 있지 않아 후보를 순회하고 raw를 함께 남긴다.
        Phase 0에서 raw를 눈으로 보고 OV_CASH_FIELDS를 확정할 것.
        """
        res = self._request(
            "GET", "/uapi/overseas-stock/v1/trading/inquire-present-balance",
            headers=self._headers(self.tr["ov_present"]),
            params={
                "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "WCRC_FRCR_DVSN_CD": "02",   # 02 = 외화
                "NATN_CD": natn_cd,
                "TR_MKET_CD": "00",          # 00 = 전체
                "INQR_DVSN_CD": "00",        # 00 = 전체
            },
        )
        self._check(res, f"해외 체결기준현재잔고({natn_cd})")

        rows = res.get("output2") or []
        if isinstance(rows, dict):
            rows = [rows]
        cash, matched_field = None, None
        for row in rows:
            if currency and str(row.get("crcy_cd", currency)).upper() != currency.upper():
                continue
            for field in OV_CASH_FIELDS:
                if row.get(field) not in (None, ""):
                    cash = float(row[field])
                    matched_field = field
                    break
            if cash is not None:
                break

        return {
            "currency": currency,
            "cash": cash,                    # 못 찾으면 None (0이 아니다)
            "cash_field": matched_field,
            "raw_output2": rows,             # Phase 0 필드 확정용
            "raw_output3": res.get("output3"),
        }

    def overseas_orderable(self, ticker: str, price: float, excd: str = "NASD") -> dict:
        """해외 주문가능금액(=실질 구매력). 매수 규모는 이 값으로 잡는다.

        왜 예수금이 아니라 이 TR인가: 모의계좌 실측(2026-09-07) 결과 외화 예수금
        (`frcr_dncl_amt_2`)은 0인데 주문가능금액은 USD 100,000이었다. 예수금으로
        규모를 잡으면 미국 매수가 항상 0주로 거부된다. 실전에서도 통합증거금·매도대금
        재사용 때문에 예수금 ≠ 구매력이므로, 이 TR이 정본이다.
        """
        res = self._request(
            "GET", "/uapi/overseas-stock/v1/trading/inquire-psamount",
            headers=self._headers(self.tr["ov_psamount"]),
            params={
                "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "OVRS_EXCG_CD": excd,
                "OVRS_ORD_UNPR": f"{price:.4f}",
                "ITEM_CD": ticker,
            },
        )
        self._check(res, f"해외 주문가능금액({ticker})")
        o = res.get("output", {}) or {}
        return {
            "currency": o.get("tr_crcy_cd", "USD"),
            "orderable_cash": float(o.get("ord_psbl_frcr_amt") or 0),
            "max_qty": int(float(o.get("max_ord_psbl_qty") or 0)),
            "exchange_rate": float(o.get("exrt") or 0),
        }

    def overseas_order(self, ticker: str, side: str, qty: int,
                       limit_price: float, excd: str = "NASD") -> dict:
        """해외(미국) 주문.

        limit_price 필수 — 모의투자 미국 주문은 지정가(00)만 받는다(공식 order.py 주석).
        시장가 폴백을 두지 않는 이유: 폴백이 있으면 모의에서 조용히 실패하고
        실전에서만 동작이 갈리는데, 그게 정확히 우리가 피하려는 종류의 사고다.
        """
        if side not in ("BUY", "SELL"):
            raise ValueError(f"side must be BUY or SELL, got {side!r}")
        if excd not in US_EXCHANGES:
            raise ValueError(f"지원 거래소는 {US_EXCHANGES}, got {excd!r}")
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        if not limit_price or limit_price <= 0:
            raise ValueError("해외 주문은 limit_price가 필수다(모의는 지정가만 지원).")
        tr_id = self.tr["ov_buy_us"] if side == "BUY" else self.tr["ov_sell_us"]
        res = self._request(
            "POST", "/uapi/overseas-stock/v1/trading/order",
            headers=self._headers(tr_id),
            body={
                "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "OVRS_EXCG_CD": excd, "PDNO": ticker,
                "ORD_QTY": str(int(qty)),
                "OVRS_ORD_UNPR": f"{limit_price:.2f}",
                "CTAC_TLNO": "", "MGCO_APTM_ODNO": "",
                "ORD_SVR_DVSN_CD": "0", "ORD_DVSN": ORD_DVSN_LIMIT,
            },
        )
        self._check(res, f"해외 주문({side} {ticker} x{qty})")
        o = res.get("output", {}) or {}
        return {"order_no": o.get("ODNO", ""), "order_time": o.get("ORD_TMD", ""),
                "orgno": o.get("KRX_FWDG_ORD_ORGNO", ""), "raw": res}

    def overseas_order_status(self, order_no: str, ticker: str = "", excd: str = "",
                              date: str = "") -> dict:
        """해외 주문 1건의 체결 상태 — `domestic_order_status`와 같은 모양으로 돌려준다.

        2026-09-16 00:39 실측(VTTS3035R): `ft_ord_qty 30 · ft_ccld_qty 30 · nccs_qty 0 ·
        ft_ccld_unpr3 141.66`. 거부는 `rjct_rson`(코드)·`rjct_rson_name`으로 온다.
        """
        # `ord_dt`는 **미국 현지 거래일**이다(2026-09-16 00:23 KST 주문이 20260915로 온다).
        # 날짜 하나를 찍으면 자정을 넘긴 run에서 못 찾으므로 세션일 ±1일로 묶어 조회한다.
        if date:
            start = end = date
        else:
            sd = market_session("US")["session_date"]
            start = (sd - timedelta(days=1)).strftime("%Y%m%d")
            end = (sd + timedelta(days=1)).strftime("%Y%m%d")
        res = self._request(
            "GET", "/uapi/overseas-stock/v1/trading/inquire-ccnl",
            headers=self._headers(self.tr["ov_ccld"]),
            params={
                "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "PDNO": ticker or "", "ORD_STRT_DT": start, "ORD_END_DT": end,
                "SLL_BUY_DVSN": "00", "CCLD_NCCS_DVSN": "00",
                "OVRS_EXCG_CD": order_excd(excd) if excd else "",
                "SORT_SQN": "DS", "ORD_DT": "", "ORD_GNO_BRNO": "", "ODNO": "",
                "CTX_AREA_NK200": "", "CTX_AREA_FK200": "",
            },
        )
        self._check(res, f"해외 주문체결내역({order_no})")
        rows = res.get("output") or []
        if isinstance(rows, dict):
            rows = [rows]
        want = str(order_no).lstrip("0") or "0"
        for r in rows:
            if str(r.get("odno", "")).lstrip("0") != want:
                continue
            ord_qty = int(float(r.get("ft_ord_qty") or 0))
            filled = int(float(r.get("ft_ccld_qty") or 0))
            remain = int(float(r.get("nccs_qty") or 0))
            rjct = str(r.get("rjct_rson", "") or "").strip()
            rvse = str(r.get("rvse_cncl_dvsn", "") or "").strip()
            return {
                "order_no": order_no, "found": True,
                "ord_qty": ord_qty, "filled_qty": filled, "remain_qty": remain,
                "avg_price": float(r.get("ft_ccld_unpr3") or 0),
                # 취소된 주문은 잔량 0·체결 0으로 남는다(rvse_cncl_dvsn 02). 거부는 사유 코드가 붙는다.
                "cancelled": rvse == CNCL or (remain == 0 and filled == 0 and not rjct
                                              and "취소" in str(r.get("prcs_stat_name", ""))),
                "rejected": bool(rjct and rjct not in ("0", "00")) and filled == 0,
                "reject_reason": r.get("rjct_rson_name", "") or rjct,
                "orgno": r.get("ord_gno_brno", ""),
                "raw": r,
            }
        return {"order_no": order_no, "found": False, "ord_qty": 0, "filled_qty": 0,
                "remain_qty": 0, "avg_price": 0.0, "cancelled": False, "rejected": False,
                "reject_reason": "", "orgno": "", "raw": {"rows": len(rows)}}

    def overseas_modify(self, order_no: str, ticker: str, excd: str, qty: int,
                        price: float = 0.0, cancel: bool = False) -> dict:
        """해외(미국) 정정 또는 취소. 반환 {order_no(새 번호), raw}. **재시도 없음**(POST)."""
        if qty <= 0:
            raise ValueError(f"qty must be positive, got {qty}")
        if not cancel and (not price or price <= 0):
            raise ValueError("정정에는 price가 필요하다")
        res = self._request(
            "POST", "/uapi/overseas-stock/v1/trading/order-rvsecncl",
            headers=self._headers(self.tr["ov_rvsecncl_us"]),
            body={
                "CANO": self.cano, "ACNT_PRDT_CD": self.acnt_prdt_cd,
                "OVRS_EXCG_CD": order_excd(excd), "PDNO": ticker,
                "ORGN_ODNO": str(order_no),
                "RVSE_CNCL_DVSN_CD": CNCL if cancel else RVSE,
                "ORD_QTY": str(int(qty)),
                "OVRS_ORD_UNPR": "0" if cancel else f"{price:.2f}",
                "MGCO_APTM_ODNO": "", "ORD_SVR_DVSN_CD": "0",
            },
        )
        self._check(res, f"해외 {'취소' if cancel else '정정'}({order_no})")
        o = res.get("output", {}) or {}
        return {"order_no": o.get("ODNO", ""), "order_time": o.get("ORD_TMD", ""), "raw": res}


def drop_incomplete_session(rows: list, min_ratio: float = 0.2) -> tuple:
    """가장 최근 행이 **미완성 세션**이면 떼어낸다. 반환 (쓸 행들, 떼어낸 행 or None).

    장중이거나 프리마켓이면 그날 행이 이미 존재하지만 종가·고저가 확정되지 않았다.
    그걸 기준 계산에 넣으면 지지·저항이 엉뚱한 값으로 나온다.
    판별은 **거래량**으로 한다 — 미완성 세션은 거래량이 확연히 적다.
    (2026-09-08 18:00 KST 실측: MU의 그날 행 거래량 242,504 vs 직전일 35,249,519 = 0.7%.)
    """
    if len(rows) < 5:
        return rows, None
    vols = sorted(r.get("volume", 0) for r in rows[1:21] if r.get("volume"))
    if not vols:
        return rows, None
    median = vols[len(vols) // 2]
    top = rows[0]
    if median and top.get("volume", 0) < median * min_ratio:
        return rows[1:], top
    return rows, None


def kr_cash_from_summary(summary: dict) -> tuple:
    """국내 잔고 output2에서 '쓸 수 있는 현금'을 고른다. 반환 (금액, 읽은 항목명).

    예수금총액(`dnca_tot_amt`)이 아니다. 국내주식 결제는 D+2라 이 값은 매수 당일에
    줄지 않는다. 그걸 cash로 쓰면 `equity = cash + Σeval_amt`에서 오늘 산 금액이
    양쪽에 잡혀 **이중계상**되고, 자산총액이 부풀어 목표비중→수량 환산이 과대해진다.
    2026-09-08 첫 실체결로 발견: 273,500원 매수 직후
      dnca_tot_amt 10,000,000 / prvs_rcdl_excc_amt 9,726,470 / tot_evlu_amt 10,000,470
    → `prvs_rcdl_excc_amt + 평가액 = tot_evlu_amt`로 딱 맞는다.
    미국 쪽이 예수금 대신 구매력을 읽는 것(`us_buying_power`)과 같은 이유다.

    항목명을 같이 돌려주는 것도 같은 이유다 — 어느 항목을 읽었는지 스냅샷에 남겨야
    나중에 잔고가 안 맞을 때 무엇을 봤는지 추적할 수 있다.
    """
    for field, label in (("prvs_rcdl_excc_amt", "prvs_rcdl_excc_amt"),
                         ("nxdy_excc_amt", "nxdy_excc_amt (D+1 대체)"),
                         ("dnca_tot_amt", "dnca_tot_amt (정산금액 없음 — 매수 당일 과대)")):
        raw = summary.get(field)
        if raw not in (None, ""):
            return float(raw), label
    return 0.0, "없음"


def us_buying_power(client: "KisClient", watchlist: dict) -> dict:
    """미국 시장의 실질 구매력을 잡는다 — ingest·journal이 **둘 다 이걸 쓴다**.

    왜 함수로 빼는가: 예수금(`frcr_dncl_amt_2`)과 구매력(`ord_psbl_frcr_amt`)이 다른데
    ingest만 고치고 journal이 예수금을 계속 읽어 **미국 벤치마크 기준점이 영영 안 박히는**
    버그가 났다(2026-09-08 발견). 로직이 두 곳에 흩어져 있던 것이 원인이므로 한 곳으로 모은다.

    반환: {cash, cash_field, exchange_rate, deposit_only, error}
    cash가 None이면 '미확인'이며, risk_guard가 매수를 보류한다(0원으로 뭉개지 않는다).
    """
    us = [e for e in (watchlist.get("US") or []) if e.get("ticker")]
    ref = us[0] if us else {"ticker": "AAPL", "excd": "NASD"}
    excd = ref.get("excd", "NASD")
    out = {"cash": None, "cash_field": None, "exchange_rate": None,
           "deposit_only": None, "error": None}
    try:
        quote_excd = {"NASD": "NAS", "NYSE": "NYS", "AMEX": "AMS"}.get(excd, "NAS")
        price = client.overseas_price(ref["ticker"], excd=quote_excd)["price"]
        ps = client.overseas_orderable(ref["ticker"], price, excd)
        out["cash"] = ps["orderable_cash"]
        out["cash_field"] = "ord_psbl_frcr_amt (VTTS3007R)"
        out["exchange_rate"] = ps["exchange_rate"]
    except KisError as e:
        out["error"] = str(e)
    try:                      # 참고용 예수금 — 구매력이 아니다. 진단에만 쓴다.
        out["deposit_only"] = client.overseas_present_balance().get("cash")
    except KisError:
        pass
    return out


def _selftest():
    """python3 kis_client.py — 키가 살아있는지, 3개 호출이 도는지 확인한다."""
    c = KisClient(svr="paper")
    # 계좌번호·토큰은 마스킹해 찍는다 — 콘솔 로그나 전사본에 원본이 남지 않게.
    masked = f"{c.cano[:2]}{'*' * max(0, len(c.cano) - 4)}{c.cano[-2:]}"
    print(f"[1/4] 서버={c.svr} 계좌={masked}-{c.acnt_prdt_cd}")
    tok = c.token()
    print(f"[2/4] 토큰 발급 OK (길이 {len(tok)}), 캐시={TOKEN_CACHE.name}")
    p = c.domestic_price("005930")
    print(f"[3/4] 국내 현재가 삼성전자: {p['price']:,.0f}원 ({p['change_pct']:+.2f}%)")
    b = c.domestic_balance()
    print(f"[4/4] 국내 잔고: 예수금 {b['cash']:,.0f}원 / 보유 {len(b['positions'])}종목")
    for pos in b["positions"]:
        print(f"        {pos['ticker']} {pos['name']} x{pos['qty']} ({pos['pnl_pct']:+.2f}%)")
    print("\n셀프테스트 통과.")


if __name__ == "__main__":
    _selftest()
