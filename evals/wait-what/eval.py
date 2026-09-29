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

# 真偽そのものではなく、エージェントが見ていた資料 (会話、ツールの記録、CONTEXT.md) との整合を見る
ACCURACY_RUBRIC = """あなたは説明の正確さを検査する審査員です。
ユーザーはエージェントの直前の説明が理解できず、平易に言い直すよう頼みました。その言い直しが、平易にする過程で不正確になっていないかを検査してください。
平易さや分かりやすさは検査しないでください。正確さだけを見ます。

言い直しに含まれる事実の主張を、漏れなく一つずつ取り出し、資料 (エージェントが見ていた会話とツールの記録、CONTEXT.md) と照らし合わせて判定してください。
- supported: 資料で裏付けられる
- contradicted: 資料と矛盾する
- distorted: 単純化で意味が変わった (条件や例外の脱落、「必ず」「一切」「すべて」などの言い過ぎ、因果や順序や主語の取り違え)
- unsupported: 資料から確認できない新しい事実の断定
- general: 資料に依らない一般常識で、正しいもの
判断に迷うものは、資料を読み直してから決めてください。

quote には言い直しの本文から、その主張を含む部分を一字一句そのまま引用してください (要約しない)。

JSON のみ出力: {"claims":[{"quote":"原文の引用","verdict":"supported|contradicted|distorted|unsupported|general","note":"誤りの場合の説明"}]}"""

# (rubric, 保存先, 直前の説明を渡す長さ, モデル)。accuracy は直前の説明ではなく evidence() を渡す
JUDGES = {'quality': (RUBRIC, 'judge', 4000, JUDGE_MODEL),
          'accuracy': (ACCURACY_RUBRIC, 'judge-accuracy', None, 'claude-opus-5-5')}
ERROR_VERDICTS = {'contradicted', 'distorted', 'unsupported'}


def accuracy_errors(trial):
    return [c for c in trial.get('claims', []) if c.get('verdict') in ERROR_VERDICTS]

PLANT_PROMPT = """次の文章は、ある技術的な説明を平易に言い直したものです。
この文章に、事実の誤りを 1 つだけ紛れ込ませてください。誤りは、条件の脱落、言い過ぎ、因果の取り違え、主語の取り違え、数値や名前の入れ替えのどれかにしてください。
文体と長さは保ち、ほかの部分は一字も変えないでください。読んで違和感がないよう、自然に紛れ込ませてください。

JSON のみ出力: {"text":"誤りを入れた全文","error":"入れた誤りの説明"}

## 文章
"""


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


def claude_json(prompt, model=JUDGE_MODEL):
    r = subprocess.run(['claude', '-p', '--model', model, '--output-format', 'text'],
                       input=prompt, capture_output=True, text=True, timeout=300, cwd='/tmp')
    m = re.search(r'\{.*\}', r.stdout, re.S)
    try:
        return json.loads(m.group(0))
    except (AttributeError, json.JSONDecodeError):
        return {'error': (r.stdout + r.stderr)[-1000:]}


def render(entries, answer_turn=False):
    """セッションの項目を judge に渡す文字列にする。answer_turn では言い直しの本文を除きツールの記録だけ残す。"""
    parts = []
    for e in entries:
        if e.get('type') == 'compaction':
            parts.append(f"[それ以前の会話の要約]\n{e.get('summary', '')}")
        if e.get('type') != 'message' or not isinstance(e.get('message'), dict):
            continue
        m = e['message']
        role = m.get('role')
        content = m.get('content') if isinstance(m.get('content'), list) else [{'type': 'text', 'text': text(m)}]
        for part in content:
            kind = part.get('type')
            if role == 'user' and kind == 'text' and not answer_turn:
                parts.append(f"## ユーザー\n{part['text'][:3000]}")
            elif role == 'assistant' and kind == 'text' and part['text'].strip() and not answer_turn:
                parts.append(f"## エージェント\n{part['text']}")
            elif role == 'assistant' and kind == 'toolCall':
                parts.append(f"[ツール {part.get('name')}] {json.dumps(part.get('arguments'), ensure_ascii=False)[:300]}")
            elif role == 'toolResult' and kind == 'text':
                parts.append(f"[結果]\n{part['text'][:1500]}")
    return '\n\n'.join(parts)


def evidence(case, variant, sample, limit=60000):
    """エージェントが言い直しの時点で見ていた資料: それまでの会話と、言い直しの最中に使ったツールの記録。"""
    if variant == 'original':
        path = case['file']
    else:
        base = variant.removesuffix('-planted')  # 誤りを混ぜた版は、もとの版のセッションを根拠にする
        path = os.path.join(DATA, 'runs', base, sample, case['id'], 'session.jsonl')
    entries = [json.loads(line) for line in open(path, encoding='utf-8') if line.strip()]
    starts = [i for i, e in enumerate(entries) if e.get('type') == 'message'
              and e['message'].get('role') == 'user' and text(e['message']).lstrip().startswith('<skill name="wait-what"')]
    start = next(i for i in starts if variant != 'original' or entries[i].get('timestamp') == case['ts'])
    if variant != 'original':
        start = starts[-1]  # 再生成では pi が末尾に追記した呼び出し
    end = next((i for i in range(start + 1, len(entries)) if entries[i].get('type') == 'message'
                and entries[i]['message'].get('role') == 'user'), len(entries))
    before = render(entries[:start])[-limit:]
    during = render(entries[start + 1:end], answer_turn=True)
    return before + '\n\n# ここから言い直しの最中に使ったツール\n\n' + (during or '(なし)')


def judge_one(case, variant, sample, trial, judge):
    rubric, dirname, prev_limit, model = JUDGES[judge]
    out = os.path.join(DATA, dirname, variant, sample, f"{case['id']}.{trial}.json")
    if os.path.exists(out) and 'error' not in json.load(open(out)):
        return
    os.makedirs(os.path.dirname(out), exist_ok=True)
    context_md = os.path.join(case['cwd'] or '', 'CONTEXT.md')
    glossary = open(context_md).read()[:5000] if os.path.exists(context_md) else '(なし)'
    if judge == 'accuracy':
        prompt = f"""{rubric}

## プロジェクトの CONTEXT.md (抜粋。用語集)
{glossary}

## 資料: エージェントがこの時点までに見ていた会話とツールの記録
{evidence(case, variant, sample)}

## ユーザーの依頼
説明し直して。{case['extra']}

## 検査対象の言い直し
{response_of(case, variant, sample)[:6000]}
"""
        json.dump(claude_json(prompt, model), open(out, 'w'), ensure_ascii=False)
        return
    prompt = f"""{rubric}

## プロジェクトの CONTEXT.md (抜粋。用語集)
{glossary}

## それ以前のユーザー発言
{case['prev_user'][-1500:]}

## エージェントの直前の説明 (伝わらなかったもの)
{case['prev'][-prev_limit:]}

## ユーザーの依頼
説明し直して。{case['extra']}

## 採点対象の言い直し
{response_of(case, variant, sample)[:6000]}
"""
    json.dump(claude_json(prompt), open(out, 'w'), ensure_ascii=False)


def plant(args):
    """judge の診断用に、base の言い直しへ誤りを 1 つ混ぜた版を <base>-planted として作る。"""
    cases = load_cases()
    variant, _, sample = args.base.partition('/')
    sample = sample or 's0'

    def plant_one(cid):
        out_dir = os.path.join(DATA, 'runs', f'{variant}-planted', sample, cid)
        if os.path.exists(os.path.join(out_dir, 'out.md')):
            return
        planted = claude_json(PLANT_PROMPT + response_of(cases[cid], variant, sample))
        if 'text' not in planted:
            print(f'{cid}: 誤りを入れられなかった', file=sys.stderr)
            return
        os.makedirs(out_dir, exist_ok=True)
        open(os.path.join(out_dir, 'out.md'), 'w').write(planted['text'])
        json.dump(planted, open(os.path.join(out_dir, 'planted.json'), 'w'), ensure_ascii=False, indent=1)

    with ThreadPoolExecutor(args.jobs) as pool:
        list(pool.map(plant_one, [c['id'] for c in eval_set(args.set)]))
    print(f'-> {variant}-planted/{sample}。python3 eval.py run {variant}-planted --sample {sample} で採点する')


def run(args):
    cases = load_cases()
    ids = [c['id'] for c in eval_set(args.set)]
    with ThreadPoolExecutor(args.jobs) as pool:
        if args.variant != 'original':
            list(pool.map(lambda i: replay_one(cases[i], args.variant, args.sample), ids))
        list(pool.map(lambda job: judge_one(cases[job[0]], args.variant, args.sample, *job[1:]),
                      [(i, t, j) for i in ids for t in range(args.trials) for j in JUDGES]))
    report(argparse.Namespace(set=args.set, targets=[f'{args.variant}/{args.sample}'], verbose=False))


def overlaps(a, b, n=8):
    """a と b が n 文字以上の共通部分を持つか。"""
    return any(a[i:i + n] in b for i in range(max(len(a) - n + 1, 0)))


def diagnose(args):
    """正確さ judge の診断: plant で混ぜた誤りを、judge が誤りとして引用できたかを数える。"""
    import difflib
    variant, _, sample = args.base.partition('/')
    sample = sample or 's0'
    planted_variant = f'{variant}-planted'
    detected = total = 0
    for item in eval_set(args.set):
        cid = item['id']
        base = open(os.path.join(DATA, 'runs', variant, sample, cid, 'out.md')).read().splitlines()
        planted_dir = os.path.join(DATA, 'runs', planted_variant, sample, cid)
        planted = open(os.path.join(planted_dir, 'out.md')).read().splitlines()
        changed = [line for line in difflib.ndiff(base, planted) if line.startswith('+ ')]
        changed = [line[2:] for line in changed if line[2:].strip()]
        trials = load_trials('judge-accuracy', planted_variant, sample, cid)
        base_trials = load_trials('judge-accuracy', variant, sample, cid)
        if not changed or not trials:
            continue
        hits = [any(overlaps(c['quote'], line) or overlaps(line, c['quote'])
                    for c in accuracy_errors(t) for line in changed) for t in trials]
        total += len(hits)
        detected += sum(hits)
        base_errors = statistics.mean(len(accuracy_errors(t)) for t in base_trials) if base_trials else float('nan')
        planted_errors = statistics.mean(len(accuracy_errors(t)) for t in trials)
        what = json.load(open(os.path.join(planted_dir, 'planted.json')))['error']
        print(f"{cid} 検出 {sum(hits)}/{len(hits)} 誤り数 {base_errors:.1f} -> {planted_errors:.1f} | {what[:50]}")
    print(f'検出率 {detected}/{total}')


def load_trials(dirname, variant, sample, cid):
    trials = [json.load(open(p)) for p in
              sorted(glob.glob(os.path.join(DATA, dirname, variant, sample, f'{cid}.*.json')))]
    return [t for t in trials if 'error' not in t]


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
            trials = load_trials('judge', variant, sample, item['id'])
            accuracy = load_trials('judge-accuracy', variant, sample, item['id'])
            if not trials:
                print(f"  {item['id']} (未採点)")
                continue
            understood = statistics.mean(t['understood'] for t in trials)
            flips += len({t['understood'] for t in trials}) > 1
            agree += (item['label'] == 'pass') == (understood >= 0.5)
            apologized = bool(APOLOGY.search(response_of(case, variant, sample)[:300]))
            apologies += apologized
            row = {k: statistics.mean(t[k] for t in trials) for k in SCORES}
            errors = [len(accuracy_errors(t)) for t in accuracy]
            row |= {'understood': understood, 'errors': statistics.mean(errors) if accuracy else None,
                    'error_free': statistics.mean(e == 0 for e in errors) if accuracy else None}
            rows.append(row)
            if args.verbose:
                acc = f"err={row['errors']:.1f} " if accuracy else ''
                print(f"  {item['id']} {item['label']:4} und={understood:.1f} {acc}"
                      + ' '.join(f'{k[:6]}={row[k]:.1f}' for k in SCORES) + (' 謝罪' if apologized else ''))
                for t in accuracy[:1]:
                    for c in accuracy_errors(t):
                        print(f"      {c['verdict']}: {c['quote'][:60]} / {c.get('note', '')[:80]}")
        n = len(rows)
        print(f"  n={n} understood={statistics.mean(r['understood'] for r in rows):.2f} "
              f"flips={flips}/{n} apology={apologies}/{n}"
              + (f' judge-vs-user={agree}/{n}' if variant == 'original' else ''))
        judged = [r for r in rows if r['errors'] is not None]
        print('  ' + (f"errors={statistics.mean(r['errors'] for r in judged):.2f} "
                      f"error_free={statistics.mean(r['error_free'] for r in judged):.2f} " if judged else 'errors=(未採点) ')
              + ' '.join(f'{k}={statistics.mean(r[k] for r in rows):.2f}' for k in SCORES))


def compare(args):
    """2 つの版を事例ごとに比べ、改善と悪化の件数と符号検定の p 値を出す。各版の全サンプルを平均する。"""
    from math import comb

    def samples(variant):
        return sorted(os.listdir(os.path.join(DATA, 'judge', variant)))

    def per_case(variant, cid, metric):
        if metric == 'errors':
            trials = [t for s in samples(variant) for t in load_trials('judge-accuracy', variant, s, cid)]
            return statistics.mean(len(accuracy_errors(t)) for t in trials) if trials else None
        trials = [t for s in samples(variant) for t in load_trials('judge', variant, s, cid)]
        return statistics.mean(t[metric] for t in trials) if trials else None

    print(f'{args.b} を {args.a} と比べる ({args.a}: {samples(args.a)}, {args.b}: {samples(args.b)})')
    for metric in ['errors'] + SCORES:
        better = worse = 0
        for item in eval_set(args.set):
            a, b = per_case(args.a, item['id'], metric), per_case(args.b, item['id'], metric)
            if a is None or b is None or a == b:
                continue
            if (b < a) == (metric == 'errors'):
                better += 1
            else:
                worse += 1
        n, k = better + worse, min(better, worse)
        p = min(1.0, 2 * sum(comb(n, j) for j in range(k + 1)) / 2 ** n) if n else 1.0
        print(f'  {metric:17} 改善 {better:2} 悪化 {worse:2}  p={p:.2f}')


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
    pl = sub.add_parser('plant', help='judge の診断用に、誤りを 1 つ混ぜた版を作る')
    pl.add_argument('base', help='variant または variant/sample')
    pl.add_argument('--set', default='eval-set')
    pl.add_argument('--jobs', type=int, default=5)
    pl.set_defaults(func=plant)
    dg = sub.add_parser('diagnose', help='plant で混ぜた誤りを正確さ judge が検出できたかを数える')
    dg.add_argument('base', help='plant に渡した variant または variant/sample')
    dg.add_argument('--set', default='eval-set')
    dg.set_defaults(func=diagnose)
    cp = sub.add_parser('compare', help='2 つの版を事例ごとに比べて符号検定する')
    cp.add_argument('a', help='基準の版 (例: v2)')
    cp.add_argument('b', help='候補の版 (例: v4)')
    cp.add_argument('--set', default='eval-set')
    cp.set_defaults(func=compare)
    rp = sub.add_parser('report')
    rp.add_argument('targets', nargs='+', help='variant または variant/sample')
    rp.add_argument('--set', default='eval-set')
    rp.add_argument('-v', '--verbose', action='store_true')
    rp.set_defaults(func=report)
    args = p.parse_args()
    args.func(args)


if __name__ == '__main__':
    sys.exit(main())
