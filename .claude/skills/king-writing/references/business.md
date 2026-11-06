# Business adaptation: King's method for emails, posts, outreach and pitches

**Status: adaptation.** King teaches fiction. This file maps his rules to short persuasive writing. The mapping is mine, not his.

## 1. Rule mapping

| King (fiction) | Business translation |
|---|---|
| Situation first, then characters | **Their situation first.** One concrete fact about *them* (a role open 63 days, a round just raised, a launch that went quiet). The reader is the protagonist. You are a supporting character. |
| What-if seed | **What if** their problem got solved this month, by you? Write that line privately. Never paste it in. |
| The blue 8 | **One exact detail** they must receive: a number, a name, a date. Everything else can stay rough. |
| A few well-chosen details | **One proof point.** A metric, a named result, or a list of 3 of *their* buyers. Not your whole CV. |
| Show, don't tell | Don't write "I'm a hard worker." Show the thing you built, with a number. |
| Ideal Reader | **One named person.** Write to Maya, not "founders". Ask: would Maya stop reading here? |
| Use "said" / no adverbs | No "truly", "really", "quickly", "super excited". Use verbs and numbers. |
| Fear = bad writing | "I hope this finds you well", "I just wanted to", "if you have a moment": all fear. Cut them. |
| Active voice | "I built", "You get", "We booked 14 meetings". Never "meetings were booked". |
| Paragraph as map of intent | Mobile: 1-3 lines per paragraph. White space signals an easy read. A one-line paragraph carries the hook. |
| Back story in the back | Your background goes in one line, or a link. Lead with *their* story. |
| Research is spice | Use research to make one sentence true and specific, not to show off. |
| 2nd draft = 1st − 10% | For short-form, **cut 30-50%**. A 180-word first draft becomes 90-110 words. |
| Door shut, door open | Write 5 versions fast, rest an hour, then cut. Show the best to one trusted reader. |
| Kill your darlings | Your cleverest line is the first suspect. |
| Tell the truth | Every claim must be verifiable. One false detail kills trust, and that fault can't be repaired. |
| Ending on an image or short line | End on **one small, easy ask**: "Worth 15 minutes Thursday?" |

## 2. Shapes

### Cold email (90-130 words)
```
LINE 1    Their situation, one concrete fact (the blue 8). One-line paragraph.
LINE 2-3  The turn: what you built or did that meets that situation, with ONE number.
LINE 4-5  What happens in week 1 if they say yes (specific, about them).
LINE 6    Trust or risk-reversal (time zone, paid trial, references).
LINE 7    Small ask, as a question.
```

### LinkedIn post / story post (120-250 words)
```
HOOK      A scene or a number. No "I'm excited to share".
SCENE     Anecdote before principle: one moment, concrete objects, a little dialogue if real.
TURN      What changed, or what you noticed (the fossil).
LESSON    One line. No moral sermon. Resonance over message.
CLOSER    A short line that echoes the hook (the callback).
```

### Follow-up
Add **one new fact** each time ("Since my last note, 3 of your competitors posted SDR roles"). Never write "just bumping this".

## 3. Worked example (door shut → door open)
Scores from `scripts/king_lint.py --mode business`.

**Door shut, 181 words, score 48:**
> Hi Maya, I hope this email finds you well. I am reaching out because I noticed that the Founding GTM role at Acme has
> been open for some time, and I wanted to quickly introduce myself as I believe I could be a really strong fit... I have
> built a robust data pipeline that is utilized to identify companies... In week 1 at Acme I would leverage this system in
> order to build a list... Would you be open to a quick 15-minute call sometime this week to discuss further?

**Door open, 105 words (−42%), score 100:**
> Hi Maya,
>
> Your Founding GTM role has been open 63 days.
>
> I built the thing that role is supposed to build. My pipeline reads 92K SEC filings, 55K job boards and 17 funding
> feeds, and flags companies the week they need GTM help. Yours showed up on a Tuesday.
>
> Week one at Acme, I'd pull the 40 fintech teams that raised a seed round this quarter (your buyers), write the first
> sequence, and send it. You judge it on booked meetings.
>
> I'm in India and work your hours, 9am-2pm ET. If a hire feels early, start me on a paid three-week trial.
>
> Worth 15 minutes Thursday?
>
> Abhinay

**Change log (1408 style):**
1. E3/E4: deleted the greeting ritual and the "reaching out" preamble. That was fear.
2. The blue 8: "for some time" became "63 days". One exact number carries the hook.
3. E7: robust / utilized / leverage / in order to became plain verbs. The passive "is utilized" became "My pipeline reads".
4. Show, don't tell: "I believe I could be a strong fit" became "I built the thing that role is supposed to build."
5. V1 punch line: "Yours showed up on a Tuesday." One short sentence after a long one. *(Use only if it's true; replace every fact with a real one.)*
6. Specifics about *them*: "a list of your ideal customers" became "40 fintech teams that raised a seed round this quarter".
7. The ask got smaller and more concrete: "sometime this week to discuss further" became "Thursday".

## 4. Business red-flag list (instant rewrite)
hope this finds you well · reaching out · just following up · quick question · I wanted to · I believe I could ·
passionate about · leverage · synergy · robust · seamless · cutting-edge · game-changer · solution(s) ·
in order to · at the end of the day · circle back · touch base · excited to announce · humbled ·
any adverb ending in -ly that a number could replace.
