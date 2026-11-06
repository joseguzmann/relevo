#!/usr/bin/env bash
# Run on the Gateway: asks for the passcode without echoing it, stores only a salted hash, restarts Relevo.
set -euo pipefail
read -rsp "Passcode: " code; echo
hash=$(CODE="$code" python3 -c 'import hashlib,os,secrets;s=secrets.token_hex(16);print(s+"$"+hashlib.pbkdf2_hmac("sha256",os.environ["CODE"].encode(),s.encode(),300000).hex())')
sed -i '/^RELEVO_PASSCODE_HASH=/d' /srv/apps/relevo/.env
printf "RELEVO_PASSCODE_HASH='%s'\n" "$hash" >> /srv/apps/relevo/.env
cd /srv/apps/relevo/src/deploy && docker compose up -d --force-recreate >/dev/null
echo "Relevo restarted"
