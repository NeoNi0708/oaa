"""Bundle Python runtime and OAA dependencies for Electron Builder.

Copies the Python interpreter, standard library, installed packages, and OAA
source into *target_dir* so the result is a self-contained Python distribution
that the Electron app can use without requiring a pre-installed Python.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path


def bundle_python(target_dir: str):
    """Copy Python runtime + installed packages + OAA source to *target_dir*."""
    target = Path(target_dir)
    target.mkdir(parents=True, exist_ok=True)

    python_root = Path(sys.executable).parent
    print(f"Python root: {python_root}")

    # Copy Python executable
    shutil.copy2(python_root / "python.exe", target / "python.exe")
    for dll in python_root.glob("*.dll"):
        shutil.copy2(dll, target / dll.name)

    # Copy standard library
    lib_root = python_root / "Lib"
    if lib_root.exists():
        for item in lib_root.iterdir():
            dst = target / "Lib" / item.name
            if item.is_dir():
                shutil.copytree(
                    item, dst, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "test", "tests"),
                )
            else:
                shutil.copy2(item, dst)

    # Copy DLLs/
    dlls_dir = python_root / "DLLs"
    if dlls_dir.exists():
        shutil.copytree(dlls_dir, target / "DLLs", dirs_exist_ok=True)

    # Copy OAA source
    oaa_src = Path(__file__).parent.parent / "oaa"
    shutil.copytree(
        oaa_src, target / "oaa", dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )

    # Create requirements.txt with frozen deps
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "freeze"],
            stdout=open(target / "requirements.txt", "w"),
            check=True,
        )
    except subprocess.CalledProcessError:
        pass

    # Generate a bootstrap bat for first-run dependency install
    bat = (
        '@echo off\n'
        f'"%~dp0python.exe" -m pip install -r "%~dp0requirements.txt" '
        f'--no-warn-script-location\n'
        'echo OAA Python environment ready.\n'
    )
    (target / "install_deps.bat").write_text(bat)

    size_mb = sum(
        f.stat().st_size for f in target.rglob("*") if f.is_file()
    ) / (1024 * 1024)
    print(f"Python bundle created at {target} ({size_mb:.0f} MB)")
    print("Electron can use: child_process.spawn(path.join(process.resourcesPath, 'python-bundle', 'python.exe'), ...)")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "../python-bundle"
    bundle_python(target)
