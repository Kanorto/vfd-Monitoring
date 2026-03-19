from __future__ import annotations

from dataclasses import dataclass
import threading
import time


@dataclass
class CounterSnapshot:
    read_bytes: int = 0
    write_bytes: int = 0
    bytes_recv: int = 0
    bytes_sent: int = 0
    timestamp: float = 0.0


@dataclass
class MetricsSnapshot:
    sampled_at: float = 0.0
    cpu_percent: int = 0
    ram_percent: int = 0
    cpu_temp: int | None = None
    gpu_percent: int | None = None
    gpu_temp: int | None = None
    disk_read: float = 0.0
    disk_write: float = 0.0
    net_in: float = 0.0
    net_out: float = 0.0


class RuntimeMetricsSampler:
    def __init__(
        self,
        *,
        cpu_percent_fn,
        ram_percent_fn,
        cpu_temp_fn,
        gpu_metrics_fn,
        net_io_fn,
        disk_io_fn,
        sample_interval: float = 1.0,
    ):
        self.cpu_percent_fn = cpu_percent_fn
        self.ram_percent_fn = ram_percent_fn
        self.cpu_temp_fn = cpu_temp_fn
        self.gpu_metrics_fn = gpu_metrics_fn
        self.net_io_fn = net_io_fn
        self.disk_io_fn = disk_io_fn
        self.sample_interval = max(0.1, float(sample_interval))
        self.lock = threading.RLock()
        self.previous_counters: CounterSnapshot | None = None
        self.snapshot = MetricsSnapshot()

    def _capture_counters(self, now: float) -> CounterSnapshot:
        net = self.net_io_fn()
        disk = self.disk_io_fn()
        return CounterSnapshot(
            read_bytes=int(getattr(disk, "read_bytes", 0) or 0),
            write_bytes=int(getattr(disk, "write_bytes", 0) or 0),
            bytes_recv=int(getattr(net, "bytes_recv", 0) or 0),
            bytes_sent=int(getattr(net, "bytes_sent", 0) or 0),
            timestamp=now,
        )

    def warmup(self, now: float | None = None):
        now = time.time() if now is None else float(now)
        with self.lock:
            self.cpu_percent_fn()
            self.previous_counters = self._capture_counters(now)
            self.snapshot = MetricsSnapshot(sampled_at=now)

    def _compute_rate(self, current_value: int, previous_value: int, dt: float) -> float:
        if dt <= 0:
            return 0.0
        return max((float(current_value) - float(previous_value)) / dt, 0.0)

    def maybe_refresh(self, now: float | None = None) -> MetricsSnapshot:
        now = time.time() if now is None else float(now)
        with self.lock:
            if self.previous_counters is None:
                self.warmup(now)
                return self.snapshot
            if self.snapshot.sampled_at and now - self.snapshot.sampled_at < self.sample_interval:
                return self.snapshot

            current_counters = self._capture_counters(now)
            previous_counters = self.previous_counters
            dt = max(now - previous_counters.timestamp, 0.0)
            cpu_percent = self.cpu_percent_fn()
            ram_percent = self.ram_percent_fn()
            cpu_temp = self.cpu_temp_fn()
            gpu_percent, gpu_temp = self.gpu_metrics_fn()
            self.snapshot = MetricsSnapshot(
                sampled_at=now,
                cpu_percent=int(cpu_percent),
                ram_percent=int(ram_percent),
                cpu_temp=cpu_temp,
                gpu_percent=gpu_percent,
                gpu_temp=gpu_temp,
                disk_read=self._compute_rate(current_counters.read_bytes, previous_counters.read_bytes, dt),
                disk_write=self._compute_rate(current_counters.write_bytes, previous_counters.write_bytes, dt),
                net_in=self._compute_rate(current_counters.bytes_recv, previous_counters.bytes_recv, dt),
                net_out=self._compute_rate(current_counters.bytes_sent, previous_counters.bytes_sent, dt),
            )
            self.previous_counters = current_counters
            return self.snapshot

    def get_snapshot(self) -> MetricsSnapshot:
        with self.lock:
            return MetricsSnapshot(**self.snapshot.__dict__)
