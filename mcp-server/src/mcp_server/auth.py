"""用 Keycloak 签发的 access token 识别用户。

笔记服务自己不保存密码。它只做资源服务器：核对签名、签发者和受众，
再把 realm role 换成工具里使用的 scope。
"""

from __future__ import annotations

import asyncio
import logging

import jwt
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError
from mcp.server.auth.provider import AccessToken

logger = logging.getLogger(__name__)

READ_SCOPE = "notes:read"
WRITE_SCOPE = "notes:write"
_ROLE_SCOPES = {
    "notes-reader": READ_SCOPE,
    "notes-writer": WRITE_SCOPE,
}


class KeycloakTokenVerifier:
    """校验 Keycloak realm 发出的 JWT。"""

    def __init__(self, issuer: str, audience: str):
        self.issuer = issuer.rstrip("/")
        self.audience = audience.rstrip("/")
        self._jwks = PyJWKClient(f"{self.issuer}/protocol/openid-connect/certs")

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuer,
                leeway=30,
                options={"require": ["exp"]},
            )
        except (jwt.PyJWTError, PyJWKClientError) as exc:
            logger.warning("拒绝 Keycloak token：%s", exc)
            return None

        roles = (claims.get("realm_access") or {}).get("roles") or []
        # 客户端请求到的 scope 谁登录都会写进 token。
        # alice 和 bob 的差别在 realm role，这里把它换成 MCP scope。
        scopes = [_ROLE_SCOPES[role] for role in roles if role in _ROLE_SCOPES]
        username = claims.get("preferred_username") or claims.get("sub")
        if not username:
            logger.warning("拒绝 Keycloak token：没有用户名")
            return None
        return AccessToken(
            token=token,
            client_id=str(claims.get("azp") or ""),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=str(username),
            claims=claims,
        )
