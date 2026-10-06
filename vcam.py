"""The CamZoom Camera from the Python side: sending frames to it, and seeing which apps are watching.

The camera itself is a DirectShow filter (driver/, built from OBS Studio's virtual camera) that apps like
Teams load. It reads frames from a shared-memory queue that we create and write here, using the same
layout as OBS's shared-memory-queue.c.
"""

import ctypes
import os
import winreg
from ctypes import wintypes
from pathlib import Path

import cv2
import numpy as np

QUEUE_NAME = "CamZoomVideo"  # must match VIDEO_NAME in driver/obs/shared-memory-queue.c
CAMERA_NAME = "CamZoom Camera"
CAMERA_CLSID = "{1DC4143B-5CE2-400B-9489-4D95F878366D}"  # must match driver/camzoom-camera.h

FILE_MAP_READ = 0x0004
FILE_MAP_ALL_ACCESS = 0x000F001F
PAGE_READWRITE = 0x04
INVALID_HANDLE_VALUE = wintypes.HANDLE(-1)
ERROR_ALREADY_EXISTS = 183
PROCESS_DUP_HANDLE = 0x0040
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DUPLICATE_SAME_ACCESS = 0x0002
SYSTEM_EXTENDED_HANDLE_INFORMATION = 64
OBJECT_NAME_INFORMATION = 1
STATUS_INFO_LENGTH_MISMATCH = 0xC0000004

_ntdll = ctypes.WinDLL("ntdll")
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
_ntdll.NtQuerySystemInformation.argtypes = [ctypes.c_ulong, ctypes.c_void_p, ctypes.c_ulong,
                                            ctypes.POINTER(ctypes.c_ulong)]
_ntdll.NtQueryObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong,
                                 ctypes.POINTER(ctypes.c_ulong)]
_kernel32.CreateFileMappingW.restype = wintypes.HANDLE
_kernel32.CreateFileMappingW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                                         wintypes.DWORD, wintypes.LPCWSTR]
_kernel32.OpenFileMappingW.restype = wintypes.HANDLE
_kernel32.MapViewOfFile.restype = ctypes.c_void_p
_kernel32.MapViewOfFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_size_t]
_kernel32.UnmapViewOfFile.argtypes = [ctypes.c_void_p]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.GetCurrentProcess.restype = wintypes.HANDLE
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.DuplicateHandle.argtypes = [wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
                                      ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                                                 ctypes.POINTER(wintypes.DWORD)]


def driver_installed():
    """Whether the CamZoom Camera DirectShow filter is registered (64-bit)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, rf"CLSID\{CAMERA_CLSID}\InprocServer32") as key:
            return Path(winreg.QueryValue(key, None)).exists()
    except OSError:
        return False


# --- Sending frames -------------------------------------------------------------------------------

class _QueueHeader(ctypes.Structure):
    _fields_ = [("write_idx", ctypes.c_uint32), ("read_idx", ctypes.c_uint32), ("state", ctypes.c_uint32),
                ("offsets", ctypes.c_uint32 * 3), ("type", ctypes.c_uint32), ("cx", ctypes.c_uint32),
                ("cy", ctypes.c_uint32), ("interval", ctypes.c_uint64), ("reserved", ctypes.c_uint32 * 8)]


STATE_STARTING, STATE_READY, STATE_STOPPING = 1, 2, 3
FRAME_HEADER_SIZE = 32  # each frame slot starts with a timestamp, padded to 32 bytes


def _align32(n):
    return (n + 31) & ~31


class VirtualCamera:
    """Writer side of the frame queue. Frames are BGR uint8 arrays of exactly width x height."""

    def __init__(self, width, height, fps):
        self.width, self.height = width, height
        frame_size = width * height * 3 // 2  # NV12
        offsets, size = [], _align32(ctypes.sizeof(_QueueHeader))
        for _ in range(3):
            offsets.append(size)
            size = _align32(size + FRAME_HEADER_SIZE + frame_size)

        self._handle = _kernel32.CreateFileMappingW(INVALID_HANDLE_VALUE, None, PAGE_READWRITE, 0, size, QUEUE_NAME)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            _kernel32.CloseHandle(self._handle)
            raise RuntimeError("The CamZoom Camera is already being fed by another program")
        self._view = _kernel32.MapViewOfFile(self._handle, FILE_MAP_ALL_ACCESS, 0, 0, 0)
        if not self._view:
            _kernel32.CloseHandle(self._handle)
            raise ctypes.WinError(ctypes.get_last_error())

        self._header = _QueueHeader.from_address(self._view)
        self._header.state = STATE_STARTING
        self._header.offsets[:] = offsets
        self._header.cx, self._header.cy = width, height
        self._header.interval = round(10_000_000 / fps)  # 100ns units
        memory = np.frombuffer((ctypes.c_uint8 * size).from_address(self._view), np.uint8)
        y_size = width * height
        self._timestamps = [memory[o:o + 8].view(np.uint64) for o in offsets]
        self._y = [memory[o + FRAME_HEADER_SIZE:][:y_size].reshape(height, width) for o in offsets]
        self._uv = [memory[o + FRAME_HEADER_SIZE + y_size:][:frame_size - y_size].reshape(height // 2, width // 2, 2)
                    for o in offsets]

    def send(self, bgr):
        h, w = self.height, self.width
        yuv = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420)  # BT.601 limited range, like OBS
        index = (self._header.write_idx + 1) & 0xFFFFFFFF
        self._header.write_idx = index
        slot = index % 3
        self._timestamps[slot][0] = time_100ns()
        self._y[slot][:] = yuv[:h]
        self._uv[slot][..., 0] = yuv[h:h + h // 4].reshape(h // 2, w // 2)  # NV12 interleaves U and V
        self._uv[slot][..., 1] = yuv[h + h // 4:].reshape(h // 2, w // 2)
        self._header.read_idx = index
        self._header.state = STATE_READY

    def close(self):
        if self._view:
            self._header.state = STATE_STOPPING
            self._header = self._timestamps = self._y = self._uv = None
            _kernel32.UnmapViewOfFile(self._view)
            _kernel32.CloseHandle(self._handle)
            self._view = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def time_100ns():
    return int(cv2.getTickCount() * 10_000_000 // cv2.getTickFrequency())


# --- Seeing which apps are watching ---------------------------------------------------------------
#
# The camera filter keeps the frame queue open exactly while an app is streaming from it (see the
# Thread() loop in driver/obs/virtualcam-filter.cpp), so we look for other processes holding a handle
# to it.

# SYSTEM_HANDLE_TABLE_ENTRY_INFO_EX (64-bit)
_HANDLE_ENTRY = np.dtype([("object", "<u8"), ("pid", "<u8"), ("handle", "<u8"), ("access", "<u4"),
                          ("backtrace", "<u2"), ("type", "<u2"), ("attributes", "<u4"), ("reserved", "<u4")])
_handle_buffer_size = 1 << 22


def _system_handles():
    global _handle_buffer_size
    while True:
        buf = ctypes.create_string_buffer(_handle_buffer_size)
        needed = ctypes.c_ulong()
        status = _ntdll.NtQuerySystemInformation(SYSTEM_EXTENDED_HANDLE_INFORMATION, buf, _handle_buffer_size,
                                                 ctypes.byref(needed)) & 0xFFFFFFFF
        if status == STATUS_INFO_LENGTH_MISMATCH:
            _handle_buffer_size = max(_handle_buffer_size * 2, needed.value + (1 << 20))
            continue
        if status != 0:
            raise OSError(f"NtQuerySystemInformation failed: {status:#x}")
        count = int.from_bytes(buf.raw[:8], "little")
        return np.frombuffer(buf.raw, _HANDLE_ENTRY, count=count, offset=16)


def _object_name(handle):
    buf = ctypes.create_string_buffer(2048)
    if _ntdll.NtQueryObject(handle, OBJECT_NAME_INFORMATION, buf, len(buf), None) != 0:
        return None
    length = int.from_bytes(buf.raw[0:2], "little")  # UNICODE_STRING: Length, MaximumLength, Buffer
    pointer = int.from_bytes(buf.raw[8:16], "little")
    return ctypes.wstring_at(pointer, length // 2) if pointer else ""


def _process_name(pid):
    proc = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not proc:
        return f"pid {pid}"
    try:
        buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
        if _kernel32.QueryFullProcessImageNameW(proc, 0, buf, ctypes.byref(size)):
            return Path(buf.value).name
        return f"pid {pid}"
    finally:
        _kernel32.CloseHandle(proc)


def camera_users():
    """{pid: exe name} of other processes currently streaming from the CamZoom Camera."""
    mine = _kernel32.OpenFileMappingW(FILE_MAP_READ, False, QUEUE_NAME)
    if not mine:
        return {}
    try:
        handles = _system_handles()
        own_pid = os.getpid()
        own = handles[(handles["pid"] == own_pid) & (handles["handle"] == mine)]
        target = _object_name(mine)
        if not len(own) or not target:
            return {}
        # Only handles of the same object type (Section) can match; check their names one process at a time.
        candidates = handles[(handles["type"] == own[0]["type"]) & (handles["pid"] != own_pid)]
        users, current = {}, _kernel32.GetCurrentProcess()
        for pid in np.unique(candidates["pid"]):
            proc = _kernel32.OpenProcess(PROCESS_DUP_HANDLE, False, int(pid))
            if not proc:
                continue  # protected/elevated process; those don't run meeting apps
            try:
                for value in candidates["handle"][candidates["pid"] == pid]:
                    dup = wintypes.HANDLE()
                    if not _kernel32.DuplicateHandle(proc, int(value), current, ctypes.byref(dup), 0, False,
                                                     DUPLICATE_SAME_ACCESS):
                        continue
                    try:
                        name = _object_name(dup)
                    finally:
                        _kernel32.CloseHandle(dup)
                    if name == target:
                        users[int(pid)] = _process_name(int(pid))
                        break
            finally:
                _kernel32.CloseHandle(proc)
        return users
    finally:
        _kernel32.CloseHandle(mine)
