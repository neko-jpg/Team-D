# 衣類採寸OSSの採用境界

最終更新: 2026-08-31

## 結論

1日MVPでは、採寸対象を**平置きの半袖クルーネックTシャツ**、項目を**着丈・身幅**に限定する。撮影は正面・背面・タグの後に、解析専用の採寸写真を1枚追加する。

```text
1/4 正面 → 2/4 背面 → 3/4 タグ
→ 採寸準備 → 4/4 採寸
→ 4端点を補正・承認
→ 背景編集
```

採寸は次の分担で実装する。

| 担当 | 役割 |
|---|---|
| OpenCV.js | 50mm専用マーカー検出、射影補正、px/cm換算、4端点間の距離計算 |
| 撮影後画像AI | 補正済み写真から襟ぐり中央、裾中央、左右脇下の4端点だけを0〜1座標で提案 |
| 利用者 | 4端点をドラッグ補正し、着丈・身幅を明示承認。失敗時は手入力 |

画像AIはcm値を返さない。OpenCV.jsだけでは「襟ぐり中央」「脇下」の意味を安定して識別できないため、輪郭だけの完全自動採寸を成功条件にしない。

## 採用: OpenCV.js

- 公式docs: [OpenCV.js](https://docs.opencv.org/4.x/d5/d10/tutorial_js_root.html)
- source: [opencv/opencv](https://github.com/opencv/opencv)
- license: Apache-2.0
- 導入: 実装開始時の公式stable OpenCV.js／WASMをURL・checksum固定し、Web Workerで実行

使うAPI:

- `cvtColor`、threshold／Canny
- `findContours`、`approxPolyDP`、`contourArea`
- `getPerspectiveTransform`、`warpPerspective`
- 点間距離と座標変換

使わない部分:

- DNN／学習済みモデル
- ArUcoなど配布buildに含まれる保証がないmodule
- native Python／server API
- camera UI、汎用衣類分類
- OpenCVの結果だけによる採寸確定

## 専用マーカー

ArUcoではなく、外形50.0mm角、5mm黒枠、内側40.0mm角の白地からなる二重正方形を使う。100%倍率で印刷し、撮影前に定規で外形1辺を確認する。衣類と同じ平面の右下へ30mm以上離して置く。

初期受入条件:

- マーカー最短辺80px以上
- 全四隅が画像端から16px超
- 最短辺／最長辺0.65以上
- 衣類との画像上の間隔24px以上
- 衣類とマーカーの全体が画角内

## 採用しないOSS

| OSS | 判断 | 代替 |
|---|---|---|
| [GarmentIQ](https://github.com/lygitdata/GarmentIQ) | PyTorchと複数モデルが1日MVPには過剰。画像上の距離をcmへするscaleも別途必要 | 画像AIの4端点＋OpenCV.js＋利用者補正 |
| [cloth-measure](https://github.com/oliver603-juang/cloth-measure) | 実装アイデアは近いが、READMEのMIT表記に対して独立LICENSEがなく、ArUco／専用撮影板前提 | 公式OpenCV.js APIだけで専用マーカー処理を新規実装 |
| [Shaku Garment Measurement](https://github.com/shakuai/Garment-Measurement) | 公開部分は外部API client中心で、採寸backend／modelがない | 採用しない |
| [AI MEASURE公開repo](https://github.com/riamitsu/aimeasure_public) | アプリ本体の実装を含まない | UX参考のみ |
| ARKit系Ruler | iOS nativeでWeb MVPへ直接流用できない | 固定2Dガイド＋平面マーカー |

## MVP成功条件

- 4枚目の採寸写真を省略できない。
- 3枚が揃っただけでは背景編集へ進めない。
- `approved_cv`または`approved_manual`になって初めて撮影・採寸完了とする。
- 自動線の正解ではなく、「ドラフト→補正→承認」または手入力で完走できることを必須とする。
- 代表Tシャツでは、補正・承認後の着丈・身幅がメジャー実測±1.0cm以内を目標とする。
