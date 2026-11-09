#!/data/data/com.termux/files/usr/bin/bash
# Registers this Android phone as a Daevis phone node (contacts search + calls only).
# Usage (the Discord DM gives the full line): curl -fsSL <relay>/phone-install.sh | bash -s -- CODE
set -euo pipefail
CODE="${1:-}"
RELAY="https://game.daehoon.games/pc-power"
DIR="$HOME/.daevis/phone"

say() { printf '%s\n' "$*"; }
fail() { say "실패: $*"; say "등록 코드는 사용되지 않았을 수 있어요. 문제를 고친 뒤 Discord에서 '폰 등록'을 다시 보내 새 명령을 받으세요."; exit 1; }

[[ "$CODE" =~ ^[A-Za-z0-9_-]{40,64}$ ]] || fail "등록 코드가 없거나 형식이 틀렸어요. DM의 명령을 한 줄 그대로 붙여넣어 주세요."
[[ -d /data/data/com.termux ]] || fail "Termux 안에서 실행해 주세요."

say "1/5 필요한 패키지 설치 (python, termux-api)…"
pkg install -y python termux-api >/dev/null 2>&1 || { pkg update -y >/dev/null 2>&1 && pkg install -y python termux-api >/dev/null 2>&1; } || fail "패키지 설치에 실패했어요. 'pkg update'를 한 번 실행한 뒤 다시 시도하세요."

say "2/5 Termux:API 앱 확인…"
timeout 20 termux-battery-status >/dev/null 2>&1 || fail "Termux:API 앱이 없거나 응답하지 않아요. Termux와 같은 곳(F-Droid)에서 Termux:API 앱을 설치하세요."

say "3/5 연락처 권한 확인 (허용 창이 뜨면 '허용'을 누르세요)…"
CONTACTS="$(timeout 60 termux-contact-list 2>/dev/null || true)"
case "$CONTACTS" in
  \[*) ;;
  *) fail "연락처를 읽지 못했어요. 설정 > 앱 > Termux:API > 권한에서 연락처와 전화를 허용하세요." ;;
esac

say "4/5 서버에 등록…"
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
curl -fsS --proto =https --max-redirs 0 "$RELAY/client.py" -o "$TMP/device-node.py" || fail "프로그램을 내려받지 못했어요. 인터넷 연결을 확인하세요."
CODE="$CODE" RELAY="$RELAY" TMP="$TMP" DIR="$DIR" python - <<'PY' || exit 1
import hashlib, json, os, re, sys, urllib.request
from pathlib import Path
relay, tmp, folder = os.environ['RELAY'], Path(os.environ['TMP']), Path(os.environ['DIR'])
request = urllib.request.Request(relay + '/enroll', data=json.dumps({'code': os.environ['CODE']}).encode(),
                                 headers={'Content-Type': 'application/json'}, method='POST')
try:
    reply = json.load(urllib.request.urlopen(request, timeout=30))
except Exception:
    sys.exit('실패: 등록 코드가 만료됐거나 이미 사용됐어요. Discord에서 "폰 등록"을 다시 보내 새 명령을 받으세요.')
client = (tmp / 'device-node.py').read_bytes()
if not re.fullmatch(r'phone-[a-f0-9]{16}', reply.get('node', '')) or reply.get('url') != relay:
    sys.exit('실패: 서버 응답이 올바르지 않아요.')
if hashlib.sha256(client).hexdigest() != reply.get('sha256'):
    sys.exit('실패: 내려받은 프로그램이 서버와 달라요. Discord에서 등록을 해제하고 다시 등록하세요.')
folder.mkdir(parents=True, exist_ok=True); os.chmod(folder, 0o700)
(folder / 'device-node.py').write_bytes(client)
token = folder / 'node-token'; token.write_text(reply['token']); os.chmod(token, 0o600)
config = {'url': relay, 'node': reply['node'], 'token_file': str(token), 'actions': ['phone_call'],
          'allow_phone_call': True, 'poll_seconds': 5}
(folder / 'node.json').write_text(json.dumps(config)); os.chmod(folder / 'node.json', 0o600)
print('등록 완료: ' + reply.get('label', '폰'))
PY

say "5/5 백그라운드 실행 설정…"
cat > "$DIR/start.sh" <<'SH'
#!/data/data/com.termux/files/usr/bin/bash
# One copy only; keeps the phone awake enough to receive requests.
DIR="$HOME/.daevis/phone"
if [ -f "$DIR/node.pid" ] && kill -0 "$(cat "$DIR/node.pid")" 2>/dev/null; then exit 0; fi
echo $$ > "$DIR/node.pid"
termux-wake-lock 2>/dev/null || true
cd "$DIR"
while true; do
  python device-node.py --config node.json >> node.log 2>&1 || true
  sleep 30
done
SH
chmod 700 "$DIR/start.sh"
mkdir -p "$HOME/.termux/boot"
printf '#!/data/data/com.termux/files/usr/bin/bash\nnohup "$HOME/.daevis/phone/start.sh" >/dev/null 2>&1 &\n' > "$HOME/.termux/boot/daevis-phone"
chmod 700 "$HOME/.termux/boot/daevis-phone"
grep -q 'daevis/phone/start.sh' "$HOME/.bashrc" 2>/dev/null || \
  printf '\n# Daevis phone node: start when Termux opens\nnohup "$HOME/.daevis/phone/start.sh" >/dev/null 2>&1 &\n' >> "$HOME/.bashrc"
nohup "$DIR/start.sh" >/dev/null 2>&1 &

say ""
say "완료. 이제 Discord에서 '○○에게 전화 걸어줘'라고 보내면 이 폰이 전화를 겁니다."
say "- 첫 통화 때 '전화 걸기' 권한 창이 뜨면 허용하세요."
say "- 설정 > 배터리에서 Termux를 '제한 없음'(최적화 제외)으로 바꾸세요."
say "- 재부팅 뒤 자동 실행을 원하면 F-Droid에서 Termux:Boot를 설치하고 한 번 열어 두세요."
