# 新モデル監視 (macOS / launchd)

Ollama公式ライブラリを毎日チェックし、**このマシンで動くサイズの新モデルが出たら
自動で pull → ベンチ2条件 → レポート作成**まで行います。

セッション内のcronではなくlaunchdを使うのは、実際に取りこぼした経験があるためです
(エージェントのセッションが閉じている・処理中だと発火しません)。

## 導入

```bash
cp watch/com.esp.llm-model-watch.plist ~/Library/LaunchAgents/
# plist内のパスを自分のホームに書き換えてから:
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.esp.llm-model-watch.plist
```

初回実行はスナップショットを作るだけで、ベンチは走りません(既存235モデルを
一斉にダウンロードしないため)。

## 動作

1. `https://ollama.com/library` の一覧を前回分と差分比較
2. 新規エントリのうち、埋め込み/音声/画像系(`bge-`, `whisper`, `embed` など)を除外
3. 残りから**人が実際に選ぶであろうタグ**を採用
   (`:27b` のような素のタグを優先。`:27b-mtp-q8_0` のような派生は後回し)
4. `ollama pull` → `run_bench.py` を**直答モード**と**思考モード**の2条件で実行
5. レポートとmacOS通知を出力

## PrismML(Bonsai)の監視 — 検知と通知のみ

Bonsai は本家 llama.cpp/Ollama で動かない(独自量子化)ため、**Ollama ライブラリには
載りません**。そこで PrismML の Hugging Face 組織(`prism-ml`)も毎日差分を取り、
新しい **GGUF** リポジトリが出たらレポートと通知を出します(MLX / AWQ / 画像モデルは除外)。

こちらは**自動でダウンロードもベンチもしません**。評価には PrismML の llama.cpp フォークの
ビルドが必要で、新しい Bonsai が setup.sh の固定コミットより新しいフォークを要求することも
あります(2026-09 に実際に起きました)。第三者コードの無人ビルドは避け、判断を挟む設計です。

通知が来たら:
1. そのファイルを読み込めるコミットでフォークをビルドする
2. `BENCH_BONSAI=1` でベンチを回す(直答・思考の両条件)
3. 採用するなら setup.sh の `BONSAI_COMMIT` と `BONSAI_GGUF` を**セットで**更新する

2つのソースは独立しており、片方の取得に失敗してももう片方は動きます。

## 安全装置

| 制限 | 既定値 | 環境変数 |
|---|---|---|
| 1タグあたりの最大サイズ | 30GB | `WATCH_MAX_PULL_GB` |
| 最小サイズ(小型モデル除外) | 4GB | `WATCH_MIN_PULL_GB` |
| 空き容量の下限(下回ると中止) | 120GB | `WATCH_MIN_FREE_GB` |
| 1回に評価する新モデル数 | 2 | `WATCH_MAX_PER_RUN` |

スナップショットは**重い処理の前に**更新するため、途中で落ちても同じモデルを
再ダウンロードし続けることはありません。

## 出力

```
~/.local/state/llm-model-watch/
├── library.json          # 前回のOllamaライブラリ一覧
├── prismml.json          # 前回のPrismML(prism-ml)リポジトリ一覧
├── watch.log             # 実行ログ
└── reports/YYYY-MM-DD.md # スコア表と生ログ
```

モデルは評価後も残ります(判断はユーザーがするため)。不要なら `ollama rm <tag>`。

## 注意

思考モードは temperature 0.6 固定で回します。ベンダー推奨値はモデルごとに違うので
(Qwen3.8は1.0、Ornithは0.6)、**レポートの数字は一次スクリーニング**と考え、
有望なものは推奨設定で測り直してください。実際、推奨外の設定で測って結論を
誤った事例がこのリポジトリのREADMEに記録してあります。
