---
name: roast-rematch
description: >-
  Re-roasts the repo after fixes and shows what changed: the score delta per judge, the overall
  before and after, which charges were dropped, which are new, and a regenerated card that reads
  "5.1 -> 6.8". Reads the previous roast from roast/history/ and the new one from roast/roast.json.
  Use when the user says "rematch", "roast it again", "re-roast after my fixes", "did my score go
  up", "show me before and after", "how much did the fixes help", or "make a before and after card".
argument-hint: "[--gentle|--savage] [--since-day-one] [previous roast.json]"
---

# roast-rematch: the comeback episode

The best part of a roast is the comeback. This skill re-convenes the same panel with the same
rubric on the fixed repo, then lines the two verdicts up side by side so the improvement is
measured, not claimed. The before and after card is the one people post.

## Inputs

- The previous roast: the path the user gives, else the newest file in `roast/history/`. The
  `--since-day-one` option selects the oldest archive instead. If there is no archive, use the
  current `roast/roast.json` only when it will be archived before the new roast is written.
- Mode flag: use the previous roast's `mode` unless the user asks for another. Changing tone
  between rounds is fine; changing the rubric is not.

## Outputs

- `roast/roast.json`, `roast/ROAST.md`: the new roast (the old one is archived to
  `roast/history/` by verdict.py).
- `roast/REMATCH.md`: the delta report.
- `roast/card.json` and `roast/card.svg`: the card with before and after scores.

## Steps

1. **Find the baseline.** Locate the previous roast as above. Ordinary rematches use the most
   recent archived `roast.json`. Use `--since-day-one` when the user asks for progress since the
   first roast. Read its `date`, `mode`, `overall`
   and per-judge scores. If there is no previous roast, say a rematch needs a first match and run
   `roast-repo` instead.

2. **Note what changed since.** `git log --oneline --since=<previous date>` and
   `git diff --stat` for uncommitted work. If nothing changed, say so and ask whether to re-roast
   anyway (scores may still move a little because judges read fresh).

3. **Run the full roast again.** Follow `roast-repo` steps 2 to 6 exactly (facts, the six judges in
   parallel, save panel files, spot-check, `verdict.py`). Give each judge one extra line in its
   brief: the previous charges against it (line and evidence), with the instruction "check each
   old charge: if it is fixed, do not repeat it; if it still stands, keep it with fresh evidence".
   `verdict.py` archives the old roast.json automatically.

4. **Compare.**
   ```
   python3 "${CLAUDE_SKILL_DIR}/delta.py" --before <previous roast.json> --after roast/roast.json \
     --out roast/REMATCH.md --card-out roast/card.json
   ```
   Use the archived path in `roast/history/` for `--before` if the baseline was the old
   `roast/roast.json`.

5. **Regenerate the card** with before and after values:
   ```
   python3 "${CLAUDE_SKILL_DIR}/../roast-card/card.py" --in roast/card.json --out roast/card.svg --badge-path roast/badge.svg
   python3 "${CLAUDE_SKILL_DIR}/../roast-card/badge.py" --in roast/card.json --out roast/badge.svg
   ```

6. **Review the dropped charges by hand.** delta.py matches charges by evidence text, so a charge
   whose line moved appears as both dropped and new. For each dropped charge, open the old location
   and confirm the problem is actually gone. List any that only moved in REMATCH.md under
   "Moved, not fixed".

7. **Deliver.** In chat: overall before and after with the tier change, a table of judges with
   deltas, the most improved judge, charges dropped and new, and the card path. If a judge's score
   went down, say why in one line with evidence. Celebrate real gains; do not inflate small ones.

## Decision rules

- Same panel, same rubric, same evidence rules as the first roast. Otherwise the delta is noise.
- A judge missing from one side shows as "absent" and is excluded from the overall comparison
  narrative; say so.
- A score change of 0.5 or less on a judge with no related code changes is noise; call it that.

## What NOT to do

- Do not edit the previous roast or reuse its panel files; every rematch is a fresh read.
- Do not tell judges the old scores or ask them to "go easier". They get old charges, not old scores.
- Do not claim a fix worked because a charge disappeared; confirm it in the code.
- Do not commit, push or post the card. Hand the files to the user.

## Hand-off

- Still charges standing -> `roast-fix` for the next round.
- Share the comeback -> `roast-card` if the user wants to tweak quotes before posting.
