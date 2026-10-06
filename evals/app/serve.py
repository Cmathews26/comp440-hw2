#!/usr/bin/env python3
"""A small local viewer for evaluation runs: scores, responses, judges, review and rating.

Usage, from the repo root:
  python3 evals/app/serve.py --open                  students: runs in evals/runs/
  python3 evals/app/serve.py --open --instructor     also show held-back items (planning/runs/)

Options:
  --port N        port on 127.0.0.1 (default 8440)
  --runs DIR      a folder of run folders; repeat for more. Default: evals/runs, plus
                  planning/runs if it exists
  --instructor    show held-back items; without it they are left out everywhere
  --open          open the viewer in the default browser

It reads the run folders described in evals/runs/README.md, and writes only one thing: what a
person decides on the Review and Rate pages, appended to the run's review/ and ratings/
folders. It listens on 127.0.0.1 only (this computer), makes no network calls, and needs no
installs: Python's standard library only. Stop it with Ctrl-C.
"""
from __future__ import annotations

import argparse
import glob
import http.server
import json
import os
import sys
import threading
import traceback
import urllib.parse
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
SHARED = os.path.join(os.path.dirname(HERE), "shared")
for p in (HERE, SHARED):
    if p not in sys.path:
        sys.path.insert(0, p)
import analysis as A  # noqa: E402
import reviewing as RV  # noqa: E402
import runfolder as R  # noqa: E402
from reviewing import Problem  # noqa: E402

HOST = "127.0.0.1"
DEFAULT_PORT = 8440
MAX_BODY = 64 * 1024
STATIC_DIR = os.path.join(HERE, "static")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/index.html": ("index.html", "text/html; charset=utf-8"),
          "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


class Settings:
    roots: list = []
    instructor = False
    port = DEFAULT_PORT


# ------------------------------------------------------------------------------ run folders

def run_dirs():
    """[(name, run folder, root)] for every run folder directly inside a configured root."""
    out, seen = [], set()
    for root in Settings.roots:
        for d in sorted(glob.glob(os.path.join(root, "*"))):
            name = os.path.basename(d)
            if not os.path.isdir(d) or not R.SAFE_ID.match(name) or not R.is_run(d):
                continue
            if not R.inside(d, root) or name in seen:     # symlinks out of the root, duplicates
                continue
            seen.add(name)
            out.append((name, d, root))
    return out


def find_run(name):
    if not isinstance(name, str) or not R.SAFE_ID.match(name):
        raise Problem(404, "no such run")
    for root in Settings.roots:
        d = os.path.join(root, name)
        if R.is_run(d) and R.inside(d, root) and os.path.realpath(d) != os.path.realpath(root):
            return d
    raise Problem(404, "no such run")


class RunCache:
    """RunData per run folder, reloaded whenever a file in it changes."""

    def __init__(self):
        self.lock = threading.Lock()
        self.entries = {}

    @staticmethod
    def signature(run_dir):
        files = [os.path.join(run_dir, R.RUN_JSON)]
        for pattern in ("responses/*.jsonl", "grades/*/grades.jsonl", "grades/*/grader.json",
                        "review/*.jsonl", "review/*.json", "ratings/*.jsonl"):
            files += glob.glob(os.path.join(run_dir, pattern))
        try:
            for p in R.run_item_paths(R.load_run(run_dir)):
                files += glob.glob(os.path.join(p, "*.jsonl")) if os.path.isdir(p) else [p]
        except (OSError, ValueError):
            pass
        sig = []
        for f in sorted(files):
            try:
                st = os.stat(f)
                sig.append((f, st.st_mtime_ns, st.st_size))
            except OSError:
                pass
        return tuple(sig)

    def get(self, run_dir):
        sig = self.signature(run_dir)
        with self.lock:
            hit = self.entries.get(run_dir)
            if hit and hit[0] == sig:
                return hit[1]
        data = R.RunData(run_dir, include_held_back=Settings.instructor)
        with self.lock:
            self.entries[run_dir] = (sig, data)
        return data


CACHE = RunCache()


def data_for(name):
    return CACHE.get(find_run(name))


def scope_of(query):
    scope = query.get("scope", "all")
    if not Settings.instructor or scope not in A.SCOPES:
        return "all"
    return scope


def default_judge(data):
    for g in data.run.get("graders", []):
        if g in data.judges:
            return g
    return next(iter(data.judges), None)


# ------------------------------------------------------------------------------ API handlers

def run_summary(name, run_dir, root):
    data = CACHE.get(run_dir)
    run = data.run
    sets = [s for s in run.get("item_sets", []) if Settings.instructor or not s.get("held_back")]
    hidden = sum(s.get("items", 0) for s in run.get("item_sets", []) if s.get("held_back"))
    return {
        "name": name, "title": run.get("title") or name, "created": run.get("created"),
        "description": run.get("description", ""), "status": run.get("status"),
        "next_steps": run.get("next_steps", []), "root": R.repo_rel(root),
        "models": A.models_meta(data),
        "item_sets": [{"path": s.get("path"), "items": s.get("items"),
                       "held_back": s.get("held_back", False)} for s in sets],
        "held_back_hidden": 0 if Settings.instructor else hidden,
        "items": len(data.items), "responses": len(data.responses),
        "judges": [A.judge_meta(j) for j in data.judges.values()],
        "queues": len(R.queue_names(run_dir)),
        "ratings": A.ratings_summary(data, "all")["comparisons"],
        "problems": data.item_errors,
    }


def api_config(q, body):
    return {"instructor": Settings.instructor, "roots": [R.repo_rel(r) for r in Settings.roots],
            "rubric_file": R.repo_rel(R.RUBRIC_FILE)}


def api_runs(q, body):
    out = []
    for name, d, root in run_dirs():
        try:
            out.append(run_summary(name, d, root))
        except Exception as e:                       # one broken run should not hide the rest
            out.append({"name": name, "title": name, "broken": f"{type(e).__name__}: {e}"})
    return {"runs": out, "instructor": Settings.instructor,
            "roots": [R.repo_rel(r) for r in Settings.roots]}


def api_run(q, body, run):
    d = find_run(run)
    root = next((r for n, _, r in run_dirs() if n == run), os.path.dirname(d))
    out = run_summary(run, d, root)
    data = CACHE.get(d)
    out["generation"] = data.run.get("generation", {})
    out["queue_list"] = RV.queue_list(data)
    notes = os.path.join(d, "notes.md")
    if os.path.isfile(notes):
        with open(notes, encoding="utf-8") as f:
            out["notes"] = f.read()[:20000]
    out["default_judge"] = default_judge(data)
    return out


def api_scoreboard(q, body, run):
    data = data_for(run)
    judge = q.get("judge") or default_judge(data)
    if judge not in data.judges:
        raise Problem(404, "no such judge in this run")
    out = A.scoreboard(data, judge, scope_of(q))
    out["judges"] = [A.judge_meta(j) for j in data.judges.values()]
    return out


def api_items(q, body, run):
    data = data_for(run)
    judge = q.get("judge") or default_judge(data)
    if judge and judge not in data.judges:
        raise Problem(404, "no such judge in this run")
    model = q.get("model") or None
    if model and model not in data.models:
        raise Problem(404, "no such model in this run")
    try:
        offset = max(0, int(q.get("offset", 0)))
        limit = min(500, max(1, int(q.get("limit", 50))))
    except ValueError:
        raise Problem(400, "offset and limit must be numbers")
    out = A.list_items(data, scope_of(q), q.get("family") or None, q.get("subtype") or None,
                       model, judge, q.get("verdict"), q.get("q"), offset, limit)
    out["judge"] = judge
    out["judges"] = [A.judge_meta(j) for j in data.judges.values()]
    out["subtypes"] = {f: A.subtypes_of(data, f) for f in R.FAMILIES}
    return out


def api_item(q, body, run, item):
    data = data_for(run)
    if item not in data.items:
        raise Problem(404, "no such item in this run")
    return A.item_detail(data, item)


def api_judges(q, body, run):
    data = data_for(run)
    scope = scope_of(q)
    return {"judges": [A.judge_meta(j) for j in data.judges.values()],
            "matrix": A.pair_matrix(data, scope), "notes_effect": A.notes_effect(data, scope),
            "queues": [{"id": R.queue_public_id(n), "name": n, "label": m.get("label"),
                        "judges": m.get("judges", []), "n": len(rows)}
                       for n, m, rows in data.queues()],
            "default_judge": default_judge(data), "scope": scope}


def api_compare(q, body, run):
    data = data_for(run)
    a, b = q.get("a"), q.get("b")
    if a not in data.judges or b not in data.judges:
        raise Problem(404, "choose two judges from this run")
    if a == b:
        raise Problem(400, "choose two different judges")
    return A.agreement(data, a, b, scope_of(q))


def api_queues(q, body, run):
    return {"queues": RV.queue_list(data_for(run))}


def api_queue(q, body, run, queue):
    return RV.queue_progress(data_for(run), queue, q.get("reviewer"))


def api_queue_item(q, body, run, queue, pos):
    return RV.queue_item(data_for(run), queue, int(pos), q.get("reviewer"))


def api_post_review(q, body, run):
    d = find_run(run)
    return RV.save_review(CACHE.get(d), body, Settings.roots)


def api_ratings(q, body, run):
    data = data_for(run)
    return {"assignments": RV.rater_assignments(data, q.get("rater")),
            "summary": A.ratings_summary(data, "all"), "models": A.models_meta(data),
            "rate_items": RV.RATE_ITEMS}


def api_assign(q, body, run):
    d = find_run(run)
    return RV.create_assignment(CACHE.get(d), body, Settings.roots)


def api_rating_item(q, body, run, assignment, pos):
    return RV.rating_item(data_for(run), assignment, int(pos), q.get("rater"))


def api_post_rating(q, body, run):
    d = find_run(run)
    return RV.save_rating(CACHE.get(d), body, Settings.roots)


def api_rubric(q, body, family):
    if family not in R.FAMILIES:
        raise Problem(404, "no such family")
    return {"family": family, "text": R.rubric_section(family),
            "source": R.repo_rel(R.RUBRIC_FILE)}


# (method, path segments after /api/, handler); "{x}" segments are parameters
ROUTES = [
    ("GET", ["config"], api_config),
    ("GET", ["runs"], api_runs),
    ("GET", ["runs", "{run}"], api_run),
    ("GET", ["runs", "{run}", "scoreboard"], api_scoreboard),
    ("GET", ["runs", "{run}", "items"], api_items),
    ("GET", ["runs", "{run}", "items", "{item}"], api_item),
    ("GET", ["runs", "{run}", "judges"], api_judges),
    ("GET", ["runs", "{run}", "judges", "compare"], api_compare),
    ("GET", ["runs", "{run}", "queues"], api_queues),
    ("GET", ["runs", "{run}", "queues", "{queue}"], api_queue),
    ("GET", ["runs", "{run}", "queues", "{queue}", "{pos}"], api_queue_item),
    ("POST", ["runs", "{run}", "review"], api_post_review),
    ("GET", ["runs", "{run}", "ratings"], api_ratings),
    ("POST", ["runs", "{run}", "ratings"], api_post_rating),
    ("POST", ["runs", "{run}", "ratings", "assign"], api_assign),
    ("GET", ["runs", "{run}", "ratings", "{assignment}", "{pos}"], api_rating_item),
    ("GET", ["rubric", "{family}"], api_rubric),
]


def match_route(method, segments):
    """(handler, params) for the request, or raise Problem 404/405."""
    allowed = False
    for m, pattern, handler in ROUTES:
        if len(pattern) != len(segments):
            continue
        params = {}
        for p, s in zip(pattern, segments):
            if p.startswith("{"):
                params[p[1:-1]] = s
            elif p != s:
                break
        else:
            if "pos" in params and not params["pos"].isdigit():
                continue
            if m == method:
                return handler, params
            allowed = True
    raise Problem(405 if allowed else 404, "not found" if not allowed else "method not allowed")


# ------------------------------------------------------------------------------ HTTP

class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "EvalViewer/1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):            # quiet: only errors are printed
        pass

    def send(self, status, body, ctype, extra=None):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, status, obj):
        self.send(status, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def host_ok(self):
        """Only requests addressed to this server by name (blocks DNS-rebinding tricks)."""
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{Settings.port}", f"localhost:{Settings.port}"}

    def origin_ok(self):
        origin = self.headers.get("Origin")
        if origin is None:
            return self.headers.get("Sec-Fetch-Site", "same-origin") in ("same-origin", "none")
        return origin in {f"http://127.0.0.1:{Settings.port}", f"http://localhost:{Settings.port}"}

    def do_GET(self):
        self.handle_request("GET")

    def do_HEAD(self):
        self.handle_request("GET")

    def do_POST(self):
        self.handle_request("POST")

    def do_PUT(self):
        self.send_json(405, {"error": "method not allowed"})

    do_DELETE = do_PATCH = do_PUT

    def handle_request(self, method):
        try:
            if not self.host_ok():
                raise Problem(403, "requests must be addressed to 127.0.0.1 or localhost")
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            if not path.startswith("/api/"):
                if method != "GET" or path not in STATIC:
                    raise Problem(404, "not found")
                name, ctype = STATIC[path]
                with open(os.path.join(STATIC_DIR, name), "rb") as f:
                    body = f.read()
                extra = {"Content-Security-Policy": CSP} if name.endswith(".html") else None
                return self.send(200, body, ctype, extra)
            raw = path[len("/api/"):].strip("/").split("/")
            segments = [urllib.parse.unquote(s) for s in raw]
            if any(not s or "/" in s or "\\" in s or "\x00" in s or s in (".", "..")
                   for s in segments):
                raise Problem(400, "bad path")
            query = {k: v[-1] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            handler, params = match_route(method, segments)
            body = None
            if method == "POST":
                body = self.read_json()
            return self.send_json(200, handler(query, body, **params))
        except Problem as p:
            self.drain()
            return self.send_json(p.status, {"error": p.message})
        except Exception as e:  # noqa: BLE001  report, keep serving
            traceback.print_exc()
            return self.send_json(500, {"error": f"internal error ({type(e).__name__}); see the "
                                                 f"terminal running serve.py"})

    def drain(self):
        """Read an unread request body so the connection stays usable."""
        if self.command == "POST" and not getattr(self, "_read_body", False):
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if 0 < n <= MAX_BODY:
                    self.rfile.read(n)
            except ValueError:
                pass
            self.close_connection = True

    def read_json(self):
        if not self.origin_ok():
            raise Problem(403, "cross-site requests are not accepted")
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            raise Problem(415, "send JSON (Content-Type: application/json)")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise Problem(400, "bad Content-Length")
        if n <= 0 or n > MAX_BODY:
            raise Problem(413, "request body missing or too large")
        data = self.rfile.read(n)
        self._read_body = True
        try:
            return json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise Problem(400, "the request body is not valid JSON")


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Local viewer for evaluation runs (127.0.0.1 only).",
        epilog=__doc__.split("\n\n", 1)[1], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="port (default 8440)")
    ap.add_argument("--runs", action="append", metavar="DIR",
                    help="a folder of run folders (repeat for more); default evals/runs plus "
                         "planning/runs if it exists")
    ap.add_argument("--instructor", action="store_true",
                    help="show held-back items (without it they are hidden everywhere)")
    ap.add_argument("--open", action="store_true", help="open the viewer in the browser")
    args = ap.parse_args(argv)

    roots = args.runs or [r for r in R.DEFAULT_ROOTS if os.path.isdir(r)
                          or r == R.DEFAULT_ROOTS[0]]
    Settings.roots = [os.path.realpath(r) for r in roots]
    Settings.instructor = args.instructor
    Settings.port = args.port
    for r in Settings.roots:
        if not os.path.isdir(r):
            print(f"note: {R.repo_rel(r)} does not exist yet (no runs there)", file=sys.stderr)
    try:
        server = Server((HOST, args.port), Handler)
    except OSError as e:
        sys.exit(f"Could not listen on {HOST}:{args.port} ({e.strerror}). Is the viewer already "
                 f"running? Try --port {args.port + 1}.")
    url = f"http://{HOST}:{args.port}/"
    runs = run_dirs()
    print(f"Viewer at {url}  ({len(runs)} run(s) from {', '.join(R.repo_rel(r) for r in roots)})")
    print("Instructor mode: held-back items are shown." if args.instructor else
          "Held-back items are hidden (add --instructor to show them).")
    print("Listening on 127.0.0.1 only. Press Ctrl-C to stop.")
    if args.open:
        threading.Timer(0.4, webbrowser.open, (url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
