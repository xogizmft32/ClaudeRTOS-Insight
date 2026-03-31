#!/usr/bin/env python3
"""
Fault Injection Tester
Automated testing of fault handling capabilities
"""

import sys
import time
import serial
import argparse
from typing import List, Dict, Optional
from dataclasses import dataclass
from enum import Enum

class FaultType(Enum):
    """Fault types matching firmware"""
    STACK_OVERFLOW = 1
    HEAP_EXHAUSTION = 2
    NULL_POINTER = 3
    DIVISION_BY_ZERO = 4
    DEADLOCK = 5
    PRIORITY_INVERSION = 6
    BUFFER_OVERFLOW = 7
    ASSERT_FAILURE = 8

@dataclass
class FaultTestResult:
    """Test result from firmware"""
    fault_type: FaultType
    fault_detected: bool
    detection_time_ms: int
    system_recovered: bool
    recovery_time_ms: int
    critical_event_captured: bool
    buffer_drops: int
    critical_drops: int
    details: str
    passed: bool

class FaultInjectionTester:
    """
    Automated fault injection tester
    
    Connects to board, runs fault tests, collects results
    """
    
    def __init__(self, port: str, baudrate: int = 115200):
        """
        Initialize tester
        
        Args:
            port: Serial port (e.g., '/dev/ttyUSB0')
            baudrate: Baud rate (default: 115200)
        """
        self.port = port
        self.baudrate = baudrate
        self.ser = None
        self.results: List[FaultTestResult] = []
    
    def connect(self):
        """Connect to board"""
        try:
            self.ser = serial.Serial(self.port, self.baudrate, timeout=2)
            print(f"✓ Connected to {self.port} @ {self.baudrate} baud")
            time.sleep(2)  # Wait for board to settle
            return True
        except Exception as e:
            print(f"✗ Connection failed: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from board"""
        if self.ser:
            self.ser.close()
            print("✓ Disconnected")
    
    def send_command(self, command: str) -> bool:
        """
        Send command to board
        
        Args:
            command: Command string
            
        Returns:
            True if sent successfully
        """
        if not self.ser:
            return False
        
        try:
            self.ser.write(f"{command}\n".encode())
            self.ser.flush()
            return True
        except Exception as e:
            print(f"✗ Command send failed: {e}")
            return False
    
    def read_result(self, timeout: float = 10.0) -> Optional[FaultTestResult]:
        """
        Read test result from board
        
        Args:
            timeout: Max time to wait in seconds
            
        Returns:
            FaultTestResult or None if timeout
        """
        if not self.ser:
            return None
        
        start_time = time.time()
        result_lines = []
        
        while time.time() - start_time < timeout:
            if self.ser.in_waiting:
                line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                print(f"  {line}")
                result_lines.append(line)
                
                # Check for test completion marker
                if "Result:" in line:
                    return self.parse_result(result_lines)
        
        print(f"✗ Timeout waiting for result")
        return None
    
    def parse_result(self, lines: List[str]) -> FaultTestResult:
        """
        Parse test result from output lines
        
        Args:
            lines: Output lines from firmware
            
        Returns:
            Parsed FaultTestResult
        """
        # Simple parsing - in production, use structured format
        result = FaultTestResult(
            fault_type=FaultType.HEAP_EXHAUSTION,
            fault_detected=False,
            detection_time_ms=0,
            system_recovered=False,
            recovery_time_ms=0,
            critical_event_captured=False,
            buffer_drops=0,
            critical_drops=0,
            details="",
            passed=False
        )
        
        for line in lines:
            if "Fault Detected: YES" in line:
                result.fault_detected = True
            elif "Detection Time:" in line:
                try:
                    result.detection_time_ms = int(line.split(':')[1].strip().split()[0])
                except:
                    pass
            elif "System Recovered: YES" in line:
                result.system_recovered = True
            elif "Recovery Time:" in line:
                try:
                    result.recovery_time_ms = int(line.split(':')[1].strip().split()[0])
                except:
                    pass
            elif "Critical Event Captured: YES" in line:
                result.critical_event_captured = True
            elif "Buffer Drops:" in line:
                try:
                    result.buffer_drops = int(line.split(':')[1].strip())
                except:
                    pass
            elif "Critical Drops:" in line:
                try:
                    result.critical_drops = int(line.split(':')[1].strip())
                except:
                    pass
            elif "Details:" in line:
                result.details = line.split(':', 1)[1].strip()
            elif "✅ PASS" in line:
                result.passed = True
            elif "❌ FAIL" in line:
                result.passed = False
        
        return result
    
    def run_test(self, fault_type: FaultType) -> Optional[FaultTestResult]:
        """
        Run single fault injection test
        
        Args:
            fault_type: Type of fault to inject
            
        Returns:
            Test result or None if failed
        """
        print(f"\n{'='*50}")
        print(f"Testing: {fault_type.name}")
        print(f"{'='*50}")
        
        # Send test command
        command = f"FAULT_INJECT {fault_type.value}"
        if not self.send_command(command):
            return None
        
        # Wait for result
        result = self.read_result(timeout=15.0)
        
        if result:
            result.fault_type = fault_type
            self.results.append(result)
        
        return result
    
    def run_all_tests(self) -> Dict[str, any]:
        """
        Run all fault injection tests
        
        Returns:
            Summary of results
        """
        print("\n" + "="*50)
        print("  Fault Injection Test Suite")
        print("="*50)
        
        # Safe tests (won't crash board)
        safe_tests = [
            FaultType.HEAP_EXHAUSTION,
            FaultType.DIVISION_BY_ZERO,
            FaultType.DEADLOCK,
            FaultType.BUFFER_OVERFLOW
        ]
        
        self.results.clear()
        
        for fault_type in safe_tests:
            result = self.run_test(fault_type)
            
            if result:
                status = "✅ PASS" if result.passed else "❌ FAIL"
                print(f"  {fault_type.name}: {status}")
            else:
                print(f"  {fault_type.name}: ⚠️  TIMEOUT")
            
            # Delay between tests
            time.sleep(1)
        
        # Generate summary
        passed = sum(1 for r in self.results if r.passed)
        total = len(self.results)
        
        summary = {
            'total_tests': total,
            'passed': passed,
            'failed': total - passed,
            'pass_rate': (passed / total * 100) if total > 0 else 0,
            'results': self.results
        }
        
        print("\n" + "="*50)
        print(f"  Tests Passed: {passed} / {total} ({summary['pass_rate']:.1f}%)")
        print("="*50)
        
        # Check critical conditions
        critical_drops = sum(r.critical_drops for r in self.results)
        if critical_drops > 0:
            print(f"\n⚠️  WARNING: {critical_drops} critical events were dropped!")
        
        return summary
    
    def export_results(self, filename: str = "fault_test_results.json"):
        """
        Export results to JSON file
        
        Args:
            filename: Output filename
        """
        import json
        
        data = {
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'port': self.port,
            'results': [
                {
                    'fault_type': r.fault_type.name,
                    'fault_detected': r.fault_detected,
                    'detection_time_ms': r.detection_time_ms,
                    'system_recovered': r.system_recovered,
                    'recovery_time_ms': r.recovery_time_ms,
                    'critical_event_captured': r.critical_event_captured,
                    'buffer_drops': r.buffer_drops,
                    'critical_drops': r.critical_drops,
                    'details': r.details,
                    'passed': r.passed
                }
                for r in self.results
            ]
        }
        
        with open(filename, 'w') as f:
            json.dump(data, f, indent=2)
        
        print(f"\n✓ Results exported to {filename}")

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Fault Injection Tester for ClaudeRTOS-Insight'
    )
    parser.add_argument('port', help='Serial port (e.g., /dev/ttyUSB0)')
    parser.add_argument('--baudrate', type=int, default=115200,
                       help='Baud rate (default: 115200)')
    parser.add_argument('--test', type=str,
                       help='Run specific test (HEAP, DEADLOCK, etc.)')
    parser.add_argument('--export', type=str, default='fault_test_results.json',
                       help='Export results to file')
    
    args = parser.parse_args()
    
    # Create tester
    tester = FaultInjectionTester(args.port, args.baudrate)
    
    # Connect
    if not tester.connect():
        return 1
    
    try:
        if args.test:
            # Run specific test
            try:
                fault_type = FaultType[args.test.upper()]
                result = tester.run_test(fault_type)
                
                if result:
                    return 0 if result.passed else 1
                else:
                    return 1
            except KeyError:
                print(f"✗ Unknown test: {args.test}")
                return 1
        else:
            # Run all tests
            summary = tester.run_all_tests()
            
            # Export results
            if args.export:
                tester.export_results(args.export)
            
            # Return 0 if all tests passed
            return 0 if summary['passed'] == summary['total_tests'] else 1
    
    finally:
        tester.disconnect()

if __name__ == '__main__':
    sys.exit(main())
