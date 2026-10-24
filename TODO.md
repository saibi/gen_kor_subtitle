# TODO

## Finder 우클릭 메뉴 최상위에 "한국어 자막 만들기" 표시
현재는 빠른 동작(`install_quick_action.py`)이라 우클릭 → 빠른 동작/서비스 하위메뉴를 거쳐야 함.
최상위 메뉴에 직접 넣으려면 **Finder Sync Extension**(정식 맥 앱) 필요.

- [ ] Xcode로 Swift 호스트 앱 + Finder Sync Extension 생성
- [ ] `FIFinderSync.menu(for: .contextualMenuForItems)`로 메뉴 항목 추가, `directoryURLs`는 `/` 등 감시 범위 지정
- [ ] 코드 서명 (개인용이면 무료 Apple ID로 가능)
- [ ] 확장은 샌드박스라 `make_subtitle.py` 직접 실행 불가 → 호스트 앱(비샌드박스) 경유로 실행하도록 설계
- [ ] 시스템 설정 → 확장 프로그램에서 활성화 방법 README에 안내

임시 대안: 시스템 설정 → 키보드 → 키보드 단축키 → 서비스에서 단축키 지정.
