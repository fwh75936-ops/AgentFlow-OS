#!/bin/bash
# Starts the single shared XFCE desktop (TigerVNC :1, localhost only) and the
# noVNC websocket proxy, then keeps the container alive. The Agent Core backend
# manages the desktop control lease; this script only provides the desktop.
# Same entrypoint for every supported distribution image.
set -e
mkdir -p "$HOME/.vnc" /workspace

if [ ! -f "$HOME/.vnc/passwd" ]; then
  # Local-only VNC password: generated once, stored in the container's home,
  # never printed to logs and never exposed through the AgentFlow API.
  tr -dc 'A-Za-z0-9' </dev/urandom | head -c 16 | vncpasswd -f > "$HOME/.vnc/passwd"
  chmod 600 "$HOME/.vnc/passwd"
fi
cat > "$HOME/.vnc/xstartup" <<'EOF'
#!/bin/sh
unset SESSION_MANAGER
unset DBUS_SESSION_BUS_ADDRESS
exec startxfce4
EOF
chmod +x "$HOME/.vnc/xstartup"

tigervncserver :1 -geometry 1280x800 -depth 24 -localhost yes
websockify --web=/usr/share/novnc "127.0.0.1:${NOVNC_PORT:-6080}" "localhost:${VNC_PORT:-5901}" &
echo "Shared desktop up: XFCE on :1 (VNC localhost:5901), noVNC on 127.0.0.1:${NOVNC_PORT:-6080}"
tail -f /dev/null
