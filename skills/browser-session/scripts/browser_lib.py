"""自動化の Python スクリプトから使う、ページの操作 (スキル browser-session)。

スクリプトは `run.bat run <スクリプト>` で実行する。そのとき、作業中のタブにつながった `page` が渡される。
ブラウザもタブも閉じないので、次のスクリプトは、前のスクリプトが終わった画面の続きから動ける。

    page.goto("http://intranet/…")          # 開いて、読み込みを待つ
    print(page.snapshot())                   # 画面の要素の一覧 (見出し・入力欄・ボタン・リンクと、指定のしかた)
    page.fill("#keyword", "見積")            # 入力欄に書く
    page.click(text="検索")                  # 文字でボタンを押す (CSS セレクタでも可: page.click("#search"))
    page.wait_for(text="検索結果")
    rows = page.tables()[0]                  # 表を、行の配列で取る
    page.save_json(rows, "結果.json")        # .handoff/browser/ に保存する

Playwright も使える (同じタブ。自動の待ち、locator、iframe):

    pw = page.playwright()
    pw.get_by_label("検索語").fill("見積")
    pw.get_by_role("button", name="検索").click()

守ること (ここと、ページの中の見張りで、機械的に止める。Playwright からの操作も止まる):
  - 送信・購入・削除・申請などの文字のボタンは押さない。押してよいのは、run に --allow-submit を付けたときだけ
  - POST のフォームの中で Enter を押さない (送信になる)。同じく --allow-submit のときだけ
  - パスワード欄には書かない (人が入力する)
  - 保存は .handoff/browser/ の中だけ
"""
from __future__ import annotations

import base64
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

from cdp import Cdp, CdpError, http_json

DANGER_WORDS = re.compile(
    r"送信|購入|注文|削除|消去|確定|申請|承認|支払|決済|退会|解約|投稿|公開|発注|契約|振込|"
    r"\b(submit|send|delete|remove|buy|purchase|order|pay|checkout|confirm|publish|post|approve)\b",
    re.I)
KEYS = {
    "Enter": ("Enter", 13, "\r"), "Tab": ("Tab", 9, ""), "Escape": ("Escape", 27, ""), "Backspace": ("Backspace", 8, ""),
    "ArrowDown": ("ArrowDown", 40, ""), "ArrowUp": ("ArrowUp", 38, ""), "ArrowLeft": ("ArrowLeft", 37, ""),
    "ArrowRight": ("ArrowRight", 39, ""), "PageDown": ("PageDown", 34, ""), "PageUp": ("PageUp", 33, ""),
    "Home": ("Home", 36, ""), "End": ("End", 35, ""), "Delete": ("Delete", 46, ""), "Space": (" ", 32, " "),
}

# 要素を探す JS。selector か text (ボタン・リンク・ラベルなどの見える文字、aria-label、value) で探し、見えるものだけを返す
FIND_JS = r"""
(function(selector, text, nth, exact) {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  const label = el => norm(el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('title') || el.getAttribute('alt') || '');
  let items = [];
  if (selector) { items = Array.from(document.querySelectorAll(selector)); }
  else {
    const pool = Array.from(document.querySelectorAll('a,button,input[type=button],input[type=submit],input[type=reset],input[type=checkbox],input[type=radio],[role=button],[role=link],[role=tab],[role=menuitem],[role=option],label,summary,option,[onclick],td,th,li,span,div'));
    const want = norm(text);
    const hits = pool.filter(el => label(el) === want);
    items = hits.length || exact ? hits : pool.filter(el => label(el).includes(want) && label(el).length <= want.length * 4 + 20);
    // 内側の要素を優先する (div の中の button など、外側の重複を除く)
    items = items.filter(el => !items.some(o => o !== el && el.contains(o)));
  }
  items = items.filter(visible);
  const el = items[nth || 0];
  if (!el) return {found: false, count: items.length};
  el.scrollIntoView({block: 'center', inline: 'center'});
  const r = el.getBoundingClientRect();
  const form = el.form || el.closest('form');
  return {found: true, count: items.length, x: r.left + r.width / 2, y: r.top + r.height / 2,
          tag: el.tagName.toLowerCase(), type: (el.getAttribute('type') || '').toLowerCase(), text: label(el).slice(0, 80),
          disabled: !!el.disabled, form_method: form ? (form.getAttribute('method') || 'get').toLowerCase() : ''};
})
"""

SNAPSHOT_JS = r"""
(function(limit) {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const visible = el => { const r = el.getBoundingClientRect(); const st = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none'; };
  const esc = s => s.replace(/"/g, '\\"');
  const sel = el => {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const name = el.getAttribute('name');
    if (name && document.querySelectorAll(el.tagName + '[name="' + esc(name) + '"]').length === 1) return el.tagName.toLowerCase() + '[name="' + esc(name) + '"]';
    const aria = el.getAttribute('aria-label');
    if (aria) return el.tagName.toLowerCase() + '[aria-label="' + esc(aria) + '"]';
    const ph = el.getAttribute('placeholder');
    if (ph) return el.tagName.toLowerCase() + '[placeholder="' + esc(ph) + '"]';
    return '';
  };
  const labelOf = el => {
    if (el.labels && el.labels.length) return norm(el.labels[0].innerText);
    return norm(el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || '');
  };
  const out = [];
  out.push('題名: ' + document.title);
  out.push('URL: ' + location.href);
  const frames = document.querySelectorAll('iframe,frame').length;
  if (frames) out.push('注意: iframe が ' + frames + ' 個あります (中の要素は、この一覧に出ない)');
  const push = s => { if (out.length < limit) out.push(s); };
  document.querySelectorAll('h1,h2,h3').forEach(h => { if (visible(h) && norm(h.innerText)) push('見出し ' + h.tagName.toLowerCase() + ': ' + norm(h.innerText).slice(0, 80)); });
  document.querySelectorAll('input,textarea,select').forEach(el => {
    if (!visible(el)) return;
    const type = (el.getAttribute('type') || (el.tagName === 'INPUT' ? 'text' : el.tagName)).toLowerCase();
    if (['hidden'].includes(type)) return;
    let v = '';
    if (el.tagName === 'SELECT') v = ' 選択肢: ' + Array.from(el.options).slice(0, 8).map(o => norm(o.text)).join(' / ');
    else if (['checkbox', 'radio'].includes(type)) v = el.checked ? ' [選択中]' : ' [未選択]';
    else if (type === 'password') v = ' (パスワード: 人が入力する)';
    else if (el.value) v = ' 値: ' + norm(el.value).slice(0, 40);
    push('入力 ' + type + ' 「' + labelOf(el).slice(0, 40) + '」' + (sel(el) ? ' → ' + sel(el) : '') + v + (el.disabled ? ' (無効)' : ''));
  });
  document.querySelectorAll('button,input[type=submit],input[type=button],[role=button]').forEach(el => {
    if (!visible(el)) return;
    const t = norm(el.innerText || el.value || el.getAttribute('aria-label'));
    if (t) push('ボタン 「' + t.slice(0, 40) + '」' + (sel(el) ? ' → ' + sel(el) : '') + (el.disabled ? ' (無効)' : ''));
  });
  let links = 0;
  document.querySelectorAll('a[href]').forEach(a => {
    if (!visible(a) || links >= 40) return;
    const t = norm(a.innerText || a.getAttribute('aria-label'));
    if (!t) return;
    links += 1;
    push('リンク 「' + t.slice(0, 40) + '」 → ' + a.getAttribute('href').slice(0, 100));
  });
  document.querySelectorAll('table').forEach((t, i) => {
    if (!visible(t)) return;
    const head = Array.from((t.rows[0] || {cells: []}).cells).map(c => norm(c.innerText).slice(0, 20)).join(' | ');
    push('表 ' + (i + 1) + ': ' + t.rows.length + ' 行 / 先頭: ' + head);
  });
  if (out.length >= limit) out.push('(多いので、ここまで。page.text() や page.tables() で読む)');
  return out.join('\n');
})
"""

TABLES_JS = r"""
(function(selector) {
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  return Array.from(document.querySelectorAll(selector)).map(t => Array.from(t.rows).map(r => Array.from(r.cells).map(c => norm(c.innerText))));
})
"""


# ページの中の見張り。Playwright から押しても、page から押しても、ここで止まる (本物の操作の click と、submit と、パスワード欄への入力)。
# 押されたことにならないよう、既定の動作を止め、後続の処理にも伝えない。止めたものは __ocGuard.blocked に残し、run が報告する。
# スクリプトが JavaScript で form.submit() を呼ぶような、わざとの回避は防げない (うっかりの操作を止めるためのもの)。
GUARD_JS = r"""
(function(allow) {
  if (window.__ocGuard) { window.__ocGuard.allow = allow; window.__ocGuard.blocked = []; return; }
  const g = window.__ocGuard = {allow: allow, blocked: []};
  const WORDS = /送信|購入|注文|削除|消去|確定|申請|承認|支払|決済|退会|解約|投稿|公開|発注|契約|振込|\b(submit|send|delete|remove|buy|purchase|order|pay|checkout|confirm|publish|post|approve)\b/i;
  const norm = s => (s || '').replace(/\s+/g, ' ').trim();
  const label = el => norm(el.getAttribute('aria-label') || el.innerText || el.value || el.getAttribute('title') || '');
  const isPost = f => !!f && (f.getAttribute('method') || 'get').toLowerCase() === 'post';
  document.addEventListener('click', e => {
    if (g.allow || !e.isTrusted || !e.target || !e.target.closest) return;
    const el = e.target.closest('button,input[type=submit],input[type=button],input[type=image],a,[role=button],[role=menuitem]');
    if (!el) return;
    const text = label(el);
    const submits = el.matches('input[type=submit],input[type=image],button:not([type]),button[type=submit]');
    if (WORDS.test(text) || (submits && isPost(el.form || el.closest('form')))) {
      e.preventDefault(); e.stopImmediatePropagation(); g.blocked.push('押す: 「' + text.slice(0, 60) + '」');
    }
  }, true);
  document.addEventListener('submit', e => {
    if (g.allow || !isPost(e.target)) return;
    e.preventDefault(); e.stopImmediatePropagation(); g.blocked.push('POST のフォームの送信: ' + (e.target.getAttribute('action') || location.pathname));
  }, true);
  const noPassword = e => {
    if (e.target && e.target.type === 'password') { e.preventDefault(); e.stopImmediatePropagation(); g.blocked.push('パスワード欄への入力'); }
  };
  document.addEventListener('beforeinput', noPassword, true);
  document.addEventListener('keydown', e => { if (e.key && e.key.length === 1) noPassword(e); }, true);
  document.addEventListener('paste', noPassword, true);
})
"""


class DangerousAction(CdpError):
    pass


class Page:
    """作業中のタブ 1 つ。スクリプトには `page` として渡される。"""

    def __init__(self, cdp: Cdp, port: int, target_id: str, out_dir: Path, allow_submit: bool = False) -> None:
        self._cdp = cdp
        self.port = port
        self.target_id = target_id
        self.out_dir = out_dir
        self.allow_submit = allow_submit
        self._pw = None
        self._pw_page = None
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        guard = f"({GUARD_JS})({'true' if allow_submit else 'false'})"
        cdp.call("Page.addScriptToEvaluateOnNewDocument", source=guard)  # これから開くページ (移動の後) にも入れる
        try:
            self.js(guard)
        except CdpError:
            pass  # 読み込みの途中など。移動の後のページには入る

    def blocked(self) -> list[str]:
        """ページの中の見張りが止めた操作 (この run で)。"""
        try:
            return list(self.js("(window.__ocGuard && window.__ocGuard.blocked) || []") or [])
        except CdpError:
            return []

    def playwright(self) -> Any:
        """同じタブを、Playwright の Page として返す (自動の待ち、locator、iframe が使える)。ブラウザもタブも閉じない。

        page.playwright().get_by_role("button", name="検索").click() のように使う。送信などは、ページの中の見張りが止める。
        """
        if self._pw_page is not None:
            return self._pw_page
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise CdpError(f"Playwright が入っていません ({sys.executable})。ハーネスの .venv に requirements.lock を入れる (playwright install は要らない)。"
                           "入れられないときは page の部品だけで書く (自分でブラウザにつなぐスクリプトは書かない)") from exc
        self._pw = sync_playwright().start()
        browser = self._pw.chromium.connect_over_cdp(f"http://127.0.0.1:{self.port}")
        for context in browser.contexts:
            for candidate in context.pages:
                session = context.new_cdp_session(candidate)
                try:
                    target_id = session.send("Target.getTargetInfo")["targetInfo"]["targetId"]
                finally:
                    session.detach()
                if target_id == self.target_id:
                    candidate.set_default_timeout(15000)
                    self._pw_page = candidate
                    return candidate
        raise CdpError("Playwright から、作業中のタブが見つかりません")

    def close(self) -> None:
        """接続を切る (ブラウザもタブも閉じない)。"""
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001 (切るときの失敗は無視する)
                pass
            self._pw = self._pw_page = None
        self._cdp.close()

    # --- 基本 ---------------------------------------------------------------
    def cdp(self, method: str, **params: Any) -> dict[str, Any]:
        """CDP を直接呼ぶ (ヘルパーに無い操作のとき)。"""
        return self._cdp.call(method, **params)

    def js(self, expression: str, await_promise: bool = True) -> Any:
        """ページの中で JavaScript を実行し、値を返す (JSON にできる値だけ)。"""
        result = self._cdp.call("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=await_promise)
        if "exceptionDetails" in result:
            detail = result["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text")
            raise CdpError(f"ページの JavaScript でエラー: {text}")
        return (result.get("result") or {}).get("value")

    def _call_js(self, function: str, *args: Any) -> Any:
        return self.js(f"({function})({', '.join(json.dumps(a, ensure_ascii=False) for a in args)})")

    @property
    def url(self) -> str:
        return self.js("location.href")

    @property
    def title(self) -> str:
        return self.js("document.title")

    def info(self) -> str:
        return f"{self.title} | {self.url}"

    # --- 移動と待ち -----------------------------------------------------------
    def goto(self, url: str, timeout: float = 30.0) -> None:
        if not re.match(r"(?i)^(https?://|about:blank$)", url):
            raise CdpError(f"開けるのは http:// か https:// の URL だけです: {url}")
        result = self._cdp.call("Page.navigate", url=url)
        if result.get("errorText"):
            raise CdpError(f"開けませんでした: {url} ({result['errorText']})")
        self.wait_load(timeout)

    def wait_load(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if self.js("document.readyState") == "complete":
                    time.sleep(0.2)
                    return
            except CdpError:
                pass  # 移動の途中は、評価が失敗することがある
            time.sleep(0.2)
        raise CdpError(f"{timeout:g} 秒待っても、読み込みが終わりません")

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)

    def wait_for(self, selector: str | None = None, text: str | None = None, timeout: float = 10.0, gone: bool = False) -> None:
        """要素 (selector) か文字 (text。ページのどこかに出る) が現れるまで待つ。gone=True なら消えるまで待つ。"""
        if not selector and not text:
            raise CdpError("wait_for には selector か text が要ります")
        expr = (f"!!document.querySelector({json.dumps(selector)})" if selector
                else f"document.body && document.body.innerText.includes({json.dumps(text, ensure_ascii=False)})")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                if bool(self.js(expr)) != gone:
                    return
            except CdpError:
                pass
            time.sleep(0.25)
        raise CdpError(f"{timeout:g} 秒待っても、{'消えません' if gone else '見つかりません'}: {selector or text}")

    # --- 操作 -----------------------------------------------------------------
    def find(self, selector: str | None = None, text: str | None = None, nth: int = 0, exact: bool = False) -> dict[str, Any]:
        if not selector and not text:
            raise CdpError("selector か text を指定してください")
        info = self._call_js(FIND_JS, selector or "", text or "", nth, exact)
        if not info or not info.get("found"):
            what = selector or f"文字「{text}」"
            raise CdpError(f"見つかりません: {what} (見えている候補 {info.get('count', 0) if info else 0} 個。snapshot で確かめる)")
        return info

    def _guard(self, info: dict[str, Any], action: str) -> None:
        # フォームを送るボタン (button の既定と type=submit) は、POST のフォームのときだけ止める (GET の検索フォームは読み取り)
        submits = info.get("type") == "submit" or (info.get("tag") == "button" and info.get("type") == "")
        submitting = submits and info.get("form_method") == "post"
        if (DANGER_WORDS.search(info.get("text") or "") or submitting) and not self.allow_submit:
            raise DangerousAction(f"「{info.get('text')}」は、送信・確定などの操作に見えるので、{action}しません。"
                                  "依頼に書かれた操作なら、run に --allow-submit を付けて実行する (書かれていなければ、人に聞く)")

    def _mouse_click(self, x: float, y: float, clicks: int = 1) -> None:
        self._cdp.call("Input.dispatchMouseEvent", type="mouseMoved", x=x, y=y)
        for count in range(1, clicks + 1):
            self._cdp.call("Input.dispatchMouseEvent", type="mousePressed", x=x, y=y, button="left", clickCount=count)
            self._cdp.call("Input.dispatchMouseEvent", type="mouseReleased", x=x, y=y, button="left", clickCount=count)

    def click(self, selector: str | None = None, text: str | None = None, nth: int = 0, exact: bool = False, double: bool = False) -> None:
        """要素を押す (本物のマウスの操作として)。selector (CSS) か text (見える文字) で指定する。"""
        info = self.find(selector, text, nth, exact)
        if info.get("disabled"):
            raise CdpError(f"無効になっています: {info.get('text') or selector}")
        self._guard(info, "押")
        self._mouse_click(info["x"], info["y"], 2 if double else 1)
        time.sleep(0.2)

    def fill(self, selector: str, value: str, clear: bool = True) -> None:
        """入力欄に書く。パスワード欄には書かない。"""
        kind = self.js(f"(function(){{const e=document.querySelector({json.dumps(selector)});return e?((e.getAttribute('type')||e.tagName)+'').toLowerCase():null;}})()")
        if kind is None:
            raise CdpError(f"見つかりません: {selector}")
        if kind == "password":
            raise DangerousAction("パスワード欄には書きません。人に入力してもらう")
        if kind == "select":
            raise CdpError("選択の欄は select() を使う")
        self.js(f"(function(){{const e=document.querySelector({json.dumps(selector)});e.scrollIntoView({{block:'center'}});e.focus();"
                + ("if('value' in e){e.select&&e.select();}" if clear else "") + "})()")
        if clear:
            self.js(f"(function(){{const e=document.querySelector({json.dumps(selector)});if(e.isContentEditable){{e.textContent='';}}else{{e.value='';}}"
                    "e.dispatchEvent(new Event('input',{bubbles:true}));})()")
        self._cdp.call("Input.insertText", text=value)
        self.js(f"(function(){{const e=document.querySelector({json.dumps(selector)});e.dispatchEvent(new Event('change',{{bubbles:true}}));}})()")

    def select(self, selector: str, option: str) -> None:
        """選択の欄 (select) で、値か表示の文字が一致する選択肢を選ぶ。"""
        ok = self.js(f"""(function(){{const e=document.querySelector({json.dumps(selector)});if(!e||e.tagName!=='SELECT')return 'none';
            const want={json.dumps(option, ensure_ascii=False)};const o=Array.from(e.options).find(o=>o.value===want||o.text.trim()===want);
            if(!o)return 'nooption';e.value=o.value;e.dispatchEvent(new Event('input',{{bubbles:true}}));e.dispatchEvent(new Event('change',{{bubbles:true}}));return 'ok';}})()""")
        if ok == "none":
            raise CdpError(f"選択の欄が見つかりません: {selector}")
        if ok == "nooption":
            raise CdpError(f"選択肢が見つかりません: {option}")

    def check(self, selector: str | None = None, text: str | None = None, on: bool = True) -> None:
        """チェックボックス・ラジオボタンを、指定の状態にする。"""
        info = self.find(selector, text)
        state = self.js(f"(function(){{const e=document.querySelector({json.dumps(selector)});return e?!!e.checked:null;}})()") if selector else None
        if state is None or state != on:
            self._mouse_click(info["x"], info["y"])

    def press(self, key: str) -> None:
        """キーを押す (Enter、Tab、Escape、ArrowDown など)。POST のフォームの中の Enter は、--allow-submit のときだけ。"""
        if key not in KEYS:
            raise CdpError(f"使えるキー: {', '.join(KEYS)}")
        if key == "Enter" and not self.allow_submit:
            method = self.js("(function(){const e=document.activeElement;const f=e&&(e.form||e.closest&&e.closest('form'));return f?(f.getAttribute('method')||'get').toLowerCase():'';})()")
            if method == "post":
                raise DangerousAction("POST のフォームの中で Enter を押すと送信になるので、押しません。依頼に書かれた操作なら --allow-submit を付ける")
        name, code, text = KEYS[key]
        down = {"type": "keyDown", "key": name, "windowsVirtualKeyCode": code, "code": name}
        if text:
            down["text"] = text
        self._cdp.call("Input.dispatchKeyEvent", **down)
        self._cdp.call("Input.dispatchKeyEvent", type="keyUp", key=name, windowsVirtualKeyCode=code, code=name)
        time.sleep(0.1)

    def scroll(self, dy: int = 600) -> None:
        self.js(f"window.scrollBy(0, {int(dy)})")

    # --- 読み取り -------------------------------------------------------------
    def snapshot(self, limit: int = 150) -> str:
        """画面の要素の一覧 (見出し・入力欄・ボタン・リンク・表) と、指定に使えるセレクタ。画像より速くトークンが少ないので、まずこれで画面を知る。"""
        return self._call_js(SNAPSHOT_JS, max(20, int(limit)))

    def text(self, selector: str | None = None, limit: int = 20000) -> str:
        """見えている文字 (selector を付けると、その要素の中だけ)。"""
        value = self.js(f"(function(){{const e={('document.querySelector(' + json.dumps(selector) + ')') if selector else 'document.body'};return e?e.innerText:null;}})()")
        if value is None:
            raise CdpError(f"見つかりません: {selector}")
        return value if len(value) <= limit else value[:limit] + f"\n…(ほか {len(value) - limit:,} 文字)"

    def tables(self, selector: str = "table") -> list[list[list[str]]]:
        """表を、[表][行][セル] の文字の配列で返す。"""
        return self._call_js(TABLES_JS, selector) or []

    def exists(self, selector: str | None = None, text: str | None = None) -> bool:
        try:
            self.find(selector, text)
            return True
        except DangerousAction:
            raise
        except CdpError:
            return False

    # --- 保存 (.handoff/browser/ の中だけ) -------------------------------------
    def _out(self, name: str) -> Path:
        path = (self.out_dir / name).resolve()
        if self.out_dir.resolve() not in path.parents:
            raise CdpError(f"保存できるのは .handoff/browser/ の中だけです: {name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def screenshot(self, name: str = "画面.png", full: bool = False) -> Path:
        """画面を PNG に保存する (人が見る。LLM も read の道具で開いて見られる)。"""
        params: dict[str, Any] = {"format": "png"}
        if full:
            params["captureBeyondViewport"] = True
        data = self._cdp.call("Page.captureScreenshot", **params)["data"]
        path = self._out(name)
        path.write_bytes(base64.b64decode(data))
        return path

    def save_json(self, data: Any, name: str) -> Path:
        path = self._out(name)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def save_text(self, text: str, name: str) -> Path:
        path = self._out(name)
        path.write_text(text, encoding="utf-8")
        return path

    def allow_downloads(self, folder: str = "downloads") -> Path:
        """これ以降のダウンロードを、.handoff/browser/<folder>/ に保存させる。"""
        path = self._out(folder + "/.keep").parent
        version = http_json(self.port, "/json/version")
        browser = Cdp(version["webSocketDebuggerUrl"])
        try:
            browser.call("Browser.setDownloadBehavior", behavior="allow", downloadPath=str(path))
        finally:
            browser.close()
        return path
