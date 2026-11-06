---
name: king-writing
description: Write, revise, or critique any text (story, essay, LinkedIn post, cold email, outreach, landing copy, memo) using the method Stephen King teaches in On Writing - plain words, active verbs, no adverb crutches, situation-first stories, door-shut drafting, door-open revision, 2nd draft = 1st draft - 10%, Ideal Reader. Use when the user asks to "write like King", "make this hook", "tighten", "punch up", "story-ify", "rewrite", or to score a draft.
---

# King Writing System

Distilled from Stephen King, *On Writing: A Memoir of the Craft* (2000), all chapters read.
It encodes the **method and the voice** so any LLM can reproduce them. It does not contain the book's text.

## The two theses (everything else serves these)
1. Good writing = master the fundamentals (vocabulary, grammar, elements of style), then fill the upper levels of the toolbox.
2. You can't make a competent writer great, but hard work can turn a competent writer into a good one.
   Method: **read a lot, write a lot**. There is no shortcut.

## Pick a mode

| User wants | Mode | Load |
|---|---|---|
| New text from scratch | **DRAFT** (door shut) | `references/toolbox.md`, `references/story-engine.md`, `references/voice.md` |
| Improve an existing text | **REVISE** (door open) | `references/revision.md`, `references/toolbox.md` |
| A score or feedback only | **CRITIQUE** | `references/revision.md`, then run `scripts/king_lint.py` |
| Email, post, outreach, pitch | Add **BUSINESS** | `references/business.md` (maps every rule to short-form persuasion) |
| How to get better over time | **PRACTICE** | `references/practice.md` (reading/writing quotas, the room, the door) |
| Other LLM (no skills support) | **PORTABLE** | Paste `PORTABLE_PROMPT.md` as the system prompt |

Read only the files the mode needs.

## The pipeline (always this order)

```
1. SITUATION   What is the predicament? Write it as a "What if...?" in one line.  (story-engine.md)
2. IDEAL READER Name the one person this is for. Every choice answers: will I.R. care?
3. DRAFT       Door shut. Fast. First word that fits. No self-editing. Tell yourself the story.
4. REST        Long work: 6 weeks in a drawer. Short work: at least one break and a fresh read.
5. REVISE      Door open. Take out everything that is not the story. Cut >= 10%.
               Then: adverbs, passive, unclear pronouns, theme, back story, pace.
6. LINT        python3 scripts/king_lint.py draft.txt [--mode business] [--revised v2.txt]
7. RUBRIC      Score against evals/rubric.md. Ship at >= 90. 95+ = King-grade.
```

When you run the pipeline for a user, **show the two drafts** (door shut, then door open) and the word-count cut.
That makes the method visible, and the user learns by watching it, the way King learned from John Gould's pen.

## The 12 non-negotiables (the quick card)

1. **Use the first word that comes to mind**, if it fits and has color. Never the long word because the short one embarrasses you.
2. **Nouns and verbs carry the weight.** If you get lost in a tangle of clauses, go back to subject + verb.
3. **Active voice.** The actor is the subject: "The writer threw the rope," not "The rope was thrown."
4. **The adverb is not your friend.** If the context already shows *how*, the -ly word is redundant. Cut it.
5. **In dialogue, use "said".** No Swifties ("he said menacingly"). No pumped-up tags ("he grated").
6. **Fear is the root of bad writing.** Hedges, passives and adverbs are fear. Commit to the sentence.
7. **The paragraph is the basic unit.** Paragraphs are maps of intent. Let short ones breathe, and use a one-line paragraph for the beat.
8. **Situation over plot.** Put characters in a predicament and watch. Never force them to follow the outline.
9. **Description:** a few well-chosen details that stand for the rest. It starts in the writer's head and finishes in the reader's.
10. **Show, don't tell.** If you have to *tell* the reader the character is sad, you lose. Show the behavior.
11. **Tell the truth.** People talk the way they talk. Honesty forgives many stylistic sins; lying is the one fault you can't repair.
12. **2nd draft = 1st draft − 10%.** Take out everything that is not the story. Kill your darlings.

## Output contract (what you hand back)
- The text itself.
- For REVISE: a short **change log** in the "1408" style: a numbered list, each item gives the cut or change and its reason in a few words (see `references/revision.md` §Edit taxonomy).
- Lint score before and after, if a shell is available. If it isn't, run the rubric by hand and say so.
- Never pad the reply with praise of the user's draft. Say what's wrong, the way an Ideal Reader would.

## Honesty about limits
- King writes fiction. `business.md` is an **adaptation**: it applies his rules to short persuasion. The adaptation is not his.
- King breaks his own rules. He measures about 12 -ly adverbs per 1000 words and says he is "just another ordinary sinner".
  The rules are defaults. Break one only when the break does a job you can name. His example: keeping "heartily" because it shows mockery.
