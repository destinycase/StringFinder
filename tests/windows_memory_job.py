"""Windows process memory limits for isolated allocation regression tests."""
import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import uuid


class _BasicLimits(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                ('flags', wintypes.DWORD), ('min_ws', ctypes.c_size_t), ('max_ws', ctypes.c_size_t),
                ('active', wintypes.DWORD), ('affinity', ctypes.c_size_t),
                ('priority', wintypes.DWORD), ('scheduling', wintypes.DWORD)]


class _IOCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in
                ('read_ops', 'write_ops', 'other_ops', 'read_bytes', 'write_bytes', 'other_bytes')]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [('basic', _BasicLimits), ('io', _IOCounters), ('process_memory', ctypes.c_size_t),
                ('job_memory', ctypes.c_size_t), ('peak_process_memory', ctypes.c_size_t),
                ('peak_job_memory', ctypes.c_size_t)]


def _kernel():
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    signatures = {
        'CreateJobObjectW': ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
        'OpenJobObjectW': ([wintypes.DWORD, wintypes.BOOL, wintypes.LPCWSTR], wintypes.HANDLE),
        'SetInformationJobObject': ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
        'AssignProcessToJobObject': ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
        'GetCurrentProcess': ([], wintypes.HANDLE),
        'CloseHandle': ([wintypes.HANDLE], wintypes.BOOL),
        'SetErrorMode': ([wintypes.UINT], wintypes.UINT),
    }
    for name, (args, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes = args
        function.restype = result
    return api


def _checked(result):
    if not result:
        raise ctypes.WinError(ctypes.get_last_error())
    return result


@contextmanager
def memory_job(megabytes):
    api = _kernel()
    name = 'SFAllocationTest_' + uuid.uuid4().hex
    handle = _checked(api.CreateJobObjectW(None, name))
    try:
        limits = _ExtendedLimits()
        limits.basic.flags = 0x100 | 0x400 | 0x2000
        limits.process_memory = megabytes * 1024**2
        _checked(api.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)))
        yield name
    finally:
        api.CloseHandle(handle)


def attach_current_process(name):
    api = _kernel()
    handle = _checked(api.OpenJobObjectW(1 | 4, False, name))
    try:
        _checked(api.AssignProcessToJobObject(handle, api.GetCurrentProcess()))
        api.SetErrorMode(2)
    finally:
        api.CloseHandle(handle)
