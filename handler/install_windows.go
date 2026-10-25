//go:build windows

package handler

import (
	"fmt"
	"os/exec"
)

func removeInstallRegistration() error {
	if err := exec.Command("reg.exe", "query", `HKCU\Software\Classes\nib`).Run(); err != nil {
		if exit, ok := err.(*exec.ExitError); ok && exit.ExitCode() == 1 {
			return nil
		}
		return err
	}
	if out, err := exec.Command("reg.exe", "delete", `HKCU\Software\Classes\nib`, "/f").CombinedOutput(); err != nil {
		return fmt.Errorf("unregister Handler URI: %w: %s", err, out)
	}
	return nil
}
