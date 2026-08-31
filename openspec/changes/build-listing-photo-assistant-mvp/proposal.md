## Why

撮影に不慣れな利用者は、衣類をどう撮り、次に何を撮れば出品用写真が揃うか分からず、出品前に離脱する。また、着丈や身幅をどこから測るか分からず、商品情報を完成できない。1日のハッカソンでは、撮影中の即時助言から撮影後のAI確認、採寸結果の確認、商品画素を保持した出品用画像の完成までを一本の体験として示す。

## What Changes

- モバイルカメラ上へ正面・背面・タグごとの固定2Dガイドと進捗を表示する。
- カメラ映像をLiveKit RoomへWebRTC publishし、stateful Agentが最新の縮小frameを、rembg `u2netp`によるローカル構図判定とOpenAI Realtimeの意味判定へ渡す。構図・距離はmask／bboxから決定論的に判定し、表裏・しわ・タグ移動は単一の常時WebSocketで判定して、有限な撮影助言をfrontendへpushする。
- 端末内では明るさ、ブレ、映像の安定性を補助判定し、Agent切断時も固定ガイドと手動撮影を維持する。
- 手動撮影後は高解像度画像をstrictな構造化AI判定へ送り、撮り直し、次撮影、完了を確定する。
- `1/4 正面 → 2/4 背面 → 3/4 タグ → 4/4 採寸`の固定順序で4枚を撮り、受け入れ済み画像を保持して撮影ループを続ける。
- 採寸は平置きの半袖クルーネックTシャツに限定し、外形50mmの専用マーカーを衣類と同じ平面へ置いた解析専用写真1枚を必須とする。
- 採寸写真をOpenCV.jsで射影補正して縮尺を求め、撮影後画像AIは着丈・身幅の4端点だけを正規化座標で提案する。OpenCV.jsがcm換算し、利用者が端点を修正して明示承認する。解析失敗時は4枚目を撮り直すか、4枚目を保持した上で手入力する。
- 必須写真と採寸結果が揃うまで背景編集および最終出力へ進めない。
- 商品を含まない背景だけを生成し、正面原本から得た商品RGBとrembg maskをCanvas合成する。
- 元画像と合成画像を比較し、利用者が明示的に承認した正面画像だけを出力する。
- LiveKit Agentsをライブ映像transport・Agent lifecycle・frontend pushの中核とし、Wardrobeは設計参考、document-autocaptureは関数の限定移植、OpenCV.jsは採寸画像処理、rembg／`u2netp`はライブ構図判定、rembg／BiRefNetは撮影後の背景分離に限定する。
- 真の空間AR、自動撮影、マーカーなしの完全自動採寸、商品再生成、人物着用生成は対象外とする。

## Capabilities

### New Capabilities

- `guided-garment-capture`: 固定カメラガイド、WebRTC経由のリアルタイムAI助言、端末内品質助言、撮影後AI判定、必須写真の進捗と復帰、マーカー付き採寸写真、着丈・身幅の半自動採寸と利用者承認を扱う。
- `background-preserving-edit`: 背景だけの生成、正面原本のmask合成、比較、承認、画像出力を扱う。

### Modified Capabilities

なし。既存の`openspec/specs/`に仕様は存在しない。

## Impact

- React／TypeScript／Vite: カメラ、固定ガイド、LiveKit Room接続とvideo publish、Agentイベント購読、端末内品質解析、撮影状態、採寸点の修正・承認、比較・承認UI。
- Python backend／LiveKit Agent: 短命token発行、camera track購読、`u2netp` maskからの決定論的な構図判定、OpenAI Realtime sessionのprewarmと再利用、両判定の安全な合成、有限な`GuidanceEvent`のdata packet／RPC push、撮影後判定、採寸4端点提案、背景生成、rembg接続。
- OpenCV.js: 50mm専用マーカー検出、射影補正、縮尺計算、画像上の測定点からcmへの換算。
- Python sidecar: rembg v2.0.81、ライブ構図用`u2netp`、撮影後背景分離用`birefnet-general-lite`。
- 外部OSSの採用境界、固定commit、ライセンス対応はルートの`architecture.md`を参照する。
- 画像はセッション内だけで扱い、資格情報とrembgポートをブラウザへ公開しない。
