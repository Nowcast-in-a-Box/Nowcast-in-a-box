//go:build !windows

package handler

import (
	"os"
	"strconv"
)

func containerUser() string {
	return strconv.Itoa(os.Getuid()) + ":" + strconv.Itoa(os.Getgid())
}
