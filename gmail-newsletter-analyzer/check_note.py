#!/usr/bin/env python3
"""
세상 공부 노트 자가 검사 — 기계로 잡을 수 있는 것만, 즉시.

LLM 감사에 10분을 쓰지 않고 여기서 2초에 잡는다. 의미 판단(하위가 진짜 메커니즘인가,
용어 설명이 충분한가)은 기계로 못 하므로 여기 없다 — 그건 사람이 읽거나 감사를 켠다.

    python3 check_note.py 세상공부/세상공부_260915_노트_v1_0.md
    python3 check_note.py 세상공부/세상공부_260911_노트_v1_1.md --legacy   # 카테고리 헤더 이전 형식
    python3 check_note.py 세상공부/세상공부_260916_보충_Axios_v1_0.md --material _raw_sources/digest_input_260916_보충.md

종료코드 0=통과, 1=지적 있음.
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 운영자가 직접 지적한 어휘. 원본 노트 177항목에 한 번도 안 나온다.
BANNED = ["뒤집어 보면", "라는 얘기", "인 셈", "는 셈", "흥미로움", "관건",
          "국면", "지점", "라는 점에서", "주목할 만한", "눈여겨볼"]
# 낱말이 아니라 *용법*이 금지인 것은 정규식으로. `그림`은 "큰 그림·그림이 그려진다"류가 금지지
# 동사 '그리다'의 명사형("…선폭까지 그림.")은 아니다 — 2026-09-16에 후자가 걸려 한 번 헛돌았다.
BANNED_RE = [("그림", re.compile(r"큰 그림|그림(이|을|은|도|만|에|에서|으로|처럼|이다|이라|이었|입니다)"))]

# 사전에는 있지만 독자가 오타·전문어로 읽는 낱말 → 흔한 말. **걸린 것만** 모은 목록이라
# 목록 밖은 마무리 검사 ②(읽기 패스)가 잡는다. 실사례(2026-09-14): `사전 억지` → "억제겠지, 오타 뭐냐".
CONFUSABLE = {"억지": "억제"}
# 오독 낱말도 낱말 경계가 필요하다 — `억지로`(부사)는 정상 어휘인데 부분일치로 걸렸다(2026-09-16).
CONFUSABLE_SKIP = re.compile(r"억지로")

# 노트 배열은 이 8개 카테고리 헤더(`## `)만, 이 순서로 (SKILL.md §5 〈배열〉, 2026-09-15).
CATEGORIES = ["거시·시장", "기업·산업", "테크·AI", "정책·제도", "부동산",
              "지정학·해외", "생활·트렌드", "할 일·일정"]
# 순서가 더는 출처를 말해 주지 않으므로 상위 항목 끝 괄호에 발행분 태그가 있어야 한다.
ISSUE_TAG = re.compile(r"\((?:[^()]*·\s*)?(UPPITY|BOODING|STARTUP WEEKLY|Axios[^()]*)\)\s*$")


def strip_meta(line):
    """줄 끝 ⟨출처⟩ 주석과 [AI]/[추정] 라벨을 벗긴다."""
    s = re.sub(r"\s{2,}⟨[^⟩]+⟩\s*$", "", line).rstrip()
    return re.sub(r"\s*\[(AI|추정)\]\s*$", "", s).rstrip()


def body_lines(path):
    """헤더·메타를 버리고 본문 불릿만. '260828' 단독 줄이 본문 시작."""
    lines = [l.rstrip("\n") for l in open(path, encoding="utf-8")]
    starts = [i for i, l in enumerate(lines) if re.fullmatch(r"2[0-9]{5}", l.strip())]
    if starts:
        lines = lines[starts[-1]:]
    return [strip_meta(l) for l in lines if l.strip().startswith("- ") or l.strip().startswith("    - ")]


def check_banned(lines):
    hits = [(w, l.strip()[:60]) for l in lines for w in BANNED if w in l]
    hits += [(w, l.strip()[:60]) for l in lines for w, rx in BANNED_RE if rx.search(l)]
    return hits


def check_confusable(lines):
    """오독 낱말 — 사전에 있어도 독자가 오타로 읽는 한자어."""
    return [("%s → %s" % (w, fix), l.strip()[:60])
            for l in lines for w, fix in CONFUSABLE.items() if w in CONFUSABLE_SKIP.sub("", l)]


def check_headers(raw_lines, legacy=False):
    """카테고리 헤더 검사 — 허용된 8개만, 표 순서대로, 구 형식(헤더 0개)은 --legacy일 때만 허용.

    헤더는 body_lines()가 애초에 버리므로 커버리지·중복 검사와는 무관하다. 여기서만 본다.
    """
    heads = [l.strip()[3:].strip() for l in raw_lines if l.startswith("## ")]
    out = []
    for h in heads:
        if h not in CATEGORIES:
            out.append(("허용 외 헤더", "## " + h))
    known = [h for h in heads if h in CATEGORIES]
    if known != sorted(known, key=CATEGORIES.index):
        out.append(("헤더 순서가 표와 다름", " → ".join(known)))
    if not heads and not legacy:
        out.append(("카테고리 헤더 없음(구 형식) — 새 노트는 8개 헤더로", ""))
    return out


def check_issue_tags(raw_lines):
    """상위 항목 끝 괄호에 발행분 태그(UPPITY·BOODING·STARTUP WEEKLY·Axios …)가 있는가.

    카테고리 배열에서는 순서가 출처를 말해 주지 않으므로 태그가 그 자리를 대신한다.
    헤더 형식 노트에만 적용한다(구 형식은 순서가 출처였다).
    """
    if not any(l.startswith("## ") for l in raw_lines):
        return []
    tops = [l for l in raw_lines if l.startswith("- ")]
    return [l.strip()[:60] for l in tops if not ISSUE_TAG.search(strip_meta(l))]


def check_unopened_pdf(raw_lines):
    """헤더 '열지 못한 것'에 PDF를 적었으면 잡는다 — 이제 PDF는 열리기 때문이다.

    왜 기계로 잡나: PDF 링크를 내장 브라우저로 `navigate`하면 렌더가 아니라 **다운로드 대화상자**가
    떠서 사용자 화면을 가로챘고, 그래 놓고 결과는 '못 연 것'으로 적혀 소득이 없었다
    (260918·260923·260924 세 회차 반복 · 사용자 지적 2026-09-25). 원인은 `fetch_article.py`에
    PDF 분기가 없어 사다리가 브라우저까지 내려간 것이었고, 지금은 스크립트가 pypdf로 바로 뽑는다.
    → **PDF를 '못 연 것'으로 적을 이유는 스캔 이미지 PDF 하나뿐**이다. 그 밖의 PDF가 그 줄에
    올라오면 스크립트를 안 돌려 본 것이므로 여기서 막는다.
    """
    hits = []
    for i, l in enumerate(raw_lines, 1):
        if "못 연 것" not in l and "열지 못한 것" not in l:
            continue
        tail = l.split("못 연 것", 1)[-1].split("열지 못한 것", 1)[-1]
        # 스캔 이미지 PDF는 정당한 사유 — 그 단서가 있으면 넘어간다
        if "스캔" in tail:
            continue
        for pat, why in ((r"PDF", "PDF"), (r"다운로드 대화상자", "다운로드 대화상자"),
                         (r"내려받기", "내려받기")):
            if re.search(pat, tail):
                hits.append((why, i, tail.strip()[:90]))
                break
    return hits


def check_render(lines):
    """노트는 **마크다운 뷰어에서 읽힌다** — 본문 문자가 서식 기호로 먹히는 것을 잡는다.

    파일로만 보면 멀쩡한데 앱에서 글자가 사라지거나 취소선이 그어진다. 눈으로는
    '앱이 이상한' 것으로 보여 원인을 못 찾으므로 여기서 기계로 잡는다.
    *실사례(2026-09-10): `10~20년·20~30년`이 앱에서 **`1020년·2030년`(가운데 취소선)** 으로
    렌더됐다. 뷰어가 물결 두 개를 짝지어 취소선 문법으로 읽은 것이다.*

    반각 물결(`~`)과 밑줄(`_`)이 **한 줄에 두 개 이상**이면 짝이 지어진다. 백틱 안은 코드라
    안전하므로 제외한다.
    """
    out = []
    for l in lines:
        bare = re.sub(r"`[^`]*`", "", l)          # 백틱(코드) 구간은 서식이 안 먹는다
        if bare.count("~") >= 2:
            out.append(("물결 → 전각 `～`로", l.strip()[:70]))
        if bare.count("_") >= 2:
            out.append(("밑줄 → 백틱으로 감쌀 것", l.strip()[:70]))
    return out


# 문자 종류가 바뀌는 자리에서 토큰을 끊는다 — 숫자를 한글에서 떼어내는 것이 핵심이다.
# 붙여두면 `760달러`가 한 토큰이 되어 영어 원문의 `$760`과 영영 만나지 못하고,
# 그 결과 **영문 발행분(Axios 등)에 귀속되는 항목이 0으로 잡힌다**(2026-09-08 실제 발생).
# 숫자는 언어를 타지 않으므로 한국어 노트와 영어 원문을 잇는 유일한 다리다.
TOKEN_RE = re.compile(r"\d[\d,.]*\d|\d|[가-힣]+|[A-Za-z]+")

# 한국어 조사를 떼지 않으면 `달러`와 `달러를`이 다른 토큰이 되어 같은 말이 안 겹친다.
# (`cache_claims.py`가 `강제경매` vs `강제경매는`으로 캐시 17건을 통째로 놓쳤던 것과 같은 버그다.)
# 어간이 2자 이상 남을 때만 뗀다 — 짧은 낱말이 뭉개지지 않게.
JOSA = ("으로서", "으로써", "이라고", "에서는", "에게는", "으로", "라고", "에서", "에게",
        "까지", "부터", "보다", "처럼", "만큼", "이나", "이란", "이는", "이가", "은", "는",
        "이", "가", "을", "를", "에", "의", "로", "와", "과", "도", "만", "씩", "째")


# 숫자는 언어를 안 타므로 한국어 노트 ↔ 영어 원문을 잇는 **유일한** 다리다.
# 낱말 하나가 겹치는 것보다 수치 하나가 겹치는 쪽이 같은 사건일 확률이 훨씬 높으므로
# 가중치를 더 준다. 이 값이 없으면 영문 발행분이 한국어 발행분과 동점으로 밀려 0항목이 된다.
NUMERIC_WEIGHT = 2.0

# 구독 환영·수신설정 안내 메일은 뉴스가 0줄이라 항목이 나올 수 없다. 그런 통을 위반으로
# 세면 검사기가 매번 헛울음을 울고, 결국 사람이 지적을 통째로 무시하게 된다.
# 그래서 **본문이 이 길이 미만인 통은 위반이 아니라 참고로 내린다**(실측: 환영 메일 992~1655자,
# 실제 발행분 4304~8817자). 숨기지는 않는다 — 진짜 놓친 것이면 사람이 보고 판단해야 하므로.
THIN_ISSUE_CHARS = 2500


def tokens(text):
    """비교용 토큰. 숫자는 쉼표를 지우고(`1,000`=`1000`), 한글은 조사를 뗀다."""
    out = []
    for t in TOKEN_RE.findall(text):
        if t[0].isdigit():
            t = t.replace(",", "").rstrip(".")
        elif "가" <= t[0] <= "힣":
            for j in JOSA:
                if t.endswith(j) and len(t) - len(j) >= 2:
                    t = t[: -len(j)]
                    break
        out.append(t)
    return out


def check_coverage(lines, material):
    """재료의 발행분마다 최소 1항목이 귀속되는가. 0인 통 = 재료를 안 읽은 것.

    귀속은 *희귀 토큰 겹침*으로 판정한다(발행분 2통 이하에만 나오는 토큰에 가중치).
    한국어 노트 ↔ 영어 원문은 낱말이 겹치지 않으므로 **숫자와 로마자 고유명사**가 다리 역할을 한다.
    """
    if not os.path.isfile(material):
        return None, "재료 파일 없음: %s" % material
    txt = open(material, encoding="utf-8").read()
    secs = []
    for p in re.split(r"^## ", txt, flags=re.M)[1:]:
        head = p.split("\n")[0]
        m = re.match(r"\[(.+?)\] (.+?) — (\d{4}/\d{2}/\d{2})", head)
        if m:
            # 발신자 이름을 자르지 말 것 — 같은 브랜드의 여러 뉴스레터(Axios Macro·Closer·PM …)가
            # 한 라벨로 뭉치면 한 통이 0항목이어도 다른 통의 항목에 가려 보이지 않는다.
            label = "%s %s%s" % (m.group(3)[5:], m.group(2).strip(),
                                 "👛" if "👛" in p.split("\n")[1] else "")
            body = p.split("### 이 발행분의 링크")[0]
            secs.append((label, p, len(body)))

    df = {}
    seclist = []
    for label, b, _n in secs:
        toks = tokens(b)
        seclist.append((label, toks))
        for t in set(toks):
            df[t] = df.get(t, 0) + 1

    hit = {label: 0 for label, _, _n in secs}
    for l in lines:
        anchors = [t for t in tokens(l) if df.get(t, 0) and df[t] <= 2 and len(t) >= 3]
        if not anchors:
            continue
        best, score = None, 0.0
        for label, toks in seclist:
            s = sum((NUMERIC_WEIGHT if t[0].isdigit() else 1.0) / df[t]
                    for t in anchors if t in toks)
            if s > score:
                best, score = label, s
        if best:
            hit[best] += 1
    size = {label: n for label, _, n in secs}
    return [(k, v, size[k]) for k, v in hit.items()], None


def check_dupes(lines, notes_dir, current):
    """최근 노트에 이미 쓴 사건을 또 쓰지 않았는가 (전이규칙 R2)."""
    # 노트만 본다 — 추가리서치 같은 파생 문서는 '직전 노트'가 아니다
    note_re = re.compile(r"^세상공부_\d{6}(_노트)?_v\d+_\d+\.md$")
    prev = sorted(p for p in
                  glob.glob(os.path.join(notes_dir, "세상공부_*.md")) +
                  glob.glob(os.path.join(notes_dir, "이전버전", "세상공부_*.md"))
                  if note_re.match(os.path.basename(p)))
    prev = [p for p in prev if os.path.abspath(p) != os.path.abspath(current)]
    # 같은 날짜의 다른 버전은 중복이 아니라 개정이므로 제외.
    # 날짜로 거른다 — 파일명 규칙이 바뀌어도(`_노트_` 도입 등) 옛 버전을 놓치지 않는다.
    m_cur = re.match(r"^세상공부_(\d{6})", os.path.basename(current))
    if m_cur:
        same_day = "세상공부_" + m_cur.group(1)
        prev = [p for p in prev if not os.path.basename(p).startswith(same_day)]
    if not prev:
        return []
    seen = set()
    for p in prev[-3:]:
        for l in body_lines(p):
            key = "".join(re.findall(r"[가-힣A-Za-z0-9]{2,}", l))[:40]
            if key:
                seen.add(key)
    out = []
    for l in lines:
        key = "".join(re.findall(r"[가-힣A-Za-z0-9]{2,}", l))[:40]
        if key and key in seen:
            out.append(l.strip()[:60])
    return out


def check_self_dupes(lines):
    """같은 노트 안에서 같은 사건을 두 번 적지 않았는가."""
    seen, out = {}, []
    for l in lines:
        key = "".join(re.findall(r"[가-힣]{2,}", l))[:26]
        if len(key) < 12:
            continue
        if key in seen:
            out.append((seen[key][:52], l.strip()[:52]))
        else:
            seen[key] = l.strip()
    return out


def selftest():
    """토큰화가 언어 경계에서 실제로 끊기는지 확인 — 고치다 깨지면 여기서 잡힌다."""
    cases = [
        ("숫자를 한글에서 분리", "760달러를 넘었고", "760"),
        ("숫자 소수점 유지", "갤런당 4.15달러", "4.15"),
        ("숫자 쉼표 제거", "1,688건", "1688"),
        ("영어 원문의 같은 수치", "cost the average U.S. household more than $760", "760"),
        ("한글 조사 제거", "달러를", "달러"),
        ("짧은 낱말은 안 뭉갬", "미국", "미국"),
    ]
    ok = True
    for name, text, want in cases:
        got = tokens(text)
        hit = want in got
        ok &= hit
        print("  %s %-22s %-34s → %s" % ("✓" if hit else "✗", name, text[:32], got[:6]))
    ko = tokens("760달러를 넘었고")
    en = tokens("more than $760")
    bridge = set(ko) & set(en)
    print("  %s 한국어↔영어 공통 토큰 %s" % ("✓" if bridge else "✗", sorted(bridge)))
    ok &= bool(bridge)

    # 서식 사고 회귀 — 실제로 앱에서 깨졌던 줄을 그대로 박아둔다(2026-09-10)
    rend = [
        ("반각 물결 2개 → 적발", "- 10~20년·20~30년 구간 대상", True),
        ("전각 물결은 통과", "- 10～20년·20～30년 구간 대상", False),
        ("물결 1개는 통과", "- 올영세일 개최 (~9/7)", False),
        ("맨 밑줄 2개 → 적발", "> **v4_0 → v5_0 (주요)** — 재작성", True),
        ("백틱 안 밑줄은 통과", "> 상세는 `_raw_sources/AI클레임_검증리포트_260901.md`", False),
    ]
    for name, line, want in rend:
        got = bool(check_render([line]))
        hit = got == want
        ok &= hit
        print("  %s %-22s %-34s → %s" % ("✓" if hit else "✗", name, line[:32],
                                         "적발" if got else "통과"))
    # 금지어 `그림` — 용법 구분(2026-09-16). 동사 명사형은 통과, 관용구는 적발.
    for name, line, want in [("그림(동사 명사형) 통과", "- 한 번에 13.5nm 선폭까지 그림.", False),
                             ("큰 그림 적발", "- 큰 그림을 보면 같은 방향", True),
                             ("그림이 적발", "- 규제가 만든 그림이 사라짐", True)]:
        got = bool(check_banned([line]))
        hit = got == want
        ok &= hit
        print("  %s %-22s %-34s → %s" % ("✓" if hit else "✗", name, line[:32],
                                         "적발" if got else "통과"))
    # 오독 낱말·카테고리 헤더·발행분 태그 — 2026-09-15 피드백에서 온 것
    conf_hit = bool(check_confusable(["- 사후 보상이 아니라 사전 억지"]))
    conf_ok = (not check_confusable(["- 사후 보상이 아니라 사전 억제"])
               and not check_confusable(["- 토론을 억지로 끝내려면"]))   # 부사 `억지로`는 통과
    hd_bad = any(w == "허용 외 헤더" for w, _ in check_headers(["## 경제", "- x"]))
    hd_ord = any("순서" in w for w, _ in check_headers(["## 부동산", "## 거시·시장", "- x"]))
    hd_ok = not check_headers(["## 거시·시장", "- x", "## 부동산", "- y"])
    hd_legacy = not check_headers(["- x"], legacy=True) and bool(check_headers(["- x"]))
    tag_miss = check_issue_tags(["## 거시·시장", "- 금리 오름 (9/11 현지)", "- 유가 급등 (9/10 · UPPITY)"])
    tag_ok = tag_miss == ["- 금리 오름 (9/11 현지)"]
    for name, hit in [("오독 낱말 적발(억지)", conf_hit), ("오독 낱말 수정본 통과", conf_ok),
                      ("허용 외 헤더 적발", hd_bad), ("헤더 순서 위반 적발", hd_ord),
                      ("헤더 규칙대로면 통과", hd_ok), ("구 형식은 --legacy만 통과", hd_legacy),
                      ("발행분 태그 누락만 경고", tag_ok)]:
        ok &= hit
        print("  %s %s" % ("✓" if hit else "✗", name))
    print("\n통과" if ok else "\n실패")
    return 0 if ok else 1


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    legacy = "--legacy" in sys.argv
    argv = sys.argv[1:]
    # --material <path>: 재료 파일을 직접 지정한다. 보충 노트처럼 그날의 본 재료가 아닌
    # 파일(`digest_input_YYMMDD_<slug>.md`)로 쓴 노트는 날짜 추정으로는 커버리지를 못 잰다.
    material = None
    if "--material" in argv:
        i = argv.index("--material")
        material = argv[i + 1]
        del argv[i:i + 2]
    path = [a for a in argv if not a.startswith("--")][0]
    lines = body_lines(path)
    raw = [l.rstrip("\n") for l in open(path, encoding="utf-8")]
    notes_dir = os.path.dirname(os.path.abspath(path))
    if material is None:
        stamp = re.search(r"(2[0-9]{5})", os.path.basename(path))
        material = os.path.join(HERE, "_raw_sources",
                                "digest_input_%s.md" % (stamp.group(1) if stamp else ""))

    problems = 0
    print("노트 %d항목 · %s\n" % (len(lines), os.path.basename(path)))

    hits = check_banned(lines)
    if hits:
        problems += 1
        print("✗ 금지 표현 %d건" % len(hits))
        for w, l in hits[:6]:
            print("    [%s] %s" % (w, l))
    else:
        print("✓ 금지 표현 0건")

    conf = check_confusable(lines)
    if conf:
        problems += 1
        print("✗ 오독 낱말 %d건 — 사전에 있어도 독자는 오타로 읽는다" % len(conf))
        for w, l in conf[:6]:
            print("    [%s] %s" % (w, l))
    else:
        print("✓ 오독 낱말 0건")

    heads = check_headers(raw, legacy=legacy)
    if heads:
        problems += 1
        print("✗ 카테고리 헤더 %d건" % len(heads))
        for w, l in heads[:6]:
            print("    [%s] %s" % (w, l))
    else:
        print("✓ 카테고리 헤더 규칙대로" if not legacy else "· 구 형식 노트(--legacy) — 헤더 검사 생략")

    tags = check_issue_tags(raw)
    if tags:
        print("· 발행분 태그 없는 상위 항목 %d건 — 끝 괄호에 UPPITY/BOODING/STARTUP WEEKLY/Axios 표기" % len(tags))
        for l in tags[:6]:
            print("    %s" % l)

    # 서식 사고는 헤더에서도 난다(`v4_0 → v5_0`이 이탤릭으로 먹힘) → 본문 불릿이 아니라 전문을 본다.
    rend = check_render(raw)
    if rend:
        problems += 1
        print("✗ 앱에서 서식으로 먹힐 문자 %d건" % len(rend))
        for w, l in rend[:6]:
            print("    [%s] %s" % (w, l))
    else:
        print("✓ 서식으로 먹힐 문자 0건")

    # PDF를 '못 연 것'으로 적었으면 스크립트를 안 돌린 것이다(브라우저는 다운로드 대화상자만 띄운다).
    updf = check_unopened_pdf(raw)
    if updf:
        problems += 1
        print("✗ '못 연 것'에 PDF %d건 — `python3 fetch_article.py <url>`로 다시 열 것" % len(updf))
        print("    (브라우저로 PDF를 열면 사용자 화면에 다운로드 대화상자만 뜬다. 스캔 이미지 PDF면 그렇게 적을 것)")
        for w, i, l in updf[:4]:
            print("    [%s · %d행] %s" % (w, i, l))
    else:
        print("✓ '못 연 것'에 PDF 없음")

    cov, err = check_coverage(lines, material)
    if err:
        print("· 커버리지 확인 불가 — %s" % err)
    else:
        zero = [(k, n) for k, v, n in cov if v == 0]
        thin = [(k, n) for k, n in zero if n < THIN_ISSUE_CHARS]
        real = [(k, n) for k, n in zero if n >= THIN_ISSUE_CHARS]
        if real:
            problems += 1
            print("✗ 0항목 발행분 %d통 — 재료를 끝까지 안 읽었을 가능성" % len(real))
            for k, n in real:
                print("    %s (본문 %d자)" % (k, n))
        if thin:
            print("· 0항목이지만 본문이 짧은 통 %d통 — 환영·안내 메일일 수 있음(위반 아님, 눈으로 확인)"
                  % len(thin))
            for k, n in thin:
                print("    %s (본문 %d자)" % (k, n))
        if not real and not thin:
            print("✓ 발행분 %d통 전부 1항목 이상" % len(cov))

    sd = check_self_dupes(lines)
    if sd:
        problems += 1
        print("✗ 노트 내 중복 %d쌍" % len(sd))
        for a, b in sd[:4]:
            print("    %s\n    %s" % (a, b))
    else:
        print("✓ 노트 내 중복 없음")

    pd = check_dupes(lines, notes_dir, path)
    if pd:
        problems += 1
        print("✗ 최근 노트와 중복 %d건 (전이규칙 R2)" % len(pd))
        for l in pd[:4]:
            print("    %s" % l)
    else:
        print("✓ 최근 노트와 중복 없음")

    print("\n%s" % ("지적 %d종 — 고치고 다시 돌릴 것" % problems if problems
                    else "통과. 의미 판단(메커니즘·용어 설명)은 사람이 읽거나 감사를 켤 것"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
