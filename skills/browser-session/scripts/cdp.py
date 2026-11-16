"""Chrome / Edge の DevTools Protocol (CDP) に、標準ライブラリだけでつなぐ部品。

- WebSocket (RFC 6455) のクライアントを、socket で最小限に実装する (pip の websocket ライブラリを使わない)
- つなぐ先は、この PC (127.0.0.1 / localhost / ::1) だけ。ほかの住所は拒否する
- Origin ヘッダーを送らない (Chrome 111 以降は、Origin 付きの接続に --remote-allow-origins が要る)
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.parse
import urllib.request
from typing import Any

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
MAX_FRAME = 64 * 1024 * 1024


class CdpError(Exception):
    """利用者に見せてよいメッセージ。"""


class CdpTimeout(CdpError):
    pass


def check_loopback(host: str | None) -> None:
    if (host or "").lower().strip("[]") not in LOOPBACK:
        raise CdpError(f"この PC 以外のブラウザには、つなぎません: {host!r}")


def http_text(port: int, path: str, method: str = "GET", timeout: float = 3.0) -> str:
    """DevTools の HTTP の口を呼び、本文の文字を返す (/json/close は JSON でなく文字を返す)。"""
    request = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", "replace")
    except OSError as exc:
        raise CdpError(f"ブラウザ (127.0.0.1:{port}) に接続できません: {exc}") from exc


def http_json(port: int, path: str, method: str = "GET", timeout: float = 3.0) -> Any:
    """DevTools の HTTP の口 (/json/version、/json/list など) を呼ぶ。"""
    text = http_text(port, path, method, timeout)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise CdpError(f"ブラウザ (127.0.0.1:{port}) の応答が JSON ではありません ({path})") from exc


class WebSocket:
    """テキストのやり取りだけの、最小の WebSocket クライアント。"""

    def __init__(self, url: str, timeout: float = 10.0) -> None:
        parsed = urllib.parse.urlparse(url)
        if parsed.scheme != "ws":
            raise CdpError(f"ws:// の住所ではありません: {url}")
        check_loopback(parsed.hostname)
        self.sock = socket.create_connection((parsed.hostname, parsed.port or 80), timeout=timeout)
        self.buffer = b""
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        path = parsed.path + (f"?{parsed.query}" if parsed.query else "")
        host = f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
        request = (f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
        self.sock.sendall(request.encode("ascii"))
        header = self._read_until(b"\r\n\r\n")
        status = header.split(b"\r\n", 1)[0]
        if b" 101 " not in status + b" ":
            raise CdpError(f"WebSocket の接続を断られました: {status.decode('latin-1')}")

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self.buffer:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise CdpError("ブラウザが接続を閉じました")
            self.buffer += chunk
        head, self.buffer = self.buffer.split(marker, 1)
        return head

    def _read_exact(self, size: int) -> bytes:
        while len(self.buffer) < size:
            chunk = self.sock.recv(max(65536, size - len(self.buffer)))
            if not chunk:
                raise CdpError("ブラウザが接続を閉じました")
            self.buffer += chunk
        data, self.buffer = self.buffer[:size], self.buffer[size:]
        return data

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        header = bytes([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header += bytes([0x80 | length])
        elif length < 65536:
            header += bytes([0x80 | 126]) + struct.pack("!H", length)
        else:
            header += bytes([0x80 | 127]) + struct.pack("!Q", length)
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(header + mask + masked)

    def send(self, text: str) -> None:
        self._send_frame(0x1, text.encode("utf-8"))

    def recv(self, timeout: float | None) -> str:
        """1 つのメッセージ (断片はつなぐ) を受け取る。ping には pong を返す。"""
        self.sock.settimeout(timeout)
        parts: list[bytes] = []
        try:
            while True:
                first, second = self._read_exact(2)
                opcode, final = first & 0x0F, bool(first & 0x80)
                length = second & 0x7F
                if length == 126:
                    length = struct.unpack("!H", self._read_exact(2))[0]
                elif length == 127:
                    length = struct.unpack("!Q", self._read_exact(8))[0]
                if length > MAX_FRAME:
                    raise CdpError("受け取ったデータが大きすぎます")
                mask = self._read_exact(4) if second & 0x80 else b""
                payload = self._read_exact(length)
                if mask:
                    payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
                if opcode == 0x8:
                    raise CdpError("ブラウザが接続を閉じました")
                if opcode == 0x9:
                    self._send_frame(0xA, payload)
                    continue
                if opcode == 0xA:
                    continue
                parts.append(payload)
                if final:
                    return b"".join(parts).decode("utf-8")
        except socket.timeout as exc:
            raise CdpTimeout("ブラウザの応答を待ちきれませんでした") from exc

    def close(self) -> None:
        try:
            self._send_frame(0x8, b"")
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass


class Cdp:
    """1 つのタブ (ページ) への CDP の接続。呼び出しの応答を id で待ち、その間に来たイベントは events に貯める。"""

    def __init__(self, ws_url: str, timeout: float = 30.0) -> None:
        self.ws = WebSocket(ws_url)
        self.timeout = timeout
        self.next_id = 0
        self.events: list[dict[str, Any]] = []

    def call(self, method: str, timeout: float | None = None, **params: Any) -> dict[str, Any]:
        self.next_id += 1
        message_id = self.next_id
        self.ws.send(json.dumps({"id": message_id, "method": method, "params": params}))
        deadline = time.monotonic() + (timeout or self.timeout)
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise CdpTimeout(f"{method} の応答を待ちきれませんでした")
            message = json.loads(self.ws.recv(left))
            if message.get("id") == message_id:
                if "error" in message:
                    error = message["error"]
                    raise CdpError(f"{method}: {error.get('message', error)}")
                return message.get("result", {})
            if "method" in message:
                self.events.append(message)
                del self.events[:-500]  # 古いイベントは捨てる

    def wait_event(self, method: str, timeout: float = 10.0) -> dict[str, Any] | None:
        """貯まったイベントか、これから来るイベントから、名前の一致するものを待つ (来なければ None)。"""
        for index, event in enumerate(self.events):
            if event.get("method") == method:
                return self.events.pop(index)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                message = json.loads(self.ws.recv(deadline - time.monotonic()))
            except CdpTimeout:
                return None
            if message.get("method") == method:
                return message
            if "method" in message:
                self.events.append(message)
        return None

    def close(self) -> None:
        self.ws.close()
