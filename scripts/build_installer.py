"""Build OAA installer — runs Electron Builder with Python bundle."""
import os
import sys
import subprocess
from pathlib import Path


def build():
    root = Path(__file__).parent.parent
    gui_dir = root / "gui"
    python_bundle_dir = root / "python-bundle"

    print("[Build] Step 1: Bundle Python runtime and deps...")
    sys.path.insert(0, str(root / "scripts"))
    from bundle_python import bundle_python
    bundle_python(str(python_bundle_dir))

    print("[Build] Step 2: Build Vue3 + Electron app...")
    result = subprocess.run(
        ["npm", "run", "build"],
        cwd=str(gui_dir),
        shell=True,
    )
    if result.returncode != 0:
        print("[Build] Electron build failed!")
        sys.exit(1)

    print(f"[Build] Done! Installer at: {gui_dir / 'release'}")
    print("[Build] Run the NSIS installer to install OAA.")


if __name__ == "__main__":
    build()
