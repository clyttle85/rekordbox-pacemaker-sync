# Building the macOS App

Mac builds run on GitHub Actions, because PyInstaller can't cross-compile from
Windows. There's nothing to install locally unless you have a Mac and want to
build there. To install the finished app, see [INSTALL_MAC.md](INSTALL_MAC.md).

## Building with GitHub Actions

`.github/workflows/build-mac.yml` builds one DMG per architecture:

| DMG | Runner | For |
|---|---|---|
| `RekordboxPacemakerSync-<version>-arm64.dmg` | `macos-15` | Apple Silicon (M-series) |
| `RekordboxPacemakerSync-<version>-intel.dmg` | `macos-15-intel` | Intel |

- **Test build:** Actions → Build macOS DMGs → Run workflow (or
  `gh workflow run build-mac.yml`). Optionally enter a version, otherwise the DMG
  is named `dev-<commit>`. Download the DMGs from the run's Artifacts section.
- **Release:** push a tag such as `v1.2.0`. The DMGs get version `1.2.0` and are
  attached to a GitHub Release, which is created only if both builds pass.

GitHub retires old macOS runner images, so if a job fails waiting for a runner,
check the current labels in the
[runner-images README](https://github.com/actions/runner-images#available-images).

### What the smoke tests check

A build fails unless all of these pass, on both architectures:

1. Every module in `core/` and `ui/` imports from source.
2. The bundle's binary is the expected architecture, and no bundled library needs
   a newer macOS than `LSMinimumSystemVersion` in the spec (currently 14.4, set by
   the PyQt6 wheels). If this fails, update the spec and INSTALL_MAC.md.
3. `RekordboxPacemakerSync --selftest` passes in the bundle. It creates and reads an
   encrypted `master.db` through the bundled pyrekordbox and SQLCipher, checks the
   Qt Multimedia backend, then opens the main window for a few seconds. See
   `core/selftest.py`. This is what catches missing `hiddenimports`.
4. The bundled app, launched normally, is still running after 10 seconds.
5. The same self-check passes from the app inside the mounted DMG.
6. `setup_mac.command --selftest` passes from a clean checkout.

They run headless on a machine with no Rekordbox or Pacemaker, so they can't
replace testing on a real Mac with real hardware.

## Building on a Mac

Python 3.9 or newer works, and Python 3.11+ gets pyrekordbox 0.5. No Homebrew
packages are needed: `sqlcipher3-wheels` ships prebuilt macOS binaries.

### Step 1: Set up the environment

Double-click `setup_mac.command`. It checks for `python3`, creates `.venv`,
installs `requirements.txt`, and starts the app. If macOS offers to install the
Command Line Developer Tools, accept, then double-click the file again. Use
`run_mac.command` to start the app after that.

If macOS won't run either file because it was downloaded in a ZIP, either
`git clone` the repo or approve it under System Settings → Privacy & Security →
Open Anyway. If it says you don't have permission, run
`chmod +x setup_mac.command run_mac.command`.

### Step 2: Build the app bundle

```bash
.venv/bin/pip install pyinstaller pillow
.venv/bin/pyinstaller --noconfirm rekordbox_pacemaker_sync_mac.spec
```

This produces `dist/RekordboxPacemakerSync.app` for your Mac's architecture. Set
`APP_VERSION=1.2.0` in the environment to set its version (the default is 0.0.0).

### Step 3: Test it

```bash
QT_QPA_PLATFORM=offscreen dist/RekordboxPacemakerSync.app/Contents/MacOS/RekordboxPacemakerSync --selftest
open dist/RekordboxPacemakerSync.app
```

An app you built yourself isn't quarantined, so Gatekeeper won't block it on this
Mac. It will on other people's Macs if it's unsigned.

## Rekordbox decryption key

There's no setup step. Both supported pyrekordbox versions (0.4.4+ and 0.5.x)
contain the key for `master.db`. Older instructions mention
`python -m pyrekordbox setup`, which no longer exists.

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

## File locations

| File | Purpose |
|---|---|
| `.github/workflows/build-mac.yml` | The CI build |
| `rekordbox_pacemaker_sync_mac.spec` | PyInstaller build config for macOS |
| `packaging/mac/entitlements.plist` | Hardened-runtime exceptions for signed builds |
| `icon.icns` | App icon |
| `setup_mac.command`, `run_mac.command` | Run from source |
