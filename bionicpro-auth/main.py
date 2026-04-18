import os
import time
import base64
import hashlib
import secrets
from typing import Dict, Any, Optional, Tuple

from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware
from itsdangerous import URLSafeTimedSerializer, BadSignature
import httpx
import uvicorn

app = FastAPI()

# --- Config ---
KC_URL_INTERNAL = os.getenv("KC_URL", "http://keycloak:8080")
KC_URL_EXTERNAL = os.getenv("KC_URL_EXTERNAL", "http://localhost:8080")

REALM = os.getenv("REALM", "reports-realm")
CLIENT_ID = os.getenv("CLIENT_ID", "bionicpro-bff")
CLIENT_SECRET = os.getenv("CLIENT_SECRET", "<CLIENT_SECRET_PLACEHOLDER>")

SESSION_SECRET = os.getenv("SESSION_SECRET", "<SESSION_SECRET_PLACEHOLDER>")

FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
CALLBACK_URL = os.getenv("CALLBACK_URL", "http://localhost:8000/auth/callback")

REPORTS_API_URL = os.getenv("REPORTS_API_URL", "http://localhost:8001/reports")

COOKIE_NAME = os.getenv("COOKIE_NAME", "bionicpro_sid")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", str(8 * 60 * 60)))

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=[FRONTEND_ORIGIN],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

oauth = OAuth()
oauth.register(
    name="keycloak",
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    authorize_url=f"{KC_URL_EXTERNAL}/realms/{REALM}/protocol/openid-connect/auth",
    access_token_url=f"{KC_URL_INTERNAL}/realms/{REALM}/protocol/openid-connect/token",
    client_kwargs={"scope": "openid profile email"},
)

PKCE_VERIFIER_BY_STATE: Dict[str, str] = {}
SESSION_BY_SID: Dict[str, Dict[str, Any]] = {}
_refresh_serializer = URLSafeTimedSerializer(SESSION_SECRET, salt="refresh-token")


def _now() -> int:
    return int(time.time())


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _new_sid() -> str:
    return _b64url(secrets.token_bytes(24))


def _generate_pkce_pair() -> Tuple[str, str]:
    verifier = _b64url(secrets.token_bytes(32))
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def _set_sid_cookie(response: Response, sid: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=sid,
        httponly=True,
        secure=COOKIE_SECURE,  # true только при HTTPS
        samesite="lax",
        max_age=SESSION_MAX_AGE_SECONDS,  # сессия должна жить дольше access token
        path="/",
    )


def _clear_sid_cookie(response: Response) -> None:
    # Надёжно: и delete_cookie, и явная установка пустого значения с expires/max_age.
    response.delete_cookie(key=COOKIE_NAME, path="/")
    response.set_cookie(
        key=COOKIE_NAME,
        value="",
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
        max_age=0,
        expires=0,
        path="/",
    )


def _encrypt_refresh_token(rt: str) -> str:
    return _refresh_serializer.dumps({"rt": rt})


def _decrypt_refresh_token(rt_enc: str) -> str:
    try:
        data = _refresh_serializer.loads(rt_enc, max_age=SESSION_MAX_AGE_SECONDS)
        rt = data.get("rt")
        if not isinstance(rt, str) or not rt:
            raise ValueError("bad token")
        return rt
    except (BadSignature, Exception):
        raise HTTPException(status_code=401, detail="Сессия истекла, нужна повторная авторизация")


async def _exchange_code_for_token(code: str, code_verifier: str) -> Dict[str, Any]:
    token_url = f"{KC_URL_INTERNAL}/realms/{REALM}/protocol/openid-connect/token"
    data = {
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "code": code,
        "redirect_uri": CALLBACK_URL,
        "code_verifier": code_verifier,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(token_url, data=data)
        if r.status_code >= 400:
            raise HTTPException(status_code=400, detail="Ошибка обмена кода на токены")
        return r.json()


async def _refresh_access_token(refresh_token: str) -> Dict[str, Any]:
    token_url = f"{KC_URL_INTERNAL}/realms/{REALM}/protocol/openid-connect/token"
    data = {
        "grant_type": "refresh_token",
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
        "refresh_token": refresh_token,
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.post(token_url, data=data)
        if r.status_code >= 400:
            raise HTTPException(status_code=401, detail="Не удалось обновить токен, выполните вход заново")
        return r.json()


def _access_is_expired(session: Dict[str, Any], skew: int = 10) -> bool:
    exp = session.get("access_expires_at")
    return not isinstance(exp, int) or exp <= (_now() + skew)


@app.get("/api/session")
async def session_status(request: Request):
    sid = request.cookies.get(COOKIE_NAME)
    authenticated = bool(sid and sid in SESSION_BY_SID)
    return {"authenticated": authenticated}


@app.get("/login")
async def login(request: Request):
    verifier, challenge = _generate_pkce_pair()
    state = _b64url(secrets.token_bytes(16))

    PKCE_VERIFIER_BY_STATE[state] = verifier

    prompt = request.query_params.get("prompt")  # например: "login"

    kwargs = dict(
        state=state,
        code_challenge=challenge,
        code_challenge_method="S256",
    )
    if prompt:
        kwargs["prompt"] = prompt

    return await oauth.keycloak.authorize_redirect(
        request,
        CALLBACK_URL,
        **kwargs,
    )

@app.get("/logout")
async def logout(request: Request):
    sid = request.cookies.get(COOKIE_NAME)
    if sid:
        SESSION_BY_SID.pop(sid, None)

    response = RedirectResponse(url=f"{FRONTEND_ORIGIN}/")
    _clear_sid_cookie(response)
    return response

@app.get("/switch-account")
async def switch_account(request: Request):
    # 1) чистим BFF сессию
    sid = request.cookies.get(COOKIE_NAME)
    if sid:
        SESSION_BY_SID.pop(sid, None)

    # 2) чистим cookie
    response = RedirectResponse(url=f"{KC_URL_EXTERNAL}/realms/{REALM}/protocol/openid-connect/auth")
    _clear_sid_cookie(response)

    # 3) вместо ручной сборки auth URL — просто редиректим на наш /login с prompt=login
    # (чтобы PKCE/state создавались корректно)
    response.headers["Location"] = f"http://localhost:8000/login?prompt=login"
    return response


@app.get("/auth/callback")
async def auth_callback(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Некорректный callback от провайдера")

    verifier = PKCE_VERIFIER_BY_STATE.pop(state, None)
    if not verifier:
        raise HTTPException(status_code=400, detail="PKCE verifier не найден или уже использован")

    token = await _exchange_code_for_token(code=code, code_verifier=verifier)

    access_token = token.get("access_token")
    refresh_token = token.get("refresh_token")
    expires_in = token.get("expires_in")

    if not access_token or not refresh_token or not isinstance(expires_in, int):
        raise HTTPException(status_code=400, detail="Keycloak не вернул ожидаемые токены")

    sid = _new_sid()
    SESSION_BY_SID[sid] = {
        "access_token": access_token,
        "access_expires_at": _now() + int(expires_in),
        "refresh_token_enc": _encrypt_refresh_token(refresh_token),
        "refresh_expires_at": None,
    }

    response = RedirectResponse(url=f"{FRONTEND_ORIGIN}/")
    _set_sid_cookie(response, sid)
    return response


@app.get("/api/reports")
async def proxy_reports(request: Request):
    sid = request.cookies.get(COOKIE_NAME)
    if not sid:
        raise HTTPException(status_code=401, detail="Необходима авторизация")

    session = SESSION_BY_SID.get(sid)
    if not session:
        raise HTTPException(status_code=401, detail="Сессия не найдена, выполните вход заново")

    # Обновление access_token через refresh_token если истёк
    if _access_is_expired(session):
        rt = _decrypt_refresh_token(session["refresh_token_enc"])
        new_token = await _refresh_access_token(rt)

        new_access = new_token.get("access_token")
        new_refresh = new_token.get("refresh_token", rt)  # KC может вернуть новый refresh или тот же
        new_expires_in = new_token.get("expires_in")

        if not new_access or not isinstance(new_expires_in, int):
            raise HTTPException(status_code=401, detail="Не удалось обновить access token")

        session["access_token"] = new_access
        session["access_expires_at"] = _now() + int(new_expires_in)
        session["refresh_token_enc"] = _encrypt_refresh_token(new_refresh)

    # Вызов защищённого API с Bearer токеном (токен не уходит во фронт)
    async with httpx.AsyncClient(timeout=10.0) as client:
        api_res = await client.get(
            REPORTS_API_URL,
            headers={"Authorization": f"Bearer {session['access_token']}"},
        )

    payload = api_res.json() if api_res.content else {}

    new_sid = _new_sid()
    SESSION_BY_SID[new_sid] = session
    SESSION_BY_SID.pop(sid, None)

    out = JSONResponse(status_code=api_res.status_code, content=payload)
    _set_sid_cookie(out, new_sid)
    return out


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)