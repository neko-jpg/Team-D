# Mercari AI Agent Hackathon Webアーキテクチャ

最終更新: 2026-08-31

## 1. 結論

LiveKit Agentsをライブ撮影体験の中核にし、小さなReactモバイルWebとPython backend／Agentを新規実装する。既存OSSをアプリごとforkはしない。

- LiveKit AgentsはWebRTC映像の受信、stateful Agent、frontendへのpushに使用
- ハッカソンではLiveKit CloudをRoom基盤として使い、SDK／AgentコードはOSSを利用。必要なら同じ構成でself-hostへ移行可能
- Wardrobeは設計参考のみ
- document-autocaptureはMIT表記を保持して必要関数だけ移植
- OpenCV.jsは50mm専用マーカー検出、射影補正、着丈・身幅の距離計算に限定使用
- rembg v2.0.81をローカルHTTP sidecarとして実行
- BiRefNet General Liteをrembg経由で使用
- 合成はnative Canvas 2D、状態管理は`useReducer`

引用コードは調査時のcommit、実行時依存は実装開始時のlockfileへ固定する。

## 2. 全体構成

```text
Mobile browser
  ├─ CameraController
  ├─ FixedGuideOverlay
  ├─ LiveKitSessionClient
  ├─ LiveQualityAnalyzer (4Hz local fallback)
  ├─ MeasurementProcessor (OpenCV.js Worker)
  ├─ MeasurementReview
  ├─ CaptureReducer
  └─ CanvasCompositor
       │ WebRTC video track        ▲ data packet / RPC push
       ▼                           │
LiveKit Room ───────────────→ Python LiveKit Agent
                              ├─ SemanticGuidanceProcessor
                              ├─ GuidanceStateMachine
                              └─ VisionGuidanceProvider → video対応AI

Mobile browser ── HTTPS /api ──→ Python FastAPI
Python backend package
  ├─ LiveKitTokenIssuer ───→ Room access token
  ├─ ShotAssessor ──────→ Responses API
  ├─ MeasurementPointSuggester → Responses API
  ├─ BackgroundGenerator → Images API (text only)
  └─ GarmentMasker ─────→ rembg HTTP / BiRefNet
```

ブラウザは一度だけ短命tokenを取得してRoomへ接続する。ライブ映像はWebRTC media trackで流し、助言はLiveKitのdata channelでAgentからpushする。`setInterval(fetch)`や`POST /api/analyze-live`は作らない。[LiveKitのlive video inputはPythonのみ対応](https://docs.livekit.io/agents/multimodality/vision/video/)しているため、AgentとHTTP APIを同じPython package／仮想環境へ寄せ、Node.js backendは置かない。OpenAI APIキー、LiveKit API secret、rembgポートは外部公開しない。

## 3. 自作する責務

| モジュール | 責務 |
|---|---|
| `CameraController` | 背面カメラ、権限、video再生、track解放、raw撮影 |
| `LiveKitSessionClient` | 短命token取得、Room接続、camera track publish、イベント購読、再接続 |
| `LiveQualityAnalyzer` | 固定ROI、輝度、Laplacianブレ、フレーム差分による即時補助判定 |
| `MeasurementProcessor` | 50mm専用マーカー検出、射影補正、px/cm換算、4端点からの距離計算 |
| `MeasurementReview` | 4端点のドラッグ修正、cm再計算、明示承認、手入力fallback |
| `MeasurementPointSuggester` | 補正済み採寸写真から4つの意味的端点を正規化座標で1回だけ提案 |
| `SemanticGuidanceProcessor` | Agent側の最新フレーム選択、AI意味判定、古い結果の破棄 |
| `GuidanceStateMachine` | AI出力を有限コードへ変換、重複抑制、sequence／expiry付与、push |
| `CaptureReducer` | 正面→背面→タグ→採寸、撮り直し、受け入れ済み画像と採寸承認の保持 |
| `ShotAssessor` | 画像AI入力、strict schema、runtime validation |
| `BackgroundGenerator` | 商品を含まない背景のtext-to-image生成 |
| `GarmentMasker` | rembgへのmultipart接続、マスク検証、timeout |
| `CanvasCompositor` | 背景＋元商品RGB＋maskの合成、比較、出力 |

## 4. OSS利用境界

### 4.1 LiveKit Agents / LiveKit JS SDK

対象: [LiveKit Agents](https://github.com/livekit/agents)、[Agents docs](https://docs.livekit.io/agents/)、[video input](https://docs.livekit.io/agents/multimodality/vision/video/)、[data packets](https://docs.livekit.io/transport/data/packets/)、[RPC](https://docs.livekit.io/transport/data/rpc/)。実装開始時のstable versionをPython lockfileとnpm lockfileへ固定する。

採用形態は**ライブ経路の中核となる実行時依存**。LiveKitが担うのは映像transport、Room、Agent lifecycle、data channelであり、衣類の意味判定そのものはvideo対応AI providerと今回実装するprocessorが担う。

| LiveKitの部分 | 今回の使い方 | 自作する境界 |
|---|---|---|
| Room／WebRTC media track | ブラウザの背面camera trackをAgentへ連続配信 | Room名、participant identity、短命tokenの発行 |
| LiveKit JS SDK | Room接続、camera publish、data／connection event購読 | AR overlay、Reducer、期限切れ・順序逆転イベントの破棄 |
| Agents job／participant | Python Agentを撮影セッションのstateful participantとして起動 | 現在shot、最後のguidance、provider errorを持つsession state |
| video track subscription／`VideoStream` | frame到着を起点に最新フレームを取得 | buffer size 1、意味判定同時1件、古いframeと結果の破棄 |
| data packets | 短命な`GuidanceEvent`をlossyでpush | schema、dedupe、sequence、`expiresAt`、表示優先度 |
| reliable packet／RPC | shot変更、撮影受理、現在状態の再同期 | idempotency、Reducerとの整合、再接続後のrehydrate |

ハッカソンではSFU運用を避けるためLiveKit Cloudを優先するが、プロダクトのtransport境界はLiveKit SDKだけに閉じる。self-host serverへの移行でUI／Agent契約を変えない。

使わないもの:

- 音声会話、電話、avatar、マルチユーザー会議
- 全フレームの保存、録画、30fpsのVLM推論
- ブラウザからAI providerへの直接接続
- LiveKit Egress／Ingress（今回の撮影セッションには不要）

### 4.2 Wardrobe

対象commit: [`f44006c`](https://github.com/tandpfun/wardrobe/tree/f44006cce7e4779e595a35b25fbbc8dabc68d7e4)

採用形態は**設計参考のみ**。依存、fork、submodule、ソースコピーは行わない。

| 参考にする箇所 | 参考内容 | 今回の実装 |
|---|---|---|
| [`normalizeImage()`](https://github.com/tandpfun/wardrobe/blob/f44006cce7e4779e595a35b25fbbc8dabc68d7e4/scripts/import-job-api.mjs#L78-L80) | EXIF回転、sRGB、PNG正規化の順序 | multipart bytesを受ける独自処理。原本とは別の解析コピーだけを正規化 |
| [`openAIAnalyze()`](https://github.com/tandpfun/wardrobe/blob/f44006cce7e4779e595a35b25fbbc8dabc68d7e4/scripts/import-job-api.mjs#L321-L340) | 画像入力＋`json_schema`＋`strict: true` | `ShotAssessor`と撮影用schemaを新規実装し、受信後もruntime検証 |
| [`deriveStatus()`／`reviewStageFor()`](https://github.com/tandpfun/wardrobe/blob/f44006cce7e4779e595a35b25fbbc8dabc68d7e4/src/import-flow.jsx#L33-L53) | 外部処理を有限UI状態へ写像 | `CaptureReducer`の状態とイベントを新規定義 |
| [`ReviewEditor()`](https://github.com/tandpfun/wardrobe/blob/f44006cce7e4779e595a35b25fbbc8dabc68d7e4/src/import-flow.jsx#L70-L102) | preview後の明示承認とbusy制御 | 元画像／合成画像の比較、撮り直し、承認を新規実装 |

使わない箇所:

- [`buildGarmentPrompt()`](https://github.com/tandpfun/wardrobe/blob/f44006cce7e4779e595a35b25fbbc8dabc68d7e4/scripts/import-job-api.mjs#L109-L136): 商品をreconstructするため
- [`openAIEdit()`](https://github.com/tandpfun/wardrobe/blob/f44006cce7e4779e595a35b25fbbc8dabc68d7e4/scripts/import-job-api.mjs#L299-L319): 商品・人物画像を編集生成するため
- [クロマキー処理](https://github.com/tandpfun/wardrobe/blob/f44006cce7e4779e595a35b25fbbc8dabc68d7e4/scripts/import-job-api.mjs#L98-L279): rembg maskと役割が重複
- [garment／modeled生成](https://github.com/tandpfun/wardrobe/blob/f44006cce7e4779e595a35b25fbbc8dabc68d7e4/scripts/import-job-api.mjs#L424-L466): 商品再生成・人物着用生成のため
- [disk job／library](https://github.com/tandpfun/wardrobe/blob/f44006cce7e4779e595a35b25fbbc8dabc68d7e4/scripts/import-job-api.mjs#L280-L421)と[900ms polling](https://github.com/tandpfun/wardrobe/blob/f44006cce7e4779e595a35b25fbbc8dabc68d7e4/src/import-flow.jsx#L161-L173): 非永続・通常fetch方針と不一致

### 4.3 document-autocapture

対象commit: [`e24df25`](https://github.com/maazkhan77/document-autocapture/tree/e24df25d17ddc4cf7d7944c653bd0fba55025452)

採用形態は**関数・実装パターンの限定移植**。`react-document-autocapture`と`js-document-autocapture`は依存へ追加しない。

| 移植・参考箇所 | 今回の変更 |
|---|---|
| [`DEFAULT_VIDEO_CONSTRAINTS`](https://github.com/maazkhan77/document-autocapture/blob/e24df25d17ddc4cf7d7944c653bd0fba55025452/packages/runtime-web/src/session/defaults.ts#L8-L12)、[`start()`](https://github.com/maazkhan77/document-autocapture/blob/e24df25d17ddc4cf7d7944c653bd0fba55025452/packages/runtime-web/src/session.ts#L241-L295) | secure context、背面カメラ、権限エラーを`CameraController`へ移植 |
| [`ensureVideoPlayback()`／`cleanupVideoStream()`](https://github.com/maazkhan77/document-autocapture/blob/e24df25d17ddc4cf7d7944c653bd0fba55025452/packages/runtime-web/src/session/video-media.ts#L7-L55) | iOS再生fallbackとtrack解放を移植 |
| [`scheduleNextFrame()`](https://github.com/maazkhan77/document-autocapture/blob/e24df25d17ddc4cf7d7944c653bd0fba55025452/packages/runtime-web/src/session.ts#L562-L591) | rVFC→rAF→timerの考え方を使い、4Hz・同時解析1件に制限 |
| [`rgbaToGrayscale()`／`laplacianVariance()`](https://github.com/maazkhan77/document-autocapture/blob/e24df25d17ddc4cf7d7944c653bd0fba55025452/packages/core-engine/src/pipeline/pixels.ts#L3-L19) | 固定ガイドROIを縮小した配列へ適用。Laplacian本体は[同ファイル](https://github.com/maazkhan77/document-autocapture/blob/e24df25d17ddc4cf7d7944c653bd0fba55025452/packages/core-engine/src/pipeline/pixels.ts#L146-L172)を限定移植 |
| [`brightnessCheck()`／`blurCheck()`](https://github.com/maazkhan77/document-autocapture/blob/e24df25d17ddc4cf7d7944c653bd0fba55025452/packages/core-engine/src/pipeline/quality.ts#L34-L85) | 書類`Quad`由来ROIを固定衣類ガイドROIへ置換 |
| [`canvasToBlob()`](https://github.com/maazkhan77/document-autocapture/blob/e24df25d17ddc4cf7d7944c653bd0fba55025452/packages/runtime-web/src/session/capture-pipeline.ts#L24-L41)と[raw frame描画](https://github.com/maazkhan77/document-autocapture/blob/e24df25d17ddc4cf7d7944c653bd0fba55025452/packages/runtime-web/src/session/capture-pipeline.ts#L83-L99) | perspective warpを除き、原寸videoをBlob化 |
| [video＋absolute overlay](https://github.com/maazkhan77/document-autocapture/blob/e24df25d17ddc4cf7d7944c653bd0fba55025452/packages/sdk-react/src/DocumentAutoCaptureCamera.tsx#L141-L162) | 書類`Quad`を固定SVG衣類ガイドへ置換 |

独自に置換するもの:

- 書類`Quad`から作るROI → 表示ガイドを映像座標へ変換した固定ROI
- 四隅移動量の`StabilityTracker` → 縮小グレースケールROIの連続フレーム差分
- `DOCUMENT_NOT_FOUND`／書類面積ガイド → 撮影ステップの固定案内と撮影後AI判定

使わないもの:

- 同OSS内のOpenCV／ML／COCOによる**書類検出**
- 書類四隅、glare、area、perspective warp
- document guidance、自動撮影readiness、Quad overlay

公開SDKは品質primitiveをexportせず、品質処理が書類検出成功に依存するため、パッケージ全体は採用しない。

### 4.4 OpenCV.js

対象: [OpenCV](https://github.com/opencv/opencv)、[OpenCV.js documentation](https://docs.opencv.org/4.x/d5/d10/tutorial_js_root.html)。実装開始時に公式配布のstable OpenCV.js／WASMを固定し、配布元URLとchecksumをlock情報へ記録する。

採用形態は**ブラウザ内の採寸前処理に限る実行時依存**。メインスレッドを塞がないようWeb Workerで実行する。幾何処理とcm換算は外部AIへ依存しない。

| 使用する部分 | 今回の用途 |
|---|---|
| `cvtColor`、threshold／Canny | 50mm二重正方形マーカーと衣類輪郭の候補抽出 |
| `findContours`、`approxPolyDP`、`contourArea` | 四角形マーカーの検出と最大衣類輪郭の抽出 |
| `getPerspectiveTransform`、`warpPerspective` | マーカー四隅を基準に撮影面を射影補正 |
| 点間距離などの画素幾何 | マーカー外形1辺=5.0cmからpx/cmを求め、着丈・身幅を0.1cm単位へ換算 |

使わないもの:

- DNN／学習済みモデル、native Python／server API
- ArUcoなど公式配布buildに含まれる保証がない追加module
- camera UI、汎用物体認識、衣類カテゴリ分類
- OpenCVの自動結果だけによる採寸確定

OpenCV.jsだけでは「襟ぐり中央」「脇下」の意味点を確実に識別できない。撮影後画像AIが4端点を正規化座標で提案し、OpenCV.jsがcmへ換算する。AI失敗時は輪郭上の粗い線または利用者の端点配置へfallbackし、いずれも利用者補正と明示承認を確定条件にする。

### 4.5 rembg / BiRefNet

rembg: [`v2.0.81`](https://github.com/danielgatis/rembg/releases/tag/v2.0.81)、commit [`b439167`](https://github.com/danielgatis/rembg/tree/b439167d2eb22e51e7ec0732efe771bf920ff5c1)

```bash
python3.11 -m venv .venv-rembg
. .venv-rembg/bin/activate
python -m pip install "rembg[cpu,cli]==2.0.81"
export REMBG_HOME="$PWD/.cache/rembg"
rembg d birefnet-general-lite
rembg s --host 127.0.0.1 --port 7000 --log_level warning --threads 1 --no-ui
```

Python FastAPIからだけ次を呼ぶ。

```text
POST http://127.0.0.1:7000/api/remove
multipart:
  file=<front image>
  model=birefnet-general-lite
  om=true
response: image/png mask
```

`model`は毎回明示する。省略時の既定`bria-rmbg`は今回のモデル・ライセンス条件と異なるため使用しない。

BiRefNetは単独repoやPyTorchコードを導入せず、rembgの`BiRefNetSessionGeneralLite`と約214MiBのONNX重みだけを使用する。サーバー起動後、本番と同じformでfixture画像を1回POSTしてsessionまでprewarmする。

API側は35秒timeout、PNG、元画像との寸法一致、空／全面maskを検証する。自動retryはせず、手動retryか元画像採用へ戻す。

### 4.6 不採用OSS

| OSS | 不採用理由 | 代替 |
|---|---|---|
| react-konva | 固定合成だけにscene graphと追加依存は不要 | native Canvas 2Dの`drawImage`、`destination-in`、`toBlob` |
| XState | 直列の6状態程度には導入・学習コストが過剰 | 型付き`useReducer`＋純粋な遷移表 |
| GarmentIQ | PyTorchと複数モデルが必要で、1日MVPの2項目だけには過剰。物理scaleも別途必要 | 撮影後画像AIの4端点＋50mmマーカー＋OpenCV.js＋利用者補正 |

## 5. ライブ撮影パイプライン

ライブ助言は、低遅延な端末内品質判定と、衣類を理解するAgent側意味判定の2層に分ける。

> **設計原則:** リアルタイム性は前処理とアーキテクチャで作り、意味判断の精度だけをAIモデルへ依存させる。

| 層 | 担当すること | AIモデル依存 | 実行頻度 |
|---|---|---|---|
| LiveKit | WebRTC映像transport、Agent lifecycle、結果push | なし | 常時接続 |
| 端末内数値前処理 | 明るさ、ブレ、動き、静止状態 | なし | 4Hz |
| Agent frame selector | 最新フレーム保持、sampling、backpressure、古いframe破棄 | なし | frame到着ごと |
| `VisionGuidanceProvider` | 衣類の収まり、距離、表裏、タグ移動の意味判定 | あり | 最大1〜2fps |
| `GuidanceStateMachine` | 有限コード化、固定文言への変換、dedupe、sequence、expiry | なし | 判定結果ごと |
| `ShotAssessor` | 高解像度撮影画像の最終受理判定 | あり | シャッター後1回 |

したがって、LiveKitや前処理が衣類の意味を理解するわけではなく、AIモデルが30fpsの動画を直接監視するわけでもない。映像trackはAgentへ常時届くが、画像AIへ渡すのはselectorが選んだ最新フレームだけである。

```text
camera MediaStream
  ├─ browser local loop (4Hz)
  │    → fixed ROI → luma / blur / frame delta
  │    → TOO_DARK / TOO_BLURRY / HOLD_STEADY / READY
  │
  └─ LiveKit WebRTC video track
       → Agent VideoStream frame event
       → latest-frame slot (capacity 1)
       → FrameSelector (shot change / motion settled / sampling limit)
       → SemanticGuidanceProcessor (max 1 in-flight)
       → video-capable AI provider
       → GuidanceStateMachine
       → LiveKit data packet / RPC
       → React AR overlay
```

AIを呼ぶ条件は、現在shotの変更、映像が規定時間静止した直後、または前回判定から規定時間が経過した場合とする。条件を満たしても推論中なら呼び出しを追加せず、完了後の最新フレームを使う。

画像AIへ渡す入力と、画像AIから受ける生の判定はprovider共通契約に固定する。

```ts
type GuidanceCode =
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

type GuidanceInput = {
  frame: EncodedImage; // selectorが選んだ縮小済み最新フレーム1枚
  requestedShot: "front" | "back" | "tag" | "measurement";
  previousCode?: GuidanceCode;
};

type VisionDecision = {
  code: GuidanceCode;
  confidence: number;
};

interface VisionGuidanceProvider {
  analyze(input: GuidanceInput): Promise<VisionDecision>;
}
```

AIへ自由文のUIメッセージや画面遷移を決めさせない。providerは有限な`code`と`confidence`だけを返し、runtime validation後に`GuidanceStateMachine`が固定文言、順序、期限を付けて`GuidanceEvent`へ変換する。本番live経路は撮影セッションごとにprewarmしたOpenAI Realtime WebSocketを1本だけ維持し、各frameを`conversation: none`の独立responseとして送る。強制function schemaのargumentsまたは有限JSON textだけを受理し、過去frameやresponseをmodel contextへ蓄積しない。この境界によりRealtime modelをUI変更なしで交換できる。

端末内loopは、`object-fit`を考慮した固定ROIを最大辺320pxへ縮小する。初期閾値はaverage luma 45〜215、Laplacian variance 24以上、normalized frame delta 0.020未満が600ms継続とする。通常4Hz、同時解析1件とし、状態変化から表示までp95 500ms以内を目標にする。

Agent側はframe到着イベントで駆動し、最大4Hzで意味判定候補を選ぶ。これはブラウザからのHTTP pollingではなく、WebRTC trackをAgentが継続購読し、同時response 1件・待機frame 1件で最新frameをcoalesceするbackpressureである。処理中の中間frameは保存せず、完了時点の最新frameから次を開始する。Realtime入力は最大辺256px、JPEG quality 55、`detail: low`をproduction defaultとする。

```ts
type GuidanceEvent = {
  sessionId: string;
  sequence: number;
  shot: "front" | "back" | "tag" | "measurement";
  code: GuidanceCode;
  message: string;
  confidence: number;
  observedAt: number;
  expiresAt: number;
};
```

- 短命な助言はlossy packetで送り、同一`shot`／`code`は変化時だけ送る。
- shot変更、撮影受理、現在状態の再同期はreliable packetまたはRPCで扱う。
- frontendは`sessionId`不一致、現在shot不一致、既読以下の`sequence`、`expiresAt`超過を破棄する。
- AI意味判定はcamera frame受付前にRealtime sessionをprewarmし、実`OPENAI_API_KEY`で成功20件以上、provider error 0件、`observedAt`からbackend publishまでp95 1秒未満を必須ゲートとする。provider response deadlineは900ms、transport deadlineは950msとし、超過responseだけをcancelしてwarm socketを維持する。cold-startは別計測する。
- Agent不在時も固定ガイド、端末内品質判定、手動撮影は残す。
- ライブ結果は助言のみ。front／back／tagの受理は撮影後の高解像度`ShotAssessment`、measurementの受理はマーカー・全体写り・品質検証だけが決める。

## 6. 採寸ワークフロー

MVPの採寸対象は、平置きの半袖クルーネックTシャツ1種類に限定する。固定順序と完了条件は次のとおり。

```text
1/4 正面を撮影・受理
→ 2/4 背面を撮影・受理
→ 3/4 タグを撮影・受理
→ 採寸準備（置き方と50mmマーカーを案内）
→ 4/4 採寸を専用写真1枚で撮影
→ マーカー・全体写り・品質を検証
→ 射影補正・px/cm換算・初期測定線を計算
→ 着丈／身幅の4端点を利用者が確認・修正
→ 明示承認
→ 背景編集を解放
```

採寸写真は出品画像ではなく解析専用である。Tシャツを平置きして背面を上にし、襟、袖、裾を広げてシワを伸ばし、無地で服とコントラストがある面へ置く。専用マーカーは外形50.0mm角、5mm幅の黒い外枠、40.0mm角の白地からなる二重正方形とし、100%印刷後に定規で外形1辺を確認する。服と同じ平面の右下へ30mm以上離して配置する。真上から服全体とマーカーが同時に入る1枚を撮る。同じ平面の1枚からscaleを得られるため、着丈と身幅を別々に撮影しない。

`MeasurementProcessor`はOpenCV.js Worker内で次を行う。

1. grayscale／threshold／Cannyで候補を抽出する。
2. contourを四角形近似し、外形と内形が入れ子になった50mm専用マーカー候補を検証する。
3. マーカー四隅からhomographyを求め、撮影面を射影補正する。
4. 補正後のマーカー1辺を5.0cmとしてpx/cmを得る。
5. マーカーを除いた最大輪郭から服領域を得る。
6. 補正済み写真を`MeasurementPointSuggester`へ1回送り、背面襟ぐり中央→裾中央と左右脇下間の4端点を0〜1の正規化座標で得る。
7. 4端点を補正面へ写像し、着丈と身幅をcmへ換算する。

6は**初期提案**であり、画像AIへcm値を決めさせない。提案失敗時は輪郭上の粗いドラフトまたは利用者の端点配置へ切り替える。`MeasurementReview`が4端点を表示し、ドラッグのたびに0.1cm単位で再計算する。初期状態は未承認とする。手入力でも衣類全体が写った4枚目は必須とし、`approved_cv`と`approved_manual`を区別する。

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

初期検出条件は、マーカー最短辺80px以上、全四隅が画像端から16px超、最短辺／最長辺0.65以上、衣類との画像上の間隔24px以上とする。失敗コードは`MARKER_MISSING|MARKER_MULTIPLE|MARKER_TOO_SMALL|MARKER_OCCLUDED|GARMENT_OUT_OF_FRAME|GARMENT_MARKER_OVERLAP|SEGMENTATION_FAILED|ENDPOINTS_INVALID`に限定する。着丈20〜100cm、身幅20〜80cmの範囲外は警告するが、再確認後は承認可能とする。

デモの受入目標は、代表Tシャツで利用者が端点を補正・承認した値がメジャー実測に対して着丈・身幅とも±1.0cm以内であること。自動ドラフト自体の誤差は成功条件にせず、「ドラフト→補正→承認」または手入力で必ず完走できることを必須とする。

## 7. API契約

### `POST /api/livekit-token`

入力: 生成済みの`sessionId`。出力: participant identity、Room名、有効期限の短いaccess token、LiveKit URL。tokenはcamera publishと必要なdata通信だけを許可し、LiveKit API secretはPython backend内に保持する。

ライブ映像解析用の`POST /api/analyze-live`は作らない。映像はWebRTC track、助言はLiveKit data channelで交換する。

### `POST /api/analyze-shot`

入力: multipart画像、`requestedShot`。

```json
{
  "shotType": "front",
  "quality": "ok",
  "issues": [],
  "missingShots": ["back", "tag"],
  "nextAction": "REQUEST_NEXT"
}
```

Responses APIの画像入力とstrict JSON Schemaを使い、返却後もruntime schemaで検証する。AIの`nextAction`をそのまま実行せず、Reducerが受け入れ済みslotから再計算する。

### `POST /api/generate-background`

入力: 許可されたstyle ID。サーバーが「空の撮影背景、真上視点、人物・衣類・文字・ロゴなし」の固定promptへ変換し、Images APIへ**テキストだけ**を送る。

### `POST /api/remove-background`

入力: `front`原本。Python FastAPIがrembgへ転送し、mask-only PNGを返す。rembgポートはブラウザから直接呼ばない。

`POST /api/analyze-shot`の`requestedShot`は`front|back|tag`に限定する。採寸の幾何検証はブラウザ内のOpenCV.js Workerへ分離する。

### `POST /api/suggest-measurement-points`

入力は射影補正済みの採寸写真1枚。出力は`lengthStart`、`lengthEnd`、`widthStart`、`widthEnd`の0〜1正規化座標とconfidenceだけを持つstrict schemaとし、cm値、UI文言、画面遷移を返さない。採寸写真を画像AIへ送るのは撮影後のこの1回だけで、連続映像は送らない。

## 8. 画像生成・合成

```text
style text → Images API → background pixels

front original → rembg → mask
front original RGB + mask → transparent foreground

background + transparent foreground
  → Canvas preview
  → compare
  → explicit approval
  → toBlob
```

- 商品画像をImages APIへ送らない。
- 商品領域のRGBは元画像からだけ取得する。
- 合成は`drawImage`と`destination-in`で行う。
- 出力は元画像の縦横比を維持し、モバイル用に最大辺を制限する。

## 9. 状態と障害時の動作

```text
CONNECTING_LIVE → CAPTURE(front) ⇄ LIVE_GUIDANCE
→ ANALYZE_SHOT → RETAKE|ACCEPT
→ CAPTURE(back) → ANALYZE
→ CAPTURE(tag) → ANALYZE
→ MEASUREMENT_PREP → CAPTURE(measurement)
→ MEASURING → MEASUREMENT_REVIEW → APPROVE_MEASUREMENT
→ READY_TO_EDIT → MASKING + BACKGROUND_GENERATION
→ PREVIEW → APPROVAL → DONE
```

- `useReducer`が4つの画像slot、`MeasurementDraft`、採寸承認状態を保持する。
- LiveKit接続状態は`connecting|connected|reconnecting|disconnected`として撮影状態と分離し、再接続中もslotを消さない。
- 再接続後、frontendは現在shotと最後に処理した`sequence`をreliable RPCでAgentへ送り、Agentの現在状態と同期する。
- Agentのsession stateはRoom内だけに保持し、frame、guidance、画像をDBへ保存しない。
- analyze 20秒、rembg 35秒、背景生成60秒を初期timeoutとする。
- rembg失敗時は原本採用、背景生成失敗時は固定背景へ戻れる。
- `PROVIDER_MODE=fixture|live`を明示し、自動的に成功fixtureへ切り替えない。

## 10. ライセンスと出典

- LiveKit Agents、LiveKit JS SDK、LiveKit serverはApache-2.0。実装時に固定versionとNOTICE要否を記録する。
- Wardrobeは設計参考のみ。コードをコピーする場合のみMIT notice追加が必要になる。
- document-autocaptureの関数を移植するため、実装時に`THIRD_PARTY_NOTICES.md`へMIT全文、`Copyright (c) 2026 Maaz Khan`、commitを記載し、移植ファイルへ出典コメントを入れる。
- OpenCV.jsはApache-2.0。固定version、公式配布URL、checksum、NOTICE要否を記録する。
- rembgコードはMIT。BiRefNet重みはrembg本体と別成果物なので、モデル名、source、確認日、checksumを記録する。
- 使用しないモデルやML assetはダウンロードしない。

## 11. デモ前チェック

1. Python 3.11とrembg v2.0.81を固定する。
2. `birefnet-general-lite`を事前downloadする。
3. 50mmマーカーを100%で印刷し、実物の1辺が50mmであることを定規で確認する。
4. OpenCV.js／WASMを事前loadし、採寸fixtureでマーカー検出とpx/cm換算を確認する。
5. fixture frontをmask-onlyで1回処理し、model sessionをwarmにする。
6. LiveKit URL／API key／secretとAI provider、rembg疎通を`/api/health`で確認する。
7. 基準端末からRoomへ接続し、camera track publish、Agent subscribe、`GuidanceEvent` pushを確認する。
8. 代表Tシャツで補正・承認後の着丈・身幅がメジャー実測±1.0cm以内であることを確認する。
9. 切断・再接続、期限切れevent破棄、Agent停止時の端末内fallbackを確認する。
10. liveとfixtureの両方で最初から画像保存まで通す。
