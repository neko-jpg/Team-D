# 未完了バックエンドタスク

## 目的

元の`openspec/changes/build-listing-photo-assistant-mvp/tasks.md`で未完了になっている作業から、`backend/**`のPythonバックエンド実装だけを抽出する。

furima-sandboxへの統合作業は考慮しない。React、Vite、UI、ブラウザ処理など、統合時に使用しない可能性があるフロントエンド実装を進めないための派生タスクリストとする。

## 抽出条件

- 元の`tasks.md`で`[ ]`の項目だけを対象にする。
- `backend/**`の実装、Agent server、またはバックエンドのunit／contract／integration／E2Eテストとして切り出せる作業を対象にする。
- 元の`tasks.md`で`[x]`の項目は再タスク化しない。
- 編集してよいファイルは`backend/**`だけとする。
- テストを追加・変更する場合も`backend/tests/**`へ置く。
- React、Vite、CSS、カメラ、UI、Reducer、OpenCV.js、Canvas、Storybook、ブラウザUIを含む完全E2E、Runbook、依存ファイルは対象外とする。
- 既存の`backend/**`実装が受け入れ条件を満たしている場合は、不要な再実装を行わない。
- この派生タスクの完了は、元の複合UI／統合タスク全体の完了を意味しない。元タスクから抽出したバックエンド部分だけを完了対象とする。
- 画像decode、EXIF反映、ICC／sRGB変換に必要なライブラリは実行環境から提供される前提とする。利用できない場合も`backend/**`外の依存ファイルは変更せず、依存追加を別作業として報告する。

## 担当方針

- ともちゃん：provider契約、strict schema、外部サービス境界、response検証
- 健太：FastAPI route、dependency接続、fixture、バックエンド単体テスト

## 1. 撮影後AI用の解析コピー正規化（元タスク4.2）

- [x] 1.1 【ともちゃん】front／back／tagのupload bytesから、原本bytesを変更せず、EXIF orientationを反映してsRGBへ変換した解析専用コピーを生成する処理を`backend/**`へ実装する。decode不能、未対応形式、変換失敗を明示的なエラーにし、原本を上書きしない。

- [ ] 1.2 【健太】`POST /api/analyze-shot`でraw uploadではなく正規化済み解析コピーだけを`ShotAssessor`へ渡すよう`backend/**`内で接続する。正規化失敗時はproviderを呼ばず、有限なAPI errorを返す。

- [ ] 1.3 【健太】向きと色空間が異なるfront／back／tag fixtureを使い、入力bytesのhash不変、出力の期待寸法・向き・sRGB、およびproviderへ渡ったbytesが正規化済みであることを`backend/tests/**`で確認する。

## 2. 採寸端点提案API（元タスク5.5）

- [x] 2.1 【ともちゃん】`MeasurementLineProvider`の入力・出力契約を`backend/**`へ実装する。射影補正済みmeasurement画像1枚を受け、`lengthStart|lengthEnd|widthStart|widthEnd`の0〜1正規化座標だけをstrict schemaで返す。cm値、UI文言、画面遷移、未知field、欠落、範囲外座標、`NaN`／`Infinity`を拒否する。

- [ ] 2.2 【健太】FastAPIの`POST /api/suggest-measurement-points`を`backend/**`へ実装し、`MeasurementLineProvider`をdependencyとして接続する。画像入力、timeout、provider error、schema不正を検証し、失敗をfixture成功へ自動変換しない。

- [ ] 2.3 【健太】正常な4端点、未知field、欠落、範囲外／非有限座標、timeoutを検証するcontract／APIテストを`backend/tests/**`へ追加する。

## 3. 正面mask API（元タスク6.2）

- [x] 3.1 【ともちゃん】`GarmentMasker`を`backend/**`へ実装し、front原本をrembgへmultipartの`file`、`model=birefnet-general-lite`、`om=true`で送ってmask-only PNGを取得する。35秒timeout、`image/png`、元画像との寸法一致、空mask、全面maskを検証し、不正なmaskを成功として返さない。

- [ ] 3.2 【健太】FastAPIの`POST /api/remove-background`を`backend/**`へ実装し、`GarmentMasker`をdependencyとして接続する。timeout、非PNG、寸法不一致、空mask、全面maskを有限なAPI errorとして返す。

- [ ] 3.3 【健太】正常maskと各異常fixtureを使ったcontract／APIテストを`backend/tests/**`へ追加し、不完全なmaskが成功responseにならないことを確認する。

## 4. 背景生成provider（元タスク6.3、元タスク6.4のバックエンド部分）

- [ ] 4.1 【ともちゃん】`BackgroundGenerator`を`backend/**`へ実装する。許可されたstyle IDだけを「空の撮影背景、真上視点、均一照明、人物・衣類・ハンガー・文字・ロゴなし」の固定promptへ変換し、外部providerへテキストだけを送る。60秒timeout、provider error、decode不能または利用不能な生成画像を明示的な失敗として扱う。

- [ ] 4.2 【健太】`BackgroundGenerator`の決定的fixtureとrequest spyテストを`backend/tests/**`へ追加する。送信bodyに商品画像、mask、tag、measurement、binary fieldが含まれず、未知style ID、timeout、provider error、decode不能または利用不能画像が拒否されることを確認する。

## 5. LiveKit Agent連携・バックエンド統合検証

このフェーズでは、元タスクで完了済みのLiveKit camera track購読、capacity 1、同時推論1、`VisionGuidanceProvider`、`GuidanceStateMachine`を再実装しない。未完了の複合タスクから、Agent server、provider接続、transport、非永続化、障害復帰、バックエンドE2Eに関する部分だけを抽出する。

- [ ] 5.1 【ともちゃん】Agentの`ProviderInference`結果を`GuidanceStateMachine`へ渡し、有限な`GuidanceEvent`へ変換してLiveKit data channelへ送るtransport adapterを`backend/**`へ実装する。短命助言はlossy、shot変更とsnapshot／再同期はreliable packetまたはRPCを使用し、providerの自由文で状態を決めない。（元タスク: 3.14、8.2）

- [ ] 5.2 【ともちゃん】Room切断／Agent再接続後にserver snapshotを再送し、再同期後の新しいsequenceからだけ助言を再開する。終了済みsession、古いshot、再接続前の結果を送信しないことを`backend/tests/**`で確認する。（元タスク: 3.14）

- [ ] 5.3 【健太】HTTP API／providerが画像、判定、助言、測定状態をDBやファイルへ永続化せず、保存制御可能な外部AI requestでは保存を無効にする。Agent終了時にpending frame参照とin-flight処理を解放するlifecycleテストを`backend/tests/**`へ追加する。（元タスク: 3.15、8.5）

- [ ] 5.4 【ともちゃん】live `MeasurementLineProvider`のtimeout、schema不正、provider errorをfixture成功や架空の4端点へ置き換えず、有限なAPI errorとして返すcontract／APIテストを`backend/tests/**`へ追加する。（元タスク: 5.8）

- [ ] 5.5 【ともちゃん】backendからloopbackのrembgへ本番と同じ`file`、`model=birefnet-general-lite`、`om=true`を送るprewarm helperまたはlive integration testを`backend/**`へ追加し、responseが`image/png`かつ入力frontと同寸法のmask-only画像であることを確認する。（元タスク: 6.1）

- [ ] 5.6 【健太】fixture transportで正常、provider timeout、古いevent、shot変更、切断／再接続、再同期を順に発生させ、sequence、expiry、現在shot、provider呼び出しが不正に巻き戻らないバックエンド回帰テストを1コマンドで実行できるようにする。（元タスク: 3.16）

- [ ] 5.7 【健太】Agent意味判定の同時実行数1、待機queue最大1、観測から助言event生成までp95 2秒以内、未処理例外0件を計測する。目標外でもqueueを増やさず、最新frameだけを処理することを確認する。（元タスク: 3.17）

- [ ] 5.8 【ともちゃん】fixtureモードでAgent guidance経路と`analyze-shot`、`suggest-measurement-points`、`remove-background`、背景生成providerを順に通すバックエンドE2Eを2回連続で実行し、同じevent、response、error契約を得る。（元タスク: 8.1）

- [ ] 5.9 【ともちゃん】live smokeとしてAgentのcamera track subscribe、有限codeの変化、撮影後判定、4端点提案、front mask、背景生成providerを順に確認する。ブラウザUI、iPhone操作、Runbook記録はこのタスクへ含めない。（元タスク: 8.2）

- [ ] 5.10 【ともちゃん】Agent停止、撮影後AI timeout、端点提案失敗、rembg timeout／無効mask、背景生成失敗をfixtureで発生させ、成功responseへ自動変換せず、有限なerrorと再試行可能性を返すバックエンド障害マトリクステストを追加する。（元タスク: 8.3）

- [ ] 5.11 【健太】Python backend／Agentのunit、contract、API、integration、backend E2EテストをcleanなPython環境で実行し、失敗0件、未処理例外0件を確認する。frontend build、browser console、Safari実機確認は含めない。（元タスク: 8.7）

## 完了条件

- [ ] 【ともちゃん】フェーズ1〜5のprovider、FastAPI、Agent transport、外部送信境界が`backend/**`だけで実装され、live失敗がfixture成功へ自動変換されないことを確認する。
- [ ] 【健太】`.venv/bin/python -m pytest -q backend/tests`を実行し、フェーズ1〜5で抽出したバックエンド部分の正常系・異常系がすべて成功することを確認する。

## 抽出対象外とした未完了タスク

カメラ、overlay、CaptureReducer、LiveKit JS、ブラウザ側のRoom接続／再接続、OpenCV.js Worker、採寸UI、Canvas合成、画像保存、Storybook、モバイル実機、Runbook、ブラウザUIを含む完全E2Eはフロントエンドまたはリポジトリ横断作業のため、このタスクリストには含めない。Agent server、LiveKit data transport、バックエンドintegration／E2Eはフェーズ5へ含める。

元タスク6.1のうち、rembg sidecarのinstall／プロセス管理、ライセンス照合、Runbook編集は対象外とし、backendからのprewarm疎通だけをフェーズ5へ含める。元タスク6.4のうち、4slotや原本を保持したUI状態、再試行・固定背景・元画像採用の表示と遷移も対象外とする。
