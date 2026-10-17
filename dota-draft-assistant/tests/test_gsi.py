import json
import urllib.request

from draft_assistant.gsi import GSIServer, config_text, parse_draft

PAYLOAD = {
    "player": {"team_name": "dire"},
    "draft": {
        "activeteam": 2,
        "team2": {"home_team": False, "pick0_id": 1, "pick1_id": 0, "ban0_id": 2, "ban1_id": 0},
        "team3": {"home_team": True, "pick0_id": 5, "pick1_id": 3, "ban0_id": 4},
    },
}


def test_parse_uses_player_side():
    state = parse_draft(PAYLOAD)
    assert state.allies == [5, 3]
    assert state.enemies == [1]
    assert sorted(state.bans) == [2, 4]


def test_parse_spectating_uses_home_team_and_override():
    spectate = {"draft": PAYLOAD["draft"]}
    assert parse_draft(spectate).allies == [5, 3]
    assert parse_draft(spectate, my_side="radiant").allies == [1]
    assert parse_draft({"map": {}}) is None


def test_config_has_draft_and_token():
    text = config_text(3001, "abc")
    assert '"draft"     "1"' in text and "127.0.0.1:3001" in text and '"abc"' in text


def test_server_roundtrip_and_auth():
    received = []
    server = GSIServer(received.append, port=0, token="secret")
    port = server.httpd.server_address[1]
    server.start_background()
    try:
        def post(body):
            req = urllib.request.Request(f"http://127.0.0.1:{port}/", json.dumps(body).encode(), method="POST")
            urllib.request.urlopen(req, timeout=5).read()

        post({**PAYLOAD, "auth": {"token": "wrong"}})
        post({**PAYLOAD, "auth": {"token": "secret"}})
        post({**PAYLOAD, "auth": {"token": "secret"}})  # mesmo draft: não repete
    finally:
        server.shutdown()
    assert len(received) == 1 and received[0].enemies == [1]
