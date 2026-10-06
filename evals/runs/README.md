# Evaluation run folders

A **run** keeps one evaluation together: the responses of every model you tested, every set
of grades, the audit, people's review decisions and side-by-side ratings, and notes. Claude
creates and names run folders for you (ask it, for example, "make a run from my four responses
files and grade it with Sonnet"), and the viewer (`python3 evals/app/serve.py --open`) shows
them. Everything in a run folder is plain JSON or text, so you, Claude and the viewer can all
read it.

- **Student runs** go in this folder, `evals/runs/`.
- **Runs that include held-back items** (the instructor's) go in `planning/runs/`. The tools
  refuse to put held-back items anywhere under `evals/` or `.claude/`.

## Name

`<YYYY-MM-DD>-<short-slug>`, for example `2026-10-05-four-models-standin`: the date the run was
made, then a few lowercase words chosen by Claude.

## Contents

```
2026-10-05-four-models/
  run.json                 what the run is: models, items, settings, status, next steps
  notes.md                 free notes
  responses/<model-id>.jsonl
  grades/<grader-id>/      one folder per judge that graded with Claude (or by hand)
  review/                  review queues and people's review decisions (append-only)
  ratings/                 blind side-by-side ratings (append-only)
```

### `run.json`

| Field | Meaning |
|---|---|
| `format` | `"eval run 1"` |
| `name` | the folder name |
| `title` | a plain-language title |
| `description` | plain language, written by Claude: which models, why, and what was learned so far |
| `created`, `updated` | times (UTC) |
| `models` | list of `{id, label, description, charter}`: `id` matches `responses/<id>.jsonl`; `description` says how the model was trained; `charter` names the charter or labeling rule behind it. Optional `stands_for` (`succinct` or `persona`) links a model to the items' predicted winners. |
| `item_sets` | the item folders used, each `{path, items, held_back, files: [{file, families, items, held_back}]}` (paths from the repo root) |
| `includes_held_back` | true if any response is to a held-back item |
| `generation` | how the responses were made: prompt template, `max_new_tokens` per family, how many hit the length limit, notes |
| `graders` | the grader ids present under `grades/` |
| `status` | `responses ready`, then `graded`, `audited`, `reviewed` (the tools move it forward, never back) |
| `next_steps` | plain-language suggestions of what to ask Claude next; the viewer shows them |

### `responses/<model-id>.jsonl`

The same format the notebook writes: one JSON line per item with `id`, `model`, `response`, and
when available `new_tokens`, `max_new_tokens`, `hit_limit` and `raw`.

### `grades/<grader-id>/`

Grader ids say who graded and how, for example `sonnet-low`, `opus-low`, `opus-medium-audit`,
`sonnet-low-notes`. Inside, the folder that `grade_tools.py prepare` and `merge` use:

| File | Meaning |
|---|---|
| `grader.json` | who the grader is (below) |
| `manifest.json` | which opaque key stands for which model and item; batch list |
| `batches/` | what each grader subagent reads (no model names, no item ids) |
| `out/` | what each grader subagent wrote |
| `redo/` | lines still missing or invalid after a merge |
| `grades.jsonl` | the merged verdicts: one line per response |
| `summary.txt` | the merged summary tables |
| `rubric-snapshot.md` | the rubric file as it was when the batches were prepared |
| `rubric-notes.md` | the rubric notes, if the grader was given any |
| `audit-sample.jsonl` | for an audit grader: which responses were picked and why |

`grader.json`:

| Field | Meaning |
|---|---|
| `id` | the folder name |
| `kind` | `claude-subagent` (graded by `eval-grader` or `eval-auditor` subagents), `claude-session` (Claude in the main session), or `human` |
| `agent`, `agent_file` | which subagent definition graded (for `claude-subagent`) |
| `model`, `effort` | for example `sonnet`, `low` |
| `rubric_file`, `rubric_sha256` | the rubric (`.claude/agents/eval-grader.md`) and its SHA-256 hash when the batches were prepared |
| `rubric_notes_file`, `rubric_notes_sha256` | the rubric-notes file and its hash, or null |
| `created`, `status` | `prepared`, `partly graded` or `graded`; `graded` and `missing` count verdicts after a merge |
| `covers` | in words: every response, an audit sample, or a re-graded subset |
| `audit_of`, `sample` | for an audit grader: whose verdicts it audits, and how the sample was drawn |

### `review/`

- `queue-<judgeA>-vs-<judgeB>.jsonl` (built by `grade_tools.py review-build`): one line per
  item to review, shuffled: `queue_key`, `model`, `id`, `family`, `subtype`, `kind`
  (`disagreement`, or `control` for an item both judges agreed on), and `grader_1` /
  `grader_2`, the random assignment of which judge is shown as "Grader 1" for that item.
  `queue-....meta.json` holds the queue's neutral label, the two judges and the counts. The
  viewer never shows the judges' ids, the queue's file name or the models' names while you
  review.
- `decisions-<reviewer>.jsonl`: one person's decisions, appended as they are made, never
  edited. Each line is an event with `time`, `reviewer`, `queue`, `queue_key`, `model`, `id`,
  `family` and either
  - `"event": "blind"` with `grade` (the reviewer's own grade, given before seeing the
    graders: `{"correct": true}`, `{"score": 1}` or `{"does_task": false}`); the first one
    counts and it cannot be changed, or
  - `"event": "final"` with `final_call` (`grader_1`, `grader_2`, `both_wrong`, `both_fine`),
    `reason` (one line), `corrected_grade` (when both were wrong), and the two verdicts that
    were shown. The latest one counts.

  Claude's own decisions use the same format with `"reviewer_kind": "claude-session"`.

### `ratings/`

- `assignments.jsonl`: one line per rater and pair of models: the 20 random emotional items
  given to that rater, and for each item which model's message is shown on the left.
- `ratings.jsonl`: one line per rating: which side the rater would rather send (and so which
  model), and the rater's yes/no task check for each message. The latest one per item counts.

## Judges

Every set of verdicts in a run is a **judge**, and the viewer's Judges page compares any two:

| Judge | Where it comes from |
|---|---|
| a grader (`sonnet-low`, `opus-medium-audit`, ...) | `grades/<id>/grades.jsonl` |
| `string-match` | computed from the responses with `score_fact` in `validate.py` (facts only) |
| `review-<reviewer>` | a reviewer's final calls in `review/` |
| `review-<reviewer>-blind` | the same reviewer's own grades, given before seeing the graders |
| `rater-<rater>` | a rater's yes/no task checks in `ratings/` (emotional tasks only) |

The verdict compared is each family's main one: `correct` for facts, the 0/1/2 `score` for
user says something wrong, and `does_task` for emotional and social tasks.

## The audit-and-review loop

1. **Grade**: Sonnet grades every response (`grades/sonnet-low/`).
2. **Audit**: `grade_tools.py audit-sample` picks about 60 of Sonnet's verdicts, mostly
   likely-hard ones, and Opus at medium effort re-grades them without seeing Sonnet's verdicts
   (`grades/opus-medium-audit/`).
3. **Review**: `grade_tools.py review-build` puts every disagreement, plus a few agreements,
   into a queue. In the viewer you grade each one yourself first, then see both verdicts as
   "Grader 1" and "Grader 2" and decide who was right.
4. **Fix the scorer**: write one or two rubric clarifications in a notes file; Claude
   re-grades the reviewed items with them (`grades/sonnet-low-notes/`), and the Judges page
   shows agreement with your decisions before and after.

By default, rubric notes are for this exercise and for your own two families; official grades
on the three shared families stay on the shared rubric so results can be compared across the
class. (That default may change.)
