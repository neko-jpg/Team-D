# React implementation guide

React で撮影体験を実装・改修・レビューするときに読む。ビジュアルの値や文言は他の参照資料を正とし、この資料では実装境界、状態、座標、検証方法を定める。

## 基本方針

- React + TypeScript + Vite を基準にする。既存プロジェクトへ適用するときは、依存関係を置き換える前に現在の構成と制約を確認する。
- スタイルは CSS Custom Properties のセマンティックトークンを正とし、Tailwind はそのトークンを参照するレイアウト／状態ユーティリティとして使う。色の数値や余白を JSX のクラスへ散在させない。
- 390 CSS px の iPhone 縦画面を設計基準にするが、固定幅にはしない。320px 程度から大型 iPhone まで破綻しない流動レイアウトにする。
- カメラが使えなくても UI を開発・確認できる `fixture-first` 構成にする。実 MediaStream は入出力アダプターの一つとして扱う。
- 品質判定のフレーム更新で React ツリーを再描画し続けない。映像・推論ループは ref／worker 側、意味のある UI 状態だけを reducer に渡す。

## UI 基盤の境界

MUI、Chakra UI など完成済みテーマを新規の基盤にしない。固有の色を上書きできるかではなく、タイポグラフィ、余白、角丸、状態表現、モーションまで別のデザイン言語が入り込み、Mercari × iOS × Guided Capture の調整面積が増えるためである。既に導入済みなら一括撤去せず、対象画面でテーマ依存を増やさない移行境界を作る。

Radix Primitives は、アクセシブルな挙動を再実装する価値がない箇所だけに使う。候補は `Dialog`、`AlertDialog`、`Popover`、`Tabs` などで、外観は必ずプロジェクトのトークンで作る。

次はカメラ固有 UI として独自コンポーネントにする。

- 撮影ガイド枠、検出輪郭、測定点、水平／距離ガイド
- リアルタイム指示、撮影準備リング、シャッター
- カメラ映像へ重なる進捗や品質フィードバック

これらに Radix の DOM 構造や状態モデルを流用しない。video 座標との同期、pointer-events、描画頻度、固定された操作位置をこちらで制御する必要がある。

## セマンティックトークン

値の定義元を一箇所に集め、コンポーネントは用途名だけを参照する。

```css
:root {
  --gc-color-bg: /* assets/capture-theme.css の値 */;
  --gc-color-surface: /* ... */;
  --gc-color-text-primary: /* ... */;
  --gc-color-text-on-camera: /* ... */;
  --gc-color-action-primary: /* ... */;
  --gc-color-feedback-info: /* ... */;
  --gc-color-feedback-success: /* ... */;
  --gc-color-feedback-danger: /* ... */;
  --gc-space-screen-inline: 16px;
  --gc-control-min-size: 44px;
  --gc-radius-panel: /* ... */;
  --gc-duration-state: /* ... */;
}
```

色名を用途へ混ぜない。たとえば `--red` ではなく `--gc-color-action-primary` とする。Tailwind 側は `bg-action-primary` のようなエイリアスへ接続し、任意値クラスはプロトタイプ以外で増やさない。ライトテーマの確認画面と暗いカメラ画面は、同じ役割のトークンをスコープで切り替える。

## コンポーネント境界

責務の目安は次の通り。画面固有の条件分岐を見た目の部品へ埋め込まない。

```text
CaptureRoute
├─ CaptureSessionProvider     permission、MediaStream、lifecycle
├─ CaptureScreen             reducer とサービスの調停
│  ├─ CaptureProgress        工程と現在地
│  ├─ CameraViewport         video と表示ジオメトリ
│  │  ├─ CameraVideo         video 要素のみ
│  │  └─ GuidanceOverlay     ガイド／輪郭／測定点
│  ├─ GuidanceMessage        選ばれた一つの行動
│  ├─ PositiveFeedback       改善済みの短い肯定
│  └─ CaptureControls        シャッター、戻る、ライト
└─ CaptureReview             採用、撮り直し、測定値の承認
```

`CameraViewport` は映像の表示矩形と座標変換を一体で公開する。`GuidanceOverlay` は通常 `pointer-events: none` とし、測定点を修正するモードだけ、ハンドル単位で入力を有効にする。video と overlay を別の DOM レイヤーにし、映像へガイドを焼き込まない。

`CameraVideo` は原則 `autoPlay muted playsInline` とする。衣類撮影は背面カメラを優先し、前面カメラ以外を鏡像化しない。unmount、再取得、ページ非表示、エラー復帰時に古い `MediaStreamTrack` を停止する。

## React 表示状態

ドメイン状態とイベントの正本は [guided-capture-states.md](guided-capture-states.md) に置き、同名の型をこの資料から別定義しない。Reactコンポーネントでは、ドメイン状態から導出したdiscriminated unionを表示用view modelとして使い、相互に矛盾するbooleanを避ける。

```ts
type CaptureView =
  | { type: 'requesting-permission' }
  | { type: 'guiding'; step: CaptureStep; advice: Advice | null }
  | { type: 'holding'; step: CaptureStep; since: number }
  | { type: 'ready'; step: CaptureStep }
  | { type: 'capturing'; step: CaptureStep; requestId: string }
  | { type: 'reviewing'; step: CaptureStep; asset: CapturedAsset }
  | { type: 'recoverable-error'; step: CaptureStep; reason: ErrorReason };

function selectCaptureView(state: CaptureState): CaptureView {
  // Domain state remains authoritative; this selector only shapes rendering.
}
```

- 推論値を直接表示せず、正規化した観測値から表示状態へ変換する selector を置く。
- 複数の問題は優先順位付けし、「次に行う一つ」だけを返す。距離、欠け、角度、明るさ、安定性などの競合規則は pure function にしてテストする。
- 閾値の前後で表示が点滅しないよう、時間デバウンスと入退出で異なる閾値（hysteresis）を使う。
- `ready` でなくても手動シャッターは残す。無効にしてよいのは、カメラ未接続や撮影処理中など物理的に実行できない場合だけで、品質評価を理由に強制ブロックしない。
- 切断や推論失敗を撮影済みアセットの消去へ結びつけない。再接続は session adapter の責務とする。
- 非同期結果には `requestId` を付け、古い撮影／推論／採寸結果を reducer が無視できるようにする。

## fixture-first と Storybook

カメラ、品質判定、採寸を interface の背後へ置く。

```ts
interface CaptureInput {
  start(): Promise<CaptureSource>;
  stop(): void;
  takePhoto(): Promise<CapturedAsset>;
}

interface GuidanceEngine {
  observe(frame: VideoFrameLike): Promise<GuidanceObservation>;
}
```

実装例は `BrowserCameraInput`、`StillImageFixtureInput`、`ScriptedGuidanceEngine`。Storybook は fixture を使い、少なくとも権限待ち、探索中、各指示、改善の肯定、静止待ち、ready、撮影中、レビュー、権限拒否、接続断、採寸補正、採寸承認を再現する。時刻、乱数、画像、非同期遅延を固定し、スクリーンショットが安定するようにする。

Story は装飾見本ではなく状態契約である。新しい reducer 状態を増やしたら、代表 Story と復帰経路を同時に追加する。

## iPhone と safe area

HTML に `viewport-fit=cover` を設定し、ルートは `100vh` 固定ではなく `min-height: 100dvh` を基準にする。上下の操作域には次のように safe area を加算する。

```css
.capture-header {
  padding-top: calc(12px + env(safe-area-inset-top));
}

.capture-controls {
  padding-bottom: calc(16px + env(safe-area-inset-bottom));
  padding-left: calc(var(--gc-space-screen-inline) + env(safe-area-inset-left));
  padding-right: calc(var(--gc-space-screen-inline) + env(safe-area-inset-right));
}
```

safe area の外まで映像は広げてよいが、戻る、進捗、シャッター、重要な指示は内側に置く。アドレスバーの伸縮で操作位置が跳ねないかを Safari 実機で確認する。390px は比較基準であり、Dynamic Island や端末枠を Web UI として描画しない。

## video と overlay の座標変換

検出座標、表示座標、保存画像座標を混在させない。次の座標系を型または命名で区別する。

- `source`: video／画像の元ピクセル
- `normalized`: 0..1
- `viewport`: `CameraViewport` 内の CSS px
- `canvas`: device pixel ratio を反映した backing pixels
- `asset`: 回転補正後の保存画像ピクセル

`object-fit: cover` で、元サイズを `Sw × Sh`、viewport を `Vw × Vh` とすると次で source から viewport へ移す。

```ts
const scale = Math.max(Vw / Sw, Vh / Sh);
const offsetX = (Vw - Sw * scale) / 2;
const offsetY = (Vh - Sh * scale) / 2;
const viewportX = sourceX * scale + offsetX;
const viewportY = sourceY * scale + offsetY;
```

`contain` の箇所では `Math.min` を使う。鏡像、90度回転、EXIF orientation はこの式の前段で明示的な transform として扱う。CSS transform の見た目だけを頼りに座標を合わせない。

Canvas の CSS サイズと backing size を分ける。

```ts
canvas.width = Math.round(cssWidth * devicePixelRatio);
canvas.height = Math.round(cssHeight * devicePixelRatio);
canvas.style.width = `${cssWidth}px`;
canvas.style.height = `${cssHeight}px`;
ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
```

変換は一つの geometry module に集約し、forward／inverse、cover crop、回転、鏡像、丸め誤差を単体テストする。overlay の ResizeObserver 結果と、実際に表示されている video の `videoWidth`／`videoHeight` を入力に使う。

## OpenCV.js による採寸

採寸は main thread で OpenCV.js を回さず、必要になった時点で dedicated Web Worker へ遅延ロードする。現行仕様の一辺 50mm の正方形マーカー、衣類輪郭、射影補正、px→cm 換算を worker の仕事とし、結果の確認と測定点のドラッグ修正は React 側に置く。別の基準物へ変える場合は先にプロダクト仕様を更新する。

worker の契約は型付きメッセージにする。

```ts
type MeasureRequest = {
  type: 'MEASURE';
  requestId: string;
  bitmap: ImageBitmap;
  marker: { kind: 'square'; edgeCm: 5 };
};

type MeasureResult = {
  type: 'MEASURED';
  requestId: string;
  markerQuad: NormalizedPoint[];
  garmentPoints: Record<string, NormalizedPoint>;
  valuesCm: Record<string, number>;
  reviewRequired: true;
};
```

- 可能なら `ImageBitmap` を transfer し、対応しない環境では縮小した `ImageData` を fallback にする。機能可否を user agent 文字列だけで決めない。
- `cv.Mat` など WASM 側オブジェクトは `try/finally` で必ず `delete()` する。worker 停止、連続撮影、再採寸でメモリが増え続けないことを確認する。
- UI を固めないための縮小は検出用だけに行い、最終座標は正規化して元画像へ戻す。表示用 crop から長さを算出しない。
- 透視補正後の着丈・身幅を候補値として返し、ユーザーが測定点と数値を確認・修正して明示的に承認する。confidence を合格スコアとして見せない。
- 新しい依頼が来たら古い `requestId` を無効化する。必要なら worker を terminate できる設計にし、キャンセル後の結果を画面へ反映しない。
- worker／OffscreenCanvas／WASM の読み込み失敗時は採寸だけを復旧可能エラーにし、既に撮った写真と通常撮影を保持する。

## アクセシビリティ

- タップ対象は最低 44 × 44 CSS px。アイコンだけのボタンには目的が分かる `aria-label` を付ける。
- 指示は色・輪郭・アニメーションだけで伝えず、短いテキストを併記する。成功、警告、ready の差を色覚だけへ依存させない。
- リアルタイムのフレームごとに live region を更新しない。優先指示が安定して変わったときだけ、画面内に一つある `aria-live="polite"` へ通知する。撮影失敗など即時対応が必要な場合だけ `assertive` を検討する。
- シャッターと戻るの位置、フォーカス順、ラベルを状態ごとに変えない。Dialog 系では Radix の focus trap／return focus を利用する。
- `prefers-reduced-motion` ではリングの脈動や収束アニメーションを停止し、静的な状態差を残す。
- ブラウザの振動や触覚を必須フィードバックにしない。使える場合も視覚・テキストの補助に留める。
- overlay がスクリーンリーダーへ検出輪郭の点群を読み上げないよう、装飾 Canvas／SVG を隠し、意味のある説明を別要素に置く。

## パフォーマンスと lifecycle

- 推論は `requestAnimationFrame` または時間間隔で間引き、同時実行を一件に制限する。React state をフレーム単位で更新しない。
- カメラ Canvas と採寸 Worker を用途別に分け、大きな画像コピーを常態化させない。blob URL、ImageBitmap、MediaStreamTrack、worker を終了時に解放する。
- `visibilitychange`、`pagehide`、復帰時の permission／track 状態を扱う。バックグラウンド復帰後に古い映像へ ready 表示を残さない。
- 低速端末で推論頻度を落としても、シャッター、戻る、指示表示の操作応答を優先する。

## テストと Visual QA

実装完了は単一の happy path ではなく、状態、座標、端末挙動で判断する。

1. reducer と selector
   - 全イベントと不正遷移、指示の優先順位、hysteresis、古い `requestId` の無視を単体テストする。
2. geometry
   - `cover`／`contain`、390px、狭幅、大型幅、縦横比違い、回転、鏡像、forward/inverse を数値テストする。
3. component
   - fixture を使って、手動シャッターが品質状態に阻害されないこと、エラー後も撮影済みデータが残ること、採寸承認前に確定しないことを確認する。
4. Storybook visual
   - 390 × 844 を基準に、375 × 812、430 × 932、文字拡大、dark camera／light review、長めの日本語、reduced motion を撮る。重なり、コントラスト、safe-area 代替 padding を見る。
5. ブラウザ E2E
   - mock MediaStream／fixture で権限、撮影、再撮影、切断、復帰、採寸 worker 失敗を自動化する。スクリーンショットの時刻と fixture は固定する。
6. iPhone 実機
   - Safari の初回権限、背面カメラ、`playsInline`、ホームインジケータ、アドレスバー伸縮、バックグラウンド復帰、低照度、回転ロック、連続撮影時の発熱／メモリを確認する。デスクトップのモバイルエミュレーションだけで完了扱いにしない。

レビューでは、映像が主役か、常時表示が増えすぎていないか、今行うことが一つに絞られているか、操作位置が状態変更で動かないかを、コード品質と同じ重要度で確認する。
