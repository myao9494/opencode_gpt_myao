"""計算 (設計計算・判断のロジック) を、出典つきの入力・単位の検査・原典の例での検算つきで確かめるコマンド (スキル calc-check から呼ぶ)。

  check    : 計算書 (JSON) を検査して、計算する。結果は Markdown (--out で .handoff/ に書ける)
  template : 計算書のひな形 (JSON) を表示する
  units    : 使える単位の一覧

計算書の式は、決まった書き方 (四則・べき・比較・if-else・決まった関数) だけを、自前の解釈で計算する。Python のコードとしては実行しない。
検査すること:
  - 入力の出典 (無ければ不合格。「仮定:」で始まるものは、仮定として一覧にする)
  - 単位: 値を SI に直して計算し、足し算・比較・関数・各ステップの単位の次元が合うかを確かめる (合わなければ不合格)
  - 検算の例: 原典 (計算例・過去の計算書・手計算) の入力で計算し、期待値と照合する (合わなければ不合格。例が無ければ不合格)
  - 場合分け: if-else・min・max のどちら側も、どれかの例で通ったか。判定 (bool) が真と偽の両方の例を持つか (無ければ注意)
  - 式の中の数: 単位の換算に見える数 (1000 など) や、出典の無い係数 (注意)
読むのは、いまのフォルダの中の計算書だけ。書くのは --out (.handoff/ の中だけ)。
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import os
import sys
from dataclasses import dataclass, field
from fractions import Fraction
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))

import units as u  # noqa: E402

MAX_SPEC_BYTES = 1_000_000
MAX_EXPR_CHARS = 2000
MAX_NODES = 400
MAX_STEPS = 300
DEFAULT_TOL = 0.005  # 相対 0.5% (原典の計算例は、3〜4 桁に丸めてあることが多い)
CONSTANTS = {"pi": math.pi}
PLAIN_NUMBERS = {0, 1, 2, 3, 4, 0.5, 0.25}  # 式に出てきても、係数として出典を求めない数
CONVERSION_LIKE = {9.8, 9.81, 9.80665, 60, 3600, 1000, 1e6, 1e9, 1e-3, 1e-6, 1e-9, 100, 0.01, 1e4, 1e-4}


class SpecError(Exception):
    """計算書の書き方の誤り (終了コード 2)。"""


class CalcError(Exception):
    """計算の誤り (単位の矛盾、計算できない値)。検査の結果として、不合格にする。"""


@dataclass
class Q:
    """単位つきの量 (値は SI)。"""
    value: float
    dim: u.Dim


Value = Any  # Q | bool | str


def is_q(value: Value) -> bool:
    return isinstance(value, Q)


def same_dim(a: Q, b: Q, what: str) -> None:
    if a.dim != b.dim:
        raise CalcError(f"{what}の単位が合いません: {u.describe_dim(a.dim)} と {u.describe_dim(b.dim)}")


def need_q(value: Value, what: str) -> Q:
    if not is_q(value):
        raise CalcError(f"{what}には数が要ります (真偽や文字は使えません)")
    return value


def need_none(value: Value, what: str) -> Q:
    q = need_q(value, what)
    if q.dim != u.NONE:
        raise CalcError(f"{what}は無次元の量だけです (いまは {u.describe_dim(q.dim)})。角度は rad か deg で書く")
    return q


def finite(value: float, what: str) -> float:
    if isinstance(value, complex) or math.isnan(value) or math.isinf(value):
        raise CalcError(f"{what}が計算できない値になりました")
    return value


@dataclass
class Tracker:
    """場合分け (if-else・min・max) の、通った側の記録。キー: (ステップ, 式の位置)。"""
    seen: dict[tuple[str, int, int], dict[str, set[str]]] = field(default_factory=dict)
    labels: dict[tuple[str, int, int], tuple[str, list[str]]] = field(default_factory=dict)

    def note(self, key: tuple[str, int, int], text: str, sides: list[str], taken: str, case: str) -> None:
        self.labels.setdefault(key, (text, sides))
        self.seen.setdefault(key, {side: set() for side in sides})[taken].add(case)


FUNCS = {"sqrt", "abs", "min", "max", "sin", "cos", "tan", "asin", "acos", "atan", "atan2", "exp", "log", "log10", "floor", "ceil", "val", "qty"}


class Evaluator:
    def __init__(self, step: str, source: str, tracker: Tracker | None, case: str) -> None:
        self.step, self.source, self.tracker, self.case = step, source, tracker, case

    def text(self, node: ast.AST) -> str:
        return (ast.get_source_segment(self.source, node) or "?").strip()

    def track(self, node: ast.AST, label: str, sides: list[str], taken: str, dims_only: bool) -> None:
        if self.tracker is not None and not dims_only:
            self.tracker.note((self.step, node.lineno, node.col_offset), label, sides, taken, self.case)

    def run(self, node: ast.AST, env: dict[str, Value], dims_only: bool = False) -> Value:  # noqa: C901 (式の種類ごとの分岐)
        if isinstance(node, ast.Expression):
            return self.run(node.body, env, dims_only)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or isinstance(node.value, str):
                return node.value
            if isinstance(node.value, (int, float)):
                return Q(float(node.value), u.NONE)
            raise CalcError(f"使えない値です: {self.text(node)}")
        if isinstance(node, ast.Name):
            if node.id in env:
                return env[node.id]
            if node.id in CONSTANTS:
                return Q(CONSTANTS[node.id], u.NONE)
            if node.id in ("true", "false"):
                return node.id == "true"
            raise CalcError(f"知らない名前です: {node.id} (入力か、前のステップの名前だけ使える)")
        if isinstance(node, ast.UnaryOp):
            operand = self.run(node.operand, env, dims_only)
            if isinstance(node.op, ast.Not):
                if not isinstance(operand, bool):
                    raise CalcError(f"not には判定 (真偽) が要ります: {self.text(node)}")
                return not operand
            q = need_q(operand, "符号")
            return Q(-q.value if isinstance(node.op, ast.USub) else q.value, q.dim)
        if isinstance(node, ast.BinOp):
            return self.binop(node, env, dims_only)
        if isinstance(node, ast.Compare):
            return self.compare(node, env, dims_only)
        if isinstance(node, ast.BoolOp):
            values = [self.run(v, env, dims_only) for v in node.values]
            if not all(isinstance(v, bool) for v in values):
                raise CalcError(f"and・or には判定 (真偽) が要ります: {self.text(node)}")
            return all(values) if isinstance(node.op, ast.And) else any(values)
        if isinstance(node, ast.IfExp):
            return self.ifexp(node, env, dims_only)
        if isinstance(node, ast.Call):
            return self.call(node, env, dims_only)
        raise CalcError(f"使えない書き方です: {self.text(node)}")

    def binop(self, node: ast.BinOp, env: dict[str, Value], dims_only: bool) -> Value:
        left = need_q(self.run(node.left, env, dims_only), "計算")
        right = need_q(self.run(node.right, env, dims_only), "計算")
        what = f"「{self.text(node)}」"
        if isinstance(node.op, (ast.Add, ast.Sub)):
            same_dim(left, right, f"足し算・引き算 {what} ")
            return Q(left.value + right.value if isinstance(node.op, ast.Add) else left.value - right.value, left.dim)
        if isinstance(node.op, ast.Mult):
            return Q(left.value * right.value, tuple(a + b for a, b in zip(left.dim, right.dim)))
        if isinstance(node.op, ast.Div):
            if right.value == 0 and not dims_only:
                raise CalcError(f"0 で割っています: {what}")
            return Q(left.value / (right.value or 1.0), tuple(a - b for a, b in zip(left.dim, right.dim)))
        if isinstance(node.op, ast.Pow):
            if right.dim != u.NONE:
                raise CalcError(f"べき乗の指数は無次元だけです: {what}")
            if left.dim != u.NONE:
                exponent = Fraction(right.value).limit_denominator(12)
                if abs(float(exponent) - right.value) > 1e-9:
                    raise CalcError(f"単位のある量のべき乗は、分数の指数だけです: {what}")
                dimension = tuple(d * exponent for d in left.dim)
            else:
                dimension = u.NONE
            if abs(right.value) > 100:
                raise CalcError(f"指数が大きすぎます: {what}")
            if dims_only:
                return Q(1.0, dimension)
            try:
                return Q(finite(left.value ** right.value, what), dimension)
            except (OverflowError, ZeroDivisionError) as exc:
                raise CalcError(f"計算できません: {what}") from exc
        raise CalcError(f"使えない計算です (使えるのは + - * / **): {what}")

    def compare(self, node: ast.Compare, env: dict[str, Value], dims_only: bool) -> bool:
        left = self.run(node.left, env, dims_only)
        result = True
        for op, right_node in zip(node.ops, node.comparators):
            right = self.run(right_node, env, dims_only)
            if isinstance(left, str) or isinstance(right, str) or isinstance(left, bool) or isinstance(right, bool):
                if not isinstance(op, (ast.Eq, ast.NotEq)) or type(left) is not type(right):
                    raise CalcError(f"文字・真偽の比べ方は == と != だけで、同じ種類どうしです: {self.text(node)}")
                ok = (left == right) == isinstance(op, ast.Eq)
            else:
                same_dim(left, right, f"比較「{self.text(node)}」")
                ops = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b, ast.Gt: lambda a, b: a > b,
                       ast.GtE: lambda a, b: a >= b, ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b}
                compare = ops.get(type(op))
                if compare is None:
                    raise CalcError(f"使えない比較です: {self.text(node)}")
                ok = compare(left.value, right.value)
            result = result and ok
            left = right
        return True if dims_only else result

    def ifexp(self, node: ast.IfExp, env: dict[str, Value], dims_only: bool) -> Value:
        test = self.run(node.test, env, dims_only)
        if not isinstance(test, bool):
            raise CalcError(f"if の条件には判定 (真偽) が要ります: {self.text(node.test)}")
        # 単位の検査は、通らない側も含めて行う (値は計算しない)
        body_dim = self.run(node.body, env, True)
        else_dim = self.run(node.orelse, env, True)
        if is_q(body_dim) and is_q(else_dim):
            same_dim(body_dim, else_dim, f"場合分け「{self.text(node)}」の両側")
        elif type(body_dim) is not type(else_dim):
            raise CalcError(f"場合分けの両側で、種類 (数・真偽・文字) が違います: {self.text(node)}")
        if dims_only:
            return body_dim
        self.track(node, f"if {self.text(node.test)}", ["真", "偽"], "真" if test else "偽", dims_only)
        return self.run(node.body if test else node.orelse, env, False)

    def call(self, node: ast.Call, env: dict[str, Value], dims_only: bool) -> Value:
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS or node.keywords:
            raise CalcError(f"使えない関数です: {self.text(node)} (使える関数: {', '.join(sorted(FUNCS))})")
        name = node.func.id
        if name in ("val", "qty"):
            if len(node.args) != 2 or not isinstance(node.args[1], ast.Constant) or not isinstance(node.args[1].value, str):
                raise CalcError(f"{name} は {name}(量, \"単位\") の形で書く: {self.text(node)}")
            try:
                unit = u.parse_unit(node.args[1].value)
            except u.UnitError as exc:
                raise CalcError(f"{self.text(node)}: {exc}") from exc
            q = need_q(self.run(node.args[0], env, dims_only), name)
            if name == "val":  # 量を、指定の単位での数 (無次元) にする。経験式に使う
                same_dim(q, Q(0, unit.dim), f"val「{self.text(node)}」")
                return Q(q.value / unit.factor, u.NONE)
            need_none(q, f"qty「{self.text(node)}」の数")  # 数に、単位を付ける
            return Q(q.value * unit.factor, unit.dim)
        args = [need_q(self.run(a, env, dims_only), name) for a in node.args]
        if name in ("min", "max"):
            if len(args) < 2:
                raise CalcError(f"{name} には 2 つ以上の値が要ります: {self.text(node)}")
            for other in args[1:]:
                same_dim(args[0], other, f"{name}「{self.text(node)}」")
            pick = (min if name == "min" else max)(range(len(args)), key=lambda i: args[i].value)
            sides = [self.text(a) for a in node.args]
            self.track(node, f"{name}({', '.join(sides)})", sides, sides[pick], dims_only)
            return args[pick]
        expect = 2 if name == "atan2" else 1
        if len(args) != expect:
            raise CalcError(f"{name} の引数は {expect} つです: {self.text(node)}")
        x = args[0]
        if name == "abs":
            return Q(abs(x.value), x.dim)
        if name == "sqrt":
            if x.value < 0 and not dims_only:
                raise CalcError(f"負の数の平方根です: {self.text(node)}")
            return Q(math.sqrt(abs(x.value)), tuple(d / 2 for d in x.dim))
        if name == "atan2":
            same_dim(args[0], args[1], f"atan2「{self.text(node)}」")
            return Q(math.atan2(args[0].value, args[1].value), u.NONE)
        need_none(x, f"{name}「{self.text(node)}」の引数")
        if dims_only:
            return Q(1.0, u.NONE)
        try:
            return Q(finite(float(getattr(math, name)(x.value)), self.text(node)), u.NONE)
        except (ValueError, OverflowError) as exc:
            raise CalcError(f"計算できません (値の範囲の外): {self.text(node)}") from exc


# ---------------------------------------------------------------------------
# 計算書
# ---------------------------------------------------------------------------

@dataclass
class Input:
    name: str
    value: Any
    unit: str
    source: str
    note: str = ""


@dataclass
class Step:
    name: str
    expr: str
    unit: str
    tree: ast.Expression
    note: str = ""
    source: str = ""


@dataclass
class Case:
    name: str
    source: str
    inputs: dict[str, Any]
    expect: dict[str, Any]
    tol: float


@dataclass
class Spec:
    title: str
    inputs: dict[str, Input]
    steps: list[Step]
    outputs: list[str]
    cases: list[Case]


def check_name(name: Any, what: str) -> str:
    if not isinstance(name, str) or not name.isidentifier() or name in CONSTANTS or name in FUNCS or name in ("true", "false", "and", "or", "not", "if", "else", "in", "is"):
        raise SpecError(f"{what}の名前が使えません: {name!r} (英字・数字・_ で、関数名や pi は使わない)")
    return name


def kind_of(unit: str) -> str:
    return unit if unit in u.SPECIAL else "number"


def parse_spec(data: Any) -> Spec:
    if not isinstance(data, dict):
        raise SpecError("計算書は、JSON のオブジェクト ({ ... }) にしてください。`template` でひな形を出せます")
    unknown = set(data) - {"title", "inputs", "steps", "outputs", "cases", "tol", "note"}
    if unknown:
        raise SpecError(f"知らない項目があります: {', '.join(sorted(unknown))}")
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise SpecError("title (計算の名前) がありません")
    raw_inputs = data.get("inputs")
    if not isinstance(raw_inputs, dict) or not raw_inputs:
        raise SpecError("inputs (入力) がありません")
    inputs: dict[str, Input] = {}
    for name, item in raw_inputs.items():
        check_name(name, "入力")
        if not isinstance(item, dict) or "value" not in item or "unit" not in item:
            raise SpecError(f"入力 {name} には value と unit が要ります ({{\"value\": 12, \"unit\": \"kN\", \"source\": \"…\"}})")
        inputs[name] = Input(name, item["value"], str(item["unit"]), str(item.get("source") or "").strip(), str(item.get("note") or ""))
        check_value(inputs[name].value, inputs[name].unit, f"入力 {name}")
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        raise SpecError("steps (計算の手順) がありません")
    if len(raw_steps) > MAX_STEPS:
        raise SpecError(f"steps が多すぎます (上限 {MAX_STEPS})")
    steps: list[Step] = []
    known = set(inputs)
    for index, item in enumerate(raw_steps, start=1):
        if not isinstance(item, dict) or not all(k in item for k in ("name", "expr", "unit")):
            raise SpecError(f"steps の {index} 番目には name・expr・unit が要ります")
        name = check_name(item["name"], "ステップ")
        if name in known:
            raise SpecError(f"名前が重なっています: {name}")
        expr = str(item["expr"])
        if len(expr) > MAX_EXPR_CHARS:
            raise SpecError(f"式が長すぎます: {name}")
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise SpecError(f"式の書き方が正しくありません ({name}): {expr} ({exc.msg})") from exc
        nodes = list(ast.walk(tree))
        if len(nodes) > MAX_NODES:
            raise SpecError(f"式が複雑すぎます: {name} (ステップを分ける)")
        for node in nodes:
            if isinstance(node, ast.Name) and node.id not in known and node.id not in CONSTANTS and node.id not in FUNCS and node.id not in ("true", "false"):
                raise SpecError(f"ステップ {name} の式に、まだ無い名前があります: {node.id} (入力か、前のステップの名前だけ使える)")
        unit = str(item["unit"])
        if unit not in u.SPECIAL:
            try:
                u.parse_unit(unit)
            except u.UnitError as exc:
                raise SpecError(f"ステップ {name} の単位: {exc}") from exc
        steps.append(Step(name, expr, unit, tree, str(item.get("note") or ""), str(item.get("source") or "").strip()))
        known.add(name)
    outputs = data.get("outputs") or [s.name for s in steps]
    if not isinstance(outputs, list) or any(o not in {s.name for s in steps} for o in outputs):
        raise SpecError("outputs には、ステップの名前を並べてください")
    tol = data.get("tol", DEFAULT_TOL)
    if not isinstance(tol, (int, float)) or not 0 <= tol < 1:
        raise SpecError("tol は、相対の許容差 (例: 0.005 = 0.5%) にしてください")
    cases: list[Case] = []
    for index, item in enumerate(data.get("cases") or [], start=1):
        if not isinstance(item, dict) or not isinstance(item.get("expect"), dict) or not item["expect"]:
            raise SpecError(f"cases の {index} 番目には expect (期待値) が要ります")
        overrides = item.get("inputs") or {}
        for name in list(overrides) + list(item["expect"]):
            if name not in inputs and name not in {s.name for s in steps}:
                raise SpecError(f"cases の {index} 番目に、知らない名前があります: {name}")
        for name in overrides:
            if name not in inputs:
                raise SpecError(f"cases の {index} 番目の inputs に、入力でない名前があります: {name}")
        case_tol = item.get("tol", tol)
        if not isinstance(case_tol, (int, float)) or not 0 <= case_tol < 1:
            raise SpecError(f"cases の {index} 番目の tol が正しくありません")
        cases.append(Case(str(item.get("name") or f"例{index}"), str(item.get("source") or "").strip(), overrides, item["expect"], float(case_tol)))
    return Spec(title.strip(), inputs, steps, outputs, cases)


def check_value(value: Any, unit: str, what: str) -> None:
    kind = kind_of(unit)
    if kind == "bool" and not isinstance(value, bool):
        raise SpecError(f"{what}: 単位が bool なら、値は true か false")
    if kind == "text" and not isinstance(value, str):
        raise SpecError(f"{what}: 単位が text なら、値は文字")
    if kind == "number":
        if isinstance(value, list) and len(value) == 2 and isinstance(value[1], str):
            value = value[0]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SpecError(f"{what}: 値は数にしてください ({value!r})")
        try:
            u.parse_unit(unit)
        except u.UnitError as exc:
            raise SpecError(f"{what}: {exc}") from exc


def to_value(raw: Any, unit: str, what: str) -> Value:
    """計算書の値 (数、[数, "単位"]、真偽、文字) を、計算の値にする。"""
    kind = kind_of(unit)
    if kind != "number":
        check_value(raw, unit, what)
        return raw
    if isinstance(raw, list) and len(raw) == 2 and isinstance(raw[1], str):
        number, unit_text = raw
    else:
        number, unit_text = raw, unit
    check_value(number, unit_text, what)
    parsed = u.parse_unit(unit_text)
    declared = u.parse_unit(unit)
    if parsed.dim != declared.dim:
        raise SpecError(f"{what}: 単位 {unit_text} は、{unit} と次元が違います")
    return Q(float(number) * parsed.factor, parsed.dim)


def show(value: Value, unit: str) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    factor = u.parse_unit(unit).factor if unit not in u.SPECIAL else 1.0
    return f"{value.value / factor:.6g}"


def evaluate(spec: Spec, overrides: dict[str, Any], tracker: Tracker | None, case: str) -> dict[str, Value]:
    env: dict[str, Value] = {}
    for name, item in spec.inputs.items():
        env[name] = to_value(overrides.get(name, item.value), item.unit, f"入力 {name}")
    for step in spec.steps:
        evaluator = Evaluator(step.name, step.expr, tracker, case)
        try:
            result = evaluator.run(step.tree, env)
        except CalcError as exc:
            raise CalcError(f"ステップ {step.name}: {exc}") from exc
        kind = kind_of(step.unit)
        if kind == "bool" and not isinstance(result, bool):
            raise CalcError(f"ステップ {step.name}: 単位が bool なのに、結果が判定 (真偽) ではありません")
        if kind == "text" and not isinstance(result, str):
            raise CalcError(f"ステップ {step.name}: 単位が text なのに、結果が文字ではありません")
        if kind == "number":
            if not is_q(result):
                raise CalcError(f"ステップ {step.name}: 結果が数ではありません (判定なら unit を bool に)")
            declared = u.parse_unit(step.unit)
            if result.dim != declared.dim:
                raise CalcError(f"ステップ {step.name}: 式の単位は {u.describe_dim(result.dim)} で、書いた単位 {step.unit} ({u.describe_dim(declared.dim)}) と合いません")
        env[step.name] = result
    return env


def literal_notes(spec: Spec) -> list[str]:
    notes = []
    for step in spec.steps:
        numbers = []
        for node in ast.walk(step.tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                numbers.append(float(node.value))
        for number in dict.fromkeys(numbers):
            if any(math.isclose(number, c, rel_tol=1e-9) for c in CONVERSION_LIKE):
                notes.append(f"ステップ {step.name} の式の数 {number:g} は、単位の換算か定数に見えます。換算なら unit に任せて消す。定数 (重力加速度など) なら、出典つきの入力にする")
            elif number not in PLAIN_NUMBERS and not step.source:
                notes.append(f"ステップ {step.name} の式の係数 {number:g} に、出典がありません (steps の source に書く)")
    return notes


def run_check(spec: Spec) -> tuple[list[str], list[str], list[str]]:
    """(報告の Markdown の行, 問題, 注意)。"""
    problems: list[str] = []
    notes: list[str] = []
    tracker = Tracker()
    lines = [f"# 計算の検査: {spec.title}", ""]
    missing = [i.name for i in spec.inputs.values() if not i.source]
    if missing:
        problems.append("出典の無い入力: " + ", ".join(missing) + " (原典の場所を source に書く。仮定なら「仮定: 理由」と書く)")
    assumptions = [i for i in spec.inputs.values() if i.source.startswith("仮定")]
    lines += ["## 入力", "", "| 名前 | 値 | 単位 | 出典 |", "|---|---|---|---|"]
    lines += [f"| {i.name} | {i.value} | {i.unit} | {i.source or '(なし)'} |" for i in spec.inputs.values()]
    base: dict[str, Value] | None = None
    try:
        base = evaluate(spec, {}, tracker, "基本")
    except CalcError as exc:
        problems.append(str(exc))
    lines += ["", "## 計算 (基本の入力)", "", "| 名前 | 式 | 値 | 単位 | 備考 |", "|---|---|---|---|---|"]
    for step in spec.steps:
        shown = show(base[step.name], step.unit) if base and step.name in base else "-"
        mark = " **(出力)**" if step.name in spec.outputs else ""
        lines.append(f"| {step.name}{mark} | `{step.expr}` | {shown} | {step.unit} | {step.note}{(' 出典: ' + step.source) if step.source else ''} |")
    lines += ["", "## 検算の例", ""]
    real_cases = [c for c in spec.cases if c.source and not c.source.startswith("仮定")]
    if not spec.cases:
        problems.append("検算の例 (cases) がありません。原典の計算例・過去の計算書・手計算の例を、出典つきで 1 つ以上入れる")
    elif not real_cases:
        problems.append("出典のある検算の例がありません (例の source に、原典の場所を書く)")
    bool_seen: dict[str, set[bool]] = {s.name: set() for s in spec.steps if s.unit == "bool" and s.name in spec.outputs}
    if base:
        for name in bool_seen:
            bool_seen[name].add(bool(base[name]))
    for case in spec.cases:
        try:
            env = evaluate(spec, case.inputs, tracker, case.name)
        except (CalcError, SpecError) as exc:
            problems.append(f"例「{case.name}」: {exc}")
            lines.append(f"- ✗ 例「{case.name}」: 計算できません ({exc})")
            continue
        for name in bool_seen:
            bool_seen[name].add(bool(env[name]))
        results = []
        for name, raw in case.expect.items():
            unit = spec.inputs[name].unit if name in spec.inputs else next(s.unit for s in spec.steps if s.name == name)
            try:
                expected = to_value(raw, unit, f"例「{case.name}」の期待値 {name}")
            except SpecError as exc:
                problems.append(str(exc))
                continue
            actual = env[name]
            if is_q(expected):
                factor = u.parse_unit(unit).factor
                a, e = actual.value / factor, expected.value / factor
                ok = abs(a - e) <= case.tol * abs(e) if e != 0 else abs(a) <= max(case.tol, 1e-12)
                diff = f"差 {abs(a - e) / abs(e) * 100:.2g}%" if e != 0 else f"差 {abs(a):.3g}"
                results.append((ok, f"{name} = {a:.6g} {unit} (期待 {e:.6g}、{diff})"))
            else:
                ok = actual == expected
                results.append((ok, f"{name} = {show(actual, unit)} (期待 {show(expected, unit)})"))
        passed = all(ok for ok, _ in results)
        lines.append(f"- {'✓' if passed else '✗'} 例「{case.name}」(出典: {case.source or 'なし'}): " + "; ".join(text for _, text in results))
        if not passed:
            problems.append(f"例「{case.name}」が合いません: " + "; ".join(text for ok, text in results if not ok))
    lines += ["", "## 場合分けの網羅", ""]
    if not tracker.labels:
        lines.append("(場合分けはありません)")
    for key, (label, sides) in tracker.labels.items():
        seen = tracker.seen[key]
        parts = [f"{side}: {', '.join(sorted(seen[side])) or 'なし'}" for side in sides]
        lines.append(f"- {key[0]} の `{label}` — " + " / ".join(parts))
        empty = [side for side in sides if not seen[side]]
        if empty:
            notes.append(f"{key[0]} の `{label}` で、{'・'.join(empty)} の側を通る例がありません (その側の検算の例を足す)")
    for name, seen_values in bool_seen.items():
        if len(seen_values) == 1:
            missing_side = "false" if True in seen_values else "true"
            notes.append(f"判定 {name} が {missing_side} になる例がありません (合格と不合格の両方の例で確かめる)")
    notes += literal_notes(spec)
    if assumptions:
        notes.append("仮定の入力: " + "; ".join(f"{i.name} = {i.value} {i.unit} ({i.source})" for i in assumptions) + " (報告に、仮定として書く)")
    lines += ["", "## 結果", "", f"**{'合格' if not problems else '不合格'}** (問題 {len(problems)} 件、注意 {len(notes)} 件)"]
    if problems:
        lines += ["", "### 問題 (直すまで、計算の結果を使わない)", ""] + [f"- {p}" for p in problems]
    if notes:
        lines += ["", "### 注意", ""] + [f"- {n}" for n in notes]
    return lines, problems, notes


TEMPLATE = {
    "title": "ボルトのせん断の検討 (例)",
    "inputs": {
        "F": {"value": 12, "unit": "kN", "source": "荷重表.xlsx シート「荷重」B4"},
        "d": {"value": 16, "unit": "mm", "source": "図面 A-102 の部品表"},
        "n": {"value": 2, "unit": "-", "source": "図面 A-102 (ボルトの本数)"},
        "tau_a": {"value": 80, "unit": "MPa", "source": "設計基準.pdf p.12 表 3"},
    },
    "steps": [
        {"name": "A", "expr": "pi * d**2 / 4", "unit": "mm2", "note": "1 本の断面積"},
        {"name": "tau", "expr": "F / (n * A)", "unit": "MPa", "note": "せん断応力"},
        {"name": "ok", "expr": "tau <= tau_a", "unit": "bool", "note": "判定"},
    ],
    "outputs": ["tau", "ok"],
    "cases": [
        {"name": "基準の計算例", "source": "設計基準.pdf p.15 例題 2", "inputs": {"F": 10, "d": 12, "n": 1}, "expect": {"tau": 88.4, "ok": False}},
        {"name": "過去の計算書", "source": "2024 計算書.pdf p.3", "inputs": {"F": [8000, "N"]}, "expect": {"tau": [19.9, "N/mm2"], "ok": True}},
    ],
}


def check_out_path(raw: str, cwd: Path) -> Path:
    """--out は、作業フォルダ直下の .handoff/ の中で、まだ無い場所だけを許す (`..` やリンクを解いた後の場所で判定する)。"""
    path = Path(raw)
    path = path if path.is_absolute() else cwd / path
    base = os.path.normcase(os.path.realpath(cwd / ".handoff"))
    parent = os.path.normcase(os.path.realpath(path.parent))
    if path.name in ("", ".", "..") or not (parent == base or parent.startswith(base.rstrip("\\/") + os.sep)):
        raise SpecError("--out は、作業フォルダ直下の .handoff フォルダの中だけ指定できます (例: .handoff/計算_ボルト.md)")
    target = Path(os.path.realpath(path.parent)) / path.name
    if target.exists() or path.exists():
        raise SpecError(f"'{raw}' はすでにあります。別の名前にしてください")
    return target


def load_spec_file(raw: str, cwd: Path) -> Spec:
    path = Path(raw)
    path = path if path.is_absolute() else cwd / path
    if not path.is_file():
        raise SpecError(f"計算書がありません: {raw}")
    resolved = os.path.normcase(os.path.realpath(path))
    root = os.path.normcase(os.path.realpath(cwd))
    if not (resolved == root or resolved.startswith(root.rstrip("\\/") + os.sep)):
        raise SpecError(f"'{raw}' は、いまのフォルダの外です (計算書は、作業フォルダの中に置く)")
    if path.stat().st_size > MAX_SPEC_BYTES:
        raise SpecError("計算書が大きすぎます")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except ValueError as exc:
        raise SpecError(f"計算書が JSON として読めません: {exc}") from exc
    return parse_spec(data)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="calc_cli", description="計算書 (JSON) を、出典・単位・検算の例で確かめる (読み取りと .handoff への書き込みだけ)")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check", help="計算書を検査して計算する")
    check.add_argument("spec", help="計算書 (JSON) のパス")
    check.add_argument("--out", help="結果を .handoff/ の中のファイルに書く")
    sub.add_parser("template", help="計算書のひな形を表示する")
    sub.add_parser("units", help="使える単位の一覧")
    return parser


def main(argv: list[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None, cwd: Path | None = None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if stdout is None:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")  # Windows の標準出力の文字化けを防ぐ
    args = build_parser().parse_args(argv)
    work = (cwd or Path.cwd()).resolve()
    try:
        if args.command == "template":
            out.write(json.dumps(TEMPLATE, ensure_ascii=False, indent=2) + "\n")
            return 0
        if args.command == "units":
            out.write(u.units_help() + "\n")
            return 0
        spec = load_spec_file(args.spec, work)
        lines, problems, notes = run_check(spec)
        text = "\n".join(lines) + "\n"
        if args.out:
            target = check_out_path(args.out, work)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8", newline="\n")
            out.write(f"出力: {target} ({len(lines)} 行)\n")
            out.write(f"結果: {'合格' if not problems else '不合格'} (問題 {len(problems)} 件、注意 {len(notes)} 件)\n")
            out.write("".join(f"問題: {p}\n" for p in problems))
            out.write("".join(f"注意: {n}\n" for n in notes))
        else:
            out.write(text)
        return 1 if problems else 0
    except SpecError as exc:
        err.write(f"エラー: {exc}\n")
        return 2
    except OSError as exc:
        err.write(f"エラー: ファイルを読み書きできません: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
