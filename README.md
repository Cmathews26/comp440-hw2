# COMP 440 HW2: Whose Preferences Count?

In this homework assignment you will train a small language model, steer it two different ways,
and measure what changed. You will:

- Understand how a language model is trained, from a "base" model, to training on example
  answers (SFT), to training on preferences between two answers (DPO).
- Understand the places where people shape the model: the *constitution* (the written rules the
  model is trained to follow), the example answers, the tests and the grader.
- Experiment with training the same model two ways and measuring the difference.
- Understand how language models are evaluated: sets of test questions, a written rubric for
  grading the answers, and an AI grader that applies it. You'll grade answers yourself and find
  where the AI grader disagrees with you.

Both of your models start from the same model. One is trained to be succinct and factual. The
other is trained to sound like a famous person you admire. Both follow the same basic rules: be
truthful, do the task, correct false claims. You'll write each model's rules down in a
constitution, and Claude will turn them into training data.

## The three parts

The assignment starts with a short setup, Part 0, and then has two main parts. Part 2 builds on
Part 1, so keep your repository from one part to the next.

| Part | What you do | Due | Weight |
|---|---|---|---|
| [Part 0: Set up](instructions/part0.md) | Get the repository, start Claude Code, learn what MCP is, connect Colab and start the evaluation viewer | 8:00am Thursday, October 8 | 20% |
| [Part 1: The tools and the tests](instructions/part1.md) | Learn how the pieces connect, how the model and the tests are built, and run the tests on the base model | 8:00am Thursday, October 15 | 30% |
| [Part 2: Train your two models and decide what changed](instructions/part2.md) | Write your two constitutions and two test families, predict the results, train on example answers (SFT) and on preferences (DPO), then grade, audit and write up whose preferences count | 8:00am Tuesday, October 27 | 50% |

## Working independently

You must do this assignment on your own. Partners aren't allowed. You are welcome to help classmates get set up. Ask questions in `#comp440-f26` on Slack. Follow
the class's [AI-use norms](https://docs.google.com/document/d/1Eb6qxeS2wy-9TBzL8eGk9j75iYu_yEV4VoLQaywRz8E/edit).

## Resources

- [Qwen3-0.6B-Base on Hugging Face](https://huggingface.co/Qwen/Qwen3-0.6B-Base): the model you
  will train.
- [What is MCP?](https://www.youtube.com/watch?v=eur8dUO9mvE) (IBM Technology, 3:46): how Claude
  Code connects to tools like Colab.
- [`evals/shared/README.md`](evals/shared/README.md): the three shared test sets and how each is
  graded.
- [`evals/app/README.md`](evals/app/README.md): the evaluation viewer.

## Troubleshooting

- **Claude says it has no Colab tools.** You probably skipped the approval in [Part 0](instructions/part0.md). Type
  `/exit`, run `claude mcp reset-project-choices` in the repository's folder, start `claude`
  again and approve the server this time. Then type `/resume`, pick your conversation and type
  `back`.
- **Claude says the notebook has no live Colab connection.** This usually means your laptop went
  to sleep. Ask Claude to reopen the notebook. A reconnect starts a new runtime on a CPU, so
  choose the T4 again and have Claude rerun the setup cells. Keep your laptop awake and plugged in
  while you train.
- **A download doesn't arrive.** Chrome asks for permission the first time a notebook downloads
  a file. Look for the prompt near the address bar and allow it.
- **"CUDA out of memory".** Restart the kernel in the Colab notebook and try again.

## What to submit

Submit your repository URL through the
[assignment submission form](https://forms.gle/mgKcnqzTGxNaGvteA) at the end of each part (0, 1, and 2).
Each one's instructions list what it must contain.

Your repository also holds `TRANSCRIPT.md`, a record of your Claude Code sessions: what you typed
and what Claude said, with its tool use cut to one line each. Claude updates it with
`dump_transcript.py` before every commit. Do your assignment sessions in this repository's
folder, since sessions run anywhere else aren't captured. Never edit it by hand, because it is
regenerated each time.

## FAQ

FAQ: TBA (ask a question on `#comp440-f26`!)
