#!/bin/sh
# Relevo. A headless X display is always up so the captcha "live apply" browser can be headed and shown over VNC
# (served only through the app, behind the passcode). x11vnc + websockify sit idle on an empty screen until a
# live apply runs; the only way to reach them is the app's authenticated /live proxy.
set -e
export DISPLAY=:99
rm -f /tmp/.X11-unix/X99 /tmp/.X99-lock 2>/dev/null || true
Xvfb :99 -screen 0 430x920x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
sleep 2
x11vnc -display :99 -forever -shared -nopw -quiet -bg >/tmp/x11vnc.log 2>&1 || true
websockify --web=/usr/share/novnc 6083 localhost:5900 >/tmp/ws.log 2>&1 &
exec python3 -m uvicorn relevo.api:app --host 0.0.0.0 --port 8000
