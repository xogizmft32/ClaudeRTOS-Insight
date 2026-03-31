# Quick Troubleshooting — ClaudeRTOS-Insight V3.5

---

## 1. 링커 오류: `undefined reference to EventClassifier_ClassifyV3`

**원인:** 구 버전 `event_classifier.c` 사용.  
**해결:** V3.5의 `event_classifier.c`를 `claudertos/` 폴더에 복사하거나 `python3 install.py --project .` 재실행.

---

## 2. CPU% 항상 0%

**원인:** `configGENERATE_RUN_TIME_STATS` 비활성.  
**해결:** `FreeRTOSConfig.h`에 추가:
```c
#define configGENERATE_RUN_TIME_STATS    1
#define configUSE_TRACE_FACILITY         1
#define portCONFIGURE_TIMER_FOR_RUN_TIME_STATS()  DWT_Init(configCPU_CLOCK_HZ)
#define portGET_RUN_TIME_COUNTER_VALUE()           ((uint32_t)DWT_GetTimestamp_us())
```
`install.py`가 자동으로 추가해줍니다.

---

## 3. ITM/SWO 데이터 수신 없음

**확인 순서:**
```
1. JLinkSWOViewer → Device: STM32F446RE, Speed: 2250000
2. 보드 리셋 후 "ClaudeRTOS-Insight V3.5.0 Started [ITM]" 확인
3. main.c에서 Transport_Init(180000000U) 호출 확인
```
자세한 내용: `docs/ITM_TROUBLESHOOTING.md`

---

## 4. AI 분석이 시작되지 않음

**확인:**
```bash
echo $ANTHROPIC_API_KEY        # 키 설정 확인
python3 integrated_demo.py --validate  # [AI_READY] 태그 확인
```
**원인:** `postmortem` 모드는 동일 이슈가 **3회 연속** 감지 후 `[AI_READY]`. 1~2회는 로컬 탐지만.  
**빠른 피드백:** `--ai-mode realtime` 사용 (개발 환경 전용).

---

## 5. heap_total = 0 / heap% 계산 불가

**원인:** `OSMonitorV3_Init()`이 `vTaskStartScheduler()` 이후에 호출됨.  
**해결:** 스케줄러 시작 **전에** 호출:
```c
OSMonitorV3_Init();      // ← 여기서 heap_total 캐시
vTaskStartScheduler();   // ← 이후는 heap 할당 발생
```

---

## 6. trace_events 데이터가 타임라인에 없음

**확인:**
```c
// FreeRTOSConfig.h에 hook 추가 여부 확인
#define traceTASK_SWITCHED_IN()  TraceEvent_ContextSwitchIn()
#define traceTASK_SWITCHED_OUT() TraceEvent_ContextSwitchOut()
```
hook 없이도 OS 스냅샷(CPU%, 힙, 스택)은 정상 수집됩니다. trace는 선택 사항.

---

더 상세한 내용:
- `docs/ITM_TROUBLESHOOTING.md` — SWO 상세 설정
- `docs/AI_USAGE_GUIDE.md` — AI 모드 선택 가이드
- `docs/TESTING_CHECKLIST.md` — 단계별 검증 체크리스트
