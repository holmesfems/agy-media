---
name: agy-review
description: Claude Code から Antigravity CLI（agy、Gemini）にコードの変更（git diff・ブランチ・コミット）を読むだけでレビューさせ、Claude とは別に学習されたモデルの目で見落としを拾うスキル。既定は Neon で、Hydrogen などを並べて走らせると同じ場所を指した指摘をまとめ、Claude が 1 件ずつ実際のコードで確かめてから報告する。「agy でレビューして」「Gemini にもレビューさせて」「セカンドオピニオン」「別のモデルの目で見て」「Claude 以外にもレビューさせたい」「この PR を Gemini で」のように、agy・Gemini を使ったコードレビューの話が出たら、明示的な指定がなくても使うこと。Use for an independent second-opinion code review of a diff through the agy (Antigravity) CLI.
---

# agy-review

同じモデルが書いたコードを同じモデルがレビューすると、見落としの癖まで同じになる。
Gemini は Claude と別に学習されているので、ミスの出方がずれる。agy に**読むだけ**でレビューさせ、その指摘を Claude が確かめる。

スクリプトはこのスキルのフォルダの `scripts/review.py`（以下 `<skill>/scripts/`）。`python -I <skill>/scripts/review.py -h` で使い方が出る。
必要なもの: `agy`（ログイン済み）、git、Python 3.10+。

## 流れ

1. **何をレビューするか決める**（ユーザーが言わなければ、まだコミットしていない変更）。
   ```
   python -I <skill>/scripts/review.py --repo <リポジトリ> --out <work>                    # コミット前の変更（HEAD との差）
   python -I <skill>/scripts/review.py --repo <リポジトリ> --base origin/main --out <work> # このブランチ全体
   python -I <skill>/scripts/review.py --repo <リポジトリ> --commit <sha> --out <work>      # 1 コミット
   ```
   - `<work>` はリポジトリの外（scratchpad など）に置く。`diff.patch`・`ask.txt`・モデルごとの `result_<model>.json`・まとめの `review.json` が入る。
   - 既定は `gemini-4-neon-high` だけ。`--model` で変えられ、くり返し指定すると並べて走らせる（例: `--model gemini-4-neon-high --model gemini-4-hydrogen-high`）。数十行〜数百行の差分で 3〜5 分。長いので Bash の `run_in_background` で回す。
   - 変更の意図はコミットメッセージから自動で入る。コミット前の変更なら `--intent "…"` で、ユーザーの言葉で書く。
2. **`review.json` を Read で読む。** `findings` は場所ごとにまとめた指摘で、`agreed`（いくつのモデルが同じ場所を挙げたか）・`severity`・各モデルの `summary` と `failure_scenario` が入る。
3. **1 件ずつ Claude が確かめる。** その行と、呼び出し元・呼び出し先を Read で読み、`failure_scenario` をコードの上でたどる。テストがあれば走らせる。
   - 本物 → 「確かめた」として、どの入力で何が起きるかを添えて報告する。
   - 再現できない・前提が違う → 「外れ」として理由を一言。
   - 判断がつかない → 「未確認」として、何を確かめればよいかを書く。
   - `agreed` が多くても、`confidence` が高くても、確かめずに本物扱いしない。逆に 1 モデルだけの指摘にも本物はある（下の実測）。
4. **ユーザーへは、確かめた結果で報告する。** agy の文をそのまま貼らない。直すかどうかはユーザーに聞く（勝手に直さない）。

## 守ること

1. **Claude の意見を agy に渡さない。** `--intent` には作者の意図だけを書く。Claude のレビュー結果や「ここが怪しい」を入れると、別の目で見る意味がなくなる。
   Claude 自身もレビューするなら、agy の結果を読む前に済ませ、あとで突き合わせる（両方が挙げたもの・片方だけのもの）。
2. **コードは Google に送られる。** `review.py` は `.env`・鍵・credentials のファイルを差分から外し（大文字小文字は問わない）、
   差分のどの行（足した行・消した行・前後の行）にでも API キーや秘密鍵らしい文字列があれば、何も送らずに止まる（`STOPPED`）。
   止まったら、その行をユーザーに見せて判断してもらう。`--allow-secret-like` はユーザーが「送ってよい」と言ったときだけ。
   **ただし agy はリポジトリ全体を読める**（呼び出し元・呼び出し先を読ませるため）。差分の外にあるファイルを開かせない守りは、
   プロンプトでのお願いと、agy 側の settings.json の deny（`read_file(regex:.*\.env.*)` など）だけ。
   秘密を置いたファイルがリポジトリにあるなら、agy の deny に足してから使う。初めて使うリポジトリでは、ユーザーに deny の設定を確かめてもらう。
3. **`--dangerously-skip-permissions` は使わない。** agy は plan モード（読むだけ）で、端末・ウェブ・リポジトリの外のファイルを使わないよう指示してある。`denied_actions` が出たら、agy が何かを実行しようとした印。結果は使えるが、ユーザーに伝える。
4. **大きい差分は分ける。** 既定で 400 KB を超えると止まる。コミットごと（`--commit`）や、フォルダを絞ったブランチに分けてレビューする。

## 実測（agy 1.5.12、TypeScript の 8 ファイル・230 行の差分に、わざと 4 つのバグを入れて試した）

| モデル | 時間 | 埋めたバグ 4 件 | それ以外 |
|---|---|---|---|
| `gemini-4-neon-high` | 3 分 40 秒 | 4 件とも | 1 件（本物。保存の競合で、払いが落ちたきせかえが残る。元のコードにもあった） |
| `gemini-4-hydrogen-high` | 1 分 30 秒 | 4 件とも | なし |
| `gemini-4-argon-high` | 2 分 40 秒 | 4 件とも | なし |

- 埋めたバグ（`Math.min`→`Math.max`、`<`→`<=`、`-`→`+`、`!==`→`===`）は単純な取り違えで、どのモデルも具体的な入力と結果まで正しく書いた。外れの指摘は 0 件。
- **差が出たのは、埋めていない本物の不具合**で、見つけたのは Neon だけだった。微妙な競合や仕様の食い違いは Neon が強い。
- Hydrogen は速い（Neon の半分以下の時間）が、挙げる数は少なく、どれも Neon と同じ場所だった。そこで既定は Neon だけにした。急ぐときは `--model gemini-4-hydrogen-high`、2 モデルの一致を目印にしたいときは両方を並べる。
- agy-media 自身の 0.3.0（新しい review.py を含む 7 ファイル・約 380 行）でも、Neon は 6 件（すべて本物）、Hydrogen は 2 件（どちらも Neon と同じ場所）を挙げた。
- 1 回の試しなので、ほかのリポジトリで外れが多いようなら、ここを書き直す。

## つまずきやすい点

| 症状 | 原因と対処 |
|---|---|
| `empty diff` | 指定の差分が空。`--base` / `--commit` / `--staged` を確かめる |
| `STOPPED`（secret-like） | 追加した行が秘密の値に見えた。行をユーザーに見せて判断してもらう |
| `findings` が空で `response` だけある | agy が JSON の形で答えなかった（`structured: false`）。`result_<model>.json` の `response` を読む |
| モデルの `status` が `ERROR` で `error` がある | そのモデルだけ失敗した。ほかのモデルの結果は `review.json` に残る |
| マージコミットを `--commit` で渡した | 1 つ目の親との差（マージで入ってきた変更）をレビューする |
| 指摘の行番号が少しずれる | 新しい版の行番号で答えるよう頼んでいるが、ずれることがある。前後を読んで探す |
| TIMEOUT | 差分が大きい・モデルが考え込んだ。分けるか `--timeout` を延ばす |
