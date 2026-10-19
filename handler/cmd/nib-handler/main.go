// Command nib-handler is the host daemon for Nowcast-in-a-Box.
package main

import (
	"os"

	"github.com/Nowcast-in-a-Box/Nowcast-in-a-box/handler"
)

func main() {
	os.Exit(handler.Main(os.Args[1:]))
}
