"""Telemetry parsing/API tests with fake sensors. No GPU queries or inference."""
from types import SimpleNamespace
import subprocess
import unittest
from unittest.mock import Mock, patch

import hardware_monitor
import test_web


class HardwareTests(unittest.TestCase):
    def test_gpu_uses_read_only_query_and_converts_units(self):
        result = SimpleNamespace(stdout="NVIDIA GeForce RTX 3080, 88, 37, 8050, 10240, 78.2, 318.4\n")
        with patch("hardware_monitor.subprocess.run", return_value=result) as run, patch("hardware_monitor.shutil.which", return_value="nvidia-smi"):
            data = hardware_monitor.gpu_snapshot()
        self.assertTrue(data["available"])
        self.assertEqual(data["devices"][0]["utilization_percent"], 88)
        self.assertEqual(data["devices"][0]["memory_total_bytes"], 10 * 1024**3)
        self.assertTrue(any(arg.startswith("--query-gpu=") for arg in run.call_args.args[0]))
        self.assertNotIn("-c", run.call_args.args[0])

    def test_unavailable_gpu_values_are_not_reported_as_zero(self):
        result = SimpleNamespace(stdout="GPU, N/A, N/A, N/A, 10240, N/A, N/A\n")
        with patch("hardware_monitor.subprocess.run", return_value=result), patch("hardware_monitor.shutil.which", return_value="nvidia-smi"):
            data = hardware_monitor.gpu_snapshot()
        self.assertIsNone(data["devices"][0]["utilization_percent"])
        with patch("hardware_monitor.subprocess.run", side_effect=subprocess.TimeoutExpired("nvidia-smi", 3)), patch("hardware_monitor.shutil.which", return_value="nvidia-smi"):
            self.assertFalse(hardware_monitor.gpu_snapshot()["available"])

    def test_host_sampling_is_cached_between_refreshes(self):
        gpu = Mock(return_value={"available": False, "devices": []})
        with patch("hardware_monitor.psutil.cpu_percent", return_value=42), patch("hardware_monitor.psutil.virtual_memory", return_value=SimpleNamespace(used=8 * 1024**3, total=16 * 1024**3, percent=50)), patch("hardware_monitor.psutil.cpu_count", return_value=16), patch("hardware_monitor.psutil.process_iter", return_value=[]):
            monitor = hardware_monitor.HardwareMonitor(gpu_reader=gpu)
            first = monitor.sample()
            self.assertEqual(first["cpu_percent"], 42)
            self.assertEqual(first["ram_percent"], 50)
            self.assertIs(monitor.sample(), first)
        self.assertEqual(gpu.call_count, 1)

    def test_real_history_preserves_missing_sensor_samples(self):
        gpu = Mock(side_effect=[{"devices":[{"utilization_percent":80}]},{"devices":[]}])
        with patch("hardware_monitor.psutil.cpu_percent", return_value=42), patch("hardware_monitor.psutil.virtual_memory", return_value=SimpleNamespace(used=8 * 1024**3,total=16 * 1024**3,percent=50)), patch("hardware_monitor.psutil.process_iter", return_value=[]):
            monitor=hardware_monitor.HardwareMonitor(gpu_reader=gpu)
            monitor.sample();monitor.last_at-=3
            result=monitor.sample()
        self.assertEqual(len(result["history"]),2)
        self.assertEqual(result["history"][0]["gpu_percent"],80)
        self.assertIsNone(result["history"][1]["gpu_percent"])


class HardwareApiTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown

    def test_telemetry_is_read_only_and_does_not_load_a_model(self):
        service = self.client.app.state.service
        with patch.object(service.hardware, "sample", return_value={"cpu_percent": 12, "gpu": {"devices": []}}):
            response = self.client.get("/api/telemetry")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["cpu_percent"], 12)
        self.assertIsNone(service.embeddings)
        self.assertEqual(self.client.post("/api/telemetry", headers=self.headers, json={}).status_code, 405)


if __name__ == "__main__":
    unittest.main()
