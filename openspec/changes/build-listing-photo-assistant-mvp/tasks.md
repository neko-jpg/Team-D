## 担当方針

- 友ちゃんさん：LiveKit Agent、AI／API契約、画像処理、fallback判断
- 徹平さん：React、カメラ、Room接続、AR overlay、状態遷移、合成・保存
- 健太さん：fixture、契約／統合テスト、runbook、ライセンス

## 0. Preflight（0:00〜0:30）

- [ ] 0.1 【友ちゃんさん】LiveKit projectとAgent用環境変数を用意し、最小接続スクリプトを1回実行して、browser participantとPython Agentのidentityが同じRoomのparticipant一覧と接続ログの両方で確認できることを検証する
- [ ] 0.2 【友ちゃんさん】LiveKit Agents／Python SDKとLiveKit JS SDKの互換性があるstable versionをそれぞれlockfileへ固定し、依存がない環境でlockfileからのclean installとSDK importが成功することを検証する
- [ ] 0.3 【友ちゃんさん】Python 3.11へ`rembg[cpu,cli]==2.0.81`を導入し、`birefnet-general-lite`のdownloadとmask-only prewarm requestが成功することを確認する
- [ ] 0.4 【健太さん】front／back／tag、dark、blur、wrong-side、cropped、mask、背景、`GuidanceEvent`のfixtureと期待結果manifestを用意し、目視結果とmanifestの期待code／受理可否／次stepが一致することを確認する
- [ ] 0.5 【健太さん】LiveKit Apache-2.0、document-autocapture MIT、rembg／BiRefNetの製品名、固定version／commit、URL、license全文または必要なnoticeを`THIRD_PARTY_NOTICES.md`へ記録し、固定済み依存と記載値が一致することを確認する

## 1. 最小の縦スライス（0:30〜1:30）

- [ ] 1.1 【徹平さん・友ちゃんさん】React + TypeScript + Viteと、FastAPI／LiveKit Agentを共有packageにしたPython backendの最小構成を作り、lockfileからのinstall、TypeScript型チェック、frontend build、Python import、`/api/health`とAgent起動ログの全てを成功させる
- [ ] 1.2 【友ちゃんさん】`GuidanceEvent`、`ShotAssessment`、撮影slot、接続状態、provider errorの共有契約を定義し、未知値と欠落を拒否するテストを通す
- [ ] 1.3 【徹平さん】型付き`useReducer`でfront→back→tag→editを実装し、AIの`message`や`nextAction`だけでは遷移せず、受理済みslotから次stepを再計算し、撮り直しや再接続で別slotを失わないReducerテストを通す
- [ ] 1.4 【徹平さん】fixtureのguidance／`ShotAssessment`とupload UIを接続し、現在shot、受理済みslot、残りslotを表示しながらfront→back→tagを進め、3枚が`quality: "ok"`で揃うまで編集開始操作が無効であるUI統合テストを通す
- [ ] 1.5 【徹平さん】画像、mask、`ShotAssessment`、`GuidanceEvent`をセッションmemoryとobject URLだけで保持し、DB／`localStorage`／`IndexedDB`へ書き込まないこと、セッション終了時にobject URLとcamera trackを解放することをテストする
- [ ] 1.6 【友ちゃんさん】T+1.5hでfixture縦スライスが未完ならUI装飾を停止し、Room→guidance push→撮影→出力の本線を優先する判断を行い、継続タスクと停止タスクをrunbookに記録する

## 2. LiveKitリアルタイムAI助言（1:30〜3:30）

- [ ] 2.1 【友ちゃんさん】`POST /api/livekit-token`で、設定値を上限とする有効期限、sessionに対応するRoom、一意なparticipant identity、camera publishと必要なdata通信だけの権限を持つtokenを発行し、decodeしたclaimの期待値と、LiveKit API secretがresponseとbrowser bundleのどちらにも含まれないことをテストする
- [ ] 2.2 【徹平さん】LiveKit JS SDKでRoomへ接続し、背面camera trackをpublishして、接続状態をUIへ表示する実機確認を行う
- [ ] 2.3 【友ちゃんさん】Python LiveKit AgentをRoom participantとして起動し、最新のcamera video trackだけへsubscribeできることをログとfixture trackで確認する
- [ ] 2.4 【友ちゃんさん】frame arrivalごとに上書きするcapacity 1のlatest-frame slotと同時推論1件のprocessorを実装し、推論中に3frame以上を流すfixtureで、queue長が1を超えず、解放後に最新frameだけが次に処理されるテストを通す
- [ ] 2.5 【友ちゃんさん】現在shotと縮小frameを受ける`VisionGuidanceProvider`を実装し、`MOVE_CLOSER|MOVE_FARTHER|CENTER_GARMENT|SHOW_FULL_GARMENT|WRONG_SIDE|MOVE_TO_TAG|HOLD_STEADY|READY`の有限codeとconfidenceへ正規化し、未知code・欠落・対象shot不一致を拒否する契約テストを通す
- [ ] 2.6 【友ちゃんさん】`GuidanceStateMachine`でsessionごとの単調増加sequence、`observedAt`、`expiresAt`、同一shot／codeのdeduplicationを付与し、短命な助言はlossy packet、step／受理／再同期は採用したreliable packetまたはRPCのどちらかに契約を固定して、transport種別とpayloadの契約テストを通す
- [ ] 2.7 【徹平さん】data eventを購読し、session／shot不一致、既読以下のsequence、期限切れeventを破棄して、有効な助言だけをAR overlayへ表示するテストを通す
- [ ] 2.8 【徹平さん】document-autocaptureのカメラ制御と品質primitiveを出典コメント付きで限定移植し、`object-fit`のクロップを反映したROIが常に最大辺320px以下になること、輝度45〜215、Laplacian分散24以上、frame delta 0.020未満が600ms継続という初期閾値で暗い／明るい／ぼけ／移動／安定fixtureを期待hintへ分類するテストを通す
- [ ] 2.9 【徹平さん】現在shotと撮影進捗に合わせてfront／back用固定ガイドまたはtag用矩形をvideo上に表示し、`TOO_DARK`／`TOO_BLURRY`／`HOLD_STEADY`中でもシャッターが有効で、撮影したraw Blobの画像fixtureにoverlay画素が含まれないことを実機と画像fixtureで確認する
- [ ] 2.10 【徹平さん・健太さん】Room切断／Agent停止時に固定ガイド、端末内品質助言、手動撮影、撮影済みslotを維持し、再接続後に現在shotから再同期する統合テストを通す
- [ ] 2.11 【健太さん】基準端末で端末内品質解析を4Hz／同時解析1で実行し、端末内の状態変化とAI助言をそれぞれ20件以上計測して、状態変化からUI反映までのp95が500ms以内、AI助言の`observedAt`からUI表示までのp95が2秒以内であることを計測ログで確認し、目標外時もqueueを増やさないことを確認する
- [ ] 2.12 【徹平さん・健太さん】カメラ権限拒否時は端末内画像uploadで同じfront→back→tagフローを継続でき、Canvas／Worker解析不可時は`ANALYZER_UNAVAILABLE`を表示しつつ固定ガイド、Agent助言、手動撮影を維持するfallback統合テストを通す

## 3. 撮影後の最終AI判定（3:30〜4:30）

- [ ] 3.1 【友ちゃんさん】Wardrobeのstrict schemaパターンを参考に`ShotAssessor`を実装し、`shotType`、`quality`、`issues`、`missingShots`、`nextAction`の全fieldをruntime検証して、front／back／tag／unknown、欠け、品質不良を受理し、未知enumとfield欠落を拒否する契約テストを通す
- [ ] 3.2 【徹平さん】原本を保持した解析コピーのEXIF回転・sRGB正規化を実装し、向きの異なるfixtureで確認する
- [ ] 3.3 【友ちゃんさん】`POST /api/analyze-shot`へ20秒timeout、MIME／size制限、runtime schema検証を実装し、失敗時に進捗が変わらないテストを通す
- [ ] 3.4 【徹平さん】ライブ`READY`でも撮影後AIが`retry`なら、`issues`の有限codeに対応する理由と撮り直し方を表示して同じshotへ戻すUI／Reducer遷移を実装し、対象slotは未受理、別slotは保持されるテストを通す
- [ ] 3.5 【友ちゃんさん】T+4.5hでlive modelが不安定なら`PROVIDER_MODE=fixture`へ明示的に切り替え、継続／停止するproviderをrunbookに記録し、live providerのerrorをfixtureの成功responseへ自動変換しない契約テストを通す

## 4. 50mmマーカー付き半自動採寸（4:30〜5:30、背景処理と並行）

- [ ] 4.1 【徹平さん】`tag`受理後に「背面を上にする」「しわを伸ばす」「無地で衣類とコントラストのある床を使う」「50mmマーカーを右下の同一平面に置く」という採寸準備UIを表示し、準備完了前はmeasurementシャッターを表示しないUIテストを通す
- [ ] 4.2 【徹平さん】measurementの衣類全体安全枠と右下50mmマーカー配置枠を表示し、ガイドを含まないraw画像を撮影後AI判定へ送り、`shotType: "measurement"`／`quality: "ok"`の場合だけ解析へ進む統合テストを通す
- [ ] 4.3 【徹平さん】OpenCV.js Workerでグレースケール化、四角形contour抽出、四隅順序付け、homography／perspective transform、50mm辺からのpx/cm計算を実装し、正常fixtureで既知縮尺に一致し、未検出／欠け／複数／重なり／四隅不正／強い遠近歪みを拒否するfixtureテストを通す
- [ ] 4.4 【徹平さん】補正後画像の衣類contour／bounding boxから、背面襟中央付け根→裾中央の着丈と、左右脇下間の平置き身幅の初期端点を提案し、身幅を2倍せず0.1cm単位で表示する計算fixtureテストを通す
- [ ] 4.5 【徹平さん】着丈・身幅の4端点をドラッグ修正するたびに数値を再計算し、初期／修正後は未承認、利用者の明示承認後だけ`ApprovedMeasurement`となり編集開始できるUI／Reducerテストを通す
- [ ] 4.6 【健太さん】マーカー解析失敗時にfront／back／tagを保持したまま理由付き撮り直しと着丈／身幅の手入力を提示し、自動推定値やfixture成功へ黙って置き換えないfallbackテストを通す
- [ ] 4.7 【徹平さん・健太さん】デモ対象トップスをメジャーで実測し、利用者補正／承認後の着丈と身幅が各±1.0cm以内であることを計測記録で確認する

## 5. 正面maskと背景生成（4:30〜6:00、採寸と並行）

- [ ] 4.1 【友ちゃんさん】rembgをloopbackの7000番で`--threads 1 --no-ui`を付けて起動し、fixture frontを本番と同じ`file`、`model=birefnet-general-lite`、`om=true`のformで送信し、元画像と同寸法のmask-only PNGが返るprewarm requestを成功させる
- [ ] 4.2 【友ちゃんさん】`POST /api/remove-background`へ35秒timeout、`image/png`、元画像との寸法一致、空／全面mask検証を実装し、timeout、非PNG、寸法不一致、空、全面の各fixtureがerrorとなり、不完全previewが承認可能状態へ進まないテストを通す
- [ ] 4.3 【友ちゃんさん】許可されたstyle IDを「空の撮影背景、真上視点、均一照明、人物・衣類・ハンガー・文字・ロゴなし」の固定promptへ変換する`BackgroundGenerator`を実装し、request spyで生成APIの送信bodyがテキストだけで商品画像／mask／tag／binary fieldを含まないことをテストする
- [ ] 4.4 【健太さん】背景生成の60秒timeout／error／利用不能画像の各fixtureで、進捗、撮影slot、front原本を保持したまま、再試行とローカル固定背景の選択肢が表示され、固定背景でpreviewへ継続できるテストを通す
- [ ] 4.5 【健太さん】maskと背景生成がfrontだけに適用され、backとtagの原本を変更しないテストを通す

## 5. 合成・承認・保存（6:00〜7:00）

- [ ] 5.1 【徹平さん】native Canvas 2Dで背景、front原本、maskを合成し、mask内が元画像RGB、mask外が背景になる画像fixtureテストを通す
- [ ] 5.2 【徹平さん】元画像と合成画像を同じaspect ratio／表示領域で切り替えまたは並列比較できるUIを実装し、初期状態とmask異常時は未承認、元画像または合成画像を明示選択した場合だけ承認済みとなり、未承認previewは保存できないUIテストを通す
- [ ] 5.3 【徹平さん】明示選択された承認済みfrontだけを`toBlob`でPNGまたはJPEGとして保存し、出力のMIME type、画像寸法、pixel hashが選択した元画像または合成fixtureと一致し、back／tag／未承認previewが出力されないことを確認する
- [ ] 5.4 【友ちゃんさん】T+6.5hで背景生成が不安定なら生成providerを停止して白背景1種へ固定し、判断と操作手順をrunbookに記録して、比較・明示承認・保存のfixture経路を再実行する

## 6. 統合検証とデモ準備（7:00〜8:00）

- [ ] 6.1 【健太さん】fixture E2Eを1コマンドで実行し、「Room接続→AI助言push→front／back／tag→理由付き撮り直し→mask→背景→比較→明示承認→保存」を2回連続で完走させ、同じ最終stateと出力pixel hashが得られることを確認する
- [ ] 6.2 【徹平さん・友ちゃんさん】代表トップス1着と基準端末で、camera publish、Agent subscribe、欠け／距離／表裏／タグ移動に応じた有限codeの変化、`READY`以外の手動撮影、撮影後判定、front mask、背景生成、合成、承認を操作し、結果と計測ログをrunbookに記録する
- [ ] 6.3 【健太さん】順序逆転／期限切れevent、Room再接続、Agent停止、端末内解析不可、撮影後AI timeout、rembg timeout／無効mask、背景生成失敗をそれぞれfixtureで発生させ、現在stepと受理済みslotが不変で、理由、再試行、または安全な代替操作がUIに表示されるfallback統合テストを通す
- [ ] 6.4 【健太さん】LiveKit／Agent起動、rembg prewarm、`/api/health`、`PROVIDER_MODE=fixture|live`切替、各timeoutと切断からのデモ復旧手順をrunbookへコピー可能なコマンドと期待結果付きで記載し、新しいterminalセッションから手順どおり再実行する
- [ ] 6.5 【健太さん】lockfileからのclean install後に`npm run build`、`npm run typecheck`、frontend／Python backendのテストスイートを実行し、失敗0件であることを確認する
- [ ] 6.6 【友ちゃんさん】runbookへ「Requirement／Scenario → task番号 → test名または実機確認手順」の対応表を追加し、全Scenarioに対応先があることを確認した上で、`openspec validate "build-listing-photo-assistant-mvp" --type change --strict --no-interactive`を成功させる
