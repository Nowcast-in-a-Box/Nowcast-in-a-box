package handler

import (
	"bytes"
	"encoding/json"
)

// containerJSON drops the stderr noise that CombinedOutput merges into a
// container's single-line JSON reply, such as Docker's platform warning when
// an arm64 host runs an amd64 image. It returns out unchanged when nothing
// parses, so callers still report the original failure.
func containerJSON(out []byte) []byte {
	if json.Valid(bytes.TrimSpace(out)) {
		return out
	}
	lines := bytes.Split(out, []byte("\n"))
	for index := len(lines) - 1; index >= 0; index-- {
		line := bytes.TrimSpace(lines[index])
		if len(line) > 0 && line[0] == '{' && json.Valid(line) {
			return line
		}
	}
	return out
}
