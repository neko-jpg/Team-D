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
* [ ] 2.5 【健太さん】PixelRoi変換の純粋関数を実装し、縦横比とクロップのfixtureテストを通す
* [x] 2.6 【友ちゃんさん】document-autocaptureの`rgbaToGrayscale()`、`brightnessCheck()`、`laplacianVariance()`の採用基準と閾値方針を決める
* [ ] 2.7 【健太さん】上記画像品質判定を出典付きで限定移植し、暗い／明るい／ぼけたfixtureの判定テストを通す
* [x] 2.8 【友ちゃんさん】Quadベースの`StabilityTracker`を使わず、連続ROIのframe-differenceで600ms安定を見る方式を確定する
* [x] 2.9 【徹平さん】frame-difference trackerを実装し、600ms安定と移動時resetのテストを通す
* [ ] 2.10 【健太さん】front／back用固定衣類ガイドとtag用矩形をvideo上へ表示し、raw撮影Blobにoverlayが含まれず、`READY`以外でも撮影できることを確認する
* [x] 2.11 【友ちゃんさん】Worker／Canvas解析不可時の固定ガイド＋手動撮影、カメラ権限拒否時のfile upload fallbackを仕様として確定する
* [ ] 2.12 【健太さん】各fallbackが実際に動くことを確認する

## 3. 撮影後AI Agent（3:30〜4:45）

* [ ] 3.1 【友ちゃんさん】撮影後AIの責務、入力画像、返却schema、問題コード、retry条件を確定する
* [ ] 3.2 【友ちゃんさん】Wardrobeの`normalizeImage()`の処理順を参考に、原本を保持したまま解析コピーだけをEXIF回転・sRGB化する処理方針を定義する
* [ ] 3.3 【徹平さん】解析コピーの正規化処理を実装し、向きの異なるfixtureで確認する
* [ ] 3.4 【友ちゃんさん】Wardrobeの`openAIAnalyze()`を参考に、画像入力＋strict JSON Schemaの`ShotAssessor`契約を定義する
* [ ] 3.5 【友ちゃんさん】`ShotAssessor`を実装し、front／back／tag／unknownと問題コードの契約テストを通す
* [ ] 3.6 【友ちゃんさん】`POST /api/analyze-shot`のmultipart、20秒timeout、MIME／サイズ検証、runtime schema検証を実装する
* [ ] 3.7 【健太さん】API失敗時に進捗が変わらないテストを通す
* [ ] 3.8 【徹平さん】ライブ`READY`でも撮影後AIが`retry`なら同じstepへ戻り、理由付きの案内になるUI・状態遷移を実装する
* [ ] 3.9 【友ちゃんさん】T+5h時点でlive AIが不安定ならfixtureをデモ本線へ切り替える判断を行い、live失敗を黙って成功へ変換しない方針を維持する

## 4. 正面画像のmaskと背景生成（4:45〜6:15）

* [ ] 4.1 【友ちゃんさん】mask生成、背景生成、fallbackの責務分離とインターフェースを確定する
* [ ] 4.2 【友ちゃんさん】rembgをloopbackの7000番で`--threads 1 --no-ui`起動し、`file`、`model=birefnet-general-lite`、`om=true`でPNG maskを返すことを確認する
* [ ] 4.3 【友ちゃんさん】`POST /api/remove-background`と`GarmentMasker`を実装し、35秒timeout、PNG、元画像との寸法一致、空／全面maskを検証する
* [ ] 4.4 【友ちゃんさん】`BackgroundGenerator`の許可style、固定prompt、商品画像を送らずテキストだけで生成する契約を定義・実装する
* [ ] 4.5 【友ちゃんさん】背景生成60秒timeoutと固定背景fallbackの条件を定義する
* [ ] 4.6 【健太さん】背景生成失敗時でも撮影slotと正面原本が保持されることを確認する
* [ ] 4.7 【健太さん】maskと背景生成が正面1枚だけへ適用され、backとtagの原本が変更されないテストを通す

## 5. 合成・承認・保存（6:15〜7:15）

* [ ] 5.1 【友ちゃんさん】原本・mask・背景・preview・approved imageのデータフローと保存条件を確定する
* [ ] 5.2 【徹平さん】native Canvas 2Dで背景、正面原本、maskを合成し、商品mask内が元画像RGB、mask外が背景になる画像fixtureテストを通す
* [ ] 5.3 【徹平さん】元画像と合成画像の比較、初期未承認、合成採用、元画像採用を実装し、未承認previewを保存できないテストを通す
* [ ] 5.4 【徹平さん】承認済み正面画像を`toBlob`でPNGまたはJPEG保存し、正しい画像だけが出力されることを確認する
* [ ] 5.5 【友ちゃんさん】T+6.5hで合成が完了していない場合、生成背景を停止して白背景1種へ固定する判断を行い、比較・承認・保存を優先する

## 6. 統合検証とデモ準備（7:15〜8:00）

* [ ] 6.1 【友ちゃんさん】OpenSpec Scenarioと実装・テストの対応関係を最終確認する
* [ ] 6.2 【健太さん】fixtureで「ライブ助言→front／back／tag→撮り直し→mask→背景→比較→承認→保存」を通す
* [ ] 6.3 【徹平さん・友ちゃんさん】live providerで代表トップス1着を実機撮影し、4Hz解析、撮影後判定、正面mask、背景生成、合成を手動確認する
* [ ] 6.4 【健太さん】rembg prewarm、`/api/health`、fixture/live切替、ngrok起動、timeout時の操作をrunbookへ記載して再実行する
* [ ] 6.5 【健太さん】`npm run build`、型チェック、主要テストを成功させる
* [ ] 6.6 【友ちゃんさん】`openspec validate "build-listing-photo-assistant-mvp" --type change --strict --no-interactive`を成功させ、仕様とのズレがないことを最終確認する
