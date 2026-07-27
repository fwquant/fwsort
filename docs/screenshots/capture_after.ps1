# Capture screenshots of all pages at PC and mobile sizes (after)
$ErrorActionPreference = "Continue"
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) {
    $edge = "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
}

$pages = @(
    @{ name="index"; url="http://localhost:8000/" },
    @{ name="detail"; url="http://localhost:8000/detail?uid=DEMO_001" },
    @{ name="accounts"; url="http://localhost:8000/accounts" },
    @{ name="follow"; url="http://localhost:8000/follow" },
    @{ name="rental"; url="http://localhost:8000/rental" },
    @{ name="admin"; url="http://localhost:8000/admin" }
)

$viewports = @(
    @{ name="pc"; w=1366; h=900; scale=1 },
    @{ name="mobile"; w=420; h=1100; scale=2 }
)

$out = "d:\git\github\fwquant\fwsort\docs\screenshots\after"
New-Item -ItemType Directory -Path $out -Force | Out-Null

foreach ($vp in $viewports) {
    foreach ($pg in $pages) {
        $f = "$out\$($pg.name)_$($vp.name).png"
        Write-Host "Capturing $($pg.name) $($vp.name)..."
        $args = @(
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--no-sandbox",
            "--window-size=$($vp.w),$($vp.h)",
            "--force-device-scale-factor=$($vp.scale)",
            "--virtual-time-budget=6000",
            "--screenshot=$f",
            $pg.url
        )
        & $edge $args 2>$null
    }
}

Write-Host "Done!"
Get-ChildItem $out
