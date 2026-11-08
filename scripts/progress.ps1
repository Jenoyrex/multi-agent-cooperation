# Shows .claude/progress.json as a progress bar and milestone list. Read-only; Windows PowerShell 5.1+.
#   powershell -ExecutionPolicy Bypass -File scripts\progress.ps1
$path = Join-Path $PSScriptRoot '..\.claude\progress.json'
if (-not (Test-Path $path)) { Write-Host "No progress file at $path"; exit 1 }
$p = Get-Content $path -Raw -Encoding UTF8 | ConvertFrom-Json
$m = @($p.milestones)
$done = @($m | Where-Object { $_.status -eq 'done' }).Count
$failing = @($p.defects).Count + @(@($p.tests.python, $p.tests.javascript) | Where-Object { $_.status -eq 'fail' }).Count
$pct = [math]::Floor(100 * $done / $m.Count)
if ($failing -gt 0 -and $pct -eq 100) { $pct = 99 }  # never 100% with a known failure
$width = 40; $fill = [math]::Floor($width * $pct / 100)
Write-Host ''
Write-Host ("  {0}  (loop {1}, updated {2})" -f $p.project, $p.loop, $p.updated)
Write-Host ('  [' + ('#' * $fill) + ('-' * ($width - $fill)) + "] $pct%  ($done of $($m.Count) milestones verified)") -ForegroundColor Cyan
foreach ($t in @('python', 'javascript')) {
  $x = $p.tests.$t
  $c = @{ pass = 'Green'; fail = 'Red'; pending = 'Yellow' }[$x.status]
  Write-Host ("  Tests {0,-10} {1,-8} latest {2} (baseline {3})" -f $t, $x.status, $(if ($null -eq $x.latest) { '-' } else { $x.latest }), $x.baseline) -ForegroundColor $c
}
Write-Host ''
$mark = @{ done = '[x]'; in_progress = '[~]'; blocked = '[!]'; pending = '[ ]' }
$col = @{ done = 'Green'; in_progress = 'Yellow'; blocked = 'Red'; pending = 'Gray' }
foreach ($i in $m) { Write-Host ("  {0} {1}" -f $mark[$i.status], $i.title) -ForegroundColor $col[$i.status] }
if (@($p.defects).Count) { Write-Host ''; Write-Host '  Open defects:' -ForegroundColor Red; foreach ($d in $p.defects) { Write-Host "   - $d" -ForegroundColor Red } }
Write-Host ''
