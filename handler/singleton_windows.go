//go:build windows

package handler

import (
	"fmt"
	"os"
	"syscall"
	"time"
	"unsafe"
)

var lockFileEx = syscall.NewLazyDLL("kernel32.dll").NewProc("LockFileEx")
var unlockFileEx = syscall.NewLazyDLL("kernel32.dll").NewProc("UnlockFileEx")

func lockFile(path string) (func(), error) {
	f, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0600)
	if err != nil {
		return nil, err
	}
	overlapped := new(syscall.Overlapped)
	deadline := time.Now().Add(singletonWait)
	for {
		ok, _, callErr := lockFileEx.Call(f.Fd(), 3, 0, 1, 0, uintptr(unsafe.Pointer(overlapped)))
		if ok != 0 {
			return func() { unlockFileEx.Call(f.Fd(), 0, 1, 0, uintptr(unsafe.Pointer(overlapped))); f.Close() }, nil
		}
		if callErr != syscall.Errno(33) {
			f.Close()
			return nil, callErr
		}
		if time.Now().After(deadline) {
			f.Close()
			return nil, fmt.Errorf("another Handler lifecycle operation is active")
		}
		time.Sleep(50 * time.Millisecond)
	}
}
