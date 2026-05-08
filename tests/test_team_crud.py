"""Iteration 4: PATCH/DELETE team — owner authorization + cascade tests."""
import os
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://volley-pro-hub.preview.emergentagent.com").rstrip("/")
ADMIN_EMAIL = "admin@zentia.com"
ADMIN_PASSWORD = "ZentiaAdmin2026!"


def _login(email, password):
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _register(email, password, name="Test", role="head_coach"):
    r = requests.post(f"{BASE_URL}/api/auth/register", json={
        "email": email, "password": password, "name": name, "role": role
    })
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _h(tok):
    return {"Authorization": f"Bearer {tok}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def admin_token():
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def other_user_token():
    email = f"other_{uuid.uuid4().hex[:8]}@zentia.com"
    return _register(email, "Pass1234!", name="Other User", role="head_coach")


# ---------- PATCH /teams/{team_id} ----------
class TestUpdateTeam:
    def test_owner_can_update_team(self, admin_token):
        # Create
        r = requests.post(f"{BASE_URL}/api/teams",
                          json={"name": f"TEST_T_{uuid.uuid4().hex[:6]}", "category": "Senior", "season": "2025-26", "color": "#EA580C"},
                          headers=_h(admin_token))
        assert r.status_code == 200
        tid = r.json()["team_id"]

        # Patch
        new_name = f"TEST_Updated_{uuid.uuid4().hex[:6]}"
        r = requests.patch(f"{BASE_URL}/api/teams/{tid}",
                           json={"name": new_name, "category": "Junior", "season": "2026-27", "color": "#0EA5E9"},
                           headers=_h(admin_token))
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["name"] == new_name
        assert d["category"] == "Junior"
        assert d["season"] == "2026-27"
        assert d["color"] == "#0EA5E9"

        # Verify persisted via GET
        g = requests.get(f"{BASE_URL}/api/teams/{tid}", headers=_h(admin_token))
        assert g.status_code == 200
        assert g.json()["name"] == new_name
        assert g.json()["color"] == "#0EA5E9"

        # cleanup
        requests.delete(f"{BASE_URL}/api/teams/{tid}", headers=_h(admin_token))

    def test_non_member_gets_403(self, admin_token, other_user_token):
        r = requests.post(f"{BASE_URL}/api/teams",
                          json={"name": f"TEST_T_{uuid.uuid4().hex[:6]}"}, headers=_h(admin_token))
        tid = r.json()["team_id"]
        r = requests.patch(f"{BASE_URL}/api/teams/{tid}",
                           json={"name": "Hacked"}, headers=_h(other_user_token))
        # Non-member -> require_team_member returns 403
        assert r.status_code == 403, r.text
        requests.delete(f"{BASE_URL}/api/teams/{tid}", headers=_h(admin_token))

    def test_member_non_owner_gets_403(self, admin_token, other_user_token):
        # Owner creates team
        r = requests.post(f"{BASE_URL}/api/teams",
                          json={"name": f"TEST_T_{uuid.uuid4().hex[:6]}"}, headers=_h(admin_token))
        tid = r.json()["team_id"]
        # Get other user's email
        me = requests.get(f"{BASE_URL}/api/auth/me", headers=_h(other_user_token)).json()
        # invite them
        ri = requests.post(f"{BASE_URL}/api/teams/{tid}/invite",
                           json={"email": me["email"], "role": "assistant_coach"},
                           headers=_h(admin_token))
        assert ri.status_code == 200, ri.text
        # Member but not owner -> 403 on PATCH
        r = requests.patch(f"{BASE_URL}/api/teams/{tid}",
                           json={"name": "Hacked"}, headers=_h(other_user_token))
        assert r.status_code == 403, r.text
        requests.delete(f"{BASE_URL}/api/teams/{tid}", headers=_h(admin_token))

    def test_unknown_team_404(self, admin_token):
        r = requests.patch(f"{BASE_URL}/api/teams/team_doesnotexist",
                           json={"name": "x"}, headers=_h(admin_token))
        assert r.status_code == 404


# ---------- DELETE /teams/{team_id} ----------
class TestDeleteTeam:
    def test_non_owner_member_cannot_delete(self, admin_token, other_user_token):
        r = requests.post(f"{BASE_URL}/api/teams",
                          json={"name": f"TEST_T_{uuid.uuid4().hex[:6]}"}, headers=_h(admin_token))
        tid = r.json()["team_id"]
        me = requests.get(f"{BASE_URL}/api/auth/me", headers=_h(other_user_token)).json()
        requests.post(f"{BASE_URL}/api/teams/{tid}/invite",
                      json={"email": me["email"], "role": "assistant_coach"},
                      headers=_h(admin_token))
        r = requests.delete(f"{BASE_URL}/api/teams/{tid}", headers=_h(other_user_token))
        assert r.status_code == 403, r.text
        requests.delete(f"{BASE_URL}/api/teams/{tid}", headers=_h(admin_token))

    def test_unknown_team_delete_404(self, admin_token):
        r = requests.delete(f"{BASE_URL}/api/teams/team_doesnotexist", headers=_h(admin_token))
        assert r.status_code == 404

    def test_owner_delete_cascades(self, admin_token):
        # Create team + populate child resources
        r = requests.post(f"{BASE_URL}/api/teams",
                          json={"name": f"TEST_Cascade_{uuid.uuid4().hex[:6]}"}, headers=_h(admin_token))
        tid = r.json()["team_id"]
        admin_id = requests.get(f"{BASE_URL}/api/auth/me", headers=_h(admin_token)).json()["user_id"]

        # player
        rp = requests.post(f"{BASE_URL}/api/players",
                           json={"team_id": tid, "name": "TEST_P", "number": 5, "position": "OH"},
                           headers=_h(admin_token))
        assert rp.status_code == 200
        pid = rp.json()["player_id"]
        # match
        rm = requests.post(f"{BASE_URL}/api/matches",
                           json={"team_id": tid, "opponent": "TEST_Op", "date": "2026-04-01"},
                           headers=_h(admin_token))
        mid = rm.json()["match_id"]
        # stat
        rs = requests.post(f"{BASE_URL}/api/stats",
                           json={"match_id": mid, "player_id": pid, "set_number": 1, "action": "attack", "quality": "kill"},
                           headers=_h(admin_token))
        assert rs.status_code == 200
        # lineup
        rl = requests.post(f"{BASE_URL}/api/lineups",
                           json={"team_id": tid, "name": "TEST_L", "positions": {"P1": pid}},
                           headers=_h(admin_token))
        assert rl.status_code == 200
        # training + attendance
        rt = requests.post(f"{BASE_URL}/api/trainings",
                           json={"team_id": tid, "title": "TEST_Tr", "date": "2026-04-02"},
                           headers=_h(admin_token))
        trid = rt.json()["training_id"]
        ra = requests.post(f"{BASE_URL}/api/attendance",
                           json={"training_id": trid, "player_id": pid, "status": "present"},
                           headers=_h(admin_token))
        assert ra.status_code == 200
        # announcement
        ran = requests.post(f"{BASE_URL}/api/announcements",
                            json={"team_id": tid, "title": "TEST_A", "body": "x"},
                            headers=_h(admin_token))
        assert ran.status_code == 200
        # message
        rmm = requests.post(f"{BASE_URL}/api/messages",
                            json={"team_id": tid, "body": "hi"}, headers=_h(admin_token))
        assert rmm.status_code == 200
        # gallery
        rg = requests.post(f"{BASE_URL}/api/gallery",
                           json={"team_id": tid, "url": "https://x.com/a.jpg", "caption": "t", "kind": "image"},
                           headers=_h(admin_token))
        assert rg.status_code == 200

        # verify team_id present in user.team_ids
        u_before = requests.get(f"{BASE_URL}/api/auth/me", headers=_h(admin_token)).json()
        assert tid in u_before.get("team_ids", [])

        # DELETE
        rd = requests.delete(f"{BASE_URL}/api/teams/{tid}", headers=_h(admin_token))
        assert rd.status_code == 200, rd.text
        assert rd.json().get("ok") is True

        # Verify cascade: GET team -> 404
        assert requests.get(f"{BASE_URL}/api/teams/{tid}", headers=_h(admin_token)).status_code == 404
        # players list -> 403 (team gone) — fine; we instead query team list
        teams_after = requests.get(f"{BASE_URL}/api/teams", headers=_h(admin_token)).json()
        assert not any(t["team_id"] == tid for t in teams_after)
        # match gone
        assert requests.get(f"{BASE_URL}/api/matches/{mid}", headers=_h(admin_token)).status_code == 404
        # team_id removed from user
        u_after = requests.get(f"{BASE_URL}/api/auth/me", headers=_h(admin_token)).json()
        assert tid not in u_after.get("team_ids", []), f"team_id not pulled from user: {u_after.get('team_ids')}"


# ---------- Reception stats regression (rece#/+/!) ----------
class TestReceptionStats:
    def test_reception_stats_logged(self, admin_token):
        r = requests.post(f"{BASE_URL}/api/teams",
                          json={"name": f"TEST_Rec_{uuid.uuid4().hex[:6]}"}, headers=_h(admin_token))
        tid = r.json()["team_id"]
        rp = requests.post(f"{BASE_URL}/api/players",
                           json={"team_id": tid, "name": "TEST_R", "number": 11, "position": "L"},
                           headers=_h(admin_token))
        pid = rp.json()["player_id"]
        rm = requests.post(f"{BASE_URL}/api/matches",
                           json={"team_id": tid, "opponent": "TEST_Op", "date": "2026-04-10"},
                           headers=_h(admin_token))
        mid = rm.json()["match_id"]
        # log reception stats: rece# -> kill, rece+ -> perfect, rece! -> medium
        for q in ("kill", "perfect", "medium"):
            rs = requests.post(f"{BASE_URL}/api/stats",
                               json={"match_id": mid, "player_id": pid, "set_number": 1,
                                     "action": "reception", "quality": q},
                               headers=_h(admin_token))
            assert rs.status_code == 200, rs.text

        rsum = requests.get(f"{BASE_URL}/api/stats/summary", params={"match_id": mid},
                            headers=_h(admin_token))
        assert rsum.status_code == 200
        s = rsum.json()[pid]
        # reception+kill should NOT count as kills (only attack+kill counts)
        assert s["kills"] == 0
        assert s["total"] == 3

        requests.delete(f"{BASE_URL}/api/teams/{tid}", headers=_h(admin_token))
