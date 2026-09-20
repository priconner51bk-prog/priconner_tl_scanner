$ErrorActionPreference = 'Continue'
$env:PYTHONIOENCODING = 'utf-8'
$env:PRICONNER_TEST_POST = '1'
$pages = @(1, 21, 41)
$log = Join-Path (Get-Location) 'backfill_youtube_period.log'
Remove-Item $log -ErrorAction SilentlyContinue

foreach ($day in 23..30) {
    $date = [DateTime]::ParseExact("2026-08-$('{0:00}' -f $day)", 'yyyy-MM-dd', $null)
    $next = $date.AddDays(1)
    $env:PRICONNER_YOUTUBE_PERIOD_START = $date.ToString('yyyy-MM-ddT03:00:00+00:00')
    $env:PRICONNER_YOUTUBE_PERIOD_END = if ($day -eq 30) { '2026-08-30T15:00:00+00:00' } else { $next.ToString('yyyy-MM-ddT03:00:00+00:00') }
    $env:PRICONNER_YOUTUBE_SEARCH_DATE_AFTER = $date.ToString('yyyyMMdd')
    $env:PRICONNER_YOUTUBE_SEARCH_DATE_BEFORE = $next.ToString('yyyyMMdd')

    foreach ($page in $pages) {
        $env:PRICONNER_YOUTUBE_SEARCH_START = "$page"
        "$(Get-Date -Format o) DAY=$day PAGE=$page START" | Tee-Object -FilePath $log -Append
        python youtube_search.py 2>&1 | Tee-Object -FilePath $log -Append
        $code = $LASTEXITCODE
        "$(Get-Date -Format o) DAY=$day PAGE=$page EXIT=$code" | Tee-Object -FilePath $log -Append
    }
}

'backfill complete' | Tee-Object -FilePath $log -Append
