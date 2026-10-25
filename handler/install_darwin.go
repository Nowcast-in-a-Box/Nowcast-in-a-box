//go:build darwin

package handler

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

func removeInstallRegistration() error {
	home, err := os.UserHomeDir()
	if err != nil {
		return err
	}
	app := filepath.Join(home, "Applications", "NiB Handler.app")
	if _, err := os.Stat(app); os.IsNotExist(err) {
		return nil
	} else if err != nil {
		return err
	}
	tool := "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
	if out, err := exec.Command(tool, "-u", app).CombinedOutput(); err != nil {
		return fmt.Errorf("unregister Handler app: %w: %s", err, out)
	}
	return os.RemoveAll(app)
}
