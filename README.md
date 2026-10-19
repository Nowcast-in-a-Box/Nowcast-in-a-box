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
4. Start the handler
   - Run the platform launcher once to register local application links. On macOS it starts a background Handler; Hub can then open or reuse it through `nib://`.
   - Depending on your operating system, choose the right one.
   - For more details, check [here](#Run the handler)
5. Open the NiB Hub, enjoy!



## Run the handler

### Windows

```powershell
bin/windows/run.ps1
```

### Linux

```sh
bin/linux/run.sh
```

### macOS

On macOS, double-click `bin/macos/start.command` in Finder, or run it from a terminal.
It remains running after Terminal closes, without enabling login autostart.
Use `start.command status`, `stop`, or `restart` for explicit control.
See [Handler lifecycle and connection](docs/handler-lifecycle.md) for configured
ports, trusted Hub origins, logs and platform limitations.









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
