#!/usr/bin/env python3
"""
세상 공부 노트 자가 검사 — 기계로 잡을 수 있는 것만, 즉시.

LLM 감사에 10분을 쓰지 않고 여기서 2초에 잡는다. 의미 판단(하위가 진짜 메커니즘인가,
용어 설명이 충분한가)은 기계로 못 하므로 여기 없다 — 그건 사람이 읽거나 감사를 켠다.
문체(서술형 종결·형식 명사 종결·비유·예고 소제목)는 모양으로 잡히므로 여기서 잡는다 — SKILL §5.

    python3 check_note.py 세상공부/세상공부_260915_노트_v1_0.md
    python3 check_note.py 세상공부/세상공부_260911_노트_v1_1.md --legacy   # 카테고리 헤더 이전 형식
    python3 check_note.py 세상공부/세상공부_260916_보충_Axios_v1_0.md --material _raw_sources/digest_input_260916_보충.md
    python3 check_note.py --prior 프랑스 국채 "토큰화 주식"     # 최근 14일 노트에서 용어별 등장 위치(중복·후속 확인)

종료코드 0=통과, 1=지적 있음. `--prior`는 찾기만 하므로 항상 0이다 — grep 반복문을 대신한다
(grep은 0건이면 1을 돌려줘 실패처럼 보인다 · 2026-10-06).
"""
import glob
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))

# 운영자가 직접 지적한 어휘. 원본 노트 177항목에 한 번도 안 나온다.
BANNED = ["뒤집어 보면", "라는 얘기", "인 셈", "는 셈", "흥미로움", "관건",
          "국면", "지점", "라는 점에서", "주목할 만한", "눈여겨볼",
          "에 다름 아니다"]   # SKILL §5-2 목록에 있었는데 코드에 빠져 있던 것(2026-10-14 · selftest가 대조한다)
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

# ── §5 문체 (2026-09-28 · 지피티 검토 + 원본 노트 대조) ──────────────────────────────
# 서술형 종결 — 동사·형용사 명사형(-ㅁ/-음)과 `~다`로 끝난 불릿. 원본 노트는 2%였고 목표는 0.
# 명사형 종결로 압축하라는 규칙이 "어미만 떼기"로 실행돼 노트 절반 가까이가 `있음·올라감·봄`으로 끝났다.
# 받침 ㅁ 전체를 잡으면 자금·제품·위험·부담·리튬이 걸리므로 **동사 명사형의 모양만** 나열한다.
VERB_END = re.compile(
    r"(?:음|함|됨|임|듦|뜀|아님"
    r"|[가-힣](?:짐|줌|봄|냄|씀|큼|둠|림|힘)"
    r"|[라러려아어여나겨져쳐켜펴]감|[아어나려와겨져]옴|[아어나타러]남"
    r"|오름|따름|다름|모름|누름|치름|어려움|쉬움|[가-힣]로움|[가-힣]거움"
    r"|생김|남김|넘김|옮김|맡김|숨김"
    r"|(?:^|\s)(?:감|옴|봄|줌|씀|둠|짐|뜸)"
    r"|(?:한|했|된|됐|이|였|있|없|았|었|진|온|간|준|본|는|낸|친)다)$")
# 위 모양이지만 명사인 낱말 — 줄 끝이 이것이면 통과. **걸린 것만** 보탠다(2026-09-28 노트 6개 실측).
VERB_END_OK = ("처음", "다음", "마음", "믿음", "걸음", "죽음", "얼음", "웃음", "울음", "물음", "묶음",
               "모음", "소음", "잡음", "발음", "녹음", "게임", "프레임", "타임", "프라임", "움직임",
               "모임", "책임", "위임", "일임", "선임", "연임", "사임", "취임", "해임", "퇴임", "부임",
               "재임", "신임", "운임", "포함", "결함", "군함", "조짐", "만큼", "말씀", "알림", "살림", "쏠림", "크림",
               "스트림", "어둠", "올봄", "지난봄", "늦봄", "한 줌", "올 봄", "이번 봄", "지난 봄", "내년 봄")
# 어미만 떼면 붙는 형식 명사 — `~하는 것·상황·구조`도 서술형이다. 앞 낱말이 관형형(받침 ㄴ·ㄹ)일
# 때만 잡는다(`검토 중`·`지배 구조`는 명사+명사라 통과). `중`은 진행형 `~하는 중`만.
FORMAL_END = re.compile(r"([가-힣]+) (것|상황|구조|모습|상태|대목|중)$")
FORMAL_NOUN_OK = ("자본", "지분", "기반", "소유권", "지배권", "권한", "전반")
# 관형형 어미의 받침 — 한글 음절 (코드 − 0xAC00) % 28 값. ㄴ(만든·오르는)=4, ㄹ(오를·될)=8.
JONG_NIEUN, JONG_RIEUL = 4, 8
QUOTE_END = ('"', "”", "’", "」")
# 줄 끝 라벨·출처 괄호·강조·문장부호를 벗긴다. 라벨은 백틱으로 감싼 형태(`[AI]`)가 실제 노트의 주류다.
TAIL_STRIP = [re.compile(r"\s*`?\[(AI|추정|기사)\]`?\s*$"), re.compile(r"\s*\([^()]*\)\s*$"),
              re.compile(r"[\s*.·…\"'”’」)]+$")]

# 비유·예고 소제목·스킬 내부 용어 — 헤더 문단까지 전 줄을 본다(백틱 안은 제외).
# 낱말 치환(`자리`→`지점`)으로 피하지 말고 대상·변화를 직접 쓸 것(SKILL §5-2·5-3).
TEASER = ["무슨 상관인가", "왜 중요한가", "무슨 의미인가", "오늘의 핵심", "결론이 무섭", "핵심 타격점",
          "새 질문이 열", "진짜 이유는", "배울 자리", "흥미로운", "얄궂은", "곤란한 대목", "요점을 다 보여",
          # 2026-10-14 승격(ai_phrases): 스킬 §3의 점검 질문이 노트 소제목으로 샜다(`**누구에게 유리·불리한가**` — 261013).
          # 질문은 작성자가 자기에게 묻는 것이고 노트에는 그 답을 쓴다(SKILL §5-3).
          "누구에게 유리", "누구에게 불리", "누가 돈을 벌", "누구 편이고", "왜 그런 규칙", "이 뉴스와 무슨"]
# 항목 카드(SKILL §2-bis)의 칸 이름을 노트 소제목으로 옮긴 줄 — 카드는 작성자의 메모다(2026-10-14).
CARD_HEAD = re.compile(r"^\s*- \*\*(사건일|출처|링크|정체|용어|첫 인과|연결|거래·기술|라벨)\*\*")
STYLE_RE = [
    ("단순한 X가 아님", re.compile(r"단순한 [^—\n]{1,24}(아님|아니다|아닌)")),
    ("자리(비유)", re.compile(r"[가-힣]+(의|는|은|던|인|된|될|할|진|한|갈|볼|낼) 자리(?!다툼|매김|잡)")),
    ("줄기", re.compile(r"줄기(?!도 |세포|차)")),
    ("마디", re.compile(r"(째|번째) 마디")),
    ("계산서(비유)", re.compile(r"(?<![가-힣])계산서")),
    ("판(비유)", re.compile(r"판(을|이) (뺏|바꾸|바뀌|달라|흔들|뒤집|깔|키우|아님|아니)")),
    ("새로 붙은 것 → '9/18 이후 추가된 내용'", re.compile(r"에서 새로 붙은 것|새로 붙었")),
    ("스킬 내부 용어", re.compile(r"선별 바|리트머스|(?<![가-힣])문 [123](?![0-9])")),
    ("생각 거품 → 'Axios 기자의 해석'", re.compile(r"생각 거품")),
    # 2026-10-08 승격(ai_phrases — 노트 감사 3 run 반복): 가격을 '값'으로 뭉갠 줄과 새 비유·예고형 소제목
    ("'값'(가격) → 가격·원가·비용", re.compile(r"(원자재|부품|칩|원가|재료|생닭|메모리) 값|값을 (내려|올려|내리|올리)")),
    ("비유(흔적·무게·겹침·닿다)", re.compile(r"흔적 (셋|둘|넷|하나)|남긴 흔적|[가-힣]+의 무게|겹침|(?<![가-힣])닿(는|은|아|게|기|을)")),
    ("예고형 소제목(~의 핵심)", re.compile(r"[가-힣]+의 핵심(\*\*)? —")),
]

# 접착어 — 두 사실을 잇는다고만 하고 사이 단계를 생략한 표지(SKILL §3 ⓑ). 단계를 풀어 쓰면 낱말이 사라진다.
# 2026-10-08 승격(connective_glue — 노트 감사 3 run 반복).
GLUE = ["맞물림", "맞물려", "같은 뿌리", "직결", "같은 방향", "같은 경로", "같은 흐름", "와 이어짐", "과 이어짐",
        # 2026-10-14 승격(connective_glue): 두 줄의 원인이 실제로 다른데 '같은 이유로'로 묶었다(261013 — 정책대출 6억 ↔ 집값 장기 상승).
        "같은 이유로"]

# 메커니즘 표지 — 하위 줄 가운데 하나라도 이것을 담으면 '왜'에 답한 블록으로 본다(후보용 휴리스틱).
# 2026-10-14 엄격화: 예전 목록은 `=`(정의)·`의미`·`면서`·`따라`·`영향`까지 인과로 쳐서 후보가 늘 0개였다 — 감사가 사실 나열
# 블록 6개를 잡은 261013 노트에서도 0이었다. 정의(`=`)는 용어 설명이지 '왜'가 아니다.
CAUSAL = re.compile(r"이유|때문|→|⇒|탓|덕분|위해|려는|려고|원인|배경|목적|취지|그래서|따라서|그 결과|로 인해|여파로"
                    r"|막으려|하려|차단|방지|\[추정\]")
# 숫자 두 개 이상을 담고 인과 표지가 없는 하위 줄 = 사실 줄. 이런 줄이 하위의 절반 이상이면 다른 줄에 인과가 있어도 후보다.
FACT_NUM = re.compile(r"\d[\d,.]*")
FOLLOWUP_LINE = re.compile(r"이후 추가된 내용")
# 용어 ② 후보 — 법·제도·지표 이름이 처음 나왔는데 그 줄과 직속 하위 줄에 '왜 있는 규칙인가'의 표지가 없다(2026-10-14).
TERM_RE = re.compile(r"[가-힣A-Za-z]+(?:법|조례|규칙|규정|지수|경쟁률|가액|기준일|확약|등급|요건|특례|협정|잔고|전환율)(?![가-힣])"
                     r"|(?:법|규칙|조례|령)\s*제?\d+조(?![\d억원])")
TERM_SKIP = {"방법", "불법", "문법", "기법", "해법", "화법", "수법", "어법", "편법", "위법", "적법", "합법", "용법",
             "사법", "입법", "공법", "필법", "작법", "타법"}
REASON = re.compile(r"이유|위해|막으려|취지|목적|방지|차단|때문|하도록|→|⇒|\[추정\]")
# 라벨 후보 — 의도·인과를 단정하는 낱말인데 `[추정]`이 없다(원문에 그 인과가 있으면 출처를, 없으면 `[추정]`을).
LABEL_HINT = re.compile(r"의도|노림|대가로|겨냥|포석|속내")
# 정체 대신 형용사 — SKILL §3 ⓐ(`널리 참조하는`·`대표적인`·`유명한`·`업계 1위`는 설명이 아니다).
ADJ_ID = re.compile(r"널리 참조하는|널리 쓰이는|대표적인|유명한|업계 1위")
# 화살표만 있는 인과 후보 — 처음 설명하는 인과를 `A → B → C`만으로 적은 줄(SKILL §5-1·5-2 · 2026-10-17, 10/13 압축복습 피드백).
# 숫자로 이어지는 화살표(`17bp → 약 20bp`·`1분기 → 3분기`)는 수치 변화라 세지 않고, 우리말 연결어가 하나라도 있으면 문장으로
# 이은 것으로 본다. `⇒`는 항목 간 참조 표시(§2-bis·§6)라 세지 않는다. 의미 판단은 못 하므로 후보일 뿐이다 — 앞에서 이미 설명한
# 관계를 정리한 줄(압축복습 '연결 지도')은 해당 없음.
ARROW_CAUSAL = re.compile(r"→(?!\s*(?:약\s*)?[\d$])")
ARROW_MIN = 3
CONNECTIVE = re.compile(r"때문|면서|따라|인데|는데|은데|라서|어서|아서|므로|니까|수록|려면|거나|지만|더라도|경우"
                        r"|[가-힣]면(?=[\s,.)]|$)|[가-힣](?:려|며)(?=[\s,]|$)|[가-힣] 때(?![가-힣])")
# 검증 리포트 교정이 노트에 옮겨졌는가 · 사전 감사 파일이 있는가(2026-10-14 — SKILL §4-bis·§7)
PREAUDIT_SINCE = "261014"
FRAG_MIN = 8
INDENT_STEP = 4        # 노트의 하위 불릿 들여쓰기(공백 4칸) — 직속 하위 줄을 가를 때 쓴다
PDF_NEAR_CHARS = 80    # PDF 언급의 앞뒤 몇 글자 안에서 면제 사유(스캔·사이트 차단)를 찾는가

# Gmail 필터 생성 권유 — 운영자가 그만 쓰라고 했다(2026-10-02: "필터 만들라는 말 그만"). 스킬 규칙만으로는
# 안 멈춘다: 헤더를 직전 노트에서 본떠 쓰면 `(… 필터가 아직 없다면 생성 필요)`가 매일 딸려 왔다(260922～261002).
FILTER_NUDGE = re.compile(r"스팸함으로 보내지 않음|필터[^\n]{0,20}(생성|만들)")


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


def clean_tail(line):
    """줄 끝의 라벨·출처 괄호·강조를 **바뀌지 않을 때까지** 벗긴다(순서가 섞여 있다)."""
    s, prev = line.rstrip(), None
    while prev != s:
        prev = s
        for rx in TAIL_STRIP:
            s = rx.sub("", s)
    return s


def _jong(ch):
    """한글 음절의 받침 번호(ㄴ=4, ㄹ=8, ㅁ=16). 한글이 아니면 -1."""
    return (ord(ch) - 0xAC00) % 28 if "가" <= ch <= "힣" else -1


def check_verb_endings(lines):
    """서술형 종결(`있음·올라감·~한다`)과 형식 명사 종결(`~하는 것·중·상황`)을 잡는다 — SKILL §5-1.

    명사형 종결 규칙이 "어미만 떼기"로 실행되면 둘 다 나온다. 고치는 법은 종결 치환이 아니라
    **변화·판단을 담은 명사로 문장을 다시 짜는 것**(`금리가 올라감` → `금리 상승`).
    줄 끝이 한글이 아니면(수치·영문·물음표) 보지 않는다.
    """
    out = []
    for l in lines:
        # 인용으로 끝나는 줄(`*"…아는 건 아님"*`)은 발언자의 말이라 종결 규칙 대상이 아니다.
        q = re.sub(r"[\s*.]+$", "", TAIL_STRIP[1].sub("", TAIL_STRIP[0].sub("", l.rstrip())))
        if q.endswith(QUOTE_END):
            continue
        s = clean_tail(l)
        if not s or not ("가" <= s[-1] <= "힣"):
            continue
        if any(s.endswith(w) for w in VERB_END_OK):
            continue
        if VERB_END.search(s):
            out.append(("서술형", l.strip()[:70]))
            continue
        m = FORMAL_END.search(s)
        if (m and m.group(1) not in FORMAL_NOUN_OK and _jong(m.group(1)[-1]) in (JONG_NIEUN, JONG_RIEUL)
                and (m.group(2) != "중" or m.group(1).endswith("는"))):
            out.append(("형식 명사", l.strip()[:70]))
    return out


def check_style_words(raw_lines):
    """비유·예고 소제목·스킬 내부 용어 — 헤더 문단까지 전 줄(코드펜스·`#` 헤더·백틱 안 제외)."""
    out, fence = [], False
    for i, raw in enumerate(raw_lines, 1):
        if raw.strip().startswith("```"):
            fence = not fence
            continue
        if fence or raw.startswith("#"):
            continue
        l = re.sub(r"`[^`]*`", "", raw)
        for t in TEASER:
            if t in l:
                out.append((t, i, raw.strip()[:70]))
        for lab, rx in STYLE_RE:
            if rx.search(l):
                out.append((lab, i, raw.strip()[:70]))
        if CARD_HEAD.search(l):                  # 본문 불릿에만 — 헤더 문단의 `> **링크**`는 노트 머리 라벨이다
            out.append(("항목 카드 칸 이름 소제목", i, raw.strip()[:70]))
    return out


def check_glue(raw_lines):
    """접착어 — 헤더 문단까지 전 줄(코드펜스·`#` 헤더·백틱 안 제외). 사이 단계를 풀어 쓸 것."""
    out, fence = [], False
    for i, raw in enumerate(raw_lines, 1):
        if raw.strip().startswith("```"):
            fence = not fence
            continue
        if fence or raw.startswith("#"):
            continue
        l = re.sub(r"`[^`]*`", "", raw)
        for w in GLUE:
            if w in l:
                out.append((w, i, raw.strip()[:70]))
    return out


def check_mechanism_candidates(raw_lines):
    """하위 줄이 2개 이상인데 그중 어느 것도 '왜'의 표지(CAUSAL)를 담지 않은 블록 — 사실 나열 후보.

    의미 판단은 기계가 못 하므로 **지적이 아니라 후보 목록**이다(problems에 안 센다). 읽기 패스에서
    하나씩 열어 원인·의미 줄을 보태거나, 사실 줄을 줄여 깊이 0으로 정리한다(게이트가 처리 여부를 묻는다).
    2026-10-08 승격(mechanism_not_fact — 노트 감사 3 run 반복).
    """
    items = []   # (행 번호, 들여쓰기, 줄)
    for i, raw in enumerate(raw_lines, 1):
        m = re.match(r"^( *)- ", raw)
        if m and not raw.startswith(">"):
            items.append((i, len(m.group(1)), raw))
    out = []
    for k, (i, ind, raw) in enumerate(items):
        kids = []
        for j, ind2, raw2 in items[k + 1:]:
            if ind2 <= ind:
                break
            if ind2 == ind + INDENT_STEP and not FOLLOWUP_LINE.search(raw2):   # 후속 표시 줄은 사실도 원인도 아니다
                kids.append(raw2)
        if len(kids) < 2:
            continue
        bare = [re.sub(r"`[^`]*`", "", c) for c in kids]
        causal = [bool(CAUSAL.search(c)) for c in bare]
        facts = [not cz and len(FACT_NUM.findall(c)) >= 2 for c, cz in zip(bare, causal)]
        # ① 하위 줄 어디에도 인과 표지가 없거나 ② 숫자만 늘어놓은 사실 줄이 둘 이상이고 하위의 절반 이상(2026-10-14)
        if not any(causal) or (sum(facts) >= 2 and sum(facts) * 2 >= len(kids)):
            out.append((i, raw.strip()[:70]))
    return out


def _bare_text(l):
    """백틱 구간·이탤릭 따옴표 인용(*"…"*)·괄호(중첩 포함)를 벗긴 본문."""
    s = re.sub(r"`[^`]*`", "", l)
    s = re.sub(r"\*\"[^\"]*\"\*|\*“[^”]*”\*", "", s)
    prev = None
    while prev != s:
        prev, s = s, re.sub(r"\([^()]*\)", "", s)
    return s


def check_arrow_chain_candidates(raw_lines):
    """본문 불릿에서 백틱·괄호를 벗긴 뒤, 숫자로 이어지지 않는 `→`가 ARROW_MIN개 이상인데 우리말 연결어(CONNECTIVE)가 하나도
    없는 줄 — (행 번호, 화살표 수, 줄). 지적이 아니라 후보 목록이다(게이트가 처리 여부를 묻는다)."""
    out = []
    for i, l in _bullets_after_header(raw_lines):
        s = _bare_text(l)
        n = len(ARROW_CAUSAL.findall(s))
        if n >= ARROW_MIN and not CONNECTIVE.search(s):
            out.append((i, n, l.strip()[:70]))
    return out


def _bullets_after_header(raw_lines):
    """(행 번호, 줄) — 머리 인용 블록(`---` 앞)을 빼고 본문 불릿만."""
    start = 0
    for i, l in enumerate(raw_lines):
        if l.strip() == "---":
            start = i + 1
            break
    return [(i + 1, l) for i, l in enumerate(raw_lines) if i >= start and re.match(r"^\s*- ", l)]


def check_mixed_labels(raw_lines):
    """한 줄에 `[AI]`와 `[추정]`이 함께 있으면 줄을 나눌 것 — 검증 대상(외부 사실)과 해석이 섞이면 fact-check가 그 줄을
    통째로 판정해야 하고, 감사는 라벨 규율 위반으로 잡는다(2026-10-14 승격 · label_discipline · 261013 두 줄)."""
    return [(i, l.strip()[:70]) for i, l in _bullets_after_header(raw_lines) if "[AI]" in l and "[추정]" in l]


def check_structure(raw_lines, legacy=False):
    """`###` 이하 헤더 · 굵은 글씨만 있는 줄(임의 소제목) · 항목 없는 카테고리 헤더 — 허용 배열은 `#` 제목 1개와 `## ` 카테고리뿐
    (SKILL §6 배열 · 2026-10-14 · invented_headers의 기계 검사분)."""
    out = []
    for i, l in enumerate(raw_lines, 1):
        if re.match(r"^#{3,}\s", l):
            out.append(("### 이하 헤더", i, l.strip()[:60]))
        elif not legacy and re.fullmatch(r"\*\*[^*]+\*\*\s*:?", l.strip()):
            out.append(("굵은 글씨만 있는 줄(임의 소제목)", i, l.strip()[:60]))
    heads = [i for i, l in enumerate(raw_lines) if l.startswith("## ")]
    for k, h in enumerate(heads):
        end = heads[k + 1] if k + 1 < len(heads) else len(raw_lines)
        if not any(raw_lines[j].startswith("- ") for j in range(h + 1, end)):
            out.append(("항목 없는 카테고리 헤더", h + 1, raw_lines[h].strip()))
    return out


def _note_day_ver(path):
    m = re.match(r"^세상공부_(\d{6})(?:_[^_]+)*?_v(\d+)_(\d+)\.md$", os.path.basename(path))
    if not m:
        m2 = re.match(r"^세상공부_(\d{6})", os.path.basename(path))
        return (m2.group(1) if m2 else None), None
    return m.group(1), (int(m.group(2)), int(m.group(3)))


def find_fc_report(path):
    """노트 날짜의 최신 검증 리포트(`AI클레임_검증리포트_<날짜>_vN_M.md`) — 없으면 None."""
    day, _ = _note_day_ver(path)
    if not day:
        return None
    best = None
    for p in glob.glob(os.path.join(HERE, "_raw_sources", "AI클레임_검증리포트_%s_v*.md" % day)):
        m = re.search(r"_v(\d+)_(\d+)\.md$", p)
        if m and (best is None or (int(m.group(1)), int(m.group(2))) > best[0]):
            best = ((int(m.group(1)), int(m.group(2))), p)
    return best[1] if best else None


def parse_corrections(report_path):
    """교정 지시서 표(`| # | 심각도 | 위치 | 현재 | 조치 | 근거 |`)에서 (번호, 심각도, 조치의 따옴표 문구들)."""
    lines = open(report_path, encoding="utf-8").read().split("\n")
    out = []
    for k, l in enumerate(lines):
        if not re.match(r"^\|\s*#\s*\|\s*심각도\s*\|", l):
            continue
        head = [c.strip() for c in l.strip().strip("|").split("|")]
        if "조치" not in head:
            continue
        ai = head.index("조치")
        for row in lines[k + 1:]:
            if not row.startswith("|"):
                break
            if re.match(r"^\|\s*-{2,}", row):
                continue
            cells = [c.strip() for c in row.strip().strip("|").split(" | ")]
            if len(cells) < len(head):
                continue
            action = cells[ai].replace('\\"', "”")
            out.append((cells[0], cells[1], re.findall(r'"([^"]+)"', action)))
    return out


def _norm_fc(s):
    s = re.sub(r"`|\*\*", "", s)
    s = re.sub(r"[\"“”‘’'「」]", "", s).replace("～", "~")
    return re.sub(r"\s+", " ", s).strip()


def check_fc_corrections_applied(raw_lines, corrections):
    """검증 리포트의 교정 조치 문구가 노트에 옮겨졌는가 — ([(번호, 심각도, 빠진 조각)], [눈으로 볼 번호]).

    판정을 맞게 해도 노트로 옮기며 빠뜨리거나 단서를 줄이면 감사가 사실 오류로 잡는다(261012·261013 — 교정 17 미반영 ·
    조례의 공유 지분 예외 탈락). 조치 문구를 `…`로 끊어 8자 이상 조각마다 노트에 그대로 있거나, 한 줄이 그 조각의
    토큰 90% 이상과 숫자 전부를 담으면 옮긴 것으로 본다(끝말을 문체에 맞춰 바꾼 것은 통과).
    """
    note_lines = [_norm_fc(l) for l in raw_lines]
    note_text = "\n".join(note_lines)
    note_tok = [set(tokens(l)) for l in note_lines]
    missing, eyes = [], []
    for num, sev, quotes in corrections:
        frags = [f.strip() for q in quotes for f in re.split(r"…|\.\.\.", _norm_fc(q)) if len(f.strip()) >= FRAG_MIN]
        if not frags:
            eyes.append(num)
            continue
        for f in frags:
            if f in note_text:
                continue
            ft = [t for t in tokens(f) if len(t) >= 2]
            nums = [t for t in ft if t[0].isdigit()]
            if ft and any(sum(t in ts for t in ft) >= 0.9 * len(ft) and all(n in ts for n in nums) for ts in note_tok):
                continue
            missing.append((num, sev, f[:60]))
            break
    return missing, eyes


def preaudit_status(path):
    """(필요한가, 있는가) — 261014 이후 노트는 `v1_0`을 넘었거나 검증 리포트가 생긴 뒤에 사전 감사 파일이 있어야 한다(SKILL §7)."""
    day, ver = _note_day_ver(path)
    if not day or day < PREAUDIT_SINCE:
        return False, True
    need = (ver is not None and ver > (1, 0)) or factcheck_report_exists(path)
    have = os.path.isfile(os.path.join(HERE, "_raw_sources", "사전감사_%s.md" % day))
    return need, have


def check_term_candidates(raw_lines):
    """용어 ② 후보 — 법·제도·지표 이름이 처음 나온 줄과 그 직속 하위 줄에 '왜 있는 규칙인가'의 표지(REASON)가 없다.
    지적이 아니라 후보 목록이다(게이트가 처리 여부를 묻는다 · 2026-10-14 · term_three_parts 5/5 run)."""
    items = _bullets_after_header(raw_lines)
    seen, out = set(), []
    for k, (i, l) in enumerate(items):
        ind = len(l) - len(l.lstrip())
        bare = re.sub(r"`[^`]*`", "", l)
        terms = [t for t in TERM_RE.findall(bare) if t not in TERM_SKIP and len(t) >= 3]
        new = [t for t in dict.fromkeys(terms) if t not in seen]
        seen.update(terms)
        if not new:
            continue
        block = [bare]
        for j, l2 in items[k + 1:]:
            ind2 = len(l2) - len(l2.lstrip())
            if ind2 <= ind:
                break
            if ind2 == ind + INDENT_STEP:
                block.append(l2)
        if not any(REASON.search(b) for b in block):
            out.append((i, "·".join(new[:3]), l.strip()[:60]))
    return out


def check_label_candidates(raw_lines):
    """의도·인과를 단정하는 낱말(`의도·대가로·겨냥·포석`)이 `[추정]` 없이 쓰인 본문 줄 — 원문에 그 인과가 있었는지 확인할 후보."""
    return [(i, LABEL_HINT.search(l).group(0), l.strip()[:60]) for i, l in _bullets_after_header(raw_lines)
            if LABEL_HINT.search(re.sub(r"`[^`]*`", "", l)) and "[추정]" not in l]


def check_adjective_identity(raw_lines):
    """정체 대신 형용사로 때운 후보(`대표적인·유명한·업계 1위`) — 무엇을 하는/재는 것인지를 쓸 것(SKILL §3 ⓐ)."""
    return [(i, ADJ_ID.search(l).group(0), l.strip()[:60]) for i, l in _bullets_after_header(raw_lines)
            if ADJ_ID.search(re.sub(r"`[^`]*`", "", l))]


# ── 연 링크(fetch 로그) 대조 — 안 연 링크 후보 · 출처 귀속 후보 (2026-10-14) ─────────────────────────────
# 노트에 오른 항목의 링크를 다 열었는가(link_unopened 5/5 run)와 괄호 속 매체를 실제로 열었는가(사실 오류 꼬리 3 run)를
# `fetch_article.py`가 남기는 `_raw_sources/fetch_log_<날짜>.jsonl`로 대조한다. 작성자가 연 것만 센다(tag writer —
# 브라우저·WebFetch로 연 것도 `--mark-opened --how …`로 같은 태그가 붙는다 · pre·aud·fc는 감사·검증 에이전트).
WRITER_TAGS = ("writer",)
SKIP_LINK = re.compile(r"axios_adlink|/signup|account/subscriptions|axios\.com/about|axioshq|forms\.gle|instagram\.com"
                       r"|docs\.google\.com/forms|savehome\.kr|axios\.com/newsletters|jpmorgan\.com|uppity\.co\.kr/newsletter")
# 매체 이름 → (도메인들, 네이버 언론사 번호들). 괄호 속 출처 표기에 쓰인 이름만 대조한다.
OUTLETS = {
    "연합뉴스": (("yna.co.kr",), ("001",)), "서울경제": (("sedaily.com",), ("011",)), "한국경제": (("hankyung.com",), ("015",)),
    "매일경제": (("mk.co.kr",), ("009",)), "뉴스1": (("news1.kr",), ("421",)), "이데일리": (("edaily.co.kr",), ("018",)),
    "머니투데이": (("mt.co.kr",), ("008",)), "조선일보": (("chosun.com",), ("023",)), "조선비즈": (("biz.chosun.com",), ("366",)),
    "중앙일보": (("joongang.co.kr",), ("025",)), "노컷뉴스": (("nocutnews.co.kr",), ("079",)),
    "헤럴드경제": (("heraldcorp.com",), ("016",)), "아이뉴스24": (("inews24.com",), ("031",)), "국민일보": (("kmib.co.kr",), ("005",)),
    "파이낸셜뉴스": (("fnnews.com",), ("014",)), "문화일보": (("munhwa.com",), ("021",)), "동아일보": (("donga.com",), ("020",)),
    "한겨레": (("hani.co.kr",), ("028",)), "경향신문": (("khan.co.kr",), ("032",)), "전자신문": (("etnews.com",), ("030",)),
    "뉴시스": (("newsis.com",), ("003",)), "매일일보": (("m-i.kr",), ()), "경기일보": (("kyeonggi.com",), ()),
    "시사저널e": (("sisajournal-e.com",), ()), "뉴스핌": (("newspim.com",), ()), "주간조선": ((), ("053",)),
    "CNBC": (("cnbc.com",), ()), "AP": (("apnews.com",), ()), "로이터": (("reuters.com",), ()), "FT": (("ft.com",), ()),
    "WSJ": (("wsj.com",), ()), "블룸버그": (("bloomberg.com",), ()), "LA타임스": (("latimes.com",), ()),
    "비즈니스인사이더": (("businessinsider.com",), ()), "NBC": (("nbcnews.com",), ()), "Al Jazeera": (("aljazeera.com",), ()),
    "아나돌루": (("aa.com.tr",), ()),
}


def _norm_url(u):
    try:
        from fetch_article import norm_url
        return norm_url(u)
    except Exception:
        return (u or "").split("?")[0].rstrip("/").lower()


def load_opened(day):
    """그 날짜와 다음 날의 fetch 로그에서 작성자가 연 URL(정규화) 집합. 로그가 없으면 None."""
    if not day:
        return None
    from datetime import datetime, timedelta
    try:
        nxt = (datetime.strptime(day, "%y%m%d") + timedelta(days=1)).strftime("%y%m%d")
    except ValueError:
        return None
    files = [os.path.join(HERE, "_raw_sources", "fetch_log_%s.jsonl" % d) for d in (day, nxt)]
    files = [f for f in files if os.path.isfile(f)]
    if not files:
        return None
    import json
    opened = set()
    for f in files:
        for line in open(f, encoding="utf-8"):
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("tag", "writer") in WRITER_TAGS and r.get("ok"):
                for k in ("norm", "final"):
                    if r.get(k):
                        opened.add(_norm_url(r[k]))
    return opened


def digest_links(material):
    """재료의 `### 이 발행분의 링크` 목록 — [(앵커, URL)]."""
    if not material or not os.path.isfile(material):
        return []
    out = []
    for m in re.finditer(r"^- \[(.*?)\] (https?://\S+)", open(material, encoding="utf-8").read(), re.M):
        out.append((m.group(1), m.group(2)))
    return out


def check_link_candidates(body, material, opened):
    """노트 본문과 드문 낱말(숫자 · 3자 이상 한글 · 4자 이상 로마자)을 공유하는 재료 링크인데 작성자가 열지 않은 것 — 후보."""
    links = [(a, u) for a, u in digest_links(material) if not SKIP_LINK.search(u)]
    df = {}
    for a, _u in links:
        for t in set(tokens(a)):
            df[t] = df.get(t, 0) + 1
    note_tok = set(tokens(" ".join(body)))
    out = []
    for a, u in links:
        if _norm_url(u) in opened:
            continue
        rare = [t for t in set(tokens(a)) if df.get(t, 9) <= 2 and (
            t[0].isdigit() or ("가" <= t[0] <= "힣" and len(t) >= 3) or (t[0].isascii() and t[0].isalpha() and len(t) >= 4))]
        hit = sorted(t for t in rare if t in note_tok)
        if hit:
            out.append((a[:40], u[:90], hit[:3]))
    return out


def _outlet_seen(name, urls):
    doms, oids = OUTLETS[name]
    for u in urls:
        if any(d in u for d in doms):
            return True
        m = re.search(r"news\.naver\.com/(?:mnews/)?article/(\d{3})/", u)
        if m and m.group(1) in oids:
            return True
    return False


def check_attribution_candidates(raw_lines, material, opened):
    """괄호 속 출처 표기에 쓴 매체인데 연 링크·재료 링크 어디에도 그 매체가 없다 — 재인용을 직접 연 것처럼 귀속했을 후보
    (261013: 넷플릭스 감원의 'LA타임스·비즈니스인사이더'는 머니투데이가 아마존 감원에 붙인 출처였다)."""
    urls = set(opened) | {_norm_url(u) for _a, u in digest_links(material)}
    out = []
    for i, l in _bullets_after_header(raw_lines):
        paren = " ".join(re.findall(r"\(([^()]*)\)", l))
        for name in OUTLETS:
            pat = r"(?<![A-Za-z가-힣])%s(?![A-Za-z가-힣0-9])" % re.escape(name)
            if re.search(pat, paren) and not _outlet_seen(name, urls):
                out.append((i, name, l.strip()[:60]))
    return out


def check_filter_nudge(raw_lines):
    """Gmail 필터 생성 권유 문구 — 헤더 문단까지 전 줄. 스팸함 발행분은 통수만 적는다(SKILL §0)."""
    return [(i, l.strip()[:70]) for i, l in enumerate(raw_lines, 1) if FILTER_NUDGE.search(l)]


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
    → **PDF를 '못 연 것'으로 적을 이유는 둘뿐**이다: ① 스캔 이미지 PDF(텍스트 층 없음) ② **사이트 전체가
    스크립트를 막는 경우**(403·401·봇차단·페이월·도메인 없음 — `fetch_article.py`가 PDF 판별 전에
    HTTP 오류로 멈춘다 · 2026-10-08 congress.gov·ft.com). 그 단서가 **그 PDF 언급 근처**(앞뒤 80자)에
    있으면 통과, 없으면 스크립트를 안 돌려 본 것이므로 막는다.
    """
    site_block = re.compile(r"403|401|봇\s?차단|차단|페이월|도메인|DNS|NXDOMAIN|IncompleteRead|전송 중단|Under Maintenance")
    hits = []
    for i, l in enumerate(raw_lines, 1):
        if "못 연 것" not in l and "열지 못한 것" not in l:
            continue
        tail = l.split("못 연 것", 1)[-1].split("열지 못한 것", 1)[-1]
        for pat, why in ((r"PDF|\.pdf\b", "PDF"), (r"다운로드 대화상자", "다운로드 대화상자"),
                         (r"내려받기", "내려받기")):
            for m in re.finditer(pat, tail, re.I):
                near = tail[max(0, m.start() - PDF_NEAR_CHARS): m.end() + PDF_NEAR_CHARS]
                if why == "PDF" and ("스캔" in near or site_block.search(near)):
                    continue
                hits.append((why, i, near.strip()[:90]))
                break
    return hits


def factcheck_report_exists(path):
    """그 날짜의 fact-check 검증 리포트가 생겼는가. 없으면 아직 ③(fact-check) 전이다.

    마무리 절차는 ① 기계 검사 → ② 읽기 → ③ fact-check 순이라, ①에서 집계 검사를 켜면
    **매번 반드시** 걸린다(2026-10-06 run의 종료 코드 1이 이것). 리포트가 생긴 뒤에만 엄격하게 본다.
    """
    m = re.match(r"^세상공부_(\d{6})", os.path.basename(path))
    if not m:
        return True          # 날짜를 모르면 엄격한 쪽으로
    return bool(glob.glob(os.path.join(HERE, "_raw_sources", "AI클레임_검증리포트_%s_*.md" % m.group(1))))


# 영상·오디오를 내장 브라우저로 열면 자동 재생으로 사용자 컴퓨터에서 소리가 난다(2026-10-06 — YouTube의
# The Axios Show·60 Minutes). 노트 헤더 링크 줄이 '무엇을 어떤 수단으로 열었나'를 적으므로 그 기록을 본다.
MEDIA_WORDS = re.compile(r"YouTube|유튜브|youtu|동영상|영상|팟캐스트|[Pp]odcast|[Vv]ideo|Axios Show")


def check_media_in_browser(raw_lines):
    """헤더 `링크` 줄의 '내장 브라우저로' 구간에 영상·오디오가 있으면 잡는다."""
    hits = []
    for i, l in enumerate(raw_lines, 1):
        if not l.startswith("> **링크**") or "내장 브라우저" not in l:
            continue
        rest = l[l.index("내장 브라우저"):]
        close = rest.find("**")
        if 0 <= close <= 15:                     # 굵은 라벨(`**앱 내장 브라우저로**:`)의 닫는 표시
            rest = rest[close + 2:]
        seg = rest.split("**", 1)[0]             # 다음 굵은 라벨(`**끝내 못 연 것**` 등)까지
        m = MEDIA_WORDS.search(seg)
        if m:
            a = max(0, m.start() - 30)
            hits.append((i, seg[a:m.end() + 40].strip()))
    return hits


def prior(terms, days=14):
    """최근 노트(메인 폴더 · 날짜별 최신판)에서 용어별 등장 위치. 찾기만 하므로 항상 0."""
    from datetime import date, timedelta
    notes_dir = os.path.join(HERE, "세상공부")
    note_re = re.compile(r"^세상공부_(\d{6})_노트_v(\d+)_(\d+)\.md$")
    cutoff = (date.today() - timedelta(days=days)).strftime("%y%m%d")
    latest = {}
    for p in glob.glob(os.path.join(notes_dir, "세상공부_*.md")):
        m = note_re.match(os.path.basename(p))
        if not m or m.group(1) < cutoff:
            continue
        ver = (int(m.group(2)), int(m.group(3)))
        if m.group(1) not in latest or ver > latest[m.group(1)][0]:
            latest[m.group(1)] = (ver, p)
    files = [(d, latest[d][1]) for d in sorted(latest)]
    print("최근 %d일 노트 %d개(%s)\n" % (days, len(files), " · ".join(os.path.basename(p) for _, p in files)))
    for t in terms:
        hits = []
        for d, p in files:
            for i, l in enumerate(open(p, encoding="utf-8"), 1):
                k = l.find(t)
                if k >= 0:
                    a = max(0, k - 25)
                    hits.append("%s/%s L%d: %s" % (d[2:4], d[4:6], i, l[a:k + len(t) + 35].strip()))
        print("■ %s — %d건" % (t, len(hits)))
        for h in hits[:5]:
            print("    " + h)
        if len(hits) > 5:
            print("    … 외 %d건" % (len(hits) - 5))
    return 0


def check_factcheck_recorded(raw_lines):
    """`[AI]` 줄이 있는데 헤더에 fact-check 판정 집계(MATCH·MISMATCH·STALE 수)가 없으면 잡는다.

    왜 기계로 잡나: 260925 노트의 검증이 31건 중 11건 판정에서 멈췄는데, 헤더에는 "결과는 아래
    〈검증 결과〉 절에 기록"이라는 예고만 남고 그 절이 없었다. 게이트 문항은 "돌렸는가"만 물어서
    **돌리다 멈춘 것**은 통과했고, 사흘 뒤 문체 재작성 때에야 발견됐다(2026-09-28).
    → 집계 숫자가 헤더에 있어야 검증이 끝난 것이다. `[AI]`가 0건이면 대상이 아니다.
    """
    body = "\n".join(raw_lines)
    if "[AI]" not in body.split("\n---", 1)[-1]:
        return None
    head = body.split("\n---", 1)[0]
    if re.search(r"(MATCH|MISMATCH|STALE)\s*\d+", head):
        return None
    return "헤더에 fact-check 판정 집계(MATCH·MISMATCH·STALE 수)가 없음"


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
    note_re = re.compile(r"^세상공부_(\d{6})(?:_노트)?_v(\d+)_(\d+)\.md$")
    # ★ 날짜별 최신판만, 날짜순으로 직전 3일치와 비교한다(2026-10-08 수정). 예전에는 경로 전체로
    #   정렬해 `이전버전/`(이)이 메인 폴더(세)보다 뒤로 가서, `prev[-3:]`가 늘 이전버전 폴더의 같은 날
    #   옛 판 3개가 됐다 — 최근 노트와의 중복을 사실상 보지 않고 있었다.
    m_cur = re.match(r"^세상공부_(\d{6})", os.path.basename(current))
    cur_day = m_cur.group(1) if m_cur else None
    latest = {}
    for p in (glob.glob(os.path.join(notes_dir, "세상공부_*.md")) +
              glob.glob(os.path.join(notes_dir, "이전버전", "세상공부_*.md"))):
        m = note_re.match(os.path.basename(p))
        if not m or os.path.abspath(p) == os.path.abspath(current):
            continue
        # 같은 날짜의 다른 버전은 중복이 아니라 개정이므로 제외(날짜로 거른다 — 파일명 규칙이 바뀌어도 안전)
        day, ver = m.group(1), (int(m.group(2)), int(m.group(3)))
        if day == cur_day or (cur_day and day > cur_day):
            continue
        if day not in latest or ver > latest[day][0]:
            latest[day] = (ver, p)
    prev = [latest[d][1] for d in sorted(latest)]
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
    # §5 문체 — 서술형·형식 명사 종결과 비유·예고 소제목(2026-09-28). 실제 노트에서 걸린 줄을 박아둔다.
    for name, line, want in [
            ("서술형 -감 적발", "- 금리가 올라감 `[추정]`", True),
            ("단독 '감' 적발", "- 그래서 돈이 빠른 답으로 감", True),
            ("있음+출처 괄호 적발", "- 공급이 부족해지고 있음 (9/24 · Axios)", True),
            ("단독 '봄' 적발", "- 시장은 반등 신호로 봄", True),
            ("아님 적발", "- 단기 이익일 뿐 구조 변화는 아님", True),
            ("~한다 적발", "- 연준이 추가 인상을 시사한다", True),
            ("~하는 것 적발", "- 규제가 만든 것", True),
            ("~하는 중 적발", "- 예측시장이 베팅 수요를 가져가는 중", True),
            ("명사 종결 통과", "- 금리 상승 (9/24 · Axios)", False),
            ("미정 통과", "- 아직 미정", False),
            ("회사 통과", "- 처음 나온 회사", False),
            ("부담감 확대 통과", "- 부담감 확대", False),
            ("사임(명사) 통과", "- 인텔 CEO 사임 (9/22 · Axios AM)", False),
            ("결함·조짐 통과", "- 경기 침체 조짐", False),
            ("검토 중 통과", "- 추가 관세 검토 중", False),
            ("자본 구조 통과", "- 오라클의 자본 구조", False),
            ("물음표 통과", "- 크래프톤의 더블랙레이블 투자. 왜?", False),
            ("수치 끝 통과", "- S&P 500 5%", False),
            ("쏠림(명사) 통과", "- 한 달 적금으로 자금 쏠림", False),
            ("운임(명사) 통과", "- 국제 운임보다 비싼 국내 해상운임", False),
            ("인용 끝 통과", '- 팰론의 답: *"헤로인을 해 봐야 아는 건 아님"* `[기사]`', False)]:
        got = bool(check_verb_endings([line]))
        hit = got == want
        ok &= hit
        print("  %s %-22s %-34s → %s" % ("✓" if hit else "✗", name, line[:32],
                                         "적발" if got else "통과"))
    for name, line, want in [
            ("예고 소제목 적발", "- **그래서 이 뉴스와 무슨 상관인가** — x", True),
            ("자리(비유) 적발", "- **왜 주가가 빠졌나 — 갈등의 자리** `[추정]`", True),
            ("계산서(비유) 적발", "- 정부 이자 계산서", True),
            ("마디 적발", "- AI 속도조절 여덟째 마디", True),
            ("헤더의 줄기 적발", "> **후속 줄기 여덟** — x", True),
            ("새로 붙은 것 적발", "- **260918에서 새로 붙은 것** — x", True),
            ("생각 거품 적발", "- **Axios의 생각 거품** — x", True),
            ("판(비유) 적발", "> 버린 것: 금액 자체는 판이 아님", True),
            ("일자리 통과", "- 일자리 감소", False),
            ("손익계산서 통과", "- 손익계산서 개선", False),
            ("배우는 질문 통과", "- 왜 자본 수요가 커지면 금리가 오르나", False),
            ("닷컴 거품 통과", "- 닷컴 거품 때", False),
            ("줄기세포 통과", "- 줄기세포 치료제 승인", False),
            ("판도(원본 어휘) 통과", "- 국내 이커머스 시장 판도 변동", False),
            ("백틱 안은 통과", "- 원문 표기 `thought bubble` 인용", False)]:
        got = bool(check_style_words([line]))
        hit = got == want
        ok &= hit
        print("  %s %-22s %-34s → %s" % ("✓" if hit else "✗", name, line[:32],
                                         "적발" if got else "통과"))
    # fact-check 결과 미기록(2026-09-28 — 260925 검증이 11/31에서 멈춘 채 통과했던 사례)
    fc_bad = check_factcheck_recorded(["# t", "> **[AI] 검증** — 결과는 아래 절에 기록", "---", "- 정의 `[AI]`"])
    fc_ok = check_factcheck_recorded(["# t", "> **[AI] 검증** — **MATCH 6 · MISMATCH 0**", "---", "- 정의 `[AI]`"])
    fc_none = check_factcheck_recorded(["# t", "> 머리말", "---", "- 사실만"])
    for name, hit in [("검증 집계 없음 적발", bool(fc_bad)), ("집계 있으면 통과", fc_ok is None),
                      ("[AI] 0건이면 통과", fc_none is None)]:
        ok &= hit
        print("  %s %s" % ("✓" if hit else "✗", name))
    # Gmail 필터 권유(2026-10-02 — 직전 노트 헤더를 본떠 매일 딸려 오던 문구)
    nz_bad = check_filter_nudge(["> `★ 스팸함에서 건진 발행분` 0통(Gmail '스팸함으로 보내지 않음' 필터가 아직 없다면 생성 필요)",
                                 "> 보고에 Gmail 필터를 만들 것"])
    nz_ok = check_filter_nudge(["> `★ 스팸함에서 건진 발행분` 0통", "- 시장 필터 없이 열린 것 전부"])
    for name, hit in [("필터 권유 적발", len(nz_bad) == 2), ("필터 낱말 일반 용법 통과", not nz_ok)]:
        ok &= hit
        print("  %s %s" % ("✓" if hit else "✗", name))
    # 영상·오디오를 내장 브라우저로(2026-10-06 — 자동 재생으로 소리가 났다)
    mb_bad = check_media_in_browser([
        "> **링크** — 스크립트로: 국내 기사 3건. **앱 내장 브라우저로**: Axios 자체 기사 24건(프랑스 · B-1) · "
        "YouTube 2건(The Axios Show 베선트 편 — 제목만 확인). **끝내 못 연 것**: FT"])
    mb_script = check_media_in_browser([
        "> **링크** — **스크립트로**: YouTube 2건(제목·설명). **앱 내장 브라우저로**: Axios 자체 기사 3건(프랑스 · B-1)"])
    mb_axios = check_media_in_browser(["> **링크** — **앱 내장 브라우저로**: Axios 자체 기사 3건(프랑스 · B-1 · 라티노)"])
    fr_pending = not factcheck_report_exists("세상공부_991231_노트_v1_0.md")
    for name, hit in [("브라우저로 연 YouTube 적발", len(mb_bad) == 1), ("스크립트로 본 YouTube 통과", not mb_script),
                      ("브라우저로 Axios 기사만 통과", not mb_axios), ("리포트 없는 날짜는 ⏳", fr_pending)]:
        ok &= hit
        print("  %s %s" % ("✓" if hit else "✗", name))
    # 2026-10-08 승격 — 감사가 3 run 반복해 잡은 표현(ai_phrases·connective_glue)과 메커니즘 후보
    for name, line, want in [
            ("'값'(가격) 적발", "- 수입 원자재 값 상승 (UPPITY)", True),
            ("값을 내려 적발", "- 한 회사가 값을 내려 손님을 데려올 동기", True),
            ("흔적 셋 적발", "- **금리 상승이 남긴 흔적 셋** (Axios Markets)", True),
            ("~의 무게 적발", "- 3분기 실적 시즌의 무게 — x", True),
            ("닿는 경로 적발", "- 담합이 소비자에게 닿는 경로 `[추정]`", True),
            ("~의 핵심 — 적발", "- 이번 시험의 핵심 — 맞춤형 수송", True),
            ("물건값(합성어) 통과", "- '물가 상승률 둔화'와 '물건값 하락'은 별개", False),
            ("값비싼 통과", "- 값비싼 장비 투자", False),
            ("핵심 공정 통과", "- HBM 생산의 핵심 장비", False)]:
        got = bool(check_style_words([line]))
        hit = got == want
        ok &= hit
        print("  %s %-22s %-34s → %s" % ("✓" if hit else "✗", name, line[:32], "적발" if got else "통과"))
    for name, line, want in [
            ("같은 경로 적발", "- ⇒ 10/5 노트의 베이시스 청산 의혹과 같은 경로", True),
            ("맞물림 적발", "- 단기채를 더 찍어야 하는 구조와 맞물림", True),
            ("직결 적발", "> 후속 — 금리와 직결", True),
            ("단계를 푼 줄 통과", "- ① 예치금을 단기국채로 굴림 → ② 단기국채 수요 증가", False),
            ("백틱 안은 통과", "- 원문 `same direction` 인용", False)]:
        got = bool(check_glue([line]))
        hit = got == want
        ok &= hit
        print("  %s %-22s %-34s → %s" % ("✓" if hit else "✗", name, line[:32], "적발" if got else "통과"))
    mech_bad = check_mechanism_candidates(["- 사건 A (UPPITY)", "    - 18명 중 16명 추가 인상 예상", "    - 의장은 전망 미제출"])
    mech_ok = check_mechanism_candidates(["- 사건 A (UPPITY)", "    - 18명 중 16명", "    - 인상 이유 — 물가 확산 우려"])
    mech_one = check_mechanism_candidates(["- 사건 A (UPPITY)", "    - 사실 하나"])
    # PDF — 스크립트를 안 돌린 PDF만 적발, 사이트 전체 차단·스캔은 통과(2026-10-08)
    pdf_bad = check_unopened_pdf(["> **끝내 못 연 것**: 감사원 보고서 PDF(링크만 확인)"])
    pdf_lower = check_unopened_pdf(["> **끝내 못 연 것**: report.pdf 한 건"])
    pdf_block = check_unopened_pdf(["> **끝내 못 연 것**: CRS 보고서 PDF(congress.gov 스크립트 403 · 도메인 전체 봇차단)"])
    pdf_scan = check_unopened_pdf(["> **끝내 못 연 것**: 판결문 PDF(스캔 이미지 — 텍스트 층 없음)"])
    pdf_mixed = check_unopened_pdf(["> **끝내 못 연 것**: 판결문 PDF(스캔 이미지) · " + "가" * 90 + " · 기관 보고서 PDF(링크만)"])
    for name, hit in [("하위 줄 모두 사실 → 후보", len(mech_bad) == 1), ("이유 줄 있으면 통과", not mech_ok),
                      ("하위 1줄은 대상 아님", not mech_one), ("못 연 PDF 적발", len(pdf_bad) == 1),
                      ("소문자 .pdf 적발", len(pdf_lower) == 1), ("사이트 전체 403 통과", not pdf_block),
                      ("스캔 PDF 통과", not pdf_scan), ("스캔 면제는 그 PDF에만", len(pdf_mixed) == 1)]:
        ok &= hit
        print("  %s %s" % ("✓" if hit else "✗", name))
    # 최근 노트 중복 — 날짜별 최신판·날짜순 직전 3일(2026-10-08 정렬 버그 회귀)
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        os.makedirs(os.path.join(d, "이전버전"))
        def _w(rel, body):
            with open(os.path.join(d, rel), "w", encoding="utf-8") as fh:
                fh.write(body)
        _w("세상공부_261005_노트_v1_3.md", "- 국채 금리 상승 확대 우려 지속 (Axios)\n")
        _w("이전버전/세상공부_261007_노트_v1_0.md", "- 옛 판에만 있던 문장입니다 확인용 (Axios)\n")
        _w("세상공부_261007_노트_v1_5.md", "- 최신판 문장 금리 경로 확인용 (Axios)\n")
        cur = os.path.join(d, "세상공부_261008_노트_v1_0.md")
        _w("세상공부_261008_노트_v1_0.md", "")
        dup_hit = check_dupes(["- 국채 금리 상승 확대 우려 지속 (Axios)"], d, cur)
        old_ver = check_dupes(["- 옛 판에만 있던 문장입니다 확인용 (Axios)"], d, cur)
    for name, hit in [("최근 노트 중복 적발(메인 폴더)", len(dup_hit) == 1),
                      ("같은 날짜 옛 판은 비교 안 함", not old_ver)]:
        ok &= hit
        print("  %s %s" % ("✓" if hit else "✗", name))
    ok &= selftest_261014()
    print("\n통과" if ok else "\n실패")
    return 0 if ok else 1


def check_skill_examples(skill_path=None):
    """SKILL.md의 ✓ 예시가 이 검사기에 걸리지 않는가 — 예시가 금지 문체를 가르치면 노트도 그렇게 쓴다(2026-10-14:
    ✓ 칸 8곳이 `올라감·씀·있었음`으로 끝나 §5-1과 모순). 또 §5-2 'AI 티가 나는 표현' 목록이 전부 BANNED에 있는가.
    (빠진 것 목록, 건너뜀 여부)."""
    skill_path = skill_path or os.path.join(HERE, "..", ".claude", "skills", "world-study", "SKILL.md")
    if not os.path.isfile(skill_path):
        return [], True
    lines = open(skill_path, encoding="utf-8").read().split("\n")
    bad, k = [], 0
    while k < len(lines):
        l = lines[k]
        if l.startswith("|") and "✓" in l and "이렇게" not in l:
            head = [c.strip() for c in l.strip().strip("|").split("|")]
            cols = [n for n, c in enumerate(head) if "✓" in c]
            k += 1
            while k < len(lines) and lines[k].startswith("|"):
                row = lines[k]
                if not re.match(r"^\|\s*-{2,}", row):
                    cells = [c.strip() for c in row.strip().strip("|").split(" | ")]
                    for n in cols:
                        if n >= len(cells):
                            continue
                        for frag in re.findall(r"`([^`]+)`", cells[n]):
                            for part in frag.split("<br>"):
                                part = part.strip()
                                if not part or len(part) < 6:
                                    continue
                                probe = ["- " + part.lstrip("- ").strip()]
                                why = ([w for w, _ in check_verb_endings(probe)] + [w for w, _ in check_banned(probe)]
                                       + [w for w, _i, _l in check_style_words(probe)] + [w for w, _i, _l in check_glue(probe)])
                                if why:
                                    bad.append((k + 1, why[0], part[:50]))
                k += 1
            continue
        k += 1
    for l in lines:
        if "AI 티가 나는 표현 금지" in l:
            for w in re.findall(r"`([^`]+)`", l):
                w = w.lstrip("~").strip()
                if w and not any(w in b or b in w for b in BANNED) and not any(lab == w for lab, _rx in BANNED_RE):
                    bad.append((0, "BANNED 누락", w))
    return bad, False


def selftest_261014():
    """2026-10-14 추가 검사 — 감사가 5 run 반복해 잡은 유형에서 실제로 걸린 줄을 박아 둔다."""
    import tempfile
    ok = True

    def _t(name, hit):
        nonlocal ok
        ok &= bool(hit)
        print("  %s %s" % ("✓" if hit else "✗", name))

    _t("에 다름 아니다 적발", check_banned(["- 규제 완화에 다름 아니다"]))
    _t("스킬 점검 질문 소제목 적발", check_style_words(["- **누구에게 유리·불리한가** — 메타는 지금까지"]))
    _t("카드 칸 이름 소제목 적발", check_style_words(["    - **첫 인과** — 금리 인상 → 차입 감소"]))
    _t("헤더의 **링크** 라벨 통과", not check_style_words(["> **링크** — 스크립트로: 국내 기사 3건"]))
    _t("같은 이유로 적발", check_glue(["- 같은 이유로 소형 아파트 강세 — 서울 전용 40㎡ 이하"]))
    _t("라벨 혼용 줄 적발", check_mixed_labels(["# t", "---", "- 법이 공제를 축소 `[AI]` → 복원되면 이익 증가 `[추정]`"]))
    _t("라벨을 나눈 줄 통과", not check_mixed_labels(["# t", "---", "- 법이 공제를 축소 `[AI]`", "    - 복원되면 이익 증가 `[추정]`"]))
    st = check_structure(["# t", "---", "## 거시·시장", "- a (UPPITY)", "### 소제목", "**경제**", "## 부동산", "## 할 일·일정", "- b (UPPITY)"])
    kinds = {w for w, _i, _l in st}
    _t("### 헤더 적발", "### 이하 헤더" in kinds)
    _t("굵은 글씨만 있는 줄 적발", "굵은 글씨만 있는 줄(임의 소제목)" in kinds)
    _t("빈 카테고리 적발", "항목 없는 카테고리 헤더" in kinds)
    _t("배열대로면 통과", not check_structure(["# t", "> **선별** — x", "---", "## 거시·시장", "- a (UPPITY)", "    - b"]))
    mech_fact = check_mechanism_candidates([
        "- 수출 261억 달러 +102% (UPPITY)", "    - 국가별 중국 +167.3% · 미국 +159.9%",
        "    - 수입 223억 달러(+65.5%) · 무역수지 38억 달러", "    - 조업일수가 이틀 많기 때문에 일평균으로 볼 것"])
    mech_follow = check_mechanism_candidates([
        "- 사건 (UPPITY)", "    - **10/1 노트 이후 추가된 내용** — 10/1 노트는 3.5와 4.2였고 오늘은 5.1과 6.2",
        "    - 인상 이유 — 물가 확산 우려"])
    _t("숫자 사실 줄이 절반 이상 → 메커니즘 후보", len(mech_fact) == 1)
    _t("후속 표시 줄은 사실 줄로 안 셈", not mech_follow)
    _t("정의(=)만 있는 하위 → 후보", check_mechanism_candidates(["- 제도 X (UPPITY)", "    - X = 무엇", "    - Y = 무엇"]))
    tc_bad = check_term_candidates(["# t", "---", "- 권리산정기준일 뒤 산 땅은 산정에서 제외 (BOODING)"])
    tc_ok = check_term_candidates(["# t", "---", "- 권리산정기준일 뒤 산 땅은 제외 (BOODING)",
                                   "    - 기준일을 두는 이유 — 지분 쪼개기를 막으려는 장치 `[추정]`"])
    _t("이유 없는 용어 → 용어 ② 후보", len(tc_bad) == 1)
    _t("이유 줄 있는 용어 통과", not tc_ok)
    _t("방법·불법은 용어 아님", not check_term_candidates(["# t", "---", "- 새 방법으로 불법 거래 차단 (UPPITY)"]))
    _t("'대가로' 무라벨 → 라벨 후보", check_label_candidates(["# t", "---", "- 대가로 중국은 관세 인하 (노컷뉴스)"]))
    _t("[추정] 있으면 라벨 후보 아님", not check_label_candidates(["# t", "---", "- 압박하려는 의도 `[추정]`"]))
    _t("형용사 정체 후보", check_adjective_identity(["# t", "---", "- MSCI는 대표적인 지수"]))
    arr = check_arrow_chain_candidates
    _t("화살표 3개·연결어 없음 → 화살표 인과 후보", arr(["# t", "---", "- 순환 — MBS 급락 → 보유자가 국채 매도 → 금리 상승 → 다시 헤지 매도"]))
    _t("↑↓ 기호 사슬도 후보", arr(["# t", "---", "- 금리 경로 = 금리↑ → 대출·할부 이자↑ → 소비·투자↓ → 가격 인상 여력↓"]))
    _t("연결어가 있으면 후보 아님", not arr(["# t", "---", "- 금리가 오르면 대출 부담 증가 → 소비 위축 → 성장 둔화 → 세수 감소"]))
    _t("숫자 변화 화살표는 안 셈", not arr(["# t", "---", "- 추세 — 주의 계약 비중 22.1%(2024년 하반기) → 18.8%(2025년) → 17.1%(올해 7개월) → 내년 더 감소 전망"]))
    _t("괄호 안 화살표는 안 셈", not arr(["# t", "---", "- 금리 상승 → 대출 부담 증가(→ 소비 위축 → 성장 둔화)"]))
    _t("화살표 2개는 후보 아님", not arr(["# t", "---", "- 금리 상승 → 대출 부담 증가 → 소비 위축"]))
    _t("헤더 문단은 안 봄", not arr(["# t", "> 선별 — A → B → C → D", "---", "- x"]))
    # 교정 반영 대조 — 그대로 옮긴 것·끝말만 바꾼 것은 통과, 단서를 뗀 것은 적발
    with tempfile.TemporaryDirectory() as d:
        rp = os.path.join(d, "r.md")
        with open(rp, "w", encoding="utf-8") as fh:
            fh.write("| # | 심각도 | 위치 | 현재 | 조치 | 근거 |\n|---|---|---|---|---|---|\n"
                     "| 1 | 🟡 | 노트 x | \"원칙적으로 90㎡\" | \"종전 토지 총면적 90㎡ 이상이거나 권리가액이 최소 분양주택 추산액 이상\" | 조례 |\n"
                     "| 2 | 🟡 | 노트 y | \"z\" | \"한 필지를 여러 명이 공유하면 1명으로 보며(기준일 전부터 공유한 지분이 요건을 채우면 예외)\" | 조례 |\n"
                     "| 3 | ⚪ | 노트 w | \"v\" | 그 줄 삭제 | 조례 |\n")
        cors = parse_corrections(rp)
        _t("교정표 파싱(3행 · 따옴표 문구)", len(cors) == 3 and cors[0][2] and not cors[2][2])
        miss, eyes = check_fc_corrections_applied(
            ["- 종전 토지 총면적 90㎡ 이상이거나 권리가액이 최소 분양주택 추산액 이상 `[AI]`",
             "- 한 필지를 여러 명이 공유하면 1명으로 봄"], cors)
        _t("그대로 옮긴 교정 통과 · 단서 뗀 교정 적발", [m[0] for m in miss] == ["2"] and eyes == ["3"])
        miss2, _e = check_fc_corrections_applied(
            ["- 종전 토지 총면적이 90㎡ 이상이거나 권리가액이 최소 분양주택 추산액 이상인 경우",
             "- 한 필지를 여러 명이 공유하면 1명으로 보되(기준일 전부터 공유한 지분이 요건을 채우면 예외)"], cors)
        _t("끝말·조사만 바꾼 교정 통과", not miss2)
    need, have = preaudit_status("세상공부_991231_노트_v1_1.md")
    _t("261014 이후 v1_1인데 사전 감사 없음 → 필요", need and not have)
    _t("261014 이전 노트는 사전 감사 대상 아님", preaudit_status("세상공부_261013_노트_v1_2.md")[0] is False)
    # 연 링크 대조 — 노트와 드문 낱말을 공유하는 링크만 후보, 연 것은 제외
    with tempfile.TemporaryDirectory() as d:
        mp = os.path.join(d, "m.md")
        with open(mp, "w", encoding="utf-8") as fh:
            fh.write("### 이 발행분의 링크\n- [전력기기 빅3 주가] https://n.news.naver.com/article/015/0005340887\n"
                     "- [Go deeper] https://www.axios.com/2026/10/12/a\n- [Go deeper] https://www.axios.com/2026/10/12/b\n"
                     "- [가입하기] https://www.axios.com/signup/am-pm\n- [디티에스] https://m.sedaily.com/amparticle/20100109\n")
        body = ["- 전력기기 빅3 주가 약세 (UPPITY)", "- 디티에스 공모주 청약 (UPPITY)"]
        lk = check_link_candidates(body, mp, set())
        lk_open = check_link_candidates(body, mp, {_norm_url("https://m.sedaily.com/amparticle/20100109"),
                                                   _norm_url("https://n.news.naver.com/mnews/article/015/0005340887?sid=101")})
        _t("노트와 겹치는 안 연 링크 → 후보(Go deeper·가입은 제외)", sorted(a for a, _u, _h in lk) == ["디티에스", "전력기기 빅3 주가"])
        _t("연 링크는 후보 아님(네이버 mnews·sid 정규화)", not lk_open)
        at = check_attribution_candidates(["# t", "---", "- 넷플릭스 감원 (10/10 LA타임스 · 머니투데이 · UPPITY)"], mp,
                                          {_norm_url("https://n.news.naver.com/mnews/article/008/0005424510")})
        _t("연 기록 없는 매체 → 출처 귀속 후보(LA타임스만)", [n for _i, n, _l in at] == ["LA타임스"])
        _t("CNBC 안의 NBC는 따로 안 셈", not check_attribution_candidates(
            ["# t", "---", "- 부인 (CNBC · Axios PM)"], mp, {_norm_url("https://www.cnbc.com/2026/10/12/x.html")}))
    bad, skipped = check_skill_examples()
    if skipped:
        print("  · SKILL.md 없음 — ✓ 예시 대조 건너뜀")
    else:
        _t("SKILL.md ✓ 예시가 문체 검사를 통과 · §5-2 금지 목록이 BANNED에 있음", not bad)
        for k, why, part in bad[:8]:
            print("      [SKILL L%d · %s] %s" % (k, why, part))
    return ok


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if "--prior" in sys.argv:
        args = sys.argv[sys.argv.index("--prior") + 1:]
        days = 14
        if "--days" in args:
            i = args.index("--days")
            days = int(args[i + 1])
            del args[i:i + 2]
        if not args:
            raise SystemExit('usage: check_note.py --prior <용어> [<용어> …] [--days N]')
        return prior(args, days)
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

    vend = check_verb_endings(lines)
    verb = [l for k, l in vend if k == "서술형"]
    formal = [l for k, l in vend if k == "형식 명사"]
    if verb:
        problems += 1
        print("✗ 서술형 종결 %d건 — 어미만 떼지 말고 변화·판단을 담은 명사로 다시 짤 것(SKILL §5-1)" % len(verb))
        for l in verb[:6]:
            print("    %s" % l)
    else:
        print("✓ 서술형 종결 0건")
    if formal:
        problems += 1
        print("✗ 형식 명사 종결 %d건 (~하는 것·중·상황·구조) — 어미만 뗀 것. 문장을 다시 짤 것(§5-1)" % len(formal))
        for l in formal[:6]:
            print("    %s" % l)
    else:
        print("✓ 형식 명사 종결 0건")

    heads = check_headers(raw, legacy=legacy)
    if heads:
        problems += 1
        print("✗ 카테고리 헤더 %d건" % len(heads))
        for w, l in heads[:6]:
            print("    [%s] %s" % (w, l))
    else:
        print("✓ 카테고리 헤더 규칙대로" if not legacy else "· 구 형식 노트(--legacy) — 헤더 검사 생략")

    tags = check_issue_tags(raw)
    if tags and not legacy:
        problems += 1        # 2026-10-14: 경고 → 지적(10월 노트 8개에서 오탐 0)
        print("✗ 발행분 태그 없는 상위 항목 %d건 — 끝 괄호에 UPPITY/BOODING/STARTUP WEEKLY/Axios 표기" % len(tags))
        for l in tags[:6]:
            print("    %s" % l)
    elif tags:
        print("· 발행분 태그 없는 상위 항목 %d건(--legacy)" % len(tags))

    struct = check_structure(raw, legacy=legacy)
    if struct:
        problems += 1
        print("✗ 배열 밖 헤더·소제목 %d건 — `#` 제목 1개와 `## ` 카테고리 8개만, 빈 카테고리 헤더는 지울 것(§6)" % len(struct))
        for w, i, l in struct[:6]:
            print("    [%s · %d행] %s" % (w, i, l))
    else:
        print("✓ 배열 밖 헤더·소제목 0건")

    mixed = check_mixed_labels(raw)
    if mixed:
        problems += 1
        print("✗ 한 줄에 `[AI]`와 `[추정]`이 함께 %d건 — 외부 사실 줄과 해석 줄로 나눌 것(§4)" % len(mixed))
        for i, l in mixed[:6]:
            print("    [%d행] %s" % (i, l))
    else:
        print("✓ 라벨 혼용 줄 0건")

    # 서식 사고는 헤더에서도 난다(`v4_0 → v5_0`이 이탤릭으로 먹힘) → 본문 불릿이 아니라 전문을 본다.
    rend = check_render(raw)
    if rend:
        problems += 1
        print("✗ 앱에서 서식으로 먹힐 문자 %d건" % len(rend))
        for w, l in rend[:6]:
            print("    [%s] %s" % (w, l))
    else:
        print("✓ 서식으로 먹힐 문자 0건")

    # 비유·예고 소제목·내부 용어는 헤더 문단(선별·후속)에도 나오므로 전문을 본다.
    sty = check_style_words(raw)
    if sty:
        problems += 1
        print("✗ 비유·예고 소제목·내부 용어 %d건 — 대상·변화를 직접 쓰고 제목은 내용 요약으로(§5-2·5-3)" % len(sty))
        for w, i, l in sty[:8]:
            print("    [%s · %d행] %s" % (w, i, l))
    else:
        print("✓ 비유·예고 소제목·내부 용어 0건")

    glue = check_glue(raw)
    if glue:
        problems += 1
        print("✗ 접착어 %d건 — '같은 경로·맞물림·직결'로 잇지 말고 A에서 B로 가는 사이 단계를 풀어 쓸 것(§3 ⓑ)" % len(glue))
        for w, i, l in glue[:8]:
            print("    [%s · %d행] %s" % (w, i, l))
    else:
        print("✓ 접착어 0건")

    mech = check_mechanism_candidates(raw)
    if mech:
        print("· 메커니즘 없는 블록 후보 %d개 — 하위 줄이 사실 위주면 원인 줄을 보태거나 사실 줄을 줄여 깊이 0으로"
              "(후보를 하나씩 열 것 · 게이트가 처리 여부를 묻는다)" % len(mech))
        for i, l in mech[:15]:
            print("    [%d행] %s" % (i, l))
    else:
        print("✓ 메커니즘 없는 블록 후보 0개")

    # ── 후보 목록(지적으로 세지 않음 · 게이트 문항 check_note_candidates_reviewed가 처리 여부를 묻는다) ──
    terms = check_term_candidates(raw)
    if terms:
        print("· 용어 ② 후보 %d개 — 처음 나온 법·제도·지표에 '왜 있는 규칙인가'가 없음(① 무엇 ② 이유 ③ 이 뉴스와의 연결 · §3)"
              % len(terms))
        for i, t, l in terms[:15]:
            print("    [%d행 · %s] %s" % (i, t, l))
    else:
        print("✓ 용어 ② 후보 0개")
    labs = check_label_candidates(raw)
    if labs:
        print("· 라벨 후보 %d개 — 의도·인과 단정인데 `[추정]` 없음: 원문에 그 인과가 있으면 출처를, 없으면 `[추정]`(§4)" % len(labs))
        for i, w, l in labs[:8]:
            print("    [%d행 · %s] %s" % (i, w, l))
    adj = check_adjective_identity(raw)
    if adj:
        print("· 형용사 정체 후보 %d개 — 무엇을 하는/재는 곳인지로 바꿀 것(§3 ⓐ)" % len(adj))
        for i, w, l in adj[:8]:
            print("    [%d행 · %s] %s" % (i, w, l))
    arrows = check_arrow_chain_candidates(raw)
    if arrows:
        print("· 화살표만 있는 인과 후보 %d개 — `A → B → C`로만 적힌 줄: 처음 설명하는 인과면 A가 왜 B가 되는지를 연결어 문장으로 쓰고, "
              "앞에서 설명한 관계의 정리면 해당 없음(§5-1 · 게이트가 처리 여부를 묻는다)" % len(arrows))
        for i, n, l in arrows[:15]:
            print("    [%d행 · →%d] %s" % (i, n, l))
    else:
        print("✓ 화살표만 있는 인과 후보 0개")
    day, _ver = _note_day_ver(path)
    opened = load_opened(day)
    if opened is None:
        print("· 안 연 링크·출처 귀속 후보 확인 불가 — fetch 로그(`_raw_sources/fetch_log_%s.jsonl`) 없음"
              "(`fetch_article.py`로 열고, 브라우저로 연 것은 `--mark-opened <url> --how browser`)" % (day or "?"))
    else:
        lk = check_link_candidates(lines, material, opened)
        if lk:
            print("· 안 연 링크 후보 %d개 — 노트 항목과 낱말이 겹치는 재료 링크인데 연 기록이 없음: 열어서 기사에만 있는 원인·"
                  "당사자·수치·결론을 옮기거나 해당 없음을 확인(§3)" % len(lk))
            for a, u, h in lk[:12]:
                print("    [%s] %s  ← %s" % (a, u, "·".join(h)))
        else:
            print("✓ 안 연 링크 후보 0개")
        at = check_attribution_candidates(raw, material, opened)
        if at:
            print("· 출처 귀속 후보 %d개 — 괄호 속 매체를 연 기록도 재료 링크도 없음: 재인용이면 '(… — 인용한 매체)'로, "
                  "직접 연 것이면 `--mark-opened`로 기록" % len(at))
            for i, n, l in at[:8]:
                print("    [%d행 · %s] %s" % (i, n, l))
        else:
            print("✓ 출처 귀속 후보 0개")

    nudge = check_filter_nudge(raw)
    if nudge:
        problems += 1
        print("✗ Gmail 필터 생성 권유 %d건 — 쓰지 않는다(사용자 요청 2026-10-02 · SKILL §0). "
              "스팸함 발행분은 통수만 적을 것" % len(nudge))
        for i, l in nudge[:4]:
            print("    [%d행] %s" % (i, l))
    else:
        print("✓ Gmail 필터 권유 0건")

    fc = check_factcheck_recorded(raw)
    if fc and not factcheck_report_exists(path):
        print("⏳ %s — 검증 리포트가 아직 없음: fact-check(③) 전이면 정상(지적으로 세지 않음). "
              "③ 뒤 새 판에 다시 돌리면 이 검사가 켜진다" % fc)
    elif fc:
        problems += 1
        print("✗ %s — `[AI]` 검증이 끝났는지 확인하고 결과를 헤더에 적을 것(SKILL §4)" % fc)
    else:
        print("✓ fact-check 결과 기록됨(또는 `[AI]` 0건)")

    rep = find_fc_report(path)
    if rep:
        miss, eyes = check_fc_corrections_applied(raw, parse_corrections(rep))
        must = [x for x in miss if "⚪" not in x[1]]
        if must:
            problems += 1
            print("✗ 검증 리포트 교정 미반영 %d건 — 조치 문구를 단서·주어째 옮길 것(§4-bis · %s)"
                  % (len(must), os.path.basename(rep)))
            for num, sev, f in must[:8]:
                print("    [교정 %s %s] %s" % (num, sev, f))
        else:
            print("✓ 검증 리포트 교정 반영됨(%s)" % os.path.basename(rep))
        opt = [x for x in miss if "⚪" in x[1]]
        if opt:
            print("· 선택 교정(⚪) 미반영 %d건 — 반영 여부만 확인: %s" % (len(opt), " · ".join("교정 " + x[0] for x in opt)))
        if eyes:
            print("· 따옴표 문구가 없는 교정 %d건 — 눈으로 확인: %s" % (len(eyes), " · ".join("교정 " + e for e in eyes)))

    need, have = preaudit_status(path)
    if need and not have:
        problems += 1
        print("✗ 사전 감사 파일 없음(`_raw_sources/사전감사_%s.md`) — 게이트 전에 §7 지시문으로 사전 감사를 돌리고 "
              "유형 단위로 고칠 것" % day)
    elif need:
        print("✓ 사전 감사 파일 있음")

    mb = check_media_in_browser(raw)
    if mb:
        problems += 1
        print("✗ 내장 브라우저로 영상·오디오를 열었음 %d건 — 자동 재생으로 사용자 컴퓨터에서 소리가 난다. "
              "`python3 fetch_article.py <url>`로만(SKILL 링크 표)" % len(mb))
        for i, l in mb[:4]:
            print("    [%d행] %s" % (i, l))
    else:
        print("✓ 내장 브라우저로 연 영상·오디오 없음")

    # PDF를 '못 연 것'으로 적었으면 스크립트를 안 돌린 것이다(브라우저는 다운로드 대화상자만 띄운다).
    updf = check_unopened_pdf(raw)
    if updf:
        problems += 1
        print("✗ '못 연 것'에 PDF %d건 — `python3 fetch_article.py <url>`로 다시 열 것" % len(updf))
        print("    (브라우저로 PDF를 열면 사용자 화면에 다운로드 대화상자만 뜬다. 스캔 이미지 PDF거나 사이트 전체가 "
              "스크립트를 막으면(403·401·봇차단·도메인 없음) 그 사유와 상태코드를 PDF 언급 옆에 적을 것)")
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
                    else "통과 — 형식 검사다. 의미 판단(메커니즘·용어 설명·주어와 인과가 읽히는가)은 사람이 읽거나 감사를 켤 것"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
