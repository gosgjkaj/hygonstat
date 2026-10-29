"""Read-only DTK SMI adapter. Never invoke device configuration commands."""

import glob
import math
import os
import re
import shutil
import subprocess


class QueryError(RuntimeError):
    """The device snapshot could not be obtained."""


# The installed C++ SMI emits malformed JSON for --showpower and --showfan.
# Its text output has explicit device indices, labels, and units.
QUERY_ARGS = (
    "--showproductname", "--showbus", "--showtemp", "--showuse",
    "--showmeminfo", "vram", "--showpower", "--showmaxpower",
    "--showdriverversion",
)
DEVICE_LINE = re.compile(r"^(?:HCU|GPU)\[(\d+)\]\s*:\s*([^:]+):\s*(.*?)\s*$")
PCI = re.compile(r"\b([0-9a-fA-F]{4}:[0-9a-fA-F]{2}:[0-9a-fA-F]{2}\.[0-7])\b")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


def resolve_smi(explicit=None):
    """Find a driver-provided CLI; prefer the active installation's wrapper."""
    override = explicit or os.environ.get("HYGONSTAT_SMI")
    if override:
        path = shutil.which(os.path.expanduser(override))
        if path:
            return path
        raise QueryError("SMI executable not found: " + override)
    candidates = [shutil.which("hy-smi"), shutil.which("rocm-smi")]
    candidates += ["/opt/dtk/bin/rocm-smi", "/opt/rocm/bin/rocm-smi"]
    # Numeric version sorting, not lexicographic sorting (26.10 > 26.9).
    version_key = lambda p: tuple(int(n) for n in re.findall(r"\d+", p))
    candidates += sorted(glob.glob("/opt/dtk-*/bin/rocm-smi"),
                         key=version_key, reverse=True)
    candidates += ["/opt/hyhal/bin/hy-smi", "/usr/local/hyhal/bin/hy-smi"]
    for path in candidates:
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    raise QueryError("No Hygon SMI found. Set HYGONSTAT_SMI=/path/to/rocm-smi "
                     "or pass --smi /path/to/rocm-smi.")


def number(value):
    """Preserve unknown values, never convert an unsupported metric to zero."""
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result < 0:
        return None
    return int(result) if result.is_integer() else result


def memory_mib(fields, label):
    for unit, divisor in (("MiB", 1), ("B", 1024 ** 2), ("bytes", 1024 ** 2)):
        for prefix in ("vram", "VRAM"):
            value = number(fields.get(f"{prefix} {label} ({unit})"))
            if value is not None:
                return int(value / divisor)
    return None


def parse_smi(output):
    """Return entries, driver version, and warnings from a single snapshot."""
    cards = {}
    driver = None
    warnings = []
    for line in ANSI.sub("", output).splitlines():
        line = line.strip()
        match = DEVICE_LINE.match(line)
        if match:
            index, key, value = match.groups()
            cards.setdefault(int(index), {})[key.strip()] = value.strip()
        elif line.startswith("Driver Version:"):
            driver = line.partition(":")[2].strip()
        elif re.search(r"\b(?:error|warning)\b", line, re.IGNORECASE):
            warnings.append(line)
    if not cards:
        raise QueryError("SMI returned no device records. Check the driver and --smi path.")
    entries = []
    for index, fields in sorted(cards.items()):
        match = PCI.search(fields.get("PCI Bus", ""))
        pci = match.group(1).lower() if match else None
        name = fields.get("Card Series") or fields.get("Card Model") or "DCU"
        util = number(fields.get("HCU use (%)", fields.get("GPU use (%)")))
        if util is not None and util > 100:
            util = None
        entry = {
            "index": index, "name": "Hygon " + name,
            "uuid": None, "pci.bus_id": pci,
            "temperature.gpu": number(fields.get("Temperature (Sensor edge) (C)")),
            "temperature.sensor": "edge",
            "fan.speed": None, "utilization.gpu": util,
            "utilization.enc": None, "utilization.dec": None,
            "power.draw": number(fields.get("Average Graphics Package Power (W)")),
            "enforced.power.limit": number(fields.get("Max Graphics Package Power (W)")),
            "memory.used": memory_mib(fields, "Total Used Memory"),
            "memory.total": memory_mib(fields, "Total Memory"),
            "processes": None,
        }
        for key in ("utilization.gpu", "memory.used", "memory.total"):
            if entry[key] is None:
                warnings.append(f"GPU {index}: {key} unavailable")
        if not pci:
            warnings.append(f"GPU {index}: PCI address unavailable; process mapping may be unavailable")
        entries.append(entry)
    return entries, driver, warnings


def query_entries(smi=None, timeout=10):
    executable = resolve_smi(smi)
    try:
        result = subprocess.run(
            [executable, *QUERY_ARGS], capture_output=True, text=True,
            errors="replace", timeout=timeout, check=False,
            env={**os.environ, "LC_ALL": "C"},
        )
    except subprocess.TimeoutExpired as exc:
        raise QueryError(f"SMI timed out after {timeout:g} seconds") from exc
    except OSError as exc:
        raise QueryError(f"Cannot run {executable}: {exc}") from exc
    if result.returncode:
        detail = ANSI.sub("", result.stderr or result.stdout).strip()[:1000]
        raise QueryError(f"SMI exited with status {result.returncode}: {detail}")
    entries, driver, warnings = parse_smi(result.stdout)
    if result.stderr.strip():
        warnings.append(ANSI.sub("", result.stderr).strip()[:1000])
    return entries, driver, warnings, executable


def new_query(smi=None, id=None, processes=True, timeout=10):
    from gpustat import GPUStat, GPUStatCollection

    entries, driver, warnings, executable = query_entries(smi, timeout)
    if id is not None:
        wanted = set(id)
        missing = wanted - {entry["index"] for entry in entries}
        if missing:
            raise QueryError("Unknown GPU indices: " + ",".join(map(str, sorted(missing))))
        entries = [entry for entry in entries if entry["index"] in wanted]
    if processes:
        from .processes import collect_processes
        warnings.extend(collect_processes(entries))
    result = GPUStatCollection([GPUStat(entry) for entry in entries], driver_version=driver)
    result.warnings = warnings
    result.smi_path = executable
    return result
