"""The two flows where a person judges: the review queue and the blind side-by-side rating.

What the person sees is built here with the identities removed: no evaluated-model names, no
judge ids (only "Grader 1" and "Grader 2"), and the graders' verdicts only after the person's
own grade is saved. What the person decides is saved here, and only here, as append-only JSON
lines in the run's review/ and ratings/ folders (see append_record). Nothing in the viewer
writes anywhere else.
"""
from __future__ import annotations

import os
import random
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.join(os.path.dirname(HERE), "shared")
if SHARED not in sys.path:
    sys.path.insert(0, SHARED)
import grade_tools as gt  # noqa: E402
import runfolder as R  # noqa: E402

RATE_ITEMS = 20                     # items per rater, as in evals/shared/README.md
MAX_REASON = 300
WRITE_LOCK = threading.Lock()       # one writer at a time (checks + append are one step)
FILE_NAME = re.compile(r"^(decisions-[a-z0-9-]{1,40}|assignments|ratings)\.jsonl$")


class Problem(Exception):
    """A request the viewer must refuse: (HTTP status, plain-language message)."""

    def __init__(self, status, message):
        super().__init__(message)
        self.status, self.message = status, message


# ------------------------------------------------------------------------------ writing

def append_record(run_dir, subdir, filename, record, roots):
    """Append one JSON line to run_dir/subdir/filename, refusing anything unexpected.

    Only review/ and ratings/ may be written, only to the known file names, only inside a run
    folder that sits inside one of the configured runs roots, and never through a symlink.
    """
    if subdir not in R.WRITABLE_DIRS:
        raise Problem(403, f"the viewer may write only to {', '.join(R.WRITABLE_DIRS)}")
    if not FILE_NAME.match(filename):
        raise Problem(403, "not a file the viewer may write")
    run_dir = os.path.realpath(run_dir)
    if not any(R.inside(run_dir, root) and run_dir != os.path.realpath(root) for root in roots):
        raise Problem(403, "that folder is not a run inside the configured runs folders")
    folder = os.path.join(run_dir, subdir)
    path = os.path.join(folder, filename)
    for p in (folder, path):
        if os.path.islink(p):
            raise Problem(403, "refusing to write through a symbolic link")
    if not R.inside(path, run_dir):
        raise Problem(403, "path escapes the run folder")
    os.makedirs(folder, exist_ok=True)
    R.append_jsonl(path, record)


def clean_name(value, what="reviewer"):
    """A person's name as typed: one line, 1-60 characters, with a usable slug."""
    if not isinstance(value, str):
        raise Problem(400, f"give a {what} name")
    name = " ".join(value.split())
    if not name or len(name) > 60 or any(ord(c) < 32 for c in name):
        raise Problem(400, f"the {what} name must be 1 to 60 characters on one line")
    s = R.slug(name)
    if not s:
        raise Problem(400, f"the {what} name needs at least one letter or digit")
    if s.startswith("claude"):
        raise Problem(400, f"names starting with 'Claude' are kept for Claude's own decisions; "
                           f"please use your own name")
    return name


def clean_reason(value):
    if not isinstance(value, str) or not value.strip():
        raise Problem(400, "write a one-line reason")
    reason = " ".join(value.split())
    if len(reason) > MAX_REASON:
        raise Problem(400, f"keep the reason under {MAX_REASON} characters")
    return reason


# ------------------------------------------------------------------------------ review queue

def item_fields(family, item):
    """The item fields the grader saw (prompt aside), for the reviewer."""
    return {f: item.get(f) for f in gt.ITEM_FIELDS[family]
            if f not in ("prompt", "subtype") and item.get(f) not in (None, "", [])}


def blinded_verdict(judge, key, family):
    """A judge's verdict without anything that says which judge it is."""
    v = judge["verdicts"].get(key)
    if not v:
        return None
    keep = [f for f in gt.VERDICT_FIELDS[family] if f != "reason"]
    return {"label": v["label"], "fields": {f: v["fields"][f] for f in keep if f in v["fields"]},
            "reason": v["reason"]}


def queue_list(data):
    out = []
    for name, meta, rows in data.queues():
        counts = {}
        for r in rows:
            counts[r["family"]] = counts.get(r["family"], 0) + 1
        out.append({"id": R.queue_public_id(name), "label": meta.get("label", "Review set"),
                    "n": len(rows), "families": counts, "created": meta.get("created")})
    return out


QUEUE_ID = re.compile(r"^set-[0-9a-f]{8}$")


def get_queue(data, public_id):
    """(file name, meta, rows) for a queue given by its opaque public id."""
    name = data.queue_name(public_id) if isinstance(public_id, str) and QUEUE_ID.match(
        public_id) else None
    if not name:
        raise Problem(404, "no such review queue")
    try:
        meta, rows = data.queue(name)
    except (OSError, ValueError):
        raise Problem(404, "no such review queue")
    return name, meta, rows


def queue_progress(data, public_id, reviewer):
    name, meta, rows = get_queue(data, public_id)
    rslug = R.slug(reviewer) if reviewer else None
    state = R.review_state(data.dir) if rslug else {}
    positions, done, blind = [], 0, 0
    for i, r in enumerate(rows):
        s = state.get((rslug, name, r["queue_key"]), {})
        st = "final" if "final" in s else "blind" if "blind" in s else "todo"
        done += st == "final"
        blind += st == "blind"
        positions.append({"pos": i, "state": st, "family": r["family"]})
    next_pos = next((p["pos"] for p in positions if p["state"] != "final"), None)
    return {"id": public_id, "label": meta.get("label", "Review set"), "n": len(rows), "done": done,
            "in_progress": blind, "positions": positions, "next_pos": next_pos}


def queue_item(data, public_id, pos, reviewer):
    name, meta, rows = get_queue(data, public_id)
    if not 0 <= pos < len(rows):
        raise Problem(404, "no item at that position")
    row = rows[pos]
    key = (row["model"], row["id"])
    item, family = data.items[row["id"]], row["family"]
    resp = data.responses[key]
    s = {}
    if reviewer:
        s = R.review_state(data.dir).get((R.slug(reviewer), name, row["queue_key"]), {})
    out = {"queue": public_id, "label": meta.get("label", "Review set"), "pos": pos, "n": len(rows),
           "queue_key": row["queue_key"], "family": family,
           "family_title": R.FAMILY_TITLES[family], "subtype": item.get("subtype"),
           "item_id": row["id"], "held_back": bool(item.get("_held_back")),
           "prompt": item.get("prompt", ""), "item": item_fields(family, item),
           "response": resp["response"],
           "length": {"new_tokens": resp.get("new_tokens"),
                      "max_new_tokens": resp.get("max_new_tokens"),
                      "hit_limit": resp.get("hit_limit")},
           "grade_field": R.LABEL_FIELD[family], "rubric": R.rubric_section(family),
           "rubric_source": R.repo_rel(R.RUBRIC_FILE),
           "blind": None, "revealed": None, "final": None}
    if "blind" in s:
        out["blind"] = s["blind"].get("grade")
        # Only now: both graders' verdicts, as Grader 1 and Grader 2. Never the judge ids.
        out["revealed"] = {g: blinded_verdict(data.judges.get(row[g], {"verdicts": {}}), key,
                                              family) for g in ("grader_1", "grader_2")}
    if "final" in s:
        f = s["final"]
        out["final"] = {"final_call": f.get("final_call"), "reason": f.get("reason"),
                        "corrected_grade": f.get("corrected_grade"), "time": f.get("time")}
    return out


def save_review(data, body, roots):
    """POST /review: {reviewer, queue, queue_key, event: blind|final, ...}."""
    if not isinstance(body, dict):
        raise Problem(400, "expected a JSON object")
    reviewer = clean_name(body.get("reviewer"))
    name, meta, rows = get_queue(data, body.get("queue"))
    row = next((r for r in rows if r["queue_key"] == body.get("queue_key")), None)
    if row is None:
        raise Problem(404, "that item is not in this queue")
    family = row["family"]
    key = (row["model"], row["id"])
    rec = {"event": body.get("event"), "time": R.now(), "reviewer": reviewer,
           "reviewer_kind": "human", "queue": name, "queue_key": row["queue_key"],
           "model": row["model"], "id": row["id"], "family": family}
    with WRITE_LOCK:
        s = R.review_state(data.dir).get((R.slug(reviewer), name, row["queue_key"]), {})
        if body.get("event") == "blind":
            if "blind" in s:
                raise Problem(409, "your own grade for this item is already saved; it stays "
                                   "fixed once the graders' verdicts have been shown")
            problem = R.check_grade(family, body.get("grade"))
            if problem:
                raise Problem(400, problem)
            rec["grade"] = body["grade"]
        elif body.get("event") == "final":
            if "blind" not in s:
                raise Problem(409, "save your own grade for this item first")
            call = body.get("final_call")
            if call not in R.FINAL_CALLS:
                raise Problem(400, f"final_call must be one of {', '.join(R.FINAL_CALLS)}")
            rec["final_call"] = call
            rec["reason"] = clean_reason(body.get("reason"))
            if call == "both_wrong":
                corrected = body.get("corrected_grade")
                problem = R.check_grade(family, corrected)
                if problem:
                    raise Problem(400, "when both graders are wrong, give the right grade: "
                                       + problem)
                rec["corrected_grade"] = corrected
            rec["blind_grade"] = s["blind"].get("grade")
            for g in ("grader_1", "grader_2"):
                v = blinded_verdict(data.judges.get(row[g], {"verdicts": {}}), key, family)
                rec[g] = {"judge": row[g], "verdict": dict((v or {}).get("fields", {}),
                                                           reason=(v or {}).get("reason"))}
        else:
            raise Problem(400, "event must be 'blind' or 'final'")
        append_record(data.dir, R.REVIEW_DIR, os.path.basename(
            R.decisions_file(data.dir, reviewer)), rec, roots)
    return {"saved": rec["event"], "time": rec["time"]}


# ------------------------------------------------------------------------------ rating

def rater_assignments(data, rater):
    """This rater's assignments: models (chosen by the rater), size and progress."""
    if not rater:
        return []
    rslug = R.slug(rater)
    done = R.ratings_state(data.dir)
    out = []
    for aid, a in R.load_assignments(data.dir).items():
        if R.slug(a.get("rater", "")) != rslug:
            continue
        ids = [x["id"] for x in a.get("items", []) if x["id"] in data.items]
        if not ids or not all(m in data.models for m in a.get("models", [])):
            continue
        out.append({"assignment": aid, "models": a["models"],
                    "labels": [data.model_label(m) for m in a["models"]], "n": len(ids),
                    "done": sum((aid, i) in done for i in ids), "created": a.get("time")})
    return out


def create_assignment(data, body, roots):
    """POST /ratings/assign: {rater, models: [a, b]} -> a random 20 emotional items."""
    if not isinstance(body, dict):
        raise Problem(400, "expected a JSON object")
    rater = clean_name(body.get("rater"), "rater")
    models = body.get("models")
    if not (isinstance(models, list) and len(models) == 2 and models[0] != models[1]
            and all(m in data.models for m in models)):
        raise Problem(400, "pick two different evaluated models")
    with WRITE_LOCK:
        for a in rater_assignments(data, rater):
            if sorted(a["models"]) == sorted(models):
                return {"assignment": a["assignment"], "existing": True}
        ids = sorted(i for i, it in data.items.items() if it["family"] == "emotional_social"
                     and all((m, i) in data.responses for m in models))
        if not ids:
            raise Problem(400, "these two models have no emotional_social responses in common")
        seed = random.SystemRandom().getrandbits(32)
        rng = random.Random(seed)
        chosen = rng.sample(ids, min(RATE_ITEMS, len(ids)))
        aid = f"a{rng.getrandbits(32):08x}"
        rec = {"event": "assign", "time": R.now(), "assignment": aid, "rater": rater,
               "models": models, "seed": seed, "include_held_back": data.include_held_back,
               "items": [{"id": i, "left": rng.choice(models)} for i in chosen]}
        append_record(data.dir, R.RATINGS_DIR, "assignments.jsonl", rec, roots)
    return {"assignment": aid, "existing": False}


def get_assignment(data, aid, rater):
    a = R.load_assignments(data.dir).get(aid) if isinstance(aid, str) else None
    if not a:
        raise Problem(404, "no such rating assignment")
    if not rater or R.slug(rater) != R.slug(a.get("rater", "")):
        raise Problem(403, "this assignment belongs to another rater")
    items = [x for x in a.get("items", []) if x["id"] in data.items]
    return a, items


def rating_item(data, aid, pos, rater):
    a, items = get_assignment(data, aid, rater)
    if not 0 <= pos < len(items):
        raise Problem(404, "no item at that position")
    x = items[pos]
    left = x["left"]
    right = next(m for m in a["models"] if m != left)
    it = data.items[x["id"]]
    saved = R.ratings_state(data.dir).get((aid, x["id"]))
    done = R.ratings_state(data.dir)
    out = {"assignment": aid, "pos": pos, "n": len(items), "item_id": x["id"],
           "subtype": it.get("subtype"), "prompt": it.get("prompt", ""),
           "task_check": it.get("task_check", ""), "length_hint": it.get("length_hint", ""),
           "left": data.responses[(left, x["id"])]["response"],
           "right": data.responses[(right, x["id"])]["response"],
           "done": [(aid, y["id"]) in done for y in items], "saved": None}
    if saved:
        tc = saved.get("task_check") or {}
        out["saved"] = {"prefer": saved.get("preferred_side"),
                        "task_check_left": tc.get(left), "task_check_right": tc.get(right)}
    return out


def save_rating(data, body, roots):
    """POST /ratings: {rater, assignment, pos, prefer: left|right, task_check_left/right}."""
    if not isinstance(body, dict):
        raise Problem(400, "expected a JSON object")
    rater = clean_name(body.get("rater"), "rater")
    a, items = get_assignment(data, body.get("assignment"), rater)
    pos = body.get("pos")
    if not isinstance(pos, int) or isinstance(pos, bool) or not 0 <= pos < len(items):
        raise Problem(400, "no item at that position")
    if body.get("prefer") not in ("left", "right"):
        raise Problem(400, "choose the response you would rather send")
    checks = (body.get("task_check_left"), body.get("task_check_right"))
    if not all(isinstance(c, bool) for c in checks):
        raise Problem(400, "answer the task check (yes or no) for both responses")
    x = items[pos]
    left = x["left"]
    right = next(m for m in a["models"] if m != left)
    rec = {"event": "rating", "time": R.now(), "assignment": a["assignment"], "rater": rater,
           "id": x["id"], "left_model": left, "right_model": right,
           "preferred_side": body["prefer"],
           "preferred": left if body["prefer"] == "left" else right,
           "task_check": {left: checks[0], right: checks[1]}}
    with WRITE_LOCK:
        append_record(data.dir, R.RATINGS_DIR, "ratings.jsonl", rec, roots)
    return {"saved": "rating", "time": rec["time"]}
