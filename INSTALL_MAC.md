# Installing Rekordbox Pacemaker Sync on a Mac

You don't need the Terminal or any technical knowledge for this. It takes about
five minutes. For a full walkthrough of installing and using the app, get the
[beginner's guide (PDF)](https://github.com/clyttle85/rekordbox-pacemaker-sync/raw/main/Rekordbox_Pacemaker_Sync_Mac_Guide.pdf).

**You'll need:**

- A Mac running **macOS 14.4 (Sonoma) or newer**. To check, click the **Apple menu**
  at the top left of the screen, then **About This Mac**. If yours is older, update
  it under **System Settings** → **General** → **Software Update**. The app won't
  run on a Mac that can't update this far.
- Rekordbox 6 or 7, installed and opened at least once.
- To put music on your Pacemaker: the Pacemaker and its USB cable.

> The Mac version is new and hasn't yet been tried widely. If something doesn't
> work, please [report it](https://github.com/clyttle85/rekordbox-pacemaker-sync/issues)
> and include the log file described under [Troubleshooting](#troubleshooting).

## 1. Download the right file

First, check which kind of Mac you have: click the **Apple menu** at the top left
of the screen, then **About This Mac**.

| If the window says | Download the file ending in |
|---|---|
| **Chip:** Apple M1, M2, M3 or similar | `-arm64.dmg` |
| **Processor:** something with "Intel" in it | `-intel.dmg` |

Get it from the [Releases page](https://github.com/clyttle85/rekordbox-pacemaker-sync/releases/latest),
under **Assets** for the newest version.

## 2. Install the app

1. Double-click the downloaded `.dmg` file. A window opens.
2. Drag **RekordboxPacemakerSync** onto the **Applications** folder in that window.
3. Close the window, then eject the disk: in Finder's sidebar, click the ⏏ next
   to **Rekordbox Pacemaker Sync**.

## 3. Open it for the first time

This app isn't registered with Apple, so macOS blocks it the first time. You
only need to do this once.

1. Open your **Applications** folder and double-click **RekordboxPacemakerSync**.
2. macOS says it can't verify the app. Click **Done**. Don't choose
   "Move to Trash".
3. Open the **Apple menu** → **System Settings** → **Privacy & Security**.
4. Scroll down to the **Security** section. You'll see a message saying
   RekordboxPacemakerSync was blocked. Click **Open Anyway**.
5. Click **Open Anyway** again to confirm, and enter your Mac password or use
   Touch ID if asked.

> On macOS 14 you may see an **Open** option instead: right-click the app, choose
> **Open**, then click **Open** in the message. If the **Open Anyway** button isn't
> there, try double-clicking the app again, then go back to Privacy & Security.

After this, the app opens normally, like any other.

## 4. Allow access when macOS asks

macOS asks permission the first time the app needs each of these. Click **Allow**
each time:

- **Removable volumes:** needed to read and write your Pacemaker.
- **Documents, Downloads or Desktop:** needed if your music is stored there.

## 5. Connect your Pacemaker

1. Connect the Pacemaker to your Mac with its USB cable and turn it on.
2. Check that it shows up in Finder's sidebar under **Locations**.

The app looks for it every couple of seconds, so you can connect it before or
after opening the app.

## What a successful first run looks like

- **REKORDBOX LIBRARY**, on the left, lists your Rekordbox folders and playlists.
- **EDITOR LIBRARY**, next to it, is empty at first. It's the app's own holding
  area for playlists you're preparing. The Pacemaker calls playlists "cases".
- **DEVICE**, on the right, lists what's on your Pacemaker. When the Pacemaker is
  found, the bar at the bottom of the window says "Pacemaker detected".

To put music on your Pacemaker:

1. Tick playlists in **REKORDBOX LIBRARY**, click **Import to Editor Library**, and
   confirm.
2. Tick the cases you want in **EDITOR LIBRARY**, then click **Sync ▶**. The app
   copies the music to the Pacemaker.
3. When it's finished, click **⏏ Eject** before unplugging.

## Troubleshooting

### "Rekordbox Library Not Found"

Install Rekordbox if you haven't, and open it once so it creates your library.
Then quit and reopen this app. You can still add music without Rekordbox, using
**File → Import from M3U8** or by dragging audio files into the library.

### "Couldn't Read Rekordbox Library" or "Couldn't Unlock Rekordbox Library"

Quit Rekordbox and reopen this app. If the unlock message keeps appearing, a newer
Rekordbox may need a newer version of this app: check the
[Releases page](https://github.com/clyttle85/rekordbox-pacemaker-sync/releases/latest).

### The Pacemaker isn't detected

1. Check it appears in Finder's sidebar. If it doesn't, try another cable or USB
   port. Some cables only charge and can't carry data.
2. Check the app has permission. Go to **System Settings** → **Privacy & Security**
   → **Files & Folders** → **RekordboxPacemakerSync**, and turn on
   **Removable Volumes**. Then quit and reopen the app.
3. Point the app at it yourself. Click **Browse…** in the **DEVICE** panel, then
   select your Pacemaker. Its data is in a hidden folder, so press
   **Command + Shift + .** (period) to show hidden files, then open `.Pacemaker`
   and choose `music.db`.

### "File not found" when syncing or previewing a track

The app may not have permission to read the folder your music is in. Go to
**System Settings** → **Privacy & Security** → **Files & Folders** →
**RekordboxPacemakerSync**, and turn on the folders listed. Also check the track
still plays in Rekordbox, in case the file has moved.

### The app won't open, or says it isn't supported on this Mac

Check your Mac is running macOS 14.4 or newer. You may also have the file for the
other kind of Mac: check step 1, delete the app from Applications, and install the
other download.

### Turning on the debug log

If you need to report a problem, the log shows what went wrong:

1. With the app open, click **Debug** in the menu bar at the top of your screen,
   then **Enable Debug Logging**. You need to do this each time you open the app.
2. Do whatever caused the problem.
3. Click **Debug** → **Open Log File…** to see it.

The log file is at
`~/Library/Application Support/RekordboxPacemakerSync/rb_pm_sync.log`. To find it
yourself, click **Go** → **Go to Folder…** in Finder's menu bar, and paste that
path in. Attach the file to your
[problem report](https://github.com/clyttle85/rekordbox-pacemaker-sync/issues).

Note that some errors happen as the app starts, before you can turn the log on.
For those, a description or screenshot of the message is the most useful thing.

## Uninstalling

Drag **RekordboxPacemakerSync** from Applications to the Trash. To also remove the
Editor Library and its settings, delete the
`~/Library/Application Support/RekordboxPacemakerSync` folder. This doesn't touch
your Rekordbox library, your music files, or your Pacemaker.
