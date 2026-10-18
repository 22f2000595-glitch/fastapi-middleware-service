
import os
import time
import uuid
import asyncio
from collections import defaultdict, deque

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware

app = FastAPI()

ALLOWED_ORIGINS = [
    "https://app-m6507z.example.com",
]

# Set EXAM_ORIGIN in Render to the actual origin of the exam page.
exam_origin = os.getenv("EXAM_ORIGIN", "").strip().rstrip("/")
if exam_origin and exam_origin not in ALLOWED_ORIGINS:
    ALLOWED_ORIGINS.append(exam_origin)

EMAIL = os.getenv("EMAIL", "22f2000595@ds.study.iitm.ac.in")
LIMIT = 12
WINDOW = 10.0

# Each client ID has an independent rolling window.
requests_by_client = defaultdict(deque)
rate_lock = asyncio.Lock()


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-ID")
        if request_id is None:
            request_id = str(uuid.uuid4())

        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # Only rate-limit the required endpoint.
        if request.url.path != "/ping" or request.method != "GET":
            return await call_next(request)

        client_id = request.headers.get("X-Client-Id")
        if not client_id:
            client_id = request.client.host if request.client else "unknown"

        now = time.monotonic()

        async with rate_lock:
            bucket = requests_by_client[client_id]

            while bucket and now - bucket[0] >= WINDOW:
                bucket.popleft()

            if len(bucket) >= LIMIT:
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded",
                        "request_id": request.state.request_id,
                    },
                    headers={
                        "X-Request-ID": request.state.request_id,
                        "Retry-After": str(
                            max(1, int(WINDOW - (now - bucket[0])))
                        ),
                    },
                )

            bucket.append(now)

        return await call_next(request)


# Middleware order: CORS -> request context -> rate limiter.
app.add_middleware(RateLimitMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "OPTIONS"],
    allow_headers=["X-Request-ID", "X-Client-Id", "Content-Type"],
    expose_headers=["X-Request-ID"],
)


@app.get("/ping")
async def ping(request: Request):
    return {
        "email": EMAIL,
        "request_id": request.state.request_id,
    }


@app.get("/")
async def root():
    return {"status": "ok", "endpoint": "/ping"}

