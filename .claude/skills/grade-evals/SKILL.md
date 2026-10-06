---
name: grade-evals
description: Grade COMP 440 evaluation responses (the shared facts, user_says_something_wrong and emotional_social families) inside Claude Code, using eval-grader subagents running Opus or Sonnet as the grader, and check the grader with an audit (eval-auditor subagents) and a human review queue. Keeps everything in run folders the viewer shows. No API key needed; it uses the Claude Code plan. Use when asked to "grade the evaluations", "grade these responses", "make an evaluation run", "run the grader", "/grade-evals", "audit the grades", "build a review queue", "re-grade with my rubric notes", "open the viewer", or to compare Opus and Sonnet as graders.
---

# Grade evaluation responses with Claude Code subagents

The `eval-grader` subagent (`.claude/agents/eval-grader.md`) grades one batch file; its
definition is the one place the grading rules are written. The `eval-auditor` subagent
(`.claude/agents/eval-auditor.md`, Opus at medium effort) re-grades an audit batch by reading
and following that same file. `evals/shared/grade_tools.py` does everything else. Run every
command from the repo root.

Everything lives in a **run folder** (format: `evals/runs/README.md`):
`evals/runs/<YYYY-MM-DD>-<slug>/` for students, `planning/runs/...` for runs that include
held-back items. The viewer (`evals/app/serve.py`) shows run folders.

## 1. Settle the run

- **Existing run**: if the user names a run (or there is one obvious run in `evals/runs/`),
  use it. Read its `run.json`.
- **New run**: from the responses files (the `eval-*.jsonl` files, one line per item: `id`,
  `response`, `model`; all evaluated models together so batches mix them). Choose a short
  lowercase slug and write a plain-language title and description (which models, how each was
  trained, what to look for), then:

  ```
  python3 evals/shared/grade_tools.py init-run --root evals/runs --name SLUG --title "..." --description "..." --responses FILE [...] --items evals/shared [--items MORE]
  ```

  Item folders: always `evals/shared`; add every other folder whose ids appear (a student's
  own families; for the instructor, `planning/hidden-evals`, and then `--root planning/runs`,
  since held-back items never go under `evals/` or `.claude/`). Afterwards fill in each
  model's `label`, `description` and `charter` in `run.json` from what the user tells you.
- **Grader**: `sonnet` unless the user says otherwise. Grader id `<model>-<effort>`, for
  example `sonnet-low` (effort comes from the agent definition).

## 2. Prepare the batches

```
python3 evals/shared/grade_tools.py prepare --run RUNDIR --grader sonnet-low --grader-model sonnet
```

It prints one row per batch (number, family, size, input path, output path) and the exact
prompt for each grader. If it says the folder was already prepared the same way, carry on:
finished verdicts are kept (`status --run RUNDIR --grader ID` shows which batches still need
a grader). If it refuses because the folder holds a different run, ask the user before adding
`--force`. Pass on any warning it prints.

(The older form without run folders still works:
`prepare RESPONSES.jsonl [...] --items evals/shared --out-dir OUT --grader-model MODEL`, then
`merge OUT`.)

## 3. Grade: one subagent per batch

For each batch row (skip rows `status` reports as complete), call the Agent tool with:

- `subagent_type`: `"eval-grader"` (or `"eval-auditor"` for an audit; `prepare` and
  `audit-sample` print which)
- `model`: `"opus"` or `"sonnet"`, the grader model `prepare` printed
- `description`: `"Grade batch NN"`
- `prompt`: exactly the lines `prepare` printed, with that row's paths, and nothing else:

  ```
  Input batch: <input path from the table>
  Output file: <output path from the table>
  ```

  plus, only when `prepare` printed one, the line `Rubric notes: <path>`.

Never add anything that could bias the grade: no model names, no responses-file names, no
expectations about which model is better, and for an auditor never the first grader's
verdicts.

Launch at most 8 graders in one message; agents in one message run in parallel. Wait for all
of them to finish (their results, or their completion notifications if they run in the
background) before launching the next group. Do not poll with sleep or status loops. A grader
that fails or writes bad lines needs no immediate retry: step 4 catches it.

## 4. Merge, then redo the gaps (at most twice)

```
python3 evals/shared/grade_tools.py merge --run RUNDIR --grader ID
```

If its output ends with `REDO NEEDED`, it lists one row per batch with a **redo input** and a
**redo output** path. Spawn one subagent of the same kind per row exactly as in step 3, with
the redo input as `Input batch` and the redo output as `Output file`, then run merge again. It
picks up the redo outputs by their names. Do this at most twice; if gaps remain, stop and tell
the user which batches and how many lines are still missing. Merge also records the grader in
`grader.json` and moves the run's `status` and `next_steps` forward.

## 5. Report

Show the user the summary `merge` prints (also saved in `grades/ID/summary.txt`), then tell
them to open the viewer (step 8). For two graders on the same run:

```
python3 evals/shared/grade_tools.py compare --run RUNDIR --graders A B [--hand HAND.jsonl --hand-model MODEL [--hand-responses FILE]]
```

Report, per family, how often the graders agree and Cohen's kappa, whether the order of the
evaluated models changes between graders, and agreement with hand grades if given. The
viewer's Judges page shows the same for any two judges.

## 6. Audit and review ("audit run X with Opus at medium effort")

1. Sample and prepare blind audit batches from the base grader's verdicts:

   ```
   python3 evals/shared/grade_tools.py audit-sample --run RUNDIR --from sonnet-low --grader opus-medium-audit --n 60
   ```

   It picks about 60 verdicts, equal across families and evaluated models, mostly likely-hard
   ones plus some at random, and writes batches in the usual format with no verdicts in them.
   Add `--visible-only` if held-back items should stay out.
2. Spawn one **`eval-auditor`** subagent per audit batch, `model: "opus"`, with exactly the
   prompt lines it printed (input and output paths only). Never tell it the first grader's
   verdicts.
3. `merge --run RUNDIR --grader opus-medium-audit` (redo gaps as in step 4). The run becomes
   `audited`.
4. Build the review queue:

   ```
   python3 evals/shared/grade_tools.py review-build --run RUNDIR --judges sonnet-low opus-medium-audit --controls 5
   ```

5. Tell the user how many disagreements there are and to open the viewer's **Review** page
   (step 8): they grade each item themselves first, then decide between "Grader 1" and
   "Grader 2". Do not tell them which grader is which.

## 7. Re-grade with rubric notes ("re-grade ... using my rubric notes in FILE")

After the user has reviewed, they may write one or two rubric clarifications in a notes file.
Re-grade the reviewed items with the same grader model and the notes:

```
python3 evals/shared/grade_tools.py prepare --run RUNDIR --grader sonnet-low-notes --grader-model sonnet --rubric-notes FILE --subset-from queue-sonnet-low-vs-opus-medium-audit
```

Then steps 3 and 4 (each prompt includes the `Rubric notes:` line `prepare` printed). The
viewer's Judges page then shows agreement with the user's review decisions before and after the
notes. Default policy (it may change): rubric notes are for this exercise and the student's
own two families; official grades on the three shared families stay on the shared rubric.

## 8. Open the viewer

```
python3 evals/app/serve.py --open
```

Instructor runs with held-back items: `python3 evals/app/serve.py --open --instructor --runs planning/runs`.
It runs until stopped (Ctrl-C), so start it in the background if you start it yourself, and
tell the user the address (`http://127.0.0.1:8440/`).

## Notes to tell the user, in plain words

- Grading runs on the user's Claude Code plan and counts against its usage limits; it costs no
  API dollars and needs no API key. Opus uses up more of the plan than Sonnet.
- Held-back items stay in `planning/` paths: when the responses include held-back ids, keep the
  run under `planning/runs/` (the tools refuse `evals/` and `.claude/`), and never copy batch
  files, grades, queues or disagreements into `evals/` or `.claude/`.
- If the Agent tool does not offer `eval-grader` or `eval-auditor`, the session probably
  started before the agent file existed: ask the user to restart Claude Code, then continue
  from step 3 (prepare keeps its output).
