const { execFileSync } = require("child_process");

const version = String(process.argv[2] || "").replace(/^v/, "") || "najnowsza";

function git(args) {
  try {
    return execFileSync("git", args, { encoding: "utf8" }).trim();
  } catch {
    return "";
  }
}

function describeChange(subject) {
  const text = String(subject || "").toLowerCase();
  if (/omnivoice|subtitle timing/.test(text)) return "Naprawiono ciągłość dubbingu OmniVoice i synchronizację napisów.";
  if (/qwen|silent dubbing/.test(text)) return "Naprawiono sytuację, w której dubbing mógł stać się cichy w trakcie filmu.";
  if (/release notes|update.*display/.test(text)) return "Ulepszono okno aktualizacji oraz czytelność opisu zmian.";
  if (/public.*update|release workflow/.test(text)) return "Ulepszono automatyczne aktualizacje i publikację wydań.";
  return "Ulepszenia i poprawki stabilności aplikacji.";
}

const currentTag = `v${version}`;
const previousTag = git(["describe", "--tags", "--abbrev=0", `${currentTag}^`]);
const range = previousTag ? `${previousTag}..${currentTag}` : currentTag;
const subjects = git(["log", range, "--pretty=%s"]).split("\n").filter(Boolean);
const changes = [...new Set(subjects.map(describeChange))].slice(0, 4);

process.stdout.write([
  `## Co nowego w v${version}`,
  "",
  ...(changes.length ? changes.map((change) => `- ${change}`) : ["- Ulepszenia i poprawki stabilności aplikacji."]),
  "",
  "## Aktualizacja",
  "- Po pobraniu aplikacja uruchomi się ponownie. Twoje ustawienia pozostaną zachowane.",
  "",
].join("\n"));
