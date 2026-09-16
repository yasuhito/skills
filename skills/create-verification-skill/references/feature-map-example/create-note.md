# note を作成する

note の作成機能では、ユーザーがブラウザまたは CLI からタイトル付きの note を保存し、未完成の下書きをキャンセルし、保存された note をユーザー向けの別のビューから確認できます。

## Sub-features

- `create-open` は、ブラウザの各エントリーポイントから空のエディターを開きます。
- `create-save` は、タイトルと本文を永続化します。
- `create-cancel` は、ブラウザで未完成の下書きを破棄します。
- `create-cli` は、ターミナルから同じ形式の note を作成します。

## How to get to it (user POV)

- ブラウザのツールバーで `New note` ボタンを選択します。
- ブラウザで、編集可能なフィールドの外にフォーカスがある状態で `n` を押します。
- ターミナルで `notes create --title <title> --body <body>` を実行します。

## Driving it with control-notes

Preconditions:

- Notes が `http://127.0.0.1:4173` で正常に動作しています。
- `Release checklist` というタイトルの note が存在しません。
- `control-notes doctor` が想定する URL と使い捨てのデータディレクトリを報告します。

- **エディターを開く。** `New note` を選択します。`control-notes browser click --role button --name "New note"` を実行します。`Note editor` という名前のフォームが表示され、`Title` textbox にフォーカスが移ります。
- **内容を入力する。** タイトルと本文を入力します。`control-notes browser fill --role textbox --name "Title" --value "Release checklist"` と `control-notes browser fill --role textbox --name "Body" --value "Tag and publish"` を実行します。`Save note` ボタンが有効になります。
- **note を保存する。** `Save note` を選択します。`control-notes browser click --role button --name "Save note"` を実行します。`Note saved` という名前の status が表示され、heading が `Release checklist` になります。
- **永続化を確認する。** note のリストに戻り、note をもう一度開きます。`control-notes browser click --role link --name "All notes"` と `control-notes browser click --role link --name "Release checklist"` を実行します。エディターに保存された両方の値が表示されます。
- **下書きをキャンセルする。** 新しい note を開き、`Discard me` と入力して `Cancel` を選択します。`control-notes browser click --role button --name "New note"`、`control-notes browser fill --role textbox --name "Title" --value "Discard me"`、`control-notes browser click --role button --name "Cancel"` を実行します。note のリストに戻り、`Discard me` link は存在しません。
- **CLI エントリー。** 2 つ目の note を作成します。`control-notes cli -- notes create --title "CLI note" --body "Created from terminal" --format json` を実行します。終了コードは `0` で、stdout に新しい note の ID とタイトルが含まれます。
- **証明。** `All notes` から、保存した両方の note をもう一度開きます。`control-notes browser snapshot --aria --path artifacts/create-note/list.aria.txt` と `control-notes browser screenshot --path artifacts/create-note/list.png` を実行します。アーティファクトに `Release checklist` と `CLI note` が表示されます。

## Gotchas

- textbox にフォーカスがある状態で `n` を押すと、新しいエディターが開かず、その文字が入力されます。
- タイトルは保存時に trim されます。下書きの入力値ではなく、render されたタイトルを assert してください。
- 保存 status だけでは証明として不十分です。リストから note をもう一度開いてください。
- fixture のクリーンアップ中に `Release checklist` と `CLI note` を削除しますが、それらの証明用アーティファクトは残してください。
