# Codex CLI への組み込み

## パターンA: 下請けワークフロー(推奨)

考え方は Claude Code と同じです([claude-code.md](claude-code.md) 参照)。
Codex はプロジェクトの `AGENTS.md` を読むので、そこに同じルールを書きます:

```markdown
## Local LLM subcontractor

Delegate mechanical generation tasks to the local LLM to save tokens:
test generation, boilerplate, and summarizing/structuring large logs or docs
before reading them.

Do NOT delegate: cross-repo changes, design decisions, debugging. Also keep tests
that lean on many internal APIs (E2E, inherited base test classes, fixture graphs):
their definitions will not fit in a spec.

Procedure:
1. Write a complete spec (inputs/outputs, edge cases, exact output format).
   The local model cannot see the repository, so it invents any name the spec
   does not contain. Paste the real code the test touches: signatures, dataclass
   definitions, exceptions with their module paths, fixtures to reuse, and the
   exact import lines as code, not comments (keep it under ~300 lines). Tell it
   to use only names in the spec and to write `# MISSING: <what>` instead of
   guessing; on a MISSING marker, add the definition and resend (this does not
   count as a retry).
2. Pipe it to `<REPO_PATH>/bin/llm -m qwen`.
3. Gate the output locally: pytest/lint for code, JSON validation for extraction.
4. On failure, retry via the local LLM with the error attached (max 2 retries).
5. Review only gated output yourself.
6. Any non-zero exit means there is no usable answer (stdout is empty): 2 = could not
   connect (server down, or a sandbox blocking localhost), 3 = the server returned an
   error or timed out (often transient during an upgrade/restart - retry), 4 = the model
   returned an empty answer. Read stderr for the cause; if it cannot be fixed, or
   retries are exhausted, do it yourself.
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
- Bonsai 2 もOllamaで動くので、同じプロバイダで `model = "bonsai-2:27b"` を指定できます。
  ただし推論モード前提のモデルなので、思考にトークンと時間を食われる点に注意
