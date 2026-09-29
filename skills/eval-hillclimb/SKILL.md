---
name: eval-hillclimb
description: Build a trustworthy eval for an LLM app, prompt, or pi skill inside the current repo, then hillclimb against it one change at a time with a held-out test split. Invoke explicitly with an argument, build-eval or hillclimb. Use when the user asks to create an eval, measure a prompt or skill, or improve one against an eval without overfitting.
disable-model-invocation: true
---

# eval-hillclimb

Provider-neutral eval design and hillclimbing. Two modes, chosen by the argument:

- `build-eval` builds `evals/<name>/` in the target repo and runs a validated baseline.
- `hillclimb` improves a chosen surface against an existing `evals/<name>/`.

If no argument is given, ask which mode. If `hillclimb` is requested and no eval exists, offer `build-eval` first.

## Ground rules (both modes)

- Every model call costs money and time. State the run size (cases x repeats x configs, rough duration) and get approval before any run.
- Pause for the user at the checkpoints marked **STOP**. Do not continue past one on your own.
- Keep secrets, customer data, and personal data out of committed fixtures. Ask about retention and sensitivity before reading production transcripts.
- The judge model must differ from the model under test. Record exact model IDs, thinking levels, and skill or prompt versions in every results file.
- Evals must exercise real pi behavior. The runner shells out to pi non-interactively, for example:
  `pi -p --no-session --model <provider/id[:thinking]> --skill <path> -- "<input>"`
  Useful isolation flags: `--no-skills` (then add only `--skill` paths under test), `--no-context-files`, `--no-extensions`, `--tools <allowlist>`, `--no-tools` for judges, `--session-dir <tmp>` for multi-turn cases.
- Run each trial in a fresh temp copy of the fixture workspace. Leftover files, git history, or sessions from earlier trials must not leak answers.

## Artifacts in `evals/<name>/`

```
cases.jsonl        one case per line: id, input, setup (optional), expect or claims, why_hard
split.json         {"train": [...ids], "test": [...ids], "seed": N}   (hillclimb only)
grader.mjs         programmatic grader, or judge prompt builder plus parser (start from templates/grader.example.mjs)
judge.md           rubric of yes/no claims (LLM judge only)
run.mjs            runner, copy from templates/runner.mjs and adapt
results/<run-id>.jsonl      one line per trial: case, repeat, config, output, grade, tokens, ms, error
results/<run-id>/<case>-<r>.txt   full transcript per trial
report.md / report.html     score with 95% interval, per-case table linking transcripts
```

Report pages are static files that open locally and load nothing from the network.

---

## Mode: build-eval

### 1. Interview

Ask briefly:
- What is under test (app path, prompt, skill, tool description) and how users hit it in real use.
- What a good output looks like, and what a bad one looks like.
- Which models or configs matter (tested model, candidate cheaper models, judge model).
- What sources of real examples exist.

### 2. Source cases, in this order

1. Real transcripts or production traffic (after the retention and sensitivity check).
2. Bug reports, tickets, and complaints.
3. 5 to 10 cases the user writes by hand.
4. Cases synthesized from the codebase, anchored on the real ones above.

Rules:
- Cases must mirror the real task distribution, not what is easy to generate or grade. User traffic can skew easy, so add deliberately hard cases.
- Do not pick cases because today's model fails them. That measures one model's weak spots. For every hard case, write `why_hard` in plain words; if you cannot say why it is hard, drop it.
- Everything the grader checks must be stated or implied by the case input. Two domain experts should reach the same verdict.
- Aim for 20 to 60 cases to start. Fewer makes the noise too large to see anything.

Write a simple review page (`evals/<name>/inputs.md` or `.html`) listing every input with its source and `why_hard`.
**STOP**: the user confirms, edits, or removes cases before you go on.

### 3. Pick the cheapest grader that fits

- **Programmatic first** when outputs are constrained: exact match, label from a fixed set, JSON schema, tests passing, file exists, command ran.
- **LLM judge** only for open-ended output. The rubric is a list of yes/no claims that can each be checked against the output, never a 1-to-5 scale. Score = fraction of claims met, and name any claim that is a hard fail. The judge returns JSON: `{"claims":[{"id":..., "met":true|false, "evidence":"..."}]}`.
- **Pairwise blind** when comparing to a baseline: the judge sees both outputs in random order, labeled A and B, is not told which is the baseline, and picks the better one or a tie. Record the position to check for position bias.
- The judge runs with no tools, no skills, no context files, and a model different from the tested one.

### 4. Validate the grader

- Grade 5 to 10 real outputs, show each verdict with its evidence, and ask: "Would you have scored any of these differently?" **STOP** until the user agrees. Fix the rubric or the cases, not the verdicts.
- Grade the same output twice for several cases. If a verdict flips, tighten the claim wording or make it programmatic.
- Include at least one known-good and one known-bad output. The good one must pass and the bad one must fail.

### 5. Baseline run

State the size (cases x repeats x configs, rough time), get approval, run with at least 3 repeats. Then check:

- **Plumbing**: count timeouts, API errors, empty or cut-off outputs, and nonzero exits. Report them separately; never let infra failures count as model failures. Rerun those trials.
- **Noise**: report the score with a 95% interval (bootstrap over cases, repeats averaged per case) and the run-to-run spread. Estimate the smallest difference the eval can detect (about 2 x the standard error of a difference). If that is larger than improvements the user would care about, say so and suggest more cases or repeats.
- **Always-fail cases**: a case failing every repeat is a sign of ambiguity or a broken grader. Read its transcripts and flag it.
- **Headroom**: if the baseline is about 95% or higher, warn that quality has no room to move. Suggest aiming the hillclimb at cost, latency, or output length at equal quality, or adding harder cases.
- Optional sanity check: a stronger model or higher thinking level should score higher. If it does not, suspect the cases or grader.

Write `report.md` (and `report.html` if asked): score, interval, noise estimate, plumbing counts, flagged cases, per-case table linking transcripts.

---

## Mode: hillclimb

### 1. Setup

Ask:
- **Goal**: better quality, or lower cost, latency, or length while quality holds. Also the smallest gain worth acting on.
- **Editable surface**: which of system prompt, skill files, instruction files (AGENTS.md etc.), tool descriptions, model and thinking level, harness code. Everything else is read-only.
- **Budget**: maximum rounds and spend.

Then:
- Split cases at random into train (about 2/3) and test (about 1/3) with a recorded seed, stratified by source if possible. Save `split.json`.
- Confirm the eval noise is smaller than the smallest gain the user cares about. If not, stop and recommend more repeats or cases first.
- Run or reuse the baseline on both splits under the current config. Commit or stash so every round can be reverted cleanly (one git commit per kept change).

### 2. Isolation rules

- **The test set is never read.** Do not open test cases, test outputs, or test transcripts. Only aggregate test scores are seen.
- **Never paste failure content into the prompt or skill.** No copied case inputs, expected answers, or transcript snippets. Fix the general behavior instead.
- **Keep answers out of reach.** The model under test must not be able to read `evals/`, expected outputs, or grader code during a trial. Run from a temp workspace that does not contain them, and restrict tools if needed.

### 3. One round

1. Read the previous round's **train** transcripts and grades only.
2. Find the most common failure and its root cause.
3. Propose **one** change as a patch that fixes the cause (rewrite the section that causes it, add the missing rule, change the parameter). Avoid cosmetic rewording whose effect cannot rise above noise.
4. Show the patch and apply it.
5. Run train and test with the same repeats as the baseline.
6. Decide:
   - Train and test both improve beyond noise: **keep** (commit).
   - Train improves but test is flat: suspected overfitting, **revert**.
   - Either split regresses beyond noise: **revert**.
   - Both flat: revert, and count the round as stalled.
7. Log the round in `evals/<name>/hillclimb.md`: hypothesis, patch summary, train and test scores with intervals, decision.

For a cost goal, try levers in this order: remove redundant or contradictory instructions and mandatory rituals, trim input size, lower thinking level, try a cheaper model. Each lever is its own round and must hold quality on both splits.

### 4. When progress stalls

After 2 to 3 stalled rounds, or earlier if no single fix could beat the noise, make no edit. Instead, read every remaining train failure and bucket it by cause:

- a real behavior gap (keep climbing on it),
- an ambiguous or impossible case,
- a grader that marks a correct answer wrong or a wrong answer right,
- harness or plumbing errors,
- run-to-run variance.

Flag broken cases and graders to the user. **STOP** for approval before editing any case or grader, and rerun the baseline after such edits because old scores are no longer comparable. Only real behavior gaps feed further rounds.

### 5. Final report

Leave the code at the config with the best **test** score for the goal. Report:
- baseline vs final on the test split, each with a 95% interval, plus cost, tokens, and latency if tracked,
- the list of kept changes and reverted ones with reasons,
- any cases or graders that were fixed.

If the gain is within noise, say so plainly and recommend not merging.

---

## Worked example: testing a user-invoked skill (`wait-what`)

`wait-what` asks the agent to re-explain the previous answer simply, fill in missing background, avoid inaccurate analogies, use `CONTEXT.md` terms only where needed (following `CONTEXT-MAP.md` if there are several), add no new terms, and skip apologies and preamble.

**Cases**: each case is a fixture repo (with `CONTEXT.md`, sometimes several plus `CONTEXT-MAP.md`) and a prior turn in which the agent gave a dense, jargon-heavy explanation. Source these from real sessions where you actually typed `/skill:wait-what`, then add hand-written ones.

**Runner**: multi-turn, so use a session. Turn 1 replays the prior explanation, turn 2 invokes the skill:
```
pi -p --session-dir "$tmp/s" --session-id case-07 --skill ~/.agents/skills/wait-what \
   --model openai-codex/gpt-6-sol:medium -- "<turn 1 question>"
pi -p --session-dir "$tmp/s" --session-id case-07 --skill ~/.agents/skills/wait-what \
   --model openai-codex/gpt-6-sol:medium -- "/skill:wait-what"
```
Grade only the turn 2 output. Judge with a different model (for example through claude-bridge), no tools.

**Programmatic checks** (cheap, run first): the first sentence contains no apology or preamble phrase from a fixed list; no term appears that is absent from both the prior turn and the relevant `CONTEXT.md`.

**Judge claims** (yes/no, each with evidence):
1. The reply starts directly with the explanation, with no apology, acknowledgement, or preamble.
2. The reply supplies at least one piece of background that the prior explanation assumed but never stated.
3. Every analogy in the reply is accurate for the point it illustrates; if there is no analogy, answer yes.
4. Every domain term used appears in the relevant `CONTEXT.md` (chosen via `CONTEXT-MAP.md` when there are several) and is explained in plain words where first used.
5. The reply introduces no new technical term that is neither in `CONTEXT.md` nor in the prior turn.
6. A reader without the background could restate the main point after reading only this reply.

**Hillclimb surface**: the body of `wait-what/SKILL.md` only. A typical stall bucket is claim 3 flipping between judge runs; fix that by making the claim stricter (for example "names what the analogy maps to"), not by editing the skill.
