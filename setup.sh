#!/usr/bin/env bash
# local-llm-subcontractor setup
#
# Usage:
#   ./setup.sh [--model flint|qwen|bonsai|both] [--dir PATH] [--yes]
#
#   --model   Which model to install; "both" = flint + bonsai
#             (default: suggest from RAM size)
#   --dir     Where to keep the Ollama log if this script starts the server
#             (default: ~/local-llm, or $LLM_HOME). Models live in Ollama's own store.
#   --yes     Non-interactive: accept all confirmations
set -euo pipefail

FLINT_MODEL="${FLINT_MODEL:-flint:latest}"  # the default; needs Ollama >= 0.40.8
QWEN_MODEL="${QWEN_MODEL:-qwen3.6:27b}"   # override e.g. QWEN_MODEL=qwen3.6:35b-a3b (needs ~32GB+ RAM)
# Bonsai 2 (PrismML's ternary build of Qwen3.8-27B) ships in the Ollama library
# since Oct 2026, so it no longer needs PrismML's llama.cpp fork built from source.
BONSAI_MODEL="${BONSAI_MODEL:-bonsai-2:27b}"

MODEL=""
LLM_HOME="${LLM_HOME:-$HOME/local-llm}"
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --dir)   LLM_HOME="$2"; shift 2 ;;
    --yes)   ASSUME_YES=1; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
done

info()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn()  { printf '\033[1;33mWARN:\033[0m %s\n' "$*"; }
die()   { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

confirm() {
  [[ $ASSUME_YES -eq 1 ]] && return 0
  read -r -p "$1 [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]]
}

OS="$(uname -s)"
case "$OS" in
  Darwin|Linux) ;;
  *) die "unsupported OS: $OS (macOS / Linux only)" ;;
esac

ram_gb() {
  if [[ "$OS" == "Darwin" ]]; then
    echo $(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
  else
    awk '/MemTotal/ {printf "%d", $2 / 1024 / 1024}' /proc/meminfo
  fi
}

# --- pick model from RAM if not specified -----------------------------------
RAM="$(ram_gb)"
if [[ -z "$MODEL" ]]; then
  if (( RAM >= 32 )); then MODEL=flint; else MODEL=bonsai; fi
  info "RAM ${RAM}GB detected -> suggesting model: $MODEL"
  if [[ $ASSUME_YES -eq 0 ]]; then
    read -r -p "Install '$MODEL'? (flint/qwen/bonsai/both) [$MODEL] " ans
    MODEL="${ans:-$MODEL}"
  fi
fi
case "$MODEL" in flint|qwen|bonsai|both) ;; *) die "invalid --model: $MODEL" ;; esac
if [[ "$MODEL" == "bonsai" || "$MODEL" == "both" ]] && (( RAM < 12 )); then
  warn "RAM ${RAM}GB is tight even for Bonsai (needs ~10GB at 32K context)."
fi

need_cmd() { command -v "$1" >/dev/null 2>&1; }

install_pkg() {
  if [[ "$OS" == "Darwin" ]]; then
    need_cmd brew || die "Homebrew required on macOS: https://brew.sh"
    brew install "$@"
  elif need_cmd apt-get; then
    sudo apt-get update -qq && sudo apt-get install -y "$@"
  elif need_cmd dnf; then
    sudo dnf install -y "$@"
  else
    die "no supported package manager found; install manually: $*"
  fi
}

mkdir -p "$LLM_HOME"
info "install dir: $LLM_HOME"

# --- Ollama (shared by both models) ---------------------------------------------
ensure_ollama() {
  if ! need_cmd ollama; then
    info "installing Ollama"
    if [[ "$OS" == "Darwin" ]]; then
      install_pkg ollama
    else
      confirm "Run the official Ollama install script (curl https://ollama.com/install.sh | sh)?" \
        || die "Ollama is required; install it and re-run"
      curl -fsSL https://ollama.com/install.sh | sh
    fi
  fi
  if ! curl -s --max-time 2 localhost:11434/api/version >/dev/null; then
    info "starting ollama serve (background)"
    nohup ollama serve > "$LLM_HOME/ollama.log" 2>&1 &
    for _ in $(seq 1 20); do
      curl -s --max-time 2 localhost:11434/api/version >/dev/null && break
      sleep 1
    done
    curl -s --max-time 2 localhost:11434/api/version >/dev/null || die "ollama serve did not come up"
  fi
  # Upgrading the package does not restart a running server, so a new model can
  # still be refused by the old one. Catch that before a long pull fails.
  if ollama --version 2>&1 | grep -q "client version is"; then
    warn "the running Ollama server is older than the installed one: $(ollama --version 2>&1 | tr '\n' ' ')"
    warn "restart it (macOS: brew services restart ollama) so new models can be pulled"
  fi
}

ollama_pull() {
  local out
  if ! out="$(ollama pull "$1" 2>&1 | tee /dev/stderr)"; then
    if grep -q "newer version of Ollama" <<<"$out"; then
      die "$1 needs a newer Ollama: upgrade it, then restart the server (macOS: brew upgrade ollama && brew services restart ollama)"
    fi
    die "ollama pull $1 failed"
  fi
}

# --- flint (default) ----------------------------------------------------------
setup_flint() {
  ensure_ollama
  info "pulling $FLINT_MODEL (~22GB, skips if up to date)"
  warn "flint needs Ollama >= 0.40.8; an older one rejects the pull with HTTP 412."
  ollama_pull "$FLINT_MODEL"
}

# --- qwen ---------------------------------------------------------------------
setup_qwen() {
  ensure_ollama
  info "pulling $QWEN_MODEL (~17GB, skips if up to date)"
  ollama_pull "$QWEN_MODEL"
}

# --- bonsai --------------------------------------------------------------------
setup_bonsai() {
  ensure_ollama
  info "pulling $BONSAI_MODEL (~8GB, skips if up to date)"
  ollama_pull "$BONSAI_MODEL"
  # Earlier versions of this kit built PrismML's llama.cpp fork into LLM_HOME.
  if [[ -d "$LLM_HOME/llama.cpp" || -n "$(ls "$LLM_HOME"/models/*Bonsai*.gguf 2>/dev/null)" ]]; then
    info "the old fork build is no longer used; safe to delete: $LLM_HOME/llama.cpp $LLM_HOME/models/*Bonsai*.gguf"
  fi
}

[[ "$MODEL" == "flint"  || "$MODEL" == "both" ]] && setup_flint
[[ "$MODEL" == "qwen" ]] && setup_qwen
[[ "$MODEL" == "bonsai" || "$MODEL" == "both" ]] && setup_bonsai

info "done. smoke test:"
[[ "$MODEL" == "flint" || "$MODEL" == "both" ]] && echo "  echo 'Reply with exactly: OK' | $(dirname "$0")/bin/llm"
[[ "$MODEL" == "qwen" ]] && echo "  echo 'Reply with exactly: OK' | $(dirname "$0")/bin/llm -m qwen"
[[ "$MODEL" == "bonsai" || "$MODEL" == "both" ]] && echo "  echo 'Reply with exactly: OK' | $(dirname "$0")/bin/llm -m bonsai"
echo "Agent integration: docs/claude-code.md / docs/codex.md"
