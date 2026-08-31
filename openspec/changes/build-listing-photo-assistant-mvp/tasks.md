## 担当方針

- 友ちゃんさん：LiveKit Agent、AI／API契約、画像処理、fallback判断
- 徹平さん：React、カメラ、Room接続、AR overlay、状態遷移、採寸UI、合成・保存
- 健太さん：fixture、契約／統合テスト、実測、runbook、ライセンス

## 0. Preflight（0:00〜0:30）

- [ ] 0.1 【友ちゃんさん】LiveKit projectとAgent用環境変数を用意し、最小接続スクリプトを1回実行して、browser participantとPython Agentのidentityが同じRoomのparticipant一覧と接続ログの両方で確認できることを検証する
- [ ] 0.2 【友ちゃんさん・徹平さん】LiveKit Agents／Python SDK、LiveKit JS SDK、OpenCV.js／WASMの互換性があるstable versionをlockfileへ固定し、依存がない環境でclean install、SDK import、OpenCV.js Worker初期化、公式配布checksum照合が成功することを検証する
- [ ] 0.3 【友ちゃんさん】Python 3.11へ`rembg[cpu,cli]==2.0.81`を導入し、`birefnet-general-lite`のdownloadとmask-only prewarm requestが成功することを確認する
- [ ] 0.4 【健太さん】front／back／tag／measurement、dark、blur、wrong-side、cropped、正常および`MARKER_MISSING|MARKER_MULTIPLE|MARKER_TOO_SMALL|MARKER_OCCLUDED|GARMENT_OUT_OF_FRAME|GARMENT_MARKER_OVERLAP|SEGMENTATION_FAILED|ENDPOINTS_INVALID`となる採寸画像、既知の着丈・身幅と4端点、mask、背景、`GuidanceEvent`のfixtureと期待結果manifestを用意し、目視結果と期待code／受理可否／次step／採寸値が一致することを確認する
- [ ] 0.5 【健太さん】LiveKitとOpenCV.jsのApache-2.0、document-autocaptureのMIT、rembg／BiRefNetの製品名、固定version／commit、URL、checksum、license全文または必要なnoticeを`THIRD_PARTY_NOTICES.md`へ記録し、固定済み依存と記載値が一致することを確認する
- [ ] 0.6 【健太さん】外形50.0mm角・5mm黒枠・内側40.0mm角の白地からなる二重正方形マーカーを100%倍率で印刷し、外形4辺が各50mmであることを定規で確認して、印刷設定と実測結果をrunbookへ記録する

## 1. 最小の縦スライス（0:30〜1:30）

- [ ] 1.1 【徹平さん・友ちゃんさん】React + TypeScript + Viteと、FastAPI／LiveKit Agentを共有packageにしたPython backendの最小構成を作り、ルートから`npm run dev:fixture`、`npm run dev:live`、`npm run verify:chapter2`を実行できるようにして、lockfile install、TypeScript型チェック、frontend build、Python import、`/api/health`、Agent起動ログを成功させる
- [ ] 1.2 【友ちゃんさん】`GuidanceEvent`、front／back／tagだけを扱う`ShotAssessment`、4つの撮影slot、正規化4端点だけを返す`MeasurementPointSuggestion`、`MeasurementDraft`／`ApprovedMeasurement`、接続状態、provider errorの共有契約を定義する。採寸値は着丈・身幅、端点は各2点、表示値は0.1cm単位に限定し、未知値・欠落・非有限値・範囲外座標を拒否する契約テストを通す
- [ ] 1.3 【徹平さん】型付き`useReducer`で`front→back→tag→measurement準備→measurement撮影→採寸確認→採寸承認→edit`を実装し、AIの`message`や`nextAction`だけでは遷移せず、受理済みslotと採寸承認状態から次stepを再計算し、撮り直しや再接続で別slotを失わないReducerテストを通す
- [ ] 1.4 【徹平さん】fixtureのguidance／`ShotAssessment`／採寸結果とupload UIを接続し、`1/4 正面→2/4 背面→3/4 タグ→4/4 採寸`を表示しながら進め、4枚の受理と採寸の明示承認が揃うまで編集開始操作が無効であるUI統合テストを通す
- [ ] 1.5 【徹平さん】画像、mask、`ShotAssessment`、`GuidanceEvent`、測定端点、採寸値をセッションmemoryとobject URLだけで保持し、DB／`localStorage`／`IndexedDB`へ書き込まないこと、セッション終了時にobject URL、camera track、OpenCV.js Workerを解放することをテストする
- [ ] 1.6 【友ちゃんさん】T+1.5hでfixture縦スライスが未完ならUI装飾を停止し、Room→guidance push→4枚撮影→採寸確認・承認→出力の本線を優先する判断を行い、継続タスクと停止タスクをrunbookに記録する

## 2. LiveKitリアルタイムAI助言と第2章完了ゲート（1:30〜4:00）

- [ ] 2.1 【友ちゃんさん】`POST /api/livekit-token`で、設定値を上限とする有効期限、sessionに対応するRoom、一意なparticipant identity、camera publishと必要なdata通信だけの権限を持つtokenを発行し、decodeしたclaimの期待値と、LiveKit API secretがresponseとbrowser bundleのどちらにも含まれないことをテストする
- [ ] 2.2 【徹平さん】LiveKit JS SDKでRoomへ接続し、背面camera trackをpublishして、接続状態をUIへ表示する実機確認を行う
- [ ] 2.3 【友ちゃんさん】Python LiveKit AgentをRoom participantとして起動し、最新のcamera video trackだけへsubscribeできることをログとfixture trackで確認する
- [ ] 2.4 【友ちゃんさん】frame arrivalごとに上書きするcapacity 1のlatest-frame slotと同時推論1件のprocessorを実装し、推論中に3frame以上を流すfixtureで、queue長が1を超えず、解放後に最新frameだけが次に処理されるテストを通す
- [ ] 2.5 【友ちゃんさん】現在shotと縮小frameを受ける`VisionGuidanceProvider`を実装し、`MOVE_CLOSER|MOVE_FARTHER|CENTER_GARMENT|SHOW_FULL_GARMENT|WRONG_SIDE|MOVE_TO_TAG|PLACE_MARKER|MARKER_NOT_VISIBLE|FLATTEN_GARMENT|CAMERA_OVERHEAD|HOLD_STEADY|READY`の有限codeとconfidenceへ正規化し、未知code・欠落・対象shot不一致を拒否する契約テストを通す
- [ ] 2.6 【友ちゃんさん】`GuidanceStateMachine`でsessionごとの単調増加sequence、`observedAt`、`expiresAt`、同一shot／codeのdeduplicationを付与し、短命な助言はlossy packet、step／受理／再同期は採用したreliable packetまたはRPCのどちらかに契約を固定して、transport種別とpayloadの契約テストを通す
- [ ] 2.7 【徹平さん】data eventを購読し、session／shot不一致、既読以下のsequence、期限切れeventを破棄して、有効な助言だけをAR overlayへ表示するテストを通す
- [ ] 2.8 【徹平さん】document-autocaptureのカメラ制御と品質primitiveを出典コメント付きで限定移植し、`object-fit`のクロップを反映したROIが常に最大辺320px以下になること、輝度45〜215、Laplacian分散24以上、frame delta 0.020未満が600ms継続という初期閾値で暗い／明るい／ぼけ／移動／安定fixtureを期待hintへ分類するテストを通す
- [ ] 2.9 【徹平さん】現在shotと進捗に合わせてfront／back用固定ガイド、tag用矩形、measurement用の衣類全体安全枠と右下50mmマーカー枠をvideo上に表示し、`TOO_DARK`／`TOO_BLURRY`／`HOLD_STEADY`中でもシャッターが有効で、raw Blobへoverlay画素が含まれないことを実機と画像fixtureで確認する
- [ ] 2.10 【徹平さん・健太さん】Room切断／Agent停止時に固定ガイド、端末内品質助言、手動撮影、撮影済みslotを維持し、再接続後に現在shotから再同期する統合テストを通す
- [ ] 2.11 【健太さん】基準端末で端末内品質解析を4Hz／同時解析1で実行し、端末内の状態変化とAI助言をそれぞれ20件以上計測して、状態変化からUI反映までのp95が500ms以内、AI助言の`observedAt`からUI表示までのp95が2秒以内であることを計測ログで確認し、目標外時もqueueを増やさないことを確認する
- [ ] 2.12 【徹平さん・健太さん】カメラ権限拒否時は端末内画像uploadで同じ4枚の撮影・採寸フローを継続でき、Canvas／Worker解析不可時は`ANALYZER_UNAVAILABLE`と着丈・身幅の手入力を表示しつつ固定ガイド、Agent助言、手動撮影を維持するfallback統合テストを通す

### 第2章完了ゲート（2.1〜2.12完了後に順番どおり実行）

> **目的:** 正常系・撮り直し・解析失敗・カメラ失敗のいずれでも、撮影済みデータと採寸結果を不当に失わず、`front → back → tag → measurement → 採寸確認・承認 → edit`を仕様どおり完走できることを保証する。また、必須4写真または採寸承認が欠けている間はeditへ進めないことを保証する。

このゲートでは、3章の実`ShotAssessor`と4章の実OpenCV.js／`MeasurementLineProvider`はまだ接続せず、決定的な`ShotAssessment`／`MeasurementDraft` fixtureを使う。これにより、後続処理を追加した際に撮影フローの不具合と新規処理の不具合を分離する。

- [ ] 2.13 【健太さん】テスト対象のcommit hash、実施日時、担当者、基準端末、OS、ブラウザversion、`PROVIDER_MODE`をrunbookへ記録し、clean checkout相当の状態からlockfile install後にTerminal Aで`npm run dev:fixture`を起動して、frontend、`/api/health`、fixture Agent接続、browser consoleの未処理error 0件を確認する
- [ ] 2.14 【健太さん】Terminal Bで`npm run verify:chapter2`を実行し、型チェック、frontend build、Python import／test、Reducer、契約、ROI、品質解析、UI統合、fixture E2Eの失敗が0件であることを確認する。境界値として輝度44／45／215／216、Laplacian分散23.99／24、frame delta 0.0199／0.020、安定時間599ms／600msを含め、`PixelRoi`が`object-fit`のcrop後も映像座標内かつ最大辺320px以下であることを確認する
- [ ] 2.15 【徹平さん・健太さん】新規fixture sessionを開始して`1/4 正面`、全slot空、edit無効を確認し、front-ok→back-ok→tag-okの順に投入して各段階で既受理slotのBlob hashが不変であることを確認する。tag受理後はeditへ進まず`4/4 採寸`を表示し、measurement-ok投入後も採寸結果が`needs_review`の間はedit無効、測定線と数値を明示承認した後だけedit有効となり、編集開始でeditへ遷移することを確認する
- [ ] 2.16 【徹平さん・健太さん】front-ok受理後にback-retry、back判定timeout、back-ok、tag-wrong-side、tag-ok、measurement-invalid、measurement-okの順でfixtureを投入し、retry／timeout中は現在stepが変わらず、別slotのBlob hashと採寸状態が不変で、AIの不正な`nextAction: COMPLETE`だけでは遷移しないことを確認する。measurement解析失敗では4枚目と先行3slotを保持して撮り直しまたは手入力を選べ、手入力値の明示承認後にだけeditへ到達することを確認する
- [ ] 2.17 【友ちゃんさん・徹平さん】fake clockとfixture transportで、有効な現在session／shotの新sequenceだけが表示され、別session、別shot、既読以下、期限切れeventが助言・step・slotを変えないことを確認する。4Hz schedulerへ解析時間を超えるframeを流し、同時解析数1、待機queue最大1、処理完了後は最新frameだけを解析し、安定条件が599msまでは`HOLD_STEADY`、600msで`READY`、resize／回転／ROI変更で安定履歴がresetされることを確認する
- [ ] 2.18 【徹平さん・友ちゃんさん・健太さん】Terminal Aを停止してTerminal Cで`npm run dev:live`を起動し、基準端末でカメラを許可してRoomのbrowser participant、camera track publish、Agent subscribeを両側ログで確認する。front／back／tag／measurementの各ガイドと現在stepを確認し、`READY`以外でも手動撮影でき、raw Blobにoverlay画素が含まれないこと、端末内状態変化とAI助言を各20件以上採取してp95目標を満たすことを確認する
- [ ] 2.19 【徹平さん・健太さん】同一sessionで順にAgent停止、Room切断と再接続、Canvas無効、Worker無効、カメラ権限拒否を発生させる。各ケースで受理済みslot、現在step、採寸draftが不変で、Agent停止／Room切断時は固定ガイド・端末内助言・手動撮影、解析不可時は`ANALYZER_UNAVAILABLE`と安全な代替操作、権限拒否時はfile uploadが残り、upload＋採寸fixture／手入力でeditまで完走できることを確認する
- [ ] 2.20 【健太さん】セッション終了操作後にcamera trackが`ended`、Roomが切断済み、Workerが終了済み、全object URLがrevoke済みで、DB／`localStorage`／`IndexedDB`に画像・判定・測定点・採寸値がないことを確認する。2.13〜2.19について下表形式で期待結果、実測、証跡、判定をrunbookへ記録し、必須ケース失敗0件、撮影済みデータ消失0件、意図しないstep遷移0件、consoleの未処理error 0件を満たした場合だけ第3章へ進む

| Test ID | Mode | Expected | Actual | Evidence | Result |
|---|---|---|---|---|---|
| 2.13〜2.20 | fixture／live | 期待するstep・slot・表示・計測値 | 実測結果 | log／screenshot／video | PASS／FAIL |

## 3. 撮影後の最終AI判定（3:30〜4:30）

- [ ] 3.1 【友ちゃんさん】Wardrobeのstrict schemaパターンを参考に`ShotAssessor`を実装し、`shotType`、`quality`、`issues`、`missingShots`、`nextAction`の全fieldをruntime検証する。front／back／tag／unknownだけを扱い、measurementをこのschemaへ混在させず、未知enumとfield欠落を拒否する契約テストを通す
- [ ] 3.2 【徹平さん】原本を保持した解析コピーのEXIF回転・sRGB正規化を実装し、向きの異なるfront／back／tag fixtureで確認する
- [ ] 3.3 【友ちゃんさん】`POST /api/analyze-shot`へ`requestedShot: front|back|tag`、20秒timeout、MIME／size制限、runtime schema検証を実装し、measurement指定を拒否し、失敗時に進捗が変わらないテストを通す
- [ ] 3.4 【徹平さん】ライブ`READY`でも撮影後AIが`retry`なら、`issues`の有限codeに対応する理由と撮り直し方を表示して同じshotへ戻すUI／Reducer遷移を実装し、対象slotは未受理、別slotは保持されるテストを通す
- [ ] 3.5 【友ちゃんさん】T+4.5hでlive modelが不安定なら`PROVIDER_MODE=fixture`へ明示的に切り替え、継続／停止するproviderをrunbookに記録し、live providerのerrorをfixtureの成功responseへ自動変換しない契約テストを通す

## 4. 50mmマーカー付き半自動採寸（4:30〜5:30、背景処理と並行）

- [ ] 4.1 【徹平さん】`tag`受理後に、採寸対象が半袖クルーネックTシャツであることと「背面を上にする」「襟・袖・裾を広げる」「しわを伸ばす」「無地で衣類とコントラストのある床を使う」「実測済み二重正方形マーカーを30mm以上離した右下の同一平面に置く」という採寸準備UIを表示し、準備完了前はmeasurementシャッターを表示しないUIテストを通す
- [ ] 4.2 【徹平さん】measurementの衣類全体安全枠と右下50mm専用マーカー配置枠を表示してraw画像を取得し、`POST /api/analyze-shot`へ送らずOpenCV.js Workerへ渡し、採寸専用validationを通った場合だけ射影補正へ進む統合テストを通す
- [ ] 4.3 【徹平さん】OpenCV.js Workerで二重輪郭のマーカー候補抽出、四角形近似、四隅順序付け、homography／perspective transform、外形50mm辺からのpx/cm計算を実装する。最短辺80px以上、四隅が画像端から16px超、短辺／長辺0.65以上、衣類との画像上間隔24px以上を検証し、失敗を有限コードへ分類するfixtureテストを通す
- [ ] 4.4 【友ちゃんさん】`POST /api/suggest-measurement-points`と`MeasurementLineProvider`を実装し、射影補正済み写真1枚から`lengthStart|lengthEnd|widthStart|widthEnd`の0〜1正規化座標とconfidenceだけをstrict schemaで返す。cm値・UI文言・画面遷移を返さず、schema不正、範囲外座標、timeoutを拒否する契約テストを通す
- [ ] 4.5 【徹平さん】AIの4端点を補正面へ写像し、背面襟中央付け根→裾中央の着丈と左右脇下間の平置き身幅をOpenCV.jsで計算して、身幅を2倍せず0.1cm単位で表示する。provider失敗時は衣類輪郭上の粗いdraftまたは利用者の4端点配置へ切り替える計算・fallbackテストを通す
- [ ] 4.6 【徹平さん】着丈・身幅の4端点をドラッグ修正するたびに数値を再計算し、画像外または衣類領域から大きく外れた端点では承認を無効にする。20〜100cmの着丈または20〜80cmの身幅は範囲外警告と再確認を要求し、明示承認後だけ`approved_cv`となるUI／Reducerテストを通す
- [ ] 4.7 【健太さん】マーカー／segmentation／端点提案の失敗時にfront／back／tagと4枚目を保持したまま有限な理由、具体的な撮り直し、4端点配置、着丈／身幅の手入力を提示し、手入力承認を`approved_manual`として区別し、成功fixtureへ黙って置き換えないfallbackテストを通す
- [ ] 4.8 【徹平さん・健太さん】デモ対象の半袖クルーネックTシャツをメジャーで実測し、同じ服を3回撮影して利用者補正・承認後の着丈と身幅が各±1.0cm以内であることを計測記録で確認する

## 5. 正面maskと背景生成（4:30〜6:00、採寸と並行）

- [ ] 5.1 【友ちゃんさん】rembgをloopbackの7000番で`--threads 1 --no-ui`を付けて起動し、fixture frontを本番と同じ`file`、`model=birefnet-general-lite`、`om=true`のformで送信し、元画像と同寸法のmask-only PNGが返るprewarm requestを成功させる
- [ ] 5.2 【友ちゃんさん】`POST /api/remove-background`へ35秒timeout、`image/png`、元画像との寸法一致、空／全面mask検証を実装し、timeout、非PNG、寸法不一致、空、全面の各fixtureがerrorとなり、不完全previewが承認可能状態へ進まないテストを通す
- [ ] 5.3 【友ちゃんさん】許可されたstyle IDを「空の撮影背景、真上視点、均一照明、人物・衣類・ハンガー・文字・ロゴなし」の固定promptへ変換する`BackgroundGenerator`を実装し、request spyで生成APIの送信bodyがテキストだけで商品画像／mask／tag／measurement／binary fieldを含まないことをテストする
- [ ] 5.4 【健太さん】背景生成の60秒timeout／error／利用不能画像の各fixtureで、進捗、4つの撮影slot、front原本、承認済み採寸値を保持したまま、再試行とローカル固定背景の選択肢が表示され、固定背景でpreviewへ継続できるテストを通す
- [ ] 5.5 【健太さん】maskと背景生成がfrontだけに適用され、back／tag／measurementの原本と承認済み採寸値を変更しないテストを通す

## 6. 合成・承認・保存（6:00〜7:00）

- [ ] 6.1 【徹平さん】native Canvas 2Dで背景、front原本、maskを合成し、mask内が元画像RGB、mask外が背景になる画像fixtureテストを通す
- [ ] 6.2 【徹平さん】元画像と合成画像を同じaspect ratio／表示領域で切り替えまたは並列比較できるUIを実装し、初期状態とmask異常時は未承認、元画像または合成画像を明示選択した場合だけ承認済みとなり、未承認previewは保存できないUIテストを通す
- [ ] 6.3 【徹平さん】明示選択された承認済みfrontだけを`toBlob`でPNGまたはJPEGとして保存し、出力のMIME type、画像寸法、pixel hashが選択した元画像または合成fixtureと一致し、back／tag／measurement／未承認previewが画像出力されないことを確認する
- [ ] 6.4 【友ちゃんさん】T+6.5hで背景生成が不安定なら生成providerを停止して白背景1種へ固定し、判断と操作手順をrunbookに記録して、比較・明示承認・保存のfixture経路を再実行する

## 7. 統合検証とデモ準備（7:00〜8:00）

- [ ] 7.1 【健太さん】fixture E2Eを1コマンドで実行し、「Room接続→AI助言push→front／back／tag／measurement→理由付き撮り直し→採寸端点修正・承認→mask→背景→比較→明示承認→保存」を2回連続で完走させ、同じ最終state、採寸値、出力pixel hashが得られることを確認する
- [ ] 7.2 【徹平さん・友ちゃんさん】代表Tシャツ1着と基準端末で、camera publish、Agent subscribe、欠け／距離／表裏／タグ移動／マーカー配置に応じた有限codeの変化、`READY`以外の手動撮影、front／back／tagの撮影後判定、measurementの採寸専用検証と4端点提案、補正・承認、front mask、背景生成、合成、承認を操作し、結果と計測ログをrunbookに記録する
- [ ] 7.3 【健太さん】順序逆転／期限切れevent、Room再接続、Agent停止、端末内解析不可、撮影後AI timeout、マーカー解析失敗、rembg timeout／無効mask、背景生成失敗をそれぞれfixtureで発生させ、現在step、受理済みslot、採寸値が不正に変わらず、理由、再試行、手入力、または安全な代替操作がUIに表示されるfallback統合テストを通す
- [ ] 7.4 【健太さん】LiveKit／Agent起動、OpenCV.js／WASM事前load、50mmマーカー印刷・実測、rembg prewarm、`/api/health`、`PROVIDER_MODE=fixture|live`切替、各timeoutと切断からのデモ復旧手順をrunbookへコピー可能なコマンドと期待結果付きで記載し、新しいterminalセッションから手順どおり再実行する
- [ ] 7.5 【健太さん】lockfileからのclean install後に`npm run build`、`npm run typecheck`、frontend／Python backendのテストスイートを実行し、失敗0件であることを確認する
- [ ] 7.6 【友ちゃんさん】runbookへ「Requirement／Scenario → task番号 → test名または実機確認手順」の対応表を追加し、採寸を含む全Scenarioに対応先があることを確認した上で、`openspec validate "build-listing-photo-assistant-mvp" --type change --strict --no-interactive`を成功させる
