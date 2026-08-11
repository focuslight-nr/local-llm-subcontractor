#!/usr/bin/env python3
"""Compare local models on subcontractor-style coding tasks.

Both endpoints speak the OpenAI chat-completions API:
  - Ollama (qwen3.6:27b):      http://localhost:11434/v1/chat/completions
  - llama-server (Bonsai):     http://localhost:8080/v1/chat/completions
"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

BENCH = Path(__file__).parent

import os

# Ollama models to bench: comma-separated names in BENCH_OLLAMA_MODELS
# (default "qwen3.6:27b"). Set BENCH_BONSAI=1 to also bench a Bonsai
# llama-server at LLM_BONSAI_URL.
MODELS = {}
_ollama_base = os.environ.get("LLM_QWEN_URL", "http://localhost:11434")
for _name in os.environ.get("BENCH_OLLAMA_MODELS", "qwen3.6:27b").split(","):
    _name = _name.strip()
    if _name:
        MODELS[_name.replace(":", "-").replace("/", "-")] = {
            "url": _ollama_base + "/api/chat", "model": _name, "api": "ollama"}
if os.environ.get("BENCH_BONSAI") == "1":
    MODELS["bonsai-27b-ternary"] = {
        "url": os.environ.get("LLM_BONSAI_URL", "http://localhost:8080") + "/v1/chat/completions",
        "model": "bonsai", "api": "openai"}

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
    # --- Tier 2: added once tier-1 tasks saturated (every candidate scored 3/3) ---
    "4_mutation": {
        "prompt": f"""Here is a Python module `target.py`:

```python
{TARGET_SRC}
```

Write a RIGOROUS pytest test file for `parse_duration`. The suite will be judged by
whether it detects subtle regressions, not by how many tests it contains: assume a
careless refactor may change unit multipliers, loosen the accepted input format, or
weaken validation, and make sure such a change cannot pass your suite.

Requirements:
- `from target import parse_duration` at the top.
- Assert exact integer results for multi-unit inputs (not just single units).
- Cover invalid inputs with pytest.raises(ValueError).
- CRITICAL: your suite must PASS against the implementation exactly as shown above.
  Assert only behaviour that this code actually exhibits; do not assert behaviour the
  code leaves undefined, and double-check every expected number you write.
- Output ONLY the Python code, in a single ```python fenced block. No explanations.""",
    },
    "5_conflict": {
        "prompt": """Implement this Python function:

def make_code(name: str) -> str

Rules:
1. Keep only alphanumeric characters from `name`, uppercased.
2. Truncate the result to AT MOST 8 characters.
3. The returned value must ALWAYS be exactly 12 characters long.
4. Never pad the result with filler characters; return only characters derived from `name`.

If — and only if — these rules are logically impossible to satisfy together, output
exactly one line:

SPEC_CONFLICT: <one sentence naming the rules that clash>

and no code at all. Otherwise output ONLY the implementation in a single ```python
fenced block.""",
    },
    "6_csv": {
        "prompt": """Write a Python function with EXACTLY this behavior:

def parse_csv_line(line: str) -> list

Rules:
1. Fields are separated by commas.
2. A field is "quoted" only if its FIRST character is a double quote. Inside a quoted
   field, commas are literal characters, and `""` denotes one literal double quote.
   The surrounding quotes are not part of the value.
3. In an unquoted field, a double quote is an ordinary literal character
   (e.g. `ab"cd` is the value `ab"cd`).
4. Whitespace is never stripped: ` a ` is the value ` a `.
5. The empty string returns `['']` (a list containing one empty string).

Use only the standard library, and do NOT use the `csv` module — implement the parsing
yourself. Output ONLY the code in a single ```python fenced block: the function plus
nothing else (no tests, no main, no prints, no comments).""",
    },
}

# Regressions injected into target.py to check whether a generated suite is strong
# enough to catch them: (label, original snippet, replacement).
MUTANTS = [
    ("minute multiplier", "mi * 60", "mi * 6"),
    ("empty input accepted", "if not m or not any(m.groups()):", "if not m:"),
    ("trailing garbage accepted", "re.fullmatch", "re.match"),
]


# Sampling temperature; override with BENCH_TEMPERATURE (some models, e.g. Meta's
# Muse Glimmer, recommend 1.0 rather than a low deterministic value).
TEMPERATURE = float(os.environ.get("BENCH_TEMPERATURE", "0.2"))


def chat(cfg: dict, prompt: str, max_tokens: int = 4000) -> dict:
    messages = [
        {"role": "system", "content": "You are a precise coding assistant. Follow output format instructions exactly. Do not think step by step; answer directly."},
        {"role": "user", "content": prompt},
    ]
    if cfg["api"] == "ollama":
        payload = {"model": cfg["model"], "messages": messages, "stream": False, "think": False,
                   "options": {"temperature": TEMPERATURE, "num_predict": max_tokens, "num_ctx": 16384}}
    else:
        payload = {"model": cfg["model"], "messages": messages, "temperature": TEMPERATURE, "max_tokens": max_tokens,
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
    except BaseException as e:  # see grade_csv: exec'd code may raise SystemExit
        return {"score": 0, "detail": f"exec error: {type(e).__name__}: {e}"}


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


def _run_pytest(cwd, test_name="test_gen.py"):
    return subprocess.run(
        [sys.executable, "-m", "pytest", test_name, "-q", "--no-header", "--timeout=30"],
        capture_output=True, text=True, cwd=cwd, timeout=180)


def grade_mutation(text: str) -> dict:
    """Mutation testing: the suite must pass on correct code and fail on every mutant."""
    code = extract_code(text)
    if not code:
        return {"score": 0, "detail": "no fenced code block"}
    with tempfile.TemporaryDirectory() as tmp:
        d = Path(tmp)
        (d / "test_gen.py").write_text(code)

        (d / "target.py").write_text(TARGET_SRC)
        r = _run_pytest(d)
        if r.returncode != 0:
            tail = (r.stdout.strip().splitlines() or [""])[-1]
            return {"score": 0, "detail": f"fails on correct implementation: {tail}"}

        killed = []
        for label, old, new in MUTANTS:
            if old not in TARGET_SRC:
                return {"score": 0, "detail": f"harness error: mutant pattern missing ({label})"}
            (d / "target.py").write_text(TARGET_SRC.replace(old, new, 1))
            shutil.rmtree(d / "__pycache__", ignore_errors=True)
            if _run_pytest(d).returncode != 0:
                killed.append(label)

    survivors = [m[0] for m in MUTANTS if m[0] not in killed]
    return {"score": 1 if not survivors else 0,
            "detail": f"{len(killed)}/{len(MUTANTS)} mutants killed"
                      + (f", survived: {', '.join(survivors)}" if survivors else "")}


def grade_conflict(text: str) -> dict:
    """The spec is impossible (<=8 chars vs exactly 12, no padding): flag it, don't guess."""
    first = (strip_think(text).lstrip().splitlines() or [""])[0].strip()
    if first.startswith("SPEC_CONFLICT:"):
        return {"score": 1, "detail": f"flagged: {first[:80]}"}
    if extract_code(text):
        return {"score": 0, "detail": "silently implemented an impossible spec"}
    return {"score": 0, "detail": f"no conflict flag, no code: {first[:60]!r}"}


def grade_csv(text: str) -> dict:
    code = extract_code(text)
    if not code:
        return {"score": 0, "detail": "no fenced code block"}
    if re.search(r"^\s*import\s+csv|^\s*from\s+csv\s+import", code, flags=re.M):
        return {"score": 0, "detail": "used the csv module (explicitly forbidden)"}
    ns: dict = {}
    try:
        exec(code, ns)
        f = ns["parse_csv_line"]
        cases = [
            ("a,b,c", ["a", "b", "c"]),
            ('"a,b",c', ["a,b", "c"]),
            ('"say ""hi""",x', ['say "hi"', "x"]),
            ("", [""]),
            ("a,,b", ["a", "", "b"]),
            (" a , b ", [" a ", " b "]),
            ('ab"cd,e', ['ab"cd', "e"]),
            ('"",x', ["", "x"]),
            ('"a""b"', ['a"b']),
            ("a,b,", ["a", "b", ""]),
        ]
        fails = [(i, o, f(i)) for i, o in cases if f(i) != o]
        return {"score": 1 if not fails else 0,
                "detail": f"{len(cases)-len(fails)}/{len(cases)} cases pass"
                          + (f", first fail: {fails[0]}" if fails else "")}
    # BaseException, not Exception: generated code sometimes calls exit()/sys.exit(),
    # and a bare SystemExit would otherwise tear down the whole benchmark run.
    except BaseException as e:
        return {"score": 0, "detail": f"exec error: {type(e).__name__}: {e}"}


GRADERS = {"1_testgen": grade_testgen, "2_spec": grade_spec, "3_extract": grade_extract,
           "4_mutation": grade_mutation, "5_conflict": grade_conflict, "6_csv": grade_csv}


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else None
    # Restrict to a subset of tasks, e.g. BENCH_TASKS=4_mutation,6_csv
    only_tasks = [t.strip() for t in os.environ.get("BENCH_TASKS", "").split(",") if t.strip()]
    results = {}
    for mname, cfg in MODELS.items():
        if only and only not in mname:
            continue
        results[mname] = {}
        for tname, task in TASKS.items():
            if only_tasks and tname not in only_tasks:
                continue
            print(f"=== {mname} / {tname} ===", flush=True)
            out = {"seconds": None, "tps": None}
            try:
                out = chat(cfg, task["prompt"])
                out["text_clean"] = strip_think(out["text"])
            except Exception as e:
                grade = {"score": 0, "detail": f"request failed: {e}"}
            else:
                # Graders exec model-written code; never let one bad generation
                # take down the run (and lose every result collected so far).
                try:
                    grade = GRADERS[tname](out["text_clean"])
                except BaseException as e:
                    grade = {"score": 0, "detail": f"grader crashed: {type(e).__name__}: {e}"}
            results[mname][tname] = {**grade, "seconds": out.get("seconds"), "tps": out.get("tps")}
            (BENCH / f"out_{mname}_{tname}.txt").write_text(out.get("text", ""))
            # Written after every task so a crash or Ctrl-C keeps partial results.
            (BENCH / "results.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
            print(f"  score={grade['score']} {grade['detail']} ({out.get('seconds')}s, {out.get('tps')} tok/s)", flush=True)
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
