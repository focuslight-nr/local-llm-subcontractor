# local-llm-subcontractor

Claude Code や Codex CLI の「下請け」としてローカルLLMを使い、トークン消費(=API課金)を減らすためのワンショット環境構築キットです。

**EN**: One-shot setup for running a local LLM (Qwen3.6-27B via Ollama, or PrismML's Bonsai 27B ternary via their llama.cpp fork) as a cheap "subcontractor" for coding agents like Claude Code / Codex CLI. The orchestrator LLM writes a spec, the local model drafts the code, a local test/lint gate filters the output, and the orchestrator only reviews code that already passes. See `docs/` for agent integration.

## コンセプト

```
Claude Code (頭脳・レビュー役)
   │  仕様を書いて投げる
   ▼
ローカルLLM (下請け: テスト生成・定型コード・ログ要約)
   │  生成物
   ▼
ローカルゲート (pytest / lint / JSONバリデーション)
   │  通ったものだけ
   ▼
Claude Code が最終レビュー
```

ポイントは**ゲートを通ったものだけをオーケストレーターに見せる**こと。これをしないと「Claudeが赤ペン先生として全部読み直す」構図になり、削減効果が消えます。

## クイックスタート

```bash
git clone https://github.com/focuslight-nr/local-llm-subcontractor.git && cd local-llm-subcontractor
./setup.sh                 # RAM量から自動でモデルを提案
./setup.sh --model qwen    # Qwen3.6-27B Q4 (Ollama, 17GB)
./setup.sh --model bonsai  # Bonsai 27B ternary (llama.cpp fork, 6.7GB)
./setup.sh --model both
```

動作確認:

```bash
echo "Write a Python one-liner that reverses a string." | ./bin/llm -m qwen
```

## どちらのモデルを選ぶか

| | Qwen3.6-27B Q4 | Bonsai 27B 三値版 |
|---|---|---|
| 必要メモリ | ~17GB(実行時ピーク~20GB) | ~7GB(実行時ピーク~10GB) |
| ランタイム | Ollama(本家) | **PrismML製llama.cppフォーク必須** |
| 品質(公称) | フル精度の~99% | フル精度の~95% |
| 向く用途 | テスト生成・仕様追従コード | ログ要約・抽出・定型コード |

**RAM 32GB以上なら Qwen を推奨**。Bonsai は 8〜16GB マシンや常駐メモリを節約したい場合の選択肢です。

### 実測ベンチ(M4 Pro / 48GB, 2026-07-15)

`bench/run_bench.py` による自動採点(タスク: pytest生成→実行 / 仕様追従コード / ログ→JSON抽出):

| タスク | Qwen3.6-27B Q4 | Bonsai 27B 三値版 |
|---|---|---|
| pytest生成(実行して全パス) | ✅ 8/8 pass (52s) | ❌ 期待値の計算ミス + import漏れ (24s) |
| 仕様追従コード(7ケース) | ✅ 7/7 (17s) | ✅ 7/7 (6s) |
| ログ→JSON抽出(完全一致) | ✅ (35s) | ✅ (24s) |
| 生成速度 | 9–10 tok/s | 11–15 tok/s |

Bonsaiの失敗はケアレスミス型(算数ミス・import忘れ)で、まさにゲートで機械的に弾けるタイプでした。

### 追記: MoE版 `qwen3.6:35b-a3b` との比較(同環境)

| タスク | qwen3.6:27b (dense) | qwen3.6:35b-a3b (MoE, 24GB) |
|---|---|---|
| pytest生成 | ✅ 16/16 (66s) | ❌ 15本中1本、期待値の算数ミス (15s) |
| 仕様追従コード | ✅ 7/7 (18s) | ✅ 7/7 (2.4s) |
| ログ→JSON抽出 | ✅ (36s) | ✅ (10s) |
| 生成速度 | 9–10 tok/s | **24–40 tok/s** |

アクティブパラメータ3BのMoEなので速度は2.5〜4倍。抽出・定型コードは同品質でした。
**RAM 48GB以上なら「デフォルト35b-a3b、テスト生成だけ27b」の併用がおすすめ**です
(`bin/llm -m qwen3.6:35b-a3b` のように `-m` へ任意のOllamaモデル名を渡せます)。
インストールは `QWEN_MODEL=qwen3.6:35b-a3b ./setup.sh --model qwen` でも、`ollama pull` 直でも。

## リポジトリ構成

```
setup.sh            # 環境構築(macOS / Linux, 冪等)
bin/llm             # 下請け呼び出しCLI(stdin/引数 → 応答をstdout)
bin/serve-bonsai    # Bonsai用llama-server起動
bench/              # 自動採点ベンチ(モデル追加時の品質確認用)
docs/claude-code.md # Claude Code への組み込み手順(CLAUDE.mdスニペット付き)
docs/codex.md       # Codex CLI への組み込み手順(AGENTS.mdスニペット付き)
```

インストール先はデフォルト `~/local-llm`(`--dir` か環境変数 `LLM_HOME` で変更可)。モデル本体・ビルド成果物はリポジトリ外に置かれます。

## 重要な注意

- **thinkingの無効化**: Qwen3.6もBonsaiもthinkingモデルです。無効化しないと思考だけでトークン上限を使い切り、本体の回答が届きません。`bin/llm` は両バックエンドで無効化済み(Ollama: `think: false` / llama-server: `chat_template_kwargs.enable_thinking: false`)。
- **Bonsaiは本家llama.cpp/Ollamaでは動きません**。独自量子化(Q2_0_g128)のため、PrismML公式のllama.cppフォーク(github.com/PrismML-Eng/llama.cpp)のビルドが必要です。モデル配布元が案内する正規ランタイムですが、本家よりコミュニティ監査が薄い第三者コードをビルド・実行することは理解した上で選んでください(`setup.sh` も確認を求めます)。
- **Ollamaのコンテキスト長**: デフォルトが短いため、`bin/llm` はリクエスト毎に `num_ctx` を指定しています。エージェントのモデルを丸ごと差し替える場合(docs参照)は `OLLAMA_CONTEXT_LENGTH=65536` 以上を推奨。

## License

MIT
