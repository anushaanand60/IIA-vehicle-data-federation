"""CONTEXT-ADDED (R2: free-text queries): a tiny client for the local Ollama model.

The model runs on the mediator laptop (http://127.0.0.1:11434), never in the cloud, and only ever sees
the user's query text, never source rows. It is an optional helper: every function returns None when
Ollama is off, slow or answers garbage, so callers always have a rules-only path.

Answers are cached on disk in data/llm_cache.json keyed by (model, prompt hash). That cache holds model
outputs only; caching source data would contradict the federation's freshness claim.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
DEFAULT_MODEL = "qwen2.5:3b"
CACHE_PATH = Path(__file__).resolve().parent.parent / "data" / "llm_cache.json"
AVAILABLE_TTL_S = 30.0     # a GUI asks on every keystroke; probing Ollama each time would add latency

_lock = threading.Lock()
_avail: Dict[str, Any] = {"at": 0.0, "up": False}
_cache: Optional[Dict[str, Any]] = None


def available(force: bool = False) -> bool:
    """Is the Ollama server answering? Cached 30 s (positive and negative)."""
    now = time.monotonic()
    if not force and now - _avail["at"] < AVAILABLE_TTL_S:
        return bool(_avail["up"])
    try:
        up = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.5).status_code == 200
    except Exception:  # connection refused, DNS, anything: the answer is simply "no model"
        up = False
    _avail.update(at=now, up=up)
    return up


def _key(model: str, kind: str, prompt: str) -> str:
    return f"{model}|{kind}|" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def _load() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        try:
            _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _cache = {}
    return _cache


def _store(key: str, value: Any) -> None:
    with _lock:
        cache = _load()
        cache[key] = value
        try:
            CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = CACHE_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, CACHE_PATH)
        except OSError:  # a read-only checkout still works, just without the disk cache
            pass


def cached(model: str, kind: str, prompt: str) -> Any:
    return _load().get(_key(model, kind, prompt))


def _generate(prompt: str, model: str, timeout_s: float, as_json: bool, system: Optional[str]) -> Optional[str]:
    if not available():
        return None
    body: Dict[str, Any] = {"model": model, "prompt": prompt, "stream": False,
                            "options": {"temperature": 0, "seed": 7}}   # deterministic: same text, same parse
    if as_json:
        body["format"] = "json"
    if system:
        body["system"] = system
    try:
        resp = httpx.post(f"{OLLAMA_URL}/api/generate", json=body, timeout=timeout_s)
        if resp.status_code != 200:
            return None
        return str(resp.json().get("response", "")) or None
    except Exception:  # timeout, model not pulled, server restarting: degrade to rules-only
        return None


def generate_text(prompt: str, model: str = DEFAULT_MODEL, timeout_s: float = 20,
                  system: Optional[str] = None) -> Optional[str]:
    """Plain completion, or None. Cached per (model, prompt)."""
    full = f"{system or ''}\n{prompt}"
    hit = cached(model, "text", full)
    if isinstance(hit, str):
        return hit
    out = _generate(prompt, model, timeout_s, as_json=False, system=system)
    if out is not None:
        _store(_key(model, "text", full), out)
    return out


def generate_json(prompt: str, model: str = DEFAULT_MODEL, timeout_s: float = 20,
                  system: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """JSON-mode completion parsed to a dict, or None (Ollama off, timeout, non-object JSON)."""
    full = f"{system or ''}\n{prompt}"
    hit = cached(model, "json", full)
    if isinstance(hit, dict):
        return hit
    out = _generate(prompt, model, timeout_s, as_json=True, system=system)
    if out is None:
        return None
    try:
        parsed = json.loads(out)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    _store(_key(model, "json", full), parsed)
    return parsed
