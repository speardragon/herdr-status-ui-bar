# herdr-status-ui-bar

Plan-usage gauges for your AI coding agents — **Claude Code, OpenAI Codex, and Grok CLI** — rendered in the [herdr](https://herdr.dev) tab bar.

```
⛅ +29°C · claude ████░░░░░░ 39%/40% │ codex ███░░░░░░░ 32%* │ grok █░░░░░░░░░ 8% │ @13:04
```

- `claude 5h%/7d%` — Claude Code rate-limit windows, captured passively from your statusline (no network, no credentials)
- `codex 30d%` — read locally from `~/.codex/sessions` rollout logs
- `grok credit%` — one lightweight call to the Grok CLI billing API, cached with graceful fallback
- gauge = 10 cells (10% each); ` │ ` separates each agent segment (also before the timestamp)
- `@HH:MM` — when the data was read (the widget refreshes every 5 minutes) · `*` — the source data is stale
- Segments for agents you don't use are silently omitted. Zero external dependencies beyond python3 (3.9+) and curl.

## Install

As a herdr plugin (herdr 0.8+):

```
herdr plugin install speardragon/herdr-status-ui-bar
# then run the plugin's "Install agent usage tab-bar widget" action
```

Or directly:

```
git clone https://github.com/speardragon/herdr-status-ui-bar && cd herdr-status-ui-bar && ./install.sh
```

To skip wrapping your Claude Code statusline (widget still runs, just without the claude segment): `AGENT_USAGE_SKIP_CLAUDE=1 ./install.sh`

What `install.sh` does (idempotent; anything it touches is backed up as `*.bak-agent-usage-<timestamp>`):

1. copies the widget script to `~/.config/herdr/agent-usage/`
2. registers one command widget in `[ui].tab_bar_right` of your herdr `config.toml`
3. if you have a Claude Code statusline, wraps it to capture rate-limit data — your original command is preserved as a sidecar and still renders exactly as before

## Data sources

Fetching approach credits: [CodexBar](https://github.com/steipete/CodexBar)

| Agent | Source | Locality | Updated when |
|---|---|---|---|
| Claude Code | statusline capture file | local | every statusline render (live while you use Claude Code) |
| Codex | `~/.codex/sessions` rollout logs (JSONL) | local | whenever the Codex CLI writes a session |
| Grok | CLI-proxy billing REST, one call | network | each widget tick (5 min), cached on failure |

The Grok token is never passed as a curl argument — it's sent via stdin config (`-K -`), so it never shows up in `ps`. No credential is ever written to a log or to stdout.

## Notes & limitations

- As of herdr 0.8.x, the tab bar does not render ANSI escapes from command widgets, so output there is always plain text. `--color` exists for running the script directly in a terminal.
- If you've never used the Codex CLI, the codex segment is simply omitted (sessions are its only data source). A `*` means your most recent Codex session is older than 24 hours — use the CLI again and it clears.
- The claude segment requires an existing Claude Code statusline; without one it's simply omitted.
- Exotic `config.toml` layouts (for example, brackets inside a widget's command string) aren't handled by the installer's bracket-counting parser — it detects this, makes no changes, and prints instructions for adding the widget line by hand.

## Uninstall

```
./uninstall.sh   # or the plugin's uninstall action — restores your original statusline
```

Backups (`*.bak-agent-usage-<timestamp>`) created by `install.sh` are left in place intentionally — uninstall does not delete them, so you can always recover a pre-install file by hand.

## License

MIT
