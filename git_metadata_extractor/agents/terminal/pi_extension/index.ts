/**
 * v2 terminal-agent: bash command blacklist.
 *
 * Loaded by the runner via `pi -e <this file>`. Intercepts every `bash`
 * tool call and blocks commands matching the install / privilege-escalate
 * / host-modify denylist below. Read-only navigation (`ls`, `find`,
 * `grep`, `cat`, …) and our skill invocations (`python -m git_metadata_extractor.skills.*`,
 * `gme-*`) pass through untouched.
 *
 * Defense-in-depth alongside the `--tools` allowlist passed by the
 * runner. If you tighten one layer, leave the other in place.
 *
 * Block reasons are written to stderr as a JSON line so they surface in
 * the captured transcript and the judge can see what the executor tried.
 */

import type { ExtensionAPI } from "@mariozechner/pi-coding-agent";

interface DenyRule {
	pattern: RegExp;
	label: string;
}

const DENYLIST: DenyRule[] = [
	// --- package managers (the user's primary concern: no `uv pip install` etc) ---
	{ pattern: /\b(pip3?|python\s+-m\s+pip)\s+(install|uninstall)\b/, label: "pip-install" },
	{
		pattern: /\buv\s+(pip\s+install|pip\s+uninstall|add|remove|sync|tool\s+install)\b/,
		label: "uv-install",
	},
	{ pattern: /\bapt(-get)?\s+(install|remove|purge|update|upgrade)\b/, label: "apt-install" },
	{ pattern: /\b(aptitude|dpkg)\s+(install|-i|--install)\b/, label: "dpkg-install" },
	{ pattern: /\b(npm|pnpm|yarn)\s+(install|i|add|remove)\b/, label: "node-install" },
	{ pattern: /\b(cargo|gem|go)\s+(install|add)\b/, label: "lang-install" },
	{ pattern: /\b(brew|port)\s+install\b/, label: "brew-install" },
	{ pattern: /\bmake\s+install\b/, label: "make-install" },
	{ pattern: /\bsetup\.py\s+install\b/, label: "setup-py-install" },
	// --- remote-fetch + execute ---
	{ pattern: /curl\s[^|]*\|\s*(sh|bash|zsh|sudo)\b/, label: "curl-pipe-sh" },
	{ pattern: /wget\s[^|]*\|\s*(sh|bash|zsh|sudo)\b/, label: "wget-pipe-sh" },
	{ pattern: /\bbash\s+<\(\s*(curl|wget)\b/, label: "bash-process-substitution-fetch" },
	// --- privilege escalation ---
	{ pattern: /\bsudo\b/, label: "sudo" },
	{ pattern: /\bdoas\b/, label: "doas" },
	{ pattern: /\bsu\s+-/, label: "su-dash" },
	// --- remote / global git mutation ---
	{ pattern: /\bgit\s+push\b/, label: "git-push" },
	{ pattern: /\bgit\s+config\s+--global\b/, label: "git-config-global" },
	// --- host-config persistence (append to shell rc files) ---
	{
		pattern: />>?\s*~?\/?\.(bashrc|profile|zshrc|bash_profile|zprofile)\b/,
		label: "shell-rc-append",
	},
];

export default function (pi: ExtensionAPI) {
	pi.on("tool_call", async (event) => {
		if (event.toolName !== "bash") return undefined;
		const command = (event.input?.command as string | undefined) ?? "";
		for (const rule of DENYLIST) {
			if (rule.pattern.test(command)) {
				const evt = {
					kind: "tool_call_blocked",
					tool: "bash",
					rule: rule.label,
					pattern: rule.pattern.source,
					command,
				};
				process.stderr.write(JSON.stringify(evt) + "\n");
				return {
					block: true,
					reason: `Blocked by v2-terminal-agent denylist (${rule.label}). Command rejected; do not retry the same shape — pick a different approach.`,
				};
			}
		}
		return undefined;
	});
}
