#!/usr/bin/env bash
# local-llm-subcontractor setup
#
# Usage:
#   ./setup.sh [--model qwen|bonsai|both] [--dir PATH] [--yes]
#
#   --model   Which backend to install (default: suggest from RAM size)
#   --dir     Install location for models/runtimes (default: ~/local-llm, or $LLM_HOME)
#   --yes     Non-interactive: accept all confirmations
set -euo pipefail

QWEN_MODEL="qwen3.6:27b"
BONSAI_REPO="https://github.com/PrismML-Eng/llama.cpp"
BONSAI_GGUF_URL="https://huggingface.co/prism-ml/Ternary-Bonsai-27B-gguf/resolve/main/Ternary-Bonsai-27B-Q2_0.gguf"
BONSAI_GGUF="Ternary-Bonsai-27B-Q2_0.gguf"

MODEL=""
LLM_HOME="${LLM_HOME:-$HOME/local-llm}"
ASSUME_YES=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model) MODEL="$2"; shift 2 ;;
    --dir)   LLM_HOME="$2"; shift 2 ;;
    --yes)   ASSUME_YES=1; shift ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
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
  if (( RAM >= 32 )); then MODEL=qwen; else MODEL=bonsai; fi
  info "RAM ${RAM}GB detected -> suggesting model: $MODEL"
  if [[ $ASSUME_YES -eq 0 ]]; then
    read -r -p "Install '$MODEL'? (qwen/bonsai/both) [$MODEL] " ans
    MODEL="${ans:-$MODEL}"
  fi
fi
case "$MODEL" in qwen|bonsai|both) ;; *) die "invalid --model: $MODEL" ;; esac
if [[ "$MODEL" != "qwen" ]] && (( RAM < 12 )); then
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

mkdir -p "$LLM_HOME/models"
info "install dir: $LLM_HOME"

# --- qwen (Ollama) -----------------------------------------------------------
setup_qwen() {
  if ! need_cmd ollama; then
    info "installing Ollama"
    if [[ "$OS" == "Darwin" ]]; then
      install_pkg ollama
    else
      confirm "Run the official Ollama install script (curl https://ollama.com/install.sh | sh)?" \
        || die "Ollama required for qwen; install it and re-run"
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
  info "pulling $QWEN_MODEL (~17GB, skips if up to date)"
  ollama pull "$QWEN_MODEL"
}

# --- bonsai (PrismML llama.cpp fork) ------------------------------------------
setup_bonsai() {
  warn "Bonsai's ternary format needs PrismML's llama.cpp FORK ($BONSAI_REPO)."
  warn "This is the model vendor's official runtime, but it is third-party code"
  warn "with less community review than mainline llama.cpp."
  confirm "Clone and build it?" || die "bonsai setup declined"

  for c in git cmake; do need_cmd "$c" || install_pkg "$c"; done
  need_cmd cc || { [[ "$OS" == "Linux" ]] && install_pkg build-essential; }

  if [[ ! -d "$LLM_HOME/llama.cpp" ]]; then
    info "cloning PrismML llama.cpp fork"
    git clone --depth 1 "$BONSAI_REPO" "$LLM_HOME/llama.cpp"
  fi
  if [[ ! -x "$LLM_HOME/llama.cpp/build/bin/llama-server" ]]; then
    info "building llama-server (a few minutes)"
    cmake -S "$LLM_HOME/llama.cpp" -B "$LLM_HOME/llama.cpp/build" -DCMAKE_BUILD_TYPE=Release
    cmake --build "$LLM_HOME/llama.cpp/build" -j "$(getconf _NPROCESSORS_ONLN)" --target llama-server llama-cli
  fi

  if [[ ! -f "$LLM_HOME/models/$BONSAI_GGUF" ]]; then
    info "downloading $BONSAI_GGUF (~6.7GB, resumable)"
    curl -fL -C - -o "$LLM_HOME/models/$BONSAI_GGUF" "$BONSAI_GGUF_URL"
  fi
  head -c 4 "$LLM_HOME/models/$BONSAI_GGUF" | grep -q GGUF || die "downloaded file is not a GGUF"
  info "bonsai ready. start it with: LLM_HOME=$LLM_HOME $(dirname "$0")/bin/serve-bonsai"
}

[[ "$MODEL" == "qwen"   || "$MODEL" == "both" ]] && setup_qwen
[[ "$MODEL" == "bonsai" || "$MODEL" == "both" ]] && setup_bonsai

info "done. smoke test:"
[[ "$MODEL" != "bonsai" ]] && echo "  echo 'Reply with exactly: OK' | $(dirname "$0")/bin/llm -m qwen"
[[ "$MODEL" != "qwen"   ]] && echo "  LLM_HOME=$LLM_HOME $(dirname "$0")/bin/serve-bonsai &   # then:"
[[ "$MODEL" != "qwen"   ]] && echo "  echo 'Reply with exactly: OK' | $(dirname "$0")/bin/llm -m bonsai"
echo "Agent integration: docs/claude-code.md / docs/codex.md"
