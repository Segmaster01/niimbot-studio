# Niimbot Studio

An offline label designer and Bluetooth driver for the **Niimbot D11** thermal label printer, for Windows.

It needs no vendor app, no account, and no cloud. Designs are saved as files on your PC.

> Unofficial project. Not affiliated with or endorsed by NIIMBOT.

## Features

- **Direct Bluetooth LE connection.** No pairing or driver install. The app reconnects automatically if the connection drops.
- **Label roll detection over RFID.** The app reads the remaining label count and recognizes the label size of known rolls. For an unknown roll it asks for the size once and remembers it.
- **Canvas matches the loaded label.** It also shades any area the 12 mm printhead can't reach.
- **Elements:**
  - Text, with optional shrink-to-fit
  - Images, converted to 1-bit with Floyd–Steinberg, Atkinson, ordered or threshold dithering
  - Rectangles, ellipses and lines
  - QR codes and Code 128 barcodes
- **Layout tools:**
  - Smart snapping guides to the label center and edges and to other elements
  - Align and distribute tools
  - An optional grid
  - Layers, locking and rotation
  - Undo/redo
  - A pixel-exact print preview
- **Numbered runs.** Put `{n}` or `{n:03}` in text or barcodes to print "BIN 001…BIN 050" in one job. `{date}` and `{time}` are also supported.
- **Print confirmation.** Every job opens a preview with quantity and darkness settings, and warns if the design doesn't match the loaded roll or the roll will run out.
- **Design library.** Saved designs appear as thumbnails, and there's a print alignment calibration with a printable ruler.
- **Updates.** The app checks GitHub Releases and can update itself.

## Install

1. Download `NiimbotStudio-Setup-x.y.z.exe` from [Releases](https://github.com/Segmaster01/niimbot-studio/releases).
2. Run it. It installs for the current user, so it doesn't need admin rights.
3. Switch the printer on, open Niimbot Studio, and pick the printer from the list.

To deploy to several PCs silently: `NiimbotStudio-Setup-x.y.z.exe /SILENT`.
Updates install over the top and keep your settings and designs.

## Brand packs

You can give the app your organization's logo and icon with a `.nsbrand` brand pack. A brand pack is a zip file containing `brand.json` plus PNG images:

```json
{
  "name": "Example Co",
  "logo": {"blue": "logo-blue.png", "red": "logo-red.png"},
  "icon": {"blue": "icon-blue.png", "red": "icon-red.png"},
  "accent_names": {"blue": "Example Blue", "red": "Example Red"}
}
```

Every field is optional, and `logo` and `icon` can also be a single file name. There are two ways to install a pack:

- **Per user:** Settings → Brand pack → Load…
- **Per install:** put the `.nsbrand` file in the same folder as `Setup.exe` before running it.

With a brand pack loaded, the toolbar shows your logo and an "Insert logo" button appears. Updates keep the pack.

## Build from source

Requires Python 3.13 on Windows.

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python "Niimbot Studio.pyw"      # run from source
.\build.ps1                                      # build dist\NiimbotStudio (+ installer if Inno Setup 6 is installed)
```

**Releasing:**

1. Bump `__version__` in `niimbot_studio/__init__.py`.
2. Commit, then tag `vX.Y.Z` and push the tag.

GitHub Actions then builds the installer and publishes the release. Installed copies pick it up on their next update check.

## Code layout

| File | Purpose |
|---|---|
| `niimbot_studio/driver.py` | Bluetooth protocol driver (async, no Qt; usable on its own) |
| `niimbot_studio/printer_service.py` | Runs the driver on a background thread: auto-connect, status polling, reconnect |
| `niimbot_studio/items.py` | Label elements and 1-bit image conversion |
| `niimbot_studio/document.py` | Document model, undo history, `.nlbl` file format, rendering |
| `niimbot_studio/canvas.py` | Editor canvas: handles, snapping guides, zoom/pan |
| `niimbot_studio/app.py` | Main window |
| `niimbot_studio/brand.py`, `updater.py` | Brand packs; GitHub release updates |

## D11 protocol notes

These were verified on a D11 with firmware 3.30 and hardware 3.03. [printers.niim.blue](https://printers.niim.blue) is an excellent general reference.

- **BLE transport:** service `e7810a71-73ae-499d-8c15-faa9aef0c3f2`, characteristic `bef8d6c9-9c21-4c9e-b632-bd58c1009f9f` (write and notify).
- **Packet format:** `55 55 CMD LEN DATA… XOR AA AA`, where the checksum is the XOR of CMD, LEN, and the data bytes.
- **Print sequence:**
  1. `SetDensity 21`
  2. `SetLabelType 23`
  3. `PrintStart 01`
  4. `PrintClear 20`
  5. `PageStart 03`
  6. `SetPageSize 13 [rows u16, cols u16]`
  7. `Quantity 15 [u16]`
  8. Row packets (`85` bitmap / `84` blank)
  9. `PageEnd e3`
  10. Wait for `e0` page-index notifications
  11. `PrintEnd f3`
- **Row pacing:** row packets must be about 10 ms apart. Sending them in bursts makes the printer reply `db 06` (data error).
- **Completion:** the D11 doesn't answer `PrintStatus a3`. It reports finished copies with unsolicited `e0 [page u16]` packets.
- **Image orientation:** the printhead is 96 dots (12 mm) and prints "left". The label image is rotated 90° clockwise before it is sent.
- **RFID (`1a` → `1b`):** returns UUID, barcode, serial, total, used, and type. The label size is encrypted on the tag and isn't exposed, so the app keeps a local barcode→size table.
- **Heartbeat (`dc` → `dd`, 10 bytes):** byte 8 is the lid state (1 = closed), byte 9 is the battery level (1–4).
- **Alignment:** printers can differ by a few dots. Use Settings → Print alignment → Print ruler to calibrate.

## Privacy

Settings, designs and learned label rolls stay on your PC. The only network request is the optional update check to the GitHub API, which you can turn off in Settings.

## License

MIT
