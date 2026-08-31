# Guided Capture の状態設計

この資料は、衣類撮影 UI を「AI に採点されるカメラ」ではなく「次の一手だけを静かに返すカメラ」として設計・実装するための契約である。React の reducer、カメラオーバーレイ、Storybook fixture を作るときに読む。

## 体験上の不変条件

- 映像を主役にし、進捗、固定ガイド、主指示、シャッターだけを常設する。
- 主指示は常に 1 つ。同時に検出した問題は優先順位で畳み込み、診断名ではなく次の行動を表示する。
- 状態が改善したら、短い肯定を返してから次の指示へ進む。新しい問題が生じた場合は肯定を中断する。
- `READY` は撮りやすい瞬間を知らせる助言であり、撮影許可ではない。撮影処理中の多重押下を防ぐ時間を除き、シャッターは常に利用可能にする。
- ライブ判定は暫定的な助言に限定する。写真の受理、撮り直し、次工程への遷移は撮影後判定または採寸検証で確定する。
- 接続状態は撮影品質と別の状態として扱う。Agent が切断しても、固定ガイド、端末内判定、手動撮影、撮影済み写真を残す。
- Agent の自由文、confidence、スコアをそのまま UI に出さない。有限コードをアプリ所有の日本語へ変換する。
- 古い session、異なる step、逆転した sequence、期限切れの判定を破棄し、表示を巻き戻さない。
- ガイド、線、メッセージはプレビューにだけ重ね、保存する画像には焼き込まない。

## 状態を三つに分離する

単一の巨大な `status` に全条件を詰め込まない。少なくとも次を直交させる。

1. **工程**: `front → back → tag → measurement`
2. **撮影ループ**: 探索、助言、静止待ち、撮影可能、撮影、検証、撮り直し、受理
3. **接続**: 接続中、接続済み、再接続中、オフライン

この分離により、たとえば「背面を撮影中・少し離れる必要あり・Agent 再接続中」を同時に表現できる。

```ts
export type CaptureStep = "front" | "back" | "tag" | "measurement";

export type CapturePhase =
  | "seeking"
  | "coaching"
  | "stabilizing"
  | "ready"
  | "capturing"
  | "validating"
  | "retake"
  | "accepted";

export type ConnectionState =
  | { status: "connecting"; attempt: number }
  | { status: "connected"; lastEventAt: number }
  | { status: "reconnecting"; attempt: number; nextRetryAt?: number }
  | { status: "disconnected"; reason: "timeout" | "network" | "agent_unavailable" };

export type AgentGuidanceCode =
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
  | "READY";

export type LocalQualityCode =
  | "TOO_DARK"
  | "TOO_BRIGHT"
  | "TOO_BLURRY"
  | "ANALYZER_UNAVAILABLE"
  | "READY";

export type GuidanceCode = AgentGuidanceCode | LocalQualityCode;

export type GuidanceSource = "local" | "agent";

export interface CandidateIssue {
  code: Exclude<GuidanceCode, "READY">;
  source: GuidanceSource;
  priority: number;       // 小さいほど優先。画面には表示しない
  confidence?: number;    // 安定化の内部入力。画面には表示しない
  observedAt: number;
  expiresAt?: number;
}

export interface PresentedGuidance {
  kind: "instruction" | "acknowledgement" | "ready";
  code: GuidanceCode | `RESOLVED_${Exclude<GuidanceCode, "READY">}`;
  message: string;
  enteredAt: number;
  minVisibleUntil: number;
}

export interface CaptureSlot {
  step: CaptureStep;
  status: "empty" | "validating" | "accepted" | "retake";
  imageUrl?: string;
  acceptance?: "validated" | "manual_measurement_photo";
  retakeReasons?: string[];
}

export interface CaptureState {
  sessionId: string;
  step: CaptureStep;
  phase: CapturePhase;
  slots: Record<CaptureStep, CaptureSlot>;
  activeIssues: CandidateIssue[];
  guidance: PresentedGuidance;
  connection: ConnectionState;
  lastAgentSequence: number;
  captureRequestId?: string;
  measurement?: MeasurementState;
}
```

## 問題の優先順位

同時に複数の問題がある場合は、次の順で最上位の 1 件だけを主指示へ出す。数値は実装例であり、同じ層では工程固有の条件を先にする。

| 優先度 | 問題 | 理由 |
|---:|---|---|
| 10 | 対象が見つからない、誤った面、タグではない | 今の工程を成立させない |
| 20 | 衣類／タグ／基準物が欠ける、基準物が隠れる・重なる | 撮影後の復元ができない |
| 30 | 近すぎる、遠すぎる、中央から外れる | 構図の大きな修正が必要 |
| 40 | 真上でない、衣類の折れ・しわ | 幾何や見え方を損なう |
| 50 | 暗い、反射、ピント外れ | 品質を損なうが構図修正後に直せる |
| 60 | 端末が動いている | 最後に静止を促す |
| 100 | `READY` | 問題が一定時間ないときだけ出す |

補足ルール:

- `tag` では `MOVE_TO_TAG` と撮影後の反射・ピント理由を、衣類全体の構図より優先する。
- `measurement` では衣類全体と既知サイズ基準物の両方が必要。片方でも欠けたら `READY` にしない。
- Agent 由来の意味判定と端末内の明るさ・ブレ判定は同じ候補列へ正規化する。ただし期限切れ Agent イベントは候補に入れない。
- 接続断は主指示の競合相手にしない。画面上部の小さなステータス表示に分け、現在の撮影指示を残す。
- Agent切断中に、端末内で新たにマーカー欠けや重なりをライブ検出したように見せない。端末内ライブ判定は現在の仕様どおり明るさ、ブレ、安定性に限り、詳細なマーカー失敗コードは撮影後のOpenCV.js検証結果として表示する。
- 撮影後の撮り直し理由はライブ候補より優先する。モーダルで塞ぎ続けず、理由 1 件と「撮り直す」を明確に表示する。

## ヒステリシスと表示安定化

フレーム単位の閾値だけで文言を切り替えると点滅する。時刻ベースのヒステリシスを reducer の外側にある selector / stabilizer で適用する。フレームレートに依存させない。

推奨の初期値:

```ts
export const guidanceTiming = {
  issueEnterMs: 300,          // 同じ問題を継続観測してから表示
  issueClearMs: 600,          // 解消を継続観測してから解除
  instructionMinDwellMs: 900, // 指示の最短表示時間
  acknowledgementMs: 800,    // 「全体が入りました」等の肯定
  readyHoldMs: 700,           // 問題なし＋静止を保って READY
  agentStaleMs: 2_500,        // expiresAt がない場合の上限例
} as const;
```

選択手順:

1. session、step、sequence、expiry を検査して不正な観測を捨てる。
2. 同じ `code` を継続観測した時間が `issueEnterMs` を超えた候補だけを active にする。
3. 現在の指示は `issueClearMs` の間、解消が続くまで維持する。より上位の問題が新しく確定した場合だけ途中で差し替える。
4. 解消時は、他の active issue がなければ肯定を 600〜1,000ms 表示する。他の問題がある場合は、肯定を省略して次の 1 指示へ移る。
5. 全問題が解消し、端末が `readyHoldMs` 静止してから `READY` にする。`READY` から一瞬の揺れで戻さず、通常の `issueEnterMs` を満たした問題だけで解除する。
6. 新しい主指示へ切り替えるときはフェード等を使えるが、表示待ちのためにシャッター入力を遅延させない。

同じ意味の観測を source ごとに二重表示しない。たとえば Agent の `HOLD_STEADY` とローカルの motion 判定は 1 件へ集約する。confidence は採用閾値には使ってよいが、ユーザー向けスコアやパーセントに変換しない。

## 工程別の固定ガイド

固定ガイドはライブ追跡ではない。工程が変わったときに形と説明を切り替え、映像の内容に合わせて輪郭を追従させない。

### 1/4 正面 `front`

- 平置きトップスのシルエットまたは安全枠を表示する。
- 襟、左右の袖、裾までが安全枠内に入ることを促す。
- カメラを衣類の真上に保ち、正面が上であることを意味判定する。
- 構図が整った後に明るさ、ブレ、静止を案内する。

### 2/4 背面 `back`

- 正面と同じ安全枠を使い、ガイド見出しを「背面」に変える。
- 背面が上かを判定し、誤って正面を向けた場合は「裏返して背面を上にしてください」とする。
- 正面の受理済み画像は保持し、背面の撮り直しで消さない。

### 3/4 タグ `tag`

- 中央に縦長またはタグ比率の矩形ガイドを表示する。
- まずタグへ近づくことを促し、その後に枠内への配置、反射、ピント、静止を判定する。
- 「タグが読める距離」を目標にし、デジタルズームを強制しない。
- タグが見つからない場合も、ユーザーが手動撮影できる状態を維持する。

### 4/4 採寸 `measurement`

撮影前の準備と、撮影中のガイドと、撮影後の確認を別状態にする。

**準備**

- 対象は平置きしたトップス。着丈と身幅だけを扱う。
- 衣類を背面が上になるように置き、襟、袖、裾、しわ、折れを整える。
- 現行仕様の 50mm 正方形マーカーを 100% 倍率で印刷し、実物の一辺が 50mm であることを確認する。
- マーカーを衣類と同一平面の右下へ、衣類から離し、重ねずに置く。別の基準物へ黙って切り替えない。基準方式を変える場合は先にプロダクト仕様を更新する。

**撮影**

- 衣類全体の安全枠と基準物の配置枠を同時に表示する。
- 衣類全体、基準物の全辺／全四隅、両者の間隔、真上視点、明るさ、ブレを確認する。
- 基準物が小さすぎる、隠れている、衣類と重なる場合は、それぞれ 1 アクションの指示へ変換する。

**撮影後**

- 既知サイズから射影補正と px/cm 換算を行い、補正済み画像へ 2 本の測定線を重ねる。
- `着丈` は背面の襟中央付け根から裾中央まで、`身幅` は左右の脇下間の平置き直線距離とし、胸囲へ 2 倍しない。
- 4 端点をドラッグ修正可能にし、移動中も値を再計算する。数値は 0.1cm 単位で表示する。
- 端点が画像外または衣類領域から大きく外れたら承認を無効にし、どの点を戻すか伝える。想定範囲外の値は警告するが、再確認後の承認経路は残す。
- 初期状態を未承認にし、ユーザーが測定線と数値を明示的に承認して初めて完了とする。
- 解析できない場合は「撮り直す」と「手入力」を提示する。live 失敗を fixture 成功へ黙って置き換えない。
- `manualInputEligible` は、衣類全体が写ったmeasurement写真を保持している場合だけtrueにする。マーカー失敗だけを理由に4枚目を省略してはならない。

```ts
export type MeasurementState =
  | { status: "preparing"; reference: ReferenceSpec }
  | { status: "analyzing"; imageUrl: string; reference: ReferenceSpec }
  | { status: "editing"; draft: MeasurementDraft; warning?: MeasurementWarning }
  | {
      status: "invalid";
      reason: MeasurementFailure;
      imageUrl: string;
      garmentFullyVisible: boolean;
      manualInputEligible: boolean;
    }
  | { status: "manual_editing"; imageUrl: string; lengthCm?: number; widthCm?: number }
  | { status: "approved_cv"; draft: MeasurementDraft }
  | { status: "approved_manual"; imageUrl: string; lengthCm: number; widthCm: number };

export type ReferenceSpec =
  { kind: "square_marker"; edgeMm: 50 };

export interface Point { x: number; y: number } // 補正画像上の 0..1 座標

export interface MeasurementDraft {
  imageUrl: string;
  length: { start: Point; end: Point; cm: number };
  width: { start: Point; end: Point; cm: number };
}

export type MeasurementFailure =
  | "MARKER_MISSING"
  | "MARKER_MULTIPLE"
  | "MARKER_TOO_SMALL"
  | "MARKER_OCCLUDED"
  | "GARMENT_OUT_OF_FRAME"
  | "GARMENT_MARKER_OVERLAP"
  | "SEGMENTATION_FAILED"
  | "ENDPOINTS_INVALID";

export type MeasurementWarning = "LENGTH_OUT_OF_RANGE" | "WIDTH_OUT_OF_RANGE";
```

## 日本語 UI コピー

原則は「状態の説明」ではなく「次にする動作」を、丁寧語の 1 文で書く。句点は短いオーバーレイでは省略してよい。

| code / 状態 | 主指示 | 解消時の肯定例 |
|---|---|---|
| `MOVE_CLOSER` | もう少し近づいてください | ちょうどよい距離です |
| `MOVE_FARTHER` | 少し離してください | 全体が入りました |
| `CENTER_GARMENT` | 衣類を中央に合わせてください | 中央に合いました |
| `SHOW_FULL_GARMENT` | 袖と裾まで画面に入れてください | 全体がきれいに入りました |
| `WRONG_SIDE`（front） | 正面を上にしてください | 正面を確認できました |
| `WRONG_SIDE`（back） | 裏返して背面を上にしてください | 背面を確認できました |
| `MOVE_TO_TAG` | タグに近づき、枠に合わせてください | タグを確認できました |
| `FLATTEN_GARMENT` | しわと折れを伸ばしてください | きれいに広がりました |
| `CAMERA_OVERHEAD` | カメラを真上に向けてください | 真上から撮れています |
| `TOO_DARK` | もう少し明るい場所で撮ってください | 明るさは十分です |
| `TOO_BRIGHT` | 光が直接当たらない位置へ動かしてください | 明るさはちょうどよいです |
| `TOO_BLURRY` | 画面をタップしてピントを合わせてください | はっきり写っています |
| `HOLD_STEADY` | そのまま止めてください | 安定しました |
| `PLACE_MARKER` | 50mmマーカーを衣類の右下に置いてください | マーカーを確認しました |
| `MARKER_NOT_VISIBLE` | マーカー全体を画面に入れてください | マーカー全体が入りました |
| `MARKER_TOO_SMALL`（撮影後） | マーカーが大きく写るよう少し近づいてください | マーカーを読み取れそうです |
| `MARKER_OCCLUDED`（撮影後） | マーカーの四隅をすべて見せてください | 四隅を確認できました |
| `GARMENT_MARKER_OVERLAP`（撮影後） | マーカーを衣類から離してください | 十分に離れました |
| `READY` | きれいに撮れそうです | — |
| 撮影中 | 撮影しています… | — |
| 撮影後判定中 | 写真を確認しています… | — |
| 撮り直し | {最優先理由}。もう一度撮影してください | — |
| 採寸解析中 | 測る位置を確認しています… | — |
| 採寸点編集 | 線の端を動かして測る位置を確認してください | — |
| 採寸未承認 | 着丈と身幅を確認して承認してください | — |

避ける表現:

- 「距離不足」「被写体欠損」「信頼度 72%」のような診断語や内部値。
- 「失敗しました」だけで終わり、回復方法がない文言。
- 「AI が認識できません」のように、ユーザーが直せる動作が分からない文言。
- 通常の撮影ミスを強い赤色と警告音で責める表現。

## 接続断、遅延、再試行

接続表示は小さなバナーまたはステータス pill にし、主指示を覆わない。

| 状態 | 表示 | 継続できること |
|---|---|---|
| `connecting` | 撮影サポートに接続しています… | 固定ガイド、手動撮影 |
| `connected` | 通常は表示しない | 全機能 |
| `reconnecting` | 再接続しています。撮影は続けられます | 固定ガイド、端末内判定、手動撮影 |
| `disconnected` | 撮影サポートに接続できません。撮影は続けられます | 固定ガイド、端末内判定、手動撮影、「再試行」 |

- 自動再接続は回数と待ち時間を有限にし、上限後は `disconnected` と明示的な「再試行」に移る。
- 再接続後は server snapshot で現在 step を照合してから Agent 助言を復帰する。古い packet を再生しない。
- 撮影後 AI が失敗した場合、現在 step と撮影済み画像を保持して「もう一度確認する」「撮り直す」を提示する。
- 採寸 CV が失敗した場合、画像を保持して「撮り直す」「手入力」を提示する。
- カメラ権限が拒否された場合は接続断と混同せず、「設定を開く」または端末内画像のアップロードへ案内する。
- fixture/live のモードは明示し、live の失敗を自動的な fixture 成功に変えない。

## Reducer のイベント契約

```ts
export type CaptureEvent =
  | { type: "LOCAL_OBSERVATIONS_UPDATED"; at: number; issues: CandidateIssue[] }
  | {
      type: "AGENT_GUIDANCE_RECEIVED";
      sessionId: string;
      step: CaptureStep;
      sequence: number;
      code: AgentGuidanceCode;
      observedAt: number;
      expiresAt: number;
    }
  | { type: "GUIDANCE_TICK"; now: number }
  | { type: "SHUTTER_PRESSED"; requestId: string; at: number }
  | { type: "PHOTO_CAPTURED"; requestId: string; imageUrl: string }
  | { type: "CAPTURE_FAILED"; requestId: string; reason: "camera" | "storage" }
  | { type: "POST_CAPTURE_ACCEPTED"; step: CaptureStep }
  | {
      type: "POST_CAPTURE_RETAKE_REQUIRED";
      step: CaptureStep;
      reasons: string[];
    }
  | { type: "ADVANCE_TO_STEP"; step: CaptureStep }
  | { type: "CONNECTION_CHANGED"; connection: ConnectionState }
  | { type: "RECONNECT_REQUESTED"; at: number }
  | { type: "MEASUREMENT_ANALYSIS_STARTED"; imageUrl: string }
  | { type: "MEASUREMENT_DRAFT_READY"; draft: MeasurementDraft }
  | {
      type: "MEASUREMENT_ANALYSIS_FAILED";
      reason: MeasurementFailure;
      imageUrl: string;
      garmentFullyVisible: boolean;
      manualInputEligible: boolean;
    }
  | { type: "MEASUREMENT_POINT_MOVED"; line: "length" | "width"; end: "start" | "end"; point: Point }
  | { type: "MEASUREMENT_CV_APPROVED" }
  | { type: "MEASUREMENT_MANUAL_STARTED"; imageUrl: string }
  | { type: "MEASUREMENT_MANUAL_VALUES_SET"; lengthCm: number; widthCm: number }
  | { type: "MEASUREMENT_MANUAL_APPROVED" };
```

Reducer の重要な振る舞い:

- `SHUTTER_PRESSED` は `ready` 以外でも受理し、`capturing` へ遷移する。`capturing` 中に同じ操作が来た場合だけ多重処理を防ぐ。
- `AGENT_GUIDANCE_RECEIVED` は session / step / sequence / expiry を検証した後だけ stabilizer へ渡す。Agent の `READY` 単独で写真を受理しない。
- `POST_CAPTURE_RETAKE_REQUIRED` は対象 slot だけを `retake` にし、他の `accepted` slot を変更しない。
- `POST_CAPTURE_ACCEPTED` 後だけ対象の写真 slot を通常受理する。measurement写真のマーカー解析が失敗しても、衣類全体が写り`manualInputEligible`がtrueなら、同じ画像を`acceptance: "manual_measurement_photo"`として4枚目に保持できる。衣類が欠ける場合は受理せず撮り直す。
- measurement写真の受理後は採寸解析または手入力へ進む。`MEASUREMENT_MANUAL_VALUES_SET`は`manual_editing`の値を更新するだけで承認しない。`MEASUREMENT_CV_APPROVED`または`MEASUREMENT_MANUAL_APPROVED`の明示イベントだけが採寸結果を承認済みにする。
- 背景編集は4 slotと`approved_cv|approved_manual`の両方が揃った場合だけ解放する。次stepはこの構造化状態から決定し、AIの自由文で決めない。
- `CONNECTION_CHANGED` で `phase`、slot、measurement を初期化しない。
- 非同期結果は `captureRequestId` と step を照合し、撮り直し後に届いた古い結果を無視する。

## Storybook fixture の最低セット

各 fixture は 390 CSS px の iPhone 縦画面を基準にし、カメラなしでも固定画像で再現できるようにする。少なくとも以下を独立 story として保存する。

```ts
export const captureFixtures = {
  frontSeeking: { step: "front", phase: "seeking", guidanceCode: "SHOW_FULL_GARMENT" },
  frontTooClose: { step: "front", phase: "coaching", guidanceCode: "MOVE_FARTHER" },
  frontResolved: { step: "front", phase: "coaching", guidanceCode: "RESOLVED_SHOW_FULL_GARMENT" },
  frontReady: { step: "front", phase: "ready", guidanceCode: "READY" },
  frontManualCaptureNotReady: { step: "front", phase: "capturing", previousGuidanceCode: "TOO_DARK" },
  backWrongSide: { step: "back", phase: "coaching", guidanceCode: "WRONG_SIDE" },
  tagMoveCloser: { step: "tag", phase: "coaching", guidanceCode: "MOVE_TO_TAG" },
  tagBlur: { step: "tag", phase: "coaching", guidanceCode: "TOO_BLURRY" },
  tagValidating: { step: "tag", phase: "validating" },
  retakeKeepsPriorSlots: {
    step: "tag",
    phase: "retake",
    acceptedSlots: ["front", "back"],
    retakeReasons: ["タグの文字がぼやけています"],
  },
  measurementPreparing: { step: "measurement", measurementStatus: "preparing" },
  measurementMarkerMissing: { step: "measurement", phase: "coaching", guidanceCode: "MARKER_NOT_VISIBLE" },
  measurementAnalyzing: { step: "measurement", measurementStatus: "analyzing" },
  measurementEditing: { step: "measurement", measurementStatus: "editing" },
  measurementMarkerOccluded: { step: "measurement", measurementStatus: "invalid", reason: "MARKER_OCCLUDED" },
  measurementInvalidEndpoint: { step: "measurement", measurementStatus: "invalid", reason: "ENDPOINTS_INVALID" },
  measurementManualFallback: {
    step: "measurement",
    measurementStatus: "invalid",
    garmentFullyVisible: true,
    manualInputEligible: true,
    actions: ["retake", "manual"],
  },
  reconnectingWhileCoaching: {
    step: "front",
    phase: "coaching",
    guidanceCode: "CAMERA_OVERHEAD",
    connection: "reconnecting",
  },
  disconnectedLocalReady: {
    step: "front",
    phase: "ready",
    guidanceCode: "READY",
    connection: "disconnected",
    guidanceSource: "local",
  },
} as const;
```

Story / interaction test では、静止画の見た目だけでなく次も確認する。

- 問題が閾値付近で揺れても主指示が点滅しない。
- 問題解消後に短い肯定が出て、その後 `READY` になる。
- `READY` でなくてもシャッター操作が `capturing` へ進む。
- 接続断で主指示、固定ガイド、受理済み slot が消えない。
- 古い Agent イベントが step や文言を巻き戻さない。
- 採寸点を動かすと数値が更新され、不正な点では承認できない。
- `prefers-reduced-motion` では意味を損なわずアニメーションを省略できる。
