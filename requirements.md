# Mercari AI Agent Hackathon 企画要件定義

最終更新: 2026-08-31

## 0. この企画を一言で

**撮影に不慣れな人へカメラ上で次の行動をリアルタイムに示し、正面・背面・タグ・採寸用の4枚を揃え、着丈・身幅と現物の商品画素を保持した出品用画像まで完成させるAI Agent。**

## 1. ハッカソンの目標

- 実装時間: 1日
- 対象: 平置きしたトップス1種類、デモ端末1台
- 中心体験: リアルタイム撮影サジェスチョン → 4枚撮影 → 着丈・身幅の確認 → 背景生成・合成 → 承認
- 成功条件: 外部サービスが正常な経路と、fixtureで確実に完走する経路の両方を持つ

## 2. 解決する課題

対象ユーザーは、売りたい服と出品意思はあるが、必要な写真、構図、明るさ、撮影順序が分からず出品前に止まる人である。

Agentは写真を採点して終わらず、撮影中から「何を、どう撮るか」を示し、撮影後に結果を確認して次の行動を決める。

## 3. MVPの確定範囲

| 項目 | 1日MVPの定義 |
|---|---|
| 撮影対象 | 平置きトップス1着 |
| 必須写真 | `front`、`back`、`tag`、`measurement`の4枚 |
| AR表現 | カメラ映像上の固定2Dオーバーレイ。3D空間ARではない |
| リアルタイム判定 | LiveKitのWebRTC映像をstateful Agentが受け、構図・距離・撮影種別をAI判定してpush。端末内品質判定も併用 |
| 写真の意味判定 | 撮影中はAIが暫定助言し、撮影後AIが受理可否を確定 |
| 採寸 | `measurement` 1枚から着丈・身幅を提案し、利用者が測定点と数値を承認 |
| 画像生成 | 商品を含まない背景画像だけを生成 |
| 商品分離 | `front` 1枚だけをrembgでマスク化 |
| 最終画像 | 生成背景＋元の`front`商品画素をCanvas合成 |
| 最終判断 | 元画像と合成画像を比較し、ユーザーが承認 |

## 4. ユーザー体験

```text
正面用ガイドとリアルタイム助言を表示
  → 手動撮影
  → AIが正面・品質・欠けを確認
  → 背面、タグも同じループで取得
  → 採寸準備を案内し、専用のmeasurement写真を1枚撮影
  → 着丈・身幅の測定線を修正して承認
  → frontのマスクと商品を含まない背景を生成
  → 元の商品画素を背景へ合成
  → 元画像と比較
  → ユーザーが承認して保存
```

## 5. 機能要件

### R1. カメラガイド

- 背面カメラを起動し、現在の撮影種別、進捗、固定ガイドを映像上に表示する。
- 撮影セッション開始時にLiveKit Roomへ接続し、カメラをWebRTC video trackとしてpublishする。
- `front`／`back`は衣類用シルエットまたは安全枠、`tag`は矩形ガイドを使う。
- `measurement`は衣類全体の安全枠と、右下の50mm正方形マーカー配置枠を使う。
- ガイドはプレビュー専用とし、撮影画像へ焼き込まない。

### R2. リアルタイムAI撮影サジェスチョン

- リアルタイム性は、WebRTCの常時接続、最新フレーム選択、数値前処理、古い結果の破棄、UIへのpushで実現する。AIモデルへ30fpsの全フレームを渡す方式ではない。
- 明るさ、ブレ、動きはモデル非依存の数値処理、衣類の収まり、距離、表裏、タグへの移動は画像AIによる意味判定として責務を分ける。
- 画像AIは交換可能なprovider境界に置き、モデルを変更してもLiveKit接続、撮影状態、表示コード、UIを変更しない。
- LiveKit Agentsのstateful Agentがcamera trackへsubscribeし、video対応AI providerを通じて衣類全体の収まり、中央寄せ、距離、表裏、タグへの切替を継続的に判定する。
- ブラウザはライブ判定のための定期HTTP requestや静止画pollingを行わない。Agentは判定結果をLiveKit data packetまたはRPCでUIへpushする。
- Agentは最新フレームだけを保持し、意味判定は同時1件に制限する。処理中に届いた古いフレームと、現在ステップより古い判定結果は破棄する。
- UIへ送る`GuidanceEvent`は`sessionId`、`sequence`、`shot`、`code`、`message`、`confidence`、`observedAt`、`expiresAt`を持つ有限な構造とする。
- 短命な助言はlossy data packet、ステップ変更・撮影受理など失ってはいけない状態はreliable data packetまたはRPCで送る。
- 表示コードは `MOVE_CLOSER`、`MOVE_FARTHER`、`CENTER_GARMENT`、`SHOW_FULL_GARMENT`、`WRONG_SIDE`、`MOVE_TO_TAG`、`HOLD_STEADY`、`READY` などに限定する。
- 端末内では固定ROIを最大辺320px以下へ縮小し、明るさ、Laplacian分散によるブレ、連続フレーム差分による安定性を4Hzで補助判定する。ローカル結果はp95 500ms以内、AI助言は観測から表示までp95 2秒以内を目標とする。
- ライブ判定は助言であり、`READY`でなくても手動シャッターを無効化しない。

### R3. 撮影後AI判定

- 撮影画像と現在要求中の撮影種別をサーバー経由で画像AIへ送る。
- ライブAIの結果は撮影前の暫定助言とし、写真の受理可否は高解像度の撮影後AI判定で確定する。
- 結果は `shotType`、`quality`、`issues`、`missingShots`、`nextAction` を含むstrictなJSON Schemaで受ける。
- `shotType`は `front|back|tag|measurement|unknown`、`quality`は `ok|retry`、`nextAction`は `RETAKE|REQUEST_NEXT|COMPLETE` に限定する。
- AIの自由文で画面遷移せず、アプリ側が受け入れ済みスロットから次の状態を決定する。

### R4. 4枚固定の撮影進捗

- 画面上で常に`1/4 正面 → 2/4 背面 → 3/4 タグ → 4/4 採寸`の固定順序と完了状態を表示する。
- `tag`が受け入れられたら背景編集へ進まず、採寸準備画面を表示する。
- 品質不良、誤った撮影種別、衣類の欠け、読めないタグは同じステップで撮り直す。
- 撮り直しても別スロットの受け入れ済み画像を保持する。
- `measurement`は採寸計算専用とし、出品画像、背景分離、背景生成の入力に使用しない。
- カメラ権限がない場合は端末内画像のアップロードで継続できる。

### R5. 採寸準備・撮影・確認

- 採寸準備画面で「トップスを背面が上になるよう平置き」「しわと折れを伸ばす」「無地で衣類とコントラストのある床面を使う」「50mm正方形マーカーを100%倍率で印刷し、衣類と重ならない右下へ同一平面で置く」を順番に表示する。
- 利用者が準備完了を選択した後、衣類全体とマーカー全体が1枚に収まる真上撮影ガイドを表示し、`measurement`を手動撮影する。
- 撮影後にマーカーの四隅、既知の50mm辺、衣類全体の収まり、明るさ、ブレを検証する。マーカー未検出、欠け、衣類との重なり、強い遠近歪み、品質不良の場合は理由付きで同じステップを撮り直す。
- 有効な1枚を射影補正し、マーカーからpx/cmを求め、次の2本の測定線とcm値を提案する。
  - `着丈`: 背面の襟中央付け根から裾中央まで。
  - `身幅`: 左右の脇下間の直線距離。平置き幅であり胸囲へ2倍しない。
- 測定結果画面で4つの端点をドラッグ修正でき、着丈・身幅を0.1cm単位で再計算して表示する。
- 初期状態を未承認とし、利用者が2本の測定線と数値を明示承認した場合だけ採寸完了とする。
- マーカー解析を完了できない場合は、撮り直しまたは着丈・身幅の手入力を提示する。自動的に推定値やfixture成功へ置き換えない。
- デモ対象トップスでは、承認後の着丈・身幅がメジャー実測値に対して各±1.0cm以内を目標とする。

### R6. 背景生成

- 必須4枚が受け入れられ、採寸結果が承認された後、商品・人物・文字・ロゴを含まない撮影背景をテキストから1枚生成できる。
- 商品画像を画像生成APIへ入力しない。
- 生成失敗時はローカル固定背景へ切り替え、撮影済み画像を失わない。

### R7. 商品画素を保持する合成

- rembg／BiRefNetで`front`のmask-only PNGを取得する。
- 商品領域のRGBは元の`front`画像からのみ取得する。
- 背景、元画像、マスクをCanvas 2Dで合成し、商品を生成AIで描き直さない。
- マスクが空、全面、寸法不一致の場合は合成画像を承認可能にしない。

### R8. 比較・承認・出力

- 元画像と合成画像を同じ表示領域で比較できる。
- 合成画像を初期状態で承認済みにしない。
- ユーザーが元画像または合成画像を明示的に選び、確定した正面画像をPNGまたはJPEGで保存できる。

### R9. 一時データと障害復帰

- 画像、マスク、判定結果、測定点、採寸値はセッション内でのみ保持し、DBへ永続保存しない。
- APIキーはブラウザへ渡さない。
- LiveKit／Agent切断時は再接続状態を表示し、固定ガイド、端末内品質判定、手動撮影を継続できる。
- AI、rembg、背景生成の失敗時に進捗を変更せず、再試行または安全な代替経路を表示する。
- fixtureとliveの切替は明示設定とし、live失敗を黙ってfixture成功へ変換しない。

## 6. 受け入れ条件

- 実機で固定ガイドとリアルタイム助言が表示される。
- 実機のcamera trackをLiveKit Roomへpublishし、Agentの意味判定が定期HTTP pollingなしでUIへpushされる。
- 衣類の欠け、距離、表裏またはタグへの移動に対し、撮影前に有限な理由コード付き助言が変化する。
- 切断または遅延後に届いた期限切れ・順序逆転イベントで、現在の助言や撮影ステップが巻き戻らない。
- `READY`以外でも手動撮影でき、撮影画像にガイドが含まれない。
- 正面・背面・タグの誤りや品質不良で、理由付きの撮り直しになる。
- `1/4 正面 → 2/4 背面 → 3/4 タグ → 4/4 採寸`の順序が画面上で明示される。
- 採寸写真1枚から着丈・身幅の測定線が提案され、端点修正と明示承認ができる。
- 4枚と採寸承認が揃うまで背景編集へ進めない。
- `front`だけが背景分離され、`back`と`tag`は原本のまま保持される。
- 背景生成APIにはテキストだけが送られ、商品画像は送信されない。
- 合成後も商品領域は元画像由来である。
- fixtureモードで開始から画像保存までを決定的に完走できる。

## 7. OSS採用方針

| OSS | 採用判断 | 利用範囲 |
|---|---|---|
| LiveKit Agents | 中核として採用 | WebRTC Room、video track購読、stateful Agent、data packet／RPCによるfrontend push |
| LiveKit JS SDK | 実行時依存 | Room接続、camera track publish、Agentイベント購読、再接続 |
| Wardrobe | 設計参考のみ | 画像正規化、Responsesのstrict schema、review／approveパターン |
| document-autocapture | 関数を限定移植 | カメラ制御、輝度、Laplacianブレ、raw撮影の一部 |
| rembg | 実行時依存 | HTTP sidecarとmask-only API |
| BiRefNet | rembg経由で採用 | `birefnet-general-lite` ONNXのみ |
| OpenCV.js | 採寸処理に採用 | 50mmマーカー四隅検出、射影補正、px/cm換算、輪郭と測定線の計算 |
| react-konva | 不採用 | native Canvas 2Dで代替 |
| XState | 不採用 | 型付き`useReducer`で代替 |
| GarmentIQ | 不採用 | 複数モデルを導入せず、OpenCV.jsと利用者補正による2項目採寸へ限定 |

コード・関数単位の採用境界は[architecture.md](./architecture.md)を唯一の詳細資料とする。

## 8. 今回実装しないこと

- WebXR、ARKit、ARCore、平面検出、ワールドアンカー、6DoF
- 衣類輪郭・肩・袖・裾・タグのライブ追跡
- ライブ判定用の連続HTTP upload／polling、全フレーム保存、30fpsでのAI推論保証
- 自動撮影、マーカーなし完全自動採寸、着丈・身幅以外の採寸、価格推定、Mercari API連携
- 人物着用画像生成、人物認識
- 商品自体の再生成、レタッチ、色・形・傷・汚れの改変
- 全端末・全衣類カテゴリへの精度保証

## 9. 関連資料

- [WebアーキテクチャとOSS利用境界](./architecture.md)
- [OSS索引](./oss-links.md)
- [OpenSpec change](./openspec/changes/build-listing-photo-assistant-mvp/)
- [LiveKit Agents](https://docs.livekit.io/agents/)
- [LiveKit video input](https://docs.livekit.io/agents/multimodality/vision/video/)
- [LiveKit data packets](https://docs.livekit.io/transport/data/packets/)
- [OpenAI Responses API](https://developers.openai.com/api/reference/cli/resources/responses/methods/create)
- [OpenAI Image Generation](https://developers.openai.com/api/docs/guides/image-generation)
