#!/usr/bin/env bash
# Prototype: a headed patchright browser on the Gateway for a captcha form, driven by Jose over noVNC.
# Measures only whether solving the reCAPTCHA yields a valid token (no application is submitted).
#   scripts/captcha-browser/run.sh "<bamboohr application url>"
# On the Mac: open the printed https tunnel, then the noVNC link. Ctrl-C here stops it.
set -euo pipefail
URL=${1:?application url}
cd "$(dirname "$0")"
ssh gateway "mkdir -p /srv/apps/relevo/captcha && cp /dev/stdin /srv/apps/relevo/captcha/probe.py" < probe.py
ssh gateway "docker rm -f captcha-probe >/dev/null 2>&1 || true
  docker run -d --name captcha-probe --network ipfort-net \
    -v /srv/apps/ip-fort/android/resolv.conf:/etc/resolv.conf:ro \
    -v /srv/apps/relevo/captcha/probe.py:/probe.py:ro -p 127.0.0.1:6083:6083 \
    --entrypoint sh vigia-linkedin-mcp -c '
      Xvfb :99 -screen 0 1440x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
      sleep 2
      x11vnc -display :99 -forever -shared -nopw -quiet -bg >/tmp/x11vnc.log 2>&1
      websockify --web=/usr/share/novnc 6083 localhost:5900 >/tmp/ws.log 2>&1 &
      DISPLAY=:99 /app/.venv/bin/python /probe.py \"$URL\"
    ' >/dev/null
  echo exit-IP: \$(docker exec captcha-probe python3 -c 'import urllib.request,hashlib;print(hashlib.sha256(urllib.request.urlopen(\"https://api.ipify.org\",timeout=10).read()).hexdigest()[:6])' 2>/dev/null || echo '?')"
echo "tunnel (leave open on the Mac):  ssh -L 6083:127.0.0.1:6083 gateway"
echo "then open:                       http://127.0.0.1:6083/vnc_lite.html?resize=scale"
echo "watch the token:                 ssh gateway docker logs -f captcha-probe"
