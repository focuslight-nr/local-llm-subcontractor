#!/usr/bin/env python3
"""Watch the official Ollama library for new models and benchmark the runnable ones.

Runs unattended (launchd). Each pass:
  1. diff the library index against the previous snapshot
  2. for each new entry, pick the largest tag that fits MAX_PULL_GB
  3. pull it, bench it twice (direct and thinking), then report

Deliberately conservative: it skips embedding/audio/vision-only models, caps how
much it downloads per run, and refuses to start when the disk is low.

It also watches PrismML's Hugging Face org for new Bonsai GGUFs. Those never
reach the Ollama library (they need PrismML's llama.cpp fork), so they are
reported only: evaluating one means building third-party code, possibly at a
newer fork commit than setup.sh pins, and that should not happen unattended.

State and reports live in ~/.local/state/llm-model-watch/.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

STATE = Path.home() / ".local/state/llm-model-watch"
SNAPSHOT = STATE / "library.json"
PRISM_SNAPSHOT = STATE / "prismml.json"
LOG = STATE / "watch.log"
REPORTS = STATE / "reports"
BENCH = Path.home() / "GitHub/local-llm/bench/run_bench.py"

MAX_PULL_GB = float(os.environ.get("WATCH_MAX_PULL_GB", "30"))   # skip bigger tags
MIN_PULL_GB = float(os.environ.get("WATCH_MIN_PULL_GB", "4"))    # skip toys
MIN_FREE_GB = float(os.environ.get("WATCH_MIN_FREE_GB", "120"))  # refuse when low
MAX_PER_RUN = int(os.environ.get("WATCH_MAX_PER_RUN", "2"))      # avoid a flood

# Model families that are not chat/code subcontractors.
SKIP = re.compile(r"embed|bge-|all-minilm|nomic|snowflake|paraphrase|rerank|"
                  r"whisper|asr|tts|speech|ocr|clip|moondream|sd-|stable-?diffusion",
                  re.I)


def log(msg):
    STATE.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {msg}"
    with LOG.open("a") as f:
        f.write(line + "\n")
    print(line, flush=True)


def notify(title, message):
    """Best-effort macOS notification; never fatal."""
    try:
        subprocess.run(["osascript", "-e",
                        f'display notification {json.dumps(message)} with title {json.dumps(title)}'],
                       capture_output=True, timeout=15)
    except Exception:
        pass


def fetch(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "llm-model-watch/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def library_index():
    html = fetch("https://ollama.com/library")
    names = set(re.findall(r'href="/library/([a-z0-9._-]+)"', html))
    if len(names) < 50:                     # page shape changed or we got blocked
        raise RuntimeError(f"library index looks wrong ({len(names)} entries)")
    return sorted(names)


def tags_for(name):
    """Return [(tag, gb)] for a library entry, largest first."""
    html = fetch(f"https://ollama.com/library/{name}/tags")
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.S)
    text = re.sub(r"[ \t]+", " ", re.sub(r"<[^>]+>", " ", html))
    out = {}
    for tag, num, unit in re.findall(rf"({re.escape(name)}:[a-z0-9._-]+)\s+([\d.]+)(GB|MB)", text):
        gb = float(num) / 1024 if unit == "MB" else float(num)
        out[tag] = gb
    return sorted(out.items(), key=lambda kv: -kv[1])


def pick_tag(name):
    """The tag a person would actually pull: prefer plain ones like `:27b` or
    `:latest` over quantisation variants like `:27b-mtp-q8_0`, then take the
    largest that still fits the pull cap."""
    fitting = [(t, gb) for t, gb in tags_for(name) if MIN_PULL_GB <= gb <= MAX_PULL_GB]
    if not fitting:
        return None, None
    plain = [(t, gb) for t, gb in fitting if "-" not in t.split(":", 1)[1]]
    return (plain or fitting)[0]


def free_gb():
    return shutil.disk_usage(Path.home()).free / 1024**3


def run(cmd, timeout, env=None):
    e = {**os.environ, **(env or {})}
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=e)


def bench(tag, think):
    """Run the full suite; return (raw stdout, parsed {task: score})."""
    env = {"BENCH_OLLAMA_MODELS": tag}
    if think:
        env |= {"BENCH_THINK": "1", "BENCH_TEMPERATURE": "0.6",
                "BENCH_MAX_TOKENS": "12000", "BENCH_NUM_CTX": "32768"}
    r = run([sys.executable, str(BENCH)], timeout=7200, env=env)
    scores = dict(re.findall(r"=== \S+ / (\S+) ===\n\s+score=(\d)", r.stdout))
    return r.stdout, scores


def evaluate(name):
    tag, gb = pick_tag(name)
    if not tag:
        log(f"  {name}: no tag within {MIN_PULL_GB}-{MAX_PULL_GB}GB, skipping")
        return None
    if free_gb() - gb < MIN_FREE_GB:
        log(f"  {name}: only {free_gb():.0f}GB free, skipping {tag} ({gb}GB)")
        return None

    log(f"  {name}: pulling {tag} ({gb}GB)")
    r = run(["ollama", "pull", tag], timeout=7200)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or ["unknown error"]
        log(f"  {name}: pull failed - {tail[0]}")
        return {"tag": tag, "gb": gb, "error": tail[0]}

    log(f"  {tag}: benching (direct)")
    direct_out, direct = bench(tag, think=False)
    log(f"  {tag}: direct {sum(int(v) for v in direct.values())}/{len(direct)}")

    log(f"  {tag}: benching (thinking)")
    think_out, thinking = bench(tag, think=True)
    log(f"  {tag}: thinking {sum(int(v) for v in thinking.values())}/{len(thinking)}")

    return {"tag": tag, "gb": gb, "direct": direct, "thinking": thinking,
            "direct_out": direct_out, "thinking_out": think_out}


def write_report(results):
    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{datetime.now():%Y-%m-%d}.md"
    with path.open("w") as f:
        f.write(f"# New models benchmarked {datetime.now():%Y-%m-%d}\n\n")
        f.write("Baseline: qwen3.6:27b scores 6/6 direct in ~275s.\n")
        f.write("Thinking runs use temperature 0.6 — check the vendor's own\n"
                "recommendation before drawing conclusions.\n\n")
        for res in results:
            f.write(f"## {res['tag']} ({res['gb']}GB)\n\n")
            if "error" in res:
                f.write(f"Pull failed: {res['error']}\n\n")
                continue
            for label, scores in (("direct", res["direct"]), ("thinking", res["thinking"])):
                total = sum(int(v) for v in scores.values())
                f.write(f"- **{label}**: {total}/{len(scores)} — "
                        + ", ".join(f"{k} {'PASS' if v == '1' else 'FAIL'}"
                                    for k, v in sorted(scores.items())) + "\n")
            f.write("\n<details><summary>raw output</summary>\n\n```\n")
            f.write(res["direct_out"][-3000:] + "\n" + res["thinking_out"][-3000:])
            f.write("\n```\n</details>\n\n")
        f.write("Remove a model you do not want to keep: `ollama rm <tag>`\n")
    return path


def check_ollama():
    try:
        current = library_index()
    except Exception as e:
        log(f"library fetch failed: {e}")
        return 1

    if not SNAPSHOT.exists():
        SNAPSHOT.write_text(json.dumps(current))
        log(f"seeded snapshot with {len(current)} models (no benchmarking on first run)")
        return 0

    previous = set(json.loads(SNAPSHOT.read_text()))
    new = [n for n in current if n not in previous]
    # Record the snapshot before any slow work, so a crash cannot re-trigger pulls.
    SNAPSHOT.write_text(json.dumps(current))

    if not new:
        log("no new models")
        return 0

    interesting = [n for n in new if not SKIP.search(n)]
    log(f"new: {', '.join(new)}" + (f" (skipping non-chat: "
        f"{', '.join(n for n in new if SKIP.search(n))})" if len(interesting) < len(new) else ""))

    if free_gb() < MIN_FREE_GB:
        log(f"only {free_gb():.0f}GB free, not benchmarking")
        notify("New local models", f"{', '.join(interesting)} — disk too low to test")
        return 0

    results = []
    for name in interesting[:MAX_PER_RUN]:
        try:
            res = evaluate(name)
        except Exception as e:
            log(f"  {name}: {type(e).__name__}: {e}")
            res = None
        if res:
            results.append(res)

    if results:
        path = write_report(results)
        summary = "; ".join(
            f"{r['tag']}: " + (r.get("error") and "pull failed" or
                               f"{sum(int(v) for v in r['thinking'].values())}/6 thinking")
            for r in results)
        log(f"report: {path}")
        notify("New local models benchmarked", summary)
    return 0


def prismml_repos():
    """All model repos in the prism-ml HF org, newest first (so a new one is
    always inside the page even if older ones fall off the end)."""
    data = json.loads(fetch("https://huggingface.co/api/models"
                            "?author=prism-ml&sort=createdAt&direction=-1&limit=100"))
    repos = [m["modelId"] for m in data]
    if not repos:                          # API shape changed or we got blocked
        raise RuntimeError("prism-ml listing came back empty")
    return repos


def check_prismml():
    current = prismml_repos()
    if not PRISM_SNAPSHOT.exists():
        PRISM_SNAPSHOT.write_text(json.dumps(current))
        log(f"prism-ml: seeded snapshot with {len(current)} repos")
        return 0

    previous = set(json.loads(PRISM_SNAPSHOT.read_text()))
    new = [r for r in current if r not in previous]
    PRISM_SNAPSHOT.write_text(json.dumps(current))
    if not new:
        log("prism-ml: no new repos")
        return 0

    log(f"prism-ml: new {', '.join(new)}")
    # Only llama.cpp-loadable text models matter to setup.sh --model bonsai.
    gguf = [r for r in new if "gguf" in r.lower() and "image" not in r.lower()]
    if not gguf:
        return 0

    REPORTS.mkdir(parents=True, exist_ok=True)
    path = REPORTS / f"{datetime.now():%Y-%m-%d}.md"
    fresh = not path.exists()
    with path.open("a") as f:
        if fresh:
            f.write(f"# Model watch {datetime.now():%Y-%m-%d}\n\n")
        f.write("## New PrismML Bonsai GGUFs (not auto-tested)\n\n")
        for r in gguf:
            f.write(f"- [{r}](https://huggingface.co/{r})\n")
        f.write("\nThese need PrismML's llama.cpp fork, so they are reported only. To evaluate:\n"
                "build the fork at a commit that loads the new file, bench with\n"
                "`BENCH_BONSAI=1`, and bump `BONSAI_COMMIT` / `BONSAI_GGUF` in setup.sh\n"
                "together if adopting it.\n\n")
    log(f"prism-ml: report {path}")
    notify("New Bonsai release", f"{', '.join(gguf)} - needs a fork build to test")
    return 0


def main():
    STATE.mkdir(parents=True, exist_ok=True)
    rc = 0
    # Independent sources: one failing must not stop the other.
    for check in (check_ollama, check_prismml):
        try:
            rc |= check() or 0
        except Exception as e:
            log(f"{check.__name__} failed: {type(e).__name__}: {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
