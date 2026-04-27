# Paste Target Lock — Manual UAT

**Date:** _________
**Tester:** _________
**Build:** v1.3.0 dev (commit `0fdd5ab` or later)

## Prerequisites
- TextWhisper running, Whisper model loaded ("Ready" tray status)
- Settings → "Paste Lock" tab → enable
- Open: Notepad, VS Code, Windows Terminal with Claude Code

## Checklist

- [ ] Enabling the master setting in Settings reveals the lock section in the tray menu (right-click tray)
- [ ] Disabling it hides the section
- [ ] Lock to Notepad: focus Notepad, press Alt+L → tray label shows "Unlock paste target (Untitled - Notepad)" + lock chime plays
- [ ] Border draws around Notepad
- [ ] Move Notepad — border follows
- [ ] Resize Notepad — border resizes
- [ ] Focus another app, dictate ("hello world test"), text lands in Notepad even though focus was elsewhere
- [ ] Focus is restored to the previously-active app after the paste
- [ ] Smart toggle: focus VS Code, press Alt+L (single press), tray now says "Unlock paste target (... - Visual Studio Code)" + lock chime + border moves to VS Code
- [ ] Press Alt+L again while focused on VS Code → unlock chime + border hides + tray label clears
- [ ] Re-lock to Claude Code in Windows Terminal: focus it, Alt+L → border draws around Windows Terminal
- [ ] Dictate from Notepad while locked to Claude Code — text lands in Claude Code
- [ ] Minimize the locked Claude Code window, dictate → window pops up + paste lands + stays restored
- [ ] Close the locked window, then dictate → tray notification "Paste target window is gone..." + transcription stays in clipboard
- [ ] Per-session capture: with no sticky lock, focus Notepad, press Alt+Z, dictate, alt-tab away, finish speaking → text lands in Notepad
- [ ] Sticky precedence: lock to Notepad via Alt+L, then press Alt+Z while focused on VS Code → text lands in Notepad (sticky won)
- [ ] Settings → uncheck "Show colored border" → border hides while sticky lock remains active
- [ ] Settings → change border color → re-lock → new color appears

## Bugs found

(Capture per-step in the table above; expand below.)
