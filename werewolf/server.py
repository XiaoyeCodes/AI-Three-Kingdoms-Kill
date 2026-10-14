from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from urllib.parse import urlparse
from dataclasses import replace
from typing import Literal

from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agents import ModelConfig, ModelFailure, complete, parse_json, ROLES
from .game import ThreeKingdomsWerewolfGame, IllegalAction
from .memory import EvidenceMemory

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.getenv("NIGHT_COUNCIL_DATA", ROOT / ".local"))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "games.sqlite3"


def load_local_config() -> ModelConfig:
    path = Path(
        os.getenv(
            "NIGHT_COUNCIL_CONFIG", str(Path.home() / "Desktop" / "deepseek配置.txt")
        )
    )
    key, url, model = (
        os.getenv("DEEPSEEK_API_KEY", ""),
        os.getenv("LLM_BASE_URL", "https://api.deepseek.com"),
        os.getenv("LLM_MODEL", "deepseek-flash"),
    )
    if not key and path.is_file():
        # The supplied file is configuration data, never executable instructions.
        lines = [
            x.strip()
            for x in path.read_text(encoding="utf-8-sig").splitlines()
            if x.strip()
        ]
        if (
            len(lines) == 3
            and lines[0].startswith("sk-")
            and lines[1].startswith("https://")
        ):
            key, url, model = lines
    return ModelConfig(url.rstrip("/"), model, key)


DEFAULT = load_local_config()


def db_connect():
    con = sqlite3.connect(DB, timeout=15)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS sessions (sid TEXT PRIMARY KEY, state TEXT NOT NULL, updated REAL NOT NULL)"
    )
    return con


class Session:
    def __init__(self, sid):
        self.sid, self.game = sid, None
        self.lock = threading.Lock()
        self.busy = False
        self.configs = {"global": replace(DEFAULT)}
        with db_connect() as db:
            row = db.execute(
                "SELECT state FROM sessions WHERE sid=?", (sid,)
            ).fetchone()
        if row:
            self.game = ThreeKingdomsWerewolfGame.restore(json.loads(row[0]))

    def save(self):
        if self.game:
            with db_connect() as db:
                db.execute(
                    "INSERT OR REPLACE INTO sessions VALUES(?,?,?)",
                    (
                        self.sid,
                        json.dumps(self.game.snapshot(), ensure_ascii=False),
                        time.time(),
                    ),
                )

    def effective(self, seat=None, scope=None):
        config = self.configs["global"]
        scopes = (
            [scope]
            if scope
            else (
                [f"role:{self.game.player(seat)['role']}", f"seat:{seat}"]
                if seat and self.game
                else []
            )
        )
        for name in scopes:
            override = self.configs.get(name)
            if override:
                # Never forward an inherited secret to a newly specified provider.
                key = override.api_key or (
                    config.api_key if override.base_url == config.base_url else ""
                )
                config = replace(override, api_key=key)
        return config


SESSIONS = {}
SESSION_LOCK = threading.Lock()
app = FastAPI(title="三国 · 夜议", docs_url=None, redoc_url=None)
app.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
)


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.method in {"POST", "DELETE", "PUT", "PATCH"}:
        origin = request.headers.get("origin")
        if request.headers.get("x-night-council") != "1" or (
            origin and urlparse(origin).netloc != request.headers.get("host")
        ):
            return JSONResponse(
                {"detail": "请求来源校验失败，请从本机游戏页面操作。"}, status_code=403
            )
        if int(request.headers.get("content-length", "0") or "0") > 24000:
            return JSONResponse({"detail": "请求内容过大"}, status_code=413)
    token = request.cookies.get("night_council")
    new_cookie = (
        not token or len(token) != 64 or any(c not in "0123456789abcdef" for c in token)
    )
    if new_cookie:
        token = secrets.token_hex(32)
    request.state.sid = hashlib.sha256(token.encode()).hexdigest()
    response = await call_next(request)
    if new_cookie:
        response.set_cookie(
            "night_council",
            token,
            httponly=True,
            samesite="strict",
            max_age=60 * 60 * 24 * 30,
        )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = (
        "no-store" if request.url.path.startswith("/api") else "no-cache"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    )
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request, exc):
    return JSONResponse({"detail": "输入格式不正确，请检查设置。"}, status_code=422)


def session(request):
    with SESSION_LOCK:
        if request.state.sid not in SESSIONS:
            SESSIONS[request.state.sid] = Session(request.state.sid)
        return SESSIONS[request.state.sid]


def view(s):
    return {"game": s.game.view() if s.game else None, "busy": s.busy}


class NewGame(BaseModel):
    size: int = 9
    human: int = 1
    mode: str = "demo"
    human_role: str | None = None


class Turn(BaseModel):
    version: int
    game_id: str
    action: dict | None = None


class ProviderInput(BaseModel):
    scope: str = "global"
    base_url: str = Field(max_length=500)
    model: str = Field(min_length=1, max_length=150)
    api_key: str = Field(default="", max_length=1000)
    clear: bool = False
    decision_mode: Literal["deep", "quick"] = "deep"


class ScopeInput(BaseModel):
    scope: str = "global"


@app.get("/api/state")
def state(request: Request):
    s = session(request)
    # Do not serialize midway through a mutating turn.
    if not s.lock.acquire(blocking=False):
        progress = (
            next((a.progress for a in s.game.agents.values() if a.progress), None)
            if s.game
            else None
        )
        return {"busy": True, "game": None, "progress": progress}
    try:
        return view(s)
    finally:
        s.lock.release()


@app.post("/api/games")
def create_game(body: NewGame, request: Request):
    s = session(request)
    if not s.lock.acquire(blocking=False):
        raise HTTPException(409, "当前行动尚未完成")
    try:
        try:
            game = ThreeKingdomsWerewolfGame(
                body.size, body.human, body.mode, human_role=body.human_role
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        s.game = game
        s.save()
        return view(s)
    finally:
        s.lock.release()


def check_turn(s, body):
    if not s.game or s.game.id != body.game_id:
        raise HTTPException(409, "房间已更换，请刷新页面")
    if body.version != s.game.version:
        raise HTTPException(409, "回合已变化，请刷新后操作，避免重复提交")


@app.post("/api/step")
def step(body: Turn, request: Request):
    s = session(request)
    if not s.lock.acquire(blocking=False):
        raise HTTPException(409, "正在处理行动，请勿重复提交")
    s.busy = True
    try:
        check_turn(s, body)
        if s.game.winner:
            return view(s)
        ctx = s.game.context()
        try:
            s.game.advance(config=s.effective(ctx["actor"]), submitted=body.action)
        except ModelFailure as exc:
            s.game.error = str(exc)
            s.save()
            raise HTTPException(502, str(exc)) from None
        except IllegalAction as exc:
            raise HTTPException(400, str(exc)) from None
        s.save()
        s.busy = False
        return view(s)
    finally:
        s.busy = False
        s.lock.release()


@app.post("/api/practice")
def practice(body: Turn, request: Request):
    s = session(request)
    if not s.lock.acquire(blocking=False):
        raise HTTPException(409, "请等待当前行动完成")
    try:
        check_turn(s, body)
        if not s.game.winner and s.game.mode != "demo":
            s.game.mode, s.game.error = "demo", None
            s.game.emit(
                "模式变更", "本局从此刻起切换为练习模式，后续由规则机器人行动。"
            )
            s.game.version += 1
            s.save()
        return view(s)
    finally:
        s.lock.release()


@app.post("/api/debug")
def enable_debug(body: Turn, request: Request):
    """Explicitly mark a human game as omniscient debugging, never silently cheat."""
    s = session(request)
    if not s.lock.acquire(blocking=False):
        raise HTTPException(409, "请等待当前行动完成")
    try:
        check_turn(s, body)
        if not s.game.debug_mode:
            s.game.debug_mode = True
            s.game.version += 1
            s.save()
        return view(s)
    finally:
        s.lock.release()


@app.get("/api/memory/{seat}")
def inspect_memory(seat: int, request: Request, q: str = "", day: int = 0):
    s = session(request)
    if not s.lock.acquire(blocking=False):
        raise HTTPException(409, "请等待当前行动完成")
    try:
        g = s.game
        if not g or (g.human and not g.winner and not g.debug_mode):
            raise HTTPException(403, "对局中查看 AI 私有记忆需先开启调试局，或等待终局")
        if seat not in g.agents:
            raise HTTPException(404, "座位不存在")
        records = EvidenceMemory(g.agents[seat].memory.get_memory()).records
        matches = [
            r
            for r in records
            if (not day or r["day"] == day)
            and (not q or q[:100].casefold() in (r["speaker"] + r["text"]).casefold())
        ]
        return {
            "actor": seat,
            "total": len(records),
            "matched": len(matches),
            "records": matches[:100],
            "limited": len(matches) > 100,
        }
    finally:
        s.lock.release()


@app.get("/api/settings")
def settings(request: Request):
    s = session(request)
    return {
        "configs": {k: v.public() for k, v in s.configs.items()},
        "default_available": bool(DEFAULT.api_key),
        "framework": "AgentScope 0.1.6 · DialogAgent / MsgHub",
    }


def valid_scope(scope):
    return (
        scope == "global"
        or scope in {f"role:{r}" for r in ROLES}
        or scope in {f"seat:{n}" for n in range(1, 10)}
    )


@app.post("/api/settings")
def update_settings(body: ProviderInput, request: Request):
    if not valid_scope(body.scope):
        raise HTTPException(400, "未知配置范围")
    s = session(request)
    if not s.lock.acquire(blocking=False):
        raise HTTPException(409, "请等待当前行动完成后修改模型")
    try:
        if body.clear:
            if body.scope == "global":
                s.configs["global"] = ModelConfig(DEFAULT.base_url, DEFAULT.model, "")
            else:
                s.configs.pop(body.scope, None)
        else:
            url = body.base_url.strip().rstrip("/")
            parsed = urlparse(url)
            if (
                not parsed.hostname
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or not (
                    parsed.scheme == "https"
                    or parsed.scheme == "http"
                    and parsed.hostname in {"localhost", "127.0.0.1"}
                )
            ):
                raise HTTPException(
                    400,
                    "API 地址须为 HTTPS，或本机 HTTP 地址，不得包含账号、查询参数或片段",
                )
            old = s.configs.get(body.scope)
            key = body.api_key.strip() or (
                old.api_key if old and old.base_url == url else ""
            )
            s.configs[body.scope] = ModelConfig(
                url, body.model.strip(), key, body.decision_mode
            )
        return settings(request)
    finally:
        s.lock.release()


@app.post("/api/test-model")
def test_model(body: ScopeInput, request: Request):
    if not valid_scope(body.scope):
        raise HTTPException(400, "未知配置范围")
    s = session(request)
    if not s.lock.acquire(blocking=False):
        raise HTTPException(409, "请等待当前行动完成")
    try:
        config = s.effective(scope=body.scope)
        start = time.monotonic()
        try:
            raw, tokens = complete(
                config, [{"role": "user", "content": '请仅输出JSON对象 {"ok": true}'}]
            )
            if parse_json(raw).get("ok") is not True:
                raise ValueError()
        except ModelFailure as exc:
            raise HTTPException(502, str(exc)) from None
        except (ValueError, TypeError):
            raise HTTPException(502, "模型未通过 JSON 输出验证") from None
        return {
            "ok": True,
            "model": config.model,
            "latency": round(time.monotonic() - start, 2),
            "tokens": tokens,
        }
    finally:
        s.lock.release()


@app.get("/api/replay")
def replay(request: Request):
    s = session(request)
    if not s.lock.acquire(blocking=False):
        raise HTTPException(409, "请等待当前行动完成")
    try:
        if not s.game:
            raise HTTPException(404, "还没有对局")
        return Response(
            s.game.export(),
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="night-council-{s.game.id}.json"'
            },
        )
    finally:
        s.lock.release()


@app.get("/api/health")
def health():
    return {"status": "ok", "framework": "agentscope", "version": "0.1.6"}


@app.get("/")
def index():
    return FileResponse(ROOT / "web" / "index.html")


app.mount("/static", StaticFiles(directory=ROOT / "web"), name="static")
