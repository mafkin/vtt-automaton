import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import services
from app.api import app


class FakeDocker:
    """The docker-proxy as the dashboard sees it: a container list, start and stop."""

    def __init__(self, running: dict[str, bool]):
        self.running = running  # compose service -> running?
        self.calls: list[str] = []
        self.fail: int | None = None

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            assert dict(request.url.params) == {"all": "true"}  # stopped ones too
            listed = [
                {
                    "Names": [f"/vtt-automaton-{s}-1"],
                    "State": "running" if on else "exited",
                    "Status": "Up 2 hours" if on else "Exited (0) 5 minutes ago",
                    "Labels": {
                        "com.docker.compose.project": "vtt-automaton",
                        "com.docker.compose.service": s,
                    },
                }
                for s, on in self.running.items()
            ]
            listed.append(
                {
                    "Names": ["/other-app-1"],
                    "State": "running",
                    "Labels": {"com.docker.compose.project": "other"},
                }
            )
            return httpx.Response(200, json=listed)
        if self.fail:
            return httpx.Response(self.fail, json={"message": "nope"})
        _, _, name, action = request.url.path.split("/")
        service = name.removeprefix("vtt-automaton-").removesuffix("-1")
        self.calls.append(
            f"{action} {service}"
            + (f" t={request.url.params['t']}" if "t" in request.url.params else "")
        )
        self.running[service] = action == "start"
        return httpx.Response(204)


ALL = [
    "backend",
    "cloudflared",
    "stt-worker",
    "discord-bot",
    "redis",
    "docker-proxy",
    "comic-api",
    "comic-worker",
]


@pytest.fixture
def docker(monkeypatch):
    fake = FakeDocker({s: True for s in ALL})
    monkeypatch.setattr(
        services,
        "_client",
        lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(fake.handler), base_url="http://proxy"
        ),
    )
    return fake


@pytest.fixture
def client(sessions_db):
    app.state.queue = object()
    with TestClient(app) as c:
        yield c
    app.state.queue = None


def toast(r) -> dict:
    return json.loads(r.headers["HX-Trigger"])["toast"]


def test_groups_show_their_state_and_only_this_stack(client, docker):
    docker.running.update({"stt-worker": False, "discord-bot": False, "cloudflared": False})
    html = client.get("/api/v1/services").text
    assert "Rules arbiter" in html and "Partly running" in html  # backend up, tunnel down
    assert "Transcription" in html and 'aria-checked="false"' in html
    assert "Always on" in html  # comics can't be switched off from the dashboard
    assert "Up 2 hours" in html and "other-app" not in html


def test_a_group_without_containers_is_not_set_up(client, docker):
    for s in ("stt-worker", "discord-bot"):
        del docker.running[s]
    html = client.get("/api/v1/services").text
    assert "Not set up" in html and '<code class="text-slate-400">transcription</code>' in html


def test_switching_transcription_off_and_on(client, docker):
    r = client.post("/api/v1/services/transcription/off")
    # The bot first (it ends a live session on SIGTERM and gets time for it), then the worker.
    assert docker.calls == ["stop discord-bot t=20", "stop stt-worker"]
    assert toast(r) == {"message": "Transcription switched off.", "level": "success"}
    assert 'aria-checked="false"' in r.text

    docker.calls.clear()
    docker.running["backend"] = False  # transcription needs the arbiter: it comes on first
    client.post("/api/v1/services/transcription/on")
    assert docker.calls == ["start backend", "start stt-worker", "start discord-bot"]


def test_switching_the_arbiter_off_stops_transcription_first(client, docker):
    html = client.get("/api/v1/services").text
    assert "This also switches off Transcription" in html  # the confirmation
    client.post("/api/v1/services/arbiter/off")
    assert docker.calls == [
        "stop discord-bot t=20",
        "stop stt-worker",
        "stop cloudflared",
        "stop backend",
    ]


def test_a_live_session_is_warned_about(client, docker):
    # The sessions fixture has a live session ("live1").
    html = client.get("/api/v1/services").text
    assert "A session is being recorded" in html


def test_comics_cannot_be_switched_off(client, docker):
    r = client.post("/api/v1/services/comics/off")
    assert toast(r)["level"] == "error" and docker.calls == []


def test_unknown_group_or_action_is_404(client, docker):
    assert client.post("/api/v1/services/nope/on").status_code == 404
    assert client.post("/api/v1/services/arbiter/restart").status_code == 404


def test_a_refused_switch_is_shown(client, docker):
    docker.fail = 403
    r = client.post("/api/v1/services/transcription/off")
    assert toast(r)["level"] == "error" and "403" in toast(r)["message"]


def test_an_unreachable_proxy_explains_the_docker_group(client, monkeypatch):
    def down(request):
        raise httpx.ConnectError("Name or service not known")

    monkeypatch.setattr(
        services,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(down), base_url="http://proxy"),
    )
    html = client.get("/api/v1/services").text
    assert "reach Docker through docker-proxy" in html and "DOCKER_GID" in html


def test_the_plan_never_touches_the_dashboard_itself():
    for g in services.GROUPS:
        if g.switchable:
            for on in (True, False):
                assert all(s in services.SWITCHABLE for _, s in services.plan(g.id, on))
    assert not {"comic-api", "docker-proxy", "redis"} & services.SWITCHABLE


def test_switchable_services_match_the_proxy_rule():
    # compose.yaml lets the proxy start/stop exactly these; a mismatch would 403 or widen it.
    import re
    from pathlib import Path

    compose = Path(__file__).parents[2] / "compose.yaml"
    rule = re.search(r"SP_ALLOW_POST=\^/containers/vtt-automaton-\(([^)]*)\)", compose.read_text())
    assert rule and set(rule.group(1).split("|")) == services.SWITCHABLE
