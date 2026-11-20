# Docker

## linux-workspace/ — Shared Linux Workspace (round 2)

The production shape of the Shared Linux Workspace: **one** Ubuntu container
(XFCE + TigerVNC + noVNC) shared by every agent — see
`linux-workspace/Dockerfile`, `entrypoint.sh`, `docker-compose.yml`.

- Non-root user `agentflow`; `cap_drop: ALL`; `no-new-privileges`
- Limits: 2 CPUs, 2048 MB RAM, 256 pids (compose) + per-command rlimits in the backend
- Only mount is the `shared_workspace` volume at `/workspace` — no host
  sensitive directories, **no Docker socket**
- VNC (5901) is localhost-only inside the container and never published;
  noVNC (6080) is published on host `127.0.0.1` only

**Verification status (honest):** the P0/round-2 development VM has **no Docker
installed**, so this image has NOT been built or run here. It is verified on the
first Docker-capable host with:

```bash
docker compose -f docker/linux-workspace/docker-compose.yml up -d --build
curl http://127.0.0.1:6080/vnc.html   # noVNC login page
```

On hosts without Docker, the backend runs the same shared workspace in
`local-process` mode against a shared directory (logical `/workspace`), and
`/api/workspace/status` reports which mode is actually active.

## Sandbox note

Per-task Docker sandboxing (isolating a single task's execution) remains P1
and is separate from the shared workspace above, which is intentionally
*shared*, not isolated per agent.
