# [LTR] 리뷰 반영

## 2026-11-21 review/LTR_review_codex-gpt-6.2-sol.md

| ID | 판단 | 처리 |
| :--- | :--- | :--- |
| LTR-R1 | 타당 (urlopen이 http_proxy를 따름) | ✅ `ProxyHandler({})` 전용 opener(`LOCAL_HTTP`)로 헬스체크·번역 요청. 프록시 환경 변수를 넣은 회귀 테스트 추가 |
| LTR-R2 | 타당 (`IncompleteRead`는 OSError가 아님) | ✅ `http.client.HTTPException`도 `RuntimeError`로 변환. 응답 도중 끊는 가짜 서버로 검증 |
| LTR-R3 | 일부 타당 (포트 경합은 드묾) | ✅ `/health` 본문 `status == "ok"`와 자식 생존을 함께 확인, 503→200 대기 테스트 추가. 포트 재시도는 생략(자식이 바인드 실패로 끝나면 오류 메시지로 드러남) |
| LTR-R4 | 일부 타당 | ⏸ 보류: Whisper 모델과 공유하는 기존 `ensure_file_downloaded()`의 문제(동시 첫 다운로드, 무결성). LTR 범위 밖으로 별도 개선 |
| LTR-R5 | 타당 | ✅ `LTR_MODEL`은 정규화 경로·크기·mtime을 캐시 키에 넣음. 같은 파일명 다른 모델 테스트 추가 |
| LTR-R6 | 타당 (흩어진 100줄 + 문맥 1,000줄은 8,192토큰 초과) | ✅ 결정 D1: 로컬 묶음 25줄(`LOCAL_BATCH`), `-c 16384` |
| LTR-R7 | 타당 (드묾) | ✅ `extract_json()`에서 짝 없는 서로게이트를 형식 오류로 처리해 재시도 |
| LTR-R8 | 타당 | ✅ Linux 설치 안내 추가, FKS 경고에 실제 실행 파일명(`llama-server`) 표시 |

### 결정
- ✅ **D1 (LTR-R6)** 문맥 한도 초과 대응 → 결정: 로컬만 묶음 25줄 + 컨텍스트 16,384토큰 (코드가 가장 작고, 실측에서 충분)

### 검증
- `python3 -m unittest test_local_translator test_mixed_subtitle test_make_subtitle_linux`(각각), `python3 test_make_foreign_subtitle.py` 모두 통과.
- 실제 모델: MXS 검증 영상 대사에서 영화 전체에 흩어진 100줄을 문맥과 함께 로컬 번역 → 25줄씩 4묶음, 80.4초, 100줄 모두 성공. 22줄 실제 번역(11.6초)은 리뷰 전 수정에서 확인.
