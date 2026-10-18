//go:build !darwin

package handler

import "fmt"

// Other platforms use foreground processes, not a registered background job.
func backgroundProcessExited(Config, int) bool { return true }

func startBackground(Config) error {
	return fmt.Errorf("background Handler startup is only implemented for macOS in this release; use the platform foreground launcher")
}
