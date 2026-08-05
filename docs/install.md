# Install

## Prerequisites

Docker Desktop or Docker Engine 24 or newer, and roughly 1 GB of disk for the
image. Extracts need their own space on top: a country is single-digit GB, a
continent is tens of GB, and filtering needs room for intermediates while it
runs.

## Windows

```
git clone https://github.com/MartinHoblisch/pbf-forge.git
cd pbf-forge
start.bat
```

`start.bat` starts Docker Desktop if it is not running, builds the image on
first use, waits for the server, and opens the browser at
`http://localhost:8000`. Double-clicking the file works as well.

It applies `docker-compose.windows.yml` on top of the base compose file. That
override mounts `/mnt` read-only into the container so the folder picker can
offer your drive letters. If you would rather not hand the container that
view, start it with `docker compose up` instead; the data directory still
works, the picker just has nothing to list.

## Linux and macOS

```bash
git clone https://github.com/MartinHoblisch/pbf-forge.git
cd pbf-forge
./start.sh
```

If Docker answers with `permission denied while trying to connect to the
Docker daemon socket`, your user is not in the `docker` group yet:

```bash
sudo usermod -aG docker $USER
```

Log out and back in, or run `newgrp docker` in the current shell, then retry.

## Where things are stored

| | |
|---|---|
| Downloaded extracts and outputs | The data directory, `DATA_DIR`. Defaults to `./data` inside the checkout. Change it in the interface, then restart the container so the bind mount follows. |
| Presets, custom URLs, filter history, job state | `./config` in the checkout, mounted at `/app/config`. |
| Reports | Next to each output file, named after it, with a `.txt` suffix. |
| Job logs | Under `config/jobs/`, one file per job; the interface links to them. |

## Memory and CPU

`docker-compose.yml` caps the container at 4 GB. A filter that has to resolve
way and relation members over a country-sized extract has been measured at
around 2 GB peak, so the cap has headroom for that case but is not unlimited.
Raise `mem_limit` there if a job is killed.

The Resources section in the interface has two presets. Full power uses every
core at normal priority. Background halves the thread count and runs at nice
10, so a long filter leaves the machine usable. Thread count and nice value
can also be set explicitly, and the explicit value wins over the preset.

## Updating

```bash
git pull
```

Then start again. The launchers rebuild the image when the source has changed.
A browser that still shows the old interface needs one hard reload
(Ctrl+Shift+R); after that the cache headers keep it current on their own.
