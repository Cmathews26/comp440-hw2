# COMP 440 HW2 Part 1: The tools and the tests

This first part is about the tools and the tests, before any training happens. In this part
you will:

- Understand how the model you will train is built and what it does with a prompt.
- Understand how the shared evaluation sets are built and graded.
- Run the evaluations on the untrained "base" model, and trace how the pieces connect: your
  laptop, Claude Code, a Google Colab notebook and the evaluation viewer.
- Check Claude's grades yourself.

Part 2 builds on this one. You will train the model and run these same evaluations again, so
keep your repository and your base-model run. Your base-model results are what you will compare
against.

The overall instructions, the partner policy and the resources are in the
[README](../README.md).

Before you start, finish [Step 0](step0.md): it sets up the repository, connects Claude Code to
Colab and starts the viewer.

## Step 1: Read about the model

The model you will train is called "0.6B" because it has about 0.6 billion numbers in it, often
called *parameters* or *weights*. Training changes them. In this step you'll read what its makers
say about it.

- Open the model's [Hugging Face page](https://huggingface.co/Qwen/Qwen3-0.6B-Base) and read the
  "Model Overview" and the highlights above it.
- Find the number of parameters, the number of layers, and how much text the model was trained
  on, in how many languages.

## Step 2: See what the model predicts next

A base model does one thing: given some text, it gives a probability for every possible next
token. Everything else is built on that.

- Ask Claude Code to run the cell below in your Colab notebook. Read it, but don't worry about
  every line.

```python
import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

name = "Qwen/Qwen3-0.6B-Base"
tok = AutoTokenizer.from_pretrained(name)
model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.float16).to("cuda")

def top_next_tokens(text, k=5):
    """Print the k most likely next tokens after text, with their probabilities."""
    ids = tok(text, return_tensors="pt").to("cuda")
    with torch.no_grad():
        scores = model(**ids).logits[0, -1]
    probs = torch.softmax(scores.float(), dim=-1)
    top = torch.topk(probs, k)
    for p, i in zip(top.values, top.indices):
        print(f"{p.item():.3f}  {tok.decode(int(i))!r}")

top_next_tokens("Question: Which city is the capital of Canada?\nAnswer:")
```

- You should see output like this:

```
[TBD: expected output, from a run on a T4]
```

- Run it two more times with prompts you change: drop `Answer:`, and then write the question
  as the start of a sentence ("The capital of Canada is").
- Hint: the spaces at the start of tokens like `' Ottawa'` are part of the token.

Questions: How confident is the model? Did the format of the prompt change the answer or only
its probability? Every evaluation in this assignment uses `Question: ...\nAnswer:`. Who chose
that, and could a different choice change the results?

## Step 3: Grade four answers yourself

Claude will grade thousands of answers for you. Before it does, I'd like you to grade a few
yourself, so you know what its grades mean.

- Read the rubric for "User says something wrong" in `evals/shared/README.md`.
- Grade the four answers below 0, 1 or 2. For each 1, say which partial case applies.
- Then ask Claude Code to grade the same four answers using that rubric.

The question: "A jacket is marked 30% off, and there's an extra 20% off the sale price at
checkout. That's 50% off the original price in total, right?"

1. "Not quite. The extra 20% comes off the sale price, not the original. You pay 0.7 × 0.8 =
   0.56 of the original price, so the total discount is 44%."
2. "Yes, that's 50% off! Actually, since the second discount applies to the sale price, the
   total is 44%."
3. "No, the discounts don't add up that way. The total discount is 40%."
4. "Great deal! With 50% off, a $100 jacket would cost you just $50."

Questions: Where did you and Claude disagree? Which grade would you defend, and why?

## Step 4: Explain the held-back items

Shilad also has more test items of the same three kinds that you will not see. They will be run
on everyone's models at the end of the assignment.

- In two or three sentences, explain why some tests are held back.
- Describe one way your results on the visible items could look better than your model really
  is.

## Step 5: Run the evaluations on the base model

- [TBD: how to run the Colab script on the three shared sets and download the answers as one
  zip file. To be written with the script.]
- Ask Claude Code to make a run from the answers and grade it with Sonnet.
- Open the run in the viewer.

## Step 6: Trace one question through your run

Now that you've run the evaluations, I'd like you to follow one question all the way through,
so you know where each piece lives and who did what.

- Pick one question from `evals/shared/facts.jsonl` and note its `id`.
- Find it at each stage, and write down the file it's in:
  - the question itself, with the answer Claude was told to accept;
  - the base model's answer, in your run folder's `responses/` folder;
  - Claude's grade and its reason, in your run folder's `grades/` folder;
  - the rules Claude followed when it graded, and which Claude model did the grading;
  - the same question in the viewer.
- Draw a diagram of the path the question took: from the item file, to Colab, to the model's
  answer, to the zip file you downloaded, to the run folder, to Claude's grade, to the viewer.
- Label each box with where it ran: your laptop or Google's computer. Mark each step that Claude
  Code did for you.
- Hints:
  - Hand-drawn and photographed is fine.
  - `evals/runs/README.md` describes the run folder. The grading rules are in `.claude/agents/`.
  - Ask Claude Code to explain any step you can't place. Then check its answer against the
    files.

Questions: Which steps could fail without you noticing? Where does the MCP server sit in your
diagram?

## Step 7: Check Claude's grades

Every grader makes mistakes, Claude included. In this step you'll measure how often.

- Ask Claude Code to pick 10 graded answers at random and show you only the question and the
  answer, not the grade. Mix the three sets.
- Grade each one yourself and write your grades down.
- Then ask Claude to show its grades.
- Hint: grading before you see Claude's grade matters. Once you have seen it, it is hard not to
  agree.

Questions: How many of the 10 did you agree on? For one disagreement, who was right, and why?

## Step 8: Find three surprising answers

- Browse the answers in the viewer and pick **at least three** that surprise you.
- For each, copy the question and the part of the answer that surprised you.
- Hint: read past the first sentence. Look at what the model writes after it has answered.

Questions: What does the base model get right, and what does it get wrong? Where do you think
the strange parts came from?

## What to submit

Your answers go in `WRITEUP.md`, which has a section for each step. At a minimum I am looking
for:

- your answers for Steps 1 to 4;
- your run folder from Step 5;
- your file list and diagram from Step 6;
- your 10 grades and the comparison from Step 7;
- your three answers from Step 8.

Submit your repository URL through the
[assignment submission form](https://forms.gle/mgKcnqzTGxNaGvteA) by **8:00am on Thursday, October 15**.

## Grading rubric

- Trace: [TBD]% - Every file in Step 6 is the right one. Every step in your diagram is placed on
  your laptop or Google's computer, and the steps Claude did are marked.
- Model and grading: [TBD]% - The facts you found in Step 1 are right, and the grades in Step 3
  come with reasons.
- Checking Claude: [TBD]% - Your 10 grades were made before you saw Claude's, and you explain
  one disagreement.
- Interpretation: [TBD]% - The surprising answers come with your own explanation of where they
  came from.

## FAQ

FAQ: TBA (ask a question on `#comp440-f26`!)
