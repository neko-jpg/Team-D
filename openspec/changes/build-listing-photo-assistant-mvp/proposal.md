## Why

撮影に不慣れな利用者は、衣類をどう撮り、次に何を撮れば出品用写真が揃うか分からず、出品前に離脱する。また、着丈や身幅をどこから測るか分からず、商品情報を完成できない。1日のハッカソンでは、撮影中の即時助言から撮影後のAI確認、採寸結果の確認、商品画素を保持した出品用画像の完成までを一本の体験として示す。

## What Changes

- モバイルカメラ上へ正面・背面・タグごとの固定2Dガイドと進捗を表示する。
- カメラ映像をLiveKit RoomへWebRTC publishし、stateful Agentがvideo対応AIで構図・距離・表裏・タグ移動を判定して、有限な撮影助言をfrontendへpushする。
- 端末内では明るさ、ブレ、映像の安定性を補助判定し、Agent切断時も固定ガイドと手動撮影を維持する。
- 手動撮影後は高解像度画像をstrictな構造化AI判定へ送り、撮り直し、次撮影、完了を確定する。
- 正面・背面・タグの3枚が揃うまで、受け入れ済み画像を保持して撮影ループを続ける。
- 正面・背面・タグとは別に、既知サイズの印刷マーカーを衣類と同じ平面へ置いた採寸写真を必須とする。
- 採寸写真をOpenCV.jsで射影補正し、マーカーから縮尺を求め、平置きトップスの着丈と身幅の測定点・数値を提案する。利用者は測定点を修正し、採寸結果を明示的に承認する。
- 必須写真と採寸結果が揃うまで背景編集および最終出力へ進めない。
- 商品を含まない背景だけを生成し、正面原本から得た商品RGBとrembg maskをCanvas合成する。
- 元画像と合成画像を比較し、利用者が明示的に承認した正面画像だけを出力する。
- LiveKit Agentsをライブ映像transport・Agent lifecycle・frontend pushの中核とし、Wardrobeは設計参考、document-autocaptureは関数の限定移植、OpenCV.jsは採寸画像処理、rembg／BiRefNetは背景分離の実行時依存とする。
- 真の空間AR、自動撮影、マーカーなしの完全自動採寸、商品再生成、人物着用生成は対象外とする。

## Capabilities

### New Capabilities

- `guided-garment-capture`: 固定カメラガイド、WebRTC経由のリアルタイムAI助言、端末内品質助言、撮影後AI判定、必須写真の進捗と復帰、マーカー付き採寸写真、着丈・身幅の半自動採寸と利用者承認を扱う。
- `background-preserving-edit`: 背景だけの生成、正面原本のmask合成、比較、承認、画像出力を扱う。

### Modified Capabilities

なし。既存の`openspec/specs/`に仕様は存在しない。

## Impact

- React／TypeScript／Vite: カメラ、固定ガイド、LiveKit Room接続とvideo publish、Agentイベント購読、端末内品質解析、撮影状態、採寸点の修正・承認、比較・承認UI。
- Python backend／LiveKit Agent: 短命token発行、camera track購読、最新フレームの意味判定、有限な`GuidanceEvent`のdata packet／RPC push、撮影後判定、背景生成、rembg接続。
- OpenCV.js: 採寸マーカー検出、射影補正、縮尺計算、画像上の測定点からcmへの換算。
- Python sidecar: rembg v2.0.81と`birefnet-general-lite`。
- 外部OSSの採用境界、固定commit、ライセンス対応はルートの`architecture.md`を参照する。
- 画像はセッション内だけで扱い、資格情報とrembgポートをブラウザへ公開しない。
