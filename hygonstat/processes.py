"""Read-only KFD process telemetry from the active Hygon SMI library.

Process memory must come from the per-device API. The per-PID aggregate is
deliberately never used, because that would overcount multi-GPU processes.
"""

from __future__ import annotations

import ctypes as ct
import os
from pathlib import Path

import psutil


_SUCCESS = 0
_INSUFFICIENT_SIZE = 11
_MIB = 1024 * 1024
_STATUS_NAMES = {
    1: "invalid arguments", 2: "not supported", 3: "file access error",
    4: "permission denied", 8: "initialization error", 9: "not implemented",
    10: "not found", 11: "buffer changed", 14: "no data", 16: "busy",
}


class _ProcessInfo(ct.Structure):
    # Exact ABI of rsmi_process_info_t in DTK's rocm_smi.h (32 bytes on x86_64).
    _fields_ = [
        ("process_id", ct.c_uint32),
        ("pasid", ct.c_uint32),
        ("vram_usage", ct.c_uint64),
        ("sdma_usage", ct.c_uint64),
        ("cu_occupancy", ct.c_uint32),
    ]


class _SMIError(RuntimeError):
    pass


def _check(status: int, operation: str) -> None:
    if status != _SUCCESS:
        detail = _STATUS_NAMES.get(status, f"status {status}")
        raise _SMIError(f"{operation}: {detail}")


def _bind(lib, name, argtypes, *, optional=False):
    try:
        function = getattr(lib, name)
    except AttributeError:
        if optional:
            return None
        raise _SMIError(f"SMI library lacks {name}") from None
    function.argtypes = argtypes
    function.restype = ct.c_int
    return function


def _load_library():
    override = os.environ.get("HYGONSTAT_SMI_LIBRARY")
    candidates = [override] if override else [
        "/opt/hyhal/lib/librocm_smi64.so",
        "/usr/local/hyhal/lib/librocm_smi64.so",
    ]
    errors = []
    for candidate in candidates:
        if not Path(candidate).is_file():
            continue
        try:
            return ct.CDLL(candidate)
        except OSError as exc:
            errors.append(str(exc))
    detail = "; ".join(errors) or "active Hygon SMI library not found"
    raise _SMIError(detail + "; set HYGONSTAT_SMI_LIBRARY to its absolute path")


def _read_array(function, item_type, *prefix):
    """Retry the documented two-call API when the process/device count grows."""
    for _ in range(3):
        count = ct.c_uint32()
        _check(function(*prefix, None, ct.byref(count)), "query process/device count")
        if count.value == 0:
            return []
        if count.value > 100_000:
            raise _SMIError("SMI returned an implausible process/device count")
        capacity = count.value
        items = (item_type * capacity)()
        status = function(*prefix, items, ct.byref(count))
        if status == _INSUFFICIENT_SIZE or count.value > capacity:
            continue
        _check(status, "query process/device list")
        return list(items[:count.value])
    raise _SMIError("process/device list changed repeatedly during sampling")


def _pci_address(value: int) -> str:
    # rsmi_dev_pci_id_get: domain in bits 32..63; bus/device/function in 8..15/3..7/0..2.
    return (f"{value >> 32:04x}:{(value >> 8) & 0xff:02x}:"
            f"{(value >> 3) & 0x1f:02x}.{value & 7:x}")


def _process_identity(pid: int):
    result = {"pid": pid, "username": "?", "command": "?"}
    try:
        process = psutil.Process(pid)
        try:
            result["username"] = process.username()
        except psutil.AccessDenied:
            pass
        try:
            result["command"] = process.name() or "?"
        except psutil.AccessDenied:
            pass
    except psutil.NoSuchProcess:
        return None
    except psutil.AccessDenied:
        pass
    return result


def collect_processes(entries: list[dict]) -> list[str]:
    """Populate gpustat process entries, returning any visibility warnings.

    An empty list means a successful sample found no processes on that card.
    None means process visibility could not be established. Device indices
    from the native library are matched by PCI address, never by list order.
    """
    warnings = []
    for entry in entries:
        entry["processes"] = None
    if not entries:
        return warnings
    lib = None
    initialized = False
    try:
        lib = _load_library()
        init = _bind(lib, "rsmi_init", [ct.c_uint64])
        shutdown = _bind(lib, "rsmi_shut_down", [])
        device_count = _bind(lib, "rsmi_num_monitor_devices", [ct.POINTER(ct.c_uint32)])
        pci_id = _bind(lib, "rsmi_dev_pci_id_get", [ct.c_uint32, ct.POINTER(ct.c_uint64)])
        process_list = _bind(lib, "rsmi_compute_process_info_get",
                             [ct.POINTER(_ProcessInfo), ct.POINTER(ct.c_uint32)])
        process_gpus = _bind(lib, "rsmi_compute_process_gpus_get",
                             [ct.c_uint32, ct.POINTER(ct.c_uint32), ct.POINTER(ct.c_uint32)])
        process_info = _bind(lib, "rsmi_compute_process_info_by_device_get",
                             [ct.c_uint32, ct.c_uint32, ct.POINTER(_ProcessInfo)], optional=True)
        _check(init(0), "initialize process telemetry")
        initialized = True
        count = ct.c_uint32()
        _check(device_count(ct.byref(count)), "query device count")
        if count.value > 65_536:
            raise _SMIError("SMI returned an implausible device count")
        wanted = {}
        for entry in entries:
            pci = entry.get("pci.bus_id")
            if pci:
                wanted.setdefault(pci.lower(), []).append(entry)
        native_addresses = {}
        for index in range(count.value):
            address = ct.c_uint64()
            status = pci_id(index, ct.byref(address))
            if status:
                warnings.append(f"Cannot map SMI device {index} to a PCI address (status {status})")
                continue
            native_addresses.setdefault(_pci_address(address.value), []).append(index)
        by_device = {}
        for address, indices in native_addresses.items():
            matches = wanted.get(address, [])
            if len(matches) == 1 and len(indices) == 1:
                by_device[indices[0]] = matches[0]
        for index, entry in by_device.items():
            entry["processes"] = []
        if len(by_device) != len(entries):
            warnings.append("Process telemetry unavailable for cards without a unique PCI match")

        processes = _read_array(process_list, _ProcessInfo)
        for proc in processes:
            pid = proc.process_id
            identity = _process_identity(pid)
            if identity is None:
                continue  # Process exited after the native snapshot.
            try:
                indices = _read_array(process_gpus, ct.c_uint32, pid)
            except _SMIError:
                if not psutil.pid_exists(pid):
                    continue
                raise
            for index in indices:
                if index not in by_device:
                    continue
                memory = None
                if process_info is None:
                    warnings.append("Per-device process memory is unsupported by this SMI library")
                else:
                    info = _ProcessInfo()
                    status = process_info(pid, index, ct.byref(info))
                    if status == 0 and info.vram_usage != 2**64 - 1:
                        memory = info.vram_usage // _MIB
                    else:
                        detail = ("unknown value" if status == 0 else
                                  _STATUS_NAMES.get(status, f"status {status}"))
                        warnings.append(f"PID {pid} device {index} memory unavailable ({detail})")
                by_device[index]["processes"].append(
                    dict(identity, gpu_memory_usage=memory))
    except (OSError, _SMIError) as exc:
        # An unknown process-to-device mapping can affect any card; do not
        # present an incomplete sample as an empty, apparently idle device.
        for entry in entries:
            entry["processes"] = None
        warnings.append(f"Process telemetry unavailable: {exc}")
    finally:
        if initialized:
            status = shutdown()
            if status:
                warnings.append(f"SMI shutdown returned status {status}")
    return list(dict.fromkeys(warnings))
