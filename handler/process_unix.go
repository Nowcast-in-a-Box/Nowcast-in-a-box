//go:build !windows

package handler

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"regexp"
	"strconv"
	"strings"
	"syscall"
)

func processExited(pid int) bool {
	return errors.Is(syscall.Kill(pid, 0), syscall.ESRCH)
}

func terminateProcess(process *os.Process) error {
	return process.Signal(syscall.SIGTERM)
}

// listenerPID finds the process listening on a loopback port so an
// unauthenticated Handler can still be stopped.
func listenerPID(port int) (int, error) {
	if lsof, err := exec.LookPath("lsof"); err == nil {
		output, err := lsofOutput(lsof, port)
		if err == nil {
			return output, nil
		}
	}
	if ss, err := exec.LookPath("ss"); err == nil {
		out, err := exec.Command(ss, "-ltnpH", "sport = :"+strconv.Itoa(port)).Output()
		if err == nil {
			if pid := ssPID(string(out)); pid > 0 {
				return pid, nil
			}
		}
	}
	return 0, fmt.Errorf("cannot identify the process on port %d; install lsof or stop it manually", port)
}

func lsofOutput(lsof string, port int) (int, error) {
	out, err := exec.Command(lsof, "-nP", "-iTCP:"+strconv.Itoa(port), "-sTCP:LISTEN", "-t").Output()
	if err != nil {
		return 0, err
	}
	for _, field := range strings.Fields(string(out)) {
		if pid, err := strconv.Atoi(field); err == nil && pid > 0 {
			return pid, nil
		}
	}
	return 0, fmt.Errorf("no listening process reported for port %d", port)
}

var ssPidPattern = regexp.MustCompile(`pid=(\d+)`)

func ssPID(output string) int {
	match := ssPidPattern.FindStringSubmatch(output)
	if match == nil {
		return 0
	}
	pid, _ := strconv.Atoi(match[1])
	return pid
}
