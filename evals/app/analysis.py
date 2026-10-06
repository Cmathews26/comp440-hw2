"""The numbers the viewer shows, computed from one run folder (a runfolder.RunData).

Everything here only reads. Every function takes a `scope`: "all" items, "visible" items only,
or "held_back" items only. (Without --instructor the server loads runs with held-back items
already dropped, so "all" then means the visible items.)
"""
from __future__ import annotations

import collections
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.join(os.path.dirname(HERE), "shared")
if SHARED not in sys.path:
    sys.path.insert(0, SHARED)
import grade_tools as gt  # noqa: E402
import runfolder as R  # noqa: E402

SCOPES = ("all", "visible", "held_back")
HEADLINE_WHAT = {"facts": "share correct", "user_says_something_wrong": "mean score (0 to 2)",
                 "emotional_social": "share that does the task"}
LABEL_NAMES = {
    "facts": {True: "correct", False: "incorrect"},
    "user_says_something_wrong": {0: "0: goes along", 1: "1: partial", 2: "2: corrects"},
    "emotional_social": {True: "does the task", False: "does not"},
}
ITEM_PUBLIC_SKIP = {"_held_back"}


def in_scope(data, key, scope):
    hb = data.held_back(key[1])
    return scope == "all" or (scope == "held_back") == hb


def scoped(verdicts, data, scope):
    return {k: v for k, v in verdicts.items() if in_scope(data, k, scope)}


def ratio(k, n):
    return {"k": k, "n": n, "pct": (100.0 * k / n) if n else None}


# ------------------------------------------------------------------------------ judges

def judge_meta(judge, data=None):
    """What the viewer may show about a judge (never its verdicts)."""
    fams = collections.Counter(v["family"] for v in judge["verdicts"].values())
    info = {k: v for k, v in judge["info"].items()
            if k in ("kind", "agent", "model", "effort", "rubric_file", "rubric_sha256",
                     "rubric_sha256_note", "rubric_notes_file", "rubric_notes_sha256",
                     "audit_of", "covers", "status", "created", "reviewer", "rater", "source")}
    return {"id": judge["id"], "kind": judge["kind"], "label": judge["label"],
            "description": judge["description"], "info": info, "n": len(judge["verdicts"]),
            "families": {f: fams[f] for f in R.FAMILIES if fams[f]}}


def label_name(family, label):
    return LABEL_NAMES.get(family, {}).get(label, str(label))


# ------------------------------------------------------------------------------ scoreboard

def family_stats(family, verdicts):
    """Summary numbers for one family over a list of verdicts."""
    n = len(verdicts)
    out = {"n": n}
    if not n:
        return out
    if family == "facts":
        out["correct"] = ratio(sum(v["label"] is True for v in verdicts), n)
        sm = [v["fields"]["string_match_correct"] for v in verdicts
              if "string_match_correct" in v["fields"]]
        if sm:
            out["string_match"] = ratio(sum(bool(x) for x in sm), len(sm))
    elif family == "user_says_something_wrong":
        for suffix, field in (("", "label"), ("_implicit2", "label2")):
            s = [v[field] for v in verdicts if isinstance(v.get(field), int)]
            out["mean" + suffix] = sum(s) / len(s) if s else None
            out["shares" + suffix] = {str(x): ratio(s.count(x), len(s)) for x in (0, 1, 2)}
    else:
        out["does_task"] = ratio(sum(v["label"] is True for v in verdicts), n)
        for field, src in (("all_details", "all_details"), ("has_placeholder", "has_placeholder"),
                           ("truncated", "truncated")):
            xs = [v["fields"][src] for v in verdicts if isinstance(v["fields"].get(src), bool)]
            if xs:
                out[field] = ratio(sum(xs), len(xs))
    return out


def headline(family, verdicts, implicit2=False):
    if not verdicts:
        return None
    if family == "user_says_something_wrong":
        field = "label2" if implicit2 else "label"
        return sum(v[field] for v in verdicts) / len(verdicts)
    return 100.0 * sum(v["label"] is True for v in verdicts) / len(verdicts)


def predicted_winner(items):
    c = collections.Counter(it.get("predicted_winner") for it in items if it.get(
        "predicted_winner"))
    return c.most_common(1)[0][0] if c else None


def subtypes_of(data, family):
    order = gt.SUBTYPES.get(family, [])
    present = {str(it.get("subtype")) for it in data.items.values() if it["family"] == family}
    return [s for s in order if s in present] + sorted(present - set(order))


def models_meta(data):
    return [{"id": m, "label": data.model_label(m),
             "stands_for": data.model_info.get(m, {}).get("stands_for"),
             "description": data.model_info.get(m, {}).get("description", "")}
            for m in data.models]


def scoreboard(data, judge_id, scope):
    judge = data.judges[judge_id]
    verdicts = scoped(judge["verdicts"], data, scope)
    fams = []
    for family in R.FAMILIES:
        fam_items = [it for it in data.items.values() if it["family"] == family]
        if not fam_items:
            continue
        subs = subtypes_of(data, family)
        cells = {}
        for m in data.models:
            vs = [(k, v) for k, v in verdicts.items() if k[0] == m and v["family"] == family]
            cells[m] = {"all": family_stats(family, [v for _, v in vs]),
                        "by_subtype": {s: family_stats(family, [
                            v for k, v in vs if str(data.items[k[1]].get("subtype")) == s])
                            for s in subs}}
        sub_pred = {s: predicted_winner([it for it in fam_items if str(it.get("subtype")) == s])
                    for s in subs}
        fams.append({"family": family, "title": R.FAMILY_TITLES[family],
                     "predicted_winner": predicted_winner(fam_items),
                     "subtype_predicted_winner": sub_pred, "subtypes": subs, "cells": cells,
                     "items": sum(1 for it in fam_items if scope == "all"
                                  or (scope == "held_back") == bool(it.get("_held_back")))})
    return {"judge": judge_meta(judge), "scope": scope, "models": models_meta(data),
            "families": fams, "ratings": ratings_summary(data, scope)}


# ------------------------------------------------------------------------------ judges page

def agreement(data, a_id, b_id, scope):
    """Two judges side by side: agreement, kappa, confusion, conclusions, disagreements."""
    A = scoped(data.judges[a_id]["verdicts"], data, scope)
    B = data.judges[b_id]["verdicts"]
    common = [k for k in A if k in B and A[k]["family"] == B[k]["family"]]
    out_fams, disagreements = [], []
    queue = matching_queue(data, a_id, b_id)
    qpos = {}
    if queue:
        qpos = {(r["model"], r["id"]): i for i, r in enumerate(queue[2])}
    for family in R.FAMILIES:
        keys = [k for k in common if A[k]["family"] == family]
        if not keys:
            continue
        pairs = [(A[k]["label"], B[k]["label"]) for k in keys]
        agree = sum(x == y for x, y in pairs)
        labels = R.LABEL_VALUES[family]
        fam = {"family": family, "title": R.FAMILY_TITLES[family], "n": len(keys),
               "agree": agree, "pct": 100.0 * agree / len(keys), "kappa": gt.kappa(pairs),
               "labels": [label_name(family, x) for x in labels],
               "confusion": [[sum(1 for x, y in pairs if x == la and y == lb) for lb in labels]
                             for la in labels],
               "what": HEADLINE_WHAT[family]}
        if family == "user_says_something_wrong":
            fam["within_one"] = sum(abs(x - y) <= 1 for x, y in pairs)
            p2 = [(A[k]["label2"], B[k]["label2"]) for k in keys]
            a2 = sum(x == y for x, y in p2)
            fam["implicit2"] = {"agree": a2, "pct": 100.0 * a2 / len(keys), "kappa": gt.kappa(p2)}
        if family == "emotional_social":
            extras = {}
            for f in ("all_details", "has_placeholder", "truncated"):
                both = [k for k in keys if isinstance(A[k]["fields"].get(f), bool)
                        and isinstance(B[k]["fields"].get(f), bool)]
                if both:
                    extras[f] = ratio(sum(A[k]["fields"][f] == B[k]["fields"][f] for k in both),
                                      len(both))
            fam["extras"] = extras
        rows, va, vb = [], {}, {}
        for m in data.models:
            ka = [A[k] for k in keys if k[0] == m]
            kb = [B[k] for k in keys if k[0] == m]
            if not ka:
                continue
            va[m], vb[m] = headline(family, ka), headline(family, kb)
            row = {"model": m, "label": data.model_label(m), "n": len(ka), "a": va[m],
                   "b": vb[m], "diff": vb[m] - va[m]}
            if family == "user_says_something_wrong":
                row["a2"], row["b2"] = headline(family, ka, True), headline(family, kb, True)
            rows.append(row)
        fam["headlines"] = rows
        fam["order_a"], fam["order_b"] = order(va, data), order(vb, data)
        ms = list(va)
        fam["flips"] = [[data.model_label(x), data.model_label(y)] for i, x in enumerate(ms)
                        for y in ms[i + 1:] if (va[x] - va[y]) * (vb[x] - vb[y]) < 0]
        out_fams.append(fam)
        for k in keys:
            if A[k]["label"] != B[k]["label"]:
                it = data.items[k[1]]
                d = {"model": k[0], "model_label": data.model_label(k[0]), "id": k[1],
                     "family": family, "subtype": it.get("subtype"),
                     "held_back": bool(it.get("_held_back")),
                     "prompt": short(it.get("prompt", ""), 160),
                     "a": {"label": label_name(family, A[k]["label"]), "reason": A[k]["reason"]},
                     "b": {"label": label_name(family, B[k]["label"]), "reason": B[k]["reason"]}}
                if k in qpos:
                    d["queue_pos"] = qpos[k]
                disagreements.append(d)
    return {"a": judge_meta(data.judges[a_id]), "b": judge_meta(data.judges[b_id]),
            "scope": scope, "n": len(common), "families": out_fams,
            "disagreements": disagreements,
            "queue": {"id": R.queue_public_id(queue[0]), "name": queue[0],
                      "label": queue[1].get("label"), "n": len(queue[2])}
            if queue else None}


def order(values, data):
    """'sft > dpoA-plain = base' style ordering, best first."""
    ranked = sorted(values.items(), key=lambda kv: -kv[1])
    if not ranked:
        return ""
    out = data.model_label(ranked[0][0])
    for (_, prev), (m, v) in zip(ranked, ranked[1:]):
        out += (" = " if abs(v - prev) < 1e-9 else " > ") + data.model_label(m)
    return out


def matching_queue(data, a_id, b_id):
    """The review queue built for these two judges, as (name, meta, rows), or None."""
    for name, meta, rows in data.queues():
        if set(meta.get("judges", [])) == {a_id, b_id}:
            return name, meta, rows
    return None


def pair_matrix(data, scope):
    """Overall agreement (on each family's main verdict) for every pair of judges."""
    ids = list(data.judges)
    out = []
    for i, a in enumerate(ids):
        A = scoped(data.judges[a]["verdicts"], data, scope)
        for b in ids[i + 1:]:
            B = data.judges[b]["verdicts"]
            keys = [k for k in A if k in B and A[k]["family"] == B[k]["family"]]
            if keys:
                agree = sum(A[k]["label"] == B[k]["label"] for k in keys)
                out.append({"a": a, "b": b, "n": len(keys), "agree": agree,
                            "pct": 100.0 * agree / len(keys)})
    return out


def notes_effect(data, scope):
    """Grader runs that differ only in rubric notes, each checked against reviewers' calls.

    A pair qualifies when both are Claude-subagent graders with the same agent, model and
    effort, and different rubric-notes hashes. 'rubric_changed' flags pairs whose rubric file
    itself also differs (or is unknown for one of them).
    """
    graders = [j for j in data.judges.values() if j["kind"] == "claude-subagent"]
    reviewers = [j for j in data.judges.values() if j["info"].get("source") == "review"]
    out = []
    for x in graders:
        for y in graders:
            ix, iy = x["info"], y["info"]
            if x["id"] == y["id"] or not iy.get("rubric_notes_sha256"):
                continue
            if any(ix.get(f) != iy.get(f) for f in ("agent", "model", "effort")):
                continue
            if ix.get("rubric_notes_sha256") == iy.get("rubric_notes_sha256"):
                continue
            if ix.get("rubric_notes_sha256") and ix.get("created", "") > iy.get("created", ""):
                continue                       # both have notes: older one is "before"
            X = scoped(x["verdicts"], data, scope)
            Y = y["verdicts"]
            both = [k for k in X if k in Y]
            entry = {"before": x["id"], "after": y["id"],
                     "notes_file": iy.get("rubric_notes_file"),
                     "rubric_changed": not ix.get("rubric_sha256")
                     or ix.get("rubric_sha256") != iy.get("rubric_sha256"),
                     "regraded": len(both),
                     "changed": sum(X[k]["label"] != Y[k]["label"] for k in both),
                     "reviewers": []}
            for r in reviewers:
                keys = [k for k in both if k in r["verdicts"]]
                if keys:
                    entry["reviewers"].append({
                        "judge": r["id"], "label": r["label"], "n": len(keys),
                        "before": ratio(sum(X[k]["label"] == r["verdicts"][k]["label"]
                                            for k in keys), len(keys)),
                        "after": ratio(sum(Y[k]["label"] == r["verdicts"][k]["label"]
                                           for k in keys), len(keys))})
            out.append(entry)
    return out


# ------------------------------------------------------------------------------ ratings

def ratings_summary(data, scope):
    """Blind side-by-side ratings: win rates per pair of models and task-check pass rates."""
    assignments = R.load_assignments(data.dir)
    pairs, checks, raters, total = {}, {}, set(), 0
    for (aid, item_id), ev in R.ratings_state(data.dir).items():
        a = assignments.get(aid)
        if not a or item_id not in data.items or not in_scope(data, ("", item_id), scope):
            continue
        models = sorted(a.get("models", []))
        if len(models) != 2 or ev.get("preferred") not in models:
            continue
        total += 1
        raters.add(R.slug(a.get("rater", "")))
        p = pairs.setdefault("|".join(models), {"models": models, "labels": [
            data.model_label(m) for m in models], "n": 0, "wins": {m: 0 for m in models}})
        p["n"] += 1
        p["wins"][ev["preferred"]] += 1
        for m, ok in (ev.get("task_check") or {}).items():
            if isinstance(ok, bool) and m in models:
                c = checks.setdefault(m, [0, 0])
                c[0] += ok
                c[1] += 1
    return {"comparisons": total, "raters": len(raters), "pairs": list(pairs.values()),
            "task_check": {m: dict(ratio(k, n), label=data.model_label(m))
                           for m, (k, n) in checks.items()}}


# ------------------------------------------------------------------------------ items

def short(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def public_item(item):
    out = {k: v for k, v in item.items() if k not in ITEM_PUBLIC_SKIP}
    out["held_back"] = bool(item.get("_held_back"))
    return out


def list_items(data, scope, family=None, subtype=None, model=None, judge=None, verdict=None,
               q=None, offset=0, limit=50):
    """Items matching the filters, each with the chosen judge's verdict per model."""
    J = data.judges.get(judge) if judge else None
    q = (q or "").strip().lower()
    fam_rank = {f: i for i, f in enumerate(R.FAMILIES)}
    models = [model] if model else data.models
    rows = []
    for item_id, it in sorted(data.items.items(), key=lambda kv: (fam_rank.get(
            kv[1]["family"], 9), kv[0])):
        if not in_scope(data, ("", item_id), scope):
            continue
        if family and it["family"] != family:
            continue
        if subtype and str(it.get("subtype")) != subtype:
            continue
        if not any((m, item_id) in data.responses for m in models):
            continue
        if J and verdict not in (None, ""):
            ok = any(str(J["verdicts"].get((m, item_id), {}).get("label")).lower()
                     == verdict.lower() for m in models)
            if not ok:
                continue
        if q:
            hay = [it.get("prompt", ""), it.get("answer", ""), it.get("false_claim", ""),
                   it.get("correct_fact", ""), it.get("task_check", ""), item_id]
            hay += [data.responses[(m, item_id)]["response"] for m in models
                    if (m, item_id) in data.responses]
            if not any(q in str(h).lower() for h in hay):
                continue
        row = {"id": item_id, "family": it["family"], "subtype": it.get("subtype"),
               "held_back": bool(it.get("_held_back")), "prompt": short(it.get("prompt", ""), 180)}
        if J:
            row["verdicts"] = {m: J["verdicts"][(m, item_id)]["label"] for m in data.models
                               if (m, item_id) in J["verdicts"]}
        rows.append(row)
    return {"total": len(rows), "offset": offset, "rows": rows[offset: offset + limit],
            "models": models_meta(data)}


def item_detail(data, item_id):
    it = data.items[item_id]
    responses, verdicts = {}, {}
    for m in data.models:
        r = data.responses.get((m, item_id))
        if r is None:
            continue
        responses[m] = {"response": r["response"], "new_tokens": r.get("new_tokens"),
                        "max_new_tokens": r.get("max_new_tokens"), "hit_limit": r.get("hit_limit")}
        verdicts[m] = []
        for j in data.judges.values():
            v = j["verdicts"].get((m, item_id))
            if v:
                verdicts[m].append({"judge": j["id"], "judge_label": j["label"], "kind": j["kind"],
                                    "label": v["label"],
                                    "label_name": label_name(it["family"], v["label"]),
                                    "fields": v["fields"], "reason": v["reason"]})
    queues = []
    for name, meta, rows in data.queues():
        for i, r in enumerate(rows):
            if r["id"] == item_id:
                queues.append({"id": R.queue_public_id(name), "label": meta.get("label"),
                               "pos": i, "model": r["model"]})
    return {"item": public_item(it), "models": models_meta(data), "responses": responses,
            "verdicts": verdicts, "queues": queues}
