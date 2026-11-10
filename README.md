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

Usage
-----

Install the release under `compatibilitytools.d` and select **Proton-ZLUDA** as
the game's compatibility tool. For the configured titles below, CUDA translation
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


Upstream Proton README
======================

Introduction
------------

**Proton** is a tool for use with the Steam client which allows games which are
exclusive to Windows to run on the Linux operating system. It uses Wine to
facilitate this.

**Most users should use Proton provided by the Steam Client itself.** See
[this Steam Community post][steam-play-introduction] for more details.

The source code is provided to enable advanced users the ability to alter
Proton. For example, some users may wish to use a different version of Wine
with a particular title.

**The changelog** is available on [our wiki][changelog].

[steam-play-introduction]: https://steamcommunity.com/games/221410/announcements/detail/1696055855739350561
[changelog]: https://github.com/ValveSoftware/Proton/wiki/Changelog


Obtaining Proton sources
------------------------

Acquire Proton's source by cloning <https://github.com/ValveSoftware/Proton>
and checking out the branch you desire.

You can clone the latest Proton to your system with this command:

```bash
git clone --recurse-submodules https://github.com/ValveSoftware/Proton.git proton
```

Be sure to update submodules when switching between branches:

```bash
git checkout experimental_6.3
git submodule update --init --recursive
```

If you want to change any subcomponent, now is the time to do so. For
example, if you wish to make changes to Wine, you would apply them to the
`wine/` directory.


Building Proton
---------------

Most of Proton builds inside the Proton SDK container with very few
dependencies on the host side.

## Preparing the build environment

You need either a Docker or a Podman setup which Proton's build system uses
internally. You should never need to use either container engine manually unless
working on those parts of build system directly.

We highly recommend [the rootless Podman setup][rootless-podman]. Please refer
to your distribution's documentation for setup instructions (e.g. Arch
[Podman][arch-podman] / [Docker][arch-docker], Debian [Podman][debian-podman] /
[Docker][debian-docker]).

[rootless-podman]: https://github.com/containers/podman/blob/main/docs/tutorials/rootless_tutorial.md
[arch-podman]: https://wiki.archlinux.org/title/Podman
[arch-docker]: https://wiki.archlinux.org/title/Docker
[debian-podman]: https://wiki.debian.org/Podman
[debian-docker]: https://wiki.debian.org/Docker


## The Easy Way

We provide a top-level Makefile which will execute most of the build commands
for you.

After checking out the repository and updating its submodules, assuming that
you have a working Docker or Podman setup, you can build and install Proton
with a simple:

```bash
make install
```

If your build system is missing dependencies, it will fail quickly with a clear
error message.

After the build finishes, you may need to restart the Steam client to see the
new Proton tool. The tool's name in the Steam client will be based on the
currently checked out branch of Proton. You can override this name using the
`build_name` variable.

See `make help` for other build targets and options.



## Manual building

### Configuring the build

```bash
mkdir ../build && cd ../build
../proton/configure.sh --enable-ccache --build-name=my_build
```

Running `configure.sh` will create a `Makefile` allowing you to build Proton.
The scripts checks if containers are functional and prompt you if any
host-side dependencies are missing. You should run the command from a
directory created specifically for your build.

The configuration script tries to discover a working Docker or Podman setup
to use, but you can force a compatible engine with
`--container-engine=<executable_name>`.

You can enable ccache with `--enable-cache` flag. This will mount your
`$CCACHE_DIR` or `$HOME/.ccache` inside the container.

`--proton-sdk-image=registry.gitlab.steamos.cloud/proton/soldier/sdk:<version>`
can be used to build with a custom version of the Proton SDK images.

Check `--help` for other configuration options.

NOTE: If **SELinux** is in use, the Proton build container may fail to access
your user's files. This is caused by [SELinux's filesystem
labels][selinux-labels]. You may pass the `--relabel-volumes` switch to
configure to cause the [container engine to relabel its
bind-mounts][bind-mounts] and allow access to those files from within the
container. This can be dangerous when used with system directories. Proceed
with caution and refer your container engine's manual.

[selinux-labels]: https://access.redhat.com/documentation/en-us/red_hat_enterprise_linux/6/html/security-enhanced_linux/sect-security-enhanced_linux-working_with_selinux-selinux_contexts_labeling_files
[bind-mounts]: https://docs.docker.com/storage/bind-mounts/


### Building

```
make
```

**Important make targets:**

`make install` - install Proton into your user's Steam directory, see the [install Proton
locally](#install-proton-locally) section for details.

`make redist` - create a redistribute build (`redist/`) that can be copied to
`~/.steam/root/compatibilitytools.d/`.

`make deploy` - create a deployment build (`deploy/`). This is what we use to
deploy Proton to Steam users via Steamworks.

`make module=<module> module` - build both 32- and 64-bit versions of the
specified wine module. This allows rapid iteration on one module. This target
is only useful after building Proton.

`make dxvk` / `make vkd3d-proton` - rebuild DXVK / vkd3d-proton.


### Figuring Out What Failed To Build

Proton build system invokes builds of many subprojects in parallel. If one
subprojects fails there can be thousands of lines printed by other sub-builds
before the top level exits. This can make the real reason of the build failing
hard to find.

Appending `2>&1 | tee build.log` will log the full build output to a `build.log`
file. Searching that file from the bottom up for occurrences of `Error` should
point to the right area. E.g.:

```
make 2>&1 | tee build.log
grep -n '] Error [0-9]' build.log
```

```
11220:make: *** [../Makefile.in:465: /builds/proton/proton/build-dir/.kaldi-i386-configure] Error 1
12427:make: *** [../Makefile.in:1323: deploy] Error 2
```


### Debug Builds

To prevent symbol stripping add `UNSTRIPPED_BUILD=1` to the `make`
invocation. This should be used only with a clean build directory.

E.g.:

```
mkdir ../debug-proton-build && cd ../debug-proton-build
../proton/configure.sh --enable-ccache --build-name=debug_build
make UNSTRIPPED_BUILD=1 install
```


### ARM64 Builds

You need an ARM64 build machine and pass `--target-arch=arm64` to `configure.sh`.

It's not possible to use the resulting builds in x86 Steam running via FEX.


Install Proton locally
----------------------

Steam ships with several versions of Proton, which games will use by default or
that you can select in Steam Settings' Steam Play page. Steam also supports
running games with local builds of Proton, which you can install on your
machine.

To install a local build of Proton into Steam, make a new directory in
`~/.steam/root/compatibilitytools.d/` with a tool name of your choosing and
place the directory containing your redistributable build under that path.

The `make install` target will perform this task for you, installing the
Proton build into the Steam folder for the current user. You will have to
restart the Steam client for it to pick up on a new tool.

A correct local tool installation should look similar to this:

```
compatibilitytools.d/my_proton/
├── compatibilitytool.vdf
├── filelock.py
├── LICENSE
├── proton
├── proton_dist.tar
├── toolmanifest.vdf
├── user_settings.sample.py
└── version
```

To enable your local build in Steam, go to the Steam Play section of the
Settings window. If the build was correctly installed, you should see
"proton-localbuild" in the drop-down list of compatibility tools.

Each component of this software is used under the terms of their licenses.
See the `LICENSE` files here, as well as the `LICENSE`, `COPYING`, etc files
in each submodule and directory for details. If you distribute a built
version of Proton to other users, you must adhere to the terms of these
licenses.


Debugging
---------

Proton builds have their symbols stripped by default. You can switch to
"debug" beta branch in Steam (search for Proton in your library,
Properties... -> BETAS -> select "debug") or build without stripping (see
[Debug Builds section](#debug-builds)).

The symbols are provided through the accompanying `.debug` files which may
need to be explicitly loaded by the debugging tools. For GDB there's a helper
script `wine/tools/gdbinit.py` (source it) that provides `load-symbol-files`
(or `lsf` for short) command which loads the symbols for all the mapped files.

For tips on debugging see [docs/DEBUGGING-LINUX.md](docs/DEBUGGING-LINUX.md)
and [docs/DEBUGGING-WINDOWS.md](docs/DEBUGGING-WINDOWS.md).


`compile_commands.json`
-----------------------

For use with [clangd](https://clangd.llvm.org/) LSP server and similar tooling.

Projects built using cmake or meson (e.g. vkd3d-proton) automatically come with
`compile_commands.json`. Wine also generates the file on its own via `makedep`.

Proton's build system collects all the `compile_commands.json` files in a build
subdirectory named `compile_commands/`.

The paths are translated to point to the real source (i.e. not the rsynced
copy). It still may depend on build directory for things like auto-generated
`config.h` though and for wine it may be beneficial to run `tools/make_requests`
in you source directories as those changes are not committed.

You can then configure your editor to use that file for clangd in a few ways:

1) directly - some editors/plugins allow you to specify the path to `compile_commands.json`
2) via `.clangd` file, e.g.
```bash
cd src/proton/wine/
cat > .clangd <<EOF
CompileFlags:
  CompilationDatabase: ../build/current-dev/compile_commands/wine-x86_64/
EOF
```
3) by symlinking:
```bash
ln -s ../build/current-dev/compile_commands/wine-x86_64/compile_commands.json .
```


Runtime Config Options
----------------------

Proton can be tuned at runtime to help certain games run. The Steam client sets
some options for known games using the `STEAM_COMPAT_CONFIG` variable.
You can override these options using the environment variables described below.

The best way to set these environment overrides for all games is by renaming
`user_settings.sample.py` to `user_settings.py` and modifying it appropriately.
This file is located in the Proton installation directory in your Steam library
(often `~/.steam/steam/steamapps/common/Proton #.#`).

If you want to change the runtime configuration for a specific game, you can
use the `Set Launch Options` setting in the game's `Properties` dialog in the
Steam client. Set the variable, followed by `%command%`. For example, input
"`PROTON_USE_WINED3D=1 %command%`" to use the OpenGL-based wined3d renderer
instead of the Vulkan-based DXVK renderer.

To enable an option, set the variable to a non-`0` value.  To disable an
option, set the variable to `0`. To use Steam's default configuration, do
not specify the variable at all.

All of the below are runtime options. They do not effect permanent changes to
the Wine prefix. Removing the option will revert to the previous behavior.

| Compat config string  | Environment Variable               | Description  |
| :-------------------- | :--------------------------------- | :----------- |
|                       | `PROTON_LOG`                       | Convenience method for dumping a useful debug log to `$PROTON_LOG_DIR/steam-$APPID.log`. Set to `1` to enable default logging, or set to a string to be appended to the default `WINEDEBUG` channels. |
|                       | `PROTON_LOG_DIR`                   | Output log files into the directory specified. Defaults to your home directory. |
|                       | `PROTON_WAIT_ATTACH`               | Wait for a debugger to attach to steam.exe before launching the game process. To attach to the game process at startup, debuggers should be set to follow child processes. |
|                       | `PROTON_CRASH_REPORT_DIR`          | Write crash logs into this directory. Does not clean up old logs, so may eat all your disk space eventually. |
| `wined3d`             | `PROTON_USE_WINED3D`               | Use OpenGL-based wined3d instead of Vulkan-based DXVK for d3d11, d3d10, and d3d9. |
| `nod3d11`             | `PROTON_NO_D3D11`                  | Disable `d3d11.dll`, for d3d11 games which can fall back to and run better with d3d9. |
| `nod3d10`             | `PROTON_NO_D3D10`                  | Disable `d3d10.dll` and `dxgi.dll`, for d3d10 games which can fall back to and run better with d3d9. |
| `dxvkd3d8`            | `PROTON_DXVK_D3D8`                 | Use DXVK's `d3d8.dll`. |
| `nofsync`             | `PROTON_NO_FSYNC`                  | Do not use futex-based in-process synchronization primitives. (Automatically disabled on systems with no `FUTEX_WAIT_MULTIPLE` support.) |
|                       | `PROTON_NO_NTSYNC`                 | Do not use ntsync. |
|                       | `HOST_LC_ALL`                      | Set value to a locale to override all other system locale settings for a game.  This variable should be used instead of `LC_ALL`. |
| `disablenvapi`        | `PROTON_DISABLE_NVAPI`             | Disable NVIDIA's NVAPI GPU support library. |
| `nativevulkanloader`  |                                    | Use the Vulkan loader shipped with the game instead of Proton's built-in Vulkan loader. This breaks VR support, but is required by a few games. |
| `forcelgadd`          | `PROTON_FORCE_LARGE_ADDRESS_AWARE` | Force Wine to enable the LARGE_ADDRESS_AWARE flag for all executables. Enabled by default. |
| `heapdelayfree`       | `PROTON_HEAP_DELAY_FREE`           | Delay freeing some memory, to work around application use-after-free bugs. |
| `gamedrive`           | `PROTON_SET_GAME_DRIVE`            | Create an S: drive which points to the Steam Library which contains the game. |
| `noforcelgadd`        |                                    | Disable forcelgadd. If both this and `forcelgadd` are set, enabled wins. |
| `oldglstr`            | `PROTON_OLD_GL_STRING`             | Set some driver overrides to limit the length of the GL extension string, for old games that crash on very long extension strings. |
| `vkd3dfl12`           |                                    | Force the Direct3D 12 feature level to 12, regardless of driver support. |
| `vkd3dbindlesstb`     |                                    | Put `force_bindless_texel_buffer` into `VKD3D_CONFIG`. |
| `nomfdxgiman`         | `WINE_DO_NOT_CREATE_DXGI_DEVICE_MANAGER` | Enable hack to work around video issues in some games due to incomplete IMFDXGIDeviceManager support. |
| `noopwr`              | `WINE_DISABLE_VULKAN_OPWR`               | Enable hack to disable Vulkan other process window rendering which sometimes causes issues on Wayland due to blit being one frame behind. |
| `hidenvgpu`           | `PROTON_HIDE_NVIDIA_GPU`           | Force Nvidia GPUs to always be reported as AMD GPUs. Some games require this if they depend on Windows-only Nvidia driver functionality. See also DXVK's nvapiHack config, which only affects reporting from Direct3D. |
|                       | `WINE_FULLSCREEN_INTEGER_SCALING`  | Enable integer scaling mode, to give sharp pixels when upscaling. |
|                       | `WINE_USE_KWIN_HACKS`              | Enable KDE-specific windowing hacks that may improve experience with KDE older than 6.4 on Wayland and KDE older than 6.6 on X11. |
| `cmdlineappend:`      |                                    | Append the string after the colon as an argument to the game command. May be specified more than once. Escape commas and backslashes with a backslash. |
| `xalia` or `noxalia`  | `PROTON_USE_XALIA`                 | Enable Xalia, a program that can add a gamepad UI for some keyboard/mouse interfaces, or set to 0 to disable. The default is to enable it dynamically based on window contents. |
| `fnad3d11`            | `FNA3D_FORCE_DRIVER=D3D11`         | Force FNA to use D3D11 for rendering. |
| `seccomp`             | `PROTON_USE_SECCOMP`               | **Note: Obsoleted in Proton 5.13.** In older versions, enable seccomp-bpf filter to emulate native syscalls, required for some DRM protections to work. |
| `d9vk`                | `PROTON_USE_D9VK`                  | **Note: Obsoleted in Proton 5.0.** In older versions, use Vulkan-based DXVK instead of OpenGL-based wined3d for d3d9. |
| `noesync`             | `PROTON_NO_ESYNC`                  | **Note: Obsoleted in Proton 11.0.** In older versions, do not use eventfd-based in-process synchronization primitives. |

<!-- Target:  GitHub Flavor Markdown.  To test locally:  pandoc -f markdown_github -t html README.md  -->
