#!/usr/bin/env python3
"""Grade the shared evaluation sets inside Claude Code, with no API key.

Claude Code subagents do the grading (the eval-grader agent in .claude/agents/eval-grader.md,
run by the grade-evals skill in .claude/skills/grade-evals/), so grading uses your Claude Code
plan instead of API credit. This script does everything around the grading:

  prepare   joins your responses with the test items, mixes the evaluated models together and
            splits them into batch files, one family per batch. Each line gets an opaque key
            in place of the model name and item id, so a grader cannot tell who wrote what.
  status    shows, for each batch, whether its verdicts are missing, partial, complete or
            invalid.
  merge     checks every verdict, writes one record per response to DIR/grades.jsonl and
            the summary tables to DIR/summary.txt, and writes redo files for any lines that
            are missing or invalid.
  compare   compares two graders (for example Opus and Sonnet) on the same responses, and
            each of them with hand grades.

Run folders (evals/runs/README.md describes the format) keep one evaluation run together:
responses, every grader's grades, the audit, review decisions and ratings. The viewer
(evals/app/serve.py) shows them. These commands work with run folders:

  init-run      creates a run folder and its run.json, and copies the responses in.
  prepare/status/merge --run RUNDIR --grader ID   (and compare --run RUNDIR --graders A B)
                the commands above, writing under RUNDIR/grades/<grader id>/ and recording
                the grader (model, effort, rubric hash, rubric notes) in grader.json.
  audit-sample  picks a sample of one grader's verdicts, weighted toward likely-hard items,
                and prepares blind batches for a second, careful grader (eval-auditor).
  review-build  builds a review queue of the items where two judges disagree, plus a few
                where they agree, for a person to decide in the viewer.

The grading rules live in one place: the eval-grader agent's definition. This script only
checks the shape of each verdict. Standard library only: nothing to install, no API key.

Typical run, from the repo root (the grade-evals skill does all of this for you):
  python3 evals/shared/grade_tools.py init-run --root evals/runs --name four-models \\
          --title "..." --description "..." --responses runs/eval-*.jsonl --items evals/shared
  python3 evals/shared/grade_tools.py prepare --run evals/runs/2026-10-05-four-models \\
          --grader sonnet-low --grader-model sonnet
  ... one eval-grader subagent per batch listed ...
  python3 evals/shared/grade_tools.py merge --run evals/runs/2026-10-05-four-models --grader sonnet-low
  python3 evals/shared/grade_tools.py audit-sample --run evals/runs/2026-10-05-four-models \\
          --from sonnet-low --grader opus-medium-audit --n 60
  ... one eval-auditor subagent per audit batch, then merge --grader opus-medium-audit ...
  python3 evals/shared/grade_tools.py review-build --run evals/runs/2026-10-05-four-models \\
          --judges sonnet-low opus-medium-audit

The older form, without run folders, still works:
  python3 evals/shared/grade_tools.py prepare runs/eval-base.jsonl runs/eval-sft.jsonl \\
          --items evals/shared --out-dir runs/grades-sonnet --grader-model sonnet
  python3 evals/shared/grade_tools.py merge runs/grades-sonnet
  python3 evals/shared/grade_tools.py compare runs/grades-opus runs/grades-sonnet
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import os
import random
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
# runfolder.py imports this module by name; when this file runs as a script, let it find
# this copy instead of loading a second one.
sys.modules.setdefault("grade_tools", sys.modules[__name__])
from validate import SUBTYPES, keywords_found, score_fact  # noqa: E402  string-match cross-checks

AGENT_FILE = os.path.join(REPO, ".claude", "agents", "eval-grader.md")
EFFORT = "low"   # set in the eval-grader agent's definition
MANIFEST_FORMAT = "grade_tools manifest 1"


def rf():
    """The run-folder module, evals/shared/runfolder.py (imported late: it imports this one)."""
    import runfolder
    return runfolder

FAMILIES = ["facts", "user_says_something_wrong", "emotional_social"]
FAMILY_TITLES = {
    "facts": "Short facts",
    "user_says_something_wrong": "User says something wrong",
    "emotional_social": "Emotional and social tasks",
}
# The grading rules themselves are written in one place only, the eval-grader agent's
# definition (.claude/agents/eval-grader.md). Here are just the verdict fields it must write
# for each family, in order, with their types: bool, str, list (of strings), or a tuple of
# the allowed integer values.
VERDICT_FIELDS = {
    "facts": {"correct": bool, "extracted_answer": str, "reason": str},
    "user_says_something_wrong": {"score": (0, 1, 2), "implicit_correction": bool,
                                  "rejected_for_wrong_reason": bool,
                                  "agreed_then_corrected": bool, "reason": str},
    "emotional_social": {"does_task": bool, "details_used": list, "missing_details": list,
                         "has_placeholder": bool, "truncated": bool, "reason": str},
}
# The item fields each batch line carries for the grader, under the item files' own names.
ITEM_FIELDS = {
    "facts": ["prompt", "answer", "accept", "scoring_note"],
    "user_says_something_wrong": ["subtype", "prompt", "false_claim", "correct_fact"],
    "emotional_social": ["subtype", "prompt", "task_check", "must_mention", "length_hint"],
}
SHORT = {"facts": "facts", "user_says_something_wrong": "wrong", "emotional_social": "emotional"}
LARGE_BATCH_CHARS = 80_000   # about 20,000 tokens: above this the grader must read in parts
REDO_RE = re.compile(r"\.redo(\d+)\.grades\.jsonl$")
NUMERIC_RE = re.compile(r"[-+]?\d[\d,.]*%?( points)?|-|n/a")


def die(message):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(2)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def write_jsonl(path, rows):
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def pct(k, n):
    return f"{100 * k / n:.0f}%" if n else "-"


def table(header, rows):
    """Plain-text table: columns of numbers right-aligned, everything else left-aligned."""
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    num = [bool(rows) and all(isinstance(r[i], (int, float)) or NUMERIC_RE.fullmatch(str(r[i]))
                              for r in rows) for i in range(len(header))]

    def fmt(row):
        return "  " + "  ".join((str(x).rjust(w) if num[i] else str(x).ljust(w))
                                for i, (x, w) in enumerate(zip(row, widths))).rstrip()
    return [fmt(header)] + [fmt(r) for r in rows]


# ------------------------------------------------------------------------------ files

def read_jsonl(path, strict=True):
    rows = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                if strict:
                    die(f"{path} line {n} is not valid JSON ({e})")
                print(f"warning: skipped line {n} of {path} (not valid JSON)", file=sys.stderr)
    return rows


def load_items(paths):
    """Map id -> item from every .jsonl file in the given folders (or the files themselves).

    An item counts as held back if its file name contains "hidden".
    """
    items = {}
    for p in paths:
        files = sorted(glob.glob(os.path.join(p, "*.jsonl"))) if os.path.isdir(p) else [p]
        if not files or not all(os.path.isfile(f) for f in files):
            die(f"--items {p}: no .jsonl item files found there")
        for f in files:
            for it in read_jsonl(f):
                if "id" not in it or "family" not in it:
                    continue
                it = dict(it, _held_back="hidden" in os.path.basename(f))
                old = items.get(it["id"])
                if old is not None and old["prompt"] != it.get("prompt"):
                    die(f"item id {it['id']} appears twice with different prompts "
                        f"(second time in {f})")
                items[it["id"]] = it
    return items


def load_responses(path):
    if not os.path.isfile(path):
        die(f"responses file not found: {path}")
    stem = os.path.splitext(os.path.basename(path))[0]
    rows = []
    for r in read_jsonl(path):
        if "id" not in r or "response" not in r:
            die(f"{path}: every line needs an 'id' and a 'response' field (got {sorted(r)})")
        rows.append(dict(r, model=str(r.get("model") or stem),
                         response="" if r["response"] is None else str(r["response"])))
    return rows


def check_known_ids(rows, items, path):
    unknown = [r["id"] for r in rows if r["id"] not in items]
    if unknown:
        hint = (" Some look like held-back items; add the folder that has them with another "
                "--items." if any("-h" in i for i in unknown) else "")
        die(f"{len(unknown)} response id(s) in {path} are not in any item file given with "
            f"--items: {', '.join(unknown[:10])}{' ...' if len(unknown) > 10 else ''}.{hint}")


# ------------------------------------------------------------------------------ records

def check_verdict(obj, family):
    """None if obj has every verdict field of the family with an allowed value."""
    for field, kind in VERDICT_FIELDS[family].items():
        if field not in obj:
            return f"the line has no {field!r} field"
        v = obj[field]
        if isinstance(kind, tuple):
            ok = isinstance(v, int) and not isinstance(v, bool) and v in kind
        elif kind is list:
            ok = isinstance(v, list) and all(isinstance(x, str) for x in v)
        else:
            ok = isinstance(v, kind)          # true/false must be JSON booleans, not 0 or 1
        if not ok:
            return f"the line's {field!r} field has an unexpected value {v!r}"
    return None


def make_record(family, item, row, verdict, grader, graded_at):
    """One line of grades.jsonl: the verdict plus the string-match cross-checks."""
    rec = {"id": item["id"], "family": family, "subtype": item.get("subtype"),
           "model": row["model"], "held_back": item["_held_back"]}
    rec.update(verdict)
    if family == "facts":
        rec["string_match_correct"] = score_fact(item, row["response"])
    elif family == "user_says_something_wrong":
        only_implicit = (verdict["score"] == 1 and verdict["implicit_correction"]
                         and not verdict["rejected_for_wrong_reason"]
                         and not verdict["agreed_then_corrected"])
        rec["score_implicit_as_2"] = 2 if only_implicit else verdict["score"]
    elif family == "emotional_social":
        rec["all_details"] = not verdict["missing_details"]
        found, missing = keywords_found(item, row["response"])
        rec["keywords_found"], rec["keywords_missing"] = found, missing
    if "new_tokens" in row:
        rec["new_tokens"] = row["new_tokens"]
    rec["grader"] = grader
    rec["graded_at"] = graded_at
    return rec


def row_groups(family, recs):
    order = SUBTYPES.get(family, [])
    present = {str(r.get("subtype")) for r in recs}
    subtypes = [s for s in order if s in present] + sorted(present - set(order))
    groups = [(s, [r for r in recs if str(r.get("subtype")) == s]) for s in subtypes]
    groups.append(("all", recs))
    held = [r for r in recs if r.get("held_back")]
    if held and len(held) < len(recs):
        groups.append(("visible items", [r for r in recs if not r.get("held_back")]))
        groups.append(("held-back items", held))
    return groups


def family_lines(family, recs):
    """The summary table for one family and one evaluated model, by subtype."""
    lines = [FAMILY_TITLES[family]]
    rows = []
    for name, rs in row_groups(family, recs):
        n = len(rs)
        if family == "facts":
            rows.append([name, n, pct(sum(r["correct"] for r in rs), n),
                         pct(sum(bool(r.get("string_match_correct")) for r in rs), n),
                         sum(r["correct"] != bool(r.get("string_match_correct")) for r in rs)])
        elif family == "user_says_something_wrong":
            s = [r["score"] for r in rs]
            rows.append([name, n, f"{sum(s) / n:.2f}", pct(s.count(0), n), pct(s.count(1), n),
                         pct(s.count(2), n),
                         f"{sum(r['score_implicit_as_2'] for r in rs) / n:.2f}"])
        else:
            rows.append([name, n, pct(sum(r["does_task"] for r in rs), n),
                         pct(sum(r["all_details"] for r in rs), n),
                         pct(sum(r["has_placeholder"] for r in rs), n),
                         pct(sum(r["truncated"] for r in rs), n),
                         pct(sum(not r.get("keywords_missing") for r in rs), n)])
    if family == "facts":
        lines += table(["subtype", "n", "correct", "string match", "disagree"], rows)
        lines.append("  (correct = the grader's verdict; string match = score_fact() in "
                     "validate.py, a cross-check; disagree = items where the two differ)")
    elif family == "user_says_something_wrong":
        lines += table(["subtype", "n", "mean", "0s", "1s", "2s", "mean if implicit = 2"], rows)
        lines.append("  (mean if implicit = 2: the mean when a correction made only implicitly "
                     "counts as 2 instead of 1)")
    else:
        lines += table(["subtype", "n", "does task", "all details", "placeholder",
                        "truncated", "all keywords (string)"], rows)
        lines.append("  (all keywords (string) = keywords_found() in validate.py found every "
                     "must_mention keyword, a cross-check)")
    return lines


# ------------------------------------------------------------------------------ prepare

def batch_line(key, family, item, response):
    """One line of a batch file: the key, the family, the item fields, the response."""
    line = {"key": key, "family": family}
    for field in ITEM_FIELDS[family]:
        if field == "scoring_note":
            if item.get("scoring_note"):       # shown only when the item has one
                line[field] = item["scoring_note"]
        elif field == "accept":
            line[field] = item.get("accept", [])
        elif field in ("subtype", "length_hint"):
            line[field] = item.get(field, "")
        else:
            line[field] = item[field]
    line["response"] = response
    return line


def split_evenly(n, size):
    """Sizes of ceil(n / size) batches that differ by at most one."""
    k = -(-n // size)
    return [n // k + (1 if i < n % k else 0) for i in range(k)]


def agent_problem():
    """None if the eval-grader agent exists and names every verdict field merge expects."""
    if not os.path.exists(AGENT_FILE):
        return f"no eval-grader agent found at {AGENT_FILE}; the grade-evals skill needs it"
    with open(AGENT_FILE, encoding="utf-8") as f:
        text = f.read()
    missing = [f for fields in VERDICT_FIELDS.values() for f in fields if f"`{f}`" not in text]
    if missing:
        return (f"{AGENT_FILE} does not mention the verdict field(s) {', '.join(missing)}, which "
                f"merge expects; the agent's output format and VERDICT_FIELDS in this script "
                f"must agree")
    return None


def inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def build_plan(responses, item_paths, out_dir, grader, seed, batch_size, keep=None):
    """Read everything and work out keys and batches. Writes nothing.

    responses: responses files; item_paths: item folders or files; grader: the dict recorded
    in the manifest and in every verdict; keep: if given, a set of (model, item id) pairs, and
    only those responses are batched (the audit sample, or a subset to re-grade).
    """
    items = load_items(item_paths)
    rows, file_of_model = [], {}
    for path in responses:
        file_rows = load_responses(path)
        check_known_ids(file_rows, items, path)
        for m in dict.fromkeys(r["model"] for r in file_rows):
            if m in file_of_model and file_of_model[m] != path:
                die(f"the model name {m!r} appears in both {file_of_model[m]} and {path}. Give "
                    f"each evaluated model its own name in the 'model' field of its responses.")
            file_of_model[m] = path
        rows += file_rows
    if not rows:
        die("the responses files have no responses")
    seen = set()
    for r in rows:
        if (r["model"], r["id"]) in seen:
            die(f"response id {r['id']} appears twice for model {r['model']!r}")
        seen.add((r["model"], r["id"]))
    if keep is not None:
        missing = sorted(set(keep) - seen)
        if missing:
            die(f"{len(missing)} of the responses to grade are not in the responses files, "
                f"e.g. {missing[:3]}")
        rows = [r for r in rows if (r["model"], r["id"]) in keep]
    for r in rows:
        if items[r["id"]]["family"] not in FAMILIES:
            die(f"item {r['id']} is in family {items[r['id']]['family']!r}, which has no "
                f"grading rules")

    rng = random.Random(seed)
    by_family = {f: [r for r in rows if items[r["id"]]["family"] == f] for f in FAMILIES}
    keys, batches, used = {}, [], set()
    for family in FAMILIES:
        group = by_family[family]
        rng.shuffle(group)                    # mixes the evaluated models within each batch
        if not group:
            continue
        start = 0
        for size in split_evenly(len(group), batch_size):
            number = len(batches) + 1
            name = f"batch-{number:02d}-{family}"
            lines = []
            for r in group[start:start + size]:
                key = f"k{rng.getrandbits(28):07x}"
                while key in used:
                    key = f"k{rng.getrandbits(28):07x}"
                used.add(key)
                info = {"model": r["model"], "id": r["id"], "family": family}
                if "new_tokens" in r:
                    info["new_tokens"] = r["new_tokens"]
                keys[key] = info
                lines.append(batch_line(key, family, items[r["id"]], r["response"]))
            batches.append({"name": name, "number": number, "family": family, "size": size,
                            "input": f"batches/{name}.jsonl",
                            "output": f"out/{name}.grades.jsonl",
                            "keys": [ln["key"] for ln in lines], "_lines": lines})
            start += size
    held_back = sorted({r["id"] for r in rows if items[r["id"]]["_held_back"]})
    out_dir = os.path.abspath(out_dir)
    manifest = {
        "format": MANIFEST_FORMAT,
        "created": now(),
        "grader": grader,
        "seed": seed,
        "batch_size": batch_size,
        "paths_are_relative_to": "this folder",
        "responses": [os.path.relpath(os.path.abspath(p), out_dir) for p in responses],
        "items": [os.path.relpath(os.path.abspath(p), out_dir) for p in item_paths],
        "models": list(dict.fromkeys(r["model"] for r in rows)),
        "counts": {f: len(by_family[f]) for f in FAMILIES},
        "held_back_items": len(held_back),
        "keys": keys,
        "batches": batches,
    }
    return manifest, held_back


def same_run(old, new):
    """True if an existing manifest describes the same keys, batches and grader."""
    def essentials(m):
        out = {k: m.get(k) for k in ("grader", "keys", "seed", "batch_size")}
        out["batches"] = [{k: v for k, v in b.items() if k != "_lines"}
                          for b in m.get("batches", [])]
        return out
    return essentials(old) == essentials(new)


def run_files(out_dir):
    names = ["manifest.json", "batches", "out", "redo", "grades.jsonl", "summary.txt"]
    return [n for n in names if os.path.exists(os.path.join(out_dir, n))]


def batch_table(out_dir, batches, extra=None):
    header = ["batch", "family", "size", "input (give to the grader)",
              "output (the grader writes)"]
    rows = []
    for b in batches:
        row = [b["number"], b["family"], b["size"], os.path.join(out_dir, b["input"]),
               os.path.join(out_dir, b["output"])]
        rows.append(row + (extra(b) if extra else []))
    return table(header + (["now"] if extra else []), rows)


def notes_fields(notes_path):
    """Manifest fields for a rubric-notes file (copied into the grader folder by prepare)."""
    if not notes_path:
        return {}
    if not os.path.isfile(notes_path):
        die(f"rubric notes file not found: {notes_path}")
    return {"rubric_notes": "rubric-notes.md", "rubric_notes_sha256": rf().sha256_file(notes_path)}


def cmd_prepare(args):
    if args.batch_size < 1:
        die("--batch-size must be at least 1")
    if args.run:
        return prepare_in_run(args)
    if not args.out_dir:
        die("give --out-dir DIR (one folder per grader model), or --run RUNDIR --grader ID "
            "to grade a run folder")
    if not args.responses:
        die("give the responses files to grade")
    if args.subset_from:
        die("--subset-from works only with --run")
    grader = {"via": "claude-code-subagent", "model": args.grader_model or "sonnet",
              "effort": EFFORT}
    grader.update(notes_fields(args.rubric_notes))
    manifest = prepare_folder(args.out_dir, args.responses, args.items or [HERE], grader,
                              args.seed, args.batch_size, args.force,
                              notes_src=args.rubric_notes)
    print_prepared(os.path.abspath(args.out_dir), manifest, again=manifest.get("_again"))
    return 0


def prepare_in_run(args):
    """prepare --run RUNDIR --grader ID: batches under RUNDIR/grades/ID, plus grader.json."""
    R = rf()
    run_dir = os.path.abspath(args.run)
    run = R.load_run(run_dir)
    agent = R.agent_settings(R.GRADER_AGENT)
    model = args.grader_model or agent.get("model") or "sonnet"
    effort = agent.get("effort") or EFFORT
    if args.rubric_notes and not args.grader:
        die("with --rubric-notes, also give --grader ID, a new grader id such as "
            f"{model}-{effort}-notes, so the graded-with-notes run sits next to the one without")
    gid = args.grader or f"{model}-{effort}"
    check_grader_id(gid)
    responses = args.responses or R.response_files(run_dir)
    if not responses:
        die(f"{run_dir}/responses has no responses files")
    items = args.items or R.run_item_paths(run)
    keep, covers = None, "every response in the run"
    if args.subset_from:
        keep, covers = subset_keys(run_dir, args.subset_from)
    grader = {"via": "claude-code-subagent", "id": gid, "agent": R.GRADER_AGENT, "model": model,
              "effort": effort}
    grader.update(notes_fields(args.rubric_notes))
    out_dir = R.grader_dir(run_dir, gid)
    info = R.make_grader_info(gid, R.GRADER_AGENT, model, effort, args.rubric_notes,
                              {"covers": covers, "subset_from": args.subset_from})
    manifest = prepare_folder(out_dir, responses, items, grader, args.seed, args.batch_size,
                              args.force, keep=keep, notes_src=args.rubric_notes)
    if not manifest.get("_again") or not os.path.exists(os.path.join(out_dir, R.GRADER_JSON)):
        write_grader_files(run_dir, gid, info, args.rubric_notes)
    print_prepared(out_dir, manifest, again=manifest.get("_again"), run_dir=run_dir)
    return 0


def check_grader_id(gid):
    R = rf()
    if not R.SAFE_ID.match(gid):
        die(f"grader id {gid!r} must be letters, digits, '.', '_' or '-' (for example "
            f"sonnet-low)")
    if gid.startswith(R.RESERVED_JUDGE_PREFIXES):
        die(f"grader ids may not start with {', '.join(R.RESERVED_JUDGE_PREFIXES)}; the viewer "
            f"uses those names for reviewers, raters and the string-match check")


def write_grader_files(run_dir, gid, info, notes_src=None):
    """grader.json, plus a copy of the rubric as it is now (what the grader will read)."""
    R = rf()
    out_dir = R.grader_dir(run_dir, gid)
    if os.path.exists(R.RUBRIC_FILE):
        shutil.copyfile(R.RUBRIC_FILE, os.path.join(out_dir, "rubric-snapshot.md"))
        info["rubric_snapshot"] = "rubric-snapshot.md"
    if notes_src:
        info["rubric_notes_copy"] = "rubric-notes.md"
    R.save_grader_info(run_dir, gid, info)
    run = R.load_run(run_dir)
    if gid not in run["graders"]:
        run["graders"].append(gid)
    R.save_run(run_dir, run)


def subset_keys(run_dir, source):
    """(model, id) pairs to re-grade: those another grader graded, or those in a queue."""
    R = rf()
    if R.QUEUE_NAME.match(os.path.splitext(os.path.basename(source))[0]):
        name = os.path.splitext(os.path.basename(source))[0]
        try:
            _, rows = R.load_queue(run_dir, name)
        except (OSError, ValueError) as e:
            die(str(e))
        keys = {(r["model"], r["id"]) for r in rows}
        return keys, f"the {len(keys)} responses in review queue {name}"
    if source not in R.grader_ids(run_dir):
        die(f"--subset-from {source}: not a grader id in this run "
            f"({', '.join(R.grader_ids(run_dir)) or 'none yet'}) or a review queue name")
    keys = {(r["model"], r["id"]) for r in R.load_grade_records(run_dir, source)}
    if not keys:
        die(f"grader {source} has no merged grades yet (run merge first)")
    return keys, f"the {len(keys)} responses graded by {source}"


def prepare_folder(out_dir, responses, item_paths, grader, seed, batch_size, force, keep=None,
                   notes_src=None):
    """Plan the batches and write them, the manifest and the notes copy into out_dir.

    Returns the manifest; manifest["_again"] is True if the folder was already prepared the
    same way (then nothing is written).
    """
    out_dir = os.path.abspath(out_dir)
    manifest, held_back = build_plan(responses, item_paths, out_dir, grader, seed, batch_size,
                                     keep)
    if held_back:
        for folder in ("evals", ".claude"):
            if inside(out_dir, os.path.join(REPO, folder)):
                die(f"{len(held_back)} of these items are held back (from files whose names "
                    f"contain 'hidden'), and the batch files would copy them into "
                    f"{os.path.join(REPO, folder)}. Choose an --out-dir outside evals/ and "
                    f".claude/, for example next to the responses.")
    manifest_path = os.path.join(out_dir, "manifest.json")
    existing = run_files(out_dir)
    if os.path.exists(manifest_path) and not force:
        with open(manifest_path, encoding="utf-8") as f:
            old = json.load(f)
        if same_run(old, manifest):
            print(f"{out_dir} was already prepared from the same responses, items, seed, batch "
                  f"size and grader. Nothing was changed; finished verdicts are kept.")
            old["_again"] = True
            return old
        die(f"{out_dir} already holds a grading run prepared differently (other responses, "
            f"items, seed, batch size, grader model or rubric notes), and its verdicts would not "
            f"match new batches. Use another folder (another --out-dir, or another --grader id "
            f"with --run), or add --force to move the old run into a 'replaced-...' folder "
            f"inside it and start over.")
    if existing and not os.path.exists(manifest_path) and not force:
        die(f"{out_dir} already has {', '.join(existing)} but no manifest.json. Use an empty "
            f"folder, or add --force to move those into a 'replaced-...' folder first.")
    if existing and force:
        keep_dir = os.path.join(out_dir,
                                "replaced-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(keep_dir)
        for name in existing:
            shutil.move(os.path.join(out_dir, name), os.path.join(keep_dir, name))
        print(f"--force: moved the earlier run ({', '.join(existing)}) into {keep_dir}")

    for sub in ("batches", "out"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
    for b in manifest["batches"]:
        write_jsonl(os.path.join(out_dir, b["input"]), b.pop("_lines"))
    if notes_src:
        dest = os.path.join(out_dir, manifest["grader"]["rubric_notes"])
        if os.path.abspath(notes_src) != dest:
            shutil.copyfile(notes_src, dest)
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1)
        f.write("\n")
    return manifest


def grader_prompt_lines(out_dir, manifest, inp="<input path from the table>",
                        out="<output path from the table>"):
    """The exact prompt for one grader subagent: input, output and (if any) rubric notes."""
    lines = [f"Input batch: {inp}", f"Output file: {out}"]
    notes = manifest["grader"].get("rubric_notes")
    if notes:
        lines.append(f"Rubric notes: {os.path.join(out_dir, notes)}")
    return lines


def merge_command(out_dir):
    """How to call merge for this folder: with --run/--grader inside a run folder."""
    script = os.path.relpath(os.path.abspath(__file__))
    run_dir = rf().find_run_dir(os.path.dirname(out_dir))
    if run_dir and os.path.basename(os.path.dirname(out_dir)) == rf().GRADES_DIR:
        return (f"python3 {script} merge --run {os.path.relpath(run_dir)} --grader "
                f"{os.path.basename(out_dir)}")
    return f"python3 {script} merge {out_dir}"


def print_prepared(out_dir, manifest, again=False, run_dir=None):
    batches = manifest["batches"]
    g = manifest["grader"]
    n = sum(b["size"] for b in batches)
    per_family = ", ".join(f"{sum(b['family'] == f for b in batches)} {SHORT[f]}"
                           for f in FAMILIES if any(b["family"] == f for b in batches))
    print(f"{'Prepared' if not again else 'Already prepared:'} {n} responses from "
          f"{len(manifest['models'])} model(s) ({', '.join(manifest['models'])}), including "
          f"{manifest['held_back_items']} held-back item(s), in {len(batches)} batches "
          f"({per_family}).")
    print(f"Grader: {g.get('id', g['model'])} ({g.get('agent', 'eval-grader')} subagent, model "
          f"{g['model']}, effort {g['effort']}"
          + (", with rubric notes" if g.get("rubric_notes") else "") + f"). Folder: {out_dir}\n")
    state = None
    if again:
        state = lambda b: [check_batch(out_dir, b, manifest)["state"]]  # noqa: E731
    print("\n".join(batch_table(out_dir, batches, state)))
    for b in batches:
        p = os.path.join(out_dir, b["input"])
        if os.path.exists(p) and os.path.getsize(p) > LARGE_BATCH_CHARS:
            print(f"note: {b['name']} is {os.path.getsize(p):,} bytes; the grader will have to "
                  f"read it in parts. A smaller --batch-size avoids that.")
    problem = agent_problem()
    if problem:
        print(f"\nwarning: {problem}")
    agent = g.get("agent", "eval-grader")
    print(f"\nNext: spawn one {agent} subagent per batch (model: {g['model']}). Its prompt is "
          f"exactly these lines, with the paths from the table:")
    print("\n".join("  " + x for x in grader_prompt_lines(out_dir, manifest)))
    print(f"Then run:\n  {merge_command(out_dir)}")


# ------------------------------------------------------------------------------ checking verdicts

def load_manifest(out_dir):
    path = os.path.join(out_dir, "manifest.json")
    if not os.path.exists(path):
        die(f"no manifest.json in {out_dir}; run 'grade_tools.py prepare' first")
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    if manifest.get("format") != MANIFEST_FORMAT:
        die(f"{path} was not written by this version of grade_tools.py")
    return manifest


def batch_inputs(out_dir, batch):
    path = os.path.join(out_dir, batch["input"])
    if not os.path.exists(path):
        die(f"batch file {path} is missing; run prepare again with --force to rebuild it")
    return {ln["key"]: ln for ln in read_jsonl(path)}


def output_files(out_dir, batch):
    """The batch's output file, then its redo outputs in order: [(path, label)]."""
    files = []
    main = os.path.join(out_dir, batch["output"])
    if os.path.exists(main):
        files.append((main, batch["name"]))
    redo = []
    for p in glob.glob(os.path.join(out_dir, "out", f"{batch['name']}.redo*.grades.jsonl")):
        m = REDO_RE.search(p)
        if m:
            redo.append((int(m.group(1)), p))
    for n, p in sorted(redo):
        files.append((p, f"{batch['name']}.redo{n}"))
    return files, max((n for n, _ in redo), default=0)


def check_line(obj, family, line):
    """Return (verdict, problem). The verdict keeps only the family's fields, in order."""
    problem = check_verdict(obj, family)
    if problem:
        return None, problem
    verdict = {k: obj[k] for k in VERDICT_FIELDS[family]}
    if family == "emotional_social":
        wanted = line["must_mention"]
        canon = {w.strip().lower(): w for w in wanted}
        lists = {}
        for field in ("details_used", "missing_details"):
            out = []
            for v in verdict[field]:
                if not isinstance(v, str) or v.strip().lower() not in canon:
                    return None, (f"{field} has {v!r}, which is not one of the must_mention "
                                  f"keywords {wanted}")
                out.append(canon[v.strip().lower()])
            lists[field] = out
        placed = lists["details_used"] + lists["missing_details"]
        if sorted(placed) != sorted(wanted):
            return None, (f"details_used and missing_details must hold each must_mention keyword "
                          f"{wanted} exactly once between them (got {placed})")
        verdict["details_used"] = [w for w in wanted if w in lists["details_used"]]
        verdict["missing_details"] = [w for w in wanted if w in lists["missing_details"]]
    return verdict, None


def check_batch(out_dir, batch, manifest, inputs=None):
    """Read every output file of one batch and sort its lines into good and bad."""
    inputs = inputs if inputs is not None else batch_inputs(out_dir, batch)
    family, wanted = batch["family"], batch["keys"]
    owner = {k: b["name"] for b in manifest["batches"] for k in b["keys"]}
    files, last_round = output_files(out_dir, batch)
    good, source, bad_keys, problems, notes = {}, {}, set(), [], collections.Counter()
    for path, label in files:
        found, dupes = {}, set()
        with open(path, encoding="utf-8") as f:
            raw_lines = f.read().splitlines()
        for n, raw in enumerate(raw_lines, 1):
            text = raw.strip()
            if not text:
                continue
            if text.startswith("```"):
                notes["code-fence line(s) ignored"] += 1
                continue
            where = f"{os.path.basename(path)} line {n}"
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                problems.append(f"{where}: not valid JSON: {text[:80]!r}")
                continue
            if not isinstance(obj, dict):
                problems.append(f"{where}: not a JSON object")
                continue
            key = obj.get("key")
            if key not in inputs:
                if key in owner:
                    problems.append(f"{where}: key {key} belongs to {owner[key]}, not this batch")
                else:
                    problems.append(f"{where}: unknown key {key!r}")
                continue
            verdict, problem = check_line(obj, family, inputs[key])
            if problem:
                problems.append(f"{where} ({key}): {problem}")
                bad_keys.add(key)
                continue
            extra = set(obj) - set(VERDICT_FIELDS[family]) - {"key"}
            if extra:
                notes["line(s) with extra fields (the extra fields were dropped)"] += 1
            if family == "user_says_something_wrong" and verdict["score"] != 1 and any(
                    verdict[k] for k in ("implicit_correction", "rejected_for_wrong_reason",
                                         "agreed_then_corrected")):
                notes["line(s) with a partial-answer flag set although the score is 0 or 2 (kept as written)"] += 1
            if key in found:
                dupes.add(key)
            found[key] = verdict
        for key in dupes:
            problems.append(f"{os.path.basename(path)}: key {key} appears more than once")
            found.pop(key)
            bad_keys.add(key)
        for key, verdict in found.items():
            if key not in good:            # an earlier file's good verdict wins
                good[key], source[key] = verdict, (label, path)
    gaps = [k for k in wanted if k not in good]
    invalid = [k for k in gaps if k in bad_keys]
    if not files:
        state = "missing"
    elif not gaps:
        state = "complete"
    elif invalid or problems:
        state = "invalid"
    else:
        state = "partial"
    return {"state": state, "good": good, "source": source, "gaps": gaps,
            "invalid": invalid, "problems": problems, "notes": notes, "files": files,
            "next_round": last_round + 1, "inputs": inputs}


def grading_dir(args, command):
    """The grading folder named by DIR, or by --run RUNDIR --grader ID."""
    if args.run:
        if args.dir:
            die(f"give either DIR or --run RUNDIR --grader ID to {command}, not both")
        if not args.grader:
            die(f"{command} --run needs --grader ID (one of: "
                f"{', '.join(rf().grader_ids(args.run)) or 'none yet'})")
        return os.path.abspath(rf().grader_dir(args.run, args.grader))
    if not args.dir:
        die(f"give the grading folder DIR, or --run RUNDIR --grader ID")
    return os.path.abspath(args.dir)


def cmd_status(args):
    if args.run and not args.grader:
        R = rf()
        gids = [g for g in R.grader_ids(args.run)
                if os.path.exists(os.path.join(R.grader_dir(args.run, g), "manifest.json"))]
        if not gids:
            print(f"No graders have been prepared in {args.run} yet.")
            return 0
        for g in gids:
            args.grader = g
            cmd_status(args)
            print()
        return 0
    out_dir = grading_dir(args, "status")
    manifest = load_manifest(out_dir)
    rows, totals = [], collections.Counter()
    for b in manifest["batches"]:
        r = check_batch(out_dir, b, manifest)
        totals[r["state"]] += 1
        totals["good"] += len(r["good"])
        rows.append([b["number"], b["family"], b["size"], r["state"], len(r["good"]),
                     len(r["gaps"]) - len(r["invalid"]), len(r["invalid"]),
                     len(r["problems"]), " + ".join(os.path.basename(p) for p, _ in r["files"])
                     or "-"])
    g = manifest["grader"]
    print(f"Grading run in {out_dir} (grader: {g.get('id', g['model'])}, model {g['model']})")
    print("\n".join(table(["batch", "family", "size", "state", "graded", "missing",
                                "invalid", "bad lines", "output files"], rows)))
    n = sum(b["size"] for b in manifest["batches"])
    print(f"\n{totals['good']} of {n} responses have a valid verdict. Batches: "
          + ", ".join(f"{totals[s]} {s}" for s in ("complete", "partial", "invalid", "missing")
                      if totals[s]) + ".")
    print("  (graded = lines with a valid verdict; missing = keys with no line yet; invalid = "
          "keys whose only lines broke the rules; bad lines = lines that could not be used)")
    if totals["complete"] == len(manifest["batches"]):
        print("Every batch is complete. Run merge to write grades.jsonl and the summary.")
    else:
        print("Run merge to see the problems and get redo files for the gaps.")
    return 0


# ------------------------------------------------------------------------------ merge

def resolve(out_dir, paths):
    return [os.path.normpath(os.path.join(out_dir, p)) for p in paths]


def merge_records(out_dir, manifest, items, results):
    """One grades.jsonl record for every key with a good verdict."""
    records = []
    order = {m: i for i, m in enumerate(manifest["models"])}
    for b in manifest["batches"]:
        r = results[b["name"]]
        for key in b["keys"]:
            if key not in r["good"]:
                continue
            info = manifest["keys"][key]
            item = items.get(info["id"])
            if item is None:
                die(f"item {info['id']} is not in the item files {manifest['items']}; pass the "
                    f"folders that hold them with --items")
            line = r["inputs"][key]
            if item["prompt"] != line["prompt"]:
                print(f"warning: the prompt of {info['id']} changed since prepare; the verdict "
                      f"was given for the old prompt", file=sys.stderr)
            row = {"id": info["id"], "model": info["model"], "response": line["response"]}
            if "new_tokens" in info:
                row["new_tokens"] = info["new_tokens"]
            _, path = r["source"][key]
            graded_at = dt.datetime.fromtimestamp(
                os.path.getmtime(path), dt.timezone.utc).isoformat(timespec="seconds")
            records.append(make_record(b["family"], item, row, r["good"][key],
                                       dict(manifest["grader"]), graded_at))
    item_order = {}
    for p in resolve(out_dir, manifest["responses"]):
        if os.path.exists(p):
            for n, row in enumerate(read_jsonl(p, strict=False)):
                item_order.setdefault(row.get("id"), n)
    records.sort(key=lambda rec: (order.get(rec["model"], 99), item_order.get(rec["id"], 0),
                                  rec["id"]))
    return records


def headline(records, family, model):
    rs = [r for r in records if r["model"] == model and r["family"] == family]
    if not rs:
        return None
    if family == "facts":
        return 100 * sum(r["correct"] for r in rs) / len(rs)
    if family == "user_says_something_wrong":
        return sum(r["score"] for r in rs) / len(rs)
    return 100 * sum(r["does_task"] for r in rs) / len(rs)


def fmt_headline(family, v):
    if v is None:
        return "-"
    return f"{v:.2f}" if family == "user_says_something_wrong" else f"{v:.0f}%"


def headline_lines(records, models):
    rows = []
    for m in models:
        recs = [r for r in records if r["model"] == m]
        wrong = [r for r in recs if r["family"] == "user_says_something_wrong"]
        rows.append([m, len(recs)]
                    + [fmt_headline("facts", headline(records, "facts", m))]
                    + [fmt_headline("user_says_something_wrong",
                                    headline(records, "user_says_something_wrong", m))]
                    + [f"{sum(r['score_implicit_as_2'] for r in wrong) / len(wrong):.2f}"
                       if wrong else "-"]
                    + [fmt_headline("emotional_social",
                                    headline(records, "emotional_social", m))])
    lines = ["Headline numbers by evaluated model (all items, visible and held back)"]
    lines += table(["model", "graded", "facts correct", "wrong-claim mean (0-2)",
                         "mean if implicit = 2", "emotional does task"], rows)
    return lines


def string_match_lines(recs):
    facts = [r for r in recs if r["family"] == "facts"]
    emo = [r for r in recs if r["family"] == "emotional_social"]
    parts = []
    if facts:
        agree = sum(r["correct"] == bool(r["string_match_correct"]) for r in facts)
        parts.append(f"facts: the grader and score_fact() agree on {agree} of {len(facts)} "
                     f"({pct(agree, len(facts))})")
    if emo:
        total = agree = 0
        for r in emo:
            for kw in r["details_used"] + r["missing_details"]:
                total += 1
                agree += (kw in r["details_used"]) == (kw in r["keywords_found"])
        parts.append(f"emotional: the grader's details lists and keywords_found() agree on "
                     f"{agree} of {total} keywords ({pct(agree, total)})")
    return ["String-match cross-check: " + "; ".join(parts)] if parts else []


def summary_text(out_dir, manifest, records, gaps, hand=None):
    g = manifest["grader"]
    n = sum(b["size"] for b in manifest["batches"])
    lines = [f"Grades from Claude Code subagents (grader {g['model']}, effort {g['effort']}) "
             f"in {out_dir}",
             f"Graded {len(records)} of {n} responses. Still missing or invalid: {gaps}"
             + (" (see the redo folder; run merge again after regrading)." if gaps else "."),
             ""]
    models = [m for m in manifest["models"] if any(r["model"] == m for r in records)]
    if records:
        lines += headline_lines(records, models) + [""]
    for model in models:
        recs = [r for r in records if r["model"] == model]
        lines += [f"Model: {model}  ({len(recs)} responses graded)", ""]
        for family in FAMILIES:
            fam = [r for r in recs if r["family"] == family]
            if fam:
                lines += family_lines(family, fam) + [""]
        lines += string_match_lines(recs) + [""]
    if hand:
        lines += hand + [""]
    return "\n".join(lines).rstrip() + "\n"


def cmd_merge(args):
    out_dir = grading_dir(args, "merge")
    manifest = load_manifest(out_dir)
    item_paths = args.items or resolve(out_dir, manifest["items"])
    run_dir = rf().find_run_dir(os.path.dirname(out_dir))
    if not args.items and run_dir and not all(os.path.exists(p) for p in item_paths):
        # The run folder was moved or copied: its run.json names the item sets from the repo root.
        item_paths = rf().run_item_paths(rf().load_run(run_dir))
    items = load_items(item_paths)
    results = {b["name"]: check_batch(out_dir, b, manifest) for b in manifest["batches"]}
    records = merge_records(out_dir, manifest, items, results)
    write_jsonl(os.path.join(out_dir, "grades.jsonl"), records)

    problems = [(b, results[b["name"]]) for b in manifest["batches"]
                if results[b["name"]]["problems"] or results[b["name"]]["notes"]]
    if problems:
        print("Problems found in the graders' output:")
        for b, r in problems:
            if r["problems"] and not r["gaps"]:
                print(f"  {b['name']}: {len(r['problems'])} unusable line(s) ignored; every "
                      f"response in this batch has a valid verdict")
            else:
                for p in r["problems"][:5]:
                    print(f"  {b['name']}: {p}")
                if len(r["problems"]) > 5:
                    print(f"  {b['name']}: ... and {len(r['problems']) - 5} more")
            for note, k in r["notes"].items():
                print(f"  {b['name']}: {k} {note}")
        print()

    redo_dir = os.path.join(out_dir, "redo")
    for old in glob.glob(os.path.join(redo_dir, "batch-*.jsonl")):
        os.remove(old)
    redo = []
    for b in manifest["batches"]:
        r = results[b["name"]]
        if r["gaps"]:
            os.makedirs(redo_dir, exist_ok=True)
            path = os.path.join(redo_dir, f"{b['name']}.jsonl")
            write_jsonl(path, [r["inputs"][k] for k in r["gaps"]])
            out = os.path.join(out_dir, "out", f"{b['name']}.redo{r['next_round']}.grades.jsonl")
            redo.append([b["number"], b["family"], len(r["gaps"]), r["state"], path, out])
    gaps = sum(row[2] for row in redo)

    hand = None
    if args.hand:
        graded = {(r["model"], r["id"]): r for r in records}
        responses = all_responses(out_dir, manifest)
        hand = hand_section(args, [(manifest["grader"]["model"], graded)], responses,
                            manifest["models"])
    text = summary_text(out_dir, manifest, records, gaps, hand)
    with open(os.path.join(out_dir, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(text)
    print(text + f"(Saved in {os.path.join(out_dir, 'summary.txt')}; the verdicts are in "
                 f"{os.path.join(out_dir, 'grades.jsonl')}.)")
    g = manifest["grader"]
    if redo:
        print(f"\nREDO NEEDED: {gaps} line(s) in {len(redo)} batch(es) have no valid verdict. "
              f"Spawn one {g.get('agent', 'eval-grader')} per row below (same grader model, "
              f"{g['model']}), with that input and output path, then run merge again:")
        print("\n".join(table(["batch", "family", "lines", "was", "redo input",
                                    "redo output"], redo)))
        print("Each redo grader's prompt is exactly:")
        print("\n".join("  " + x for x in grader_prompt_lines(
            out_dir, manifest, "<redo input>", "<redo output>")))
    else:
        print("\nEvery response has a valid verdict.")
    after_merge_in_run(out_dir, manifest, len(records), gaps)
    return 0


def after_merge_in_run(out_dir, manifest, graded, gaps):
    """Inside a run folder: record the merge in grader.json and move run.json's status on."""
    R = rf()
    run_dir = R.find_run_dir(os.path.dirname(out_dir))
    if not run_dir or os.path.basename(os.path.dirname(out_dir)) != R.GRADES_DIR:
        return
    gid = os.path.basename(out_dir)
    info = R.load_grader_info(run_dir, gid)
    info.update({"status": "graded" if not gaps else "partly graded", "graded": graded,
                 "missing": gaps, "merged": now()})
    if info.get("rubric_sha256") and os.path.exists(R.RUBRIC_FILE) \
            and R.sha256_file(R.RUBRIC_FILE) != info["rubric_sha256"]:
        print(f"\nwarning: {R.repo_rel(R.RUBRIC_FILE)} has changed since this grader was "
              f"prepared; grader.json records the rubric as it was then (rubric-snapshot.md).")
    R.save_grader_info(run_dir, gid, info)
    run = R.load_run(run_dir)
    if gid not in run["graders"]:
        run["graders"].append(gid)
    if not gaps:
        name = run["name"]
        if info.get("audit_of"):
            R.advance_status(run, "audited")
            run["next_steps"] = [
                f"Build the review queue: ask Claude \"build the review queue for run {name}: "
                f"{info['audit_of']} vs {gid}\".",
                f"Then open the viewer ({viewer_command(run_dir)}), go to Review, and decide "
                f"each disagreement."]
        elif R.advance_status(run, "graded"):
            run["next_steps"] = [
                f"Audit the grades: ask Claude \"audit run {name} with Opus at medium effort\".",
                f"Look at the scoreboard: {viewer_command(run_dir)}"]
    R.save_run(run_dir, run)
    print(f"\nRecorded in {os.path.relpath(os.path.join(out_dir, R.GRADER_JSON))} and "
          f"{os.path.relpath(os.path.join(run_dir, R.RUN_JSON))} (status: {run['status']}).")


def viewer_command(run_dir):
    """The command that opens the viewer on this run (instructor mode for planning/ runs)."""
    R = rf()
    cmd = "python3 evals/app/serve.py --open"
    if not R.inside(run_dir, os.path.join(REPO, "evals")):
        root = os.path.dirname(os.path.abspath(run_dir))
        cmd += f" --instructor --runs {R.repo_rel(root)}"
    return cmd


# ------------------------------------------------------------------------------ hand grades

def all_responses(out_dir, manifest):
    """(model, id) -> (batch line) for every key, read from the batch files."""
    out = {}
    for b in manifest["batches"]:
        for key, line in batch_inputs(out_dir, b).items():
            info = manifest["keys"][key]
            out[(info["model"], info["id"])] = line
    return out


def kappa(pairs):
    """Cohen's kappa: agreement corrected for the agreement expected by chance."""
    n = len(pairs)
    if not n:
        return None
    po = sum(a == b for a, b in pairs) / n
    ca, cb = collections.Counter(a for a, _ in pairs), collections.Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return None if pe == 1 else (po - pe) / (1 - pe)


def fmt_kappa(k):
    return "n/a" if k is None else f"{k:.2f}"


def hand_section(args, graders, responses, models):
    """Agreement of each grader with hand grades of user_says_something_wrong items.

    graders: [(name, {(model, id): record})]; responses: {(model, id): batch line}.
    """
    hand = {}
    for r in read_jsonl(args.hand):
        if "id" in r:
            hand[r["id"]] = r
    if not hand:
        die(f"no lines with an 'id' in {args.hand}")
    model = args.hand_model
    if model is None:
        named = {r.get("model") for r in hand.values()}
        if len(named) == 1 and None not in named:
            model = named.pop()
        elif len(models) == 1:
            model = models[0]
        else:
            die(f"the hand grades in {args.hand} do not say which model's responses they grade. "
                f"Add --hand-model NAME (one of: {', '.join(models)}).")
    if model not in models:
        die(f"--hand-model {model!r} is not one of the graded models ({', '.join(models)})")
    seen = {}
    if args.hand_responses:
        for r in read_jsonl(args.hand_responses):
            if "id" in r and "response" in r:
                seen[r["id"]] = r["response"]
    for i, r in hand.items():
        if "response" in r:
            seen[i] = r["response"]
    lines = [f"Agreement with hand grades in {args.hand} (model {model}, user says something "
             f"wrong)"]
    usable, changed = [], []
    for i in sorted(hand):
        line = responses.get((model, i))
        if line is None or line["family"] != "user_says_something_wrong":
            continue
        if i in seen and seen[i] != line["response"]:
            changed.append(i)
            continue
        usable.append(i)
    if seen:
        lines.append(f"  {len(usable)} items compared; {len(changed)} left out because the hand "
                     f"grader saw a different response text"
                     + (f" ({', '.join(changed[:8])}{' ...' if len(changed) > 8 else ''})"
                        if changed else ""))
    else:
        lines.append(f"  {len(usable)} items compared. The hand grades do not include the "
                     f"responses they were given for, so this cannot check that they are the same "
                     f"responses (add --hand-responses FILE to check).")
    views = [("implicit corrections = 1", "score", ("score", "score_implicit_as_1")),
             ("implicit corrections = 2", "score_implicit_as_2", ("score_implicit_as_2",))]
    rows, grids = [], []
    for name, recs in graders:
        row = [name]
        for view, field, ref_fields in views:
            pairs = []
            for i in usable:
                rec = recs.get((model, i))
                want = next((hand[i][f] for f in ref_fields if f in hand[i]), None)
                if rec is not None and want is not None:
                    pairs.append((int(want), rec[field]))
            agree = sum(a == b for a, b in pairs)
            row.append(f"{agree} of {len(pairs)} ({pct(agree, len(pairs))}), kappa "
                       f"{fmt_kappa(kappa(pairs))}" if pairs else "-")
            if field == "score" and pairs:
                grid = [[f"hand {w}"] + [sum(1 for a, c in pairs if a == w and c == k)
                                         for k in (0, 1, 2)] for w in (0, 1, 2)]
                grids.append((name, grid))
        rows.append(row)
    lines += ["  " + x for x in table(["grader"] + [v[0] for v in views], rows)]
    for name, grid in grids:
        lines.append(f"  {name} against the hand grades (implicit corrections = 1):")
        lines += ["  " + x for x in table(["", f"{name} 0", f"{name} 1", f"{name} 2"], grid)]
    return lines


# ------------------------------------------------------------------------------ compare

LABEL = {"facts": "correct", "user_says_something_wrong": "score",
         "emotional_social": "does_task"}
DETAIL_FIELDS = {"facts": ["answer", "accept"],
                 "user_says_something_wrong": ["false_claim", "correct_fact"],
                 "emotional_social": ["task_check", "must_mention"]}


def load_graded(path):
    out_dir = os.path.abspath(path)
    manifest = load_manifest(out_dir)
    grades = os.path.join(out_dir, "grades.jsonl")
    if not os.path.exists(grades):
        die(f"no grades.jsonl in {out_dir}; run 'grade_tools.py merge {path}' first")
    recs = {(r["model"], r["id"]): r for r in read_jsonl(grades)}
    return out_dir, manifest, recs, all_responses(out_dir, manifest)


def order_text(values):
    ranked = sorted(values.items(), key=lambda kv: -kv[1])
    out = ranked[0][0]
    for (_, prev), (m, v) in zip(ranked, ranked[1:]):
        out += (" = " if abs(v - prev) < 1e-9 else " > ") + m
    return out


def cmd_compare(args):
    if args.run:
        if args.dir_a or args.dir_b:
            die("give either DIR_A DIR_B or --run RUNDIR --graders A B, not both")
        if not args.graders:
            die("compare --run needs --graders A B (two grader ids in the run)")
        args.dir_a, args.dir_b = (rf().grader_dir(args.run, g) for g in args.graders)
    elif not (args.dir_a and args.dir_b):
        die("give two graded folders DIR_A DIR_B, or --run RUNDIR --graders A B")
    dir_a, man_a, A, resp_a = load_graded(args.dir_a)
    dir_b, man_b, B, resp_b = load_graded(args.dir_b)
    na = man_a["grader"].get("id") or man_a["grader"]["model"]
    nb = man_b["grader"].get("id") or man_b["grader"]["model"]
    if na == nb:
        na, nb = os.path.basename(dir_a), os.path.basename(dir_b)
    common = [k for k in A if k in B]
    differ = [k for k in common if resp_a[k]["response"] != resp_b[k]["response"]]
    common = [k for k in common if k not in set(differ)]
    out = [f"Comparing two graders: {na} ({dir_a}) and {nb} ({dir_b})",
           f"{len(common)} responses graded by both ({len(A)} by {na}, {len(B)} by {nb})."]
    if differ:
        out.append(f"warning: {len(differ)} (model, id) pairs have different response text in "
                   f"the two runs and are left out, e.g. {differ[:3]}")
    models = [m for m in man_a["models"] if any(k[0] == m for k in common)]
    disagreements = []
    for family in FAMILIES:
        keys = [k for k in common if A[k]["family"] == family]
        if not keys:
            continue
        field = LABEL[family]
        pairs = [(A[k][field], B[k][field]) for k in keys]
        agree = sum(a == b for a, b in pairs)
        out += ["", f"{FAMILY_TITLES[family]}: {len(keys)} responses",
                f"  {field}: the graders agree on {agree} of {len(keys)} ({pct(agree, len(keys))})"
                f", Cohen's kappa {fmt_kappa(kappa(pairs))}"]
        if family == "user_says_something_wrong":
            near = sum(abs(a - b) <= 1 for a, b in pairs)
            p2 = [(A[k]["score_implicit_as_2"], B[k]["score_implicit_as_2"]) for k in keys]
            a2 = sum(a == b for a, b in p2)
            out.append(f"  within one point: {near} of {len(keys)}; with implicit corrections "
                       f"counted as 2: agree on {a2} ({pct(a2, len(keys))}), kappa "
                       f"{fmt_kappa(kappa(p2))}")
            grid = [[f"{na} {a}"] + [sum(1 for x, y in pairs if x == a and y == b)
                                     for b in (0, 1, 2)] for a in (0, 1, 2)]
            out += ["  " + x for x in table(["", f"{nb} 0", f"{nb} 1", f"{nb} 2"], grid)]
        if family == "emotional_social":
            for extra in ("all_details", "has_placeholder", "truncated"):
                ok = sum(A[k][extra] == B[k][extra] for k in keys)
                out.append(f"  {extra}: agree on {ok} of {len(keys)} ({pct(ok, len(keys))})")
        rows, va, vb = [], {}, {}
        for m in models:
            ka = [A[k] for k in keys if k[0] == m]
            kb = [B[k] for k in keys if k[0] == m]
            va[m], vb[m] = headline(ka, family, m), headline(kb, family, m)
            if va[m] is None:
                continue
            diff = vb[m] - va[m]
            rows.append([m, len(ka), fmt_headline(family, va[m]), fmt_headline(family, vb[m]),
                         f"{diff:+.2f}" if family == "user_says_something_wrong"
                         else f"{diff:+.0f} points"])
        what = {"facts": "share correct", "user_says_something_wrong": "mean score, 0 to 2",
                "emotional_social": "share that does the task"}[family]
        out.append(f"  Headline per evaluated model ({what}):")
        out += ["  " + x for x in table(["model", "n", na, nb, f"{nb} minus {na}"], rows)]
        va = {m: v for m, v in va.items() if v is not None}
        vb = {m: v for m, v in vb.items() if v is not None}
        if len(va) > 1:
            flips = [f"{x} vs {y}" for i, x in enumerate(va) for y in list(va)[i + 1:]
                     if (va[x] - va[y]) * (vb[x] - vb[y]) < 0]
            out.append(f"  Order of models, best first: {na}: {order_text(va)};  {nb}: "
                       f"{order_text(vb)}")
            out.append("  Conclusions: " + ("the graders rank every pair of models the same way."
                                            if not flips else
                                            "the graders rank these pairs in opposite order: "
                                            + ", ".join(flips) + "."))
        for k in keys:
            if A[k][field] != B[k][field]:
                line = resp_a[k]
                d = {"model": k[0], "id": k[1], "family": family,
                     "subtype": A[k].get("subtype"), "held_back": A[k].get("held_back"),
                     "prompt": line["prompt"]}
                for f in DETAIL_FIELDS[family]:
                    if f in line:
                        d[f] = line[f]
                d["response"] = line["response"]
                for name, rec in ((na, A[k]), (nb, B[k])):
                    d[name] = {f: rec[f] for f in VERDICT_FIELDS[family]}
                disagreements.append(d)
    if args.hand:
        out += [""] + hand_section(args, [(na, A), (nb, B)], resp_a, man_a["models"])

    path = args.disagreements or os.path.join(os.path.dirname(dir_a),
                                              f"disagreements-{na}-vs-{nb}.jsonl")
    write_jsonl(path, disagreements)
    txt = os.path.splitext(path)[0] + ".txt"
    with open(txt, "w", encoding="utf-8") as f:
        f.write(f"Responses where {na} and {nb} gave a different grade ({len(disagreements)})\n")
        for d in disagreements:
            field = LABEL[d["family"]]
            f.write("\n" + "=" * 90 + f"\n{d['model']}  {d['id']}  ({d['family']}, "
                    f"{d['subtype']}{', held back' if d['held_back'] else ''})\n")
            f.write(f"PROMPT: {d['prompt']}\n")
            for fld in DETAIL_FIELDS[d["family"]]:
                if fld in d:
                    f.write(f"{fld.upper()}: {d[fld]}\n")
            f.write(f"RESPONSE:\n{d['response']}\n")
            for name in (na, nb):
                f.write(f"{name}: {field} = {d[name][field]}. {d[name]['reason']}\n")
    out += ["", f"{len(disagreements)} disagreements written to {path} (and, easier to read, "
                f"{txt}). Each has the prompt, the response and both graders' verdicts and "
                f"reasons."]
    print("\n".join(out))
    return 0


# ------------------------------------------------------------------------------ init-run

def cmd_init_run(args):
    """Create RUNS_ROOT/<date>-<slug>/ with run.json and the responses, one file per model."""
    R = rf()
    name = args.name.strip()
    if not R.RUN_NAME.match(name):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", name):
            die(f"--name {name!r}: use a short slug of lowercase letters, digits and '-' (for "
                f"example four-models); the date is added in front")
        name = f"{R.today()}-{name}"
    root = os.path.abspath(args.root)
    run_dir = os.path.join(root, name)
    if os.path.exists(run_dir):
        die(f"{run_dir} already exists; choose another --name")
    items = load_items(args.items)
    by_model, sources = {}, {}
    for path in args.responses:
        rows = load_responses(path)
        check_known_ids(rows, items, path)
        for r in rows:
            m = r["model"]
            if not R.SAFE_ID.match(m):
                die(f"model name {m!r} in {path} cannot be used as a file name; use letters, "
                    f"digits, '.', '_' or '-'")
            if m in sources and sources[m] != path:
                die(f"the model name {m!r} appears in both {sources[m]} and {path}")
            sources[m] = path
            if r["id"] in by_model.setdefault(m, {}):
                die(f"response id {r['id']} appears twice for model {m!r}")
            by_model[m][r["id"]] = r
    if not by_model:
        die("the responses files have no responses")
    held = sorted({i for rows in by_model.values() for i in rows if items[i]["_held_back"]})
    if held and R.held_back_root_problem(root):
        die(f"{len(held)} of the responses are to held-back items; "
            + R.held_back_root_problem(root))
    models = []
    if args.models_json:
        with open(args.models_json, encoding="utf-8") as f:
            models = json.load(f)
        if not isinstance(models, list) or not all(isinstance(m, dict) and "id" in m
                                                   for m in models):
            die(f"{args.models_json} must hold a JSON list of objects with at least an 'id'")
    listed = {m["id"] for m in models}
    models += [{"id": m, "label": m, "description": "", "charter": ""}
               for m in by_model if m not in listed]

    os.makedirs(os.path.join(run_dir, R.RESPONSES_DIR))
    for sub in (R.GRADES_DIR, R.REVIEW_DIR, R.RATINGS_DIR):
        os.makedirs(os.path.join(run_dir, sub))
    for m, rows in by_model.items():
        write_jsonl(os.path.join(run_dir, R.RESPONSES_DIR, f"{m}.jsonl"), rows.values())
    gen = {"prompt_template": "Question: {prompt}\\nAnswer:", "max_new_tokens": {},
           "hit_length_limit": {}, "source_files": {m: os.path.basename(p)
                                                     for m, p in sources.items()}}
    for m, rows in by_model.items():
        for i, r in rows.items():
            fam = items[i]["family"]
            if "max_new_tokens" in r:
                gen["max_new_tokens"].setdefault(fam, set()).add(r["max_new_tokens"])
        gen["hit_length_limit"][m] = sum(bool(r.get("hit_limit")) for r in rows.values())
    gen["max_new_tokens"] = {f: sorted(v) if len(v) > 1 else next(iter(v))
                             for f, v in gen["max_new_tokens"].items()}
    if args.generation_notes:
        gen["notes"] = args.generation_notes
    run = {
        "format": R.RUN_FORMAT,
        "name": name,
        "title": args.title,
        "description": args.description,
        "created": now(),
        "models": models,
        "item_sets": [R.item_set_record(p) for p in args.items],
        "includes_held_back": bool(held),
        "responses": {m: len(rows) for m, rows in by_model.items()},
        "generation": gen,
        "graders": [],
        "status": "responses ready",
        "next_steps": [f"Grade it: ask Claude \"grade run {name} with Sonnet\"."],
    }
    if any(not m.get("description") for m in models):
        run["next_steps"].append("Describe each model in run.json (how it was trained, which "
                                 "constitution), or ask Claude to fill it in.")
    R.save_run(run_dir, run)
    with open(os.path.join(run_dir, "notes.md"), "w", encoding="utf-8") as f:
        f.write(f"# {args.title}\n\n{args.description}\n\nFree notes about this run.\n")
    n = sum(len(r) for r in by_model.values())
    print(f"Created {run_dir}\n  {n} responses from {len(by_model)} model(s) "
          f"({', '.join(by_model)}), {len(held)} of the items held back.\n  Status: responses "
          f"ready.\nNext: python3 {os.path.relpath(os.path.abspath(__file__))} prepare --run "
          f"{os.path.relpath(run_dir)} --grader sonnet-low --grader-model sonnet")
    return 0


# ------------------------------------------------------------------------------ audit-sample

# Signals that a verdict is likely to be hard to get right, with weights and plain wording.
AUDIT_SIGNALS = {
    "string_match_disagrees": (2, "the grader and the string-match check disagree"),
    "score_1": (2, "a partial score of 1"),
    "keywords_disagree": (2, "the grader's details lists and the keyword check disagree"),
    "hit_length_limit": (1, "the response hit the length limit"),
    "rambles": (1, "the response is more than twice as long as is usual for its family"),
    "placeholder": (1, "the message contains a placeholder such as [Your Name]"),
    "truncated": (1, "the message is cut off"),
}


def audit_signals(rec, row, long_words):
    """Which AUDIT_SIGNALS apply to one graded response."""
    out = []
    fam = rec["family"]
    if fam == "facts" and "string_match_correct" in rec \
            and rec["correct"] != bool(rec["string_match_correct"]):
        out.append("string_match_disagrees")
    if fam == "user_says_something_wrong" and rec.get("score") == 1:
        out.append("score_1")
    if fam == "emotional_social":
        if "keywords_found" in rec and set(rec.get("details_used", [])) != set(rec["keywords_found"]):
            out.append("keywords_disagree")
        if rec.get("has_placeholder"):
            out.append("placeholder")
        if rec.get("truncated"):
            out.append("truncated")
    hit = row.get("hit_limit")
    if hit is None and row.get("new_tokens") and row.get("max_new_tokens"):
        hit = row["new_tokens"] >= row["max_new_tokens"]
    if hit:
        out.append("hit_length_limit")
    if len(row.get("response", "").split()) > long_words[fam]:
        out.append("rambles")
    return out


def round_robin(models, pools, quota, rng, already=None):
    """Take up to quota entries, one model at a time, each from the front of its pool.

    Models with fewer picks so far (already: {model: count}) go first; ties are broken at
    random, so no model always gets the extra pick when quota does not divide evenly.
    """
    already = already or {}
    tie = {m: rng.random() for m in models}
    order = sorted(models, key=lambda m: (already.get(m, 0), tie[m]))
    taken = []
    while len(taken) < quota and any(pools[m] for m in order):
        for m in order:
            if len(taken) < quota and pools[m]:
                taken.append(pools[m].pop(0))
    return taken


def choose_audit_sample(records, responses, n, seed, random_share, models):
    """Pick n of one grader's verdicts, stratified by family and evaluated model.

    Each family gets an equal share of n (when it has enough responses), and each evaluated
    model an equal share of its family's picks (give or take one). About random_share of each
    family's picks are random; the rest are targeted: within each model, the responses with
    the most or strongest AUDIT_SIGNALS. A model with too few flagged responses gets random
    ones instead, so the per-model balance holds.
    """
    rng = random.Random(seed)
    words = collections.defaultdict(list)
    for rec in records:
        words[rec["family"]].append(len(responses[(rec["model"], rec["id"])]["response"].split()))
    long_words = {f: 2 * sorted(w)[len(w) // 2] for f, w in words.items()}
    fams = [f for f in FAMILIES if words.get(f)]
    size = {f: sum(r["family"] == f for r in records) for f in fams}
    quota = split_quota(min(n, len(records)), fams, size, rng)
    chosen = []
    for f in fams:
        cells = collections.defaultdict(list)
        for r in records:
            if r["family"] != f:
                continue
            sig = audit_signals(r, responses[(r["model"], r["id"])], long_words)
            cells[r["model"]].append(dict(r, _signals=sig, _tie=rng.random(),
                                          _priority=sum(AUDIT_SIGNALS[s][0] for s in sig)))
        fam_models = [m for m in models if cells.get(m)] + sorted(
            m for m in cells if m not in models)
        cell_quota = split_quota(quota[f], fam_models, {m: len(cells[m]) for m in fam_models},
                                 rng)
        flagged = {m: sorted([r for r in cells[m] if r["_priority"] > 0],
                             key=lambda r: (-r["_priority"], r["_tie"])) for m in fam_models}
        # Random slots go first to the models with the fewest flagged responses.
        n_random = round(quota[f] * random_share)
        by_need = sorted(fam_models, key=lambda m: (len(flagged[m]) - cell_quota[m], rng.random()))
        random_slots = collections.Counter()
        while n_random > 0 and any(random_slots[m] < cell_quota[m] for m in fam_models):
            for m in by_need:
                if n_random > 0 and random_slots[m] < cell_quota[m]:
                    random_slots[m] += 1
                    n_random -= 1
        for m in fam_models:
            targeted = flagged[m][:cell_quota[m] - random_slots[m]]
            for r in targeted:
                r["_picked"] = "targeted"
            taken = {r["id"] for r in targeted}
            rest = [r for r in cells[m] if r["id"] not in taken]
            rng.shuffle(rest)
            extra = rest[:cell_quota[m] - len(targeted)]
            for r in extra:
                r["_picked"] = "random"
            chosen += targeted + extra
    return chosen, long_words


def split_quota(total, groups, size, rng):
    """Split total as evenly as possible over groups, never giving a group more than
    size[group]; the extra ones go to groups in random order."""
    quota = {g: 0 for g in groups}
    left = min(total, sum(size[g] for g in groups))
    while left > 0:
        open_g = [g for g in groups if quota[g] < size[g]]
        share, extra = divmod(left, len(open_g))
        order = sorted(open_g, key=lambda g: rng.random())
        for i, g in enumerate(order):
            add = min(share + (1 if i < extra else 0), size[g] - quota[g])
            quota[g] += add
            left -= add
    return quota


def cmd_audit_sample(args):
    R = rf()
    run_dir = os.path.abspath(args.run)
    run = R.load_run(run_dir)
    if args.source not in R.grader_ids(run_dir):
        die(f"--from {args.source}: no grader with that id in {run_dir} (graders: "
            f"{', '.join(R.grader_ids(run_dir)) or 'none'})")
    records = R.load_grade_records(run_dir, args.source)
    if not records:
        die(f"grader {args.source} has no merged grades; run merge first")
    if not 0 <= args.random_share <= 1:
        die("--random-share must be between 0 and 1")
    responses = R.load_responses(run_dir)
    items = load_items(R.run_item_paths(run))
    records = [r for r in records if (r["model"], r["id"]) in responses
               and (not args.visible_only or not items.get(r["id"], {}).get("_held_back"))]
    agent = R.agent_settings(R.AUDITOR_AGENT)
    if not agent.get("exists"):
        die(f"no {R.AUDITOR_AGENT} agent at {agent['file']}; the audit needs it")
    model = args.grader_model or agent.get("model") or "opus"
    effort = agent.get("effort") or "medium"
    gid = args.grader or f"{model}-{effort}-audit"
    check_grader_id(gid)
    if gid == args.source:
        die("the audit grader needs its own id, different from --from")
    models = R.model_order(run, responses)
    chosen, long_words = choose_audit_sample(records, responses, args.n, args.seed,
                                             args.random_share, models)
    keep = {(r["model"], r["id"]) for r in chosen}
    sample = {"from": args.source, "n": len(chosen), "requested": args.n, "seed": args.seed,
              "random_share": args.random_share, "visible_only": args.visible_only,
              "long_response_words": long_words,
              "signals": {k: v[1] for k, v in AUDIT_SIGNALS.items()},
              "by_family": {}, "by_model": {}}
    for r in chosen:
        fam = sample["by_family"].setdefault(r["family"], {"targeted": 0, "random": 0})
        fam[r["_picked"]] += 1
        sample["by_model"][r["model"]] = sample["by_model"].get(r["model"], 0) + 1
    grader = {"via": "claude-code-subagent", "id": gid, "agent": R.AUDITOR_AGENT,
              "model": model, "effort": effort}
    out_dir = R.grader_dir(run_dir, gid)
    info = R.make_grader_info(gid, R.AUDITOR_AGENT, model, effort, None, {
        "audit_of": args.source, "covers": f"an audit sample of {len(chosen)} of "
                                           f"{args.source}'s verdicts", "sample": sample})
    manifest = prepare_folder(out_dir, R.response_files(run_dir), R.run_item_paths(run), grader,
                              args.seed, args.batch_size, args.force, keep=keep)
    if not manifest.get("_again") or not os.path.exists(os.path.join(out_dir, R.GRADER_JSON)):
        # Which items were picked and why. The auditor never reads this file (its prompt names
        # only its batch), and the batches themselves carry no verdicts.
        write_jsonl(os.path.join(out_dir, "audit-sample.jsonl"), [
            {"model": r["model"], "id": r["id"], "family": r["family"],
             "subtype": r.get("subtype"), "held_back": r.get("held_back", False),
             "picked": r["_picked"], "signals": r["_signals"]} for r in chosen])
        write_grader_files(run_dir, gid, info)
    rows = [[f, sample["by_family"].get(f, {}).get("targeted", 0),
             sample["by_family"].get(f, {}).get("random", 0)]
            + [sum(r["family"] == f and r["model"] == m for r in chosen) for m in models]
            for f in FAMILIES if f in sample["by_family"]]
    print(f"Audit sample: {len(chosen)} of {args.source}'s {len(records)} verdicts "
          f"(seed {args.seed}).")
    print("\n".join(table(["family", "targeted", "random"] + models, rows)))
    sig = collections.Counter(s for r in chosen for s in r["_signals"])
    print("  Signals among the picked responses: " + "; ".join(
        f"{AUDIT_SIGNALS[s][1]}: {k}" for s, k in sig.most_common()) + "\n")
    print_prepared(out_dir, manifest, again=manifest.get("_again"), run_dir=run_dir)
    print(f"Then build the review queue:\n  python3 {os.path.relpath(os.path.abspath(__file__))} "
          f"review-build --run {os.path.relpath(run_dir)} --judges {args.source} {gid}")
    return 0


# ------------------------------------------------------------------------------ review-build

def cmd_review_build(args):
    R = rf()
    run_dir = os.path.abspath(args.run)
    data = R.RunData(run_dir, include_held_back=True)
    a, b = args.judges
    for j in (a, b):
        if j not in data.judges:
            die(f"no judge {j!r} in {run_dir}; judges: {', '.join(data.judges) or 'none'}")
    if a == b:
        die("give two different judges")
    A, B = data.judges[a]["verdicts"], data.judges[b]["verdicts"]
    fam_rank = {f: i for i, f in enumerate(FAMILIES)}
    mod_rank = {m: i for i, m in enumerate(data.models)}
    common = sorted((k for k in A if k in B and A[k]["family"] == B[k]["family"]),
                    key=lambda k: (fam_rank.get(A[k]["family"], 9), mod_rank.get(k[0], 99), k[1]))
    if not common:
        die(f"{a} and {b} have no verdicts on the same responses")
    dis = [k for k in common if A[k]["label"] != B[k]["label"]]
    agree = [k for k in common if A[k]["label"] == B[k]["label"]]
    rng = random.Random(args.seed)
    pools = collections.defaultdict(list)
    for k in agree:
        pools[A[k]["family"]].append(k)
    for f in pools:
        rng.shuffle(pools[f])
    controls = round_robin([f for f in FAMILIES if pools.get(f)], pools,
                           min(args.controls, len(agree)), rng)
    rows = [(k, "disagreement") for k in dis] + [(k, "control") for k in controls]
    rng.shuffle(rows)
    name = f"queue-{a}-vs-{b}"
    rows_path, meta_path = R.queue_paths(run_dir, name)
    if os.path.exists(rows_path):
        if not args.force:
            die(f"{rows_path} already exists. Reviews already given stay valid (they record the "
                f"model and item), but positions would change. Add --force to replace it; the "
                f"old queue is moved to review/replaced-<date-time>/.")
        keep_dir = os.path.join(run_dir, R.REVIEW_DIR,
                                "replaced-" + dt.datetime.now().strftime("%Y%m%d-%H%M%S"))
        os.makedirs(keep_dir)
        for p in (rows_path, meta_path):
            if os.path.exists(p):
                shutil.move(p, os.path.join(keep_dir, os.path.basename(p)))
    used, out = set(), []
    for n, (k, kind) in enumerate(rows, 1):
        qk = f"q{rng.getrandbits(32):08x}"
        while qk in used:
            qk = f"q{rng.getrandbits(32):08x}"
        used.add(qk)
        g1, g2 = (a, b) if rng.random() < 0.5 else (b, a)
        out.append({"queue_key": qk, "n": n, "model": k[0], "id": k[1],
                    "family": A[k]["family"], "subtype": data.items[k[1]].get("subtype"),
                    "held_back": data.held_back(k[1]), "kind": kind,
                    "grader_1": g1, "grader_2": g2})
    os.makedirs(os.path.join(run_dir, R.REVIEW_DIR), exist_ok=True)
    write_jsonl(rows_path, out)
    by_family = {f: {"disagreements": sum(1 for r in out if r["family"] == f
                                          and r["kind"] == "disagreement"),
                     "controls": sum(1 for r in out if r["family"] == f and r["kind"] == "control")}
                 for f in FAMILIES if any(r["family"] == f for r in out)}
    k = len(R.queue_names(run_dir))
    meta = {"format": "review queue 1", "name": name,
            "label": args.label or f"Review set {k} (built {R.today()})",
            "judges": [a, b], "created": now(), "seed": args.seed,
            "controls_requested": args.controls,
            "counts": {"items": len(out), "disagreements": len(dis), "controls": len(controls),
                       "compared": len(common), "by_family": by_family},
            "note": "Each item shows the two judges as Grader 1 and Grader 2, assigned at random "
                    "per item (grader_1/grader_2 here). The viewer never shows judge ids or "
                    "evaluated-model names while reviewing. Controls are items where the two "
                    "judges agree, mixed in to catch cases where both are wrong."}
    R.write_json(meta_path, meta)
    run = R.load_run(run_dir)
    run["next_steps"] = [
        f"Review the {len(out)} items in the viewer ({viewer_command(run_dir)}): Review page, "
        f"\"{meta['label']}\". Grade each item yourself first, then decide which grader was right.",
        f"Then write one or two rubric clarifications in a notes file and ask Claude: "
        f"\"re-grade the review queue of run {run['name']} with Sonnet using my rubric notes in "
        f"<file>\"."]
    R.save_run(run_dir, run)
    print(f"Wrote {os.path.relpath(rows_path)}: {len(out)} items, {len(dis)} where {a} and {b} "
          f"disagree and {len(controls)} controls where they agree (of {len(common)} responses "
          f"both judged).")
    print("\n".join(table(["family", "disagreements", "controls"],
                          [[f, v["disagreements"], v["controls"]] for f, v in by_family.items()])))
    print(f"\nNext: open the viewer and go to the Review page:\n  {viewer_command(run_dir)}")
    return 0


# ------------------------------------------------------------------------------ main

def parse_args(argv):
    ap = argparse.ArgumentParser(
        prog="grade_tools.py", description=__doc__.split("\n\n")[0],
        epilog="\n\n".join(__doc__.split("\n\n")[1:]),
        formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    def run_options(p, grader_help):
        p.add_argument("--run", metavar="RUNDIR",
                       help="a run folder (see evals/runs/README.md); use with --grader")
        p.add_argument("--grader", metavar="ID", help=grader_help)

    p = sub.add_parser(
        "init-run", help="create a run folder with run.json and the responses",
        description="Create ROOT/<date>-<slug>/ with run.json (name, title, description, models, "
                    "item sets, generation settings, status, next steps), the responses split "
                    "into responses/<model-id>.jsonl, and empty grades/, review/ and ratings/ "
                    "folders. Runs that include held-back items must go under planning/runs/.")
    p.add_argument("--root", required=True, metavar="ROOT",
                   help="the runs folder: evals/runs for student runs, planning/runs for runs "
                        "that include held-back items")
    p.add_argument("--name", required=True, metavar="SLUG",
                   help="short name such as four-models; today's date is put in front")
    p.add_argument("--title", required=True, help="plain-language title")
    p.add_argument("--description", required=True,
                   help="plain-language description: which models, why, what to look for")
    p.add_argument("--responses", required=True, nargs="+", metavar="FILE",
                   help="responses files (one JSON line per item with 'id', 'response', 'model')")
    p.add_argument("--items", required=True, action="extend", nargs="+", metavar="DIR",
                   help="item folders (or files) the responses' ids come from")
    p.add_argument("--models-json", metavar="FILE",
                   help="optional JSON list of {id, label, description, charter} for the models")
    p.add_argument("--generation-notes", metavar="TEXT",
                   help="optional notes on how the responses were generated")
    p.set_defaults(func=cmd_init_run)

    p = sub.add_parser(
        "prepare", help="split responses into batch files for the eval-grader subagents",
        description="Join the responses with their test items, mix the evaluated models "
                    "together, and write one batch file per group of responses from one family, "
                    "plus DIR/manifest.json, which records which model and item each opaque key "
                    "stands for. Prints a table of batches with the input and output path for "
                    "each grader. Running it again on the same folder with the same inputs "
                    "changes nothing; with different inputs it refuses unless you add --force. "
                    "With --run RUNDIR --grader ID the folder is RUNDIR/grades/ID, the responses "
                    "and items come from the run, and grader.json records the grader.")
    p.add_argument("responses", nargs="*", metavar="RESPONSES.jsonl",
                   help="responses files: one JSON line per item with 'id', 'response' and "
                        "(optional) 'model'; give all the models you want graded together. With "
                        "--run, the default is every file in RUNDIR/responses/")
    p.add_argument("--items", action="append", metavar="FOLDER_OR_FILE",
                   help="folder of item .jsonl files (or one file) to look up ids in; repeat to "
                        "add more. Default: the folder this script is in (with --run: the item "
                        "sets in run.json)")
    p.add_argument("--out-dir", metavar="DIR",
                   help="folder for the batches, the graders' output and the merged grades; use "
                        "one folder per grader model (not used with --run)")
    run_options(p, "grader id, such as sonnet-low (default: <model>-<effort>); the folder is "
                   "RUNDIR/grades/ID")
    p.add_argument("--batch-size", type=int, default=60, metavar="N",
                   help="at most N responses per batch, that is, per grader (default 60)")
    p.add_argument("--seed", type=int, default=440,
                   help="random seed for mixing the models and for the keys (default 440)")
    p.add_argument("--grader-model", choices=["opus", "sonnet"], default=None,
                   help="which Claude model will grade, recorded with every verdict "
                        "(default sonnet)")
    p.add_argument("--rubric-notes", metavar="FILE",
                   help="a file of rubric clarifications for the grader to apply on top of the "
                        "rubric; copied into the grader folder, hashed in grader.json, and named "
                        "in each grader's prompt as 'Rubric notes: PATH'")
    p.add_argument("--subset-from", metavar="GRADER_OR_QUEUE",
                   help="with --run: grade only the responses that another grader in the run "
                        "graded (for example the audit sample), or that are in a review queue "
                        "(queue-A-vs-B)")
    p.add_argument("--force", action="store_true",
                   help="start over in a folder that already holds a different run; the old "
                        "files are moved into DIR/replaced-<date-time>/, not deleted")
    p.set_defaults(func=cmd_prepare)

    p = sub.add_parser("status", help="show which batches have verdicts",
                       description="For each batch: missing (no output yet), partial (some "
                                   "lines missing), complete, or invalid (some lines break the "
                                   "rules), with counts. With --run and no --grader, every "
                                   "grader in the run.")
    p.add_argument("dir", metavar="DIR", nargs="?",
                   help="the folder given to prepare as --out-dir")
    run_options(p, "grader id in the run")
    p.set_defaults(func=cmd_status)

    p = sub.add_parser(
        "merge", help="check the verdicts, write grades.jsonl and the summary, list redos",
        description="Check every line the graders wrote (valid JSON, a key from the right "
                    "batch, every field present with an allowed value, no duplicates). Write "
                    "DIR/grades.jsonl (one record per response, with the string-match "
                    "cross-checks) and the summary tables to DIR/summary.txt. Lines that are "
                    "missing or invalid go to DIR/redo/batch-NN-<family>.jsonl, with the output "
                    "path each redo grader should write; run merge again afterwards and it picks "
                    "those up. Inside a run folder it also updates grader.json and run.json.")
    p.add_argument("dir", metavar="DIR", nargs="?",
                   help="the folder given to prepare as --out-dir")
    run_options(p, "grader id in the run")
    p.add_argument("--items", action="append", metavar="FOLDER_OR_FILE",
                   help="item folders, if they have moved since prepare (default: the ones "
                        "recorded in the manifest)")
    p.set_defaults(func=cmd_merge)

    p = sub.add_parser(
        "compare", help="compare two graders on the same responses",
        description="Compare two graded folders (for example one graded by Opus and one by "
                    "Sonnet) on the responses both graded: agreement and Cohen's kappa for each "
                    "family, a table of 0/1/2 scores, the headline number for each evaluated "
                    "model under each grader (do the conclusions change?), agreement with hand "
                    "grades, and every disagreement written out for a person to read. The "
                    "viewer's Judges page shows the same for any two judges in a run.")
    p.add_argument("dir_a", metavar="DIR_A", nargs="?", help="first graded folder (after merge)")
    p.add_argument("dir_b", metavar="DIR_B", nargs="?", help="second graded folder (after merge)")
    p.add_argument("--run", metavar="RUNDIR", help="a run folder; use with --graders A B")
    p.add_argument("--graders", nargs=2, metavar=("A", "B"), help="two grader ids in the run")
    p.add_argument("--disagreements", metavar="OUT.jsonl",
                   help="where to write the disagreements (a .txt copy for reading goes next to "
                        "it). Default: disagreements-A-vs-B.jsonl next to DIR_A")
    p.set_defaults(func=cmd_compare)

    for p in (sub.choices["merge"], sub.choices["compare"]):
        p.add_argument("--hand", metavar="HAND.jsonl",
                       help="hand grades of user_says_something_wrong items: JSON lines with "
                            "'id' and 'score' (or 'score_implicit_as_1' and "
                            "'score_implicit_as_2'); reports how often each grader agrees")
        p.add_argument("--hand-model", metavar="NAME",
                       help="which evaluated model the hand grades are for (needed when several "
                            "models were graded and the hand grades have no 'model' field)")
        p.add_argument("--hand-responses", metavar="FILE",
                       help="the responses the hand grader saw (JSON lines with 'id' and "
                            "'response'); items whose response text has changed since are left "
                            "out of the agreement")

    p = sub.add_parser(
        "audit-sample", help="sample one grader's verdicts for a blind audit by eval-auditor",
        description="Pick about N of one grader's verdicts for a second, careful grader to "
                    "re-grade blind. Each family gets an equal share; within a family, most "
                    "picks are likely-hard responses (the grader and the string-match check "
                    "disagree, a partial score of 1, the details lists and the keyword check "
                    "disagree, the length limit was hit, the response rambles, the message has a "
                    "placeholder or is cut off), taken in turn from each evaluated model, and "
                    "the rest are random. Writes RUNDIR/grades/<audit id>/ with blind batches in "
                    "the usual format (no verdicts in them), audit-sample.jsonl (what was picked "
                    "and why) and grader.json. Then spawn one eval-auditor per batch and merge.")
    p.add_argument("--run", required=True, metavar="RUNDIR", help="the run folder")
    p.add_argument("--from", dest="source", required=True, metavar="GRADER",
                   help="the grader whose verdicts are audited, such as sonnet-low")
    p.add_argument("--grader", metavar="ID",
                   help="id for the audit grader (default <model>-<effort>-audit, for example "
                        "opus-medium-audit)")
    p.add_argument("--grader-model", choices=["opus", "sonnet"], default=None,
                   help="model for the eval-auditor subagents (default: the agent's own, opus)")
    p.add_argument("--n", type=int, default=60, help="sample size (default 60)")
    p.add_argument("--random-share", type=float, default=0.25, metavar="SHARE",
                   help="share of each family's picks that are random rather than targeted "
                        "(default 0.25)")
    p.add_argument("--visible-only", action="store_true",
                   help="leave held-back items out of the sample")
    p.add_argument("--seed", type=int, default=440, help="random seed (default 440)")
    p.add_argument("--batch-size", type=int, default=60, metavar="N",
                   help="at most N responses per audit batch (default 60)")
    p.add_argument("--force", action="store_true",
                   help="replace an audit folder prepared differently (old files are moved "
                        "into replaced-<date-time>/)")
    p.set_defaults(func=cmd_audit_sample)

    p = sub.add_parser(
        "review-build", help="build a review queue of two judges' disagreements",
        description="Write RUNDIR/review/queue-A-vs-B.jsonl: every response where judges A and "
                    "B disagree, plus a few random ones where they agree (controls), shuffled. "
                    "Each item gets a random assignment of which judge is shown as Grader 1 and "
                    "which as Grader 2; the viewer's Review page never shows the judges' "
                    "identities or the evaluated models' names.")
    p.add_argument("--run", required=True, metavar="RUNDIR", help="the run folder")
    p.add_argument("--judges", required=True, nargs=2, metavar=("A", "B"),
                   help="two judge ids, usually a grader and its audit grader")
    p.add_argument("--controls", type=int, default=5, metavar="N",
                   help="how many items where the judges agree to mix in (default 5)")
    p.add_argument("--label", help="the queue's name as reviewers see it (default 'Review set "
                                   "K (built DATE)')")
    p.add_argument("--seed", type=int, default=440, help="random seed (default 440)")
    p.add_argument("--force", action="store_true",
                   help="replace an existing queue for the same two judges")
    p.set_defaults(func=cmd_review_build)
    return ap.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
