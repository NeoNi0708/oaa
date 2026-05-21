"""Async file I/O — offloads reads/writes to a thread pool to avoid blocking the event loop."""
import asyncio
import json
import os


def _sync_write(path: str, data: str, mode: str = "w"):
    """Synchronous text write."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, mode, encoding="utf-8") as f:
        f.write(data)


def _sync_read(path: str) -> str:
    """Synchronous text read."""
    with open(path, encoding="utf-8") as f:
        return f.read()


def _sync_write_json(path: str, data, **kwargs):
    """Synchronous JSON write."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, **kwargs)


def _sync_read_json(path: str):
    """Synchronous JSON read."""
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def async_write(path: str, data: str, mode: str = "w"):
    """Write text to *path* via thread pool. Creates parent directories."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _sync_write, path, data, mode)


async def async_read(path: str) -> str | None:
    """Read text from *path* via thread pool. Returns ``None`` on failure."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _sync_read, path)
    except (FileNotFoundError, OSError):
        return None


async def async_write_json(path: str, data, **kwargs):
    """Write JSON to *path* via thread pool. Creates parent directories."""
    loop = asyncio.get_running_loop()
    fn = lambda: _sync_write_json(path, data, **kwargs)
    await loop.run_in_executor(None, fn)


async def async_read_json(path: str, default=None):
    """Read JSON from *path* via thread pool. Returns *default* on failure."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _sync_read_json, path)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default
