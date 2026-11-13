Set-Location $PSScriptRoot
& .\.venv\Scripts\Activate.ps1
tripmoney serve @args
