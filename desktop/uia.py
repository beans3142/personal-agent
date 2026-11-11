"""Windows UI Automation snapshots with element ids, plus mouse and keyboard input.

Like the phone's screen snapshot, `ui` lists visible elements as e1, e2, ... and later
steps act on those ids. Ids are valid until the next snapshot.
"""
import ctypes
from ctypes import wintypes
import time

KEYS = {
    'ctrl': 0x11, 'control': 0x11, 'shift': 0x10, 'alt': 0x12, 'win': 0x5B, 'windows': 0x5B,
    'enter': 0x0D, 'return': 0x0D, 'esc': 0x1B, 'escape': 0x1B, 'tab': 0x09, 'space': 0x20, 'backspace': 0x08,
    'delete': 0x2E, 'del': 0x2E, 'insert': 0x2D, 'home': 0x24, 'end': 0x23, 'pageup': 0x21, 'pagedown': 0x22,
    'up': 0x26, 'down': 0x28, 'left': 0x25, 'right': 0x27, 'printscreen': 0x2C, 'capslock': 0x14,
    'volume_mute': 0xAD, 'volume_down': 0xAE, 'volume_up': 0xAF, 'media_next': 0xB0, 'media_prev': 0xB1,
    'media_stop': 0xB2, 'media_play_pause': 0xB3, 'hangul': 0x15, 'hanja': 0x19,
    **{f'f{n}': 0x6F + n for n in range(1, 25)},
}
MODIFIERS = {0x11, 0x10, 0x12, 0x5B}
EXTENDED = {0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B, 0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3}


def parse_keys(text):
    """'ctrl+shift+esc' -> virtual-key codes, modifiers first as written."""
    parts = [p.strip().lower() for p in (text or '').split('+')]
    if not 1 <= len(parts) <= 5 or not all(parts):
        raise ValueError('keys look like ctrl+c, alt+f4, win+r or enter')
    codes = []
    for part in parts:
        if part in KEYS:
            codes.append(KEYS[part])
        elif len(part) == 1 and part.isascii() and part.isalnum():
            codes.append(ord(part.upper()))
        else:
            raise ValueError(f'unknown key {part!r}')
    return codes


# --- SendInput ---------------------------------------------------------------------------------

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [('dx', wintypes.LONG), ('dy', wintypes.LONG), ('mouseData', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
                ('time', wintypes.DWORD), ('dwExtraInfo', ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [('wVk', wintypes.WORD), ('wScan', wintypes.WORD), ('dwFlags', wintypes.DWORD), ('time', wintypes.DWORD),
                ('dwExtraInfo', ULONG_PTR)]


class _UNION(ctypes.Union):
    _fields_ = [('mi', MOUSEINPUT), ('ki', KEYBDINPUT), ('padding', ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [('type', wintypes.DWORD), ('u', _UNION)]


def _send(*inputs):
    array = (INPUT * len(inputs))(*inputs)
    if ctypes.windll.user32.SendInput(len(inputs), array, ctypes.sizeof(INPUT)) != len(inputs):
        raise OSError('input was blocked (a window running as administrator may be in front)')


def _key(vk=0, scan=0, flags=0):
    item = INPUT(type=1)
    item.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return item


def press(keys):
    codes = parse_keys(keys) if isinstance(keys, str) else keys
    ext = lambda vk: 0x1 if vk in EXTENDED else 0
    _send(*[_key(vk, flags=ext(vk)) for vk in codes], *[_key(vk, flags=ext(vk) | 0x2) for vk in reversed(codes)])


def type_text(text):
    """Unicode typing (Korean included), independent of the keyboard layout."""
    for chunk in range(0, len(text), 200):
        items = []
        for char in text[chunk:chunk + 200]:
            if char == '\n':
                items += [_key(0x0D), _key(0x0D, flags=0x2)]
                continue
            data = char.encode('utf-16-le')  # Characters outside the BMP are two units.
            for i in range(0, len(data), 2):
                unit = int.from_bytes(data[i:i + 2], 'little')
                items += [_key(0, unit, 0x4), _key(0, unit, 0x4 | 0x2)]  # KEYEVENTF_UNICODE
        _send(*items)


def click(x, y, button='left', double=False):
    user32 = ctypes.windll.user32
    left, top = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)
    width, height = user32.GetSystemMetrics(78), user32.GetSystemMetrics(79)
    ax, ay = int((x - left) * 65535 / max(1, width - 1)), int((y - top) * 65535 / max(1, height - 1))
    move = INPUT(type=0)
    move.u.mi = MOUSEINPUT(ax, ay, 0, 0x0001 | 0x8000 | 0x4000, 0, 0)  # MOVE | ABSOLUTE | VIRTUALDESK
    down, up = {'left': (0x2, 0x4), 'right': (0x8, 0x10), 'middle': (0x20, 0x40)}[button]
    clicks = []
    for _ in range(2 if double else 1):
        for flag in (down, up):
            item = INPUT(type=0)
            item.u.mi = MOUSEINPUT(0, 0, 0, flag, 0, 0)
            clicks.append(item)
    _send(move, *clicks)


def scroll(x, y, amount):
    """Positive amount scrolls up, in wheel notches."""
    ctypes.windll.user32.SetCursorPos(int(x), int(y))
    item = INPUT(type=0)
    item.u.mi = MOUSEINPUT(0, 0, ctypes.c_uint32(int(amount) * 120).value, 0x0800, 0, 0)
    _send(item)


# --- snapshot ---------------------------------------------------------------------------------------

class Snapshot:
    """Numbered, flattened view of a UI tree; `tree` gives children(node) and info(node)."""
    def __init__(self, tree):
        self.tree, self.elements = tree, {}

    def take(self, root, limit=150, depth=12):
        self.elements, items = {}, []
        stack = [(root, 0)]
        while stack and len(items) < limit:
            node, level = stack.pop()
            try:
                info = self.tree.info(node)
            except Exception:
                continue
            if info.get('offscreen'):
                continue
            element_id = f'e{len(items) + 1}'
            self.elements[element_id] = node
            info.pop('offscreen', None)
            items.append({'id': element_id, 'depth': level, **info})
            if level < depth:  # depth = levels below the root
                try:
                    children = list(self.tree.children(node))
                except Exception:
                    children = []
                stack.extend((child, level + 1) for child in reversed(children))
        return items

    def element(self, element_id):
        if element_id not in self.elements:
            raise ValueError(f'unknown element {element_id}; take a new pc_ui snapshot')
        return self.elements[element_id]


class ComTree:
    """The real UI Automation tree through comtypes (UIAutomationCore)."""
    TYPES = {50000: 'Button', 50001: 'Calendar', 50002: 'CheckBox', 50003: 'ComboBox', 50004: 'Edit', 50005: 'Hyperlink',
             50006: 'Image', 50007: 'ListItem', 50008: 'List', 50009: 'Menu', 50010: 'MenuBar', 50011: 'MenuItem',
             50012: 'ProgressBar', 50013: 'RadioButton', 50014: 'ScrollBar', 50015: 'Slider', 50016: 'Spinner',
             50017: 'StatusBar', 50018: 'Tab', 50019: 'TabItem', 50020: 'Text', 50021: 'ToolBar', 50022: 'ToolTip',
             50023: 'Tree', 50024: 'TreeItem', 50025: 'Custom', 50026: 'Group', 50027: 'Thumb', 50028: 'DataGrid',
             50029: 'DataItem', 50030: 'Document', 50031: 'SplitButton', 50032: 'Window', 50033: 'Pane', 50034: 'Header',
             50035: 'HeaderItem', 50036: 'Table', 50037: 'TitleBar', 50038: 'Separator'}

    def __init__(self):
        import comtypes.client
        comtypes.client.GetModule('UIAutomationCore.dll')
        from comtypes.gen import UIAutomationClient as U
        self.U = U
        self.uia = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
        self.walker = self.uia.ControlViewWalker

    def root(self):
        return self.uia.GetRootElement()

    def window(self, query):
        """Top-level window whose title contains `query` (case-insensitive), else the foreground window."""
        if not query:
            return self.uia.ElementFromHandle(ctypes.windll.user32.GetForegroundWindow())
        children = self.root().FindAll(self.U.TreeScope_Children, self.uia.CreateTrueCondition())
        for index in range(children.Length):
            element = children.GetElement(index)
            if query.casefold() in (element.CurrentName or '').casefold():
                return element
        raise ValueError(f'no window titled like {query!r}; use pc_windows')

    def children(self, node):
        child = self.walker.GetFirstChildElement(node)
        while child:
            yield child
            child = self.walker.GetNextSiblingElement(child)

    def info(self, node):
        rect = node.CurrentBoundingRectangle
        item = {'name': (node.CurrentName or '')[:120], 'type': self.TYPES.get(node.CurrentControlType, str(node.CurrentControlType)),
                'rect': [rect.left, rect.top, rect.right, rect.bottom], 'offscreen': bool(node.CurrentIsOffscreen)}
        if not node.CurrentIsEnabled:
            item['enabled'] = False
        try:
            value = node.GetCurrentPattern(self.U.UIA_ValuePatternId)
            if value:
                text = value.QueryInterface(self.U.IUIAutomationValuePattern).CurrentValue
                if text:
                    item['value'] = text[:200]
        except Exception:
            pass
        return item

    def invoke(self, node):
        pattern = node.GetCurrentPattern(self.U.UIA_InvokePatternId)
        if not pattern:
            return False
        pattern.QueryInterface(self.U.IUIAutomationInvokePattern).Invoke()
        return True

    def set_value(self, node, text):
        pattern = node.GetCurrentPattern(self.U.UIA_ValuePatternId)
        if not pattern:
            return False
        pattern.QueryInterface(self.U.IUIAutomationValuePattern).SetValue(text)
        return True

    def focus(self, node):
        node.SetFocus()

    def center(self, node):
        rect = node.CurrentBoundingRectangle
        if rect.right <= rect.left or rect.bottom <= rect.top:
            raise ValueError('element has no size on screen')
        return (rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2


def windows():
    """Visible top-level windows with title, process name, position and which one is in front."""
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    front, found = user32.GetForegroundWindow(), []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def each(hwnd, _):
        if not user32.IsWindowVisible(hwnd) or user32.GetWindow(hwnd, 4):  # GW_OWNER: skip owned popups.
            return True
        length = user32.GetWindowTextLengthW(hwnd)
        if not length:
            return True
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        name = ''
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if handle:
            buffer, size = ctypes.create_unicode_buffer(520), wintypes.DWORD(520)
            if kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                name = buffer.value.rsplit('\\', 1)[-1]
            kernel32.CloseHandle(handle)
        found.append({'title': title.value[:150], 'process': name, 'rect': [rect.left, rect.top, rect.right, rect.bottom],
                      'minimized': bool(user32.IsIconic(hwnd)), 'active': hwnd == front, 'hwnd': int(hwnd)})
        return True
    user32.EnumWindows(each, 0)
    return found


def activate(hwnd):
    user32 = ctypes.windll.user32
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # Windows only lets the foreground process change focus; an Alt tap lifts that lock.
    press([0x12])
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.2)
