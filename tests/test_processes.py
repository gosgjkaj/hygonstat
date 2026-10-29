import ctypes as ct
import unittest
from unittest.mock import Mock, patch

import psutil

from hygonstat import processes as p


def set_value(pointer, value):
    pointer._obj.value = value


class FakeSMI:
    def __init__(self, process_ids=()):
        self.pids = list(process_ids)
        self.rsmi_init = Mock(return_value=0)
        self.rsmi_shut_down = Mock(return_value=0)
        self.rsmi_num_monitor_devices = Mock(side_effect=self.count)
        self.rsmi_dev_pci_id_get = Mock(side_effect=self.pci)
        self.rsmi_compute_process_info_get = Mock(side_effect=self.processes)
        self.rsmi_compute_process_gpus_get = Mock(side_effect=self.gpus)
        self.rsmi_compute_process_info_by_device_get = Mock(side_effect=self.memory)

    def count(self, pointer):
        set_value(pointer, 2)
        return 0

    def pci(self, index, pointer):
        set_value(pointer, [0x3600, 0x900][index])  # Deliberately reversed SMI order.
        return 0

    def processes(self, array, count):
        set_value(count, len(self.pids))
        if array is not None:
            for i, pid in enumerate(self.pids):
                array[i].process_id = pid
                array[i].vram_usage = 9999999999  # Aggregate must never be used.
        return 0

    def gpus(self, pid, array, count):
        set_value(count, 2)
        if array is not None:
            array[0], array[1] = 0, 1
        return 0

    def memory(self, pid, index, pointer):
        pointer._obj.vram_usage = [256, 128][index] * 1024**2
        return 0


class ProcessTests(unittest.TestCase):
    def entries(self):
        return [{"index": 0, "pci.bus_id": "0000:09:00.0"},
                {"index": 1, "pci.bus_id": "0000:36:00.0"}]

    def query(self, lib, entries=None):
        entries = entries if entries is not None else self.entries()
        with patch.object(p, "_load_library", return_value=lib), patch.object(
            p, "_process_identity", side_effect=lambda pid: {
                "pid": pid, "username": "tester", "command": "python"}):
            warnings = p.collect_processes(entries)
        return entries, warnings

    def test_pci_mapping_and_multigpu_memory(self):
        entries, warnings = self.query(FakeSMI([123]))
        self.assertEqual(warnings, [])
        self.assertEqual(entries[0]["processes"][0]["gpu_memory_usage"], 128)
        self.assertEqual(entries[1]["processes"][0]["gpu_memory_usage"], 256)

    def test_empty_success_means_empty_lists(self):
        lib = FakeSMI()
        entries, warnings = self.query(lib)
        self.assertEqual([e["processes"] for e in entries], [[], []])
        self.assertEqual(warnings, [])
        lib.rsmi_shut_down.assert_called_once()

    def test_permission_denied_is_unknown_not_empty(self):
        lib = FakeSMI()
        lib.rsmi_compute_process_info_get.side_effect = None
        lib.rsmi_compute_process_info_get.return_value = 4
        entries, warnings = self.query(lib)
        self.assertTrue(all(e["processes"] is None for e in entries))
        self.assertIn("permission denied", warnings[0])
        lib.rsmi_shut_down.assert_called_once()

    def test_failed_mapping_is_not_guessed_by_index(self):
        entries = self.entries()
        entries[0]["pci.bus_id"] = "0000:ff:00.0"
        entries, warnings = self.query(FakeSMI(), entries)
        self.assertIsNone(entries[0]["processes"])
        self.assertEqual(entries[1]["processes"], [])
        self.assertTrue(warnings)

    def test_missing_per_device_memory_keeps_pid_with_unknown_memory(self):
        lib = FakeSMI([123])
        del lib.rsmi_compute_process_info_by_device_get
        entries, warnings = self.query(lib)
        self.assertEqual(entries[0]["processes"][0]["pid"], 123)
        self.assertIsNone(entries[0]["processes"][0]["gpu_memory_usage"])
        self.assertTrue(warnings)

    def test_duplicate_native_pci_address_is_not_accepted(self):
        lib = FakeSMI()

        def duplicate_pci(index, pointer):
            set_value(pointer, 0x900)
            return 0

        lib.rsmi_dev_pci_id_get.side_effect = duplicate_pci
        entries, warnings = self.query(lib)
        self.assertTrue(all(e["processes"] is None for e in entries))
        self.assertTrue(warnings)

    def test_mapping_failure_invalidates_partial_process_sample(self):
        lib = FakeSMI([123])
        lib.rsmi_compute_process_gpus_get.side_effect = None
        lib.rsmi_compute_process_gpus_get.return_value = 4
        with patch.object(p.psutil, "pid_exists", return_value=True):
            entries, warnings = self.query(lib)
        self.assertTrue(all(e["processes"] is None for e in entries))
        self.assertTrue(warnings)

    def test_buffer_growth_retries(self):
        calls = 0

        def changing(array, pointer):
            nonlocal calls
            calls += 1
            if array is None:
                set_value(pointer, 1 if calls == 1 else 2)
                return 0
            if calls == 2:
                set_value(pointer, 2)
                return 11
            array[0], array[1] = 6, 7
            set_value(pointer, 2)
            return 0

        self.assertEqual(p._read_array(changing, ct.c_uint32), [6, 7])

    def test_exited_process_is_ignored(self):
        with patch.object(p, "_load_library", return_value=FakeSMI([123])), \
                patch.object(p, "_process_identity", return_value=None):
            entries = self.entries()
            self.assertEqual(p.collect_processes(entries), [])
        self.assertEqual(entries[0]["processes"], [])

    def test_process_identity_access_denied_is_visible(self):
        process = Mock()
        process.username.side_effect = psutil.AccessDenied(123)
        process.name.side_effect = psutil.AccessDenied(123)
        with patch.object(p.psutil, "Process", return_value=process):
            identity = p._process_identity(123)
        self.assertEqual(identity, {"pid": 123, "username": "?", "command": "?"})

    def test_native_process_abi(self):
        self.assertEqual(ct.sizeof(p._ProcessInfo), 32)
        self.assertEqual(p._ProcessInfo.vram_usage.offset, 8)

    def test_process_constructor_access_denied_preserves_pid(self):
        with patch.object(p.psutil, "Process", side_effect=psutil.AccessDenied(123)):
            self.assertEqual(p._process_identity(123),
                             {"pid": 123, "username": "?", "command": "?"})


if __name__ == "__main__":
    unittest.main()
