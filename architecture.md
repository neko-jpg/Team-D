# Mercari AI Agent Hackathon Webアーキテクチャ

最終更新: 2026-08-25

## 1. 結論

ハッカソンではSwift／ARKitを使わず、ReactによるモバイルWebとして実装する。

スマートフォン実機からはngrokのHTTPS URLへアクセスする。フロントエンド、AIによる写真判断、背景分離を一つのWeb体験としてつなぎ、APIキーや画像処理はブラウザから直接呼ばずローカルのバックエンドを経由する。

Wardrobeを丸ごと製品の土台にするのではなく、各OSSから必要な機能だけを利用・参考にする。

## 2. ユーザー体験

```text
スマートフォンでWebアプリを開く
  ↓
Agentの案内に従って衣服を撮影する
  ↓
ブレ・暗さなどを確認する
  ↓
画像AIが正面・背面・タグなどを判断する
  ↓
不足があれば次の撮影または撮り直しを案内する
  ↓
必要な写真が揃ったら商品マスクを作る
  ↓
元の商品画像を残したまま背景を選択・編集する
  ↓
元画像と編集後画像を確認して完成する
```

## 3. 全体構成

```text
iPhone／Androidのブラウザ
        │
        │ HTTPS（ngrok）
        ▼
React + TypeScript + Vite
  ├─ カメラ起動・撮影
  ├─ ブレ・暗さなどの確認
  ├─ 撮影ステップの管理
  ├─ 背景の選択・編集
  └─ 元画像との比較・最終確認
        │
        │ /api（Viteのproxyで同一URLにまとめる）
        ▼
Node.js API
  ├─ 画像AIへ写真を送る
  ├─ 構造化された判定結果を返す
  ├─ rembgへ画像を送る
  └─ OpenAI APIキーをブラウザから隠す
        │
        ├──────────→ OpenAI API
        │              └─ 衣服・撮影方向・不足写真の判断
        │
        └──────────→ rembg HTTP server
                       └─ BiRefNetで商品マスクを生成
```

ngrokではReact／Vite側のポートを一つだけ公開し、`/api`をローカルのNode.js APIへ転送する。スマートフォンから複数のローカルポートへ接続する必要はない。

## 4. 各工程と参考Repo

| 工程 | 利用・参考Repo | 使う部分 | 今回作る部分 |
|---|---|---|---|
| ReactモバイルWeb | [Wardrobe](https://github.com/tandpfun/wardrobe) | React／Vite構成、画像処理中・確認・承認の考え方 | メルカリ出品撮影に合わせた画面 |
| eKYC型撮影 | [document-autocapture](https://github.com/maazkhan77/document-autocapture) | カメラ、ブレ・暗さ・反射・静止判定、撮影、撮り直し、Worker、テスト | 正面・背面・タグなどの撮影順序 |
| 写真の意味判断 | [Wardrobe](https://github.com/tandpfun/wardrobe) | 画像をAIへ渡して構造化情報を得る実装 | 正面・背面・タグ・不足写真を判断する指示とJSON |
| 商品マスク | [rembg](https://github.com/danielgatis/rembg) | HTTP server、マスク出力 | Node.js APIとの接続 |
| マスクモデル | [BiRefNet](https://github.com/ZhengPeng7/BiRefNet) | 商品と背景の分離 | 衣服写真でのモデル比較 |
| 背景編集 | [react-konva](https://github.com/konvajs/react-konva) | Canvasレイヤー、タッチ操作、画像書き出し | 背景選択と最終確認UI |
| 撮影状態管理 | [XState](https://github.com/statelyai/xstate) | 明示的な状態遷移 | 画面数が少なければReactのReducerで代用可能 |

## 5. eKYC型Repoから使う範囲

`document-autocapture`はUIだけでなく、カメラと画像品質判定の実装も利用候補にする。

### 利用する部分

- スマートフォンのカメラ起動
- 撮影画像の取得
- ブレ・暗さ・反射の判定
- カメラが安定しているかの判定
- 手動撮影
- 撮り直し
- Web Worker
- Vitest／Playwrightのテスト構成

### そのまま利用しない部分

- 四角い書類の輪郭検出
- 書類の四隅補正
- 書類がガイド枠内に収まったかの判定
- 書類検出を前提にした自動撮影

衣服は免許証のような四角形ではないため、衣服の内容や撮影方向は撮影後に画像AIで判断する。自動撮影はMVPの必須要件にしない。

## 6. Agentの処理

画像AIは画面遷移を自由に決めず、決められたJSONだけを返す。

```json
{
  "shotType": "front",
  "quality": "ok",
  "issues": [],
  "missingShots": ["back", "tag"],
  "nextAction": "REQUEST_NEXT"
}
```

想定する`nextAction`は次の範囲に限定する。

- `RETAKE`: 同じ写真を撮り直す
- `REQUEST_NEXT`: 次の写真を案内する
- `COMPLETE`: 必要な写真が揃った

実際の画面遷移はReact側の状態管理が行う。これにより、AIの返答が不安定でもデモ全体が壊れにくくなる。

## 7. 背景編集の方針

Wardrobeの生成画像を、商品の事実を示す中心画像には使わない。

1. 元画像を保持する
2. rembg／BiRefNetから商品マスクを取得する
3. 元画像のRGBへマスクを適用する
4. 商品レイヤーと背景レイヤーをCanvas上で合成する
5. ユーザーが元画像と比較して承認する

これにより、商品の柄・傷・汚れ・使用感を生成AIが描き直すことを避ける。マスクが誤っている場合に備え、元画像の確認を必須にする。

最初に試すモデルは`birefnet-general-lite`とする。rembg本体とモデルのライセンスは別であるため、使用するモデル名と条件を明示的に確認する。

## 8. ngrokを使った開発・実機テスト

```text
開発PC
├─ React／Vite
├─ Node.js API
└─ rembg HTTP server
       │
       └─ ngrokでReact／ViteのポートをHTTPS公開
              │
              └─ iPhone Safari／Android Chromeからアクセス
```

基本的な確認方法は以下である。

1. PCでは保存済みの衣服画像をアップロードして各処理をテストする
2. Viteを外部端末から接続できる設定で起動する
3. ngrokでViteのポートをHTTPS公開する
4. スマートフォンでngrok URLを開き、カメラ権限を許可する
5. 実際の衣服を撮影し、撮り直しと次の写真への遷移を確認する

カメラの自動テストだけに依存せず、iPhone SafariとAndroid Chromeの少なくとも一方で実機確認する。

## 9. 2日間で作る範囲

### 必須

1. ReactモバイルWebで写真を撮影できる
2. 撮影後に画像AIが内容を判断する
3. 撮り直しまたは次の撮影を案内する
4. 複数の必要写真を揃えられる
5. 商品マスクを生成できる
6. 商品を残したまま背景を変更できる
7. 元画像と編集後画像を比較して承認できる

### 時間があれば追加

- ブレ・暗さ・静止状態のリアルタイム表示
- 自動撮影
- 背景上での商品位置・大きさ調整
- 半自動採寸

## 10. 寸法機能を追加する場合

寸法機能はARKitを使わず、Web上で衣類ランドマーク検出と自動採寸処理を組み合わせる。

| Repo | 担当 |
|---|---|
| [GarmentIQ](https://github.com/lygitdata/GarmentIQ) | 肩・脇・裾などのランドマーク候補を検出 |
| 自動採寸モデル／API | ランドマークと撮影情報から実寸値を算出 |

```text
服を平らに置いて撮影
  ↓
GarmentIQで肩・脇・裾の測定点を提案
  ↓
自動採寸モデル／APIで実寸値を算出
  ↓
ユーザーが測定線を確認・修正
```

GarmentIQ単体が返す距離はcmではなくピクセル値であるため、実寸値の算出には別の自動採寸処理が必要になる。ハッカソンで追加する場合は、平置きTシャツの肩幅・身幅などに対象を限定し、完全自動ではなくユーザー確認を含む半自動方式とする。

## 11. 採用しない／直接流用しないもの

- Swift／ARKitによるネイティブ実装
- Wardrobeによる生成切り抜きを中心商品画像として使用すること
- 書類の四角形検出を衣服へそのまま適用すること
- [Nitidoc](https://github.com/santiagoisra/nitidoc)の直接流用
  - AGPL-3.0であり、現在のmainではREADMEにあるライブ自動撮影が削除されているため、設計参考に限定する
- [IMG.LY background-removal-js](https://github.com/imgly/background-removal-js)の直接採用
  - ブラウザ内で動作するがAGPL-3.0であり、モバイル端末上のモデル読込もデモの不安定要因になるため
