"""Agent 启动时走 Keycloak 授权码登录。

客户端已在 realm 里注册，所以这里不再做动态注册。
浏览器打开登录页，授权码回到本机回调地址。
"""

from __future__ import annotations

import asyncio
import base64
import html
import json
import os
import webbrowser
from urllib.parse import parse_qs, urlsplit

import httpx2
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.auth.exceptions import OAuthFlowError
from mcp.shared.auth import AuthorizationCodeResult, OAuthClientInformationFull, OAuthClientMetadata, OAuthToken
from pydantic import AnyUrl

DEFAULT_REDIRECT_URI = "http://127.0.0.1:3030/callback"
DEFAULT_CLIENT_ID = "agent-demo"
_LOGIN_TIMEOUT = 300.0


def _username_from_access_token(access_token: str) -> str | None:
    try:
        payload = access_token.split(".")[1]
        padded = payload + "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(padded))
    except (IndexError, ValueError, json.JSONDecodeError):
        return None
    username = claims.get("preferred_username")
    return str(username) if username else None


class MemoryTokenStore(TokenStorage):
    """进程内记住预先注册的客户端和这一轮登录拿到的 token。"""

    def __init__(self, client_id: str, redirect_uri: str):
        self.username: str | None = None
        self._tokens: OAuthToken | None = None
        self._client = OAuthClientInformationFull(
            client_id=client_id,
            redirect_uris=[AnyUrl(redirect_uri)],
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        )

    async def get_tokens(self) -> OAuthToken | None:
        return self._tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self._tokens = tokens
        self.username = _username_from_access_token(tokens.access_token)

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self._client

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._client = client_info


class LoginCallback:
    """在 redirect URI 上收下授权码。"""

    def __init__(self, redirect_uri: str):
        parsed = urlsplit(redirect_uri)
        if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.scheme != "http":
            raise RuntimeError("OAUTH_REDIRECT_URI 必须是本机 http 地址，例如 http://127.0.0.1:3030/callback")
        self.redirect_uri = redirect_uri
        self.host = parsed.hostname
        self.port = parsed.port or 80
        self.path = parsed.path or "/"
        self._server: asyncio.Server | None = None
        self._waiter: asyncio.Future[AuthorizationCodeResult] | None = None

    async def start(self) -> None:
        try:
            self._server = await asyncio.start_server(self._handle, self.host, self.port)
        except OSError as exc:
            raise RuntimeError(
                f"登录回调地址 {self.redirect_uri} 绑定失败。先关掉另一个正在登录的 agent。"
            ) from exc

    async def close(self) -> None:
        server = self._server
        self._server = None
        if server is None:
            return
        server.close()
        await server.wait_closed()

    async def redirect(self, url: str) -> None:
        loop = asyncio.get_running_loop()
        self._waiter = loop.create_future()
        print(
            "\n需要登录 Keycloak 才能连接笔记服务。\n"
            "演示账号：alice / alice（可记笔记、删除），bob / bob（只能读自己的笔记）。\n"
            f"如果浏览器没有打开，手动访问：\n{url}\n",
            flush=True,
        )
        webbrowser.open(url)

    async def wait_for_code(self) -> AuthorizationCodeResult:
        if self._waiter is None:
            raise OAuthFlowError("登录回调还没开始")
        try:
            return await asyncio.wait_for(self._waiter, timeout=_LOGIN_TIMEOUT)
        except TimeoutError as exc:
            raise OAuthFlowError("等待 Keycloak 登录超时") from exc

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = b""
            while b"\r\n\r\n" not in data and len(data) < 65536:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                data += chunk
            request_line = data.split(b"\r\n", 1)[0].decode("latin1", errors="replace")
            parts = request_line.split(" ")
            target = parts[1] if len(parts) >= 2 else "/"
            parsed = urlsplit(target)
            if parsed.path != self.path:
                self._reply(writer, 404, "找不到这个地址")
                await writer.drain()
                return
            query = parse_qs(parsed.query)
            error = _first(query, "error")
            if error:
                self._reply(writer, 400, f"登录没有完成：{error}")
                await writer.drain()
                self._fail(OAuthFlowError(error))
                return
            code = _first(query, "code")
            if not code:
                self._reply(writer, 400, "登录回调里没有授权码")
                await writer.drain()
                self._fail(OAuthFlowError("登录回调里没有授权码"))
                return
            self._reply(writer, 200, "登录完成，回到运行 Agent 的终端。")
            await writer.drain()
            waiter = self._waiter
            if waiter is not None and not waiter.done():
                waiter.set_result(
                    AuthorizationCodeResult(
                        code=code,
                        state=_first(query, "state"),
                        iss=_first(query, "iss"),
                    )
                )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    def _fail(self, exc: Exception) -> None:
        waiter = self._waiter
        if waiter is not None and not waiter.done():
            waiter.set_exception(exc)

    def _reply(self, writer: asyncio.StreamWriter, status: int, message: str) -> None:
        reason = "OK" if status == 200 else "Error"
        safe = html.escape(message)
        body = (
            "<!doctype html><meta charset=\"utf-8\">"
            f"<title>{safe}</title><p>{safe}</p>"
        ).encode()
        header = (
            f"HTTP/1.1 {status} {reason}\r\n"
            "Content-Type: text/html; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n\r\n"
        ).encode()
        writer.write(header + body)


def oauth_settings() -> tuple[str, str]:
    redirect_uri = os.getenv("OAUTH_REDIRECT_URI", "").strip() or DEFAULT_REDIRECT_URI
    client_id = os.getenv("KEYCLOAK_CLIENT_ID", "").strip() or DEFAULT_CLIENT_ID
    return redirect_uri, client_id


def build_http_client(server_url: str, login: LoginCallback, store: MemoryTokenStore) -> httpx2.AsyncClient:
    redirect_uri, _client_id = oauth_settings()
    provider = OAuthClientProvider(
        server_url=server_url,
        client_metadata=OAuthClientMetadata(
            redirect_uris=[AnyUrl(redirect_uri)],
            client_name="agent-demo",
            token_endpoint_auth_method="none",
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
        ),
        storage=store,
        redirect_handler=login.redirect,
        callback_handler=login.wait_for_code,
    )
    # 登录会停在这一次 HTTP 请求里，超时要盖住用户填表的时间。
    # connect 仍保持短，Keycloak 没起来时尽快失败。
    return httpx2.AsyncClient(
        auth=provider,
        timeout=httpx2.Timeout(_LOGIN_TIMEOUT, connect=10.0),
        trust_env=False,
    )


def _first(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values:
        return None
    return values[0]
