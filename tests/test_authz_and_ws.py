"""
Tests for team-member authorization on resource endpoints
and WebSocket chat / datavolley live updates.

Iteration 3 — Zentia VolleyPro
"""
import os
import asyncio
import json
import uuid
import pytest
import requests
import websockets

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://volley-pro-hub.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"
WS = API.replace("http", "ws", 1)  # https -> wss

UNIQ = uuid.uuid4().hex[:6]
COACH_A = {"email": f"coachA_{UNIQ}@test.zentia.com", "password": "Pass1234!", "name": "Coach A", "role": "head_coach"}
COACH_B = {"email": f"coachB_{UNIQ}@test.zentia.com", "password": "Pass1234!", "name": "Coach B", "role": "head_coach"}


def _register_or_login(payload):
    s = requests.Session()
    r = s.post(f"{API}/auth/register", json=payload)
    if r.status_code == 400:  # already exists
        r = s.post(f"{API}/auth/login", json={"email": payload["email"], "password": payload["password"]})
    assert r.status_code == 200, f"auth failed: {r.status_code} {r.text}"
    return s, r.json()["token"]


# ---------- Fixtures ----------
@pytest.fixture(scope="module")
def coach_a():
    s, tok = _register_or_login(COACH_A)
    return s, tok


@pytest.fixture(scope="module")
def coach_b():
    s, tok = _register_or_login(COACH_B)
    return s, tok


@pytest.fixture(scope="module")
def team_a(coach_a):
    s, _ = coach_a
    r = s.post(f"{API}/teams", json={"name": f"TEST_TeamA_{UNIQ}", "category": "Senior", "season": "2026"})
    assert r.status_code == 200
    return r.json()


@pytest.fixture(scope="module")
def team_b(coach_b):
    s, _ = coach_b
    r = s.post(f"{API}/teams", json={"name": f"TEST_TeamB_{UNIQ}", "category": "Senior", "season": "2026"})
    assert r.status_code == 200
    return r.json()


@pytest.fixture(scope="module")
def player_a(coach_a, team_a):
    s, _ = coach_a
    r = s.post(f"{API}/players", json={
        "team_id": team_a["team_id"], "name": "TEST_PA_Player", "number": 7, "position": "OH"
    })
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def match_a(coach_a, team_a):
    s, _ = coach_a
    r = s.post(f"{API}/matches", json={
        "team_id": team_a["team_id"], "opponent": "TEST_Opp", "date": "2026-02-01", "home": True
    })
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def stat_a(coach_a, match_a, player_a):
    s, _ = coach_a
    r = s.post(f"{API}/stats", json={
        "match_id": match_a["match_id"], "player_id": player_a["player_id"],
        "set_number": 1, "action": "attack", "quality": "kill"
    })
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def lineup_a(coach_a, team_a, player_a):
    s, _ = coach_a
    r = s.post(f"{API}/lineups", json={
        "team_id": team_a["team_id"], "name": "TEST_Lineup",
        "positions": {"P1": player_a["player_id"]}
    })
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def training_a(coach_a, team_a):
    s, _ = coach_a
    r = s.post(f"{API}/trainings", json={
        "team_id": team_a["team_id"], "title": "TEST_Training", "date": "2026-02-02"
    })
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def announcement_a(coach_a, team_a):
    s, _ = coach_a
    r = s.post(f"{API}/announcements", json={
        "team_id": team_a["team_id"], "title": "TEST_Ann", "body": "hello"
    })
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture(scope="module")
def gallery_a(coach_a, team_a):
    s, _ = coach_a
    r = s.post(f"{API}/gallery", json={
        "team_id": team_a["team_id"], "url": "https://picsum.photos/200", "kind": "image"
    })
    assert r.status_code == 200, r.text
    return r.json()


# ---------- Authorization (403 for non-members) ----------
class TestAuthorization403:

    def test_team_get_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.get(f"{API}/teams/{team_a['team_id']}")
        assert r.status_code == 403, r.text

    def test_team_invite_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.post(f"{API}/teams/{team_a['team_id']}/invite", json={"email": COACH_B["email"], "role": "player"})
        assert r.status_code == 403, r.text

    def test_players_list_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.get(f"{API}/players?team_id={team_a['team_id']}")
        assert r.status_code == 403

    def test_players_create_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.post(f"{API}/players", json={
            "team_id": team_a["team_id"], "name": "Hack", "number": 99, "position": "OH"
        })
        assert r.status_code == 403

    def test_player_delete_forbidden(self, coach_b, player_a):
        s, _ = coach_b
        r = s.delete(f"{API}/players/{player_a['player_id']}")
        assert r.status_code == 403

    def test_matches_list_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.get(f"{API}/matches?team_id={team_a['team_id']}")
        assert r.status_code == 403

    def test_match_get_forbidden(self, coach_b, match_a):
        s, _ = coach_b
        r = s.get(f"{API}/matches/{match_a['match_id']}")
        assert r.status_code == 403

    def test_match_patch_forbidden(self, coach_b, match_a):
        s, _ = coach_b
        r = s.patch(f"{API}/matches/{match_a['match_id']}", json={"home_score": 99})
        assert r.status_code == 403

    def test_match_delete_forbidden(self, coach_b, match_a):
        s, _ = coach_b
        r = s.delete(f"{API}/matches/{match_a['match_id']}")
        assert r.status_code == 403

    def test_stats_list_forbidden(self, coach_b, match_a):
        s, _ = coach_b
        r = s.get(f"{API}/stats?match_id={match_a['match_id']}")
        assert r.status_code == 403

    def test_stats_summary_forbidden(self, coach_b, match_a):
        s, _ = coach_b
        r = s.get(f"{API}/stats/summary?match_id={match_a['match_id']}")
        assert r.status_code == 403

    def test_stats_create_forbidden(self, coach_b, match_a, player_a):
        s, _ = coach_b
        r = s.post(f"{API}/stats", json={
            "match_id": match_a["match_id"], "player_id": player_a["player_id"],
            "set_number": 1, "action": "attack", "quality": "kill"
        })
        assert r.status_code == 403

    def test_stat_delete_forbidden(self, coach_b, stat_a):
        s, _ = coach_b
        r = s.delete(f"{API}/stats/{stat_a['stat_id']}")
        assert r.status_code == 403

    def test_lineup_list_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.get(f"{API}/lineups?team_id={team_a['team_id']}")
        assert r.status_code == 403

    def test_lineup_create_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.post(f"{API}/lineups", json={"team_id": team_a["team_id"], "name": "X", "positions": {}})
        assert r.status_code == 403

    def test_lineup_delete_forbidden(self, coach_b, lineup_a):
        s, _ = coach_b
        r = s.delete(f"{API}/lineups/{lineup_a['lineup_id']}")
        assert r.status_code == 403

    def test_training_list_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.get(f"{API}/trainings?team_id={team_a['team_id']}")
        assert r.status_code == 403

    def test_training_create_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.post(f"{API}/trainings", json={"team_id": team_a["team_id"], "title": "x", "date": "2026-01-01"})
        assert r.status_code == 403

    def test_training_delete_forbidden(self, coach_b, training_a):
        s, _ = coach_b
        r = s.delete(f"{API}/trainings/{training_a['training_id']}")
        assert r.status_code == 403

    def test_attendance_get_forbidden(self, coach_b, training_a):
        s, _ = coach_b
        r = s.get(f"{API}/attendance?training_id={training_a['training_id']}")
        assert r.status_code == 403

    def test_attendance_post_forbidden(self, coach_b, training_a, player_a):
        s, _ = coach_b
        r = s.post(f"{API}/attendance", json={
            "training_id": training_a["training_id"], "player_id": player_a["player_id"], "status": "present"
        })
        assert r.status_code == 403

    def test_announcement_list_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.get(f"{API}/announcements?team_id={team_a['team_id']}")
        assert r.status_code == 403

    def test_announcement_create_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.post(f"{API}/announcements", json={"team_id": team_a["team_id"], "title": "x", "body": "y"})
        assert r.status_code == 403

    def test_announcement_delete_forbidden(self, coach_b, announcement_a):
        s, _ = coach_b
        r = s.delete(f"{API}/announcements/{announcement_a['announcement_id']}")
        assert r.status_code == 403

    def test_messages_list_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.get(f"{API}/messages?team_id={team_a['team_id']}")
        assert r.status_code == 403

    def test_messages_post_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.post(f"{API}/messages", json={"team_id": team_a["team_id"], "body": "hack"})
        assert r.status_code == 403

    def test_gallery_list_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.get(f"{API}/gallery?team_id={team_a['team_id']}")
        assert r.status_code == 403

    def test_gallery_create_forbidden(self, coach_b, team_a):
        s, _ = coach_b
        r = s.post(f"{API}/gallery", json={"team_id": team_a["team_id"], "url": "u", "kind": "image"})
        assert r.status_code == 403

    def test_gallery_delete_forbidden(self, coach_b, gallery_a):
        s, _ = coach_b
        r = s.delete(f"{API}/gallery/{gallery_a['gallery_id']}")
        assert r.status_code == 403


# ---------- Member regression: 200 OK ----------
class TestMemberRegression:

    def test_member_get_team(self, coach_a, team_a):
        s, _ = coach_a
        r = s.get(f"{API}/teams/{team_a['team_id']}")
        assert r.status_code == 200

    def test_member_list_players(self, coach_a, team_a):
        s, _ = coach_a
        assert s.get(f"{API}/players?team_id={team_a['team_id']}").status_code == 200

    def test_member_list_matches(self, coach_a, team_a):
        s, _ = coach_a
        assert s.get(f"{API}/matches?team_id={team_a['team_id']}").status_code == 200

    def test_member_get_match(self, coach_a, match_a):
        s, _ = coach_a
        assert s.get(f"{API}/matches/{match_a['match_id']}").status_code == 200

    def test_member_stats_summary(self, coach_a, match_a):
        s, _ = coach_a
        assert s.get(f"{API}/stats/summary?match_id={match_a['match_id']}").status_code == 200


# ---------- DELETE 404 behavior on non-existent ----------
class TestDelete404:

    def test_delete_nonexistent_player(self, coach_a):
        s, _ = coach_a
        r = s.delete(f"{API}/players/ply_nonexistent_xxx")
        assert r.status_code == 404

    def test_delete_nonexistent_match(self, coach_a):
        s, _ = coach_a
        r = s.delete(f"{API}/matches/mat_nonexistent_xxx")
        assert r.status_code == 404

    def test_delete_nonexistent_stat(self, coach_a):
        s, _ = coach_a
        r = s.delete(f"{API}/stats/st_nonexistent_xxx")
        assert r.status_code == 404

    def test_delete_nonexistent_lineup(self, coach_a):
        s, _ = coach_a
        r = s.delete(f"{API}/lineups/ln_nonexistent_xxx")
        assert r.status_code == 404

    def test_delete_nonexistent_training(self, coach_a):
        s, _ = coach_a
        r = s.delete(f"{API}/trainings/tr_nonexistent_xxx")
        assert r.status_code == 404

    def test_delete_nonexistent_announcement(self, coach_a):
        s, _ = coach_a
        r = s.delete(f"{API}/announcements/ann_nonexistent_xxx")
        assert r.status_code == 404

    def test_delete_nonexistent_gallery(self, coach_a):
        s, _ = coach_a
        r = s.delete(f"{API}/gallery/gal_nonexistent_xxx")
        assert r.status_code == 404


# ---------- WebSocket Chat ----------
def _ws_url(path, token):
    return f"{WS}{path}?token={token}"


@pytest.mark.asyncio
async def test_ws_chat_member_can_send_and_broadcast(coach_a, team_a):
    _, tok = coach_a
    url = _ws_url(f"/ws/chat/{team_a['team_id']}", tok)
    # open two connections (same user, both members)
    async with websockets.connect(url, open_timeout=10) as ws1, websockets.connect(url, open_timeout=10) as ws2:
        await ws1.send(json.dumps({"body": f"TEST_WS_HELLO_{UNIQ}"}))
        # Both should receive
        msg2 = json.loads(await asyncio.wait_for(ws2.recv(), timeout=5))
        msg1 = json.loads(await asyncio.wait_for(ws1.recv(), timeout=5))
        assert msg1["type"] == "message"
        assert msg2["type"] == "message"
        assert msg1["data"]["body"] == f"TEST_WS_HELLO_{UNIQ}"
        assert msg2["data"]["body"] == f"TEST_WS_HELLO_{UNIQ}"


@pytest.mark.asyncio
async def test_ws_chat_non_member_rejected(coach_b, team_a):
    _, tok_b = coach_b
    url = _ws_url(f"/ws/chat/{team_a['team_id']}", tok_b)
    with pytest.raises((websockets.exceptions.ConnectionClosed, websockets.exceptions.InvalidStatus, Exception)):
        async with websockets.connect(url, open_timeout=10) as ws:
            # Server should close with 1008; receiving will raise
            await asyncio.wait_for(ws.recv(), timeout=5)


@pytest.mark.asyncio
async def test_ws_chat_no_token_rejected(team_a):
    url = f"{WS}/ws/chat/{team_a['team_id']}"
    with pytest.raises(Exception):
        async with websockets.connect(url, open_timeout=10) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)


# ---------- WebSocket Datavolley ----------
@pytest.mark.asyncio
async def test_ws_datavolley_stat_added_broadcast(coach_a, match_a, player_a):
    s, tok = coach_a
    url = _ws_url(f"/ws/datavolley/{match_a['match_id']}", tok)
    async with websockets.connect(url, open_timeout=10) as ws:
        # Trigger a stat add via HTTP
        await asyncio.sleep(0.3)  # ensure subscription registered
        loop = asyncio.get_event_loop()
        r = await loop.run_in_executor(None, lambda: s.post(f"{API}/stats", json={
            "match_id": match_a["match_id"], "player_id": player_a["player_id"],
            "set_number": 1, "action": "serve", "quality": "ace"
        }))
        assert r.status_code == 200
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert msg["type"] == "stat_added"
        assert "summary" in msg
        assert msg["data"]["match_id"] == match_a["match_id"]


@pytest.mark.asyncio
async def test_ws_datavolley_match_update_broadcast(coach_a, match_a):
    s, tok = coach_a
    url = _ws_url(f"/ws/datavolley/{match_a['match_id']}", tok)
    async with websockets.connect(url, open_timeout=10) as ws:
        await asyncio.sleep(0.3)
        loop = asyncio.get_event_loop()
        r = await loop.run_in_executor(None, lambda: s.patch(f"{API}/matches/{match_a['match_id']}", json={"home_score": 12}))
        assert r.status_code == 200
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        assert msg["type"] == "match_update"
        assert msg["data"]["home_score"] == 12


@pytest.mark.asyncio
async def test_ws_datavolley_non_member_rejected(coach_b, match_a):
    _, tok_b = coach_b
    url = _ws_url(f"/ws/datavolley/{match_a['match_id']}", tok_b)
    with pytest.raises(Exception):
        async with websockets.connect(url, open_timeout=10) as ws:
            await asyncio.wait_for(ws.recv(), timeout=5)
