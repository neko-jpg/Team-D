## Context

既存リポジトリには企画資料と静的モックだけがあり、アプリ、API、依存、テスト基盤は存在しない。1日で、平置きの半袖クルーネックTシャツ1着について、WebRTC映像を理解するAgentの撮影中助言、正面・背面・タグ・採寸の固定4枚、50mm専用マーカーを用いた着丈・身幅の半自動採寸と承認、正面の背景生成・合成までを一本のデモにする。

利用者から見える振る舞いは`specs/`、OSSのcommit・関数単位の採用境界はリポジトリルートの`architecture.md`を正とする。

## Goals / Non-Goals

**Goals:**

- LiveKitの持続的なWebRTC接続でカメラ映像をAgentへ渡し、AI意味判定をfrontendへpushする。
- 固定2Dガイドと端末内ライブ品質助言を、Agent切断時のfallbackとしても表示する。
- 撮影後AIのstrictな結果から正面、背面、タグを受理し、採寸写真の受理と採寸値の幾何計算は独立した採寸処理で行う。
- 50mm専用マーカーから射影補正と縮尺計算を行い、撮影後画像AIの正規化4端点から修正可能な着丈・身幅を提案する。
- 商品を含まない背景だけを生成し、正面原本の商品RGBを保持して合成する。
- live providerと決定的fixtureの両方で完走できる。
- 秘密値、rembgポート、処理画像を不用意に公開・永続化しない。

**Non-Goals:**

- WebXR、ARKit、ARCore、平面検出、空間アンカー、衣類輪郭追跡。
- ライブ映像を静止画HTTP upload／pollingで解析する構成、全frame保存、30fpsのAI推論、自動撮影。
- マーカーなしの完全自動採寸、半袖クルーネックTシャツ以外、着丈・身幅以外の採寸、価格推定、Mercari API連携。
- 商品再生成、人物着用生成、人物認識、商品レタッチ。
- ユーザー認証、DB、永続ジョブ、本番監視、全端末対応。

## Decisions

### 1. OSSは役割単位で採用し、アプリ全体をforkしない

| OSS | 決定 |
|---|---|
| LiveKit Agents／LiveKit JS SDK | Room、WebRTC camera track、stateful Agent、data packet／RPCをライブ経路の中核にする |
| Wardrobe | `normalizeImage`、Responses strict schema、review／approveの処理パターンだけ参考にし、コードコピーしない |
| document-autocapture | カメラ制御、グレースケール、輝度、Laplacian分散、raw撮影を限定移植する |
| OpenCV.js | 50mm専用マーカー検出、homography、perspective transform、px/cm換算、衣類輪郭、4端点の距離計算に限定して使う |
| rembg | v2.0.81のHTTP sidecarをloopbackで実行する |
| BiRefNet | `birefnet-general-lite`をrembg経由でのみ使用する |
| react-konva | 導入せず、native Canvas 2Dを使う |
| XState | 導入せず、型付き`useReducer`を使う |
| GarmentIQ | PyTorchと複数モデルを導入せず、撮影後画像AIの正規化4端点＋OpenCV.js＋利用者補正で代替する |

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
  ├─ SemanticGuidanceProcessor → OpenAI Realtime provider
  └─ GuidanceStateMachine
       │ lossy data packet: 短命な助言
       │ reliable packet / RPC: step・受理・再同期
       ▼
React AR overlay / CaptureReducer
```

ハッカソンではSFUの構築時間を省くためLiveKit Cloudを使う。ブラウザはPython FastAPIから短命tokenを取得し、camera trackをRoomへpublishする。Agentはparticipantとしてjoinしてtrackへsubscribeする。この境界はLiveKit OSS SDKに閉じるため、後からself-host serverへ切り替えてもUIとAgentの契約は変わらない。

LiveKit Agentsは映像transportとAgent lifecycleを提供するが、衣類の意味判定モデルではない。`SemanticGuidanceProcessor`がframe arrivalを起点にOpenAI Realtime providerを呼び、結果を有限な`GuidanceEvent`へ正規化する。

設計原則は「リアルタイム性は前処理とアーキテクチャ、意味判断の精度はAIモデル」とする。

| 責務 | モデル依存 |
|---|---|
| WebRTC常時接続、最新frame保持、sampling、backpressure、結果push | しない |
| 明るさ、ブレ、動き、静止状態の数値判定 | しない |
| 衣類の収まり、距離、表裏、タグ移動の意味判定 | `VisionGuidanceProvider`だけが依存 |
| 有限コード化、固定文言、dedupe、sequence、expiry、画面遷移 | しない |

OpenAI Realtimeはvideo入力ではなくimage入力を扱うため、30fpsの全frameを直接モデルへ渡さない。Agentのselectorが現在shotの変更、静止、sampling上限から最新frame 1枚を選び、最大辺320px以下のJPEGへ縮小してproviderへ渡す。providerは有限なcodeだけを返し、backendがruntime validation、固定文言、confidenceの正規化を行う。自由文でUIや遷移を決めない。この契約によりprovider交換時もLiveKit、Reducer、AR overlayを変更しない。

#### 2.1 OpenAI Realtime sessionを撮影セッション中は再利用する

```text
LiveKit camera track
  → latest-frame slot (capacity 1)
  → resize / JPEG encode (max 320px)
  → one persistent OpenAI Realtime WebSocket
      → out-of-band response (`conversation: none`)
      → exact finite code only
  → runtime validation / dedupe / LiveKit push
```

- Agent起動またはRoom join時にRealtime WebSocketをprewarmし、frameごとにTLS／WebSocket／sessionを作り直さない。
- 各frame判定は会話へ追加せず、`conversation: none`のout-of-band responseとして画像1枚と現在shotの短い固定指示だけを送る。過去frame、過去code、過去responseをmodel contextへ蓄積しない。
- Realtime modelはstructured outputsを提供しないため、出力をshotごとの有限code 1個だけに制限し、backendのallowlistで完全一致を検証する。UI文言は既存のbackend固定mapから作る。
- response出力は最小token数に制限する。deadlineは950ms以下とし、期限超過はcancelまたは破棄して`AGENT_UNAVAILABLE`へ正規化する。
- providerは同時response 1件、待機frame 1件とする。新しいframe到着時は中間frameを破棄し、shot generationが変わったresponseをpublishしない。
- WebSocket切断時だけ有限回再接続し、再接続完了までは固定ガイド、端末内品質助言、手動撮影を残す。frame判定失敗をResponses APIへ自動fallbackして1秒SLOを破らない。
- default modelはimage input対応の低遅延Realtime modelを実測して選び、環境変数で固定する。model変更時も実credential 20件以上の性能ゲートを再実行する。

```ts
type GuidanceEvent = {
  sessionId: string;
  sequence: number;
  shot: "front" | "back" | "tag" | "measurement";
  code:
    | "MOVE_CLOSER"
    | "MOVE_FARTHER"
    | "CENTER_GARMENT"
    | "SHOW_FULL_GARMENT"
    | "WRONG_SIDE"
    | "MOVE_TO_TAG"
    | "PLACE_MARKER"
    | "MARKER_NOT_VISIBLE"
    | "FLATTEN_GARMENT"
    | "CAMERA_OVERHEAD"
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
- 意味判定は最大4Hzのframe選択を許可するが、同時response 1件と最新frame 1件で自然にbackpressureを掛ける。clientの定期HTTP pollingは使わない。
- 同一shot／codeは変化時だけlossy packetで送り、step変更、撮影受理、再同期はreliable packetまたはRPCで送る。
- 同一shot／codeの継続判定は表示eventとして再送しない。接続の生存確認は助言eventと分離したheartbeat packetで伝え、UIの主文言を再描画させない。
- frontendからのshot変更は有限なcommand schemaで受信し、保留frameへ観測時のshotとgenerationを束縛する。Agent再起動時はprocess epoch付きsnapshotを使い、同じRoom名でsequenceが再開しても旧processと混同させない。
- live providerには950ms以下のdeadlineを設け、timeout／errorは`AGENT_UNAVAILABLE`へ正規化して未処理例外を残さない。
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

`ShotAssessment`へmeasurementの受理、端点、cm値を混在させない。AIの自由文や`nextAction`を直接実行せず、front／back／tagの3つの受理済みslot、採寸slot、採寸承認状態からReducerが次状態を再計算する。

### 4. Python backendのprovider境界に外部サービスを閉じる

- `VisionGuidanceProvider`: Agent内でvideo frameと現在shotを受け、撮影セッション中に再利用するOpenAI Realtime WebSocketから有限な意味判定を返す。
- `ShotAssessor`: Responses APIへ撮影画像と指示を送り、strict schemaとruntime schemaで検証する。
- `MeasurementLineProvider`: 射影補正済みの採寸解析コピーを受け、着丈・身幅の4端点だけを0〜1の正規化座標で返す。cm値と画面遷移は返さない。
- `BackgroundGenerator`: 許可されたstyle IDを固定promptへ変換し、Images APIへテキストだけを送る。
- `GarmentMasker`: rembg `/api/remove`へ`file`、`model=birefnet-general-lite`、`om=true`を送り、PNG maskを検証する。

LiveKitのlive video inputはPythonのみ対応しているため、HTTP APIとAgentを同じPython package／仮想環境へ置く。FastAPI serverとAgent workerは別entrypointで起動し、provider schemaと設定を共有する。Node.js backendは追加しない。

各providerはfixture実装を持つ。`PROVIDER_MODE`で明示的に切り替え、live失敗を自動成功へ変換しない。採寸解析または`MeasurementLineProvider`失敗時は成功fixtureへ置き換えず、理由付き撮り直し、衣類輪郭上の粗いdraft、利用者の4端点配置、または着丈・身幅の手入力へ明示的に切り替える。

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

### 6. 50mmマーカーとOpenCV.jsで着丈・身幅を半自動採寸する

採寸対象は半袖クルーネックTシャツに限定する。背面を上にして襟、袖、裾を広げ、外形50.0mm角・5mm黒枠・40.0mm白地の専用マーカーを100%印刷して定規で確認し、衣類から30mm以上離れた同一平面の右下へ置いて真上から1枚撮影する。measurement原本は`ShotAssessment`へ送らず、採寸専用validationとOpenCV.js Workerで次を実行する。

1. グレースケール化と輪郭抽出から外形・内形が入れ子になったマーカー候補を四角形近似する。
2. 四隅を順序付けし、homographyで撮影面を射影補正する。
3. 補正後の50mm辺からpx/cmを計算する。
4. マーカーを除いた衣類輪郭を求め、射影補正済みの解析コピーを`MeasurementLineProvider`へ1回だけ送り、背面襟中央付け根、裾中央、左右脇下の4端点を正規化座標で得る。
5. provider失敗時は衣類輪郭上の粗いdraftまたは利用者の4端点配置へ切り替える。
6. 利用者が4端点をドラッグするたびに0.1cm単位で再計算し、明示承認後だけ確定する。身幅は2倍しない。

```ts
type MeasurementDraft = {
  imageId: string;
  marker?: {
    knownSideCm: 5;
    corners: [Point, Point, Point, Point];
    pxPerCm: number;
  };
  length: { start: Point; end: Point; valueCm: number };
  width: { start: Point; end: Point; valueCm: number };
  source: "ai" | "contour" | "user";
  status: "needs_review" | "approved_cv" | "approved_manual";
};
```

初期検出条件はマーカー最短辺80px以上、全四隅が画像端から16px超、最短辺／最長辺0.65以上、衣類との画像上の間隔24px以上とする。失敗は`MARKER_MISSING|MARKER_MULTIPLE|MARKER_TOO_SMALL|MARKER_OCCLUDED|GARMENT_OUT_OF_FRAME|GARMENT_MARKER_OVERLAP|SEGMENTATION_FAILED|ENDPOINTS_INVALID`へ限定する。着丈20〜100cm、身幅20〜80cmの範囲外は警告するが再確認後は承認可能とする。OpenCV.jsまたはproviderが失敗しても4枚目は必須とし、撮り直し、粗いドラフト、端点配置、手入力で完走させる。代表Tシャツで補正・承認後の2値がメジャー実測に対して各±1.0cm以内であることを受入目標とし、自動ドラフト自体の誤差は成功条件にしない。

### 7. 直列状態は型付きReducerで管理する

```text
CONNECTING_LIVE → CAPTURE(front) ⇄ LIVE_GUIDANCE
→ ANALYZING_SHOT → RETAKE|ACCEPT
→ CAPTURE(back) → ANALYZING
→ CAPTURE(tag) → ANALYZING
→ MEASUREMENT_PREP → CAPTURE(measurement) → VALIDATING_MEASUREMENT
→ MEASURING → MEASUREMENT_REVIEW → RETAKE|MANUAL_INPUT|APPROVE
→ READY_TO_EDIT → MASKING + GENERATING_BACKGROUND
→ PREVIEW → APPROVAL → DONE
```

各slotは原本Blob、object URL、判定結果を持ち、採寸状態は`MeasurementDraft`と承認状態を持つ。4枚が保持され、採寸状態が`approved_cv`または`approved_manual`になるまで`READY_TO_EDIT`へ遷移しない。失敗時は現在stepと受け入れ済みslotを変更せず、終了時に画像とOpenCV中間データを破棄してobject URLを解放する。

### 8. デモは最初にfixtureで縦スライスを通す

開始時にfront／back／tag／measurement、正常・欠け・重なり・遠近歪みのマーカー、既知縮尺と測定線、撮り直し、誤種別、順序逆転／期限切れの`GuidanceEvent`、Agent切断、AIエラー、mask、固定背景のfixtureを用意する。uploadで全体を通してからLiveKit Room、Agent、live AI、OpenCV.js、rembg、背景生成を順に接続する。

rembgはPython 3.11、v2.0.81、`birefnet-general-lite`を固定し、デモ前にモデルをdownloadしてfixture frontを1回処理する。mask寸法はEXIF orientation適用後の解析寸法と比較し、backend-only検証用には元RGBへmaskをalpha適用したRGBA PNG previewも返せるようにする。初期timeoutはanalyze 20秒、live guidanceは950ms以下の設定可能なdeadline、rembg 35秒、背景生成60秒とする。live guidanceは実OpenAI credentialでウォーム状態の成功20件以上、error 0件、`observedAt→backend publish` p95 1,000ms未満を必須ゲートとし、Realtime接続時間はcold-startとして別記録する。

## Risks / Trade-offs

- **[ライブ意味判定が遅い／揺れる]** → Realtime sessionをprewarm・再利用し、入力画像とpromptと出力tokenを最小化する。最新frame 1件、同時推論1、950ms以下の期限、dedupeを設け、実credential 20件のp95が1秒未満になるまでmodelと画像条件を調整する。合格しないproviderを本番live経路として採用しない。
- **[LiveKit／Agentが切断する]** → 再接続状態を明示し、端末内品質助言、手動撮影、受け入れ済みslotを維持する。
- **[object-fitでROIがずれる]** → 表示座標から映像座標への変換を純粋関数としてテストする。
- **[外部AIまたはrembgが遅い]** → timeout、明示的retry、固定背景、原本採用、fixtureを用意する。
- **[maskが袖や裾を欠く]** → 空／全面／寸法を検査し、承認前比較と元画像採用を必須にする。
- **[マーカー検出や初期測定線がずれる]** → 印刷倍率と同一平面を案内し、異常理由を表示して撮り直しまたは手入力を許可し、4端点の補正と明示承認を必須にする。
- **[OpenCV.jsがUIを停止させる]** → Worker内で同時解析1件にし、失敗時は手入力へ逃がす。
- **[OSSライセンスを落とす]** → document-autocaptureのMITとOpenCV.jsのApache-2.0、固定version、NOTICE要否を記録する。
- **[1日で過剰になる]** → 背景処理は正面1枚、採寸は半袖クルーネックTシャツ1種・専用1枚・2項目だけに限定する。

## Migration Plan

既存実装や永続データはないため移行は発生しない。

1. fixture、Reducer、uploadで4枚の撮影ループと採寸承認ゲートを完成させる。
2. LiveKit token、Room接続、camera track publish、Agent subscribe、fixture guidance pushを接続する。
3. 最新frame処理とliveの`VisionGuidanceProvider`を接続し、切断／古いeventのfallbackを確認する。
4. 端末内品質判定と撮影後`ShotAssessor`をfront／back／tagへ接続する。
5. OpenCV.jsのマーカー検出、射影補正、`MeasurementLineProvider`、測定線、修正・承認を接続する。
6. rembgをprewarmし、正面maskを接続する。
7. 背景生成、Canvas合成、比較、承認、保存を接続する。
8. 基準端末とfixtureで垂直スライスと採寸精度を確認する。

ロールバックはAgentのlive model providerを停止し、明示的なfixture guidanceへ切り替える。LiveKit自体が使えない場合は端末内品質助言＋手動撮影へ縮退する。撮影済み進捗を黙って成功扱いにはしない。

## 9. 取り込み済みcapture coreの位置づけ

`origin/main`から、React／TypeScript／ViteとNode.js APIのscaffold、front／back／tag用の共有契約、fixture `ShotAssessor`、3slotの`CaptureReducer`とupload UI、4Hz scheduler、frame-difference trackerを取り込んだ。これらは最終フローの基盤として再利用する。

現在の3slot完了後に編集入口を開く実装は暫定縦スライスであり、最終完了条件ではない。`measurement` slot、採寸専用validation、`MeasurementLineProvider`、利用者の採寸承認を追加し、4枚と`approved_cv|approved_manual`が揃うまで`READY_TO_EDIT`へ遷移しないよう拡張する。
