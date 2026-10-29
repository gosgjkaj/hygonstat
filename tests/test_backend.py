"""Regression checks for real DTK output and the read-only query boundary."""

from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

from hygonstat import backend


FIXTURE = Path(__file__).parent / "fixtures" / "sugon-smi.txt"


class ParseSMITests(unittest.TestCase):
    def test_sugon_eight_cards_preserve_smi_indices_and_pci_addresses(self):
        entries, driver, warnings = backend.parse_smi(FIXTURE.read_text())
        self.assertEqual(driver, "6.3.32-V1.6.0")
        self.assertEqual(warnings, [])
        self.assertEqual([card["index"] for card in entries], list(range(8)))
        self.assertEqual(
            [card["pci.bus_id"] for card in entries],
            ["0000:09:00.0", "0000:36:00.0", "0000:55:00.0", "0000:77:00.0",
             "0000:85:00.0", "0000:b5:00.0", "0000:d5:00.0", "0000:f5:00.0"],
        )
        self.assertEqual([card["temperature.gpu"] for card in entries],
                         [53, 50, 52, 52, 53, 54, 52, 51])
        self.assertEqual([card["power.draw"] for card in entries],
                         [89, 94, 90, 107, 92, 92, 93, 86])
        for card in entries:
            self.assertEqual(card["name"], "Hygon BW")
            self.assertEqual(card["memory.total"], 65520)
            self.assertEqual(card["memory.used"], 2)
            self.assertEqual(card["utilization.gpu"], 0)
            # SMI serial numbers repeat across cards: they are not UUIDs.
            self.assertIsNone(card["uuid"])
            self.assertIsNone(card["processes"])

    def test_memory_units_are_binary_and_byte_values_are_not_reported_as_mib(self):
        cases = [("vram", "MiB", "64", "1.5"),
                 ("VRAM", "B", "67108864", "1572864"),
                 ("vram", "bytes", "67108864", "1572864")]
        for prefix, unit, total, used in cases:
            with self.subTest(unit=unit):
                output = (f"GPU[12]: {prefix} Total Memory ({unit}): {total}\n"
                          f"GPU[12]: {prefix} Total Used Memory ({unit}): {used}\n")
                entries, _, _ = backend.parse_smi(output)
                self.assertEqual(entries[0]["index"], 12)
                self.assertEqual(entries[0]["memory.total"], 64)
                self.assertEqual(entries[0]["memory.used"], 1)

    def test_sparse_out_of_order_indices_and_pci_suffixes(self):
        entries, _, _ = backend.parse_smi(
            "\x1b[31mHCU[12]: PCI Bus: 0001:AF:02.3 --> SN: duplicate\x1b[0m\n"
            "HCU[2]: PCI Bus: 0000:05:00.0 --> SN: duplicate\n"
            "HCU[12]: HCU use (%): 75\n"
        )
        self.assertEqual([card["index"] for card in entries], [2, 12])
        self.assertEqual(entries[1]["pci.bus_id"], "0001:af:02.3")
        self.assertEqual(entries[1]["utilization.gpu"], 75)

    def test_missing_and_invalid_metrics_remain_unknown(self):
        for invalid in ("N/A", "not supported", "-1", "nan", "inf", ""):
            with self.subTest(invalid=invalid):
                entries, _, warnings = backend.parse_smi(
                    f"HCU[0]: HCU use (%): {invalid}\n"
                    f"HCU[0]: vram Total Used Memory (MiB): {invalid}\n"
                    f"HCU[0]: Temperature (Sensor edge) (C): {invalid}\n"
                    "HCU[0]: PCI Bus: unavailable\n"
                )
                card = entries[0]
                for key in ("utilization.gpu", "memory.used", "memory.total",
                            "temperature.gpu", "pci.bus_id"):
                    self.assertIsNone(card[key], key)
                self.assertTrue(any("utilization.gpu unavailable" in w for w in warnings))
                self.assertTrue(any("PCI address unavailable" in w for w in warnings))

    def test_zero_is_a_known_value_but_impossible_utilization_is_not(self):
        entries, _, _ = backend.parse_smi(
            "HCU[0]: HCU use (%): 0\n"
            "HCU[0]: vram Total Used Memory (B): 0\n"
            "HCU[3]: HCU use (%): 101\n"
        )
        self.assertEqual(entries[0]["utilization.gpu"], 0)
        self.assertEqual(entries[0]["memory.used"], 0)
        self.assertIsNone(entries[1]["utilization.gpu"])

    def test_no_device_output_is_an_error(self):
        for output in ("", "Driver Version: 1\n", "ERROR: no devices found\n"):
            with self.subTest(output=output):
                with self.assertRaises(backend.QueryError):
                    backend.parse_smi(output)


class QueryBoundaryTests(unittest.TestCase):
    def test_query_uses_one_read_only_snapshot_and_preserves_stderr_warning(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=FIXTURE.read_text(),
            stderr="\x1b[33mWarning: partial permissions\x1b[0m\n",
        )
        with patch.object(backend, "resolve_smi", return_value="/dtk/rocm-smi"), \
                patch.object(backend.subprocess, "run", return_value=completed) as run:
            entries, _, warnings, executable = backend.query_entries(timeout=3.5)
        self.assertEqual(len(entries), 8)
        self.assertEqual(executable, "/dtk/rocm-smi")
        self.assertEqual(warnings, ["Warning: partial permissions"])
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[0], "/dtk/rocm-smi")
        self.assertTrue(all(arg.startswith("--show") or arg == "vram"
                            for arg in command[1:]))
        self.assertEqual(run.call_args.kwargs["timeout"], 3.5)
        self.assertEqual(run.call_args.kwargs["env"]["LC_ALL"], "C")
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_timeout_and_launch_failure_become_actionable_query_errors(self):
        cases = [(subprocess.TimeoutExpired("rocm-smi", 2), "timed out"),
                 (PermissionError("permission denied"), "Cannot run")]
        for error, message in cases:
            with self.subTest(error=type(error).__name__), \
                    patch.object(backend, "resolve_smi", return_value="/dtk/rocm-smi"), \
                    patch.object(backend.subprocess, "run", side_effect=error):
                with self.assertRaisesRegex(backend.QueryError, message):
                    backend.query_entries(timeout=2)

    def test_nonzero_exit_is_not_misrepresented_as_valid_partial_output(self):
        completed = subprocess.CompletedProcess(
            args=[], returncode=7, stdout=FIXTURE.read_text(), stderr="driver unavailable",
        )
        with patch.object(backend, "resolve_smi", return_value="/dtk/rocm-smi"), \
                patch.object(backend.subprocess, "run", return_value=completed):
            with self.assertRaisesRegex(backend.QueryError, "status 7: driver unavailable"):
                backend.query_entries()

    def test_invalid_explicit_path_does_not_silently_select_a_different_driver(self):
        with patch.object(backend.shutil, "which", return_value=None):
            with self.assertRaisesRegex(backend.QueryError, "SMI executable not found"):
                backend.resolve_smi("/missing/rocm-smi")

    def test_device_selection_preserves_original_indices_and_pci_mapping(self):
        # Only the formatter boundary is stubbed; selection uses parsed real data.
        class Collection:
            def __init__(self, gpus, driver_version=None):
                self.gpus = gpus
                self.driver_version = driver_version

        gpustat = types.SimpleNamespace(GPUStat=lambda entry: entry,
                                        GPUStatCollection=Collection)
        snapshot = (*backend.parse_smi(FIXTURE.read_text()), "/dtk/rocm-smi")
        with patch.dict(sys.modules, {"gpustat": gpustat}), \
                patch.object(backend, "query_entries", return_value=snapshot):
            selected = backend.new_query(id=[7, 2, 7], processes=False)
            self.assertEqual([card["index"] for card in selected.gpus], [2, 7])
            self.assertEqual([card["pci.bus_id"] for card in selected.gpus],
                             ["0000:55:00.0", "0000:f5:00.0"])
            self.assertTrue(all(card["processes"] is None for card in selected.gpus))
            with self.assertRaisesRegex(backend.QueryError, "Unknown GPU indices: 8"):
                backend.new_query(id=[0, 8], processes=False)


if __name__ == "__main__":
    unittest.main()
