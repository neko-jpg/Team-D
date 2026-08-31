# Mercari AI Agent Hackathon OSS索引

最終更新: 2026-08-31

このファイルは採用判断の索引とする。コード・関数単位の詳細は[architecture.md](./architecture.md#4-oss利用境界)へ集約する。

## 採用・限定移植

| OSS | 固定対象 | 採用形態 | 使う部分 |
|---|---|---|---|
| [Wardrobe](https://github.com/tandpfun/wardrobe/tree/f44006cce7e4779e595a35b25fbbc8dabc68d7e4) | `f44006c` | 設計参考のみ | 画像正規化の順序、Responses strict schema、review／approve状態設計 |
| [document-autocapture](https://github.com/maazkhan77/document-autocapture/tree/e24df25d17ddc4cf7d7944c653bd0fba55025452) | `e24df25` / 1.0.6 | 関数を限定移植 | カメラ制御、グレースケール、輝度、Laplacian分散、raw撮影 |
| [rembg](https://github.com/danielgatis/rembg/tree/b439167d2eb22e51e7ec0732efe771bf920ff5c1) | v2.0.81 / `b439167` | Python HTTP sidecar | `/api/remove`のmask-only応答 |
| [BiRefNet](https://github.com/ZhengPeng7/BiRefNet) | `birefnet-general-lite` | rembg経由のみ | General Lite ONNX重み |

## 明示的に使わない部分

| OSS | 使わない部分 |
|---|---|
| Wardrobe | garment reconstruction、Images Edit、人物着用生成、クロマキー、disk job、polling |
| document-autocapture | 書類検出、Quad、glare／area、perspective warp、自動撮影、document guidance |
| rembg | 既定モデル任せ、GET URL入力、Gradio UI、alpha matting、ブラウザ直結 |
| BiRefNet | 学習repo、PyTorch直実行、fine-tune、large／videoモデル |

## 今回不採用

| OSS | 理由 | 代替 |
|---|---|---|
| [react-konva](https://github.com/konvajs/react-konva) | 固定合成だけには過剰 | native Canvas 2D |
| [XState](https://github.com/statelyai/xstate) | 直列フローには過剰 | 型付き`useReducer` |
| [GarmentIQ](https://github.com/lygitdata/GarmentIQ) | 採寸と複数モデルが1日スコープ外 | 採寸を除外 |
| [Nitidoc](https://github.com/santiagoisra/nitidoc) | AGPL-3.0かつ現行版が目的と不一致 | document-autocaptureの一部を限定移植 |
| [background-removal-js](https://github.com/imgly/background-removal-js) | AGPL-3.0とモバイルモデル読込リスク | サーバー側rembg |

## ライセンス対応

- Wardrobeは直接コピーしない。参考commitだけを記録する。
- document-autocaptureは限定移植するため、実装時にMIT本文・著作権・commitを`THIRD_PARTY_NOTICES.md`へ記載する。
- rembg本体のMITと、BiRefNetモデル重みの条件を別々に記録する。
