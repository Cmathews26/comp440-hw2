/* Evaluation run viewer: plain JavaScript, no libraries, no network calls except to this
   server's /api/.

   Safety rule for this file: model responses and item text are untrusted. They are only ever
   put on the page as text (the h() helper turns every string child into a text node, and
   nothing here assigns innerHTML), so HTML, markdown or injection text in a response shows
   up literally.

   Pages (hash routes, so every view is linkable):
     #/                                   Runs
     #/run/NAME                           Results (same as #/run/NAME/results)
     #/run/NAME/results?grader=&family=&implicit2=1&scope=
     #/run/NAME/answers?family=&model=&grade=&q=&subtype=&grader=&limit=
     #/run/NAME/answers/ITEM_ID?grader=
     #/run/NAME/graders?a=&b=&scope=
     #/run/NAME/review?queue=&pos=
     #/run/NAME/rate?assignment=&pos=
   Older links (overview, scoreboard, responses, item, judges) are redirected.
*/
"use strict";

(function () {
  const main = document.getElementById("main");
  const tabsEl = document.getElementById("tabs");
  const crumbEl = document.getElementById("crumb");
  const modeEl = document.getElementById("mode");
  const helpBtn = document.getElementById("help");
  const helpBox = document.getElementById("helpbox");

  const FAMILIES = ["facts", "user_says_something_wrong", "emotional_social"];
  const TASK = { facts: "Facts", user_says_something_wrong: "Corrects the user", emotional_social: "Emotional tasks" };
  const MEASURE = { facts: "% right", user_says_something_wrong: "average, 0–2", emotional_social: "% done" };
  const LABEL_FIELD = { facts: "correct", user_says_something_wrong: "score", emotional_social: "does_task" };
  const GRADES = {
    facts: [{ v: true, word: "Right", sym: "✓", key: "y" }, { v: false, word: "Wrong", sym: "✗", key: "n" }],
    user_says_something_wrong: [{ v: 0, word: "goes along", sym: "0", key: "0" }, { v: 1, word: "partly", sym: "1", key: "1" },
      { v: 2, word: "corrects", sym: "2", key: "2" }],
    emotional_social: [{ v: true, word: "Done", sym: "✓", key: "y" }, { v: false, word: "Not done", sym: "✗", key: "n" }],
  };
  // The compare endpoint names labels in words; these turn them back into values.
  const LABEL_BACK = { correct: true, incorrect: false, "0: goes along": 0, "1: partial": 1, "2: corrects": 2,
    "does the task": true, "does not": false };
  const FIELD_NAMES = {
    answer: "Right answer", accept: "Also accepted", reject_if_also: "Wrong if it also says",
    wrong_examples: "Common wrong answers", scoring_note: "Note", false_claim: "False claim",
    correct_fact: "The truth", source_note: "Source", task_check: "Counts if", must_mention: "Details to use",
    length_hint: "Length", why: "What it tests",
  };
  const PAGES = [["results", "Results"], ["answers", "Answers"], ["graders", "Graders"], ["review", "Review"], ["rate", "Rate"]];
  const OLD_PAGES = { overview: "results", scoreboard: "results", responses: "answers", item: "answers", judges: "graders" };
  const OLD_PARAMS = { judge: "grader", verdict: "grade" };

  const S = { config: null, token: 0, keys: null, help: [], lastList: null, open: new Set(), lastHash: "", backTo: null };

  // ------------------------------------------------------------------ DOM helpers

  /** Create an element. Every string or number child becomes a text node (never HTML). */
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === "class") el.className = v;
        else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2), v);
        else if (k === "value" || k === "checked" || k === "disabled" || k === "selected") el[k] = v;
        else el.setAttribute(k, v === true ? "" : String(v));
      }
    }
    return add(el, kids);
  }
  function add(el, kids) {
    for (const k of kids.flat(Infinity)) {
      if (k === null || k === undefined || k === false) continue;
      el.append(k instanceof Node ? k : String(k));
    }
    return el;
  }
  /** Append children, skipping null, undefined and false (native append would print "null"). */
  function put(el, ...kids) { return add(el, kids); }
  const enc = encodeURIComponent;

  function qs(obj) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(obj)) if (v !== null && v !== undefined && v !== "" && v !== false) p.set(k, String(v));
    const t = p.toString();
    return t ? "?" + t : "";
  }
  function href(parts, params) { return "#/" + parts.map(enc).join("/") + qs(params || {}); }
  function runHref(run, page, params, extra) {
    const parts = ["run", run];
    if (page) parts.push(page);
    if (extra) parts.push(...extra);
    return href(parts, params);
  }
  function parseHash() {
    const raw = location.hash.replace(/^#\/?/, "");
    const i = raw.indexOf("?");
    const path = i < 0 ? raw : raw.slice(0, i);
    const params = new URLSearchParams(i < 0 ? "" : raw.slice(i + 1));
    const parts = path.split("/").filter(Boolean).map((x) => {
      try { return decodeURIComponent(x); } catch (e) { return x; }
    });
    return { parts, params };
  }
  function paramsObj(params) { const o = {}; params.forEach((v, k) => { o[k] = v; }); return o; }
  /** Change the current page's query without adding a history entry, then re-render. */
  function setParams(changes, top) {
    const { parts, params } = parseHash();
    for (const [k, v] of Object.entries(changes)) {
      if (v === null || v === undefined || v === "" || v === false) params.delete(k);
      else params.set(k, String(v));
    }
    history.replaceState(null, "", href(parts, paramsObj(params)));
    if (top) window.scrollTo(0, 0);
    render();
  }
  function go(hash) {
    if (location.hash === hash) render();
    else location.hash = hash;
  }

  const store = {
    get(k, d) { try { const v = localStorage.getItem("evalviewer." + k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem("evalviewer." + k, v); } catch (e) { /* private mode */ } },
  };
  function getName() { return store.get("name", store.get("reviewer", store.get("rater", ""))); }
  function setName(v) { store.set("name", v); store.set("reviewer", v); store.set("rater", v); }

  async function api(path, body) {
    const opts = body === undefined ? {} : {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    };
    const res = await fetch("/api/" + path, opts);
    let data;
    try { data = await res.json(); } catch (e) { data = { error: "the server sent something unexpected" }; }
    if (!res.ok) {
      const err = new Error(data.error || res.statusText);
      err.status = res.status;
      throw err;
    }
    return data;
  }

  // ------------------------------------------------------------------ words and numbers

  const pct = (r) => (r && r.n && r.pct !== null && r.pct !== undefined ? Math.round(r.pct) + "%" : "–");
  const avg = (x) => (x === null || x === undefined ? "–" : x.toFixed(2));
  const date = (iso) => (iso ? String(iso).slice(0, 10) : "");
  const cap = (t) => (t ? t.charAt(0).toUpperCase() + t.slice(1) : t);
  const kindName = (s) => cap(String(s || "").replace(/_/g, " "));
  const oneLine = (t) => String(t || "").split(/\n\s*\n/)[0].replace(/\s+/g, " ").trim();

  /** "DPO, plain labeler" -> "Plain DPO"; short labels stay as they are. */
  function shortModel(m) {
    const label = m.label || m.id;
    const x = /^(.+?),\s*(\w+)\s+labeler$/i.exec(label);
    return x ? cap(x[2]) + " " + x[1] : label;
  }

  /** Plain names for graders: Sonnet, Opus, Claude (hand), String match, a person's name. */
  function graderNames(judges) {
    const base = (j) => {
      const i = j.info || {};
      if (j.kind === "automatic") return "String match";
      if (i.source === "review") return j.kind === "claude-session" ? "Claude (review)" : `${i.reviewer || j.label} (review)`;
      if (i.source === "review-blind") return `${i.reviewer || j.label} (first grade)`;
      if (i.source === "ratings") return `${i.rater || j.label} (rating)`;
      if (j.kind === "claude-session") return "Claude (hand)";
      if (j.kind === "claude-subagent") {
        const m = String(i.model || "").toLowerCase();
        let n = ["sonnet", "opus", "haiku"].find((x) => m.includes(x));
        n = n ? cap(n) : i.model ? cap(String(i.model)) : j.label;
        if (i.audit_of) n += " (audit)";
        else if (i.rubric_notes_file) n += " + notes";
        return n;
      }
      return j.label;
    };
    const out = {};
    const count = (names) => names.reduce((c, n) => { c[n] = (c[n] || 0) + 1; return c; }, {});
    const first = judges.map((j) => [j, base(j)]);
    const c1 = count(first.map(([, n]) => n));
    for (const [j, n] of first) out[j.id] = c1[n] > 1 && j.info && j.info.effort ? `${n}, ${j.info.effort}` : n;
    const c2 = count(Object.values(out));
    for (const j of judges) if (c2[out[j.id]] > 1) out[j.id] = `${out[j.id]} (${j.id})`;
    return out;
  }

  function grade(family, v) { return (GRADES[family] || []).find((g) => g.v === v); }
  function tone(family, v) {
    if (family === "user_says_something_wrong") return v === 2 ? "good" : v === 1 ? "mid" : "bad";
    return v ? "good" : "bad";
  }
  function gradeWord(family, v) {
    const g = grade(family, v);
    if (!g) return String(v);
    return family === "user_says_something_wrong" ? `${g.sym} (${g.word})` : g.word;
  }
  function badge(family, v) {
    const g = grade(family, v);
    const word = gradeWord(family, v);
    return h("span", { class: "badge " + tone(family, v), title: word, "aria-label": word }, g ? g.sym : String(v));
  }
  /** The one line that says what a right answer looks like. */
  function keyLine(family, item) {
    const pairs = { facts: "answer", user_says_something_wrong: "correct_fact", emotional_social: "task_check" };
    const f = pairs[family];
    if (!item || !item[f]) return null;
    return h("p", { class: "key" }, h("span", { class: "muted" }, FIELD_NAMES[f] + ": "), String(item[f]));
  }
  function itemDetails(family, item, skip) {
    const dl = h("dl", { class: "fields" });
    const order = ["answer", "accept", "reject_if_also", "wrong_examples", "scoring_note", "false_claim", "correct_fact",
      "source_note", "task_check", "must_mention", "length_hint", "why"];
    for (const f of order) {
      if ((skip || []).includes(f)) continue;
      const v = item[f];
      if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) continue;
      add(dl, [h("dt", null, FIELD_NAMES[f]), h("dd", null, Array.isArray(v) ? v.join(", ") : String(v))]);
    }
    return dl.childNodes.length ? dl : null;
  }

  // ------------------------------------------------------------------ small building blocks

  function statusLine() { return h("p", { class: "status", role: "status", "aria-live": "polite" }); }
  function flash(el, text) { el.textContent = text; }

  function pick(label, options, value, onchange) {
    const sel = h("select", null, options.map(([v, t]) => h("option", { value: v, selected: String(v) === String(value ?? "") }, t)));
    sel.addEventListener("change", () => onchange(sel.value));
    return h("label", { class: "pick" }, label ? h("span", null, label) : null, sel);
  }
  function copyButton(text) {
    const btn = h("button", { type: "button", class: "small-btn" }, "Copy");
    btn.addEventListener("click", async () => {
      try { await navigator.clipboard.writeText(text); btn.textContent = "Copied"; }
      catch (e) { btn.textContent = "Copy by hand"; }
    });
    return btn;
  }
  function askLine(lead, request) {
    return h("p", { class: "ask" }, h("span", { class: "muted" }, lead + " "), h("code", null, request), " ", copyButton(request));
  }
  /** A small toggle that shows or hides a panel; remembers whether it was open. */
  function toggle(key, label, build) {
    const isOpen = S.open.has(key);
    const panel = h("div", { class: "panel", hidden: !isOpen });
    const btn = h("button", { type: "button", class: "toggle", "aria-expanded": String(isOpen) }, label);
    let built = false;
    const fill = () => { if (!built) { built = true; add(panel, [build()]); } };
    if (isOpen) fill();
    btn.addEventListener("click", () => {
      const open = panel.hidden;
      panel.hidden = !open;
      btn.setAttribute("aria-expanded", String(open));
      if (open) { S.open.add(key); fill(); } else S.open.delete(key);
    });
    return { btn, panel };
  }
  function table(head, rows, cls) {
    return h("div", { class: "table-wrap" }, h("table", { class: cls || "data" },
      h("thead", null, h("tr", null, head)), h("tbody", null, rows)));
  }
  function progressBar(done, n) {
    const fill = h("span");
    fill.style.width = `${(100 * done) / Math.max(1, n)}%`;
    return h("div", { class: "progress", role: "progressbar", "aria-label": `${done} of ${n} finished`, title: `${done} of ${n} finished`,
      "aria-valuemin": 0, "aria-valuemax": n, "aria-valuenow": done }, fill);
  }
  function whoLine(name) {
    return h("span", { class: "who" }, name, " ", h("button", { type: "button", class: "link-btn", onclick: () => setParams({ who: "change" }) }, "Change"));
  }
  function flowBar(pos, n, done, move, name) {
    return h("div", { class: "flowbar" },
      h("span", { class: "count" }, `${pos + 1} of ${n}`),
      progressBar(done, n),
      h("span", { class: "nav" },
        h("button", { type: "button", onclick: () => move(-1), disabled: pos === 0 }, "Back"),
        h("button", { type: "button", onclick: () => move(1), disabled: pos >= n - 1 }, "Next")),
      whoLine(name));
  }
  function nameStep(page, title, sentence, current) {
    const input = h("input", { type: "text", value: current || "", placeholder: "Your name", "aria-label": "Your name", maxlength: 60, autocomplete: "name" });
    const save = () => { const v = input.value.trim(); if (v) { setName(v); setParams({ who: "" }); } };
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") save(); });
    put(page, h("h1", null, title), h("p", { class: "lede" }, sentence),
      h("div", { class: "toolbar" }, input, h("button", { type: "button", class: "primary", onclick: save }, "Start")));
    setTimeout(() => input.focus(), 0);
    return page;
  }
  function typing(e) {
    const t = e.target;
    return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
  }

  // ------------------------------------------------------------------ frame, help and routing

  function setFrame(run, page, ctx) {
    tabsEl.replaceChildren();
    crumbEl.replaceChildren();
    if (!run) return;
    put(crumbEl, h("a", { href: runHref(run, "results") }, ctx ? ctx.r.title : run));
    for (const [id, title] of PAGES) {
      put(tabsEl, h("a", { href: runHref(run, id), "aria-current": id === page ? "page" : null }, title));
    }
  }

  function showHelp(open) {
    helpBox.hidden = !open;
    helpBtn.setAttribute("aria-expanded", String(open));
    if (open) {
      helpBox.replaceChildren(h("div", { class: "help-inner" }, h("strong", null, "Keyboard shortcuts"),
        h("dl", null, S.help.map(([k, t]) => [h("dt", null, k.split(" ").map((x) => h("kbd", null, x))), h("dd", null, t)]))));
    }
  }
  helpBtn.addEventListener("click", () => showHelp(helpBox.hidden));

  async function runCtx(run) {
    const r = await api("runs/" + enc(run));
    const models = {};
    for (const m of r.models) models[m.id] = shortModel(m);
    return { r, models, names: graderNames(r.judges), judges: r.judges };
  }
  function graderFor(run, params, ctx) {
    const ids = ctx.judges.map((j) => j.id);
    let g = params.get("grader");
    if (!ids.includes(g)) g = store.get("grader." + run, "");
    if (!ids.includes(g)) g = ctx.r.default_judge || ids[0];
    return g;
  }
  function graderOptions(ctx) { return ctx.judges.map((j) => [j.id, ctx.names[j.id]]); }

  async function render() {
    const token = ++S.token;
    S.keys = null;
    S.help = [];
    showHelp(false);
    const { parts, params } = parseHash();
    const isQuestion = (hash) => /^#\/run\/[^/]+\/answers\/[^/?]+/.test(hash);
    if (isQuestion(location.hash) && S.lastHash && !isQuestion(S.lastHash)) S.backTo = S.lastHash;   // where "Back" returns to
    S.lastHash = location.hash;
    try {
      if (!S.config) S.config = await api("config");
      modeEl.textContent = S.config.instructor ? "Instructor view" : "";
      let view;
      if (parts[0] === "run" && parts[1]) {
        const run = parts[1];
        const page = parts[2] || "results";
        if (OLD_PAGES[page]) {                         // links from the earlier version of the viewer
          const p = {};
          params.forEach((v, k) => { p[OLD_PARAMS[k] || k] = v; });
          history.replaceState(null, "", runHref(run, OLD_PAGES[page], p, page === "item" ? parts.slice(3) : null));
          return render();
        }
        const ctx = await runCtx(run);
        if (token !== S.token) return;
        setFrame(run, page, ctx);
        const fn = PAGE_FUNCS[page];
        if (!fn) throw new Error(`There is no page called “${page}”.`);
        view = await fn(run, params, parts.slice(3), ctx);
        const title = (PAGES.find((p) => p[0] === page) || ["", ""])[1];
        document.title = `${title} · ${ctx.r.title}`;
      } else {
        setFrame(null);
        view = await pageRuns();
        document.title = "Evaluation runs";
      }
      if (token !== S.token) return;
      main.replaceChildren(view);
      helpBtn.hidden = !S.help.length;
    } catch (err) {
      if (token !== S.token) return;
      helpBtn.hidden = true;
      main.replaceChildren(h("div", { class: "page" }, h("h1", null, "This page could not be shown"),
        h("p", null, err.message), h("p", null, h("a", { href: "#/" }, "All runs"))));
    }
  }

  document.addEventListener("keydown", (e) => {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "Escape" && !helpBox.hidden) { showHelp(false); return; }
    if (e.key === "?" && !typing(e) && S.help.length) { e.preventDefault(); showHelp(helpBox.hidden); return; }
    if (S.keys) S.keys(e);
  });
  window.addEventListener("hashchange", render);

  // ------------------------------------------------------------------ Runs

  async function pageRuns() {
    const d = await api("runs");
    const page = h("div", { class: "page" }, h("h1", null, "Evaluation runs"));
    if (!d.runs.length) {
      put(page, h("p", { class: "muted" }, "No runs yet. The viewer looks in ", d.roots.map((r, i) => [i ? ", " : "", h("code", null, r)]), "."));
      return page;
    }
    put(page, h("ul", { class: "runs" }, d.runs.map((r) => r.broken
      ? h("li", null, h("span", { class: "run" }, h("span", { class: "run-title" }, r.name), h("span", { class: "desc" }, "This run folder could not be read.")))
      : h("li", null, h("a", { class: "run", href: runHref(r.name, "results") },
        h("span", { class: "run-title" }, r.title), h("span", { class: "date" }, date(r.created)),
        h("span", { class: "desc", title: oneLine(r.description) }, oneLine(r.description)))))));
    return page;
  }

  // ------------------------------------------------------------------ Results

  function headline(family, a, imp2) {
    if (!a || !a.n) return null;
    if (family === "facts") return pct(a.correct);
    if (family === "user_says_something_wrong") return avg(imp2 ? a.mean_implicit2 : a.mean);
    return pct(a.does_task);
  }
  function headlineValue(family, a, imp2) {         // 0-100, for the bars
    if (!a || !a.n) return null;
    if (family === "facts") return a.correct ? a.correct.pct : null;
    if (family === "user_says_something_wrong") { const v = imp2 ? a.mean_implicit2 : a.mean; return v === null || v === undefined ? null : 50 * v; }
    return a.does_task ? a.does_task.pct : null;
  }

  async function pageResults(run, params, rest, ctx) {
    const grader = graderFor(run, params, ctx);
    const imp2 = params.get("implicit2") === "1";
    const fam = params.get("family") || "";
    const scope = params.get("scope") || "";
    const d = await api(`runs/${enc(run)}/scoreboard` + qs({ judge: grader, scope }));
    const page = h("div", { class: "page" }, h("h1", null, "Results"));

    const more = toggle("results-more", "More", () => h("div", { class: "options" },
      h("label", { class: "check" }, h("input", { type: "checkbox", checked: imp2, onchange: (e) => setParams({ implicit2: e.target.checked ? "1" : "" }) }),
        "Count implicit corrections as 2"),
      S.config.instructor ? pick("Questions", [["", "All"], ["visible", "Visible only"], ["held_back", "Held back only"]], scope,
        (v) => setParams({ scope: v })) : null));
    put(page, h("div", { class: "toolbar" },
      pick("Graded by", graderOptions(ctx), grader, (v) => { store.set("grader." + run, v); setParams({ grader: v }); }), more.btn), more.panel);

    const fams = FAMILIES.map((f) => d.families.find((x) => x.family === f)).filter(Boolean);
    put(page, resultsTable(d, fams, ctx, imp2, fam));
    const open = fams.find((f) => f.family === fam);
    if (open) put(page, breakdown(d, open, ctx, imp2, run));
    put(page, coreStyle(d, fams, ctx, imp2));
    if (d.ratings && d.ratings.comparisons) put(page, peoplesPicks(d.ratings, ctx));

    const next = nextStep(ctx.r);
    if (next) put(page, next);
    const about = toggle("results-about", "About this run", () => aboutRun(ctx));
    put(page, h("div", { class: "foot-tools" }, about.btn), about.panel);
    return page;
  }

  function resultsTable(d, fams, ctx, imp2, open) {
    const head = [h("th", { scope: "col" }, "Model"), fams.map((f) => h("th", { scope: "col", class: "num" + (f.family === open ? " open" : "") },
      h("button", { type: "button", class: "colhead", "aria-expanded": String(f.family === open), title: "Show by kind",
        onclick: () => setParams({ family: f.family === open ? "" : f.family }) },
      h("span", { class: "colname" }, `${TASK[f.family]} (${f.items})`), h("span", { class: "colsub" }, MEASURE[f.family]))))];
    const rows = d.models.map((m) => h("tr", null, h("th", { scope: "row" }, ctx.models[m.id]), fams.map((f) => {
      const a = (f.cells[m.id] || {}).all;
      const v = headline(f.family, a, imp2);
      return h("td", { class: "num big" + (f.family === open ? " open" : "") }, v || h("span", { class: "muted" }, "–"),
        v && a.n < f.items ? h("span", { class: "partial", title: `${a.n} of ${f.items} graded` }, ` (${a.n})`) : null);
    })));
    return table(head, rows, "data results");
  }

  function breakdown(d, f, ctx, imp2, run) {
    const fam = f.family;
    const sec = h("section", { class: "breakdown", "aria-label": `${TASK[fam]} by kind` },
      h("div", { class: "section-head" }, h("h2", null, `${TASK[fam]} by kind`),
        h("button", { type: "button", onclick: () => setParams({ family: "" }) }, "Close")));
    const nOf = (s) => Math.max(0, ...d.models.map((m) => ((f.cells[m.id] || {}).by_subtype[s] || {}).n || 0));
    put(sec, table([h("th", null, "Model"), f.subtypes.map((s) => h("th", { class: "num" },
      h("a", { href: runHref(run, "answers", { family: fam, subtype: s }) }, kindName(s)), ` (${nOf(s)})`))],
    d.models.map((m) => h("tr", null, h("th", { scope: "row" }, ctx.models[m.id]), f.subtypes.map((s) =>
      h("td", { class: "num" }, headline(fam, (f.cells[m.id] || {}).by_subtype[s], imp2) || "–"))))));

    const share = (a, k) => pct(((imp2 ? a.shares_implicit2 : a.shares) || {})[k]);
    const extra = {
      facts: [["String match", (a) => pct(a.string_match)]],
      user_says_something_wrong: [["Scored 0", (a) => share(a, "0")], ["Scored 1", (a) => share(a, "1")], ["Scored 2", (a) => share(a, "2")]],
      emotional_social: [["Uses all details", (a) => pct(a.all_details)], ["Has a placeholder", (a) => pct(a.has_placeholder)],
        ["Cut off", (a) => pct(a.truncated)]],
    }[fam];
    const any = d.models.some((m) => extra.some(([, fn]) => { const a = (f.cells[m.id] || {}).all; return a && a.n && fn(a) !== "–"; }));
    if (any) {
      put(sec, h("h3", null, "Other numbers"), table([h("th", null, "Model"), extra.map(([t]) => h("th", { class: "num" }, t))],
        d.models.map((m) => { const a = (f.cells[m.id] || {}).all || {}; return h("tr", null, h("th", { scope: "row" }, ctx.models[m.id]),
          extra.map(([, fn]) => h("td", { class: "num" }, a.n ? fn(a) : "–"))); })));
    }
    if (f.predicted_winner) {
      const m = d.models.find((x) => x.stands_for === f.predicted_winner);
      put(sec, h("p", { class: "muted" }, "Expected winner: " + (m ? ctx.models[m.id] : f.predicted_winner) + "."));
    }
    return sec;
  }

  /** One small bar panel per task type, models in the same order in each. */
  function coreStyle(d, fams, ctx, imp2) {
    const grid = h("div", { class: "panels" });
    for (const f of fams) {
      const style = f.family === "emotional_social";
      put(grid, h("figure", { class: "panel-chart " + (style ? "style" : "core") }, h("figcaption", null, TASK[f.family]),
        d.models.map((m) => {
          const a = (f.cells[m.id] || {}).all;
          const v = headlineValue(f.family, a, imp2);
          const text = headline(f.family, a, imp2) || "–";
          const fill = h("span", { class: "fill" });
          fill.style.width = `${Math.max(0, Math.min(100, v || 0))}%`;
          return h("div", { class: "bar-row", title: `${ctx.models[m.id]}: ${text}` },
            h("span", { class: "bar-name" }, ctx.models[m.id]), h("span", { class: "bar-track" }, v === null ? null : fill),
            h("span", { class: "bar-val" }, text));
        })));
    }
    return h("section", { class: "chart" }, h("h2", null, "Core vs. style"), grid,
      h("p", { class: "muted" }, "Facts and correcting the user should hold steady; emotional tasks show the style."));
  }

  function peoplesPicks(r, ctx) {
    return h("section", null, h("h2", null, "People’s picks"), h("ul", { class: "plain" }, r.pairs.map((p) =>
      h("li", null, p.models.map((m, i) => `${i ? ", " : ""}${ctx.models[m] || p.labels[i]} ${Math.round((100 * p.wins[m]) / p.n)}%`).join(""),
        h("span", { class: "muted" }, ` (${p.n} ${p.n === 1 ? "pick" : "picks"})`)))));
  }

  function nextStep(r) {
    for (const t of r.next_steps || []) {
      const m = String(t).match(/ask Claude:? “([^”]+)”|ask Claude:? "([^"]+)"/i);
      if (m) return askLine("Next step, ask Claude:", m[1] || m[2]);
    }
    return null;
  }

  function aboutRun(ctx) {
    const r = ctx.r;
    const box = h("div", { class: "about" });
    put(box, ...String(r.description || "").split(/\n\s*\n/).filter((p) => p.trim()).map((p) => h("p", null, p.trim())));
    put(box, h("h3", null, "Models"), h("dl", { class: "fields" }, r.models.map((m) => [h("dt", null, ctx.models[m.id]), h("dd", null, m.description || m.label)])));
    put(box, h("h3", null, "Questions"), h("dl", { class: "fields" }, r.item_sets.map((s) => [h("dt", null, h("code", null, s.path)),
      h("dd", null, `${s.items} questions` + (s.held_back ? ", held back" : ""))])));
    const gen = Object.entries(r.generation || {});
    if (gen.length) {
      put(box, h("h3", null, "How the answers were made"), h("dl", { class: "fields" }, gen.map(([k, v]) =>
        [h("dt", null, cap(k.replace(/_/g, " "))), h("dd", null, typeof v === "object" ? JSON.stringify(v) : String(v))])));
    }
    if (r.notes) put(box, h("h3", null, "Notes"), h("pre", { class: "notes" }, r.notes));
    return box;
  }

  // ------------------------------------------------------------------ Answers

  async function pageAnswers(run, params, rest, ctx) {
    if (rest[0]) return pageQuestion(run, params, rest[0], ctx);
    const grader = graderFor(run, params, ctx);
    const p = Object.fromEntries(["family", "subtype", "model", "grade", "q", "scope", "limit"].map((k) => [k, params.get(k) || ""]));
    const limit = Math.max(50, parseInt(p.limit || "50", 10) || 50);
    const d = await api(`runs/${enc(run)}/items` + qs({ family: p.family, subtype: p.subtype, model: p.model, judge: grader,
      verdict: p.family ? p.grade : "", q: p.q, scope: p.scope, limit }));
    const page = h("div", { class: "page" }, h("h1", null, "Answers"));

    const search = h("input", { type: "search", value: p.q, placeholder: "Search", "aria-label": "Search questions and answers" });
    let timer = null;
    search.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => setParams({ q: search.value, limit: "" }), 400); });
    search.addEventListener("keydown", (e) => { if (e.key === "Enter") { clearTimeout(timer); setParams({ q: search.value, limit: "" }); } });
    put(page, h("div", { class: "toolbar" },
      pick("Task type", [["", "All"], ...FAMILIES.map((f) => [f, TASK[f]])], p.family, (v) => setParams({ family: v, subtype: "", grade: "", limit: "" })),
      pick("Model", [["", "All"], ...d.models.map((m) => [m.id, ctx.models[m.id]])], p.model, (v) => setParams({ model: v, limit: "" })),
      p.family ? pick("Grade", [["", "Any"], ...GRADES[p.family].map((g) => [String(g.v), gradeWord(p.family, g.v)])], p.grade,
        (v) => setParams({ grade: v, limit: "" })) : null,
      search));

    const shown = p.model ? d.models.filter((m) => m.id === p.model) : d.models;
    S.lastList = { run, back: location.hash, ids: d.rows.map((r) => r.id) };
    put(page, h("p", { class: "count-line muted" }, `${d.total} ${d.total === 1 ? "question" : "questions"}, graded by ${ctx.names[grader] || grader}`,
      p.subtype ? [`, kind: ${kindName(p.subtype)} `, h("button", { type: "button", class: "link-btn", onclick: () => setParams({ subtype: "" }) }, "Clear")] : null));
    if (!d.rows.length) { put(page, h("p", null, "No questions match.")); return page; }
    let lastFam = null;
    put(page, table([h("th", { scope: "col" }, "Question"), shown.map((m) => h("th", { scope: "col", class: "num" }, ctx.models[m.id]))],
      d.rows.map((r) => {
        const link = runHref(run, "answers", null, [r.id]);
        const group = !p.family && r.family !== lastFam
          ? h("tr", { class: "group" }, h("th", { scope: "colgroup", colspan: shown.length + 1 }, TASK[r.family])) : null;
        lastFam = r.family;
        return [group, h("tr", { class: "clickable", onclick: (e) => { if (e.target.tagName !== "A") go(link); } },
          h("td", null, h("a", { href: link }, r.prompt), r.held_back ? h("span", { class: "tag" }, "held back") : null),
          shown.map((m) => h("td", { class: "num" }, r.verdicts && r.verdicts[m.id] !== undefined ? badge(r.family, r.verdicts[m.id]) : h("span", { class: "muted" }, "–"))))];
      }), "data list"));
    if (d.total > d.rows.length) {
      put(page, h("p", null, h("button", { type: "button", onclick: () => setParams({ limit: limit + 50 }) }, "Show more")));
    }
    return page;
  }

  async function pageQuestion(run, params, id, ctx) {
    const d = await api(`runs/${enc(run)}/items/${enc(id)}`);
    const it = d.item;
    const fam = it.family;
    const grader = graderFor(run, params, ctx);
    const list = S.lastList && S.lastList.run === run ? S.lastList : null;
    const i = list ? list.ids.indexOf(id) : -1;
    const prev = i > 0 ? list.ids[i - 1] : null;
    const next = list && i >= 0 && i < list.ids.length - 1 ? list.ids[i + 1] : null;
    const at = (x) => runHref(run, "answers", params.get("grader") ? { grader: params.get("grader") } : null, [x]);
    const page = h("div", { class: "page" });
    const back = S.backTo && S.backTo.startsWith(`#/run/${enc(run)}/`) ? S.backTo : list ? list.back : runHref(run, "answers");
    put(page, h("div", { class: "pager" }, h("a", { href: back }, "Back"),
      h("span", { class: "nav" }, prev ? h("a", { href: at(prev) }, "Previous") : null, next ? h("a", { href: at(next) }, "Next") : null)));
    if (prev || next) {
      S.help.push(["← →", "Previous or next question"]);
      S.keys = (e) => {
        if (typing(e)) return;
        if (e.key === "ArrowLeft" && prev) go(at(prev));
        if (e.key === "ArrowRight" && next) go(at(next));
      };
    }
    put(page, h("p", { class: "context muted" }, TASK[fam], it.held_back ? h("span", { class: "tag" }, "held back") : null),
      h("h1", { class: "question" }, it.prompt), keyLine(fam, it));

    for (const m of d.models) {
      const r = d.responses[m.id];
      if (!r) continue;
      const vs = d.verdicts[m.id] || [];
      const mainV = vs.find((v) => v.judge === grader);
      const others = vs.filter((v) => v !== mainV);
      const other = others.length ? toggle("q-others-" + m.id, "Other graders", () => h("ul", { class: "verdicts" }, others.map((v) =>
        h("li", null, badge(fam, v.label), h("span", { class: "who-name" }, ctx.names[v.judge] || v.judge_label), v.reason ? h("span", { class: "reason" }, v.reason) : null)))) : null;
      put(page, h("section", { class: "answer" },
        h("div", { class: "answer-head" }, h("h2", null, ctx.models[m.id]), mainV ? badge(fam, mainV.label) : h("span", { class: "muted small" }, "Not graded"),
          r.hit_limit ? h("span", { class: "muted small", title: "The answer reached the length limit" }, "Cut off") : null),
        h("pre", { class: "text" }, r.response || "(empty answer)"),
        mainV && mainV.reason ? h("p", { class: "reason" }, `${ctx.names[grader]}: ${mainV.reason}`) : null,
        other ? [other.btn, other.panel] : null));
    }
    const details = toggle("q-details", "Details", () => h("div", null,
      h("dl", { class: "fields" }, h("dt", null, "ID"), h("dd", null, it.id), h("dt", null, "Kind"), h("dd", null, kindName(it.subtype))),
      itemDetails(fam, it, ["answer", "correct_fact", "task_check"]),
      d.queues.length ? h("p", null, d.queues.map((q) => h("a", { href: runHref(run, "review", { queue: q.queue || q.id, pos: q.pos }) }, `In ${q.label}, item ${q.pos + 1}`))) : null));
    put(page, h("div", { class: "foot-tools" }, details.btn), details.panel);
    return page;
  }

  // ------------------------------------------------------------------ Graders

  async function pageGraders(run, params, rest, ctx) {
    const scope = params.get("scope") || "";
    const J = await api(`runs/${enc(run)}/judges` + qs({ scope }));
    const ids = J.judges.map((j) => j.id);
    const names = graderNames(J.judges);
    let a = params.get("a");
    let b = params.get("b");
    if (!ids.includes(a)) a = J.default_judge || ids[0];
    if (!ids.includes(b) || b === a) {
      const graders = J.judges.filter((j) => j.kind === "claude-subagent" && j.id !== a);
      b = (graders[0] || J.judges.find((j) => j.id !== a) || {}).id;
    }
    const page = h("div", { class: "page" }, h("h1", null, "Graders"), h("p", { class: "lede" }, "How often two graders give the same grade."));
    if (!b) { put(page, h("p", null, "This run has only one grader.")); return page; }
    const opts = J.judges.map((j) => [j.id, names[j.id]]);
    put(page, h("div", { class: "toolbar" }, h("span", null, "Compare"), pick(null, opts, a, (v) => setParams({ a: v })),
      h("span", null, "with"), pick(null, opts, b, (v) => setParams({ b: v }))));
    const C = await api(`runs/${enc(run)}/judges/compare` + qs({ a, b, scope }));
    if (!C.n) { put(page, h("p", null, "These two graders have no answers in common.")); return page; }

    put(page, table([h("th", { scope: "col" }, "Task type"), h("th", { scope: "col", class: "num" }, "Agree")],
      C.families.map((f) => h("tr", null, h("th", { scope: "row" }, `${TASK[f.family]} (${f.n})`), h("td", { class: "num big" }, Math.round(f.pct) + "%"))),
      "data narrow"));

    const nd = C.disagreements.length;
    const sec = h("section", { class: "disagree" });
    put(sec, h("div", { class: "section-head" }, h("h2", null, nd ? `They disagree on ${nd} ${nd === 1 ? "answer" : "answers"}` : "They agree on every answer"),
      nd && C.queue ? h("a", { class: "button primary", href: runHref(run, "review", { queue: C.queue.id }) }, "Review them") : null));
    if (nd && !C.queue) put(sec, askLine("To review them, ask Claude:", `build a review queue for run ${run}: ${a} vs ${b}`));
    if (nd) {
      const all = S.open.has("graders-all");
      const shown = all ? C.disagreements : C.disagreements.slice(0, 8);
      put(sec, h("ul", { class: "dis-list" }, shown.map((x) => {
        const fam = x.family;
        return h("li", null, h("a", { href: runHref(run, "answers", { grader: a }, [x.id]) },
          h("span", { class: "dis-q" }, x.prompt), h("span", { class: "sub" }, `${ctx.models[x.model] || x.model_label}, ${TASK[fam]}`)),
        h("span", { class: "dis-grades" }, h("span", null, names[a], " ", badge(fam, LABEL_BACK[x.a.label])),
          h("span", null, names[b], " ", badge(fam, LABEL_BACK[x.b.label]))));
      })));
      if (nd > 8) {
        put(sec, h("p", null, h("button", { type: "button", onclick: () => { if (all) S.open.delete("graders-all"); else S.open.add("graders-all"); render(); } },
          all ? "Show fewer" : `Show all ${nd}`)));
      }
    }
    put(page, sec);
    const details = toggle("graders-details", "Details", () => gradersDetails(run, C, J, names, ctx, a, b));
    put(page, h("div", { class: "foot-tools" }, details.btn), details.panel);
    return page;
  }

  function gradersDetails(run, C, J, names, ctx, a, b) {
    const box = h("div", { class: "details" });
    put(box, h("p", null, "Kappa is agreement corrected for chance: 1 means the two always agree, 0 means no better than guessing."),
      table([h("th", null, "Task type"), h("th", { class: "num" }, "Agree"), h("th", { class: "num" }, "Kappa")],
        C.families.map((f) => h("tr", null, h("th", { scope: "row" }, TASK[f.family]), h("td", { class: "num" }, `${f.agree} of ${f.n}`),
          h("td", { class: "num" }, f.kappa === null || f.kappa === undefined ? "–" : f.kappa.toFixed(2)))), "data narrow"));
    for (const f of C.families) {
      const isMean = f.family === "user_says_something_wrong";
      const fmt = (x) => (x === null || x === undefined ? "–" : isMean ? avg(x) : Math.round(x) + "%");
      const rankA = [...f.headlines].sort((x, y) => y.a - x.a).map((r) => ctx.models[r.model]).join(", ");
      put(box, h("h3", null, TASK[f.family]),
        h("p", { class: "muted" }, `Who said what (rows: ${names[a]}, columns: ${names[b]})`),
        table([h("th", null, ""), f.labels.map((l) => h("th", { class: "num" }, cap(l)))],
          f.confusion.map((row, i) => h("tr", null, h("th", { scope: "row" }, cap(f.labels[i])),
            row.map((x, j) => h("td", { class: "num" + (x && i !== j ? " off" : "") }, x)))), "data narrow"),
        isMean ? h("p", { class: "muted" }, `Within one point: ${f.within_one} of ${f.n}. Counting implicit corrections as 2: ${Math.round(f.implicit2.pct)}% agree.`) : null,
        h("p", { class: "muted" }, `Results under each grader (${MEASURE[f.family]})`),
        table([h("th", null, "Model"), h("th", { class: "num" }, names[a]), h("th", { class: "num" }, names[b])],
          f.headlines.map((r) => h("tr", null, h("th", { scope: "row" }, ctx.models[r.model] || r.label), h("td", { class: "num" }, fmt(r.a)), h("td", { class: "num" }, fmt(r.b)))),
          "data narrow"),
        h("p", null, f.flips.length
          ? "The order of models changes: " + f.flips.map((x) => x.map((l) => shortModel({ label: l })).join(" vs ")).join("; ") + "."
          : `Same order of models under both: ${rankA}.`));
    }
    if (J.notes_effect.length) {
      put(box, h("h3", null, "Rubric notes: before and after"));
      for (const e of J.notes_effect) {
        put(box, h("p", null, `${names[e.before] || e.before} to ${names[e.after] || e.after}: ${e.changed} of ${e.regraded} grades changed` +
          (e.notes_file ? ` (notes: ${e.notes_file})` : "") + "." + (e.rubric_changed ? " The rubric file also changed." : "")));
        if (e.reviewers.length) {
          put(box, table([h("th", null, "Agreement with"), h("th", { class: "num" }, "Before"), h("th", { class: "num" }, "After")],
            e.reviewers.map((r) => h("tr", null, h("th", { scope: "row" }, names[r.judge] || r.label), h("td", { class: "num" }, pct(r.before)), h("td", { class: "num" }, pct(r.after)))),
            "data narrow"));
        }
      }
    }
    const pairs = [...J.matrix].sort((x, y) => y.n - x.n);
    put(box, h("h3", null, "Every pair"), table([h("th", null, "Grader"), h("th", null, "Grader"), h("th", { class: "num" }, "Both graded"), h("th", { class: "num" }, "Agree")],
      pairs.map((r) => h("tr", { class: "clickable", onclick: () => { setParams({ a: r.a, b: r.b }, true); } },
        h("td", null, h("a", { href: runHref(run, "graders", { a: r.a, b: r.b }) }, names[r.a])), h("td", null, names[r.b]),
        h("td", { class: "num" }, r.n), h("td", { class: "num" }, Math.round(r.pct) + "%")))));
    put(box, h("h3", null, "The graders"), h("dl", { class: "fields" }, J.judges.map((j) => [h("dt", null, names[j.id]), h("dd", null, j.description)])));
    return box;
  }

  // ------------------------------------------------------------------ Review

  async function pageReview(run, params, rest, ctx) {
    const name = getName();
    const page = h("div", { class: "page" });
    if (!name || params.get("who") === "change") return nameStep(page, "Review", "Grade answers yourself, then see what two graders said.", name);
    const { queues } = await api(`runs/${enc(run)}/queues`);
    if (!queues.length) {
      put(page, h("h1", null, "Review"), h("p", null, "No review set yet."), askLine("To make one, ask Claude:", `audit run ${run} with Opus at medium effort`));
      return page;
    }
    let queue = params.get("queue");
    if (!queues.some((q) => q.id === queue)) {
      if (queues.length > 1) {
        put(page, h("h1", null, "Review"), h("ul", { class: "runs" }, queues.map((q) => h("li", null,
          h("a", { class: "run", href: runHref(run, "review", { queue: q.id }) }, h("span", { class: "run-title" }, q.label), h("span", { class: "date" }, `${q.n} answers`))))));
        return page;
      }
      queue = queues[0].id;
      const p = paramsObj(params);
      p.queue = queue;
      history.replaceState(null, "", runHref(run, "review", p));
    }
    const prog = await api(`runs/${enc(run)}/queues/${enc(queue)}` + qs({ reviewer: name }));
    let pos = params.has("pos") ? parseInt(params.get("pos"), 10) : prog.next_pos;
    if (pos === null || pos === undefined || Number.isNaN(pos)) return reviewDone(run, page, prog, ctx, name);
    pos = Math.max(0, Math.min(prog.n - 1, pos));
    const it = await api(`runs/${enc(run)}/queues/${enc(queue)}/${pos}` + qs({ reviewer: name }));
    const move = (dlt) => { const p = it.pos + dlt; if (p >= 0 && p < it.n) setParams({ pos: p }, true); };
    put(page, h("h1", { class: "sr-only" }, "Review"), flowBar(it.pos, it.n, prog.done, move, name), reviewItem(run, queue, it, move, name));
    return page;
  }

  function reviewDone(run, page, prog, ctx, name) {
    const me = ctx.judges.find((j) => j.info && j.info.source === "review" && j.info.reviewer === name);
    put(page, h("h1", null, "Review"), whoLine(name), h("p", { class: "big-note" }, `All ${prog.n} done.`),
      h("div", { class: "actions" },
        h("a", { class: "button primary", href: runHref(run, "graders", me ? { a: ctx.r.default_judge, b: me.id } : null) }, "Compare with graders"),
        h("button", { type: "button", onclick: () => setParams({ pos: 0 }, true) }, "Look back")));
    return page;
  }

  function gradeButtons(fam, current, onpick, big) {
    return h("div", { class: "grades" + (big ? " big" : ""), role: "group", "aria-label": "Grade" }, GRADES[fam].map((g) =>
      h("button", { type: "button", class: "grade " + tone(fam, g.v), "aria-pressed": String(current === g.v), onclick: () => onpick(g.v) },
        fam === "user_says_something_wrong" ? [h("span", { class: "num-big" }, g.sym), h("span", { class: "cap" }, g.word)]
          : fam === "facts" ? `${g.sym} ${g.word}` : g.word)));
  }

  function graderBlock(title, fam, v) {
    return h("div", { class: "grader" }, h("h3", null, title),
      v ? [h("p", { class: "grader-grade" }, badge(fam, v.label), " ", gradeWord(fam, v.label)), h("p", { class: "reason" }, v.reason || "No reason given.")]
        : h("p", { class: "muted" }, "No grade."));
  }

  function reviewItem(run, queue, it, move, name) {
    const fam = it.family;
    const field = LABEL_FIELD[fam];
    const box = h("div", { class: "review" });
    const rubric = toggle("review-rubric", "Rubric", () => h("div", null, itemDetails(fam, it.item, [{ facts: "answer", user_says_something_wrong: "correct_fact", emotional_social: "task_check" }[fam]]),
      h("pre", { class: "notes" }, it.rubric || "(The rubric file was not found.)")));
    put(box, h("p", { class: "context muted" }, TASK[fam], it.held_back ? h("span", { class: "tag" }, "held back") : null, rubric.btn), rubric.panel,
      h("p", { class: "question" }, it.prompt), keyLine(fam, it.item),
      h("pre", { class: "text" }, it.response || "(empty answer)"),
      it.length && it.length.hit_limit ? h("p", { class: "muted small" }, "Cut off at the length limit.") : null);

    // Step 1: the reviewer's own grade, before seeing anything else. Saving locks it.
    const step1 = h("section", { class: "step" }, h("h2", null, "Your grade"));
    put(box, step1);
    if (it.blind) {
      const mine = it.blind[field];
      put(step1, h("p", { class: "locked" }, badge(fam, mine), " ", gradeWord(fam, mine), h("span", { class: "muted small" }, "Saved")));
    } else {
      let choice;
      const status = statusLine();
      const btns = h("div");
      const draw = () => btns.replaceChildren(gradeButtons(fam, choice, (v) => { choice = v; draw(); }, true));
      draw();
      const save = async () => {
        if (choice === undefined) { flash(status, "Choose a grade first."); return; }
        try {
          await api(`runs/${enc(run)}/review`, { reviewer: name, queue, queue_key: it.queue_key, event: "blind", grade: { [field]: choice } });
          render();
        } catch (e) { flash(status, e.message); }
      };
      put(step1, btns, h("div", { class: "actions" }, h("button", { type: "button", class: "primary", onclick: save }, "Save")), status);
      S.help.push([GRADES[fam].map((g) => g.key.toUpperCase()).join(" "), "Choose a grade"], ["Enter", "Save"], ["← →", "Back or next"]);
      S.keys = (e) => {
        if (typing(e)) return;
        const g = GRADES[fam].find((x) => x.key === e.key.toLowerCase());
        if (g) { choice = g.v; draw(); e.preventDefault(); }
        else if (e.key === "Enter") { e.preventDefault(); save(); }
        else if (e.key === "ArrowLeft") move(-1);
        else if (e.key === "ArrowRight") move(1);
      };
      return box;
    }

    // Step 2: the two graders, identities hidden. Step 3: who was right.
    const g1 = it.revealed && it.revealed.grader_1;
    const g2 = it.revealed && it.revealed.grader_2;
    put(box, h("section", { class: "step" }, h("h2", null, "The graders"),
      h("div", { class: "pair" }, graderBlock("Grader 1", fam, g1), graderBlock("Grader 2", fam, g2))));
    const agree = g1 && g2 && g1.label === g2.label;
    let call = it.final ? it.final.final_call : null;
    let corrected = it.final && it.final.corrected_grade ? it.final.corrected_grade[field] : it.blind[field];
    const reason = h("input", { type: "text", class: "why", id: "why", maxlength: 300, value: it.final ? it.final.reason : "", autocomplete: "off" });
    const status = statusLine();
    const calls = h("div");
    const opts = [["grader_1", "Grader 1", "1"], ["grader_2", "Grader 2", "2"], ["both_wrong", "Neither", "N"]];
    if (agree || call === "both_fine") opts.push(["both_fine", "Both", "B"]);
    const draw = () => {
      calls.replaceChildren();
      put(calls, h("div", { class: "calls", role: "group", "aria-label": "Who was right?" }, opts.map(([v, t]) =>
        h("button", { type: "button", "aria-pressed": String(call === v), onclick: () => { call = v; draw(); } }, t))),
      call === "both_wrong" ? h("div", { class: "corrected" }, h("span", { class: "muted" }, "The right grade:"),
        gradeButtons(fam, corrected, (v) => { corrected = v; draw(); }, false)) : null);
    };
    draw();
    const save = async () => {
      if (!call) { flash(status, "Choose who was right."); return; }
      if (!reason.value.trim()) { flash(status, "Write a few words on why."); reason.focus(); return; }
      const body = { reviewer: name, queue, queue_key: it.queue_key, event: "final", final_call: call, reason: reason.value };
      if (call === "both_wrong") body.corrected_grade = { [field]: corrected };
      try {
        await api(`runs/${enc(run)}/review`, body);
        const p2 = await api(`runs/${enc(run)}/queues/${enc(queue)}` + qs({ reviewer: name }));
        const nextTodo = p2.positions.find((p) => p.pos > it.pos && p.state !== "final") || p2.positions.find((p) => p.state !== "final");
        setParams({ pos: nextTodo ? nextTodo.pos : "" }, true);
      } catch (e) { flash(status, e.message); }
    };
    put(box, h("section", { class: "step" }, h("h2", null, "Who was right?"), calls,
      h("label", { class: "why-label", for: "why" }, "Why?"), reason,
      h("div", { class: "actions" }, h("button", { type: "button", class: "primary", onclick: save }, "Save & next"),
        it.final ? h("span", { class: "muted small" }, "Saved. Saving again replaces it.") : null),
      status));
    S.help.push([opts.map((o) => o[2]).join(" "), "Who was right"], ["R", "Write why"], ["Enter", "Save & next"], ["← →", "Back or next"]);
    S.keys = (e) => {
      if (typing(e)) {
        if (e.key === "Enter" && e.target === reason) { e.preventDefault(); save(); }
        if (e.key === "Escape") e.target.blur();
        return;
      }
      const k = e.key.toLowerCase();
      const o = opts.find((x) => x[2].toLowerCase() === k);
      if (o) { call = o[0]; draw(); e.preventDefault(); }
      else if (k === "r") { e.preventDefault(); reason.focus(); }
      else if (e.key === "Enter") { e.preventDefault(); if (reason.value.trim()) save(); else reason.focus(); }
      else if (e.key === "ArrowLeft") move(-1);
      else if (e.key === "ArrowRight") move(1);
    };
    return box;
  }

  // ------------------------------------------------------------------ Rate

  async function pageRate(run, params, rest, ctx) {
    const name = getName();
    const page = h("div", { class: "page" });
    if (!name || params.get("who") === "change") return nameStep(page, "Rate", "Pick the message you would rather send, without knowing which model wrote it.", name);
    const d = await api(`runs/${enc(run)}/ratings` + qs({ rater: name }));
    const aid = params.get("assignment");
    const mine = d.assignments.find((a) => a.assignment === aid);
    if (!mine) return rateStart(run, page, d, ctx, name);
    if (params.get("pos") === "done" || (mine.done >= mine.n && !params.has("pos"))) {
      put(page, h("h1", null, "Rate"), whoLine(name), h("p", { class: "big-note" }, `All ${mine.n} done. Thank you.`),
        h("div", { class: "actions" }, h("button", { type: "button", onclick: () => setParams({ pos: 0 }, true) }, "Look back"),
          h("a", { class: "button", href: runHref(run, "rate") }, "Start another")));
      return page;
    }
    let pos = params.has("pos") ? parseInt(params.get("pos"), 10) : 0;
    if (Number.isNaN(pos)) pos = 0;
    pos = Math.max(0, Math.min(mine.n - 1, pos));
    let it = await api(`runs/${enc(run)}/ratings/${enc(aid)}/${pos}` + qs({ rater: name }));
    if (!params.has("pos") && it.done[pos]) {                // pick up where this rater left off
      const todo = it.done.findIndex((x) => !x);
      if (todo >= 0) it = await api(`runs/${enc(run)}/ratings/${enc(aid)}/${todo}` + qs({ rater: name }));
    }
    put(page, h("h1", { class: "sr-only" }, "Rate"), rateItem(run, aid, it, name));
    return page;
  }

  function rateStart(run, page, d, ctx, name) {
    const models = d.models;
    const persona = models.find((m) => m.stands_for === "persona");
    const succinct = models.find((m) => m.stands_for === "succinct");
    let a = (succinct || models[0] || {}).id;
    let b = (persona || models.find((m) => m.id !== a) || {}).id;
    const status = statusLine();
    const opts = models.map((m) => [m.id, ctx.models[m.id]]);
    const start = async () => {
      if (!a || !b || a === b) { flash(status, "Choose two different models."); return; }
      try {
        const r = await api(`runs/${enc(run)}/ratings/assign`, { rater: name, models: [a, b] });
        go(runHref(run, "rate", { assignment: r.assignment }));
      } catch (e) { flash(status, e.message); }
    };
    put(page, h("div", { class: "page-head" }, h("h1", null, "Rate"), whoLine(name)),
      h("p", { class: "lede" }, "Pick the message you would rather send, without knowing which model wrote it."),
      h("div", { class: "toolbar" }, h("span", null, "Compare"), pick(null, opts, a, (v) => { a = v; }), h("span", null, "with"),
        pick(null, opts, b, (v) => { b = v; }), h("button", { type: "button", class: "primary", onclick: start }, "Start")), status);
    if (d.assignments.length) {
      put(page, h("h2", null, "Your sets"), h("ul", { class: "runs" }, d.assignments.map((x) => h("li", null,
        h("a", { class: "run", href: runHref(run, "rate", { assignment: x.assignment }) },
          h("span", { class: "run-title" }, x.models.map((m) => ctx.models[m] || m).join(" vs ")), h("span", { class: "date" }, `${x.done} of ${x.n}`))))));
    }
    return page;
  }

  function rateItem(run, aid, it, name) {
    let tl = it.saved ? it.saved.task_check_left : null;
    let tr = it.saved ? it.saved.task_check_right : null;
    const prefer = it.saved ? it.saved.prefer : null;
    const status = statusLine();
    const move = (dlt) => { const p = it.pos + dlt; if (p >= 0 && p < it.n) setParams({ pos: p }, true); };
    const send = async (side) => {
      if (typeof tl !== "boolean" || typeof tr !== "boolean") { flash(status, "Answer “Does it do the task?” for both messages first."); return; }
      try {
        await api(`runs/${enc(run)}/ratings`, { rater: name, assignment: aid, pos: it.pos, prefer: side, task_check_left: tl, task_check_right: tr });
        const nextTodo = it.done.findIndex((x, i) => !x && i > it.pos);
        const firstTodo = it.done.findIndex((x, i) => !x && i !== it.pos);
        const nxt = nextTodo >= 0 ? nextTodo : firstTodo;
        setParams({ pos: nxt >= 0 ? nxt : "done" }, true);
      } catch (e) { flash(status, e.message); }
    };
    const sides = h("div", { class: "sides" });
    const side = (which, text, check, set) => h("div", { class: "side" + (prefer === which ? " chosen" : "") },
      h("pre", { class: "text" }, text || "(empty message)"),
      h("div", { class: "check" }, h("span", null, "Does it do the task?"),
        h("span", { class: "yesno", role: "group", "aria-label": "Does it do the task?" },
          h("button", { type: "button", class: "good", "aria-pressed": String(check === true), onclick: () => { set(true); draw(); } }, "Yes"),
          h("button", { type: "button", class: "bad", "aria-pressed": String(check === false), onclick: () => { set(false); draw(); } }, "No"))),
      h("button", { type: "button", class: "send" + (prefer === which ? " primary" : ""), onclick: () => send(which) }, "Send this one"));
    const draw = () => sides.replaceChildren(side("left", it.left, tl, (v) => { tl = v; }), side("right", it.right, tr, (v) => { tr = v; }));
    draw();
    S.help.push(["Q A", "Left: yes or no"], ["P L", "Right: yes or no"], ["1 2", "Send left or right"], ["← →", "Back or next"]);
    S.keys = (e) => {
      if (typing(e)) return;
      const k = e.key.toLowerCase();
      const set = { q: () => { tl = true; }, a: () => { tl = false; }, p: () => { tr = true; }, l: () => { tr = false; } }[k];
      if (set) { set(); draw(); e.preventDefault(); }
      else if (k === "1") { e.preventDefault(); send("left"); }
      else if (k === "2") { e.preventDefault(); send("right"); }
      else if (e.key === "ArrowLeft") move(-1);
      else if (e.key === "ArrowRight") move(1);
    };
    return h("div", { class: "rate" }, flowBar(it.pos, it.n, it.done.filter(Boolean).length, move, name),
      h("p", { class: "question" }, it.prompt),
      it.task_check ? h("p", { class: "key" }, h("span", { class: "muted" }, "Counts if: "), it.task_check) : null,
      sides, status);
  }

  const PAGE_FUNCS = { results: pageResults, answers: pageAnswers, graders: pageGraders, review: pageReview, rate: pageRate };

  render();
})();
