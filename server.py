from dotenv import load_dotenv
from pathlib import Path

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

from fastapi import FastAPI, APIRouter, HTTPException, Request, Response, Depends, WebSocket, WebSocketDisconnect
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone, timedelta
import os
import uuid
import logging
import bcrypt
import jwt
import requests as http_requests

# ---------- DB ----------
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# ---------- App ----------
app = FastAPI(title="Zentia VolleyPro API")
api = APIRouter(prefix="/api")

JWT_SECRET = os.environ.get("JWT_SECRET", "dev_secret_change_me")
JWT_ALG = "HS256"

logger = logging.getLogger("zentia")
logging.basicConfig(level=logging.INFO)


# ---------- Helpers ----------
def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


def verify_password(p: str, h: str) -> bool:
    try:
        return bcrypt.checkpw(p.encode(), h.encode())
    except Exception:
        return False


def create_token(user_id: str, kind: str = "access", days: int = 7) -> str:
    payload = {
        "sub": user_id,
        "type": kind,
        "exp": datetime.now(timezone.utc) + timedelta(days=days),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def set_auth_cookie(response: Response, token: str):
    response.set_cookie(
        key="access_token",
        value=token,
        httponly=True,
        secure=True,
        samesite="none",
        max_age=7 * 24 * 3600,
        path="/",
    )


def gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def current_user(request: Request) -> dict:
    token = request.cookies.get("access_token")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
    if not token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")
    user = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0, "password_hash": 0})
    if not user:
        raise HTTPException(401, "User not found")
    return user


def _is_member(user_id: str, team: dict) -> bool:
    if not team:
        return False
    if user_id == team.get("owner_id"):
        return True
    return user_id in [m.get("user_id") for m in team.get("members", [])]


async def require_team_member(team_id: str, user: dict) -> dict:
    team = await db.teams.find_one({"team_id": team_id})
    if not team:
        raise HTTPException(404, "Team not found")
    if not _is_member(user["user_id"], team):
        raise HTTPException(403, "Not a member of this team")
    return team


async def require_match_member(match_id: str, user: dict) -> dict:
    match = await db.matches.find_one({"match_id": match_id}, {"_id": 0})
    if not match:
        raise HTTPException(404, "Match not found")
    await require_team_member(match["team_id"], user)
    return match


# ---------- WebSocket Connection Manager ----------
class ConnectionManager:
    def __init__(self):
        self.rooms: Dict[str, set] = {}

    async def connect(self, room: str, ws: WebSocket):
        await ws.accept()
        self.rooms.setdefault(room, set()).add(ws)

    def disconnect(self, room: str, ws: WebSocket):
        if room in self.rooms:
            self.rooms[room].discard(ws)
            if not self.rooms[room]:
                self.rooms.pop(room, None)

    async def broadcast(self, room: str, message: dict):
        for ws in list(self.rooms.get(room, set())):
            try:
                await ws.send_json(message)
            except Exception:
                self.rooms.get(room, set()).discard(ws)


chat_mgr = ConnectionManager()
dv_mgr = ConnectionManager()


async def _ws_authenticate(websocket: WebSocket) -> Optional[dict]:
    token = websocket.cookies.get("access_token") or websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008)
        return None
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
    except Exception:
        await websocket.close(code=1008)
        return None
    user = await db.users.find_one({"user_id": payload["sub"]}, {"_id": 0, "password_hash": 0})
    if not user:
        await websocket.close(code=1008)
        return None
    return user


# ---------- Models ----------
class RegisterIn(BaseModel):
    email: EmailStr
    password: str
    name: str
    role: str = "head_coach"  # head_coach | assistant_coach | player


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class GoogleSessionIn(BaseModel):
    session_id: str


class TeamIn(BaseModel):
    name: str
    category: Optional[str] = ""
    season: Optional[str] = ""
    color: Optional[str] = "#EA580C"


class PlayerIn(BaseModel):
    team_id: str
    name: str
    number: int
    position: str  # OH, OPP, MB, S, L, DS
    height_cm: Optional[int] = None
    user_id: Optional[str] = None  # link to a player account if exists


class MatchIn(BaseModel):
    team_id: str
    opponent: str
    date: str
    location: Optional[str] = ""
    home: bool = True


class MatchUpdateIn(BaseModel):
    home_score: Optional[int] = None
    away_score: Optional[int] = None
    sets: Optional[List[Dict[str, int]]] = None  # [{home: 25, away: 23}]
    status: Optional[str] = None  # scheduled | live | finished
    notes: Optional[str] = None


class StatIn(BaseModel):
    match_id: str
    player_id: str
    set_number: int = 1
    action: str  # serve, attack, block, dig, set, reception
    quality: str  # ace, kill, error, perfect, medium, neutral
    code: Optional[str] = None  # advanced datavolley code
    timestamp: Optional[str] = None


class LineupIn(BaseModel):
    team_id: str
    match_id: Optional[str] = None
    name: str
    positions: Dict[str, str]  # {"P1": player_id, "P2": player_id, ...}


class TrainingIn(BaseModel):
    team_id: str
    title: str
    date: str
    location: Optional[str] = ""
    notes: Optional[str] = ""


class AttendanceIn(BaseModel):
    training_id: str
    player_id: str
    status: str  # present, absent, late, excused


class AnnouncementIn(BaseModel):
    team_id: str
    title: str
    body: str
    pinned: bool = False


class MessageIn(BaseModel):
    team_id: str
    body: str


class GalleryIn(BaseModel):
    team_id: str
    url: str
    caption: Optional[str] = ""
    kind: str = "image"  # image | video


# ---------- Auth Routes ----------
@api.post("/auth/register")
async def register(payload: RegisterIn, response: Response):
    email = payload.email.lower().strip()
    if await db.users.find_one({"email": email}):
        raise HTTPException(400, "Email already registered")
    role = payload.role if payload.role in ("head_coach", "assistant_coach", "player") else "player"
    user_id = gen_id("user")
    doc = {
        "user_id": user_id,
        "email": email,
        "name": payload.name,
        "role": role,
        "password_hash": hash_password(payload.password),
        "auth_provider": "local",
        "picture": None,
        "team_ids": [],
        "created_at": now_iso(),
    }
    await db.users.insert_one(doc)
    token = create_token(user_id)
    set_auth_cookie(response, token)
    doc.pop("password_hash", None)
    doc.pop("_id", None)
    return {"user": doc, "token": token}


@api.post("/auth/login")
async def login(payload: LoginIn, response: Response):
    email = payload.email.lower().strip()
    user = await db.users.find_one({"email": email})
    if not user or not user.get("password_hash") or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(401, "Invalid credentials")
    token = create_token(user["user_id"])
    set_auth_cookie(response, token)
    user.pop("password_hash", None)
    user.pop("_id", None)
    return {"user": user, "token": token}


@api.post("/auth/google/session")
async def google_session(payload: GoogleSessionIn, response: Response):
    """REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH"""
    try:
        r = http_requests.get(
            "https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
            headers={"X-Session-ID": payload.session_id},
            timeout=10,
        )
        if r.status_code != 200:
            raise HTTPException(401, "Invalid session")
        data = r.json()
    except Exception as e:
        raise HTTPException(401, f"Auth error: {e}")

    email = data["email"].lower()
    existing = await db.users.find_one({"email": email})
    if existing:
        user_id = existing["user_id"]
        await db.users.update_one(
            {"user_id": user_id},
            {"$set": {"name": data.get("name"), "picture": data.get("picture")}},
        )
    else:
        user_id = gen_id("user")
        await db.users.insert_one({
            "user_id": user_id,
            "email": email,
            "name": data.get("name", email),
            "role": "head_coach",
            "auth_provider": "google",
            "picture": data.get("picture"),
            "team_ids": [],
            "created_at": now_iso(),
        })

    token = create_token(user_id)
    set_auth_cookie(response, token)
    user = await db.users.find_one({"user_id": user_id}, {"_id": 0, "password_hash": 0})
    return {"user": user, "token": token}


@api.get("/auth/me")
async def me(user=Depends(current_user)):
    return user


@api.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie("access_token", path="/")
    return {"ok": True}


# ---------- Teams ----------
@api.post("/teams")
async def create_team(payload: TeamIn, user=Depends(current_user)):
    team_id = gen_id("team")
    doc = {
        "team_id": team_id,
        "name": payload.name,
        "category": payload.category,
        "season": payload.season,
        "color": payload.color,
        "owner_id": user["user_id"],
        "members": [{"user_id": user["user_id"], "role": "head_coach"}],
        "created_at": now_iso(),
    }
    await db.teams.insert_one(doc)
    await db.users.update_one({"user_id": user["user_id"]}, {"$addToSet": {"team_ids": team_id}})
    doc.pop("_id", None)
    return doc


@api.get("/teams")
async def list_teams(user=Depends(current_user)):
    teams = await db.teams.find(
        {"$or": [{"owner_id": user["user_id"]}, {"members.user_id": user["user_id"]}]},
        {"_id": 0},
    ).to_list(200)
    return teams


@api.get("/teams/{team_id}")
async def get_team(team_id: str, user=Depends(current_user)):
    t = await db.teams.find_one({"team_id": team_id}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Team not found")
    member_ids = [m["user_id"] for m in t.get("members", [])]
    if user["user_id"] != t.get("owner_id") and user["user_id"] not in member_ids:
        raise HTTPException(403, "Not a member of this team")
    return t


@api.post("/teams/{team_id}/invite")
async def invite_member(team_id: str, body: Dict[str, str], user=Depends(current_user)):
    team = await require_team_member(team_id, user)
    # only head_coach (owner or member with coach role) can invite
    is_coach = user["user_id"] == team.get("owner_id") or any(
        m.get("user_id") == user["user_id"] and m.get("role") in ("head_coach", "assistant_coach")
        for m in team.get("members", [])
    )
    if not is_coach:
        raise HTTPException(403, "Only coaches can invite")
    email = body.get("email", "").lower().strip()
    role = body.get("role", "player")
    invited = await db.users.find_one({"email": email}, {"_id": 0, "password_hash": 0})
    if not invited:
        raise HTTPException(404, "User not found. Ask them to register first.")
    await db.teams.update_one(
        {"team_id": team_id},
        {"$addToSet": {"members": {"user_id": invited["user_id"], "role": role}}},
    )
    await db.users.update_one({"user_id": invited["user_id"]}, {"$addToSet": {"team_ids": team_id}})
    return {"ok": True, "user": invited}


@api.patch("/teams/{team_id}")
async def update_team(team_id: str, payload: TeamIn, user=Depends(current_user)):
    team = await require_team_member(team_id, user)
    if user["user_id"] != team.get("owner_id"):
        raise HTTPException(403, "Only the team owner can edit")
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    await db.teams.update_one({"team_id": team_id}, {"$set": update})
    return await db.teams.find_one({"team_id": team_id}, {"_id": 0})


@api.delete("/teams/{team_id}")
async def delete_team(team_id: str, user=Depends(current_user)):
    team = await db.teams.find_one({"team_id": team_id})
    if not team:
        raise HTTPException(404, "Team not found")
    if user["user_id"] != team.get("owner_id"):
        raise HTTPException(403, "Only the team owner can delete")
    # cascade
    await db.teams.delete_one({"team_id": team_id})
    await db.players.delete_many({"team_id": team_id})
    matches = await db.matches.find({"team_id": team_id}, {"_id": 0, "match_id": 1}).to_list(2000)
    match_ids = [m["match_id"] for m in matches]
    if match_ids:
        await db.stats.delete_many({"match_id": {"$in": match_ids}})
    await db.matches.delete_many({"team_id": team_id})
    await db.lineups.delete_many({"team_id": team_id})
    trainings = await db.trainings.find({"team_id": team_id}, {"_id": 0, "training_id": 1}).to_list(2000)
    training_ids = [t["training_id"] for t in trainings]
    if training_ids:
        await db.attendance.delete_many({"training_id": {"$in": training_ids}})
    await db.trainings.delete_many({"team_id": team_id})
    await db.announcements.delete_many({"team_id": team_id})
    await db.messages.delete_many({"team_id": team_id})
    await db.gallery.delete_many({"team_id": team_id})
    await db.users.update_many({}, {"$pull": {"team_ids": team_id}})
    return {"ok": True}


# ---------- Players ----------
@api.post("/players")
async def create_player(payload: PlayerIn, user=Depends(current_user)):
    await require_team_member(payload.team_id, user)
    pid = gen_id("ply")
    doc = payload.model_dump()
    doc["player_id"] = pid
    doc["created_at"] = now_iso()
    await db.players.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.get("/players")
async def list_players(team_id: str, user=Depends(current_user)):
    await require_team_member(team_id, user)
    players = await db.players.find({"team_id": team_id}, {"_id": 0}).to_list(200)
    return players


@api.delete("/players/{player_id}")
async def delete_player(player_id: str, user=Depends(current_user)):
    p = await db.players.find_one({"player_id": player_id}, {"_id": 0})
    if not p:
        raise HTTPException(404, "Player not found")
    await require_team_member(p["team_id"], user)
    await db.players.delete_one({"player_id": player_id})
    return {"ok": True}


# ---------- Matches ----------
@api.post("/matches")
async def create_match(payload: MatchIn, user=Depends(current_user)):
    await require_team_member(payload.team_id, user)
    mid = gen_id("mat")
    doc = payload.model_dump()
    doc["match_id"] = mid
    doc["status"] = "scheduled"
    doc["home_score"] = 0
    doc["away_score"] = 0
    doc["sets"] = []
    doc["notes"] = ""
    doc["created_at"] = now_iso()
    await db.matches.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.get("/matches")
async def list_matches(team_id: str, user=Depends(current_user)):
    await require_team_member(team_id, user)
    return await db.matches.find({"team_id": team_id}, {"_id": 0}).sort("date", -1).to_list(500)


@api.get("/matches/{match_id}")
async def get_match(match_id: str, user=Depends(current_user)):
    return await require_match_member(match_id, user)


@api.patch("/matches/{match_id}")
async def update_match(match_id: str, payload: MatchUpdateIn, user=Depends(current_user)):
    await require_match_member(match_id, user)
    update = {k: v for k, v in payload.model_dump().items() if v is not None}
    await db.matches.update_one({"match_id": match_id}, {"$set": update})
    m = await db.matches.find_one({"match_id": match_id}, {"_id": 0})
    await dv_mgr.broadcast(f"match:{match_id}", {"type": "match_update", "data": m})
    return m


@api.delete("/matches/{match_id}")
async def delete_match(match_id: str, user=Depends(current_user)):
    await require_match_member(match_id, user)
    await db.matches.delete_one({"match_id": match_id})
    await db.stats.delete_many({"match_id": match_id})
    return {"ok": True}


# ---------- Stats (Datavolley) ----------
@api.post("/stats")
async def add_stat(payload: StatIn, user=Depends(current_user)):
    await require_match_member(payload.match_id, user)
    sid = gen_id("st")
    doc = payload.model_dump()
    doc["stat_id"] = sid
    doc["created_at"] = now_iso()
    if not doc.get("timestamp"):
        doc["timestamp"] = doc["created_at"]
    await db.stats.insert_one(doc)
    doc.pop("_id", None)
    summary = await _compute_summary(payload.match_id)
    await dv_mgr.broadcast(
        f"match:{payload.match_id}",
        {"type": "stat_added", "data": doc, "summary": summary},
    )
    return doc


@api.get("/stats")
async def list_stats(match_id: str, user=Depends(current_user)):
    await require_match_member(match_id, user)
    return await db.stats.find({"match_id": match_id}, {"_id": 0}).sort("created_at", 1).to_list(5000)


@api.delete("/stats/{stat_id}")
async def delete_stat(stat_id: str, user=Depends(current_user)):
    s = await db.stats.find_one({"stat_id": stat_id}, {"_id": 0})
    if not s:
        raise HTTPException(404, "Stat not found")
    await require_match_member(s["match_id"], user)
    await db.stats.delete_one({"stat_id": stat_id})
    summary = await _compute_summary(s["match_id"])
    await dv_mgr.broadcast(
        f"match:{s['match_id']}",
        {"type": "stat_deleted", "stat_id": stat_id, "summary": summary},
    )
    return {"ok": True}


async def _compute_summary(match_id: str) -> Dict[str, Dict[str, int]]:
    stats = await db.stats.find({"match_id": match_id}, {"_id": 0}).to_list(5000)
    summary: Dict[str, Dict[str, int]] = {}
    for s in stats:
        pid = s["player_id"]
        if pid not in summary:
            summary[pid] = {"kills": 0, "errors": 0, "aces": 0, "blocks": 0, "digs": 0, "total": 0}
        summary[pid]["total"] += 1
        q = s.get("quality")
        a = s.get("action")
        if a == "attack" and q == "kill":
            summary[pid]["kills"] += 1
        elif a == "serve" and q == "ace":
            summary[pid]["aces"] += 1
        elif a == "block" and q in ("kill", "perfect"):
            summary[pid]["blocks"] += 1
        elif a == "dig" and q == "perfect":
            summary[pid]["digs"] += 1
        if q == "error":
            summary[pid]["errors"] += 1
    return summary


@api.get("/stats/summary")
async def stats_summary(match_id: str, user=Depends(current_user)):
    await require_match_member(match_id, user)
    return await _compute_summary(match_id)


@api.get("/stats/team-summary")
async def stats_team_summary(team_id: str, user=Depends(current_user)):
    await require_team_member(team_id, user)
    matches = await db.matches.find({"team_id": team_id}, {"_id": 0, "match_id": 1}).to_list(2000)
    match_ids = [m["match_id"] for m in matches]
    if not match_ids:
        return {}
    stats = await db.stats.find({"match_id": {"$in": match_ids}}, {"_id": 0}).to_list(50000)
    summary: Dict[str, Dict[str, Any]] = {}
    matches_played: Dict[str, set] = {}
    for s in stats:
        pid = s["player_id"]
        if pid not in summary:
            summary[pid] = {
                "matches_played": 0, "total": 0,
                "attacks": 0, "kills": 0, "atk_errors": 0,
                "serves": 0, "aces": 0, "serve_errors": 0,
                "blocks": 0, "block_errors": 0,
                "digs": 0,
                "receptions": 0, "rec_excellent": 0, "rec_perfect": 0, "rec_ok": 0, "rec_errors": 0,
                "sets": 0, "set_errors": 0,
                "errors": 0,
            }
            matches_played[pid] = set()
        matches_played[pid].add(s["match_id"])
        a, q = s.get("action"), s.get("quality")
        bucket = summary[pid]
        bucket["total"] += 1
        if a == "attack":
            bucket["attacks"] += 1
            if q == "kill":
                bucket["kills"] += 1
            elif q == "error":
                bucket["atk_errors"] += 1
        elif a == "serve":
            bucket["serves"] += 1
            if q == "ace":
                bucket["aces"] += 1
            elif q == "error":
                bucket["serve_errors"] += 1
        elif a == "block":
            if q in ("kill", "perfect"):
                bucket["blocks"] += 1
            elif q == "error":
                bucket["block_errors"] += 1
        elif a == "dig" and q == "perfect":
            bucket["digs"] += 1
        elif a == "reception":
            bucket["receptions"] += 1
            if q == "kill":
                bucket["rec_excellent"] += 1
            elif q == "perfect":
                bucket["rec_perfect"] += 1
            elif q == "medium":
                bucket["rec_ok"] += 1
            elif q == "error":
                bucket["rec_errors"] += 1
        elif a == "set":
            bucket["sets"] += 1
            if q == "error":
                bucket["set_errors"] += 1
        if q == "error":
            bucket["errors"] += 1
    for pid, s in summary.items():
        s["matches_played"] = len(matches_played[pid])
        rec = s["receptions"]
        s["reception_pct"] = round(((s["rec_excellent"] + s["rec_perfect"]) / rec) * 100, 1) if rec else 0.0
        atk = s["attacks"]
        s["attack_pct"] = round(((s["kills"] - s["atk_errors"]) / atk) * 100, 1) if atk else 0.0
    return summary


# ---------- Lineups ----------
@api.post("/lineups")
async def create_lineup(payload: LineupIn, user=Depends(current_user)):
    await require_team_member(payload.team_id, user)
    lid = gen_id("ln")
    doc = payload.model_dump()
    doc["lineup_id"] = lid
    doc["created_at"] = now_iso()
    await db.lineups.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.get("/lineups")
async def list_lineups(team_id: str, user=Depends(current_user)):
    await require_team_member(team_id, user)
    return await db.lineups.find({"team_id": team_id}, {"_id": 0}).to_list(500)


@api.delete("/lineups/{lineup_id}")
async def delete_lineup(lineup_id: str, user=Depends(current_user)):
    l = await db.lineups.find_one({"lineup_id": lineup_id}, {"_id": 0})
    if not l:
        raise HTTPException(404, "Lineup not found")
    await require_team_member(l["team_id"], user)
    await db.lineups.delete_one({"lineup_id": lineup_id})
    return {"ok": True}


# ---------- Trainings ----------
@api.post("/trainings")
async def create_training(payload: TrainingIn, user=Depends(current_user)):
    await require_team_member(payload.team_id, user)
    tid = gen_id("tr")
    doc = payload.model_dump()
    doc["training_id"] = tid
    doc["created_at"] = now_iso()
    await db.trainings.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.get("/trainings")
async def list_trainings(team_id: str, user=Depends(current_user)):
    await require_team_member(team_id, user)
    return await db.trainings.find({"team_id": team_id}, {"_id": 0}).sort("date", -1).to_list(500)


@api.delete("/trainings/{training_id}")
async def delete_training(training_id: str, user=Depends(current_user)):
    t = await db.trainings.find_one({"training_id": training_id}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Training not found")
    await require_team_member(t["team_id"], user)
    await db.trainings.delete_one({"training_id": training_id})
    return {"ok": True}


@api.post("/attendance")
async def mark_attendance(payload: AttendanceIn, user=Depends(current_user)):
    t = await db.trainings.find_one({"training_id": payload.training_id}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Training not found")
    await require_team_member(t["team_id"], user)
    await db.attendance.update_one(
        {"training_id": payload.training_id, "player_id": payload.player_id},
        {"$set": payload.model_dump() | {"updated_at": now_iso()}},
        upsert=True,
    )
    return {"ok": True}


@api.get("/attendance")
async def get_attendance(training_id: str, user=Depends(current_user)):
    t = await db.trainings.find_one({"training_id": training_id}, {"_id": 0})
    if not t:
        raise HTTPException(404, "Training not found")
    await require_team_member(t["team_id"], user)
    return await db.attendance.find({"training_id": training_id}, {"_id": 0}).to_list(200)


# ---------- Communications ----------
@api.post("/announcements")
async def create_announcement(payload: AnnouncementIn, user=Depends(current_user)):
    await require_team_member(payload.team_id, user)
    aid = gen_id("ann")
    doc = payload.model_dump()
    doc["announcement_id"] = aid
    doc["author_id"] = user["user_id"]
    doc["author_name"] = user.get("name", "")
    doc["created_at"] = now_iso()
    await db.announcements.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.get("/announcements")
async def list_announcements(team_id: str, user=Depends(current_user)):
    await require_team_member(team_id, user)
    return await db.announcements.find({"team_id": team_id}, {"_id": 0}).sort("created_at", -1).to_list(200)


@api.delete("/announcements/{announcement_id}")
async def delete_announcement(announcement_id: str, user=Depends(current_user)):
    a = await db.announcements.find_one({"announcement_id": announcement_id}, {"_id": 0})
    if not a:
        raise HTTPException(404, "Announcement not found")
    await require_team_member(a["team_id"], user)
    await db.announcements.delete_one({"announcement_id": announcement_id})
    return {"ok": True}


@api.post("/messages")
async def post_message(payload: MessageIn, user=Depends(current_user)):
    await require_team_member(payload.team_id, user)
    mid = gen_id("msg")
    doc = payload.model_dump()
    doc["message_id"] = mid
    doc["author_id"] = user["user_id"]
    doc["author_name"] = user.get("name", "")
    doc["author_picture"] = user.get("picture")
    doc["created_at"] = now_iso()
    await db.messages.insert_one(doc)
    doc.pop("_id", None)
    await chat_mgr.broadcast(f"team:{payload.team_id}", {"type": "message", "data": doc})
    return doc


@api.get("/messages")
async def list_messages(team_id: str, user=Depends(current_user)):
    await require_team_member(team_id, user)
    return await db.messages.find({"team_id": team_id}, {"_id": 0}).sort("created_at", 1).to_list(500)


# ---------- Gallery ----------
@api.post("/gallery")
async def add_gallery(payload: GalleryIn, user=Depends(current_user)):
    await require_team_member(payload.team_id, user)
    gid = gen_id("gal")
    doc = payload.model_dump()
    doc["gallery_id"] = gid
    doc["author_id"] = user["user_id"]
    doc["created_at"] = now_iso()
    await db.gallery.insert_one(doc)
    doc.pop("_id", None)
    return doc


@api.get("/gallery")
async def list_gallery(team_id: str, user=Depends(current_user)):
    await require_team_member(team_id, user)
    return await db.gallery.find({"team_id": team_id}, {"_id": 0}).sort("created_at", -1).to_list(500)


@api.delete("/gallery/{gallery_id}")
async def delete_gallery(gallery_id: str, user=Depends(current_user)):
    g = await db.gallery.find_one({"gallery_id": gallery_id}, {"_id": 0})
    if not g:
        raise HTTPException(404, "Gallery item not found")
    await require_team_member(g["team_id"], user)
    await db.gallery.delete_one({"gallery_id": gallery_id})
    return {"ok": True}


# ---------- WebSockets ----------
@api.websocket("/ws/chat/{team_id}")
async def ws_chat(websocket: WebSocket, team_id: str):
    user = await _ws_authenticate(websocket)
    if not user:
        return
    team = await db.teams.find_one({"team_id": team_id})
    if not team or not _is_member(user["user_id"], team):
        await websocket.close(code=1008)
        return
    room = f"team:{team_id}"
    await chat_mgr.connect(room, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            body = (data.get("body") or "").strip()
            if not body:
                continue
            doc = {
                "message_id": gen_id("msg"),
                "team_id": team_id,
                "body": body,
                "author_id": user["user_id"],
                "author_name": user.get("name", ""),
                "author_picture": user.get("picture"),
                "created_at": now_iso(),
            }
            await db.messages.insert_one(doc)
            doc.pop("_id", None)
            await chat_mgr.broadcast(room, {"type": "message", "data": doc})
    except WebSocketDisconnect:
        chat_mgr.disconnect(room, websocket)
    except Exception:
        chat_mgr.disconnect(room, websocket)


@api.websocket("/ws/datavolley/{match_id}")
async def ws_datavolley(websocket: WebSocket, match_id: str):
    user = await _ws_authenticate(websocket)
    if not user:
        return
    match = await db.matches.find_one({"match_id": match_id})
    if not match:
        await websocket.close(code=1008)
        return
    team = await db.teams.find_one({"team_id": match["team_id"]})
    if not team or not _is_member(user["user_id"], team):
        await websocket.close(code=1008)
        return
    room = f"match:{match_id}"
    await dv_mgr.connect(room, websocket)
    try:
        while True:
            # keepalive — clients can also send ping payloads
            await websocket.receive_text()
    except WebSocketDisconnect:
        dv_mgr.disconnect(room, websocket)
    except Exception:
        dv_mgr.disconnect(room, websocket)


# ---------- Health ----------
@api.get("/")
async def root():
    return {"app": "Zentia VolleyPro", "status": "ok"}


# ---------- Mount ----------
app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_origin_regex=".*",
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def startup():
    await db.users.create_index("email", unique=True)
    await db.users.create_index("user_id", unique=True)
    await db.teams.create_index("team_id", unique=True)
    await db.players.create_index("player_id", unique=True)
    await db.matches.create_index("match_id", unique=True)
    # seed admin
    admin_email = os.environ.get("ADMIN_EMAIL", "admin@zentia.com")
    admin_password = os.environ.get("ADMIN_PASSWORD", "ZentiaAdmin2026!")
    existing = await db.users.find_one({"email": admin_email})
    if not existing:
        await db.users.insert_one({
            "user_id": gen_id("user"),
            "email": admin_email,
            "name": "Zentia Admin",
            "role": "head_coach",
            "password_hash": hash_password(admin_password),
            "auth_provider": "local",
            "picture": None,
            "team_ids": [],
            "created_at": now_iso(),
        })
        logger.info("Admin user seeded")


@app.on_event("shutdown")
async def shutdown():
    client.close()
