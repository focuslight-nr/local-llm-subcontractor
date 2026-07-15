#!/usr/bin/env python3
"""Compare local models on subcontractor-style coding tasks.

Both endpoints speak the OpenAI chat-completions API:
  - Ollama (qwen3.6:27b):      http://localhost:11434/v1/chat/completions
  - llama-server (Bonsai):     http://localhost:8080/v1/chat/completions
"""
import json
import re
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BENCH = Path(__file__).parent

import os

MODELS = {
    "qwen3.6-27b-q4": {
        "url": os.environ.get("LLM_QWEN_URL", "http://localhost:11434") + "/api/chat",
        "model": os.environ.get("LLM_QWEN_MODEL", "qwen3.6:27b"), "api": "ollama"},
    "bonsai-27b-ternary": {
        "url": os.environ.get("LLM_BONSAI_URL", "http://localhost:8080") + "/v1/chat/completions",
        "model": "bonsai", "api": "openai"},
}

TARGET_SRC = (BENCH / "target.py").read_text()
LOG_SRC = (BENCH / "app.log").read_text()

TASKS = {
    "1_testgen": {
        "prompt": f"""Here is a Python module `target.py`:

```python
{TARGET_SRC}
```

Write a pytest test file for `parse_duration`. Requirements:
- `from target import parse_duration` at the top.
- At least 8 test functions covering valid inputs, edge cases, and invalid inputs (use pytest.raises).
- Output ONLY the Python code, in a single ```python fenced block. No explanations.""",
    },
    "2_spec": {
        "prompt": """Write a Python function with EXACTLY this behavior:

def slugify(title: str) -> str

Rules:
1. Lowercase the input.
2. Replace every run of characters that are not a-z or 0-9 with a single hyphen.
3. Strip leading/trailing hyphens.
4. If the result is empty, return "untitled".
5. Truncate to at most 40 characters, then strip trailing hyphens again.

Use only the standard library. Output ONLY the code in a single ```python fenced block: the function plus nothing else (no tests, no main, no prints, no comments).""",
    },
    "3_extract": {
        "prompt": f"""Below is a server log. Extract ALL ERROR-level entries and return ONLY a JSON array (no fenced block, no prose). Each element must have exactly these keys:
- "time": the ISO timestamp string
- "component": the logger name (e.g. "api.orders")
- "message": the err= value if present, otherwise the log message text
- "request_id": the request_id value or null

Log:
{LOG_SRC}""",
    },
}


def chat(cfg: dict, prompt: str, max_tokens: int = 4000) -> dict:
    messages = [
        {"role": "system", "content": "You are a precise coding assistant. Follow output format instructions exactly. Do not think step by step; answer directly."},
        {"role": "user", "content": prompt},
    ]
    if cfg["api"] == "ollama":
        payload = {"model": cfg["model"], "messages": messages, "stream": False, "think": False,
                   "options": {"temperature": 0.2, "num_predict": max_tokens, "num_ctx": 16384}}
    else:
        payload = {"model": cfg["model"], "messages": messages, "temperature": 0.2, "max_tokens": max_tokens,
                   "chat_template_kwargs": {"enable_thinking": False}}
    req = urllib.request.Request(cfg["url"], data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        resp = json.load(r)
    dt = time.time() - t0
    if cfg["api"] == "ollama":
        text = resp["message"]["content"]
        ntok = resp.get("eval_count", 0)
    else:
        text = resp["choices"][0]["message"]["content"]
        ntok = resp.get("usage", {}).get("completion_tokens", 0)
    return {"text": text, "seconds": round(dt, 1), "completion_tokens": ntok,
            "tps": round(ntok / dt, 1) if dt else None}


def strip_think(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()


def extract_code(text: str):
    m = re.findall(r"```(?:python)?\n(.*?)```", text, flags=re.S)
    return m[-1].strip() if m else None


def grade_testgen(text: str) -> dict:
    code = extract_code(text)
    if not code:
        return {"score": 0, "detail": "no fenced code block"}
    p = BENCH / "_gen_test.py"
    p.write_text(code)
    r = subprocess.run([sys.executable, "-m", "pytest", str(p), "-q", "--no-header", "-x", "--timeout=30"],
                       capture_output=True, text=True, cwd=BENCH, timeout=120)
    tail = (r.stdout.strip().splitlines() or [""])[-1]
    n_tests = len(re.findall(r"^def test_", code, flags=re.M))
    return {"score": 1 if r.returncode == 0 and n_tests >= 8 else 0,
            "detail": f"{n_tests} tests, pytest: {tail}"}


def grade_spec(text: str) -> dict:
    code = extract_code(text)
    if not code:
        return {"score": 0, "detail": "no fenced code block"}
    ns: dict = {}
    try:
        exec(code, ns)
        f = ns["slugify"]
        cases = [
            ("Hello, World!", "hello-world"),
            ("  --Multiple   Spaces & Symbols!!  ", "multiple-spaces-symbols"),
            ("日本語タイトル", "untitled"),
            ("", "untitled"),
            ("A" * 100, "a" * 40),
            ("foo-bar_baz 42", "foo-bar-baz-42"),
            ("x" * 39 + "!Y", ("x" * 39 + "-y")[:40].rstrip("-")),
        ]
        fails = [(i, o, f(i)) for i, o in cases if f(i) != o]
        extra_defs = len(re.findall(r"^(?:def |class )", code, flags=re.M))
        return {"score": 1 if not fails and extra_defs == 1 else 0,
                "detail": f"{len(cases)-len(fails)}/{len(cases)} cases pass" + (f", first fail: {fails[0]}" if fails else "") + (f", extra defs: {extra_defs-1}" if extra_defs != 1 else "")}
    except Exception as e:
        return {"score": 0, "detail": f"exec error: {e}"}


def grade_extract(text: str) -> dict:
    t = strip_think(text)
    m = re.search(r"```(?:json)?\n(.*?)```", t, flags=re.S)
    raw = m.group(1) if m else t
    fmt_ok = m is None  # spec said: no fenced block
    try:
        data = json.loads(raw)
    except Exception as e:
        return {"score": 0, "detail": f"invalid JSON: {e}"}
    if not isinstance(data, list) or len(data) != 5:
        return {"score": 0, "detail": f"expected 5 ERROR entries, got {len(data) if isinstance(data, list) else type(data).__name__}"}
    keys_ok = all(set(d) == {"time", "component", "message", "request_id"} for d in data)
    comps = [d.get("component") for d in data]
    comp_ok = comps == ["api.orders", "api.orders", "worker.payments", "db.pool", "api.orders"]
    rid_ok = [d.get("request_id") for d in data] == ["req_a81f", "req_a81f", "req_c2d4", None, "req_f7b0"]
    score = 1 if keys_ok and comp_ok and rid_ok else 0
    return {"score": score, "detail": f"keys_ok={keys_ok} components_ok={comp_ok} request_ids_ok={rid_ok} no_fence={fmt_ok}"}


GRADERS = {"1_testgen": grade_testgen, "2_spec": grade_spec, "3_extract": grade_extract}


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    results = {}
    for mname, cfg in MODELS.items():
        if only and only not in mname:
            continue
        results[mname] = {}
        for tname, task in TASKS.items():
            print(f"=== {mname} / {tname} ===", flush=True)
            try:
                out = chat(cfg, task["prompt"])
                out["text_clean"] = strip_think(out["text"])
                grade = GRADERS[tname](out["text_clean"])
            except Exception as e:
                out, grade = {"seconds": None, "tps": None}, {"score": 0, "detail": f"request failed: {e}"}
            results[mname][tname] = {**grade, "seconds": out.get("seconds"), "tps": out.get("tps")}
            (BENCH / f"out_{mname}_{tname}.txt").write_text(out.get("text", ""))
            print(f"  score={grade['score']} {grade['detail']} ({out.get('seconds')}s, {out.get('tps')} tok/s)", flush=True)
    (BENCH / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
