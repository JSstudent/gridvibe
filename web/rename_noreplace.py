"""Atomic exclusive rename for POSIX entries that cannot be hard-linked."""

import ctypes
import errno
import os
import sys


def rename_noreplace(source, destination):
    library = ctypes.CDLL(None, use_errno=True)
    old, new = os.fsencode(source), os.fsencode(destination)
    if sys.platform.startswith('linux') and hasattr(library, 'renameat2'):
        rename = library.renameat2
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(-100, old, -100, new, 1)  # AT_FDCWD, RENAME_NOREPLACE
    elif sys.platform == 'darwin' and hasattr(library, 'renamex_np'):
        rename = library.renamex_np
        rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        result = rename(old, new, 4)  # RENAME_EXCL
    else:
        raise OSError(errno.ENOTSUP, 'This system cannot safely rename without replacement', destination)
    if result != 0:
        error = ctypes.get_errno()
        if error in {errno.ENOSYS, errno.EINVAL, errno.ENOTSUP}:
            raise OSError(errno.ENOTSUP, 'This filesystem cannot safely rename without replacement', destination)
        raise OSError(error, os.strerror(error), destination)
