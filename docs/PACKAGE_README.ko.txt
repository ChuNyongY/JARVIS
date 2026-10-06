JAVIS-PC 0.1.0 로컬 Windows 패키지

이 폴더는 현재 사용자 권한으로 실행하며 관리자 권한을 요구하지 않습니다.
NIM.txt는 패키지에 포함되어 있지 않으며 운영 기본값은 Windows Credential Manager입니다.

1. 콘솔에서 이 폴더로 이동합니다.
2. 키를 화면에 표시하지 않고 현재 사용자 Credential Manager에 저장합니다.
   .\JAVIS.exe provision-nim-credential
3. 무부작용 사전점검을 실행합니다.
   .\JAVIS.exe preflight-product-host-credential (Get-Location).Path (Resolve-Path .\config\product_host.json).Path
4. 마이크를 열지 않고 전체 서비스를 조립해 확인합니다.
   .\JAVIS.exe check-product-service-credential (Get-Location).Path (Resolve-Path .\config\product_host.json).Path
5. 사용자 동의 후 트레이 호스트를 시작합니다.
   .\JAVIS-Tray.exe run-product-tray-credential (Get-Location).Path (Resolve-Path .\config\product_host.json).Path

자동 시작은 기본 해제입니다. 명시적으로 원할 때만 JAVIS.exe install-autostart-credential을
실행하십시오. 삭제는 먼저 remove-autostart-credential과 remove-nim-credential을 실행하고 이
폴더를 제거합니다. 사용자 state는 자동으로 삭제하지 않습니다. 이 패키지는 아직 Authenticode
서명되지 않았습니다.
