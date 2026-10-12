![nib-logo](https://avatars.githubusercontent.com/u/318600662?s=200&v=4)

# Nowcast-in-a-Box

**Nowcast-in-a-Box (NiB)** supports local deployment and operation of AI nowcasting services. It provides a common mechanism for adaptation, deployment, local execution, and post-processing, reducing the effort needed to connect local data, configure model environments, and use nowcasting products.



## Usage

Follow the step-by-step guidance down below:



1. Where would you like to go for a run?
   - Cloud server: no need to download the NiB framework; just open the NiB Hub.
   - Local machine: follow the next steps.
2. Install the Docker engine.
   - Follow Docker's [official documentation](https://docs.docker.com/engine/install/).
   - If you encounter any problems, feel free to open an issue or contact contributors.
3. Go to the Release page and download the latest version of NiB
   - [Releases](https://github.com/Nowcast-in-a-Box/Nowcast-in-a-box/releases)
4. Start the handler
   - You only need to start the handler once; next time, NiB Hub will start it automatically.
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

Double-click `bin/macos/start.command` in Finder, or run it from a terminal.
The entry points use a prebuilt handler; users do not install Go.
The scripts under `scripts/` forward to these entries.









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
