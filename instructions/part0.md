# COMP 440 HW2 Part 0: Set up

Due **8:00am Thursday, October 8**. The overall instructions are in the [README](../README.md).

This should take about 30 minutes. You do it once, before [Part 1](part1.md). I've made it due early so
that any setup problems come up while there is still plenty of time to fix them.

You'll train your models on a free GPU in Google Colab, and Claude Code will run the Colab
notebook for you. Claude Code talks to Colab through an *MCP server*: a small program on your
laptop that gives Claude Code tools to open, edit and run a Colab notebook.

Claude does most of the setup and tells you what to do as you go. If you get stuck, ask Claude
first. If Claude isn't running or can't fix it, see Troubleshooting in the
[README](../README.md#troubleshooting) or ask in `#comp440-f26`.

You need:

- Claude Code, the same as in HW1.
- A Google account that can use [Colab](https://colab.research.google.com/). Your Macalester
  account should work fine.
- Chrome, set as your default browser. Claude opens the Colab tab in your default browser.

Claude installs everything else, including `uv`, a Python package manager, if you don't have it.

## Step A: Get the repository and start Claude Code

- Fork Shilad's [hw2 repo](https://github.com/shilad/comp440-hw2), then clone your fork.
- In a terminal, go to the repository's folder and start Claude Code with `claude`.
- Claude Code will ask whether to use the `cool-colab-mcp` server. Choose **Use this MCP
  server**. Don't just press Enter: the question starts on "Continue without using this MCP
  server", which turns the server off.
- If you skip or decline it, Claude has no Colab tools. To fix it, type `/exit`, run
  `claude mcp reset-project-choices` in the repository's folder, start `claude` again and
  approve the server this time. Then type `/resume`, pick your conversation and type `back`.
- Type `/setup` to start the setup.
- Approve Claude's commands when it asks. It may ask several times.
- If Claude asks you to restart Claude Code, type `/exit` and start `claude` again the way it
  tells you. Then type `/resume`, pick your conversation and type `back`.

## Step B: Learn what MCP is

Once the MCP server is connected, Claude asks you to do this step.

- Watch [What is MCP?](https://www.youtube.com/watch?v=eur8dUO9mvE) (IBM Technology, 3:46). It
  explains what you just set up.
- Write a short paragraph in your own words: what is an MCP server, and why is
  it useful?
- Give **at least one** example of something an MCP server could connect an AI assistant to,
  other than Colab.
- Hint: tell Claude your answers and it writes them into `WRITEUP.md`. It may tidy your wording,
  but the ideas will be yours.

> [!IMPORTANT]
> **Question:** Why not just paste code into Colab yourself?

## Step C: Let Claude connect Colab

Claude does most of this step. You click a few things in your Chrome browser.

- When Claude says a Colab tab is opening, click Connect in Colab's dialog in Chrome within about a
  minute, since Claude only waits that long. If Chrome asks about devices on your local
  network, click Allow.
- Choose **Runtime > Change runtime type > T4 GPU** in Colab's menu. Don't pick the TPU, even
  though it sounds faster. The training code in this assignment is built for NVIDIA's T4 GPU.
- Claude then starts the evaluation viewer, a web page where you'll look at test results. It's
  empty until Part 1. If it isn't running later, ask Claude to restart it.

## Step D: Explain how it connects

> [!IMPORTANT]
> **Question:** Now that you've seen it work, how does the MCP server connect Claude Code on your
> laptop to a notebook running on Google's computer? Claude asks for your answer at the end of
> setup.

## What to submit

- Your name and date, and your answers from Steps B and D, in `WRITEUP.md`. Claude commits
  them. When it asks whether to push, say yes, since I can only see what's on GitHub.
- Submit your fork's URL through the
  [assignment submission form](https://forms.gle/mgKcnqzTGxNaGvteA) by **8:00am Thursday,
  October 8**.

Then go on to [Part 1](part1.md).
