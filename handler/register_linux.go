//go:build !darwin && !windows

package handler

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strconv"
	"strings"
)

func registerInstall(cfg Config) error {
	if os.Getenv("NIB_SKIP_URI_REGISTRATION") == "1" || cfg.Source == "" {
		return nil
	}
	executable, err := os.Executable()
	if err != nil {
		return err
	}
	root := os.Getenv("XDG_DATA_HOME")
	if root == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return err
		}
		root = filepath.Join(home, ".local", "share")
	}
	root = filepath.Join(root, "applications")
	if err := os.MkdirAll(root, 0700); err != nil {
		return err
	}
	// Desktop Exec uses its own quoting, not a shell command.
	body := fmt.Sprintf("[Desktop Entry]\nType=Application\nName=NiB Handler\nExec=%s connect -config %s %%u\nMimeType=x-scheme-handler/nib;\nNoDisplay=true\n", strconv.Quote(strings.ReplaceAll(executable, "%", "%%")), strconv.Quote(strings.ReplaceAll(cfg.Source, "%", "%%")))
	if err := os.WriteFile(filepath.Join(root, "nib-handler.desktop"), []byte(body), 0600); err != nil {
		return err
	}
	if tool, err := exec.LookPath("xdg-mime"); err == nil {
		if out, err := exec.Command(tool, "default", "nib-handler.desktop", "x-scheme-handler/nib").CombinedOutput(); err != nil {
			return fmt.Errorf("register Handler URI: %w: %s", err, out)
		}
	}
	return nil
}
