// Example grader: programmatic checks first, then a yes/no-claim LLM judge on a different model.
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const JUDGE_MODEL = process.env.JUDGE_MODEL ?? "claude-bridge/claude-opus-5-5:medium"; // must differ from the tested model
const PREAMBLE = [/^\s*(sorry|apologies|i apologize|good question|sure|of course)/i];

export async function grade(c, output) {
  if (PREAMBLE.some((re) => re.test(output))) return { score: 0, hardFail: "preamble", claims: [] };
  if (!c.claims?.length) return { score: 1, claims: [] };
  const rubric = readFileSync(join(here, "judge.md"), "utf8");
  const prompt = `${rubric}\n\n## Case input\n${c.input}\n\n## Context\n${c.judgeContext ?? ""}\n\n## Output to grade\n${output}\n\n` +
    `## Claims\n${c.claims.map((q, i) => `${i + 1}. ${q}`).join("\n")}\n\n` +
    `Answer each claim yes or no with short evidence quoted from the output. Reply with JSON only: {"claims":[{"id":1,"met":true,"evidence":"..."}]}`;
  const r = spawnSync("pi", ["-p", "--no-session", "--no-tools", "--no-skills", "--no-context-files", "--no-extensions",
    "--model", JUDGE_MODEL, "--", prompt], { encoding: "utf8", stdio: ["ignore", "pipe", "pipe"], timeout: 180000, maxBuffer: 16 << 20 });
  const json = (r.stdout ?? "").match(/\{[\s\S]*\}/);
  if (!json) return { score: null, error: "judge returned no JSON", raw: r.stdout };
  const claims = JSON.parse(json[0]).claims;
  return { score: claims.filter((x) => x.met).length / c.claims.length, claims, judge: JUDGE_MODEL };
}
