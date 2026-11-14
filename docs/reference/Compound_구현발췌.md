# Compound 구현 발췌 — 문맥을 준비하고 학습 결과를 이어받기

[학습 경험](../06_Compound_학습경험.md)

기준: Compound **v0.8.0**, 2026-11-10, 로컬 앱 커밋 `39ed516482b34cee4c56991fc20dd10f3bb8fb2c`. 2026-11-14에 아래 두 함수가 해당 커밋과 같음을 확인했습니다. 이 저장소에는 앱 전체 소스 대신 문맥 전달·회화 조건을 설명하는 최소 부분만 공개합니다. 운영 주소·계정·실제 노트·환경 설정은 포함하지 않았습니다. 발췌는 의존 타입·보조 함수가 생략되어 단독 실행용 코드가 아닙니다. 세부 구현은 AI와 진행했습니다.

## 선택한 문장을 상위 설명과 함께 보냅니다

원본 경로는 앱 내부 `src/modules/news/ask.ts`입니다. `contextLines`는 선택한 줄에서 상위 불릿을 찾아 원래 순서대로 모으고, `buildPrompt`는 날짜·항목·선택 부분·질문과 함께 구성합니다. 코드에 쓰인 `whole`은 그 줄 전체를 선택했는지, `depth`는 설명의 깊이를 뜻합니다.

```typescript
export function contextLines(lines: Line[], picked: PickedLine[]): ContextLine[] {
  const pos = new Map(lines.map((l, i) => [l.id, i]));
  const up = (i: number) => {
    const out: number[] = [];
    for (let j = i - 1, d = lines[i].depth; j >= 0 && d > 1; j--) {
      if (lines[j].depth < d) {
        out.push(j);
        d = lines[j].depth;
      }
    }
    return out;
  };
  const whole = new Set(picked.filter((p) => p.whole).map((p) => p.id));
  const keep = new Set<number>();
  for (const p of picked) {
    const i = pos.get(p.id);
    if (i === undefined) continue;
    if (!p.whole) keep.add(i);
    for (const j of up(i)) if (!whole.has(lines[j].id)) keep.add(j);
  }
  for (const i of [...keep]) for (const j of up(i)) keep.add(j);
  const idx = [...keep].sort((a, b) => a - b);
  const top = Math.min(...idx.map((i) => lines[i].depth));
  return idx.map((i) => ({ level: lines[i].depth - top, text: plain(lines[i].text).replace(/\s+/g, ' ') }));
}

export function buildPrompt(t: AskTarget, question: string) {
  const pick = clipPick(wellFormed(t.text));
  const [, m, d] = t.noteDate.split('-').map(Number);
  const ctx = t.context.length ? `\n\n고른 부분이 나온 노트 내용:\n${t.context.map((c) => `${'  '.repeat(c.level)}- ${c.text}`).join('\n')}` : '';
  return wellFormed(`[세상 공부 ${m}/${d} 노트]\n항목: ${t.title}${t.meaning ? ` — ${t.meaning}` : ''}${ctx}\n\n고른 부분: "${pick}"\n\n질문: ${question.trim() || DEFAULT_QUESTION}`);
}
```

선택 부분에 이미 들어간 줄은 중복을 줄이지만, 문맥에 실은 줄의 상위 설명은 다시 확보합니다. 선택 글이 길어 앞부분만 남는 경우에도 하위 설명이 다른 항목 아래에 붙은 것처럼 읽히지 않도록 한 처리입니다. 이 코드는 질문용 문맥 구성을 보여주며 ChatGPT 답변 품질을 측정한 결과는 아닙니다.

## 회화의 목표·교정 범위·돌려받을 형식을 정합니다

원본 경로는 앱 내부 `src/modules/english/handoff.ts`입니다. 상황·두 사람의 역할·미션·연습 표현·지난 교정을 전달하고, 결과를 실제 발언·교정·복습할 표현으로 받습니다. 텍스트만 보면서 발음을 평가하지 않는 조건도 포함합니다.

```typescript
export function buildTutorPrompt({ task, targets, saved, watch, mode }: TutorInput) {
  return `You are my English speaking partner for a role-play. I am a Korean adult at about CEFR B2.

Situation: ${task.sceneEn}
My role: ${task.myRole}
Your role: ${task.partnerRole}
My 3 missions:
${task.missions.map((m, i) => `${i + 1}. ${m.en}`).join('\n')}
${list('Target expressions (give me natural chances to use them, do not use them for me)', targets)}${list('Also bring back these expressions I saved earlier', saved)}${list('This time please watch for', watch)}
Rules:
- Speak as your role and start now with one short opening line.
- Stay at B2 level. Use at most 2 sentences per turn and ask one question at a time.
- Do not do my missions for me. When all 3 are done, wrap up naturally.
- ${mode === 'instant' ? 'After each of my turns, give one short correction if I need it, then continue.' : 'Do not correct me during the conversation. Only if you cannot understand me, say what you think I mean and ask if that is right.'}
- If I say "hint", give me a sentence starter. If I say "hint" again, give a full sample reply. "slower" and "repeat" always work.
- When I say or type "report", stop the role-play and write the report below as text in one message. Use only what I actually said and do not invent sentences. Do not score pronunciation, because you only see a transcript.

Report format (keep the markers exactly, one item per line, columns separated by |):
###REPORT v1
[MISSIONS]
- 1 | done or not | short quote of what I said
[CORRECTIONS]
- what I said | better version | reason in Korean, one line | grammar, vocabulary, word order or naturalness
[BETTER]
- correct but unnatural sentence I said | more natural version
[SAVE]
- expression | Korean meaning | example sentence
[FOCUS]
one thing to focus on next time, in Korean
###END

For [CORRECTIONS] give 3 to 5 items, the ones that hurt understanding most first, then the most frequent. For [SAVE] give 5 useful expressions.`;
}
```

앱은 결과를 붙여 넣어 교정과 표현을 저장하는 역할을 맡고, 회화는 ChatGPT에서 진행합니다. 모든 세부 프롬프트를 제가 독자적으로 발명했다는 의미는 아닙니다. 제품의 학습 흐름과 결과 기준을 정하고 AI의 구현·검토를 통해 구체화한 자산입니다.

## 공개 화면의 출처와 범위

[읽기 화면](../images/compound_reading.png)은 v0.8.0의 읽던 위치 복원 설명용 캡처입니다. 원본은 해당 버전의 `v080-explainer` 화면 검토 자료이고, 캡처 스크립트의 가상 노트와 모의 API 응답으로 촬영됐습니다. 390×740 CSS 픽셀, 배율 2의 Chromium 화면입니다. 실제 회원 기록이나 실제 뉴스레터 본문이 아닙니다.

이번 문서 개편에서는 기존 캡처와 생성 스크립트·결과 기록을 대조하고 이미지를 직접 확인했습니다. 원래의 화면 시험을 새로 실행하거나 아이폰에서 재검증한 결과로 표시하지 않습니다. 0.9 작업 중 자료는 포함하지 않았습니다.
