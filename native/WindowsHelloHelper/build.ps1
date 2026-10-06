$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$dotnet = Join-Path $projectRoot ".dotnet\dotnet.exe"
$project = Join-Path $PSScriptRoot "WindowsHelloHelper.csproj"
$publish = Join-Path $PSScriptRoot "publish-fdd"
$entrypoint = Join-Path $publish "Javis.WindowsHelloHelper.dll"
$manifest = Join-Path $projectRoot "config\windows_hello_helper.json"
$hostExecutable = (Get-Command dotnet.exe -ErrorAction Stop).Source

if (-not (Test-Path -LiteralPath $dotnet -PathType Leaf)) {
    throw "Project-local .NET SDK is missing. Install SDK 9.0.306 under .dotnet first."
}

$env:DOTNET_CLI_TELEMETRY_OPTOUT = "1"
$env:DOTNET_NOLOGO = "1"
& $dotnet publish $project -c Release -o $publish --self-contained false -p:PublishSingleFile=false -p:UseAppHost=false
if ($LASTEXITCODE -ne 0) {
    throw "Windows Hello helper publish failed."
}

$signature = Get-AuthenticodeSignature -LiteralPath $hostExecutable
if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "O=Microsoft Corporation") {
    throw "The selected dotnet host is not validly signed by Microsoft."
}

$hostDigest = (Get-FileHash -LiteralPath $hostExecutable -Algorithm SHA256).Hash.ToLowerInvariant()
$files = @(
    Get-ChildItem -LiteralPath $publish -File |
        Sort-Object -Property Name |
        ForEach-Object {
            [ordered]@{
                path = "native/WindowsHelloHelper/publish-fdd/$($_.Name)"
                sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
            }
        }
)
if (-not (Test-Path -LiteralPath $entrypoint -PathType Leaf) -or $files.Count -ne 5) {
    throw "Unexpected Windows Hello helper publish layout."
}

$payload = [ordered]@{
    schema_version = "1.1"
    host_executable = $hostExecutable
    host_sha256 = $hostDigest
    entrypoint = "native/WindowsHelloHelper/publish-fdd/Javis.WindowsHelloHelper.dll"
    files = $files
} | ConvertTo-Json -Depth 4
$temporary = "$manifest.tmp"
[System.IO.File]::WriteAllText($temporary, $payload + [Environment]::NewLine, [System.Text.UTF8Encoding]::new($false))
Move-Item -LiteralPath $temporary -Destination $manifest -Force
Write-Output "Published hash-pinned Windows Hello helper through signed host: $hostDigest"
