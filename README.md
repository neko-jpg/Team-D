# Mercari AI Agent Hackathon — Team-D

## 概要

**服を売りたいのに、撮影環境や撮影・採寸の知識がなく出品を諦めてしまう人に対して、出品に必要な写真が揃うまで伴走するAI Agentを作る。**

本企画は「Mercari AI Agent Hackathon for PM」に向けたTeam-Dのプロジェクトである。

ユーザーが衣類を撮影するたびにAI Agentが写真の内容と品質を確認し、撮り直し方や次に必要な写真を案内する。必要な写真が揃った後は、実物の商品情報を保ったまま背景を整え、ユーザーの承認を経て出品用画像を完成させる。

このリポジトリは、企画要件・技術設計・OSS調査をまとめたドキュメントの入口である。

## 開発環境

Node.js 20.19以上、npm、uvを用意する。macOSでuvが未導入の場合は
`brew install uv`で導入できる。Pythonは`.python-version`と`uv.lock`により
3.11へ固定され、FastAPIとLiveKit Agentは同じPython環境、provider契約、
設定を利用する。

初回はリポジトリルートでlockfileどおりに依存を導入する。

```bash
npm ci
uv sync --frozen
```

通常の画面開発では、fixture providerのFastAPIとViteを起動する。

```bash
npm run dev
```

ViteはWeb画面を公開し、`/api`へのリクエストをloopback上のFastAPIへ転送する。
API単体は`npm run dev:api`で起動できる。fixture Agentを含む3プロセスは、
LiveKit資格情報を設定したうえで`npm run dev:fixture`を使う。fixtureは画像判定を
固定化するモードであり、Room transport自体にはLiveKit接続が必要となる。

```bash
curl -i http://127.0.0.1:3001/api/health
# HTTP/1.1 200 OK
# {"status":"ok"}
```

Agentを起動する場合は`.env.example`を参考にserver-onlyのLiveKit資格情報を
`.env.local`へ用意し、現在のshellへ読み込む。判定まで固定化する場合は
`npm run dev:fixture`、live判定を選ぶ場合は`npm run dev:live`を実行する。
live失敗をfixture成功へ自動的に切り替えることはない。

```bash
set -a
source .env.local
set +a
npm run dev:live
```

起動前のimport、設定、provider構築だけを外部接続なしで確認する場合は次を実行する。
診断にはprovider modeと設定有無だけを出し、API keyとsecretは出力しない。

```bash
npm run check:backend:fixture
npm run check:backend:live
npm run check:agent:fixture
npm run check:agent:live
```

依存・build・テスト・バックエンド起動境界の確認コマンドは次のとおり。

```bash
npm run typecheck
npm test
npm run build
npm run test:backend
npm run verify:backend

uv run --frozen python -c \
  "from backend.app import app; from backend.agent import main; from backend.settings import BackendSettings; from backend.providers.vision_guidance import VisionGuidanceProvider; print('python imports ok')"
```

LiveKit Cloudへの最小接続と撮影後ShotAssessorのfixture／live切替は
[デモrunbook](./runbook.md)を参照する。ブラウザ側の最小接続画面は
`npm run dev:livekit-browser`で起動できる。

## UIフロー

![衣類の出品撮影を支援するAI AgentのUIフロー](./ekyc-ar-ui-flow-final.png)

## ドキュメント一覧

| ファイル | 内容 |
|---|---|
| [requirements.md](./requirements.md) | 企画背景、対象ユーザー、解決策、中心体験、実装範囲をまとめた要件定義 |
| [architecture.md](./architecture.md) | モバイルWeb、画像AI、背景分離、状態管理などの技術構成 |
| [runbook.md](./runbook.md) | LiveKit Cloudの最小接続とデモ運用手順 |
| [garment-measurement-oss.md](./garment-measurement-oss.md) | 衣類の半自動採寸に利用できるOSSと実装方法の調査 |
| [oss-links.md](./oss-links.md) | 採用候補および参考OSSの一覧 |
| [ekyc-ar-ui-flow-final.png](./ekyc-ar-ui-flow-final.png) | AR準備から背景設定までのUIフロー |

## 読む順番

企画全体を理解する場合は、最初に[要件定義](./requirements.md)、次に
[Webアーキテクチャ](./architecture.md)を読む。採寸機能を検討する場合は
[衣類自動採寸OSS調査](./garment-measurement-oss.md)、利用技術を一覧で確認する場合は
[OSS一覧](./oss-links.md)を参照する。
