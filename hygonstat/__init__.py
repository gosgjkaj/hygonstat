"""Hygon DCU monitoring with gpustat's formatter."""

__version__ = "0.1.2"


def new_query(**kwargs):
    """Return a gpustat GPUStatCollection without querying NVIDIA NVML."""
    from .backend import new_query as query
    return query(**kwargs)
