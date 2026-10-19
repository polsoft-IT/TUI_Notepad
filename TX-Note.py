#!/usr/bin/env python3
"""TXNote v1.2: a compact curses text editor."""

from __future__ import annotations

import curses
import os
import re
import sys
from pathlib import Path
from typing import Optional


_CP_EDITOR = 1
_CP_TITLE = 2
_CP_STATUS = 3
_CP_CURSOR = 4
_CP_LINE_NUM = 5
_CP_MATCH = 6
_CP_MENU = 7
_CP_MENU_KEY = 8


def _init_colors() -> None:
    if not curses.has_colors():
        return
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(_CP_EDITOR, curses.COLOR_WHITE, curses.COLOR_BLACK)
    curses.init_pair(_CP_TITLE, curses.COLOR_CYAN, curses.COLOR_BLACK)
    curses.init_pair(_CP_STATUS, curses.COLOR_BLACK, curses.COLOR_CYAN)
    curses.init_pair(_CP_CURSOR, curses.COLOR_BLACK, curses.COLOR_WHITE)
    curses.init_pair(_CP_LINE_NUM, curses.COLOR_BLUE, curses.COLOR_BLACK)
    curses.init_pair(_CP_MATCH, curses.COLOR_BLACK, curses.COLOR_YELLOW)
    curses.init_pair(_CP_MENU, curses.COLOR_WHITE, curses.COLOR_BLUE)
    curses.init_pair(_CP_MENU_KEY, curses.COLOR_YELLOW, curses.COLOR_BLUE)


def _clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    return text[:width]


def _safe_addstr(win, row: int, column: int, text: str, attr: int = 0) -> None:
    """Avoid curses ERR when text reaches the terminal's lower-right cell."""
    try:
        max_width = max(0, win.getmaxyx()[1] - column - 1)
        win.addnstr(row, column, text, max_width, attr)
    except curses.error:
        pass


def _prompt(stdscr, title: str, prompt: str, default: str = "") -> Optional[str]:
    height, width = stdscr.getmaxyx()
    if height < 3 or width < 4:
        return None
    dialog_height = min(5, height)
    dialog_width = min(max(12, len(prompt) + len(default) + 8), width)
    dialog_width = max(4, dialog_width)
    dialog_y = max(0, (height - dialog_height) // 2)
    dialog_x = max(0, (width - dialog_width) // 2)
    win = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
    win.bkgd(" ", curses.color_pair(_CP_STATUS))
    win.border()
    if dialog_width > 8:
        win.addstr(0, max(1, (dialog_width - len(title) - 2) // 2),
                   _clip(f" {title} ", dialog_width - 2),
                   curses.color_pair(_CP_MENU_KEY) | curses.A_BOLD)
    if dialog_height > 2:
        win.addstr(1, min(2, dialog_width - 1),
                   _clip(prompt, max(0, dialog_width - 4)))

    value = list(default)
    input_row = min(2, dialog_height - 1)
    input_column = min(2, max(0, dialog_width - 1))
    input_width = max(0, dialog_width - input_column - 1)
    curses.curs_set(1)
    try:
        while True:
            win.addstr(2, 2, " " * max(1, dialog_width - 4))
            win.addstr(2, 2, "".join(value)[:max(1, dialog_width - 4)])
            win.move(2, min(dialog_width - 2, 2 + len(value)))
            win.refresh()
            key = win.getch()
            if key in (10, 13, curses.KEY_ENTER):
                return "".join(value)
            if key == 27:
                return None
            if key in (curses.KEY_BACKSPACE, 8, 127):
                if value:
                    value.pop()
            elif 32 <= key <= 126 and len(value) < input_width:
                value.append(chr(key))
    finally:
        curses.curs_set(0)


class Notepad:
    def __init__(self, stdscr, path: Optional[Path] = None) -> None:
        self.stdscr = stdscr
        self.path = path.resolve() if path else None
        self.lines = [""]
        self.row = 0
        self.column = 0
        self.top = 0
        self.left = 0
        self.modified = False
        self.running = True
        self.message = "Ready"
        self.search_term = ""
        self.show_line_numbers = True
        self.undo_stack = []
        self.redo_stack = []
        self.menu_items = [
            ("Save", "save"),
            ("Save as", "save_as"),
            ("Open", "open"),
            ("Find", "find"),
            ("Replace", "replace"),
            ("Go to line", "goto"),
            ("Undo", "undo"),
            ("Redo", "redo"),
            ("Toggle line numbers", "lines"),
            ("Help", "help"),
            ("About", "about"),
            ("Quit", "quit"),
        ]
        if self.path:
            self._load(self.path)

    @property
    def title(self) -> str:
        name = str(self.path) if self.path else "Untitled"
        return f" TXNote v1.2 | {name}{' *' if self.modified else ''} "

    def _load(self, path: Path) -> bool:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
            self.lines = text.splitlines()
            if not self.lines:
                self.lines = [""]
            self.path = path.resolve()
            self.row = self.column = self.top = self.left = 0
            self.modified = False
            self.undo_stack.clear()
            self.redo_stack.clear()
            self.message = f"Opened {path.name}"
            return True
        except OSError as exc:
            self.message = f"Open error: {exc}"
            return False

    def _save(self, path: Optional[Path] = None) -> bool:
        target = path or self.path
        if target is None:
            value = _prompt(self.stdscr, "Save as", "File path:")
            if not value:
                return False
            target = Path(value).expanduser()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("\n".join(self.lines) + "\n", encoding="utf-8")
            self.path = target.resolve()
            self.modified = False
            self.message = f"Saved {target.name}"
            return True
        except OSError as exc:
            self.message = f"Save error: {exc}"
            return False

    def _confirm(self, title: str, text: str) -> bool:
        height, width = self.stdscr.getmaxyx()
        dialog_width = min(max(36, len(text) + 6), width - 2)
        win = curses.newwin(5, dialog_width, max(0, (height - 5) // 2),
                            max(0, (width - dialog_width) // 2))
        win.bkgd(" ", curses.color_pair(_CP_STATUS))
        win.border()
        win.addstr(0, max(1, (dialog_width - len(title) - 2) // 2),
                   f" {title} ", curses.color_pair(_CP_MENU_KEY) | curses.A_BOLD)
        win.addstr(1, 2, _clip(text, dialog_width - 4))
        win.addstr(3, 2, "[Y] Yes    [N] No", curses.A_BOLD)
        win.refresh()
        while True:
            key = win.getch()
            if key in (ord("y"), ord("Y")):
                return True
            if key in (ord("n"), ord("N"), 27):
                return False

    def _ensure_cursor_visible(self) -> None:
        height, width = self.stdscr.getmaxyx()
        text_height = max(1, height - 3)
        text_width = max(1, width - 8)
        if self.row < self.top:
            self.top = self.row
        elif self.row >= self.top + text_height:
            self.top = self.row - text_height + 1
        if self.column < self.left:
            self.left = self.column
        elif self.column >= self.left + text_width:
            self.left = self.column - text_width + 1

    def _insert(self, text: str) -> None:
        self._record_edit()
        line = self.lines[self.row]
        self.lines[self.row] = line[:self.column] + text + line[self.column:]
        self.column += len(text)
        self.modified = True

    def _backspace(self) -> None:
        if self.column:
            self._record_edit()
            line = self.lines[self.row]
            self.lines[self.row] = line[:self.column - 1] + line[self.column:]
            self.column -= 1
        elif self.row:
            self._record_edit()
            previous = len(self.lines[self.row - 1])
            self.lines[self.row - 1] += self.lines.pop(self.row)
            self.row -= 1
            self.column = previous
        else:
            return
        self.modified = True

    def _delete(self) -> None:
        line = self.lines[self.row]
        if self.column < len(line):
            self._record_edit()
            self.lines[self.row] = line[:self.column] + line[self.column + 1:]
        elif self.row + 1 < len(self.lines):
            self._record_edit()
            self.lines[self.row] += self.lines.pop(self.row + 1)
        else:
            return
        self.modified = True

    def _newline(self) -> None:
        self._record_edit()
        line = self.lines[self.row]
        before = line[:self.column]
        after = line[self.column:]
        indentation = before[:len(before) - len(before.lstrip(" \t"))]
        self.lines[self.row] = before
        self.lines.insert(self.row + 1, indentation + after)
        self.row += 1
        self.column = len(indentation)
        self.modified = True

    def _duplicate_line(self) -> None:
        self._record_edit()
        self.lines.insert(self.row + 1, self.lines[self.row])
        self.row += 1
        self.column = min(self.column, len(self.lines[self.row]))
        self.modified = True
        self.message = "Line duplicated"

    def _snapshot(self):
        return (self.lines.copy(), self.row, self.column, self.modified)

    def _restore(self, snapshot) -> None:
        self.lines, self.row, self.column, self.modified = snapshot
        self.row = min(self.row, len(self.lines) - 1)
        self.column = min(self.column, len(self.lines[self.row]))
        self._ensure_cursor_visible()

    def _record_edit(self) -> None:
        self.undo_stack.append(self._snapshot())
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)
        self.redo_stack.clear()

    def _undo(self) -> None:
        if not self.undo_stack:
            self.message = "Nothing to undo"
            return
        self.redo_stack.append(self._snapshot())
        self._restore(self.undo_stack.pop())
        self.message = "Undo"

    def _redo(self) -> None:
        if not self.redo_stack:
            self.message = "Nothing to redo"
            return
        self.undo_stack.append(self._snapshot())
        self._restore(self.redo_stack.pop())
        self.message = "Redo"

    def _goto_line(self) -> None:
        value = _prompt(self.stdscr, "Go to line", "Line number:", str(self.row + 1))
        if not value:
            return
        try:
            line_number = int(value)
        except ValueError:
            self.message = "Invalid line number"
            return
        if not 1 <= line_number <= len(self.lines):
            self.message = f"Line must be 1-{len(self.lines)}"
            return
        self.row = line_number - 1
        self.column = min(self.column, len(self.lines[self.row]))
        self.message = f"Line {line_number}"

    def _find(self) -> None:
        value = _prompt(self.stdscr, "Find", "Search:", self.search_term)
        if value is None:
            return
        self.search_term = value
        if not value:
            self.message = "Search cleared"
            return
        current_line = self.lines[self.row].lower()
        search_value = value.lower()
        column = current_line.find(search_value, self.column)
        if column >= 0:
            self.column = column
            self.message = f"Found: {value}"
            return
        start = self.row + 1
        for index in list(range(start, len(self.lines))) + list(range(0, self.row + 1)):
            column = self.lines[index].lower().find(search_value)
            if column >= 0:
                self.row, self.column = index, column
                self.message = f"Found: {value}"
                return
        self.message = f"Not found: {value}"

    def _replace(self) -> None:
        value = _prompt(self.stdscr, "Replace", "Find:", self.search_term)
        if value is None or not value:
            return
        replacement = _prompt(self.stdscr, "Replace", "Replace with:")
        if replacement is None:
            return
        updated = []
        total = 0
        for line in self.lines:
            replaced, count = re.subn(re.escape(value), lambda _: replacement, line,
                                      flags=re.IGNORECASE)
            updated.append(replaced)
            total += count
        if total:
            self._record_edit()
            self.lines = updated
            self.modified = True
            self.search_term = value
            self.message = f"Replaced {total} occurrence(s)"
        else:
            self.message = f"Not found: {value}"

    def draw(self) -> None:
        height, width = self.stdscr.getmaxyx()
        self.stdscr.erase()
        title_attr = curses.color_pair(_CP_TITLE) | curses.A_BOLD
        self.stdscr.addstr(0, 0, " " * width, title_attr)
        self.stdscr.addstr(0, 0, _clip(self.title, width), title_attr)
        text_height = max(1, height - 3)
        number_width = max(4, len(str(len(self.lines))) + 1) if self.show_line_numbers else 0
        text_width = max(1, width - number_width - 1)
        for screen_row in range(text_height):
            index = self.top + screen_row
            if index >= len(self.lines):
                break
            if self.show_line_numbers:
                number = f"{index + 1:>{number_width - 1}} "
                self.stdscr.addstr(screen_row + 1, 0, number,
                                   curses.color_pair(_CP_LINE_NUM))
            visible = self.lines[index][self.left:self.left + text_width]
            self.stdscr.addstr(screen_row + 1, number_width, visible,
                               curses.color_pair(_CP_EDITOR))
            if self.search_term and self.search_term.lower() in visible.lower():
                match = visible.lower().find(self.search_term.lower())
                self.stdscr.addstr(screen_row + 1, number_width + match,
                                   _clip(self.search_term, text_width - match),
                                   curses.color_pair(_CP_MATCH))
        status = f" Ln {self.row + 1}, Col {self.column + 1} | {len(self.lines)} lines | {self.message} "
        _safe_addstr(self.stdscr, height - 2, 0,
                 _clip(status.ljust(width), width),
                 curses.color_pair(_CP_STATUS))
        if width >= 160:
            menu = (" F1 Menu | Ctrl+S Save | Ctrl+O Open | Ctrl+F Find | Ctrl+R Replace | "
                    "Ctrl+G GoTo | Ctrl+L Lines | Ctrl+D Duplicate | Ctrl+Z Undo | "
                    "Ctrl+Y Redo | Ctrl+Q Quit ")
        elif width >= 112:
            menu = (" F1 Menu | Ctrl+S Save | Ctrl+O Open | Ctrl+F Find | Ctrl+R Replace | "
                    "Ctrl+Z Undo | Ctrl+Y Redo | Ctrl+Q Quit ")
        elif width >= 86:
            menu = " F1 Menu | Ctrl+S Save | Ctrl+O Open | Ctrl+F Find | Ctrl+Z Undo | Ctrl+Q Quit "
        else:
            menu = " F1 Menu | S Save | Z Undo | Q Quit "
        _safe_addstr(self.stdscr, height - 1, 0,
                 _clip(menu.ljust(width), width),
                 curses.color_pair(_CP_MENU))
        if width > 0:
            cursor_x = number_width + self.column - self.left
            cursor_y = 1 + self.row - self.top
            if 1 <= cursor_y < height - 2 and number_width <= cursor_x < width:
                self.stdscr.move(cursor_y, cursor_x)
        self.stdscr.refresh()

    def _open(self) -> None:
        value = _prompt(self.stdscr, "Open", "File path:", str(self.path) if self.path else "")
        if value:
            self._load(Path(value).expanduser())

    def _save_as(self) -> None:
        value = _prompt(self.stdscr, "Save as", "File path:", str(self.path) if self.path else "")
        if value:
            self._save(Path(value).expanduser())

    def _show_text_dialog(self, title: str, lines) -> None:
        height, width = self.stdscr.getmaxyx()
        if height < 3 or width < 4:
            self.stdscr.erase()
            _safe_addstr(self.stdscr, 0, 0, _clip(title, max(0, width - 1)),
                         curses.color_pair(_CP_STATUS))
            self.stdscr.refresh()
            self.stdscr.getch()
            return
        max_width = max(4, width - 2)
        max_height = max(3, height - 2)
        requested_width = max([len(title) + 6] + [len(line) + 4 for line in lines])
        dialog_width = min(max(4, requested_width), max_width)
        dialog_height = min(max(3, len(lines) + 4), max_height)
        dialog_y = max(0, min((height - dialog_height) // 2, height - dialog_height))
        dialog_x = max(0, min((width - dialog_width) // 2, width - dialog_width))
        win = curses.newwin(dialog_height, dialog_width, dialog_y, dialog_x)
        win.bkgd(" ", curses.color_pair(_CP_STATUS))
        win.border()
        win.addstr(0, max(1, min(dialog_width - len(title) - 2,
                                  (dialog_width - len(title) - 2) // 2)),
                   _clip(f" {title} ", max(0, dialog_width - 2)),
                   curses.color_pair(_CP_MENU_KEY) | curses.A_BOLD)
        for index, line in enumerate(lines[:max(0, dialog_height - 3)]):
            _safe_addstr(win, index + 1, 2, _clip(line, max(0, dialog_width - 4)),
                         curses.color_pair(_CP_STATUS))
        _safe_addstr(win, dialog_height - 2, 2,
                     _clip("Enter / Esc = close", max(0, dialog_width - 4)), curses.A_BOLD)
        win.refresh()
        while win.getch() not in (27, 10, 13, curses.KEY_ENTER, curses.KEY_F1):
            pass

    def _show_help(self) -> None:
        self._show_text_dialog("Help", [
            "F1       open menu",
            "Ctrl+S   save       Ctrl+O open",
            "Ctrl+F   find       Ctrl+R replace",
            "Ctrl+G   go to line Ctrl+D duplicate",
            "Ctrl+Z   undo       Ctrl+Y redo",
            "Ctrl+L   line numbers on/off",
            "Arrows   move       Home/End line edges",
            "PgUp/PgDn scroll    Tab insert spaces",
            "Ctrl+Q   quit",
        ])

    def _show_about(self) -> None:
        self._show_text_dialog("About", [
            "TXNote v1.2",
            "A compact curses text editor",
            "UTF-8 files | Undo/Redo | Search",
            "Built for TerminalX Navigator",
            "",
            "author:  Sebastian Januchowski",
            "company: polsoft.ITS™",
            "email:   polsoft.its@mail.com",
            "github:  https://github.com/polsoft-it",
        ])

    def _run_menu_action(self, action: str) -> None:
        actions = {
            "save": self._save,
            "save_as": self._save_as,
            "open": self._open,
            "find": self._find,
            "replace": self._replace,
            "goto": self._goto_line,
            "undo": self._undo,
            "redo": self._redo,
            "help": self._show_help,
            "about": self._show_about,
        }
        if action in actions:
            actions[action]()
        elif action == "lines":
            self.show_line_numbers = not self.show_line_numbers
            self.message = "Line numbers on" if self.show_line_numbers else "Line numbers off"
        elif action == "quit":
            if not self.modified or self._confirm("Quit", "Discard unsaved changes?"):
                self.running = False

    def _show_menu(self) -> None:
        height, width = self.stdscr.getmaxyx()
        menu_width = min(34, max(22, width - 4))
        menu_height = min(len(self.menu_items) + 2, max(4, height - 2))
        win = curses.newwin(menu_height, menu_width,
                            max(0, (height - menu_height) // 2),
                            max(0, (width - menu_width) // 2))
        win.bkgd(" ", curses.color_pair(_CP_MENU))
        win.keypad(True)
        selected = 0
        while True:
            win.erase()
            win.border()
            win.addstr(0, 2, " Menu ", curses.color_pair(_CP_MENU_KEY) | curses.A_BOLD)
            visible = menu_height - 2
            for index, (label, _) in enumerate(self.menu_items[:visible]):
                attr = curses.color_pair(_CP_CURSOR) if index == selected else curses.color_pair(_CP_MENU)
                win.addstr(index + 1, 2, f"{label:<{menu_width - 4}}", attr)
            win.refresh()
            key = win.getch()
            if key == curses.KEY_UP:
                selected = (selected - 1) % min(len(self.menu_items), visible)
            elif key == curses.KEY_DOWN:
                selected = (selected + 1) % min(len(self.menu_items), visible)
            elif key in (10, 13, curses.KEY_ENTER):
                self._run_menu_action(self.menu_items[selected][1])
                return
            elif key in (27, curses.KEY_F1):
                return

    def _handle_command(self, key: int) -> bool:
        commands = {
            curses.KEY_F1: self._show_menu,
            19: self._save,  # Ctrl+S
            15: self._open,  # Ctrl+O
            6: self._find,  # Ctrl+F
            18: self._replace,  # Ctrl+R
            7: self._goto_line,  # Ctrl+G
            4: self._duplicate_line,  # Ctrl+D
            26: self._undo,  # Ctrl+Z
            25: self._redo,  # Ctrl+Y
        }
        if key in commands:
            commands[key]()
            return True
        if key == 12:  # Ctrl+L
            self.show_line_numbers = not self.show_line_numbers
            self.message = "Line numbers on" if self.show_line_numbers else "Line numbers off"
            return True
        if key == 17:  # Ctrl+Q
            if not self.modified or self._confirm("Quit", "Discard unsaved changes?"):
                self.running = False
            return True
        return False
    def _handle_navigation(self, key: int) -> bool:
        if key == curses.KEY_LEFT:
            self.column = max(0, self.column - 1)
        elif key == curses.KEY_RIGHT:
            self.column = min(len(self.lines[self.row]), self.column + 1)
        elif key == curses.KEY_UP:
            self.row = max(0, self.row - 1)
            self.column = min(self.column, len(self.lines[self.row]))
        elif key == curses.KEY_DOWN:
            self.row = min(len(self.lines) - 1, self.row + 1)
            self.column = min(self.column, len(self.lines[self.row]))
        elif key == curses.KEY_HOME:
            self.column = 0
        elif key == curses.KEY_END:
            self.column = len(self.lines[self.row])
        elif key == curses.KEY_PPAGE:
            self.row = max(0, self.row - max(1, self.stdscr.getmaxyx()[0] - 3))
            self.column = min(self.column, len(self.lines[self.row]))
        elif key == curses.KEY_NPAGE:
            self.row = min(len(self.lines) - 1, self.row + max(1, self.stdscr.getmaxyx()[0] - 3))
            self.column = min(self.column, len(self.lines[self.row]))
        else:
            return False
        return True
    def _handle_editing(self, key: int) -> bool:
        if key in (curses.KEY_BACKSPACE, 8, 127):
            self._backspace()
        elif key == curses.KEY_DC:
            self._delete()
        elif key in (10, 13, curses.KEY_ENTER):
            self._newline()
        elif key == 9:
            self._insert("    ")
        elif 32 <= key <= 126:
            self._insert(chr(key))
        else:
            return False
        return True
    def handle(self, key: int) -> None:
        if not self._handle_command(key):
            self._handle_navigation(key)
            self._handle_editing(key)
        self._ensure_cursor_visible()

    def run(self) -> None:
        curses.curs_set(1)
        self.stdscr.keypad(True)
        _init_colors()
        while self.running:
            self.draw()
            self.handle(self.stdscr.getch())


def main() -> int:
    path = Path(sys.argv[1]).expanduser() if len(sys.argv) > 1 else None
    if path and not path.exists():
        path = path.resolve()
    try:
        curses.wrapper(lambda stdscr: Notepad(stdscr, path).run())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
