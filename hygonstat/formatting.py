"""Align gpustat's rendered columns without changing its data or colors."""

import re

from wcwidth import wcswidth

from .backend import ANSI


GPU_ROW = re.compile(r"^\[\d+\]\s")
NUMBER = r"(?:\?\?|\d+(?:\.\d*)?(?:[eE][+-]?\d+)?)"
# These are the status layouts emitted by gpustat 1.1.x for our CLI flags.
# Capture each field's existing padding as well as its number, so alignment
# only adds spaces and retains every original character and precision.
STATUS_FIELDS = re.compile(
    rf"(?P<temperature> *{NUMBER})°C, "
    rf"(?P<utilization> *{NUMBER}) %"
    rf"(?:,  (?P<power_draw> *{NUMBER})"
    rf"(?: / (?P<power_limit> *{NUMBER}))? W)? *"
)
MEMORY_FIELDS = re.compile(
    rf" (?P<memory_used> *{NUMBER}) / (?P<memory_total> *{NUMBER}) MB *"
)


def _pad_fields(text, match, widths):
    """Insert padding at visible field starts without rewriting ANSI colors."""
    if match is None:
        return text
    # Regex spans refer to the text without ANSI sequences. Map those character
    # offsets back to the untouched colored string before inserting spaces.
    offsets = []
    cursor = 0
    for escape in ANSI.finditer(text):
        offsets.extend(range(cursor, escape.start()))
        cursor = escape.end()
    offsets.extend(range(cursor, len(text)))
    insertions = [(offsets[match.start(name)], widths[name] - len(value))
                  for name, value in match.groupdict().items() if value is not None]
    for offset, padding in sorted(insertions, reverse=True):
        text = text[:offset] + " " * padding + text[offset:]
    return text


class ColumnAligner:
    """Pad status columns, retaining their largest widths across watch frames."""

    def __init__(self):
        self._widths = [0, 0, 0]
        self._field_widths = {}

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
            matches = (STATUS_FIELDS.fullmatch(plain[1]),
                       MEMORY_FIELDS.fullmatch(plain[2].rstrip("\r\n")))
            for match in matches:
                if match is not None:
                    for name, value in match.groupdict().items():
                        if value is not None:
                            self._field_widths[name] = max(
                                self._field_widths.get(name, 0), len(value))
            rows.append((line, parts, matches))

        # First align the numeric subfields, then measure the outer columns.
        # All widths must be known before emitting any row of the frame.
        padded_rows = []
        for line, parts, matches in rows:
            if parts is None:
                padded_rows.append((line, None, None))
                continue
            for index, match in enumerate(matches, start=1):
                parts[index] = _pad_fields(parts[index], match, self._field_widths)
            widths = [wcswidth(ANSI.sub("", part)) for part in parts[:separators]]
            for index, width in enumerate(widths):
                self._widths[index] = max(self._widths[index], width)
            padded_rows.append((line, parts, widths))

        output = []
        for line, parts, widths in padded_rows:
            if parts is None:
                output.append(line)
                continue
            padded = [part + " " * (self._widths[index] - widths[index])
                      for index, part in enumerate(parts[:separators])]
            output.append("|".join(padded + parts[separators:]))
        return "".join(output)
