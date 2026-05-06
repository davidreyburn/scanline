import sys

# On Linux, read key events directly from /dev/input/event* (evdev).
# On Windows (dev machine), return a no-op stub.

if sys.platform != 'win32':
    import glob
    import os
    import select
    import struct

    _EV_KEY   = 0x01
    _KEY_DOWN = 1
    _KEY_HOLD = 2

    # input_event struct: timeval (2×long) + u16 type + u16 code + s32 value
    # '@' = native alignment: 16 bytes on 32-bit ARM, 24 bytes on 64-bit
    _FMT  = '@llHHi'
    _SIZE = struct.calcsize(_FMT)

    # Linux key code → Scanline action string
    _KEY_MAP = {
        1:   'dismiss',   # ESC
        16:  'quit',      # Q
        103: 'nav_up',    # UP arrow
        108: 'nav_down',  # DOWN arrow
        105: 'ch_prev',   # LEFT arrow
        106: 'ch_next',   # RIGHT arrow
        28:  'select',    # ENTER
        2:   'ch_1',
        3:   'ch_2',
        4:   'ch_3',
        5:   'ch_4',
        6:   'ch_5',
        7:   'ch_6',
        8:   'ch_7',
        9:   'ch_8',
        10:  'ch_9',
        49:  'topo_n',    # N — next palette
        48:  'topo_b',    # B — prev palette
        46:  'topo_c',    # C — cycle char mode
        31:  'topo_s',    # S — cycle speed
        25:  'topo_p',    # P — pause/play
        57:  'topo_p',    # SPACE — pause/play
    }

    class EvdevReader:
        """Read key events from all /dev/input/event* devices without a TTY."""

        def __init__(self) -> None:
            self._fds = []
            for path in sorted(glob.glob('/dev/input/event*')):
                try:
                    self._fds.append(os.open(path, os.O_RDONLY | os.O_NONBLOCK))
                except OSError:
                    pass
            print(f'[input] evdev: watching {len(self._fds)} device(s)', flush=True)

        def poll(self):
            """Return the next action string, or None if no key pressed."""
            if not self._fds:
                return None
            r, _, _ = select.select(self._fds, [], [], 0)
            for fd in r:
                try:
                    data = os.read(fd, _SIZE)
                    if len(data) == _SIZE:
                        _, _, ev_type, code, value = struct.unpack(_FMT, data)
                        if ev_type == _EV_KEY and value in (_KEY_DOWN, _KEY_HOLD):
                            action = _KEY_MAP.get(code)
                            if action:
                                return action
                except OSError:
                    pass
            return None

        def close(self) -> None:
            for fd in self._fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._fds.clear()

else:
    class EvdevReader:  # type: ignore[no-redef]
        def poll(self):
            return None

        def close(self) -> None:
            pass
