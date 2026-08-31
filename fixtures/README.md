# Garment fixtures

このディレクトリは、撮影後判定・マスク処理の開発用 fixture です。実物商品を表すものではなく、デモと自動テストの入力として扱います。

| ファイル | 想定ケース |
|---|---|
| `garment/front.png` | 正常な正面写真 |
| `garment/back.png` | 正常な背面写真 |
| `garment/tag.png` | 正常なタグ写真 |
| `garment/dark.png` | 暗い写真による撮り直し |
| `garment/blur.png` | 低解像度から拡大したぼけ写真による撮り直し |
| `garment/wrong-shot.png` | 正面要求時に返すタグの誤種別 |
| `garment/known-*-mask.png` | 合成テスト用の既知商品 mask（白＝商品、黒＝背景） |

再生成する場合:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\generate-fixtures.ps1
```

2026-08-31 に front、back、tag、dark、blur、wrong-shot、既知 mask を目視確認済みです。rembg の mask-only prewarm には `garment/front.png` を使用します。
