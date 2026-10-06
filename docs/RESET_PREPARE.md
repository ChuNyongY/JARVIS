# Windows reset 준비 경계

`system.reset.prepare`는 R4 snapshot-VM 대상 reset 요청을 준비할 뿐 Windows reset API를 호출하지
않는다. 대상은 생성 시 고정한 `snapshot-vm:<vm_id>`만 허용하며, `keep_files` 선택과 action ID에
결합된 5분 수명의 무작위 영수증을 메모리에 저장한다.

결과는 `automatic_commit=false`, `os_mutation_count=0`을 강제한다. verifier는 영수증의 수명과
정확한 action arguments 결합을 다시 확인한다. 영수증은 프로세스 종료 시 사라지며 실제 reset
commit 권한이 아니다. `system.reset.commit`은 별도 강한 인증과 수동 최종 경계, snapshot VM 복원
증거가 없으면 실행할 수 없어야 한다.
