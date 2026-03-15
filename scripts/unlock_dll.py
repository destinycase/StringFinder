import ctypes
from ctypes import wintypes
import os
import psutil

# Restart Manager API
try:
    rstrtmgr = ctypes.windll.Rstrtmgr
    kernel32 = ctypes.windll.kernel32
except AttributeError:
    pass  # Not on Windows or dll not found

CCH_RM_MAX_APP_NAME = 255
CCH_RM_MAX_SVC_NAME = 63

class RM_UNIQUE_PROCESS(ctypes.Structure):
    _fields_ = [
        ("dwProcessId", wintypes.DWORD),
        ("ProcessStartTime", wintypes.FILETIME)
    ]

class RM_PROCESS_INFO(ctypes.Structure):
    _fields_ = [
        ("Process", RM_UNIQUE_PROCESS),
        ("strAppName", wintypes.WCHAR * (CCH_RM_MAX_APP_NAME + 1)),
        ("strServiceShortName", wintypes.WCHAR * (CCH_RM_MAX_SVC_NAME + 1)),
        ("ApplicationType", wintypes.DWORD),
        ("AppStatus", wintypes.DWORD),
        ("TSSessionId", wintypes.DWORD),
        ("bRestartable", wintypes.BOOL)
    ]

def get_locking_processes(file_path):
    if os.name != "nt":
        return []
    
    session_handle = wintypes.DWORD()
    session_key = (wintypes.WCHAR * 33)()
    
    res = rstrtmgr.RmStartSession(ctypes.byref(session_handle), 0, session_key)
    if res != 0:
        return []

    try:
        paths = (ctypes.c_wchar_p * 1)(os.path.abspath(file_path))
        res = rstrtmgr.RmRegisterResources(session_handle.value, 1, paths, 0, None, 0, None)
        if res != 0:
            return []

        n_proc_info_needed = wintypes.DWORD(0)
        n_proc_info = wintypes.DWORD(0)
        reboot_reasons = wintypes.DWORD(0)
        
        res = rstrtmgr.RmGetList(session_handle.value, ctypes.byref(n_proc_info_needed), ctypes.byref(n_proc_info), None, ctypes.byref(reboot_reasons))
        
        if res == 234: # ERROR_MORE_DATA
            n_proc_info = n_proc_info_needed
            proc_infos = (RM_PROCESS_INFO * n_proc_info.value)()
            res = rstrtmgr.RmGetList(session_handle.value, ctypes.byref(n_proc_info_needed), ctypes.byref(n_proc_info), proc_infos, ctypes.byref(reboot_reasons))
            if res == 0:
                pids = []
                for i in range(n_proc_info.value):
                    pids.append(proc_infos[i].Process.dwProcessId)
                return pids
                
        return []
    finally:
        rstrtmgr.RmEndSession(session_handle.value)

def unlock_file(file_path):
    pids = get_locking_processes(file_path)
    if not pids:
        print(f"No processes are locking {file_path}")
        return False
        
    for pid in pids:
        if pid == os.getpid():
            continue
        try:
            print(f"File is locked by PID: {pid}. Attempting to terminate...")
            process = psutil.Process(pid)
            process.terminate()
            process.wait(timeout=2.0)
            print(f"Terminated PID: {pid}")
        except Exception as e:
            print(f"Failed to terminate PID {pid}: {e}")
            try:
                # Force kill if terminate fails
                process.kill()
                print(f"Force killed PID: {pid}")
            except Exception as e2:
                print(f"Force kill also failed: {e2}")
    return True

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        unlock_file(sys.argv[1])
