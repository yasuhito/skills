# note を検索する

検索機能では、ユーザーがタイトルまたは本文のテキストから note を見つけ、一致した note を調べ、一致なしと検索不能を区別できます。

## Sub-features

- `search-open` は、サポートされる各ブラウザエントリーポイントから検索を開きます。
- `search-match` は、note のデータを変更せず、タイトルと本文の一致を返します。
- `search-open-result` は、検索結果を note エディターで開きます。
- `search-empty` は、一致しないクエリに対して完全な空の状態を表示します。
- `search-clear` は、クエリを削除し、最近使った note のビューを復元します。
- `search-cli` は、ターミナルから同じ一致する note を返します。

## How to get to it (user POV)

- ブラウザのツールバーで `Search` ボタンを選択します。
- ブラウザで、編集可能なフィールドの外にフォーカスがある状態で `/` を押します。
- ターミナルで `notes search <query>` を実行します。

## Driving it with control-notes

Preconditions:

- Notes が `http://127.0.0.1:4173` で正常に動作しています。
- 使い捨てのデータディレクトリに、本文が `Draft budget` の `Quarterly plan` が含まれています。
- `control-notes doctor` が想定する URL とデータディレクトリを報告します。

- **ツールバーから入る。** `Search` ボタンを選択します。`control-notes browser click --role button --name "Search"` を実行します。`Search notes` という名前の dialog が表示され、その searchbox にフォーカスが移ります。
- **キーボードから入る。** dialog を閉じ、ページにフォーカスを移して `/` を押します。`control-notes browser press --key "/"` を実行します。同じ dialog が表示され、ページにスラッシュは挿入されません。
- **タイトルの一致。** `quarterly` と入力します。`control-notes browser fill --role searchbox --name "Search notes" --value "quarterly"` を実行します。`Search results` リストには `Quarterly plan` が含まれ、`Grocery list` は含まれません。
- **本文の一致。** クエリを `budget` に置き換えます。`control-notes browser fill --role searchbox --name "Search notes" --value "budget"` を実行します。検索結果 `Quarterly plan` は、本文の一致箇所の抜粋とともに表示されたままです。
- **検索結果を開く。** `Quarterly plan` を選択します。`control-notes browser click --role link --name "Quarterly plan"` を実行します。dialog が閉じ、エディターの heading が `Quarterly plan` になります。
- **空の状態。** 検索を再度開き、`volcano` と入力します。`control-notes browser fill --role searchbox --name "Search notes" --value "volcano"` を実行します。検索完了後、`No matching notes` という名前の status が表示されます。
- **クエリをクリアする。** `Clear search` を選択します。`control-notes browser click --role button --name "Clear search"` を実行します。searchbox が空になり、検索結果リストが `Recent notes` region に置き換わります。
- **CLI での一致。** ターミナルから検索します。`control-notes cli -- notes search "quarterly" --format json` を実行します。終了コードは `0` で、stdout には title が `Quarterly plan` の object が 1 つ含まれます。
- **CLI での不一致。** 存在しない値を検索します。`control-notes cli -- notes search "volcano" --format json` を実行します。終了コードは `0` で、stdout は `[]` です。
- **証明。** 検索結果が存在する状態を取得します。`control-notes browser snapshot --aria --path artifacts/search/results.aria.txt` と `control-notes browser screenshot --path artifacts/search/results.png` を実行します。両方のアーティファクトで、Notes、クエリ、`Quarterly plan` を識別できます。

## Gotchas

- エディターまたは searchbox にフォーカスがある状態で `/` を押すと、検索が開かず、テキストが挿入されます。
- 検索結果は短い debounce の後に更新されます。固定時間 sleep するのではなく、検索結果リストまたは空の status を待ってください。
- ユーザーが `Include archived` を有効にしない限り、archived note は除外されます。
- CLI はデフォルトで人間が読みやすい出力を使用します。安定した assert には `--format json` を使用してください。
- 検索結果を開くと、ブラウザの状態が変わります。別のクエリを証明する前に、検索を再度開いてください。
