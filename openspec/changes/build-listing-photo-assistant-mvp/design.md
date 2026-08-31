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
