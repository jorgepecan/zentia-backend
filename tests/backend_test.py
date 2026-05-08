"""Zentia VolleyPro - Backend API tests (pytest)"""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://volley-pro-hub.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@zentia.com"
ADMIN_PASSWORD = "ZentiaAdmin2026!"


@pytest.fixture(scope="session")
def admin_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"Admin login failed: {r.status_code} {r.text}"
    data = r.json()
    assert "token" in data
    s.headers.update({"Authorization": f"Bearer {data['token']}"})
    return s


@pytest.fixture(scope="session")
def team(admin_session):
    r = admin_session.post(f"{BASE_URL}/api/teams", json={"name": f"TEST_Team_{uuid.uuid4().hex[:6]}", "category": "Senior", "season": "2025-26"})
    assert r.status_code == 200
    return r.json()


# ---------- Auth ----------
class TestAuth:
    def test_health(self):
        r = requests.get(f"{BASE_URL}/api/")
        assert r.status_code == 200
        assert r.json().get("status") == "ok"

    def test_register_new_user(self):
        email = f"test_{uuid.uuid4().hex[:8]}@zentia.com"
        r = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": email, "password": "Test1234!", "name": "Test User", "role": "player"
        })
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["user"]["email"] == email
        assert d["user"]["role"] == "player"
        assert "token" in d

    def test_register_duplicate_fails(self):
        r = requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": ADMIN_EMAIL, "password": "x", "name": "x", "role": "head_coach"
        })
        assert r.status_code == 400

    def test_login_admin(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        assert r.status_code == 200
        assert r.json()["user"]["email"] == ADMIN_EMAIL

    def test_login_invalid(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong"})
        assert r.status_code == 401

    def test_me_unauthenticated(self):
        r = requests.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 401

    def test_me_authenticated(self, admin_session):
        r = admin_session.get(f"{BASE_URL}/api/auth/me")
        assert r.status_code == 200
        assert r.json()["email"] == ADMIN_EMAIL

    def test_logout(self, admin_session):
        r = admin_session.post(f"{BASE_URL}/api/auth/logout")
        assert r.status_code == 200


# ---------- Teams ----------
class TestTeams:
    def test_create_and_list_team(self, admin_session, team):
        assert "team_id" in team
        r = admin_session.get(f"{BASE_URL}/api/teams")
        assert r.status_code == 200
        assert any(t["team_id"] == team["team_id"] for t in r.json())

    def test_get_team(self, admin_session, team):
        r = admin_session.get(f"{BASE_URL}/api/teams/{team['team_id']}")
        assert r.status_code == 200
        assert r.json()["name"] == team["name"]

    def test_invite_existing_user(self, admin_session, team):
        # register a user then invite
        email = f"invitee_{uuid.uuid4().hex[:6]}@zentia.com"
        requests.post(f"{BASE_URL}/api/auth/register", json={
            "email": email, "password": "Test1234!", "name": "Invitee", "role": "player"
        })
        r = admin_session.post(f"{BASE_URL}/api/teams/{team['team_id']}/invite", json={"email": email, "role": "player"})
        assert r.status_code == 200

    def test_invite_nonexistent_user_404(self, admin_session, team):
        r = admin_session.post(f"{BASE_URL}/api/teams/{team['team_id']}/invite", json={"email": "ghost@nope.com", "role": "player"})
        assert r.status_code == 404


# ---------- Players ----------
class TestPlayers:
    def test_player_crud(self, admin_session, team):
        r = admin_session.post(f"{BASE_URL}/api/players", json={
            "team_id": team["team_id"], "name": "TEST_Player", "number": 7, "position": "OH", "height_cm": 188
        })
        assert r.status_code == 200
        pid = r.json()["player_id"]
        # list
        r = admin_session.get(f"{BASE_URL}/api/players", params={"team_id": team["team_id"]})
        assert r.status_code == 200
        assert any(p["player_id"] == pid for p in r.json())
        # delete
        r = admin_session.delete(f"{BASE_URL}/api/players/{pid}")
        assert r.status_code == 200


# ---------- Matches ----------
class TestMatches:
    def test_match_crud_and_update(self, admin_session, team):
        r = admin_session.post(f"{BASE_URL}/api/matches", json={
            "team_id": team["team_id"], "opponent": "TEST_Rivals", "date": "2026-02-15", "home": True
        })
        assert r.status_code == 200, r.text
        mid = r.json()["match_id"]
        assert r.json()["status"] == "scheduled"

        r = admin_session.get(f"{BASE_URL}/api/matches/{mid}")
        assert r.status_code == 200

        r = admin_session.patch(f"{BASE_URL}/api/matches/{mid}", json={
            "home_score": 3, "away_score": 1, "status": "finished",
            "sets": [{"home": 25, "away": 22}, {"home": 25, "away": 23}]
        })
        assert r.status_code == 200
        assert r.json()["home_score"] == 3
        assert r.json()["status"] == "finished"

        r = admin_session.get(f"{BASE_URL}/api/matches", params={"team_id": team["team_id"]})
        assert r.status_code == 200
        assert any(m["match_id"] == mid for m in r.json())

        r = admin_session.delete(f"{BASE_URL}/api/matches/{mid}")
        assert r.status_code == 200


# ---------- Stats ----------
class TestStats:
    def test_stats_and_summary(self, admin_session, team):
        # create match + player
        rm = admin_session.post(f"{BASE_URL}/api/matches", json={"team_id": team["team_id"], "opponent": "TEST_StatRivals", "date": "2026-03-01"})
        mid = rm.json()["match_id"]
        rp = admin_session.post(f"{BASE_URL}/api/players", json={"team_id": team["team_id"], "name": "TEST_Hitter", "number": 99, "position": "OPP"})
        pid = rp.json()["player_id"]

        # log a few datavolley actions
        for action, quality in [("attack", "kill"), ("attack", "kill"), ("attack", "error"), ("serve", "ace"), ("block", "kill"), ("dig", "perfect")]:
            r = admin_session.post(f"{BASE_URL}/api/stats", json={
                "match_id": mid, "player_id": pid, "set_number": 1, "action": action, "quality": quality, "code": "01a#hp+"
            })
            assert r.status_code == 200

        r = admin_session.get(f"{BASE_URL}/api/stats", params={"match_id": mid})
        assert r.status_code == 200
        assert len(r.json()) == 6

        r = admin_session.get(f"{BASE_URL}/api/stats/summary", params={"match_id": mid})
        assert r.status_code == 200, r.text
        s = r.json()[pid]
        assert s["kills"] == 2
        assert s["aces"] == 1
        assert s["blocks"] == 1
        assert s["digs"] == 1
        assert s["errors"] == 1
        assert s["total"] == 6


# ---------- Lineups ----------
class TestLineups:
    def test_lineup_crud(self, admin_session, team):
        r = admin_session.post(f"{BASE_URL}/api/lineups", json={
            "team_id": team["team_id"], "name": "TEST_Lineup",
            "positions": {"P1": "p1", "P2": "p2", "P3": "p3", "P4": "p4", "P5": "p5", "P6": "p6"}
        })
        assert r.status_code == 200
        lid = r.json()["lineup_id"]
        r = admin_session.get(f"{BASE_URL}/api/lineups", params={"team_id": team["team_id"]})
        assert r.status_code == 200
        assert any(l["lineup_id"] == lid for l in r.json())
        assert admin_session.delete(f"{BASE_URL}/api/lineups/{lid}").status_code == 200


# ---------- Trainings & Attendance ----------
class TestTrainingsAttendance:
    def test_training_attendance(self, admin_session, team):
        r = admin_session.post(f"{BASE_URL}/api/trainings", json={
            "team_id": team["team_id"], "title": "TEST_Training", "date": "2026-02-20", "location": "Gym"
        })
        assert r.status_code == 200
        tid = r.json()["training_id"]

        r = admin_session.post(f"{BASE_URL}/api/attendance", json={"training_id": tid, "player_id": "ply_x", "status": "present"})
        assert r.status_code == 200
        # upsert: change status
        r = admin_session.post(f"{BASE_URL}/api/attendance", json={"training_id": tid, "player_id": "ply_x", "status": "late"})
        assert r.status_code == 200

        r = admin_session.get(f"{BASE_URL}/api/attendance", params={"training_id": tid})
        assert r.status_code == 200
        recs = r.json()
        assert len(recs) == 1
        assert recs[0]["status"] == "late"

        assert admin_session.delete(f"{BASE_URL}/api/trainings/{tid}").status_code == 200


# ---------- Communications ----------
class TestComms:
    def test_announcement_message_gallery(self, admin_session, team):
        r = admin_session.post(f"{BASE_URL}/api/announcements", json={"team_id": team["team_id"], "title": "TEST_Anuncio", "body": "Hola equipo", "pinned": True})
        assert r.status_code == 200
        aid = r.json()["announcement_id"]
        r = admin_session.get(f"{BASE_URL}/api/announcements", params={"team_id": team["team_id"]})
        assert r.status_code == 200 and any(a["announcement_id"] == aid for a in r.json())
        admin_session.delete(f"{BASE_URL}/api/announcements/{aid}")

        r = admin_session.post(f"{BASE_URL}/api/messages", json={"team_id": team["team_id"], "body": "TEST_msg"})
        assert r.status_code == 200
        r = admin_session.get(f"{BASE_URL}/api/messages", params={"team_id": team["team_id"]})
        assert r.status_code == 200 and len(r.json()) >= 1

        r = admin_session.post(f"{BASE_URL}/api/gallery", json={"team_id": team["team_id"], "url": "https://x.com/a.jpg", "caption": "TEST", "kind": "image"})
        assert r.status_code == 200
        gid = r.json()["gallery_id"]
        r = admin_session.get(f"{BASE_URL}/api/gallery", params={"team_id": team["team_id"]})
        assert r.status_code == 200 and any(g["gallery_id"] == gid for g in r.json())
        admin_session.delete(f"{BASE_URL}/api/gallery/{gid}")
