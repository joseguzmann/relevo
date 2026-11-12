#!/usr/bin/env bash
# Live apply for a captcha ATS: Relevo fills a HEADED browser on the Gateway; Jose solves the captcha and submits
# by hand over noVNC. BambooHR only for now. Reached over an SSH tunnel on the desktop (NOT public: the VNC has no
# auth, so it must not be exposed on a public URL until the view is gated behind Relevo's passcode).
#   scripts/captcha-browser/live.sh <bamboohr_url> <answers.json> <cv.pdf>     (answers = Relevo panel fields)
#   Mac: ssh -L 6084:127.0.0.1:6084 gateway ; open http://127.0.0.1:6084/vnc_lite.html?resize=scale
# Stops when the page says submitted (result.json), or Ctrl-C / docker rm relevo-live.
set -euo pipefail
URL=${1:?form url}; ANS=${2:?answers.json}; CV=${3:?cv.pdf}
cd "$(dirname "$0")"
D=/srv/apps/relevo/live
ssh gateway "mkdir -p $D"
scp -q live.py "$ANS" "$CV" gateway:$D/ >/dev/null
ssh gateway "mv -f $D/$(basename "$ANS") $D/answers.json; mv -f $D/$(basename "$CV") $D/cv.pdf
  docker rm -f relevo-live >/dev/null 2>&1 || true
  docker run -d --name relevo-live --network ipfort-net -p 127.0.0.1:6084:6083 \
    -v /srv/apps/ip-fort/android/resolv.conf:/etc/resolv.conf:ro -v $D:/live \
    --entrypoint sh vigia-linkedin-mcp -c '
      Xvfb :99 -screen 0 1440x900x24 -nolisten tcp >/tmp/xvfb.log 2>&1 &
      sleep 2
      x11vnc -display :99 -forever -shared -nopw -quiet -bg >/tmp/x11vnc.log 2>&1
      websockify --web=/usr/share/novnc 6083 localhost:5900 >/tmp/ws.log 2>&1 &
      DISPLAY=:99 /app/.venv/bin/python /live/live.py \"$URL\" /live/answers.json /live/cv.pdf /live/result.json
    ' >/dev/null"
echo "tunnel: ssh -L 6084:127.0.0.1:6084 gateway"
echo "open:   http://127.0.0.1:6084/vnc_lite.html?resize=scale"
echo "watch:  ssh gateway docker logs -f relevo-live"
