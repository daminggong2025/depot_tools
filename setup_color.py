#!/usr/bin/env python3
# Copyright (c) 2016 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import ctypes
import ctypes.wintypes
import os
import platform
import re
import subprocess
import sys

IS_TTY = None
OUT_TYPE = 'unknown'


class _AnsiCodes:
    """Base class for the ANSI escape sequence constants below.

    Subclasses declare class attributes with numeric ANSI codes. On
    instantiation, each attribute is replaced by the escape sequence for
    that code, e.g. RED = 31 becomes the string ESC + '[31m'.
    """

    def __init__(self):
        for name in dir(self):
            if not name.startswith('_'):
                setattr(self, name, '\033[%dm' % getattr(self, name))


class _AnsiFore(_AnsiCodes):
    BLACK = 30
    RED = 31
    GREEN = 32
    YELLOW = 33
    BLUE = 34
    MAGENTA = 35
    CYAN = 36
    WHITE = 37
    RESET = 39


class _AnsiBack(_AnsiCodes):
    BLACK = 40
    RED = 41
    GREEN = 42
    YELLOW = 43
    BLUE = 44
    MAGENTA = 45
    CYAN = 46
    WHITE = 47
    RESET = 49


class _AnsiStyle(_AnsiCodes):
    BRIGHT = 1
    DIM = 2
    NORMAL = 22
    RESET_ALL = 0


# ANSI escape sequences for terminal colors and styles, e.g. Fore.RED
# switches the foreground color to red and Style.RESET_ALL resets all
# colors and styles back to the default.
Fore = _AnsiFore()
Back = _AnsiBack()
Style = _AnsiStyle()

# ANSI escape sequences in their CSI (colors, cursor movement) and OSC
# (window title) forms, optionally surrounded by the \x01/\x02 invisible-text
# markers used by readline.
_ANSI_CSI_RE = re.compile(r'\x01?\x1b\[(?:\d|;)*[a-zA-Z]\x02?')
_ANSI_OSC_RE = re.compile(r'\x01?\x1b\](?:.|;)*?\x07\x02?')


class StripAnsiWriter:
    """Wraps a stream and removes ANSI escape sequences from write() calls.

    All other attribute accesses go directly to the wrapped stream.
    """

    def __init__(self, wrapped):
        self._wrapped = wrapped

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

    def write(self, text):
        text = _ANSI_OSC_RE.sub('', text)
        return self._wrapped.write(_ANSI_CSI_RE.sub('', text))


def enable_native_ansi():
    """Enables native ANSI sequences in console. Windows 10 only.

    Returns whether successful.
    """
    kernel32 = ctypes.windll.kernel32
    ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x04

    out_handle = kernel32.GetStdHandle(subprocess.STD_OUTPUT_HANDLE)

    # GetConsoleMode fails if the terminal isn't native.
    mode = ctypes.wintypes.DWORD()
    if kernel32.GetConsoleMode(out_handle, ctypes.byref(mode)) == 0:
        return False

    if not (mode.value & ENABLE_VIRTUAL_TERMINAL_PROCESSING):
        if kernel32.SetConsoleMode(
                out_handle,
                mode.value | ENABLE_VIRTUAL_TERMINAL_PROCESSING) == 0:
            print('kernel32.SetConsoleMode to enable ANSI sequences failed',
                  file=sys.stderr)
            return False

    return True


def init():
    global IS_TTY, OUT_TYPE
    IS_TTY = sys.stdout.isatty()
    is_windows = sys.platform.startswith('win')
    if IS_TTY:
        # Yay! We detected a console in the normal way. It doesn't really matter
        # if it's windows or not, we win.
        OUT_TYPE = 'console'
    elif is_windows:
        # assume this is some sort of file
        OUT_TYPE = 'file (win)'

        import msvcrt
        h = msvcrt.get_osfhandle(sys.stdout.fileno())
        # h is the win32 HANDLE for stdout.
        ftype = ctypes.windll.kernel32.GetFileType(h)
        if ftype == 2:  # FILE_TYPE_CHAR
            # This is a normal cmd console, but we'll only get here if we're
            # running inside a `git command` which is actually
            # git->bash->command. Not sure why isatty doesn't detect this case.
            OUT_TYPE = 'console (cmd via msys)'
            IS_TTY = True
        elif ftype == 3:  # FILE_TYPE_PIPE
            OUT_TYPE = 'pipe (win)'

            # This is some kind of pipe on windows. This could either be a real
            # pipe or this could be msys using a pipe to emulate a pty. We use
            # the same algorithm that msys-git uses to determine if it's
            # connected to a pty or not.

            # This function and the structures are defined in the MSDN
            # documentation using the same names.
            def NT_SUCCESS(status):
                # The first two bits of status are the severity. The success
                # severities are 0 and 1, and the !success severities are 2 and
                # 3. Therefore since ctypes interprets the default restype of
                # the call to be an 'C int' (which is guaranteed to be signed 32
                # bits), All success codes are positive, and all !success codes
                # are negative.
                return status >= 0

            class UNICODE_STRING(ctypes.Structure):
                _fields_ = [('Length', ctypes.c_ushort),
                            ('MaximumLength', ctypes.c_ushort),
                            ('Buffer', ctypes.c_wchar_p)]

            class OBJECT_NAME_INFORMATION(ctypes.Structure):
                _fields_ = [('Name', UNICODE_STRING),
                            ('NameBuffer', ctypes.c_wchar_p)]

            buf = ctypes.create_string_buffer(1024)
            # Ask NT what the name of the object our stdout HANDLE is. It would
            # be possible to use GetFileInformationByHandleEx, but it's only
            # available on Vista+. If you're reading this in 2017 or later, feel
            # free to refactor this out.
            #
            # The '1' here is ObjectNameInformation
            if NT_SUCCESS(
                    ctypes.windll.ntdll.NtQueryObject(h, 1, buf,
                                                      len(buf) - 2, None)):
                out = OBJECT_NAME_INFORMATION.from_buffer(buf)
                name = out.Name.Buffer.split('\\')[-1]
                IS_TTY = name.startswith('msys-') and '-pty' in name
                if IS_TTY:
                    OUT_TYPE = 'bash (msys)'
        else:
            # A normal file, or an unknown file type.
            pass
    else:
        # This is non-windows, so we trust isatty.
        OUT_TYPE = 'pipe or file'

    # Follow the https://no-color.org convention: NO_COLOR set to any
    # non-empty value disables color, and CLICOLOR_FORCE set to a non-empty
    # value other than '0' forces color on even when stdout is not a
    # terminal. NO_COLOR wins when both are set, which matches most
    # implementations of the convention. OUT_TYPE keeps describing what
    # stdout actually is; IS_TTY is what tools use to decide about color.
    if os.environ.get('NO_COLOR'):
        IS_TTY = False
        # Strip ANSI sequences from the output, so that color is also
        # disabled for tools that emit them without checking IS_TTY.
        sys.stdout = StripAnsiWriter(sys.stdout)
        sys.stderr = StripAnsiWriter(sys.stderr)
        return
    if os.environ.get('CLICOLOR_FORCE', '0') not in ('', '0'):
        IS_TTY = True

    if IS_TTY and is_windows and platform.release() == '10':
        # Windows 10 consoles don't interpret ANSI sequences unless native
        # ANSI processing is switched on; later Windows versions enable it
        # by default (crbug.com/1114548).
        enable_native_ansi()


if __name__ == '__main__':
    init()
    print('IS_TTY:', IS_TTY)
    print('OUT_TYPE:', OUT_TYPE)
