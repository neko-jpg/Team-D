$ErrorActionPreference = 'Stop'

Add-Type -AssemblyName System.Drawing

$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$outputDirectory = Join-Path $root 'fixtures\garment'
New-Item -ItemType Directory -Force -Path $outputDirectory | Out-Null

function New-CardiganBitmap {
    param(
        [int]$Width = 800,
        [int]$Height = 800,
        [ValidateSet('front', 'back', 'tag', 'dark')]
        [string]$Kind = 'front'
    )

    $bitmap = [System.Drawing.Bitmap]::new($Width, $Height, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $graphics.CompositingQuality = [System.Drawing.Drawing2D.CompositingQuality]::HighQuality
    $graphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic

    if ($Kind -eq 'dark') {
        $background = [System.Drawing.Color]::FromArgb(30, 34, 38)
        $garment = [System.Drawing.Color]::FromArgb(67, 96, 77)
        $detail = [System.Drawing.Color]::FromArgb(47, 70, 56)
    } elseif ($Kind -eq 'tag') {
        $background = [System.Drawing.Color]::FromArgb(246, 242, 235)
        $garment = [System.Drawing.Color]::FromArgb(135, 165, 139)
        $detail = [System.Drawing.Color]::FromArgb(64, 82, 67)
    } else {
        $background = [System.Drawing.Color]::FromArgb(246, 242, 235)
        $garment = [System.Drawing.Color]::FromArgb(135, 165, 139)
        $detail = [System.Drawing.Color]::FromArgb(86, 111, 91)
    }

    $graphics.Clear($background)

    $floorPen = [System.Drawing.Pen]::new([System.Drawing.Color]::FromArgb(30, 145, 139, 125), [Math]::Max(1, [int]($Width / 400)))
    for ($offset = -$Height; $offset -lt $Width + $Height; $offset += [Math]::Max(36, [int]($Width / 12))) {
        $graphics.DrawLine($floorPen, $offset, 0, $offset + $Height, $Height)
    }
    $floorPen.Dispose()

    if ($Kind -eq 'tag') {
        $tagBrush = [System.Drawing.SolidBrush]::new($garment)
        $graphics.FillRectangle($tagBrush, [int]($Width * 0.16), [int]($Height * 0.12), [int]($Width * 0.68), [int]($Height * 0.76))
        $tagBrush.Dispose()
        $tagPen = [System.Drawing.Pen]::new($detail, [Math]::Max(2, [int]($Width / 160)))
        $graphics.DrawRectangle($tagPen, [int]($Width * 0.28), [int]($Height * 0.25), [int]($Width * 0.44), [int]($Height * 0.42))
        $graphics.DrawLine($tagPen, [int]($Width * 0.50), [int]($Height * 0.25), [int]($Width * 0.50), [int]($Height * 0.67))
        $tagPen.Dispose()
        $font = [System.Drawing.Font]::new('Arial', [Math]::Max(16, [int]($Width / 14)), [System.Drawing.FontStyle]::Bold)
        $textBrush = [System.Drawing.SolidBrush]::new($detail)
        $format = [System.Drawing.StringFormat]::new()
        $format.Alignment = [System.Drawing.StringAlignment]::Center
        $format.LineAlignment = [System.Drawing.StringAlignment]::Center
        $textRectangle = [System.Drawing.RectangleF]::new([single]($Width * 0.28), [single]($Height * 0.25), [single]($Width * 0.44), [single]($Height * 0.42))
        $graphics.DrawString('M', $font, $textBrush, $textRectangle, $format)
        $format.Dispose()
        $textBrush.Dispose()
        $font.Dispose()
        $graphics.Dispose()
        return $bitmap
    }

    $scale = $Width / 800.0
    $bodyPoints = @(
        ([System.Drawing.Point]::new([int](250 * $scale), [int](215 * $scale))),
        ([System.Drawing.Point]::new([int](550 * $scale), [int](215 * $scale))),
        ([System.Drawing.Point]::new([int](625 * $scale), [int](690 * $scale))),
        ([System.Drawing.Point]::new([int](175 * $scale), [int](690 * $scale)))
    )
    $leftSleeve = @(
        ([System.Drawing.Point]::new([int](255 * $scale), [int](245 * $scale))),
        ([System.Drawing.Point]::new([int](175 * $scale), [int](270 * $scale))),
        ([System.Drawing.Point]::new([int](70 * $scale), [int](535 * $scale))),
        ([System.Drawing.Point]::new([int](155 * $scale), [int](580 * $scale))),
        ([System.Drawing.Point]::new([int](300 * $scale), [int](355 * $scale)))
    )
    $rightSleeve = @(
        ([System.Drawing.Point]::new([int](545 * $scale), [int](245 * $scale))),
        ([System.Drawing.Point]::new([int](625 * $scale), [int](270 * $scale))),
        ([System.Drawing.Point]::new([int](730 * $scale), [int](535 * $scale))),
        ([System.Drawing.Point]::new([int](645 * $scale), [int](580 * $scale))),
        ([System.Drawing.Point]::new([int](500 * $scale), [int](355 * $scale)))
    )

    $garmentBrush = [System.Drawing.SolidBrush]::new($garment)
    $graphics.FillPolygon($garmentBrush, $leftSleeve)
    $graphics.FillPolygon($garmentBrush, $rightSleeve)
    $graphics.FillPolygon($garmentBrush, $bodyPoints)
    $garmentBrush.Dispose()

    $detailPen = [System.Drawing.Pen]::new($detail, [Math]::Max(2, [int]($Width / 160)))
    $graphics.DrawPolygon($detailPen, $bodyPoints)
    $graphics.DrawPolygon($detailPen, $leftSleeve)
    $graphics.DrawPolygon($detailPen, $rightSleeve)
    $graphics.DrawLine($detailPen, [int](400 * $scale), [int](225 * $scale), [int](400 * $scale), [int](690 * $scale))
    for ($y = 275; $y -lt 670; $y += 42) {
        $graphics.DrawLine($detailPen, [int](375 * $scale), [int]($y * $scale), [int](425 * $scale), [int]($y * $scale))
    }
    if ($Kind -eq 'back') {
        $graphics.DrawArc($detailPen, [int](315 * $scale), [int](170 * $scale), [int](170 * $scale), [int](100 * $scale), 0, 180)
        $graphics.DrawLine($detailPen, [int](260 * $scale), [int](335 * $scale), [int](540 * $scale), [int](335 * $scale))
    } else {
        $graphics.DrawArc($detailPen, [int](345 * $scale), [int](175 * $scale), [int](110 * $scale), [int](85 * $scale), 0, 180)
    }
    $detailPen.Dispose()
    $graphics.Dispose()
    return $bitmap
}

function Save-Png {
    param([System.Drawing.Bitmap]$Bitmap, [string]$Path)
    $Bitmap.Save($Path, [System.Drawing.Imaging.ImageFormat]::Png)
    $Bitmap.Dispose()
}

function New-KnownMask {
    param(
        [int]$Width = 800,
        [int]$Height = 800,
        [ValidateSet('front', 'back', 'tag')]
        [string]$Kind = 'front',
        [string]$Path
    )

    $mask = [System.Drawing.Bitmap]::new($Width, $Height, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $graphics = [System.Drawing.Graphics]::FromImage($mask)
    $graphics.Clear([System.Drawing.Color]::Black)
    $white = [System.Drawing.SolidBrush]::new([System.Drawing.Color]::White)
    if ($Kind -eq 'tag') {
        $graphics.FillRectangle($white, [int]($Width * 0.16), [int]($Height * 0.12), [int]($Width * 0.68), [int]($Height * 0.76))
    } else {
        $scale = $Width / 800.0
        $body = @(
            ([System.Drawing.Point]::new([int](250 * $scale), [int](215 * $scale))),
            ([System.Drawing.Point]::new([int](550 * $scale), [int](215 * $scale))),
            ([System.Drawing.Point]::new([int](625 * $scale), [int](690 * $scale))),
            ([System.Drawing.Point]::new([int](175 * $scale), [int](690 * $scale)))
        )
        $left = @(
            ([System.Drawing.Point]::new([int](255 * $scale), [int](245 * $scale))),
            ([System.Drawing.Point]::new([int](175 * $scale), [int](270 * $scale))),
            ([System.Drawing.Point]::new([int](70 * $scale), [int](535 * $scale))),
            ([System.Drawing.Point]::new([int](155 * $scale), [int](580 * $scale))),
            ([System.Drawing.Point]::new([int](300 * $scale), [int](355 * $scale)))
        )
        $right = @(
            ([System.Drawing.Point]::new([int](545 * $scale), [int](245 * $scale))),
            ([System.Drawing.Point]::new([int](625 * $scale), [int](270 * $scale))),
            ([System.Drawing.Point]::new([int](730 * $scale), [int](535 * $scale))),
            ([System.Drawing.Point]::new([int](645 * $scale), [int](580 * $scale))),
            ([System.Drawing.Point]::new([int](500 * $scale), [int](355 * $scale)))
        )
        $graphics.FillPolygon($white, $body)
        $graphics.FillPolygon($white, $left)
        $graphics.FillPolygon($white, $right)
    }
    $white.Dispose()
    $graphics.Dispose()
    Save-Png $mask $Path
}

Save-Png (New-CardiganBitmap -Kind 'front') (Join-Path $outputDirectory 'front.png')
Save-Png (New-CardiganBitmap -Kind 'back') (Join-Path $outputDirectory 'back.png')
Save-Png (New-CardiganBitmap -Kind 'tag') (Join-Path $outputDirectory 'tag.png')
Save-Png (New-CardiganBitmap -Kind 'dark') (Join-Path $outputDirectory 'dark.png')

$smallBlur = New-CardiganBitmap -Width 160 -Height 160 -Kind 'front'
$blur = [System.Drawing.Bitmap]::new(800, 800, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)
$blurGraphics = [System.Drawing.Graphics]::FromImage($blur)
$blurGraphics.InterpolationMode = [System.Drawing.Drawing2D.InterpolationMode]::HighQualityBicubic
$blurGraphics.DrawImage($smallBlur, 0, 0, 800, 800)
$blurGraphics.Dispose()
$smallBlur.Dispose()
Save-Png $blur (Join-Path $outputDirectory 'blur.png')

# This is intentionally a tag close-up used as a wrong-shot response when front is requested.
Save-Png (New-CardiganBitmap -Kind 'tag') (Join-Path $outputDirectory 'wrong-shot.png')

New-KnownMask -Kind 'front' -Path (Join-Path $outputDirectory 'known-front-mask.png')
New-KnownMask -Kind 'back' -Path (Join-Path $outputDirectory 'known-back-mask.png')
New-KnownMask -Kind 'tag' -Path (Join-Path $outputDirectory 'known-tag-mask.png')

Write-Output "Generated fixture images in $outputDirectory"
