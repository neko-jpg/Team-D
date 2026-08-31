# Mercari AI Agent Hackathon OSS一覧

最終更新: 2026-08-31

## 採用・利用候補

| 機能 | OSS | 用途・利用範囲 |
|---|---|---|
| モバイルWeb・画面構成 | [Wardrobe](https://github.com/tandpfun/wardrobe) | React／Vite構成、画像処理中・確認・承認フローの参考 |
| カメラ撮影・eKYC型品質判定 | [document-autocapture](https://github.com/maazkhan77/document-autocapture) | カメラ起動、ブレ・暗さ・反射・静止判定、撮り直し、Web Worker |
| 衣類・撮影方向のAI判定 | [Wardrobe](https://github.com/tandpfun/wardrobe) | 画像をAIに送り、構造化データを取得する実装の参考 |
| 商品と背景の分離 | [rembg](https://github.com/danielgatis/rembg) | HTTPサーバーとして背景除去・商品マスクを生成 |
| 高精度な商品マスク | [BiRefNet](https://github.com/ZhengPeng7/BiRefNet) | `rembg`から使用する背景分離モデル候補 |
| 背景選択・画像合成 | [react-konva](https://github.com/konvajs/react-konva) | 商品レイヤーと背景レイヤーの合成、位置調整、画像出力 |
| 撮影進捗・状態遷移 | [XState](https://github.com/statelyai/xstate) | 正面、背面、タグ、確認などの状態管理 |
| 衣類の測定点検出 | [GarmentIQ](https://github.com/lygitdata/GarmentIQ) | 肩、脇、裾などのランドマーク候補を取得 |

## 参考のみ・今回は直接採用しないOSS

| OSS | 採用しない理由 |
|---|---|
| [Nitidoc](https://github.com/santiagoisra/nitidoc) | AGPL-3.0で、現行版ではREADME掲載のライブ自動撮影が削除されているため、設計参考に限定する |
| [background-removal-js](https://github.com/imgly/background-removal-js) | AGPL-3.0で、モバイル上のモデル読み込みがデモの不安定要因になるため |

## 関連資料

- [企画要件定義](./requirements.md)
- [Webアーキテクチャ](./architecture.md)
- [衣類自動採寸OSS調査](./garment-measurement-oss.md)
- [eKYC・AR UIフロー画像](./ekyc-ar-ui-flow-final.png)
