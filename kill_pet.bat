@echo off
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*claude-pet*pet.pyw*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Pet tue (si present).
pause
