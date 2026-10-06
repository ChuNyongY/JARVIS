# 설치 패키지 identity 검증

`package.verify`는 설치 파일을 실행하지 않고 identity만 확인하는 R1 Native capability다. 허용된
fixture root 아래의 `.exe`, `.msi`, `.msix`, `.appx` 파일만 대상으로 하며 절대 경로, SHA-256,
정확한 Authenticode certificate subject를 action과 `package-sha256:<digest>` target에 결합한다.

Windows에서는 deny-write/delete lease를 유지한 채 hash와 publisher를 검사한다. 결과에는 패키지
경로나 publisher 원문 대신 각 SHA-256만 기록한다. verifier는 같은 검사를 다시 수행하며 불일치나
외부 verifier 오류를 fail-closed 처리한다. `package.install`은 R3 snapshot VM capability로
카탈로그와 plan contract에만 등록되어 있으며 기본 executor가 없다. target은 snapshot VM으로
결합되고 `snapshot.restore.verified` postcondition을 요구한다. 서명된 VM 실행·복원 증거가 없는
현재 host에서는 따라서 실행할 수 없다.
