---
name: setup
description: First-time setup for HW2 (Step 0), run once after cloning the fork. Adds the upstream remote, checks the Colab MCP server, connects a Colab notebook with a T4 GPU, records the student's name and date, starts the evaluation viewer, collects the MCP paragraph, and commits. Run it again if anything breaks or to check a setup is complete.
---

Do these in order and show what you ran. Skip what is already done. If something fails, say in
plain words what it means and what to change, and stop there.

1. Add the `upstream` remote if it is missing:
   `git remote add upstream "${HW2_UPSTREAM:-https://github.com/shilad/comp440-hw2}"`
   It is there in case a fix to the template has to go out mid-assignment.
2. Check that the Colab tools from the `cool-colab-mcp` server are available, for example
   `open_colab_browser_connection`. If they are not, the server was declined or failed to start.
   Ask them to type `/mcp`. If it is missing or declined: run `claude mcp reset-project-choices`
   in this folder, restart Claude Code, approve the server, and run `/setup` again. If it
   failed: check that `uv --version` works and that
   `uv sync --frozen --directory tools/cool-colab-mcp` succeeds, then restart Claude Code. Stop
   here until the tools are there.
3. Load the HW2 notebook into Colab. Call `register_notebook` with `notebook_id` "hw2", `name`
   "COMP 440 HW2" and `local_path` set to the absolute path of `colab/hw2.ipynb` in this
   repository. Then call `open_notebook` with "hw2". Tell them a Colab tab is opening with the
   HW2 notebook in it, and that they should approve the connection there. Wait until it reports
   a connection. If registering fails because local notebooks are disabled, Claude Code was
   started before `.mcp.json` allowed the `colab/` folder: ask them to restart Claude Code.
4. Ask them to choose Runtime > Change runtime type > T4 GPU in Colab's menu, not the TPU, and to
   say when it is done. Then call `get_runtime_status` with `notebook_id` "hw2". If it does not
   report a T4 GPU, say what it reports and ask again. Tell them the small cell it added at the
   top of the notebook comes from the server and is harmless.
5. Ask their name, and fill `**Name:**` and `**Date:**` at the top of `WRITEUP.md`. Those two
   are yours to compose; every other slot in that file you fill from their own words.
6. Run `python3 dump_transcript.py`, then commit `WRITEUP.md` as `Name and date`.
7. Start the viewer yourself, in the background: `python3 evals/app/serve.py --open`. Ask
   whether it opened in their browser. If port 8440 is taken, it may already be running; say so.
8. Step D: ask whether they have watched the "What is MCP?" video, then ask for their paragraph:
   what an MCP server is, why it is useful, and at least one example other than Colab. Write it
   into the Step 0 slot of `WRITEUP.md` following the writing rules in `CLAUDE.md`, show it, and
   ask whether it says what they meant.
9. Run `python3 dump_transcript.py`, commit as `Step 0 done`, ask whether to push, and on a yes
   run `git push`.

Then remind them to submit their repository URL through the form
(https://forms.gle/mgKcnqzTGxNaGvteA) by 8:00am Thursday, October 8, and say that Part 1 Step 1,
reading about the model, is next.
