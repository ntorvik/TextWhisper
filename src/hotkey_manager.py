"""Global hotkey listener built on pynput.

Each registered hotkey fires :attr:`triggered` once per chord-press.
The mapping is hot-swappable via :meth:`update_mapping`.
"""

from __future__ import annotations

import contextlib
import logging

from pynput import keyboard
from pynput.keyboard import HotKey, Listener
from PyQt6.QtCore import QObject, pyqtSignal

log = logging.getLogger(__name__)

_MODIFIER_TOKENS = {
    "<alt>", "<alt_l>", "<alt_r>", "<alt_gr>",
    "<ctrl>", "<ctrl_l>", "<ctrl_r>",
    "<shift>", "<shift_l>", "<shift_r>",
    "<cmd>", "<cmd_l>", "<cmd_r>",
    "<win>", "<win_l>", "<win_r>",
}

_CHAR_ALIASES = {
    "<plus>": "+",
}


def _tokens(hotkey: str) -> list[str]:
    """Split a hotkey string into tokens (lowercased), aware of literal ``+``.

    A ``+`` is normally a separator. It is treated as a literal key when it is
    leading, trailing, or part of a ``++`` pair.
    """
    s = (hotkey or "").strip()
    out: list[str] = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == "<":
            j = s.find(">", i)
            if j == -1:
                break
            out.append(s[i : j + 1].lower())
            i = j + 1
        elif c == "+":
            doubled = i + 1 < n and s[i + 1] == "+"
            leading = i == 0 or (out and out[-1] == "+")
            trailing = i == n - 1
            if doubled:
                out.append("+")
                i += 2
            elif leading or trailing:
                out.append("+")
                i += 1
            else:
                i += 1
        elif c.isspace():
            i += 1
        else:
            out.append(c.lower())
            i += 1
    return out


def has_modifier(hotkey: str) -> bool:
    return any(t in _MODIFIER_TOKENS for t in _tokens(hotkey))


# Special-key tokens that produce a printable character in the focused window.
_PRINTABLE_SPECIAL_TOKENS = {"<space>", "<enter>", "<tab>"}


def chars_inserted_per_press(hotkey: str) -> int:
    """Estimate how many printable chars the focused window receives per hotkey press.

    pynput does not suppress hotkey events — pressing the chord delivers the
    keystroke to whichever app currently has focus too. For a chord like
    ``<alt>+z`` the focused app usually does nothing visible. For a single
    printable key like ``+``, ``z``, or ``<space>`` the app inserts that
    character at the caret. We need to know about that so cleanup actions
    delete those stray characters before/along with the intended action.

    Returns 0 for: any chord with a modifier, function keys, navigation keys,
    Delete/Backspace, etc. Returns 1 for: bare printable single-char tokens,
    ``<space>``, ``<enter>``, ``<tab>``, and our ``<plus>`` alias.
    """
    if has_modifier(hotkey):
        return 0
    toks = _tokens(hotkey)
    if len(toks) != 1:
        return 0
    tok = toks[0]
    if tok in _PRINTABLE_SPECIAL_TOKENS:
        return 1
    if tok in _CHAR_ALIASES:
        return 1
    if tok.startswith("<") and tok.endswith(">"):
        return 0  # <delete>, <f9>, <home>, arrows, etc. — no character inserted
    if len(tok) == 1 and tok.isprintable():
        return 1
    return 0


def normalize_hotkey(hotkey: str) -> str:
    toks = _tokens(hotkey)
    if not toks:
        return ""
    mods = sorted(t for t in toks if t in _MODIFIER_TOKENS)
    keys = [t for t in toks if t not in _MODIFIER_TOKENS]
    keys = ["<plus>" if k == "+" else k for k in keys]
    return "+".join(mods + keys)


def parse_hotkey_to_keys(hotkey: str) -> list:
    """Convert a hotkey string into pynput ``Key``/``KeyCode`` objects.

    Mirrors ``pynput.keyboard.HotKey.parse``'s convention so the resulting
    keys compare equal to what the listener delivers:

      * Modifier specials (``<alt>``, ``<ctrl>``, ``<shift>``, etc.) -> ``Key`` enum
      * Every *other* special (``<delete>``, ``<f9>``, ``<space>``, ...) ->
        ``KeyCode.from_vk(key.value.vk)`` — listener canonicalises real keypresses
        to a bare KeyCode, so storing the Key enum here would never match.
      * Single chars (``z``, ``5``, ``+``) -> ``KeyCode.from_char``.

    Also supports our ``<plus>`` alias which pynput's own parser cannot.
    """
    from pynput.keyboard import Key, KeyCode

    # Same set pynput uses internally to decide modifier vs KeyCode.
    try:
        from pynput.keyboard._base import _NORMAL_MODIFIERS  # type: ignore[attr-defined]
        modifier_set = set(_NORMAL_MODIFIERS.values())
    except (ImportError, AttributeError):
        modifier_set = {Key.alt, Key.ctrl, Key.shift, Key.cmd}

    out: list = []
    for tok in _tokens(hotkey):
        if tok in _CHAR_ALIASES:
            out.append(KeyCode.from_char(_CHAR_ALIASES[tok]))
            continue
        if tok.startswith("<") and tok.endswith(">"):
            name = tok[1:-1]
            try:
                key = Key[name]
            except KeyError as e:
                raise ValueError(f"unknown special key {tok!r}") from e
            if key in modifier_set:
                out.append(key)
            else:
                # Non-modifier specials must be reduced to a bare VK KeyCode
                # so they match listener.canonical(real_key) at runtime.
                out.append(KeyCode.from_vk(key.value.vk))
        elif len(tok) == 1:
            out.append(KeyCode.from_char(tok))
        else:
            raise ValueError(f"unrecognised hotkey token {tok!r}")
    if not out:
        raise ValueError(f"hotkey {hotkey!r} resolves to no keys")
    return out


def validate_hotkeys(toggle: str, delete: str) -> list[tuple[str, str]]:
    issues: list[tuple[str, str]] = []
    toggle_n = normalize_hotkey(toggle)
    delete_n = normalize_hotkey(delete)

    if not toggle_n:
        issues.append(("error", "Dictation hotkey is empty."))
    if not delete_n:
        issues.append(("error", "Delete-word hotkey is empty."))

    if toggle_n and delete_n and toggle_n == delete_n:
        issues.append(
            ("error", f"Both hotkeys are set to the same chord ({toggle_n}). "
                      "Pick a different one for delete-word.")
        )

    if toggle_n and not has_modifier(toggle):
        issues.append(
            ("warn", f"Dictation hotkey {toggle_n!r} has no modifier — pressing that "
                     "key in any app will toggle dictation, which is rarely what you want.")
        )
    if delete_n and not has_modifier(delete):
        issues.append(
            ("warn", f"Delete-word hotkey {delete_n!r} has no modifier — it will fire "
                     "globally and conflict with the key's normal function. "
                     "Consider <ctrl>+<backspace>.")
        )
    return issues


class HotkeyManager(QObject):
    triggered = pyqtSignal(str)  # name of the hotkey that fired
    error = pyqtSignal(str)

    def __init__(self, mapping: dict[str, str]) -> None:
        """``mapping``: ``name -> hotkey_str`` (e.g. ``{"toggle": "<alt>+z"}``)."""
        super().__init__()
        self._mapping: dict[str, str] = {k: str(v) for k, v in mapping.items()}
        self._listener: Listener | None = None
        self._hotkeys: list[HotKey] = []
        # One-shot "next-keypress" callback used by the auto-Enter feature
        # to cancel itself silently when the user touches the keyboard.
        self._cancel_callback = None

    @property
    def mapping(self) -> dict[str, str]:
        return dict(self._mapping)

    def start(self) -> None:
        if self._listener is not None or not self._mapping:
            return
        try:
            self._hotkeys = []
            for name, hk_str in self._mapping.items():
                hk_str = (hk_str or "").strip()
                if not hk_str:
                    continue
                keys = parse_hotkey_to_keys(hk_str)
                self._hotkeys.append(
                    HotKey(keys, self._make_emit(self.triggered, name))
                )
            if not self._hotkeys:
                return

            def on_press(key):
                try:
                    if self._listener is None:
                        return
                    k = self._listener.canonical(key)
                    for hk in self._hotkeys:
                        hk.press(k)
                    # One-shot any-key cancel callback (used by auto-Enter).
                    cb = self._cancel_callback
                    if cb is not None:
                        self._cancel_callback = None
                        try:
                            cb()
                        except Exception:
                            log.exception("Cancel callback failed")
                except Exception:
                    log.exception("Hotkey on_press failed for %r", key)

            def on_release(key):
                try:
                    if self._listener is None:
                        return
                    k = self._listener.canonical(key)
                    for hk in self._hotkeys:
                        hk.release(k)
                except Exception:
                    log.exception("Hotkey on_release failed for %r", key)

            self._listener = Listener(on_press=on_press, on_release=on_release)
            self._listener.start()
            log.info("Hotkey listener started with mapping=%s", self._mapping)
        except Exception as e:
            self._listener = None
            self.error.emit(f"Could not register hotkeys {self._mapping}: {e}")

    def stop(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            with contextlib.suppress(Exception):
                listener.stop()
        self._hotkeys = []

    def update_mapping(self, mapping: dict[str, str]) -> None:
        self.stop()
        self._mapping = {k: str(v) for k, v in mapping.items()}
        self.start()

    @property
    def is_alive(self) -> bool:
        if self._listener is None:
            return False
        try:
            return self._listener.is_alive()
        except Exception:
            return False

    def restart_if_dead(self) -> bool:
        if self._listener is not None and not self._listener.is_alive():
            log.warning("Hotkey listener thread is dead — restarting.")
            self.stop()
            self.start()
            return True
        return False

    def reset_state(self) -> None:
        """Clear internal pressed-key state of every registered HotKey."""
        for hk in list(self._hotkeys):
            try:
                hk._state.clear()
            except Exception:
                log.exception("Could not reset hotkey state for %r", hk)
        log.info("Hotkey state reset (%d hotkeys).", len(self._hotkeys))

    def arm_cancel_on_any_key(self, callback) -> None:
        """Install a one-shot callback that fires the next time ANY key is pressed.

        After firing, the callback is automatically disarmed; arm again if you
        want a fresh window. Used by the auto-Enter feature so the user can
        silently abort the pending Enter by touching any key.
        """
        self._cancel_callback = callback

    def disarm_cancel(self) -> None:
        """Cancel a previously-armed any-key callback without firing it."""
        self._cancel_callback = None

    def _make_emit(self, sig, name: str):
        def cb() -> None:
            sig.emit(name)

        return cb


__all__ = [
    "HotkeyManager",
    "has_modifier",
    "keyboard",
    "normalize_hotkey",
    "parse_hotkey_to_keys",
    "validate_hotkeys",
]
