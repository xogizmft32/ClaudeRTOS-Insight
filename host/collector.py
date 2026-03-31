#!/usr/bin/env python3
"""
ClaudeRTOS-Insight Collector V3.2

지원 전송 방식:
  ITM/SWO  — J-Link  (pylink-square)
  ITM/SWO  — OpenOCD (TCP)
  UART     — 시리얼 포트 (pyserial)

핵심 수정 (ITM-06,07,08,09,10):
  - ITM 프로토콜 헤더 파싱 수정 (ARM IHI0029E 스펙)
      port      = (header >> 3) & 0x1F   ← 수정 (기존: header & 0x1F)
      size_bits = (header >> 1) & 0x03   ← 수정 (기존: (header >> 3) & 0x03)
  - 포트별 바이트 누적 후 StreamingParser.feed() 호출
  - 루프에서 첫 패킷만 return 하던 버그 제거 (전체 swo_data 처리)
  - OpenOCDCollector TCP 구현 추가
  - UARTCollector 추가
  - 모든 Collector → StreamingParser → 콜백 통일 인터페이스
"""

from __future__ import annotations

import time
import socket
import threading
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Optional, Callable, Dict

logger = logging.getLogger(__name__)


# ── 콜백 타입 ────────────────────────────────────────────────────
# on_packet(result): ParsedSnapshot | ParsedFault | None
PacketCallback = Callable[[object], None]


# ═══════════════════════════════════════════════════════════════
#  ITM 포트별 바이트 누산기
# ═══════════════════════════════════════════════════════════════
class ITMPortAccumulator:
    """
    포트별 바이트를 누적하다 StreamingParser로 전달.

    ITM 스티뮬러스 패킷은 1~4바이트 단위로 도착.
    ClaudeRTOS 바이너리 패킷은 최소 16바이트이므로
    여러 ITM 패킷이 쌓여야 완성됨.

    StreamingParser는 완성된 패킷을 발견하면 콜백을 호출.
    """

    def __init__(self, on_packet: PacketCallback):
        # port → StreamingParser 인스턴스
        from parsers.binary_parser import BinaryParserV3, StreamingParser
        self._parsers: Dict[int, StreamingParser] = {}
        self._on_packet = on_packet
        self._ParserCls  = BinaryParserV3
        self._StreamCls  = StreamingParser

    def _get_parser(self, port: int) -> object:
        if port not in self._parsers:
            bp = self._ParserCls()
            sp = self._StreamCls(bp)
            sp.on_packet(self._on_packet)
            self._parsers[port] = sp
        return self._parsers[port]

    def feed_port(self, port: int, data: bytes) -> None:
        """포트 N 에서 수신한 바이트들을 해당 StreamingParser로 전달."""
        self._get_parser(port).feed(data)

    def get_stats(self) -> dict:
        stats = {}
        for port, sp in self._parsers.items():
            stats[f'port{port}'] = sp.stats
        return stats


# ═══════════════════════════════════════════════════════════════
#  ITM SWO 프레임 파서
# ═══════════════════════════════════════════════════════════════
def parse_itm_swo_frame(frame: bytes,
                         accumulator: ITMPortAccumulator,
                         stats: dict) -> None:
    """
    ITM/SWO 원시 바이트 프레임을 파싱하여 포트별 페이로드를
    ITMPortAccumulator로 전달.

    ARM IHI0029E 스티뮬러스 패킷 포맷:
      Bit 7-3 : 포트 주소 (5비트, 0-31)
      Bit 2-1 : 페이로드 크기 (00=reserved, 01=1B, 10=2B, 11=4B)
      Bit 0   : 1 (소스 패킷 마커)

    ITM-06 fix: 올바른 비트 추출
    ITM-07 fix: frame 전체 처리 (첫 패킷 후 return 제거)
    """
    i = 0
    while i < len(frame):
        hdr = frame[i]
        i += 1

        # ── 동기 패킷 (0x00 = null) ──────────────────
        if hdr == 0x00:
            continue

        # ── 오버플로 패킷 (0x70) ─────────────────────
        if hdr == 0x70:
            stats['itm_overflow'] = stats.get('itm_overflow', 0) + 1
            logger.warning("ITM overflow detected")
            continue

        # ── 소스(스티뮬러스) 패킷인지 확인 ───────────
        if (hdr & 0x01) == 0:
            # 소스 패킷이 아님 (timestamp, hardware source 등) → 스킵
            # 타임스탬프 패킷: header & 0x0F == 0, 이하 가변 길이
            # 단순 처리: 다음 바이트로
            continue

        # ── 올바른 포트·크기 추출 (ITM-06 fix) ───────
        port      = (hdr >> 3) & 0x1F          # bits 7:3
        size_bits = (hdr >> 1) & 0x03          # bits 2:1
        size_map  = {0b01: 1, 0b10: 2, 0b11: 4}
        size      = size_map.get(size_bits, 0)

        if size == 0:
            stats['parse_errors'] = stats.get('parse_errors', 0) + 1
            continue

        if i + size > len(frame):
            # 프레임 끝에서 잘림 → 다음 호출에서 이어받지 못함
            # (StreamingParser가 내부에서 처리)
            stats['truncated'] = stats.get('truncated', 0) + 1
            break

        payload = frame[i:i + size]
        i += size

        # ── 포트별 누산기로 전달 (ITM-08 fix) ────────
        stats['bytes_received'] = stats.get('bytes_received', 0) + size
        accumulator.feed_port(port, payload)


# ═══════════════════════════════════════════════════════════════
#  추상 Collector
# ═══════════════════════════════════════════════════════════════
class BaseCollector(ABC):

    def __init__(self, on_packet: PacketCallback):
        self._on_packet  = on_packet
        self._accumulator = ITMPortAccumulator(on_packet)
        self._running    = False
        self._thread: Optional[threading.Thread] = None
        self.stats: dict = {'bytes_received': 0, 'itm_overflow': 0,
                            'parse_errors': 0, 'truncated': 0,
                            'connect_errors': 0}

    @abstractmethod
    def _connect(self) -> bool:
        """하드웨어/소켓 연결. True=성공."""

    @abstractmethod
    def _disconnect(self) -> None:
        """연결 해제."""

    @abstractmethod
    def _read_raw(self) -> Optional[bytes]:
        """원시 바이트 한 청크 읽기. None=타임아웃/없음."""

    def start(self) -> bool:
        """수집 시작 (별도 스레드)."""
        if not self._connect():
            return False
        self._running = True
        self._thread  = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._running = False
        self._disconnect()
        if self._thread:
            self._thread.join(timeout=3.0)

    def _loop(self) -> None:
        """수집 루프 (스레드에서 실행)."""
        while self._running:
            raw = self._read_raw()
            if raw:
                self._process(raw)

    def _process(self, raw: bytes) -> None:
        """원시 바이트를 ITM 파서로 전달."""
        parse_itm_swo_frame(raw, self._accumulator, self.stats)

    def get_port_stats(self) -> dict:
        return self._accumulator.get_stats()


# ═══════════════════════════════════════════════════════════════
#  J-Link SWO Collector  (ITM-09 fix: StreamingParser 연결)
# ═══════════════════════════════════════════════════════════════
class JLinkCollector(BaseCollector):
    """
    J-Link SWO를 통해 ITM 데이터를 수집.
    pylink-square 라이브러리 필요: pip install pylink-square
    """

    SWO_SPEED = 2_250_000   # 2.25 MHz

    def __init__(self, on_packet: PacketCallback,
                 device: str = "STM32F446RE",
                 swd_speed_khz: int = 4000):
        super().__init__(on_packet)
        self._device   = device
        self._speed    = swd_speed_khz
        self._jlink    = None

    def _connect(self) -> bool:
        try:
            import pylink
            self._jlink = pylink.JLink()
            self._jlink.open()
            self._jlink.set_tif(pylink.enums.JLinkInterfaces.SWD)
            self._jlink.connect(self._device, self._speed)
            self._jlink.swo_start(self.SWO_SPEED)
            # 포트 0(바이너리), 3(진단) 활성화
            self._jlink.swo_enable(0b1001)   # 비트 0, 3
            logger.info("J-Link connected: %s @ %d kHz SWD, SWO=%d Hz",
                        self._device, self._speed, self.SWO_SPEED)
            return True
        except ImportError:
            logger.error("pylink-square not installed: pip install pylink-square")
            self.stats['connect_errors'] += 1
            return False
        except Exception as e:
            logger.error("J-Link connection failed: %s", e)
            self.stats['connect_errors'] += 1
            return False

    def _disconnect(self) -> None:
        if self._jlink:
            try:
                self._jlink.swo_stop()
                self._jlink.close()
            except Exception:
                pass
            self._jlink = None

    def _read_raw(self) -> Optional[bytes]:
        if not self._jlink:
            return None
        try:
            # ITM-07 fix: swo_read로 전체 데이터를 한 번에 읽음
            data = self._jlink.swo_read(0, 4096, remove=True)
            if data:
                return bytes(data)
            time.sleep(0.005)
            return None
        except Exception as e:
            logger.debug("J-Link read error: %s", e)
            self.stats['parse_errors'] += 1
            return None


# ═══════════════════════════════════════════════════════════════
#  OpenOCD TCP Collector  (ITM-10: 실제 구현)
# ═══════════════════════════════════════════════════════════════
class OpenOCDCollector(BaseCollector):
    """
    OpenOCD TCP ITM 수집.

    OpenOCD 설정 예시 (openocd.cfg):
        source [find interface/stlink.cfg]
        source [find target/stm32f4x.cfg]
        tpiu config internal /tmp/itm.fifo uart false 180000000 2250000
        itm ports on

    또는 tcl_port를 통한 제어:
        openocd -f openocd.cfg -c "tpiu config ..."

    이 클래스는 OpenOCD의 ITM raw capture TCP 포트(기본 3344)에서
    SWO 바이트 스트림을 직접 읽는다.

    OpenOCD 실행:
        openocd -f interface/stlink.cfg -f target/stm32f4x.cfg \
                -c "tpiu config internal - uart false 180000000 2250000" \
                -c "tcl_port 6666"
    그 후 별도 터미널:
        nc localhost 3344  # SWO raw stream
    """

    def __init__(self, on_packet: PacketCallback,
                 host: str = "localhost",
                 port: int = 3344,
                 timeout: float = 0.1):
        super().__init__(on_packet)
        self._host    = host
        self._port    = port
        self._timeout = timeout
        self._sock: Optional[socket.socket] = None

    def _connect(self) -> bool:
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.settimeout(self._timeout)
            self._sock.connect((self._host, self._port))
            logger.info("OpenOCD connected: %s:%d", self._host, self._port)
            return True
        except Exception as e:
            logger.error("OpenOCD connection failed: %s", e)
            self.stats['connect_errors'] += 1
            return False

    def _disconnect(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def _read_raw(self) -> Optional[bytes]:
        if not self._sock:
            return None
        try:
            data = self._sock.recv(4096)
            return data if data else None
        except socket.timeout:
            return None
        except Exception as e:
            logger.debug("OpenOCD read error: %s", e)
            self.stats['parse_errors'] += 1
            return None


# ═══════════════════════════════════════════════════════════════
#  UART Collector
# ═══════════════════════════════════════════════════════════════
class UARTCollector(BaseCollector):
    """
    UART 시리얼 포트에서 ClaudeRTOS 바이너리 스트림을 수집.

    UART 모드에서는 ITM SWO 패킷 래핑이 없으므로
    바이트를 직접 StreamingParser로 전달 (포트 0 고정).

    pyserial 필요: pip install pyserial
    """

    def __init__(self, on_packet: PacketCallback,
                 port: str = "/dev/ttyUSB0",
                 baudrate: int = 115200,
                 timeout: float = 0.1):
        super().__init__(on_packet)
        self._port_name = port
        self._baudrate  = baudrate
        self._timeout   = timeout
        self._serial    = None
        # UART는 ITM 래핑 없이 직접 StreamingParser로
        from parsers.binary_parser import BinaryParserV3, StreamingParser
        self._stream_parser = StreamingParser(BinaryParserV3())
        self._stream_parser.on_packet(on_packet)

    def _connect(self) -> bool:
        try:
            import serial
            self._serial = serial.Serial(
                port=self._port_name,
                baudrate=self._baudrate,
                timeout=self._timeout,
            )
            logger.info("UART connected: %s @ %d baud",
                        self._port_name, self._baudrate)
            return True
        except ImportError:
            logger.error("pyserial not installed: pip install pyserial")
            self.stats['connect_errors'] += 1
            return False
        except Exception as e:
            logger.error("UART connection failed: %s", e)
            self.stats['connect_errors'] += 1
            return False

    def _disconnect(self) -> None:
        if self._serial:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def _read_raw(self) -> Optional[bytes]:
        if not self._serial:
            return None
        try:
            waiting = self._serial.in_waiting
            if waiting > 0:
                return self._serial.read(min(waiting, 4096))
            time.sleep(0.005)
            return None
        except Exception as e:
            logger.debug("UART read error: %s", e)
            self.stats['parse_errors'] += 1
            return None

    def _process(self, raw: bytes) -> None:
        """UART: ITM 파싱 없이 직접 StreamingParser로."""
        self.stats['bytes_received'] = \
            self.stats.get('bytes_received', 0) + len(raw)
        self._stream_parser.feed(raw)


# ═══════════════════════════════════════════════════════════════
#  Factory
# ═══════════════════════════════════════════════════════════════
def create_collector(source: str,
                     on_packet: PacketCallback,
                     **kwargs) -> BaseCollector:
    """
    source 형식:
      'jlink'              → J-Link SWO
      'jlink:STM32F446RE'  → 디바이스 지정
      'openocd'            → OpenOCD TCP (localhost:3344)
      'openocd:host:port'  → 호스트:포트 지정
      'uart:/dev/ttyUSB0'  → UART 시리얼
      'uart:COM3:9600'     → 포트:보드레이트 지정
    """
    parts = source.split(':', 2)
    kind  = parts[0].lower()

    if kind == 'jlink':
        device = parts[1] if len(parts) > 1 else kwargs.get('device', 'STM32F446RE')
        return JLinkCollector(on_packet, device=device, **{
            k: v for k, v in kwargs.items() if k not in ('device',)})

    if kind == 'openocd':
        host = parts[1] if len(parts) > 1 else kwargs.get('host', 'localhost')
        port = int(parts[2]) if len(parts) > 2 else kwargs.get('port', 3344)
        return OpenOCDCollector(on_packet, host=host, port=port)

    if kind == 'uart':
        dev  = parts[1] if len(parts) > 1 else kwargs.get('port', '/dev/ttyUSB0')
        baud = int(parts[2]) if len(parts) > 2 else kwargs.get('baudrate', 115200)
        return UARTCollector(on_packet, port=dev, baudrate=baud)

    raise ValueError(
        f"Unknown source '{source}'. "
        "Use: jlink, openocd, uart:/dev/ttyUSB0, uart:COM3:115200"
    )
