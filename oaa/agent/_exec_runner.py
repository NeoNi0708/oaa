"""Exec runner — in-process Python execution for do_code_exec.

Called as: python _exec_runner.py <code_file_path> <result_file_path>

More permissive than _sandbox_runner.py — allows most imports but disables
specific dangerous functions (os.system, subprocess.Popen, shutil.rmtree).
Writes the ``result`` variable as JSON to <result_file_path>.
"""
import builtins
import json
import sys
import traceback

# ---- Dangerous-function patches (per-module) ----

_DANGEROUS: dict[str, list[str]] = {
    "os": [
        "system", "popen", "execl", "execle", "execlp", "execlpe",
        "execv", "execve", "execvp", "execvpe",
        "spawnl", "spawnle", "spawnlp", "spawnlpe",
        "spawnv", "spawnve", "spawnvp", "spawnvpe",
    ],
    "subprocess": [
        "Popen", "run", "call", "check_call", "check_output",
        "getoutput", "getstatusoutput",
    ],
    "shutil": ["rmtree"],
}


def _patch_module(mod_name: str) -> None:
    """Replace dangerous functions on *mod_name* with a stub that raises."""
    try:
        mod = __import__(mod_name)
    except ImportError:
        return
    for func_name in _DANGEROUS.get(mod_name, []):
        if hasattr(mod, func_name):
            original_name = func_name

            def _raise(*args, **kwargs):
                raise RuntimeError(
                    f"{mod_name}.{original_name}() is disabled in code_exec. "
                    f"Use code_run or shell_run for shell commands."
                )
            setattr(mod, func_name, _raise)


for _mod in ("os", "subprocess", "shutil"):
    _patch_module(_mod)

# ---- Restricted builtins (remove dynamic code execution) ----

_SAFE_BUILTINS: dict = {
    k: v for k, v in builtins.__dict__.items()
    if k not in {"exec", "eval", "compile"}
}

# ---- Execute user code ----

if len(sys.argv) < 3:
    print("ERROR: Usage: _exec_runner.py <code_file> <result_file>", file=sys.stderr)
    sys.exit(1)

code_path = sys.argv[1]
result_path = sys.argv[2]

try:
    with open(code_path, "r", encoding="utf-8") as f:
        code = f.read()
except FileNotFoundError:
    print(f"ERROR: code file not found: {code_path}", file=sys.stderr)
    sys.exit(1)

_globals: dict = {"__builtins__": _SAFE_BUILTINS, "__name__": "__exec__"}
try:
    exec(code, _globals)
except SystemExit:
    pass
except Exception:
    traceback.print_exc(file=sys.stderr)
    sys.exit(1)

# Extract the ``result`` variable and write it as JSON
result_value = _globals.get("result")
try:
    with open(result_path, "w", encoding="utf-8") as f:
        json.dump({"result": result_value}, f, ensure_ascii=False, default=str)
except Exception as exc:
    print(f"ERROR: failed to write result: {exc}", file=sys.stderr)
    sys.exit(1)
