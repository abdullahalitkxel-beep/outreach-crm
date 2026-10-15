# Builds the Microsoft Store package and installs a test-signed copy so the app can be tested
# exactly as Store users will run it.
#   packaging\msix.ps1 -Mode pack       -> dist\OutreachCRM-Store.msix (unsigned; the Store signs it)
#   packaging\msix.ps1 -Mode installtest -> signs a copy with a throwaway test certificate and installs it
#   packaging\msix.ps1 -Mode uninstall
param([ValidateSet("pack", "installtest", "uninstall")][string]$Mode = "pack")
$ErrorActionPreference = "Stop"

$kits = Join-Path ${env:ProgramFiles(x86)} "Windows Kits\10\bin"
$sdk = Get-ChildItem $kits -Directory | Where-Object { $_.Name -match '^10\.' -and (Test-Path (Join-Path $_.FullName "x64\makeappx.exe")) } |
       Sort-Object Name -Descending | Select-Object -First 1
if (-not $sdk) { throw "Windows SDK (makeappx.exe) not found" }
$makeappx = Join-Path $sdk.FullName "x64\makeappx.exe"
$signtool = Join-Path $sdk.FullName "x64\signtool.exe"

if ($Mode -eq "pack") {
    $layout = "build\msix-layout"
    Remove-Item $layout -Recurse -Force -ErrorAction SilentlyContinue
    Copy-Item "dist\OutreachCRM" $layout -Recurse
    New-Item "$layout\Assets" -ItemType Directory -Force | Out-Null
    Copy-Item "packaging\msix-assets\*" "$layout\Assets\"
    Copy-Item "build\AppxManifest.xml" "$layout\AppxManifest.xml"
    & $makeappx pack /d $layout /p "dist\OutreachCRM-Store.msix" /o
    if ($LASTEXITCODE) { throw "makeappx failed ($LASTEXITCODE)" }
    Write-Host "Built dist\OutreachCRM-Store.msix"
}

if ($Mode -eq "installtest") {
    [xml]$m = Get-Content "build\AppxManifest.xml"
    $publisher = $m.Package.Identity.Publisher
    $cert = New-SelfSignedCertificate -Type Custom -Subject $publisher -KeyUsage DigitalSignature `
        -FriendlyName "Outreach CRM CI test only" -CertStoreLocation "Cert:\CurrentUser\My" `
        -TextExtension @("2.5.29.37={text}1.3.6.1.5.5.7.3.3", "2.5.29.19={text}")
    $pw = ConvertTo-SecureString -String "ci-test-only" -Force -AsPlainText
    Export-PfxCertificate -Cert $cert -FilePath "build\ci-test.pfx" -Password $pw | Out-Null
    Export-Certificate -Cert $cert -FilePath "build\ci-test.cer" | Out-Null
    Import-Certificate -FilePath "build\ci-test.cer" -CertStoreLocation "Cert:\LocalMachine\TrustedPeople" | Out-Null
    Copy-Item "dist\OutreachCRM-Store.msix" "build\OutreachCRM-citest.msix" -Force
    & $signtool sign /fd SHA256 /f "build\ci-test.pfx" /p "ci-test-only" "build\OutreachCRM-citest.msix"
    if ($LASTEXITCODE) { throw "signtool failed ($LASTEXITCODE)" }
    Add-AppxPackage "build\OutreachCRM-citest.msix"
    $pkg = Get-AppxPackage | Where-Object { $_.Name -eq $m.Package.Identity.Name }
    if (-not $pkg) { throw "package did not install" }
    Write-Host "Installed $($pkg.PackageFullName) (publisher display: $($m.Package.Properties.PublisherDisplayName))"
    Write-Host "::notice title=Store package installed::$($pkg.PackageFullName) — publisher shown as $($m.Package.Properties.PublisherDisplayName)"
}

if ($Mode -eq "uninstall") {
    [xml]$m = Get-Content "build\AppxManifest.xml"
    Get-AppxPackage | Where-Object { $_.Name -eq $m.Package.Identity.Name } | Remove-AppxPackage
}
