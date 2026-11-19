"""Align gpustat's rendered columns without changing its data or colors."""

import re

from wcwidth import wcswidth

from .backend import ANSI


GPU_ROW = re.compile(r"^\[\d+\]\s")


class ColumnAligner:
    """Pad status columns, retaining their largest widths across watch frames."""

    def __init__(self):
        self._widths = [0, 0, 0]

    def align(self, text, *, no_processes=False):
        # The suffix after the final status separator is process text. Never
        # split it further: a process command may itself contain a pipe.
        separators = 2 if no_processes else 3
        rows = []
        for line in text.splitlines(keepends=True):
            parts = line.split("|", separators)
            plain = [ANSI.sub("", part) for part in parts]
            if (len(parts) != separators + 1
                    or not GPU_ROW.match(plain[0])
                    or "°C" not in plain[1]
                    or not plain[2].strip().endswith(" MB")):
                rows.append((line, None, None))
                continue
            widths = [wcswidth(part) for part in plain[:separators]]
            if any(width < 0 for width in widths):
                # Unsupported control characters have no reliable cell width.
                rows.append((line, None, None))
                continue
            for index, width in enumerate(widths):
                self._widths[index] = max(self._widths[index], width)
            rows.append((line, parts, widths))

        output = []
        for line, parts, widths in rows:
            if parts is None:
                output.append(line)
                continue
            padded = [part + " " * (self._widths[index] - widths[index])
                      for index, part in enumerate(parts[:separators])]
            output.append("|".join(padded + parts[separators:]))
        return "".join(output)
