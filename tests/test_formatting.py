"""Check displayed column positions using the real gpustat renderer."""

from copy import deepcopy
from io import StringIO
import os
import re
import unittest
from unittest.mock import patch

from gpustat import GPUStat, GPUStatCollection
from wcwidth import wcswidth

from hygonstat.backend import ANSI
from hygonstat.formatting import ColumnAligner


def collection():
    devices = []
    for index, utilization in enumerate((0, 7.5, 68.8, 100, None)):
        devices.append(GPUStat({
            "index": 10 if index == 4 else index,
            "name": "Hygon 海光" if index == 4 else "Hygon BW",
            "temperature.gpu": 53.5 if index == 1 else 56,
            "fan.speed": None,
            "utilization.gpu": utilization,
            "utilization.enc": None,
            "utilization.dec": None,
            "power.draw": 1234.5 if index == 1 else 89,
            "enforced.power.limit": 1500 if index == 1 else 1000,
            "memory.used": 100000 if index == 2 else 2,
            "memory.total": 131072 if index == 2 else 65520,
            "processes": ([{"username": "user", "command": "worker|child",
                            "pid": 42, "gpu_memory_usage": 100000}]
                          if index == 2 else []),
        }))
    return GPUStatCollection(devices, driver_version="test-driver")


def render(stats, *, color=False, no_processes=False, show_power="draw,limit"):
    output = StringIO()
    with patch.dict(os.environ, {"TERM": "xterm-256color"}):
        stats.print_formatted(fp=output, force_color=color, no_color=not color,
                              show_header=False, show_user=True, show_cmd=True,
                              show_pid=True, show_power=show_power,
                              no_processes=no_processes)
    return output.getvalue()


def separator_positions(line, count):
    plain = ANSI.sub("", line)
    offsets = [index for index, char in enumerate(plain) if char == "|"][:count]
    return tuple(wcswidth(plain[:offset]) for offset in offsets)


def metric_layout(line):
    """Read displayed values, right edges and punctuation, excluding processes."""
    plain = ANSI.sub("", line)
    parts = plain.split("|", 3)
    prefix = len(parts[0]) + 1
    status = "|".join(parts[1:3])
    numbers = list(re.finditer(r"\?\?|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?", status))
    marks = list(re.finditer(r"°C|%|,|/|\bW\b|\bMB\b", status))
    return (
        tuple(match.group() for match in numbers),
        tuple(wcswidth(plain[:prefix + match.end()]) for match in numbers),
        tuple((match.group(), wcswidth(plain[:prefix + match.start()]))
              for match in marks),
    )


class FormattingTests(unittest.TestCase):
    def test_numeric_right_edges_and_units_align_without_reformatting_values(self):
        stats = collection()
        stats.gpus[0].entry["power.draw"] = 0
        stats.gpus[3].entry["enforced.power.limit"] = 10000
        for field in ("temperature.gpu", "power.draw", "enforced.power.limit",
                      "memory.used", "memory.total"):
            stats.gpus[-1].entry[field] = None
        original_data = deepcopy(stats.jsonify())
        for color in (False, True):
            with self.subTest(color=color):
                before = render(stats, color=color)
                after = ColumnAligner().align(before)
                original = [metric_layout(line) for line in before.splitlines()]
                aligned = [metric_layout(line) for line in after.splitlines()]
                self.assertEqual([layout[0] for layout in original],
                                 [layout[0] for layout in aligned])
                self.assertEqual(len({layout[1] for layout in aligned}), 1)
                self.assertEqual(len({layout[2] for layout in aligned}), 1)
                self.assertTrue(all(len(layout[1]) == 6 for layout in aligned))
                self.assertEqual(before.replace(" ", ""), after.replace(" ", ""))
        self.assertEqual(stats.jsonify(), original_data)

    def test_numeric_alignment_supports_optional_power_and_process_columns(self):
        for show_power in (None, "draw", "limit", "draw,limit"):
            for no_processes in (False, True):
                with self.subTest(show_power=show_power, no_processes=no_processes):
                    before = render(collection(), show_power=show_power,
                                    no_processes=no_processes)
                    after = ColumnAligner().align(before, no_processes=no_processes)
                    original = [metric_layout(line) for line in before.splitlines()]
                    aligned = [metric_layout(line) for line in after.splitlines()]
                    self.assertEqual([layout[0] for layout in original],
                                     [layout[0] for layout in aligned])
                    self.assertEqual(len({layout[1] for layout in aligned}), 1)
                    self.assertEqual(len({layout[2] for layout in aligned}), 1)

    def test_numeric_columns_do_not_shrink_when_wide_values_disappear(self):
        wide_gpu = deepcopy(collection().gpus[0])
        wide_gpu.entry.update({
            "temperature.gpu": 100.125, "utilization.gpu": 99.99,
            "power.draw": 1234.567, "enforced.power.limit": 10000,
            "memory.used": 100000, "memory.total": 131072,
        })
        stats = GPUStatCollection([wide_gpu], driver_version="test")
        aligner = ColumnAligner()
        wide = aligner.align(render(stats, color=True))
        narrow_stats = GPUStatCollection([collection().gpus[0]], driver_version="test")
        narrow = aligner.align(render(narrow_stats, color=True))
        first_wide = metric_layout(wide.splitlines()[0])
        first_narrow = metric_layout(narrow.splitlines()[0])
        self.assertNotEqual(first_narrow[0], first_wide[0])
        self.assertEqual(first_narrow[1:], first_wide[1:])

    def test_fractional_unknown_and_wide_values_align_without_changing_data(self):
        stats = collection()
        original_data = deepcopy(stats.jsonify())
        before = render(stats)
        self.assertGreater(len({separator_positions(line, 3)
                                for line in before.splitlines()}), 1)
        after = ColumnAligner().align(before)
        positions = {separator_positions(line, 3) for line in after.splitlines()}
        self.assertEqual(len(positions), 1)
        self.assertEqual(len(next(iter(positions))), 3)
        self.assertEqual(stats.jsonify(), original_data)
        for value in ("7.5 %", "68.8 %", "100 %", "?? %", "53.5°C",
                      "1234.5", "100000", "131072", "[10]", "海光"):
            self.assertIn(value, after)
        # Padding must not remove or alter any existing display characters.
        self.assertEqual(before.replace(" ", ""), after.replace(" ", ""))

    def test_color_sequences_do_not_change_visible_column_positions(self):
        stats = collection()
        plain = ColumnAligner().align(render(stats))
        colored = ColumnAligner().align(render(stats, color=True))
        self.assertIn("\033[", colored)
        self.assertEqual(ANSI.sub("", colored), plain)

    def test_process_text_including_pipes_is_preserved_exactly(self):
        before = render(collection(), color=True)
        after = ColumnAligner().align(before)
        for original, aligned in zip(before.splitlines(), after.splitlines()):
            self.assertEqual(original.split("|", 3)[3], aligned.split("|", 3)[3])
        self.assertIn("worker|child", ANSI.sub("", after))

    def test_no_processes_aligns_two_separators_without_adding_a_third(self):
        before = render(collection(), no_processes=True)
        after = ColumnAligner().align(before, no_processes=True)
        positions = {separator_positions(line, 2) for line in after.splitlines()}
        self.assertEqual(len(positions), 1)
        self.assertTrue(all(line.count("|") == 2 for line in after.splitlines()))

    def test_headers_warnings_and_non_gpu_rows_are_unchanged(self):
        header = "host | time | driver\n"
        warning = "hygonstat: unavailable | retry\n"
        not_gpu = "[4] diagnostic | not a metric | not memory | keep\n"
        aligner = ColumnAligner()
        gpu_rows = render(collection())
        result = aligner.align(header + gpu_rows + warning + not_gpu)
        self.assertTrue(result.startswith(header))
        self.assertTrue(result.endswith(warning + not_gpu))
        self.assertEqual(aligner.align("(No GPUs are available)\n"),
                         "(No GPUs are available)\n")

    def test_columns_keep_their_width_after_values_or_devices_disappear(self):
        stats = collection()
        aligner = ColumnAligner()
        wide = aligner.align(render(stats))
        narrow_stats = GPUStatCollection([stats.gpus[0]], driver_version="test")
        narrow = aligner.align(render(narrow_stats))
        self.assertEqual(separator_positions(narrow.splitlines()[0], 3),
                         separator_positions(wide.splitlines()[0], 3))
        # Starting narrow must still expand to fit a later wider frame.
        growing = ColumnAligner()
        first = growing.align(render(narrow_stats))
        expanded = growing.align(render(stats))
        self.assertNotEqual(separator_positions(first.splitlines()[0], 3),
                            separator_positions(expanded.splitlines()[0], 3))
        self.assertEqual(expanded, wide)

    def test_missing_final_newline_and_crlf_are_preserved(self):
        before = render(collection()).replace("\n", "\r\n").rstrip("\r\n")
        after = ColumnAligner().align(before)
        self.assertEqual(after.count("\r\n"), before.count("\r\n"))
        self.assertFalse(after.endswith("\n"))


if __name__ == "__main__":
    unittest.main()
