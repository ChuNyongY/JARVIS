# Windows Hello native helper

이 helper는 Python 프로세스가 PIN·비밀번호·생체 정보를 다루지 않고 Windows가 소유한 인증 UI의
결과만 받도록 하는 작은 C# .NET 9 WinForms 경계다. Windows 11 build 22000 이상을 대상으로 하며
현재 target framework는 `net9.0-windows10.0.22621.0`이다.

## 프로토콜

stdin에서 최대 8 KiB의 JSON 한 줄만 받는다. `schema_version`은 문자열 `"1.0"`, `challenge`는
비어 있지 않은 canonical UUID, `message`는 제어문자 없는 1~1,024자 문자열이어야 한다. 추가
field, 중복으로 인한 field 수 증가, trailing comma, comment, 여러 줄 입력은 거부한다.

```json
{"schema_version":"1.0","challenge":"11111111-1111-1111-1111-111111111111","message":"확인 메시지"}
```

가용성을 `UserConsentVerifier.CheckAvailabilityAsync`로 먼저 확인한다. 사용 가능할 때만 표시된
helper 창의 HWND를 `UserConsentVerifierInterop.RequestVerificationForWindowAsync`에 전달한다.
응답은 같은 challenge를 포함한 JSON 하나이며 result는 `VERIFIED`, `CANCELED`, `UNAVAILABLE`,
`FAILED` 중 하나다. malformed input은 stdout/stderr 없이 nonzero로 종료한다.

## 빌드와 신뢰

공식 `dotnet-install.ps1`로 설치한 project-local SDK 9.0.306이 `.dotnet`에 있어야 한다.

```powershell
& .\native\WindowsHelloHelper\build.ps1
```

이 PC에서는 unsigned single-file apphost가 Windows application-control 정책에 차단되므로 정책을
우회하지 않는다. 스크립트는 framework-dependent managed 배포본을 `publish-fdd`에 만들고,
Authenticode가 유효한 Microsoft system `dotnet.exe`를 host로 선택한다. manifest에는 host와
helper DLL·deps·runtimeconfig·Windows SDK projection dependency의 SHA-256이 모두 기록된다.

Python loader는 managed 파일이 trusted root 안에 있는지 확인하고 symlink·경로 탈출·hash
mismatch를 거부한다. 다섯 파일이 같은 디렉터리에 정확히 존재해야 하며 추가 파일도 거부한다.
실행 때마다 host와 모든 파일의 hash를 다시 계산하고 shell 없이 최소 환경, timeout, 4 KiB
stdout 상한, challenge 일치 검사를 적용한다.

현재 managed 산출물 자체는 Authenticode 서명된 독립 배포 binary가 아니지만 서명된 Microsoft
host 아래에서 모든 로컬 파일을 hash-pinning한다. 실제 대화형 smoke는 권한을 부여하지 않고
`FAILED`로 닫혔으므로 `VERIFIED` 성공 경로는 사용자가 화면 앞에 있을 때 별도로 확인해야 한다.
