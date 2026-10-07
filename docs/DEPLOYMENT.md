# 로컬 배포 및 사전점검

JAVIS는 현재 이 Windows PC에 고정된 연구 prototype이다. Windows Update나 앱 업데이트 뒤에는
실행 파일 hash와 package 경로가 바뀔 수 있으므로 manifest 검증을 우회하지 않는다.

## 설치

Python 3.12 가상환경에서 전체 로컬 기능을 설치한다.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev,voice,uia,trash,audio-control,packaging]"
```

이 PC의 Windows application-control 정책에서는 최신 일부 unsigned native wheel이 차단됐다.
따라서 voice extra는 `openwakeword>=0.5.1,<0.6`, `scikit-learn>=1.7,<1.8`,
`scipy>=1.16,<1.17` 범위를 사용한다. 제한을 임의로 풀지 말고 버전 변경 시 import와 local ONNX
무음 inference를 다시 검증한다.

Windows Hello helper를 다시 빌드해야 할 때만 Microsoft 공식 `dotnet-install.ps1`로 SDK
9.0.306을 프로젝트 `.dotnet`에 설치한 뒤 다음 스크립트를 실행한다.

```powershell
& .\native\WindowsHelloHelper\build.ps1
```

스크립트는 system `dotnet.exe`의 유효한 Microsoft Authenticode를 확인하고 host와 관리 파일
다섯 개의 SHA-256 manifest를 원자적으로 갱신한다. Windows application-control 정책을 끄거나
unsigned apphost를 허용하지 않는다.

## 무부작용 사전점검

```powershell
python -m javis_core.cli doctor (Get-Location).Path
```

doctor는 Windows/Python, SQLite FTS5, 앱 registry publisher, Hello manifest, wake/STT bundle,
무음·무파일 SAPI COM probe, 선택 dependency와 local SDK 존재를 확인한다. 앱·모델·마이크·Hello
UI나 실제 음성 출력을 실행하지 않는다.
출력은 component, PASS/WARN/FAIL, stable code뿐이며 경로·예외 원문·환경값·장치명·secret을
포함하지 않는다. required FAIL이 하나라도 있으면 exit 1, optional dependency나 build SDK
부재는 WARN과 exit 0이다.

실제 product host를 조립하거나 capture를 시작하기 전에는 secret 보호까지 포함한 preflight를
추가로 통과해야 한다.

```powershell
$root = (Get-Location).Path
python -m javis_core.cli provision-nim-credential
python -m javis_core.cli preflight-product-host-credential `
  $root `
  (Resolve-Path .\config\product_host.json).Path
```

운영 기본값은 현재 사용자 Windows Credential Manager의 고정 target `JAVIS-PC/NVIDIA-NIM`이다.
`nim-credential-status`는 `PRESENT`/`ABSENT`만 출력하고, `remove-nim-credential`은 이 고정 target만
삭제한다. 기존 `NIM.txt` 경로는 마이그레이션·복구 호환용이며 저장소나 wheel에는 포함하지 않는다.
두 preflight 모두 bounded key 형식, strict product config와 전체 doctor를 검사하며 마이크를 열지 않는다.

사용자 화면을 수집하지 않는 Vision backend 검증은 JAVIS가 만든 일회성 합성 창으로만 수행한다.
합성 창을 잠깐 foreground로 만들고 원래 cursor 위치를 복원하며 image/template는 임시 디렉터리와
함께 폐기한다.

```powershell
python tools\vision_smoke.py
```

## 패키지 감사

```powershell
python -m pip wheel . --no-deps --wheel-dir dist
```

wheel은 Python core와 `javis` entry point를 배포한다. PC별 `config`, 모델 bundle, native helper
managed 파일은 wheel에 내장하지 않고 명시적 project root 아래에서 별도 hash 검증한다. 따라서
wheel만 복사하면 완전한 실행 환경이 되지 않으며, doctor가 모두 PASS한 원본 배포 디렉터리가
필요하다.

Windows voice extra는 현재 application-control 검증을 통과한 PyAV 18.1 계열로 제한한다. PyAV
19.0.0 wheel의 native `_core`는 이 PC에서 application-control 정책에 차단돼 격리 설치가 실패했으며,
정책 우회 대신 `av>=18.1,<19` 의존성으로 재현 가능한 설치 범위를 고정했다.
최종 wheel을 system site package 없는 새 venv에 전체 제품 extra와 함께 설치했을 때 PyAV 18.1.0이
선택됐고 13-capability service 조립과 설치 wheel의 Explorer exact-folder actual smoke 3/3이 통과했다.

## Windows 제품 패키지와 Setup

다음 명령은 console 관리용 `JAVIS.exe`, 창 없는 `JAVIS-Tray.exe`, 검증된 `config`, 로컬 모델,
framework-dependent Windows Hello helper를 one-folder로 조립한다. `NIM.txt`, `state`, 테스트와
빌드 cache는 포함하지 않는다. source asset에 symlink 또는 Windows reparse point가 하나라도 있으면
실패하고, 산출물의 모든 파일은 정렬된 `package-manifest.json`의 size·SHA-256에 결합된다.

```powershell
python tools\build_windows_package.py
.\dist\JAVIS-PC-0.1.0\JAVIS.exe verify-package `
  (Resolve-Path .\dist\JAVIS-PC-0.1.0).Path
python tools\build_windows_installer.py
Get-FileHash .\dist\JAVIS-PC-0.1.0-Setup.exe -Algorithm SHA256
# 서명 인증서의 exact Subject를 사용한다. 현재 unsigned build는 exit 1이 정상이다.
python -m javis_core.cli audit-windows-release `
  (Resolve-Path .\dist\JAVIS-PC-0.1.0).Path `
  (Resolve-Path .\dist\JAVIS-PC-0.1.0-Setup.exe).Path `
  'CN=배포 인증서의 정확한 Subject'
```

기존 산출물을 덮어쓰지 않고 별도 후보를 만들 때는 새 package와 Setup 경로를 명시한다. 두 경로는
검증된 기존 파일을 덮어쓰지 않으며 Setup의 부모 디렉터리는 미리 존재해야 한다.

```powershell
python tools\build_windows_package.py --destination C:\absolute\build\JAVIS-candidate
python tools\build_windows_installer.py `
  --package C:\absolute\build\JAVIS-candidate `
  --destination C:\absolute\build\JAVIS-candidate-Setup.exe
```

현재 후보 one-folder는 1,826 manifest entry이며 source verifier로 exact manifest를 통과했다.
package manifest SHA-256은 `76c35518ec503c196d7a70de9a9a00538438ff523db8dec9a6a6e33bd843f018`다.
Setup은 Microsoft 서명된 시스템 IExpress builder를 사용해 생성한 260,366,336바이트 per-user
extractor이며 SHA-256은 `18630f573286f9879f1ae15ff5458df0a1e8c96f0ce581f484016322ccfcfdaf`이다.
내부 설치 진입부는
PowerShell 실행정책을 우회하지 않고 Windows `cmd`, `certutil`, `tar`와 staged `JAVIS.exe
verify-package`만 사용한다. 실제 최초 설치, 설치본 manifest 일치, Credential preflight와 서비스 조립을
통과했다.

Setup은 `%LOCALAPPDATA%\JAVIS-PC\0.1.0`이 비어 있을 때만 새로 설치하고, payload SHA-256과 내부
package manifest를 모두 확인한다. 같은 경로가 이미 있으면 verified 동일 설치만 성공으로 보고,
손상되거나 다른 파일은 덮어쓰지 않는다. 자동 시작, 마이크, tray 또는 앱을 자동 실행하지 않는다.
키는 설치 후 `JAVIS.exe provision-nim-credential`의 echo 없는 prompt로 현재 사용자 Credential
Manager에 별도 저장한다. 자동 시작은 `install-autostart-credential <root> <config>`를 명시적으로
실행해야 하며 shortcut 인자에 키나 secret 파일 경로가 없다. 삭제 전
`remove-autostart-credential`과 `remove-nim-credential`을 각각 명시적으로 실행하고 사용자 `state`
보존 여부를 결정해야 하므로 자동 silent uninstall은 제공하지 않는다.

시스템 IExpress 원본은 Microsoft Authenticode `Valid`였지만 생성된 Setup은 현재 `NotSigned`다.
따라서 이 파일은 manifest 감사용 산출물이며, 외부 배포 및 현재 정책 환경에서 실행하기 전에 조직의
신뢰된 코드 서명 인증서로 두 실행 파일과 Setup을 서명하고 서명 후 manifest/SHA-256, frozen doctor,
preflight와 설치·제거 smoke를 다시 생성·기록해야 한다. Smart App Control을 끄는 방식은 검증 절차가
아니다.

배포 인증서를 준비한 뒤에는 세 signing 인자를 두 builder에 모두 전달한다. thumbprint는 현재 사용자
`My` store의 code-signing EKU와 private key를 가진 인증서여야 하며 timestamp는 HTTPS만 허용한다.
builder는 console/tray를 서명·exact publisher 검증한 뒤 manifest를 만들고, Setup을 서명한 뒤
sidecar SHA-256을 기록한다. 일부 인자만 주면 빌드는 거부된다.

```powershell
$signing = @(
  '--certificate-thumbprint', '<40 또는 64자리 hex>',
  '--expected-publisher', 'CN=인증서의 정확한 Subject',
  '--timestamp-url', 'https://신뢰된-timestamp-endpoint'
)
python tools\build_windows_package.py @signing
python tools\build_windows_installer.py @signing
```
