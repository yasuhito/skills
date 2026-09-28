# wait-what の評価

`/wait-what` の言い直しの質を、pi の実際の利用履歴で測る。
手法は [Automating eval design and hillclimbing](https://claude.dev/blog/automating-eval-design-and-hillclimbing/) に倣う。

## 仕組み

1. `extract`: `~/.pi/agent/sessions` から `/skill:wait-what` の呼び出しを抜き出し、`data/cases.json` に保存する
2. `run <variant>`: 各事例のセッションを呼び出し直前で切り、`variants/<variant>/` のスキルで pi に言い直させる (本番と同じモデル、読み取り専用ツール)。
   それを LLM judge (Claude) が rubric で 2 回ずつ採点する。`original` は履歴に残る当時の応答をそのまま採点する
3. `report`: 項目別の平均、2 回の採点で判定が割れた件数、謝罪で始まる応答の数 (正規表現) を出す

評価セット `data/eval-set.json` は 20 件で、ユーザーの次の発言をもとに成功 (`pass`) / 失敗 (`fail`) のラベルを付けてある。
形式は `[{"id": "<呼び出し時刻の ID>", "label": "pass" | "fail", "why": "..."}]`。

`data/` は、評価セットも含めて私的な会話の内容を含むので gitignore している。
別のマシンでは、`extract` で事例を作り直し、評価セットを手元のバックアップから戻す。

## 使い方

```sh
python3 eval.py extract
cp -r variants/v2 variants/v3   # 候補版を作って SKILL.md を 1 か所だけ変える
python3 eval.py run v3
python3 eval.py run v2 --sample s1   # ベースラインを再生成してぶれ幅を見る
python3 eval.py report v2 v2/s1 v3 -v
```

判断のルール:

- 1 ラウンドに 1 変更
- ベースラインを 2 回生成したときのぶれ幅を超えて改善し、他の項目が下がらないときだけ採用する

## 注意

- judge の `understood` は、ユーザーの実際の反応との一致が 13/20 しかない。項目別スコアを指標にする
- judge は説明の正確さを採点していない。採用前に出力を読んで確かめる
- `no_bad_analogy` はほぼ満点で頭打ち。改善の対象ではなく、悪化していないかを見る

## 履歴

| 版 | 変更 | plain | understood | 謝罪 |
|---|---|---|---|---|
| v0 | 日本語版の初版 | 2.90 / 3.23 (2 回生成) | 0.55 / 0.68 | 17 / 14 |
| v1 | CONTEXT.md の用語は平易に言い換え、必要なものだけ使う | 4.08 | 0.93 | 12 |
| v2 | 謝罪や前置きをせず本題から始める | 4.12 | 0.93 | 0 |

いま最も低い項目は `actionable` (3.75)。
