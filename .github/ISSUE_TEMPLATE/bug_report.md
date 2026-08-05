---
name: Bug report
about: Something is broken or behaving unexpectedly
labels: bug
---

**Describe the bug**
A clear description of what went wrong.

**Steps to reproduce**
1.
2.
3.

**Expected behaviour**
What you expected to happen.

**Actual behaviour**
What actually happened. Paste any error messages from the interface or from `docker compose logs`.

**Environment**
- PBF Forge version / commit:
- OS:
- Docker version (`docker --version`):
- Browser (if the problem is in the interface):

**The job that failed** (if relevant)
- Source file or URL:
- Include tags:
- Exclude tags:
- Geometry types checked:
- Attribute mode:
- Output format:

If an output file was produced, attach the `.txt` report written next to it.
It records the source extract, its OSM timestamp, every expression and the
per-phase timings, which is most of what is needed to reproduce the run. The
job log under `config/jobs/` helps as well.
