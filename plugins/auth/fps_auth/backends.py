import uuid
from typing import Generic, Optional

import httpx
from fastapi import Depends, HTTPException, Response, WebSocket, status
from fastapi_users import (  # type: ignore
    BaseUserManager,
    FastAPIUsers,
    UUIDIDMixin,
    models,
)
from fastapi_users.authentication import (
    AuthenticationBackend,
    CookieTransport,
    JWTStrategy,
)
from fastapi_users.authentication.strategy.base import Strategy
from fastapi_users.authentication.transport.base import Transport
from fastapi_users.db import SQLAlchemyUserDatabase
from fps.exceptions import RedirectException  # type: ignore
from fps.logging import get_configured_logger  # type: ignore
from httpx_oauth.clients.github import GitHubOAuth2  # type: ignore
from starlette.requests import Request

from .config import get_auth_config
from .db import User, get_user_db, secret
from .models import UserCreate

logger = get_configured_logger("auth")


class NoAuthTransport(Transport):
    scheme = None  # type: ignore


class NoAuthStrategy(Strategy, Generic[models.UP, models.ID]):
    async def read_token(
        self, token: Optional[str], user_manager: BaseUserManager[models.UP, models.ID]
    ) -> Optional[models.UP]:
        active_user = await user_manager.user_db.get_by_email(get_auth_config().global_email)
        return active_user


class GitHubTransport(CookieTransport):
    async def get_login_response(self, token: str, response: Response):
        await super().get_login_response(token, response)
        response.status_code = status.HTTP_302_FOUND
        response.headers["Location"] = "/lab"


noauth_transport = NoAuthTransport()
cookie_transport = CookieTransport(cookie_secure=get_auth_config().cookie_secure)
github_transport = GitHubTransport()


def get_noauth_strategy() -> NoAuthStrategy:
    return NoAuthStrategy()


def get_jwt_strategy() -> JWTStrategy:
    return JWTStrategy(secret=secret, lifetime_seconds=None)  # type: ignore


noauth_authentication = AuthenticationBackend(
    name="noauth",
    transport=noauth_transport,
    get_strategy=get_noauth_strategy,
)
cookie_authentication = AuthenticationBackend(
    name="cookie",
    transport=cookie_transport,
    get_strategy=get_jwt_strategy,
)
github_cookie_authentication = AuthenticationBackend(
    name="github",
    transport=github_transport,
    get_strategy=get_jwt_strategy,
)
github_authentication = GitHubOAuth2(
    get_auth_config().client_id, get_auth_config().client_secret.get_secret_value()
)


class UserManager(UUIDIDMixin, BaseUserManager[User, uuid.UUID]):
    async def on_after_register(self, user: User, request: Optional[Request] = None):
        for oauth_account in user.oauth_accounts:
            if oauth_account.oauth_name == "github":
                async with httpx.AsyncClient() as client:
                    r = (
                        await client.get(f"https://api.github.com/user/{oauth_account.account_id}")
                    ).json()

                await self.user_db.update(
                    user,
                    dict(
                        anonymous=False,
                        username=r["login"],
                        color=None,
                        avatar_url=r["avatar_url"],
                        is_active=True,
                    ),
                )


async def get_user_manager(user_db: SQLAlchemyUserDatabase = Depends(get_user_db)):
    yield UserManager(user_db)


async def get_enabled_backends(auth_config=Depends(get_auth_config)):
    if auth_config.mode == "noauth" and not auth_config.collaborative:
        return [noauth_authentication, github_cookie_authentication]
    else:
        return [cookie_authentication, github_cookie_authentication]


fapi_users = FastAPIUsers[User, uuid.UUID](
    get_user_manager,
    [noauth_authentication, cookie_authentication, github_cookie_authentication],
)


async def create_guest(user_manager, auth_config):
    # workspace and settings are copied from global user
    # but this is a new user
    global_user = await user_manager.get_by_email(auth_config.global_email)
    user_id = str(uuid.uuid4())
    guest = dict(
        anonymous=True,
        email=f"{user_id}@jupyter.com",
        username=f"{user_id}@jupyter.com",
        password="",
        workspace=global_user.workspace,
        settings=global_user.settings,
        permissions={"admin": ["read", "write", "execute"]}
    )
    return await user_manager.create(UserCreate(**guest))


def current_user(resource: Optional[str] = None):
    async def _(
        request: Request,
        response: Response,
        token: Optional[str] = None,
        user: Optional[User] = Depends(
            fapi_users.current_user(optional=True, get_enabled_backends=get_enabled_backends)
        ),
        user_manager: UserManager = Depends(get_user_manager),
        auth_config=Depends(get_auth_config),
    ):
        if auth_config.mode == "user":
            # "user" authentication: check authorization
            if user and resource:
                # check if allowed to access the resource
                permissions = user.permissions.get(resource, [])
                if request.method in ("GET", "HEAD"):
                    if "read" not in permissions:
                        user = None
                elif request.method in ("POST", "PUT", "PATCH", "DELETE"):
                    if "write" not in permissions:
                        user = None
        else:
            # "noauth" or "token" authentication
            if auth_config.collaborative:
                if not user and auth_config.mode == "noauth":
                    user = await create_guest(user_manager, auth_config)
                    await cookie_authentication.login(get_jwt_strategy(), user, response)

                elif not user and auth_config.mode == "token":
                    global_user = await user_manager.get_by_email(auth_config.global_email)
                    if global_user and global_user.hashed_password == token:
                        user = await create_guest(user_manager, auth_config)
                        await cookie_authentication.login(get_jwt_strategy(), user, response)
            else:
                if auth_config.mode == "token":
                    global_user = await user_manager.get_by_email(auth_config.global_email)
                    if global_user and global_user.username == token:
                        user = global_user
                        await cookie_authentication.login(get_jwt_strategy(), user, response)

        if user:
            return user

        elif auth_config.login_url:
            raise RedirectException(auth_config.login_url)

        else:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    return _


def websocket_for_current_user(resource: str):
    async def _(
        websocket: WebSocket,
        auth_config=Depends(get_auth_config),
        user_manager: UserManager = Depends(get_user_manager),
    ) -> Optional[WebSocket]:
        accept_websocket = False
        if auth_config.mode == "noauth":
            accept_websocket = True
        elif "fastapiusersauth" in websocket._cookies:
            token = websocket._cookies["fastapiusersauth"]
            user = await get_jwt_strategy().read_token(token, user_manager)
            if user:
                if auth_config.mode == "user":
                    if "execute" in user.permissions.get(resource, []):
                        accept_websocket = True
                else:
                    accept_websocket = True
        if accept_websocket:
            return websocket
        else:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return None

    return _
