"""単位の読み取りと、単位つきの量 (標準ライブラリだけ)。

値は、内部ではいつも SI (m・kg・s・A・K・mol) で持つ。単位は「SI への倍率」と「次元」(各基本単位の指数) の組。
例: "kN" = 1000 倍・kg m s^-2、"mm2" = 1e-6 倍・m^2、"N/mm2" = 1e6 倍・kg m^-1 s^-2 (= MPa)。
温度は K だけ (摂氏は、差なのか値なのかで換算が変わるので、扱わない)。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from fractions import Fraction

BASE_NAMES = ("m", "kg", "s", "A", "K", "mol")
Dim = tuple  # Fraction の 6 つ組


def dim(**exponents: int) -> Dim:
    return tuple(Fraction(exponents.get(name, 0)) for name in BASE_NAMES)


NONE = dim()
FORCE = dim(kg=1, m=1, s=-2)
PRESSURE = dim(kg=1, m=-1, s=-2)

# 記号: (SI への倍率, 次元, 接頭語を付けてよいか)
SYMBOLS: dict[str, tuple[float, Dim, bool]] = {
    "m": (1.0, dim(m=1), True),
    "g": (1e-3, dim(kg=1), True),
    "s": (1.0, dim(s=1), True),
    "A": (1.0, dim(A=1), True),
    "K": (1.0, dim(K=1), False),
    "mol": (1.0, dim(mol=1), True),
    "N": (1.0, FORCE, True),
    "Pa": (1.0, PRESSURE, True),
    "J": (1.0, dim(kg=1, m=2, s=-2), True),
    "W": (1.0, dim(kg=1, m=2, s=-3), True),
    "V": (1.0, dim(kg=1, m=2, s=-3, A=-1), True),
    "ohm": (1.0, dim(kg=1, m=2, s=-3, A=-2), True),
    "Ω": (1.0, dim(kg=1, m=2, s=-3, A=-2), True),
    "Hz": (1.0, dim(s=-1), True),
    "L": (1e-3, dim(m=3), True),
    "t": (1e3, dim(kg=1), False),
    "min": (60.0, dim(s=1), False),
    "h": (3600.0, dim(s=1), False),
    "kgf": (9.80665, FORCE, False),
    "bar": (1e5, PRESSURE, False),
    "rad": (1.0, NONE, False),
    "deg": (math.pi / 180, NONE, False),
    "°": (math.pi / 180, NONE, False),
    "%": (0.01, NONE, False),
}
PREFIXES = {"G": 1e9, "M": 1e6, "k": 1e3, "c": 1e-2, "m": 1e-3, "u": 1e-6, "μ": 1e-6, "µ": 1e-6, "n": 1e-9}
DIMENSIONLESS = {"", "-", "1", "無次元"}
SPECIAL = {"bool", "text"}  # 判定 (真偽) と、文字 (材料の種類など)
NAMED_DIMS = {
    NONE: "無次元", dim(m=1): "長さ", dim(m=2): "面積", dim(m=3): "体積", dim(m=4): "断面二次モーメント (m^4)",
    dim(kg=1): "質量", dim(s=1): "時間", FORCE: "力", PRESSURE: "応力・圧力", dim(kg=1, m=2, s=-2): "エネルギー・モーメント",
    dim(kg=1, m=2, s=-3): "仕事率", dim(m=1, s=-1): "速度", dim(m=1, s=-2): "加速度", dim(kg=1, m=-3): "密度",
    dim(kg=1, s=-2): "力/長さ (N/m)", dim(K=1): "温度",
}


class UnitError(Exception):
    pass


@dataclass(frozen=True)
class Unit:
    text: str
    factor: float
    dim: Dim


def _symbol(symbol: str) -> tuple[float, Dim]:
    if symbol in SYMBOLS:
        factor, dimension, _ = SYMBOLS[symbol]
        return factor, dimension
    for prefix, scale in PREFIXES.items():
        rest = symbol[len(prefix):]
        if symbol.startswith(prefix) and rest in SYMBOLS and SYMBOLS[rest][2]:
            factor, dimension, _ = SYMBOLS[rest]
            return scale * factor, dimension
    if symbol in ("℃", "degC", "°C"):
        raise UnitError("摂氏 (℃) は扱いません。温度差は K、温度は K に直して書く")
    raise UnitError(f"単位が分かりません: '{symbol}' (使える記号: {', '.join(SYMBOLS)}。接頭語: {', '.join(PREFIXES)})")


def parse_unit(text: str) -> Unit:
    """'kN'、'N/mm2'、'kN*m'、'kN·m'、'mm^4'、'm/s2'、'kg/m3'、'%'、'-' などを読む。かっこは使わない (kg/m/s2 のように書く)。"""
    raw = (text or "").strip()
    if raw in DIMENSIONLESS:
        return Unit(raw, 1.0, NONE)
    if raw in SPECIAL:
        raise UnitError(f"'{raw}' は数の単位ではありません")
    normalized = raw
    for old, new in (("²", "^2"), ("³", "^3"), ("⁴", "^4"), ("⁻¹", "^-1"), ("⁻²", "^-2"), ("⁻³", "^-3"), ("·", "*"), ("・", "*"), ("×", "*"), (" ", "*")):
        normalized = normalized.replace(old, new)
    if "(" in normalized or ")" in normalized:
        raise UnitError(f"単位にかっこは使えません: '{raw}' (kg/(m*s2) は kg/m/s2 と書く)")
    factor, dimension = 1.0, [Fraction(0)] * len(BASE_NAMES)
    tokens = re.split(r"([*/])", normalized)
    sign = 1
    for token in tokens:
        if token == "*":
            sign = 1
            continue
        if token == "/":
            sign = -1
            continue
        if not token:
            raise UnitError(f"単位の書き方が正しくありません: '{raw}'")
        match = re.fullmatch(r"(.+?)\^?(-?\d+(?:/\d+)?)?", token)
        if not match:
            raise UnitError(f"単位の書き方が正しくありません: '{raw}'")
        symbol, power_text = match.group(1), match.group(2)
        if symbol == "1" and power_text is None:
            continue
        power = Fraction(power_text) if power_text else Fraction(1)
        base_factor, base_dim = _symbol(symbol)
        factor *= base_factor ** float(power * sign)
        dimension = [d + b * power * sign for d, b in zip(dimension, base_dim)]
    return Unit(raw, factor, tuple(dimension))


def describe_dim(dimension: Dim) -> str:
    parts = []
    for name, exponent in zip(BASE_NAMES, dimension):
        if exponent == 1:
            parts.append(name)
        elif exponent:
            parts.append(f"{name}^{exponent}")
    text = "·".join(parts) or "1"
    named = NAMED_DIMS.get(tuple(dimension))
    return f"{text} ({named})" if named else text


def units_help() -> str:
    lines = ["使える単位の記号 (接頭語つきは、* の付いたものだけ):"]
    for symbol, (factor, dimension, prefixable) in SYMBOLS.items():
        lines.append(f"  {symbol}{'*' if prefixable else ''}  = {factor:g} × {describe_dim(dimension)}")
    lines.append("接頭語: " + ", ".join(f"{p} ({v:g})" for p, v in PREFIXES.items() if p != "µ"))
    lines.append("書き方: N/mm2、kN*m (kN·m)、mm^4 (mm4)、m/s2、kg/m3。かっこは使わない。無次元は '-'。判定は 'bool'、文字は 'text'")
    lines.append("温度は K だけ (℃ は扱わない)")
    return "\n".join(lines)
