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

## 撮影後 ShotAssessor のデモ切替（OpenSpec 4.5）

`PROVIDER_MODE` はプロセス起動時に選ぶ明示的な provider 選択である。`live`
での timeout、外部 provider error、または runtime schema error は HTTP の失敗として
利用者へ返し、同じ request の中で fixture の成功 response に置き換えてはならない。
撮影済み slot と現在 step はその失敗で変更しない。

### live を継続する条件

デモ開始前に `live` で front、back、tag の各 1 request を試す。各 request は
20 秒以内に runtime schema を満たす response を返し、provider error がなく、結果が
requested shot と整合する場合に live を継続する。失敗した request は同じ画像で 1 回
だけ再試行してよい。再試行でも次のいずれかが起きたら、ライブ検証を止めて fixture
へ切り替える。

- 20 秒 timeout、接続／認証／rate-limit を含む provider error
- schema validation error または requested shot と矛盾する response
- front、back、tag のいずれかで 2 回連続して有効な response を得られない

この判断は「live が失敗したので成功扱いにする」ものではない。画面上の失敗を確認し、
fixture を選ぶために operator が process を切り替える。

live preflightの前に、gitignoredの`.env.local`へserver-onlyの資格情報と固定モデルを
設定する。`OPENAI_API_KEY`を`VITE_`で始まる変数へ複製してはならない。

```dotenv
OPENAI_API_KEY=...
SHOT_ASSESSOR_MODEL=gpt-5.6-luna
```

### fixture へ明示的に切り替える

1. live API／Agent を起動している terminal で `Ctrl-C` を押し、`PROVIDER_MODE=live`
   の process が残っていないことを確認する。
2. 新しい terminal でリポジトリ root に移動し、fixture process を起動する。

   ```bash
   npm run dev:fixture
   ```

   期待結果は startup log に `provider_mode=fixture` が出て、
   `curl --fail http://127.0.0.1:3001/api/health` が `{"status":"ok"}` を返すこと。
3. ブラウザを更新して fixture の撮影フローを新しい request として開始する。live で
   失敗した request を成功として再利用せず、fixture response が返ることを確認する。
4. fixture で front、back、tag を各 1 回実行し、決定的な response でデモを継続する。

### live へ戻す条件

外部 provider の資格情報・接続・schema を修正した後だけ、fixture process を停止する。
新しいterminalで`.env.local`を明示的に読み込んでからliveを起動する。

```bash
set -a
source .env.local
set +a
npm run dev:live
```

上の preflight（3 shot、各 20 秒以内）にすべて通るまで live をデモ経路へ戻さない。
live が再び失敗した場合も、自動 fallback はせず、同じ停止条件で operator が fixtureを
選択する。
