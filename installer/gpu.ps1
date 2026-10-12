# Mémoire de la plus grosse carte graphique, en Go (0 si inconnue)
$max = 0
Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}\0*" -ErrorAction SilentlyContinue |
  ForEach-Object { $v = $_."HardwareInformation.qwMemorySize"; if ($v -and [int64]$v -gt $max) { $max = [int64]$v } }
[Console]::Write([math]::Round($max / 1GB))
