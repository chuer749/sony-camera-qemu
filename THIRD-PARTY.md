# Third-party dependency

The A7M3 reference project is an external Git submodule at `third_party/a7m3`, pinned by this repository's gitlink to commit `020603daf84aff35069a1071a6385f80fb41f397`:

- Upstream: [Derek2011cc/A7M3-Firmware-QEMU-Boot-Experiment](https://github.com/Derek2011cc/A7M3-Firmware-QEMU-Boot-Experiment)
- License and attribution: see the upstream repository at the pinned commit.

This repository does not contain a copy of that project's source, patches, scripts, firmware findings, or license text. The offline generator uses files from the submodule, so initialize it before using those commands.

The QEMU source is fetched separately by `engine/prepare_source.py` from [ma1co/qemu](https://github.com/ma1co/qemu) at the revision recorded in the source. QEMU itself is not included here.

The first-party project files in this repository have no declared open-source license.
