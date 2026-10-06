# 이벤트 기반 전경 컨텍스트

`WindowsContextEventSource`는 Windows의 `EVENT_SYSTEM_FOREGROUND`와
`EVENT_OBJECT_FOCUS`만 구독한다. 각 이벤트는 단조 증가 sequence, monotonic 시각, root
`HWND`, `PID`만 담는다. 창 제목, UI 텍스트, 화면 이미지, 명령줄과 실행 파일 경로는 이벤트에
포함하지 않는다.

`EventDrivenContextManager`는 이벤트가 도착했을 때만 기존 전경 컨텍스트 collector를 호출한다.
수집 결과의 PID와 HWND가 이벤트 snapshot과 정확히 같아야 갱신을 수락한다. 짧은 시간 안에 들어온
동일 PID/HWND 중복 이벤트는 collector를 다시 호출하지 않고 억제한다. sequence 또는 monotonic
시각의 역행, hook/thread 오류, collector 오류, PID/HWND 불일치는 모두 실패로 닫힌다.

WinEvent callback은 bounded queue에만 기록한다. queue가 넘치면 일부 이벤트를 버리고 계속하지
않으며 source를 재시작할 때까지 영구 overflow 오류로 처리한다. 이는 누락된 포커스 전환 뒤의
오래된 컨텍스트가 정상 상태처럼 사용되는 것을 막는다. 종료는 hook thread에 메시지를 보내고
제한 시간 안에 join하며, 종료 실패도 안정적인 오류로 보고한다.

Planner에는 등록 신원이 검증된 경우에만 `foreground_app_id` 한 필드를 제공한다. 로컬에 남는
HWND, PID, sequence와 시각은 실행 권한이 아니다. 이 컨텍스트는 capability policy, exact-action
approval token, process session 검증 또는 실행 직전 대상 재검사를 대체하지 않는다.

현재 증거는 계약 테스트 10개와 JAVIS가 만든 일회성 합성 Tk 창에서 실제 WinEvent 포커스 수신,
동일 PID/HWND Vision 실행 및 화면 변화 검증이 성공한 것이다. 실제 사용자 앱의 장시간 이벤트
운용과 포커스 폭주·세션 전환 환경은 아직 검증하지 않았다.
