#!/usr/bin/env python3
"""Evaluation run folders: the files on disk, and the "judges" read from them.

A run folder holds one evaluation run: every evaluated model's responses, every set of
grades, people's review decisions and side-by-side ratings. The format is described in
evals/runs/README.md. This module is the one place that reads and writes those files:
grade_tools.py (the command line) and the viewer (evals/app/serve.py) both use it.

A "judge" is any set of verdicts on (evaluated model, item) pairs that can be compared with
another: a Claude grader run (grades/<grader-id>/grades.jsonl, including the audit grader and
Claude's hand grades), the string-match cross-check for facts, a reviewer's decisions in
review/, and a rater's task checks in ratings/.

Standard library only.
"""
from __future__ import annotations

import datetime as dt
import glob
import hashlib
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import grade_tools as gt  # noqa: E402  families, verdict fields, item loading, kappa
from validate import contains, cut_response, normalize  # noqa: E402  string-match judge

AGENTS_DIR = os.path.join(REPO, ".claude", "agents")
GRADER_AGENT = "eval-grader"
AUDITOR_AGENT = "eval-auditor"
RUBRIC_FILE = os.path.join(AGENTS_DIR, "eval-grader.md")   # the one copy of the grading rules

RUN_FORMAT = "eval run 1"
RUN_JSON = "run.json"
GRADER_JSON = "grader.json"
RESPONSES_DIR, GRADES_DIR, REVIEW_DIR, RATINGS_DIR = "responses", "grades", "review", "ratings"
WRITABLE_DIRS = (REVIEW_DIR, RATINGS_DIR)        # the only folders the viewer may write to
STATUSES = ["responses ready", "graded", "audited", "reviewed"]
DEFAULT_ROOTS = [os.path.join(REPO, "evals", "runs"), os.path.join(REPO, "planning", "runs")]

SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")      # model, grader, run ids
RUN_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}-[a-z0-9][a-z0-9-]{0,63}$")
QUEUE_NAME = re.compile(r"^queue-[A-Za-z0-9._-]{1,150}$")
RESERVED_JUDGE_PREFIXES = ("review-", "rater-", "string-match")

FAMILIES = gt.FAMILIES
FAMILY_TITLES = gt.FAMILY_TITLES
LABEL_FIELD = {"facts": "correct", "user_says_something_wrong": "score",
               "emotional_social": "does_task"}
LABEL_VALUES = {"facts": [True, False], "user_says_something_wrong": [0, 1, 2],
                "emotional_social": [True, False]}
FINAL_CALLS = ["grader_1", "grader_2", "both_wrong", "both_fine"]


# ------------------------------------------------------------------------------ small helpers

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def today():
    return dt.date.today().isoformat()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, obj):
    """Write JSON atomically (a half-written run.json would break every tool)."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
        f.write("\n")
    os.replace(tmp, path)


def read_jsonl(path):
    """Lines of a JSONL file; unreadable lines are skipped (append-only logs may be cut)."""
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def append_jsonl(path, obj):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())


def count(n, word):
    """'1 verdict', '2 verdicts'."""
    return f"{n} {word}" + ("" if n == 1 else "s")


def slug(text, limit=40):
    """'Ana María P.' -> 'ana-maria-p': safe for file names and judge ids."""
    s = normalize(str(text)).replace(".", " ")
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:limit].strip("-")


def inside(path, folder):
    path, folder = os.path.realpath(path), os.path.realpath(folder)
    return path == folder or path.startswith(folder + os.sep)


def repo_rel(path):
    """A path as recorded in run.json: relative to the repo root when it is inside it."""
    p = os.path.abspath(path)
    return os.path.relpath(p, REPO) if inside(p, REPO) else p


def from_repo(path):
    return path if os.path.isabs(path) else os.path.normpath(os.path.join(REPO, path))


def agent_settings(name):
    """The front matter of .claude/agents/<name>.md as a dict (model, effort, tools, ...)."""
    path = os.path.join(AGENTS_DIR, f"{name}.md")
    out = {"file": repo_rel(path), "exists": os.path.exists(path)}
    if not out["exists"]:
        return out
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.match(r"---\n(.*?)\n---\n", text, flags=re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
    return out


def rubric_section(family, path=RUBRIC_FILE):
    """The grading rules for one family, read from the grader's definition at call time.

    Returns the text of the '### Family `<family>`' section (up to the next heading), so the
    viewer can show a reminder without keeping a second copy of the rules.
    """
    if family not in FAMILIES or not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        text = f.read()
    m = re.search(rf"^### Family `{re.escape(family)}`\s*\n(.*?)(?=^#{{2,3}} |\Z)", text,
                  flags=re.S | re.M)
    return m.group(1).strip() if m else ""


# ------------------------------------------------------------------------------ run.json

def is_run(path):
    return os.path.isfile(os.path.join(path, RUN_JSON))


def load_run(run_dir):
    path = os.path.join(run_dir, RUN_JSON)
    if not os.path.exists(path):
        raise FileNotFoundError(f"{run_dir} is not a run folder (no {RUN_JSON})")
    run = read_json(path)
    run.setdefault("name", os.path.basename(os.path.abspath(run_dir)))
    for key, default in (("models", []), ("item_sets", []), ("graders", []),
                         ("next_steps", []), ("status", "responses ready"),
                         ("title", run["name"]), ("description", ""), ("generation", {})):
        run.setdefault(key, default)
    return run


def save_run(run_dir, run):
    run["updated"] = now()
    write_json(os.path.join(run_dir, RUN_JSON), run)


def advance_status(run, status):
    """Move the run's status forward (never back): responses ready -> graded -> ..."""
    old = run.get("status")
    if old not in STATUSES or STATUSES.index(status) > STATUSES.index(old):
        run["status"] = status
        return True
    return False


def find_run_dir(path):
    """The run folder that contains path (a grades/<id> folder, say), or None."""
    p = os.path.abspath(path)
    for _ in range(4):
        if is_run(p):
            return p
        parent = os.path.dirname(p)
        if parent == p:
            break
        p = parent
    return None


def held_back_root_problem(run_dir):
    """A message if a run folder that holds held-back items sits in evals/ or .claude/."""
    for folder in ("evals", ".claude"):
        if inside(run_dir, os.path.join(REPO, folder)):
            return (f"this run includes held-back items, which must never be copied into "
                    f"{folder}/. Put the run under planning/runs/ instead.")
    return None


def run_item_paths(run):
    return [from_repo(s["path"]) for s in run.get("item_sets", [])]


def item_set_record(path):
    """What run.json says about one --items folder or file."""
    files = sorted(glob.glob(os.path.join(path, "*.jsonl"))) if os.path.isdir(path) else [path]
    out = {"path": repo_rel(path), "files": []}
    for f in files:
        fams, n = set(), 0
        for row in read_jsonl(f):
            if "id" in row and "family" in row:
                n += 1
                fams.add(row["family"])
        out["files"].append({"file": os.path.basename(f), "families": sorted(fams), "items": n,
                             "held_back": "hidden" in os.path.basename(f)})
    out["items"] = sum(x["items"] for x in out["files"])
    out["held_back"] = any(x["held_back"] for x in out["files"])
    return out


def response_files(run_dir):
    return sorted(glob.glob(os.path.join(run_dir, RESPONSES_DIR, "*.jsonl")))


def load_responses(run_dir):
    """(model, item id) -> the response row, from responses/<model-id>.jsonl."""
    out = {}
    for path in response_files(run_dir):
        stem = os.path.splitext(os.path.basename(path))[0]
        for r in read_jsonl(path):
            if "id" not in r:
                continue
            model = str(r.get("model") or stem)
            r = dict(r, model=model, response="" if r.get("response") is None
                     else str(r.get("response")))
            out[(model, r["id"])] = r
    return out


def model_order(run, responses):
    listed = [m["id"] for m in run.get("models", []) if isinstance(m, dict) and "id" in m]
    seen = list(dict.fromkeys(m for m, _ in responses))
    return [m for m in listed if m in seen] + [m for m in seen if m not in listed]


# ------------------------------------------------------------------------------ graders

def grader_dir(run_dir, grader_id):
    return os.path.join(run_dir, GRADES_DIR, grader_id)


def grader_ids(run_dir):
    out = []
    for d in sorted(glob.glob(os.path.join(run_dir, GRADES_DIR, "*"))):
        name = os.path.basename(d)
        if os.path.isdir(d) and SAFE_ID.match(name) and (
                os.path.exists(os.path.join(d, GRADER_JSON))
                or os.path.exists(os.path.join(d, "grades.jsonl"))):
            out.append(name)
    return out


def load_grader_info(run_dir, grader_id):
    path = os.path.join(grader_dir(run_dir, grader_id), GRADER_JSON)
    info = read_json(path) if os.path.exists(path) else {}
    info.setdefault("id", grader_id)
    info.setdefault("kind", "claude-subagent")
    return info


def save_grader_info(run_dir, grader_id, info):
    write_json(os.path.join(grader_dir(run_dir, grader_id), GRADER_JSON), info)


def make_grader_info(grader_id, agent, model, effort, notes_path=None, extra=None):
    """grader.json for a Claude subagent grader, with the rubric's (and notes') hashes."""
    info = {
        "id": grader_id,
        "kind": "claude-subagent",
        "agent": agent,
        "agent_file": repo_rel(os.path.join(AGENTS_DIR, f"{agent}.md")),
        "model": model,
        "effort": effort,
        "rubric_file": repo_rel(RUBRIC_FILE),
        "rubric_sha256": sha256_file(RUBRIC_FILE) if os.path.exists(RUBRIC_FILE) else None,
        "rubric_notes_file": None,
        "rubric_notes_sha256": None,
        "created": now(),
        "status": "prepared",
    }
    if agent != GRADER_AGENT:
        path = os.path.join(AGENTS_DIR, f"{agent}.md")
        info["agent_sha256"] = sha256_file(path) if os.path.exists(path) else None
    if notes_path:
        info["rubric_notes_file"] = repo_rel(notes_path)
        info["rubric_notes_sha256"] = sha256_file(notes_path)
    info.update(extra or {})
    return info


def load_grade_records(run_dir, grader_id):
    return read_jsonl(os.path.join(grader_dir(run_dir, grader_id), "grades.jsonl"))


def grader_description(info, n):
    """One plain sentence about a grader, for the viewer."""
    kind = info.get("kind")
    model = info.get("model")
    who = {"opus": "Claude Opus", "sonnet": "Claude Sonnet",
           "haiku": "Claude Haiku"}.get(str(model), str(model) if model else "Claude")
    if kind == "claude-subagent":
        text = (f"{who} at {info.get('effort', '?')} effort, as the "
                f"{info.get('agent', GRADER_AGENT)} subagent")
        if info.get("audit_of"):
            text += f", auditing a sample of {info['audit_of']}'s grades (blind to them)"
        if info.get("rubric_notes_file"):
            text += f", with rubric notes from {info['rubric_notes_file']}"
    elif kind == "claude-session":
        text = "Claude in the main Claude Code session (not a subagent)"
    elif kind == "human":
        text = "A person"
    else:
        text = str(kind)
    return f"{text}; {count(n, 'verdict')}."


# ------------------------------------------------------------------------------ verdicts

def verdict_from_record(rec):
    """A grades.jsonl record -> the judge-neutral verdict used everywhere in the viewer."""
    family = rec.get("family")
    if family not in LABEL_FIELD or LABEL_FIELD[family] not in rec:
        return None
    label = rec[LABEL_FIELD[family]]
    fields = {k: rec[k] for k in gt.VERDICT_FIELDS[family] if k in rec and k != "reason"}
    for extra in ("all_details", "string_match_correct", "keywords_found", "keywords_missing"):
        if extra in rec:
            fields[extra] = rec[extra]
    return {"family": family, "label": label,
            "label2": rec.get("score_implicit_as_2", label) if family == "user_says_something_wrong"
            else label,
            "fields": fields, "reason": str(rec.get("reason") or "")}


def grade_to_label(family, grade):
    """A reviewer's grade ({"correct": true} / {"score": 1} / {"does_task": false})."""
    if not isinstance(grade, dict):
        return None
    v = grade.get(LABEL_FIELD.get(family, ""))
    if family == "user_says_something_wrong":
        return v if isinstance(v, int) and not isinstance(v, bool) and v in (0, 1, 2) else None
    return v if isinstance(v, bool) else None


def check_grade(family, grade):
    """None if grade is a valid reviewer grade for the family, else a message."""
    if family not in LABEL_FIELD:
        return f"unknown family {family!r}"
    if not isinstance(grade, dict) or set(grade) != {LABEL_FIELD[family]}:
        return f"the grade must be an object with exactly the field {LABEL_FIELD[family]!r}"
    if grade_to_label(family, grade) is None:
        want = "0, 1 or 2" if family == "user_says_something_wrong" else "true or false"
        return f"{LABEL_FIELD[family]} must be {want}"
    return None


def string_match_verdict(item, response):
    """The facts string-match cross-check (validate.score_fact) as a verdict with a reason."""
    text = normalize(cut_response(response))
    rejects = [r for r in item.get("reject_if_also", []) if contains(text, r)]
    accepts = [a for a in item.get("accept", []) if contains(text, a)]
    ok = bool(accepts) and not rejects
    if rejects:
        reason = f"contains {rejects[0]!r}, which is on the reject list"
    elif accepts:
        reason = f"contains the accepted answer {accepts[0]!r} somewhere in the response"
    else:
        reason = "no accepted answer appears in the response"
    return {"family": "facts", "label": ok, "label2": ok, "fields": {"correct": ok},
            "reason": f"String match: {reason}."}


# ------------------------------------------------------------------------------ review queues

def queue_names(run_dir):
    out = []
    for p in sorted(glob.glob(os.path.join(run_dir, REVIEW_DIR, "queue-*.jsonl"))):
        name = os.path.splitext(os.path.basename(p))[0]
        if QUEUE_NAME.match(name):
            out.append(name)
    return out


def queue_public_id(name):
    """An opaque id for a queue. The file name says which two judges it compares, so the
    Review page refers to queues only by this id (and by their neutral label)."""
    return "set-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]


def queue_paths(run_dir, name):
    base = os.path.join(run_dir, REVIEW_DIR, name)
    return base + ".jsonl", base + ".meta.json"


def load_queue(run_dir, name):
    """(meta, rows) of review/<name>.jsonl and its .meta.json."""
    if not QUEUE_NAME.match(name):
        raise ValueError(f"not a queue name: {name!r}")
    rows_path, meta_path = queue_paths(run_dir, name)
    if not os.path.exists(rows_path):
        raise FileNotFoundError(f"no review queue {name}")
    meta = read_json(meta_path) if os.path.exists(meta_path) else {}
    meta.setdefault("name", name)
    meta.setdefault("label", name)
    return meta, read_jsonl(rows_path)


def decision_files(run_dir):
    return sorted(glob.glob(os.path.join(run_dir, REVIEW_DIR, "decisions-*.jsonl")))


def decisions_file(run_dir, reviewer):
    return os.path.join(run_dir, REVIEW_DIR, f"decisions-{slug(reviewer)}.jsonl")


def review_state(run_dir):
    """{(reviewer slug, queue, queue key): {"blind": event, "final": event}} from review/.

    Append-only logs: the first blind grade counts (it is locked once given) and the latest
    final call counts (a reviewer may fix a typo in a reason).
    """
    state = {}
    for path in decision_files(run_dir):
        for ev in read_jsonl(path):
            if ev.get("event") not in ("blind", "final") or not ev.get("reviewer"):
                continue
            k = (slug(ev["reviewer"]), ev.get("queue"), ev.get("queue_key"))
            s = state.setdefault(k, {})
            if ev["event"] == "blind":
                s.setdefault("blind", ev)
            else:
                s["final"] = ev
    return state


def reviewer_final_label(family, final, blind):
    """The grade a reviewer's final decision stands for (None if it names no single grade)."""
    call = final.get("final_call")
    shown = {g: (final.get(g) or {}).get("verdict") or {} for g in ("grader_1", "grader_2")}
    labels = {g: shown[g].get(LABEL_FIELD[family]) for g in shown}
    corrected = grade_to_label(family, final.get("corrected_grade"))
    blind_label = grade_to_label(family, (blind or {}).get("grade"))
    if call in ("grader_1", "grader_2"):
        return labels[call]
    if call == "both_fine":
        if labels["grader_1"] == labels["grader_2"]:
            return labels["grader_1"]
        return corrected if corrected is not None else blind_label
    if call == "both_wrong":
        return corrected if corrected is not None else blind_label
    return None


# ------------------------------------------------------------------------------ ratings

def load_assignments(run_dir):
    out = {}
    for ev in read_jsonl(os.path.join(run_dir, RATINGS_DIR, "assignments.jsonl")):
        if ev.get("event") == "assign" and ev.get("assignment"):
            out.setdefault(ev["assignment"], ev)
    return out


def ratings_state(run_dir):
    """{(assignment, item id): latest rating event}."""
    out = {}
    for ev in read_jsonl(os.path.join(run_dir, RATINGS_DIR, "ratings.jsonl")):
        if ev.get("event") == "rating" and ev.get("assignment") and ev.get("id"):
            out[(ev["assignment"], ev["id"])] = ev
    return out


# ------------------------------------------------------------------------------ the whole run

class RunData:
    """Everything the viewer and review-build need from one run folder, read once.

    With include_held_back=False, held-back items are dropped at load time, so nothing
    downstream (scores, judges, queues, ratings) can show them.
    """

    def __init__(self, run_dir, include_held_back=True):
        self.dir = os.path.abspath(run_dir)
        self.run = load_run(self.dir)
        self.name = self.run["name"]
        self.include_held_back = include_held_back
        self.items, self.item_errors = {}, []
        try:
            self.items = gt.load_items(run_item_paths(self.run))
        except SystemExit:
            self.item_errors.append("the item files listed in run.json could not be read")
        if not include_held_back:
            self.items = {i: it for i, it in self.items.items() if not it.get("_held_back")}
        self.responses = {k: r for k, r in load_responses(self.dir).items() if k[1] in self.items}
        self.models = model_order(self.run, self.responses)
        self.model_info = {m["id"]: m for m in self.run.get("models", []) if isinstance(m, dict)}
        self.judges = {}
        self._load_graders()
        self._load_string_match()
        self._load_reviewers()
        self._load_raters()

    # -- helpers
    def held_back(self, item_id):
        return bool(self.items.get(item_id, {}).get("_held_back"))

    def family(self, item_id):
        return self.items.get(item_id, {}).get("family")

    def model_label(self, model):
        return self.model_info.get(model, {}).get("label") or model

    def keep(self, key):
        return key[1] in self.items and key in self.responses

    def _add(self, jid, kind, label, description, info, verdicts):
        self.judges[jid] = {"id": jid, "kind": kind, "label": label, "description": description,
                            "info": info, "verdicts": verdicts}

    # -- judges
    def _load_graders(self):
        for gid in grader_ids(self.dir):
            info = load_grader_info(self.dir, gid)
            verdicts = {}
            for rec in load_grade_records(self.dir, gid):
                key = (rec.get("model"), rec.get("id"))
                v = verdict_from_record(rec)
                if v and self.keep(key):
                    verdicts[key] = v
            self._add(gid, info.get("kind", "claude-subagent"), gid,
                      grader_description(info, len(verdicts)), info, verdicts)

    def _load_string_match(self):
        verdicts = {}
        for key, row in self.responses.items():
            item = self.items[key[1]]
            if item.get("family") == "facts" and item.get("accept"):
                verdicts[key] = string_match_verdict(item, row["response"])
        if verdicts:
            self._add("string-match", "automatic", "string-match",
                      "The automatic string-match check for facts (score_fact in validate.py): "
                      "correct if an accepted answer appears anywhere in the response. Facts "
                      f"only; {count(len(verdicts), 'verdict')}.",
                      {"kind": "automatic", "families": ["facts"]}, verdicts)

    def _load_reviewers(self):
        by_reviewer = {}
        for (rslug, queue, qkey), s in review_state(self.dir).items():
            ev = s.get("final") or s.get("blind")
            key = (ev.get("model"), ev.get("id"))
            family = ev.get("family") or self.family(key[1])
            if not self.keep(key) or family not in LABEL_FIELD:
                continue
            r = by_reviewer.setdefault(rslug, {"name": ev.get("reviewer"), "kind":
                                               ev.get("reviewer_kind", "human"),
                                               "final": {}, "blind": {}, "times": {}})
            blind, final = s.get("blind"), s.get("final")
            t = (final or blind).get("time", "")
            if blind:
                lab = grade_to_label(family, blind.get("grade"))
                if lab is not None:
                    r["blind"][key] = {"family": family, "label": lab, "label2": lab,
                                       "fields": {LABEL_FIELD[family]: lab},
                                       "reason": "Own grade, given before seeing the graders."}
            if final and t >= r["times"].get(key, ""):
                lab = reviewer_final_label(family, final, blind)
                if lab is not None:
                    r["times"][key] = t
                    fields = {LABEL_FIELD[family]: lab, "final_call": final.get("final_call")}
                    if final.get("confidence"):
                        fields["confidence"] = final["confidence"]
                    r["final"][key] = {"family": family, "label": lab, "label2": lab,
                                       "fields": fields,
                                       "reason": final.get("reason") or "(no reason recorded)"}
        for rslug, r in sorted(by_reviewer.items()):
            kind = "claude-session" if r["kind"] == "claude-session" else "human"
            who = r["name"] or rslug
            self._add(f"review-{rslug}", kind, f"{who} (final call)",
                      f"{who}'s final decisions in the review queue, after seeing both graders' "
                      f"verdicts; {count(len(r['final']), 'verdict')}.",
                      {"kind": kind, "reviewer": who, "source": "review"}, r["final"])
            if r["blind"]:
                self._add(f"review-{rslug}-blind", kind, f"{who} (own grade, blind)",
                          f"{who}'s own grades, given before seeing the graders' verdicts; "
                          f"{count(len(r['blind']), 'verdict')}.",
                          {"kind": kind, "reviewer": who, "source": "review-blind"}, r["blind"])

    def _load_raters(self):
        assignments = load_assignments(self.dir)
        by_rater = {}
        for (aid, item_id), ev in sorted(ratings_state(self.dir).items(),
                                         key=lambda kv: kv[1].get("time", "")):
            a = assignments.get(aid)
            if not a or item_id not in self.items:
                continue
            r = by_rater.setdefault(slug(a.get("rater", "")), {"name": a.get("rater"),
                                                               "verdicts": {}})
            for model, ok in (ev.get("task_check") or {}).items():
                key = (model, item_id)
                if isinstance(ok, bool) and self.keep(key):
                    r["verdicts"][key] = {"family": "emotional_social", "label": ok, "label2": ok,
                                          "fields": {"does_task": ok},
                                          "reason": "Rater's task check in a blind side-by-side "
                                                    "comparison."}
        for rslug, r in sorted(by_rater.items()):
            if r["verdicts"]:
                self._add(f"rater-{rslug}", "human", f"{r['name']} (side-by-side task check)",
                          f"{r['name']}'s yes/no task checks from the blind side-by-side "
                          f"ratings (emotional tasks only); {count(len(r['verdicts']), 'verdict')}.",
                          {"kind": "human", "rater": r["name"], "source": "ratings"},
                          r["verdicts"])

    # -- queues
    def queue(self, name):
        """(meta, rows) with rows for dropped (held-back) items left out."""
        meta, rows = load_queue(self.dir, name)
        rows = [r for r in rows if self.keep((r.get("model"), r.get("id")))]
        return meta, rows

    def queue_name(self, public_id):
        """The queue file name behind an opaque public id, or None."""
        for name in queue_names(self.dir):
            if queue_public_id(name) == public_id:
                return name
        return None

    def queues(self):
        out = []
        for name in queue_names(self.dir):
            try:
                out.append((name,) + self.queue(name))
            except (OSError, ValueError):
                continue
        return out
