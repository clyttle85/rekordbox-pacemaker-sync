# Building the macOS App

Everything below runs **on a Mac** — PyInstaller can't cross-compile, so none
of this can be done from Windows. `icon.icns` is already generated and
committed (converted from `D:\Downloads\icon.png` via Pillow on the Windows
side), so that part's done.

The Windows-specific code paths were ported to macOS in the same session this
doc was written, but **none of it has been run on an actual Mac yet** — the
steps below are the first real test. Expect at least one round of fixing
`hiddenimports` or similar after the first build attempt; that's normal.

## Step 1 — Install dependencies

```bash
# PyQt6, pyrekordbox, mutagen, sqlalchemy
pip install -r requirements.txt

# SQLCipher (for reading Rekordbox's encrypted master.db) — requirements.txt
# deliberately excludes sqlcipher3-wheels on Mac since it's a Windows-only
# prebuilt wheel. Install libsqlcipher via Homebrew first, then build the
# Python binding against it:
brew install sqlcipher
pip install sqlcipher3-binary   # or: pip install sqlcipher3 (builds from source)
```

If neither `sqlcipher3-binary` nor a from-source `sqlcipher3` install works
cleanly, check pyrekordbox's own docs/issues for the currently-recommended
macOS SQLCipher setup — this is the one dependency here that's genuinely
OS-specific and the above is a best-effort starting point, not verified.

```bash
# One-time: let pyrekordbox find/derive the decryption key for your Rekordbox master.db
python -m pyrekordbox setup
```

## Step 2 — Install PyInstaller and Pillow (if not already)

```bash
pip install pyinstaller pillow
```

## Step 3 — Build the app bundle

```bash
pyinstaller rekordbox_pacemaker_sync_mac.spec
```

Produces `dist/RekordboxPacemakerSync.app`. That's the whole deliverable —
it's a normal double-clickable macOS app, runnable directly from `dist/` (or
anywhere else) with **no install step and no need to move it into
`/Applications`** — that's just a convention, never a requirement for `.app`
bundles.

## Step 4 — Run it

```bash
open dist/RekordboxPacemakerSync.app
```

or just double-click it in Finder.

**Expect Gatekeeper to complain** the first time, since this build is
unsigned: *"RekordboxPacemakerSync.app" cannot be opened because the
developer cannot be verified.* Right-click → Open (instead of double-click)
the first time bypasses this per-app. A real fix for end users needs code
signing + notarization, which needs an Apple Developer ID ($99/year) — not
set up here. `rekordbox_pacemaker_sync_mac.spec` has `codesign_identity=None`
as a placeholder for when/if you set that up.

## Step 5 (optional) — Wrap it in a .dmg for distribution

Not required to run the app (see Step 3) — only useful if you want a nicer
single-file download for others. A plain `.zip` of the `.app` works just as
well and is simpler:

```bash
# Option A: zip (simplest)
cd dist && zip -r RekordboxPacemakerSync.zip RekordboxPacemakerSync.app

# Option B: a proper disk image
hdiutil create -volname "Rekordbox Pacemaker Sync" \
  -srcfolder dist/RekordboxPacemakerSync.app \
  -ov -format UDZO \
  dist/RekordboxPacemakerSync.dmg
```

Either way, opening the result and double-clicking the `.app` inside runs it
immediately — dragging to `/Applications` is optional, for anyone who wants
it to persist/show up in Launchpad.

## Rebuilding after code changes

```bash
pyinstaller rekordbox_pacemaker_sync_mac.spec
```

## File locations

| File | Purpose |
|---|---|
| `icon.icns` | App icon (generated from `D:\Downloads\icon.png` via Pillow, on Windows) |
| `rekordbox_pacemaker_sync_mac.spec` | PyInstaller build config for macOS |
| `dist/RekordboxPacemakerSync.app` | The built app — run it directly |
