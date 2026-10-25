//go:build !darwin && !windows

package handler

import (
	"errors"
	"os"
	"path/filepath"
)

func removeInstallRegistration() error {
	root := os.Getenv("XDG_DATA_HOME")
	if root == "" {
		home, err := os.UserHomeDir()
		if err != nil {
			return err
		}
		root = filepath.Join(home, ".local", "share")
	}
	err := os.Remove(filepath.Join(root, "applications", "nib-handler.desktop"))
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	return err
}
