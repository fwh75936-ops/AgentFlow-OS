# AgentFlow OS — Shared Linux Workspace (ONE container shared by ALL agents)
# Ubuntu + XFCE desktop + TigerVNC + noVNC. Agents do NOT get their own
# containers; they share this environment, its /workspace volume and desktop.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:1 \
    VNC_PORT=5901 \
    NOVNC_PORT=6080

RUN apt-get update && apt-get install -y --no-install-recommends \
      xfce4-session xfce4-panel xfwm4 xfdesktop4 xfce4-terminal thunar \
      tigervnc-standalone-server tigervnc-tools websockify novnc \
      dbus-x11 x11-xserver-utils \
      python3 python3-pip python3-pytest bash coreutils procps sudo \
    && rm -rf /var/lib/apt/lists/*

# Non-root runtime user. sudo is NOT granted: agents run unprivileged and
# high-risk commands go through AgentFlow's human-approval gate instead.
RUN useradd -m -s /bin/bash agentflow && mkdir -p /workspace && chown agentflow:agentflow /workspace

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

USER agentflow
WORKDIR /workspace
EXPOSE 6080
# VNC itself (5901) is localhost-only inside the container (tigervnc -localhost
# yes) and is never published; only noVNC is published, bound to 127.0.0.1 by compose.
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
