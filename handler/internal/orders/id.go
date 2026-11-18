package orders

import (
	"regexp"
	"strings"
)

// File names must be safe on every platform: no separators or colons, no
// leading dot, no trailing dot/space, and no Windows-reserved stems. Hub ids
// are lowercase by contract, but the manifest grammar is not enforced here.
var idPattern = regexp.MustCompile(`^[A-Za-z0-9_-]+$`)

var packageIDPattern = regexp.MustCompile(`^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$`)

var windowsReservedNames = map[string]bool{
	"con": true, "prn": true, "aux": true, "nul": true,
	"com1": true, "com2": true, "com3": true, "com4": true, "com5": true,
	"com6": true, "com7": true, "com8": true, "com9": true,
	"lpt1": true, "lpt2": true, "lpt3": true, "lpt4": true, "lpt5": true,
	"lpt6": true, "lpt7": true, "lpt8": true, "lpt9": true,
}

func ValidID(id string) bool {
	if !packageIDPattern.MatchString(id) || strings.HasSuffix(id, ".") || strings.HasSuffix(id, " ") {
		return false
	}
	stem, _, _ := strings.Cut(strings.ToLower(id), ".")
	return !windowsReservedNames[stem]
}
