from __future__ import annotations

from types import SimpleNamespace
import unittest

from metrics_engine import RuntimeMetricsSampler


class FakeCounters:
    def __init__(self, items):
        self.items = list(items)
        self.index = 0

    def __call__(self):
        item = self.items[min(self.index, len(self.items) - 1)]
        self.index += 1
        return item


class RuntimeMetricsSamplerTests(unittest.TestCase):
    def build_sampler(self):
        cpu_values = iter([0, 17, 23])
        ram_values = iter([42, 43])
        cpu_temp_values = iter([61, 62])
        gpu_values = iter([(55, 67), (57, 68)])
        net_provider = FakeCounters([
            SimpleNamespace(bytes_recv=1000, bytes_sent=2000),
            SimpleNamespace(bytes_recv=1600, bytes_sent=2600),
            SimpleNamespace(bytes_recv=2200, bytes_sent=3200),
        ])
        disk_provider = FakeCounters([
            SimpleNamespace(read_bytes=5000, write_bytes=7000),
            SimpleNamespace(read_bytes=6200, write_bytes=8200),
            SimpleNamespace(read_bytes=7400, write_bytes=9400),
        ])
        sampler = RuntimeMetricsSampler(
            cpu_percent_fn=lambda: next(cpu_values),
            ram_percent_fn=lambda: next(ram_values),
            cpu_temp_fn=lambda: next(cpu_temp_values),
            gpu_metrics_fn=lambda: next(gpu_values),
            net_io_fn=net_provider,
            disk_io_fn=disk_provider,
            sample_interval=1.0,
        )
        return sampler

    def test_sampler_computes_rates_from_actual_elapsed_time(self):
        sampler = self.build_sampler()
        sampler.warmup(now=10.0)
        snapshot = sampler.maybe_refresh(now=11.0)

        self.assertEqual(snapshot.cpu_percent, 17)
        self.assertEqual(snapshot.ram_percent, 42)
        self.assertEqual(snapshot.cpu_temp, 61)
        self.assertEqual(snapshot.gpu_percent, 55)
        self.assertEqual(snapshot.gpu_temp, 67)
        self.assertEqual(snapshot.net_in, 600.0)
        self.assertEqual(snapshot.net_out, 600.0)
        self.assertEqual(snapshot.disk_read, 1200.0)
        self.assertEqual(snapshot.disk_write, 1200.0)

    def test_sampler_keeps_last_snapshot_until_next_sampling_window(self):
        sampler = self.build_sampler()
        sampler.warmup(now=10.0)
        first = sampler.maybe_refresh(now=11.0)
        second = sampler.maybe_refresh(now=11.4)

        self.assertEqual(second.sampled_at, first.sampled_at)
        self.assertEqual(second.cpu_percent, first.cpu_percent)
        self.assertEqual(second.net_in, first.net_in)


if __name__ == "__main__":
    unittest.main()
