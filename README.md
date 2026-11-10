Proton-ZLUDA
============

**Proton-ZLUDA** is Valve's Proton 11.0 with [ZLUDA][zluda] built in. It runs the
CUDA parts of Windows games, mostly GPU PhysX and APEX effects, on AMD GPUs.
The work is developed and tested on the Steam Deck.

Fork components:
- [ZLUDA][zluda-fork]: the CUDA backend. It is upstream ZLUDA plus
  [two runtime/compiler commits][zluda-base] and the
  [legacy PTX series][zluda-legacy].
- [ROCm LLVM][llvm-smem]: the LLVM ZLUDA compiles with, plus one AMDGPU fix.
- [zluda-wine][zluda-wine]: the Wine `nvcuda` bridge for 32-bit and 64-bit games.
- Three patches applied at build time:
  - [Wine `ntdll`][wine-steamclient];
  - [dxvk-nvapi 1][nvapi-allow];
  - [dxvk-nvapi 2][nvapi-v2].

Everything else is Valve's Proton (base `bc896217`).

[zluda]: https://github.com/vosen/ZLUDA
[zluda-fork]: https://github.com/chkuendig/ZLUDA
[zluda-base]: https://github.com/vosen/ZLUDA/compare/f4df215edbc3b6698a1c61ef7a8688fdff3872be...chkuendig:ZLUDA:5220554de03a471de81a895e7b0dce3d10cf6f1c
[zluda-runtime]: https://github.com/chkuendig/ZLUDA/commit/806d3392fd7b3d2f9632692075857aec68e8e9c9
[zluda-contexts]: https://github.com/chkuendig/ZLUDA/commit/5220554de03a471de81a895e7b0dce3d10cf6f1c
[zluda-legacy]: https://github.com/chkuendig/ZLUDA/compare/5220554de03a471de81a895e7b0dce3d10cf6f1c...39ef5ce92bb7c2363ba756b3316b8cfce65d5b65
[llvm-smem]: https://github.com/chkuendig/llvm-project/commit/c83b929aeff0aa6b9877c6712e9914d1275b1d36
[zluda-wine]: https://github.com/chkuendig/zluda-wine
[zluda-wine-pe32]: https://github.com/chkuendig/zluda-wine/compare/be3ba09a0a982ab8625cbe0b1620768c9294e9b5...bf80022f07cf66fbec96445cf77c12d2081c32ee
[zluda-wine-dataex]: https://github.com/chkuendig/zluda-wine/commit/e35df245
[zluda-wine-interop]: https://github.com/chkuendig/zluda-wine/commit/ba87c199
[wine-steamclient]: patches/wine/0001-ntdll-restore-thread-state-after-steamclient-fault.patch
[nvapi-allow]: patches/dxvk-nvapi/0001-zluda-physx-honor-DXVK_NVAPI_ALLOW_OTHER_DRIVERS-in-.patch
[nvapi-v2]: patches/dxvk-nvapi/0002-Handle-the-legacy-PhysX-compute-topology-V2-query-Ar.patch

Install
-------

Download `Proton-ZLUDA-<commit>.tar.gz` from [Releases][releases] and extract it
into Steam's `compatibilitytools.d`:

```sh
# Steam Deck and native Steam
mkdir -p ~/.steam/root/compatibilitytools.d
tar -xzf Proton-ZLUDA-*.tar.gz -C ~/.steam/root/compatibilitytools.d/
# Flatpak Steam: ~/.var/app/com.valvesoftware.Steam/data/Steam/compatibilitytools.d/
```

Restart Steam. Then, per game: Properties → Compatibility → "Force the use of a
specific Steam Play compatibility tool" → **Proton-ZLUDA-<commit>**. Each build
has its own tool name, so several can be installed side by side.

Proton managers (ProtonPlus, ProtonUp-Qt, Protonup-rs) ship fixed lists of
tools and cannot install Proton-ZLUDA yet.

The first pre-release, `proton-zluda-11.0-20261108-799caed8`, predates this layout:
its archive has no top-level folder and its tool name is `Proton-ZLUDA`. Extract
it into a new folder `compatibilitytools.d/Proton-ZLUDA` instead.

[releases]: https://github.com/chkuendig/Proton-ZLUDA/releases

Usage
-----

For the configured titles below, CUDA translation
turns on by default on systems with an AMD GPU and no NVIDIA GPU. `PROTON_ENABLE_ZLUDA=1` forces it on and `0` turns it off;
explicit settings always win. With ZLUDA on, Proton enables NVAPI with
`DXVK_NVAPI_ALLOW_OTHER_DRIVERS=1`. On the Steam Deck it also sets
`HSA_OVERRIDE_GFX_VERSION`. The game fixes below apply automatically. The one
exception is Just Cause 2's D3D10 interop, which needs the launch option
`ZLUDA_WINE_INTEROP=1 %command%`.

The launcher uses private builtin copies in each prefix (`PROTON_DLL_COPY=*`),
which take additional prefix disk space.

Tested games
------------

Tested on a Steam Deck. **WIP** marks titles that do not run with GPU PhysX yet.

| Game | App ID | Status |
|---|---|---|
| Batman: Arkham Asylum | 35140 | Works: cloth banners, smoke |
| Batman: Arkham City | 200260 | Works: GPU PhysX in game |
| Batman: Arkham Origins | 209000 | **WIP**: menu and cinematic, no GPU PhysX yet |
| Batman: Arkham Knight | 208650 | Works: smoke, paper, debris |
| Borderlands 2 | 49520 | Works: PhysX High fluids |
| FluidMark (PhysX benchmark) | – | Works: GPU fluid simulation |
| Just Cause 2 | 8190 | Works: Bokeh filter and GPU water |
| Mafia II | 50130 | Works: APEX High debris |
| Mirror's Edge | 17410 | Works: cloth, glass, debris |
| The Bureau: XCOM Declassified | 65930 | **WIP**: exits before CUDA starts |

### Game notes

**Batman: Arkham Asylum**
- [ROCm LLVM][llvm-smem]: cloth exploded because AMDGPU scalar memory loads
  were emitted with an unaligned SGPR offset in PhysX's
  `KernelDeformableUberCollision`.
- [dxvk-nvapi 2][nvapi-v2]: PhysXDevice 2.8.4 asks for the legacy V2 compute
  topology, including through the private entry point `0x36e39e6b`; both are
  answered.
- Title setup: WoW64 mode, plus `d3dx9`, `d3dcompiler_43` and `physx` installed
  on first launch.
- On Steam Deck, `AMD_CU_MASK=0x3f %command%` is an optional Steam launch
  setting that limits RADV to six compute units. In the uncapped built-in
  benchmark with PhysX High, average FPS rose from 71.25 to 82.63 on Valve
  Proton `11.0-2e` with the `zluda-wine` bridge.

**Batman: Arkham City**
- Needs the PhysX System Software. The launcher has mouse-only buttons.

**Batman: Arkham Origins (WIP)**
- The PhysX and APEX modules load from the system PhysX runtime.
- GPU PhysX needs 32-bit `cuModuleLoadDataEx` with JIT options
  ([zluda-wine][zluda-wine-dataex]), which is still being tested.

**Batman: Arkham Knight**
- [ZLUDA][zluda-runtime]: a generic PTX pass inserts barriers for shared-memory
  write-after-read races. Knight's CUB 1.0.2 radix-sort downsweep lacks the
  `CTA_SYNC` that NVIDIA added in CUB 1.8.0; on AMD this faulted the GPU. The
  pass also replaces "precise memory" mode, which only masked the race and cost
  about 4 % of frame rate.
- [ZLUDA][zluda-runtime]: legacy texture references keep cached bindings instead
  of rebinding with host-blocking copies (about +13 % average frame rate on the
  Deck). A bound array stays alive until no texture reference shows it.
- [ZLUDA][zluda-runtime]: HIP's legacy `hipTexRefSetArray` overwrote half-float
  array views with UINT16, which broke the smoke; the format is now overridden
  explicitly.
- [dxvk-nvapi 1][nvapi-allow]: `NvAPI_GPU_CudaEnumComputeCapableGpus` honours
  `DXVK_NVAPI_ALLOW_OTHER_DRIVERS`. Without it, PhysXDevice64 greys out the
  in-game PhysX option.
- Title setup: `physx` on first launch.
- D3D11 interop is off by default; see [D3D interop findings](#d3d-interop-findings).

**Borderlands 2**
- [zluda-wine][zluda-wine]: the 32-bit bridge was missing the cache and
  shared-memory configuration hints. `cuCtxSetCacheConfig` returned "not
  supported" and PhysX stopped after about 260 kernels at High. The hints are
  now accepted as no-ops.
- Title setup: WoW64 mode, `physx` on first launch. `Launcher.exe` is replaced
  with `Borderlands2.exe` and `-NoSplash` is added, so the game starts directly.

**FluidMark**
- [ZLUDA][zluda-runtime]: the same barrier pass also covers shared-memory
  write-after-write races. FluidMark's PhysX runtime (`PhysXCore.dll` 2.8.4.12,
  CUDPP `scan4`) zero-pads and writes per-warp totals to shared memory without a
  barrier between them, which faulted the GPU on AMD.

**Just Cause 2**
- [ZLUDA legacy PTX series][zluda-legacy]: the game ships cudart 2.3 kernels
  (PTX 1.4, `sm_10`). The series adds or changes:
  - legacy texture declarations and signed selections;
  - pitched memory bound to legacy texture references;
  - typed globals in 32-bit modules;
  - truncated 16-bit special-register reads;
  - no Windows-only scalar-cache kernel prelude;
  - removal of functions unreachable from kernels;
  - padded 2D and 1D texture coordinates;
  - the legacy `map_f64_to_f32` target option.
- [zluda-wine][zluda-wine-pe32]: 32-bit forwarding of legacy modules, kernel
  launches and pitched texture bindings. Also the D3D10 entry points
  (`cuD3D10GetDevice`, `cuD3D10CtxCreate`, resource registration and mapping).
- Launch option `ZLUDA_WINE_INTEROP=1 %command%` for the Bokeh filter and GPU
  water; see [D3D interop findings](#d3d-interop-findings).

**Mafia II**
- [Wine `ntdll`][wine-steamclient]: CEG titles run with `WINESTEAMNOEXEC=1`, so
  every call into the game's `steamclient.dll` faults and is redirected to
  lsteamclient. In WoW64 mode the redirect skipped restoring the 32-bit `%fs`
  selector, and the game crashed on its first TEB access. The patch restores the
  thread state.
- Title setup: WoW64 mode, `d3dx9` on first launch.

**Mirror's Edge**
- Title setup: WoW64 mode, plus `d3dx9`, `d3dcompiler_43` and `physx` on first
  launch. The PhysX DLLs bundled with the game are moved aside so the installed
  PhysX runtime is used.

**The Bureau: XCOM Declassified (WIP)**
- The game exits before any CUDA call. This also happens with stock Proton.
- If it exits at start-up under every Proton version, run "Verify integrity of
  game files" first: a damaged CEG executable was found that way once.

**All games**
- [ZLUDA][zluda-runtime]: runtime-owned primary contexts keep their flags and
  retain counts.
- [ZLUDA][zluda-contexts]: destroying a primary context is rejected, and
  destroyed handles are detached.
- [ZLUDA][zluda-runtime]: native calls preserve Wine's signal state.
- [ZLUDA][zluda-runtime] Steam Deck defaults: `HSA_ENABLE_SDMA=0` and
  `GPU_MAX_HW_QUEUES=2`.

### D3D interop findings

Arkham Knight (D3D11) and Just Cause 2 (D3D10) share Direct3D resources with
CUDA. ROCm 7.2 on Linux has no route for zero-copy sharing:
- External semaphore import is not implemented.
- dma-buf import into a HIP array ignores the Vulkan driver's (RADV) tiling.

zluda-wine therefore implements interop as a CPU copy. It is
[off by default][zluda-wine-interop]; `ZLUDA_WINE_INTEROP=1` enables it for
D3D10 and D3D11.

**Arkham Knight, D3D11.** APEX treats interop as optional: when registration
fails, it falls back to its own non-interop `writeBuffer()` path.

| | Copy interop on | Off (default) |
|---|---|---|
| Smoke and paper debris | Rendered | Rendered |
| Average FPS | Baseline | +2.4 % |
| 1 % lows | Baseline | +11 % |

The copy's cost was not the bytes moved. About 83 % of it was waiting for the
D3D map while holding the game's `ID3D10Multithread` lock, around 15 ms per
frame.

**Just Cause 2, D3D10.** The game uses the D3D10 interop API directly for the
Bokeh filter and GPU water. With `ZLUDA_WINE_INTEROP=1` the whole chain
succeeds:
- device and context creation;
- 4 resource registrations;
- 15,824 map/unmap pairs in one run;
- 67,252 kernel launches in the same run.

The water simulation also has a host-copy fallback in the game itself.

Building
--------

This branch builds the complete ZLUDA tool with Proton's own build system.
After `git submodule update --init --recursive`, hydrate only the Linux bitcode:
`git -C zluda lfs pull --include='llvm_zluda/src/device-libs/ockl.bc,ptx/lib/*.bc'`.
Run `make -j3 build_name=Proton-ZLUDA zluda`, followed by
`make -j3 build_name=Proton-ZLUDA redist`; the output includes the native backend,
ROCm runtime and the matching Wine bridge. Downloads are pinned in
`make/zluda-deps.json`; no separate packaging checkout is needed. An out-of-tree
`configure.sh --build-name=Proton-ZLUDA` followed by `make -j3 zluda` and then
`make -j3 redist` also works. Build the native component first to keep its
three compiler jobs separate from Proton's other parallel targets.
Budget 3–5 hours for a cold build and about 50 GiB of free space after the SDK pull.
The feature workflow builds with three jobs, runs `make zluda-smoke`, and
executes the installed launcher through `make zluda-launcher-smoke`.
Off-mode CUDA stubs come from the same Wine build, with their complete module
sources checked against pinned Valve hashes. Installation verifies Wine's own
output after Proton's normal strip step. The manifest binds both architectures
and private prefix copies to those build hashes.

Steam supplies its usual external media libraries; CPU CI uses the FFmpeg libraries
already built by Proton as a private test dependency. GPU operation and the real
Steam handshake require a Steam Deck check.


Upstream Proton
---------------

Everything not described above is Valve's Proton. For its introduction, the
general build environment, debugging and runtime options, see Valve's README at
the [base commit][valve-readme].

[valve-readme]: https://github.com/ValveSoftware/Proton/blob/bc896217ffcd67f892f952b010f898fe7bd0f0a1/README.md
