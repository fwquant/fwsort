# D2 阶段截图脚本（演示/生产模式对照 + 关键功能页）
$ErrorActionPreference = "Continue"
$edge = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
if (-not (Test-Path $edge)) {
    $edge = "C:\Program Files\Microsoft\Edge\Application\msedge.exe"
}

# D2 关键页面（含演示模式 + 总榜 + 详情 + 任务页）
$pages = @(
    @{ name="d2_index_demo";       url="http://localhost:8000/demo" },
    @{ name="d2_accounts_demo";    url="http://localhost:8000/demo/accounts" },
    @{ name="d2_follow_demo";      url="http://localhost:8000/demo/follow" },
    @{ name="d2_detail_demo";      url="http://localhost:8000/demo/detail?uid=ACC-DEMO1000" },
    @{ name="d2_profile_demo";     url="http://localhost:8000/demo/profile" },
    @{ name="d2_rental_demo";      url="http://localhost:8000/demo/rental" }
)

$viewports = @(
    @{ name="pc";     w=1366; h=900;  scale=1 },
    @{ name="mobile"; w=420;  h=1100; scale=2 }
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
            "--virtual-time-budget=8000",
            "--screenshot=$f",
            $pg.url
        )
        & $edge $args 2>$null
    }
}

Write-Host "Done!"
Get-ChildItem $out | Where-Object { $_.Name -like "d2_*" }
