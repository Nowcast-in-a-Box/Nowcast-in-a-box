//go:build windows

package handler

import "syscall"

func processExited(pid int) bool {
	handle, err := syscall.OpenProcess(syscall.SYNCHRONIZE, false, uint32(pid))
	if err != nil {
		return err == syscall.Errno(87)
	} // Invalid PID, not access denied.
	defer syscall.CloseHandle(handle)
	state, err := syscall.WaitForSingleObject(handle, 0)
	return err == nil && state == syscall.WAIT_OBJECT_0
}
