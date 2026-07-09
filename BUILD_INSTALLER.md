# Building the Windows Installer

**Session:** <!-- paste your Claude session ID here — see instructions below -->
**Date:** 2026-06-23

### How to find the session ID

**Claude Code desktop app (most likely):**
Look at the conversation list in the left sidebar. Right-click the current conversation and look for "Copy link" or "Copy ID", or hover over it — the ID is the long alphanumeric string (e.g. `a1b2c3d4-...`). Alternatively, if the app shows a URL in the title bar or address area, the ID is the last path segment.

**Claude.ai in a browser:**
The session ID is in the URL bar while the conversation is open:
```
https://claude.ai/chat/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
                        ^^^^^^^^^^^^^ this part ^^^^^^^^^^^^
```

**Claude Code CLI (`claude` in terminal):**
Run `claude --help` or check `~/.claude/` — conversation logs may be stored there with the ID as the filename.

---

## What Was Done This Session

1. **Installed into venv:** `pyinstaller` and `pillow`
2. **Converted icon:** `D:\Downloads\icon.png` → `icon.ico` (multi-resolution: 16–256px)
3. **Created PyInstaller spec:** `rekordbox_pacemaker_sync.spec`
4. **Built the app:** `dist\RekordboxPacemakerSync\` (166 MB total)
   - `dist\RekordboxPacemakerSync\RekordboxPacemakerSync.exe` (~6 MB)
   - `dist\RekordboxPacemakerSync\_internal\` (all dependencies)
5. **Created Inno Setup script:** `installer.iss`

---

## What's Left: Build the Installer

### Step 1 — Install Inno Setup 6

Download and install from: https://jrsoftware.org/isdl.php  
It's free, ~5 MB, standard Windows installer tool.

### Step 2 — Build the installer package

Run this in Claude Code (the `!` prefix runs it in the terminal):

```
! & "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" "D:\Documents\Code Projects\Python Projects\rekordbox-pacemaker-sync\installer.iss"
```

This produces:

```
dist\RekordboxPacemakerSync_Setup.exe
```

### Step 3 — Run the installer

Double-click `RekordboxPacemakerSync_Setup.exe`. It will:
- Install to `C:\Program Files\RekordboxPacemakerSync\`
- Create a **desktop shortcut** (checked by default in the wizard)
- Add a **Start Menu** entry under "Rekordbox Pacemaker Sync"
- Register in **Add/Remove Programs** with an uninstaller

---

## Rebuilding After Code Changes

If you change the Python source and need to rebuild:

```
! cd "D:\Documents\Code Projects\Python Projects\rekordbox-pacemaker-sync" ; .venv\Scripts\pyinstaller rekordbox_pacemaker_sync.spec
```

Then re-run the Inno Setup step above to repackage.

---

## File Locations

| File | Purpose |
|---|---|
| `icon.ico` | App icon (generated from `D:\Downloads\icon.png`) |
| `rekordbox_pacemaker_sync.spec` | PyInstaller build config |
| `installer.iss` | Inno Setup installer script |
| `dist\RekordboxPacemakerSync\` | Built app (input to installer) |
| `dist\RekordboxPacemakerSync_Setup.exe` | Final installer (created by Inno Setup) |
