# Mercari AI Agent Hackathon OSS索引

最終更新: 2026-08-31

このファイルは採用判断の索引とする。コード・関数単位の詳細は[architecture.md](./architecture.md#4-oss利用境界)へ集約する。

## 採用・限定移植

| OSS | 固定対象 | 採用形態 | 使う部分 |
|---|---|---|---|
| [LiveKit Agents](https://github.com/livekit/agents) / [LiveKit JS SDK](https://github.com/livekit/client-sdk-js) | 実装開始時のstable版をlockfile固定 | ライブ経路の中核 | WebRTC Room、camera track、Agent participant、video stream、data packet／RPC、再接続 |
| [Wardrobe](https://github.com/tandpfun/wardrobe/tree/f44006cce7e4779e595a35b25fbbc8dabc68d7e4) | `f44006c` | 設計参考のみ | 画像正規化の順序、Responses strict schema、review／approve状態設計 |
| [document-autocapture](https://github.com/maazkhan77/document-autocapture/tree/e24df25d17ddc4cf7d7944c653bd0fba55025452) | `e24df25` / 1.0.6 | 関数を限定移植 | カメラ制御、グレースケール、輝度、Laplacian分散、raw撮影 |
| [rembg](https://github.com/danielgatis/rembg/tree/b439167d2eb22e51e7ec0732efe771bf920ff5c1) | v2.0.81 / `b439167` | Python HTTP sidecar | `/api/remove`のmask-only応答 |
| [BiRefNet](https://github.com/ZhengPeng7/BiRefNet) | `birefnet-general-lite` | rembg経由のみ | General Lite ONNX重み |

## 明示的に使わない部分

| OSS | 使わない部分 |
|---|---|
| LiveKit | 音声会話、電話、avatar、録画、Egress／Ingress、30fps AI推論、ブラウザからのAI直結 |
| Wardrobe | garment reconstruction、Images Edit、人物着用生成、クロマキー、disk job、polling |
| document-autocapture | 書類検出、Quad、glare／area、perspective warp、自動撮影、document guidance |
| rembg | 既定モデル任せ、GET URL入力、Gradio UI、alpha matting、ブラウザ直結 |
| BiRefNet | 学習repo、PyTorch直実行、fine-tune、large／videoモデル |

## 今回不採用

| OSS | 理由 | 代替 |
|---|---|---|
| [Vision Agents](https://visionagents.ai/) | video processorは魅力的だが、1日MVPでは抽象層とStream依存を増やす | LiveKit Agentsのvideo trackを直接処理 |
| [Pipecat](https://github.com/pipecat-ai/pipecat) | pipeline構築に加えてRoom／frontend pushの設計が必要 | LiveKit AgentsでtransportとAgent lifecycleを統一 |
| [react-konva](https://github.com/konvajs/react-konva) | 固定合成だけには過剰 | native Canvas 2D |
| [XState](https://github.com/statelyai/xstate) | 直列フローには過剰 | 型付き`useReducer` |
| [GarmentIQ](https://github.com/lygitdata/GarmentIQ) | 採寸と複数モデルが1日スコープ外 | 採寸を除外 |
| [Nitidoc](https://github.com/santiagoisra/nitidoc) | AGPL-3.0かつ現行版が目的と不一致 | document-autocaptureの一部を限定移植 |
| [background-removal-js](https://github.com/imgly/background-removal-js) | AGPL-3.0とモバイルモデル読込リスク | サーバー側rembg |

## ライセンス対応

- LiveKit Agents、LiveKit JS SDK、LiveKit serverはApache-2.0。固定versionとNOTICE要否を実装時に記録する。
- Wardrobeは直接コピーしない。参考commitだけを記録する。
- document-autocaptureは限定移植するため、実装時にMIT本文・著作権・commitを`THIRD_PARTY_NOTICES.md`へ記載する。
- rembg本体のMITと、BiRefNetモデル重みの条件を別々に記録する。
