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

- [x] 1.2 【健太】`POST /api/analyze-shot`でraw uploadではなく正規化済み解析コピーだけを`ShotAssessor`へ渡すよう`backend/**`内で接続する。正規化失敗時はproviderを呼ばず、有限なAPI errorを返す。

- [x] 1.3 【健太】向きと色空間が異なるfront／back／tag fixtureを使い、入力bytesのhash不変、出力の期待寸法・向き・sRGB、およびproviderへ渡ったbytesが正規化済みであることを`backend/tests/**`で確認する。

## 2. 採寸端点提案API（元タスク5.5）

- [x] 2.1 【ともちゃん】`MeasurementLineProvider`の入力・出力契約を`backend/**`へ実装する。射影補正済みmeasurement画像1枚を受け、`lengthStart|lengthEnd|widthStart|widthEnd`の0〜1正規化座標だけをstrict schemaで返す。cm値、UI文言、画面遷移、未知field、欠落、範囲外座標、`NaN`／`Infinity`を拒否する。

- [x] 2.2 【健太】FastAPIの`POST /api/suggest-measurement-points`を`backend/**`へ実装し、`MeasurementLineProvider`をdependencyとして接続する。画像入力、timeout、provider error、schema不正を検証し、失敗をfixture成功へ自動変換しない。

- [x] 2.3 【健太】正常な4端点、未知field、欠落、範囲外／非有限座標、timeoutを検証するcontract／APIテストを`backend/tests/**`へ追加する。

## 3. 正面mask API（元タスク6.2）

- [x] 3.1 【ともちゃん】`GarmentMasker`を`backend/**`へ実装し、front原本をrembgへmultipartの`file`、`model=birefnet-general-lite`、`om=true`で送ってmask-only PNGを取得する。35秒timeout、`image/png`、元画像との寸法一致、空mask、全面maskを検証し、不正なmaskを成功として返さない。

- [x] 3.2 【健太】FastAPIの`POST /api/remove-background`を`backend/**`へ実装し、`GarmentMasker`をdependencyとして接続する。timeout、非PNG、寸法不一致、空mask、全面maskを有限なAPI errorとして返す。

- [x] 3.3 【健太】正常maskと各異常fixtureを使ったcontract／APIテストを`backend/tests/**`へ追加し、不完全なmaskが成功responseにならないことを確認する。

## 4. 背景生成provider（元タスク6.3、元タスク6.4のバックエンド部分）

- [x] 4.1 【ともちゃん】`BackgroundGenerator`を`backend/**`へ実装する。許可されたstyle IDだけを「空の撮影背景、真上視点、均一照明、人物・衣類・ハンガー・文字・ロゴなし」の固定promptへ変換し、外部providerへテキストだけを送る。60秒timeout、provider error、decode不能または利用不能な生成画像を明示的な失敗として扱う。

- [x] 4.2 【健太】`BackgroundGenerator`の決定的fixtureとrequest spyテストを`backend/tests/**`へ追加する。送信bodyに商品画像、mask、tag、measurement、binary fieldが含まれず、未知style ID、timeout、provider error、decode不能または利用不能画像が拒否されることを確認する。

## 5. LiveKit Agent連携・バックエンド統合検証

このフェーズでは、元タスクで完了済みのLiveKit camera track購読、capacity 1、同時推論1、`VisionGuidanceProvider`、`GuidanceStateMachine`を再実装しない。未完了の複合タスクから、Agent server、provider接続、transport、非永続化、障害復帰、バックエンドE2Eに関する部分だけを抽出する。

- [x] 5.1 【ともちゃん】Agentの`ProviderInference`結果を`GuidanceStateMachine`へ渡し、有限な`GuidanceEvent`へ変換してLiveKit data channelへ送るtransport adapterを`backend/**`へ実装する。短命助言はlossy、shot変更とsnapshot／再同期はreliable packetまたはRPCを使用し、providerの自由文で状態を決めない。（元タスク: 3.14、8.2）

- [x] 5.2 【ともちゃん】Room切断／Agent再接続後にserver snapshotを再送し、再同期後の新しいsequenceからだけ助言を再開する。終了済みsession、古いshot、再接続前の結果を送信しないことを`backend/tests/**`で確認する。（元タスク: 3.14）

- [x] 5.3 【健太】HTTP API／providerが画像、判定、助言、測定状態をDBやファイルへ永続化せず、保存制御可能な外部AI requestでは保存を無効にする。Agent終了時にpending frame参照とin-flight処理を解放するlifecycleテストを`backend/tests/**`へ追加する。（元タスク: 3.15、8.5）

- [x] 5.4 【ともちゃん】live `MeasurementLineProvider`のtimeout、schema不正、provider errorをfixture成功や架空の4端点へ置き換えず、有限なAPI errorとして返すcontract／APIテストを`backend/tests/**`へ追加する。（元タスク: 5.8）

- [x] 5.5 【ともちゃん】backendからloopbackのrembgへ本番と同じ`file`、`model=birefnet-general-lite`、`om=true`を送るprewarm helperまたはlive integration testを`backend/**`へ追加し、responseが`image/png`かつ入力frontと同寸法のmask-only画像であることを確認する。（元タスク: 6.1）

- [x] 5.6 【健太】fixture transportで正常、provider timeout、古いevent、shot変更、切断／再接続、再同期を順に発生させ、sequence、expiry、現在shot、provider呼び出しが不正に巻き戻らないバックエンド回帰テストを1コマンドで実行できるようにする。（元タスク: 3.16）

- [x] 5.7 【健太】Agent意味判定の同時実行数1、待機queue最大1、prewarm済み実OpenAI Realtimeの成功20件以上・provider error 0件・観測から助言event生成までp95 1秒未満、未処理例外0件を計測する。目標外でもqueueを増やさず、最新frameだけを処理することを確認する。（元タスク: 3.17、9.10）

- [x] 5.8 【ともちゃん】fixtureモードでAgent guidance経路と`analyze-shot`、`suggest-measurement-points`、`remove-background`、背景生成providerを順に通すバックエンドE2Eを2回連続で実行し、同じevent、response、error契約を得る。（元タスク: 8.1）

- [x] 5.9 【ともちゃん】live smokeとしてAgentのcamera track subscribe、有限codeの変化、撮影後判定、4端点提案、front mask、背景生成providerを順に確認する。ブラウザUI、iPhone操作、Runbook記録はこのタスクへ含めない。（元タスク: 8.2）

- [x] 5.10 【ともちゃん】Agent停止、撮影後AI timeout、端点提案失敗、rembg timeout／無効mask、背景生成失敗をfixtureで発生させ、成功responseへ自動変換せず、有限なerrorと再試行可能性を返すバックエンド障害マトリクステストを追加する。（元タスク: 8.3）

- [x] 5.11 【健太】Python backend／Agentのunit、contract、API、integration、backend E2EテストをcleanなPython環境で実行し、失敗0件、未処理例外0件を確認する。frontend build、browser console、Safari実機確認は含めない。（元タスク: 8.7）

## 6. OpenAI Realtime 1秒経路

- [x] 6.1 【ともちゃん】frameごとのResponses APIを廃止し、撮影セッション単位のOpenAI Realtime WebSocket、prewarm、`conversation: none`、最大辺256px JPEG、有限function schemaへ変更する。
- [x] 6.2 【ともちゃん】timeout時はresponseだけをcancelしてwarm socketを維持し、切断時だけ再接続する。session closeでprovider clientを1回だけ解放する契約テストを通す。
- [x] 6.3 【健太】`.env.local`の実`OPENAI_API_KEY`で20件を計測し、接続1回、provider error 0件、provider p95 528.842ms、`observedAt→backend publish` p95 529.307ms、backend publish p95 0.297msを確認する。

## 7. 公開実画像による精度・透過検証

- [x] 7.1 【ともちゃん】Open Images V7の公式bbox、human image label、instance segmentation、画像単位のライセンス情報から、衣類16件とGT maskを取得する再現可能なCLIを追加する。人物annotationを除外してもannotation非網羅による着用画像が残るため、raw画像のbbox由来labelをflat-lay、front／back、tag、または最終`READY`の正解として扱わない。
- [x] 7.2 【ともちゃん】公式GT maskで衣類RGBだけを切り抜き、無地の512px canvasへ遠すぎ、近すぎ、中央ずれ、欠けの4状態を決定的に合成する。16 sourceのうちoccluded／truncated／group／depiction／inside flagがなく、元bboxが画像端に接しない5 sourceだけを採用し、5 source×4状態=20件について画像と同寸のGT mask、変形後の実bbox、元ライセンスをmanifestへ保持する。人手確認していない元画像から`READY`正解を作らない。
- [x] 7.3 【健太】実OpenAI Realtimeへ上記の非READY 20件を同一sessionで投入し、モデルが返してはならない`HOLD_STEADY`／`AGENT_UNAVAILABLE`をtool schemaから除外する。曖昧時の`READY`を禁止し、front／back解析コピーだけへ固定のシアン枠と中心十字を重ねる。最終構成`gpt-realtime-mini`、256px、low detail、32 tokenでprovider error 0件、接続1回、誤`READY` 0件、禁止code 0件、provider p50 514.854ms／p95 706.506ms／max 794.119msを確認する。別の反復では外部API揺らぎによりprovider error 2件・p95 1.340秒となったため、成功時の1秒経路は確認済みだがインターネットを含む全反復での1秒保証とはしない。
- [x] 7.4 【健太】production transportでは非READYを1回目から即時配信し、`READY`だけを同じshotの2回連続一致後に配信する。同一shot・同一codeの後続結果は助言を再送せず、現在code、固定message、更新済み`expiresAt`、`displayChanged=false`を持つheartbeatだけにする。最初のlossy助言が欠落しても次のheartbeatから表示を復元できる回帰テストを追加する。
- [x] 7.5 【ともちゃん】実rembg `birefnet-general-lite`へclean sourceから作った20件を送り、provider error 0件、mean／minimum IoU 0.988096／0.964637、mean／minimum precision 0.991359／0.966008、mean／minimum recall 0.996700／0.990157を確認する。RGBA previewは20/20件で透明・不透明画素を含み、alphaがmaskと一致し、不透明商品画素の元RGBを保持する。ウォーム後の処理時間はp50 4.656秒／p95 4.756秒であり、ライブ助言の1秒経路とは分離した撮影後処理とする。評価器は16件未満、p50 IoU 0.85未満、または1件でもIoU／precision／recall 0.90未満なら失敗にする。
- [x] 7.6 【健太】上位`gpt-realtime-2`は同じ80件でp95 2.310秒、provider error 39件となり1秒要件を満たさないため採用しない。複数fieldを一度に返す条件分解方式もp95 903.304ms、provider error 7件、誤`READY` 2件へ悪化したため採用せず、検証済みの有限code方式をproduction defaultとして維持する。

OpenAI単独だった初回評価では、非READY構図4分類のexact一致は20件中5件（25%）、`MOVE_FARTHER`は0/5だった。この失敗を受け、構図をOpenAIの意味推論から分離して、prewarm済み`u2netp`のmask／bboxを決定論的に分類するハイブリッド方式へ変更した。公開画像の切り抜きには表裏・平置き・折れの正解がなく、`READY`正例も人手確認していないため、ハイブリッド後の20/20もプロダクト全体の助言精度または`READY`再現率とはみなさない。front／back／tagの撮影助言精度と撮影後受理精度を完了判定するには、実際の撮影手順で正解を付けた12〜30枚以上を別途用意する。RepViT-SAMもApple M3のCoreMLで20件を計測してp95 238.6msだったが、動的decoderのexport互換性とLinux portabilityを優先し、このbackendですでに運用するrembg／ONNXの`u2netp`を採用した。

## 8. ローカル構図判定とRealtime意味判定のハイブリッド

- [x] 8.1 【ともちゃん】最大連結成分のmask／bboxから画像端1px接触、span 0.42未満、span 0.77超、中心軸ずれ0.12超の優先順で4構図codeまたはPASSだけを返す`GeometryGuidanceProvider`を`backend/**`へ実装する。ローカル判定から`READY`を返さず、空／全面／寸法不一致maskを契約違反として拒否する。
- [x] 8.2 【ともちゃん】既存rembg v2.0.81 sidecarのprewarm済み`u2netp`をライブ構図mask専用に接続し、model名固定、450ms以下timeout、PNG／寸法検証を行う。`front|back`だけで使用し、撮影後の`birefnet-general-lite`経路とtimeoutを分離する。
- [x] 8.3 【ともちゃん】同じ最新frameからgeometryとOpenAI Realtime semanticを並列開始し、geometry補正を優先、PASS時だけsemanticを採用するhybrid analyzerを実装する。geometry失敗またはPASS時のsemantic失敗は成功結果へfallbackせず、既存transportで`AGENT_UNAVAILABLE`へ正規化する。tag／measurementは既存semantic経路を維持する。
- [x] 8.4 【健太】20件の公開衣類構図fixtureを本番と同じrembg HTTPへ通し、各code 5/5、全体20/20、誤`READY` 0件、provider error 0件、ウォームp95 400ms未満をbackend-only gateで確認する。hybrid実credential反復ではgeometry／semantic／合成／publishを分離計測し、成功20件以上、error 0件、全体p95 1秒未満を確認する。

構図ゲートは4反復すべて20/20、各code 5/5、誤`READY` 0件、provider error 0件で、ウォームp95は181.300〜241.293ms、最大値は369.766msだった。`.env.local`の実`OPENAI_API_KEY`と本番runtime factoryを使った20件のhybrid transport計測はprovider error 0件、`observedAt→provider完了` p95 633.596ms、backend publish p95 0.244ms、`observedAt→backend publish` p95 633.789msで合格した。別の構図補正20件はp95 199.913ms、意味判定が必要なPASS 20件はp95 522.873msで、1本のprewarm済みRealtime接続が再利用された。

## 完了条件

- [x] 【ともちゃん】フェーズ1〜5のprovider、FastAPI、Agent transport、外部送信境界が`backend/**`だけで実装され、live失敗がfixture成功へ自動変換されないことを確認する。
- [x] 【健太】`.venv/bin/python -m pytest -q backend/tests`を実行し、フェーズ1〜5で抽出したバックエンド部分の正常系・異常系がすべて成功することを確認する。

## 抽出対象外とした未完了タスク

カメラ、overlay、CaptureReducer、LiveKit JS、ブラウザ側のRoom接続／再接続、OpenCV.js Worker、採寸UI、Canvas合成、画像保存、Storybook、モバイル実機、Runbook、ブラウザUIを含む完全E2Eはフロントエンドまたはリポジトリ横断作業のため、このタスクリストには含めない。Agent server、LiveKit data transport、バックエンドintegration／E2Eはフェーズ5へ含める。

元タスク6.1のうち、rembg sidecarのinstall／プロセス管理、ライセンス照合、Runbook編集は対象外とし、backendからのprewarm疎通だけをフェーズ5へ含める。元タスク6.4のうち、4slotや原本を保持したUI状態、再試行・固定背景・元画像採用の表示と遷移も対象外とする。
