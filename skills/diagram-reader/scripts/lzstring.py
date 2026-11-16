"""LZ-String の decompressFromBase64 の、Python (標準ライブラリだけ) での実装。

Obsidian の Excalidraw プラグインは、図のデータを ```compressed-json に、LZString.compressToBase64 で圧縮して保存することがある。
読み込み側 (excalidraw_read.py) が、これを展開するために使う。圧縮 (書き込み) は実装しない。

アルゴリズムは、lz-string (MIT, pieroxy) の _decompress を、そのまま移したもの。
JavaScript の文字列は UTF-16 のコード単位の列なので、展開結果も、コード単位として組み立てたあとで UTF-16 として解釈する。
"""
from __future__ import annotations

KEY_STR_BASE64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/="
_BASE_VALUES = {ch: i for i, ch in enumerate(KEY_STR_BASE64)}


class LZStringError(Exception):
    pass


def decompress_from_base64(text: str) -> str:
    cleaned = "".join(text.split())
    if not cleaned:
        return ""
    for ch in cleaned:
        if ch not in _BASE_VALUES:
            raise LZStringError(f"Base64 として正しくない文字があります: {ch!r}")
    values = [_BASE_VALUES[ch] for ch in cleaned]
    result = _decompress(len(values), 32, lambda index: values[index] if index < len(values) else 0)
    if result is None:
        raise LZStringError("展開に失敗しました (データが壊れています)")
    try:
        return result.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
    except UnicodeError as exc:
        raise LZStringError("展開した文字列を UTF-16 として読めません") from exc


def _decompress(length: int, reset_value: int, get_next_value) -> str | None:  # noqa: ANN001
    dictionary: dict[int, str] = {}
    enlarge_in = 4
    dict_size = 4
    num_bits = 3
    result: list[str] = []
    for i in range(3):
        dictionary[i] = chr(i)
    state = {"val": get_next_value(0), "position": reset_value, "index": 1}

    def read_bits(count: int) -> int:
        bits = 0
        power = 1
        maxpower = 1 << count
        while power != maxpower:
            resb = state["val"] & state["position"]
            state["position"] >>= 1
            if state["position"] == 0:
                state["position"] = reset_value
                state["val"] = get_next_value(state["index"])
                state["index"] += 1
            bits |= (1 if resb > 0 else 0) * power
            power <<= 1
        return bits

    nxt = read_bits(2)
    if nxt == 0:
        c = chr(read_bits(8))
    elif nxt == 1:
        c = chr(read_bits(16))
    else:
        return ""
    dictionary[3] = c
    w = c
    result.append(c)
    while True:
        if state["index"] > length:
            return ""
        code = read_bits(num_bits)
        if code == 0:
            dictionary[dict_size] = chr(read_bits(8))
            dict_size += 1
            code = dict_size - 1
            enlarge_in -= 1
        elif code == 1:
            dictionary[dict_size] = chr(read_bits(16))
            dict_size += 1
            code = dict_size - 1
            enlarge_in -= 1
        elif code == 2:
            return "".join(result)
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1
        if code in dictionary:
            entry = dictionary[code]
        elif code == dict_size:
            entry = w + w[0]
        else:
            return None
        result.append(entry)
        dictionary[dict_size] = w + entry[0]
        dict_size += 1
        enlarge_in -= 1
        w = entry
        if enlarge_in == 0:
            enlarge_in = 1 << num_bits
            num_bits += 1
