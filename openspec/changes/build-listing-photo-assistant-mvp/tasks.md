## 0. Preflight（0:00〜0:30）

- [ ] 0.1 Python 3.11へ`rembg[cpu,cli]==2.0.81`を導入し、`birefnet-general-lite`を事前downloadして、mask-onlyのprewarm requestが成功することを確認する
- [ ] 0.2 front／back／tag、dark、blur、wrong-shotのfixture画像と既知maskを用意し、各fixtureを人間が目視確認する
- [ ] 0.3 Wardrobe `f44006c`、document-autocapture `e24df25`、rembg `b439167`を参照元として固定し、コードを限定移植するdocument-autocaptureのMIT全文・著作権・commitを`THIRD_PARTY_NOTICES.md`へ記載する

## 1. 最小の縦スライス（0:30〜2:00）

- [ ] 1.1 React + TypeScript + ViteとNode.js APIを作成し、`npm install`、型チェック、build、`/api/health`が成功することを確認する
- [ ] 1.2 `ShotAssessment`、`LiveCaptureAssessment`、撮影slot、provider errorの共有型とruntime schemaを定義し、未知値と欠落を拒否する単体テストを通す
- [ ] 1.3 型付き`useReducer`でfront→back→tag→editの状態遷移を実装し、撮り直しても他slotを保持する単体テストを通す
- [ ] 1.4 fixtureの`ShotAssessor`とupload UIを接続し、front／back／tagの3枚が揃うまで編集へ進めない垂直スライスを通す
- [ ] 1.5 T+2h時点でupload fixtureが完走しない場合、カメラ以外のUI装飾を止めて縦スライスを優先する

## 2. カメラとリアルタイム助言（2:00〜3:30）

- [ ] 2.1 document-autocaptureの`DEFAULT_VIDEO_CONSTRAINTS`、`start()`、`ensureVideoPlayback()`、`cleanupVideoStream()`を限定移植し、背面カメラ起動、権限拒否、track解放を実機で確認する
- [ ] 2.2 `scheduleNextFrame()`のrVFC→rAF→timerパターンを4Hz・同時解析1へ変更して実装し、中間フレームを蓄積しないテストを通す
- [ ] 2.3 固定ガイドを`object-fit`を考慮して映像PixelRoiへ変換する純粋関数を実装し、縦横比とクロップのfixtureテストを通す
- [ ] 2.4 document-autocaptureの`rgbaToGrayscale()`、`brightnessCheck()`、`laplacianVariance()`を出典付きで限定移植し、暗い／明るい／ぼけたfixtureの判定テストを通す
- [ ] 2.5 Quadベースの`StabilityTracker`を使わず連続ROIのframe-difference trackerを実装し、600ms安定と移動時resetのテストを通す
- [ ] 2.6 front／back用固定衣類ガイドとtag用矩形をvideo上へ表示し、raw撮影Blobにoverlayが含まれず、`READY`以外でも撮影できることを確認する
- [ ] 2.7 Worker／Canvas解析が使えない場合は固定ガイド＋手動撮影、カメラ権限拒否時はfile uploadへ戻れることを確認する

## 3. 撮影後AI Agent（3:30〜4:45）

- [ ] 3.1 Wardrobeの`normalizeImage()`の処理順を参考に、原本を保持したまま解析コピーだけをEXIF回転・sRGB化する処理を実装し、向きの異なるfixtureで確認する
- [ ] 3.2 Wardrobeの`openAIAnalyze()`を参考に、画像入力＋strict JSON Schemaの`ShotAssessor`を新規実装し、front／back／tag／unknownと問題コードの契約テストを通す
- [ ] 3.3 `POST /api/analyze-shot`へmultipart入力、20秒timeout、MIME／サイズ検証、runtime schema検証を追加し、失敗時に進捗が変わらないAPIテストを通す
- [ ] 3.4 ライブ`READY`でも撮影後AIが`retry`なら同じstepへ戻り、理由付きの案内になる統合テストを通す
- [ ] 3.5 T+5h時点でlive AIが不安定ならfixtureをデモ本線として明示し、live失敗を黙って成功へ変換しない

## 4. 正面画像のmaskと背景生成（4:45〜6:15）

- [ ] 4.1 rembgをloopbackの7000番で`--threads 1 --no-ui`起動し、`file`、`model=birefnet-general-lite`、`om=true`でPNG maskを返すことを確認する
- [ ] 4.2 `POST /api/remove-background`と`GarmentMasker`を実装し、35秒timeout、PNG、元画像との寸法一致、空／全面maskを検証するAPIテストを通す
- [ ] 4.3 `BackgroundGenerator`を実装し、許可styleから固定promptを作り、商品画像を送らずテキストだけで背景を生成する契約テストを通す
- [ ] 4.4 背景生成へ60秒timeoutと固定背景fallbackを追加し、失敗しても撮影slotと正面原本が保持されることを確認する
- [ ] 4.5 maskと背景生成を正面1枚だけへ適用し、backとtagの原本が変更されないテストを通す

## 5. 合成・承認・保存（6:15〜7:15）

- [ ] 5.1 native Canvas 2Dで背景、正面原本、maskを合成し、商品mask内が元画像RGB、mask外が背景になる画像fixtureテストを通す
- [ ] 5.2 元画像と合成画像の比較、初期未承認、合成採用、元画像採用を実装し、未承認previewを保存できないテストを通す
- [ ] 5.3 承認済み正面画像を`toBlob`でPNGまたはJPEG保存し、正しい画像だけが出力されることを確認する
- [ ] 5.4 T+6.5hで合成が完了していない場合、生成背景を止めて白背景1種へ固定し、比較・承認・保存を優先する

## 6. 統合検証とデモ準備（7:15〜8:00）

- [ ] 6.1 fixtureで「ライブ助言→front／back／tag→撮り直し→mask→背景→比較→承認→保存」を通し、各OpenSpec Scenarioとの対応を確認する
- [ ] 6.2 live providerで代表トップス1着を実機撮影し、4Hz解析、撮影後判定、正面mask、背景生成、合成を手動確認する
- [ ] 6.3 rembg prewarm、`/api/health`、fixture/live切替、ngrok起動、timeout時の操作をrunbookへ記載して再実行する
- [ ] 6.4 `npm run build`、型チェック、主要テスト、`openspec validate "build-listing-photo-assistant-mvp" --type change --strict --no-interactive`を成功させる
