# Environment status — 2026-11-05

Target: yingboyun_pare (`pjy1pare`), project `/root/data1/FlashTrustfulRSI`.

- Source, pinned dependencies, completed trajectories, credentials reference and SQLite request ledger migrated from 138.
- 184 pre-migration request rows have identical semantic SHA-256 on both servers: `a5848012de5e21adbdbbd3324a8239142836016784a46def9eebf08247b898e5`.
- 138 request admission remains drained. No pending requests remained at transfer. New host restored the existing phase limits without resetting usage.
- Python 3.12.15 independent runtime; 40 integration checks passed. Public core suite: 19 offline checks plus 6 optional source-mode checks.
- Project Docker daemon 29.9.3 runs on a private Unix socket. Actual image import currently fails with `unshare: operation not permitted`; generated code profiles remain blocked.
- Current instance exposes 4 GiB memory and 2 CPU quota, read-only cgroup mount, no configured subordinate UID ranges. Per the user’s instruction, Docker-dependent experiments are now explicitly skipped while adapter integration continues; daemon connectivity alone is not sufficient.
- Shared data-volume inode quota is nearly full. Upstream precomputed runs are excluded. Project evidence remains intact; no unrelated files were deleted.

References: [Docker binary installation](https://docs.docker.com/engine/install/binaries), [rootless resource limits](https://docs.docker.com/engine/security/rootless/tips/).
