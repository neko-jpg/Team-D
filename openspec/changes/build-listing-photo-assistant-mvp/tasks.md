## 担当方針

* 友ちゃんさん：上流設計、共通型・契約、AI/API方針、画像処理方針、フォールバック判断
* 徹平さん：状態遷移、upload統合、scheduler、安定性、合成・保存
* 健太さん：scaffold、fixture、受け入れテスト、カメラ、PixelRoi、品質判定、overlay、統合検証、runbook、ライセンス・運用確認

## 0. Preflight（0:00〜0:30）

* [x] 0.1 【友ちゃんさん】Python 3.11へ`rembg[cpu,cli]==2.0.81`を導入し、`birefnet-general-lite`を事前downloadして、mask-onlyのprewarm requestが成功することを確認する
* [x] 0.2 【健太さん】front／back／tag、dark、blur、wrong-shotのfixture画像と既知maskを用意し、各fixtureを人間が目視確認する
* [x] 0.3 【友ちゃんさん】Wardrobe `f44006c`、document-autocapture `e24df25`、rembg `b439167`を参照元として固定し、限定移植範囲と利用方針を決める
* [x] 0.4 【健太さん】document-autocaptureのMIT全文・著作権・commitを`THIRD_PARTY_NOTICES.md`へ記載する

## 1. 最小の縦スライス（0:30〜2:00）

* [x] 1.1 【友ちゃんさん】最小縦スライスの責務分割、API境界、front→back→tag→editの完了条件を確定する
* [x] 1.2 【健太さん】React + TypeScript + ViteとNode.js APIを作成し、`npm install`、型チェック、build、`/api/health`が成功することを確認する
* [x] 1.3 【友ちゃんさん】`ShotAssessment`、`LiveCaptureAssessment`、撮影slot、provider errorの共有型とruntime schemaを定義し、未知値と欠落を拒否する単体テストを通す
* [x] 1.4 【徹平さん】型付き`useReducer`でfront→back→tag→editの状態遷移を実装し、撮り直しても他slotを保持する単体テストを通す
* [x] 1.5 【徹平さん・健太さん】fixtureの`ShotAssessor`とupload UIを接続して縦スライスを通し、受け入れテストでretry時のslot保持とfront／back／tagの3枚が揃うまで編集へ進めないことを確認する
* [x] 1.6 【友ちゃんさん】T+2h時点でupload fixtureが完走しない場合のスコープ削減を判断し、カメラ以外のUI装飾を止めて縦スライスを優先する

## 2. カメラとリアルタイム助言（2:00〜3:30）

* [x] 2.1 【友ちゃんさん】ライブ解析の責務、4Hz・同時解析1、READY判定、手動撮影を阻害しない方針を確定する
* [ ] 2.2 【健太さん】document-autocaptureの`DEFAULT_VIDEO_CONSTRAINTS`、`start()`、`ensureVideoPlayback()`、`cleanupVideoStream()`を限定移植し、背面カメラ起動、権限拒否、track解放を実機で確認する
* [x] 2.3 【徹平さん】`scheduleNextFrame()`のrVFC→rAF→timerパターンを4Hz・同時解析1へ変更して実装し、中間フレームを蓄積しないテストを通す
* [x] 2.4 【友ちゃんさん】固定ガイドを`object-fit`を考慮して映像PixelRoiへ変換する仕様と入出力を定義する
* [x] 2.5 【健太さん】PixelRoi変換の純粋関数を実装し、縦横比とクロップのfixtureテストを通す
* [x] 2.6 【友ちゃんさん】document-autocaptureの`rgbaToGrayscale()`、`brightnessCheck()`、`laplacianVariance()`の採用基準と閾値方針を決める
* [x] 2.7 【健太さん】上記画像品質判定を出典付きで限定移植し、暗い／明るい／ぼけたfixtureの判定テストを通す
* [x] 2.8 【友ちゃんさん】Quadベースの`StabilityTracker`を使わず、連続ROIのframe-differenceで600ms安定を見る方式を確定する
* [x] 2.9 【徹平さん】frame-difference trackerを実装し、600ms安定と移動時resetのテストを通す
* [x] 2.10 【健太さん】front／back用固定衣類ガイドとtag用矩形をvideo上へ表示し、raw撮影Blobにoverlayが含まれず、`READY`以外でも撮影できることを確認する
* [x] 2.11 【友ちゃんさん】Worker／Canvas解析不可時の固定ガイド＋手動撮影、カメラ権限拒否時のfile upload fallbackを仕様として確定する
* [x] 2.12 【健太さん】各fallbackが実際に動くことを確認する

## 3. 既存capture coreのLiveKit・4slot拡張

- [x] 3.1 【友ちゃんさん】LiveKit project、Room、browser participant、Python Agent用の環境変数と最小接続手順を用意し、同じRoomのparticipant一覧と両側ログでbrowserとAgentの一意なidentityを確認する
- [x] 3.2 【友ちゃんさん・健太さん】LiveKit Agents／Python SDKとLiveKit JS SDKの互換性があるstable versionをPython／npm lockfileへ固定し、clean installとSDK importを成功させ、製品名、version、URL、license／noticeを`THIRD_PARTY_NOTICES.md`の固定値と照合する
- [x] 3.3 【友ちゃんさん・健太さん】完了済みNode.js API scaffoldの`/api/health`契約を保ったまま、FastAPI serverとLiveKit Agent workerがprovider schemaと設定を共有するPython packageへbackendを移行し、ルートからfixture／liveを起動してfrontend build、Python import、health response、Agent起動ログを確認する
- [x] 3.4 【友ちゃんさん】既存のfront／back／tag契約を、`GuidanceEvent`、measurementを含む4slot、正規化4端点、`MeasurementDraft`／`ApprovedMeasurement`、接続状態、provider errorへ後方互換に拡張し、未知値、欠落、非有限値、範囲外座標、measurementを含む`ShotAssessment`を拒否する契約テストを通す
- [ ] 3.5 【徹平さん】既存`CaptureReducer`を、工程、撮影phase、接続状態を分離した`front→back→tag→measurement準備→measurement撮影→採寸確認・承認→edit`へ拡張し、受理済み4slotと`approved_cv|approved_manual`からだけ次stepを導出して、撮り直し、再接続、不正なAI `nextAction`、古い`requestId`で別slotや採寸状態が変わらないReducerテストを通す
- [ ] 3.6 【徹平さん・健太さん】既存upload fixture縦スライスを`1/4 正面→2/4 背面→3/4 タグ→4/4 採寸`へ拡張し、4枚の保持後も採寸が`needs_review`なら編集開始を無効にし、測定線と数値の明示承認後だけeditへ進むUI統合テストを通す
- [x] 3.7 【友ちゃんさん】`POST /api/livekit-token`で、設定上限内の短い有効期限、sessionに対応するRoom、一意なparticipant identity、camera publishと必要なdata通信だけを許可するtokenを発行し、decodeしたclaimと、LiveKit API secretがresponse／browser bundleへ含まれないことをテストする
- [ ] 3.8 【徹平さん・健太さん】LiveKit JS SDKでRoomへ接続し、2.2で実装する背面camera trackをpublishして、工程と独立した`connecting|connected|reconnecting|disconnected`をUIへ反映し、基準iPhoneのparticipant一覧とtrack情報で接続を確認する
- [x] 3.9 【友ちゃんさん】Python LiveKit AgentをRoom participantとして起動し、camera video trackへsubscribeして、frame arrivalごとに上書きするcapacity 1のlatest-frame slotと同時推論1件のprocessorを実装し、推論中に3frame以上を流してもqueueが1を超えず、完了後は最新frameだけが処理されるテストを通す
- [x] 3.10 【友ちゃんさん】現在shotと縮小frameを受ける`VisionGuidanceProvider`と`GuidanceStateMachine`を実装し、有限codeへのruntime validation、session単位の単調増加sequence、`observedAt`／`expiresAt`、同一shot／codeのdedupe、短命助言と再同期のtransport区分を契約テストで確認する
- [ ] 3.11 【徹平さん】LiveKit data eventを購読し、session／shot不一致、既読以下のsequence、期限切れeventを破棄したうえで、Agentと端末内の候補から工程不成立→欠け→構図→角度／しわ→品質→安定性の優先順で主指示を1件だけ選ぶselectorを実装し、時間ベースのenter／clear hysteresis、解消時の短い肯定、`READY`安定化をfake clockでテストする
- [ ] 3.12 【健太さん】2.10の固定ガイドと手動シャッターを、全画面camera、固定された進捗・戻る・help・light・shutter、アプリ所有の短い日本語案内へ拡張し、confidence／診断語を表示せず、助言変更で操作位置が動かず、`READY`以外でもraw Blobを撮影できることをcomponent／画像fixtureで確認する
- [ ] 3.13 【健太さん】権限待ち、各主指示、解消の肯定、ready、撮影中、検証中、撮り直し、再接続、offline、4slot進捗を決定的なStorybook fixtureで再現し、390×844、375×812、430×932、200%文字拡大、safe area、44px操作領域、`aria-live`抑制、reduced motion、visible focusをvisual／accessibility testで確認する
- [ ] 3.14 【徹平さん・健太さん】Room切断／Agent停止時にも固定ガイド、端末内品質助言、手動撮影、現在step、受理済みslotを維持し、有限回の自動再接続後は明示的な再試行を提示して、server snapshot同期後の新sequenceからだけ助言を再開する統合テストを通す
- [ ] 3.15 【徹平さん・健太さん】2.12のfile upload／解析不可fallbackを4slotへ拡張し、画像、判定、助言、測定状態をsession memoryとobject URLだけに保持してDB／`localStorage`／`IndexedDB`へ書き込まず、終了、unmount、`pagehide`、再取得時にcamera track、Room、object URLを解放し、`visibilitychange`後に古い映像へ`READY`を残さないlifecycleテストを通す

### 第3章完了ゲート

- [ ] 3.16 【友ちゃんさん・徹平さん・健太さん】fixture transportで正常、撮り直し、判定timeout、measurement未承認、古いevent、resize／回転、切断／再接続、解析不可、権限拒否を順に実行し、撮影済みBlob hash、現在step、採寸状態が不正に変わらず、4slot＋明示承認が揃う前はeditへ進めないことを1コマンドの回帰テストで確認する
- [ ] 3.17 【健太さん】基準iPhoneで端末内品質解析を4Hz／同時解析1、Agent意味判定を同時1で計測し、状態変化からUI表示までp95 500ms以内、prewarm済みの実OpenAI Realtimeで`observedAt`からAI助言表示までp95 1秒未満、待機queue最大1、provider／console未処理error 0件をログへ記録する

## 4. 撮影後AI判定

- [x] 4.1 【友ちゃんさん】Wardrobeのstrict schemaパターンを参考に`ShotAssessor`を実装し、`shotType`、`quality`、有限な`issues`、`missingShots`、`nextAction`の全fieldをruntime検証して、front／back／tag／unknown以外、measurement、未知enum、field欠落を拒否する契約テストを通す
- [ ] 4.2 【徹平さん】正面原本を変更せず解析コピーだけをEXIF回転・sRGB正規化する処理を実装し、向きと色空間が異なるfront／back／tag fixtureで、原本hash不変と解析画像の期待寸法・向きを確認する
- [x] 4.3 【友ちゃんさん】FastAPIの`POST /api/analyze-shot`へ`requestedShot: front|back|tag`、multipart、20秒timeout、MIME／size制限、runtime schema検証を実装し、measurement指定、schema不正、timeout時に進捗を変更しないAPI／統合テストを通す
- [ ] 4.4 【徹平さん】ライブ`READY`でも撮影後AIが`retry`なら、有限issueから最優先の理由と具体的な撮り直し方を1件表示して同じshotへ戻し、対象slotだけを未受理にして他slotを保持し、撮り直し後に届いた古いrequest結果を無視するUI／Reducerテストを通す
- [x] 4.5 【友ちゃんさん】live `ShotAssessor`がデモ時間内に安定しない場合は`PROVIDER_MODE=fixture`へ明示的に切り替える手順と継続／停止条件をrunbookへ記録し、live errorをfixture成功responseへ自動変換しない契約テストを通す

## 5. 50mmマーカーによる半自動採寸

- [ ] 5.1 【友ちゃんさん・健太さん】OpenCV.js／WASMのstable version、公式配布URL、checksum、Apache-2.0 license／noticeを固定し、依存がない環境でWorker初期化とchecksum照合を成功させる
- [ ] 5.2 【健太さん】外形50.0mm角・5mm黒枠・内側40.0mm角の白地からなる二重正方形マーカーを100%倍率で印刷し、外形4辺が各50mmであることを定規で確認して、印刷設定と実測結果をrunbookへ記録する。また、正常、欠け、複数、小さすぎ、遮蔽、衣類欠け、重なり、segmentation失敗、端点不正の採寸fixtureと既知scale／4端点／着丈／身幅をmanifestで目視確認する
- [ ] 5.3 【徹平さん・健太さん】tag受理後に、対象が半袖クルーネックTシャツであること、背面を上にして襟・袖・裾・しわを整えること、無地でコントラストのある同一平面へ実測済みマーカーを衣類から30mm以上離した右下に置くことをチェックリストで案内し、フード、襟付き、長袖、パンツ、スカート、ワンピースを対応対象として表示しない。準備完了後の`4/4 採寸`で衣類全体安全枠とマーカー枠、常時有効な手動シャッターを表示するUIテストを通す
- [ ] 5.4 【徹平さん】measurementのraw画像を`ShotAssessment`へ送らずOpenCV.js専用Workerへ渡し、二重輪郭候補抽出、四角形近似、四隅順序付け、homography／射影補正、50mm辺からのpx/cm計算を実装して、最短辺80px、端から16px超、短辺／長辺0.65以上、衣類との間隔24px以上、衣類全体収容の境界fixtureを有限な失敗codeへ分類するテストを通す
- [ ] 5.5 【友ちゃんさん】`POST /api/suggest-measurement-points`と`MeasurementLineProvider`を実装し、射影補正済み写真1枚から`lengthStart|lengthEnd|widthStart|widthEnd`の0〜1正規化座標だけをstrict schemaで返して、cm値、UI文言、画面遷移、範囲外／非有限座標、欠落、timeoutを拒否する契約テストを通す
- [ ] 5.6 【徹平さん】有効な4端点を補正面へ写像し、背面襟中央付け根から裾中央までの着丈と左右脇下間の平置き身幅をOpenCV.jsで計算して0.1cm単位で表示し、身幅を2倍しないこと、provider失敗時は衣類輪郭上の粗いdraftまたは利用者の4端点配置へ切り替わることを計算／fallbackテストで確認する
- [ ] 5.7 【徹平さん】着丈・身幅の4端点を44px以上の操作領域でドラッグ修正するたびに値と既存承認を再計算・解除し、画像外または衣類領域から大きく外れた端点では`ENDPOINTS_INVALID`として承認を無効にする。着丈20〜100cm／身幅20〜80cmの範囲外は再確認後の承認を残し、明示操作後だけ`approved_cv`となるUI／Reducerテストを通す
- [ ] 5.8 【健太さん】マーカー／segmentation／端点提案の失敗時に、front／back／tagと衣類全体が写ったmeasurement画像を保持し、有限な理由、具体的な撮り直し、4端点配置、着丈／身幅の手入力を提示する。衣類全体が写る場合だけ手入力可能にし、値設定だけでは承認せず明示操作後だけ`approved_manual`となり、live失敗をfixture成功へ置き換えないfallbackテストを通す
- [ ] 5.9 【徹平さん】OpenCV.jsをdedicated Workerへ遅延loadし、`ImageBitmap`または縮小`ImageData`を渡してmain threadを塞がず、requestId不一致／cancel後の結果を破棄し、連続撮影、再採寸、終了時にWorker、ImageBitmap、WASM objectを解放してメモリが増え続けないことを計測する
- [ ] 5.10 【徹平さん・健太さん】採寸準備、解析中、端点編集、範囲外警告、マーカー各失敗、手入力、CV承認、手入力承認をStorybook／interaction fixtureで確認し、代表Tシャツをメジャー実測して同じ服を3回撮影し、利用者補正・承認後の着丈と身幅が各±1.0cm以内になることを計測記録で確認する

## 6. 正面mask・背景生成

- [ ] 6.1 【友ちゃんさん】0.1で用意した`rembg[cpu,cli]==2.0.81`と`birefnet-general-lite`の製品名、固定version／commit、配布URL、license／noticeを`THIRD_PARTY_NOTICES.md`と照合したうえで、rembgをloopbackの7000番で`--threads 1 --no-ui`付きで起動し、fixture frontを本番と同じ`file`、`model=birefnet-general-lite`、`om=true`で送信して、元画像と同寸法のmask-only PNGが返るprewarm手順を再現する
- [ ] 6.2 【友ちゃんさん】FastAPIの`POST /api/remove-background`と`GarmentMasker`へ35秒timeout、`image/png`、元画像との寸法一致、空／全面mask検証を実装し、timeout、非PNG、寸法不一致、空、全面のfixtureがerrorとなり、不完全previewを承認可能にしないテストを通す
- [ ] 6.3 【友ちゃんさん】許可されたstyle IDを「空の撮影背景、真上視点、均一照明、人物・衣類・ハンガー・文字・ロゴなし」の固定promptへ変換する`BackgroundGenerator`を実装し、request spyで送信bodyがテキストだけで商品画像、mask、tag、measurement、binary fieldを含まないことをテストする
- [ ] 6.4 【健太さん】背景生成の60秒timeout、error、利用不能画像をfixtureで発生させ、4slot、front原本、承認済み採寸値を保持したまま再試行、ローカル固定背景、元画像採用を提示し、固定背景からpreviewへ継続できる統合テストを通す
- [ ] 6.5 【健太さん】maskと背景生成がfrontだけへ適用され、back／tag／measurementの原本、受理状態、測定端点、承認済み採寸値を変更しないことをBlob hashとstate snapshotで確認する

## 7. 合成・承認・保存

- [ ] 7.1 【徹平さん】native Canvas 2Dで背景、front原本、maskを同一寸法へ合成し、mask内が元画像RGB、mask外だけが背景になり、商品領域の色、柄、形、傷、汚れを再生成・レタッチしないことを画像fixtureのpixel比較で確認する
- [ ] 7.2 【徹平さん・健太さん】元画像と合成画像を同じaspect ratio／表示領域で比較できるUIを実装し、初期状態とmask異常時は未承認、元画像または現在の合成画像を明示選択した場合だけ承認済みとなり、選択変更と再生成で承認が適切に解除されるUI／visual testを通す
- [ ] 7.3 【徹平さん】明示選択された承認済みfrontだけを`toBlob`でPNGまたはJPEGとして保存し、MIME type、画像寸法、pixel hashが選択結果と一致し、back／tag／measurement／未承認preview／処理途中画像が出力されないことをテストする
- [ ] 7.4 【友ちゃんさん】背景生成がデモ時間内に安定しない場合は生成providerを停止して白背景1種へ固定する判断基準と復旧操作をrunbookへ記録し、比較、明示承認、保存のfixture経路を再実行して原本採用も可能なことを確認する

## 8. 統合検証・デモ準備

- [ ] 8.1 【健太さん】fixture E2Eを1コマンドで実行し、「Room接続→1件だけのAI助言→front／back／tag／measurement→理由付き撮り直し→採寸端点修正・承認→mask→背景→比較→明示承認→保存」を2回連続で完走させ、同じ最終state、採寸値、出力pixel hashを得る
- [ ] 8.2 【徹平さん・友ちゃんさん】代表Tシャツ1着と基準iPhoneで、camera publish、Agent subscribe、有限codeの変化、`READY`以外の手動撮影、front／back／tagの撮影後判定、measurement専用検証と4端点提案、補正・承認、front mask、背景生成、比較、承認、保存を操作し、結果と性能計測をrunbookへ記録する
- [ ] 8.3 【健太さん】順序逆転／期限切れevent、Room再接続、Agent停止、端末内解析不可、カメラ権限拒否、撮影後AI timeout、マーカー解析／端点提案失敗、rembg timeout／無効mask、背景生成失敗をfixtureで発生させ、現在step、受理済みslot、採寸値、原本が不正に変わらず、保持内容と再試行／手入力／代替操作がUIに示されるfallback統合テストを通す
- [ ] 8.4 【健太さん】実画像を使い、390×844、375×812、430×932のiPhone相当viewportとSafari実機で、full-bleed映像、safe area、browser chrome、ホームインジケータ、200%文字拡大、44px操作領域、visible focus、screen reader、reduced motion、背景復帰、長い日本語でも進捗、主指示、操作が重ならず固定位置を保つことをvisual QA記録で確認する
- [ ] 8.5 【徹平さん・健太さん】セッション終了後にcamera trackが`ended`、Roomが切断済み、Workerが終了済み、全object URLがrevoke済みで、DB／`localStorage`／`IndexedDB`に画像、判定、助言、測定点、採寸値がなく、back／tag／measurementが出品画像として保存されないことを確認する
- [ ] 8.6 【健太さん】LiveKit／Agent起動、OpenCV.js／WASM事前load、50mmマーカー印刷・実測、rembg prewarm、`/api/health`、`PROVIDER_MODE=fixture|live`切替、各timeoutと切断からの復旧手順をコピー可能なコマンドと期待結果付きでrunbookへ記載し、新しいterminal sessionから手順どおり再実行する
- [ ] 8.7 【健太さん】lockfileからclean installした環境でfrontend／Python backend／Agentを起動し、`npm run build`、`npm run typecheck`、frontend／Pythonのunit・contract・integration・E2E testを実行して失敗0件、browser console未処理error 0件を確認する
- [ ] 8.8 【友ちゃんさん】runbookへ全Requirement／Scenarioとtask番号、test名または実機確認手順の対応表を追加し、未対応Scenarioが0件であることを確認したうえで、`openspec validate "build-listing-photo-assistant-mvp" --type change --strict --no-interactive`を成功させる

## 9. Backend P0/P1 hardening

- [x] 9.1 【友ちゃんさん】LiveKit data command／RPCから有限schemaのshot変更を`AgentRuntime.set_shot`へ接続し、観測時shot generationで保留frameを隔離し、Agent再起動時のprocess epoch付きreliable snapshotを契約テストで確認する
- [x] 9.2 【友ちゃんさん】同一shot／codeの表示event dedupeとliveness heartbeatを分離し、live providerへ有限deadlineを設定してtimeout／errorを`AGENT_UNAVAILABLE`へ正規化し、未処理例外0件をテストする
- [x] 9.3 【友ちゃんさん】shot別guidance code allowlistと`ShotAssessment`のrequested shot／quality／issues／nextAction整合性をruntime検証し、矛盾したAI結果で進捗が変わらない契約テストを通す
- [x] 9.4 【友ちゃんさん】EXIF orientation適用後の解析寸法でmaskを検証し、正面原本RGBへmaskをalpha適用したRGBA PNG preview endpointと、透明／不透明領域およびmask面積のsanity testを実装する
- [x] 9.5 【健太さん】fixture frameだけで`observedAt→provider完了→backend publish`、同時推論数、queue深度、同一判定の表示event数を計測するbackend-only回帰テストを1コマンド化し、fixture p95 1秒以内を必須、任意のlive credentialsではp50／p95と1秒／2秒目標の判定を出力する
- [x] 9.6 【友ちゃんさん】OpenAI公式仕様に基づき、live guidanceをframeごとのResponses APIから、image input対応Realtime modelの単一WebSocket、prewarm、`conversation: none`、有限code完全一致、950ms以下deadlineへ設計変更し、OpenSpec strict validationを通す
- [x] 9.7 【友ちゃんさん】撮影セッション中に再利用するRealtime providerを実装し、接続確立、独立response、有限code validation、timeout／cancel、切断時再接続、明示closeを偽WebSocketの契約テストで確認する
- [x] 9.8 【友ちゃんさん】Agent workerへRealtime providerのprewarm／close lifecycleを接続し、同時response 1件、待機frame 1件、shot generation変更時の旧result破棄、同一codeの表示event抑制を統合テストで確認する
- [x] 9.9 【健太さん】backend-only verifierをRealtimeの同一session再利用へ対応させ、cold-startと`observedAt→provider完了→backend publish`を分離し、fixture gateとlive 20件以上のp50／p95／error数を1コマンドで出力する
- [x] 9.10 【友ちゃんさん・健太さん】実`OPENAI_API_KEY`で低遅延Realtime model、最大辺、JPEG品質、prompt、出力tokenを反復比較し、成功20件以上、error 0件、ウォーム状態の`observedAt→backend publish` p95 1,000ms未満を満たす組合せをproduction defaultへ固定して計測結果を記録する
- [x] 9.11 【友ちゃんさん】`GeometryGuidanceProvider`とmask／bbox classifierを実装し、最大連結成分の画像端1px接触→`SHOW_FULL_GARMENT`、span 0.42未満→`MOVE_CLOSER`、0.77超→`MOVE_FARTHER`、中心軸ずれ0.12超→`CENTER_GARMENT`の優先順をruntime validationと境界値テストで固定する。構図合格時はPASSとし、ローカル判定から`READY`を返さない
- [x] 9.12 【友ちゃんさん】rembg v2.0.81のprewarm済み`u2netp`をライブ構図mask専用providerとして接続し、450ms以下timeout、PNG／寸法／空／全面mask検証、固定model名を契約テストで確認する。配布ONNXのURL、checksum、上流Apache-2.0と配布物来歴の注意点をarchitecture／third-party noticeへ記録する
- [x] 9.13 【友ちゃんさん】`front|back`で同じ最新frameからgeometryとOpenAI Realtime semanticを並列に開始するhybrid analyzerを実装し、geometry補正を優先、PASS時だけsemanticを採用、tag／measurementはsemanticへ直結する。geometry失敗またはPASS時のsemantic失敗をAIの`READY`へfallbackせず`AGENT_UNAVAILABLE`へ正規化する。prewarm／close／new session、同時推論1、待機frame 1、shot generation fence、同一code heartbeatを回帰テストする
- [x] 9.14 【健太さん】公開衣類と正解maskから作った4構図各5件以上を本番と同じrembg HTTPとclassifierへ通し、provider error 0件、各code 5/5、全体20/20、誤`READY` 0件、ウォームp95 400ms未満を確認する。実OpenAI credentialを使うhybrid反復ではgeometry／semantic／合成／publishを分離計測し、成功20件以上、error 0件、`observedAt→backend publish` p95 1,000ms未満を合格条件にする。実測は構図ゲートp95 181.300〜241.293ms、実credentialのhybrid transport 20/20・error 0件・`observedAt→backend publish` p95 633.789ms、backend publish p95 0.244msだった
