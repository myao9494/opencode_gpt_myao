"""Excalidraw の図 (.excalidraw.md / .excalidraw) を読み込む。

読める形式: Obsidian の .excalidraw.md (```json と、圧縮された ```compressed-json の両方)、標準の .excalidraw (JSON)。
読み込んだ結果 (図のデータ) は、そのまま共通の中間形式として、diagram_model.analyze に渡せる。
"""
from __future__ import annotations

import json
import re
from typing import Any

import lzstring


class ReadError(Exception):
    """利用者に見せてよいメッセージ。"""


DRAWING_RE = re.compile(r"##? Drawing\s*\n```(compressed-json|json)\s*\n(.*?)```", re.S)


def load_drawing(text: str, name: str = "") -> dict[str, Any]:
    """ファイルの内容から、図のデータ (JSON のオブジェクト) を取り出す。"""
    stripped = text.lstrip("﻿").lstrip()
    if name.lower().endswith(".excalidraw") or stripped.startswith("{"):
        raw = stripped
    else:
        match = DRAWING_RE.search(text)
        if not match:
            raise ReadError("図のデータ (## Drawing の下の json) が見つかりません。Excalidraw のファイルではないかもしれません")
        kind, body = match.group(1), match.group(2)
        if kind == "compressed-json":
            try:
                raw = lzstring.decompress_from_base64(body)
            except lzstring.LZStringError as exc:
                raise ReadError(f"圧縮された図のデータを展開できません: {exc}") from exc
        else:
            raw = body
    try:
        doc = json.loads(raw)
    except ValueError as exc:
        raise ReadError(f"図のデータ (JSON) を読めません: {exc}") from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("elements"), list):
        raise ReadError("図のデータに elements (要素の配列) がありません")
    return doc


