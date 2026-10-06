---
name: setup
description: First-time setup for HW2 (Part 0), run once after cloning the fork. Checks the git remotes, gets the Colab MCP server running, collects the student's answers about MCP (Part 0 Steps B and D), connects a Colab notebook with a T4 GPU, records the student's name and date, starts the evaluation viewer, and ends with the Part 0 checkpoint. Run it again if anything breaks or to check a setup is complete.
---

Do these in order and show what you ran. Skip what is already done. If something fails, say in
plain words what it means and what to change, and stop there. Every restart of Claude Code
follows "Restarting Claude Code" in `CLAUDE.md`.

If `git log --format=%s` shows a commit whose whole subject is exactly `Part 0 done`, setup is
already finished. Only check that the Colab tools (items 2 to 4), the notebook (items 6 and 7)
and the viewer (item 10) work. Don't write in `WRITEUP.md` and don't commit.

1. Run `git remote -v`. If `origin` is `shilad/comp440-hw2` itself, they cloned the template
   without forking it, and they can't push there. Ask them to fork it on GitHub and give you
   their fork's URL, then run `git remote set-url origin <fork URL>`. Then add the
   `upstream` remote if it is missing:
   `git remote add upstream "${HW2_UPSTREAM:-https://github.com/shilad/comp440-hw2}"`
   It is there in case a fix to the template has to go out mid-assignment.
2. If the Colab tools from the `cool-colab-mcp` server (for example
   `open_colab_browser_connection`) are available, skip to item 5. Otherwise find out why
   before installing anything. Run `claude mcp get cool-colab-mcp` and read its `Status:` line:
   - `Connected`: the server works, and this session's copy may still be starting. Wait a
     minute and check for the tools again. If they are still missing, restart Claude Code as in
     item 4.
   - `Rejected` or `Pending approval`: the server was declined or never approved. Restart
     Claude Code as in item 4, with `claude mcp reset-project-choices` as the command to run
     first. They approve the server when asked.
   - `Failed to connect`: its packages are probably missing. Go on to item 3.

   If that command fails or shows none of these, ask them to type `/mcp` and tell you what it
   shows for `cool-colab-mcp`. Still connecting: wait and check again. Declined, disabled or
   missing: restart with `claude mcp reset-project-choices` as above. Failed: go on to item 3.
3. Install what the server needs.
   - Run `uv --version`. If `uv` is missing, ask whether you may install it with
     `curl -LsSf https://astral.sh/uv/install.sh | sh` (the official installer), and run it only
     on a yes. If `uv` is not on the PATH yet, call it by the full path the installer printed.
   - Run `uv sync --frozen --directory tools/cool-colab-mcp`, allowing up to 10 minutes. The
     first run downloads Python and about 370 MB of packages, so tell them it may take a while.
     `--frozen` installs exactly the versions in the lock file; never drop it.
4. Restart Claude Code so the server starts again. If you just installed `uv`, the way back
   starts with a new terminal window, because the old one can't find `uv`. When they are back,
   check for the Colab tools.
   - If they are still missing, run `claude mcp get cool-colab-mcp` again and the server's own
     check, `uv run --frozen --directory tools/cool-colab-mcp cool-colab-mcp doctor`, to see
     why. Fix what they report and restart once more.
   - If that restart doesn't fix it, stop. Ask them to post the error in `#comp440-f26`.
5. Step B. Point them to Step B of `instructions/part0.md`: the "What is MCP?" video
   (https://www.youtube.com/watch?v=eur8dUO9mvE, IBM Technology, 3:46) and the questions. Wait
   until they have watched it. Ask for their paragraph: what an MCP server is, why it is
   useful, and at least one example other than Colab. Write it under "Step B: What an MCP
   server is and why it's useful" in `WRITEUP.md`, following the writing rules in `CLAUDE.md`.
   Show it and ask whether it says what they meant. Then ask for their answer to "Why not just
   paste code into Colab yourself?" and write it under "Step B: Why not paste code into Colab
   yourself" the same way.
6. Load the HW2 notebook into Colab.
   - Call `register_notebook` with `notebook_id` "hw2", `name` "COMP 440 HW2" and `local_path`
     set to the absolute path of `colab/hw2.ipynb` in this repository.
   - Before you call `open_notebook`, tell them a Colab tab is about to open in their browser.
     Within a minute they should click Connect in Colab's dialog, and Allow if Chrome asks
     about devices on their local network.
   - Call `open_notebook` with "hw2". It waits 60 seconds for the tab to connect.
   - If it times out, ask what they see: no tab, a Google sign-in page, or the dialog. Fix that,
     call `open_notebook` again, and ask them to close the older tab so only one tab is
     connected.
7. Ask them to choose Runtime > Change runtime type > T4 GPU in Colab's menu, not the TPU, and to
   say when it is done. Then call `get_runtime_status` with `notebook_id` "hw2". If it does not
   report a T4 GPU, say what it reports and ask again. Tell them the small cell it added at the
   top of the notebook comes from the server and is harmless. If Colab says no GPU is
   available, tell them that is Colab's limit on free GPUs, not a problem with their setup.
   Finish the rest of setup and try the T4 again later, since Part 1 needs it.
8. Ask their name, and fill `**Name:**` and `**Date:**` at the top of `WRITEUP.md`. Those two
   are yours to compose; every other slot in that file you fill from their own words.
9. Run `python3 dump_transcript.py`, then commit `WRITEUP.md` as `Name and date`.
10. Start the viewer yourself, in the background: `python3 evals/app/serve.py --open`. Ask
    whether it opened in their browser. If port 8440 is taken, it may already be running; say
    so.
11. Step D: ask how the MCP server connects Claude Code on their laptop to the notebook on
    Google's computer, now that they have seen it work. Don't explain it first; the answer is
    theirs. Write it under "Step D: How the MCP server connects Claude Code to Colab" the same
    way.
12. Run the `checkpoint` skill for Part 0. It commits `Part 0 done`, pushes on a yes and
    reminds them about the submission form. Mention that Part 0 is due 8:00am Thursday,
    October 8.

Then say that Part 1 Step 1, reading about the model, is next.
