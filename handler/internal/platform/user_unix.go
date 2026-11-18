//go:build !windows

package platform

import (
	"os"
	"strconv"
)

func ContainerUser() string {
	return strconv.Itoa(os.Getuid()) + ":" + strconv.Itoa(os.Getgid())
}
