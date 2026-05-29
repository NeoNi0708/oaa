"""Symbol-level cross-reference audit tool for Python source code.

Uses ``ast`` to build call graphs and detect structural issues without
relying on the LLM's context window. The agent calls this tool instead
of reading dozens of source files into its context.
"""

import ast
import os
from typing import Optional


def audit_module(root_path: str, module_path: str, resolve_calls: bool = True) -> dict:
    """Parse a Python module (or package directory) and return a call-graph report.

    Args:
        root_path: Project root directory (e.g. ``E:/GenericAgent/oaa``).
        module_path: Dotted module name (e.g. ``oaa.agent.loop``) or
                     a file path relative to *root_path*.
        resolve_calls: If True, attempt to resolve call targets across
                       known modules under *root_path* and flag calls
                       to functions/methods that don't appear to exist.

    Returns a dict with keys:
        ``module``, ``file``, ``classes`` (list of class dicts with
        ``name``, ``methods``), ``functions`` (list of top-level
        function dicts), ``calls`` (list of ``{caller, callee, line}``
        dicts), ``unresolved_calls`` (list of calls whose target could
        not be found), ``imports``, ``summary``.
    """
    # Resolve module path → file path
    rel = module_path.replace(".", os.sep)
    py_file = os.path.join(root_path, rel + ".py")
    pkg_init = os.path.join(root_path, rel, "__init__.py")

    files_to_parse: list[str] = []
    if os.path.isfile(py_file):
        files_to_parse.append(py_file)
    elif os.path.isdir(os.path.join(root_path, rel)):
        # Parse whole package
        pkg_dir = os.path.join(root_path, rel)
        for dirpath, _dirnames, filenames in os.walk(pkg_dir):
            for fn in sorted(filenames):
                if fn.endswith(".py"):
                    files_to_parse.append(os.path.join(dirpath, fn))
    else:
        return {"error": f"Module not found: {module_path} (tried {py_file} and {pkg_init})"}

    report: dict = {
        "module": module_path,
        "files": [os.path.relpath(f, root_path) for f in files_to_parse],
        "classes": [],
        "functions": [],
        "calls": [],
        "unresolved_calls": [],
        "imports": [],
        "summary": "",
    }

    all_defs: dict[str, str] = {}   # name → location
    all_calls: list[dict] = []

    for fpath in files_to_parse:
        try:
            with open(fpath, encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=fpath)
        except (SyntaxError, OSError) as exc:
            report.setdefault("parse_errors", []).append(f"{fpath}: {exc}")
            continue

        short = os.path.relpath(fpath, root_path)
        visitor = _ModuleVisitor(short)
        visitor.visit(tree)

        report["classes"].extend(visitor.classes)
        report["functions"].extend(visitor.functions)
        report["calls"].extend(visitor.calls)
        report["imports"].extend(visitor.imports)
        for d in visitor.classes:
            for m in d.get("methods", []):
                qname = f"{d['name']}.{m['name']}"
                all_defs[qname] = f"{short}:{m['lineno']}"
        for fn in visitor.functions:
            all_defs[fn["name"]] = f"{short}:{fn['lineno']}"
        all_calls.extend(visitor.calls)

    # Resolve calls across the scanned files
    if resolve_calls:
        for call in all_calls:
            target = call["callee"]
            # Skip builtins
            if target in _BUILTINS or target.startswith("_"):
                continue
            # Check simple name, dotted name, and parent.method variants
            found = target in all_defs
            if not found:
                # Try class.method patterns
                for def_name in all_defs:
                    if def_name.endswith("." + target) or def_name.endswith("." + target.split(".")[-1]):
                        found = True
                        break
            if not found:
                report["unresolved_calls"].append({
                    "caller": call["caller"],
                    "callee": target,
                    "line": call["line"],
                    "file": call["file"],
                })

    # Summary
    total_defs = sum(len(c.get("methods", [])) for c in report["classes"]) + len(report["functions"])
    total_calls = len(report["calls"])
    unresolved = len(report["unresolved_calls"])
    report["summary"] = (
        f"{len(report['files'])} file(s), {len(report['classes'])} class(es), "
        f"{total_defs} function(s)/method(s), {total_calls} call(s)"
    )
    if unresolved:
        report["summary"] += f", {unresolved} unresolved call(s) ⚠️"
    if report.get("parse_errors"):
        report["summary"] += f", {len(report['parse_errors'])} parse error(s)"
    # Keep only top 50 unresolved to avoid noise
    if len(report["unresolved_calls"]) > 50:
        report["unresolved_calls"] = report["unresolved_calls"][:50]
        report["summary"] += " (truncated to 50)"

    return report


# Python builtins — not flagged as unresolved
_BUILTINS: set[str] = set(dir(__builtins__)) if isinstance(__builtins__, dict) else set()
_BUILTINS.update({
    "super", "range", "len", "print", "isinstance", "hasattr", "getattr",
    "setattr", "enumerate", "zip", "open", "int", "str", "float", "bool",
    "list", "dict", "tuple", "set", "frozenset", "type", "object",
    "Exception", "ValueError", "TypeError", "KeyError", "OSError",
    "AttributeError", "ImportError", "json", "os", "sys", "time",
    "datetime", "re", "logging", "logger", "asdict", "field",
})

# ------------------------------------------------------------------
# AST visitor
# ------------------------------------------------------------------

class _ModuleVisitor(ast.NodeVisitor):
    def __init__(self, file_rel: str):
        self.file_rel = file_rel
        self.classes: list[dict] = []
        self.functions: list[dict] = []
        self.calls: list[dict] = []
        self.imports: list[str] = []

    def visit_ClassDef(self, node: ast.ClassDef):
        methods = []
        for item in node.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                methods.append({
                    "name": item.name,
                    "lineno": item.lineno,
                    "args": [a.arg for a in item.args.args],
                })
            # Collect calls inside methods too
            for child in ast.walk(item):
                if isinstance(child, ast.Call):
                    callee = _call_name(child)
                    if callee:
                        self.calls.append({
                            "caller": f"{node.name}.{item.name}" if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) else node.name,
                            "callee": callee,
                            "line": child.lineno,
                            "file": self.file_rel,
                        })
        self.classes.append({
            "name": node.name,
            "lineno": node.lineno,
            "methods": methods,
        })

    def visit_FunctionDef(self, node: ast.FunctionDef):
        _visit_func_or_async(self, node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef):
        _visit_func_or_async(self, node, is_async=True)

    def visit_Import(self, node: ast.Import):
        for alias in node.names:
            self.imports.append(f"import {alias.name}")

    def visit_ImportFrom(self, node: ast.ImportFrom):
        mod = node.module or ""
        names = ", ".join(a.name for a in node.names)
        self.imports.append(f"from {mod} import {names}")


def _visit_func_or_async(visitor: _ModuleVisitor, node: ast.FunctionDef | ast.AsyncFunctionDef,
                         is_async: bool):
    prefix = "async " if is_async else ""
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            callee = _call_name(child)
            if callee:
                visitor.calls.append({
                    "caller": node.name,
                    "callee": callee,
                    "line": child.lineno,
                    "file": visitor.file_rel,
                })
    visitor.functions.append({
        "name": node.name,
        "lineno": node.lineno,
        "args": [a.arg for a in node.args.args],
        "is_async": is_async,
    })


def _call_name(call: ast.Call) -> Optional[str]:
    """Extract a human-readable callee name from an ast.Call node."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        node: ast.AST = func
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        return ".".join(reversed(parts))
    return None
