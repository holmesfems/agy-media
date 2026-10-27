---
name: agy-media
description: Claude Code から Antigravity CLI（agy、Gemini）を呼び出して、Claude にできないこと・苦手なことを補うスキル。画像の生成、動画の書き出しを見て・聞いての BGM の確認（曲調が場面に合うか・つなぎ目）と声と BGM の音量差の測定、音声（wav・mp3・動画の音）を聞いての文字起こし・話者分け・発音やアクセントの違和感の指摘、画像や動画のコマの解析（場面の説明・物の位置のおおよその座標）を agy に頼み、その前後の機械的な作業（ffmpeg での切り出し・コマ画像化・座標の目盛り、無音区間・音量・スペクトログラム、OpenCV での追跡、結果の描き込み確認）はローカルのスクリプトで行う。「agy で画像を作って」「Gemini に画像生成させて」「BGM 合ってるか見て」「BGM が大きすぎないか」「この音声を聞いて」「文字起こしして」「アクセントおかしいところ探して」「この動画を agy に見せて」「◯秒から◯秒を解析」「敵の座標を追跡」「agy を呼び出して」のように、agy・Antigravity・Gemini を使った画像・動画・音声の話が出たら、明示的な指定がなくても使うこと。Use for image generation and image / video / audio analysis through the agy (Antigravity) CLI.
---

# agy-media

Claude は画像を「見る」ことはできるが、**画像を作る・音を聞く**ことはできず、**画素単位の座標を目で読むのも苦手**。
そこを agy（Gemini）とローカルのスクリプトで補う。

| やりたいこと | 担当 |
|---|---|
| 画像を作る | agy（組み込みの画像生成） |
| 音声の文字起こし・話者分け・発音の違和感 | agy（`gemini-4-argon-high`） |
| BGM が場面に合うか（曲調・テンポ・雰囲気）・つなぎ目 | agy（`gemini-4-argon-high`、mp4 をそのまま） |
| 声と BGM の音量差・声が埋もれる区間 | `mix_balance.py`（声だけのトラックと完成品を比べる） |
| 無音区間・音量（LUFS）・波形／スペクトログラム | `audio.py`（ffmpeg） |
| 動画の切り出し・コマ画像・座標の目盛り | `frames.py`（ffmpeg） |
| 画像・コマの意味（何が起きているか、何秒に何が） | agy または Claude 自身 |
| 物の位置（おおよそ） | agy（目盛り付きのコマで） |
| 物の位置（精密・全フレーム） | `track.py`（OpenCV）※エフェクトが重なると弱い |
| 結果の確認 | `overlay.py` で描き込んだ画像を Claude が Read で見る |

スクリプトはこのスキルのフォルダの `scripts/` にある（以下 `<skill>/scripts/`）。どれも `python -I <script> -h` で使い方が出る。
必要なもの: `agy`（ログイン済み）、Python 3 と Pillow、ffmpeg（PATH に無ければ `pip install imageio-ffmpeg`）、追跡・音量差には opencv-python（numpy）。

## 守ること

1. **agy は必ず `agy_run.py` 経由で呼ぶ。** プロンプトを 1 つの引数で渡すのでクォートが壊れない。JSON で結果を返し、agy が作った画像を取り出す。
   出力の文字化けを避けるため、結果は `--save <file>` で UTF-8 のファイルに書いて Read で読む（PowerShell のリダイレクト `>` は使わない）。
2. **`--dangerously-skip-permissions` は使わない。** 頭なし（`-p`）の agy はコマンド実行の許可を取れないので、ffmpeg などが要る下ごしらえは Claude が先にやり、agy には「ファイルを見る・聞く・作る」だけを頼む。
   `agy_run.py` はプロンプトの頭に「端末なし・ウェブ検索なし・指定のファイルだけ開く」の決まりを付ける（agy は放っておくと、ユーザー名でウェブ検索したり、関係ないファイルを開こうとしたりする）。
3. **agy の答えはそのまま信じない。** 画像は Read で見る。座標は `overlay.py` で描き込んで見る。時刻は `audio.py` の `sound_spans` と突き合わせる。
4. 頼むファイルは `--dir` で渡したフォルダに置き、プロンプトにはフルパスで書く。一度に渡す画像は少なめに（全画面 14 枚で混乱した例がある）。

## 画像を作る

```
python -I <skill>/scripts/agy_run.py --prompt "Generate an image of <内容> (16:9) with your image generation tool." --out <保存先フォルダ> --save <保存先>/agy.json
```
- 返りの `media` に、保存先へコピーした画像のパスが入る（agy は自分の `~/.gemini/antigravity-cli/brain/<会話ID>/` に JPG で保存する。指定のパスには保存しない）。
- できた画像は Read で見て、意図と合っているか確かめてからユーザーに見せる。1 枚 50 秒ほど。

## 音声を聞いてもらう

```
python -I <skill>/scripts/audio.py <音声または動画> --out <work> --start 0 --end 40
python -I <skill>/scripts/agy_run.py --prompt-file <work>/ask.txt --dir <work> --model gemini-4-argon-high --save <work>/agy.json
```
- `audio.py` は `audio.wav`（モノラル 16 kHz の切り出し）、`wave.png`、`spectrum.png`（時間・周波数の目盛り付き）、`stats.json`（ラウドネス、音のある区間・無音区間＝元ファイルの秒）を書く。
  無音区間は発話の区切りとほぼ一致する（実測で字幕との差 0.05 秒以内）ので、正確な時刻はこちらを使う。
- agy への頼み方の例（`ask.txt`）:
  ```
  Task: open <work の audio.wav のフルパス> once with your file-reading tool and listen to it (40 s of Japanese speech, two voices).
  Then answer with a transcript, one utterance per line: [start-end seconds] A or B: Japanese text
  After the transcript, add a line "ISSUES:" and list words that sound mispronounced or unnaturally accented (time + word), or "none".
  ```
- モデルは `gemini-4-argon-high`。Flash では音声のあとに出力が途切れて失敗した。40 秒で 1 分ほど。長い音声は 30〜60 秒ずつに分ける。
- 文字起こし・話者分けはかなり正確（読み上げ台本 40 秒でほぼ一致）。発音の指摘は**候補**として扱い、ユーザーに聞いて確かめてもらう。
- Claude 自身も `spectrum.png` / `wave.png` を Read で見れば、音の有無・高さの動き・BGM や効果音の入り方はおおまかに分かる。

## BGM を確かめる（動画の書き出しを見て・聞いてもらう）

agy は mp4 を**映像と音の両方**で理解できる（ロゴの文字・画面のタイトル・最初のセリフまで正しく答えた）。
「この BGM は場面に合っているか」のような、Claude が波形を見ても分からない判断を頼める。

```
ffmpeg -ss 0 -to 40 -i <書き出した動画> -vf "scale=1280:-2,fps=30" -c:v libx264 -crf 28 -c:a aac -b:a 160k <work>/clip.mp4
python -I <skill>/scripts/agy_run.py --prompt-file <work>/ask.txt --dir <work> --model gemini-4-argon-high --save <work>/agy.json
python -I <skill>/scripts/mix_balance.py <書き出した動画> <声だけのトラック> --start 0 --end 40 --out <work>/balance.json
```
- 渡す mp4 は 30〜60 秒、720p・30fps に落とす（40 秒 3 MB で 1 分半ほど）。
- `ask.txt` には、まず**見えた・聞こえた証拠**（冒頭のロゴの文字、画面のタイトル 2 つ、最初のセリフ）を書かせ、それから
  BGM のジャンル・テンポ・雰囲気・楽器、場面に合うか（1〜5 と理由）、声が聞き取りにくい時刻、不自然なつなぎ目、直し方の案、を日本語で答えさせる。
  場面の説明（例: 落ち着いた解説の導入、暗い SF 調の UI）も添える。
- **agy の「合う・合わない」は 1 つの意見**として伝える。曲選びはユーザーの好みとシリーズの雰囲気で決まる。
- **音量の判断は数値で裏を取る。** agy は「BGM が大きめ」という全体の傾向は当てたが、「声が埋もれている」と挙げた時刻は、測ると一番聞き取りやすい区間だった。
  場所の特定は `mix_balance.py` の `buried_spans` を使う。
- `mix_balance.py` は声だけのトラックを完成品に自動で位置合わせして差し引き、残り（BGM＋効果音）との差を 0.5 秒ごとに出す。
  トーク動画の目安は BGM が声より 12〜20 dB 下。差が `--min-db`（既定 6 dB）未満の区間を `buried_spans` に挙げる。
  文末で声が小さくなる所や、ゲーム音が鳴っている所も拾うので、数字を見て判断する。声に強い EQ・リバーブをかけていると BGM が大きめに出る。

## 画像・動画を見てもらう

```
python -I <skill>/scripts/frames.py <動画> --out <work> --start 50 --end 57 --fps 2 --crop 1152:648:0:0 --grid 100 --sheet
```
- `frames/f_001.png …` と `index.json`（各コマの元動画での秒、切り抜きのずれ）を書く。`--grid 100` は 100 px ごとに**元動画の座標**の目盛りを描く。`--sheet` は全コマを 1 枚に並べた `sheet.png`。
- 見たい所だけ `--crop` で切り抜く（座標は目盛りに元動画の値で出るので換算不要）。
- agy への頼み方: 「frames の f_001〜f_0NN を開き、目盛りの数字で読んで、◯◯の体の中心を x, y で。見えなければ null。JSON だけで答えて」。対象は見た目と、最初のコマでのだいたいの位置で指定する。
- 結果の確認:
  ```
  python -I <skill>/scripts/overlay.py <work>/index.json <work>/agy.json [<work>/cv.json] --out <work>/check.png --zoom X:Y:W:H
  ```
  複数の結果を色違いで重ねられる。check.png を Read で見て当たっているか確かめる。
- 実測（アークナイツの録画、エフェクトの多い敵）: 目盛りなしでは 100 px ほどずれた。目盛りありでは止まっている間は 20〜30 px、動き出すと 50〜90 px のずれ。**おおよその位置・動きの流れ**には使えるが、演出の位置合わせにはそのままでは粗い。

## 精密に追う（OpenCV）

```
python -I <skill>/scripts/track.py <動画> --start 50 --end 57 --box x,y,w,h --out <work>/cv.json
```
- 最初のコマの箱（元動画の画素）は、目盛り付きのコマを見て Claude が決める。全フレームの中心座標と一致度（score）を出す。一致度が `--hold` 未満のフレームは位置を据え置いて `held` を付ける。
- 背景が素直で見た目があまり変わらない物（UI、カーソル、テロップ、小さなユニット）には強い。ビームや爆発が重なる敵は見失いやすい。その場合は agy や Claude で 0.5 秒ごとのキーフレームを取り、間をつなぐほうが確実。
- 結果は必ず `overlay.py` で描いて確かめる。

## つまずきやすい点

| 症状 | 原因と対処 |
|---|---|
| `response` が空で `denied_actions` に command | agy がコマンドを使おうとした。下ごしらえを Claude が済ませ、プロンプトを「見るだけ」にする |
| 日本語が文字化け | パイプ越しの agy はコンソールのコードページ（cp932 など）で出す。`agy_run.py` が自動で読み替える。結果は `--save` のファイルを Read で読む |
| 話が別の動画・別の話題にそれる | 渡す画像が多すぎる／長い。切り抜く・枚数を減らす・モデルを変える |
| 画像が指定のパスに無い | agy は brain フォルダに保存する。`--out` を付ければ `agy_run.py` がコピーする |
| `agy -p` が返ってこない（Windows） | `agy_run.py` は `--timeout`＋60 秒で打ち切る |
