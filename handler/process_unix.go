//go:build !windows

package handler

import (
	"errors"
	"syscall"
)

func processExited(pid int) bool {
	return errors.Is(syscall.Kill(pid, 0), syscall.ESRCH)
}
