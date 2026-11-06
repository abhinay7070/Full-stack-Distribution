# King-Grade Rubric: how "95% replication" is measured

"95%" means a text scores **≥ 95/100** on this rubric. It doesn't mean copying King's words.
Part A is mechanical, and `scripts/king_lint.py` scores it. Part B needs judgment, from the LLM or a human. **Final = 0.4 × A + 0.6 × B.**

## Part A: mechanics (0-100, from king_lint.py)
Adverbs 14 · passive 12 · plain vocabulary 12 · rhythm 12 · dialogue tags 8 · fresh images 10 · short words 10 ·
paragraph shape 8 · no hedges 8 · sounds like talk 6.

## Part B: craft (score each 0-10, sum × 1.0 = 0-100)

| # | Criterion | 10 = | 0 = |
|---|---|---|---|
| B1 | **Situation / hook** | Opens in a concrete predicament or fact about the reader | Opens with throat-clearing or a summary |
| B2 | **The blue 8** | One exact, load-bearing detail lands | Everything is vague or everything is exact |
| B3 | **Show, don't tell** | Behavior, objects and numbers do the work | Feelings and qualities are stated |
| B4 | **Truth** | Every line rings honest; dialogue sounds like real people | A false note, a cliché line, or a claim that can't be verified |
| B5 | **Description economy** | 2-4 chosen details, then back to story | Inventory, or none at all |
| B6 | **Ideal Reader fit** | Clearly written to one person; nothing they'd skip | Generic audience; boring parts left in |
| B7 | **Cut discipline** | Nothing removable; obvious ≥10% cut from a draft | Puffy: repetition, stage directions, overexplaining |
| B8 | **Voice** | Talk-like rhythm, a short punch line, an aside, concrete names | Flat, uniform, corporate, or machine-sounding |
| B9 | **Resonance / theme** | One idea lingers; no sermon | A moral is stated, or nothing is felt |
| B10 | **Ending** | A short line, an image, a callback, or a small ask | Summary, "in conclusion", or a list of asks |

## Grading bands

| Final | Verdict |
|---|---|
| 95-100 | King-grade. Ship it. |
| 90-94 | Ship it, and note one fix for next time |
| 75-89 | Revise: run another door-open pass |
| < 75 | Redraft from the situation |

## Test prompts (run these to check the skill holds 95%)
1. "Write a 300-word scene: a night-shift nurse finds a patient's chart has her own name on it." (fiction, door shut → door open)
2. "Rewrite this cold email in the King style" + any 180-word corporate email. (business; expect −30-50% words)
3. "Critique this paragraph" + a paragraph salted with 5 adverbs, 3 passives, 2 clichés and a Swiftie. (Expect all 11 caught, plus fixes.)
4. "Write a LinkedIn post about the first customer who said no." (story post shape: scene, turn, lesson, callback)
5. "Describe a crowded diner in 80 words." (expect ≤ 4 details, ≥ 1 non-visual sense, no cliché)

For each test, record: lint score A, craft score B, final, and the words cut. Pass = final ≥ 95 on at least 4 of the 5.
