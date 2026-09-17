#!/usr/bin/env python3
"""검증 캐시 — 이미 대조한 클레임을 다시 대조하지 않는다.

뉴스레터 노트는 같은 제도가 반복 등장한다(금산법·장기보유특별공제·상법 특별결의…).
매번 처음부터 T1을 다시 찾으면 fact-check 시간의 대부분이 재작업이다.

    python3 cache_claims.py split _raw_sources/AI클레임_260831.md
        → 캐시 대조(A) / 신규 조달(B)로 갈라 `*_작업.md`로 저장

    python3 cache_claims.py add "금산법 10%룰" MATCH https://... --note "제24조 제1항 제3호"
    python3 cache_claims.py add "국민연금 목표비중" STALE https://... --volatile

    python3 cache_claims.py keys        # 키의 변별력 점검(앵커 없음·약한 키)
    python3 cache_claims.py selftest    # 매칭 회귀 테스트

`--volatile`(시효 있는 사실 — 요율·비중·부처명·정책)은 **캐시 히트여도 재확인 대상**으로 표시한다.
제도는 바뀌므로 캐시가 오히려 낡은 답을 굳히는 것을 막는다.

★ **이 도구가 틀리는 방향은 '못 찾음'보다 '엉뚱한 걸 찾음'이 위험하다.** 미적중은 새로 조달하면
그만이지만, 오적중은 *확보된 사실*이라는 이름표를 달고 검증을 통과시킨다. 그래서 애매하면
A절(자동 대조)이 아니라 C절(사람이 열어봄)로 보낸다 — 재현율보다 정밀도를 택한다.
"""
import json
import os
import re
import sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "_raw_sources", "검증캐시.json")
STOP = {"있음", "없음", "필요", "때문", "경우", "이것", "그것", "라는", "하는", "되는", "이라",
        "정의", "차이", "구조", "관련", "내용"}
HIT = 0.50    # 캐시 대조(A)로 보낼 합격선
NEAR = 0.35   # 합격선엔 못 미쳐도 사람이 볼 근접 후보(C) — 조용히 버리지 않는다
COVER = 0.60  # 클레임⊂키 역방향 인정에 필요한 글자 커버리지(짧은 조각 오탐 차단)
REV_MIN = 3   # 역방향을 인정할 조각의 **절대** 최소 길이 — 비율만으로는 짧은 조각을 못 막는다
WEAK = 2      # 이 길이 이하의 앵커만 가진 키는 '약한 키' — 부분 일치를 인정하지 않는다


def load():
    if os.path.isfile(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(d):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2, sort_keys=True)


def sig(text):
    """표현이 달라도 같은 클레임으로 묶이도록 의미 토큰 집합으로 정규화."""
    toks = re.findall(r"[가-힣A-Za-z]{2,}", text)
    return {t for t in toks if t not in STOP}


def norm(text):
    """부분문자열 대조용 — 공백·문장부호를 지운 연속 문자열로 만든다."""
    return re.sub(r"[^가-힣A-Za-z0-9]", "", text)


# 클레임 토큰 끝에 붙는 조사. 긴 것부터 벗겨야 `으로`가 `로`로 잘못 잘리지 않는다.
PARTICLES = ("이라는", "이라고", "에서는", "으로써", "에게서", "라는", "라고", "에서", "에게",
             "한테", "까지", "부터", "보다", "처럼", "으로", "이며", "로써", "로서",
             "은", "는", "이", "가", "을", "를", "의", "에", "도", "만", "와", "과", "로")


def stems(token):
    """토큰과 조사를 벗긴 형태를 함께 돌려준다 — `차상위는` → {차상위는, 차상위}.

    형태소 분석기 없이 접미 조사만 떼는 근사다. 어간이 2자 미만이면 안 뗀다
    (`제도`에서 `도`를 떼어 `제`가 되는 식의 과다 절단 방지).
    """
    out = {token}
    for p in PARTICLES:
        if token.endswith(p) and len(token) - len(p) >= 2:
            out.add(token[: -len(p)])
            break
    return out


# 추출 파일이 클레임 앞뒤에 달아두는 잡음. **매칭 앵커가 되면 안 된다.**
# 실사례(2026-09-10): `(L109) **정제마진** = …` 이라는 클레임이 키 `금산법 10%룰`에 0.40으로 걸렸다.
# 걸린 이유는 정제마진과 금산법이 닮아서가 아니라 **`L109`의 `10`이 키의 `10`과 맞았기** 때문이다.
NOISE = re.compile(r"\(L\d+\)|`?\[(AI|추정|기사|자체계산)\]`?|[*`>]")


def clean_claim(text):
    """매칭에 쓸 클레임 본문 — 위치 프리픽스·라벨·마크다운 강조를 걷어낸다."""
    return NOISE.sub(" ", text).strip()


def key_tokens(key):
    """캐시 키에서 식별 토큰. 키는 사람이 쓴 깔끔한 명사구라 조사가 안 붙는다.

    ★ **순수 숫자는 앵커가 아니다.** 숫자는 어느 문서에나 있어 변별력이 0인데 길이 가중까지 받는다.
    실사례: 키 `금산법 10%룰`의 `10`이 클레임 `S&P100`의 `100`에 걸려 0.40이 나왔다.
    """
    return [t for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", key)
            if t not in STOP and not t.isdigit()]


def is_weak_key(key):
    """앵커가 전부 짧아 단독으로는 대상을 특정하지 못하는 키인가.

    `세 부담 상한`의 앵커는 `부담`·`상한` 둘 다 2자다. `부담`은 「본인부담률」에도
    「부담만 남은 계약」에도 들어가므로, 하나만 맞은 0.50을 적중으로 보면 오적중이 된다.
    반면 `D램 낸드 차이`도 2자 앵커뿐이지만 **둘 다** 맞으면 그건 진짜 적중이다.
    → 약한 키는 버리지 말고 **부분 일치만 막는다**(hit_threshold 참조).
    """
    kt = key_tokens(key)
    return bool(kt) and max(len(t) for t in kt) <= WEAK


def hit_threshold(key):
    """이 키가 A절(캐시 대조)로 가려면 넘어야 하는 점수. 약한 키는 전부 맞아야 한다."""
    return 1.0 if is_weak_key(key) else HIT


def _tail_is_korean(frag, token):
    """역방향(클레임 조각 ⊂ 키 토큰)에서 조각을 뺀 나머지가 한글뿐인가.

    한글 꼬리(`계층`·`율`·`센터`)는 같은 말의 합성·접미라 조각이 그 말을 가리킨다고 볼 수 있지만,
    영문·숫자 꼬리(`3E`·`4`·`plus`)는 세대·모델·규격을 가르는 *다른 이름*이다 — `HBM`은 `HBM3E`가 아니다.
    """
    return re.fullmatch(r"[가-힣]*", token.replace(frag, "", 1)) is not None


def match_score(claim, key):
    """캐시 키가 이 클레임을 가리키는가 — 0.0~1.0.

    ★ 한국어는 교착어라 키의 `강제경매`가 클레임에서는 `강제경매는`으로 나온다.
    토큰 *완전일치*로 재면 조사 하나 때문에 전건이 빗나간다.
    실측(2026-09-01): 캐시 17건 × 클레임 31건에서 적중 **0건**, 12건을 손으로 다시 갈랐다.
    → 키 토큰이 클레임 안에 **부분문자열로** 있는지로 재고, 긴 토큰일수록 변별력이
      크므로 글자 수로 가중한다. 키만 보고 note는 안 본다(note는 길어서 점수를 눌러
      오히려 재현율을 떨어뜨림 — 재현율이 아쉬우면 note가 아니라 *키를 잘 쓰는* 것이 답).
    """
    kt = key_tokens(key)
    if not kt:
        return 0.0
    claim = clean_claim(claim)
    hay = norm(claim)
    ct = {s for t in re.findall(r"[가-힣A-Za-z0-9]{2,}", claim) for s in stems(t)}

    def present(t):
        if t in hay:                      # 키 ⊂ 클레임 — `강제경매` ⊂ `강제경매는`
            return True
        # 클레임 ⊂ 키 — 키가 더 긴 합성어인 경우(`차상위계층` 키 ↔ `차상위는` 클레임).
        # 짧은 조각이 긴 키에 우연히 걸리는 것을 막으려 **비율과 절대 길이를 함께** 본다.
        # ★ 비율만 보면 못 막는다: `입주`(2)는 `입주율`(3)의 0.67을 덮어 COVER를 통과하지만
        #   입주율은 입주가 아니다(실사례 2026-09-10 — 「지식산업센터」 클레임이 키 `입주율 정의`에
        #   **1.00**으로 걸렸다). 조각이 짧을수록 우연히 들어맞을 확률이 커지므로 하한을 함께 건다.
        # ★ 길이·비율을 다 통과해도 두 구멍이 남았다(실사례 2026-09-14, 둘 다 **1.00**):
        #   ① `HBM`(3) ⊂ `HBM3E`(5) — 비율 0.6이 `차상위`⊂`차상위계층`과 똑같다. 차이는 **남는
        #      꼬리**다: `계층`은 한글 접미라 같은 말의 확장이지만 `3E`는 영숫자라 *다른 낱말*이다.
        #   ② `100` ⊂ `P100` — 키 쪽 숫자는 앵커에서 뺐지만 **클레임 쪽 숫자 조각**은 그대로 역방향에
        #      쓰였다. 숫자 조각은 어느 문서에나 있으므로 역방향의 근거가 될 수 없다.
        return any(c in t and not c.isdigit()
                   and len(c) >= REV_MIN and len(c) >= COVER * len(t)
                   and _tail_is_korean(c, t) for c in ct)

    return sum(len(t) for t in kt if present(t)) / float(sum(len(t) for t in kt))


def claims_from(path):
    """번호 매긴 클레임 목록에서 본문만 뽑는다. 두 형식을 모두 받는다.

      `1. 클레임 본문`            — 한 줄형
      `1. **[맥락]**` + 다음 줄   — 두 줄형(맥락 헤더 + 본문)

    세션마다 추출 형식이 달라서 한쪽만 지원하면 조용히 0건이 된다.
    """
    out, pending = [], None
    for raw in open(path, encoding="utf-8"):
        l = raw.rstrip()
        m = re.match(r"^\s*\d+\.\s+(.*)$", l)
        if m:
            body = m.group(1).strip().lstrip("- ").strip()
            # 맥락 헤더(`**[...]**`)만 있는 줄이면 다음 줄이 본문
            if re.fullmatch(r"\*\*\[.*\]\*\*", body) or not body:
                pending = True
            else:
                out.append(re.sub(r"\s*\[(AI|추정)\]\s*$", "", body))
                pending = None
            continue
        if pending and l.strip():
            out.append(re.sub(r"\s*\[(AI|추정)\]\s*$", "", l.strip().lstrip("- ").strip()))
            pending = None
    return out


def cmd_split(path):
    """캐시는 '건너뛰기'가 아니라 '웹 조회 없이 대조'다.

    ★ 캐시 적중 = 검증 면제가 아니다. 실측 사례: `주총 특별결의 요건`이 캐시에 올바른 형태로
    있었는데 노트는 요건 하나를 빠뜨린 채 썼다(MISMATCH). 적중했다고 건너뛰면 오답이 통과한다.
    적중분은 **확보된 T1 사실을 붙여** 웹 조회 없이 문구만 대조하게 넘긴다.
    """
    cache, claims = load(), claims_from(path)
    if not claims:
        raise SystemExit("클레임을 못 읽었습니다: %s" % path)

    known, fresh, near = [], [], []
    for c in claims:
        best, score = None, 0.0
        for k in cache:
            sc = match_score(c, k)
            if sc > score:
                best, score = k, sc
        if best and score >= hit_threshold(best):
            known.append((c, best, cache[best], score))
        else:
            fresh.append(c)
            if best and score >= NEAR:
                near.append((c, best, score))

    n_vol = sum(1 for _, _, v, _ in known if v.get("volatile"))
    print("클레임 %d건 — 캐시 대조 %d(웹 조회 불요, 그중 시효 %d은 재확인) · 신규 조사 %d"
          % (len(claims), len(known), n_vol, len(fresh)))
    for c, k, v, sc in known:
        print("  · [%s %.2f] %s%s" % (k, sc, c[:44], "  ⏳시효" if v.get("volatile") else ""))

    # 매칭에 참여할 수 없는 키는 **조용히 죽는다** — 등재해 놓고 영영 안 걸리는 것을 드러낸다.
    dead = [k for k in cache if not key_tokens(k)]
    if dead:
        print("\n  ⚠ 앵커가 없어 영영 안 걸리는 키 %d건 — 이름을 고쳐 다시 등재할 것: %s"
              % (len(dead), ", ".join(sorted(dead))))

    out = re.sub(r"\.md$", "_작업.md", path)
    with open(out, "w", encoding="utf-8") as f:
        f.write("# fact-check 작업지시 — %s\n\n" % os.path.basename(path))
        f.write("총 %d건. **A는 웹 조회 없이 문구만 대조**하고, B만 새로 조달한다.\n" % len(claims))
        f.write("B는 출처 성격별로 묶어 **병렬**로 조달할 것(법령 / 정부·중앙은행 / 언론 / 기술문서).\n\n")

        f.write("## A. 캐시 대조 %d건 — 확보된 사실과 문구만 맞춰볼 것\n\n" % len(known))
        f.write("> 적중은 면제가 아니다. 클레임이 아래 사실을 **빠짐없이·과장 없이** 담았는지 본다.\n")
        f.write("> 요건이 여럿인 제도에서 하나만 쓰거나, 범위를 과하게 단정한 경우가 실제로 나왔다.\n\n")
        for i, (c, k, v, sc) in enumerate(known, 1):
            f.write("%d. **클레임**: %s\n" % (i, c))
            f.write("   - 확보된 사실(%s, %s 확인, 매칭 %.2f): %s\n"
                    % (k, v["as_of"], sc, v.get("note") or v["verdict"]))
            f.write("   - 출처: %s\n" % v["source"])
            if v.get("volatile"):
                f.write("   - ⏳ **시효 있음 — 값이 바뀌었는지 출처를 한 번 더 볼 것**\n")
            f.write("\n")

        f.write("## B. 신규 조사 %d건 — T1을 새로 찾을 것\n\n" % len(fresh))
        for i, c in enumerate(fresh, 1):
            f.write("%d. %s\n\n" % (i, c))

        if near:
            f.write("## C. 근접 후보 %d건 — 합격선(%.2f) 미만이라 B로 보냈으나 캐시에 비슷한 게 있음\n\n"
                    % (len(near), HIT))
            f.write("> 조사 전에 이 캐시 항목부터 열어볼 것. 실제로 같은 사실이면 웹 조회를 아낄 수 있고,\n")
            f.write("> 같은 사실인데 매칭이 빗나간 것이면 **캐시 키를 클레임에 쓰이는 말로 고쳐 등재**한다.\n\n")
            for i, (c, k, sc) in enumerate(near, 1):
                f.write("%d. (%.2f · [%s]) %s\n\n" % (i, sc, k, c))
    print("\n→ %s" % out)
    print("   A %d건은 웹 조회 없이 대조 · B %d건만 새로 조달(병렬 권장)" % (len(known), len(fresh)))
    if near:
        print("   ※ B 중 %d건은 근접 후보(C절) — 조사 전에 캐시부터 확인" % len(near))


def cmd_add(argv):
    if len(argv) < 3:
        raise SystemExit('usage: add "<용어>" <MATCH|MISMATCH|STALE> <URL> [--note "..."] [--volatile]')
    key, verdict, url = argv[0], argv[1], argv[2]
    note = ""
    if "--note" in argv:
        note = argv[argv.index("--note") + 1]
    cache = load()
    cache[key] = {
        "verdict": verdict, "source": url, "note": note,
        "as_of": date.today().isoformat(),
        "volatile": "--volatile" in argv,
        "tokens": sorted(sig(key + " " + note)),
    }
    save(cache)
    print("등재: %s (%s%s)" % (key, verdict, " · 시효" if cache[key]["volatile"] else ""))
    # ★ 키의 변별력을 등재 시점에 알린다 — 나중에 오적중이나 미적중으로 드러나면 이미 늦다.
    kt = key_tokens(key)
    if not kt:
        print("   ⚠ 이 키에는 앵커가 없어 **영영 매칭되지 않는다**"
              "(2자 이상 비숫자 낱말이 필요) — 이름을 고쳐 다시 등재할 것")
    elif is_weak_key(key):
        print("   ⚠ 앵커가 %s뿐이라 변별력이 약하다 — **전부 맞아야** 적중으로 친다"
              "(부분 일치는 C절로). 더 특정적인 이름이 있으면 그쪽이 낫다" % kt)


def cmd_selftest():
    """매칭 회귀 방지 — 실제로 터졌던 케이스를 그대로 박아둔다.

    2026-09-01에 토큰 완전일치라서 캐시 17건 × 클레임 31건 적중이 0이었다.
    로직이 미묘해서 눈으로는 회귀를 못 잡으므로 여기에 고정한다.
    """
    적중 = lambda k: (lambda s: s >= hit_threshold(k))       # noqa: E731
    기각 = lambda k: (lambda s: s < hit_threshold(k))        # noqa: E731
    cases = [
        # ── 재현율(놓치면 안 되는 것) ──
        ("조사 붙은 클레임(키⊂클레임)", "강제경매는 판결 같은 집행권원을 먼저 받아 신청하는 경매",
         "강제경매 집행권원", lambda s: s >= 0.99),
        ("키가 더 긴 합성어(클레임⊂키)", "차상위는 기초생활수급자 바로 위 소득 계층",
         "차상위계층", lambda s: s >= 0.99),
        ("부분 일치는 부분 점수", "가스복합화력은 배기열로 증기터빈을 또 돌리는 방식",
         "가스복합화력 램프레이트", 적중("가스복합화력 램프레이트")),
        ("약한 키도 전부 맞으면 적중", "D램은 연산을 돕고 낸드는 데이터를 쌓아두는 저장용",
         "D램 낸드 차이", 적중("D램 낸드 차이")),
        # ── 정밀도(2026-09-10 세 회차 연속 오적중을 그대로 박아둔다) ──
        ("짧은 조각 오탐 기각", "주택 가격이 올랐다", "실질주택가격 정의", 기각("실질주택가격 정의")),
        ("역방향 2자 조각 기각", "지식산업센터는 정해진 업종의 기업이 입주하도록 짓는 집합 건물",
         "입주율 정의", 기각("입주율 정의")),
        ("약한 키 부분 일치 기각", "본인부담률은 진료비 중 내가 내는 비율",
         "세 부담 상한", 기각("세 부담 상한")),
        ("숫자는 앵커가 아님", "S&P100은 S&P500에서 고른 100곳을 담는 지수",
         "금산법 10%룰", 기각("금산법 10%룰")),
        ("위치 프리픽스는 앵커가 아님", "(L109) 정제마진은 정유사의 수익성 지표",
         "금산법 10%룰", 기각("금산법 10%룰")),
        # ── 정밀도(2026-09-14 — 길이·비율을 통과하고도 1.00으로 걸린 두 경로) ──
        ("영숫자 꼬리 역방향 기각", "KV캐시가 앉는 자리가 GPU 옆의 HBM", "HBM3E", 기각("HBM3E")),
        ("클레임 숫자 조각 역방향 기각", "물적분할은 새 회사 주식을 모회사가 100% 가짐",
         "S&P100", 기각("S&P100")),
        ("한글 꼬리 역방향은 유지", "지식산업센터에 입주한 기업", "지식산업센터 공실률",
         lambda s: s >= 0.5),
        ("무관한 클레임", "오늘 날씨가 좋다", "강제경매 집행권원", lambda s: s == 0.0),
    ]
    bad = 0
    for name, claim, key, ok in cases:
        s = match_score(claim, key)
        good = ok(s)
        bad += 0 if good else 1
        print("  %s %-28s %.2f (문턱 %.2f)  [%s]"
              % ("✓" if good else "✗", name, s, hit_threshold(key), key))
    if stems("제도") != {"제도"}:
        print("  ✗ 과다절단 방지 — stems('제도')=%s" % sorted(stems("제도")))
        bad += 1
    else:
        print("  ✓ 과다절단 방지                    stems('제도')={'제도'}")
    print("\n%s" % ("통과" if not bad else "실패 %d건" % bad))
    return 1 if bad else 0


def cmd_keys():
    """키의 변별력 점검 — 오적중·미적중은 로직보다 **키 이름**에서 더 자주 온다.

    등재할 때는 멀쩡해 보이던 이름이, 캐시가 커지면 남의 클레임을 끌어당긴다.
    `split`이 알려주기 전에 여기서 먼저 훑는다.
    """
    cache = load()
    dead, weak = [], []
    for k in sorted(cache):
        kt = key_tokens(k)
        if not kt:
            dead.append(k)
        elif is_weak_key(k):
            weak.append((k, kt))
        print("%-28s %s" % (k, kt if kt else "(앵커 없음)"))
    print("\n캐시 %d건 · 앵커 없음 %d · 약한 키 %d" % (len(cache), len(dead), len(weak)))
    if dead:
        print("  ⚠ 영영 안 걸림 — 이름을 고쳐 재등재: %s" % ", ".join(dead))
    if weak:
        print("  ⚠ 전부 맞아야만 적중(부분 일치는 C절): %s"
              % ", ".join("%s%s" % (k, t) for k, t in weak))
    return 0


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    cmd = sys.argv[1]
    if cmd == "selftest":
        return sys.exit(cmd_selftest())
    if cmd == "keys":
        return sys.exit(cmd_keys())
    if cmd == "split":
        cmd_split(sys.argv[2])
    elif cmd == "add":
        cmd_add(sys.argv[2:])
    elif cmd == "list":
        for k, v in sorted(load().items()):
            print("%-28s %-11s %s %s" % (k, v["verdict"], v["as_of"],
                                         "⏳시효" if v.get("volatile") else ""))
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
