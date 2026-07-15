# Codex CLI への組み込み

## パターンA: 下請けワークフロー(推奨)

考え方は Claude Code と同じです([claude-code.md](claude-code.md) 参照)。
Codex はプロジェクトの `AGENTS.md` を読むので、そこに同じルールを書きます:

```markdown
## Local LLM subcontractor

Delegate mechanical generation tasks to the local LLM to save tokens:
test generation, boilerplate, and summarizing/structuring large logs or docs
before reading them.

Do NOT delegate: cross-repo changes, design decisions, debugging.

Procedure:
1. Write a complete spec (inputs/outputs, edge cases, exact output format).
2. Pipe it to `<REPO_PATH>/bin/llm -m qwen`.
3. Gate the output locally: pytest/lint for code, JSON validation for extraction.
4. On failure, retry via the local LLM with the error attached (max 2 retries).
5. Review only gated output yourself.
6. If the server is unreachable (exit code 2) or retries are exhausted, do it yourself.
```

## パターンB: モデル丸ごと差し替え

Ollama は OpenAI 互換 API(`/v1`)も話すので、Codex のモデルプロバイダとして
直接指定できます。`~/.codex/config.toml`:

```toml
[model_providers.ollama]
name = "Ollama (local)"
base_url = "http://localhost:11434/v1"

[profiles.local]
model_provider = "ollama"
model = "qwen3.6:27b"
```

起動: `codex --profile local`

注意点は Claude Code と同じです:
- `OLLAMA_CONTEXT_LENGTH=65536` 以上にしないとエージェントループが回りません
- 27Bクラスにフルのエージェントハーネスはかなり厳しいので実験用途向け
- Bonsai(llama-server)も OpenAI 互換なので `base_url = "http://localhost:8080/v1"`
  で同様に差し替え可能ですが、thinking をリクエスト側で無効化できない
  ハーネスでは思考にトークンを食われる点に注意
