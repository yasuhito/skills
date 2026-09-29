#!/usr/bin/env node
// Template runner for evals/<name>/. Adapt runCase() and grader.mjs to the target.
// Usage: node run.mjs --model openai-codex/gpt-6-sol:medium --repeats 3 [--split train|test|all] [--label baseline]
import { spawnSync } from "node:child_process";
import { mkdtempSync, cpSync, mkdirSync, readFileSync, writeFileSync, appendFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const args = Object.fromEntries(process.argv.slice(2).reduce((a, v, i, all) =>
  v.startsWith("--") ? [...a, [v.slice(2), all[i + 1]?.startsWith("--") ? true : all[i + 1] ?? true]] : a, []));
const model = args.model ?? "openai-codex/gpt-6-sol:medium";
const repeats = Number(args.repeats ?? 3);
const split = args.split ?? "all";
const label = args.label ?? "run";
const timeoutMs = Number(args.timeout ?? 300000);
const { grade } = await import(join(here, "grader.mjs"));

let cases = readFileSync(join(here, "cases.jsonl"), "utf8").split("\n").filter(Boolean).map(JSON.parse);
if (split !== "all") {
  const ids = new Set(JSON.parse(readFileSync(join(here, "split.json"), "utf8"))[split]);
  cases = cases.filter((c) => ids.has(c.id));
}

const runId = `${new Date().toISOString().replace(/[:.]/g, "-")}-${label}-${split}`;
const outDir = join(here, "results", runId);
mkdirSync(outDir, { recursive: true });
const resultsFile = join(here, "results", `${runId}.jsonl`);

export function pi(argv, cwd) {
  const t0 = Date.now();
  const r = spawnSync("pi", argv, { cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: timeoutMs, maxBuffer: 64 << 20 });
  return { out: r.stdout ?? "", err: r.stderr ?? "", code: r.status, timedOut: r.error?.code === "ETIMEDOUT", ms: Date.now() - t0 };
}

// One trial in a fresh workspace that does NOT contain evals/ (answers stay out of reach).
function runCase(c, r) {
  const ws = mkdtempSync(join(tmpdir(), `eval-${c.id}-`));
  if (c.setup?.fixture) cpSync(join(here, "fixtures", c.setup.fixture), ws, { recursive: true });
  const base = ["-p", "--model", model, "--session-dir", join(ws, ".sessions"), "--session-id", `${c.id}-${r}`];
  for (const s of c.setup?.skills ?? []) base.push("--skill", s);
  const turns = [...(c.setup?.priorTurns ?? []), c.input];
  let last;
  for (const t of turns) last = pi([...base, "--", t], ws);
  return last; // grade the final turn only
}

const rows = [];
for (const c of cases) for (let r = 0; r < repeats; r++) {
  const res = runCase(c, r);
  const plumbing = res.timedOut ? "timeout" : res.code !== 0 ? `exit ${res.code}` : !res.out.trim() ? "empty" : null;
  let g = plumbing ? null : await grade(c, res.out);
  const failed = plumbing ?? (g?.score == null ? "grader-error" : null);
  const row = { case: c.id, repeat: r, model, label, split, ms: res.ms, plumbing: failed, grade: g, output: res.out };
  rows.push(row);
  appendFileSync(resultsFile, JSON.stringify(row) + "\n");
  writeFileSync(join(outDir, `${c.id}-${r}.txt`), `${res.out}\n\n--- stderr ---\n${res.err}`);
  process.stderr.write(`${c.id}#${r} ${failed ?? g.score}\n`);
}

// Per-case mean over repeats, bootstrap 95% interval over cases. Plumbing failures excluded and counted.
const byCase = {};
for (const x of rows) if (!x.plumbing) (byCase[x.case] ??= []).push(x.grade.score);
const per = Object.entries(byCase).map(([id, s]) => [id, s.reduce((a, b) => a + b, 0) / s.length]);
const mean = (a) => a.reduce((s, v) => s + v, 0) / (a.length || 1);
const m = mean(per.map((p) => p[1]));
const boots = Array.from({ length: 2000 }, () => mean(per.map(() => per[Math.floor(Math.random() * per.length)][1]))).sort((a, b) => a - b);
const lo = boots[50], hi = boots[1949];
const plumbingCount = rows.filter((x) => x.plumbing).length;
const alwaysFail = per.filter((p) => p[1] === 0).map((p) => p[0]);

const md = [`# ${label} (${split}) ${runId}`, ``, `model: ${model}, repeats: ${repeats}, cases: ${per.length}`,
  `score: ${(m * 100).toFixed(1)}% (95% CI ${(lo * 100).toFixed(1)} to ${(hi * 100).toFixed(1)})`,
  `plumbing failures: ${plumbingCount} (rerun these)`, `always-fail cases: ${alwaysFail.join(", ") || "none"}`,
  m >= 0.95 ? `WARNING: near ceiling, aim at cost/latency/length or add harder cases.` : ``, ``,
  `| case | mean | transcripts |`, `|---|---|---|`,
  ...per.map(([id, s]) => `| ${id} | ${(s * 100).toFixed(0)}% | ${Array.from({ length: repeats }, (_, r) => `[${r}](results/${runId}/${id}-${r}.txt)`).join(" ")} |`)].join("\n");
writeFileSync(join(here, "report.md"), md + "\n");
console.log(md);
