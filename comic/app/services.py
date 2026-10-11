"""The stack's services as the Server page shows them: groups that can be switched on and off.

The dashboard reaches Docker only through docker-proxy (compose.yaml), which allows listing
containers and starting or stopping exactly the containers in SWITCHABLE, nothing else. A
stopped container stays stopped across reboots (restart: unless-stopped), until it's switched
on again here or `docker compose up -d` starts the whole stack.
"""

from dataclasses import dataclass, field

import httpx

from app.config import settings

SERVICE_LABEL = "com.docker.compose.service"
PROJECT_LABEL = "com.docker.compose.project"
# The Discord bot ends a recording session on SIGTERM; give it time (compose.yaml matches).
STOP_SECONDS = {"discord-bot": 20}


@dataclass(frozen=True)
class Group:
    id: str
    name: str
    description: str
    # Compose services, in start order (stopped in reverse).
    services: tuple[str, ...]
    switchable: bool = True
    # Groups this one needs running: switching it on starts them, and switching them off
    # stops this one too.
    needs: tuple[str, ...] = ()
    profile: str | None = None


GROUPS: tuple[Group, ...] = (
    Group(
        "arbiter",
        "Rules arbiter",
        "Rulings in Foundry and the public address (Cloudflare tunnel).",
        ("backend", "cloudflared"),
    ),
    Group(
        "transcription",
        "Transcription",
        "Discord bot and GPU speech-to-text. Switch off between sessions to free the GPU.",
        ("stt-worker", "discord-bot"),
        needs=("arbiter",),
        profile="transcription",
    ),
    Group(
        "comics",
        "Comics",
        "This dashboard, the comic worker and their queue. Always on while you use it.",
        ("redis", "docker-proxy", "comic-api", "comic-worker"),
        switchable=False,
        profile="comic",
    ),
)
SWITCHABLE = {s for g in GROUPS if g.switchable for s in g.services}


class ServiceError(RuntimeError):
    """Docker (through the proxy) refused or failed; the message is shown on the page."""


@dataclass
class Container:
    service: str
    name: str
    state: str  # running, exited, restarting, created, paused, dead
    status: str  # Docker's human text, e.g. "Up 3 hours"

    @property
    def running(self) -> bool:
        return self.state in ("running", "restarting")


@dataclass
class GroupState:
    group: Group
    containers: list[Container] = field(default_factory=list)

    @property
    def installed(self) -> bool:
        """False when the group's compose profile isn't enabled: no containers exist."""
        return bool(self.containers)

    @property
    def on(self) -> bool:
        return self.installed and all(c.running for c in self.containers)

    @property
    def summary(self) -> str:
        """running, stopped, partial or missing: the status chip."""
        if not self.installed:
            return "missing"
        running = sum(c.running for c in self.containers)
        if running == len(self.containers):
            return "running"
        return "stopped" if running == 0 else "partial"


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=settings.docker_proxy_url, timeout=40.0)


async def containers() -> list[Container]:
    """This compose project's containers (the proxy sees every container on the host)."""
    try:
        async with _client() as client:
            r = await client.get("/containers/json", params={"all": "true"})
            r.raise_for_status()
            listed = r.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ServiceError(f"Can't reach Docker through docker-proxy: {exc}") from exc
    found = []
    for c in listed:
        labels = c.get("Labels") or {}
        if labels.get(PROJECT_LABEL) != settings.compose_project:
            continue
        found.append(
            Container(
                service=labels.get(SERVICE_LABEL, ""),
                name=(c.get("Names") or ["?"])[0].lstrip("/"),
                state=c.get("State", ""),
                status=c.get("Status", ""),
            )
        )
    return found


def group_states(found: list[Container]) -> list[GroupState]:
    by_service = {c.service: c for c in found}
    return [GroupState(g, [by_service[s] for s in g.services if s in by_service]) for g in GROUPS]


def group(group_id: str) -> Group:
    for g in GROUPS:
        if g.id == group_id:
            return g
    raise KeyError(group_id)


def plan(group_id: str, on: bool) -> list[tuple[str, str]]:
    """(action, service) steps to switch a group: on starts what it needs first; off stops
    the groups that need it first. Services are stopped in reverse start order."""
    target = group(group_id)
    if not target.switchable:
        raise ServiceError(f"{target.name} can't be switched off from the dashboard.")
    if on:
        groups = [group(n) for n in target.needs] + [target]
        return [("start", s) for g in groups for s in g.services]
    dependants = [g for g in GROUPS if group_id in g.needs]
    return [("stop", s) for g in [*dependants, target] for s in reversed(g.services)]


async def switch(group_id: str, on: bool) -> list[str]:
    """Run the plan on the containers that exist; returns what was done, for the toast."""
    steps = plan(group_id, on)
    by_service = {c.service: c for c in await containers()}
    done = []
    async with _client() as client:
        for action, service in steps:
            c = by_service.get(service)
            if c is None or service not in SWITCHABLE:
                continue  # not installed (profile off): nothing to switch
            if c.running == (action == "start"):
                continue
            params = (
                {"t": STOP_SECONDS[service]} if action == "stop" and service in STOP_SECONDS else {}
            )
            try:
                r = await client.post(f"/containers/{c.name}/{action}", params=params)
            except httpx.HTTPError as exc:
                raise ServiceError(f"{action} {service} failed: {exc}") from exc
            if r.status_code not in (204, 304):  # 304: already in that state
                raise ServiceError(f"{action} {service}: Docker answered {r.status_code}")
            done.append(f"{action} {service}")
    return done
