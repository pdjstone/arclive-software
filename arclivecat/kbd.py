"""A markdown-it plugin that gives <kbd>KEY</kbd> the colour of that key on an
A3000 as a class: red for the function keys, dark for the grey ones, and
none for the cream ones (kbd-a3000-simple.css in Archimedes Live draws them)"""
import re

RED_KEY = re.compile(r'f([1-9]|1[0-2])$')          # F1-F12
DARK_KEYS = {
    'esc', 'escape', 'tab', 'caps lock', 'capslock', 'shift', 'left shift', 'right shift',
    'ctrl', 'left ctrl', 'right ctrl', 'alt', 'left alt', 'right alt', 'return', 'enter',
    'backspace', 'delete', 'copy', 'insert', 'home', 'page up', 'page down', 'print',
    'scroll lock', 'break', 'num lock', 'up', 'down', 'left', 'right', '↑', '↓', '←', '→',
}


def key_class(name):
    """'red', 'dark' or None (cream) for the key called name"""
    name = ' '.join(name.split()).lower()
    if RED_KEY.match(name):
        return 'red'
    if name in DARK_KEYS:
        return 'dark'
    return None


def is_tag(token, tag):
    return token.type == 'html_inline' and token.content.lower() == tag


def kbd_classes(state):
    # Inline HTML comes as separate tokens: <kbd>, the key's text, </kbd>.
    # Only a bare <kbd> is changed, so one with a class keeps it, and in a
    # combination (<kbd><kbd>Ctrl</kbd>+<kbd>X</kbd></kbd>) only the keys are
    for block in state.tokens:
        if block.type != 'inline' or not block.children:
            continue
        kids = block.children
        for i in range(len(kids) - 2):
            open_, text, close = kids[i:i + 3]
            if (is_tag(open_, '<kbd>') and text.type == 'text' and is_tag(close, '</kbd>')
                    and (cls := key_class(text.content))):
                open_.content = f'<kbd class="{cls}">'


def kbd_class_plugin(md):
    md.core.ruler.push('kbd_classes', kbd_classes)
