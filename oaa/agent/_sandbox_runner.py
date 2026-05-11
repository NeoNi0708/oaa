"""Sandbox runner — process-level isolated Python execution for do_code_run.

Called as: python -I _sandbox_runner.py <code_file_path>

Combines multiple layers of defense:
1. MetaPathFinder — intercepts import machinery lookups
2. sys.modules cleanup — removes denied modules before exec
3. builtins.__import__ monkey-patch — catches imports that would bypass
   MetaPathFinder because the module is already in sys.modules
"""
import builtins
import importlib.abc
import sys
import traceback

DENIED_MODULES = frozenset({
    "os", "subprocess", "shutil", "sys", "ctypes",
    "signal", "socket", "importlib", "builtins",
})


# ---- Layer 1: MetaPathFinder blocks at the import protocol level ----

class _SandboxBlocker(importlib.abc.MetaPathFinder):
    """Blocks denied modules at the import machinery level."""

    def find_spec(self, fullname, path, target=None):
        top_level = fullname.split(".", 1)[0]
        if top_level in DENIED_MODULES:
            raise ImportError(
                f"module '{fullname}' is blocked in sandbox"
            )
        return None


sys.meta_path.insert(0, _SandboxBlocker())

# ---- Layer 2: clean sys.modules so cached entries don't bypass the finder ----

_original_exit = sys.exit
_original_stderr = sys.stderr

for mod_name in list(sys.modules.keys()):
    top = mod_name.split(".", 1)[0]
    if top in DENIED_MODULES:
        del sys.modules[mod_name]

# ---- Layer 3: builtins.__import__ monkey-patch ----

_original_import: object = builtins.__import__


def _sandbox_import(name: str, *args: object, **kwargs: object) -> object:
    top_level = name.split(".", 1)[0]
    if top_level in DENIED_MODULES:
        raise ImportError(f"module '{name}' is blocked in sandbox")
    return _original_import(name, *args, **kwargs)  # type: ignore[arg-type]


builtins.__import__ = _sandbox_import

# ---- Execute user code ----

if len(sys.argv) < 2:
    print("ERROR: No code file specified", file=_original_stderr)
    _original_exit(1)

code_path = sys.argv[1]
try:
    with open(code_path, "r", encoding="utf-8") as f:
        code = f.read()
except FileNotFoundError:
    print(f"ERROR: code file not found: {code_path}", file=_original_stderr)
    _original_exit(1)

_globals: dict = {"__builtins__": builtins, "__name__": "__sandbox__"}
try:
    exec(code, _globals)
except SystemExit:
    pass
except Exception:
    traceback.print_exc(file=_original_stderr)
    _original_exit(1)
