# Texto sugerido para o PR no countervolts/d4r

**Título:** `d4r-check: report the GPU and kernel set the bridge uses; add CI`

**Corpo:**

---

Three small tooling changes, one commit each. Nothing here touches the shim, the bridge, ZLUDA or the kernels.

### 1. `d4r-check.sh` reports the GPU and kernel folder the bridge actually uses

The check took the first KFD GPU node and only looked for `kernels/<target>`. The bridge picks the GPU with the
most SIMDs, honours `D4R_GPU_ARCH`, and serves `<target>-fp8` on RDNA4 with `NativeFp8` and the marked
`accuracy/<target>` set with `PreferAccuracy`. On a Ryzen 7000 desktop with the iGPU enabled (gfx1036, often
listed before the discrete GPU), the check reported the iGPU and said its native kernels were missing. It also
never noticed when `PreferAccuracy = true` had no accuracy set to use.

The check now mirrors `tools/d4r_native_selection.h` for the release layout, reading `NativeFp8`,
`PreferAccuracy` and `NativeKernels` from `d4r.ini` as the shim does (any key case, inline comments, CRLF),
with the environment winning. Example output on an iGPU + RX 9070 XT layout:

```
  note     2 GPUs; d4r uses the one with the most SIMDs (gfx1201); D4R_GPU_ARCH overrides
  ok       GPU gfx1201: native kernels in d4r/kernels/gfx1201-fp8
```

`tests/test_install_check.py` runs the script against fake KFD topologies and compares the folder it reports
with the C selection for every target × accuracy × FP8 combination. The new tests fail on the current script
and pass with this change under dash and bash. `D4R_CHECK_KFD_NODES` replaces the topology path for the tests.

### 2. `check_environment.sh` without ripgrep

`rg` was used for lspci, vulkaninfo and pacman but is not a listed requirement; without it those sections only
printed an error. It now uses `grep -E`, lists the KFD GPUs with their gfx target and the one d4r uses, and
lists packages with dpkg or rpm where pacman is missing.

### 3. CI (GitHub Actions, Ubuntu 24.04)

- `python3 -m unittest discover -s tests` (the existing accuracy tests and the new install-check tests)
- ShellCheck at error level on the shell scripts (`d4r_proton_env.sh` gets a `# shellcheck shell=bash`
  directive because it is sourced), and a Python syntax check
- Builds the NGX shim (MinGW-w64 + clang-cl) and the Wine CUDA bridge (winegcc)

The native kernels need ROCm and are not built in CI.

### Testing

- 19 tests pass (11 existing + 8 new); ShellCheck reports no errors; actionlint passes on the workflow.
- Both CI jobs were run locally on Ubuntu 24.04 with the same packages: the shim and the bridge build
  (the bridge shows only `-Wformat-truncation` warnings, as before).
- Not tested on a real GPU yet. I have an RX 9070 XT and plan to report RDNA4 results separately.

---
🤖 Generated with [Claude Code](https://claude.com/claude-code)
