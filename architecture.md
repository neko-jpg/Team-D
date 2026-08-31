# Mercari AI Agent Hackathon Webアーキテクチャ

最終更新: 2026-08-31

## 1. 結論

Wardrobe等を土台としてforkせず、小さなReactモバイルWebを新規実装する。

- Wardrobeは設計参考のみ
- document-autocaptureはMIT表記を保持して必要関数だけ移植
- rembg v2.0.81をローカルHTTP sidecarとして実行
- BiRefNet General Liteをrembg経由で使用
- 合成はnative Canvas 2D、状態管理は`useReducer`

コード参照先は調査時のcommitへ固定する。

## 2. 全体構成

```text
Mobile browser
  ├─ CameraController
  ├─ FixedGuideOverlay
  ├─ LiveQualityAnalyzer (4Hz local only)
  ├─ CaptureReducer
  └─ CanvasCompositor
          │ HTTPS /api
          ▼
Node.js API
  ├─ ShotAssessor ──────→ Responses API
  ├─ BackgroundGenerator → Images API (text only)
  └─ GarmentMasker ─────→ rembg HTTP / BiRefNet
```

ブラウザからはViteのURLだけを公開し、`/api`をNode.jsへproxyする。OpenAI APIキーとrembgポートは外部公開しない。

## 3. 自作する責務

| モジュール | 責務 |
|---|---|
| `CameraController` | 背面カメラ、権限、video再生、track解放、raw撮影 |
| `LiveQualityAnalyzer` | 固定ROI、輝度、Laplacianブレ、フレーム差分、主案内 |
| `CaptureReducer` | 正面→背面→タグ、撮り直し、受け入れ済み画像の保持 |
| `ShotAssessor` | 画像AI入力、strict schema、runtime validation |
| `BackgroundGenerator` | 商品を含まない背景のtext-to-image生成 |
| `GarmentMasker` | rembgへのmultipart接続、マスク検証、timeout |
| `CanvasCompositor` | 背景＋元商品RGB＋maskの合成、比較、出力 |

## 4. OSS利用境界

### 4.1 Wardrobe

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

### 4.2 document-autocapture

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

- OpenCV／ML／COCOの書類検出
- 書類四隅、glare、area、perspective warp
- document guidance、自動撮影readiness、Quad overlay

公開SDKは品質primitiveをexportせず、品質処理が書類検出成功に依存するため、パッケージ全体は採用しない。

### 4.3 rembg / BiRefNet

rembg: [`v2.0.81`](https://github.com/danielgatis/rembg/releases/tag/v2.0.81)、commit [`b439167`](https://github.com/danielgatis/rembg/tree/b439167d2eb22e51e7ec0732efe771bf920ff5c1)

```bash
python3.11 -m venv .venv-rembg
. .venv-rembg/bin/activate
python -m pip install "rembg[cpu,cli]==2.0.81"
export REMBG_HOME="$PWD/.cache/rembg"
rembg d birefnet-general-lite
rembg s --host 127.0.0.1 --port 7000 --log_level warning --threads 1 --no-ui
```

Node.jsからだけ次を呼ぶ。

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

Node側は35秒timeout、PNG、元画像との寸法一致、空／全面maskを検証する。自動retryはせず、手動retryか元画像採用へ戻す。

### 4.4 不採用OSS

| OSS | 不採用理由 | 代替 |
|---|---|---|
| react-konva | 固定合成だけにscene graphと追加依存は不要 | native Canvas 2Dの`drawImage`、`destination-in`、`toBlob` |
| XState | 直列の6状態程度には導入・学習コストが過剰 | 型付き`useReducer`＋純粋な遷移表 |
| GarmentIQ | PyTorchと複数モデルが必要で、pixel距離をcmへ変換できない | 採寸を1日MVPから除外 |

## 5. ライブ撮影パイプライン

```text
video frame
  → object-fitを考慮して固定ガイドをPixelRoiへ変換
  → ROIを最大辺320pxへ縮小
  → grayscale
  ├─ average luma: dark < 45 / bright > 215
  ├─ Laplacian variance: blurry < 24
  └─ normalized frame delta: stable < 0.020 for 600ms
  → primary hint
```

- 通常4Hz、同時解析は1件。処理中の中間フレームを蓄積しない。
- 問題優先度は、解析エラー → 明るさ → ブレ → 静止 → READY。
- 閾値はデモ端末とサンプル衣類で調整できる定数にする。
- ライブ結果は助言のみ。写真の受理は撮影後`ShotAssessment`だけが決める。

## 6. API契約

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

入力: `front`原本。Node.jsがrembgへ転送し、mask-only PNGを返す。rembgポートはブラウザから直接呼ばない。

## 7. 画像生成・合成

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

## 8. 状態と障害時の動作

```text
CAPTURE(front) → ANALYZE → RETAKE|ACCEPT
→ CAPTURE(back) → ANALYZE
→ CAPTURE(tag) → ANALYZE
→ READY_TO_EDIT → MASKING + BACKGROUND_GENERATION
→ PREVIEW → APPROVAL → DONE
```

- `useReducer`が画像slotと状態を保持する。
- analyze 20秒、rembg 35秒、背景生成60秒を初期timeoutとする。
- rembg失敗時は原本採用、背景生成失敗時は固定背景へ戻れる。
- `PROVIDER_MODE=fixture|live`を明示し、自動的に成功fixtureへ切り替えない。

## 9. ライセンスと出典

- Wardrobeは設計参考のみ。コードをコピーする場合のみMIT notice追加が必要になる。
- document-autocaptureの関数を移植するため、実装時に`THIRD_PARTY_NOTICES.md`へMIT全文、`Copyright (c) 2026 Maaz Khan`、commitを記載し、移植ファイルへ出典コメントを入れる。
- rembgコードはMIT。BiRefNet重みはrembg本体と別成果物なので、モデル名、source、確認日、checksumを記録する。
- 使用しないモデルやML assetはダウンロードしない。

## 10. デモ前チェック

1. Python 3.11とrembg v2.0.81を固定する。
2. `birefnet-general-lite`を事前downloadする。
3. fixture frontをmask-onlyで1回処理し、model sessionをwarmにする。
4. API key、AI疎通、rembg疎通を`/api/health`で確認する。
5. ngrok HTTPSから基準端末のカメラ、4Hz解析、raw撮影を確認する。
6. liveとfixtureの両方で最初から画像保存まで通す。
