#!/usr/bin/env python3
"""
collector.py — 실제 하드웨어 데이터 수신기

J-Link ITM(SWO) 또는 UART로부터 Binary Protocol V4 패킷을 수신하여
StreamingParser에 공급한다.

지원 연결:
  --port jlink          J-Link ITM(SWO), pylink-square 필요
  --port uart:/dev/ttyUSB0   UART, pyserial 필요
  --port uart:COM3            UART (Windows)
  --port simulate       시뮬레이션 (테스트용)

사용:
    collector = Collector.from_port_str("jlink")
    collector.open()
    for packet in collector.stream():          # raw bytes 스트림
        parsed = streaming_parser.feed(packet)
        if parsed:
            analyze(parsed)
    collector.close()
"""

from __future__ import annotations
import logging

_log = logging.getLogger(__name__)

import os
import struct
import threading
import time
from abc import ABC, abstractmethod
from typing import Iterator, Optional


# ── 추상 기반 클래스 ─────────────────────────────────────────
class BaseCollector(ABC):
    """수신기 공통 인터페이스."""

    def __init__(self):
        self._running = False
        self._bytes_received = 0
        self._packets_received = 0

    @abstractmethod
    def open(self) -> None:
        """연결 열기."""

    @abstractmethod
    def close(self) -> None:
        """연결 닫기."""

    @abstractmethod
    def stream(self) -> Iterator[bytes]:
        """raw bytes 패킷 이터레이터."""

    @property
    def stats(self) -> dict:
        return {
            'bytes':   self._bytes_received,
            'packets': self._packets_received,
        }

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *_):
        self.close()


# ── J-Link ITM (SWO) 수신기 ──────────────────────────────────
class JLinkCollector(BaseCollector):
    """
    J-Link SWO(ITM) 수신기.

    pylink-square 라이브러리 사용:
      pip install pylink-square>=1.2.0

    SWO 속도: CPU 클럭 / 4 이상 권장
      STM32F446RE @ 180MHz → SWO 45MHz 이상

    ITM 채널:
      0번 채널 — ClaudeRTOS-Insight Binary Protocol V4

    사용:
      collector = JLinkCollector(cpu_hz=180_000_000, swo_hz=4_500_000)
      collector.open()
      for pkt in collector.stream():
          parser.feed(pkt)
    """

    # Binary Protocol V4 sync word
    SYNC_MAGIC = 0xDEAD

    def __init__(self,
                 cpu_hz:    int = 180_000_000,
                 swo_hz:    int = 4_500_000,
                 itm_ch:    int = 0,
                 device:    str = 'STM32F446RE',
                 interface: str = 'SWD'):
        super().__init__()
        self._cpu_hz    = cpu_hz
        self._swo_hz    = swo_hz
        self._itm_ch    = itm_ch
        self._device    = device
        self._interface = interface
        self._jlink     = None
        self._buf       = bytearray()

    def open(self) -> None:
        try:
            import pylink
        except ImportError:
            raise ImportError(
                "pylink-square 미설치.\n"
                "설치: pip install pylink-square>=1.2.0\n"
                "또는 UART 모드 사용: --port uart:/dev/ttyUSB0")

        self._jlink = pylink.JLink()
        self._jlink.open()

        # SWD 인터페이스 + 타겟 연결
        self._jlink.set_tif(pylink.enums.JLinkInterfaces.SWD)
        self._jlink.connect(self._device, self._cpu_hz)

        # SWO 활성화
        self._jlink.swd_speed(self._swo_hz)
        self._jlink.swo_start(self._swo_hz)
        self._jlink.swo_flush()

        # ITM 채널 0 활성화
        self._jlink.set_trace_source(pylink.enums.JLinkTraceSource.ETM)
        self._running = True
        _log.info("[JLink] 연결됨: {self._device} @ {self._cpu_hz//1_000_000}MHz, "
              f"SWO {self._swo_hz//1_000_000}MHz")

    def close(self) -> None:
        self._running = False
        if self._jlink:
            try:
                self._jlink.swo_stop()
                self._jlink.close()
            except Exception:
                pass
            self._jlink = None
        _log.info("[JLink] 연결 해제")

    _JLINK_RECONNECT_MAX = 3     # J-Link 재연결 최대 시도 횟수
    _BUF_MAX_BYTES       = 65536 # 호스트 버퍼 최대 크기 (64KB)
    _POLL_INTERVAL       = 0.001 # 1ms 폴링

    def stream(self) -> Iterator[bytes]:
        """
        SWO 버퍼에서 ITM 패킷을 지속적으로 읽어 반환.

        연결 오류 발생 시 최대 3회 재연결 시도.
        버퍼가 64KB 초과 시 오래된 데이터부터 절반 제거(backpressure).
        """
        reconnect_count = 0

        while self._running:
            try:
                data = self._jlink.swo_read(0, 1024, remove=True)
                if data:
                    self._buf.extend(bytes(data))
                    self._bytes_received += len(data)

                    # 버퍼 크기 제한 (overflow 방지)
                    if len(self._buf) > self._BUF_MAX_BYTES:
                        drop = len(self._buf) // 2
                        self._buf = self._buf[drop:]
                        _log.warning("[JLink] 버퍼 초과 → {drop}B 드롭")

                    for pkt in self._extract_packets():
                        self._packets_received += 1
                        yield pkt
                    reconnect_count = 0  # 성공 시 재시도 카운터 리셋
                else:
                    time.sleep(self._POLL_INTERVAL)

            except (OSError, RuntimeError) as e:
                # 연결 끊김 — 재연결 시도
                if not self._running:
                    break
                reconnect_count += 1
                if reconnect_count > self._JLINK_RECONNECT_MAX:
                    _log.error("[JLink] 재연결 {self._JLINK_RECONNECT_MAX}회 실패 — 수신 중단")
                    self._running = False
                    break
                _log.warning("[JLink] 오류: {e} — 재연결 시도 {reconnect_count}/{self._JLINK_RECONNECT_MAX}")
                time.sleep(1.0 * reconnect_count)  # 지수 백오프
                try:
                    self._jlink.swo_flush()
                    self._jlink.swo_start(self._swo_hz)
                except Exception:
                    pass

            except Exception as e:
                if self._running:
                    _log.error("[JLink] 예기치 않은 오류: {e}")
                    time.sleep(0.1)

    def _extract_packets(self) -> Iterator[bytes]:
        """버퍼에서 완전한 패킷 추출."""
        while len(self._buf) >= 4:
            # sync word 탐색
            idx = self._buf.find(struct.pack('<H', self.SYNC_MAGIC))
            if idx < 0:
                # sync word 없음 — 버퍼 최근 2바이트만 유지
                self._buf = self._buf[-2:]
                return
            if idx > 0:
                self._buf = self._buf[idx:]   # sync 이전 버림

            # 헤더 파싱 (최소 10바이트: magic(2)+ver(1)+type(1)+seq(2)+ts(4))
            if len(self._buf) < 10:
                return
            # 패킷 길이는 헤더 이후 데이터 크기 + CRC(4)
            # 간단 휴리스틱: 다음 sync word까지를 패킷으로 처리
            next_idx = self._buf.find(struct.pack('<H', self.SYNC_MAGIC), 2)
            if next_idx > 0:
                pkt = bytes(self._buf[:next_idx])
                self._buf = self._buf[next_idx:]
                yield pkt
            else:
                # 다음 sync 없음 — 버퍼 누적 대기
                return


# ── UART 수신기 ───────────────────────────────────────────────
class UARTCollector(BaseCollector):
    """
    UART 수신기.

    pyserial 라이브러리 사용:
      pip install pyserial>=3.5  (requirements.txt에 포함)

    사용:
      collector = UARTCollector(port='/dev/ttyUSB0', baud=115200)
      collector.open()
      for pkt in collector.stream():
          parser.feed(pkt)
    """

    SYNC_MAGIC = 0xDEAD

    def __init__(self,
                 port:    str = '/dev/ttyUSB0',
                 baud:    int = 115200,
                 timeout: float = 1.0):
        super().__init__()
        self._port    = port
        self._baud    = baud
        self._timeout = timeout
        self._serial  = None
        self._buf     = bytearray()

    def open(self) -> None:
        try:
            import serial
        except ImportError:
            raise ImportError(
                "pyserial 미설치.\n"
                "설치: pip install pyserial>=3.5")

        import serial
        self._serial = serial.Serial(
            port     = self._port,
            baudrate = self._baud,
            timeout  = self._timeout,
            bytesize = serial.EIGHTBITS,
            parity   = serial.PARITY_NONE,
            stopbits = serial.STOPBITS_ONE,
        )
        self._running = True
        _log.info("[UART] 연결됨: {self._port} @ {self._baud} baud")

    def close(self) -> None:
        self._running = False
        if self._serial and self._serial.is_open:
            self._serial.close()
            self._serial = None
        _log.info("[UART] 연결 해제")

    _BUF_MAX_BYTES = 65536  # 64KB 버퍼 상한

    def stream(self) -> Iterator[bytes]:
        """
        UART에서 Binary Protocol 패킷을 지속적으로 읽어 반환.

        직렬 포트 오류(케이블 탈거, 권한 문제) 시 명확한 메시지와 함께 중단.
        버퍼 64KB 초과 시 오래된 데이터 절반 제거.
        """
        import serial

        while self._running:
            try:
                data = self._serial.read(256)
                if data:
                    self._buf.extend(data)
                    self._bytes_received += len(data)

                    # 버퍼 크기 제한
                    if len(self._buf) > self._BUF_MAX_BYTES:
                        drop = len(self._buf) // 2
                        self._buf = self._buf[drop:]
                        _log.warning("[UART] 버퍼 초과 → {drop}B 드롭")

                    for pkt in self._extract_packets():
                        self._packets_received += 1
                        yield pkt

            except serial.SerialException as e:
                _log.error("[UART] 포트 오류: {e}")
                _log.info("  힌트: sudo usermod -aG dialout $USER && newgrp dialout")
                self._running = False
                break
            except PermissionError as e:
                _log.error("[UART] 권한 오류: {e}")
                _log.info("  힌트: sudo chmod 666 {self._port}  또는  sudo usermod -aG dialout $USER")
                self._running = False
                break
            except OSError as e:
                if self._running:
                    _log.warning("[UART] OS 오류: {e} — 재시도 중...")
                    time.sleep(0.5)
            except Exception as e:
                if self._running:
                    _log.error("[UART] 예기치 않은 오류: {e}")
                    time.sleep(0.1)

    def _extract_packets(self) -> Iterator[bytes]:
        """버퍼에서 완전한 패킷 추출 (JLink와 동일 로직)."""
        while len(self._buf) >= 4:
            idx = self._buf.find(struct.pack('<H', self.SYNC_MAGIC))
            if idx < 0:
                self._buf = self._buf[-2:]
                return
            if idx > 0:
                self._buf = self._buf[idx:]
            if len(self._buf) < 10:
                return
            next_idx = self._buf.find(struct.pack('<H', self.SYNC_MAGIC), 2)
            if next_idx > 0:
                pkt = bytes(self._buf[:next_idx])
                self._buf = self._buf[next_idx:]
                yield pkt
            else:
                return


# ── 시뮬레이션 수신기 ────────────────────────────────────────
class SimulateCollector(BaseCollector):
    """
    시뮬레이션 수신기 — 하드웨어 없이 테스트용.

    integrated_demo.py --simulate-switch 와 동일한 데이터를 생성한다.
    실제 하드웨어 없이 파이프라인 전체를 테스트할 때 사용.
    """

    def __init__(self, scenario: str = 'deadlock', interval: float = 3.0):
        super().__init__()
        self._scenario = scenario
        self._interval = interval

    def open(self) -> None:
        self._running = True
        _log.debug("[Simulate] 시작 (시나리오: {self._scenario})")

    def close(self) -> None:
        self._running = False
        _log.debug("[Simulate] 종료")

    def stream(self) -> Iterator[bytes]:
        """시나리오별 합성 스냅샷 생성."""
        seq = 0
        scenarios = {
            'deadlock': self._gen_deadlock,
            'stack':    self._gen_stack_overflow,
            'heap':     self._gen_heap_exhaustion,
        }
        gen_fn = scenarios.get(self._scenario, self._gen_deadlock)

        while self._running:
            pkt = gen_fn(seq)
            seq += 1
            self._packets_received += 1
            self._bytes_received   += len(pkt)
            yield pkt
            time.sleep(self._interval)

    def _gen_deadlock(self, seq: int) -> bytes:
        """데드락 시나리오 패킷 (dict 형태 — StreamingParser 우회)."""
        import json
        snap = {
            '_sim': True, 'sequence': seq, 'snapshot_count': seq+1,
            'timestamp_us': seq * 3_000_000,
            'uptime_ms':    seq * 3000,
            'cpu_usage':    96 + min(3, seq % 4),  # seq=0: 96% → high_cpu 즉시 감지
            '_parser_stats': {},
            'heap': {'free': 2000 - seq*50, 'min': 1800,
                     'total': 8192, 'used_pct': 75 + seq},
            'tasks': [
                {'task_id':0,'name':'Task0','priority':5,'state':2,
                 'state_name':'Blocked','cpu_pct':0,'stack_hwm':8   ,'runtime_us':0},
                {'task_id':1,'name':'Task1','priority':3,'state':2,
                 'state_name':'Blocked','cpu_pct':0,'stack_hwm':15  ,'runtime_us':0},
            ]
        }
        return json.dumps(snap).encode()

    def _gen_stack_overflow(self, seq: int) -> bytes:
        import json
        snap = {
            '_sim': True, 'sequence': seq, 'snapshot_count': seq+1,
            'timestamp_us': seq * 3_000_000, 'uptime_ms': seq * 3000,
            'cpu_usage': 60, '_parser_stats': {},
            'heap': {'free': 4000,'min': 3500,'total': 8192,'used_pct': 51},
            'tasks': [
                {'task_id':0,'name':'HighTask','priority':5,'state':0,
                 'state_name':'Running','cpu_pct':60,
                 'stack_hwm': max(5, 15 - seq*2), 'runtime_us': 0},
            ]
        }
        return json.dumps(snap).encode()

    def _gen_heap_exhaustion(self, seq: int) -> bytes:
        import json
        free = max(50, 120 - seq * 10)   # seq=0: free=120B → low_heap 즉시 감지
        snap = {
            '_sim': True, 'sequence': seq, 'snapshot_count': seq+1,
            'timestamp_us': seq * 3_000_000, 'uptime_ms': seq * 3000,
            'cpu_usage': 70, '_parser_stats': {},
            'heap': {'free': free, 'min': free-100,
                     'total': 8192, 'used_pct': int((8192-free)/8192*100)},
            'tasks': [
                {'task_id':0,'name':'AllocTask','priority':5,'state':0,
                 'state_name':'Running','cpu_pct':96    ,'stack_hwm':150,'runtime_us':0},
            ]
        }
        return json.dumps(snap).encode()


# ── 팩토리 함수 ───────────────────────────────────────────────
def Collector(port_str: str,
              cpu_hz: int = 180_000_000,
              **kwargs) -> BaseCollector:
    """
    포트 문자열로 적합한 수신기 반환.

    포트 형식:
      'jlink'                → JLinkCollector
      'uart:/dev/ttyUSB0'   → UARTCollector(port='/dev/ttyUSB0')
      'uart:COM3'           → UARTCollector(port='COM3')
      'simulate'            → SimulateCollector
      'simulate:stack'      → SimulateCollector(scenario='stack')

    예시:
      collector = Collector('jlink', cpu_hz=180_000_000)
      collector = Collector('uart:/dev/ttyUSB0', baud=115200)
      collector = Collector('simulate:deadlock')
    """
    # 접두사만 소문자 정규화 — 포트 경로는 대소문자 보존
    port_lower = port_str.strip().lower()

    if port_lower == 'jlink':
        return JLinkCollector(cpu_hz=cpu_hz, **kwargs)

    if port_lower.startswith('uart:'):
        port = port_str.strip()[5:]  # 원본 대소문자 보존
        baud = kwargs.pop('baud', 115200)
        return UARTCollector(port=port, baud=baud, **kwargs)

    if port_lower.startswith('simulate'):
        scenario = port_lower.split(':')[1] if ':' in port_lower else 'deadlock'
        return SimulateCollector(scenario=scenario, **kwargs)

    raise ValueError(
        f"알 수 없는 포트: '{port_str}'\n"
        f"지원 형식: jlink / uart:/dev/ttyUSB0 / simulate[:deadlock|stack|heap]")



# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  integrated_demo.py 호환 API
#  (ITM SWO 프레임 파싱 + 레거시 create_collector)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

import struct as _struct
from typing import Callable


class ITMPortAccumulator:
    """
    ITM SWO 바이트 스트림 → ParsedSnapshot / ParsedFault 콜백.

    ITM 채널 0 데이터를 누적하여 완전한 Binary Protocol V4 패킷이
    완성되면 BinaryParserV3로 파싱 후 on_packet 콜백을 호출한다.

    integrated_demo.py 호환: on_packet(ParsedSnapshot | ParsedFault)
    """

    def __init__(self, on_packet: Callable):
        self._cb  = on_packet
        self._buf = bytearray()
        # BinaryParserV3 지연 import (순환 import 방지)
        self._parser = None

    def _get_parser(self):
        if self._parser is None:
            from parsers.binary_parser import BinaryParserV3
            self._parser = BinaryParserV3()
        return self._parser

    def feed(self, data: bytes) -> None:
        """바이트 데이터 추가 후 완전한 패킷 파싱."""
        self._buf.extend(data)
        self._flush()

    def flush(self) -> None:
        """
        버퍼에 남은 데이터를 강제 파싱.

        단일 패킷 테스트나 스트림 종료 시 호출.
        다음 SYNC가 없어도 버퍼 전체를 BinaryParserV3로 직접 파싱.
        """
        self._flush(force=True)

    def _flush(self, force: bool = False) -> None:
        """
        SYNC 경계 기반 패킷 추출.

        force=True: 다음 SYNC 없어도 버퍼 전체를 파싱 시도 (단일 패킷용)
        force=False: 다음 SYNC 발견 시에만 파싱 (스트리밍 기본 동작)
        """
        SYNC = b'\xc1\xad'   # Binary Protocol V4: MAGIC1=0xC1, MAGIC2=0xAD
        while len(self._buf) >= 4:
            idx = self._buf.find(SYNC)
            if idx < 0:
                self._buf = self._buf[-1:]
                return
            if idx > 0:
                self._buf = self._buf[idx:]
            next_idx = self._buf.find(SYNC, 2)
            if next_idx < 0:
                if force:
                    raw_pkt = bytes(self._buf)
                    self._buf = bytearray()
                    if raw_pkt and len(raw_pkt) >= 10:
                        result = self._get_parser().parse_packet(raw_pkt)
                        if result is not None:
                            self._cb(result)
                return
            raw_pkt = bytes(self._buf[:next_idx])
            self._buf = self._buf[next_idx:]
            if raw_pkt and len(raw_pkt) >= 10:  # 최소 헤더 크기 검증
                result = self._get_parser().parse_packet(raw_pkt)
                if result is not None:
                    self._cb(result)
                # parse 실패는 sync 충돌 또는 손상 패킷 → 조용히 버림


def parse_itm_swo_frame(frame: bytes,
                         acc: ITMPortAccumulator,
                         stats: dict) -> None:
    """
    ITM SWO 프레임을 파싱하여 ITMPortAccumulator에 전달.

    ITM 패킷 형식 (ARMv7-M):
      0x00            — sync / padding → 스킵
      0x70            — ITM overflow → itm_overflow 카운터 증가
      hdr & 0x03 > 0  — Stimulus 패킷: size = 1/2/4B
      hdr bit[7:3]    — port number (ClaudeRTOS = 0)

    Stats keys:
      frames       — 호출 횟수
      bytes_ch0    — 채널 0으로 수신된 바이트
      itm_overflow — 오버플로 패킷 수
      malformed    — 잘못된 패킷 수
    """
    stats.setdefault('frames', 0)
    stats.setdefault('bytes_ch0', 0)
    stats.setdefault('itm_overflow', 0)
    stats.setdefault('malformed', 0)
    stats['frames'] += 1

    i = 0
    while i < len(frame):
        hdr = frame[i]
        i += 1

        if hdr == 0x00:
            # sync / padding → 스킵
            continue

        if hdr == 0x70:
            # ITM 오버플로 패킷
            stats['itm_overflow'] += 1
            continue

        # Stimulus (Software) 패킷
        size_bits = hdr & 0x03
        port      = (hdr >> 3) & 0x1F

        if size_bits == 0b01:
            sz = 1
        elif size_bits == 0b10:
            sz = 2
        elif size_bits == 0b11:
            sz = 4
        else:
            stats['malformed'] += 1
            continue

        # wrap_itm 형식: [hdr(1B)][data(1B)] 인터리브
        # → 각 ITM 헤더 다음에 실제 데이터는 항상 1바이트
        # (size_bits가 4를 가리켜도 wrap_itm은 1바이트씩 감쌈)
        if i >= len(frame):
            stats['malformed'] += 1
            break

        data = frame[i:i + 1]   # 항상 1바이트씩 읽기
        i   += 1

        if port == 0:
            stats['bytes_ch0'] += 1
            acc.feed(data)
        elif port == 1:
            # Ch1: Fault 패킷 (ch0과 동일 acc로 병합)
            stats.setdefault('bytes_ch1', 0)
            stats['bytes_ch1'] += 1
            acc.feed(data)


def create_collector(source: str,
                     on_packet: Callable[[bytes], None],
                     **kwargs):
    """
    레거시 팩토리 — integrated_demo.py 호환.

    source:
      'jlink'            → JLinkCollector 래퍼
      'uart:/dev/...'    → UARTCollector 래퍼
      'simulate'         → SimulateCollector 래퍼
      'validate'         → DummyCollector (검증 전용)

    반환: start() / stop() 메서드를 가진 객체
    """
    return _LegacyCollectorWrapper(source, on_packet, **kwargs)


class _LegacyCollectorWrapper:
    """create_collector() 반환 객체 — start/stop 인터페이스."""

    def __init__(self, source: str, on_packet: Callable, **kwargs):
        self._source    = source
        self._cb        = on_packet
        self._collector = None
        self._thread    = None
        self._kwargs    = kwargs

    def start(self) -> bool:
        import threading
        try:
            self._collector = Collector(self._source, **self._kwargs)
            self._collector.open()
        except Exception as e:
            print(f"[Collector] 시작 실패: {e}")
            return False
        self._thread = threading.Thread(
            target=self._run, daemon=True)
        self._thread.start()
        return True

    def _run(self):
        try:
            for raw in self._collector.stream():
                self._cb(raw)
        except Exception:
            pass

    def stop(self):
        if self._collector:
            self._collector.close()
        if self._thread:
            self._thread.join(timeout=2)
