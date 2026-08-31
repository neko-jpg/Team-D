## Context

既存リポジトリには企画資料と静的モックだけがあり、アプリ、API、依存、テスト基盤は存在しない。1日で、平置きトップス1着について、WebRTC映像を理解するAgentの撮影中助言、正面・背面・タグの撮影後確認、正面の背景生成・合成、承認までを一本のデモにする。

利用者から見える振る舞いは`specs/`、OSSのcommit・関数単位の採用境界はリポジトリルートの`architecture.md`を正とする。

## Goals / Non-Goals

**Goals:**

- LiveKitの持続的なWebRTC接続でカメラ映像をAgentへ渡し、AI意味判定をfrontendへpushする。
- 固定2Dガイドと端末内ライブ品質助言を、Agent切断時のfallbackとしても表示する。
- 撮影後AIのstrictな結果から正面、背面、タグを揃える。
- 商品を含まない背景だけを生成し、正面原本の商品RGBを保持して合成する。
- live providerと決定的fixtureの両方で完走できる。
- 秘密値、rembgポート、処理画像を不用意に公開・永続化しない。

**Non-Goals:**

- WebXR、ARKit、ARCore、平面検出、空間アンカー、衣類輪郭追跡。
- ライブ映像を静止画HTTP upload／pollingで解析する構成、全frame保存、30fpsのAI推論、自動撮影。
- 採寸、価格推定、Mercari API連携。
- 商品再生成、人物着用生成、人物認識、商品レタッチ。
- ユーザー認証、DB、永続ジョブ、本番監視、全端末対応。

## Decisions

### 1. OSSは役割単位で採用し、アプリ全体をforkしない

| OSS | 決定 |
|---|---|
| LiveKit Agents／LiveKit JS SDK | Room、WebRTC camera track、stateful Agent、data packet／RPCをライブ経路の中核にする |
| Wardrobe | `normalizeImage`、Responses strict schema、review／approveの処理パターンだけ参考にし、コードコピーしない |
| document-autocapture | カメラ制御、グレースケール、輝度、Laplacian分散、raw撮影を限定移植する |
| rembg | v2.0.81のHTTP sidecarをloopbackで実行する |
| BiRefNet | `birefnet-general-lite`をrembg経由でのみ使用する |
| react-konva | 導入せず、native Canvas 2Dを使う |
| XState | 導入せず、型付き`useReducer`を使う |
| GarmentIQ | 採寸とともに除外する |

詳細なファイル、関数、除外箇所、ライセンスは`architecture.md`の「OSS利用境界」に集約する。

**代替案:** Wardrobeまたはdocument-autocaptureを丸ごと導入すると、商品再生成、人物生成、書類検出、perspective warp、永続jobを外す作業が増えるため採用しない。

### 2. LiveKit Agentsをライブ経路の中核にする

```text
React camera
  │ WebRTC video track
  ▼
LiveKit Room
  ▼
Python LiveKit Agent
  ├─ video track subscription / latest-frame slot
  ├─ SemanticGuidanceProcessor → video対応AI provider
  └─ GuidanceStateMachine
       │ lossy data packet: 短命な助言
       │ reliable packet / RPC: step・受理・再同期
       ▼
React AR overlay / CaptureReducer
```

ハッカソンではSFUの構築時間を省くためLiveKit Cloudを使う。ブラウザはPython FastAPIから短命tokenを取得し、camera trackをRoomへpublishする。Agentはparticipantとしてjoinしてtrackへsubscribeする。この境界はLiveKit OSS SDKに閉じるため、後からself-host serverへ切り替えてもUIとAgentの契約は変わらない。

LiveKit Agentsは映像transportとAgent lifecycleを提供するが、衣類の意味判定モデルではない。`SemanticGuidanceProcessor`がframe arrivalを起点にvideo対応AI providerを呼び、結果を有限な`GuidanceEvent`へ正規化する。

設計原則は「リアルタイム性は前処理とアーキテクチャ、意味判断の精度はAIモデル」とする。

| 責務 | モデル依存 |
|---|---|
| WebRTC常時接続、最新frame保持、sampling、backpressure、結果push | しない |
| 明るさ、ブレ、動き、静止状態の数値判定 | しない |
| 衣類の収まり、距離、表裏、タグ移動の意味判定 | `VisionGuidanceProvider`だけが依存 |
| 有限コード化、固定文言、dedupe、sequence、expiry、画面遷移 | しない |

画像AIは30fpsの全frameを監視しない。Agentのselectorが現在shotの変更、静止、sampling上限から最新frame 1枚を選び、`requestedShot`と前回codeを添えてproviderへ渡す。providerは有限なcodeとconfidenceだけを返し、自由文でUIや遷移を決めない。この契約によりprovider交換時もLiveKit、Reducer、AR overlayを変更しない。

```ts
type GuidanceEvent = {
  sessionId: string;
  sequence: number;
  shot: "front" | "back" | "tag";
  code:
    | "MOVE_CLOSER"
    | "MOVE_FARTHER"
    | "CENTER_GARMENT"
    | "SHOW_FULL_GARMENT"
    | "WRONG_SIDE"
    | "MOVE_TO_TAG"
    | "HOLD_STEADY"
    | "READY"
    | "AGENT_UNAVAILABLE";
  message: string;
  confidence: number;
  observedAt: number;
  expiresAt: number;
};
```

- Agentはbuffer capacity 1、意味判定同時1件とし、処理中に届いた中間frameをcoalesceする。
- 意味判定は1〜2fpsを上限にする。clientの定期HTTP pollingではなく、継続中のWebRTC trackへbackpressureを掛ける。
- 同一shot／codeは変化時だけlossy packetで送り、step変更、撮影受理、再同期はreliable packetまたはRPCで送る。
- frontendはsession、shot、sequence、expiryを検査し、古い結果で表示や状態を巻き戻さない。
- Agent切断時は再接続表示へ切り替え、固定ガイド、端末内品質判定、手動撮影を残す。

**代替案:** `setInterval`で静止画を`/api/analyze-live`へ送る方式は、request競合、古い応答、接続管理をアプリ側へ持ち込むため採用しない。Vision AgentsやPipecatは有力だが、1日MVPではLiveKitのRoom／Agent／data APIへ直接寄せる方が依存とデバッグ面を減らせる。

### 3. ライブ助言と撮影後受理判定を分離する

端末内の`LocalQualityHint`は固定ROIから4Hzで作り、明るさ、ブレ、静止状態を即時表示する。

```ts
type LocalQualityHint =
  | "TOO_DARK"
  | "TOO_BRIGHT"
  | "TOO_BLURRY"
  | "HOLD_STEADY"
  | "READY"
  | "ANALYZER_UNAVAILABLE";
```

- 固定ROIを最大辺320pxへ縮小する。
- 初期値は輝度45〜215、Laplacian分散24以上、frame delta 0.020未満が600ms継続とする。
- 通常4Hz、同時解析1、状態変化からUI反映p95 500ms以内を目標とする。
- Agentの意味判定は撮影前の暫定助言に限定する。
- いずれのライブ結果が`READY`でなくても手動撮影を許可する。

`ShotAssessment`は撮影後の高解像度画像から取得し、写真の受理可否だけに使う。

```ts
type ShotAssessment = {
  shotType: "front" | "back" | "tag" | "unknown";
  quality: "ok" | "retry";
  issues: string[];
  missingShots: Array<"front" | "back" | "tag">;
  nextAction: "RETAKE" | "REQUEST_NEXT" | "COMPLETE";
};
```

AIの自由文や`nextAction`を直接実行せず、受け入れ済みslotからReducerが次状態を再計算する。

### 4. Python backendのprovider境界に外部サービスを閉じる

- `VisionGuidanceProvider`: Agent内でvideo frameと現在shotを受け、有限な意味判定を返す。
- `ShotAssessor`: Responses APIへ撮影画像と指示を送り、strict schemaとruntime schemaで検証する。
- `BackgroundGenerator`: 許可されたstyle IDを固定promptへ変換し、Images APIへテキストだけを送る。
- `GarmentMasker`: rembg `/api/remove`へ`file`、`model=birefnet-general-lite`、`om=true`を送り、PNG maskを検証する。

LiveKitのlive video inputはPythonのみ対応しているため、HTTP APIとAgentを同じPython package／仮想環境へ置く。FastAPI serverとAgent workerは別entrypointで起動し、provider schemaと設定を共有する。Node.js backendは追加しない。

各providerはfixture実装を持つ。`PROVIDER_MODE`で明示的に切り替え、live失敗を自動成功へ変換しない。

**代替案:** Node.js APIを別に置く構成は言語とschemaの境界を1つ増やすため採用しない。LiveKit Room接続を除き、ブラウザからAI／画像生成／rembg APIを直接呼ぶ構成も、秘密値、CORS、rembgのCORS `*`、端末差分を制御できないため採用しない。

### 5. 商品を含まない背景だけを生成する

背景生成APIへ商品画像を渡さない。生成promptは「空の撮影背景、真上視点、均一照明、人物・衣類・ハンガー・文字・ロゴなし」に限定し、失敗時は同梱の固定背景を使う。

正面原本はrembgへ送り、mask-onlyを取得する。商品前景は元画像RGBへmaskを適用して作る。

```text
background text → Images API → background
front original → rembg → mask
front original RGB × mask → foreground
background + foreground → Canvas preview → approval → output
```

合成はnative Canvas 2Dの`drawImage`、`destination-in`、`toBlob`で行う。位置調整を要件に含めないためreact-konvaは使わない。

### 6. 直列状態は型付きReducerで管理する

```text
CONNECTING_LIVE → CAPTURE(front) ⇄ LIVE_GUIDANCE
→ ANALYZING_SHOT → RETAKE|ACCEPT
→ CAPTURE(back) → ANALYZING
→ CAPTURE(tag) → ANALYZING
→ READY_TO_EDIT → MASKING + GENERATING_BACKGROUND
→ PREVIEW → APPROVAL → DONE
```

各slotは原本Blob、object URL、判定結果を持つ。失敗時は現在stepと受け入れ済みslotを変更しない。終了時にobject URLを解放する。

### 7. デモは最初にfixtureで縦スライスを通す

開始時にfront／back／tag、撮り直し、誤種別、順序逆転／期限切れの`GuidanceEvent`、Agent切断、AIエラー、mask、固定背景のfixtureを用意する。uploadで全体を通してからLiveKit Room、Agent、live AI、rembg、背景生成を順に接続する。

rembgはPython 3.11、v2.0.81、`birefnet-general-lite`を固定し、デモ前にモデルをdownloadしてfixture frontを1回処理する。初期timeoutはanalyze 20秒、rembg 35秒、背景生成60秒とする。

## Risks / Trade-offs

- **[ライブ意味判定が遅い／揺れる]** → 最新frame 1件、同時推論1、期限、dedupeを設け、助言に限定して撮影後AIを最終判定にする。
- **[LiveKit／Agentが切断する]** → 再接続状態を明示し、端末内品質助言、手動撮影、受け入れ済みslotを維持する。
- **[object-fitでROIがずれる]** → 表示座標から映像座標への変換を純粋関数としてテストする。
- **[外部AIまたはrembgが遅い]** → timeout、明示的retry、固定背景、原本採用、fixtureを用意する。
- **[maskが袖や裾を欠く]** → 空／全面／寸法を検査し、承認前比較と元画像採用を必須にする。
- **[OSSライセンスを落とす]** → document-autocapture限定移植時にMIT全文、著作権、commit、出典コメントを追加する。
- **[1日で過剰になる]** → 背景処理は正面1枚だけ。人物生成、採寸、自動撮影、位置調整を追加しない。

## Migration Plan

既存実装や永続データはないため移行は発生しない。

1. fixture、Reducer、uploadで3枚の撮影ループを完成させる。
2. LiveKit token、Room接続、camera track publish、Agent subscribe、fixture guidance pushを接続する。
3. 最新frame処理とliveの`VisionGuidanceProvider`を接続し、切断／古いeventのfallbackを確認する。
4. 端末内品質判定と撮影後`ShotAssessor`を接続する。
5. rembgをprewarmし、正面maskを接続する。
6. 背景生成、Canvas合成、比較、承認、保存を接続する。
7. 基準端末とfixtureで垂直スライスを確認する。

ロールバックはAgentのlive model providerを停止し、明示的なfixture guidanceへ切り替える。LiveKit自体が使えない場合は端末内品質助言＋手動撮影へ縮退する。撮影済み進捗を黙って成功扱いにはしない。
