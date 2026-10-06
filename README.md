# COMP 440 HW2 (in planning)

Private working repo for HW2, "Whose Preferences Count?" (Fall 2026). Nothing here is
student-facing yet.

Start here:

- `planning/stage1-plan.md`: the plan for the assignment and its first stage.
- `planning/exploration-notes.md`: running notes from the hands-on exploration, covering what
  was tried, measured and decided.
- `planning/summary-2026-10-03.md`: the original Oct 3 planning summary.

What is built:

- `evals/shared/`: the three shared task families, the checks (`validate.py`) and the
  run-folder tools (`grade_tools.py`).
- `evals/app/`: a local viewer for evaluation runs. Start it with
  `python3 evals/app/serve.py --open`, adding `--instructor` to show held-back items.
- `.claude/agents/` and `.claude/skills/grade-evals/`: Claude Code subagents that grade
  answers (Sonnet) and audit the grades (Opus), plus the skill that runs them.
- `tools/cool-colab-mcp/` and `.mcp.json`: the Colab MCP server (a security-reviewed
  community fork) that lets Claude Code drive a Colab notebook.

`planning/` holds held-back test items and answer-key material. Remove it before the repo
goes to students.
