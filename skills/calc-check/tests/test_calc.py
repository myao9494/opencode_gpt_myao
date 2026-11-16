"""calc_cli のテスト。実行: python -m unittest discover -s skills/calc-check/tests -v   (リポジトリのルートから)"""
from __future__ import annotations

import copy
import io
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

import calc_cli as cli  # noqa: E402
import units as u  # noqa: E402


class UnitTests(unittest.TestCase):
    def test_よく使う単位の倍率と次元(self) -> None:
        cases = {
            "kN": (1e3, u.FORCE), "N/mm2": (1e6, u.PRESSURE), "MPa": (1e6, u.PRESSURE), "N/mm^2": (1e6, u.PRESSURE),
            "mm2": (1e-6, u.dim(m=2)), "mm⁴": (1e-12, u.dim(m=4)), "kN*m": (1e3, u.dim(kg=1, m=2, s=-2)), "kN·m": (1e3, u.dim(kg=1, m=2, s=-2)),
            "m/s2": (1.0, u.dim(m=1, s=-2)), "kg/m3": (1.0, u.dim(kg=1, m=-3)), "kgf": (9.80665, u.FORCE), "t": (1e3, u.dim(kg=1)),
            "min": (60.0, u.dim(s=1)), "ms": (1e-3, u.dim(s=1)), "deg": (math.pi / 180, u.NONE), "%": (0.01, u.NONE), "-": (1.0, u.NONE),
            "1/s": (1.0, u.dim(s=-1)), "kN/m": (1e3, u.dim(kg=1, s=-2)), "μm": (1e-6, u.dim(m=1)), "GPa": (1e9, u.PRESSURE),
        }
        for text, (factor, dimension) in cases.items():
            unit = u.parse_unit(text)
            self.assertTrue(math.isclose(unit.factor, factor, rel_tol=1e-12), text)
            self.assertEqual(unit.dim, dimension, text)

    def test_分からない単位と摂氏とかっこ(self) -> None:
        for bad in ("kgg", "℃", "degC", "kg/(m*s2)", "N//mm", "kt"):
            with self.assertRaises(u.UnitError, msg=bad):
                u.parse_unit(bad)


def spec(**changes: object) -> dict:
    data = copy.deepcopy(cli.TEMPLATE)
    data.update(changes)
    return data


class CheckTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cwd = Path(self._tmp.name).resolve()

    def run_cli(self, data: object, *extra: str) -> tuple[int, str, str]:
        (self.cwd / "calc.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        code = cli.main(["check", "calc.json", *extra], stdout=out, stderr=err, cwd=self.cwd)
        return code, out.getvalue(), err.getvalue()

    def test_ひな形は合格_単位を混ぜても_SIで正しく計算する(self) -> None:
        code, out, err = self.run_cli(spec())
        self.assertEqual(code, 0, out + err)
        self.assertIn("| tau **(出力)** | `F / (n * A)` | 29.8416 | MPa |", out)
        self.assertIn("**合格** (問題 0 件、注意 0 件)", out)

    def test_ひな形を表示し_単位の一覧を出す(self) -> None:
        out = io.StringIO()
        self.assertEqual(cli.main(["template"], stdout=out, stderr=io.StringIO(), cwd=self.cwd), 0)
        self.assertEqual(json.loads(out.getvalue()), cli.TEMPLATE)
        out = io.StringIO()
        self.assertEqual(cli.main(["units"], stdout=out, stderr=io.StringIO(), cwd=self.cwd), 0)
        self.assertIn("N/mm2", out.getvalue())

    def test_単位の換算を式に書くと_検算で見つかり_数の注意も出る(self) -> None:
        data = spec()
        data["steps"][1]["expr"] = "F * 1000 / (n * A)"  # kN を N に直したつもり (内部は SI なので 1000 倍ずれる)
        code, out, _ = self.run_cli(data)
        self.assertEqual(code, 1)
        self.assertIn("例「基準の計算例」が合いません", out)
        self.assertIn("1000 は、単位の換算か定数に見えます", out)

    def test_次元の合わない足し算_比較_ステップの単位は不合格(self) -> None:
        for expr, unit, needle in (("F + d", "kN", "足し算・引き算"), ("F / (n * A)", "kN", "書いた単位 kN"), ("tau <= d", "bool", "比較")):
            data = spec()
            target = 2 if unit == "bool" else 1
            data["steps"][target]["expr"] = expr
            data["steps"][target]["unit"] = unit
            code, out, _ = self.run_cli(data)
            self.assertEqual(code, 1, expr)
            self.assertIn(needle, out, expr)
            self.assertIn("**不合格**", out)

    def test_出典の無い入力と_検算の例が無いものは不合格_仮定は注意(self) -> None:
        data = spec()
        data["inputs"]["F"]["source"] = ""
        data["inputs"]["d"]["source"] = "仮定: 図面が無いので M16 とした"
        data["cases"] = []
        code, out, _ = self.run_cli(data)
        self.assertEqual(code, 1)
        self.assertIn("出典の無い入力: F", out)
        self.assertIn("検算の例 (cases) がありません", out)
        self.assertIn("仮定の入力: d = 16 mm (仮定: 図面が無いので M16 とした)", out)
        data = spec()
        for case in data["cases"]:
            case["source"] = "仮定: 自分で考えた"
        code, out, _ = self.run_cli(data)
        self.assertIn("出典のある検算の例がありません", out)

    def test_場合分けの網羅と_判定の両側(self) -> None:
        data = spec()
        data["steps"].insert(1, {"name": "k", "expr": "1.0 if d <= qty(20, 'mm') else 0.9", "unit": "-", "note": "径による低減", "source": "設計基準 表 4"})
        data["steps"][2]["expr"] = "F / (n * A * k)"
        data["cases"] = [data["cases"][1]]  # 判定が true になる例だけ
        code, out, _ = self.run_cli(data)
        self.assertEqual(code, 0, out)
        self.assertIn("偽 の側を通る例がありません", out)
        self.assertIn("判定 ok が false になる例がありません", out)
        self.assertNotIn("係数 0.9 に、出典がありません", out)  # source があるので
        data["steps"][1]["source"] = ""
        _, out, _ = self.run_cli(data)
        self.assertIn("係数 0.9 に、出典がありません", out)

    def test_場合分けの両側の単位も確かめる_minも記録する_数と長さは比べない(self) -> None:
        data = spec()
        data["steps"][1]["expr"] = "F / (n * A) if n > 1 else F"
        code, out, _ = self.run_cli(data)
        self.assertEqual(code, 1)
        self.assertIn("両側の単位が合いません", out)
        data = spec()
        data["steps"].append({"name": "tau_d", "expr": "min(tau, tau_a)", "unit": "MPa"})
        code, out, _ = self.run_cli(data)
        self.assertEqual(code, 0, out)
        self.assertIn("- tau_d の `min(tau, tau_a)` — tau: 基本, 過去の計算書 / tau_a: 基準の計算例", out)
        data = spec()
        data["steps"][2]["expr"] = "tau <= 80"  # 単位の無い数と、応力を比べている
        code, out, _ = self.run_cli(data)
        self.assertEqual(code, 1)
        self.assertIn("比較「tau <= 80」の単位が合いません", out)
        self.assertNotIn("になる例がありません", out)  # 計算できないときは、判定の網羅を言わない

    def test_経験式は_valとqtyで単位を明示する(self) -> None:
        data = {
            "title": "経験式",
            "inputs": {"fy": {"value": 235, "unit": "N/mm2", "source": "JIS"}, "t": {"value": 12, "unit": "mm", "source": "図面"}},
            "steps": [{"name": "P", "expr": "qty(3.5 * val(fy, 'MPa')**0.5 * val(t, 'mm'), 'N')", "unit": "kN", "source": "社内基準 3.2 式 (5)"}],
            "cases": [{"name": "基準の例", "source": "社内基準 3.2 例", "expect": {"P": 0.6439}}],
        }
        code, out, _ = self.run_cli(data)
        self.assertEqual(code, 0, out)
        self.assertIn("| P **(出力)** |", out)
        data["steps"][0]["expr"] = "3.5 * fy**0.5 * t"  # 経験式に、単位の指定を付け忘れると、次元が合わない
        code, out, _ = self.run_cli(data)
        self.assertEqual(code, 1)
        self.assertIn("書いた単位 kN", out)

    def test_文字の入力と判定(self) -> None:
        data = {
            "title": "材料の選択",
            "inputs": {"mat": {"value": "SS400", "unit": "text", "source": "図面"}, "t": {"value": 12, "unit": "mm", "source": "図面"}},
            "steps": [{"name": "fy", "expr": "qty(235, 'MPa') if mat == 'SS400' else qty(325, 'MPa')", "unit": "MPa", "source": "JIS G 3101"}],
            "cases": [{"name": "SM490", "source": "JIS G 3106", "inputs": {"mat": "SM490"}, "expect": {"fy": 325}}],
        }
        code, out, _ = self.run_cli(data)
        self.assertEqual(code, 0, out)
        self.assertIn("真: 基本 / 偽: SM490", out)

    def test_Pythonとしては実行しない(self) -> None:
        for expr in ("__import__('os').system('echo x')", "open('a').read()", "[x for x in range(3)]", "d.__class__", "lambda: 1", "F % 2"):
            data = spec()
            data["steps"][0]["expr"] = expr
            code, out, err = self.run_cli(data)
            self.assertNotEqual(code, 0, expr)
        source = (SKILL_DIR / "scripts" / "calc_cli.py").read_text(encoding="utf-8")
        for needle in ("eval(", "exec(", "compile(", "subprocess", "os.system", "urlopen", "socket", ".unlink(", "rmtree"):
            self.assertNotIn(needle, source, needle)

    def test_大きすぎる計算と_0で割る(self) -> None:
        for expr, needle in (("d ** 200", "指数が大きすぎます"), ("F / (n - 2)", "0 で割っています")):
            data = spec()
            data["steps"][1]["expr"] = expr
            data["steps"][1]["unit"] = "MPa"
            code, out, _ = self.run_cli(data)
            self.assertEqual(code, 1, expr)
            self.assertIn(needle, out)

    def test_書き方の誤りは終了コード2(self) -> None:
        bad = [
            "[]", {"title": "x"}, spec(steps=[{"name": "A", "expr": "B * 2", "unit": "mm"}]),
            spec(steps=[{"name": "A", "expr": "d *", "unit": "mm"}]), spec(steps=[{"name": "pi", "expr": "d", "unit": "mm"}]),
            spec(cases=[{"name": "x", "source": "y", "expect": {"zz": 1}}]), spec(extra=1), spec(tol=2),
        ]
        bad.append(spec())
        bad[-1]["inputs"]["F"]["unit"] = "kgg"
        for data in bad:
            code, _, err = self.run_cli(data)
            self.assertEqual(code, 2, data)
            self.assertIn("エラー:", err)

    def test_outは_handoffの中だけ(self) -> None:
        code, out, _ = self.run_cli(spec(), "--out", ".handoff/計算.md")
        self.assertEqual(code, 0)
        self.assertIn("結果: 合格", out)
        self.assertIn("# 計算の検査", (self.cwd / ".handoff" / "計算.md").read_text(encoding="utf-8"))
        for bad in (".handoff/計算.md", "計算.md", ".handoff/../x.md"):
            code, _, err = self.run_cli(spec(), "--out", bad)
            self.assertEqual(code, 2, bad)

    def test_作業フォルダの外の計算書は読まない(self) -> None:
        outside = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: (outside / "a.json").unlink() if (outside / "a.json").exists() else None)
        (outside / "a.json").write_text(json.dumps(spec()), encoding="utf-8")
        err = io.StringIO()
        self.assertEqual(cli.main(["check", str(outside / "a.json")], stdout=io.StringIO(), stderr=err, cwd=self.cwd), 2)
        self.assertIn("いまのフォルダの外", err.getvalue())


if __name__ == "__main__":
    unittest.main()
