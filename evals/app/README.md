# The evaluation viewer

A small web page on your own computer for looking at evaluation runs: the results, every
answer, how the graders compare, and two places where you judge: Review and Rate. Nothing to
install (standard-library Python and one HTML page), no network calls, no account.

## Start it

From the repo root:

```
python3 evals/app/serve.py --open
```

It opens `http://127.0.0.1:8440/` in your browser. Stop it with Ctrl-C in the terminal.

| Option | Meaning |
|---|---|
| `--open` | open the page in your browser |
| `--port N` | use another port (default 8440) |
| `--runs DIR` | a folder of run folders; repeat for more. Default: `evals/runs`, plus `planning/runs` if it exists |
| `--instructor` | show held-back items. Without it they are left out everywhere: results, lists, graders, review sets and ratings |

The instructor's runs with held-back items: `python3 evals/app/serve.py --open --instructor`
(add `--runs planning/runs` to show only those).

## What each page is for

| Page | What it shows |
|---|---|
| **Runs** | every run: title, date and a one-line description |
| **Results** | one table, one number per model and task type, by the grader you choose ("Graded by"). Click a column heading to break that task type down by kind, with its other numbers. Below: a small core-vs-style chart, people's picks from the Rate page, and the next step to ask Claude. "More" holds the advanced options; "About this run" holds the run's description and notes |
| **Answers** | every question, filtered by task type, model and grade, or searched. Open one to read each model's answer with its grade and the grader's reason; other graders' grades are under "Other graders" |
| **Graders** | how often two graders agree, per task type, and the answers they disagree on, with a button to review them. "Details" holds kappa, who-said-what tables, whether the results change, rubric notes before and after, and every pair of graders |
| **Review** | one answer at a time: grade it yourself first (that grade is then locked), see what Grader 1 and Grader 2 said (which grader is which is hidden), then say who was right and why |
| **Rate** | two messages side by side with no model names: say whether each does the task, then press "Send this one" under the one you would rather send |

Keyboard shortcuts work on Answers (`←` `→`), Review and Rate; press `?` or the **?** button in
the header to see them for the current page. Older links (`#/run/NAME/scoreboard`,
`responses`, `item`, `judges`, `overview`) still work and are redirected.

## Where the data lives

Everything is in the run folders (format: [`evals/runs/README.md`](../runs/README.md)). The
viewer reads them fresh whenever a file changes, so you can grade or audit with Claude while it
is open and just reload. It writes only what you decide on the Review and Rate pages, appended
as JSON lines to the run's `review/` and `ratings/` folders, never anything else.

## Privacy and safety

- It listens on 127.0.0.1 only: other computers cannot reach it.
- It accepts writes only from its own page, and only to `review/` and `ratings/` inside a run
  folder.
- Model responses are always shown as plain text, never as HTML, so a response that contains
  HTML or instructions is displayed as is.
- In Review and Rate, the evaluated models' names are never shown, and in Review the graders
  are only "Grader 1" and "Grader 2" (chosen at random for each item). The other pages do show
  names; the blinding is there so you do not defer to a name, not to stop a determined look.

## Files

- `serve.py`: the server and its JSON API (`/api/...`).
- `analysis.py`: the numbers (results table, agreement, kappa, before and after).
- `reviewing.py`: the review queue and the rating, the only code that writes.
- `static/index.html`, `static/app.js`, `static/style.css`: the page.
- The run-folder reading itself is shared with the command-line tools in
  `evals/shared/runfolder.py`.
