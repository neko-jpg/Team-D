# Team-D demo runbook

## LiveKit Cloud最小接続（OpenSpec 3.1）

### 完了条件

同じ`LIVEKIT_ROOM`へ次の2 participantを同時接続し、identityが互いに異なることを3か所で照合する。

- LiveKit CLIのparticipant一覧
- browser画面／DevTools Consoleの`[browser]`ログ
- Python Agent terminalの`team-d-livekit-smoke`ログ

`LIVEKIT_API_SECRET`はbrowserへ渡さない。browserへ渡すのはProject URL、Room名、2つのidentity、10分の短命tokenだけとする。

### 1. LiveKit Cloud projectとCLIを用意する

1. LiveKit CloudでTeam-D用projectを1つ作成する。
2. macOSでは`brew install livekit-cli`で`lk`を導入する。
3. リポジトリルートで`lk cloud auth`を実行し、browserでTeam-D用projectを選択する。
4. `lk project list`で対象projectがdefaultになっていることを確認する。`--json`はAPI secretも表示するため使用しない。
5. `lk app env -w`でprojectの`LIVEKIT_URL`、`LIVEKIT_API_KEY`、`LIVEKIT_API_SECRET`をgitignoredの`.env.local`へ書き出す。

公式手順: [LiveKit CLI](https://docs.livekit.io/reference/developer-tools/livekit-cli/)、[project commands](https://docs.livekit.io/reference/developer-tools/livekit-cli/projects/)

### 2. Roomと一意なidentityを設定する

`.env.example`から次の値を`.env.local`へ追記する。`VITE_LIVEKIT_URL`には`LIVEKIT_URL`と同じURLを設定する。browserとAgentのidentityは必ず異なる値にする。

```dotenv
LIVEKIT_ROOM=team-d-livekit-smoke
LIVEKIT_BROWSER_IDENTITY=browser-tomo-smoke
LIVEKIT_AGENT_IDENTITY=agent-guidance-smoke
LIVEKIT_TOKEN_TTL_MINUTES=10
VITE_LIVEKIT_URL=wss://your-project.livekit.cloud
VITE_LIVEKIT_ROOM=team-d-livekit-smoke
VITE_LIVEKIT_BROWSER_IDENTITY=browser-tomo-smoke
VITE_LIVEKIT_AGENT_IDENTITY=agent-guidance-smoke
VITE_LIVEKIT_BROWSER_TOKEN=
```

環境変数を現在のterminalへ読み込み、identityの重複を拒否してからRoomを作る。

```bash
set -a
source .env.local
set +a
test "$LIVEKIT_BROWSER_IDENTITY" != "$LIVEKIT_AGENT_IDENTITY"
lk room create --empty-timeout 600 "$LIVEKIT_ROOM"
lk room list
```

Roomは最初のparticipant接続時にも自動作成されるため、同名Roomが既に存在する場合はcreateを省略する。公式手順: [Room management](https://docs.livekit.io/intro/basics/rooms-participants-tracks/rooms/)

### 3. 最小接続用依存を準備する

```bash
npm install
python3.11 -m venv .venv-livekit
.venv-livekit/bin/python -m pip install --upgrade pip
.venv-livekit/bin/python -m pip install -r requirements-livekit-smoke.txt
```

### 4. Python Agent participantを接続する（terminal A）

```bash
set -a
source .env.local
set +a
.venv-livekit/bin/python scripts/livekit/agent_smoke.py connect \
  --room "$LIVEKIT_ROOM" \
  --participant-identity "$LIVEKIT_AGENT_IDENTITY"
```

期待ログ:

```text
agent_connected room=team-d-livekit-smoke identity=agent-guidance-smoke sid=...
identity_check_waiting room=team-d-livekit-smoke expected_browser_identity=browser-tomo-smoke
```

### 5. browser participantを接続する（terminal B）

短命tokenをshellだけへ設定してViteを起動する。tokenとAPI secretをログへ出さない。

```bash
set -a
source .env.local
set +a
export VITE_LIVEKIT_BROWSER_TOKEN="$(
  .venv-livekit/bin/python scripts/livekit/create_browser_token.py
)"
npm run dev:livekit-browser
```

`http://127.0.0.1:5173/livekit-smoke.html`で「Roomへ接続」を押す。画面ログとDevTools Consoleの両方で次を確認する。

```text
[browser] connected {"room":"team-d-livekit-smoke","identity":"browser-tomo-smoke",...}
[browser] identity_check {"status":"ok",...,"browserIdentity":"browser-tomo-smoke","agentIdentity":"agent-guidance-smoke","unique":true}
```

terminal Aにも次が追加される。

```text
participant_connected room=team-d-livekit-smoke identity=browser-tomo-smoke sid=...
identity_check status=ok room=team-d-livekit-smoke browser_identity=browser-tomo-smoke agent_identity=agent-guidance-smoke unique=true
```

### 6. participant一覧で最終照合する（terminal C）

両方を接続したまま実行する。

```bash
set -a
source .env.local
set +a
lk room participants list "$LIVEKIT_ROOM"
```

一覧に`browser-tomo-smoke`と`agent-guidance-smoke`が各1件だけ存在し、browser／Agent両側の`identity_check status=ok`と同じ値であることを確認する。公式手順: [Participant management](https://docs.livekit.io/intro/basics/rooms-participants-tracks/participants/)

検証記録を残す場合はtokenやAPI secretを含めず、`tmp/livekit-smoke/`配下へparticipant一覧と両側の該当ログだけを保存する。`tmp/`はgitignoredである。

### 終了

browserタブを閉じ、terminal Aで`Ctrl-C`、terminal Bで`Ctrl-C`を実行する。Roomは最後のparticipant退出後に閉じる。明示削除する場合は、他の検証participantがいないことを確認してから次を実行する。

```bash
lk room delete "$LIVEKIT_ROOM"
```
