package handler

import (
	"fmt"
	"os/exec"
	"runtime"
	"strings"
)

func nativePickFolder() (string, error) {
	var command *exec.Cmd
	switch runtime.GOOS {
	case "darwin":
		command = exec.Command("osascript", "-e", `POSIX path of (choose folder with prompt "Choose the local data directory")`)
	case "windows":
		// shortcut: this dialog needs an interactive desktop, upgrade if a service host must choose folders.
		command = exec.Command("powershell", "-NoProfile", "-STA", "-Command", `Add-Type -AssemblyName System.Windows.Forms; $dialog = New-Object System.Windows.Forms.FolderBrowserDialog; $dialog.Description = 'Choose the local data directory'; if ($dialog.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 1 }; Write-Output $dialog.SelectedPath`)
	default:
		command = exec.Command("zenity", "--file-selection", "--directory", "--title=Choose the local data directory")
	}
	out, err := command.Output()
	if err != nil {
		if _, ok := err.(*exec.ExitError); ok {
			return "", fmt.Errorf("folder selection was canceled")
		}
		return "", fmt.Errorf("folder picker is not available on this computer")
	}
	path := strings.TrimSpace(string(out))
	if path == "" {
		return "", fmt.Errorf("folder selection was canceled")
	}
	return path, nil
}
