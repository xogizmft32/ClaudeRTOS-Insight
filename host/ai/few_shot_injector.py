#!/usr/bin/env python3
"""
few_shot_injector.py — 과거 사례 기반 Few-Shot 주입기

임베딩 벡터 없이 이슈 타입 + 시스템 상태 특성으로 유사도를 계산,
관련성 높은 과거 진단 사례를 컨텍스트에 주입한다.

유사도 계산 방식:
  1. 이슈 타입 집합 교집합 (Jaccard)
  2. CPU/Heap 범위 일치 (심각도 버킷)
  3. 태스크 수 근접도

사용:
    injector = FewShotInjector()
    # 새 사례 기록
    injector.record(snap, issues, diagnosis="heap 누수 확인됨", fix="pvPortMalloc 추적 추가")
    # 유사 사례 주입
    examples = injector.get_relevant(snap, issues, top_k=3)
    for ex in examples:
        print(ex.summary())
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import pickle
import time
from typing import Dict, List, Optional, Tuple


# ── 저장 사례 ─────────────────────────────────────────────────────

@dataclasses.dataclass
class DiagnosticExample:
    """진단 사례 하나."""
    issue_types:   List[str]     # 감지된 이슈 타입 목록
    cpu_bucket:    str           # 'low'(<50) / 'mid'(50~85) / 'high'(>85)
    heap_bucket:   str           # 'ok'(>30%) / 'warn'(10~30%) / 'crit'(<10%)
    task_count:    int
    diagnosis:     str           # AI 또는 엔지니어의 최종 진단
    root_cause:    str           # 근본 원인
    fix:           str           # 수정 방법
    confidence:    float
    timestamp:     float = dataclasses.field(default_factory=time.time)
    case_id:       str   = ""

    def __post_init__(self):
        if not self.case_id:
            h = hashlib.md5(
                (str(sorted(self.issue_types)) + self.cpu_bucket + self.heap_bucket).encode()
            ).hexdigest()[:8]
            self.case_id = f"case_{h}"

    def similarity(self, issue_types: List[str],
                   cpu_bucket: str, heap_bucket: str, task_count: int) -> float:
        """0.0~1.0 유사도 점수."""
        # Jaccard 이슈 타입
        a, b = set(self.issue_types), set(issue_types)
        jaccard = len(a & b) / max(len(a | b), 1)
        # 상태 버킷 일치
        cpu_match  = 1.0 if self.cpu_bucket == cpu_bucket else 0.0
        heap_match = 1.0 if self.heap_bucket == heap_bucket else 0.0
        # 태스크 수 유사도
        task_sim = 1.0 - min(abs(self.task_count - task_count) / max(task_count, 1), 1.0)
        return jaccard * 0.5 + cpu_match * 0.2 + heap_match * 0.2 + task_sim * 0.1

    def summary(self) -> str:
        """컨텍스트 주입용 요약 문자열."""
        return (
            f"[{self.case_id}] 이슈: {', '.join(self.issue_types)}\n"
            f"  근본원인: {self.root_cause}\n"
            f"  수정: {self.fix}\n"
            f"  신뢰도: {self.confidence:.0%}"
        )


# ── 주입기 ───────────────────────────────────────────────────────

class FewShotInjector:
    """
    과거 진단 사례 저장 및 유사 사례 검색.

    Parameters
    ----------
    db_path     : 사례 DB 파일 경로 (pickle)
    max_examples: 최대 저장 사례 수
    """

    def __init__(self,
                 db_path: str = "logs/few_shot_db.pkl",
                 max_examples: int = 500):
        self._db_path      = db_path
        self._max_examples = max_examples
        self._examples: List[DiagnosticExample] = []
        self._load()

        # 내장 시드 사례 (초기 학습 없이도 동작)
        if not self._examples:
            self._seed_examples()

    # ── 시드 사례 ────────────────────────────────────────────────

    def _seed_examples(self):
        """기본 내장 사례 — 임베딩 없이 즉시 사용 가능."""
        seeds = [
            DiagnosticExample(
                issue_types=['heap_exhaustion'],
                cpu_bucket='mid', heap_bucket='crit', task_count=4,
                diagnosis="동적 할당 후 해제 누락으로 힙 고갈",
                root_cause="pvPortMalloc() 후 vPortFree() 미호출",
                fix="할당-해제 쌍 확인; FreeRTOS heap_4.c 사용 권장; "
                    "configUSE_HEAP_PROTECT_BLOCK 활성화",
                confidence=0.85,
            ),
            DiagnosticExample(
                issue_types=['stack_overflow_imminent'],
                cpu_bucket='low', heap_bucket='ok', task_count=3,
                diagnosis="재귀 함수 또는 큰 로컬 배열로 스택 소모",
                root_cause="함수 호출 깊이 증가 또는 지역 배열 선언",
                fix="configMINIMAL_STACK_SIZE 증가; "
                    "큰 배열을 정적(static) 또는 힙으로 이동; "
                    "uxTaskGetStackHighWaterMark() 주기적 모니터링",
                confidence=0.90,
            ),
            DiagnosticExample(
                issue_types=['priority_inversion', 'task_starvation'],
                cpu_bucket='high', heap_bucket='ok', task_count=5,
                diagnosis="mutex 미사용으로 우선순위 역전 발생",
                root_cause="공유 자원에 mutex 없이 접근, 낮은 우선순위 태스크가 점유",
                fix="xSemaphoreCreateMutex() 사용; "
                    "configUSE_MUTEXES=1 활성화; "
                    "Priority Ceiling Protocol 적용 고려",
                confidence=0.88,
            ),
            DiagnosticExample(
                issue_types=['isr_invalid_exc_return'],
                cpu_bucket='high', heap_bucket='ok', task_count=3,
                diagnosis="ISR에서 비ISR API 직접 호출로 컨텍스트 오류",
                root_cause="xQueueSend() 등을 ISR 내에서 호출",
                fix="xQueueSendFromISR() / xSemaphoreGiveFromISR() 등 "
                    "FromISR 접미사 함수로 교체; portYIELD_FROM_ISR() 추가",
                confidence=0.92,
            ),
            DiagnosticExample(
                issue_types=['i2c_nack_storm', 'i2c_timeout_repeated'],
                cpu_bucket='mid', heap_bucket='ok', task_count=4,
                diagnosis="I2C 버스 전기적 문제 또는 슬레이브 무응답",
                root_cause="풀업 저항 불량, 버스 속도 불일치, 슬레이브 주소 오류",
                fix="I2C 풀업 4.7kΩ 확인; HAL_I2C_Init() clock speed 재확인; "
                    "로직 애널라이저로 SDA/SCL 파형 점검",
                confidence=0.80,
            ),
            DiagnosticExample(
                issue_types=['cpu_overload', 'task_starvation'],
                cpu_bucket='high', heap_bucket='ok', task_count=6,
                diagnosis="특정 태스크의 CPU 독점으로 낮은 우선순위 기아",
                root_cause="무한 루프 내 vTaskDelay() 없음 또는 과도한 폴링",
                fix="vTaskDelay(pdMS_TO_TICKS(1)) 삽입; "
                    "taskYIELD() 추가; 폴링을 인터럽트 기반으로 전환",
                confidence=0.87,
            ),
            DiagnosticExample(
                issue_types=['heap_leak_trend', 'heap_exhaustion'],
                cpu_bucket='low', heap_bucket='warn', task_count=4,
                diagnosis="점진적 메모리 누수 — 수 시간 후 힙 고갈 예상",
                root_cause="이벤트 핸들러마다 할당, 해제 경로 누락",
                fix="valgrind 대신 FreeRTOS heap trace 활성화; "
                    "pvPortMalloc/vPortFree 래퍼 추가 후 미해제 추적",
                confidence=0.82,
            ),
            DiagnosticExample(
                issue_types=['bus_fault_precise'],
                cpu_bucket='mid', heap_bucket='ok', task_count=3,
                diagnosis="잘못된 메모리 주소 접근 (NULL 포인터 또는 해제된 포인터)",
                root_cause="vPortFree() 후 포인터 미초기화 및 재접근",
                fix="해제 후 포인터 NULL 설정; "
                    "MPU 활성화로 비합법 접근 조기 감지; "
                    "BFAR 레지스터 값으로 오류 주소 특정",
                confidence=0.85,
            ),
        ]
        self._examples = seeds
        _log.info("[FewShot] 시드 사례 %d개 로드", len(seeds))

    # ── 공개 API ─────────────────────────────────────────────────

    def record(self, snap: Dict, issues: List[Dict],
               diagnosis: str, root_cause: str = "", fix: str = "",
               confidence: float = 0.75) -> DiagnosticExample:
        """새 진단 사례 기록."""
        ex = DiagnosticExample(
            issue_types  = [i.get('issue_type', i.get('type', '?')) for i in issues],
            cpu_bucket   = _cpu_bucket(snap.get('cpu_usage', 0)),
            heap_bucket  = _heap_bucket(snap.get('heap', {}).get('used_pct', 0)),
            task_count   = len(snap.get('tasks', [])),
            diagnosis    = diagnosis,
            root_cause   = root_cause,
            fix          = fix,
            confidence   = confidence,
        )
        # 중복 제거 (동일 case_id)
        self._examples = [e for e in self._examples if e.case_id != ex.case_id]
        self._examples.append(ex)
        # 용량 초과 시 오래된 것부터 제거
        if len(self._examples) > self._max_examples:
            self._examples = sorted(
                self._examples, key=lambda e: e.timestamp)[-self._max_examples:]
        self._save()
        return ex

    def get_relevant(self, snap: Dict, issues: List[Dict],
                     top_k: int = 3,
                     min_similarity: float = 0.2) -> List[DiagnosticExample]:
        """현재 상황과 유사한 사례 top_k개 반환."""
        issue_types = [i.get('issue_type', i.get('type', '?')) for i in issues]
        cpu_bkt     = _cpu_bucket(snap.get('cpu_usage', 0))
        heap_bkt    = _heap_bucket(snap.get('heap', {}).get('used_pct', 0))
        n_tasks     = len(snap.get('tasks', []))

        scored: List[Tuple[float, DiagnosticExample]] = []
        for ex in self._examples:
            sim = ex.similarity(issue_types, cpu_bkt, heap_bkt, n_tasks)
            if sim >= min_similarity:
                scored.append((sim, ex))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [ex for _, ex in scored[:top_k]]

    def inject_to_context(self, snap: Dict, issues: List[Dict],
                          top_k: int = 3) -> str:
        """컨텍스트에 삽입할 Few-Shot 문자열 생성."""
        examples = self.get_relevant(snap, issues, top_k=top_k)
        if not examples:
            return ""
        lines = ["## 유사 사례 (Few-Shot)"]
        for i, ex in enumerate(examples, 1):
            lines.append(f"\n### 사례 {i} (유사도 포함)")
            lines.append(ex.summary())
        return "\n".join(lines)

    def stats(self) -> Dict:
        """DB 통계."""
        return {
            'total':      len(self._examples),
            'by_type':    self._type_counts(),
            'db_path':    self._db_path,
        }

    # ── 내부 메서드 ──────────────────────────────────────────────

    def _type_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for ex in self._examples:
            for t in ex.issue_types:
                counts[t] = counts.get(t, 0) + 1
        return dict(sorted(counts.items(), key=lambda x: -x[1]))

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self._db_path) or '.', exist_ok=True)
            with open(self._db_path, 'wb') as f:
                pickle.dump(self._examples, f)
        except Exception as e:
            _log.debug("[FewShot] 저장 실패: %s", e)

    def _load(self):
        try:
            if os.path.exists(self._db_path):
                with open(self._db_path, 'rb') as f:
                    self._examples = pickle.load(f)
                _log.info("[FewShot] DB 로드: %d건", len(self._examples))
        except Exception as e:
            _log.debug("[FewShot] 로드 실패: %s", e)
            self._examples = []


# ── 유틸 ─────────────────────────────────────────────────────────

def _cpu_bucket(cpu_pct: int) -> str:
    if cpu_pct < 50:  return 'low'
    if cpu_pct < 85:  return 'mid'
    return 'high'

def _heap_bucket(used_pct: int) -> str:
    if used_pct < 70: return 'ok'
    if used_pct < 90: return 'warn'
    return 'crit'


import logging
_log = logging.getLogger(__name__)
