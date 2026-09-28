#!/usr/bin/env python3
"""wait-what スキルの評価ハーネス。使い方は README.md を参照。"""
import argparse
import glob
import json
import os
import re
import statistics
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
CASES = os.path.join(DATA, 'cases.json')
SESSIONS = os.path.expanduser('~/.pi/agent/sessions')

GEN_MODEL = 'openai-codex/gpt-6-sol'
GEN_THINKING = 'medium'
JUDGE_MODEL = 'claude-sonnet-5'
SCORES = ['plain', 'no_bad_analogy', 'background', 'targets_question', 'actionable']
APOLOGY = re.compile(r'ごめん|すみません|申し訳|失礼しました|お詫び|sorry|apologi', re.I)

RUBRIC = """あなたは説明の質を採点する審査員です。
ユーザーはエージェントの直前の説明が理解できず「分からない、説明し直して」と頼みました。その言い直しを採点してください。
ユーザーはソフトウェア開発者ですが、その話題の専門用語や内部事情は知らない前提です。

各項目 1-5 で採点:
- plain: 子供にも分かるほど平易か。未説明の専門用語・内部用語・略語が残っていないか (残っていれば大きく減点)
- no_bad_analogy: 不正確・曖昧な例え/比喩がないか (例えなしなら 5)
- background: ユーザーが欠いていた背景を補っているか
- targets_question: ユーザーが付け加えた具体的な質問・つまずき箇所に、真正面から答えているか (付け加えがなければ、直前の説明の要点に答えているか)
- actionable: ユーザーが次に何をすべきか/何を判断すべきかが明確か (該当しなければ 5)
最後に understood: このユーザーがこれを読んで「わかった」と先に進める可能性が高いなら true、また聞き返しそうなら false。
回答は日本語であること (英語の回答は plain=1)。

JSON のみ出力: {"plain":n,"no_bad_analogy":n,"background":n,"targets_question":n,"actionable":n,"understood":bool,"reason":"一文"}"""


def case_id(ts):
    return re.sub(r'[-:]|\.\d+', '', ts)  # 2000-01-02T03:04:05.678Z -> 20000102T030405Z


def text(message):
    content = message.get('content')
    if isinstance(content, str):
        return content
    return '\n'.join(p.get('text', '') for p in content or [] if isinstance(p, dict) and p.get('type') == 'text')


def assistant_text(msgs, start, end):
    return '\n\n'.join(text(m['message']) for m in msgs[start:end]
                       if m['message'].get('role') == 'assistant' and text(m['message']).strip())


def extract(_args):
    """pi の全セッションから /skill:wait-what の呼び出しを抜き出して data/cases.json に保存する。"""
    cases = {}
    for path in sorted(glob.glob(f'{SESSIONS}/**/*.jsonl', recursive=True)):
        if '/forks/' in path:
            continue
        try:
            entries = [json.loads(line) for line in open(path, encoding='utf-8') if line.strip()]
        except (OSError, json.JSONDecodeError):
            continue
        cwd = next((e.get('cwd') for e in entries if e.get('type') == 'session'), None)
        msgs = [e for e in entries if e.get('type') == 'message' and isinstance(e.get('message'), dict)]
        users = [i for i, m in enumerate(msgs) if m['message'].get('role') == 'user']
        for k, i in enumerate(users):
            body = text(msgs[i]['message'])
            if not body.lstrip().startswith('<skill name="wait-what"'):
                continue
            cid = case_id(msgs[i]['timestamp'])
            if cid in cases:  # コピーされたセッションに同じ呼び出しが重複して残っている
                continue
            prev_user = users[k - 1] if k > 0 else None
            next_user = users[k + 1] if k + 1 < len(users) else len(msgs)
            cases[cid] = dict(
                id=cid, ts=msgs[i]['timestamp'], cwd=cwd, file=path,
                skill_body=body.split('</skill>')[0],
                extra=body.split('</skill>', 1)[1].strip() if '</skill>' in body else '',
                prev_user=text(msgs[prev_user]['message'])[-2000:] if prev_user is not None else '',
                prev=assistant_text(msgs, (prev_user or 0) + 1, i),
                response=assistant_text(msgs, i + 1, next_user),
                after_user=text(msgs[next_user]['message'])[:600] if next_user < len(msgs) else '')
    os.makedirs(DATA, exist_ok=True)
    json.dump(sorted(cases.values(), key=lambda c: c['ts']), open(CASES, 'w'), ensure_ascii=False, indent=1)
    print(f'{len(cases)} cases -> {CASES}')


def load_cases():
    return {c['id']: c for c in json.load(open(CASES))}


def eval_set(name):
    return json.load(open(os.path.join(DATA, f'{name}.json')))


def replay_one(case, variant, sample):
    """呼び出し直前で切ったセッションを pi で再開し、候補版スキルで言い直しを生成する。"""
    out_dir = os.path.join(DATA, 'runs', variant, sample, case['id'])
    out = os.path.join(out_dir, 'out.md')
    if os.path.exists(out) and os.path.getsize(out):
        return
    os.makedirs(out_dir, exist_ok=True)
    lines = open(case['file'], encoding='utf-8').read().splitlines()
    cut = next(i for i, line in enumerate(lines)
               if (e := json.loads(line)).get('type') == 'message' and e.get('timestamp') == case['ts'])
    session = os.path.join(out_dir, 'session.jsonl')
    open(session, 'w').write('\n'.join(lines[:cut]) + '\n')
    skill_dir = os.path.join(HERE, 'variants', variant)
    # stdin を閉じないと pi -p が入力待ちで止まる。ツールは読み取り専用、extension は副作用を避けるため無効
    r = subprocess.run(['pi', '--session', session, '-p', '-a', '-ne', '-ns', '--skill', skill_dir,
                        '-t', 'read,grep,find,ls', '--model', GEN_MODEL, '--thinking', GEN_THINKING,
                        ('/skill:wait-what ' + case['extra']).strip()],
                       cwd=case['cwd'], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=300)
    open(os.path.join(out_dir, 'err.txt'), 'w').write(r.stderr)
    open(out, 'w').write(r.stdout)


def response_of(case, variant, sample):
    if variant == 'original':
        return case['response']
    return open(os.path.join(DATA, 'runs', variant, sample, case['id'], 'out.md')).read()


def judge_one(case, variant, sample, trial):
    out = os.path.join(DATA, 'judge', variant, sample, f"{case['id']}.{trial}.json")
    if os.path.exists(out) and 'error' not in json.load(open(out)):
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    context_md = os.path.join(case['cwd'] or '', 'CONTEXT.md')
    glossary = open(context_md).read()[:5000] if os.path.exists(context_md) else '(なし)'
    prompt = f"""{RUBRIC}

## プロジェクトの CONTEXT.md (抜粋。用語集)
{glossary}

## それ以前のユーザー発言
{case['prev_user'][-1500:]}

## エージェントの直前の説明 (伝わらなかったもの)
{case['prev'][-4000:]}

## ユーザーの依頼
説明し直して。{case['extra']}

## 採点対象の言い直し
{response_of(case, variant, sample)[:6000]}
"""
    r = subprocess.run(['claude', '-p', '--model', JUDGE_MODEL, '--output-format', 'text'],
                       input=prompt, capture_output=True, text=True, timeout=300, cwd='/tmp')
    m = re.search(r'\{.*\}', r.stdout, re.S)
    open(out, 'w').write(m.group(0) if m else json.dumps({'error': (r.stdout + r.stderr)[-1000:]}))


def run(args):
    cases = load_cases()
    ids = [c['id'] for c in eval_set(args.set)]
    with ThreadPoolExecutor(args.jobs) as pool:
        if args.variant != 'original':
            list(pool.map(lambda i: replay_one(cases[i], args.variant, args.sample), ids))
        list(pool.map(lambda job: judge_one(cases[job[0]], args.variant, args.sample, job[1]),
                      [(i, t) for i in ids for t in range(args.trials)]))
    report(argparse.Namespace(set=args.set, targets=[f'{args.variant}/{args.sample}'], verbose=False))


def report(args):
    cases = load_cases()
    labeled = eval_set(args.set)
    for target in args.targets:
        variant, _, sample = target.partition('/')
        sample = sample or 's0'
        print(f'== {variant}/{sample}')
        rows, flips, agree, apologies = [], 0, 0, 0
        for item in labeled:
            case = cases[item['id']]
            trials = [json.load(open(p)) for p in
                      sorted(glob.glob(os.path.join(DATA, 'judge', variant, sample, f"{item['id']}.*.json")))]
            trials = [t for t in trials if 'error' not in t]
            if not trials:
                print(f"  {item['id']} (未採点)")
                continue
            understood = statistics.mean(t['understood'] for t in trials)
            flips += len({t['understood'] for t in trials}) > 1
            agree += (item['label'] == 'pass') == (understood >= 0.5)
            apologized = bool(APOLOGY.search(response_of(case, variant, sample)[:300]))
            apologies += apologized
            row = {k: statistics.mean(t[k] for t in trials) for k in SCORES}
            rows.append(row | {'understood': understood})
            if args.verbose:
                print(f"  {item['id']} {item['label']:4} und={understood:.1f} "
                      + ' '.join(f'{k[:6]}={row[k]:.1f}' for k in SCORES) + (' 謝罪' if apologized else ''))
        n = len(rows)
        print(f"  n={n} understood={statistics.mean(r['understood'] for r in rows):.2f} "
              f"flips={flips}/{n} apology={apologies}/{n}"
              + (f' judge-vs-user={agree}/{n}' if variant == 'original' else ''))
        print('  ' + ' '.join(f'{k}={statistics.mean(r[k] for r in rows):.2f}' for k in SCORES))


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(required=True)
    sub.add_parser('extract').set_defaults(func=extract)
    r = sub.add_parser('run', help='候補版で再生成して採点する (original は履歴の応答をそのまま採点)')
    r.add_argument('variant')
    r.add_argument('--sample', default='s0', help='同じ候補版を再度生成するときは s1, s2 ... を指定')
    r.add_argument('--set', default='eval-set')
    r.add_argument('--trials', type=int, default=2)
    r.add_argument('--jobs', type=int, default=5)
    r.set_defaults(func=run)
    rp = sub.add_parser('report')
    rp.add_argument('targets', nargs='+', help='variant または variant/sample')
    rp.add_argument('--set', default='eval-set')
    rp.add_argument('-v', '--verbose', action='store_true')
    rp.set_defaults(func=report)
    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    sys.exit(main())
