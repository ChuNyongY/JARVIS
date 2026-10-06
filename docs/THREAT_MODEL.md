# 위협 모델

## 보호 대상과 공격자

보호 대상은 사용자 파일, 계정, 메시지, 시스템 설정, 자격 증명, 승인 의사와 감사 로그다. Planner 입력에는 사용자 발화뿐 아니라 웹페이지·PDF·이메일 같은 공격자 통제 데이터가 포함될 수 있다. LLM 출력, 화면 텍스트, plugin 응답은 모두 비신뢰 데이터로 취급한다.

## `process.launch` 경계

앱 실행은 `app_id`만 입력받는다. `app_id`는 사용자가 사전에 설정한 절대 실행 파일 경로와 해시에 매핑된다. Planner가 실행 파일 경로, 명령행 인자, 작업 디렉터리, 환경변수, 셸 명령, URI 또는 권한 상승 옵션을 제공할 수 없다.

실행 직전 다음 조건을 모두 검사한다.

- capability가 `process.launch`인가
- 인자가 정확히 `app_id` 하나뿐인가
- `app_id`가 registry에 존재하는가
- `target_identity`가 `app_id`와 일치하는가
- 현재 canonical path와 SHA-256이 등록 시점과 같은가
- 로컬 STOP이 설정되지 않았는가

registry 로드 단계에서는 JSON 크기와 앱 수를 제한하고, 중복 ID·상대 경로·symlink·해시
불일치를 거부한다. publisher가 설정되면 고정된 PowerShell Authenticode 검사에서 서명 상태가
`Valid`이고 signer certificate subject가 설정값과 정확히 일치해야 한다. 검사 입력은 script에
보간하지 않고 환경변수로 전달한다.

실행은 인자 배열과 `shell=False`를 사용하며 제한된 환경변수만 상속한다. 감사 로그에는 `app_id`, 정책 결과와 성공 여부만 기록하고 전체 명령행이나 환경변수는 기록하지 않는다.

## 주요 위협과 대응

| 위협 | 대응 | 잔여 위험 |
|---|---|---|
| 명령 주입 | 경로·인자·셸 입력 자체를 계약에서 제거 | 등록된 실행 파일 자체의 취약점 |
| 경로 치환 | deny-write/delete handle의 final path와 SHA-256을 검사하고 launch까지 유지 | 관리자·kernel 공격자 또는 비표준 filesystem semantics |
| reparse/symlink 우회 | 등록 시 symlink 거부, 실행 시 handle final canonical path exact match | parent reparse point를 privileged attacker가 사전에 통제한 경우 |
| 환경변수 비밀 유출 | 시스템 실행에 필요한 제한 목록만 전달 | 허용 환경변수 안에 포함된 애플리케이션 정보 |
| UAC·권한 상승 | elevation option 미지원 | 실행 파일 자체 manifest가 UAC를 요청할 수 있음 |
| 애매한 실패 뒤 중복 실행 | side effect 가능 오류에는 다른 adapter fallback 금지 | 앱이 시작됐지만 PID 반환 전에 오류가 난 경우 |
| fault 시험이 안전성을 과대평가 | 호출 후 주입 오류는 항상 side-effect possible, retry 불가 | VM 밖 실제 OS 고장은 별도 검증 필요 |
| 승인 대상 변경 | target과 arguments hash에 묶인 짧은 수명 토큰 | 승인 UI 자체가 손상된 경우 |
| launcher→runtime handoff 혼동 | launch 전 PID snapshot, exact runtime path/hash, 전 구간 polling과 안정된 유일 후보 | signed app 내부의 복잡한 multi-process topology |
| single-instance 앱이 기존 PID로 인계 | launch 전 exact top-level window snapshot, 새 PID/HWND·class·image hash의 전 구간 안정성 검증 | 앱이 새 창을 만들지 않고 기존 창만 활성화하면 의도적으로 실패 |
| UIA provider hang | 고정 child worker, strict JSON IPC, 5초 parent timeout | mutation timeout 뒤 실제 상태는 모호하므로 자동 retry 불가 |

현재 launcher는 Windows file handle을 검사부터 process creation 반환까지 유지하고 app registry의
publisher allowlist를 함께 사용한다. 그러나 관리자·kernel 공격자, filesystem filter driver,
실행 파일 자체 취약점으로부터 완전한 보호를 주장하지 않는다.

## 필수 시험

- 미등록 `app_id`, 추가 인자, target 불일치가 프로세스 생성 전에 거부되는가
- 등록 후 파일 내용 또는 경로가 바뀌면 실행이 거부되는가
- API 키 등 비허용 환경변수가 하위 프로세스로 전달되지 않는가
- 취소 신호가 launch 전에 도착하면 실행되지 않는가
- 실행 결과가 불명확한 오류에서 다른 adapter로 재실행하지 않는가
- 앱 실행 전 action별 정책이 다시 평가되는가
