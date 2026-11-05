# A7R IIIA research scripts

This directory preserves first-party, one-off scripts used for the offline boot and `/dev/dmpgles2` investigations. It contains source only; no firmware, extracted binaries, disk images, manifests, serial captures, debugger logs, or QEMU logs are included.

## Inputs and outputs

The boot and disk-building scripts expect the user's own inputs under ignored `local/` paths, matching the original experiment layout. They write all generated kernels, synthetic disks, manifests, and logs under `local/r3a-research/`. The hash required by `patch_grm_r3a.py` must be supplied from the user's local manifest through `R3A_EXPECTED_PARTITION15_SHA256`; it is deliberately not stored in this repository.

Debugger scripts use `/inputs` and `/outputs` paths intended for an isolated Linux container with read-only inputs and disposable output copies. They connect only to a local QEMU GDB or serial endpoint; they do not access USB or a physical camera.

These scripts are experimental and tied to one firmware snapshot, kernel build, and QEMU layout. Some are early probes whose limitations are documented in [the V002 summary](../../docs/validations/V002-r3a-boot.md). They are not general-purpose utilities. Never point patch scripts at original backups or a base image; use disposable copies.
