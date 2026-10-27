# agy-media

[Claude Code](https://claude.com/claude-code) から [Antigravity CLI](https://antigravity.google/)（`agy`、Gemini）を呼び出して、Claude の苦手なところを補うためのスキルです。

- **画像を作る**：agy の画像生成で作り、できた画像を手元のフォルダに取り出す
- **音を聞く**：文字起こし、話者分け、発音・アクセントの違和感の指摘
- **画像・動画を見る**：場面の説明、物のおおよその座標
- **機械的な作業はローカルで**：ffmpeg による切り出し・コマ画像化・座標の目盛り、無音区間・ラウドネス・スペクトログラム、OpenCV での追跡、結果の描き込み確認

> English: A small Claude Code skill that delegates image generation and image / video / audio understanding to the Antigravity CLI (`agy`, Gemini), with local helper scripts (ffmpeg, OpenCV, Pillow) to prepare inputs and to check the answers. It never runs agy with `--dangerously-skip-permissions`.

## 必要なもの

- `agy`（インストールしてログイン済み。`agy models` でモデルが出ること）
- Python 3.10+ と Pillow
- ffmpeg（PATH に無ければ `pip install imageio-ffmpeg`）
- 追跡を使うなら `opencv-python`

```
pip install -r requirements.txt
```

## 入れ方

Claude Code のプラグインとして入れる場合：

```
/plugin marketplace add holmesfems/agy-media
/plugin install agy-media@agy-media
```

スキルだけ入れる場合は、`skills/agy-media` を `~/.claude/skills/agy-media` にコピーしてください。

## 中身

| ファイル | 役割 |
|---|---|
| `skills/agy-media/SKILL.md` | Claude への手順書（いつ・どう agy を使うか、確かめ方） |
| `scripts/agy_run.py` | agy を頭なしで実行して JSON で結果を返し、生成された画像を取り出す |
| `scripts/frames.py` | 動画の範囲をコマ画像に（切り抜き・座標の目盛り・一覧画像） |
| `scripts/audio.py` | 音声の切り出し・波形・スペクトログラム・ラウドネス・無音区間 |
| `scripts/track.py` | OpenCV のテンプレートマッチングで 1 つの物を追う |
| `scripts/overlay.py` | 座標をコマに描き込み、並べて確認用の画像にする |

## 試した結果（Windows 11、agy 1.4.8）

- **画像生成**：1 枚およそ 50 秒。agy は指定のパスではなく自分の作業フォルダ（`~/.gemini/antigravity-cli/brain/<会話ID>/`）に保存するため、`agy_run.py --out` で取り出す。
- **音声**：読み上げ台本 40 秒の文字起こしは字幕とほぼ一致し、2 人の話者分けも全行正しかった（`gemini-4-argon-high`、約 1 分）。Flash 系のモデルは音声のあと出力が途切れて失敗した。
- **動画の座標**：ゲーム録画の敵の位置は、目盛りなしでは約 100 px ずれた。目盛りを入れると、止まっている間は 20〜30 px、動いている間は 50〜90 px のずれ。おおよその位置や動きの流れには使えるが、精密な位置合わせには向かない。
- **OpenCV の追跡**：エフェクトが激しく重なる対象は見失いやすい。素直な対象向け。

## 注意

- agy を `--dangerously-skip-permissions` で動かすことはしません。頭なしの agy はコマンドの許可を取れないので、ffmpeg などの下ごしらえは Claude 側で済ませ、agy には見る・聞く・作るだけを頼みます。
- agy は放っておくと、ウェブ検索をしたり、頼んでいないファイルを開こうとしたりすることがあります。`agy_run.py` はプロンプトの頭に、それを禁じる決まりを付けます。それでも、渡したファイルの内容は Google のサービスに送られます。見せたくないファイルは渡さないでください。
- agy の答えは間違うことがあります。必ず描き込み画像や数値で確かめてください。

## License

MIT
