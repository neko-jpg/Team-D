## Context

既存リポジトリには企画資料と静的モックだけがあり、アプリ、API、依存、テスト基盤は存在しない。1日で、平置きトップス1着について、撮影中の助言、正面・背面・タグの撮影後確認、正面の背景生成・合成、承認までを一本のデモにする。

利用者から見える振る舞いは`specs/`、OSSのcommit・関数単位の採用境界はリポジトリルートの`architecture.md`を正とする。

## Goals / Non-Goals

**Goals:**

- 固定2Dガイドと端末内ライブ品質助言を表示する。
- 撮影後AIのstrictな結果から正面、背面、タグを揃える。
- 商品を含まない背景だけを生成し、正面原本の商品RGBを保持して合成する。
- live providerと決定的fixtureの両方で完走できる。
- 秘密値、rembgポート、処理画像を不用意に公開・永続化しない。

**Non-Goals:**

- WebXR、ARKit、ARCore、平面検出、空間アンカー、衣類輪郭追跡。
- ライブ映像の意味判定、連続クラウド送信、自動撮影。
- 採寸、価格推定、Mercari API連携。
- 商品再生成、人物着用生成、人物認識、商品レタッチ。
- ユーザー認証、DB、永続ジョブ、本番監視、全端末対応。

## Decisions

### 1. OSSは役割単位で採用し、アプリ全体をforkしない

| OSS | 決定 |
|---|---|
| Wardrobe | `normalizeImage`、Responses strict schema、review／approveの処理パターンだけ参考にし、コードコピーしない |
| document-autocapture | カメラ制御、グレースケール、輝度、Laplacian分散、raw撮影を限定移植する |
| rembg | v2.0.81のHTTP sidecarをloopbackで実行する |
| BiRefNet | `birefnet-general-lite`をrembg経由でのみ使用する |
| react-konva | 導入せず、native Canvas 2Dを使う |
| XState | 導入せず、型付き`useReducer`を使う |
| GarmentIQ | 採寸とともに除外する |

詳細なファイル、関数、除外箇所、ライセンスは`architecture.md`の「OSS利用境界」に集約する。

**代替案:** Wardrobeまたはdocument-autocaptureを丸ごと導入すると、商品再生成、人物生成、書類検出、perspective warp、永続jobを外す作業が増えるため採用しない。

### 2. ライブ判定と撮影後判定を分離する

`LiveCaptureAssessment`は固定ROIから端末内で作り、撮影前の助言だけに使う。

```ts
type LiveHint =
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
- ライブ結果が`READY`でなくても手動撮影を許可する。

`ShotAssessment`は撮影後に画像AIから取得し、写真の受理可否だけに使う。

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

### 3. 外部サービスはNode.jsのprovider境界に閉じる

- `ShotAssessor`: Responses APIへ撮影画像と指示を送り、strict schemaとruntime schemaで検証する。
- `BackgroundGenerator`: 許可されたstyle IDを固定promptへ変換し、Images APIへテキストだけを送る。
- `GarmentMasker`: rembg `/api/remove`へ`file`、`model=birefnet-general-lite`、`om=true`を送り、PNG maskを検証する。

各providerはfixture実装を持つ。`PROVIDER_MODE`で明示的に切り替え、live失敗を自動成功へ変換しない。

**代替案:** ブラウザから外部APIを直接呼ぶ構成は、秘密値、CORS、rembgのCORS `*`、端末差分を制御できないため採用しない。

### 4. 商品を含まない背景だけを生成する

背景生成APIへ商品画像を渡さない。生成promptは「空の撮影背景、真上視点、均一照明、人物・衣類・ハンガー・文字・ロゴなし」に限定し、失敗時は同梱の固定背景を使う。

正面原本はrembgへ送り、mask-onlyを取得する。商品前景は元画像RGBへmaskを適用して作る。

```text
background text → Images API → background
front original → rembg → mask
front original RGB × mask → foreground
background + foreground → Canvas preview → approval → output
```

合成はnative Canvas 2Dの`drawImage`、`destination-in`、`toBlob`で行う。位置調整を要件に含めないためreact-konvaは使わない。

### 5. 直列状態は型付きReducerで管理する

```text
CAPTURE(front) → ANALYZING → RETAKE|ACCEPT
→ CAPTURE(back) → ANALYZING
→ CAPTURE(tag) → ANALYZING
→ READY_TO_EDIT → MASKING + GENERATING_BACKGROUND
→ PREVIEW → APPROVAL → DONE
```

各slotは原本Blob、object URL、判定結果を持つ。失敗時は現在stepと受け入れ済みslotを変更しない。終了時にobject URLを解放する。

### 6. デモは最初にfixtureで縦スライスを通す

開始時にfront／back／tag、撮り直し、誤種別、AIエラー、mask、固定背景のfixtureを用意する。uploadで全体を通してからカメラ、live AI、rembg、背景生成を順に接続する。

rembgはPython 3.11、v2.0.81、`birefnet-general-lite`を固定し、デモ前にモデルをdownloadしてfixture frontを1回処理する。初期timeoutはanalyze 20秒、rembg 35秒、背景生成60秒とする。

## Risks / Trade-offs

- **[ライブ品質判定が無地衣類で不安定]** → 助言に限定し、手動撮影と撮影後AIを優先する。
- **[object-fitでROIがずれる]** → 表示座標から映像座標への変換を純粋関数としてテストする。
- **[外部AIまたはrembgが遅い]** → timeout、明示的retry、固定背景、原本採用、fixtureを用意する。
- **[maskが袖や裾を欠く]** → 空／全面／寸法を検査し、承認前比較と元画像採用を必須にする。
- **[OSSライセンスを落とす]** → document-autocapture限定移植時にMIT全文、著作権、commit、出典コメントを追加する。
- **[1日で過剰になる]** → 背景処理は正面1枚だけ。人物生成、採寸、自動撮影、位置調整を追加しない。

## Migration Plan

既存実装や永続データはないため移行は発生しない。

1. fixture、Reducer、uploadで3枚の撮影ループを完成させる。
2. カメラ、固定ガイド、端末内ライブ判定を接続する。
3. liveの`ShotAssessor`を接続する。
4. rembgをprewarmし、正面maskを接続する。
5. 背景生成、Canvas合成、比較、承認、保存を接続する。
6. 基準端末とfixtureで垂直スライスを確認する。

ロールバックはlive providerを停止し、明示的なfixtureモードへ切り替える。撮影済み進捗を黙って成功扱いにはしない。

## 11. Capture core の最小縦スライス契約

1.1 の対象は、カメラや外部AIの実装ではなく、upload fixture だけで撮影開始から編集入口までを再現できる責務境界である。各責務は次の境界を越えて状態を直接変更しない。

| 責務 | 入力 | 出力・権限 |
|---|---|---|
| `CaptureReducer` | 型付き action と現在 state | 現在 step、受け入れ済み slot、表示用エラーを更新する唯一の状態遷移点 |
| `ShotAssessor` | `Blob` と要求中の `front`／`back`／`tag` | strict な `ShotAssessment` または `ProviderError`。slot を直接更新しない |
| `FixtureShotAssessor` | fixture id と要求中 slot | live provider と同じ `ShotAssessment` 契約。失敗を成功 fixture へ変換しない |
| `UploadCapture` | file input の `File` | raw Blob と object URL を reducer へ渡す。ガイドや UI を画像へ描画しない |
| `EditGate` | reducer state | 3 slot が `quality: ok` のときだけ `READY_TO_EDIT` を返す |
| UI | state と dispatch | 現在 step、残り slot、retry 理由、編集開始を表示する。provider の自由文で遷移しない |

API 境界は `ShotAssessor.assess(input): Promise<ShotAssessment>` とし、live 実装は後から同じ interface に差し替える。HTTP を使う場合も browser は `/api/analyze-shot` だけを呼び、API key や rembg endpoint は client module に公開しない。fixture モードは明示的な `PROVIDER_MODE=fixture` 相当の設定で選択する。

最小縦スライスの完了条件は次のとおりとする。

1. 新規 session は `front` から始まり、`back`、`tag` の順に進む。
2. `front`、`back`、`tag` の各 slot は raw Blob、object URL、受け入れ済み assessment を保持する。
3. 要求中 slot と `shotType` が一致し、`quality: ok` の結果だけを受け入れる。不一致、`retry`、`unknown` は対象 slot を置き換えず同じ step に留まる。
4. 撮り直しは対象 slot のみを差し替え、他の受け入れ済み slot と進捗を保持する。
5. provider error は進捗を変更せず、同じ画像の retry または撮り直しを提示する。
6. 3 slot が揃うまで編集入口を表示せず、揃ったときだけ `READY_TO_EDIT` へ進める。

## 12. ライブ解析の責務と判定方針

2.1 では `LiveCaptureAssessment` を撮影前の助言専用とする。ライブフレームは端末内の固定 ROI だけで解析し、撮影後の受理は必ず `ShotAssessment` が行う。通常の解析周期は 4Hz（250ms 間隔）、同時実行は 1 件、解析中に到着したフレームは最新 1 件だけを保持する。古い中間フレームを queue に積まない。

判定は次の閾値を初期値とし、優先順位の高い hint を 1 つだけ表示する。

1. analyzer 例外・Canvas/Worker 不可なら `ANALYZER_UNAVAILABLE`
2. 平均輝度が 45 未満なら `TOO_DARK`
3. 平均輝度が 215 超なら `TOO_BRIGHT`
4. Laplacian 分散が 24 未満なら `TOO_BLURRY`
5. normalized frame difference が 0.020 以上なら `HOLD_STEADY` かつ安定履歴を reset
6. 上記を満たし、frame difference が 0.020 未満の状態が 600ms 以上継続したら `READY`

`READY` は撮影可能の目安であり、カメラが利用可能な限り manual shutter は常に有効とする。ライブ判定が遅い、失敗する、または `READY` でないことだけを理由に raw 撮影を禁止しない。

## 13. Fixed guide から video PixelRoi への契約

2.4 の入力と出力は CSS/表示座標と video の intrinsic pixel を混在させない。

```ts
type NormalizedGuideRect = { x: number; y: number; width: number; height: number };
type VideoRoiInput = {
  guide: NormalizedGuideRect;       // 表示領域内の 0..1 比率
  display: { width: number; height: number }; // CSS pixel
  video: { width: number; height: number };   // intrinsic pixel
  objectFit: "cover" | "contain";
};
type PixelRoi = { x: number; y: number; width: number; height: number };
```

`guide` は表示矩形の左上を原点とし、正規化後に 0..1 へ clamp する。`object-fit: cover` では `scale = max(display.width/video.width, display.height/video.height)`、`rendered = video * scale`、`offset = (display - rendered) / 2` を求め、`(displayPoint - offset) / scale` で video pixel へ戻す。`contain` は同じ式で `scale = min(...)` とする。rendered video の外側にある guide 部分は切り捨て、最終矩形は video bounds 内へ clamp する。幅または高さが 1 pixel 未満なら解析対象外として扱う。

video の intrinsic 寸法、表示寸法、guide、object-fit のいずれかが変わったら ROI と安定履歴を再計算・reset する。解析に渡す ROI は最大辺 320px 以下へ縮小する。

## 14. 画像品質 primitive の採用基準

2.6 では document-autocapture の実装パターンだけを限定移植し、書類検出・Quad・perspective warp には依存しない。

- `rgbaToGrayscale`: ROI の RGBA を輝度配列へ変換する端末内 primitive。alpha は RGB の欠落を成功扱いするために使わず、opaque な RGB の輝度を計算する。
- `brightnessCheck`: grayscale の平均値を返し、`<45` を暗い、`>215` を明るい、それ以外を許容とする。
- `laplacianVariance`: 隣接 pixel の二次差分分散を返し、`<24` を blurry とする。これは意味理解ではなく撮影品質の助言だけに使う。
- frame difference: 同一サイズの連続 grayscale ROI に対する normalized mean absolute difference。`<0.020` を安定、以上を移動とする。

閾値はコード上の設定値として差し替え可能にするが、閾値を超えたからといって shutter や撮影後判定を自動で抑止しない。Worker/Canvas 実装が利用できない場合は品質判定を `ANALYZER_UNAVAILABLE` に倒し、固定ガイドと手動撮影を残す。

## 15. StabilityTracker の方式

2.8 は Quad の角点移動量ではなく、同一 PixelRoi の連続 grayscale frame difference を使う。最初の frame は基準として保存するだけで `stable` にはならない。次 frame の normalized difference が閾値未満なら `stableSince` を維持し、閾値以上なら基準 frame と `stableSince` をその frame で置き換える。`now - stableSince >= 600ms` で安定と判定する。

ROI の pixel 数、video 寸法、表示→video 変換が変わったときは履歴を破棄する。解析処理中の frame drop は tracker の時間を進めず、最新 frame の解析が完了した時刻でのみ履歴を更新する。

## 16. Fallback 契約

2.11 の fallback は成功を偽装せず、ユーザーが同じ capture state を継続できることを目的とする。

- Worker または Canvas 解析不可: `ANALYZER_UNAVAILABLE` と固定 front/back/tag guide を表示し、manual shutter を有効にする。raw Blob には overlay を描画しない。
- カメラ権限拒否、非対応、stream 起動失敗: file input を表示し、upload された画像を manual capture と同じ `ShotAssessor`／reducer 経路へ送る。
- analyzer/provider の timeout/error: 現在 step と受け入れ済み slot を維持し、同じ画像の retry または撮り直しを選ばせる。
- fixture/live の切替: 明示設定だけで行い、live の失敗を fixture の成功結果へ自動変換しない。

この切り替えにより、最小縦スライスでは装飾的なカメラ UI より upload fixture の完走を優先する。T+2h 判定では upload の開始→3 slot 受理→`READY_TO_EDIT` が通るかを確認し、通らない場合はカメラ以外の UI 装飾とライブの見た目を停止して、fixture 経路の修復へ集中する。

## 17. T+2h スコープゲートの判定

1.6 の判定として、fixture upload は `front` → `back` → `tag` の受理、撮り直し時の別 slot 保持、provider error 後の進捗維持、3 枚完了後だけの edit gate を自動テストで完走した。したがって、今回のゲートでは追加の削減は不要とする。ただし、カメラ・ライブ品質の見た目や装飾をこの縦スライスの完了条件へ広げず、以降も fixture 経路をデモの基準線として保持する。
