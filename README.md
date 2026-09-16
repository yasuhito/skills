# skills

外部で公開されているエージェントスキルを、日本語で利用できるように移植するリポジトリです。

## 収録範囲

現在は pstack から、次の内容のみを日本語に翻訳しています。

- `create-verification-skill`
  - `references/feature-map-example/` の完全な例を含みます。
- `maintain-verification-skill`

## ディレクトリ構成

```text
skills/
├── create-verification-skill/
│   ├── SKILL.md
│   └── references/feature-map-example/
└── maintain-verification-skill/
    └── SKILL.md
```

## 使用方法

使用するスキルのディレクトリを、対象プロジェクトの `.cursor/skills/` にコピーしてください。例:

```sh
cp -R skills/create-verification-skill /path/to/project/.cursor/skills/
```

その後、Cursor で `/create-verification-skill` または `/maintain-verification-skill` を実行します。追加のインストール手順はありません。

## ライセンス

著作権表示とライセンス条件は [LICENSE](LICENSE) を参照してください。
