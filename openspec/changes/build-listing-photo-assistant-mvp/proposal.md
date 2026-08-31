## Why

撮影に不慣れな利用者は、衣類をどう撮り、次に何を撮れば出品用写真が揃うか分からず、出品前に離脱する。1日のハッカソンでは、撮影中の即時助言から撮影後のAI確認、商品画素を保持した出品用画像の完成までを一本の体験として示す。

## What Changes

- モバイルカメラ上へ正面・背面・タグごとの固定2Dガイドと進捗を表示する。
- 固定ROIの明るさ、ブレ、映像の安定性を端末内で継続判定し、有限な撮影助言を表示する。
- 手動撮影後だけ画像AIを呼び、strictな構造化結果から撮り直し、次撮影、完了を決める。
- 正面・背面・タグの3枚が揃うまで、受け入れ済み画像を保持して撮影ループを続ける。
- 商品を含まない背景だけを生成し、正面原本から得た商品RGBとrembg maskをCanvas合成する。
- 元画像と合成画像を比較し、利用者が明示的に承認した正面画像だけを出力する。
- Wardrobeは設計参考、document-autocaptureは関数の限定移植、rembg／BiRefNetは背景分離の実行時依存とする。
- 真の空間AR、自動撮影、採寸、商品再生成、人物着用生成は対象外とする。

## Capabilities

### New Capabilities

- `guided-garment-capture`: 固定カメラガイド、端末内ライブ品質助言、撮影後AI判定、必須写真の進捗と復帰を扱う。
- `background-preserving-edit`: 背景だけの生成、正面原本のmask合成、比較、承認、画像出力を扱う。

### Modified Capabilities

なし。既存の`openspec/specs/`に仕様は存在しない。

## Impact

- React／TypeScript／Vite: カメラ、固定ガイド、ライブ品質解析、撮影状態、比較・承認UI。
- Node.js API: 構造化画像判定、背景生成、rembg接続、timeoutと入力検証。
- Python sidecar: rembg v2.0.81と`birefnet-general-lite`。
- 外部OSSの採用境界、固定commit、ライセンス対応はルートの`architecture.md`を参照する。
- 画像はセッション内だけで扱い、資格情報とrembgポートをブラウザへ公開しない。
