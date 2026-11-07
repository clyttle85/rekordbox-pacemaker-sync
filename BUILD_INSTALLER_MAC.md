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

## Turning on code signing and notarization

Unsigned builds work, but every user has to approve the app by hand the first
time (see [INSTALL_MAC.md](INSTALL_MAC.md)). Signing and notarizing removes that
step. The workflow already contains the steps, and they run automatically once
all five secrets below exist. Until then they're skipped and the build stays
unsigned. **These steps have never been run**, so expect to check the first
signed build carefully.

You need an [Apple Developer Program](https://developer.apple.com/programs/)
membership ($99/year). None of this needs a Mac:

1. **Create a signing key and request** (on Windows, using Git Bash's `openssl`):

   ```bash
   openssl req -new -newkey rsa:2048 -nodes -keyout developer_id.key \
     -out developer_id.csr -subj "/emailAddress=you@example.com/CN=Your Name/C=US"
   ```

2. **Get the certificate.** Sign in to
   [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources/certificates/list),
   add a **Developer ID Application** certificate, upload `developer_id.csr`, and
   download the resulting `.cer`. Only the account holder can create this type.

3. **Bundle them into a `.p12`.** `-legacy` matters because the macOS keychain
   can't import OpenSSL 3's default encryption:

   ```bash
   openssl x509 -inform der -in developerID_application.cer -out developer_id.pem
   openssl pkcs12 -export -legacy -inkey developer_id.key -in developer_id.pem \
     -out developer_id.p12
   ```

4. **Create an app-specific password** at [account.apple.com](https://account.apple.com)
   under Sign-In and Security.

5. **Add the repo secrets** (Settings → Secrets and variables → Actions):

   | Secret | Value |
   |---|---|
   | `APPLE_CERT_P12` | The `.p12`, base64-encoded: `base64 -w0 developer_id.p12` |
   | `APPLE_CERT_PASSWORD` | The password chosen when exporting the `.p12` |
   | `APPLE_ID` | Your Apple account email |
   | `APPLE_TEAM_ID` | The 10-character Team ID from your Apple Developer account |
   | `APPLE_APP_PASSWORD` | The app-specific password from step 4 |

6. **Run the workflow.** Check the "Sign app bundle" and "Sign and notarize DMG"
   steps ran rather than being skipped. The hardened-runtime exceptions the app
   needs are in `packaging/mac/entitlements.plist`, and the signed app goes through
   the same smoke tests, so a missing entitlement fails the build.

7. **Delete the private key files** from your computer once the secrets are saved,
   and update INSTALL_MAC.md to remove the "Open Anyway" steps.
