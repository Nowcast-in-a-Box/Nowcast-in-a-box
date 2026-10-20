![nib-logo](https://avatars.githubusercontent.com/u/318600662?s=200&v=4)

# Nowcast-in-a-Box

**Nowcast-in-a-Box (NiB)** supports local deployment and operation of AI nowcasting services. It provides a common mechanism for adaptation, deployment, local execution, and post-processing, reducing the effort needed to connect local data, configure model environments, and use nowcasting products.



## Usage

The [CMA Radar / NowcastNet reference demo](docs/cma-demo.md) describes the July
2026 dataset, 3 km regional grid, local weights and complete Hub workflow.

Follow the step-by-step guidance down below:



1. Where would you like to go for a run?
   - Cloud server: cloud execution is not implemented in this release.
   - Local machine: follow the next steps.
2. Install the Docker engine.
   - Follow Docker's [official documentation](https://docs.docker.com/engine/install/).
   - If you encounter any problems, feel free to open an issue or contact contributors.
3. Go to the Release page and download the latest version of NiB
   - [Releases](https://github.com/Nowcast-in-a-Box/Nowcast-in-a-box/releases)
4. Start the handler from the downloaded folder. No terminal command or environment variable is needed.
   - macOS: double-click `Start NiB.command`.
   - Windows: double-click `Start NiB.cmd`.
   - Linux: run `Start NiB.sh`.
   - The first run registers the local `nib://` link. The Handler reads `config.yaml` next to the launcher; it does not need a dataset in that file. Hub then opens or reuses the Handler through `nib://`.
   - For more details, check [here](#Run the handler)
5. Open the NiB Hub, enjoy!



## Run the handler

### Windows

Double-click `Start NiB.cmd` in the downloaded folder, or run:

```powershell
bin/windows/run.ps1
```

### Linux

Run `Start NiB.sh`, or:

```sh
bin/linux/run.sh
```

### macOS

Double-click `Start NiB.command` in Finder (or `bin/macos/start.command`), or run it from a terminal.
It remains running after Terminal closes, without enabling login autostart.
Use `bin/macos/start.command status`, `stop`, or `restart` for explicit control.
See [Handler lifecycle and connection](docs/handler-lifecycle.md) for configured
ports, trusted Hub origins, logs and platform limitations.

### Remove the local Handler

To return this computer to a fresh-user state, remove the local Handler
installation with the matching uninstaller:

- macOS: double-click `Uninstall NiB.command`
- Windows: double-click `Uninstall NiB.cmd`
- Linux: run `Uninstall NiB.sh`

By default the uninstaller stops the Handler and removes the macOS app, launchd
job, URL-scheme registration, local cache, `nib-settings.json`, and Handler log.
It keeps `runs/`, `artifacts/`, and your data folder for inspection. Add
`--dry-run` to preview the paths, or `--purge` to remove generated runs,
artifacts, resources, logs, and downloaded Handler binaries as well. `--purge`
never removes the external data folder.

The root `config.yaml` holds host settings only: handler, review and diagnostics
viewer addresses, paths, trusted Hub origins and the Atlas revision. It never
selects a dataset or container. The Hub chooses the dataset from the packaged
`runtime.yaml` manifests.









## Contribution

If you are interested in contributing to NiB, please read CONTRIBUTING.md first.





[docs]: docs/
[contrib]: CONTRIBUTING.md
[gh]: docs/github-setup.md
[dev]: docs/development.md
[collab]: docs/collaboration.md
[modules]: docs/modules.yaml
[weights]: docs/weights.md
[go]: https://go.dev/dl/
[releases]: https://github.com/Nowcast-in-a-Box/Nowcast-in-a-box/releases

## Credits

We sincerely appreciate the following works.

- [MLCast](https://mlcast.org/)
- [NCAR-MILES](https://github.com/NCAR/miles-credit/)
- [NVIDIA-Earth2Studio](https://nvidia.github.io/earth2studio/main/)
