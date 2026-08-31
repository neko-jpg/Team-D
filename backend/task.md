# 未完了バックエンドタスク

## 目的

元の`openspec/changes/build-listing-photo-assistant-mvp/tasks.md`で未完了になっている作業から、`backend/**`のPythonバックエンド実装だけを抽出する。

furima-sandboxへの統合作業は考慮しない。React、Vite、UI、ブラウザ処理など、統合時に使用しない可能性があるフロントエンド実装を進めないための派生タスクリストとする。

## 抽出条件

- 元の`tasks.md`で`[ ]`の項目だけを対象にする。
- `backend/**`の実装またはバックエンド単体テストで完結する作業だけを対象にする。
- 元の`tasks.md`で`[x]`の項目は再タスク化しない。
- 編集してよいファイルは`backend/**`だけとする。
- テストを追加・変更する場合も`backend/tests/**`へ置く。
- React、Vite、CSS、カメラ、UI、Reducer、OpenCV.js、Canvas、Storybook、E2E、Runbook、依存ファイルは対象外とする。
- 既存の`backend/**`実装が受け入れ条件を満たしている場合は、不要な再実装を行わない。
- この派生タスクの完了は、元の複合UI／統合タスク全体の完了を意味しない。元タスクから抽出したバックエンド部分だけを完了対象とする。
- 画像decode、EXIF反映、ICC／sRGB変換に必要なライブラリは実行環境から提供される前提とする。利用できない場合も`backend/**`外の依存ファイルは変更せず、依存追加を別作業として報告する。

## 担当方針

- ともちゃん：provider契約、strict schema、外部サービス境界、response検証
- 健太：FastAPI route、dependency接続、fixture、バックエンド単体テスト

## 1. 撮影後AI用の解析コピー正規化（元タスク4.2）

- [ ] 1.1 【ともちゃん】front／back／tagのupload bytesから、原本bytesを変更せず、EXIF orientationを反映してsRGBへ変換した解析専用コピーを生成する処理を`backend/**`へ実装する。decode不能、未対応形式、変換失敗を明示的なエラーにし、原本を上書きしない。

- [ ] 1.2 【健太】`POST /api/analyze-shot`でraw uploadではなく正規化済み解析コピーだけを`ShotAssessor`へ渡すよう`backend/**`内で接続する。正規化失敗時はproviderを呼ばず、有限なAPI errorを返す。

- [ ] 1.3 【健太】向きと色空間が異なるfront／back／tag fixtureを使い、入力bytesのhash不変、出力の期待寸法・向き・sRGB、およびproviderへ渡ったbytesが正規化済みであることを`backend/tests/**`で確認する。

## 2. 採寸端点提案API（元タスク5.5）

- [ ] 2.1 【ともちゃん】`MeasurementLineProvider`の入力・出力契約を`backend/**`へ実装する。射影補正済みmeasurement画像1枚を受け、`lengthStart|lengthEnd|widthStart|widthEnd`の0〜1正規化座標だけをstrict schemaで返す。cm値、UI文言、画面遷移、未知field、欠落、範囲外座標、`NaN`／`Infinity`を拒否する。

- [ ] 2.2 【健太】FastAPIの`POST /api/suggest-measurement-points`を`backend/**`へ実装し、`MeasurementLineProvider`をdependencyとして接続する。画像入力、timeout、provider error、schema不正を検証し、失敗をfixture成功へ自動変換しない。

- [ ] 2.3 【健太】正常な4端点、未知field、欠落、範囲外／非有限座標、timeoutを検証するcontract／APIテストを`backend/tests/**`へ追加する。

## 3. 正面mask API（元タスク6.2）

- [ ] 3.1 【ともちゃん】`GarmentMasker`を`backend/**`へ実装し、front原本をrembgへmultipartの`file`、`model=birefnet-general-lite`、`om=true`で送ってmask-only PNGを取得する。35秒timeout、`image/png`、元画像との寸法一致、空mask、全面maskを検証し、不正なmaskを成功として返さない。

- [ ] 3.2 【健太】FastAPIの`POST /api/remove-background`を`backend/**`へ実装し、`GarmentMasker`をdependencyとして接続する。timeout、非PNG、寸法不一致、空mask、全面maskを有限なAPI errorとして返す。

- [ ] 3.3 【健太】正常maskと各異常fixtureを使ったcontract／APIテストを`backend/tests/**`へ追加し、不完全なmaskが成功responseにならないことを確認する。

## 4. 背景生成provider（元タスク6.3、元タスク6.4のバックエンド部分）

- [ ] 4.1 【ともちゃん】`BackgroundGenerator`を`backend/**`へ実装する。許可されたstyle IDだけを「空の撮影背景、真上視点、均一照明、人物・衣類・ハンガー・文字・ロゴなし」の固定promptへ変換し、外部providerへテキストだけを送る。60秒timeout、provider error、decode不能または利用不能な生成画像を明示的な失敗として扱う。

- [ ] 4.2 【健太】`BackgroundGenerator`の決定的fixtureとrequest spyテストを`backend/tests/**`へ追加する。送信bodyに商品画像、mask、tag、measurement、binary fieldが含まれず、未知style ID、timeout、provider error、decode不能または利用不能画像が拒否されることを確認する。

## 完了条件

- [ ] 【ともちゃん】元タスク4.2、5.5、6.2、6.3と、元タスク6.4から抽出したprovider部分が`backend/**`だけで実装されていることを確認する。
- [ ] 【健太】`.venv/bin/python -m pytest -q backend/tests`を実行し、抽出したバックエンド部分の正常系・異常系がすべて成功することを確認する。

## 抽出対象外とした未完了タスク

カメラ、overlay、CaptureReducer、LiveKit JS、ブラウザ再接続、OpenCV.js Worker、採寸UI、Canvas合成、画像保存、Storybook、モバイル実機、Runbook、E2Eはフロントエンドまたはリポジトリ横断作業のため、このタスクリストには含めない。

元タスク6.1はrembg sidecarの起動、ライセンス照合、prewarm再現という運用検証であり、`backend/**`内のPython実装ではないため対象外とする。元タスク6.4のうち、4slotや原本を保持したUI状態、再試行・固定背景・元画像採用の表示と遷移も対象外とする。
