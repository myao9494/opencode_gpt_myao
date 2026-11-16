"""ブラウザ (Edge / Chrome) を開いたまま、タブにつなぎ直して操作するコマンド (スキル browser-session から呼ぶ)。

  start    : 自動化の専用のプロファイルで、ブラウザを起動する (デバッグの口は 127.0.0.1 だけ)。起動済みなら、それを使う
  status   : ブラウザと、作業中のタブ
  tabs     : タブの一覧
  open     : 新しいタブで URL を開き、作業中のタブにする
  use      : 既存のタブ (tabs の番号) を、作業中のタブにする
  snapshot : 作業中のタブの要素の一覧 (見出し・入力欄・ボタン・リンク・表。指定に使えるセレクタつき)
  text     : 作業中のタブの文字
  run      : Python のスクリプトを、作業中のタブにつないで実行する (page が渡される)。--allow-submit で送信などを許す
  close    : 作業中のタブを閉じる

ブラウザもタブも、コマンドが終わっても閉じない。作業中のタブの ID は .handoff/browser/session.json に残り、
次のコマンドは、そのタブにつなぎ直す (ログインや入力の状態が、そのまま続く)。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import traceback
from pathlib import Path
from typing import Any, TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))

from browser_lib import DangerousAction, Page  # noqa: E402
from cdp import Cdp, CdpError, http_json, http_text  # noqa: E402

SKILL_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 9222


class CliError(Exception):
    pass


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or SKILL_DIR / "config.local.json"
    config: dict[str, Any] = {"browser": "edge", "port": DEFAULT_PORT, "executable": "", "profile_dir": ""}
    if target.is_file():
        try:
            config.update(json.loads(target.read_text(encoding="utf-8-sig")))
        except (OSError, ValueError) as exc:
            raise CliError(f"設定ファイルを読めません: {target}: {exc}") from exc
    if config["browser"] not in ("edge", "chrome"):
        raise CliError("browser は edge か chrome にしてください")
    port = int(config["port"])
    if not 1024 <= port <= 65535:
        raise CliError("port は 1024〜65535 にしてください")
    config["port"] = port
    return config


def find_executable(browser: str, configured: str) -> str:
    if configured:
        if not Path(configured).is_file():
            raise CliError(f"設定の executable がありません: {configured}")
        return configured
    system = platform.system()
    candidates: list[str] = []
    if system == "Windows":
        roots = [os.environ.get(k, "") for k in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
        tail = r"Microsoft\Edge\Application\msedge.exe" if browser == "edge" else r"Google\Chrome\Application\chrome.exe"
        candidates = [str(Path(r) / tail) for r in roots if r]
    elif system == "Darwin":
        candidates = ["/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"] if browser == "edge" else ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
    else:
        names = ["microsoft-edge", "microsoft-edge-stable"] if browser == "edge" else ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]
        candidates = [p for p in (shutil.which(n) for n in names) if p]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise CliError(f"{'Edge' if browser == 'edge' else 'Chrome'} が見つかりません。config.local.json の executable に場所を書く (または browser を変える)")


def profile_dir(config: dict[str, Any]) -> Path:
    if config.get("profile_dir"):
        return Path(config["profile_dir"]).expanduser()
    base = Path(os.environ["LOCALAPPDATA"]) if platform.system() == "Windows" and os.environ.get("LOCALAPPDATA") else Path.home()
    return base / ".opencode-browser" / f"{config['browser']}-profile"


def browser_version(port: int) -> dict[str, Any] | None:
    try:
        return http_json(port, "/json/version", timeout=1.5)
    except CdpError:
        return None


def page_targets(port: int) -> list[dict[str, Any]]:
    return [t for t in http_json(port, "/json/list") if t.get("type") == "page"]


class Session:
    """作業フォルダの .handoff/browser/session.json (作業中のタブの ID)。"""

    def __init__(self, cwd: Path) -> None:
        self.dir = cwd / ".handoff" / "browser"
        self.path = self.dir / "session.json"

    def load(self) -> dict[str, Any]:
        if self.path.is_file():
            try:
                return json.loads(self.path.read_text(encoding="utf-8"))
            except ValueError:
                return {}
        return {}

    def save(self, data: dict[str, Any]) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def require_browser(port: int) -> dict[str, Any]:
    version = browser_version(port)
    if version is None:
        raise CliError(f"ブラウザが起動していません (127.0.0.1:{port})。先に start")
    return version


def current_target(port: int, session: Session) -> dict[str, Any]:
    require_browser(port)
    target_id = session.load().get("target_id")
    if not target_id:
        raise CliError("作業中のタブがありません。open <URL> で開くか、tabs → use <番号>")
    for target in page_targets(port):
        if target.get("id") == target_id:
            return target
    raise CliError("作業中のタブが閉じられています。open <URL> で開き直すか、tabs → use <番号>")


def connect(port: int, session: Session, allow_submit: bool = False) -> Page:
    target = current_target(port, session)
    ws = target.get("webSocketDebuggerUrl")
    if not ws:
        raise CliError("このタブには、つなげません (DevTools で開かれている可能性。DevTools を閉じる)")
    return Page(Cdp(ws), port, target["id"], session.dir, allow_submit)


def describe_target(target: dict[str, Any]) -> str:
    return f"{(target.get('title') or '(題名なし)')[:60]} | {target.get('url', '')[:120]}"


def cmd_start(config: dict[str, Any], headless: bool, out: TextIO) -> int:
    port = config["port"]
    version = browser_version(port)
    if version:
        out.write(f"起動しています: {version.get('Browser')} (127.0.0.1:{port})。そのまま使います\n")
        return 0
    exe = find_executable(config["browser"], config.get("executable", ""))
    profile = profile_dir(config)
    profile.mkdir(parents=True, exist_ok=True)
    args = [exe, f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1", f"--user-data-dir={profile}",
            "--no-first-run", "--no-default-browser-check"] + (["--headless=new"] if headless else []) + ["about:blank"]
    flags: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if platform.system() == "Windows":
        flags["creationflags"] = 0x00000008 | 0x00000200  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP (コマンドが終わっても閉じない)
    else:
        flags["start_new_session"] = True
    subprocess.Popen(args, **flags)  # noqa: S603 (決まった引数でブラウザを起動するだけ。シェルは使わない)
    for _ in range(60):
        time.sleep(0.25)
        version = browser_version(port)
        if version:
            out.write(f"起動しました: {version.get('Browser')} (127.0.0.1:{port})\n  プロファイル: {profile} (自動化の専用。ログインは、ここに残る)\n")
            return 0
    raise CliError(f"ブラウザを起動しましたが、127.0.0.1:{port} に応答がありません。同じプロファイルのブラウザが、デバッグの口なしで開いていないか確かめる")


def cmd_status(port: int, session: Session, out: TextIO) -> int:
    version = browser_version(port)
    if not version:
        out.write(f"ブラウザ: 起動していません (127.0.0.1:{port})。start で起動する\n")
        return 3
    out.write(f"ブラウザ: {version.get('Browser')} (127.0.0.1:{port})\n")
    target_id = session.load().get("target_id")
    current = next((t for t in page_targets(port) if t.get("id") == target_id), None) if target_id else None
    out.write(f"作業中のタブ: {describe_target(current) if current else 'なし (open か use で決める)'}\n")
    return 0


def cmd_tabs(port: int, session: Session, out: TextIO) -> int:
    require_browser(port)
    target_id = session.load().get("target_id")
    targets = page_targets(port)
    for index, target in enumerate(targets, start=1):
        out.write(f"{'*' if target.get('id') == target_id else ' '} {index}. {describe_target(target)}\n")
    if not targets:
        out.write("(タブがありません)\n")
    return 0


def cmd_open(port: int, session: Session, url: str, out: TextIO) -> int:
    require_browser(port)
    if not url.lower().startswith(("http://", "https://")) and url != "about:blank":
        raise CliError(f"開けるのは http:// か https:// の URL だけです: {url}")
    from urllib.parse import quote
    try:
        target = http_json(port, "/json/new?" + quote(url, safe=":/?&=%#"), method="PUT")
    except CdpError:
        target = http_json(port, "/json/new?" + quote(url, safe=":/?&=%#"))
    data = session.load()
    data.update({"port": port, "target_id": target["id"]})
    data.setdefault("opened", []).append(target["id"])
    session.save(data)
    page = connect(port, session)
    try:
        page.wait_load(30)
        out.write(f"開きました (作業中のタブ): {page.info()}\n")
    finally:
        page.close()
    return 0


def cmd_use(port: int, session: Session, number: int, out: TextIO) -> int:
    require_browser(port)
    targets = page_targets(port)
    if not 1 <= number <= len(targets):
        raise CliError(f"番号は 1〜{len(targets)} です (tabs で確かめる)")
    data = session.load()
    data.update({"port": port, "target_id": targets[number - 1]["id"]})
    session.save(data)
    out.write(f"作業中のタブにしました: {describe_target(targets[number - 1])}\n")
    return 0


def cmd_read(port: int, session: Session, what: str, selector: str | None, limit: int, out: TextIO) -> int:
    page = connect(port, session)
    try:
        out.write((page.snapshot(limit) if what == "snapshot" else page.text(selector, limit)) + "\n")
    finally:
        page.close()
    return 0


def resolve_script(raw: str, cwd: Path) -> Path:
    path = Path(raw)
    path = path if path.is_absolute() else cwd / path
    if not path.is_file() or path.suffix.lower() != ".py":
        raise CliError(f"スクリプト (.py) がありません: {raw}")
    if cwd.resolve() not in path.resolve().parents:
        raise CliError("スクリプトは、作業フォルダの中に置く (例: .handoff/browser/01_検索.py)")
    return path.resolve()


def cmd_run(port: int, session: Session, script: str, script_args: list[str], allow_submit: bool, cwd: Path, out: TextIO, err: TextIO) -> int:
    path = resolve_script(script, cwd)
    page = connect(port, session, allow_submit)
    namespace: dict[str, Any] = {"__name__": "__browser_script__", "__file__": str(path), "page": page, "args": script_args,
                                 "out_dir": session.dir, "DangerousAction": DangerousAction}
    code = compile(path.read_text(encoding="utf-8-sig"), str(path), "exec")
    old_stdout = sys.stdout
    sys.stdout = out
    try:
        exec(code, namespace)  # noqa: S102 (ユーザーの承認を得て実行する、作業フォルダのスクリプト)
        status = 0
    except DangerousAction as exc:
        err.write(f"止めました: {exc}\n")
        status = 4
    except Exception as exc:  # noqa: BLE001 (スクリプトの誤りを、行番号つきで返す)
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == str(path)]
        where = f" ({path.name} の {frames[-1].lineno} 行目: {frames[-1].line})" if frames else ""
        err.write(f"スクリプトのエラー{where}: {type(exc).__name__}: {exc}\n")
        status = 1
    finally:
        sys.stdout = old_stdout
    stopped = page.blocked()
    if stopped:
        for item in stopped:
            err.write(f"止めました (ページの中の見張り): {item}。依頼に書かれた操作なら --allow-submit を付けて実行する (書かれていなければ、人に聞く)\n")
        status = 4 if status in (0, 1) else status
    try:
        out.write(f"--- 作業中のタブ: {page.info()}\n")
    except CdpError:
        out.write("--- 作業中のタブの状態を読めませんでした\n")
    finally:
        page.close()
    return status


def cmd_close(port: int, session: Session, out: TextIO) -> int:
    target = current_target(port, session)
    data = session.load()
    if target["id"] not in data.get("opened", []):
        raise CliError("このタブは、自動化で開いたタブではないので閉じません (人が閉じる)")
    http_text(port, f"/json/close/{target['id']}")
    data["target_id"] = ""
    session.save(data)
    out.write(f"閉じました: {describe_target(target)}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="browser_cli", description="ブラウザを開いたまま、タブにつなぎ直して操作する")
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("start", help="専用のプロファイルでブラウザを起動する (起動済みなら使う)")
    start.add_argument("--headless", action="store_true", help="画面を出さない (試験用)")
    sub.add_parser("status", help="ブラウザと作業中のタブ")
    sub.add_parser("tabs", help="タブの一覧")
    open_ = sub.add_parser("open", help="新しいタブで開いて、作業中のタブにする")
    open_.add_argument("url")
    use = sub.add_parser("use", help="既存のタブを、作業中のタブにする")
    use.add_argument("number", type=int)
    snapshot = sub.add_parser("snapshot", help="要素の一覧 (見出し・入力欄・ボタン・リンク・表)")
    snapshot.add_argument("--limit", type=int, default=150)
    text = sub.add_parser("text", help="見えている文字")
    text.add_argument("--selector")
    text.add_argument("--limit", type=int, default=20000)
    run = sub.add_parser("run", help="スクリプトを、作業中のタブにつないで実行する")
    run.add_argument("script")
    run.add_argument("--allow-submit", action="store_true", help="送信・確定などのボタンと、POST のフォームの Enter を許す (依頼に書かれた操作だけ)")
    run.add_argument("script_args", nargs="*")
    sub.add_parser("close", help="作業中のタブを閉じる (自動化で開いたタブだけ)")
    return parser


def main(argv: list[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None, cwd: Path | None = None, config_path: Path | None = None) -> int:
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    if stdout is None:
        for stream in (sys.stdout, sys.stderr):
            if hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8")  # Windows の標準出力の文字化けを防ぐ
    args = build_parser().parse_args(argv)
    work = (cwd or Path.cwd()).resolve()
    try:
        config = load_config(config_path)
        port, session = config["port"], Session(work)
        if args.command == "start":
            return cmd_start(config, args.headless, out)
        if args.command == "status":
            return cmd_status(port, session, out)
        if args.command == "tabs":
            return cmd_tabs(port, session, out)
        if args.command == "open":
            return cmd_open(port, session, args.url, out)
        if args.command == "use":
            return cmd_use(port, session, args.number, out)
        if args.command in ("snapshot", "text"):
            return cmd_read(port, session, args.command, getattr(args, "selector", None), args.limit, out)
        if args.command == "run":
            return cmd_run(port, session, args.script, args.script_args, args.allow_submit, work, out, err)
        return cmd_close(port, session, out)
    except (CliError, CdpError) as exc:
        err.write(f"エラー: {exc}\n")
        return 2
    except OSError as exc:
        err.write(f"エラー: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
