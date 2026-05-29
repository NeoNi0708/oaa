"""Embedding model loader — ONNX Runtime fallback for sentence embeddings.

Auto-downloads ``paraphrase-multilingual-MiniLM-L12-v2`` ONNX model from
HuggingFace on first use, cached to ``data_dir/models/embeddings/``.
Falls back to hash-based encoding when the model is not yet available.
"""

import json
import os
import re
from typing import Optional

from ...logging_config import get_logger

logger = get_logger("agent.memory.embedding")

EMBEDDING_DIM = 384
HF_REPO = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
_HF_MIRRORS = [
    "https://hf-mirror.com",
    "https://huggingface.co",
]
ONNX_FILES = {
    "model.onnx": f"/{HF_REPO}/resolve/main/onnx/model.onnx",
    "tokenizer.json": f"/{HF_REPO}/resolve/main/tokenizer.json",
}


def get_model_dir(data_dir: str) -> str:
    path = os.path.join(data_dir, "models", "embeddings")
    os.makedirs(path, exist_ok=True)
    return path


def is_model_available(data_dir: str) -> bool:
    model_dir = get_model_dir(data_dir)
    return all(os.path.isfile(os.path.join(model_dir, f)) for f in ONNX_FILES)


def download_model(data_dir: str):
    """Auto-download ONNX model + tokenizer from HuggingFace (with mirrors)."""
    import requests
    model_dir = get_model_dir(data_dir)
    logger.info("Downloading embedding model to %s (this happens once)", model_dir)
    for fname, suffix in ONNX_FILES.items():
        dest = os.path.join(model_dir, fname)
        if os.path.isfile(dest):
            continue
        downloaded = False
        for mirror in _HF_MIRRORS:
            url = mirror + suffix
            try:
                logger.info("  Trying %s ...", mirror.split("/")[2])
                resp = requests.get(url, timeout=60, stream=True)
                resp.raise_for_status()
                with open(dest, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=8192):
                        f.write(chunk)
                logger.info("  Downloaded %s (from %s)", fname, mirror.split("/")[2])
                downloaded = True
                break
            except Exception as exc:
                logger.warning("  %s failed: %s", mirror.split("/")[2], exc)
        if not downloaded:
            logger.warning("  Failed to download %s from all mirrors", fname)
    if is_model_available(data_dir):
        logger.info("Embedding model ready (%d files)", len(ONNX_FILES))
    else:
        logger.warning("Embedding model not available — using hash fallback")


class Embedder:
    """Text → 384-dim vector via ONNX-served multilingual MiniLM."""

    def __init__(self, data_dir: str):
        self._model_dir = get_model_dir(data_dir)
        self._session = None
        self._rust_tokenizer = None
        if is_model_available(data_dir):
            try:
                self._load_onnx()
            except Exception as exc:
                logger.warning("Failed to load ONNX model: %s — using fallback", exc)

    def _load_onnx(self):
        import onnxruntime as ort
        model_path = os.path.join(self._model_dir, "model.onnx")
        tokenizer_path = os.path.join(self._model_dir, "tokenizer.json")
        if not os.path.isfile(model_path):
            raise FileNotFoundError(f"ONNX model not found: {model_path}")
        self._session = ort.InferenceSession(model_path, providers=["CPUExecutionProvider"])
        if os.path.isfile(tokenizer_path):
            try:
                from tokenizers import Tokenizer as _Tok
                self._rust_tokenizer = _Tok.from_file(tokenizer_path)
                self._rust_tokenizer.enable_truncation(max_length=256)
                self._rust_tokenizer.enable_padding(length=256, pad_id=0, pad_token="<pad>")
            except Exception as exc:
                logger.warning("Failed to load tokenizer: %s", exc)

    def _tokenize(self, text: str, max_len: int = 256) -> dict:
        if self._rust_tokenizer is not None:
            encoded = self._rust_tokenizer.encode(text)
            return {
                "input_ids": [self._pad(encoded.ids, max_len)],
                "attention_mask": [self._pad(encoded.attention_mask, max_len)],
                "token_type_ids": [self._pad(encoded.type_ids, max_len)],
            }
        return {"_fallback": True, "text": text}

    @staticmethod
    def _pad(arr: list[int], max_len: int) -> list[int]:
        return arr[:max_len] + [0] * (max_len - len(arr))

    def encode(self, text: str) -> list[float]:
        if not self._session:
            return self._hash_fallback(text)
        tokens = self._tokenize(text)
        if tokens.get("_fallback"):
            return self._hash_fallback(text)
        import numpy as np
        result = self._session.run(None, {
            "input_ids": tokens["input_ids"],
            "attention_mask": tokens["attention_mask"],
            "token_type_ids": tokens["token_type_ids"],
        })
        token_embs = result[0][0]
        mask = tokens["attention_mask"][0]
        valid_len = sum(mask)
        if valid_len > 0:
            token_embs = token_embs[:valid_len]
        return np.mean(token_embs, axis=0).tolist()

    def encode_many(self, texts: list[str]) -> list[list[float]]:
        return [self.encode(t) for t in texts]

    @staticmethod
    def _hash_fallback(text: str) -> list[float]:
        import hashlib
        vec = [0.0] * EMBEDDING_DIM
        words = re.findall(r"\w+", text.lower())
        for i, word in enumerate(words):
            h = hashlib.md5(word.encode()).digest()
            for j in range(min(EMBEDDING_DIM, len(h))):
                vec[(i + j) % EMBEDDING_DIM] += (h[j] / 255.0) - 0.5
        norm = sum(x * x for x in vec) ** 0.5
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec
