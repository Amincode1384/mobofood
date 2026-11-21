# ارسال نوتیف دسکتاپ Windows
# استفاده: powershell -NoProfile -ExecutionPolicy Bypass -File .\notify.ps1 -Message "متن" -Title "عنوان"
param(
    [string]$Title = "Buffy",
    [string]$Message = "کار تمام شد"
)
Add-Type -AssemblyName System.Windows.Forms
$n = New-Object System.Windows.Forms.NotifyIcon
$n.Icon = [System.Drawing.SystemIcons]::Information
$n.BalloonTipTitle = $Title
$n.BalloonTipText = $Message
$n.Visible = $true
$n.ShowBalloonTip(5000)
Start-Sleep -Seconds 6
$n.Dispose()