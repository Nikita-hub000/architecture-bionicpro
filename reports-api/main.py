import os
import time
import json
from typing import Any, Dict, Optional, Set

import httpx
import clickhouse_connect
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, Request
from jose import jwt
from jose.exceptions import JWTError
from urllib.parse import quote

app = FastAPI()

KC_URL_INTERNAL = os.getenv("KC_URL", "http://keycloak:8080")
KC_URL_EXTERNAL = os.getenv("KC_URL_EXTERNAL", "http://localhost:8080")
REALM = os.getenv("REALM", "reports-realm")

ALLOWED_ISSUERS: Set[str] = {
    os.getenv("EXPECTED_ISSUER", f"{KC_URL_INTERNAL}/realms/{REALM}"),
    os.getenv("EXPECTED_ISSUER_EXTERNAL", f"{KC_URL_EXTERNAL}/realms/{REALM}"),
}

EXPECTED_AUDIENCE = os.getenv("EXPECTED_AUDIENCE")  # опционально

JWKS_URL = os.getenv("JWKS_URL", f"{KC_URL_INTERNAL}/realms/{REALM}/protocol/openid-connect/certs")

CH_HOST = os.getenv("CH_HOST", "clickhouse")
CH_PORT = int(os.getenv("CH_PORT", "8123"))
CH_DB = os.getenv("CH_DB", "reports")
CH_USER = os.getenv("CH_USER", "reports_user")
CH_PASSWORD = os.getenv("CH_PASSWORD", "reports_password")

_jwks_cache: Dict[str, Any] = {"fetched_at": 0, "jwks": None}
JWKS_CACHE_TTL_SECONDS = int(os.getenv("JWKS_CACHE_TTL_SECONDS", "300"))

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://minio:9000")
S3_BUCKET = os.getenv("S3_BUCKET", "reports")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioUser")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioPassword")
CDN_BASE_URL = os.getenv("CDN_BASE_URL", "http://localhost:8082")

def _ch():
    return clickhouse_connect.get_client(
        host=CH_HOST, port=CH_PORT, username=CH_USER, password=CH_PASSWORD, database=CH_DB
    )


async def _get_jwks() -> Dict[str, Any]:
    now = int(time.time())
    if _jwks_cache["jwks"] and (now - _jwks_cache["fetched_at"] < JWKS_CACHE_TTL_SECONDS):
        return _jwks_cache["jwks"]

    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(JWKS_URL)
        if r.status_code >= 400:
            raise HTTPException(status_code=503, detail="Не удалось загрузить JWKS из Keycloak")
        jwks = r.json()

    _jwks_cache["jwks"] = jwks
    _jwks_cache["fetched_at"] = now
    return jwks


def _extract_bearer_token(request: Request) -> str:
    auth = request.headers.get("authorization")
    if not auth:
        raise HTTPException(status_code=401, detail="Нет заголовка Authorization (Bearer токен не передан)")
    parts = auth.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="Неверный формат Authorization (ожидается 'Bearer <token>')")
    return parts[1].strip()


def _find_jwk_for_kid(jwks: Dict[str, Any], kid: str) -> Optional[Dict[str, Any]]:
    for k in jwks.get("keys", []):
        if k.get("kid") == kid:
            return k
    return None


async def _verify_and_decode(token: str) -> Dict[str, Any]:
    try:
        headers = jwt.get_unverified_header(token)
    except JWTError:
        raise HTTPException(status_code=401, detail="Некорректный JWT")

    kid = headers.get("kid")
    if not kid:
        raise HTTPException(status_code=401, detail="JWT без kid")

    jwks = await _get_jwks()
    jwk = _find_jwk_for_kid(jwks, kid)
    if not jwk:
        _jwks_cache["jwks"] = None
        jwks = await _get_jwks()
        jwk = _find_jwk_for_kid(jwks, kid)

    if not jwk:
        raise HTTPException(status_code=401, detail="kid не найден в JWKS")

    options = {
        "verify_signature": True,
        "verify_exp": True,
        "verify_iat": True,
        "verify_iss": False,
        "verify_aud": bool(EXPECTED_AUDIENCE),
    }

    try:
        claims = jwt.decode(token, jwk, algorithms=["RS256"], audience=EXPECTED_AUDIENCE, options=options)
    except JWTError:
        raise HTTPException(status_code=401, detail="Токен недействителен (подпись/exp/aud)")

    iss = claims.get("iss")
    if not isinstance(iss, str) or iss not in ALLOWED_ISSUERS:
        raise HTTPException(status_code=401, detail="Токен недействителен (iss)")

    return claims


def _s3():
    return boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def _s3_exists(bucket: str, key: str) -> bool:
    try:
        _s3().head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def _s3_put_json(bucket: str, key: str, payload: Dict[str, Any]) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    _s3().put_object(
        Bucket=bucket,
        Key=key,
        Body=body,
        ContentType="application/json; charset=utf-8",
        CacheControl="public, max-age=1800",
    )

def _get_watermark_value() -> Optional[str]:
    client = _ch()
    r = client.query(
        "SELECT value FROM etl_watermark WHERE name='reports' ORDER BY updated_at DESC LIMIT 1"
    )
    if not r.result_rows:
        return None
    return str(r.result_rows[0][0])


def _cdn_url_for_key(key: str) -> str:
    # Кодируем ключ так, чтобы пробелы/двоеточия/прочие символы не ломали URL.
    # '/' оставляем, чтобы сохранилась структура пути.
    encoded = quote(key, safe="/")
    return f"{CDN_BASE_URL}/reports/{encoded}"

@app.get("/reports")
async def reports(request: Request):
    token = _extract_bearer_token(request)
    claims = await _verify_and_decode(token)

    user_key = claims.get("email")
    if not isinstance(user_key, str) or not user_key:
        raise HTTPException(status_code=403, detail="В токене нет email (нужен scope email)")

    client = _ch()

    # ЧИТАЕМ НОВУЮ CDC-ВИТРИНУ
    q = """
    SELECT
      user_key,
      customer_id,
      email,
      first_name,
      last_name,
      prosthetic_id,
      period_start,
      period_end,
      sessions,
      avg_latency_ms,
      alerts,
      mart_updated_at
    FROM report_mart_cdc
    WHERE user_key = {user_key:String}
    ORDER BY period_start DESC, mart_updated_at DESC
    LIMIT 1
    """
    res = client.query(q, parameters={"user_key": user_key})
    if not res.result_rows:
        raise HTTPException(status_code=404, detail="Отчёт для пользователя не найден в CDC-витрине")

    row = res.result_rows[0]
    period_start = str(row[6])
    period_end = str(row[7])
    mart_updated_at_raw = str(row[11])
    mart_updated_at_safe = mart_updated_at_raw.replace(" ", "T").replace(":", "-")

    object_key = f"cdc/{user_key}/{period_start}_{period_end}/{mart_updated_at_safe}.json"

    if _s3_exists(S3_BUCKET, object_key):
        return {"cdnUrl": _cdn_url_for_key(object_key), "meta": {"cache": "s3-hit"}}

    report_payload = {
        "userKey": row[0],
        "customer": {
            "customerId": row[1],
            "email": row[2],
            "firstName": row[3],
            "lastName": row[4],
            "prostheticId": row[5],
        },
        "period": {"from": period_start, "to": period_end},
        "summary": {
            "sessions": int(row[8]),
            "avgLatencyMs": float(row[9]),
            "alerts": int(row[10]),
        },
        "mart": {"updatedAt": mart_updated_at_raw},
    }

    _s3_put_json(S3_BUCKET, object_key, report_payload)
    return {"cdnUrl": _cdn_url_for_key(object_key), "meta": {"cache": "generated"}}