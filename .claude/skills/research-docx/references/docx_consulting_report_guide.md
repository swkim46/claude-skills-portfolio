# 컨설팅 분석 보고서 docx 빌드 가이드

python-docx로 컨설팅·리서치 분석 보고서를 생성하는 표준 패턴 정리. 본문 인용·출처 관리·표·하이퍼링크 등 재사용 가능한 함수 템플릿 포함.

---

## 목차

- [1. 환경 설정](#1-환경-설정)
- [2. 문서 기본 구조](#2-문서-기본-구조)
- [3. 헬퍼 함수 — 헤더·본문·불릿·인용·표](#3-헬퍼-함수--헤더본문불릿표)
- [4. 하이퍼링크 임베드 함수](#4-하이퍼링크-임베드-함수)
- [5. 출처 관리 패턴 — SRC dict + 코드 라벨](#5-출처-관리-패턴--src-dict--코드-라벨)
- [5-A. 내용 구성 원칙 — 양보단 질](#5-a-내용-구성-원칙--양보단-질)
- [6. 표준 문서 구조](#6-표준-문서-구조)
- [7. 어투 원칙 — 자연스러운 비즈니스 한국어](#7-어투-원칙--자연스러운-비즈니스-한국어)
- [8. 출처 정직성 원칙](#8-출처-정직성-원칙)
- [9. 빌드·검증·버전 관리](#9-빌드검증버전-관리)

---

## 1. 환경 설정

### 패키지

```bash
pip install python-docx
```

### 폰트 — 한국어 처리

- 본문 한국어 폰트: **맑은 고딕** (Windows 호환·기본 설치)
- 영문/숫자: 맑은 고딕 (자동 fallback) 또는 별도 지정
- EastAsia 속성 필수 — 한국어가 잘리지 않도록 `w:eastAsia`에 폰트명 명시

```python
from docx.oxml.ns import qn

# 모든 Run에 적용해야 하는 패턴
run.font.name = '맑은 고딕'
run._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
```

---

## 2. 문서 기본 구조

### 도큐먼트 초기화 + 여백·기본 스타일

```python
from docx import Document
from docx.shared import Pt, RGBColor, Cm
from docx.oxml.ns import qn
from docx.oxml import OxmlElement

doc = Document()

# 기본 스타일
style = doc.styles['Normal']
style.font.name = '맑은 고딕'
style._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
style.font.size = Pt(10)

# 여백 (A4 기준 안정적인 값)
for section in doc.sections:
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)
```

### 제목·부제

```python
# 제목
title = doc.add_paragraph()
trun = title.add_run('증권사 B증권 IB·Global 부문 재편 사례 분석')
trun.font.name = '맑은 고딕'
trun._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
trun.font.bold = True
trun.font.size = Pt(18)

# 부제 (작성자·날짜)
sub = doc.add_paragraph()
sr = sub.add_run('운영자 / 2026.06.03')
sr.font.name = '맑은 고딕'
sr._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
sr.font.size = Pt(9)
sr.font.color.rgb = RGBColor(0x70, 0x70, 0x70)
```

---

## 3. 헬퍼 함수 — 헤더·본문·불릿·표

### H1 / H2 / H3 — 헤더

```python
def H1(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(18)
    p.paragraph_format.space_after = Pt(8)
    r = p.add_run(text)
    r.font.name = '맑은 고딕'
    r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
    r.font.bold = True
    r.font.size = Pt(14)
    r.font.color.rgb = RGBColor(0x1F, 0x1F, 0x1F)


def H2(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(12)
    p.paragraph_format.space_after = Pt(4)
    r = p.add_run(text)
    r.font.name = '맑은 고딕'
    r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
    r.font.bold = True
    r.font.size = Pt(11.5)


def H3(text):
    p = doc.add_paragraph()
    p.paragraph_format.space_before = Pt(8)
    p.paragraph_format.space_after = Pt(3)
    r = p.add_run(text)
    r.font.name = '맑은 고딕'
    r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
    r.font.bold = True
    r.font.size = Pt(10.5)
```

### P — 본문 단락 (인라인 bold 지원)

```python
def P(parts, size=10, space_after=4):
    """parts: str | list of (text, bold) tuples"""
    if isinstance(parts, str):
        parts = [(parts, False)]
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(space_after)
    p.paragraph_format.line_spacing = 1.4
    for text, bold in parts:
        r = p.add_run(text)
        r.font.name = '맑은 고딕'
        r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
        r.font.size = Pt(size)
        if bold:
            r.font.bold = True
    return p
```

**사용 예**:
```python
P([
    ('재편 직후 2023년 증권사 B증권은 ', False),
    ('IB부문 영업순수익 -70.3%', True),     # bold
    ('의 큰 폭 후퇴를 기록함.', False),
])
```

### B — 불릿 (인라인 bold + 출처 참조 지원)

```python
def B(parts, level=0, size=10, refs=None):
    """parts: str | list of (text, bold)
       level: 0(•), 1(◦), 2(–)
       refs: list of (label, url) — 끝에 [label] 하이퍼링크 부착"""
    if isinstance(parts, str):
        parts = [(parts, False)]
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.5 + level * 0.7)
    p.paragraph_format.first_line_indent = Cm(-0.4)
    p.paragraph_format.space_after = Pt(2)
    p.paragraph_format.line_spacing = 1.35
    bullet = '•' if level == 0 else ('◦' if level == 1 else '–')
    r0 = p.add_run(f'{bullet}  ')
    r0.font.name = '맑은 고딕'
    r0._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
    r0.font.size = Pt(size)
    for text, bold in parts:
        r = p.add_run(text)
        r.font.name = '맑은 고딕'
        r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
        r.font.size = Pt(size)
        if bold:
            r.font.bold = True
    if refs:
        for ref_label, url in refs:
            r = p.add_run(' [')
            r.font.name = '맑은 고딕'
            r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
            r.font.size = Pt(size - 1)
            r.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
            add_hyperlink(p, url, ref_label, font_size=size - 1,
                          font_name='맑은 고딕', is_eastasia=True,
                          color_hex='0563C1')
            r = p.add_run(']')
            r.font.name = '맑은 고딕'
            r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
            r.font.size = Pt(size - 1)
            r.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
    return p
```

**사용 예**:
```python
B([
    ('해외 비중 39%', True),
    (' — 부동산금융 잔액 중 39%가 해외 부동산에 집중', False),
], refs=[('K1', SRC['K1'])])

# 하위 레벨 불릿
B('이건 하위 항목', level=1)
```

### P_with_refs — 단락 + 출처 참조

```python
def P_with_refs(parts, refs=None, size=10):
    """P() + 끝에 [label] 하이퍼링크 부착"""
    if isinstance(parts, str):
        parts = [(parts, False)]
    p = doc.add_paragraph()
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.4
    for text, bold in parts:
        r = p.add_run(text)
        r.font.name = '맑은 고딕'
        r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
        r.font.size = Pt(size)
        if bold:
            r.font.bold = True
    if refs:
        for ref_label, url in refs:
            r = p.add_run(' [')
            r.font.name = '맑은 고딕'
            r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
            r.font.size = Pt(size - 1)
            r.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
            add_hyperlink(p, url, ref_label, font_size=size - 1,
                          font_name='맑은 고딕', is_eastasia=True,
                          color_hex='0563C1')
            r = p.add_run(']')
            r.font.name = '맑은 고딕'
            r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
            r.font.size = Pt(size - 1)
            r.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
    return p
```

### Q — 원문 verbatim 인용 (이탤릭 + 들여쓰기 + 라벨 하이퍼링크)

원문 표현을 그대로 인용할 때 사용. 본문 안에서 *"~"* 형태로 풀어 쓰는 것보다, 들여쓰기 블록으로 분리해서 시각적 강조 + 출처 라벨 부착하는 패턴이 가독성·신뢰도 모두 우월.

```python
def Q(quote, source_label=None, source_url=None):
    """원문 verbatim 인용 — 들여쓰기 + 이탤릭 + 따옴표 + 라벨 링크"""
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.8)
    p.paragraph_format.right_indent = Cm(0.5)
    p.paragraph_format.space_before = Pt(4)
    p.paragraph_format.space_after = Pt(4)
    p.paragraph_format.line_spacing = 1.35
    r = p.add_run(f'"{quote}"')
    r.font.name = '맑은 고딕'
    r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
    r.font.size = Pt(9.5)
    r.font.italic = True
    r.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
    if source_label and source_url:
        r2 = p.add_run('  [')
        r2.font.size = Pt(8.5)
        r2.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
        add_hyperlink(p, source_url, source_label, font_size=8.5,
                      font_name='맑은 고딕', is_eastasia=True, color_hex='0563C1')
        r3 = p.add_run(']')
        r3.font.size = Pt(8.5)
        r3.font.color.rgb = RGBColor(0x60, 0x60, 0x60)
    return p
```

**사용 예**:
```python
Q('Our study measures competitiveness across four dimensions: business environment, provider capability, stability, and tax and regulation.',
  source_label='C4 p.7', source_url=SRC['C4'])
```

**원칙**:
- 영어 원문은 영어 그대로, 번역 없이. 한국어 풀이는 별도 P()로 뒤에 배치.
- 출처 라벨에 페이지 번호 함께 표기 권장 (예: `C4 p.7`) — 검증 추적성 ↑

### TABLE — 표 (Light Grid Accent 1 스타일)

```python
def TABLE(headers, rows):
    """headers: list of column titles
       rows: list of row lists"""
    t = doc.add_table(rows=1 + len(rows), cols=len(headers))
    t.style = 'Light Grid Accent 1'
    # 헤더 행
    hdr = t.rows[0]
    for i, h in enumerate(headers):
        cell = hdr.cells[i]
        cell.text = ''
        para = cell.paragraphs[0]
        r = para.add_run(h)
        r.font.name = '맑은 고딕'
        r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
        r.font.bold = True
        r.font.size = Pt(9.5)
    # 데이터 행
    for ri, row in enumerate(rows):
        for ci, val in enumerate(row):
            cell = t.rows[1 + ri].cells[ci]
            cell.text = ''
            para = cell.paragraphs[0]
            r = para.add_run(str(val))
            r.font.name = '맑은 고딕'
            r._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
            r.font.size = Pt(9.5)
    doc.add_paragraph().paragraph_format.space_after = Pt(4)
```

**사용 예**:
```python
TABLE(
    ['부문', '2020', '2021', '2022', '2023'],
    [
        ['영업순수익(총)', '21,751', '27,947', '15,374', '14,783'],
        ['IB', '3,310', '4,470', '3,775', '1,120'],
    ]
)
```

---

## 4. 하이퍼링크 임베드 함수

본문에 클릭 가능한 외부 URL 하이퍼링크를 추가하는 함수.

```python
def add_hyperlink(paragraph, url, text, font_size=9,
                  font_name='맑은 고딕', is_eastasia=True,
                  color_hex='0563C1', bold=False):
    """paragraph에 클릭 가능한 하이퍼링크 삽입"""
    part = paragraph.part
    r_id = part.relate_to(
        url,
        'http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink',
        is_external=True
    )
    hyperlink = OxmlElement('w:hyperlink')
    hyperlink.set(qn('r:id'), r_id)
    new_run = OxmlElement('w:r')
    rPr = OxmlElement('w:rPr')
    
    # 폰트
    rFonts = OxmlElement('w:rFonts')
    rFonts.set(qn('w:ascii'), font_name)
    rFonts.set(qn('w:hAnsi'), font_name)
    if is_eastasia:
        rFonts.set(qn('w:eastAsia'), font_name)
    rPr.append(rFonts)
    
    # 색상 (기본 Word 링크색 0563C1)
    color = OxmlElement('w:color')
    color.set(qn('w:val'), color_hex)
    rPr.append(color)
    
    # 밑줄
    u = OxmlElement('w:u')
    u.set(qn('w:val'), 'single')
    rPr.append(u)
    
    # 크기 (half-points)
    sz = OxmlElement('w:sz')
    sz.set(qn('w:val'), str(int(font_size * 2)))
    rPr.append(sz)
    szCs = OxmlElement('w:szCs')
    szCs.set(qn('w:val'), str(int(font_size * 2)))
    rPr.append(szCs)
    
    if bold:
        b = OxmlElement('w:b')
        rPr.append(b)
    
    new_run.append(rPr)
    t = OxmlElement('w:t')
    t.text = text
    t.set(qn('xml:space'), 'preserve')
    new_run.append(t)
    hyperlink.append(new_run)
    paragraph._p.append(hyperlink)
    return hyperlink
```

**핵심 포인트**:
- `part.relate_to(url, ..., is_external=True)` → docx의 `word/_rels/document.xml.rels`에 관계 등록
- 결과적으로 Word에서 Ctrl+클릭으로 URL 이동 가능
- `xml:space=preserve` — 텍스트 공백 유지

---

## 5. 출처 관리 패턴 — SRC dict + 코드 라벨

### SRC dict — URL 중앙 관리

```python
SRC = {
    # 회사 공식 자료 (M = Mirae 등)
    'M3': 'https://securities.miraeasset.com/.../global.jsp',
    'M4': 'https://securities.miraeasset.com/.../leadership.jsp',
    
    # 신용평가 1차 자료 (K = KIS)
    'K1': 'https://kisrating.com/.../rs20260121-8.pdf',
    'K2': 'https://kisrating.com/.../rs20250423-1.pdf',
    
    # 공인 1차 자료 (H = Historical/Heritage)
    'H2': 'https://www.kcmi.re.kr/publications/...',
    'H4': 'https://www.fsc.go.kr/.../79607...',
    'H5': 'https://www.kcmi.re.kr/...',
    
    # 매체 보도 (B = the Bell)
    'B1': 'https://www.thebell.co.kr/.../202211161857...',
    'B2': 'https://www.thebell.co.kr/.../202211290800...',
    
    # 외신·뉴스 (N = News)
    'N1': 'https://www.metroseoul.co.kr/...',
    'N2': 'https://m.fntimes.com/...',
    'N11': 'https://www.bloter.net/news/articleView.html?idxno=616052',
    'N14': 'https://www.consumernews.co.kr/...',
    # ...
}
```

### 코드 라벨 — 본문 인용 패턴

본문 인용은 `refs=[('CODE', SRC['CODE'])]` 형태로 전달:

```python
B([('해외 부동산 익스포저 약 1조 원', True),
   (' — 키움증권(약 3,810억) 대비 약 3배', False)],
  refs=[('N12', SRC['N12']), ('N11', SRC['N11'])])
```

→ 결과 본문: "해외 부동산 익스포저 약 1조 원 — 키움증권(약 3,810억) 대비 약 3배 [N12] [N11]"
→ [N12], [N11]은 각각 클릭 가능한 하이퍼링크

### 출처 섹션 — [코드] prefix 부착

본문의 [N12]를 보고 독자가 출처 섹션에서 즉시 찾을 수 있도록 동일 코드를 prefix로:

```python
H1('출처')

H2('증권사 B증권 공식 자료')
for label, url in [
    ('[M3] 증권사 B증권 — Global Business 사업 페이지', SRC['M3']),
    ('[M4] 증권사 B증권 — 경영정보 (리더십)', SRC['M4']),
]:
    # bullet + 라벨 하이퍼링크 + URL 하이퍼링크
    p = doc.add_paragraph()
    p.paragraph_format.left_indent = Cm(0.5)
    p.paragraph_format.first_line_indent = Cm(-0.4)
    p.paragraph_format.space_after = Pt(1)
    p.paragraph_format.line_spacing = 1.3
    rb = p.add_run('•  ')
    rb.font.name = '맑은 고딕'
    rb._element.rPr.rFonts.set(qn('w:eastAsia'), '맑은 고딕')
    rb.font.size = Pt(9)
    add_hyperlink(p, url, label, font_size=9, font_name='맑은 고딕',
                  is_eastasia=True, color_hex='0563C1')
    # URL 줄 (Consolas)
    p2 = doc.add_paragraph()
    p2.paragraph_format.left_indent = Cm(0.8)
    p2.paragraph_format.space_after = Pt(3)
    p2.paragraph_format.line_spacing = 1.2
    add_hyperlink(p2, url, url, font_size=8, font_name='Consolas',
                  is_eastasia=False, color_hex='404080')
```

### 카테고리 분류 — 신뢰도순

권위 있는 1차 자료부터 매체 보도순으로 배치:

1. 회사 공식 자료 (M)
2. 신용평가 1차 자료 (K)
3. 공인 1차 자료 — KCMI·금융위 등 (H 일부)
4. 매체 보도 — 더벨 (B), 외신·뉴스 (N), 산업·시계열 매체 (H 일부)

---

## 5-A. 내용 구성 원칙 — 양보단 질

컨설팅 분석 보고서의 내용 구성 시 다음 원칙을 기준으로 함:

### ① 양보단 질 — 적은 수의 확실한 출처

여러 약한 출처를 나열하기보다 1차 자료 1~2개로 핵심을 압축. 다수 회사 비교보다 **기준 사업자 1~2개 디테일 분석**이 시장 본질 파악에 효과적.

### ② 기준 사업자(anchor) 디테일 분석 패턴

1~2개 핵심 기업을 깊게 분석해 시장 구조의 본질을 도출:

```
- TradFi PB 시장 → Morgan Stanley WM + UBS GWM 2개사 디테일 분석
- 크립토 PB 시장 → Sygnum + AMINA 2개사 디테일 분석
- IB 재편 시장 → 증권사 B증권 단일 anchor 분석
```

**Anchor 분석에 포함되어야 할 4축**:
1. **사업 부문 구조** — 그룹 내 PB/IB 등 분석 대상 사업부의 위치·정의
2. **매출 라인 분해** — 정량 분해 (운용 수수료 vs 순이자수익 vs 거래 수익 등 % 비중)
3. **핵심 상품** — 시장 차별화 product 1~2개 디테일 (예: Lombard 대출, Sygnum Select)
4. **재무 비교** — 동일 시장 내 다른 anchor와 직접 비교 (FY 매출·비중·margin)

### ③ 매출 라인 분해 — 산업 일반화의 핵심

본 사례 회사의 매출을 라인별 % 분해 → 다수 anchor에서 동일 패턴 확인 → **산업 일반화**.

**예시 패턴** (TradFi PB):
| 라인 | MS WM 2025 | UBS GWM 2025 | 평균 비중 |
|---|---|---|---|
| 운용 자산 수수료 | 58.7% | 52.7% | 약 55% |
| 순이자수익 | 24.9% | 27.0% | 약 26% |
| 거래 수수료 | 14.4% | 20.1% | 약 17% |

→ 두 기준 사업자가 거의 동일한 매출 구성을 보임 → **"운용 수수료 55% + NII 26% + 거래 17%"가 전통 PB 수익원의 일반화 가능한 패턴**.

### ④ "한눈에 보기" Executive Summary — 한 흐름의 prose

표·불릿이 아닌 **읽히는 단락 형태**로 작성. 5~7개 단락으로 시장 정의 → 매출 구조 → 거점 → 시장 특수성 → 시사점 순으로 흐름.

피해야 할 패턴:
- 표를 그대로 옮긴 Executive Summary (정보 중복)
- 불릿 일색 (한 흐름의 narrative가 안 됨)
- 너무 짧은 한 줄 요약 (정보 부족)
- **★ 정크 문장 — 익썸은 *결론·핵심 수치·so-what*만.** 다음은 익썸에서 빼서 본문으로: ① 교차참조("…는 §X/1.1절 참조") ② 용어·개념 정의("KBMI는 핵심자본 기준 분류임") ③ 검증·작성 절차 캐비엇 ④ 동어반복·하나마나 한 문장. **리트머스: 익썸 각 문장이 '결론/판단/핵심 수치'인가** — 정의·참조·과정 설명이면 정크. *(실사례: 익썸의 'KBMI는 …분류이며 차이·이유는 1.1절에 정리함'은 정의+교차참조 정크 → 삭제하고 등급·수치 결론만.)*

권장 패턴:
- 단락 5~7개, 각 단락 2~4 문장
- 주요 수치는 bold 처리
- 마지막 단락에 한국 클라이언트(또는 클라이언트 관점)의 시사점 1줄
- 각 문장 = *결론·판단·핵심 수치·so-what* 중 하나(정의·참조·과정은 본문으로)

### ⑤ 표 아래 verbose 텍스트 피하기

표가 이미 정보를 전달했다면:
- 표 아래에 같은 정보를 prose로 풀이하지 않음
- 보충 설명이 필요하면 3~5개의 짧은 불렛으로 압축
- 1줄짜리 "→ 시사점" 정도면 충분한 경우 다수

---

## 6. 표준 문서 구조

보고서는 크게 **분석 보고서**(사례 심층 분석)와 **인사이트 전달용 보고서**(리서치 정리·전달) 두 유형으로 나뉘며, 구조가 다름.

### 6-A. 분석 보고서 — 권장 목차 (사례 심층 분석용)

```
[제목]
[작성자 / 날짜]

Executive Summary
  ├ 핵심 발견 1
  ├ 핵심 발견 2
  └ 핵심 발견 3

1. 배경
  1.1 시장 측면 — 산업 일반 구조·동향
  1.2 회사 측면 — 본 사례 회사의 직전 상태

2. WHY — 의사결정의 동기
  동기 ①
  동기 ②
  ...

3. WHAT — 변화 내용 (정밀 매핑)
  [표: Old vs New]
  주요 인사 / 구성 디테일

4. HOW — 실행 방식 + 후속 진화
  보고·리더십
  운영 메커니즘
  후속 단계
  한계 — 확인 못한 부분

5. (선택) 별도 분석
  특정 이슈 심층 분석

출처
  ├ 회사 공식 자료
  ├ 신용평가 1차 자료
  ├ 공인 1차 자료
  └ 주요 매체
```

### 6-B. 인사이트 전달용 보고서 — 권장 목차 (리서치 정리·전달용)

```
[제목]
[작성자 / 날짜]

Executive Summary
  ├ 한 흐름의 prose 4~6단락
  └ 마지막 단락에 시사점 1줄

1. 본문 (사실·이니셔티브 정리)
  1.1 ~ 1.N 각 항목
  (각 항목: 사실 → 출처 라벨 → 본문에 자연스럽게 녹인 분류·매핑 정보)

2. 타임라인 / 분포도 (선택)

3. 매핑 / 커버리지 표

4. 핵심 시사점
  4.1 시사점 ①
  4.2 시사점 ②

출처
```

### 두 유형의 결정적 차이 — "분석 범위·방법론" 섹션의 유무

| 항목 | 분석 보고서 (6-A) | 인사이트 전달용 보고서 (6-B) |
|---|---|---|
| "분석 범위 및 방법론" 섹션 | (선택) Executive Summary 직후 짧게 가능 | **절대 금지** — 본문 바로 진입 |
| 검증 방식·매핑 기준 등 메타 정보 | 부록 또는 별첨으로 | **본문에 노출 X** |
| 본문 톤 | 사례 추적·해설 톤 | 사실 정리·요점 전달 톤 |
| 표 활용 비중 | 중간 (인과 연결 위주) | 높음 (정보 압축 시각화) |

**원칙**: 인사이트 전달용 보고서의 독자는 사실과 시사점을 빠르게 확인하는 것이 목적이므로, 작성자의 분석 절차·검증 방법·매핑 기준 등을 본문에서 다루지 않음. "분석 범위 및 방법론" 같은 학술 톤 섹션은 인사이트 전달용 보고서에서 절대 사용하지 않음.

### 6-B의 하위 유형 — 리서치 정리 보고서 vs 인사이트 분석 보고서

6-B 인사이트 전달용 보고서는 다시 두 유형으로 나뉘며, 시사점·해석 섹션의 유무가 다름.

| 항목 | 리서치 정리 보고서 (6-B-1) | 인사이트 분석 보고서 (6-B-2) |
|---|---|---|
| 목적 | 1차 자료 기반 사실 정리 | 사실 + 시사점 도출 |
| "시사점·결론" 섹션 | **절대 금지** | (선택) 마지막 섹션으로 포함 가능 |
| 4축 매핑 결과 해석 | 사실 나열만 (해석 X) | 패턴·전략 방향 등 해석 가능 |
| 본문에 "추정", "가능성 큼", "~로 보임" 같은 추론 표현 | **금지** | 명시적 표시 어구와 함께 허용 |
| 대상 독자 | 작성자가 후속 분석을 위해 사실을 압축한 형태 | 의사결정자가 사실+해석을 동시에 받는 형태 |

**리서치 정리 보고서(6-B-1)의 시사점 섹션 금지 원칙**:
- "Phase 1·2 전반의 사업모델 커버리지 분포를 보면, 증권사 B은 ①·⑩·③ 3축에 가장 집중" 같은 패턴 해석 → 금지
- "해외 증권사 C는 기관·HNW 중심 사업 구조라 리테일 브로커리지 비중은 작은 것으로 추정" 같은 추론 → 금지
- "증권사 B이 리테일 채널·ETF에 비해 해외 증권사 C는 100% 기관 중심" 같은 비교 해석 → 금지
- 본문에 노출하지 않고, 작성자의 후속 분석 작업으로 별도 처리

**원칙**: 리서치 정리 보고서는 "1차 자료에 명시된 사실"만 담는 컨테이너. 해석·추정·전략 방향 등 작성자의 분석은 별도 보고서에서 다룸. 시사점 섹션을 둘지 여부는 보고서 유형(6-B-1 vs 6-B-2)을 사전에 결정해야 하며, 6-B-1이면 시사점 섹션 자체를 만들지 않음.

### 6-B 본문 패턴 — 산문 단락 금지, 라벨+값 불릿화

인사이트 전달용 보고서의 각 항목은 산문 단락(긴 한 문장)이 아닌 **라벨+값 불릿 형태**로 압축. 모바일·스캐닝 환경에서 한눈에 정보 추출 가능한 형태가 핵심.

**❌ 산문 단락 (피할 패턴)**:
```
2026.1.29 증권사 B증권이 1천억 원($71.5~72M) 규모 디지털 채권을 HSBC Orion DLT
플랫폼에서 발행 — 한국 금융기관 최초 블록체인 기반 채권 (HKD 325M + USD 30M,
사모). HSBC lead arranger, 증권사 B HK co-arranger, HSBC Orion은 홍콩 통화관리
국 CMU와 연동된 플랫폼 (홍콩 정부 digital green bond와 동일 인프라).
```
- 정보가 한 단락에 압축되어 무엇이 핵심인지 식별 어려움
- 모바일 가독성 낮음
- 비교·매핑 시 정보 추출 비용 큼

**⭕ 라벨+값 불릿 (권장 패턴)**:
```
• 발행 규모: 1천억 원 ($71.5~72M) / HKD 325M + USD 30M / 사모
• 지위: 한국 금융기관 최초 블록체인 기반 채권
• 인수단: HSBC lead arranger / 증권사 B HK co-arranger
• 플랫폼: HSBC Orion DLT, 홍콩 통화관리국 CMU 연동
• 효익: 자동화된 발행·상환·쿠폰 지급
• → 관련 사업모델: ① 토큰화 상품 발행/청약 · ② BS 언더라이팅
```
- 정보 카테고리(라벨)와 값(value)이 명확 구분
- 스캐닝으로 필요한 정보 즉시 추출 가능
- 표 형태 변환·다른 보고서 재인용 시 정보 손실 없음

**라벨 작명 원칙**:
- 1~3 어절의 명사구 (예: "인수 주체", "매도자", "코빗 위상", "잔여 8%")
- 한국어 비즈니스 표현 (영어 단어 회피)
- 값과 명확히 구분되도록 끝에 콜론(`:`)
- 라벨 자체는 bold 처리, 값은 일반 텍스트

**값(value) 작성 원칙**:
- 핵심 숫자·고유명사·키워드만 포함
- 연결사·조사·서술어 최소화 ("~로 인수되었다" → "/ 인수")
- 슬래시(`/`) 또는 가운뎃점(`·`)으로 동위 정보 구분
- 한 줄에 들어가는 길이로 압축

**각 항목 마지막 줄 — "→ 관련 사업모델" 패턴**:
- 사업모델 매핑이 필요한 보고서는 각 항목 마지막 줄에 화살표로 표시
- 라벨 형태 ("매핑:", "분류:") 회피 — 자연스러운 한국어 표현
- 예: `→ 관련 사업모델: ④ 거래소 · ⑦ 커스터디`

**컨텍스트 라벨 — 시점·권역·주체 등 비교 축 추가**:
- 글로벌·다지역 분석에서 각 이니셔티브가 어느 지역·시점·주체로 진행되는지 라벨로 추가
- 예: `권역: 미국 (US federal regulator OCC)` / `권역: 일본 (도쿄)` / `권역: 글로벌 (Jersey HQ · 두바이 사무소)`
- 시점은 H2 제목에 괄호로 (예: `H2('1.7 Laser Digital US National Trust Bank 신청 (2026.1.28)')`) 또는 본문 첫 불릿에 명시
- 컨텍스트 라벨이 추가되면 후속 비교·매핑 작업에서 정보 추출 비용 ↓

---

## 7. 어투 원칙 — 자연스러운 비즈니스 한국어

### 피해야 할 AI tic 표현

| 피할 표현 | 대체 |
|---|---|
| ~할 가능성이 시사된다 | ~할 가능성이 있다 / ~로 보인다 |
| ~로 풀이된다 | ~이다 / ~로 평가된다 |
| 이는 ~을 의미한다 | (앞 문장과 자연스럽게 연결) |
| ~할 수 있는 측면이 있다 | ~할 수 있다 |
| 본 사례에서는 / 본 분석은 | (생략 또는 "이 사례는") |
| → 분기별 분산이 아닌 ~ 크게 드러남 | 결산 시점 충격이 더 크게 드러남 |

### 피해야 할 Dramatic wording

극적·서사적 framing은 사실관계의 객관성을 훼손함. 컨설팅 보고서는 사실 단위로 서술.

| 피할 표현 | 대체 |
|---|---|
| 역사적 reversal / 사상 최초 #1 | 2025년 1위로 변동 / 200년 이상 유지되던 1위 자리 변동 |
| 200년 만에 처음으로 추월 | 200년 이상 유지되던 1위 자리가 2025년 변동 |
| 200년 PB 역사상 가장 큰 사건 | 거점 구조 변동의 전환점 |
| 변곡점 / 인플렉션 포인트 | 전환점 |
| 정반대 패턴 / 본질적 차이 | 대조적 구조 / 구조적 차이 |
| 압도적 우위 / 절대 강자 | 단일 거점 집중 / 시장 주도 사업자 |
| 누가 가고 왜 가는가 | 거점별 고객 구성과 선호 요인 (또는 해당 섹션 자체 삭제) |
| 결정적 / 가장 중요한 사실 | 핵심 / 주요 |

### 피해야 할 비정형·구어체 표현

컨설팅 보고서는 사실을 평이하게 서술. 구어체·감정 섞인 표현은 모두 제외.

| 피할 표현 | 대체 |
|---|---|
| ~까지 가서 / 굳이 ~에서 | (제거) / ~를 통해 / ~에서 |
| 보여준 / 보여주는 | 발신한 / 명시한 / 제시한 |
| 가서 보여준 메시지 | 발신한 메시지 / 제시한 전략 |
| 단순 ~이 아니라 / 단순 ~가 아닌 | ~보다는 / ~가 아닌 (감정 톤 제거) |
| ~의 끝판왕 / ~의 정수 | 핵심 / 대표 사례 |
| 막 / 그 / 이런 거 등 구어체 | (모두 제거) |
| 열다 / 두다 / (자금)굴리다 / 삼다 | 개설 / 보유 / 운용 / 활용 |
| (수요)몰리다 / 넘어오다 / (사례)나오다 | 집중 / 전환 / 발생·이뤄지다 |
| 묶다·묶음 / 얹히다 / 넓히다 / 만들다 / 쓰이다 | 결합 / 더해지다 / 확대 / 마련·도입 / 활용 |
| 한꺼번에 / 곧바로 / 이제 막 | 일괄 / 즉시 / 이제 |
| ~라서·~이라(구어 인과) | ~이므로 / ~이어서 |
| 문턱 / 발판 / 먹거리 / 대못 / 死제도 | 진입 장벽 / 기반 / 수익원 / 핵심 제약 / 미가동 |
| 물량 / 양방향 / 강자끼리 | 거래량·거래 규모 / 상호(양측) / 선도사 간 |
| 아무 ~나 ~한 게 아니라 / ~게 아니라 | 임의로 ~한 것이 아니라 / ~것이 아니라 |
| 마중물 / 빡셈 / 조이다 / 서빙 / 핏 | 초기 시장 조성 / 엄격 / 규제 강화 / 대상 영업 / 적합성 |

> **★ 위 표는 대표 스캔 목록이지 전부가 아니다.** 지목된 한두 단어만 고치면 같은 부류의 다른 구어가 남아 워딩 피드백이 반복된다 → *매 빌드 전 문서 전체를 이 부류(구어 동사·부사·비유 명사·구어 구문)로 훑어 정식 실무어로 치환*한다. 종결은 명사형(임/음/함/됨). 리드·본문의 **엠대시(—) 절잇기**는 번역투이니 콜론(':')이나 완결 문장으로. 제목은 *간결한 명사구*(완결 문장형 '~로 ~으로' 금지). 비동기 audit `consulting-register` lens가 이 사전을 스캔한다.

### 절대 금지 — 메타 코멘트 (보고서 본문에 노출 금지)

출처 검증·작성 절차는 작성자의 책임 영역이며, **최종 보고서 본문에는 어떤 형태로도 노출하지 않음**. 검증된 사실만 평이하게 기술. 다음 표현들은 본문에서 전면 제외:

| 금지 표현 | 사유 |
|---|---|
| "verbatim 확인" / "verbatim으로 명시됨" / "1차 자료 verbatim" | 검증 절차는 작성자의 내부 작업 — 보고서 독자에게 노출할 정보 아님 |
| "본 보고서는 ... 검증된 사실만으로 구성" | 보고서가 사실에 기반한다는 것은 default — 명시할 필요 없음 |
| "본 보고서는 ... 의도적으로 제외함" | 무엇을 제외했는지 보고서가 설명하지 않음 |
| "원문 verbatim:" / "본문 verbatim:" 같은 인용 도입부 | 단순히 "PR Newswire:" / "ADGM:" 등 출처명만 기재 |
| "추정·추론 시나리오 제외" | 추정이 제외되었다는 것 자체가 메타 — 보고서에는 검증된 사실만 등장하므로 명시 불필요 |
| "1차 자료에서 미언급" / "본 보고서 미반영" | 없는 정보를 굳이 언급하지 않음. 필요시 "구체 수치는 본 보고서에서 별도 기재하지 않음" 정도로 중립 처리 |
| 표 컬럼 헤더의 "(공지 사실)" / "(검증됨)" 같은 라벨 | 표 내용 자체가 사실이라는 것은 default |
| 헤더 텍스트의 "— 1차 자료 verbatim" / "— 본 보고서 분석" 같은 메타 라벨 | 헤더는 콘텐츠 주제만 명시 |
| "양 출처에서 동일 표현 등장" / "두 출처에 verbatim 명시" | 인용 옆에 출처 라벨이 있으면 충분 — 별도 설명 불요 |
| "**구분: 컨설턴트 기존 정리 / 새로 발견**" 같은 분류 라벨 | 어느 사실이 기존이고 어느 것이 신규인지의 분류는 작성자 내부 정보 — 본문에 노출 시 AI 틱·비프로페셔널 느낌 |
| "**사업모델 매핑: ①②**" 같은 라벨형 메타 표기 | "→ 관련 사업모델: ①②" 정도로 자연스러운 한국어 표현이 그나마 나음. 가능하면 본문 흐름에 자연스럽게 녹이거나 표로 분리 |
| "**(컨설팅 펌 장표 기재)** / **(McKinsey 보고서 인용)**" 같은 외부 분석 출처명 직접 노출 | 보고서 본문에 다른 컨설팅사·외부 분석 주체명을 직접 명시하지 않음. 필요시 "외부 분석 자료에서 별도 다루는 영역" 정도 중립 표현 |
| 본문에 노출된 "**Verbatim**" 영어 단어 (예: "원문 Verbatim 확인 결과") | 그 자체로 AI 틱. 한국어 "원문 확인된" / "본문에 명시된" 등으로 변환 |
| "**분석 범위 및 방법론**" 같은 학술 톤 섹션 라벨 | 인사이트 전달용 보고서에서는 부적절. 분석 절차를 본문에 노출하지 않고 본문 바로 진입 (6절 참조) |

**원칙**: 검증의 흔적과 작성 절차는 작성 과정에 남기고, 보고서 본문에는 검증의 결과(=확인된 사실)만 노출. 보고서를 받는 클라이언트는 작성자의 검증 절차·분류 라벨·외부 출처 참조 메타가 아닌 최종 사실관계와 분석을 필요로 함.

### 피해야 할 Decorative emoji

| 피할 emoji | 이유 / 대체 |
|---|---|
| 🇨🇭 🇭🇰 🇸🇬 🇺🇸 🇬🇧 (국기) | 프로페셔널 톤 훼손. 국가명 텍스트로 표기 |
| ⭐ ⭐⭐ ⭐⭐⭐ (중요도 별점) | "**핵심**", "**중요**" 같은 bold text로 대체 |
| ✅ ❌ (큰 체크/X) | 표 안에서 ○/✗/△ 같은 단순 기호로 대체 |
| 🔥 💡 📌 (감정/주목) | 모두 제거 |

> 단, 표 셀 안의 ○/✗/△ 같은 단순 status 기호는 OK. 본문 흐름 안의 emoji는 모두 제거.

### 영어/한국어 사용 규율

원문 verbatim 인용이 아닌 한 영어 단어 노출 최소화. 자주 등장하는 영어 단어의 한국어 대체 패턴:

| 영어 | 한국어 대체 |
|---|---|
| anchor (기업) | 기준 사업자 |
| hub | 거점 |
| framework | 분류체계 / 체계 / 평가 기준 |
| mix (수익) | 구성 |
| segment | 부문 / 분류 |
| dynamic | 흐름 변동 / 구조적 변화 |
| trajectory | 추이 |
| reversal | 순위 변동 |
| spectrum | 연속선 |

**예외 — 영어 그대로 유지하는 경우**:
- 원문 verbatim 인용 (Q() 블록 안의 영어)
- 고유명사 (회사명·제품명: Sygnum Select, Lombard Loan 등)
- 산업 표준 용어 중 한국어 대체가 어색한 경우 (NII, AUM, FINMA, OCC 등 — 단 첫 등장 시 한국어 풀이 병기)

### 권장 패턴

- **명사형 종결**: ~함 / ~임 / ~됨 (간결한 보고서 톤)
- **구체적 숫자 + 직접 인용**: "2023년 4분기 약 1,000억" / *"~"* 형태
- **인과를 직접 연결**: "A → B → C" 화살표 활용
- **표·불릿 적극 활용**: 긴 문장 대신 시각화
- **표 + 표 아래 verbose 텍스트 회피**: 표가 이미 정보를 전달하면 추가 텍스트는 불필요. 보충 설명이 필요하면 짧은 불렛(3~5개) 형태로 압축

### 제목 작명 — 추상어 회피

- ❌ "갈래", "궤적", "면모", "기조" (지나치게 문학적)
- ⭕ "요인", "차이", "추이", "동인" (실무적)

### "최초·유일·first" 한정자 표현 — 원문 그대로 유지, broad 변환 금지

원문에 "first natively tokenized bitcoin yield fund" 같이 **한정자(qualifier)가 붙은 first 표현**이 있을 때, 한정자를 떼고 "**업계 최초**", "**세계 최초**" 같은 broad 표현으로 변환하지 않음. 한정자는 그 사실이 어떤 범위에서 first인지를 정확히 한정하므로 누락하면 과장된 주장이 됨.

| 원문 표현 | 정확한 한국어 표기 | 금지 표기 (broad 변환) |
|---|---|---|
| "the first natively tokenized bitcoin yield fund" | "최초 natively tokenized 비트코인 yield 펀드" | ❌ "업계 최초 비트코인 yield 펀드" |
| "a first for any subsidiary of a Japanese financial institution" | "일본 금융기관 자회사 최초" | ❌ "일본 최초" / "세계 최초" |
| "Japan's only consortium-based blockchain platform" | "일본 유일 consortium 기반 블록체인 플랫폼" | ❌ "세계 유일 블록체인 플랫폼" |
| "first U.S.-registered tokenized money market fund" | "미국 등록 토큰화 MMF 최초" | ❌ "최초 토큰화 MMF" |

**점검 절차**:
1. 본문에 "최초", "유일", "세계 첫", "한국 첫" 등의 표현 grep
2. 각 표현이 원문 어떤 한정자 안에서 first인지 verbatim 대조
3. 한정자가 누락되어 broad해진 경우 → 원문 한정자 명시한 표현으로 복원

**원칙**: "first"는 항상 어떤 범위에서 first인지의 한정자를 동반함. 한정자를 떼면 부정확한 주장이 됨. 한정자 유지가 "최초" 표현의 신뢰도 기준.

---

## 8. 출처 정직성 원칙

### 양 < 질 — 출처의 본질

**다수의 약한 출처보다 소수의 1차 자료가 우월**. 한 보고서 안에 출처가 30개 들어가도, 그중 검증되지 않은 매체 인용이 다수면 신뢰도는 떨어짐. 10~15개의 Tier 0~1 자료로 구성된 보고서가 더 강함.

원칙:
- 검증 가능한 1차 자료 우선 (규제기관·SEC·기업 공시)
- 검증 불가능한 출처는 의도적으로 **삭제** (남겨두지 않음)
- 출처 섹션 도입부에 "본 보고서는 본문 직접 검증된 N개 1차·2차 자료만으로 구성. 검증 못한 다수 출처는 의도적으로 제외함" 명시

### 출처 신뢰도 Tier 체계

| Tier | 정의 | 예시 |
|---|---|---|
| **Tier 0** | 규제기관·중앙은행·국제기구 1차 자료 | OCC handbook, FINMA risk monitor, Fed press release |
| **Tier 1** | SEC·금감원 등 공식 의무 공시 + 글로벌 컨설팅 1차 보고서 | 10-K / 20-F / 6-K / 8-K, firm GWR, McKinsey survey |
| **Tier 2** | 기업 자체 공시 (보도자료·연차 보고서·홈페이지) + 산업 데이터 사업자 | 기업 IR 페이지, Capgemini 보고서 |
| **Tier 3** | 트레이드 매체 (finews, CoinDesk 등) — 이해당사자 인터뷰·재공시 | finews 비교 분석, Hubbis 인터뷰 |
| **Tier 4** | 검증 불가 매체·블로그 — **인용 시 별도 caveat 필수** | 신원 미상 블로그, 출처 추적 불가 매체 |

본문 인용 시 Tier 0~2를 우선. Tier 3는 1차 자료의 보조로만, Tier 4는 가급적 회피.

### 검증 단계

각 인용에 대해 다음을 순서대로 확인:

1. **URL 접근성** — HTTP 200 또는 브라우저 정상 접근 (Cloudflare 봇 차단은 별도 표기)
2. **PDF 원문 우선 검증** — 웹페이지 텍스트만 보지 말 것. 웹페이지 본문과 PDF 원문이 다른 경우 다수 (예: Deloitte IWMC Ranking 2024 — 웹페이지에는 4기준 없음, PDF p.7에 명시). **PDF가 제공되면 PDF를 1차 출처로**
3. **본문 인용 일치** — 원문에 해당 표현·수치가 실제 등장하는지 확인. 단순 키워드 매칭이 아닌 맥락 정합성까지
4. **페이지 번호 포함 라벨링** — 가능한 경우 출처 라벨에 `C4 p.7` 형태로 페이지 번호 함께 표기 → 검증 추적성 ↑
5. **시점 정합성** — KIS·신용평가 보고서의 시점(2023 vs 2024 등) / SEC 공시 fiscal year 명확히 구분

### 검증 자동화 패턴

```bash
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/121.0"
curl -s -A "$UA" -L --max-time 25 "$URL" | python3 -c "
import sys, re, html
raw = sys.stdin.read()
raw = re.sub(r'<style[^>]*>.*?</style>', ' ', raw, flags=re.DOTALL)
raw = re.sub(r'<script[^>]*>.*?</script>', ' ', raw, flags=re.DOTALL)
t = re.sub(r'<[^>]+>', ' ', raw)
t = html.unescape(t)
t = re.sub(r'\s+', ' ', t).strip()
# 인용한 핵심 표현·수치가 본문에 존재하는지 검증
for kw in ['수치1', '수치2', '인용표현']:
    print(f'{\"✓\" if kw in t else \"✗\"}  \"{kw}\"')
"
```

**PDF 본문 추출 패턴**:
```bash
# PDF 다운로드 후 텍스트 추출 → 핵심 인용문 grep
pdftotext -layout source.pdf source.txt
grep -in "인용 핵심구" source.txt
```

### 검증 실패 시 처리

웹페이지·PDF 본문에서 인용 표현이 확인되지 않으면:
- (a) **인용 자체 삭제** (가장 권장)
- (b) **다른 출처로 대체** (해당 사실이 다른 1차 자료에 있다면)
- (c) "본 보고서 분석 합성" 등으로 framing 전환 (사실 인용에서 분석 해석으로)

추론을 사실 인용으로 위장하는 것은 보고서 신뢰도 전체를 무너뜨림.

### 분석 합성 framework 솔직 acknowledgement

분류체계·평가 framework이 단일 1차 자료에 존재하지 않는 본 보고서의 분석 합성인 경우, **반드시 명시**:

**명시 패턴**:
```
> "X 분류는 공식 규제 카테고리가 부재하여 단일 1차 자료에 존재하지 않음.
>  아래 분류는 OCC R1 PB 정의(#T1)와 각 사업자의 라이선스·인가 사실
>  (#C7 / #C17 / #C8 / #C3 / #C11)을 종합한 본 보고서의 분석 framework임.
>  ④조건 같은 'X first-class' 기준은 본 분석 합성이며 별도 1차 출처 없음."
```

**분해 매트릭스 패턴** — 각 구성 요소의 출처 상태를 명시:

| 항목 | 출처 상태 |
|---|---|
| ① 조건 A | T1 (OCC handbook) 유도 가능 |
| ② 조건 B | T1 유도 가능 |
| ③ 조건 C | 부분 유도 |
| ④ 조건 D | **순수 본 분석** — 1차 출처 없음 |

→ 독자가 어느 부분이 사실 기반이고 어느 부분이 분석 합성인지 한눈에 구분 가능.

### 분석 vs 출처 — 명확한 구분

본문이 출처에 직접 명시된 사실인지, 본인의 분석적 해석인지를 명확히 구분:

- **출처 직접 인용**: Q() 블록 + 출처 라벨 (예: `Q("...", source_label='C4 p.7', source_url=...)`)
- **출처 derive 사실**: 본문 + `[LABEL]` 하이퍼링크
- **분석적 해석**: "→ 시장 분석 관점의 핵심 인사이트:" 같은 표시 어구로 시작, 출처 라벨 없음
- 추론을 사실로 위장하지 않음

### 외부 정보 차용 절대 금지 — 본 보고서 사용 출처 본문에 명시되지 않은 사실 금지

본 보고서가 사용하는 N개 출처 본문에 직접 명시되지 않은 사실을 외부(일반 상식·다른 검색 결과·LLM 학습 데이터)에서 가져와 본문에 끼워 넣지 않음. "알려진 사실"이거나 "맞다고 생각하는 정보"라도 본 출처 본문에 등장하지 않으면 보고서에 포함하지 않음.

**금지 케이스 예시**:
- N9·N10 본문에 등장하지 않는 "Boostry는 Nomura Holdings + Nomura Research Institute 합작" 정보 → 본 출처에 NRI 명시 없으면 "Nomura 그룹 자회사"로만 표현
- N9·N10 본문에 등장하지 않는 "Boostry + Nomura Securities + SMBC Nikko + 해외 증권사 F Securities 컨소시엄" 정보 → 본 출처에 SMBC·해외 증권사 F 명시 없으면 "Boostry + Nomura Securities 주도 컨소시엄"으로 축소
- N10 본문에 등장하지 않는 "Hitachi 그린본드 5년 100억엔" 수치 → 발행 결정 전 보도자료이므로 본 출처에 수치 미명시. 다른 매체 보도 정보 차용 금지

**검증 절차**:
1. 본문에 작성하려는 사실을 키워드로 본 출처 텍스트 전체에서 grep
2. 미발견 시 두 가지 옵션:
   - (a) 해당 표현을 본 출처에 명시된 더 약한 표현으로 축소 (예: "+NRI 합작" → "Nomura 그룹 자회사")
   - (b) 해당 사실을 본문에서 완전 제거

**원칙**: 일반 상식·LLM 학습 데이터는 본 보고서의 근거가 아님. 본 보고서의 근거는 오직 사용 출처 본문.

### 외부 검토 의견도 1차 자료로 재검증

Gemini·동료 검토 등 외부 검토 의견을 받아 정정 작업을 할 때도, **외부 검토자의 지적 자체를 1차 자료(원본 출처 본문)와 다시 대조**해 검증해야 함. 외부 검토자도 hallucination·잘못된 메모리·다른 문서와의 혼동을 발생시킬 수 있음.

**검증 절차**:
```bash
# 외부 검토가 "본문 X 위치에 Y 문구가 있다"고 주장하면 — 실제 본문에서 grep
grep -n "주장된 문구" build_docx.py
# 미발견 시: 외부 검토의 hallucination 가능성 — 정정 보류, 사용자에게 검증 결과 보고
```

**케이스 예시**: 해외 증권사 C 보고서에 대해 "Executive Summary 3번째 문단이 증권사 B 텍스트로 잘못 기재됨"이라는 외부 검토 지적을 받았으나, 실제 v1~v8 모든 버전에서 해당 문구 grep 결과 미발견 → 외부 검토의 hallucination으로 판정, 정정 보류. 외부 검토 지적이 어떤 형식으로 들어와도 1차 자료(본문) 직접 grep으로 검증한 후 행동.

**원칙**: 외부 검토 의견은 정정 작업의 단서일 뿐, 검토자의 단어를 그대로 정정 명령으로 받지 않음. 본문 + 1차 자료 출처 본문 양쪽 grep으로 검증 후 작업.

---

## 9. 빌드·검증·버전 관리

### 빌드

```bash
python3 build_docx.py
```

### 하이퍼링크 임베드 검증

```python
import zipfile, re

with zipfile.ZipFile('output.docx') as z:
    rels = z.read('word/_rels/document.xml.rels').decode()
    print('외부 하이퍼링크 수:', len(re.findall('TargetMode="External"', rels)))
    print('Unique URLs:', len(set(re.findall(r'Target="(https?://[^"]+)"', rels))))
```

### 본문 라벨↔URL 매칭 검증

```python
from docx import Document
from xml.etree import ElementTree as ET

doc = Document('output.docx')
# (구체적 검증 코드는 케이스별로)
```

### 버전 관리 규칙

- 파일명: `{보고서명}_v{N}.docx` (v1, v2, ...)
- 변경 단위로 버전 증가 (사소한 수정이라도)
- 이전 버전은 유지 (롤백 가능)
- 빌드 스크립트도 함께 보관

### 구조 변경 시 ripple grep — 빌드 사이클 최소화 원칙

문서·코드의 구조 변경(섹션 번호 재정렬, 라벨·식별자 rename, 출처 ID 재배치, 표/그림 번호 변경 등)을 할 때 단일 변경처럼 보여도 본문 곳곳에 cross-reference가 흩어져 있을 가능성이 큼. 빌드 사이클을 1회로 끝내려면 변경 직전에 다음 절차를 반드시 수행:

```bash
# 1) 변경 대상 패턴을 전체 파일에서 grep — H1/H2 정의 위치뿐 아니라 본문·표·인용까지
grep -n "2\.[1-8]" build_docx.py

# 2) 발견된 모든 위치 일괄 수정 (sed 또는 Edit 다중 호출)
sed -i '' "s/2\.\([1-8]\)/1.\1/g" build_docx.py

# 3) 변경 후 동일 패턴 재검색 — 잔존 여부 확인
grep -n "2\.[1-8]" build_docx.py   # 결과가 비어야 OK

# 4) 잔존 없음 확인 후 빌드 1회
python3 build_docx.py
```

**Why**: 섹션 번호만 H1/H2에서 재정렬하고 본문 표 셀의 cross-reference(`2.2 HSBC Orion · 2.4 Polygon` 등)를 빠뜨려서 추가 빌드 요청을 받은 사례가 있었음. 모바일에서 docx 다운로드·확인 비용이 크기 때문에 빌드 1회당 사용자 부담이 큼. 구조 변경 시 ripple effect를 먼저 잡고 한 번에 처리.

**적용 대상 작업 예시**:
- 섹션 번호 재정렬 (1→2, 2→3 등) → 표 셀·시사점·각주 등 모든 cross-reference 점검
- 출처 ID rename (예: `[#A26]` → `[#A50]`) → 본문 인용 전체 grep
- 헬퍼 함수 인자 변경 → 호출부 전체 점검
- 파일·디렉토리 rename → import·경로 참조 전체 점검

### 정렬·논리 순서 자동 재검토 — 단순 텍스트 변경의 함정

데이터 값(시점·금액·순위 등)이 변경되거나 새 항목이 추가되면 **그 데이터가 속한 정렬·논리 순서가 깨지지 않는지 반드시 함께 점검**. 텍스트만 바꾸는 것이 아니라, 변경이 미치는 의미적 정합성까지 검토해야 함.

**케이스 예시 — 시점 명시로 인한 정렬 순서 깨짐**:
- 변경 전: "2026 (시점 미상) 코빗 인수" — 정렬에서 가장 뒤
- 변경 후: "2026.2 코빗 인수" — 1.29와 4.2 사이에 와야 함
- **단순 텍스트만 바꾸고 위치를 유지하면 시간 순 정렬이 깨짐** — 독자가 혼란

**점검 절차**:
1. 변경 대상 데이터가 들어있는 정렬 컨텍스트 식별 — 타임라인·금액 순·우선순위 등
2. 변경 후 그 컨텍스트 안에서 새 값의 올바른 위치 계산
3. 위치 변경 필요 시 항목 자체를 이동 (텍스트만 바꾸는 것이 아님)
4. 이동 후 전체 정렬이 깨지지 않는지 처음~끝 재확인

**적용 대상 작업 예시**:
- 타임라인에 새 사건 추가 / 기존 사건 시점 정밀화 → 시간 순 위치 재조정
- 금액 기준 순위표에 항목 추가 / 금액 수정 → 순위 재정렬
- 우선순위 리스트에 항목 추가 / 가중치 변경 → 우선순위 재정렬
- 카테고리 분류 변경 → 같은 카테고리 그룹 안으로 이동

**원칙**: "글자만 바꾼다고 되는 일이 아님". 데이터 변경은 그 데이터가 속한 구조의 의미적 정합성까지 함께 책임지는 작업.

### 다중 인용 사실 — 본문/타임라인/매핑표/Executive Summary 일괄 ripple grep

같은 사실(연도·금액·고유명사·관계 등)이 보고서의 여러 위치에 동시 등장하는 것이 일반적. 본문 한 곳만 정정하면 **다른 위치에 옛 값이 잔존**해서 추가 빌드 사이클을 유발.

**다중 인용이 자주 발생하는 위치 5종** (정정 시 반드시 함께 grep):
1. **본문 (각 항목 라벨+값 불릿)** — 가장 자주 변경되는 1차 위치
2. **Executive Summary** — 본문 사실을 요약·인용
3. **타임라인** — 시점 정렬용 짧은 라벨로 재인용 (예: "2023.11.16 Hitachi 그린본드 100억엔")
4. **매핑/커버리지 표** — 근거 셀에 본문 항목 번호와 짧은 키워드 인용 (예: "1.9 Boostry consortium chain (Nomura + SMBC + 해외 증권사 F 등)")
5. **합계·집계 라인** — 표 하단의 "✓ N개 커버 + ✗ M개 미확인" 같은 수치

**점검 절차**:
```bash
# 정정 대상 키워드 (수치·고유명사·시점)를 grep으로 전체 위치 확보
grep -n "100억엔\|SMBC\|해외 증권사 F\|NRI" build_docx.py

# 발견된 모든 위치 일괄 수정
sed -i '' "s/100억엔//; s/Nomura + SMBC + 해외 증권사 F/Nomura 주도/" build_docx.py

# 잔존 검증
grep -n "100억엔\|SMBC\|해외 증권사 F\|NRI" build_docx.py
```

**Why**: 해외 증권사 C 보고서 작업에서 본문 1.9를 "5년 100억엔 → 발행"으로 정정한 후, 타임라인에 "100억엔" 잔존을 별도 외부 검토에서 발견하여 추가 빌드 (v5→v7) 요청. 같은 패턴이 NRI·SMBC·해외 증권사 F 등 다른 데이터에서도 반복되어 v5→v7→v8 3번의 빌드 사이클 누적. 모두 다중 인용 위치 한 번의 grep으로 잡을 수 있었음.

**원칙**: 데이터 정정 작업은 본문 한 곳이 아닌 **그 데이터의 모든 인용 컨텍스트를 동시에 정정하는 것**까지가 한 단위.

### 사용자에게 전달

```python
# SendUserFile (Claude Code 환경) — 첨부 + 캡션
```

캡션 작성 원칙:
- 변경된 핵심 사항을 간단히 (1줄)
- 버전 번호 명시
- 예: *"v10 — 코드 prefix 라벨링 복원 + 5.2/5.5/5.6 제목 정돈"*

---

## 부록 — 자주 사용하는 색상 코드

| 용도 | RGB / Hex |
|---|---|
| 제목 본문 | `RGBColor(0x1F, 0x1F, 0x1F)` (거의 검정) |
| 부제목 / 작성자 | `RGBColor(0x70, 0x70, 0x70)` (회색) |
| 하이퍼링크 (Word 기본) | `0563C1` |
| URL 본문 (Consolas) | `404080` (어두운 남색) |
| 인용 라벨 괄호 | `RGBColor(0x60, 0x60, 0x60)` |

---

## 10. 이번 세션 추가 규칙 (2026.6) — `research_verification_methodology_guide_v2.md` Part E 연계

### 10-1. 표 셀 출처도 클릭 하이퍼링크
표(`TABLE`)의 출처 열에 `C1·R2`처럼 라벨이 있으면, 평문이 아니라 **라벨별 클릭 하이퍼링크**로 렌더(셀 paragraph에 라벨마다 `add_hyperlink`, 구분자는 평문). 백틱·평문 URL은 클릭 불가.

### 10-2. 사용자 수동편집 보존 — diff 교차검증
빌드 산출물(docx)을 사용자가 Word에서 직접 편집한 뒤 재빌드할 때: 편집본 텍스트 추출 → 빌드 스크립트 출력과 **diff**로 모든 변경 식별(수동 스캔은 표 헤더 단축 등 미세 편집을 놓침) → 빌드 스크립트에 반영 → 재-diff로 의도된 변경만 남는지 확인. [E-7]

### 10-3. 실적 vs 전망 / 단위
- 성장률 등 표에 미래 연도(전망)가 섞이면 실적/추정/전망 구분 행으로 명시. [E-5]
- 통화·대규모 수치는 영어식 B/T, 영어 원문 magnitude와 자릿수 대조(억/조 변환 10배 오류 주의). docx는 `$` 그대로(수식 충돌 없음). [E-4]

### 10-4. §7 비전문 워딩 — 추가 금지어
§7 Dramatic/구어체 표에 다음 추가: **역설**(→ 비대칭 구조/괴리), **앞으로도 별볼일 없다/앞으로는 좋다**(→ 하방/상방 시나리오), **파격**(→ 비약·비선형), **폭발/폭발적**(→ 급증·급격), **윈드폴**(→ 유동성 전이/수혜), **깊고 두껍게 번다**(→ 수익성·자본이 최상위권). 문장 종결은 **명사형(~함/~임/~됨)으로 통일**.

### 10-5. Tier 라벨 제거 (클라이언트 산출물)
출처 섹션 헤더의 `(Tier 0~1)` 등 등급 분류·메타 라벨은 최종 보고서에서 제거(§7 원칙). 출처 코드(R1)는 인용용으로 유지. 작업 마스터(.md)에서만 Tier 유지. [E-9]
