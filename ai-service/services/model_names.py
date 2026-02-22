"""Helpers for normalizing model identifiers across providers."""

from __future__ import annotations

import re


def normalize_vertex_model_name(model_name: str | None) -> str:
    """Normalize Vertex model names to plain IDs expected by SDKs.

    Accepts any of:
    - gemini-3.1-pro-preview
    - google/gemini-3.1-pro-preview
    - publishers/google/models/gemini-3.1-pro-preview
    - projects/.../publishers/google/models/gemini-3.1-pro-preview
    """
    raw = (model_name or "").strip()
    if not raw:
        return ""

    match = re.search(r"publishers/google/models/([A-Za-z0-9._-]+)", raw)
    if match:
        return match.group(1)

    if raw.startswith("google/"):
        return raw.split("/", 1)[1]

    return raw

