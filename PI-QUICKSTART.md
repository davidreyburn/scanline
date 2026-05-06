# Scanline — Pi Quickstart

Get a fresh Pi 3 from bare SD card to SSH-ready deploy target in ~10 minutes.

---

## What you need

- Raspberry Pi 3B/3B+
- SD card (16GB+)
- HDMI cable + monitor/TV
- [Raspberry Pi Imager](https://www.raspberrypi.com/software/) installed on your dev machine
- Your dev machine's SSH public key (see below if you don't have one)

---

## 1. Check your SSH key

On your dev machine, run:

```powershell
cat $env:USERPROFILE\.ssh\id_ed25519.pub
```

If that file doesn't exist, generate a key first:

```powershell
ssh-keygen -t ed25519 -C "scanline-deploy"
```

Accept all defaults (no passphrase). Copy the contents of the `.pub` file — you'll paste it into Imager in the next step.

---

## 2. Flash the SD card

1. Open **Raspberry Pi Imager**
2. **Choose Device** → Raspberry Pi 3
3. **Choose OS** → *Raspberry Pi OS (other)* → **Raspberry Pi OS Lite (64-bit)**
   - "Lite" = no desktop. This is what we want.
4. **Choose Storage** → your SD card
5. Click **Next** → when prompted, click **Edit Settings**

In the settings panel, configure:

| Setting | Value |
|---|---|
| Hostname | `scanline` |
| Username | `chives` |
| Password | something you'll remember |
| Enable SSH | ✅ — *Allow public-key authentication only* |
| SSH public key | paste your `id_ed25519.pub` contents |
| WiFi SSID | your network name |
| WiFi password | your network password |
| Timezone | America/New_York (or yours) |
| Keyboard layout | us |

Click **Save**, then **Yes** to apply, then **Yes** to write. Flashing takes 2–3 minutes.

---

## 3. First boot

1. Insert the SD card into the Pi
2. Connect HDMI (so you can see it boot if needed)
3. Power on
4. Wait **90 seconds** for the first-boot setup to complete

Find the Pi on your network:

```powershell
ping scanline.local
```

If mDNS doesn't work, check your router's DHCP table for a device named `scanline` and note its IP.

---

## 4. SSH in and update

```bash
ssh chives@scanline.local
```

Once in, update the system:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo reboot
```

Reconnect after ~30 seconds:

```bash
ssh chives@scanline.local
```

---

## 5. Scanline prep

Run these once on the Pi:

```bash
# evdev keyboard access (required for Phase 3 input)
sudo usermod -a -G input,video,tty chives

# create deploy target
mkdir -p /home/chives/scanline

# note the IP for deploy.sh
hostname -I
```

Write down that IP — you'll need it when you update `deploy.sh`.

Log out:

```bash
exit
```

---

## 6. Configure deploy.sh on your dev machine

Open `deploy.sh` (not written yet — comes in Phase 6) and set:

```bash
HOST="chives@192.168.1.43"
REMOTE_DIR="/home/pi/scanline"
```

Add an SSH config alias for convenience (optional but recommended):

```
# Add to C:\Users\David\.ssh\config
Host scanline
    HostName 192.168.1.43
    User chives
    IdentityFile ~/.ssh/id_ed25519
```

Then `ssh scanline` works from anywhere on your dev machine.

---

## 7. Verify passwordless SSH

From your dev machine:

```powershell
ssh chives@scanline.local "echo ok"
```

Should print `ok` with no password prompt. If it asks for a password, the public key wasn't installed correctly — re-flash with the correct key in Imager settings.

---

## You're ready

The Pi is now:
- Running Raspberry Pi OS Lite (no desktop, minimal footprint)
- Accepting passwordless SSH from your dev machine
- `pi` user in the `input`, `video`, and `tty` groups
- `/home/pi/scanline/` directory ready for deployment

Next step: once `deploy.sh` is written in Phase 6, run `./deploy.sh --full` to push the code and set up the systemd service.

Until then, you can manually copy files with `scp`:

```powershell
scp -r C:\Users\David\agent\scanline\* chives@192.168.1.43:/home/chives/scanline/
```
