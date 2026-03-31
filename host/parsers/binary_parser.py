#!/usr/bin/env python3
"""
Binary Parser V3.2

J: Dict 반환 → typed dataclass 도입
   - ParsedSnapshot, ParsedFault, ParsedTask: 오타·없는 키 접근 시
     AttributeError로 즉시 노출 (Dict의 조용한 None 반환 제거)
   - 하위 호환: .to_dict() 메서드 제공
   - AnalysisEngine, rtos_debugger 는 .to_dict() 경유 또는
     직접 필드 접근 모두 가능

FIX-08: signed delta 시퀀스 갭 감지 유지
FIX-09: _parser_stats 자동 포함 유지
"""

import struct
import zlib
import logging
import time
from typing import Optional, Dict, List, Tuple
from dataclasses import dataclass, field, asdict

logger = logging.getLogger(__name__)

# ── Protocol constants ──────────────────────────────────────────
MAGIC1 = 0xC1
MAGIC2 = 0xAD
PROTOCOL_VERSION    = 4   # 현재 버전
PROTOCOL_VERSION_MIN = 3   # 최소 지원 버전 (V3 하위 호환)

PTYPE_OS_SNAPSHOT = 0x01
PTYPE_TASK_EVENT  = 0x02
PTYPE_FAULT       = 0x10

HEADER_SIZE    = 16
TASK_ENTRY_SZ  = 28
OS_FIXED_OVH   = 44
FAULT_PKT_SIZE = 92

HEADER_FMT      = '<BBBBQHBB'
OS_PAYLOAD_FMT  = '<IIIIIIBBBB'   # 28 bytes
TASK_FMT        = '<BBBBHHl16s'   # 28 bytes
FAULT_PAYLOAD_FMT = '<IIIIIIIIIIIII I 16s I'


# ── J: Typed dataclasses ─────────────────────────────────────────
@dataclass
class ParsedTask:
    task_id:    int
    name:       str
    priority:   int
    state:      int
    state_name: str
    cpu_pct:    int
    stack_hwm:  int       # words remaining
    runtime_us: int

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class ParsedSnapshot:
    type:           str       # always 'os_snapshot'
    timestamp_us:   int
    sequence:       int
    snapshot_count: int
    uptime_ms:      int
    cpu_usage:      int
    heap_free:      int
    heap_min:       int
    heap_total:     int
    heap_used_pct:  int
    tasks:          List[ParsedTask] = field(default_factory=list)
    _parser_stats:  Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d['heap'] = {
            'free':     self.heap_free,
            'min':      self.heap_min,
            'total':    self.heap_total,
            'used_pct': self.heap_used_pct,
        }
        d['tasks'] = [t.to_dict() for t in self.tasks]
        return d


@dataclass
class ParsedFault:
    type:         str       # always 'fault'
    timestamp_us: int
    sequence:     int
    fault_type:   str
    active_task:  Dict      # {'id': int, 'name': str}
    registers:    Dict      # {'CFSR': '0x...', ...}
    cfsr_decoded: Dict      # {'MemManage': {...}, ...}
    _parser_stats: Dict = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


# ── FIX-08: Sequence tracker (signed delta) ──────────────────────
class SequenceTracker:
    def __init__(self, cpu_hz: int = 180_000_000):
        self._cpu_hz = cpu_hz
        self._last: Optional[int] = None
        self.gaps:  List[Tuple[int, int]] = []

    def update(self, seq: int) -> Optional[int]:
        if self._last is None:
            self._last = seq
            return None
        raw   = (seq - self._last) & 0xFFFF
        delta = raw if raw < 32768 else raw - 65536
        if delta == 1:
            self._last = seq
            return None
        if delta <= 0:
            logger.debug("Out-of-order/duplicate: last=%d got=%d", self._last, seq)
            return None
        lost = delta - 1
        self.gaps.append((self._last, seq))
        logger.warning("Seq gap: last=%d got=%d (%d lost)", self._last, seq, lost)
        self._last = seq
        return lost


# ── Parser ───────────────────────────────────────────────────────
class BinaryParserV3:

    def __init__(self):
        self.seq_tracker = SequenceTracker()
        self._stats = {
            'parsed_os':    0, 'parsed_fault': 0,
            'crc_errors':   0, 'format_errors': 0,
            'sequence_gaps':0, 'packets_lost':  0,
            'v3_packets':   0, 'v4_packets':    0,
        }
        self._detected_major = 3   # 수신된 패킷 major 버전

    @staticmethod
    def _verify_crc(data: bytes) -> bool:
        if len(data) < 4:
            return False
        return (zlib.crc32(data[:-4]) & 0xFFFFFFFF ==
                struct.unpack_from('<I', data, len(data)-4)[0])

    def _parse_header(self, data: bytes) -> Optional[Dict]:
        if len(data) < HEADER_SIZE:
            return None
        try:
            m1, m2, ver, port, ts, seq, ptype, flags = \
                struct.unpack_from(HEADER_FMT, data, 0)
        except struct.error:
            self._stats['format_errors'] += 1
            return None
        if m1 != MAGIC1 or m2 != MAGIC2 or not (PROTOCOL_VERSION_MIN <= ver <= PROTOCOL_VERSION):
            self._stats['format_errors'] += 1
            return None
        lost = self.seq_tracker.update(seq)
        if lost is not None:
            self._stats['sequence_gaps'] += 1
            self._stats['packets_lost']  += lost
        return {'port': port, 'timestamp_us': ts,
                'sequence': seq, 'packet_type': ptype, 'flags': flags}

    def parse_os_snapshot(self, data: bytes) -> Optional[ParsedSnapshot]:
        if not self._verify_crc(data):
            self._stats['crc_errors'] += 1
            return None
        hdr = self._parse_header(data)
        if not hdr or hdr['packet_type'] != PTYPE_OS_SNAPSHOT:
            return None
        if len(data) < OS_FIXED_OVH + 4:
            self._stats['format_errors'] += 1
            return None
        try:
            (tick, snapshot_count,
             heap_free, heap_min, heap_total, uptime_ms,
             cpu, num_tasks, _r2, _r3) = \
                struct.unpack_from(OS_PAYLOAD_FMT, data, HEADER_SIZE)
        except struct.error:
            self._stats['format_errors'] += 1
            return None

        tasks: List[ParsedTask] = []
        pos = OS_FIXED_OVH
        for _ in range(num_tasks):
            if pos + TASK_ENTRY_SZ > len(data) - 4:
                break
            try:
                tid, pri, state, cpu_pct, hwm, _res, rt_us, raw_name = \
                    struct.unpack_from(TASK_FMT, data, pos)
            except struct.error:
                break
            name = raw_name.split(b'\x00')[0].decode('ascii', errors='replace')
            tasks.append(ParsedTask(
                task_id=tid, name=name or f'Task{tid}',
                priority=pri, state=state, state_name=_state_name(state),
                cpu_pct=cpu_pct, stack_hwm=hwm, runtime_us=rt_us,
            ))
            pos += TASK_ENTRY_SZ

        used_pct = int((heap_total - heap_free) * 100 / heap_total) \
                   if heap_total > 0 else 0
        self._stats['parsed_os'] += 1

        return ParsedSnapshot(
            type='os_snapshot',
            timestamp_us=hdr['timestamp_us'],
            sequence=hdr['sequence'],
            snapshot_count=snapshot_count,
            uptime_ms=uptime_ms,
            cpu_usage=cpu,
            heap_free=heap_free, heap_min=heap_min,
            heap_total=heap_total, heap_used_pct=used_pct,
            tasks=tasks,
            _parser_stats=self.get_stats(),
        )

    def parse_fault_packet(self, data: bytes) -> Optional[ParsedFault]:
        if not self._verify_crc(data):
            self._stats['crc_errors'] += 1
            return None
        hdr = self._parse_header(data)
        if not hdr or hdr['packet_type'] != PTYPE_FAULT:
            return None
        if len(data) < FAULT_PKT_SIZE:
            self._stats['format_errors'] += 1
            return None
        try:
            (cfsr, hfsr, mmfar, bfar, pc, lr, sp, psr,
             r0, r1, r2, r3, r12, task_id, raw_name, _crc) = \
                struct.unpack_from(FAULT_PAYLOAD_FMT, data, HEADER_SIZE)
        except struct.error:
            self._stats['format_errors'] += 1
            return None

        task_name = raw_name.split(b'\x00')[0].decode('ascii', errors='replace')
        self._stats['parsed_fault'] += 1

        return ParsedFault(
            type='fault',
            timestamp_us=hdr['timestamp_us'],
            sequence=hdr['sequence'],
            fault_type=_decode_cfsr(cfsr),
            active_task={'id': task_id, 'name': task_name or f'Task{task_id}'},
            registers={
                'CFSR':  f'0x{cfsr:08X}', 'HFSR':  f'0x{hfsr:08X}',
                'MMFAR': f'0x{mmfar:08X}', 'BFAR': f'0x{bfar:08X}',
                'PC':    f'0x{pc:08X}',   'LR':   f'0x{lr:08X}',
                'SP':    f'0x{sp:08X}',   'PSR':  f'0x{psr:08X}',
                'R0':    f'0x{r0:08X}',   'R1':   f'0x{r1:08X}',
                'R2':    f'0x{r2:08X}',   'R3':   f'0x{r3:08X}',
                'R12':   f'0x{r12:08X}',
            },
            cfsr_decoded=_decode_cfsr_bits(cfsr),
            _parser_stats=self.get_stats(),
        )

    def parse_packet(self, data: bytes) -> Optional[object]:
        """Returns ParsedSnapshot | ParsedFault | None."""
        if len(data) < HEADER_SIZE:
            return None
        ptype = data[14]
        if ptype == PTYPE_OS_SNAPSHOT:
            return self.parse_os_snapshot(data)
        if ptype == PTYPE_FAULT:
            return self.parse_fault_packet(data)
        return None

    def get_stats(self) -> Dict:
        return dict(self._stats)


# ── Helpers ──────────────────────────────────────────────────────
def _state_name(s: int) -> str:
    return {0:'Running',1:'Ready',2:'Blocked',3:'Suspended',4:'Deleted'}.get(s,f'?({s})')

def _decode_cfsr(cfsr: int) -> str:
    table = [
        (0x0001,'IACCVIOL (Instruction access violation)'),
        (0x0002,'DACCVIOL (Data access violation)'),
        (0x0008,'MUNSTKERR (MemManage on exception return)'),
        (0x0010,'MSTKERR (MemManage on exception entry)'),
        (0x0100,'IBUSERR (Instruction bus error)'),
        (0x0200,'PRECISERR (Precise data bus error)'),
        (0x0400,'IMPRECISERR (Imprecise bus error)'),
        (0x0800,'UNSTKERR (BusFault on exception return)'),
        (0x1000,'STKERR (BusFault on exception entry)'),
        (0x10000,'UNDEFINSTR (Undefined instruction)'),
        (0x20000,'INVSTATE (Invalid EPSR state)'),
        (0x40000,'INVPC (Invalid PC load)'),
        (0x80000,'NOCP (No coprocessor)'),
        (0x1000000,'UNALIGNED (Unaligned memory access)'),
        (0x2000000,'DIVBYZERO (Divide by zero)'),
    ]
    for mask, name in table:
        if cfsr & mask:
            return name
    return f'UNKNOWN (0x{cfsr:08X})'

def _decode_cfsr_bits(cfsr: int) -> Dict:
    return {
        'MemManage': {
            'IACCVIOL': bool(cfsr&0x0001), 'DACCVIOL': bool(cfsr&0x0002),
            'MUNSTKERR':bool(cfsr&0x0008), 'MSTKERR': bool(cfsr&0x0010),
            'MMARVALID':bool(cfsr&0x0080),
        },
        'BusFault': {
            'IBUSERR':    bool(cfsr&0x0100), 'PRECISERR':  bool(cfsr&0x0200),
            'IMPRECISERR':bool(cfsr&0x0400), 'UNSTKERR':   bool(cfsr&0x0800),
            'STKERR':     bool(cfsr&0x1000), 'BFARVALID':  bool(cfsr&0x8000),
        },
        'UsageFault': {
            'UNDEFINSTR':bool(cfsr&0x10000), 'INVSTATE': bool(cfsr&0x20000),
            'INVPC':     bool(cfsr&0x40000), 'NOCP':     bool(cfsr&0x80000),
            'UNALIGNED': bool(cfsr&0x1000000),'DIVBYZERO':bool(cfsr&0x2000000),
        },
    }


# ── E: 스트리밍 파서 ─────────────────────────────────────────────
    def _cycles_to_us(self, cycles: int) -> int:
        """DWT CYCCNT cycles → µs 변환 (V4 패킷용).
        V3 패킷은 이미 µs 단위이므로 그대로 반환."""
        if self._detected_major >= 4 and self._cpu_hz > 0:
            return int(cycles * 1_000_000 // self._cpu_hz)
        return cycles  # V3: 이미 µs


class StreamingParser:
    """
    실제 하드웨어 SWO/UART 바이트 스트림용 파서.

    OS snapshot은 가변 길이(num_tasks 의존)이므로 2단계 수집:
      1단계: 헤더(16B) + OS 고정 페이로드(28B) = 44B 수집
             → num_tasks(offset 41) 읽어 가변 부분 크기 결정
      2단계: tasks(N*28B) + CRC(4B) 수집 → 완성

    Fault/기타는 헤더 뒤 고정 크기이므로 1단계만 필요.

    상태:
      HUNT      → 0xC1 탐색
      MAGIC2    → 0xAD 확인
      HEADER    → 나머지 14B 수집 (magic 2B 포함 총 16B)
      OS_FIXED  → OS snapshot 전용: 28B 고정 페이로드 수집
      PAYLOAD   → 나머지 가변/고정 페이로드 + CRC 수집
    """

    _HUNT    = 0
    _MAGIC2  = 1
    _HEADER  = 2
    _OS_FIX  = 3   # OS snapshot 전용 2단계
    _PAYLOAD = 4

    MAX_PKT = 512

    def __init__(self, packet_parser=None):
        self._parser = packet_parser or BinaryParserV3()
        self._buf    = bytearray()
        self._state  = self._HUNT
        self._need   = 0
        self._callbacks = []
        self.stats = {'bytes_in': 0, 'packets_assembled': 0,
                      'sync_losses': 0, 'oversized': 0}

    def on_packet(self, cb) -> None:
        self._callbacks.append(cb)

    def feed(self, data: bytes) -> list:
        results = []
        self.stats['bytes_in'] += len(data)
        for byte in data:
            r = self._feed_byte(byte)
            if r is not None:
                results.append(r)
                for cb in self._callbacks:
                    cb(r)
        return results

    def _reset(self) -> None:
        self._buf   = bytearray()
        self._state = self._HUNT
        self._need  = 0

    def _feed_byte(self, b: int):
        # ── HUNT ──────────────────────────────────────
        if self._state == self._HUNT:
            if b == MAGIC1:
                self._buf = bytearray([b])
                self._state = self._MAGIC2
            return None

        # ── MAGIC2 ────────────────────────────────────
        if self._state == self._MAGIC2:
            if b == MAGIC2:
                self._buf.append(b)
                self._need  = HEADER_SIZE - 2   # 나머지 14B
                self._state = self._HEADER
            else:
                self.stats['sync_losses'] += 1
                if b == MAGIC1:
                    self._buf = bytearray([b])
                    self._state = self._MAGIC2
                else:
                    self._reset()
            return None

        # ── HEADER ────────────────────────────────────
        if self._state == self._HEADER:
            self._buf.append(b)
            self._need -= 1
            if self._need == 0:
                ptype = self._buf[14]
                if ptype == PTYPE_OS_SNAPSHOT:
                    # OS snapshot: 고정 페이로드 28B 추가 수집
                    self._need  = OS_FIXED_OVH - HEADER_SIZE   # = 28
                    self._state = self._OS_FIX
                elif ptype == PTYPE_FAULT:
                    self._need  = FAULT_PKT_SIZE - HEADER_SIZE
                    self._state = self._PAYLOAD
                else:
                    # 미지의 타입: 포기
                    self.stats['sync_losses'] += 1
                    self._reset()
            return None

        # ── OS_FIX ────────────────────────────────────
        if self._state == self._OS_FIX:
            self._buf.append(b)
            self._need -= 1
            if self._need == 0:
                # num_tasks = buf[41], tasks+CRC 남은 바이트
                num_tasks = self._buf[41]
                remaining = num_tasks * TASK_ENTRY_SZ + 4   # tasks + CRC
                if HEADER_SIZE + OS_FIXED_OVH - HEADER_SIZE + remaining > self.MAX_PKT:
                    self.stats['oversized'] += 1
                    self._reset()
                else:
                    self._need  = remaining
                    self._state = self._PAYLOAD
            return None

        # ── PAYLOAD ───────────────────────────────────
        if self._state == self._PAYLOAD:
            self._buf.append(b)
            self._need -= 1
            if self._need == 0:
                pkt_bytes = bytes(self._buf)
                self._reset()
                self.stats['packets_assembled'] += 1
                return self._parser.parse_packet(pkt_bytes)

        return None

