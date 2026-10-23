//go:build windows

package handler

import (
	"fmt"
	"os"
	"os/exec"
	"strconv"
	"strings"
	"syscall"
)

func processExited(pid int) bool {
	handle, err := syscall.OpenProcess(syscall.SYNCHRONIZE, false, uint32(pid))
	if err != nil {
		return err == syscall.Errno(87)
	} // Invalid PID, not access denied.
	defer syscall.CloseHandle(handle)
	state, err := syscall.WaitForSingleObject(handle, 0)
	return err == nil && state == syscall.WAIT_OBJECT_0
}

// Windows has no SIGTERM; os.Process.Signal only supports Kill here.
func terminateProcess(process *os.Process) error {
	return process.Kill()
}

// listenerPID finds the process listening on a loopback port so an
// unauthenticated Handler can still be stopped.
func listenerPID(port int) (int, error) {
	out, err := exec.Command("netstat", "-ano", "-p", "tcp").Output()
	if err != nil {
		return 0, fmt.Errorf("cannot identify the process on port %d: %w", port, err)
	}
	suffix := ":" + strconv.Itoa(port)
	for _, line := range strings.Split(string(out), "\n") {
		fields := strings.Fields(line)
		if len(fields) < 5 || fields[3] != "LISTENING" || !strings.HasSuffix(fields[1], suffix) {
			continue
		}
		if pid, err := strconv.Atoi(fields[len(fields)-1]); err == nil && pid > 0 {
			return pid, nil
		}
	}
	return 0, fmt.Errorf("no listening process reported for port %d", port)
}
