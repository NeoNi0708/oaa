"""Exec runner — in-process Python execution for do_code_exec.

Called as: python _exec_runner.py [--timeout N] <code_file_path> <result_file_path>

More permissive than _sandbox_runner.py — allows most imports but disables
specific dangerous functions (os.system, subprocess.Popen, shutil.rmtree).
Writes the ``result`` variable as JSON to <result_file_path>.
"""
import builtins
import json
import sys
import threading
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

# ---- Argument parsing with optional timeout ----

_ARGS = [a for a in sys.argv[1:] if not a.startswith("--")]  # positional args
_timeout = 30  # default timeout seconds
for i, a in enumerate(sys.argv[1:], 1):
    if a == "--timeout" and i + 1 < len(sys.argv[1:]):
        try:
            _timeout = max(1, int(sys.argv[i + 1]))
        except ValueError:
            pass

if len(_ARGS) < 2:
    print(f"ERROR: Usage: _exec_runner.py [--timeout N] <code_file> <result_file>", file=sys.stderr)
    sys.exit(1)

code_path = _ARGS[0]
result_path = _ARGS[1]

try:
    with open(code_path, "r", encoding="utf-8") as f:
        code = f.read()
except FileNotFoundError:
    print(f"ERROR: code file not found: {code_path}", file=sys.stderr)
    sys.exit(1)

# ---- Execute user code with timeout watchdog ----

_result: dict = {}
_exc_info: list[str] = []

def _run_code():
    """Execute user code in a separate thread."""
    _g: dict = {"__builtins__": _SAFE_BUILTINS, "__name__": "__exec__"}
    try:
        exec(code, _g)
    except SystemExit:
        pass
    except Exception:
        _exc_info.append(traceback.format_exc())
        return

    result_value = _g.get("result")
    try:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump({"result": result_value}, f, ensure_ascii=False, default=str)
    except Exception as exc:
        _exc_info.append(f"ERROR: failed to write result: {exc}")

_thread = threading.Thread(target=_run_code, daemon=True)
_thread.start()
_thread.join(timeout=_timeout)

if _thread.is_alive():
    # Timeout — the code is still running.  This is a subprocess, so
    # os._exit is safe (no cleanup needed).
    print(f"ERROR: code execution timed out after {_timeout}s", file=sys.stderr)
    sys.exit(1)

# Thread finished — check for errors
if _exc_info:
    sys.stderr.write(_exc_info[0])
    sys.exit(1)
