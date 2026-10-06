---
name: checkpoint
description: The end-of-part ritual for HW2. Run it at the end of every part and before submission. Checks the part's run folder, names the part's blank writeup slots, confirms this session is in TRANSCRIPT.md, and commits and pushes the part once the student says yes.
---

Five steps, in this order, every time. Stop at the first thing missing: that is the current step,
and the part is not done until it is filled.

1. Check the part's run folder. For Part 1: a folder under `evals/runs/` with a response file for
   the base model and a Sonnet grades folder, and `status` in its `run.json` at `graded` or
   later. Show what you found in one line each. Do not say whether the numbers look right.
2. Read `WRITEUP.md` in full and name, one line each, every slot in **this part's section** that
   is still `XXXX`, and every answer that doesn't answer its step's question. Name them and ask
   what each needs; do not suggest wording.
3. Run `python3 dump_transcript.py` and paste its last line. It says how many sessions are in
   `TRANSCRIPT.md` and whether this session is one of them. If the script fails on their
   machine, say so and go on: it costs them nothing.
4. If they have not already said to commit this part, ask whether they are ready, and wait.
   Only on a yes, said now or earlier in the message that asked for this checkpoint, commit and
   push:

   ```
   git add -A
   git commit -m "Part N done"
   git push
   ```

   On anything other than a yes, say what is still open and stop.
5. Remind them to submit their repository URL through the form:
   https://forms.gle/mgKcnqzTGxNaGvteA. When they say they have, say **YOU ARE FINISHED WITH
   PART N!**

Nothing here is a judgment about the work. Presence and form only.
