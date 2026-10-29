"""Exercise user-visible CLI output, streaming, and failure semantics."""

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from io import StringIO
import json
import unittest
from unittest.mock import Mock, patch

from hygonstat import cli
from hygonstat.backend import QueryError


def snapshot(warnings=None):
    stats = Mock()
    stats.smi_path = "/opt/dtk/bin/rocm-smi"
    stats.warnings = list(warnings or [])
    stats.jsonify.return_value = {
        "hostname": "sugon", "query_time": datetime(2026, 10, 29, 12, 30),
        "gpus": [{"index": 2, "memory.used": None, "utilization.gpu": 0}],
    }
    stats.print_formatted.side_effect = lambda **kw: kw["fp"].write("GPU snapshot\n")
    return stats


class CLITests(unittest.TestCase):
    def test_json_is_machine_readable_with_units_unknowns_and_warnings(self):
        stats = snapshot(["process permissions limited"])
        stdout, stderr = StringIO(), StringIO()
        with patch.object(cli, "new_query", return_value=stats) as query, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(["--json", "--no-processes", "--id", "2,7",
                               "--timeout", "2.5", "--smi", "/custom/rocm-smi"])
        self.assertEqual(status, 0)
        data = json.loads(stdout.getvalue())
        self.assertEqual(data["backend"], "hygon")
        self.assertEqual(data["memory_unit"], "MiB")
        self.assertEqual(data["warnings"], ["process permissions limited"])
        self.assertEqual(data["query_time"], "2026-10-29T12:30:00")
        self.assertIsNone(data["gpus"][0]["memory.used"])
        self.assertEqual(data["gpus"][0]["utilization.gpu"], 0)
        self.assertEqual(stderr.getvalue(), "")
        query.assert_called_once_with(smi="/custom/rocm-smi", id=[2, 7],
                                      processes=False, timeout=2.5)
        stats.print_formatted.assert_not_called()

    def test_json_watch_emits_one_complete_object_per_line_and_stops_on_ctrl_c(self):
        stdout, stderr = StringIO(), StringIO()
        with patch.object(cli, "new_query", side_effect=[snapshot(), snapshot(), KeyboardInterrupt]), \
                patch.object(cli.time, "sleep") as sleep, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(["--json", "--watch", "0.5"])
        self.assertEqual(status, 130)
        lines = stdout.getvalue().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(json.loads(line)["backend"] == "hygon" for line in lines))
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(sleep.call_count, 2)
        self.assertTrue(all(0 <= call.args[0] <= 0.5 for call in sleep.call_args_list))

    def test_watch_does_not_add_terminal_escape_sequences_to_redirected_output(self):
        stdout, stderr = StringIO(), StringIO()
        with patch.object(cli, "new_query", side_effect=[snapshot(), KeyboardInterrupt]), \
                patch.object(cli.time, "sleep"), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(["--watch", "--no-color"])
        self.assertEqual(status, 130)
        self.assertEqual(stdout.getvalue(), "GPU snapshot\n")

    def test_repeated_warnings_are_reported_once_until_they_change(self):
        stdout, stderr = StringIO(), StringIO()
        with patch.object(cli, "new_query", side_effect=[
                snapshot(["limited"]), snapshot(["limited"]),
                snapshot(["unavailable"]), KeyboardInterrupt]), \
                patch.object(cli.time, "sleep"), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(["-i"])
        self.assertEqual(status, 130)
        self.assertEqual(stderr.getvalue().splitlines(),
                         ["hygonstat: limited", "hygonstat: unavailable"])

    def test_display_flags_reach_formatter(self):
        stats = snapshot()
        with patch.object(cli, "new_query", return_value=stats), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            status = cli.main(["-a", "--no-header", "--no-color"])
        self.assertEqual(status, 0)
        kwargs = stats.print_formatted.call_args.kwargs
        for flag in ("show_user", "show_cmd", "show_pid", "no_color"):
            self.assertTrue(kwargs[flag], flag)
        self.assertFalse(kwargs["show_header"])
        self.assertEqual(kwargs["show_power"], "draw,limit")

    def test_terminal_redraw_keeps_persistent_warnings_visible(self):
        stdout, stderr = StringIO(), StringIO()
        with patch.object(cli, "new_query", side_effect=[
                snapshot(["limited"]), snapshot(["limited"]), KeyboardInterrupt]), \
                patch.object(cli.time, "sleep"), \
                patch.object(stdout, "isatty", return_value=True), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            self.assertEqual(cli.main(["-i"]), 130)
        self.assertEqual(stdout.getvalue().count("\033[H\033[2J"), 2)
        self.assertEqual(stderr.getvalue().count("hygonstat: limited"), 2)

    def test_query_failure_has_nonzero_exit_and_no_success_payload(self):
        stdout, stderr = StringIO(), StringIO()
        with patch.object(cli, "new_query", side_effect=QueryError("SMI timed out")), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main(["--json"])
        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(stderr.getvalue(), "hygonstat: SMI timed out\n")

    def test_closed_output_pipe_exits_without_traceback(self):
        stats = snapshot()
        stats.print_formatted.side_effect = BrokenPipeError()
        with patch.object(cli, "new_query", return_value=stats), \
                redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            self.assertEqual(cli.main([]), 0)

    def test_invalid_ids_intervals_and_conflicting_colors_fail_before_query(self):
        cases = [["--id", "-1"], ["--id", "0,x"], ["--id", ""],
                 ["--watch", "0"], ["--watch", "-1"], ["--watch", "nan"],
                 ["--timeout", "inf"], ["--timeout", "0"],
                 ["--color", "--no-color"]]
        for arguments in cases:
            with self.subTest(arguments=arguments), \
                    patch.object(cli, "new_query") as query, \
                    redirect_stderr(StringIO()):
                with self.assertRaises(SystemExit) as raised:
                    cli.main(arguments)
                self.assertEqual(raised.exception.code, 2)
                query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
