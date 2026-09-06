# Install

## Prerequisites

Docker Desktop or Docker Engine 24 or newer, and roughly 1 GB of disk for the
image. Extracts need their own space on top: a country needs a few GB, a
continent tens of GB.

Filtering needs more room than the extract alone. The source, the filtered PBF,
a streaming export and the finished output can exist at the same time. Measured
on Iceland (62 MB) with a broad `highway` filter to GeoPackage, the peak was
209 MB, about 3.4 times the source. A narrow filter that keeps little needs far
less, because every intermediate shrinks with it. Budget four times the extract
and a broad filter still fits.

The image carries osmium-tool 1.19 and GDAL 3.12 from the Ubuntu 26.04 archive.
Which expression forms and export options exist follows from those versions.
Ask the container directly with
`docker compose exec pbf-forge osmium --version`.

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
offer your drive letters. If you do not want to give the container that
access, start it with `docker compose up` instead. The data directory still
works; the picker then has nothing to list.

## Linux

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

| What | Where |
|---|---|
| Downloaded extracts and outputs | The data directory, `DATA_DIR`. Defaults to `./data` inside the checkout. Change it in the interface, then restart the container so the bind mount follows. |
| Presets, custom URLs, filter history, job state | `./config` in the checkout, mounted at `/app/config`. |
| Reports | Next to each output file, named after it, with a `.txt` suffix. |
| Job logs | Under `config/jobs/`, one file per job; the interface links to them. |
| Largest download accepted | 100 GB, from `MAX_DOWNLOAD_SIZE`. Set that environment variable to raise or lower it. A planet file fits inside the default; every regional extract is far below it. |

## Memory and CPU

`docker-compose.yml` caps the container at 4 GB. A filter that has to resolve
way and relation members over a country-sized extract has been measured at
around 2 GB peak, so the cap has headroom for that case but is not unlimited.
If a job is killed for memory, raise `mem_limit` and `memswap_limit` together:
`memswap_limit` is also 4 GB, and Docker refuses to start a container whose
swap limit is below its memory limit.

The Resource limits section in the interface has two presets. Full power uses
every core at normal priority. Background halves the thread count and runs at a
lower process priority (nice 10), so you can keep using the machine while a
long filter runs. Thread count and nice value can also be set explicitly, and
an explicit value overrides the preset.

## Updating

```bash
git pull
```

Then start again. The launchers rebuild the image when the source has changed.
A browser that still shows the old interface needs one hard reload
(Ctrl+Shift+R); after that the cache headers keep it current on their own.
