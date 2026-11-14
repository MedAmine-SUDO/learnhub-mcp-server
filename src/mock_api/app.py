"""Mock LearnHub LMS API: OAuth client credentials, tenant isolation, pagination, rate limits.

Stands in for a real LMS so the MCP server can be run and tested end to end.
"""

import math
import os
import secrets
import time
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, Form, Header, HTTPException, Query
from fastapi.responses import JSONResponse

from .data import DATASETS

# Demo OAuth clients: client_id -> (client_secret, company_id)
CLIENTS = {"acme-client": ("acme-secret", 1), "globex-client": ("globex-secret", 2)}
TOKEN_TTL_SECONDS = int(os.getenv("MOCK_TOKEN_TTL", "3600"))
RATE_LIMIT_PER_MINUTE = int(os.getenv("MOCK_RATE_LIMIT", "300"))

app = FastAPI(title="LearnHub mock LMS API", version="1.0")
_tokens: dict[str, tuple[int, float]] = {}
_requests: dict[int, deque] = defaultdict(deque)


@app.post("/oauth/token")
def issue_token(
    grant_type: str = Form(...), client_id: str = Form(...), client_secret: str = Form(...)
):
    expected = CLIENTS.get(client_id)
    if grant_type != "client_credentials" or not expected or not secrets.compare_digest(expected[0], client_secret):
        raise HTTPException(401, "invalid_client")
    token = secrets.token_urlsafe(24)
    _tokens[token] = (expected[1], time.time() + TOKEN_TTL_SECONDS)
    return {"access_token": token, "token_type": "Bearer", "expires_in": TOKEN_TTL_SECONDS}


def company(authorization: str = Header("")) -> int:
    token = authorization.removeprefix("Bearer ").strip()
    entry = _tokens.get(token)
    if not entry or entry[1] < time.time():
        raise HTTPException(401, "invalid_token")
    company_id = entry[0]
    window = _requests[company_id]
    now = time.time()
    while window and now - window[0] >= 60:
        window.popleft()
    if len(window) >= RATE_LIMIT_PER_MINUTE:
        raise HTTPException(429, "rate_limited", headers={"Retry-After": str(math.ceil(60 - (now - window[0])))})
    window.append(now)
    return company_id


def _page(items: list, page: int, page_size: int) -> dict:
    total = len(items)
    start = (page - 1) * page_size
    return {
        "data": items[start : start + page_size],
        "meta": {"page": page, "page_size": page_size, "total": total,
                 "total_pages": max(1, math.ceil(total / page_size))},
    }


@app.get("/v1/users")
def list_users(
    company_id: int = Depends(company),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    role: str | None = None,
    status: str | None = None,
    search: str | None = None,
):
    users = DATASETS[company_id]["users"]
    if role:
        users = [u for u in users if u["role"] == role]
    if status:
        users = [u for u in users if u["status"] == status]
    if search:
        q = search.lower()
        users = [u for u in users if q in f"{u['first_name']} {u['last_name']} {u['email']}".lower()]
    return _page(users, page, page_size)


@app.get("/v1/users/{user_id}")
def get_user(user_id: int, company_id: int = Depends(company)):
    for user in DATASETS[company_id]["users"]:
        if user["id"] == user_id:
            return user
    raise HTTPException(404, "user_not_found")


@app.get("/v1/trainings")
def list_trainings(
    company_id: int = Depends(company),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    status: str | None = None,
    category: str | None = None,
):
    trainings = DATASETS[company_id]["trainings"]
    if status:
        trainings = [t for t in trainings if t["status"] == status]
    if category:
        trainings = [t for t in trainings if t["category"].lower() == category.lower()]
    return _page(trainings, page, page_size)


def _training(company_id: int, training_id: int) -> dict:
    for training in DATASETS[company_id]["trainings"]:
        if training["id"] == training_id:
            return training
    raise HTTPException(404, "training_not_found")


@app.get("/v1/trainings/{training_id}")
def get_training(training_id: int, company_id: int = Depends(company)):
    training = _training(company_id, training_id)
    enrolled = [e for e in DATASETS[company_id]["enrollments"] if e["training_id"] == training_id]
    done = sum(e["completion"] == "completed" for e in enrolled)
    return {**training, "enrolled": len(enrolled),
            "completion_rate": round(done / len(enrolled), 3) if enrolled else 0.0}


@app.get("/v1/trainings/{training_id}/participants")
def list_participants(
    training_id: int,
    company_id: int = Depends(company),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    completion: str | None = None,
):
    _training(company_id, training_id)
    users = {u["id"]: u for u in DATASETS[company_id]["users"]}
    rows = []
    for e in DATASETS[company_id]["enrollments"]:
        if e["training_id"] != training_id or (completion and e["completion"] != completion):
            continue
        u = users[e["user_id"]]
        rows.append({**e, "name": f"{u['first_name']} {u['last_name']}", "email": u["email"]})
    return _page(rows, page, page_size)


@app.exception_handler(HTTPException)
def _http_error(request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code, headers=exc.headers)


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=os.getenv("MOCK_HOST", "127.0.0.1"), port=int(os.getenv("MOCK_PORT", "8000")))
