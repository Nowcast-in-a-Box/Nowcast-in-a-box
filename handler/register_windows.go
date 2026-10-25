//go:build windows

package handler

import (
	"fmt"
	"os"
	"os/exec"
)

func registerInstall(cfg Config) error {
	if os.Getenv("NIB_SKIP_URI_REGISTRATION") == "1" || cfg.Source == "" {
		return nil
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	command := fmt.Sprintf("\"%s\" connect -config \"%s\" \"%%1\"", executable, cfg.Source)
	for _, args := range [][]string{
		{"add", `HKCU\Software\Classes\nib`, "/v", "URL Protocol", "/d", "", "/f"},
		{"add", `HKCU\Software\Classes\nib\shell\open\command`, "/ve", "/d", command, "/f"},
	} {
		if out, err := exec.Command("reg.exe", args...).CombinedOutput(); err != nil {
			return fmt.Errorf("register Handler URI: %w: %s", err, out)
		}
	}
	return nil
}
