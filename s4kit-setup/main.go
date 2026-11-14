// S4Kit-Setup.exe: runs the embedded setup_s4kit.ps1 in a visible PowerShell console.
package main

import (
	_ "embed"
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
)

//go:embed setup_s4kit.ps1
var script []byte

func pause(code int) {
	fmt.Print("\nPress Enter to close this window...")
	bufio.NewReader(os.Stdin).ReadString('\n')
	os.Exit(code)
}

func main() {
	fmt.Println("S4Kit setup - installs the S4Kit MCP server and Blender add-on.")
	dir := filepath.Join(os.Getenv("USERPROFILE"), "S4Kit")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		fmt.Println("Cannot create", dir, ":", err)
		pause(1)
	}
	ps1 := filepath.Join(dir, "setup_s4kit.ps1")
	// UTF-8 BOM so Windows PowerShell 5.1 reads the file correctly.
	if err := os.WriteFile(ps1, append([]byte{0xEF, 0xBB, 0xBF}, script...), 0o644); err != nil {
		fmt.Println("Cannot write", ps1, ":", err)
		pause(1)
	}
	cmd := exec.Command("powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1)
	cmd.Dir = dir
	cmd.Stdin, cmd.Stdout, cmd.Stderr = os.Stdin, os.Stdout, os.Stderr
	err := cmd.Run()
	code := 0
	if err != nil {
		fmt.Println("\nSetup stopped with an error:", err)
		code = 1
	} else {
		fmt.Println("\nSetup finished.")
	}
	pause(code)
}
