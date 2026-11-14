// S4Kit-Register.exe: registers an already-installed S4Kit with Claude Desktop and
// Claude Code (user scope), then prints the S4Kit environment report.
// Backs up each config file first and changes only the "s4kit" entry.
package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"time"
)

func pause(code int) {
	fmt.Print("\nPress Enter to close this window...")
	bufio.NewReader(os.Stdin).ReadString('\n')
	os.Exit(code)
}

func exists(p string) bool { _, err := os.Stat(p); return err == nil }

func findKit(home string) string {
	cands := []string{filepath.Join(home, "S4Kit", "LibreTV", "sims4-creator-kit")}
	m, _ := filepath.Glob(filepath.Join(home, "S4Kit", "zip", "*", "sims4-creator-kit"))
	cands = append(cands, m...)
	for _, c := range cands {
		if exists(filepath.Join(c, ".venv", "Scripts", "s4kit-mcp.exe")) {
			return c
		}
	}
	return ""
}

func load(path string) (map[string]any, error) {
	obj := map[string]any{}
	b, err := os.ReadFile(path)
	if os.IsNotExist(err) {
		return obj, nil
	}
	if err != nil {
		return nil, err
	}
	if len(b) >= 3 && b[0] == 0xEF && b[1] == 0xBB && b[2] == 0xBF {
		b = b[3:]
	}
	if len(b) == 0 {
		return obj, nil
	}
	if err := json.Unmarshal(b, &obj); err != nil {
		return nil, fmt.Errorf("%s is not valid JSON (%v); not touching it", path, err)
	}
	bak := path + ".bak-" + time.Now().Format("20060102-150405")
	if err := os.WriteFile(bak, b, 0o644); err != nil {
		return nil, err
	}
	fmt.Println("  backup:", bak)
	return obj, nil
}

func save(path string, obj map[string]any) error {
	os.MkdirAll(filepath.Dir(path), 0o755)
	b, err := json.MarshalIndent(obj, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, b, 0o644)
}

func servers(obj map[string]any) map[string]any {
	s, ok := obj["mcpServers"].(map[string]any)
	if !ok {
		s = map[string]any{}
		obj["mcpServers"] = s
	}
	return s
}

func main() {
	home := os.Getenv("USERPROFILE")
	appdata := os.Getenv("APPDATA")
	fmt.Println("S4Kit register - connects S4Kit to the Claude app and Claude Code.")

	kit := findKit(home)
	if kit == "" {
		fmt.Println("\nS4Kit is not installed yet (no s4kit-mcp.exe under", filepath.Join(home, "S4Kit")+").")
		fmt.Println("Run S4Kit-Setup.exe first, then run this again.")
		pause(1)
	}
	exe := filepath.Join(kit, ".venv", "Scripts", "s4kit-mcp.exe")
	py := filepath.Join(kit, ".venv", "Scripts", "python.exe")
	fmt.Println("\nS4Kit server:", exe)

	// Claude Desktop
	failed := false
	desk := filepath.Join(appdata, "Claude", "claude_desktop_config.json")
	fmt.Println("\n[Claude app]", desk)
	env := map[string]any{}
	if d, err := load(desk); err != nil {
		fmt.Println("  ERROR:", err)
		failed = true
	} else {
		s := servers(d)
		if old, ok := s["s4kit"].(map[string]any); ok {
			if e, ok := old["env"].(map[string]any); ok {
				env = e
			}
		}
		entry := map[string]any{"command": exe}
		if len(env) > 0 {
			entry["env"] = env
		}
		s["s4kit"] = entry
		if err := save(desk, d); err != nil {
			fmt.Println("  ERROR:", err)
			failed = true
		} else {
			fmt.Println("  s4kit registered")
		}
	}

	// Claude Code (user scope lives in ~/.claude.json -> mcpServers)
	cc := filepath.Join(home, ".claude.json")
	fmt.Println("\n[Claude Code]", cc)
	if c, err := load(cc); err != nil {
		fmt.Println("  ERROR:", err)
		failed = true
	} else {
		entry := map[string]any{"type": "stdio", "command": exe, "args": []any{}, "env": env}
		servers(c)["s4kit"] = entry
		if err := save(cc, c); err != nil {
			fmt.Println("  ERROR:", err)
			failed = true
		} else {
			fmt.Println("  s4kit registered (user scope)")
		}
	}

	// Environment report with the same overrides
	fmt.Println("\n[Environment report]")
	cmd := exec.Command(py, "-c", "import json,s4kit.apps as a; print(json.dumps(a.environment_report(), indent=2))")
	cmd.Env = os.Environ()
	for k, v := range env {
		cmd.Env = append(cmd.Env, fmt.Sprintf("%s=%v", k, v))
	}
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	if err := cmd.Run(); err != nil {
		fmt.Println("  report failed:", err)
		failed = true
	}

	fmt.Println(`
Next:
  1. Right-click the Claude tray icon > Quit, then reopen Claude.
  2. Keep Blender open with S4Kit > Start bridge running.
  3. Start a NEW chat (Chat or Code) and type: run environment_report`)
	if failed {
		pause(1)
	}
	pause(0)
}
