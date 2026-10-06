# COMP 440 HW2 Step 0: Set up

Due **8:00am Thursday, October 8**. The overall instructions are in the [README](../README.md).

This should take about 30 minutes. You do it once, before Part 1. I've made it due early so
that any setup problems come up while there is still plenty of time to fix them.

You'll train your models on a free GPU in Google Colab, and Claude Code will run the Colab
notebook for you. The two talk through an *MCP server*: a small program on your laptop that
gives Claude Code extra tools, here the tools to open, edit and run a Colab notebook.

Before you start, watch [What is MCP?](https://www.youtube.com/watch?v=eur8dUO9mvE) (IBM
Technology, 3:46). It explains what you are about to set up, and you'll write about it in
Step D.

Remember **you can always ask Claude itself for help if you get stuck on this setup.**

You need:

- Claude Code, the same as in HW1.
- `uv`, a Python package manager. If `uv --version` doesn't work, follow the
  [installation instructions](https://docs.astral.sh/uv/getting-started/installation/).
- A Google account that can use [Colab](https://colab.research.google.com/), and Chrome. Your Macalester account should work fine.

**Step A: Get the repository**

- fork and clone Shilad's [hw2 repo](https://github.com/shilad/comp440-hw2).
- Install the MCP server's packages, from the repository's folder:

```
uv sync --frozen --directory tools/cool-colab-mcp
```

- Hint: `--frozen` tells `uv` to install exactly the versions listed in the repository. Use it
  every time.

**Step B: Start Claude Code and approve the MCP server.** This is the step that is easiest to
miss.

- Start Claude Code in the repository's folder.
- Claude Code will ask whether to use the `cool-colab-mcp` server listed in `.mcp.json`. **Approve
  it.**
- Warning: if you skip or decline this prompt, Claude has no Colab tools. To fix it, run `claude mcp reset-project-choices` in the repository's folder, restart Claude Code and approve the server this time.

**Step C: Let Claude do the rest**

From here, Claude drives. The first time you open Claude Code in the repository, it starts the
setup by itself. If it doesn't, type `/setup`. You answer its questions and click a few things in
your browser. The details below are for reference, so you know what to expect and can check that
each part worked.

1. Claude checks that it has the Colab tools. (You can check too: type `/mcp`, and
   `cool-colab-mcp` should say "connected".)
2. It opens the HW2 notebook in Colab. The notebook is `colab/hw2.ipynb` in your repository, and
   Claude loads it into a Colab tab in your browser. Approve the connection when it asks.
3. It asks you to choose **Runtime > Change runtime type > T4 GPU** in Colab's menu. Don't pick
   the TPU, even though it sounds faster. The training code in this assignment is built for
   NVIDIA GPUs like the T4.
4. It checks that the notebook has a T4 GPU. It adds a small cell to the top of your notebook to
   do this. That cell comes from the MCP server, not from you, and it's harmless.
5. It asks your name, writes it at the top of `WRITEUP.md` and commits.
6. It starts the evaluation viewer (`python3 evals/app/serve.py --open`), which opens in your
   browser. It shows no runs yet; your first one comes in Part 1. If the viewer isn't running
   later, ask Claude to start it again.
7. It asks for your paragraph about MCP (Step D), then commits and offers to push.

**Step D: Explain MCP in your own words**

- Think back to the [What is MCP?](https://www.youtube.com/watch?v=eur8dUO9mvE) video you
  watched before setup. Rewatch it if you need to; it's under four minutes.
- In `WRITEUP.md`, under Step 0, write a short paragraph in your own words, not the video's: what
  is an MCP server, and why is it useful?
- Give **at least one** example of something an MCP server could connect an AI assistant to,
  other than Colab.
- Hint: Claude asks for this at the end of setup. Tell it your answer and it writes it into
  `WRITEUP.md`. It may tidy your wording, but the ideas must be yours.

Questions: Now that you've set one up, how does the MCP server connect Claude Code on your
laptop to a notebook running on Google's computer? Why not just paste code into Colab
yourself?

## What to submit

- Your name and date, and your MCP paragraph from Step D, in `WRITEUP.md`. Claude commits
  them; ask Claude to push.
- Submit your repository URL through the
  [assignment submission form](https://forms.gle/mgKcnqzTGxNaGvteA) by **8:00am Thursday,
  October 8**.

If anything in setup doesn't work, see Troubleshooting in the [README](../README.md#troubleshooting)
or ask in `#comp440-f26`.
